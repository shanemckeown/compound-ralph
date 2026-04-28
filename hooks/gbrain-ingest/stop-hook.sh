#!/usr/bin/env bash
# gbrain-ingest Stop hook — LUCY-feaj
# Fires at every Claude Code session end. Marks the transcript as pending
# diarization. Runs in <50ms, never blocks Claude Code exit, errors are
# swallowed into a log so a broken hook cannot disrupt any session.

set +e
LOG="$HOME/.claude/hooks/gbrain-ingest/logs/hook.log"
MARKERS="$HOME/.claude/hooks/gbrain-ingest/markers"
mkdir -p "$(dirname "$LOG")" "$MARKERS" 2>/dev/null

# Recursion guard — subprocess claude -p spawned by worker.sh must not mark itself.
if [[ "${GBRAIN_INGEST_SUBPROCESS:-}" == "1" ]]; then
  exit 0
fi

ts() { date -u +%FT%TZ; }

# Read hook input JSON from stdin
input=$(cat 2>/dev/null)
if [[ -z "$input" ]]; then
  echo "[$(ts)] stop-hook: no stdin, skipping" >> "$LOG"
  exit 0
fi

# Extract fields with python3 — don't fail the hook on malformed JSON
read -r session_id transcript_path <<<"$(python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('session_id',''), d.get('transcript_path',''))
except Exception:
    print('', '')
" <<<"$input" 2>/dev/null)"

if [[ -z "$session_id" || -z "$transcript_path" ]]; then
  echo "[$(ts)] stop-hook: missing session_id or transcript_path, skipping" >> "$LOG"
  exit 0
fi

# Don't mark transcripts that no longer exist (dev edge)
if [[ ! -f "$transcript_path" ]]; then
  echo "[$(ts)] stop-hook: transcript not found at $transcript_path, skipping" >> "$LOG"
  exit 0
fi

marker="$MARKERS/${session_id}.pending"

# If already processed this session, refresh marker so worker re-runs (idempotent on gbrain side — same slug)
if [[ -f "${MARKERS}/${session_id}.diarised" ]]; then
  echo "[$(ts)] stop-hook: session=$session_id already diarised, skipping mark" >> "$LOG"
  exit 0
fi

echo "$transcript_path" > "$marker" 2>/dev/null
echo "[$(ts)] stop-hook: marked session=$session_id" >> "$LOG"
exit 0
