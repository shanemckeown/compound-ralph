#!/usr/bin/env bash
# gbrain-ingest background worker — LUCY-feaj
# Invoked by launchctl every 5 minutes (or via backfill). Processes pending
# markers by spawning `claude -p` subprocesses (subscription-powered, £0
# marginal cost) to produce diarizations, then SSHes to Hermes VPS and
# writes to GBrain via `gbrain put`.

set -u
LOG="$HOME/.claude/hooks/gbrain-ingest/logs/worker.log"
MARKERS="$HOME/.claude/hooks/gbrain-ingest/markers"
PROMPT="$HOME/.claude/hooks/gbrain-ingest/diarize-prompt.md"
CLAUDE_BIN="/Users/shane/.local/bin/claude"
VPS_HOST="hermes@91.99.204.29"
SSH_KEY="$HOME/.ssh/id_ed25519_aestheticc"
LOCK="$MARKERS/.worker.lock"

mkdir -p "$(dirname "$LOG")" "$MARKERS" 2>/dev/null

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Single-instance lock (launchctl may fire overlapping if a run is slow).
# Stale-lock watchdog: if the lock dir is older than 30 min, a previous run
# crashed without cleanup — reap it before re-attempting.
if [[ -d "$LOCK" ]]; then
  lock_age=$(( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || echo 0) ))
  if [[ $lock_age -gt 1800 ]]; then
    log "worker: stale lock (age ${lock_age}s) — reaping"
    rmdir "$LOCK" 2>/dev/null
  fi
fi
if ! mkdir "$LOCK" 2>/dev/null; then
  log "worker: already running, exiting"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

if [[ ! -x "$CLAUDE_BIN" ]]; then
  log "worker: claude binary not at $CLAUDE_BIN, aborting"
  exit 0
fi

shopt -s nullglob
pending=("$MARKERS"/*.pending)
if [[ ${#pending[@]} -eq 0 ]]; then
  exit 0
fi

log "worker: scanning ${#pending[@]} pending marker(s)"
processed=0
for marker in "${pending[@]}"; do
  session_id=$(basename "$marker" .pending)
  transcript_path=$(cat "$marker" 2>/dev/null)

  if [[ ! -f "$transcript_path" ]]; then
    log "worker: transcript missing session=$session_id path=$transcript_path"
    mv "$marker" "${marker%.pending}.error-no-transcript"
    continue
  fi

  log "worker: diarizing session=$session_id"

  # Ask claude -p to read the transcript + the prompt and produce the JSON envelope.
  # Invoke with --dangerously-skip-permissions so file reads go through without
  # TUI prompts; we control what we ask for.
  response=$(
    GBRAIN_INGEST_SUBPROCESS=1 \
    "$CLAUDE_BIN" --dangerously-skip-permissions -p \
      "Read the instructions at $PROMPT, then read the transcript at $transcript_path, then respond per instructions. Transcript session_id: $session_id." \
      2>>"$LOG"
  )
  exit_code=$?

  if [[ $exit_code -ne 0 || -z "$response" ]]; then
    log "worker: claude -p failed (exit=$exit_code, empty=$([[ -z "$response" ]] && echo 1 || echo 0)) session=$session_id"
    mv "$marker" "${marker%.pending}.error-claude"
    continue
  fi

  # Strip a leading/trailing ```json fence if present, then parse
  slug=$(echo "$response" | python3 -c "
import sys, json, re
raw = sys.stdin.read().strip()
m = re.search(r'\`\`\`json\s*(.+?)\s*\`\`\`', raw, re.DOTALL)
payload = m.group(1) if m else raw
try:
    d = json.loads(payload)
    print(d.get('slug',''))
except Exception:
    print('')
" 2>/dev/null)

  body=$(echo "$response" | python3 -c "
import sys, json, re
raw = sys.stdin.read().strip()
m = re.search(r'\`\`\`json\s*(.+?)\s*\`\`\`', raw, re.DOTALL)
payload = m.group(1) if m else raw
try:
    d = json.loads(payload)
    print(d.get('body',''))
except Exception:
    print('')
" 2>/dev/null)

  entities_json=$(echo "$response" | python3 -c "
import sys, json, re
raw = sys.stdin.read().strip()
m = re.search(r'\`\`\`json\s*(.+?)\s*\`\`\`', raw, re.DOTALL)
payload = m.group(1) if m else raw
try:
    d = json.loads(payload)
    print(json.dumps(d.get('entities', [])))
except Exception:
    print('[]')
" 2>/dev/null)

  if [[ -z "$slug" || -z "$body" ]]; then
    log "worker: parse failed session=$session_id (slug='$slug' body_len=${#body})"
    # Save raw response for debugging
    echo "$response" > "${marker%.pending}.raw-response"
    mv "$marker" "${marker%.pending}.error-parse"
    continue
  fi

  # Validate slug shape — kebab-case starting with claude-session-
  if [[ ! "$slug" =~ ^claude-session-[a-z0-9-]+$ ]]; then
    log "worker: bad slug '$slug' for session=$session_id, falling back"
    slug="claude-session-$(date -u +%Y-%m-%d)-${session_id:0:8}"
  fi

  # Write body to GBrain via SSH
  put_result=$(
    echo "$body" | ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 "$VPS_HOST" \
      "PATH=\$HOME/.bun/bin:\$PATH /home/hermes/.bun/bin/gbrain put '$slug'" 2>>"$LOG"
  )
  ssh_exit=$?

  if [[ $ssh_exit -ne 0 ]]; then
    log "worker: gbrain put failed session=$session_id slug=$slug ssh_exit=$ssh_exit"
    mv "$marker" "${marker%.pending}.error-gbrain-put"
    continue
  fi

  log "worker: wrote slug=$slug session=$session_id result=$put_result"

  # Link entities (best-effort; failures are non-fatal)
  echo "$entities_json" | python3 -c "
import sys, json
try:
    for e in json.load(sys.stdin):
        print(e)
except Exception:
    pass
" 2>/dev/null | while IFS= read -r entity; do
    [[ -z "$entity" ]] && continue
    # Slugify entity for the target — GBrain will create if missing
    entity_slug=$(echo "$entity" | python3 -c "
import sys, re
s = sys.stdin.read().strip().lower()
s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
print(s)
" 2>/dev/null)
    [[ -z "$entity_slug" ]] && continue
    ssh -i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 "$VPS_HOST" \
      "PATH=\$HOME/.bun/bin:\$PATH /home/hermes/.bun/bin/gbrain link '$slug' '$entity_slug' --type mentions" \
      >>"$LOG" 2>&1 || true
  done

  mv "$marker" "${marker%.pending}.diarised"
  processed=$((processed + 1))
done

log "worker: done, processed=$processed"
exit 0
