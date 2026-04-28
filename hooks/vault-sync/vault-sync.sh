#!/usr/bin/env bash
# vault-sync — nightly Mac → VPS Obsidian vault sync + GBrain re-embed.
# Pushes any committed local changes, pulls them on the VPS, then runs
# `gbrain embed --stale` so only changed pages get re-embedded.
#
# Does NOT auto-commit — uncommitted changes are surfaced to SHANE_TODO.md.

set -u
HOST="hermes@91.99.204.29"
SSH_KEY="$HOME/.ssh/id_ed25519_aestheticc"
VAULT="/Users/shane/Documents/Obsidian"
LOG="$HOME/.claude/hooks/vault-sync/logs/vault-sync.log"
TODO="/Users/shane/Documents/Obsidian/Aestheticc/Ops/Hermes/SHANE_TODO.md"

mkdir -p "$(dirname "$LOG")"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

log "=== vault-sync start ==="

cd "$VAULT" || { log "ERROR: vault not found at $VAULT"; exit 1; }

# Surface uncommitted changes but don't block the sync of committed work
dirty_count=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [[ $dirty_count -gt 0 ]]; then
  log "vault has $dirty_count uncommitted changes (skipping commit, only pushing committed work)"
fi

# Push any committed changes to GitHub
push_out=$(git push origin main 2>&1)
push_exit=$?
if [[ $push_exit -ne 0 ]]; then
  log "ERROR: git push failed: $push_out"
  marker="<!-- vault-sync-push-fail -->"
  if ! grep -q "$marker" "$TODO" 2>/dev/null; then
    {
      echo ""
      echo "### [ ] 🚨 Vault sync: git push failed — $(ts) $marker"
      echo "\`\`\`"
      echo "$push_out"
      echo "\`\`\`"
      echo "Log: \`~/.claude/hooks/vault-sync/logs/vault-sync.log\`"
    } >> "$TODO"
  fi
  exit 1
fi
log "git push OK: $(echo "$push_out" | tail -1)"

# Pull on VPS + re-embed stale pages
remote_out=$(ssh -i "$SSH_KEY" -o ConnectTimeout=15 -o BatchMode=yes "$HOST" '
  set -e
  cd /home/hermes/vault-repo
  before=$(git rev-parse HEAD)
  git pull --ff-only origin main 2>&1
  after=$(git rev-parse HEAD)
  if [[ "$before" == "$after" ]]; then
    echo "no-new-commits"
  else
    echo "pulled $before -> $after"
  fi
  echo "--- embed ---"
  # Strip carriage-return progress spam, keep only the final summary line
  PATH=$HOME/.bun/bin:$PATH gbrain embed --stale 2>&1 | tr "\r" "\n" | grep -E "embedded|error|done|skipped" | tail -3
' 2>&1)
remote_exit=$?

if [[ $remote_exit -ne 0 ]]; then
  log "ERROR: VPS pull/embed failed: $remote_out"
  marker="<!-- vault-sync-vps-fail -->"
  if ! grep -q "$marker" "$TODO" 2>/dev/null; then
    {
      echo ""
      echo "### [ ] 🚨 Vault sync: VPS pull/embed failed — $(ts) $marker"
      echo "\`\`\`"
      echo "$remote_out"
      echo "\`\`\`"
    } >> "$TODO"
  fi
  exit 1
fi
log "VPS sync OK:"
echo "$remote_out" | sed 's/^/    /' >> "$LOG"
log "=== vault-sync end ==="
