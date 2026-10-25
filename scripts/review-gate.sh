#!/usr/bin/env bash
# review-gate.sh <bead> <full-tip-sha> --lead L --verdict V... --out-dir D --round N [--cleared-escalation ID]... [--cleared-comment 'YYYY-MM-DD HH:MM:HEX']... [--dry-run]
# The sanctioned review-gate step (LUCY-p4pjf): runs review_lead_check.py on untouched reviewer
# files and, only when it prints BLOCKING=0 ESCALATE=0, writes the Ops/reviews gate file and the
# `PASS @<tip>` bead comment. Logic and refusals: ~/.claude/references/review-lead/review_gate.py
set -euo pipefail
exec python3 "$HOME/.claude/references/review-lead/review_gate.py" "$@"
