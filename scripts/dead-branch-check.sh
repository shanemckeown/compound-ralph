#!/usr/bin/env bash
# dead-branch-check.sh — list origin goal/* and long-goal/* branches whose bead is CLOSED
# but the branch was never merged to origin/main (e.g. closed SUPERSEDED / won't-fix).
# land-batch discovery keys on the branch, not the bead, so these resurface as "finished work"
# (frq8j, q29ok, 22 Oct 2026). Read-only: prints a table + the retire command, never deletes.
#
# Usage: dead-branch-check.sh [repo-dir]   (default ~/Documents/GitReBase/AestheticcNext)
# Exit: 0 none found, 1 dead branches found, 2 setup error.
set -uo pipefail
REPO="${1:-$HOME/Documents/GitReBase/AestheticcNext}"
cd "$REPO" 2>/dev/null || { echo "dead-branch-check: cannot cd $REPO" >&2; exit 2; }
command -v bd >/dev/null && command -v jq >/dev/null || { echo "dead-branch-check: need bd and jq" >&2; exit 2; }
git fetch -q origin 2>/dev/null || echo "dead-branch-check: fetch failed, using local refs" >&2
found=0
while read -r ref; do
  br="${ref#origin/}"
  bead="AestheticcNext-${br##*/aestheticcnext-}"
  # epic branches are long-goal/aestheticcnext-<id>; child suffixes like 1x8p.36 are kept as-is
  git merge-base --is-ancestor "$ref" origin/main 2>/dev/null && continue   # already landed
  row=$(bd show "$bead" --json 2>/dev/null | jq -r '.[0] | select(.status=="closed") | "\(.status)\t\(.close_reason // "no reason")"' 2>/dev/null)
  [ -z "$row" ] && continue
  ahead=$(git rev-list --count origin/main.."$ref")
  printf '%s\t%s\tahead=%s\t%s\n' "$bead" "$br" "$ahead" "${row#*$'\t'}"
  found=1
done < <(git for-each-ref --format='%(refname:short)' 'refs/remotes/origin/goal/aestheticcnext-*' 'refs/remotes/origin/long-goal/aestheticcnext-*')
if [ "$found" = 1 ]; then
  echo "--- closed bead + unmerged branch: retire it (git push origin --delete <branch>) or reopen the bead; do not land." >&2
  exit 1
fi
echo "dead-branch-check: none"
