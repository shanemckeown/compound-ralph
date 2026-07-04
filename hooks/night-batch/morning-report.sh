#!/usr/bin/env bash
# morning-report.sh — render night-batch state markers into SHANE_TODO.md.
# Bead: LUCY-fef4
#
# Reads .complete / .guarded / .reviewed markers from state/, gathers per-bead
# context (diff, commits, blind-claude review, PR), appends a structured
# section to SHANE_TODO.md, then archives processed markers to state/processed/.
#
# Triggered by:
#   1. night-batch.sh at end of run (every overnight orchestrator exit)
#   2. ~/Library/LaunchAgents/com.aestheticc.morning-report.plist at 07:00 BST
#      as a safety net (catches anything orchestrator missed)

set -u
ROOT="$HOME/.claude/hooks/night-batch"
STATE="$ROOT/state"
PROCESSED="$STATE/processed/$(date +%Y-%m-%d)"
TODO="/Users/shane/Documents/Obsidian/Aestheticc/Ops/Hermes/SHANE_TODO.md"
LOG="$ROOT/logs/morning-report.log"
ZERO_ALERT_FILE="$STATE/zero-eligible-alert"
WORKTREE_BASE="$HOME/.worktrees/AestheticcNext"
export BEADS_DIR="${BEADS_DIR:-/Users/shane/Documents/GitReBase/AestheticcNext/.beads}"

# Shane's Slack user ID for shane-gate v1 DMs (LUCY-c5x6). Hack The Planet
# workspace, U-prefix from the slack-bot logs (`event.user` field). Public
# user ID, not a secret — fine to commit.
SHANE_SLACK_USER_ID="${SHANE_SLACK_USER_ID:-U0AAHJHPYCC}"

mkdir -p "$PROCESSED" "$(dirname "$LOG")"
ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Collect markers
shopt -s nullglob
complete_files=("$STATE"/*.complete)
guarded_files=("$STATE"/*.guarded)
reviewed_files=("$STATE"/*.reviewed)

complete_count=${#complete_files[@]}
guarded_count=${#guarded_files[@]}
reviewed_count=${#reviewed_files[@]}
total=$((complete_count + guarded_count + reviewed_count))
zero_alert=""
if [[ -f "$ZERO_ALERT_FILE" ]]; then
  zero_alert=$(head -1 "$ZERO_ALERT_FILE" 2>/dev/null || true)
fi

if [[ $total -eq 0 && -z "$zero_alert" ]]; then
  log "no markers to report — exiting"
  exit 0
fi

if [[ $total -eq 0 ]]; then
  {
    echo ""
    echo "---"
    echo ""
    echo "## Overnight runs — $(date +"%Y-%m-%d %H:%M %Z")"
    echo ""
    echo "$zero_alert"
    echo ""
    echo "_Report log: \`~/.claude/hooks/night-batch/logs/morning-report.log\`_"
    echo ""
  } >> "$TODO"
  rm -f "$ZERO_ALERT_FILE"
  log "appended zero-eligible alert to SHANE_TODO.md"
  echo "morning-report: zero-eligible alert appended to $TODO"
  exit 0
fi

log "rendering report: $total beads ($complete_count complete, $reviewed_count reviewed, $guarded_count guarded)"

# Helper: bead title via bd show, fallback to ID if bd unreachable
bead_title() {
  local bead="$1"
  local title=$(bd show "$bead" 2>/dev/null | head -1 | sed -E 's/^[^·]*·[[:space:]]*//; s/[[:space:]]*\[●.*$//')
  echo "${title:-$bead}"
}

# Helper: render one bead block. Args: bead, status, marker_file
render_bead() {
  local bead="$1"
  local status="$2"   # complete | guarded | reviewed
  local title=$(bead_title "$bead")
  local worktree="$WORKTREE_BASE/$bead"
  local branch="auto/$bead"
  local emoji
  local action
  case "$status" in
    complete) emoji="🟢"; action="Push + merge: \`cd $worktree && git push -u origin $branch && gh pr create --base main\`";;
    reviewed) emoji="🟡"; action="Read blind-claude review (BLOCKERs flagged), decide fix or discard";;
    guarded)  emoji="🔴"; action="Investigate why claude went off-track (forbidden path / oversize / no diff)";;
  esac

  echo ""
  echo "### $emoji $bead — $title"
  echo "**Status:** \`.$status\`"

  if [[ -d "$worktree" ]]; then
    local base=$(git -C "$worktree" merge-base HEAD main 2>/dev/null)
    if [[ -n "$base" ]]; then
      local stat=$(git -C "$worktree" diff --shortstat "$base"..HEAD 2>/dev/null | xargs)
      [[ -n "$stat" ]] && echo "**Diff:** $stat" || echo "**Diff:** (empty)"
      echo "**Commits:**"
      git -C "$worktree" log --oneline "$base"..HEAD 2>/dev/null | head -10 | sed 's/^/  - /'
    fi
  else
    echo "**Worktree:** _(missing — auto-bead.sh failed to create)_"
  fi

  # Surface blind-claude verdict + any BLOCKER lines inline
  local review_file="$worktree/.compound-review/blind-claude-${bead}.txt"
  if [[ -f "$review_file" ]]; then
    local summary=$(grep -E '^SUMMARY:' "$review_file" | head -1)
    local b=$(grep -cE '^\[BLOCKER\]' "$review_file")
    local c=$(grep -cE '^\[CONCERN\]' "$review_file")
    local n=$(grep -cE '^\[NIT\]' "$review_file")
    echo "**Gate 3 (blind-claude):** ${summary:-no summary} — BLOCKERS=${b:-0} CONCERNS=${c:-0} NITS=${n:-0}"
    if [[ ${b:-0} -gt 0 ]]; then
      echo "  **BLOCKER lines:**"
      grep -E '^\[BLOCKER\]' "$review_file" | sed 's/^/    /'
    fi
    if [[ ${c:-0} -gt 0 ]]; then
      echo "  **CONCERN lines:**"
      grep -E '^\[CONCERN\]' "$review_file" | head -5 | sed 's/^/    /'
    fi
  fi

  # PR check (cheap — gh handles offline gracefully)
  local pr_url=$(gh pr list --head "$branch" --json url --jq '.[0].url' 2>/dev/null)
  if [[ -n "$pr_url" ]]; then
    echo "**PR:** $pr_url"
  else
    echo "**PR:** _(not pushed yet — sandbox or push failure; commits are local on \`$branch\`)_"
  fi

  # Staging preview URL — set by staging-preview-deploy.sh, may be ready/pending/failed
  local preview_marker="$STATE/${bead}.preview-url"
  if [[ -f "$preview_marker" ]]; then
    local p_status=$(grep '^status=' "$preview_marker" | head -1 | cut -d= -f2)
    local p_url=$(grep '^url=' "$preview_marker" | head -1 | cut -d= -f2)
    case "$p_status" in
      ready)   echo "**Preview:** $p_url ✅";;
      pending) echo "**Preview:** $p_url _(build in progress)_";;
      failed)  echo "**Preview:** _(deploy failed — see \`logs/${bead}.preview.log\`)_";;
      *)       echo "**Preview:** _(unknown state: $p_status)_";;
    esac
  fi

  echo "**Action:** $action"
  echo ""
}

# Build the report
report=$(cat <<EOF

---

## Overnight runs — $(date +"%Y-%m-%d %H:%M %Z")

**Summary:** $total bead(s) processed | 🟢 $complete_count ready to merge | 🟡 $reviewed_count needs review | 🔴 $guarded_count guard failed
EOF
)

# Order: complete first (good news), then reviewed (needs human look), then guarded (problems)
{
  echo "$report"
  for f in "${complete_files[@]}"; do
    bead=$(basename "$f" .complete)
    render_bead "$bead" "complete" "$f"
  done
  for f in "${reviewed_files[@]}"; do
    bead=$(basename "$f" .reviewed)
    render_bead "$bead" "reviewed" "$f"
  done
  for f in "${guarded_files[@]}"; do
    bead=$(basename "$f" .guarded)
    render_bead "$bead" "guarded" "$f"
  done
  echo "_Report log: \`~/.claude/hooks/night-batch/logs/morning-report.log\`_"
  echo ""
} >> "$TODO"

# Archive processed markers (state markers + preview-url markers)
for f in "${complete_files[@]}" "${reviewed_files[@]}" "${guarded_files[@]}"; do
  mv "$f" "$PROCESSED/" 2>/dev/null
  # Also archive the matching .preview-url marker if any (so the next night's
  # run starts clean even if Shane never opened /morning-review)
  bead=$(basename "$f" | sed -E 's/\.(complete|guarded|reviewed)$//')
  preview_marker="$STATE/${bead}.preview-url"
  [[ -f "$preview_marker" ]] && mv "$preview_marker" "$PROCESSED/" 2>/dev/null
done

log "appended report to SHANE_TODO.md and moved $total markers to $PROCESSED/"
echo "morning-report: $total bead(s) appended to $TODO"

# ────────────────────────────────────────────────────────────────────────
# shane-gate v1 (LUCY-c5x6) — Slack DM Shane the per-PR summary so he can
# approve/kick-back from his phone. v1 is text-only with PR links; the
# button-based "60-second gate" UX needs Slack admin (interactivity enable +
# Lucy app reinstall) + slack-bot @action handler — filed as v1.5 follow-up.
# Until v1.5 lands, Shane taps the PR link → uses GitHub mobile to merge.
# ────────────────────────────────────────────────────────────────────────
if [[ -n "${SHANE_SLACK_USER_ID:-}" ]] && [[ -f /Users/shane/Documents/GitReBase/claude-code-slack-bot/.env ]]; then
  # Read SLACK_BOT_TOKEN from the slack-bot's .env without `set -a; .`
  # (which would export every var in that file into morning-report's env —
  # Codex CONCERN 2026-04-28). grep just the line we need; trim quotes.
  SLACK_BOT_TOKEN=$(grep -E '^SLACK_BOT_TOKEN=' /Users/shane/Documents/GitReBase/claude-code-slack-bot/.env 2>/dev/null \
    | head -1 | cut -d= -f2- | sed -E 's/^["'\'']?(.*)["'\'']?$/\1/' || true)

  if [[ -n "${SLACK_BOT_TOKEN:-}" ]]; then
    # Build a compact summary of what's in the queue.
    summary_lines=()
    if [[ $complete_count -gt 0 ]]; then
      summary_lines+=("🟢 *${complete_count}* ready to merge")
    fi
    if [[ $reviewed_count -gt 0 ]]; then
      summary_lines+=("🟡 *${reviewed_count}* need review (Gate 3 found BLOCKERs)")
    fi
    if [[ $guarded_count -gt 0 ]]; then
      summary_lines+=("🔴 *${guarded_count}* guard failed")
    fi
    summary_text=$(IFS=' · '; echo "${summary_lines[*]}")

    # Per-PR list with title + URL, max 8 to keep DM readable.
    # Use REAL newlines (literal LF) in the variable; python json.dumps
    # below escapes them correctly into the JSON payload. Earlier version
    # used `\\n` literal-backslash-n which broke whenever a bead title
    # contained quotes/backticks/control chars (Codex BLOCKER 2026-04-28).
    pr_lines=""
    pr_count=0
    for f in "${complete_files[@]}" "${reviewed_files[@]}"; do
      [[ $pr_count -ge 8 ]] && break
      bead=$(basename "$f" | sed 's/\.[^.]*$//')
      branch="auto/${bead}"
      title=$(bead_title "$bead")
      pr_url=$(gh pr list --head "$branch" --json url --jq '.[0].url' 2>/dev/null)
      [[ -z "$pr_url" ]] && continue
      status_emoji="🟢"
      [[ "$f" == *.reviewed ]] && status_emoji="🟡"
      # Preview URL: ready/pending/failed/none
      preview_link=""
      preview_marker="$STATE/${bead}.preview-url"
      if [[ -f "$preview_marker" ]]; then
        p_status=$(grep '^status=' "$preview_marker" | head -1 | cut -d= -f2)
        p_url=$(grep '^url=' "$preview_marker" | head -1 | cut -d= -f2)
        case "$p_status" in
          ready)   preview_link=" · <${p_url}|preview>";;
          pending) preview_link=" · _preview building_";;
          failed)  preview_link=" · _preview failed_";;
        esac
      fi
      pr_lines+="${status_emoji} <${pr_url}|${bead}>: ${title}${preview_link}"$'\n'
      pr_count=$((pr_count + 1))
    done

    # 🌙 Dreams: list overnight dream-digest files (Brain/Dreams) from the last
    # ~26h as plain paths, so Shane can open + triage them into /ceo-respond.
    dream_lines=""
    DREAM_ROOT="/Users/shane/Documents/Obsidian/Aestheticc/Brain/Dreams"
    if [[ -d "$DREAM_ROOT" ]]; then
      while IFS= read -r df; do
        [[ -z "$df" ]] && continue
        dream_lines+="🌙 Aestheticc/Brain/Dreams/${df#"$DREAM_ROOT"/}"$'\n'
      done < <(find "$DREAM_ROOT" -name '*.md' -type f -mtime -1 2>/dev/null | sort)
    fi
    dream_section=""
    [[ -n "$dream_lines" ]] && dream_section=$'\n\n'"🌙 *New dream pages* — read + triage into /ceo-respond:"$'\n'"${dream_lines}"

    # Send the DM if there are PRs OR new dreams (so dreams arrive daily even on
    # nights with no auto-bead PRs).
    if [[ -n "$pr_lines" || -n "$dream_lines" ]]; then
      pr_section=""
      [[ -n "$pr_lines" ]] && pr_section=$'\n\n'"${summary_text}"$'\n\n'"${pr_lines}"$'\n'"Review: SHANE_TODO.md → tap a link → merge from GitHub mobile."
      message_text="*Overnight* — $(date +'%a %d %b %Y %H:%M %Z')${pr_section}${dream_section}"

      # Construct JSON via python json.dumps so any quotes/backslashes/
      # control chars in titles get escaped correctly. Reading args from
      # stdin avoids shell-arg-length / arg-injection issues entirely.
      PAYLOAD=$(printf '%s\n%s\n' "$SHANE_SLACK_USER_ID" "$message_text" \
        | python3 -c '
import json, sys
lines = sys.stdin.read().split("\n", 1)
channel = lines[0]
text = lines[1].rstrip("\n")
print(json.dumps({"channel": channel, "text": text, "unfurl_links": False}))
')

      curl -sS -X POST https://slack.com/api/chat.postMessage \
        -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
        -H "Content-Type: application/json; charset=utf-8" \
        --data-binary "$PAYLOAD" \
        > "$ROOT/logs/shane-gate-dm.log" 2>&1
      log "shane-gate v1: DM'd ${pr_count} PR(s) to ${SHANE_SLACK_USER_ID}"
    fi
  fi
fi
