#!/bin/bash
#
# cancel-ralph.sh - Stop a running Ralph loop
#
# Usage: ./cancel-ralph.sh BEAD_ID
#

PROJECT_NAME="${PROJECT_NAME:-AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/$PROJECT_NAME}"

BEAD_ID="${1:-}"

if [[ -z "$BEAD_ID" ]]; then
  echo "Usage: ./cancel-ralph.sh BEAD_ID" >&2
  exit 1
fi

WORKTREE_PATH="$WORKTREE_BASE/$BEAD_ID"
COMPOUND_DIR="$WORKTREE_PATH/.compound"
STOP_FILE="$COMPOUND_DIR/STOP"

echo "🛑 Stopping Ralph loop for $BEAD_ID"

# Create stop file
touch "$STOP_FILE"
echo "  ✓ Stop file created: $STOP_FILE"

# Check for tmux session
SESSION_NAME="ralph-$BEAD_ID"
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo ""
  read -p "  Kill tmux session '$SESSION_NAME'? (y/n) " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    tmux kill-session -t "$SESSION_NAME"
    echo "  ✓ tmux session killed"
  else
    echo "  → Session still running. It will stop at next iteration check."
  fi
fi

echo ""
echo "Done. Loop will stop gracefully at next iteration check."
