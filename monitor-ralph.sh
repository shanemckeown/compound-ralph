#!/bin/bash
#
# monitor-ralph.sh - Monitor a running Ralph loop
#
# Usage: ./monitor-ralph.sh BEAD_ID
#

PROJECT_NAME="${PROJECT_NAME:-AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/$PROJECT_NAME}"

BEAD_ID="${1:-}"

if [[ -z "$BEAD_ID" ]]; then
  echo "Usage: ./monitor-ralph.sh BEAD_ID" >&2
  exit 1
fi

WORKTREE_PATH="$WORKTREE_BASE/$BEAD_ID"
COMPOUND_DIR="$WORKTREE_PATH/.compound"
PRD_FILE="$COMPOUND_DIR/prd.json"
STATUS_FILE="$COMPOUND_DIR/status.md"
LOG_FILE="$COMPOUND_DIR/logs/latest.log"

if [[ ! -d "$WORKTREE_PATH" ]]; then
  echo "❌ Worktree not found: $WORKTREE_PATH" >&2
  exit 1
fi

clear

echo "╔════════════════════════════════════════════════════════════╗"
echo "║  Ralph Monitor: $BEAD_ID"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Show status
echo "📊 STATUS"
echo "─────────"
if [[ -f "$STATUS_FILE" ]]; then
  head -20 "$STATUS_FILE"
else
  echo "No status file found"
fi

echo ""
echo "📋 TASK PROGRESS"
echo "────────────────"
if [[ -f "$PRD_FILE" ]]; then
  jq -r '.tasks[] | (if .passes then "✓" else "○" end) + " " + .id + ": " + .title' "$PRD_FILE"
  echo ""
  TOTAL=$(jq '.tasks | length' "$PRD_FILE")
  DONE=$(jq '[.tasks[] | select(.passes == true)] | length' "$PRD_FILE")
  echo "Progress: $DONE / $TOTAL tasks"
else
  echo "No prd.json found"
fi

echo ""
echo "📝 RECENT LOG OUTPUT"
echo "────────────────────"
if [[ -f "$LOG_FILE" ]]; then
  tail -30 "$LOG_FILE"
else
  echo "No log file found"
fi

echo ""
echo "─────────────────────────────────────────────────────────────"
echo "Commands:"
echo "  Tail log:     tail -f $LOG_FILE"
echo "  Stop loop:    touch $COMPOUND_DIR/STOP"
echo "  Attach tmux:  tmux attach -t ralph-$BEAD_ID"
echo ""
