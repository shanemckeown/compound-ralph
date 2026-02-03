#!/bin/bash
#
# ralph-loop.sh - Phase 2: The headless Ralph loop
#
# Usage: ./ralph-loop.sh BEAD_ID [OPTIONS]
#
# This runs HEADLESS (no interaction) in a bash while loop.
# Each iteration:
# 1. Reads the stateless prompt
# 2. Feeds it to Claude with --dangerously-skip-permissions
# 3. Checks for completion
# 4. Repeats until done or max iterations
#

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Project configuration
PROJECT_NAME="${PROJECT_NAME:-AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/$PROJECT_NAME}"

# Loop defaults
DEFAULT_MAX_ITERATIONS=50
ITERATION_DELAY=5  # seconds between iterations

# ============================================================================
# Argument Parsing
# ============================================================================

show_help() {
  cat << 'EOF'
ralph-loop.sh - Run the headless Ralph loop

USAGE:
  ./ralph-loop.sh BEAD_ID [OPTIONS]

ARGUMENTS:
  BEAD_ID              The bead ID to execute

OPTIONS:
  --max-iterations N   Override max iterations from prd.json
  --allowed-tools T    Comma-separated list of allowed tools
                       (default: Bash,Read,Write,Edit,Glob,Grep)
  --verbose            Show Claude output in real-time
  -h, --help           Show this help

EXAMPLES:
  ./ralph-loop.sh LUCY-1234
  ./ralph-loop.sh LUCY-1234 --max-iterations 30
  ./ralph-loop.sh LUCY-1234 --verbose

MONITORING:
  # In another terminal:
  ./monitor-ralph.sh LUCY-1234

  # Or manually:
  tail -f ~/.worktrees/AestheticcNext/LUCY-1234/.compound/logs/latest.log

STOPPING:
  # Create stop file:
  touch ~/.worktrees/AestheticcNext/LUCY-1234/.compound/STOP

  # Or use cancel script:
  ./cancel-ralph.sh LUCY-1234
EOF
}

BEAD_ID=""
MAX_ITERATIONS=""
ALLOWED_TOOLS="Bash,Read,Write,Edit,Glob,Grep"
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
    --allowed-tools)
      ALLOWED_TOOLS="$2"
      shift 2
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
  exit 1
fi

# ============================================================================
# Setup
# ============================================================================

WORKTREE_PATH="$WORKTREE_BASE/$BEAD_ID"
COMPOUND_DIR="$WORKTREE_PATH/.compound"
PROMPT_FILE="$COMPOUND_DIR/RALPH_PROMPT.md"
STATUS_FILE="$COMPOUND_DIR/status.md"
PRD_FILE="$COMPOUND_DIR/prd.json"
LOGS_DIR="$COMPOUND_DIR/logs"
STOP_FILE="$COMPOUND_DIR/STOP"

# Verify setup was done
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "❌ Setup not complete. Run setup-bead.sh first." >&2
  echo "   Missing: $PROMPT_FILE" >&2
  exit 1
fi

if [[ ! -f "$PRD_FILE" ]]; then
  echo "❌ Setup not complete. Run setup-bead.sh first." >&2
  echo "   Missing: $PRD_FILE" >&2
  exit 1
fi

# Get max iterations from prd.json if not overridden
if [[ -z "$MAX_ITERATIONS" ]]; then
  MAX_ITERATIONS=$(jq -r '.max_iterations // 50' "$PRD_FILE")
fi

# Change to worktree
cd "$WORKTREE_PATH"

# ============================================================================
# Helper Functions
# ============================================================================

get_iteration() {
  if [[ -f "$STATUS_FILE" ]]; then
    grep '^iteration:' "$STATUS_FILE" | sed 's/iteration: *//' | head -1 || echo "1"
  else
    echo "1"
  fi
}

update_iteration() {
  local iter=$1
  if [[ -f "$STATUS_FILE" ]]; then
    sed -i.bak "s/^iteration:.*/iteration: $iter/" "$STATUS_FILE"
    rm -f "$STATUS_FILE.bak"
  fi
}

is_complete() {
  grep -q "BEAD COMPLETE" "$STATUS_FILE" 2>/dev/null
}

check_stop_file() {
  [[ -f "$STOP_FILE" ]]
}

log_iteration() {
  local iter=$1
  local log_file="$LOGS_DIR/iteration-$(printf '%03d' "$iter").log"

  # Also maintain a 'latest' symlink
  ln -sf "iteration-$(printf '%03d' "$iter").log" "$LOGS_DIR/latest.log"

  echo "$log_file"
}

task_progress() {
  local total=$(jq '.tasks | length' "$PRD_FILE")
  local done=$(jq '[.tasks[] | select(.passes == true)] | length' "$PRD_FILE")
  echo "$done/$total"
}

# ============================================================================
# Main Loop
# ============================================================================

echo "🔄 Ralph Loop Starting"
echo "======================"
echo ""
echo "  Bead:           $BEAD_ID"
echo "  Worktree:       $WORKTREE_PATH"
echo "  Max iterations: $MAX_ITERATIONS"
echo "  Allowed tools:  $ALLOWED_TOOLS"
echo ""
echo "  To stop: touch $STOP_FILE"
echo "  To monitor: tail -f $LOGS_DIR/latest.log"
echo ""
echo "======================"
echo ""

ITERATION=$(get_iteration)
START_TIME=$(date +%s)

# Remove any existing stop file
rm -f "$STOP_FILE"

while true; do
  # ========== Pre-iteration checks ==========

  # Check for stop file
  if check_stop_file; then
    echo ""
    echo "🛑 Stop file detected. Exiting gracefully."
    rm -f "$STOP_FILE"
    break
  fi

  # Check for completion
  if is_complete; then
    echo ""
    echo "✅ BEAD COMPLETE detected!"
    break
  fi

  # Check iteration limit
  if [[ $ITERATION -gt $MAX_ITERATIONS ]]; then
    echo ""
    echo "🛑 Max iterations ($MAX_ITERATIONS) reached."
    break
  fi

  # ========== Run iteration ==========

  LOG_FILE=$(log_iteration "$ITERATION")
  PROGRESS=$(task_progress)

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "🔄 Iteration $ITERATION / $MAX_ITERATIONS | Progress: $PROGRESS tasks"
  echo "   $(date '+%Y-%m-%d %H:%M:%S')"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Update iteration in status file
  update_iteration "$ITERATION"

  # Read prompt and substitute iteration number
  PROMPT=$(cat "$PROMPT_FILE" | sed "s/\${ITERATION}/$ITERATION/g")

  # Run Claude (prompt via stdin for long prompts)
  echo "→ Running Claude (output in $LOG_FILE)..."

  if [[ "$VERBOSE" == "true" ]]; then
    # Show output in real-time and save to log
    echo "$PROMPT" | claude -p \
      --dangerously-skip-permissions \
      --allowedTools "$ALLOWED_TOOLS" \
      2>&1 | tee "$LOG_FILE"
    CLAUDE_EXIT=${PIPESTATUS[1]}
  else
    # Just save to log
    echo "$PROMPT" | claude -p \
      --dangerously-skip-permissions \
      --allowedTools "$ALLOWED_TOOLS" \
      > "$LOG_FILE" 2>&1
    CLAUDE_EXIT=${PIPESTATUS[1]}
  fi

  # Log exit status
  echo "" >> "$LOG_FILE"
  echo "--- Exit code: $CLAUDE_EXIT ---" >> "$LOG_FILE"

  if [[ $CLAUDE_EXIT -ne 0 ]]; then
    echo "⚠️  Claude exited with code $CLAUDE_EXIT"
    # Continue anyway - might be transient
  fi

  # Brief pause before next iteration
  echo "→ Waiting ${ITERATION_DELAY}s before next iteration..."
  sleep "$ITERATION_DELAY"

  # Increment iteration
  ((ITERATION++))
done

# ============================================================================
# Summary
# ============================================================================

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
ELAPSED_MIN=$((ELAPSED / 60))
ELAPSED_SEC=$((ELAPSED % 60))

echo ""
echo "======================"
echo "🏁 Ralph Loop Complete"
echo "======================"
echo ""
echo "  Iterations:  $((ITERATION - 1))"
echo "  Duration:    ${ELAPSED_MIN}m ${ELAPSED_SEC}s"
echo "  Progress:    $(task_progress) tasks"
echo ""

if is_complete; then
  echo "  Status: ✅ BEAD COMPLETE"
  echo ""
  echo "  Next: Run complete-bead.sh to create PR"
else
  echo "  Status: ⚠️  Incomplete (check logs)"
  echo ""
  echo "  Review: $LOGS_DIR/latest.log"
fi
