#!/usr/bin/env python3
"""tier_gates.py: the push-time gate set for one LAND batch, chosen by risk tier (LUCY-z3ykz.2).

    tier_gates.py plan --scratch DIR --base SHA --out FILE.json
    tier_gates.py link-node-modules --scratch DIR --repo REPO
    tier_gates.py check --plan FILE.json --head SHA --evidence-dir DIR   # push-time verdict

`plan` diffs the combined batch tree (the scratch HEAD with every green merge) against the
pinned base, classifies it with ~/.claude/scripts/risk_tier.py, and writes the tier, its
reasons and the ordered push-time gate list to --out. The agent never picks the tier: the
script does, from the paths. One full candidate makes the whole batch full, because the
combined diff contains its paths.

Per-merge scoped gates (scoped lint, related jest) run per candidate in Step 2 and are not
listed here. The push-time gates, once on the combined tree, in list order, one at a time:
  every tier: full-project tsc; plus tests/config-effect when a beads export changed (it
         reads KNOWN_GAPS beads, so the export is a test input even in a UI batch).
  full:  then cloudbuild-full: two Cloud Build lanes of cloudbuild-gate.yaml via
         ~/.claude/scripts/cloudbuild-gate-remote.py (LUCY-vxk2v). It is the default since
         LUCY-nd0r5: no laptop pg lane runs for a land. LAND_GATE_REMOTE=0 (Cloud Build
         outage) does not fall back to the laptop: the gate exits 75 and the land holds.

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

GATED = 'python3 "$HOME/.claude/scripts/gated-test-run.py"'
TSC = {"name": "tsc", "cmd": f"{GATED} tsc-heavy --noEmit"}
BEADS_SUITE = {"name": "beads-config-effect",
               "cmd": f"{GATED} jest --runTestsByPath tests/config-effect/coverage.config-effect.test.ts"}
# LUCY-vxk2v: the gate runs on Cloud Build (two lanes of cloudbuild-gate.yaml). Nothing runs on
# the laptop, so no pg-lane lock. The SKILL loop runs the cmd in the scratch, hence `--scratch .`.
# `uv run --script` reads the script's inline metadata (PyYAML for its allow-list; system python3
# has none). Without PyYAML the script refuses with exit 2; it never falls back to a text scan.
CLOUDBUILD = {"name": "cloudbuild-full",
              "cmd": 'uv run --script "$HOME/.claude/scripts/cloudbuild-gate-remote.py" --scratch .'}
# LUCY-nd0r5: LAND_GATE_REMOTE=0 is the Cloud Build outage switch. It holds the land (exit 75,
# a whole-batch HOLD in the SKILL); it never runs the old laptop simulation in its place.
# A subshell: the SKILL wrapper evals the cmd, and a bare exit would end the wrapper before it
# writes the exit file (own-gate review round 1).
HELD_EXIT = 75
CLOUDBUILD_HELD_CMD = ("(echo 'HELD: LAND_GATE_REMOTE=0 (Cloud Build gate switched off); no laptop "
                       f"pg lane runs in its place. Unset it once Cloud Build is back.' >&2; exit {HELD_EXIT})")
# LAND_GATE_REMOTE=0 holds, =1 or unset (or any other value) runs on Cloud Build.
REMOTE_DEFAULT = True


def gate_remote(env=None):
    value = (os.environ if env is None else env).get("LAND_GATE_REMOTE", "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return REMOTE_DEFAULT


def cloudbuild_gate(env=None):
    gate = dict(CLOUDBUILD)
    if not gate_remote(env):
        gate["cmd"] = CLOUDBUILD_HELD_CMD
    return gate


def gates_for(tier, paths=(), env=None):
    """Ordered push-time gates. Run in list order, one at a time; stop at the first red."""
    gates = [TSC]
    if any(p.strip() in risk_tier.BEADS_EXPORTS for p in paths):
        gates.append(BEADS_SUITE)
    if tier == "full":
        gates.append(cloudbuild_gate(env))
    return [dict(g) for g in gates]


def plan(paths, repo=None, env=None):
    tier, reasons = risk_tier.classify(paths, repo)
    return {"tier": tier, "reasons": reasons, "sequential": True, "gates": gates_for(tier, paths, env),
            "cloudbuild_remote": gate_remote(env)}


def push_problems(tier_plan, head, evidence_dir):
    """Why a push of ``head`` must be held; [] only when every required gate ran green on it.

    LUCY-z3ykz.8: a plan with a matching head and ``gates: []`` used to pass the SKILL loop
    (nothing to check, so nothing red). The minimum is enforced here, not trusted from the file:
    tsc always, and the Cloud Build simulation whenever the tier is full (or unknown).
    """
    if not isinstance(tier_plan, dict):
        return ["tier plan unreadable"]
    problems = []
    tier = tier_plan.get("tier")
    if tier not in ("light", "full"):
        problems.append(f"unknown tier {tier!r} (treated as full)")
    gates = tier_plan.get("gates")
    if not isinstance(gates, list) or not gates:
        problems.append("gate list empty or missing")
        gates = []
    names = [g.get("name") for g in gates if isinstance(g, dict)]
    required = [TSC["name"]] + ([] if tier == "light" else [CLOUDBUILD["name"]])
    problems += [f"required gate {name} missing from the plan" for name in required if name not in names]
    if not head or tier_plan.get("head") != head:
        problems.append(f"plan head {tier_plan.get('head')!r} is not the tree to push {head!r}")
    for name in names:
        try:
            code = (Path(evidence_dir) / f"{name}-{head}.exit").read_text().strip()
        except OSError:
            code = "missing"
        if code != "0":
            problems.append(f"{name}:{code}")
    return problems


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
    c = sub.add_parser("check", help="exit 0 only if every required gate is green on --head")
    c.add_argument("--plan", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--evidence-dir", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "link-node-modules":
        print(link_node_modules(args.scratch, args.repo))
        return 0
    if args.cmd == "check":
        try:
            tier_plan = json.loads(Path(args.plan).read_text())
        except (OSError, ValueError):
            tier_plan = None
        problems = push_problems(tier_plan, args.head, args.evidence_dir)
        for problem in problems:
            print(problem)
        return 1 if problems else 0
    result = plan(risk_tier.diff_paths(args.scratch, args.base), args.scratch)
    result["base"] = args.base
    result["head"] = subprocess.run(["git", "-C", args.scratch, "rev-parse", "HEAD"],
                                    check=True, capture_output=True, text=True).stdout.strip()
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"tier": result["tier"], "gates": [g["name"] for g in result["gates"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
