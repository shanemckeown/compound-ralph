#!/usr/bin/env bash
# morning-report.sh — durable 07:00 Slack summary + night-batch TODO renderer.
# Beads: LUCY-fef4, LUCY-c5x6, AestheticcNext-yoi4o.
#
# Choose archived-marker reading rather than leaving markers for a later job:
# yesterday + today + active state tolerate skipped night runs, post-midnight
# completion and legacy archives on the first morning after rollout. Successful
# delivery receipts suppress repeat reports and repeat marker identities; failed
# payloads survive for retry. No ordering contract with the night job is needed.
#
# Default (07:00 launchd or manual): report recent undelivered results to Shane.
# night-batch calls --archive-only: append active results to TODO and archive,
# without Slack. This deliberately moves the old 22:00 PR/dream DM to 07:00.
# --dry-run: print the DM without credentials, network delivery or any writes.
# See PLAN.md for changeover and the accepted-response/lost-receipt duplicate risk.

set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPORT_NAME=morning-report
# shellcheck source=hooks/night-batch/morning-common.sh
source "$SCRIPT_DIR/morning-common.sh"
init_report "$@" || exit $?
lock_report "$@" || exit $?
prepare_delivery || exit 1

TODO="${NIGHT_BATCH_TODO:-/Users/shane/Documents/Obsidian/Aestheticc/Ops/Hermes/SHANE_TODO.md}"
WORKTREE_BASE="${NIGHT_BATCH_WORKTREES:-$HOME/.worktrees/AestheticcNext}"
DREAM_ROOT="${NIGHT_BATCH_DREAM_ROOT:-/Users/shane/Documents/Obsidian/Aestheticc/Brain/Dreams}"
export BEADS_DIR="${BEADS_DIR:-/Users/shane/Documents/GitReBase/AestheticcNext/.beads}"
collect_markers 1 || exit 1
complete_count=0
reviewed_count=0
guarded_count=0
for f in ${marker_files[@]+"${marker_files[@]}"}; do
  case "$f" in
    *.complete) complete_count=$((complete_count + 1));;
    *.reviewed) reviewed_count=$((reviewed_count + 1));;
    *.guarded) guarded_count=$((guarded_count + 1));;
  esac
done
total=${#marker_files[@]}
zero_alert=''
[[ -n "$zero_alert_file" ]] && zero_alert=$(head -1 "$zero_alert_file")

# Helper: bead title via bd show, fallback to ID if bd unreachable
bead_title() {
  local bead="$1"
  local title
  title=$(bd show "$bead" 2>/dev/null | head -1 | sed -E 's/^[^·]*·[[:space:]]*//; s/[[:space:]]*\[●.*$//')
  echo "${title:-$bead}"
}

# Helper: render one bead block. Args: bead, status, marker_file
render_bead() {
  local bead="$1"
  local status="$2"   # complete | guarded | reviewed
  local marker_file="$3"
  local title
  title=$(bead_title "$bead")
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
    local base
    base=$(git -C "$worktree" merge-base HEAD main 2>/dev/null)
    if [[ -n "$base" ]]; then
      local stat
      stat=$(git -C "$worktree" diff --shortstat "$base"..HEAD 2>/dev/null | xargs)
      if [[ -n "$stat" ]]; then echo "**Diff:** $stat"; else echo "**Diff:** (empty)"; fi
      echo "**Commits:**"
      git -C "$worktree" log --oneline "$base"..HEAD 2>/dev/null | head -10 | sed 's/^/  - /'
    fi
  else
    echo "**Worktree:** _(missing — auto-bead.sh failed to create)_"
  fi

  # Surface blind-claude verdict + any BLOCKER lines inline
  local review_file="$worktree/.compound-review/blind-claude-${bead}.txt"
  if [[ -f "$review_file" ]]; then
    local summary
    summary=$(grep -E '^SUMMARY:' "$review_file" | head -1)
    local b
    b=$(grep -cE '^\[BLOCKER\]' "$review_file")
    local c
    c=$(grep -cE '^\[CONCERN\]' "$review_file")
    local n
    n=$(grep -cE '^\[NIT\]' "$review_file")
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
  local pr_url
  pr_url=$(gh pr list --head "$branch" --json url --jq '.[0].url' 2>/dev/null)
  if [[ -n "$pr_url" ]]; then
    echo "**PR:** $pr_url"
  else
    echo "**PR:** _(not pushed yet — sandbox or push failure; commits are local on \`$branch\`)_"
  fi

  # Staging preview URL — set by staging-preview-deploy.sh, may be ready/pending/failed
  local preview_marker="${marker_file%/*}/${bead}.preview-url"
  if [[ -f "$preview_marker" ]]; then
    local p_status
    p_status=$(grep '^status=' "$preview_marker" | head -1 | cut -d= -f2)
    local p_url
    p_url=$(grep '^url=' "$preview_marker" | head -1 | cut -d= -f2)
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

# Build Slack text before moving any marker, so preview lookup uses its source.
message_text="*Morning report* — $(date +'%a %d %b %Y %H:%M %Z')"$'\n'
message_text+="Sources: active markers + archives $YESTERDAY and $TODAY."$'\n\n'
message_text+="🟢 ${complete_count} passed gates · 🟡 ${reviewed_count} need review · 🔴 ${guarded_count} guarded"$'\n'
if [[ $total -eq 0 ]]; then
  message_text+=$'\n'"No new completed, reviewed or guarded results in this window."$'\n'
fi
shown=0
# Good news first, then review requests, then failures; all statuses get details,
# including guarded-only nights and completed beads with no PR yet.
for status in complete reviewed guarded; do
  for f in ${marker_files[@]+"${marker_files[@]}"}; do
    [[ "$f" == *."$status" ]] || continue
    [[ $shown -ge 8 ]] && continue
    bead=$(basename "$f" ".$status")
    title=$(bead_title "$bead")
    case "$status" in
      complete) emoji='🟢';;
      reviewed) emoji='🟡';;
      guarded) emoji='🔴';;
    esac
    pr_url=$(gh pr list --head "auto/$bead" --json url --jq '.[0].url' 2>/dev/null || true)
    link="$bead"
    [[ -n "$pr_url" ]] && link="<${pr_url}|${bead}>"
    detail="$status"
    [[ -z "$pr_url" && "$status" != guarded ]] && detail+="; no PR found"
    preview_marker="${f%/*}/${bead}.preview-url"
    if [[ -f "$preview_marker" ]]; then
      p_status=$(sed -n 's/^status=//p' "$preview_marker" | head -1)
      p_url=$(sed -n 's/^url=//p' "$preview_marker" | head -1)
      case "$p_status" in
        ready) detail+=" · <${p_url}|preview>";;
        pending) detail+=' · preview building';;
        failed) detail+=' · preview failed';;
      esac
    fi
    message_text+=$'\n'"${emoji} ${link}: ${title} (${detail})"
    shown=$((shown + 1))
  done
done
[[ $total -gt 8 ]] && message_text+=$'\n'"… $((total - 8)) more result(s) in SHANE_TODO.md."
[[ $total -gt 0 ]] && message_text+=$'\n\n'"Review: SHANE_TODO.md → /morning-review; investigate guarded beads before shipping."
[[ -n "$zero_alert" ]] && message_text+=$'\n\n'"${zero_alert}"
if [[ -d "$DREAM_ROOT" ]]; then
  dream_lines=''
  while IFS= read -r df; do
    dream_lines+="🌙 Aestheticc/Brain/Dreams/${df#"$DREAM_ROOT"/}"$'\n'
  done < <(find "$DREAM_ROOT" -name '*.md' -type f -mtime -1 2>/dev/null | sort)
  [[ -n "$dream_lines" ]] && message_text+=$'\n\n'"🌙 *New dream pages* — read + triage into /ceo-respond:"$'\n'"$dream_lines"
fi

# This guard precedes TODO writes, marker moves, credentials and Slack transport.
if [[ $DRY_RUN -eq 1 ]]; then
  printf '%s\n' "$message_text"
  exit 0
fi

# Append only active markers. Archived results already have a TODO section.
active_files=()
for f in ${marker_files[@]+"${marker_files[@]}"}; do
  [[ "${f%/*}" == "$STATE" ]] && active_files+=("$f")
done
active_alert=''
[[ "$zero_alert_file" == "$STATE/zero-eligible-alert" ]] && active_alert="$zero_alert_file"
if [[ ${#active_files[@]} -gt 0 || -n "$active_alert" ]]; then
  mkdir -p "$PROCESSED" || exit 1
  todo_fragment=$(mktemp "$ROOT/logs/morning-todo.XXXXXX") || exit 1
  {
    printf '\n---\n\n## Overnight runs — %s\n\n' "$(date +'%Y-%m-%d %H:%M %Z')"
    printf '**New active results:** %s bead(s)\n' "${#active_files[@]}"
    for f in ${active_files[@]+"${active_files[@]}"}; do
      status="${f##*.}"
      bead=$(basename "$f" ".$status")
      render_bead "$bead" "$status" "$f"
    done
    [[ -n "$active_alert" ]] && printf '\n%s\n' "$zero_alert"
    # Literal Markdown backticks, not command substitutions.
    # shellcheck disable=SC2016
    printf '\n_Report log: `~/.claude/hooks/night-batch/logs/morning-report.log`_\n'
  } > "$todo_fragment"
  if ! cat "$todo_fragment" >> "$TODO"; then
    log "ERROR: TODO append failed; active markers retained"
    rm -f "$todo_fragment"
    exit 1
  fi
  rm -f "$todo_fragment"
  for f in ${active_files[@]+"${active_files[@]}"}; do
    bead=$(basename "${f%.*}")
    preview_marker="$STATE/${bead}.preview-url"
    if [[ -f "$preview_marker" ]]; then
      mv "$preview_marker" "$PROCESSED/" || exit 1
    fi
    mv "$f" "$PROCESSED/" || exit 1
  done
  if [[ -n "$active_alert" ]]; then
    mv "$active_alert" "$PROCESSED/" || exit 1
  fi
  log "appended ${#active_files[@]} active result(s) to TODO and archived to $PROCESSED"
fi
if [[ $ARCHIVE_ONLY -eq 1 ]]; then
  log "archive-only complete; Slack delivery belongs to the morning job"
  echo 'morning-report: archive-only complete; no Slack DM'
  exit 0
fi
queue_message "$message_text" || exit 1
deliver_pending || exit 1
echo "morning-report: delivered $total result(s) for $TODAY"
