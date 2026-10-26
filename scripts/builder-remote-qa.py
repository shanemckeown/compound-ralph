#!/usr/bin/env python3
"""builder-remote-qa.py: a builder's or reviewer's HEAVY test run, on Cloud Build (LUCY-ll8dh).

    builder-remote-qa.py [--sha HEAD] [--max-inflight N] [--admit-wait 600] [--poll 30] [--keep-scratch]

Run from an AestheticcNext worktree root. Fast targeted tests stay local (targeted-tests.py);
this is the heavy set: full node + dom + mobile jest, the full pg lane, lint, tsc and the
production build, i.e. the two lanes of the land gate's cloudbuild-gate.yaml, run by the
existing runner ~/.claude/scripts/cloudbuild-gate-remote.py (unchanged).

1. Resolve --sha to an exact commit. The scratch is `git archive <sha>` into a fresh temp dir,
   never the worktree, so every verdict names the tree it tested. A dirty worktree is not
   tested: a note says the run covers the committed sha only.
2. Admission (Cloud Build quota cloudbuild.googleapis.com/ongoing_builds, quota id
   OngoingBuildsPerProject, project aestheticc, region global; shared with prod builds and
   landing gates). The project limit is read once per run:
   `gcloud beta quotas preferences describe ongoing-builds-20 --project=aestheticc
   --format=value(quotaConfig.grantedValue)`; a failed, empty or non-integer read means 10.
   The cap for builder runs is limit - BUILDER_QA_RESERVE (default 4, for prod + landing),
   so 10 -> 6 and 20 -> 16; BUILDER_QA_MAX_INFLIGHT (or --max-inflight) overrides the cap.
   A run adds 2 builds: while `gcloud builds list --ongoing` + 2 > cap, poll every --poll
   seconds up to --admit-wait, then fall back local. A failed list is infra: fall back local,
   never submit blind. Count-then-submit is racy (two builders can both see room and briefly
   overshoot by 2); acceptable, because the reserve still leaves prod and landing room.
3. Remote verdict: runner exit 0 WITH its `RESULT unit=SUCCESS pg=SUCCESS exit 0` line ->
   `REMOTE GREEN @<sha>`, exit 0. Exit 1 WITH a `FAILED LANE` line -> `REMOTE RED @<sha>` and
   that line, exit 1. Anything else (2 refused, 137 infra, 127, a 1 with no FAILED LANE line,
   a 0 with no RESULT line, uv or gcloud missing, any exception) is infra: never green, never
   a code red. It prints `FALLBACK local: <reason>` and runs the local lane on the same
   scratch (node_modules linked from the worktree): node + dom jest via gated-test-run.py, the
   full pg lane under pg-lane-lock.py, then `npm run build`, stopping at the first red. Its
   exit is the verdict: `LOCAL GREEN @<sha>` (0) or `LOCAL RED @<sha>` (that step's exit).
   SIGTERM/SIGINT pass to the runner (it cancels its builds) and exit 130, with no fallback.
4. BUILDER_QA_REMOTE=0 forces local, =1 remote; unset or unrecognised = REMOTE_DEFAULT
   (tier_gates.gate_remote()'s pattern).
5. Evidence: sha, mode, build ids, wall and queue-wait seconds on stdout, and one JSON line
   appended to ~/.claude/state/builder-remote-qa.jsonl (BUILDER_QA_LOG overrides).

Never exits 0 unless a green was observed. Never deploys, never touches traffic.
Every gcloud call goes through one runner (CLOUDBUILD_GATE_GCLOUD names another binary);
tests inject fakes into main().
"""
import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
RUNNER = SCRIPTS / "cloudbuild-gate-remote.py"
GATED = [sys.executable, str(SCRIPTS / "gated-test-run.py")]
PG_LOCK = [sys.executable, str(SCRIPTS / "pg-lane-lock.py"), "--"]
TIER_GATES = SCRIPTS.parent / "skills" / "land-batch" / "bin" / "tier_gates.py"
PROJECT, REGION = "aestheticc", "global"
QUOTA_PREFERENCE = "ongoing-builds-20"
FALLBACK_LIMIT = 10  # the project's granted ongoing-builds limit when it cannot be read
DEFAULT_RESERVE = 4  # builds kept free for prod builds and landing gates
BUILDS_PER_RUN = 2  # the runner submits two lanes (unit + pg)
GCLOUD_TIMEOUT = 90
# BUILDER_QA_REMOTE=1 / =0 picks remote / local; unset uses this.
REMOTE_DEFAULT = True
LOG = Path.home() / ".claude" / "state" / "builder-remote-qa.jsonl"
GREEN_LINE = re.compile(r"^RESULT unit=SUCCESS pg=SUCCESS exit 0\s*$", re.M)
FAILED_LANE = re.compile(r"^FAILED LANE \S+: .*$", re.M)
LANE_BUILD = re.compile(r"^LANE (\S+) build (\S+)", re.M)
EXIT_GREEN, EXIT_RED, EXIT_LOCAL_UNAVAILABLE, EXIT_INTERRUPTED = 0, 1, 3, 130
# The gate's next-build placeholders (cloudbuild-gate.yaml): no secrets, the same module graph.
BUILD_ENV = {"NODE_OPTIONS": "--max-old-space-size=6144", "NEXT_TELEMETRY_DISABLED": "1",
             "NEXT_DISABLE_WEBPACK_CACHE": "1", "NEXTAUTH_URL": "https://aestheti.cc",
             "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY": "pk_test_land_gate_placeholder",
             "NEXT_PUBLIC_RECAPTCHA_SITE_KEY": "land-gate-placeholder",
             "NEXT_PUBLIC_POSTHOG_KEY": "phc_land_gate_placeholder",
             "NEXT_PUBLIC_SENTRY_DSN": "", "NEXT_PUBLIC_SENTRY_ENVIRONMENT": "land-gate"}


class Interrupted(Exception):
    """Raised by the SIGTERM/SIGINT handler."""


# ---------------------------------------------------------------- pure plan functions

def qa_remote(env=None):
    value = (os.environ if env is None else env).get("BUILDER_QA_REMOTE", "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return REMOTE_DEFAULT


def parse_limit(result):
    """(limit, note) from the quota read's (code, stdout, stderr); FALLBACK_LIMIT unless clean."""
    code, out, err = result
    value = (out or "").strip().strip("'\"")
    if code != 0:
        return FALLBACK_LIMIT, f"quota read failed (exit {code}: {(err or '').strip()[-200:]}); limit {FALLBACK_LIMIT}"
    if not value.isdigit() or int(value) <= 0:
        return FALLBACK_LIMIT, f"quota read returned {value!r}; limit {FALLBACK_LIMIT}"
    return int(value), f"granted limit {int(value)}"


def builder_cap(limit, env=None, override=None):
    """(cap, note): the ongoing builds a builder run may bring the project to."""
    env = os.environ if env is None else env
    raw = override if override is not None else env.get("BUILDER_QA_MAX_INFLIGHT", "").strip()
    if str(raw).strip():
        try:
            cap = int(raw)
        except ValueError:
            cap = None
        if cap is not None and cap >= 0:
            return cap, f"cap {cap} (override)"
    reserve = DEFAULT_RESERVE
    raw_reserve = env.get("BUILDER_QA_RESERVE", "").strip()
    if raw_reserve.isdigit():
        reserve = int(raw_reserve)
    cap = max(0, limit - reserve)
    return cap, f"cap {cap} = limit {limit} - reserve {reserve}"


def count_ongoing(result):
    """Ongoing build count from `gcloud builds list --ongoing --format=value(id)`, or None."""
    code, out, _ = result
    if code != 0:
        return None
    return len([line for line in (out or "").splitlines() if line.strip()])


def remote_verdict(code, output):
    """('green'|'red'|'infra', reason). Green and red each need the runner's own evidence line."""
    if code == 0 and GREEN_LINE.search(output or ""):
        return "green", "both lanes SUCCESS"
    if code == 1:
        failed = FAILED_LANE.findall(output or "")
        if failed:
            return "red", failed[-1]
        return "infra", "runner exit 1 with no FAILED LANE line (not a test result)"
    if code == 0:
        return "infra", "runner exit 0 with no RESULT line (not an observed green)"
    return "infra", f"runner exit {code}"


def build_ids(output):
    return {lane: build for lane, build in LANE_BUILD.findall(output or "")}


def local_steps():
    """The local fallback lane, in order: (label, command, env overrides, env removals)."""
    return [
        ("node + dom jest", [*GATED, "jest"], {}, ("RUN_PG_TESTS", "PG_LANE_LOCK_TOKEN")),
        ("full pg lane", [*PG_LOCK, *GATED, "jest", "--selectProjects", "pg-tests"],
         {"RUN_PG_TESTS": "true", "TZ": "UTC"}, ("SKIP_PG_TESTS", "PG_LANE_LOCK_TOKEN")),
        ("npm run build", ["npm", "run", "build"], dict(BUILD_ENV), ("PG_LANE_LOCK_TOKEN",)),
    ]


def step_env(base, overrides, removals):
    env = {k: v for k, v in base.items() if k not in removals}
    env.update(overrides)
    return env


# ---------------------------------------------------------------- side effects

def run_gcloud(args):
    """The only place gcloud runs. Returns (returncode, stdout, stderr); never raises."""
    binary = os.environ.get("CLOUDBUILD_GATE_GCLOUD") or "gcloud"
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=GCLOUD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "", f"gcloud {' '.join(args[:3])} timed out after {GCLOUD_TIMEOUT}s"
    except OSError as exc:
        return 127, "", f"could not execute {binary}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def read_limit(gcloud):
    try:
        result = gcloud(["beta", "quotas", "preferences", "describe", QUOTA_PREFERENCE,
                         f"--project={PROJECT}", "--format=value(quotaConfig.grantedValue)", "--quiet"])
    except Exception as exc:  # noqa: BLE001 (an unreadable limit is the fallback limit)
        result = (127, "", str(exc))
    return parse_limit(result)


def ongoing(gcloud):
    try:
        return count_ongoing(gcloud(["builds", "list", "--ongoing", f"--project={PROJECT}",
                                     f"--region={REGION}", "--format=value(id)", "--quiet"]))
    except Exception:  # noqa: BLE001 (could not count = infra)
        return None


def admit(gcloud, cap, wait, poll, sleep, clock, say):
    """(admitted, waited seconds, reason). Counts again after every poll."""
    started = clock()
    announced = False
    while True:
        count = ongoing(gcloud)
        waited = clock() - started
        if count is None:
            return False, waited, "gcloud builds list failed (cannot count ongoing builds; not submitting blind)"
        if count + BUILDS_PER_RUN <= cap:
            return True, waited, f"{count} ongoing + {BUILDS_PER_RUN} <= cap {cap}"
        if waited >= wait:
            return False, waited, (f"admission: {count} ongoing + {BUILDS_PER_RUN} > cap {cap} "
                                   f"after {waited:.0f}s wait")
        if not announced:
            say(f"QUEUE: {count} ongoing + {BUILDS_PER_RUN} > cap {cap}; polling every {poll:.0f}s "
                f"for up to {wait:.0f}s")
            announced = True
        sleep(poll)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_scratch(sha, worktree):
    """A fresh dir holding exactly the tree of `sha` (git archive | tar -x)."""
    scratch = Path(tempfile.mkdtemp(prefix=f"builder-qa-{sha[:12]}-"))
    archive = subprocess.Popen(["git", "archive", "--format=tar", sha], cwd=worktree,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    untar = subprocess.run(["tar", "-x", "-C", str(scratch)], stdin=archive.stdout,
                           capture_output=True)
    archive.stdout.close()
    archive_err = archive.stderr.read().decode(errors="replace")
    archive.stderr.close()
    if archive.wait() != 0 or untar.returncode != 0:
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError(f"git archive {sha[:12]} failed: {archive_err.strip()[-300:]} "
                           f"{untar.stderr.decode(errors='replace').strip()[-300:]}")
    return scratch


def link_node_modules(scratch, worktree):
    """tier_gates.link_node_modules (the land scratch's): scratch/node_modules -> the worktree's."""
    if not (Path(worktree) / "node_modules").is_dir():  # follows a symlinked node_modules
        return None
    spec = importlib.util.spec_from_file_location("tier_gates", TIER_GATES)
    tier_gates = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tier_gates)
    return tier_gates.link_node_modules(scratch, worktree)


class Child:
    """The one running subprocess, so the signal path can pass the signal on."""
    proc = None


def stream(command, cwd, env, say, child):
    """Run command, echoing its output line by line. Returns (code, output)."""
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    child.proc = proc  # stays set on an exception, so main()'s signal path can stop it
    lines = []
    for line in proc.stdout:
        lines.append(line)
        say(line.rstrip("\n"))
    code = proc.wait()
    child.proc = None
    return code, "".join(lines)


def stop_child(child):
    """SIGTERM the running child (the runner then cancels its own builds); kill it after 60 s."""
    proc = child.proc
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_remote(scratch, say, child):
    command = ["uv", "run", "--script", str(RUNNER), "--scratch", str(scratch)]
    return stream(command, str(scratch), dict(os.environ), say, child)


def run_local(scratch, worktree, say, child):
    """(code, label of the failed step or None)."""
    if not link_node_modules(scratch, worktree):
        say(f"LOCAL UNAVAILABLE: no node_modules in {worktree}")
        return EXIT_LOCAL_UNAVAILABLE, "node_modules"
    for label, command, overrides, removals in local_steps():
        say(f"==> local {label}: {' '.join(command)}")
        code, _ = stream(command, str(scratch), step_env(os.environ, overrides, removals), say, child)
        say(f"<== local {label}: exit {code}")
        if code != 0:
            return code, label
    return EXIT_GREEN, None


def append_log(record, path=None):
    path = Path(path or os.environ.get("BUILDER_QA_LOG") or LOG)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # evidence only; never changes the verdict


def _interrupt(signum, _frame):
    raise Interrupted(signal.Signals(signum).name)


# ---------------------------------------------------------------- main

def main(argv=None, gcloud=run_gcloud, remote=run_remote, local=run_local, scratch_maker=make_scratch,
         sleep=time.sleep, clock=time.monotonic, out=None, env=None, cwd=None):
    out = out or sys.stdout
    env = os.environ if env is None else env

    def say(text):
        print(text, file=out, flush=True)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--max-inflight", default=None, help="override the cap (else BUILDER_QA_MAX_INFLIGHT, "
                                                          "else project limit - BUILDER_QA_RESERVE)")
    ap.add_argument("--admit-wait", type=float, default=600)
    ap.add_argument("--poll", type=float, default=30)
    ap.add_argument("--keep-scratch", action="store_true")
    args = ap.parse_args(argv)

    worktree = Path(cwd or os.getcwd())
    started = clock()
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "worktree": str(worktree), "mode": None,
              "verdict": None, "exit": None, "build_ids": {}, "queue_wait_s": 0, "reason": None}
    child = Child()
    holder = {}
    sigs = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.signal(sig, _interrupt) for sig in sigs}
    try:
        code = _run(args, worktree, env, gcloud, remote, local, scratch_maker, sleep, clock, say,
                    record, child, holder)
    except Interrupted as exc:
        for sig in sigs:
            signal.signal(sig, signal.SIG_IGN)
        stop_child(child)
        say(f"INTERRUPTED by {exc}: no verdict @{record.get('sha', '?')}")
        record.update(verdict="interrupted", reason=str(exc))
        code = EXIT_INTERRUPTED
    except Exception as exc:  # noqa: BLE001 (a crash here is never a green)
        stop_child(child)
        say(f"ERROR: {type(exc).__name__}: {exc}: no verdict @{record.get('sha', '?')}")
        record.update(verdict="error", reason=f"{type(exc).__name__}: {exc}")
        code = EXIT_LOCAL_UNAVAILABLE
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        scratch = holder.get("scratch")
        if scratch is not None and not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        elif scratch is not None:
            say(f"scratch kept: {scratch}")
    if code == 0 and record.get("verdict") not in ("remote-green", "local-green"):
        code = EXIT_LOCAL_UNAVAILABLE  # belt and braces: 0 only with an observed green
    record["exit"] = code
    record["wall_s"] = round(clock() - started, 1)
    say(f"EVIDENCE sha={record.get('sha')} mode={record['mode']} verdict={record['verdict']} "
        f"builds={','.join(f'{k}:{v}' for k, v in record['build_ids'].items()) or '-'} "
        f"wall={record['wall_s']}s queue_wait={record['queue_wait_s']}s")
    append_log(record, env.get("BUILDER_QA_LOG"))
    return code


def _run(args, worktree, env, gcloud, remote, local, scratch_maker, sleep, clock, say, record, child,
         holder):
    sha = git("rev-parse", "--verify", f"{args.sha}^{{commit}}", cwd=worktree).strip()
    record["sha"] = sha
    say(f"builder-remote-qa: sha {sha}")
    try:
        dirty = git("status", "--porcelain", cwd=worktree).strip()
    except subprocess.CalledProcessError:
        dirty = "unknown"
    if dirty:
        say(f"NOTE: the worktree has uncommitted changes; this run tests the committed sha {sha[:12]} only")
    record["dirty"] = bool(dirty)
    scratch = holder["scratch"] = scratch_maker(sha, worktree)

    def fall_back(reason):
        say(f"FALLBACK local: {reason}")
        record["reason"] = reason
        return _local(scratch, worktree, sha, local, say, record, child, mode="fallback")

    if not qa_remote(env):
        record["reason"] = "BUILDER_QA_REMOTE=0"
        say("mode local (BUILDER_QA_REMOTE=0)")
        return _local(scratch, worktree, sha, local, say, record, child, mode="local")

    limit, limit_note = read_limit(gcloud)  # once per run, never cached across runs
    cap, cap_note = builder_cap(limit, env, args.max_inflight)
    record.update(limit=limit, cap=cap)
    say(f"admission: {limit_note}; {cap_note}")
    admitted, waited, why = admit(gcloud, cap, args.admit_wait, args.poll, sleep, clock, say)
    record["queue_wait_s"] = round(waited, 1)
    if not admitted:
        return fall_back(why)
    say(f"admitted: {why} (queue wait {waited:.0f}s)")
    try:
        code, output = remote(scratch, say, child)
    except Interrupted:
        raise
    except Exception as exc:  # noqa: BLE001 (uv missing, exec errors: infra)
        return fall_back(f"runner could not run: {type(exc).__name__}: {exc}")
    record["build_ids"] = build_ids(output)
    verdict, reason = remote_verdict(code, output)
    if verdict == "green":
        record.update(mode="remote", verdict="remote-green", reason=reason)
        say(f"REMOTE GREEN @{sha}")
        return EXIT_GREEN
    if verdict == "red":
        record.update(mode="remote", verdict="remote-red", reason=reason)
        say(f"REMOTE RED @{sha}")
        say(reason)
        return EXIT_RED
    return fall_back(reason)


def _local(scratch, worktree, sha, local, say, record, child, mode):
    record["mode"] = mode
    code, failed = local(scratch, worktree, say, child)
    if code == 0 and failed is None:
        record["verdict"] = "local-green"
        say(f"LOCAL GREEN @{sha}")
        return EXIT_GREEN
    code = code or EXIT_RED  # a failed step that somehow exited 0 is still not green
    record["verdict"] = "local-red"
    record["failed_step"] = failed
    say(f"LOCAL RED @{sha}: {failed} exit {code}")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
