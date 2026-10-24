#!/usr/bin/env bash
# review-gate.sh <bead> <full-tip-sha> --lead L --verdict V... --out-dir D --round N [--cleared-escalation ID]... [--dry-run]
# The sanctioned review-gate step (LUCY-p4pjf): runs review_lead_check.py on untouched reviewer
# files and, only when it prints BLOCKING=0 ESCALATE=0, writes the Ops/reviews gate file and the
# `PASS @<tip>` bead comment. Logic and refusals: references/review-lead/review_gate.py
set -euo pipefail
# Runs the committed copy through the live export, never the checkout (LUCY-wqn96).
exec "$HOME/.claude/.git/live/run-gate" references/review-lead/review_gate.py "$@"
