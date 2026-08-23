#!/usr/bin/env bash
# ceo-review-prompt.sh — SessionStart hook
#
# Reminds Shane to respond to today's CEO review if the review has been pushed
# and he has not yet logged a response.

set -euo pipefail

cat >/dev/null

TODAY="$(date +%F)"
BASE_DIR="$HOME/.local/share/ceo-reviews"
RESPONDED="$BASE_DIR/responded/$TODAY"
PUSHED="$BASE_DIR/last-pushed-$TODAY"

[ -f "$RESPONDED" ] && exit 0
[ -f "$PUSHED" ] || exit 0

jq -n --arg ctx "$(cat <<'CTX'
🎙 CEO review awaiting your response.
Today's surfaced gaps are in your Slack DMs (07:30).
When ready, type /ceo-respond to log your response so tomorrow's review adapts.
CTX
)" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
