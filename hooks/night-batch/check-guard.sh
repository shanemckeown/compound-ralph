#!/usr/bin/env bash
# check-guard.sh — Gate 2 of night-batch pipeline.
# Run AFTER ralph-loop completes inside a worktree, BEFORE PR creation.
# Aborts if any forbidden path was touched OR diff size exceeds the cap.
# Bead: LUCY-tir0
#
# Usage: check-guard.sh <BEAD_ID> <WORKTREE_PATH>
# Exit: 0 = pass; non-zero = fail with reason on stderr.

set -u
BEAD="${1:-}"
WORKTREE="${2:-}"
if [[ -z "$BEAD" || -z "$WORKTREE" ]]; then
  echo "FAIL: usage: $0 <BEAD_ID> <WORKTREE_PATH>" >&2
  exit 2
fi

if [[ ! -d "$WORKTREE/.git" ]] && [[ ! -f "$WORKTREE/.git" ]]; then
  echo "FAIL: $WORKTREE is not a git worktree" >&2
  exit 2
fi

# Forbidden path prefixes. Touching any of these aborts the run.
FORBIDDEN_PATHS=(
  "lib/db/"
  "drizzle/migrations/"
  "drizzle/meta/"
  "lib/stripe/"
  "lib/auth/"
  "lib/payments/"
  "pages/api/auth/"
  "pages/api/admin/"
  "pages/api/webhooks/"
  "lib/email/templates/"
  "scripts/migrate"
  ".github/workflows/"
  "cloudbuild"
  "terraform/"
  "infra/"
)

# Hard diff-size cap (lines added + removed). Slightly above the 200-LOC
# eligibility doc to allow small overruns.
MAX_DIFF_LOC=250

cd "$WORKTREE" || { echo "FAIL: cannot cd to $WORKTREE" >&2; exit 2; }

# Determine the merge-base against main.
BASE=$(git merge-base HEAD main 2>/dev/null || git merge-base HEAD origin/main 2>/dev/null || echo "")
if [[ -z "$BASE" ]]; then
  echo "FAIL: cannot find merge-base against main/origin/main" >&2
  exit 2
fi

# Empty-diff check — promoted from soft-pass to fail. A bead that produces
# no diff is a failed bead: claude bailed early, hallucinated "complete," or
# misread the task. We need this caught explicitly so the morning report can
# surface "tried, did nothing" as distinct from "tried, succeeded with PR."
TOUCHED=$(git diff --name-only "$BASE"..HEAD 2>/dev/null)
if [[ -z "$TOUCHED" ]]; then
  echo "FAIL: empty diff — bead produced no work (claude likely bailed early or misread the task)" >&2
  exit 1
fi

violations=()
for path in $TOUCHED; do
  for forbidden in "${FORBIDDEN_PATHS[@]}"; do
    if [[ "$path" == ${forbidden}* ]]; then
      violations+=("$path matches forbidden prefix '$forbidden'")
      break
    fi
  done
done

if [[ ${#violations[@]} -gt 0 ]]; then
  echo "FAIL: forbidden path(s) touched:" >&2
  for v in "${violations[@]}"; do echo "  - $v" >&2; done
  exit 1
fi

# Diff-size check
STATS=$(git diff --shortstat "$BASE"..HEAD 2>/dev/null)
# Output looks like: " 3 files changed, 42 insertions(+), 7 deletions(-)"
added=$(echo "$STATS" | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+' || echo 0)
removed=$(echo "$STATS" | grep -oE '[0-9]+ deletion' | grep -oE '[0-9]+' || echo 0)
total=$((added + removed))

if [[ $total -gt $MAX_DIFF_LOC ]]; then
  echo "FAIL: diff size $total LOC (added=$added, removed=$removed) exceeds cap $MAX_DIFF_LOC" >&2
  echo "FILES:" >&2
  echo "$TOUCHED" | sed 's/^/  /' >&2
  exit 1
fi

echo "PASS: $BEAD diff clean ($total LOC across $(echo "$TOUCHED" | wc -l | tr -d ' ') files, no forbidden paths)"
exit 0
