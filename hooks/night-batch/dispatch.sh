#!/usr/bin/env bash
# dispatch.sh — spawn one /do-bead --background tmux session for a single bead.
# Called by night-batch.sh; safe to run manually for a single-bead test.
#
# Usage: dispatch.sh <BEAD_ID>
#
# Side effects:
#   - Spawns tmux session named "auto-${BEAD_ID}"
#   - Writes per-bead log to ~/.claude/hooks/night-batch/logs/${BEAD_ID}.log
#   - Returns immediately (does NOT wait for completion)

set -u
BEAD="${1:-}"
if [[ -z "$BEAD" ]]; then
  echo "Usage: $0 <BEAD_ID>" >&2
  exit 2
fi

LOG_DIR="$HOME/.claude/hooks/night-batch/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/${BEAD}.log"
SESSION="auto-${BEAD}"

ts() { date -u +%FT%TZ; }

# Bail if a session for this bead is already running
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[$(ts)] dispatch: session $SESSION already exists, skipping" >> "$LOG"
  exit 0
fi

# Spawn tmux running auto-bead.sh — our headless-native executor.
# We were calling /do-bead but that path uses compound-ralph's setup-bead.sh,
# which has an interactive Phase 0 that breaks under claude -p (silently
# hallucinated "complete" state, 41s exits). auto-bead.sh creates the worktree
# directly and dispatches a single focused claude -p invocation. See observer
# log entry 2026-04-26 for the /do-bead-headless incompatibility.
AUTOBEAD="$HOME/.claude/hooks/night-batch/auto-bead.sh"
echo "[$(ts)] dispatch: spawning tmux session $SESSION for $BEAD (auto-bead.sh)" >> "$LOG"

tmux new-session -d -s "$SESSION" \
  "exec >>'$LOG' 2>&1; '$AUTOBEAD' '$BEAD'; \
   echo '[$(ts)] auto-bead exited with code '\$?''"

# Write a marker file so the orchestrator can poll for completion
MARKER="$HOME/.claude/hooks/night-batch/state/${BEAD}.running"
mkdir -p "$(dirname "$MARKER")"
date -u +%s > "$MARKER"
echo "[$(ts)] dispatch: marker written at $MARKER" >> "$LOG"
