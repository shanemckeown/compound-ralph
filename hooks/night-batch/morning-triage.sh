#!/usr/bin/env bash
# morning-triage.sh — assemble a pipeline-health report on the previous night's
# auto-bead run and Slack DM it to Shane. Fires at 07:30 BST daily; complements
# morning-report.sh (07:00 BST), which already writes SHANE_TODO.md + DMs the
# per-PR list. This script's value-add is:
#   - confirm bd-cleanup force-pushed per bead (the fix landed 2026-05-02)
#   - spot-check PR diff sizes (regression test: should be small without
#     `.beads/issues.jsonl` churn)
#   - one-line pipeline-health verdict (WORKED / PARTIAL / BROKEN)
#
# No-op silently if nothing ran overnight (state/ + state/processed/<date>/
# both empty for AestheticcNext-* markers).
#
# Bead: LUCY-2026-05-02 (auto-bead.sh bd-cleanup verification)

set -u
ROOT="$HOME/.claude/hooks/night-batch"
STATE="$ROOT/state"
TODAY=$(date +%Y-%m-%d)
PROCESSED="$STATE/processed/$TODAY"
LOG="$ROOT/logs/morning-triage.log"
ORCH_LOG="$ROOT/logs/orchestrator.log"
SLACK_USER="${SHANE_SLACK_USER_ID:-U0AAHJHPYCC}"
SLACK_BOT_ENV="/Users/shane/Documents/GitReBase/claude-code-slack-bot/.env"
REPO="/Users/shane/Documents/GitReBase/AestheticcNext"

mkdir -p "$ROOT/logs"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

log "=== morning-triage start ==="

# Collect this night's beads from state/ (if morning-report hasn't archived)
# AND state/processed/<today>/ (if it has).
shopt -s nullglob
beads=()
for f in "$STATE"/AestheticcNext-*.complete \
         "$STATE"/AestheticcNext-*.reviewed \
         "$STATE"/AestheticcNext-*.guarded \
         "$PROCESSED"/AestheticcNext-*.complete \
         "$PROCESSED"/AestheticcNext-*.reviewed \
         "$PROCESSED"/AestheticcNext-*.guarded; do
  [[ -f "$f" ]] || continue
  bead=$(basename "$f" | sed -E 's/\.(complete|reviewed|guarded)$//')
  # Dedup
  [[ " ${beads[*]} " == *" $bead "* ]] || beads+=("$bead")
done

if [[ ${#beads[@]} -eq 0 ]]; then
  log "no AestheticcNext-* markers found in state/ or processed/$TODAY/ — exiting silently"
  exit 0
fi

log "found ${#beads[@]} bead(s) to triage: ${beads[*]}"

# Check orchestrator log for last night's fire (22:00 BST = 21:00 UTC of yesterday)
YESTERDAY_UTC=$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d 'yesterday' +%Y-%m-%d 2>/dev/null)
ORCH_FIRED="UNKNOWN"
ORCH_DISPATCHED="?"
if grep -q "^\[${YESTERDAY_UTC}T21:.*=== night-batch start" "$ORCH_LOG" 2>/dev/null \
   || grep -q "^\[${YESTERDAY_UTC}T22:.*=== night-batch start" "$ORCH_LOG" 2>/dev/null; then
  ORCH_FIRED="YES"
  ORCH_DISPATCHED=$(grep "^\[${YESTERDAY_UTC}T2[12]:" "$ORCH_LOG" 2>/dev/null \
    | grep -oE 'dispatched=[0-9]+/[0-9]+' | tail -1 | cut -d= -f2 || echo "?")
else
  ORCH_FIRED="NO"
fi

# Assemble per-bead diagnostics
complete_count=0
guarded_count=0
reviewed_count=0
bd_cleanup_ok=0
bd_cleanup_missing=()
codex_blocker_beads=()
preview_ready_count=0
preview_failed_count=0
small_pr_count=0
large_pr_count=0
large_pr_details=()

for bead in "${beads[@]}"; do
  # Marker status — search both dirs, archived wins
  status="unknown"
  for ext in complete reviewed guarded; do
    if [[ -f "$PROCESSED/${bead}.${ext}" ]] || [[ -f "$STATE/${bead}.${ext}" ]]; then
      status="$ext"
    fi
  done
  case "$status" in
    complete) complete_count=$((complete_count+1));;
    reviewed) reviewed_count=$((reviewed_count+1));;
    guarded)  guarded_count=$((guarded_count+1));;
  esac

  # bd-cleanup signal: per-bead log should have the force-push line
  if [[ -f "$ROOT/logs/${bead}.log" ]] \
     && grep -q "bd-cleanup: force-pushed" "$ROOT/logs/${bead}.log"; then
    bd_cleanup_ok=$((bd_cleanup_ok+1))
  else
    bd_cleanup_missing+=("$bead")
  fi

  # Codex BLOCKER count
  codex_log="$STATE/gate3/${bead}/codex.log"
  if [[ -f "$codex_log" ]]; then
    blockers=$(grep -cE '^\[BLOCKER\]' "$codex_log" 2>/dev/null | tr -d '[:space:]' || echo 0)
    [[ "${blockers:-0}" -gt 0 ]] && codex_blocker_beads+=("$bead ($blockers)")
  fi

  # Preview status
  for d in "$PROCESSED" "$STATE"; do
    if [[ -f "$d/${bead}.preview-url" ]]; then
      pstatus=$(grep '^status=' "$d/${bead}.preview-url" | head -1 | cut -d= -f2)
      case "$pstatus" in
        ready)  preview_ready_count=$((preview_ready_count+1));;
        failed) preview_failed_count=$((preview_failed_count+1));;
      esac
      break
    fi
  done

  # PR diff size (regression test for bd-cleanup fix)
  if command -v gh >/dev/null 2>&1; then
    pr_num=$(gh pr list --repo shanemckeown/AestheticcNext --head "auto/${bead}" \
      --state all --json number --jq '.[0].number' 2>/dev/null)
    if [[ -n "$pr_num" ]]; then
      pr_files=$(gh pr diff "$pr_num" --repo shanemckeown/AestheticcNext \
        --name-only 2>/dev/null | wc -l | tr -d ' ')
      if echo "$(gh pr diff "$pr_num" --repo shanemckeown/AestheticcNext --name-only 2>/dev/null)" \
         | grep -qE '^(\.beads/issues\.jsonl|issues\.jsonl)$'; then
        large_pr_count=$((large_pr_count+1))
        large_pr_details+=("PR #${pr_num} (${bead}) still has bd state churn")
      else
        small_pr_count=$((small_pr_count+1))
      fi
    fi
  fi
done

# Health verdict
HEALTH="WORKED"
if [[ ${guarded_count} -gt 0 ]] || [[ ${preview_failed_count} -gt 0 ]] \
   || [[ ${#bd_cleanup_missing[@]} -gt 0 ]] || [[ ${large_pr_count} -gt 0 ]]; then
  HEALTH="PARTIAL"
fi
if [[ ${complete_count} -eq 0 ]] && [[ ${reviewed_count} -eq 0 ]]; then
  HEALTH="BROKEN"
fi

# Build the Slack message (Markdown via Slack mrkdwn)
total=${#beads[@]}
msg="*Overnight triage* — $(date +'%a %d %b %Y %H:%M %Z')"$'\n'
msg+="Health: *${HEALTH}* · launchd-fired: ${ORCH_FIRED} · dispatched: ${ORCH_DISPATCHED}"$'\n\n'
msg+=":large_green_circle: ready to ship: ${complete_count}"$'\n'
[[ ${reviewed_count} -gt 0 ]]   && msg+=":large_yellow_circle: codex flagged: ${reviewed_count}"$'\n'
[[ ${guarded_count} -gt 0 ]]    && msg+=":red_circle: gate-2 guarded: ${guarded_count}"$'\n'
msg+=$'\n'"bd-cleanup ran: ${bd_cleanup_ok}/${total}"$'\n'
msg+="staging-previews ready: ${preview_ready_count}/${total}"$'\n'
msg+="PR diff hygiene: ${small_pr_count}/${total} clean (no bd state churn)"$'\n'

if [[ ${#codex_blocker_beads[@]} -gt 0 ]]; then
  msg+=$'\n'"*Codex BLOCKERs:*"$'\n'
  for b in "${codex_blocker_beads[@]}"; do msg+="• ${b}"$'\n'; done
fi
if [[ ${#large_pr_details[@]} -gt 0 ]]; then
  msg+=$'\n'":warning: *PR diff regression:*"$'\n'
  for d in "${large_pr_details[@]}"; do msg+="• ${d}"$'\n'; done
fi
if [[ ${#bd_cleanup_missing[@]} -gt 0 ]]; then
  msg+=$'\n'":warning: *bd-cleanup did NOT run on:* ${bd_cleanup_missing[*]}"$'\n'
fi

case "$HEALTH" in
  WORKED)  msg+=$'\n'"Recommendation: open \`/morning-review\` and \`ship all green\`.";;
  PARTIAL) msg+=$'\n'"Recommendation: open \`/morning-review\` and review flagged beads before shipping.";;
  BROKEN)  msg+=$'\n'"Recommendation: investigate pipeline failure — orchestrator.log + per-bead logs.";;
esac

# Send via Slack — mirrors morning-report.sh's pattern (no `set -a`, json via python)
if [[ -f "$SLACK_BOT_ENV" ]]; then
  SLACK_BOT_TOKEN=$(grep -E '^SLACK_BOT_TOKEN=' "$SLACK_BOT_ENV" 2>/dev/null \
    | head -1 | cut -d= -f2- | sed -E 's/^["'\'']?(.*)["'\'']?$/\1/' || true)
  if [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
    PAYLOAD=$(printf '%s\n%s\n' "$SLACK_USER" "$msg" \
      | python3 -c '
import json, sys
lines = sys.stdin.read().split("\n", 1)
print(json.dumps({"channel": lines[0], "text": lines[1].rstrip("\n"), "unfurl_links": False, "mrkdwn": True}))
')
    curl -sS -X POST https://slack.com/api/chat.postMessage \
      -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
      -H "Content-Type: application/json; charset=utf-8" \
      --data-binary "$PAYLOAD" \
      > "$ROOT/logs/morning-triage-dm.log" 2>&1
    log "DM sent to ${SLACK_USER} (health=${HEALTH})"
  else
    log "WARN: SLACK_BOT_TOKEN missing in $SLACK_BOT_ENV"
  fi
else
  log "WARN: $SLACK_BOT_ENV not found — skipping Slack DM"
fi

# Also print to stdout (captured by launchd's StandardOutPath for debugging)
echo "morning-triage: health=${HEALTH} beads=${total} ready=${complete_count} guarded=${guarded_count}"
log "=== morning-triage end (health=${HEALTH}) ==="
