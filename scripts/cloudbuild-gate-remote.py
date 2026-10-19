#!/usr/bin/env python3
"""cloudbuild-gate-remote.py: run the land-batch full gate on Cloud Build (LUCY-vxk2v, CI Phase 1).

    cloudbuild-gate-remote.py --scratch DIR [--config cloudbuild-gate.yaml] [--project aestheticc]
                              [--region global] [--poll 20] [--ceiling 2100]

tier_gates.py plans this as the `cloudbuild-full` cmd when LAND_GATE_REMOTE is on; the SKILL
loop runs it under nohup in the scratch and writes cloudbuild-full-<sha>.exit/.log from its exit
code and stdout, exactly as for the local simulation.

1. Refuse (exit 2) before any submit if the config is named cloudbuild.yaml, has a top-level
   images:/artifacts: key, or any step mentions docker push, gcloud run, update-traffic,
   --to-latest or deploy. This file is the guard, not the config's name (Fable review risk 3).
2. Submit both lanes (_LANE=unit, _LANE=pg) from DIR with --async; print ids and log URLs first.
3. Poll `gcloud builds describe` until both are terminal. Past the ceiling: cancel both, exit 137.
4. Append each lane's full `gcloud builds log`, headed by lane and status.
5. Exit 0 only if both SUCCESS; any FAILURE 1 (with a "FAILED LANE <lane>: <suites>" last line);
   otherwise TIMEOUT/INTERNAL_ERROR/CANCELLED/EXPIRED 137, which the SKILL treats as infra
   (rerun once, then HOLD). Never retries. Never touches traffic, never deploys.

Every gcloud call goes through one runner (`run_gcloud`); CLOUDBUILD_GATE_GCLOUD names another
binary, and tests inject a fake runner into main().
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

LANES = ("unit", "pg")
INFRA = {"TIMEOUT", "INTERNAL_ERROR", "CANCELLED", "EXPIRED"}
TERMINAL = {"SUCCESS", "FAILURE"} | INFRA
FORBIDDEN = ("docker push", "gcloud run", "update-traffic", "--to-latest", "deploy")
TOP_LEVEL_FORBIDDEN = re.compile(r"^(images|artifacts)\s*:", re.M)
TOP_LEVEL_KEY = re.compile(r"^[A-Za-z_][\w-]*\s*:")
# Builder steps split a command into args (`entrypoint: gcloud`, `args: ['run', ...]`), so the
# phrase scan alone misses them. A gate runs tests in plain images: no deploy-capable builder.
BUILDER = re.compile(r"^[\s-]*(name|entrypoint)\s*:\s*['\"]?([^'\"\s#]+)", re.M)
DEPLOY_BUILDERS = {"gcloud", "docker", "cloud-sdk", "kubectl", "gke-deploy", "gsutil"}
WORD = re.compile(r"[a-z0-9_.@/-]+")
JEST_FAIL = re.compile(r"\bFAIL\s+(.*)$")
TEST_PATH = re.compile(r"\S+\.(?:test|spec)\.[cm]?[jt]sx?\b")
EXIT_REFUSED, EXIT_FAILED, EXIT_INFRA = 2, 1, 137


def run_gcloud(args):
    """The only place gcloud runs. Returns (returncode, stdout, stderr)."""
    binary = os.environ.get("CLOUDBUILD_GATE_GCLOUD") or "gcloud"
    proc = subprocess.run([binary, *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def config_problems(config_path):
    """Why this config must not be submitted; [] when it is a test-only gate config."""
    path = Path(config_path)
    if path.name == "cloudbuild.yaml":
        return ["config is cloudbuild.yaml (the deploy build); the land gate runs cloudbuild-gate.yaml"]
    try:
        text = path.read_text()
    except OSError as exc:
        return [f"config unreadable: {exc}"]
    problems = [f"top-level {m.group(1)}: key (a gate config publishes nothing)"
                for m in TOP_LEVEL_FORBIDDEN.finditer(text)]
    steps = steps_text(text)
    if steps is None:
        problems.append("no top-level steps: key")
    else:
        lowered = steps.lower()
        problems += [f"a step mentions {word!r}" for word in FORBIDDEN if word in lowered]
        if "push" in WORD.findall(lowered):
            problems.append("a step passes a 'push' arg")
        for key, value in BUILDER.findall(steps):
            builder = value.rsplit("/", 1)[-1].split(":", 1)[0].split("@", 1)[0].lower()
            if builder in DEPLOY_BUILDERS:
                problems.append(f"a step's {key} is the {builder} builder")
    return problems


def steps_text(text):
    """The steps: block (to the next top-level key), full-line comments dropped. None if absent."""
    lines, inside, seen = [], False, False
    for line in text.splitlines():
        if TOP_LEVEL_KEY.match(line):
            inside = line.split(":", 1)[0].strip() == "steps"
            seen = seen or inside
            if inside:
                lines.append(line.split(":", 1)[1])
            continue
        if inside and not line.lstrip().startswith("#"):
            lines.append(line)
    return "\n".join(lines) if seen else None


def submit(runner, scratch, config, lane, project, region):
    """Submit one lane async. Returns (build_id, log_url) or raises RuntimeError."""
    code, out, err = runner(["builds", "submit", str(scratch), f"--config={config}",
                             f"--substitutions=_LANE={lane}", "--async", "--format=json",
                             f"--project={project}", f"--region={region}"])
    if code != 0:
        raise RuntimeError(f"submit {lane} exited {code}: {err.strip()[-500:]}")
    try:
        build = json.loads(out)
    except ValueError as exc:
        raise RuntimeError(f"submit {lane} printed no JSON: {exc}") from exc
    build = (build.get("metadata") or {}).get("build", build) if isinstance(build, dict) else {}
    if not build.get("id"):
        raise RuntimeError(f"submit {lane} returned no build id")
    return build["id"], build.get("logUrl", "")


def describe(runner, build_id, project, region):
    """The build resource, or None when gcloud could not read it (polled again next tick)."""
    code, out, _ = runner(["builds", "describe", build_id, "--format=json",
                           f"--project={project}", f"--region={region}"])
    if code != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


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


def main(argv=None, runner=run_gcloud, sleep=time.sleep, clock=time.monotonic, out=None):
    out = out or sys.stdout
    say = lambda *parts: print(*parts, file=out, flush=True)  # noqa: E731
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--config", default="cloudbuild-gate.yaml")
    ap.add_argument("--project", default="aestheticc")
    ap.add_argument("--region", default="global")
    ap.add_argument("--poll", type=float, default=20)
    ap.add_argument("--ceiling", type=float, default=2100)
    args = ap.parse_args(argv)

    scratch = Path(args.scratch).resolve()
    config = scratch / args.config
    problems = config_problems(config)
    if problems:
        for problem in problems:
            say(f"REFUSED {config}: {problem}")
        return EXIT_REFUSED

    builds = {}
    for lane in LANES:
        try:
            builds[lane], log_url = submit(runner, scratch, config, lane, args.project, args.region)
        except RuntimeError as exc:
            say(f"SUBMIT FAILED: {exc}")
            for started in builds.values():
                cancel(runner, started, args.project, args.region)
            return EXIT_INFRA
        say(f"LANE {lane} build {builds[lane]} {log_url}")

    started, final = clock(), {}
    while len(final) < len(LANES):
        for lane in LANES:
            if lane not in final:
                build = describe(runner, builds[lane], args.project, args.region)
                if build and build.get("status") in TERMINAL:
                    final[lane] = build
        if len(final) == len(LANES):
            break
        if clock() - started > args.ceiling:
            say(f"CEILING {args.ceiling:.0f}s passed; cancelling both builds")
            for lane in LANES:
                cancel(runner, builds[lane], args.project, args.region)
            return EXIT_INFRA
        sleep(args.poll)

    logs = {}
    for lane in LANES:
        status = final[lane]["status"]
        _, log_text, log_err = runner(["builds", "log", builds[lane],
                                       f"--project={args.project}", f"--region={args.region}"])
        logs[lane] = log_text or ""
        say(f"===== LANE {lane} build {builds[lane]} status {status} =====")
        say(logs[lane].rstrip() or f"(no log: {log_err.strip()[-300:]})")

    statuses = [final[lane]["status"] for lane in LANES]
    code = exit_code(statuses)
    say(f"RESULT {' '.join(f'{lane}={s}' for lane, s in zip(LANES, statuses))} exit {code}")
    for lane in LANES:
        if final[lane]["status"] in INFRA:
            say(f"INFRA LANE {lane}: {final[lane]['status']}")
    for lane in LANES:  # last, so the evidence log's final lines name the red suites
        if final[lane]["status"] == "FAILURE":
            named = failed_suites(logs[lane]) or failed_steps(final[lane]) or ["unknown"]
            say(f"FAILED LANE {lane}: {' '.join(named)}")
    return code


if __name__ == "__main__":
    sys.exit(main())
