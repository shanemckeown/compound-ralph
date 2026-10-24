#!/usr/bin/env bash
# review-gate.sh <bead> <full-tip-sha> --lead L --verdict V... --out-dir D --round N [--cleared-escalation ID]... [--dry-run]
# The sanctioned review-gate step (LUCY-p4pjf): runs review_lead_check.py on untouched reviewer
# files and, only when it prints BLOCKING=0 ESCALATE=0, writes the Ops/reviews gate file and the
# `PASS @<tip>` bead comment. Logic and refusals: references/review-lead/review_gate.py
set -euo pipefail
# Runs the committed copy through the live export, never the checkout (LUCY-wqn96). No
# fallback to the checkout: until install.sh has run, this refuses.
RUN_GATE="$HOME/.claude/.git/live/run-gate"
if [ ! -x "$RUN_GATE" ]; then
  echo "run-gate not installed: run bash ~/.claude/scripts/git-hooks/install.sh ~/.claude (orchestrator, right after merge)" >&2
  exit 3
fi
exec "$RUN_GATE" references/review-lead/review_gate.py "$@"
