#!/usr/bin/env bash
# check-eligibility.sh — Gate 1 of night-batch pipeline.
# Validates a bead against CLAUDE.md HEADLESS MODE auto-eligible rules.
# Bead: LUCY-6zxw
#
# Usage: check-eligibility.sh <BEAD_ID>
# Exit: 0 = pass; non-zero = fail with reason on stderr.
# Output: brief reason on stderr + machine-readable verdict on stdout.

set -u
BEAD="${1:-}"
if [[ -z "$BEAD" ]]; then
  echo "FAIL: missing bead id" >&2
  exit 2
fi

export BEADS_DIR="${BEADS_DIR:-/Users/shane/Documents/Obsidian/.beads}"

# Forbidden keywords (case-insensitive). Hits in title OR description fail.
FORBIDDEN_REGEX='(schema|migration|payment|refund|stripe|auth|RLS|multi-tenant|GDPR|deletion|secret|token|rotate|deploy|cron-changing|webhook|cloudbuild|terraform|infra)'

# Pull bead. bd show is the one source of truth. Check bd's own exit code
# rather than grepping output (descriptions can legitimately contain "not found").
SHOW=$(bd show "$BEAD" 2>&1)
bd_exit=$?
if [[ $bd_exit -ne 0 ]] || ! echo "$SHOW" | head -1 | grep -q "$BEAD"; then
  echo "FAIL: $BEAD not found in beads db (bd exit=$bd_exit)" >&2
  exit 1
fi

# Extract structured fields. bd show output is stable enough to grep.
status=$(echo "$SHOW" | head -1 | grep -oE 'OPEN|CLOSED|IN_PROGRESS|BLOCKED' | head -1)
# Type: lives mid-line on row 2 ("Owner: X · Type: Y · ..."). Match anywhere.
type=$(echo "$SHOW" | grep -oE 'Type: [a-z]+' | head -1 | awk '{print tolower($2)}')
priority=$(echo "$SHOW" | head -1 | grep -oE 'P[0-4]' | head -1)
title=$(echo "$SHOW" | head -1 | sed -E 's/.*[·•] //; s/  *\[.*//' | tr '[:upper:]' '[:lower:]')
labels=$(echo "$SHOW" | grep -E '^LABELS:' | sed 's/^LABELS: //' | tr -d ' ')
description=$(echo "$SHOW" | sed -n '/^DESCRIPTION$/,/^[A-Z][A-Z]*$/p' | tr '[:upper:]' '[:lower:]')

# 1. Status
if [[ "$status" != "OPEN" ]]; then
  echo "FAIL: status=$status (must be OPEN)" >&2
  exit 1
fi

# 2. Has auto-eligible label
if ! echo ",$labels," | grep -q ",auto-eligible,"; then
  echo "FAIL: missing auto-eligible label (have: $labels)" >&2
  exit 1
fi

# 3. Type
case "$type" in
  task|bug|chore) ;;
  *) echo "FAIL: type=$type (must be task/bug/chore)" >&2; exit 1;;
esac

# 4. Priority
case "$priority" in
  P2|P3|P4) ;;
  *) echo "FAIL: priority=$priority (must be P2/P3/P4)" >&2; exit 1;;
esac

# 5. Forbidden keywords in title
if echo "$title" | grep -iqE "$FORBIDDEN_REGEX"; then
  hit=$(echo "$title" | grep -oiE "$FORBIDDEN_REGEX" | head -1)
  echo "FAIL: forbidden keyword in title: '$hit'" >&2
  exit 1
fi

# 6. Forbidden keywords in description
if echo "$description" | grep -iqE "$FORBIDDEN_REGEX"; then
  hit=$(echo "$description" | grep -oiE "$FORBIDDEN_REGEX" | head -1)
  echo "FAIL: forbidden keyword in description: '$hit'" >&2
  exit 1
fi

# 7. Not the parent of any open bead (shouldn't be an epic-style container)
# bd show CHILDREN section lists open children — if any open, this is a container.
children_open=$(echo "$SHOW" | sed -n '/^CHILDREN$/,/^[A-Z][A-Z]*$/p' | grep -cE '^  ↳ ○')
if [[ ${children_open:-0} -gt 0 ]]; then
  echo "FAIL: has $children_open open children — looks like a container, not a leaf task" >&2
  exit 1
fi

echo "PASS: $BEAD eligible (type=$type prio=$priority labels=$labels)"
exit 0
