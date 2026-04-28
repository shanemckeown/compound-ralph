#!/usr/bin/env bash
# hermes-healthcheck — pings VPS gateway every 15min, alerts after 2 strikes.
# Self-healing: attempts remote restart on first detected failure.

set -u
HOST="hermes@91.99.204.29"
SSH_KEY="$HOME/.ssh/id_ed25519_aestheticc"
LOG="$HOME/.claude/hooks/hermes-healthcheck/logs/healthcheck.log"
STATE="$HOME/.claude/hooks/hermes-healthcheck/state"
TODO="/Users/shane/Documents/Obsidian/Aestheticc/Ops/Hermes/SHANE_TODO.md"

mkdir -p "$(dirname "$LOG")" "$STATE"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Probe: does the VPS respond AND is the gateway process alive?
probe=$(ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes "$HOST" \
  'pgrep -f "gateway run" >/dev/null && echo OK || echo NO_GATEWAY' 2>&1)
exit_code=$?

strike_file="$STATE/strikes"
strikes=$(cat "$strike_file" 2>/dev/null || echo 0)

if [[ $exit_code -eq 0 && "$probe" == "OK" ]]; then
  if [[ $strikes -gt 0 ]]; then
    log "recovered (was at $strikes strikes)"
  fi
  echo 0 > "$strike_file"
  exit 0
fi

strikes=$((strikes + 1))
echo "$strikes" > "$strike_file"
log "strike $strikes: probe=$probe exit=$exit_code"

# Strike 1: attempt remote restart silently
if [[ $strikes -eq 1 ]]; then
  log "attempting remote gateway restart"
  restart_out=$(ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes "$HOST" '
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
    if systemctl --user is-enabled hermes-gateway.service 2>/dev/null | grep -q enabled; then
      systemctl --user restart hermes-gateway.service && echo "systemd-restarted"
    else
      nohup /home/hermes/.hermes/hermes-agent/venv/bin/python -m hermes_cli.main gateway run --replace >>/home/hermes/.hermes/gateway.stdout.log 2>>/home/hermes/.hermes/gateway.stderr.log &
      disown
      sleep 2
      pgrep -f "gateway run" >/dev/null && echo "bare-restarted" || echo "restart-failed"
    fi
  ' 2>&1)
  log "restart result: $restart_out"
  exit 0
fi

# Strike 2+: notify Shane
if [[ $strikes -ge 2 ]]; then
  marker="<!-- hermes-healthcheck-alert -->"
  if ! grep -q "$marker" "$TODO" 2>/dev/null; then
    {
      echo ""
      echo "### [ ] 🚨 Hermes gateway DOWN — $(ts) $marker"
      echo ""
      echo "Healthcheck probe failed $strikes times in a row. Auto-restart attempt(s) didn't recover it."
      echo "Last probe: \`$probe\` (ssh exit $exit_code)"
      echo ""
      echo "Manual diag:"
      echo '```'
      echo "ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29"
      echo "tail -50 ~/.hermes/gateway.stderr.log"
      echo "pgrep -af 'gateway run' || echo not-running"
      echo '```'
      echo ""
      echo "Healthcheck log: \`~/.claude/hooks/hermes-healthcheck/logs/healthcheck.log\`"
    } >> "$TODO"
    log "alert appended to SHANE_TODO.md"
  fi
fi
