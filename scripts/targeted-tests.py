#!/usr/bin/env python3
"""Builder test gate: run only the suites related to what this branch changed (LUCY-w6fpi).

    targeted-tests.py [--base origin/main] [--dry-run] [--no-pg]

Run from an AestheticcNext worktree root. Changed files = diff from the merge-base
with --base, plus staged, unstaged and untracked files. Then:
  1. gated jest --findRelatedTests <changed>                (node + dom projects)
  2. RUN_PG_TESTS=true gated jest --selectProjects pg-tests --findRelatedTests <changed>
     (only the pg suites that import the changed code, or were edited themselves)

Builders never run the full pg lane: it runs once on the combined landing tree
(land-batch, serialised by pg-lane-lock.py) and again in Cloud Build. If the related
pg set is at least half the lane (e.g. lib/schema.ts touched) it takes the same lock.
Exit code is the first failing step's.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
GATED = [sys.executable, str(SCRIPTS / "gated-test-run.py")]
LOCK = [sys.executable, str(SCRIPTS / "pg-lane-lock.py"), "--"]
# .json: fixtures imported by tests (tests/fixtures/*.json) are in jest's graph too.
SOURCE_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")
# A related pg set this big (share of the whole lane) is effectively a full lane.
PG_FULL_FRACTION = float(os.environ.get("PG_FULL_FRACTION", "0.5"))


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def changed_files(base):
    names = set(git("diff", "--name-only", f"{base}...HEAD").split())
    names |= set(git("diff", "--name-only", "HEAD").split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    sources = {n for n in names if n.endswith(SOURCE_EXT)}
    present = {n for n in sources if Path(n).is_file()}
    # A deleted module has no graph node left, so jest can't find its importers
    # (e.g. a pg-only helper tsc never checks). Select the tests that still name it.
    return sorted(present | tests_naming(sources - present))


def tests_naming(deleted):
    found = set()
    for name in deleted:
        stem = Path(name).stem
        if stem.endswith(".d"):
            continue
        hits = subprocess.run(["git", "grep", "-l", "-F", stem, "--", "tests"],
                              capture_output=True, text=True).stdout.split()
        found |= {h for h in hits if ".test." in h and Path(h).is_file()}
    return found


def pg_env():
    return {**os.environ, "RUN_PG_TESTS": "true", "TZ": "UTC"}


def plan(files, pg_suites, pg_total=0):
    """The commands to run. Pure, so tests can assert on it."""
    related = ["--findRelatedTests", *files, "--passWithNoTests"]
    steps = [("related jest", [*GATED, "jest", *related], None)]
    if pg_suites is not None:
        pg = [*GATED, "jest", "--selectProjects", "pg-tests", *related]
        if pg_suites and len(pg_suites) >= PG_FULL_FRACTION * pg_total:
            pg = [*LOCK, *pg]  # effectively a full lane: serialise it
        steps.append(("related pg-tests", pg, pg_env()))
    return steps


def list_pg_suites(files=None):
    related = ["--findRelatedTests", *files] if files else []
    out = subprocess.run(
        ["node", "./node_modules/jest/bin/jest.js", "--selectProjects", "pg-tests",
         "--listTests", *related],
        capture_output=True, text=True, env=pg_env())
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        raise SystemExit(f"targeted-tests: listing related pg suites failed ({out.returncode})")
    return [line for line in out.stdout.splitlines() if line.strip().startswith("/")]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    parser.add_argument("--no-pg", action="store_true")
    args = parser.parse_args()
    files = changed_files(args.base)
    if not files:
        print("targeted-tests: no changed source files; nothing to run")
        return 0
    pg_suites, pg_total = (None if args.no_pg else []), 0
    if pg_suites is not None and not args.dry_run:
        pg_suites, pg_total = list_pg_suites(files), len(list_pg_suites())
        print(f"targeted-tests: {len(pg_suites)} of {pg_total} pg suite(s) related")
        if not pg_suites:
            pg_suites = None  # skip starting a container for nothing
    first_failure = 0
    for label, command, env in plan(files, pg_suites, pg_total):
        print(f"==> {label}: {' '.join(command)}", flush=True)
        if args.dry_run:
            continue
        code = subprocess.run(command, env=env).returncode
        print(f"<== {label}: exit {code}", flush=True)
        first_failure = first_failure or code
    return first_failure


if __name__ == "__main__":
    sys.exit(main())
