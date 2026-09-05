#!/usr/bin/env bash
# verify-sensitive-file-merge.sh — detect content loss on a clean (non-conflicted)
# merge, for a fixed list of version/changelog-shaped files.
#
# Usage: verify-sensitive-file-merge.sh <scratch-worktree> <merge-sha>
#
# Motivation (AestheticcNext-cozxa, 2026-09-05): git reported
# "Auto-merging package.json" with zero CONFLICT markers, but the merge
# RESULT silently reverted two real security fixes back to the vulnerable
# state, because an intervening commit had touched the same lines between
# the source branch's fork point and the merge — git's line-based 3-way
# merge has no semantic understanding of the file and resolved the
# ambiguity wrong without any warning. See SKILL.md Hard guardrail 10.
#
# For each sensitive file the source branch actually touched (relative to
# the merge-base), this checks:
#   - every line the source branch's own diff ADDED is present somewhere
#     in the merge result ("DROPPED ADDITION" if not)
#   - every line the source branch's own diff REMOVED is absent from the
#     merge result ("REVERTED REMOVAL" if still present)
#
# Deliberately narrow: presence/absence only (no positional check — a
# CHANGELOG.md entry landing in a different position is fine), fixed file
# list only, not a general merge-conflict rebuild. A hit here does not mean
# the merge is definitely wrong (a later, intentional edit to the same line
# is possible) — it means a human needs to look. Exit 1 with one line per
# violation on stdout if anything is flagged; silent exit 0 if clean.

set -euo pipefail

SCRATCH="${1:?usage: verify-sensitive-file-merge.sh <scratch-worktree> <merge-sha>}"
MERGE_SHA="${2:?usage: verify-sensitive-file-merge.sh <scratch-worktree> <merge-sha>}"
SENSITIVE_FILES=(package.json VERSION CHANGELOG.md)

PARENT1="${MERGE_SHA}^1"
PARENT2="${MERGE_SHA}^2"

# Not a real two-parent merge (e.g. fast-forward) — nothing to reconcile.
if ! git -C "$SCRATCH" rev-parse -q --verify "$PARENT2" >/dev/null 2>&1; then
  exit 0
fi

BASE_SHA="$(git -C "$SCRATCH" merge-base "$PARENT1" "$PARENT2")"
FOUND=0

for f in "${SENSITIVE_FILES[@]}"; do
  # Skip files the source branch didn't touch relative to the merge-base —
  # this failure mode only applies where two sides both changed the file.
  if git -C "$SCRATCH" diff --quiet "$BASE_SHA" "$PARENT2" -- "$f" 2>/dev/null; then
    continue
  fi

  MERGED_CONTENT="$(git -C "$SCRATCH" show "${MERGE_SHA}:${f}" 2>/dev/null || true)"
  if [ -z "$MERGED_CONTENT" ]; then
    continue
  fi

  DIFF_OUTPUT="$(git -C "$SCRATCH" diff "$BASE_SHA" "$PARENT2" -- "$f" 2>/dev/null || true)"

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    if ! grep -qxF -- "$line" <<< "$MERGED_CONTENT"; then
      printf 'DROPPED ADDITION in %s: %s\n' "$f" "$line"
      FOUND=1
    fi
  done < <(grep '^+[^+]' <<< "$DIFF_OUTPUT" | sed 's/^+//')

  while IFS= read -r line; do
    [ -z "$line" ] && continue
    if grep -qxF -- "$line" <<< "$MERGED_CONTENT"; then
      printf 'REVERTED REMOVAL in %s: %s\n' "$f" "$line"
      FOUND=1
    fi
  done < <(grep '^-[^-]' <<< "$DIFF_OUTPUT" | sed 's/^-//')
done

exit "$FOUND"
