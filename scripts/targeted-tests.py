#!/usr/bin/env python3
"""Builder test gate: run only the suites related to what this branch changed (LUCY-w6fpi).

    targeted-tests.py [--base origin/main] [--dry-run] [--no-pg]

Run from an AestheticcNext worktree root. Changed paths = diff (no rename pairing, so
a rename is its deleted old path plus its new path) from the merge-base with --base,
plus staged, unstaged and untracked files. Then:
  1. node + dom: gated jest --findRelatedTests <changed sources>, with RUN_PG_TESTS
     removed from the environment so it can never pull in the pg project.
     It falls back to the BROAD set (full node + dom jest, the old builder gate) when
     any changed path is outside jest's import graph (SQL, config, package.json,
     scripts, a deleted module nothing names...) or when no related test exists.
     It never reports green on zero tests.
  2. pg: only the related pg suites, run by exact path (--runTestsByPath). If the
     related set is over the cap (default half the lane, e.g. lib/schema.ts touched),
     only the pg test files this branch edited run and the rest is logged as deferred.
     If even those are over the cap, they run under the pg-lane lock (attempt 3).

Builders never run the full pg lane: the full lane
runs once on the combined landing tree (/land-batch, serialised by pg-lane-lock.py)
and again in Cloud Build. Exit code is the first failing step's.

LUCY-ll8dh: the HEAVY steps (the BROAD node + dom set, or a pg set still over the cap)
do not run on the laptop. One builder-remote-qa.py step replaces them: both Cloud Build
lanes of the land gate on the committed HEAD (full node + dom + pg, lint, tsc, build),
falling back to the local lane itself on infra. A remaining related node/dom step stays
local. It needs a clean worktree (the remote run tests the committed sha only); dirty, or
BUILDER_QA_REMOTE=0, the heavy steps run locally exactly as before.
"""
import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
GATED = [sys.executable, str(SCRIPTS / "gated-test-run.py")]
JEST = ["node", "./node_modules/jest/bin/jest.js"]
GRAPH_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
# Read by tests at most by name; never change how the code behaves.
DOC_EXT = (".md", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".txt")
SKIP_PREFIX = (".beads/", ".goal/")
# Graph-shaped files that change how every test runs, not what one imports.
CONFIG_DIRS = ("tests/setup/", "tests/meta/", "drizzle/", "scripts/")
CONFIG_NAMES = ("jest.", "babel.", "next.config.", "tsconfig", "package.json",
                "package-lock.json", ".gcloudignore", ".babelrc")
# Stems too common to find the importers of a deleted file by name.
GENERIC_STEMS = {"index", "utils", "types", "route", "helpers", "constants", "config"}
PG_CAP_FRACTION = float(os.environ.get("PG_TARGET_MAX_FRACTION", "0.5"))
REMOTE_QA = SCRIPTS / "builder-remote-qa.py"
BROAD_LABEL = "broad jest (node + dom)"


def qa_remote():
    """builder-remote-qa.qa_remote(): BUILDER_QA_REMOTE has one source of truth."""
    spec = importlib.util.spec_from_file_location("builder_remote_qa", REMOTE_QA)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.qa_remote()


def worktree_clean():
    """No staged, unstaged or untracked change outside SKIP_PREFIX (not test inputs)."""
    lines = git("status", "--porcelain", "--untracked-files=all").splitlines()
    return not [line for line in lines if not line[3:].strip('"').startswith(SKIP_PREFIX)]


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changed_paths(base):
    names = set(git("diff", "--no-renames", "--name-only", f"{base}...HEAD").split())
    names |= set(git("diff", "--no-renames", "--name-only", "HEAD").split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(names)


def tests_naming(path):
    stem = Path(path).name.split(".")[0]
    if len(stem) < 4 or stem in GENERIC_STEMS:
        return None
    hits = subprocess.run(["git", "grep", "-l", "-F", stem, "--", "tests"],
                          capture_output=True, text=True).stdout.split()
    return {h for h in hits if ".test." in h and Path(h).is_file()}


def classify(paths):
    """Return (files for --findRelatedTests, reasons to run the broad node/dom set)."""
    related, broad = set(), []
    for path in paths:
        if path.startswith(SKIP_PREFIX):
            continue
        name = Path(path).name
        is_config = path.startswith(CONFIG_DIRS) or name.startswith(CONFIG_NAMES)
        if path.endswith(GRAPH_EXT) and not is_config:
            if Path(path).is_file():
                related.add(path)
                continue
            naming = tests_naming(path)  # deleted: no graph node left for jest to walk
            if naming:
                related |= naming
            else:
                broad.append(f"deleted {path}: no test names it")
        elif path.endswith(DOC_EXT) and not is_config:
            related |= tests_naming(path) or set()
        else:
            broad.append(f"{path}: outside the jest import graph")
            if not name.startswith(CONFIG_NAMES):  # "package" names half the suite
                related |= tests_naming(path) or set()  # e.g. a migration's pg test
    return sorted(related), broad


def node_env():
    env = {k: v for k, v in os.environ.items() if k != "RUN_PG_TESTS"}
    env.pop("PG_LANE_LOCK_TOKEN", None)
    return env


def pg_env():
    return {**node_env(), "RUN_PG_TESTS": "true", "TZ": "UTC"}


def plan(files, broad, node_related, pg_related, pg_total, touched_pg, remote_sha=None):
    """The (label, command, env) steps. Pure, so tests can assert on it.

    node_related: how many node/dom tests --findRelatedTests selects.
    pg_related / touched_pg: absolute pg suite paths; pg_related None = pg skipped.
    remote_sha: set (remote on, clean worktree) -> the heavy steps become one
    builder-remote-qa.py step on that sha; None -> today's all-local plan.
    """
    steps, notes = local_plan(files, broad, node_related, pg_related, pg_total, touched_pg)
    lock = str(SCRIPTS / "pg-lane-lock.py")
    heavy = [label for label, command, _ in steps if label == BROAD_LABEL or lock in command]
    if not remote_sha or not heavy:
        return steps, notes
    # The remote lanes run the full node + dom + pg sets: the broad and every pg step go.
    kept = [step for step in steps if step[0] == "related jest"]
    notes.append(f"heavy set ({', '.join(heavy)}) -> Cloud Build via builder-remote-qa.py on "
                 f"{remote_sha[:12]} (full node + dom + pg, lint, tsc, build; local fallback on infra)")
    command = [sys.executable, str(REMOTE_QA), "--sha", remote_sha]
    return kept + [("heavy set on Cloud Build", command, node_env())], notes


def local_plan(files, broad, node_related, pg_related, pg_total, touched_pg):
    """Today's all-local steps (BUILDER_QA_REMOTE=0, a dirty worktree, or nothing heavy)."""
    notes = []
    if broad or not files or node_related == 0:
        why = broad or ["no related node/dom test"]
        notes.append("node/dom: BROAD set (full node + dom jest): " + "; ".join(why))
        steps = [(BROAD_LABEL, [*GATED, "jest"], node_env())]
    else:
        steps = [("related jest", [*GATED, "jest", "--findRelatedTests", *files], node_env())]
    if pg_related is None:
        return steps, notes
    suites = pg_related
    if pg_total and len(pg_related) > PG_CAP_FRACTION * pg_total:
        touched = {os.path.realpath(t) for t in touched_pg}
        suites = [s for s in pg_related if os.path.realpath(s) in touched]
        notes.append(f"pg: {len(pg_related)} of {pg_total} suites related (over the cap); "
                     f"running the {len(suites)} this branch edited, the rest is deferred "
                     "to the landing full lane")
    if suites:
        command = [*GATED, "jest", "--selectProjects", "pg-tests", "--runTestsByPath", *suites]
        if pg_total and len(suites) > PG_CAP_FRACTION * pg_total:
            # Still over the cap (the branch edited that many pg files): as heavy as a
            # full lane, so it waits for the laptop-wide lock like one (attempt 3).
            command = [sys.executable, str(SCRIPTS / "pg-lane-lock.py"), "--", *command]
            notes.append(f"pg: the {len(suites)} edited suites are still over the cap; "
                         "running them under the pg-lane lock")
        steps.append(("related pg-tests", command, pg_env()))
    else:
        notes.append("pg: no pg suite to run here; covered by the landing full lane")
    return steps, notes


def list_tests(args, env):
    out = subprocess.run([*JEST, "--listTests", *args], capture_output=True, text=True, env=env)
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        raise SystemExit(f"targeted-tests: jest --listTests {' '.join(args[:3])} failed "
                         f"({out.returncode}); refusing to guess")
    return [line.strip() for line in out.stdout.splitlines() if line.strip().startswith("/")]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    parser.add_argument("--no-pg", action="store_true")
    args = parser.parse_args()
    paths = changed_paths(args.base)
    files, broad = classify(paths)
    related_flag = ["--findRelatedTests", *files]
    node_related = len(list_tests(related_flag, node_env())) if files and not broad else 0
    pg_related = pg_total = touched = None
    if not args.no_pg:
        pg = ["--selectProjects", "pg-tests"]
        pg_related = list_tests([*pg, *related_flag], pg_env()) if files else []
        pg_total = len(list_tests(pg, pg_env()))
        root = os.getcwd()
        touched = [os.path.join(root, p) for p in paths if Path(p).is_file()]
        print(f"targeted-tests: {len(pg_related)} of {pg_total} pg suite(s) related")
    remote_sha = None
    if qa_remote():
        if worktree_clean():
            remote_sha = git("rev-parse", "HEAD").strip()
        else:
            print("targeted-tests: worktree has uncommitted changes, so any heavy step runs "
                  "locally (the remote run tests the committed sha only; commit to use Cloud Build)")
    steps, notes = plan(files, broad, node_related, pg_related, pg_total or 0, touched or [],
                        remote_sha)
    for note in notes:
        print(f"targeted-tests: {note}", flush=True)
    first_failure = 0
    for label, command, env in steps:
        print(f"==> {label}: {' '.join(command)}", flush=True)
        if args.dry_run:
            continue
        code = subprocess.run(command, env=env).returncode
        print(f"<== {label}: exit {code}", flush=True)
        first_failure = first_failure or code
    return first_failure


if __name__ == "__main__":
    sys.exit(main())
