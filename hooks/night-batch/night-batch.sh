#!/usr/bin/env bash
# night-batch.sh — overnight autonomous bead pipeline orchestrator.
#
# Picks up to 5 beads tagged `auto-eligible` from `bd ready`, dispatches
# each to a tmux /do-bead --background session, polls for completion every
# 5 min, backfills as sessions end, hard-caps at 8h total runtime.
#
# Bead: LUCY-hww5
# Triggered by: ~/Library/LaunchAgents/com.aestheticc.night-batch.plist
#               (fires at 22:00 BST Mon-Thu — load with launchctl bootstrap)
#
# Manual test (recommended FIRST run):
#   ~/.claude/hooks/night-batch/night-batch.sh --max-beads 1 --dry-run

set -u
MAX_CONCURRENT=5
MAX_BEADS_PER_NIGHT=15      # backfill ceiling
MAX_RUNTIME_SECONDS=$((8*3600))
POLL_INTERVAL=300           # 5 min
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift;;
    --max-beads) MAX_BEADS_PER_NIGHT=$2; shift 2;;
    --max-concurrent) MAX_CONCURRENT=$2; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

ROOT="$HOME/.claude/hooks/night-batch"
LOG="$ROOT/logs/orchestrator.log"
STATE="$ROOT/state"
DISPATCH="$ROOT/dispatch.sh"
BEADS_REPO="/Users/shane/Documents/Obsidian"
export BEADS_DIR="${BEADS_DIR:-$BEADS_REPO/.beads}"

mkdir -p "$ROOT/logs" "$STATE"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }
log "=== night-batch start (concurrent=$MAX_CONCURRENT, ceiling=$MAX_BEADS_PER_NIGHT, dry=$DRY_RUN) ==="


# Hard runtime cap
START_EPOCH=$(date +%s)
runtime_exceeded() {
  local now=$(date +%s)
  local elapsed=$((now - START_EPOCH))
  [[ $elapsed -ge $MAX_RUNTIME_SECONDS ]]
}

# Pick eligible beads — must have label auto-eligible AND pass Gate 1 validator
# AND not already have a state marker for tonight (any of .running, .complete,
# .guarded, .reviewed, or archived under processed/<today>/).
#
# The dedup against state markers is the fix for the 2026-04-28 backfill loop
# bug: a Gate 2-failed bead produces a .guarded marker; the prior version of
# this function only filtered against the bd label, so the same bead was
# re-dispatched up to 14 times in a row (until MAX_BEADS_PER_NIGHT). Now we
# filter at selection time.
pick_eligible() {
  local limit="$1"
  cd "$BEADS_REPO" || return 1
  local validator="$ROOT/check-eligibility.sh"
  local today_processed="$STATE/processed/$(date +%Y-%m-%d)"
  local picked=0
  for prio in 2 3 4; do
    [[ $picked -ge $limit ]] && break
    while IFS= read -r id; do
      [[ $picked -ge $limit ]] && break

      # Dedup: any marker for $id in this night's state? (active or processed)
      if compgen -G "$STATE/${id}".* >/dev/null 2>&1 \
         || compgen -G "$today_processed/${id}".* >/dev/null 2>&1; then
        log "pick_eligible: $id already has a state marker tonight, skipping"
        continue
      fi

      if reason=$("$validator" "$id" 2>&1 >/dev/null); then
        echo "$id"
        picked=$((picked+1))
      else
        log "Gate 1 reject $id: $reason"
      fi
    done < <(bd ready --priority "$prio" --label auto-eligible --limit "$limit" 2>/dev/null \
      | grep -oE 'LUCY-[a-z0-9]+')
  done
}

# Count active sessions matching auto-* (awk handles 0-match cleanly,
# avoiding the grep -c exit-1 footgun)
active_count() {
  tmux list-sessions 2>/dev/null | awk '/^auto-/{c++} END{print c+0}'
}

# Reap completed sessions: tmux ended → run Gate 2 (path + diff + size) →
# Gate 3. State buckets:
#   .complete   = both gates pass (PR ready for human merge)
#   .guarded    = Gate 2 failed (forbidden path, oversized, or empty diff)
#   .reviewed   = Gate 2 passed but Gate 3 found BLOCKERs (needs human review)
#
# Gate 3 architecture (Shane decision 2026-04-27): Codex is the gating
# reviewer (better at reviewing existing code). blind-claude runs in
# parallel for A/B comparison; its verdict does NOT gate. After ~1 week
# of side-by-side data, decide whether to drop blind-claude or keep both.
reap_completed() {
  local reaped=0
  local guard="$ROOT/check-guard.sh"
  local gate_review="$ROOT/codex-review.sh"
  local parallel_review="$ROOT/blind-claude-review.sh"
  for marker in "$STATE"/*.running; do
    [[ -f "$marker" ]] || continue
    local bead=$(basename "$marker" .running)
    local session="auto-${bead}"
    if ! tmux has-session -t "$session" 2>/dev/null; then
      log "reap: $bead session ended — running gates"
      local worktree="$HOME/.worktrees/AestheticcNext/${bead}"
      if [[ ! -d "$worktree" ]]; then
        log "  $bead: no worktree → .guarded (auto-bead failed to create one)"
        mv "$marker" "$STATE/${bead}.guarded" 2>/dev/null
      elif ! "$guard" "$bead" "$worktree" >/tmp/guard-$bead.out 2>&1; then
        log "  $bead: Gate 2 FAIL — $(tail -3 /tmp/guard-$bead.out | tr '\n' ' ')"
        mv "$marker" "$STATE/${bead}.guarded" 2>/dev/null
      else
        log "  $bead: Gate 2 PASS — $(tail -1 /tmp/guard-$bead.out)"
        local gate3_dir="$STATE/gate3/$bead"
        mkdir -p "$gate3_dir"

        # Kick off blind-claude in parallel (A/B comparison data only).
        # Wrap with `timeout` so a hung blind-claude can't block reap_completed
        # forever (Codex-flagged risk 2026-04-27). 10min cap is generous —
        # blind-claude usually finishes in <30s.
        timeout 600 "$parallel_review" "$bead" "$worktree" >"$gate3_dir/blind.log" 2>&1 &
        local parallel_pid=$!

        log "  $bead: running Gate 3 (codex gating; blind-claude parallel, 10min cap)"
        if "$gate_review" "$bead" "$worktree" >"$gate3_dir/codex.log" 2>&1; then
          local pass_line=$(grep '^PASS:' "$gate3_dir/codex.log" | tail -1)
          log "  $bead: Gate 3 PASS (codex) — ${pass_line:-no summary parsed}"
          if ! mv "$marker" "$STATE/${bead}.complete" 2>/dev/null; then
            log "  $bead: ⚠️  ALERT — mv to .complete failed; marker stuck at .running"
          fi
        else
          log "  $bead: Gate 3 FAIL (codex) — $(grep '^FAIL:' "$gate3_dir/codex.log" | head -1)"
          if ! mv "$marker" "$STATE/${bead}.reviewed" 2>/dev/null; then
            log "  $bead: ⚠️  ALERT — mv to .reviewed failed; marker stuck at .running"
          fi
        fi

        # Wait for blind-claude parallel run to finish + log its verdict.
        # `timeout 600` returns 124 on TERM, 137 on KILL — record either as a
        # known shape so morning-report doesn't mistake it for an unrelated fail.
        wait "$parallel_pid" 2>/dev/null
        local blind_exit=$?
        local blind_summary
        if [[ $blind_exit -eq 124 || $blind_exit -eq 137 ]]; then
          blind_summary="TIMEOUT after 10min (capped — investigate blind-claude.log)"
        else
          blind_summary=$(grep -E '^PASS:|^FAIL:' "$gate3_dir/blind.log" | head -1)
        fi
        log "  $bead: blind-claude (parallel, no-gate) exit=$blind_exit — ${blind_summary:-no verdict}"
      fi
      rm -f /tmp/guard-$bead.out
      reaped=$((reaped+1))
    fi
  done
  echo "$reaped"
}

# === Main loop ===
dispatched=0
queue=$(pick_eligible "$MAX_BEADS_PER_NIGHT")
queue_count=$(echo "$queue" | grep -c "^LUCY-" || true)
log "found $queue_count eligible beads in initial queue"

if [[ $queue_count -eq 0 ]]; then
  log "no eligible beads — exiting clean"
  exit 0
fi

# Initial fan-out up to MAX_CONCURRENT
for bead in $queue; do
  current=$(active_count)
  if [[ $current -ge $MAX_CONCURRENT ]]; then
    break
  fi
  if [[ $DRY_RUN -eq 1 ]]; then
    log "DRY: would dispatch $bead"
  else
    log "dispatching $bead (active=$current)"
    "$DISPATCH" "$bead"
  fi
  dispatched=$((dispatched+1))
done

# Poll + backfill loop
while ! runtime_exceeded; do
  sleep "$POLL_INTERVAL"
  reaped=$(reap_completed)
  current=$(active_count)
  log "poll: active=$current reaped=$reaped dispatched=$dispatched/$MAX_BEADS_PER_NIGHT"

  # If nothing active and we've hit the ceiling, exit
  if [[ $current -eq 0 ]] && [[ $dispatched -ge $MAX_BEADS_PER_NIGHT ]]; then
    log "all sessions complete and ceiling hit — exiting"
    break
  fi

  # Backfill: while under concurrent cap and under nightly ceiling.
  # pick_eligible() now filters out beads with any state marker for tonight,
  # so the redundant dedup check that lived here previously (and used `break`
  # incorrectly, killing the entire backfill on first stale match) is gone.
  while [[ $(active_count) -lt $MAX_CONCURRENT ]] && [[ $dispatched -lt $MAX_BEADS_PER_NIGHT ]]; do
    next=$(pick_eligible 1)
    [[ -z "$next" ]] && { log "backfill: no more eligible beads"; break; }
    if [[ $DRY_RUN -eq 1 ]]; then
      log "DRY: would backfill $next"
    else
      log "backfill: dispatching $next"
      "$DISPATCH" "$next"
    fi
    dispatched=$((dispatched+1))
  done

  if [[ $(active_count) -eq 0 ]]; then
    log "no active sessions remain — exiting"
    break
  fi
done

# Hard kill anything still running if runtime exceeded
if runtime_exceeded; then
  log "RUNTIME EXCEEDED — killing remaining sessions"
  for s in $(tmux list-sessions 2>/dev/null | grep '^auto-' | cut -d: -f1); do
    log "  killing $s"
    tmux kill-session -t "$s" 2>/dev/null
  done
fi

log "=== night-batch end (dispatched=$dispatched) ==="

# Auto-render the morning report so Shane wakes to a SHANE_TODO.md section.
# A safety-net launchd at 07:00 BST also runs morning-report.sh in case
# this end-of-run path didn't fire (orchestrator crash, etc).
if [[ -x "$ROOT/morning-report.sh" ]]; then
  log "running morning-report.sh"
  "$ROOT/morning-report.sh" 2>&1 | tee -a "$LOG"
fi
