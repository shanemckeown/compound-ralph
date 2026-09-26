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
PG_LANE_LOCK = Path(__file__).resolve().parent / "pg-lane-lock.py"
# Any of these narrows a pg-tests run below the full lane (LUCY-w6fpi).
PG_NARROWING = ("--findRelatedTests", "--testPathPattern", "--testPathPatterns",
                "--runTestsByPath", "--onlyChanged", "-o", "--changedSince")
# Options whose values are never test filters. Every token up to the next option is
# treated as their value; that over-locks at worst. --selectProjects is an array
# option: jest reads `--selectProjects pg-tests tests/x.test.ts` as two project names.
JEST_PATH_OPTIONS = {"--selectProjects", "--testEnvironment", "--env", "--testRunner",
                     "--testPathIgnorePatterns", "--modulePaths", "--moduleDirectories",
                     "--config", "-c", "--outputFile", "--rootDir", "--roots", "--reporters",
                     "--coverageDirectory", "--cacheDirectory", "--testSequencer",
                     "--globalSetup", "--globalTeardown", "--setupFiles",
                     "--setupFilesAfterEnv", "--testResultsProcessor", "--resolver"}


def is_full_pg_lane(tail, env=None):
    """True for `jest --selectProjects pg-tests` with no path/related filter.

    Also true for a bare `jest` run with RUN_PG_TESTS=true: jest.config.js then adds
    the pg-tests project to the default set, so it is the full lane plus node/dom.
    Full lanes are serialised laptop-wide through pg-lane-lock.py; targeted
    pg runs (the builder gate) are not.
    """
    env = os.environ if env is None else env
    selects = any(t == "--selectProjects" or t.startswith("--selectProjects=") for t in tail)
    selects_pg = not selects and env.get("RUN_PG_TESTS") == "true"
    for index, token in enumerate(tail):
        if token == "--selectProjects":
            selects_pg |= any(value == "pg-tests" for value in
                              _values_after(tail, index + 1))
        elif token.startswith("--selectProjects="):
            selects_pg |= "pg-tests" in token.split("=", 1)[1].split(",")
    if not selects_pg:
        return False
    if any(token.split("=", 1)[0] in PG_NARROWING for token in tail):
        return False
    # A positional test path (tests/pg-integration/x.test.ts) also narrows it. Err
    # towards "full": an unrecognised token only costs a wait for the lock, while a
    # full lane misread as targeted is the load this lock exists to stop.
    in_path_option = False
    for token in tail:
        if token.startswith("-"):
            # `--selectProjects=pg-tests x` still consumes x as a project name.
            in_path_option = token.split("=", 1)[0] in JEST_PATH_OPTIONS
            continue
        if not in_path_option and ("/" in token or ".test" in token
                                   or token.endswith((".ts", ".tsx", ".js"))):
            return False
    return True


def _values_after(tail, start):
    for token in tail[start:]:
        if token.startswith("-"):
            return
        yield token


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
        if is_full_pg_lane(tail):
            command = [sys.executable, str(PG_LANE_LOCK), "--", *command]
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
        # 2026-09-09, Shane, explicit and direct: "this needs fixed completely
        # right now, for all memory guardrails we have, the experiment has
        # failed" -- after this exact gate deferred a one-shot tsc-heavy run
        # (and blocked AestheticcNext-y2w10 live) for ~12 hours straight on a
        # machine that was, in fact, genuinely tight (24 GiB RAM, swap at
        # 10.9/12 GiB used when checked) rather than the ~40 GiB he believed
        # was configured. Real tradeoff, not a false alarm on either side --
        # but it's his machine and his explicit call once he had the real
        # numbers. Admission is now advisory: try once, log the real
        # available/pressure/need numbers either way, and ALWAYS run the
        # command. This keeps the NODE_OPTIONS heap-sizing and worker-count
        # logic above (still genuinely useful, unrelated to the complaint)
        # while removing every deferral/wait path. Do not silently restore
        # blocking behavior without Shane asking for it back.
        try:
            if os.getpgrp() != os.getpid():
                os.setpgid(0, 0)
            admitted, detail = attempt(reserve, args.headroom_mib)
            log(("admitted: " if admitted else "PROCEEDING UNGATED (admission would have refused): ") + detail)
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
