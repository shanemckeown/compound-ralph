#!/usr/bin/env python3
"""tier_gates.py: the push-time gate set for one LAND batch, chosen by risk tier (LUCY-z3ykz.2).

    tier_gates.py plan --scratch DIR --base SHA --out FILE.json
    tier_gates.py link-node-modules --scratch DIR --repo REPO

`plan` diffs the combined batch tree (the scratch HEAD with every green merge) against the
pinned base, classifies it with ~/.claude/scripts/risk_tier.py, and writes the tier, its
reasons and the ordered push-time gate list to --out. The agent never picks the tier: the
script does, from the paths. One full candidate makes the whole batch full, because the
combined diff contains its paths.

Per-merge scoped gates (tsc, scoped lint, related jest) run for every tier in Step 2 and are
not listed here. The push-time gates are:
  light: none. The scoped gates already ran on every merge.
  full:  the full pg lane, then the Cloud Build simulation (steps #0 and #3; step #2 IS the
         pg lane, so it is not run twice). Strictly one after the other, each under the
         laptop-wide pg-lane lock: run concurrently on 12 Oct they produced 9 false pg
         failures (evidence/land-batch/20261012-0u6v0/cloudbuild-run1-contended.log).

`link-node-modules` symlinks the trunk's node_modules into a fresh land scratch. A scratch
without it fails the prettier pre-commit hook with ENOENT on its first commit.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

RISK_TIER = Path(__file__).resolve().parents[3] / "scripts" / "risk_tier.py"
_spec = importlib.util.spec_from_file_location("risk_tier", RISK_TIER)
risk_tier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(risk_tier)

PG_LOCK = 'python3 "$HOME/.claude/scripts/pg-lane-lock.py" --'
FULL_GATES = (
    {"name": "full-pg-lane",
     "cmd": f"{PG_LOCK} env TZ=UTC RUN_PG_TESTS=true npx jest --selectProjects pg-tests"},
    {"name": "cloudbuild-step0",
     "cmd": f"{PG_LOCK} bash scripts/ci/simulate-cloudbuild-test.sh --step 0"},
    {"name": "cloudbuild-step3",
     "cmd": f"{PG_LOCK} bash scripts/ci/simulate-cloudbuild-test.sh --step 3"},
)


def gates_for(tier):
    """Ordered push-time gates. Run in list order, one at a time; stop at the first red."""
    return [dict(g) for g in FULL_GATES] if tier == "full" else []


def plan(paths):
    tier, reasons = risk_tier.classify(paths)
    return {"tier": tier, "reasons": reasons, "sequential": True, "gates": gates_for(tier)}


def link_node_modules(scratch, repo):
    """Make scratch/node_modules point at repo/node_modules. Returns the link path."""
    link = Path(scratch) / "node_modules"
    if not link.exists() and not link.is_symlink():
        os.symlink(Path(repo) / "node_modules", link)
    return link


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--scratch", required=True)
    p.add_argument("--base", required=True)
    p.add_argument("--out", required=True)
    n = sub.add_parser("link-node-modules")
    n.add_argument("--scratch", required=True)
    n.add_argument("--repo", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "link-node-modules":
        print(link_node_modules(args.scratch, args.repo))
        return 0
    result = plan(risk_tier.diff_paths(args.scratch, args.base))
    result["base"] = args.base
    result["head"] = subprocess.run(["git", "-C", args.scratch, "rev-parse", "HEAD"],
                                    check=True, capture_output=True, text=True).stdout.strip()
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"tier": result["tier"], "gates": [g["name"] for g in result["gates"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
