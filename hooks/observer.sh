#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║  Claude Code Peripheral Vision (/pv)                               ║
# ║                                                                    ║
# ║  Captures "drive-by observations" — bugs, misconfigs, and code     ║
# ║  smells noticed during AI coding but outside the current task.     ║
# ║                                                                    ║
# ║  See DESIGN.md for architecture decisions and format discovery.    ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ─── Dependency check ────────────────────────────────────────────────
if ! command -v jq &>/dev/null; then
  cat <<'EOF' >&2
Observer ERROR: jq is required but not installed.
  macOS:  brew install jq
  Linux:  sudo apt install jq
  Other:  https://jqlang.github.io/jq/download/
EOF
  exit 2
fi

# ─── Read hook input ─────────────────────────────────────────────────
INPUT=$(cat)
EVENT=$(jq -r '.hook_event_name // empty' <<< "$INPUT" 2>/dev/null || echo "")

# ─── Config ──────────────────────────────────────────────────────────
# Observations write to a global location so nothing is lost when
# worktrees or Conductor workspaces are cleaned up.
# Override with OBSERVER_DIR to change the location.
OBSERVER_HOME="${OBSERVER_DIR:-$HOME/.claude/observer}"
OBS_FILE="$OBSERVER_HOME/observations.log"
STATE_DIR="$OBSERVER_HOME/.state"
REMIND_INTERVAL=${OBSERVER_REMIND_INTERVAL:-3}

# Project context (for branch detection and instruction injection)
PROJECT_DIR=$(jq -r '.cwd // empty' <<< "$INPUT" 2>/dev/null || echo "")
PROJECT_DIR="${PROJECT_DIR:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"

# ─── Setup ───────────────────────────────────────────────────────────
mkdir -p "$STATE_DIR"

# ─── Helpers ─────────────────────────────────────────────────────────
obs_count() {
  [ -f "$OBS_FILE" ] && grep -c "^- \[" "$OBS_FILE" 2>/dev/null || echo 0
}

get_branch() {
  git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || echo "unknown"
}

md5_hash() {
  if command -v md5sum &>/dev/null; then
    md5sum <<< "$1" | cut -d' ' -f1
  else
    md5 -q -s "$1" 2>/dev/null || echo "nohash"
  fi
}

is_seen() {
  local hash="$1"
  local seen_file="$STATE_DIR/seen-hashes"
  [ -f "$seen_file" ] && grep -qF "$hash" "$seen_file" 2>/dev/null
}

mark_seen() {
  echo "$1" >> "$STATE_DIR/seen-hashes"
}

# ─── Event Handlers ─────────────────────────────────────────────────
case "${EVENT}" in

  # ── Session Start ──────────────────────────────────────────────────
  # Fires on: startup, resume, context compaction
  # Resets counters, injects the observation instruction
  SessionStart)
    echo "0" > "$STATE_DIR/prompt-count"
    obs_count > "$STATE_DIR/session-start-count"

    COUNT=$(obs_count)
    BACKLOG=""
    if [ "$COUNT" -gt 0 ]; then
      BACKLOG="
$COUNT unreviewed observations from previous sessions. Review: cat ~/.claude/observer/observations.log"
    fi

    jq -n --arg ctx "Observer active.${BACKLOG}

When you notice bugs, misconfigs, deprecations, or code smells OUTSIDE your current task while reading code or reviewing output, log them immediately with one append:

echo \"- [\$(date +\"%Y-%m-%d %H:%M\")] (\$(git branch --show-current 2>/dev/null || echo unknown)) description [file:line]\" >> ~/.claude/observer/observations.log" \
      '{
        hookSpecificOutput: {
          hookEventName: "SessionStart",
          additionalContext: $ctx
        }
      }'
    ;;

  # ── User Prompt Submit ─────────────────────────────────────────────
  # Periodic reminder so Claude doesn't need to "remember"
  UserPromptSubmit)
    COUNTER="$STATE_DIR/prompt-count"
    N=0
    [ -f "$COUNTER" ] && N=$(cat "$COUNTER" 2>/dev/null || echo 0)
    N=$((N + 1))
    echo "$N" > "$COUNTER"

    if [ $((N % REMIND_INTERVAL)) -eq 0 ]; then
      OBS=$(obs_count)
      jq -n --arg n "$OBS" \
        '{
          hookSpecificOutput: {
            hookEventName: "UserPromptSubmit",
            additionalContext: ("Observer (" + $n + " logged): noticed anything outside current scope? Log it.")
          }
        }'
    fi
    ;;

  # ── Post Tool Use: Bash ────────────────────────────────────────────
  # Auto-captures warnings/deprecations from command output (async)
  # Fully programmatic — no LLM involvement, just pattern matching
  PostToolUse)
    TOOL=$(jq -r '.tool_name // empty' <<< "$INPUT" 2>/dev/null || echo "")

    if [ "$TOOL" = "Bash" ]; then
      # Confirmed format: .tool_response.stdout (discovered 2026-04-06)
      RESPONSE=$(jq -r '.tool_response.stdout // empty' <<< "$INPUT" 2>/dev/null || echo "")

      # Capture warnings/deprecations, filter common noise
      # Require substantive content after the keyword (.{10,})
      FINDINGS=$(echo "$RESPONSE" \
        | grep -iE '(deprecat.{10,}|warning:.{10,}|WARN[^I].{10,}|[0-9]+ vulnerabilit)' \
        | grep -viE '(node_modules/|peer dep|npm warn config|ExperimentalWarning|punycode|Validation Warning:$|^[0-9a-f]{7,}|^[+-] |\.warn\(|warn:[[:space:]]*jest)' \
        | head -5 \
        || true)

      if [ -n "$FINDINGS" ]; then
        # Normalize timestamped log paths before hashing so repeated runs of
        # the same underlying error collapse to one entry (e.g. gcloud daily
        # log path: /.../logs/2026.04.07/14.14.26.329737.log).
        NORMALIZED=$(echo "$FINDINGS" | sed -E 's|/[0-9]{4}\.[0-9]{2}\.[0-9]{2}/[0-9.]+\.log|/TS|g')
        HASH=$(md5_hash "$NORMALIZED")

        if ! is_seen "$HASH"; then
          mark_seen "$HASH"
          CMD=$(jq -r '.tool_input.command // "unknown"' <<< "$INPUT" 2>/dev/null | head -c 100)
          BRANCH=$(get_branch)
          TS=$(date +"%Y-%m-%d %H:%M")
          {
            echo "- [$TS] ($BRANCH) [auto] \`${CMD}\`"
            echo "$FINDINGS" | sed 's/^/  /'
            echo ""
          } >> "$OBS_FILE"
        fi
      fi
    fi
    ;;

  # ── Stop ───────────────────────────────────────────────────────────
  # Fires after each Claude response
  # 1. Regex scans last_assistant_message for tangential observations
  # 2. Outputs session summary count
  Stop)
    # Confirmed format: .last_assistant_message (discovered 2026-04-06)
    LAST_MSG=$(jq -r '.last_assistant_message // empty' <<< "$INPUT" 2>/dev/null || echo "")

    if [ -n "$LAST_MSG" ]; then
      # Scan for observation patterns in Claude's response
      # Two-pass filter:
      #   1. Match observation language ("I notice", "outside scope", etc.)
      #   2. Require code context (file paths, line numbers, function names)
      #      to avoid false positives from conversational text
      CANDIDATES=$(echo "$LAST_MSG" \
        | grep -iE '(I notic|outside.{0,20}scope|not.{0,20}(our |the )focus|separate issue|tangential|but that.{0,3}s not what|unrelated.{0,20}(issue|bug|error|problem)|pre-existing.{0,20}(error|issue|bug|warning)|existing.{0,10}(error|bug|issue).{0,10}(before|prior|already)|were there before|already broken|not.{0,10}(caused|introduced) by)' \
        || true)

      MATCHES=""
      if [ -n "$CANDIDATES" ]; then
        # Second pass: must contain code-specific context
        MATCHES=$(echo "$CANDIDATES" \
          | grep -iE '(\.[jt]sx?|\.(py|rb|go|rs|sh|css|sql)|:[0-9]+|line [0-9]|function |import |const |var |class |def |TODO|FIXME|HACK|deprecat|type.?error|null|undefined|race.?condition|missing.?index|N\+1)' \
          | head -3 \
          || true)
      fi

      if [ -n "$MATCHES" ]; then
        BRANCH=$(get_branch)
        TS=$(date +"%Y-%m-%d %H:%M")
        {
          echo "- [$TS] ($BRANCH) [scan] Extracted from Claude's response:"
          echo "$MATCHES" | sed 's/^/  /'
          echo ""
        } >> "$OBS_FILE"
      fi
    fi

    # Session summary: how many new observations since session start
    START_COUNT=0
    [ -f "$STATE_DIR/session-start-count" ] && START_COUNT=$(cat "$STATE_DIR/session-start-count" 2>/dev/null || echo 0)
    CURRENT=$(obs_count)
    DELTA=$((CURRENT - START_COUNT))

    if [ "$DELTA" -gt 0 ]; then
      echo "Observer: $DELTA new observation(s) this session"
    fi
    ;;

esac

exit 0
