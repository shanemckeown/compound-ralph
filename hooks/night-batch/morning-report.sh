#!/usr/bin/env bash
# morning-report.sh — render night-batch state markers into SHANE_TODO.md.
# Bead: LUCY-fef4
#
# Reads .complete / .guarded / .reviewed markers from state/, gathers per-bead
# context (diff, commits, blind-claude review, PR), appends a structured
# section to SHANE_TODO.md, then archives processed markers to state/processed/.
#
# Triggered by:
#   1. night-batch.sh at end of run (every overnight orchestrator exit)
#   2. ~/Library/LaunchAgents/com.aestheticc.morning-report.plist at 07:00 BST
#      as a safety net (catches anything orchestrator missed)

set -u
ROOT="$HOME/.claude/hooks/night-batch"
STATE="$ROOT/state"
PROCESSED="$STATE/processed/$(date +%Y-%m-%d)"
TODO="/Users/shane/Documents/Obsidian/Aestheticc/Ops/Hermes/SHANE_TODO.md"
LOG="$ROOT/logs/morning-report.log"
WORKTREE_BASE="$HOME/.worktrees/AestheticcNext"
export BEADS_DIR="${BEADS_DIR:-/Users/shane/Documents/Obsidian/.beads}"

mkdir -p "$PROCESSED" "$(dirname "$LOG")"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Collect markers
shopt -s nullglob
complete_files=("$STATE"/*.complete)
guarded_files=("$STATE"/*.guarded)
reviewed_files=("$STATE"/*.reviewed)

complete_count=${#complete_files[@]}
guarded_count=${#guarded_files[@]}
reviewed_count=${#reviewed_files[@]}
total=$((complete_count + guarded_count + reviewed_count))

if [[ $total -eq 0 ]]; then
  log "no markers to report — exiting"
  exit 0
fi

log "rendering report: $total beads ($complete_count complete, $reviewed_count reviewed, $guarded_count guarded)"

# Helper: bead title via bd show, fallback to ID if bd unreachable
bead_title() {
  local bead="$1"
  local title=$(bd show "$bead" 2>/dev/null | head -1 | sed -E 's/^[^·]*·[[:space:]]*//; s/[[:space:]]*\[●.*$//')
  echo "${title:-$bead}"
}

# Helper: render one bead block. Args: bead, status, marker_file
render_bead() {
  local bead="$1"
  local status="$2"   # complete | guarded | reviewed
  local title=$(bead_title "$bead")
  local worktree="$WORKTREE_BASE/$bead"
  local branch="auto/$bead"
  local emoji
  local action
  case "$status" in
    complete) emoji="🟢"; action="Push + merge: \`cd $worktree && git push -u origin $branch && gh pr create --base main\`";;
    reviewed) emoji="🟡"; action="Read blind-claude review (BLOCKERs flagged), decide fix or discard";;
    guarded)  emoji="🔴"; action="Investigate why claude went off-track (forbidden path / oversize / no diff)";;
  esac

  echo ""
  echo "### $emoji LUCY-${bead#LUCY-} — $title"
  echo "**Status:** \`.$status\`"

  if [[ -d "$worktree" ]]; then
    local base=$(git -C "$worktree" merge-base HEAD main 2>/dev/null)
    if [[ -n "$base" ]]; then
      local stat=$(git -C "$worktree" diff --shortstat "$base"..HEAD 2>/dev/null | xargs)
      [[ -n "$stat" ]] && echo "**Diff:** $stat" || echo "**Diff:** (empty)"
      echo "**Commits:**"
      git -C "$worktree" log --oneline "$base"..HEAD 2>/dev/null | head -10 | sed 's/^/  - /'
    fi
  else
    echo "**Worktree:** _(missing — auto-bead.sh failed to create)_"
  fi

  # Surface blind-claude verdict + any BLOCKER lines inline
  local review_file="$worktree/.compound-review/blind-claude-${bead}.txt"
  if [[ -f "$review_file" ]]; then
    local summary=$(grep -E '^SUMMARY:' "$review_file" | head -1)
    local b=$(grep -cE '^\[BLOCKER\]' "$review_file")
    local c=$(grep -cE '^\[CONCERN\]' "$review_file")
    local n=$(grep -cE '^\[NIT\]' "$review_file")
    echo "**Gate 3 (blind-claude):** ${summary:-no summary} — BLOCKERS=${b:-0} CONCERNS=${c:-0} NITS=${n:-0}"
    if [[ ${b:-0} -gt 0 ]]; then
      echo "  **BLOCKER lines:**"
      grep -E '^\[BLOCKER\]' "$review_file" | sed 's/^/    /'
    fi
    if [[ ${c:-0} -gt 0 ]]; then
      echo "  **CONCERN lines:**"
      grep -E '^\[CONCERN\]' "$review_file" | head -5 | sed 's/^/    /'
    fi
  fi

  # PR check (cheap — gh handles offline gracefully)
  local pr_url=$(gh pr list --head "$branch" --json url --jq '.[0].url' 2>/dev/null)
  if [[ -n "$pr_url" ]]; then
    echo "**PR:** $pr_url"
  else
    echo "**PR:** _(not pushed yet — sandbox or push failure; commits are local on \`$branch\`)_"
  fi

  echo "**Action:** $action"
  echo ""
}

# Build the report
report=$(cat <<EOF

---

## Overnight runs — $(date +"%Y-%m-%d %H:%M %Z")

**Summary:** $total bead(s) processed | 🟢 $complete_count ready to merge | 🟡 $reviewed_count needs review | 🔴 $guarded_count guard failed
EOF
)

# Order: complete first (good news), then reviewed (needs human look), then guarded (problems)
{
  echo "$report"
  for f in "${complete_files[@]}"; do
    bead=$(basename "$f" .complete)
    render_bead "$bead" "complete" "$f"
  done
  for f in "${reviewed_files[@]}"; do
    bead=$(basename "$f" .reviewed)
    render_bead "$bead" "reviewed" "$f"
  done
  for f in "${guarded_files[@]}"; do
    bead=$(basename "$f" .guarded)
    render_bead "$bead" "guarded" "$f"
  done
  echo "_Report log: \`~/.claude/hooks/night-batch/logs/morning-report.log\`_"
  echo ""
} >> "$TODO"

# Archive processed markers
for f in "${complete_files[@]}" "${reviewed_files[@]}" "${guarded_files[@]}"; do
  mv "$f" "$PROCESSED/" 2>/dev/null
done

log "appended report to SHANE_TODO.md and moved $total markers to $PROCESSED/"
echo "morning-report: $total bead(s) appended to $TODO"
