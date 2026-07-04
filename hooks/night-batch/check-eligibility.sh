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

export BEADS_DIR="${BEADS_DIR:-/Users/shane/Documents/GitReBase/AestheticcNext/.beads}"

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

# 2. Has any auto-* prefixed label (auto, auto-eligible, AUTO, etc).
# Pre-2026-05-01 this required the literal `auto-eligible` label, which
# nothing was tagged with. Shane was using `auto` + `scope:s/m/xs` instead;
# now any `auto*` label counts as the eligibility signal.
if ! echo ",$labels," | grep -qiE ',auto[a-z0-9-]*,'; then
  echo "FAIL: missing auto-* label (have: $labels)" >&2
  exit 1
fi

# 3. Type — task/bug/chore always OK; feature OK only when scope:xs or scope:s
# (scope:m/l features are too big for one auto-bead run; bead the slice instead)
case "$type" in
  task|bug|chore) ;;
  feature)
    if ! echo ",$labels," | grep -qE ',scope:(xs|s),'; then
      echo "FAIL: type=feature requires scope:xs or scope:s label (have: $labels)" >&2
      exit 1
    fi
    ;;
  *) echo "FAIL: type=$type (must be task/bug/chore or feature with scope:xs/s)" >&2; exit 1;;
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

# 8. Reject if the auto/<BEAD> branch already exists and represents work that's
# either merged or explicitly abandoned. 2026-04-29 fix for the overnight
# empty-diff loop: a bead labelled auto-eligible whose work is already done
# or rejected will keep producing Gate 2 fails until it's closed.
REPO="/Users/shane/Documents/GitReBase/AestheticcNext"
BRANCH="auto/$BEAD"
if [[ -d "$REPO/.git" ]]; then
  ( cd "$REPO" && git fetch origin --quiet 2>/dev/null ) || true

  if cd "$REPO" 2>/dev/null && git ls-remote --heads origin "$BRANCH" 2>/dev/null | grep -q "$BRANCH"; then
    # (a) all commits already merged → work done.
    unmerged=$(git rev-list --count "origin/$BRANCH" --not origin/main 2>/dev/null || echo 999)
    if [[ "${unmerged:-999}" -eq 0 ]]; then
      echo "FAIL: auto/$BEAD branch's commits are all merged into main — work done, close the bead" >&2
      exit 1
    fi

    # (b) PR was closed without merging → work rejected.
    if command -v gh >/dev/null 2>&1; then
      pr_info=$(gh pr list --repo shanemckeown/AestheticcNext \
        --head "$BRANCH" --state all \
        --json state,mergedAt --jq '.[0] // empty' 2>/dev/null || true)
      if [[ -n "$pr_info" ]]; then
        pr_state=$(echo "$pr_info" | grep -oE '"state":"[A-Z]+"' | cut -d'"' -f4)
        pr_merged=$(echo "$pr_info" | grep -oE '"mergedAt":(null|"[^"]+")' | cut -d: -f2)
        if [[ "$pr_state" == "CLOSED" && ( -z "$pr_merged" || "$pr_merged" == "null" ) ]]; then
          echo "FAIL: PR for auto/$BEAD was closed without merging — work rejected, close the bead or refile fresh" >&2
          exit 1
        fi
      fi
    fi
  fi
fi

echo "PASS: $BEAD eligible (type=$type prio=$priority labels=$labels)"
exit 0
