#!/usr/bin/env python3
"""
gate-tier.py — B-7 (AI Fleet Grand Plan, 2026-09-01)

Classifies a proposed action into:
  - a consequence TIER (T0 hard-halt / T1 async-flag / T2 auto-proceed), and
  - a rigor CLASS (R0 clinical-money-tenancy-consent / R1 booking-comms-scheduling
    / R2 everything else)

from the changed file paths + the action being taken, so /land-batch, /goal,
and fleet-dispatch.py share ONE definition instead of each carrying its own
prose version of "is this sensitive."

Source: Aestheticc/Product/AI_FLEET_GRAND_PLAN_2026-09-01.md Part 3 Q5 (tier
table) and Q6 (rigor gradient), reusing goal.md Phase 0e's existing sensitive-
path list rather than inventing a second one.

Usage:
    gate-tier.py --action <action> --paths <file1> [<file2> ...]
    gate-tier.py --action <action> --paths-file <path-list.txt>
    echo -e "path1\\npath2" | gate-tier.py --action <action> --paths-stdin

    <action> one of: land, deploy-prod, migrate-apply, credential-rotate,
                      send-client-comms, dispatch, push

Output (stdout, one line, machine-parseable):
    TIER=<T0|T1|T2> RCLASS=<R0|R1|R2> REASON="<short human reason>"

Exit code is always 0 (this is a pure classifier, not a gate itself — the
caller decides what T0/T1/T2 means for its own flow). Never silently
misclassify: an action this script doesn't recognize is T0 (fail toward the
human, not around them).
"""
import argparse
import re
import sys

# --- Path classes -----------------------------------------------------------
# R0: clinical / money / tenancy / consent. Mirrors goal.md Phase 0e's list
# and the money-core cluster (H-9..H-14, n7ani.*) so this isn't a second,
# drifting copy of "what's sensitive here."
R0_PATTERNS = [
    r"^lib/payments/",
    r"^pages/api/webhooks/stripe\.ts$",
    r"^pages/api/payments/",
    r"^lib/glp1/",
    r"^lib/forms/consent",
    r"^lib/db/team-utils\.ts$",
    r"^lib/auth/",
    r"^drizzle/migrations/",
    r"^migrations/",
    r"schema\.ts$",
]

# R1: booking / comms / scheduling.
R1_PATTERNS = [
    r"^lib/booking/",
    r"^lib/scheduling/",
    r"^pages/api/public/book\.ts$",
    r"^lib/automation/",
    r"^lib/comms",
    r"^lib/email/",
    r"^lib/forms/",
]

# T0 / T1 signal actions — these are ACTIONS, not paths. Path-based tiering
# only ever escalates a T2 land to T1 (sensitive path) or above; it never
# invents a T0 on its own — T0 is reserved for actions with irreversible,
# real-world, client/money/credential consequence, decided by the ACTION,
# not by which files happened to change.
T0_ACTIONS = {
    "deploy-prod",
    "migrate-apply",
    "credential-rotate",
    "send-client-comms",  # a real email/SMS to a non-Shane client
    "money-movement",
}

T2_ACTIONS = {
    "dispatch",  # dispatching a headless-eligible bead
    "push",  # pushing a feature branch
    "read-only-query",
}


def classify_rclass(paths):
    for p in paths:
        for pat in R0_PATTERNS:
            if re.search(pat, p):
                return "R0", pat
    for p in paths:
        for pat in R1_PATTERNS:
            if re.search(pat, p):
                return "R1", pat
    return "R2", None


def classify_tier(action, rclass):
    if action in T0_ACTIONS:
        return "T0", f"action '{action}' is a hard-halt class (prod/migration/credential/client-comms/money)"
    if action == "land":
        if rclass in ("R0", "R1"):
            return "T1", f"sensitive-path land ({rclass}) — auto-lands to main, never prod; queued for the daily pending-review table, not a per-branch prompt"
        return "T2", "non-sensitive land — auto-proceed"
    if action in T2_ACTIONS:
        return "T2", f"action '{action}' never blocks"
    # Unknown action: fail toward the human, not around them.
    return "T0", f"unrecognized action '{action}' — treated as hard-halt until this script knows about it"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--action", required=True)
    parser.add_argument("--paths", nargs="*", default=[])
    parser.add_argument("--paths-file")
    parser.add_argument("--paths-stdin", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = list(args.paths)
    if args.paths_file:
        with open(args.paths_file) as f:
            paths += [line.strip() for line in f if line.strip()]
    if args.paths_stdin:
        paths += [line.strip() for line in sys.stdin if line.strip()]

    rclass, rmatch = classify_rclass(paths)
    tier, reason = classify_tier(args.action, rclass)

    if args.json:
        import json as _json
        print(_json.dumps({
            "tier": tier, "rclass": rclass,
            "reason": reason, "matched_pattern": rmatch,
            "action": args.action, "path_count": len(paths),
        }))
    else:
        print(f'TIER={tier} RCLASS={rclass} REASON="{reason}"')


if __name__ == "__main__":
    main()
