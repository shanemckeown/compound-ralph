#!/bin/bash
#
# ralph-bead.sh - Main entry point for autonomous bead execution
#
# Usage: ./ralph-bead.sh BEAD_ID [OPTIONS]
#
# This script orchestrates the full bead execution:
# 1. Setup (interactive): Create worktree, generate tasks
# 2. Loop (headless): Execute tasks autonomously
# 3. Complete (interactive): Create PR, close bead
#

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Import sub-scripts
SETUP_SCRIPT="$SCRIPT_DIR/setup-bead.sh"
LOOP_SCRIPT="$SCRIPT_DIR/ralph-loop.sh"
COMPLETE_SCRIPT="$SCRIPT_DIR/complete-bead.sh"

# Project configuration
PROJECT_NAME="${PROJECT_NAME:-AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/$PROJECT_NAME}"

# ============================================================================
# Argument Parsing
# ============================================================================

show_help() {
  cat << 'EOF'
ralph-bead.sh - Autonomous bead execution

USAGE:
  ./ralph-bead.sh BEAD_ID [OPTIONS]

ARGUMENTS:
  BEAD_ID              The bead ID to execute (e.g., LUCY-1234)

OPTIONS:
  --max-iterations N   Max iterations for the loop (default: 50)
  --skip-setup         Skip setup phase (use existing worktree)
  --skip-complete      Skip completion phase (no PR)
  --background         Run in background tmux session
  --dry-run            Show what would be done without doing it
  --verbose            Show Claude output in real-time
  -h, --help           Show this help

EXAMPLES:
  # Full execution
  ./ralph-bead.sh LUCY-1234

  # Run in background (walk away)
  ./ralph-bead.sh LUCY-1234 --background

  # Resume an existing setup
  ./ralph-bead.sh LUCY-1234 --skip-setup

  # Just setup, don't start loop
  ./ralph-bead.sh LUCY-1234 --max-iterations 0

MONITORING (when running in background):
  # Attach to tmux session
  tmux attach -t ralph-LUCY-1234

  # View logs
  tail -f ~/.worktrees/AestheticcNext/LUCY-1234/.compound/logs/latest.log

  # Check status
  cat ~/.worktrees/AestheticcNext/LUCY-1234/.compound/status.md

STOPPING:
  # Graceful stop
  touch ~/.worktrees/AestheticcNext/LUCY-1234/.compound/STOP

  # Or kill tmux session
  tmux kill-session -t ralph-LUCY-1234
EOF
}

BEAD_ID=""
MAX_ITERATIONS=50
SKIP_SETUP=false
SKIP_COMPLETE=false
BACKGROUND=false
DRY_RUN=false
VERBOSE=false

while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      show_help
      exit 0
      ;;
    --max-iterations)
      MAX_ITERATIONS="$2"
      shift 2
      ;;
    --skip-setup)
      SKIP_SETUP=true
      shift
      ;;
    --skip-complete)
      SKIP_COMPLETE=true
      shift
      ;;
    --background)
      BACKGROUND=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --verbose)
      VERBOSE=true
      shift
      ;;
    -*)
      echo "❌ Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$BEAD_ID" ]]; then
        BEAD_ID="$1"
      else
        echo "❌ Unexpected argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$BEAD_ID" ]]; then
  echo "❌ Error: BEAD_ID is required" >&2
  echo "" >&2
  echo "Usage: ./ralph-bead.sh BEAD_ID" >&2
  echo "       ./ralph-bead.sh --help for more options" >&2
  exit 1
fi

# ============================================================================
# Background Mode
# ============================================================================

if [[ "$BACKGROUND" == "true" ]]; then
  SESSION_NAME="ralph-$BEAD_ID"

  # Check if session already exists
  if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "⚠️  tmux session '$SESSION_NAME' already exists"
    echo ""
    echo "Options:"
    echo "  Attach: tmux attach -t $SESSION_NAME"
    echo "  Kill:   tmux kill-session -t $SESSION_NAME"
    exit 1
  fi

  echo "🚀 Starting ralph-bead in background tmux session: $SESSION_NAME"
  echo ""
  echo "To monitor:"
  echo "  tmux attach -t $SESSION_NAME"
  echo ""
  echo "To stop:"
  echo "  touch $WORKTREE_BASE/$BEAD_ID/.compound/STOP"
  echo ""

  # Build command without --background flag
  CMD="$SCRIPT_DIR/ralph-bead.sh $BEAD_ID --max-iterations $MAX_ITERATIONS"
  [[ "$SKIP_SETUP" == "true" ]] && CMD="$CMD --skip-setup"
  [[ "$SKIP_COMPLETE" == "true" ]] && CMD="$CMD --skip-complete"
  [[ "$VERBOSE" == "true" ]] && CMD="$CMD --verbose"

  # Start tmux session
  tmux new-session -d -s "$SESSION_NAME" "$CMD"

  echo "✅ Background session started"
  exit 0
fi

# ============================================================================
# Phase 1: Setup
# ============================================================================

WORKTREE_PATH="$WORKTREE_BASE/$BEAD_ID"
COMPOUND_DIR="$WORKTREE_PATH/.compound"
STATUS_FILE="$COMPOUND_DIR/status.md"

if [[ "$SKIP_SETUP" == "true" ]]; then
  echo "→ Skipping setup phase (--skip-setup)"

  if [[ ! -d "$WORKTREE_PATH" ]]; then
    echo "❌ Worktree not found: $WORKTREE_PATH" >&2
    echo "   Run without --skip-setup to create it" >&2
    exit 1
  fi
else
  echo ""
  echo "╔════════════════════════════════════════════════════════════╗"
  echo "║  PHASE 1: SETUP                                            ║"
  echo "╚════════════════════════════════════════════════════════════╝"
  echo ""

  SETUP_ARGS="$BEAD_ID --max-iterations $MAX_ITERATIONS"
  [[ "$DRY_RUN" == "true" ]] && SETUP_ARGS="$SETUP_ARGS --dry-run"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY RUN] Would run: $SETUP_SCRIPT $SETUP_ARGS"
  else
    "$SETUP_SCRIPT" $SETUP_ARGS

    if [[ $? -ne 0 ]]; then
      echo "❌ Setup failed" >&2
      exit 1
    fi
  fi
fi

# ============================================================================
# Phase 2: Ralph Loop
# ============================================================================

# Skip loop if max-iterations is 0 (setup-only mode)
if [[ "$MAX_ITERATIONS" -eq 0 ]]; then
  echo ""
  echo "→ Skipping loop phase (--max-iterations 0)"
  echo "  Setup complete. Run manually with: ./ralph-loop.sh $BEAD_ID"
  exit 0
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  PHASE 2: RALPH LOOP                                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

LOOP_ARGS="$BEAD_ID --max-iterations $MAX_ITERATIONS"
[[ "$VERBOSE" == "true" ]] && LOOP_ARGS="$LOOP_ARGS --verbose"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY RUN] Would run: $LOOP_SCRIPT $LOOP_ARGS"
else
  "$LOOP_SCRIPT" $LOOP_ARGS
  LOOP_EXIT=$?
fi

# ============================================================================
# Phase 3: Completion
# ============================================================================

if [[ "$SKIP_COMPLETE" == "true" ]]; then
  echo ""
  echo "→ Skipping completion phase (--skip-complete)"
  exit 0
fi

# Check if actually complete
if ! grep -q "BEAD COMPLETE" "$STATUS_FILE" 2>/dev/null; then
  echo ""
  echo "⚠️  Bead not complete. Skipping PR creation."
  echo "   Review logs and run ./complete-bead.sh manually when ready."
  exit 1
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║  PHASE 3: COMPLETION                                       ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
  echo "[DRY RUN] Would run: $COMPLETE_SCRIPT $BEAD_ID"
else
  "$COMPLETE_SCRIPT" "$BEAD_ID"
fi

echo ""
echo "🎉 ralph-bead complete for $BEAD_ID!"
