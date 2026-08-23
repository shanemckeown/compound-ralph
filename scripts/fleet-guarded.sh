#!/bin/bash
# fleet-guarded.sh — run a codex/glm call inside the fleet-slots.py concurrency budget.
#
# Why: fleet-dispatch.py caps Agent View sessions at 5 of 11 total slots, but that
# left Codex/GLM calls (made either directly by the orchestrator, or by a dispatched
# session's own QA phase) uncounted against the same budget. This wrapper closes
# that gap without requiring anyone to remember two extra steps around every call —
# claim/release happens here, once, regardless of who invokes it.
#
# Usage:
#   fleet-guarded.sh codex "<label>" codex exec -s read-only "<prompt>"
#   fleet-guarded.sh glm   "<label>" glm review "<focus>" --cd "$(pwd)" --base origin/main
#
# Fails OPEN, not closed: if slots stay at capacity for 90s, this proceeds WITHOUT a
# claimed slot rather than blocking real work (often a QA gate a dispatched session
# cannot skip) indefinitely. A resource-accounting mechanism must never become a new
# deadlock risk. The warning it prints in that case is the signal the budget itself
# may need raising, not something to silently ignore.
set -uo pipefail

if [ "$#" -lt 3 ]; then
  echo "usage: fleet-guarded.sh <codex|glm> <label> <command...>" >&2
  exit 64
fi

KIND="$1"; LABEL="$2"; shift 2

SLOTS="$HOME/.claude/scripts/fleet-slots.py"
TOKEN=""
DEADLINE=$(( $(date +%s) + 90 ))

while true; do
  TOKEN=$(python3 "$SLOTS" claim-codex-glm "$KIND" "$LABEL" 2>/dev/null)
  RC=$?
  if [ "$RC" -eq 0 ] && [ -n "$TOKEN" ]; then
    break
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "fleet-guarded: fleet at capacity for 90s -- proceeding WITHOUT a claimed slot (fail-open). If this recurs often, the 11-total budget may need raising, not routing around." >&2
    TOKEN=""
    break
  fi
  sleep 2
done

cleanup() {
  if [ -n "$TOKEN" ]; then
    python3 "$SLOTS" release-codex-glm "$TOKEN" >/dev/null 2>&1
  fi
}
trap cleanup EXIT INT TERM

"$@"
exit $?
