#!/usr/bin/env python3
"""Serialise full pg-tests lanes on this laptop (LUCY-w6fpi).

    pg-lane-lock.py -- <command...>

Takes an exclusive flock on ~/.claude/fleet/pg-lane.lock, waiting (and logging who
holds it) if another full lane is running, then runs the command as a child and
returns its exit code. At most ONE full pg lane runs at a time.

Shane, 25 Sep: per-builder full pg lanes (testcontainers in Colima) pushed load to
~20. Builders run targeted suites (targeted-tests.py); the full lane runs at landing
and in Cloud Build, one at a time here.

Attempt 2 rules (Astra xhigh review of 72c2386):
- Fail CLOSED. If the lock cannot be opened or taken (sandbox EPERM, ENOLCK), the
  command does NOT run and this exits LOCK_UNAVAILABLE. Unserialised full lanes are
  the load this exists to stop.
- Only this wrapper holds the lock fd. It is close-on-exec and never inherited, so a
  jest worker or container helper that outlives a kill -9'd wrapper cannot keep the
  lock. The kernel drops it the moment this process dies.
- No environment flag is trusted. A nested wrapper skips locking only when the lock
  is really held right now AND its recorded holder pid is one of our ancestors AND
  the token the holder handed its child matches the one recorded in the lock file.
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

LOCK_DIR = Path(os.environ.get("PG_LANE_LOCK_DIR", Path.home() / ".claude" / "fleet"))
LOCK = LOCK_DIR / "pg-lane.lock"
LOG = LOCK_DIR / "pg-lane.log"
TOKEN_ENV = "PG_LANE_LOCK_TOKEN"
LOCK_UNAVAILABLE = 3


class LockUnavailable(Exception):
    pass


def log(message):
    line = f"[pg-lane-lock {time.strftime('%Y-%m-%dT%H:%M:%S')}] pid={os.getpid()} {message}"
    print(line, file=sys.stderr, flush=True)
    try:
        with open(LOG, "a") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def read_holder():
    try:
        return json.loads(LOCK.read_text() or "{}")
    except (OSError, ValueError):
        return {}


def ancestors():
    """Our parent chain. ps can be unavailable (sandbox); then just the parent."""
    chain, pid = [], os.getppid()
    for _ in range(64):
        if pid <= 1 or pid in chain:
            break
        chain.append(pid)
        try:
            out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=5)
            pid = int(out.stdout.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return chain


def lock_is_held():
    try:
        fd = os.open(LOCK, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def held_by_ancestor():
    token = os.environ.get(TOKEN_ENV)
    if not token:
        return False
    holder = read_holder()
    return (holder.get("token") == token and lock_is_held()
            and holder.get("pid") in ancestors())


def acquire(command):
    """Return (fd, token) with the lock held. Raise LockUnavailable otherwise."""
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    except OSError as error:
        raise LockUnavailable(f"cannot open {LOCK} ({error})") from error
    try:
        os.set_inheritable(fd, False)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            started = time.time()
            log(f"waiting: full pg lane already running, held by {read_holder() or 'unknown'}")
            fcntl.flock(fd, fcntl.LOCK_EX)
            log(f"acquired after waiting {time.time() - started:.0f}s")
        else:
            log("acquired (no wait)")
    except OSError as error:
        os.close(fd)
        raise LockUnavailable(f"cannot lock {LOCK} ({error})") from error
    token = uuid.uuid4().hex
    try:  # holder metadata is best-effort; the lock is what serialises
        info = {"pid": os.getpid(), "token": token, "cwd": os.getcwd(),
                "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "command": " ".join(command)[:300]}
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(info).encode())
    except OSError as error:
        log(f"WARNING: could not record lock holder ({error}); lock still held")
    return fd, token


def run(command, env):
    try:
        child = subprocess.Popen(command, env=env, close_fds=True)
    except OSError as error:
        log(f"cannot execute {command[0]}: {error}")
        return 127

    def forward(signum, _frame):
        try:
            child.send_signal(signum)
        except OSError:
            pass
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, forward)
    code = child.wait()
    return 128 - code if code < 0 else code  # killed by signal N -> 128+N, like a shell


def main(argv):
    command = argv[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("usage: pg-lane-lock.py -- <command...>", file=sys.stderr)
        return 2
    if held_by_ancestor():
        log("lock already held by an ancestor wrapper; running nested lane directly")
        return run(command, os.environ.copy())
    try:
        _fd, token = acquire(command)  # held until this process exits
    except LockUnavailable as error:
        log(f"REFUSED: {error}. The full pg lane was NOT run (fail closed, LUCY-w6fpi). "
            "Run it unsandboxed so ~/.claude/fleet is writable.")
        return LOCK_UNAVAILABLE
    return run(command, {**os.environ, TOKEN_ENV: token})


if __name__ == "__main__":
    sys.exit(main(sys.argv))
