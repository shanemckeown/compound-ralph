#!/usr/bin/env python3
"""One-Mac, cross-worktree RAM admission for one-shot Jest and TypeScript runs.

Usage: python3 scripts/gated-test-run.py jest --findRelatedTests path/to/file.ts
       python3 scripts/gated-test-run.py tsc --noEmit -p tsconfig.small.json
       python3 scripts/gated-test-run.py tsc-heavy --noEmit
       python3 scripts/gated-test-run.py --reserve-mib 4096 -- command args...

Python 3 standard library only. See Product/QA/MEMORY_GATE_DESIGN.md.
"""

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import random
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time

MIB = 1024 * 1024
ROOT = Path(os.environ.get("HEAVY_GATE_DIR", "~/.claude/state/heavy-runs-v1")).expanduser()


def log(message):
    print("memory-gate: " + message, file=sys.stderr, flush=True)


def output(*args):
    return subprocess.check_output(args, text=True, timeout=3,
                                   env={**os.environ, "LC_ALL": "C"})


def parse_vm(text):
    page_size = int(re.search(r"page size of (\d+) bytes", text)[1])
    counters = {name.strip('"'): int(value) for name, value in
                re.findall(r'^([^:\n]+):\s+(\d+)\.', text, re.M)}
    # vm_stat prints free EXCLUDING speculative. Do not add purgeable/file-backed:
    # these overlap other categories. Inactive is potentially reclaimable, not free.
    available = sum(counters[key] for key in
                    ("Pages free", "Pages inactive", "Pages speculative"))
    return available * page_size / MIB


def memory():
    available = parse_vm(output("/usr/bin/vm_stat"))
    total = int(output("/usr/sbin/sysctl", "-n", "hw.memsize")) / MIB
    # This sysctl exports DISPATCH flags (1 normal, 2 warning, 4 critical),
    # not XNU's internal enum (0 normal, 1 warning, ...).
    pressure = int(output("/usr/sbin/sysctl", "-n",
                          "kern.memorystatus_vm_pressure_level"))
    if not 0 <= available <= total or pressure not in (1, 2, 4):
        raise ValueError("unrecognised macOS memory telemetry")
    return available, pressure


def processes():
    result = {}
    for line in output("/bin/ps", "-axo", "pid=,pgid=,rss=,stat=,lstart=").splitlines():
        pid, pgid, rss, status, born = line.strip().split(None, 4)
        if "Z" not in status:
            result[int(pid)] = {"pgid": int(pgid), "rss": int(rss) / 1024,
                                "born": born}
    return result


def live_leases(leases, procs):
    live, growth = [], 0
    for lease in leases:
        pid = lease["pid"]
        if (not isinstance(pid, int) or pid <= 0 or
                not isinstance(lease["reserve"], (float, int)) or
                not 0 < lease["reserve"] < 1048576 or
                not isinstance(lease["born"], str)):
            raise ValueError("invalid reservation")
        # A new leader with the same PID means the old group ended (PID reuse).
        if pid in procs and procs[pid]["born"] != lease["born"]:
            continue
        members = [p for p in procs.values() if p["pgid"] == pid]
        if not members:
            continue
        # A killed Jest parent can leave workers alive. Keep accounting for the
        # group until ALL members leave; no TTL may evict a still-running job.
        rss = sum(p["rss"] for p in members)
        growth += max(0, lease["reserve"] - rss)
        live.append(lease)
    return live, growth


@contextlib.contextmanager
def admission_lock():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Never unlink/replace this inode, including during ledger recovery.
    with (ROOT / "admission.lock").open("a") as lock:
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("admission mutex unavailable for 10s")
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def save(leases):
    fd, name = tempfile.mkstemp(prefix="ledger-", dir=ROOT)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(leases, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, ROOT / "leases.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def attempt(reserve, headroom):
    with admission_lock():
        procs = processes()
        ledger = ROOT / "leases.json"
        try:
            leases = json.loads(ledger.read_text()) if ledger.exists() else []
            if not isinstance(leases, list):
                raise ValueError("ledger must be a list")
            leases, growth = live_leases(leases, procs)
        except (ValueError, TypeError, KeyError):
            # Keep evidence, recover future calls, fail open THIS call visibly.
            ledger.rename(ROOT / ("leases.corrupt-" + str(time.time_ns())))
            raise ValueError("corrupt reservation ledger quarantined")
        available, pressure = memory()
        effective = available - growth
        admitted = pressure == 1 and effective >= reserve + headroom
        message = (f"available~{available:.0f} MiB, pending-growth={growth:.0f} MiB, "
                   f"need={reserve + headroom:.0f} MiB, pressure={pressure}, "
                   f"active={len(leases)}")
        if admitted:
            leases.append({"pid": os.getpid(), "born": procs[os.getpid()]["born"],
                           "reserve": reserve})
        save(leases)  # Claim is durable BEFORE exec; next contender sees it.
        return admitted, message


def command_for(args, parser):
    rest = args.command
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        parser.error("supply jest, jest-heavy, tsc, tsc-heavy, or -- command")
    profile, *tail = rest
    profiles = {"jest": 2048, "jest-heavy": 3072, "tsc": 3072, "tsc-heavy": 6144}
    env = os.environ.copy()
    if profile not in profiles:
        if args.reserve_mib is None:
            parser.error("arbitrary commands require --reserve-mib")
        return rest, args.reserve_mib, env
    if (any(arg.startswith(("--watch", "--daemon")) for arg in tail) or
            (profile.startswith("tsc") and "-w" in tail)):
        parser.error("one-shot runs only; watch needs a per-rebuild gate")
    heap = args.heap_mib or profiles[profile]
    # Remove inherited blanket heap overrides; preserve unrelated NODE_OPTIONS.
    options, skip = [], False
    for token in shlex.split(env.get("NODE_OPTIONS", "")):
        if skip:
            skip = False
            continue
        name = token.split("=", 1)[0].replace("_", "-")
        if name in ("--max-old-space-size", "--max-old-space-size-percentage"):
            skip = "=" not in token
        else:
            options.append(token)
    options.append(f"--max-old-space-size={heap}")
    env["NODE_OPTIONS"] = " ".join(json.dumps(token) for token in options)
    if profile.startswith("jest"):
        in_band = "--runInBand" in tail or "-i" in tail
        workers, explicit_workers = 2, False
        for index, token in enumerate(tail):
            if token in ("--maxWorkers", "-w") or token.startswith("--maxWorkers="):
                value = token.split("=", 1)[1] if "=" in token else tail[index + 1]
                if not value.isdigit() or int(value) <= 0:
                    parser.error("use a positive integer --maxWorkers with this gate")
                workers, explicit_workers = int(value), True
        worker_args = [] if in_band or explicit_workers else ["--maxWorkers=2"]
        command = ["node", "./node_modules/jest/bin/jest.js", *tail, *worker_args]
        # AestheticcNext-54pd3: full node/dom ~1.2 GiB each; concurrent groups
        # ~3.54 GiB combined. Reserve 4 GiB for a normal two-worker run, with
        # margin and linear growth for extra workers. Heaps are ceilings, not RSS.
        if profile == "jest" and heap == 2048 and not in_band:
            reserve = 1024 + 1536 * workers
        else:
            reserve = (heap + 512) * (1 if in_band else workers + 1)
    else:
        command = ["node", "./node_modules/typescript/bin/tsc", *tail]
        reserve = heap + 768
    return command, args.reserve_mib or reserve, env


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reserve-mib", type=positive)
    parser.add_argument("--heap-mib", type=positive)
    parser.add_argument("--headroom-mib", type=positive, default=3072)
    parser.add_argument("--wait-seconds", type=positive, default=1800)
    parser.add_argument("--check", action="store_true", help="print memory; do not run")
    parser.add_argument("--bypass", action="store_true", help="warn and run without admission")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.check:
        available, pressure = memory()
        print(json.dumps({"available_estimate_mib": round(available), "pressure": pressure,
                          "state_dir": str(ROOT)}))
        return 0
    command, reserve, env = command_for(args, parser)
    if args.bypass:
        log("WARNING: explicit bypass; running without RAM admission")
    else:
        try:
            # exec keeps PID/birth/group, so no launcher can die leaving an
            # unaccounted child. Workers inherit the group. No shell eval/npx.
            if os.getpgrp() != os.getpid():
                os.setpgid(0, 0)
            deadline, next_log = time.monotonic() + args.wait_seconds, 0
            while True:
                admitted, detail = attempt(reserve, args.headroom_mib)
                if admitted:
                    log("admitted: " + detail)
                    break
                now = time.monotonic()
                if now >= deadline:
                    log("DEFERRED: memory still tight; command NOT RUN (exit 75). " + detail)
                    return 75
                if now >= next_log:
                    log("waiting: " + detail)
                    next_log = now + 30
                time.sleep(min(random.uniform(4, 6), deadline - now))
        except Exception as error:
            # Only infrastructure errors reach here; command has not started.
            # Never retry a failed test command as though the gate broke.
            log(f"WARNING: admission broken ({type(error).__name__}: {error}); FAIL OPEN")
    # Restore normal signals, including Python's ignored SIGPIPE, before exec.
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGPIPE):
        signal.signal(sig, signal.SIG_DFL)
    try:
        os.execvpe(command[0], command, env)
    except FileNotFoundError:
        log("command not found: " + command[0])
        return 127
    except OSError as error:
        log("cannot execute command: " + str(error))
        return 126


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
