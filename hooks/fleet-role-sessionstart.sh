#!/usr/bin/env bash
# fleet-role-sessionstart.sh — SessionStart hook
#
# Wires fleet-role.py (built 2026-08-11, never hooked in until 2026-08-23) into
# every session start. Makes "am I the orchestrator?" a tested fact instead of
# CLAUDE.md prose the model has to infer. Default-deny: any failure here reads
# as SUB, never ORCHESTRATOR.
#
# Does NOT auto-claim. Claiming stays a deliberate act — a session only runs
# `fleet-role.py <id> --claim` when a human explicitly tells it "you are the
# orchestrator now" (see Aestheticc/CLAUDE.md, "Orchestrator vs sub-Claude").

set -euo pipefail

INPUT="$(cat)"
SESSION_ID="$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null || true)"
ROLE_SCRIPT="/Users/shane/.claude/scripts/fleet-role.py"

if [ -z "$SESSION_ID" ] || [ ! -x "$ROLE_SCRIPT" ]; then
  # Can't determine identity — default-deny, say nothing rather than guess.
  exit 0
fi

set +e
RESULT="$(python3 "$ROLE_SCRIPT" "$SESSION_ID" 2>&1)"
STATUS=$?
set -e

if [ "$STATUS" -eq 0 ]; then
  CTX="🎯 fleet-role: this session IS the ORCHESTRATOR (claimed marker at ~/.claude/fleet/ORCHESTRATOR matches this session id). Dispatch, don't do the work yourself — see CLAUDE.md 'The orchestrator dispatches; it does not do the work itself'."
else
  CTX="fleet-role: this session is SUB (default-deny — no live orchestrator claim matches this session id). If Shane explicitly tells you 'you are the orchestrator now', make it real: run \`python3 /Users/shane/.claude/scripts/fleet-role.py $SESSION_ID --claim\` (add --steal only if a stale/wrong claim refuses it) so future sessions can test this instead of relying on prose."
fi

jq -n --arg ctx "$CTX" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}'
