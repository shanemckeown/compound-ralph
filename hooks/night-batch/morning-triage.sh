#!/usr/bin/env bash
# morning-triage.sh — 07:30 pipeline health, complementing the 07:00 PR report.
# Beads: LUCY-2026-05-02, AestheticcNext-yoi4o.
#
# Read yesterday + today archives AND active markers: the night job archives
# before midnight, and late completions may still be active at 07:30. Reuse
# delivery receipts/outboxes for safe manual retry. Empty arrays must work on
# macOS Bash 3.2 with set -u (the old beads[*] dedup crashed on the first bead).
# Same-day retries preserve the first queued payload and its marker IDs.
# --dry-run prints the DM with no credential access, writes or Slack transport.
# No-op if no recent AestheticcNext-* markers exist.

set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPORT_NAME=morning-triage
# shellcheck source=hooks/night-batch/morning-common.sh
source "$SCRIPT_DIR/morning-common.sh"
init_report "$@" || exit $?
lock_report "$@" || exit $?
prepare_delivery || exit 1
ORCH_LOG="$ROOT/logs/orchestrator.log"
log '=== morning-triage start ==='
collect_markers 0 || exit 1
triage_files=()
for f in ${marker_files[@]+"${marker_files[@]}"}; do
  [[ "${f##*/}" == AestheticcNext-* ]] && triage_files+=("$f")
done
if [[ ${#triage_files[@]} -eq 0 ]]; then
  log "no AestheticcNext-* markers in active state or archives $YESTERDAY and $TODAY"
  echo 'morning-triage: no recent AestheticcNext-* markers; no DM'
  exit 0
fi

# Check orchestrator log for last night's fire (22:00 BST = 21:00 UTC of yesterday)
YESTERDAY_UTC="$YESTERDAY"
ORCH_DISPATCHED="?"
if grep -q "^\[${YESTERDAY_UTC}T21:.*=== night-batch start" "$ORCH_LOG" 2>/dev/null \
   || grep -q "^\[${YESTERDAY_UTC}T22:.*=== night-batch start" "$ORCH_LOG" 2>/dev/null; then
  ORCH_FIRED="YES"
  ORCH_DISPATCHED=$(grep "^\[${YESTERDAY_UTC}T2[12]:" "$ORCH_LOG" 2>/dev/null \
    | grep -oE 'dispatched=[0-9]+/[0-9]+' | tail -1 | cut -d= -f2 || echo "?")
else
  ORCH_FIRED="NO"
fi
ORCH_DISPATCHED="${ORCH_DISPATCHED:-?}"

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

for f in "${triage_files[@]}"; do
  bead=$(basename "${f%.*}")
  status="${f##*.}"
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
    blockers=$(grep -cE '^\[BLOCKER\]' "$codex_log" 2>/dev/null | tr -d '[:space:]' || true)
    [[ "${blockers:-0}" -gt 0 ]] && codex_blocker_beads+=("$bead ($blockers)")
  fi

  # Preview status
  preview_marker="${f%/*}/${bead}.preview-url"
  if [[ -f "$preview_marker" ]]; then
    pstatus=$(grep '^status=' "$preview_marker" | head -1 | cut -d= -f2)
    case "$pstatus" in
      ready)  preview_ready_count=$((preview_ready_count+1));;
      failed) preview_failed_count=$((preview_failed_count+1));;
    esac
  fi

  # PR diff size (regression test for bd-cleanup fix)
  if command -v gh >/dev/null 2>&1; then
    pr_num=$(gh pr list --repo shanemckeown/AestheticcNext --head "auto/${bead}" \
      --state all --json number --jq '.[0].number' 2>/dev/null)
    if [[ -n "$pr_num" ]]; then
      if gh pr diff "$pr_num" --repo shanemckeown/AestheticcNext --name-only 2>/dev/null \
         | grep -E '^(\.beads/issues\.jsonl|issues\.jsonl)$' >/dev/null; then
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
total=${#triage_files[@]}
msg="*Overnight triage* — $(date +'%a %d %b %Y %H:%M %Z')"$'\n'
msg+="Health: *${HEALTH}* · launchd-fired: ${ORCH_FIRED} · dispatched: ${ORCH_DISPATCHED}"$'\n\n'
msg+="Sources: active markers + archives $YESTERDAY and $TODAY."$'\n\n'
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

# Exit before any credentials or transport; fixtures can inspect exact DM text.
if [[ $DRY_RUN -eq 1 ]]; then
  printf '%s\n' "$msg"
  exit 0
fi
queue_message "$msg" || exit 1
deliver_pending || exit 1
echo "morning-triage: health=${HEALTH} beads=${total} ready=${complete_count} guarded=${guarded_count}"
log "=== morning-triage end (health=${HEALTH}) ==="
