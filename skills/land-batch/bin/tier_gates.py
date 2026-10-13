#!/usr/bin/env python3
"""tier_gates.py: the push-time gate set for one LAND batch, chosen by risk tier (LUCY-z3ykz.2).

    tier_gates.py plan --scratch DIR --base SHA --out FILE.json
    tier_gates.py link-node-modules --scratch DIR --repo REPO

`plan` diffs the combined batch tree (the scratch HEAD with every green merge) against the
pinned base, classifies it with ~/.claude/scripts/risk_tier.py, and writes the tier, its
reasons and the ordered push-time gate list to --out. The agent never picks the tier: the
script does, from the paths. One full candidate makes the whole batch full, because the
combined diff contains its paths.

Per-merge scoped gates (scoped lint, related jest) run per candidate in Step 2 and are not
listed here. The push-time gates, once on the combined tree, in list order, one at a time:
  every tier: full-project tsc; plus tests/config-effect when a beads export changed (it
         reads KNOWN_GAPS beads, so the export is a test input even in a UI batch).
  full:  then the whole Cloud Build simulation (steps #0, #2 and #3). Its step #2 IS the
         full pg lane, run inside the upload tarball so a fixture .gcloudignore drops fails
         here and not in Cloud Build. One command under the laptop-wide pg-lane lock, never
         beside another pg lane: run concurrently on 12 Oct they produced 9 false pg
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
GATED = 'python3 "$HOME/.claude/scripts/gated-test-run.py"'
TSC = {"name": "tsc", "cmd": f"{GATED} tsc-heavy --noEmit"}
BEADS_SUITE = {"name": "beads-config-effect",
               "cmd": f"{GATED} jest --runTestsByPath tests/config-effect/coverage.config-effect.test.ts"}
CLOUDBUILD = {"name": "cloudbuild-full",
              # env -u: an inherited SKIP_PG_TESTS=1 would silently drop step #2 (the pg lane).
              "cmd": f"{PG_LOCK} env -u SKIP_PG_TESTS bash scripts/ci/simulate-cloudbuild-test.sh"}


def gates_for(tier, paths=()):
    """Ordered push-time gates. Run in list order, one at a time; stop at the first red."""
    gates = [TSC]
    if any(p.strip() in risk_tier.BEADS_EXPORTS for p in paths):
        gates.append(BEADS_SUITE)
    if tier == "full":
        gates.append(CLOUDBUILD)
    return [dict(g) for g in gates]


def plan(paths, repo=None):
    tier, reasons = risk_tier.classify(paths, repo)
    return {"tier": tier, "reasons": reasons, "sequential": True, "gates": gates_for(tier, paths)}


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
    result = plan(risk_tier.diff_paths(args.scratch, args.base), args.scratch)
    result["base"] = args.base
    result["head"] = subprocess.run(["git", "-C", args.scratch, "rev-parse", "HEAD"],
                                    check=True, capture_output=True, text=True).stdout.strip()
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"tier": result["tier"], "gates": [g["name"] for g in result["gates"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
