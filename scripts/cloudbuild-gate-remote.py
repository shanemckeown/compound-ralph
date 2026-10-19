#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml"]
# ///
"""cloudbuild-gate-remote.py: run the land-batch full gate on Cloud Build (LUCY-vxk2v, CI Phase 1).

    cloudbuild-gate-remote.py --scratch DIR [--config cloudbuild-gate.yaml] [--project aestheticc]
                              [--region global] [--poll 20] [--ceiling 2100]
                              [--service-account projects/aestheticc/serviceAccounts/cloudbuild-gate@...]

tier_gates.py plans this as the `cloudbuild-full` cmd when LAND_GATE_REMOTE is on; the SKILL
loop runs it under nohup in the scratch and writes cloudbuild-full-<sha>.exit/.log from its exit
code and stdout, exactly as for the local simulation. Needs PyYAML (tier_gates runs it with `uv run
--script`, which reads the inline metadata above); without it the config is refused, exit 2.

1. Refuse (exit 2) before any gcloud call unless the config passes an ALLOW-LIST (review round 1,
   P1-1: a text deny-list was bypassed by flow style, flag order, substitutions and anchors):
   it parses with a strict SafeLoader subclass that refuses duplicate and merge keys; its top-level keys are a subset of steps/timeout/options/
   substitutions; every step's name is node:<tag>; every entrypoint is bash/sh/npm/npx/node;
   no serviceAccount key anywhere; and the raw text (comments included, any case) never
   mentions gcloud, run.googleapis, docker, kubectl, metadata or 169.254.169.254. A file named
   cloudbuild.yaml (the deploy build) is refused by name.
2. Refuse (exit 2) unless the test-only gate service account exists (`gcloud iam
   service-accounts describe`); both lanes run as it, never as the default Cloud Build SA
   that can deploy (P2-1).
   The YAML loads with a strict SafeLoader: a repeated key at any depth, or a `<<` merge key,
   is refused (gcloud keeps a duplicate's first value, PyYAML its last). `options` keys are
   allow-listed too: machineType, logging, diskSizeGb only.
3. Submit both lanes (_LANE=unit, _LANE=pg) from DIR with --async, concurrently (one thread each);
   print each id and log URL as soon as it is known. stdout is flushed line by line.
4. Poll `gcloud builds describe` until both are terminal. Past the ceiling: cancel both, exit 137.
5. Fetch the lanes' `gcloud builds log` one after the other (LUCY-tdbtv: parallel reads hit the
   Cloud Logging read quota, HTTP 429, and gcloud exited with a partial log). A fetch that fails
   (non-zero exit, 429 or other error text) is retried with exponential backoff (FETCH_DELAYS).
   For a FAILURE lane whose failed step is a jest step, a log without that step's "Test Suites:"
   line is incomplete and is retried the same way; still incomplete, it prints "LOG INCOMPLETE
   for <lane>: ..." (the exit code is unchanged: the build status is authoritative). A FAILURE
   lane also gets one server-side `gcloud logging read` of its "FAIL " lines, a second source of
   suite names. A SUCCESS lane prints a summary only (status, id, log URL, the jest
   "Test Suites:"/"Tests:"/"Time:" lines); any other lane its full log.
6. Exit 0 only if both SUCCESS; any FAILURE 1 (with a "FAILED LANE <lane>: <suites>" last line);
   otherwise TIMEOUT/INTERNAL_ERROR/CANCELLED/EXPIRED 137, which the SKILL treats as infra
   (rerun once, then HOLD). A gcloud that cannot run, or a submit that fails or prints no JSON,
   is infra too: 137, never 1 (P3-1). Every gcloud call has a GCLOUD_TIMEOUT (a hung call is
   infra). SIGTERM/SIGINT cancel every submitted lane, at most once each, and always exit 137
   (P3-2), within SIGNAL_JOIN_BOUND even when a submit is hung. Never retries a build (only log reads).
   Never touches traffic, never deploys.

Every gcloud call goes through one runner (`run_gcloud`); CLOUDBUILD_GATE_GCLOUD names another
binary, and tests inject a fake runner into main().
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

LANES = ("unit", "pg")
INFRA = {"TIMEOUT", "INTERNAL_ERROR", "CANCELLED", "EXPIRED"}
TERMINAL = {"SUCCESS", "FAILURE"} | INFRA
ALLOWED_TOP_LEVEL = {"steps", "timeout", "options", "substitutions"}
ALLOWED_ENTRYPOINTS = {"bash", "sh", "npm", "npx", "node"}
# No pool/workerPool (a private pool), env/secretEnv/volumes (inputs the allow-list cannot see).
ALLOWED_OPTIONS = {"machineType", "logging", "diskSizeGb"}
JEST_SUMMARY = re.compile(r"\b(?:Test Suites|Tests|Time):")
SAY_LOCK = threading.Lock()
STEP_IMAGE = re.compile(r"node:[A-Za-z0-9][A-Za-z0-9._-]*")
# Anywhere in the raw file, comments included, any case. A gate config has no reason to name any.
FORBIDDEN_TEXT = ("gcloud", "run.googleapis", "docker", "kubectl", "metadata", "169.254.169.254")
GATE_SA = "projects/aestheticc/serviceAccounts/cloudbuild-gate@aestheticc.iam.gserviceaccount.com"
JEST_FAIL = re.compile(r"\bFAIL\s+(.*)$")
TEST_PATH = re.compile(r"\S+\.(?:test|spec)\.[cm]?[jt]sx?\b")
EXIT_REFUSED, EXIT_FAILED, EXIT_INFRA = 2, 1, 137
EXEC_ERROR = 127  # run_gcloud's code when gcloud itself could not be executed
TIMED_OUT = 124  # run_gcloud's code when a gcloud call outlived GCLOUD_TIMEOUT
# Well over a slow (~2 min) source upload; a call still running then is hung, and hung is infra.
GCLOUD_TIMEOUT = 600
# How long the signal path waits for in-flight submits to return (and cancel their own build)
# before it kills their gcloud and exits 137 anyway.
SIGNAL_JOIN_BOUND = 20.0
# Log reads: one try, then a wait before each retry (exponential, ~10 s to ~120 s): 6 tries.
FETCH_DELAYS = (10, 20, 40, 80, 120)
FETCH_ERROR = re.compile(r"\b429\b|RESOURCE_EXHAUSTED|Quota exceeded|Too Many Requests|^ERROR:", re.M | re.I)
JEST_STEPS = ("unit-tests", "pg-tests", "mobile-tests")
_LIVE = set()  # gcloud processes still running, so the signal path can kill a hung one
_LIVE_LOCK = threading.Lock()


class Interrupted(Exception):
    """Raised by the SIGTERM/SIGINT handler so main() can cancel what it submitted."""


def run_gcloud(args):
    """The only place gcloud runs. Returns (returncode, stdout, stderr); never raises on exec."""
    binary = os.environ.get("CLOUDBUILD_GATE_GCLOUD") or "gcloud"
    try:
        proc = subprocess.Popen([binary, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as exc:  # FileNotFoundError, PermissionError, ...
        return EXEC_ERROR, "", f"could not execute {binary}: {exc}"
    with _LIVE_LOCK:
        _LIVE.add(proc)
    try:
        out, err = proc.communicate(timeout=GCLOUD_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return TIMED_OUT, "", f"gcloud {' '.join(args[:2])} timed out after {GCLOUD_TIMEOUT}s"
    finally:
        with _LIVE_LOCK:
            _LIVE.discard(proc)
    return proc.returncode, out, err


def kill_live_gcloud():
    """Kill every gcloud call still running (the signal path's last resort for a hung submit)."""
    with _LIVE_LOCK:
        live = list(_LIVE)
    for proc in live:
        try:
            proc.kill()
        except OSError:
            pass
    return len(live)


def guarded(runner):
    """Any runner (the real one or an injected one) raising OSError reads as an exec error."""
    def call(args):
        try:
            return runner(args)
        except OSError as exc:
            return EXEC_ERROR, "", f"could not execute gcloud: {exc}"
    return call


def config_problems(config_path):
    """Why this config must not be submitted; [] only when it passes the allow-list."""
    path = Path(config_path)
    if path.name == "cloudbuild.yaml":
        return ["config is cloudbuild.yaml (the deploy build); the land gate runs cloudbuild-gate.yaml"]
    try:
        text = path.read_text()
    except OSError as exc:
        return [f"config unreadable: {exc}"]
    lowered = text.lower()
    problems = [f"the file mentions {word!r}" for word in FORBIDDEN_TEXT if word in lowered]
    try:
        import yaml
    except ImportError:
        return problems + ["PyYAML is not installed, so the config cannot be checked"]
    try:
        config = yaml.load(text, Loader=strict_loader(yaml))  # noqa: S506 (a SafeLoader subclass)
    except StrictYamlError as exc:
        return problems + [str(exc)]
    except yaml.YAMLError as exc:
        return problems + [f"config does not parse as YAML: {str(exc).splitlines()[0]}"]
    if not isinstance(config, dict):
        return problems + ["config is not a mapping"]
    problems += [f"top-level key {key!r} is not allowed (only {', '.join(sorted(ALLOWED_TOP_LEVEL))})"
                 for key in config if key not in ALLOWED_TOP_LEVEL]
    options = config.get("options", {})
    if not isinstance(options, dict):
        problems.append("options is not a mapping")
    else:
        problems += [f"options key {key!r} is not allowed (only {', '.join(sorted(ALLOWED_OPTIONS))})"
                     for key in options if key not in ALLOWED_OPTIONS]
    problems += [f"a serviceAccount key at {where} (the runner picks the build's account)"
                 for where in service_account_keys(config)]
    steps = config.get("steps")
    if not isinstance(steps, list) or not steps:
        return problems + ["no steps: list"]
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            problems.append(f"step {i} is not a mapping")
            continue
        name = step.get("name")
        if not isinstance(name, str) or not STEP_IMAGE.fullmatch(name):
            problems.append(f"step {i} name {name!r} is not a node:<tag> image")
        if "entrypoint" in step and step["entrypoint"] not in ALLOWED_ENTRYPOINTS:
            problems.append(f"step {i} entrypoint {step['entrypoint']!r} is not one of "
                            f"{', '.join(sorted(ALLOWED_ENTRYPOINTS))}")
    return problems


class StrictYamlError(Exception):
    """A config shape the two parsers could read differently (duplicate key, merge key)."""


def strict_loader(yaml):
    """A SafeLoader that refuses a repeated key at any depth, and refuses `<<` merge keys.

    PyYAML keeps the LAST value of a repeated key; gcloud's ruamel loader keeps the FIRST, so the
    allow-list could approve one image while gcloud runs another (review round 2, P3-R2-1). A merge
    key is refused outright: its override semantics are the same split by another name, and a gate
    config has no need for one. Anchors and aliases still resolve.
    """
    merge_tag = "tag:yaml.org,2002:merge"

    class StrictSafeLoader(yaml.SafeLoader):
        def flatten_mapping(self, node):
            for key_node, _ in node.value:
                if key_node.tag == merge_tag:
                    raise StrictYamlError(f"merge key '<<' at line {key_node.start_mark.line + 1} "
                                          "is not allowed (gcloud and PyYAML can resolve it differently)")
            super().flatten_mapping(node)

        def construct_mapping(self, node, deep=False):
            if isinstance(node, yaml.MappingNode):
                self.flatten_mapping(node)
                seen = {}
                for key_node, _ in node.value:
                    key = self.construct_object(key_node, deep=deep)
                    try:
                        first = seen.get(key)
                    except TypeError:
                        continue  # unhashable; the base constructor raises its own error
                    if first is not None:
                        raise StrictYamlError(
                            f"duplicate key {key!r} at line {key_node.start_mark.line + 1} (first at line "
                            f"{first}): gcloud keeps the first value and PyYAML the last, so refused")
                    seen[key] = key_node.start_mark.line + 1
            return super().construct_mapping(node, deep=deep)

    return StrictSafeLoader


def service_account_keys(node, where="config"):
    """Paths of every serviceAccount key (any case) at any depth."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{where}.{key}"
            if isinstance(key, str) and key.lower() == "serviceaccount":
                found.append(here)
            found += service_account_keys(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += service_account_keys(value, f"{where}[{i}]")
    return found


def sa_email(service_account):
    return service_account.rsplit("/", 1)[-1]


def sa_resource(service_account, project):
    if service_account.startswith("projects/"):
        return service_account
    return f"projects/{project}/serviceAccounts/{service_account}"


def sa_check(runner, service_account, project):
    """None if the gate SA exists; otherwise (exit code, message)."""
    code, _, err = runner(["iam", "service-accounts", "describe", sa_email(service_account),
                           f"--project={project}", "--format=json"])
    if code in (EXEC_ERROR, TIMED_OUT):  # "could not check" is infra, never "SA missing"
        return EXIT_INFRA, f"GCLOUD UNAVAILABLE: {err.strip()[-500:]}"
    if code != 0:
        return EXIT_REFUSED, (f"REFUSED: gate service account {sa_email(service_account)} not found in "
                              f"{project} (the lanes never run as the default Cloud Build account): "
                              f"{err.strip()[-500:]}")
    return None


def submit(runner, scratch, config, lane, project, region, service_account):
    """Submit one lane async. Returns (build_id, log_url) or raises RuntimeError."""
    code, out, err = runner(["builds", "submit", str(scratch), f"--config={config}",
                             f"--substitutions=_LANE={lane}", "--async", "--format=json",
                             f"--service-account={service_account}",
                             f"--project={project}", f"--region={region}"])
    if code != 0:
        raise RuntimeError(f"submit {lane} exited {code}: {err.strip()[-500:]}")
    try:
        build = json.loads(out)
    except ValueError as exc:
        raise RuntimeError(f"submit {lane} printed no JSON: {exc}") from exc
    build = (build.get("metadata") or {}).get("build", build) if isinstance(build, dict) else {}
    if not isinstance(build, dict) or not build.get("id"):
        raise RuntimeError(f"submit {lane} returned no build id")
    return build["id"], build.get("logUrl", "")


def describe(runner, build_id, project, region):
    """The build resource, or None when gcloud could not read it (polled again next tick)."""
    code, out, _ = runner(["builds", "describe", build_id, "--format=json",
                           f"--project={project}", f"--region={region}"])
    if code != 0:
        return None
    try:
        build = json.loads(out)
    except ValueError:
        return None
    return build if isinstance(build, dict) else None


def cancel(runner, build_id, project, region):
    runner(["builds", "cancel", build_id, f"--project={project}", f"--region={region}"])


def failed_suites(log_text):
    """Jest suite paths from "FAIL <path>" lines, in first-seen order."""
    suites = []
    for line in log_text.splitlines():
        match = JEST_FAIL.search(line)
        path = TEST_PATH.search(match.group(1)) if match else None
        if path and path.group(0) not in suites:
            suites.append(path.group(0))
    return suites


def failed_steps(build):
    return [s.get("id") or s.get("name", "?") for s in (build or {}).get("steps", [])
            if s.get("status") == "FAILURE"]


def exit_code(statuses):
    if all(s == "SUCCESS" for s in statuses):
        return 0
    if any(s == "FAILURE" for s in statuses):
        return EXIT_FAILED  # a real red wins over the other lane's infra state
    return EXIT_INFRA


def _interrupt(signum, _frame):
    raise Interrupted(signal.Signals(signum).name)


class LaneState:
    """Submitted builds, shared by the submit threads and the main thread's signal path.

    `stopped` and `builds` change only under `lock`, so every submitted build is cancelled: either
    it is in the main thread's snapshot when the stop is set, or its submit thread sees `stopped`
    when it records the id and cancels the build itself. Every cancel goes through cancel_once,
    so no path (signal, submit error, ceiling, or a signal during one of those) cancels twice.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.builds, self.urls, self.errors = {}, {}, {}
        self.cancelled = set()
        self.stopped = False
        self.threads = []

    def stop(self):
        with self.lock:
            self.stopped = True
            return dict(self.builds)

    def cancel_once(self, call, build_id, args):
        """Cancel build_id unless some path already has. Claimed before the call, so at most once."""
        with self.lock:
            if build_id in self.cancelled:
                return
            self.cancelled.add(build_id)
        cancel(call, build_id, args.project, args.region)

    def join_submits(self, bound):
        """Wait up to `bound` seconds (real time) for the submit threads that started.

        A thread whose start() was cut short by the signal never ran, so it is skipped rather than
        joined (joining it raises RuntimeError, which would exit 1, a red). Returns the threads
        still running at the bound.
        """
        deadline = time.monotonic() + bound
        alive = []
        for thread in list(self.threads):
            while True:
                try:
                    thread.join(max(0.0, min(0.05, deadline - time.monotonic())))
                except RuntimeError:  # never started
                    break
                if not thread.is_alive() or time.monotonic() >= deadline:
                    break
            if thread.is_alive():
                alive.append(thread)
        return alive


def main(argv=None, runner=run_gcloud, sleep=time.sleep, clock=time.monotonic, out=None):
    out = out or sys.stdout

    def say(*parts):
        with SAY_LOCK:  # lines from the submit threads never interleave
            print(*parts, file=out, flush=True)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--config", default="cloudbuild-gate.yaml")
    ap.add_argument("--project", default="aestheticc")
    ap.add_argument("--region", default="global")
    ap.add_argument("--poll", type=float, default=20)
    ap.add_argument("--ceiling", type=float, default=2100)
    ap.add_argument("--service-account", default=GATE_SA,
                    help="the test-only account both lanes run as (resource name or email)")
    args = ap.parse_args(argv)

    scratch = Path(args.scratch).resolve()
    config = scratch / args.config
    problems = config_problems(config)
    if problems:
        for problem in problems:
            say(f"REFUSED {config}: {problem}")
        return EXIT_REFUSED

    call = guarded(runner)
    state = LaneState()
    sigs = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.signal(sig, _interrupt) for sig in sigs}
    try:
        return _run(args, scratch, config, call, say, sleep, clock, state)
    except Interrupted as exc:
        for sig in sigs:  # a second signal must not cut the cancels short
            signal.signal(sig, signal.SIG_IGN)
        submitted = state.stop()
        say(f"INTERRUPTED by {exc}; cancelling {len(submitted)} submitted lane build(s)")
        for build_id in submitted.values():
            state.cancel_once(call, build_id, args)
        # An in-flight submit cancels its own build when it returns; a hung one must not hold the
        # exit, so the wait is bounded and its gcloud is killed after it.
        hung = state.join_submits(SIGNAL_JOIN_BOUND)
        if hung:
            killed = kill_live_gcloud()
            say(f"{len(hung)} submit(s) still running after {SIGNAL_JOIN_BOUND:.0f}s "
                f"({', '.join(t.name for t in hung)}); killed {killed} gcloud call(s)")
            state.join_submits(2.0)
        return EXIT_INFRA
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _submit_lane(call, scratch, config, lane, args, service_account, state, say):
    try:
        build_id, log_url = submit(call, scratch, config, lane, args.project, args.region, service_account)
    except Exception as exc:  # noqa: BLE001 (any submit error is infra, never a red)
        with state.lock:
            state.errors[lane] = exc
        return
    with state.lock:
        state.builds[lane], state.urls[lane] = build_id, log_url
        late = state.stopped
    if late:
        state.cancel_once(call, build_id, args)
        say(f"LANE {lane} build {build_id} submitted after the stop; cancelled")
    else:
        say(f"LANE {lane} build {build_id} {log_url}")


def fetch_failed(result):
    """Why this gcloud read did not complete, or None. Error text counts even on exit 0."""
    code, _, err = result
    if code != 0:
        return f"exit {code}: {(err or '').strip()[-300:]}"
    match = FETCH_ERROR.search(err or "")
    return f"error text: {(err or '').strip()[-300:]}" if match else None


def has_jest_summary(log_text, step):
    """A complete jest step's log has its own "Test Suites:" line."""
    prefix = re.compile(r'^Step #\d+ - "' + re.escape(step) + r'":.*\bTest Suites:', re.M)
    return bool(prefix.search(log_text or ""))


def fetch_with_retry(call, args, sleep, complete=None):
    """Run a gcloud read, retrying a failed (or, per `complete`, incomplete) one with backoff.

    Returns (result, problem): the last complete result and None, or, once the tries are spent,
    the most complete result seen (longest stdout) and why it is not complete. A partial read is
    never returned as complete.
    """
    best, problem = None, None
    for attempt in range(len(FETCH_DELAYS) + 1):
        if attempt:
            sleep(FETCH_DELAYS[attempt - 1])
        result = call(args)
        problem = fetch_failed(result)
        if problem is None and complete is not None:
            problem = complete(result[1] or "")
        if problem is None:
            return result, None
        if best is None or len(result[1] or "") >= len(best[1] or ""):
            best = result
    return best, problem


def filtered_fail_lines(call, args, build_id, sleep):
    """The build's "FAIL " lines, read server-side from Cloud Logging (a second source of suites)."""
    log_filter = (f'resource.type="build" AND resource.labels.build_id="{build_id}" '
                  f'AND textPayload:"FAIL "')
    result, _ = fetch_with_retry(call, ["logging", "read", log_filter, f"--project={args.project}",
                                        "--format=value(textPayload)", "--limit=2000"], sleep)
    return (result[1] or "") if fetch_failed(result) is None else ""


def lane_summary(log_text):
    """The jest summary lines of a green lane's log."""
    return [line for line in log_text.splitlines() if JEST_SUMMARY.search(line)]


def _run(args, scratch, config, call, say, sleep, clock, state):
    refusal = sa_check(call, args.service_account, args.project)
    if refusal:
        code, message = refusal
        say(message)
        return code
    service_account = sa_resource(args.service_account, args.project)

    # Both source uploads at once (they took ~85 s each, one after the other).
    for lane in LANES:
        # Daemon, so a hung submit can never hold the process open after main() returns 137.
        # Tracked before start(): a signal inside start() may still leave it running, and
        # join_submits skips one that never started.
        thread = threading.Thread(target=_submit_lane, name=f"submit-{lane}", daemon=True,
                                  args=(call, scratch, config, lane, args, service_account, state, say))
        state.threads.append(thread)
        thread.start()
    for thread in state.threads:
        while thread.is_alive():  # a short timeout keeps the main thread responsive to signals
            thread.join(0.05)
    if state.errors:
        for lane in LANES:
            if lane in state.errors:
                say(f"SUBMIT FAILED: {state.errors[lane]}")
        for started in state.stop().values():
            state.cancel_once(call, started, args)
        return EXIT_INFRA
    builds = state.builds

    started, final = clock(), {}
    while len(final) < len(LANES):
        for lane in LANES:
            if lane not in final:
                build = describe(call, builds[lane], args.project, args.region)
                if build and build.get("status") in TERMINAL:
                    final[lane] = build
        if len(final) == len(LANES):
            break
        if clock() - started > args.ceiling:
            say(f"CEILING {args.ceiling:.0f}s passed; cancelling both builds")
            for lane in LANES:
                state.cancel_once(call, builds[lane], args)
            return EXIT_INFRA
        sleep(args.poll)

    logs, extra_suites = {}, {}
    for lane in LANES:  # one after the other: parallel reads trip the Cloud Logging read quota
        status = final[lane]["status"]
        jest_steps = [s for s in failed_steps(final[lane]) if s in JEST_STEPS] if status == "FAILURE" else []

        def complete(text, jest_steps=jest_steps):
            missing = [s for s in jest_steps if not has_jest_summary(text, s)]
            return f"no jest summary for {', '.join(missing)}" if missing else None
        (_, log_text, log_err), problem = fetch_with_retry(
            call, ["builds", "log", builds[lane], f"--project={args.project}", f"--region={args.region}"],
            sleep, complete)
        logs[lane] = log_text or ""
        url = state.urls.get(lane) or final[lane].get("logUrl", "")
        say(f"===== LANE {lane} build {builds[lane]} status {status} =====")
        if status == "SUCCESS":  # a green lane's full log is noise; the URL keeps it one click away
            say(f"log: {url}")
            summary = lane_summary(logs[lane])
            say("\n".join(summary) if summary else "(no jest summary lines in the log)")
        else:
            say(logs[lane].rstrip() or f"(no log: {(log_err or '').strip()[-300:]})")
        if problem:
            lines = len(logs[lane].splitlines())
            missing = [s for s in jest_steps if not has_jest_summary(logs[lane], s)]
            if missing:
                say(f"LOG INCOMPLETE for {lane}: {lines} lines fetched, no jest summary for "
                    f"{', '.join(missing)}; see {url}")
            else:
                say(f"LOG INCOMPLETE for {lane}: {lines} lines fetched, fetch failed after "
                    f"{len(FETCH_DELAYS) + 1} tries ({problem}); see {url}")
        if status == "FAILURE":
            extra_suites[lane] = failed_suites(filtered_fail_lines(call, args, builds[lane], sleep))

    statuses = [final[lane]["status"] for lane in LANES]
    code = exit_code(statuses)
    say(f"RESULT {' '.join(f'{lane}={s}' for lane, s in zip(LANES, statuses))} exit {code}")
    for lane in LANES:
        if final[lane]["status"] in INFRA:
            say(f"INFRA LANE {lane}: {final[lane]['status']}")
    for lane in LANES:  # last, so the evidence log's final lines name the red suites
        if final[lane]["status"] == "FAILURE":
            suites = failed_suites(logs[lane])
            suites += [s for s in extra_suites.get(lane, []) if s not in suites]
            named = suites or failed_steps(final[lane]) or ["unknown"]
            say(f"FAILED LANE {lane}: {' '.join(named)}")
    return code


if __name__ == "__main__":
    # Line-buffered even when redirected, so the build id/URL lines reach the evidence file at once.
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
