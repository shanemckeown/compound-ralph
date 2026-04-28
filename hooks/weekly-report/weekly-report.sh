#!/usr/bin/env bash
# weekly-report — every Monday 09:00 local, audits the self-healing layer
# (healthcheck, vault-sync, gbrain-ingest, VPS orphan reaper, gateway uptime)
# and posts a green/yellow/red report to SHANE_TODO.md.
#
# Runs on the Mac (NOT a remote routine) so it can read local logs and SSH
# to the VPS using Shane's local key.

set -u
HOST="hermes@91.99.204.29"
SSH_KEY="$HOME/.ssh/id_ed25519_aestheticc"
TODO="/Users/shane/Documents/Obsidian/Aestheticc/Ops/Hermes/SHANE_TODO.md"
LOG="$HOME/.claude/hooks/weekly-report/logs/weekly-report.log"
HC_LOG="$HOME/.claude/hooks/hermes-healthcheck/logs/healthcheck.log"
VS_LOG="$HOME/.claude/hooks/vault-sync/logs/vault-sync.log"
GI_LOG="$HOME/.claude/hooks/gbrain-ingest/logs/worker.log"

mkdir -p "$(dirname "$LOG")"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }
log "=== weekly-report start ==="

# Cutoff: 7 days ago (epoch seconds)
WEEK_AGO=$(( $(date +%s) - 7*86400 ))
WEEK_AGO_ISO=$(date -u -r "$WEEK_AGO" +%FT%TZ)

# Helper: count log lines newer than WEEK_AGO_ISO matching a pattern.
# Logs use [YYYY-MM-DDTHH:MM:SSZ] prefix from `date -u +%FT%TZ`.
count_recent() {
  local file="$1"; local pattern="$2"
  [[ -f "$file" ]] || { echo 0; return; }
  awk -v cutoff="$WEEK_AGO_ISO" -v pat="$pattern" '
    match($0, /\[[0-9-]+T[0-9:]+Z\]/) {
      stamp = substr($0, RSTART+1, RLENGTH-2)
      if (stamp >= cutoff && $0 ~ pat) c++
    }
    END { print c+0 }
  ' "$file"
}

# 1. healthcheck strikes/recoveries
hc_strikes=$(count_recent "$HC_LOG" "strike")
hc_recovered=$(count_recent "$HC_LOG" "recovered")
hc_restarts=$(count_recent "$HC_LOG" "restart result")

# 2. vault-sync runs (count "=== vault-sync start ===" markers)
vs_runs=$(count_recent "$VS_LOG" "vault-sync start")
vs_errors=$(count_recent "$VS_LOG" "ERROR")

# 3. gbrain-ingest stale-lock reaps + processing
gi_reaps=$(count_recent "$GI_LOG" "stale lock")
gi_errors=$(count_recent "$GI_LOG" "error-claude\\|error-no-transcript")
gi_processed=$(awk -v cutoff="$WEEK_AGO_ISO" '
  match($0, /\[[0-9-]+T[0-9:]+Z\]/) {
    stamp = substr($0, RSTART+1, RLENGTH-2)
    if (stamp >= cutoff && $0 ~ /done, processed=/) {
      sub(/.*processed=/, ""); sub(/[^0-9].*/, ""); s += $0
    }
  }
  END { print s+0 }
' "$GI_LOG" 2>/dev/null)

# 4 & 5. VPS reaper + gateway uptime
vps_data=$(ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes "$HOST" '
  reaps_7d=$(awk -v cutoff="'"$WEEK_AGO_ISO"'" "
    match(\$0, /\[[0-9-]+T[0-9:]+Z\]/) {
      stamp = substr(\$0, RSTART+1, RLENGTH-2)
      if (stamp >= cutoff && \$0 ~ /reap pid=/) c++
    }
    END { print c+0 }" ~/.hermes/mcp-reaper.log 2>/dev/null)
  echo "REAPS=${reaps_7d:-0}"
  gateway_state=$(sudo systemctl is-active hermes-gateway.service 2>/dev/null)
  echo "GATEWAY_ACTIVE=$gateway_state"
  gateway_since=$(sudo systemctl show hermes-gateway.service -p ActiveEnterTimestamp --value 2>/dev/null)
  echo "GATEWAY_SINCE=$gateway_since"
  restarts_7d=$(sudo journalctl -u hermes-gateway.service --since "7 days ago" --no-pager 2>/dev/null | grep -c "Started hermes-gateway.service")
  echo "GATEWAY_RESTARTS=${restarts_7d:-0}"
' 2>&1)
vps_exit=$?

vps_reaps=$(echo "$vps_data" | grep ^REAPS= | cut -d= -f2)
gateway_active=$(echo "$vps_data" | grep ^GATEWAY_ACTIVE= | cut -d= -f2)
gateway_since=$(echo "$vps_data" | grep ^GATEWAY_SINCE= | cut -d= -f2-)
gateway_restarts=$(echo "$vps_data" | grep ^GATEWAY_RESTARTS= | cut -d= -f2)

# Verdict logic
verdict="🟢 GREEN"
notes=()
if [[ $vps_exit -ne 0 ]]; then
  verdict="🔴 RED"; notes+=("VPS unreachable from healthcheck script")
elif [[ "$gateway_active" != "active" ]]; then
  verdict="🔴 RED"; notes+=("hermes-gateway is **$gateway_active** — investigate")
elif [[ ${vs_runs:-0} -lt 6 ]]; then
  verdict="🟡 YELLOW"; notes+=("vault-sync ran $vs_runs/7 days — expected ≥6")
elif [[ ${vs_errors:-0} -gt 0 || ${hc_strikes:-0} -gt 0 || ${gi_errors:-0} -gt 0 || ${gateway_restarts:-0} -gt 0 ]]; then
  verdict="🟡 YELLOW"
  [[ ${hc_strikes:-0} -gt 0 ]]   && notes+=("healthcheck fired $hc_strikes strike(s), $hc_recovered recovery, $hc_restarts auto-restart(s)")
  [[ ${vs_errors:-0} -gt 0 ]]    && notes+=("vault-sync logged $vs_errors error(s)")
  [[ ${gi_errors:-0} -gt 0 ]]    && notes+=("gbrain-ingest had $gi_errors marker error(s)")
  [[ ${gateway_restarts:-0} -gt 0 ]] && notes+=("gateway restarted $gateway_restarts time(s) — usually fine, sanity-check journalctl")
fi

# Compose report
report=$(cat <<EOF

### [ ] Weekly self-healing report — $(date +"%Y-%m-%d %H:%M %Z") $verdict

| Component | Activity (last 7d) |
|---|---|
| Hermes gateway | active=\`$gateway_active\`, since \`$gateway_since\`, restarts=$gateway_restarts |
| Mac healthcheck | strikes=$hc_strikes, recoveries=$hc_recovered, auto-restarts=$hc_restarts |
| Vault sync | runs=$vs_runs/7, errors=$vs_errors |
| GBrain ingest | processed=$gi_processed, stale-lock reaps=$gi_reaps, marker errors=$gi_errors |
| VPS orphan reaper | orphans killed=$vps_reaps |

EOF
)
if [[ ${#notes[@]} -gt 0 ]]; then
  report+=$'\n**Notes:**\n'
  for n in "${notes[@]}"; do
    report+="- $n"$'\n'
  done
fi
report+=$'\n_Report log: `~/.claude/hooks/weekly-report/logs/weekly-report.log`_\n'

# Append (don't overwrite — Shane prunes via /night)
{
  echo "$report"
} >> "$TODO"

log "=== weekly-report end (verdict: $verdict) ==="
echo "$verdict — appended to $TODO"
