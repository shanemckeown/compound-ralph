#!/usr/bin/env bash
# sentry-triage.sh — fetch recent Sentry issues, dispatch read-only Codex audits
# for the highest-signal NEW/SPIKING issues, and Slack DM Shane a Layer 1 digest.

set -u
set -o pipefail
ROOT="$HOME/.claude/hooks/night-batch"
LOG="$ROOT/logs/sentry-triage.log"
STATE_FILE="$HOME/.aestheticc-ops/sentry-state.json"
SLACK_USER="${SHANE_SLACK_USER_ID:-U0AAHJHPYCC}"
SLACK_BOT_ENV="/Users/shane/Documents/GitReBase/claude-code-slack-bot/.env"

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

mkdir -p "$ROOT/logs" "$(dirname "$STATE_FILE")"
LOCK_FILE="$ROOT/state/sentry-triage.lock"
LOCK_HELD_WITH_NOCLOBBER=0
OUTPUT_FILE=""
cleanup() {
  [[ -n "${OUTPUT_FILE:-}" ]] && rm -f "$OUTPUT_FILE"
  if [[ "$LOCK_HELD_WITH_NOCLOBBER" == "1" ]] && [[ "$(cat "$LOCK_FILE" 2>/dev/null || true)" == "$$" ]]; then
    rm -f "$LOCK_FILE"
  fi
}

mkdir -p "$ROOT/state"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    log "another sentry-triage instance is running — exiting"
    exit 0
  fi
else
  if ! (set -o noclobber; printf '%s\n' "$$" > "$LOCK_FILE") 2>/dev/null; then
    lock_pid=$(cat "$LOCK_FILE" 2>/dev/null || true)
    if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
      log "another sentry-triage instance is running — exiting"
      exit 0
    fi
    rm -f "$LOCK_FILE"
    if ! (set -o noclobber; printf '%s\n' "$$" > "$LOCK_FILE") 2>/dev/null; then
      log "another sentry-triage instance is running — exiting"
      exit 0
    fi
  fi
  LOCK_HELD_WITH_NOCLOBBER=1
fi
trap cleanup EXIT

OUTPUT_FILE=$(mktemp "$ROOT/logs/sentry-triage-output.XXXXXX.json")

log "=== sentry-triage start ==="

if [[ "${SENTRY_TRIAGE_DRY_RUN:-}" == "1" ]]; then
  export SENTRY_TRIAGE_DRY_RUN
fi

if ! node "$ROOT/sentry-triage.mjs" > "$OUTPUT_FILE" 2>>"$LOG"; then
  log "ERROR: sentry-triage.mjs failed"
  printf '%s\n' '{"slack_text":"*Sentry triage* — node runner failed before producing a digest","dispatched":0,"health":"NODE_FAIL"}' > "$OUTPUT_FILE"
fi

slack_text=$(python3 - "$OUTPUT_FILE" <<'PY' 2>>"$LOG"
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)
    print(data.get("slack_text") or "")
except Exception:
    print("")
PY
)

dispatched=$(python3 - "$OUTPUT_FILE" <<'PY' 2>>"$LOG"
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)
    print(data.get("dispatched", 0))
except Exception:
    print(0)
PY
)

health=$(python3 - "$OUTPUT_FILE" <<'PY' 2>>"$LOG"
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)
    print(data.get("health", "UNKNOWN"))
except Exception:
    print("UNKNOWN")
PY
)

if [[ "${SENTRY_TRIAGE_DRY_RUN:-}" == "1" ]]; then
  cat "$OUTPUT_FILE"
  log "=== sentry-triage end (dispatched=${dispatched} health=${health}) ==="
  exit 0
fi

if [[ -n "$slack_text" ]]; then
  if [[ -f "$SLACK_BOT_ENV" ]]; then
    SLACK_BOT_TOKEN=$(grep -E '^SLACK_BOT_TOKEN=' "$SLACK_BOT_ENV" 2>/dev/null \
      | head -1 | cut -d= -f2- | sed -E 's/^["'\'']?(.*)["'\'']?$/\1/' || true)
    if [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
      PAYLOAD=$(printf '%s\n%s\n' "$SLACK_USER" "$slack_text" \
        | python3 -c '
import json, sys
lines = sys.stdin.read().split("\n", 1)
print(json.dumps({"channel": lines[0], "text": lines[1].rstrip("\n"), "unfurl_links": False, "mrkdwn": True}))
')
      SLACK_RESPONSE=$(curl -sS -X POST https://slack.com/api/chat.postMessage \
        -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
        -H "Content-Type: application/json; charset=utf-8" \
        --data-binary "$PAYLOAD" 2>>"$ROOT/logs/sentry-triage-dm.log")
      SLACK_OK=$(echo "$SLACK_RESPONSE" | python3 -c "import json,sys
try: print(json.loads(sys.stdin.read()).get('ok', False))
except: print(False)" 2>/dev/null || echo False)
      if [[ "$SLACK_OK" == "True" ]]; then
        log "DM sent ok=true"
      else
        log "WARN: Slack DM failed — response: $(echo "$SLACK_RESPONSE" | head -c 300)"
      fi
    else
      log "WARN: SLACK_BOT_TOKEN missing in $SLACK_BOT_ENV"
    fi
  else
    log "WARN: $SLACK_BOT_ENV not found — skipping Slack DM"
  fi
fi

log "=== sentry-triage end (dispatched=${dispatched} health=${health}) ==="
