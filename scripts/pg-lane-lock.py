#!/usr/bin/env python3
"""Serialise full pg-tests lanes on this laptop (LUCY-w6fpi).

    pg-lane-lock.py -- <command...>

Takes an exclusive flock on ~/.claude/fleet/pg-lane.lock, waiting (and logging
who holds it) if another full lane is running, then execs the command with the
lock fd inherited. The kernel drops the lock when the lane exits, so a crashed
lane never leaves a stale lock. At most ONE full pg lane runs at a time.

Shane, 25 Sep: per-builder full pg lanes (testcontainers in Colima) pushed load
to ~20. Builders run targeted suites (targeted-tests.py); the full lane runs at
landing and in Cloud Build, one at a time here.

If the lock cannot be opened (e.g. a sandboxed caller) the command still runs,
with a loud warning: house rule since 2026-09-09 is that infra never blocks a gate.
"""
import fcntl
import json
import os
import sys
import time
from pathlib import Path

LOCK_DIR = Path(os.environ.get("PG_LANE_LOCK_DIR", Path.home() / ".claude" / "fleet"))
LOCK = LOCK_DIR / "pg-lane.lock"
LOG = LOCK_DIR / "pg-lane.log"
HELD_ENV = "PG_LANE_LOCK_HELD"


def log(message):
    line = f"[pg-lane-lock {time.strftime('%Y-%m-%dT%H:%M:%S')}] pid={os.getpid()} {message}"
    print(line, file=sys.stderr, flush=True)
    try:
        with open(LOG, "a") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def holder(fd):
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, 4096).decode(errors="replace").strip() or "unknown"
    except OSError:
        return "unknown"


def acquire():
    """Return the locked fd, or None when locking is unavailable (fail open)."""
    try:
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as error:
        log(f"WARNING: cannot open {LOCK} ({error}); running UNSERIALISED")
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            started = time.time()
            log(f"waiting: full pg lane already running, held by {holder(fd)}")
            fcntl.flock(fd, fcntl.LOCK_EX)
            log(f"acquired after waiting {time.time() - started:.0f}s")
        else:
            log("acquired (no wait)")
        os.set_inheritable(fd, True)  # exec keeps the fd, so the lane keeps the lock
    except OSError as error:  # ENOLCK etc: never block the gate on infra
        log(f"WARNING: cannot lock {LOCK} ({error}); running UNSERIALISED")
        return None
    try:  # holder metadata is best-effort; the lock is what matters
        info = {"pid": os.getpid(), "cwd": os.getcwd(),
                "since": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "command": " ".join(sys.argv[2:])[:300]}
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(info).encode())
    except OSError as error:
        log(f"WARNING: could not record lock holder ({error}); lock still held")
    return fd


def main(argv):
    command = argv[1:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("usage: pg-lane-lock.py -- <command...>", file=sys.stderr)
        return 2
    if os.environ.get(HELD_ENV) != "1":
        acquire()
        os.environ[HELD_ENV] = "1"  # nested wrappers must not wait on themselves
    try:
        os.execvp(command[0], command)
    except OSError as error:
        log(f"cannot execute {command[0]}: {error}")
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
