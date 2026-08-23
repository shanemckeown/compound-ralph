#!/usr/bin/env bash
# relay-to-orchestrator.sh — the missing bridge from 2026-08-09 §8.2, actually built 2026-08-23.
#
# Codex's job when Shane says "check in on ongoing work" or "make a bead to do X" by
# voice: don't guess, don't run tools yourself, don't ask Shane to name files — just
# hand the raw instruction to whichever Claude session currently holds the orchestrator
# role (per fleet-role.py) and let it use its own context/judgement.
#
# Mechanism: SendMessage is a tool, only callable from inside a live Claude Code turn —
# it has no bare CLI form. So this spins up a cheap, tightly-scoped one-shot `claude -p`
# session that is pre-authorized for ONLY the SendMessage tool (--allowedTools), gives it
# your message, and lets it deliver. That one-shot session does no investigation of its
# own — the orchestrator does the real thinking on the other end.
#
# Deliberately does NOT use --allow-dangerously-skip-permissions. --allowedTools already
# pre-authorizes the single tool this needs; a blanket permission bypass is not required
# and is not something this script should grant on its own.
#
# Usage: relay-to-orchestrator.sh "<Shane's instruction, verbatim>"
# Exit 0 = delivered. Exit 1 = no live orchestrator right now (message NOT sent) —
# Codex should tell Shane that plainly rather than pretending it went through.

set -euo pipefail

MSG="${1:-}"
if [ -z "$MSG" ]; then
  echo "usage: relay-to-orchestrator.sh \"<message>\"" >&2
  exit 2
fi

WHO="$(python3 /Users/shane/.claude/scripts/fleet-role.py --who 2>&1)"

if echo "$WHO" | grep -q "VACANT"; then
  echo "NO LIVE ORCHESTRATOR — nobody currently holds the role (marker is vacant)." >&2
  echo "Message NOT delivered. Tell Shane directly rather than acting like it went through." >&2
  exit 1
fi

if echo "$WHO" | grep -q "NO LONGER LIVE"; then
  echo "NO LIVE ORCHESTRATOR — the claim is stale, the holding session is gone." >&2
  echo "Message NOT delivered. Tell Shane directly rather than acting like it went through." >&2
  exit 1
fi

# "orchestrator: <name> (<session_id>)" — pull the name.
NAME="$(echo "$WHO" | head -1 | sed -E 's/^orchestrator: (.+) \([^)]+\)$/\1/')"
if [ -z "$NAME" ]; then
  echo "Could not parse orchestrator name from: $WHO" >&2
  exit 1
fi

RELAY_PROMPT="Your ONLY job this turn: call the SendMessage tool exactly once, with to=\"${NAME}\" and message set to the text below verbatim (you may prefix it with 'Voice relay from Shane via Codex:' so the recipient knows the channel). Do not investigate, read files, or do anything else — send and stop.

MESSAGE TO RELAY:
${MSG}"

claude -p "$RELAY_PROMPT" \
  --allowedTools "SendMessage" \
  --model claude-haiku-4-5-20251001

echo "Relayed to orchestrator (${NAME})." >&2
