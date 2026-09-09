#!/usr/bin/env bash
# Shared reporting plumbing for AestheticcNext-yoi4o. Sourced by the two
# morning scripts; no work or credential access occurs merely by sourcing.
# The first queued payload and marker IDs for each date stay unchanged on
# same-day retries. Callers decide when to return delivery failures so the
# morning report can finish its local TODO/archive work while Slack is down.

init_report() {
  DRY_RUN=0
  ARCHIVE_ONLY=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=1;;
      --archive-only)
        [[ "$REPORT_NAME" == morning-report ]] || return 2
        ARCHIVE_ONLY=1;;
      *) echo "unknown arg: $1" >&2; return 2;;
    esac
    shift
  done
  ROOT="${NIGHT_BATCH_ROOT:-$SCRIPT_DIR}"
  STATE="$ROOT/state"
  TODAY=$(date +%Y-%m-%d)
  YESTERDAY=$(python3 - "$TODAY" <<'PY'
import datetime, sys
print(datetime.date.fromisoformat(sys.argv[1]) - datetime.timedelta(days=1))
PY
  ) || return 1
  PROCESSED="$STATE/processed/$TODAY"
  LOG="$ROOT/logs/$REPORT_NAME.log"
  DELIVERY="$STATE/$REPORT_NAME-delivery"
  SLACK_USER="${SHANE_SLACK_USER_ID:-U0AAHJHPYCC}"
  SLACK_BOT_ENV="${NIGHT_BATCH_SLACK_ENV:-/Users/shane/Documents/GitReBase/claude-code-slack-bot/.env}"
  shopt -s nullglob
}

log() {
  [[ $DRY_RUN -eq 1 ]] && return 0
  printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*" >> "$LOG"
}

# flock is not shipped as a command on macOS. Python's OS file lock survives
# exec into the same Bash, is released on exit/crash, and has no stale PID file.
# Wait for an overlapping archive-only run, then recheck delivery receipts;
# exiting on contention could otherwise skip the only 07:00 invocation.
lock_report() {
  [[ $DRY_RUN -eq 1 ]] && return 0
  [[ "${MORNING_REPORT_LOCK:-}" == "$DELIVERY" ]] && return 0
  mkdir -p "$DELIVERY" "$ROOT/logs" || return 1
  exec python3 - "$DELIVERY" "$BASH" "$0" "$@" <<'PY'
import fcntl, os, sys
directory, bash, script, *args = sys.argv[1:]
fd = os.open(directory + '/.lock', os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
os.set_inheritable(fd, True)
os.environ['MORNING_REPORT_LOCK'] = directory
os.execv(bash, [bash, script, *args])
PY
}

marker_identity() {
  { basename "$1"; cat "$1"; } | shasum -a 256 | cut -d' ' -f1
}

# Most recent location wins per bead: active, today's archive, yesterday's.
# Content identities survive archiving. Only the main report filters already
# delivered results; triage independently inspects the recent pipeline state.
collect_markers() {
  local filter_delivered="$1" d f bead identity receipt seen='|' delivered=''
  local source_dirs=("$STATE")
  if [[ $ARCHIVE_ONLY -eq 0 ]]; then
    source_dirs+=("$PROCESSED" "$STATE/processed/$YESTERDAY")
  else
    filter_delivered=0
  fi
  marker_files=()
  marker_ids=()
  zero_alert_file=''
  if [[ $filter_delivered -eq 1 ]]; then
    for receipt in "$DELIVERY"/????-??-??/sent.json; do
      delivered+="$(cat "${receipt%/sent.json}/markers")"$'\n'
    done
  fi
  for d in "${source_dirs[@]}"; do
    # Prefer the conservative status if conflicting statuses coexist in one dir.
    for f in "$d"/*.guarded "$d"/*.reviewed "$d"/*.complete; do
      bead=$(basename "${f%.*}")
      [[ "$seen" == *"|$bead|"* ]] && continue
      seen+="$bead|"
      identity=$(marker_identity "$f") || return 1
      if [[ $filter_delivered -eq 1 ]] && grep -Fqx "$identity" <<< "$delivered"; then
        continue
      fi
      marker_files+=("$f")
      marker_ids+=("$identity")
    done
    if [[ -z "$zero_alert_file" && -f "$d/zero-eligible-alert" ]]; then
      zero_alert_file="$d/zero-eligible-alert"
    fi
  done
  if [[ -n "$zero_alert_file" ]]; then
    identity=$(marker_identity "$zero_alert_file") || return 1
    if [[ $filter_delivered -eq 1 ]] && grep -Fqx "$identity" <<< "$delivered"; then
      zero_alert_file=''
    else
      marker_ids+=("$identity")
    fi
  fi
}

# Called only outside --dry-run. An HTTP 200 with ok:false is still a failed
# delivery. Keep payload and marker identities until Slack acknowledges it.
deliver_pending() {
  [[ $DRY_RUN -eq 1 || $ARCHIVE_ONLY -eq 1 ]] && return 0
  local pending token response
  for pending in "$DELIVERY"/????-??-??; do
    [[ -f "$pending/payload.json" && ! -f "$pending/sent.json" ]] || continue
    token=''
    if [[ -f "$SLACK_BOT_ENV" ]]; then
      token=$(grep -E '^SLACK_BOT_TOKEN=' "$SLACK_BOT_ENV" | head -1 | cut -d= -f2- \
        | sed -E 's/^["'\'']//; s/["'\'']$//' || true)
    fi
    if [[ -z "$token" ]]; then
      log "ERROR: missing Slack token; retained $pending/payload.json"
      echo "$REPORT_NAME: missing Slack token; delivery pending" >&2
      return 1
    fi
    response="$pending/response.tmp"
    if ! curl --fail --silent --show-error --connect-timeout 10 --max-time 60 \
      -X POST https://slack.com/api/chat.postMessage \
      -H "Authorization: Bearer $token" \
      -H 'Content-Type: application/json; charset=utf-8' \
      --data-binary "@$pending/payload.json" > "$response"; then
      log "ERROR: Slack transport failed; retained $pending/payload.json"
      return 1
    fi
    if ! python3 - "$response" <<'PY'
import json, sys
try:
    response = json.load(open(sys.argv[1]))
    if response.get('ok') is not True:
        raise ValueError(response.get('error', 'Slack did not acknowledge delivery'))
except (ValueError, OSError, AttributeError) as error:
    print('Slack delivery failed: ' + str(error), file=sys.stderr)
    sys.exit(1)
PY
    then
      log "ERROR: Slack rejected delivery; retained $pending/payload.json"
      return 1
    fi
    mv "$response" "$pending/sent.json" || return 1
    log "Slack acknowledged $pending/payload.json"
  done
}

queue_message() {
  # Keep the original snapshot; never move staging inside an existing outbox.
  [[ -f "$DELIVERY/$TODAY/payload.json" ]] && return 0
  local message="$1" staging="$DELIVERY/.pending-$$"
  mkdir "$staging" || return 1
  if ! printf '%s\n%s' "$SLACK_USER" "$message" | python3 -c '
import json, sys
channel, text = sys.stdin.read().split("\n", 1)
print(json.dumps({"channel": channel, "text": text, "unfurl_links": False, "mrkdwn": True}))
' > "$staging/payload.json"; then
    rm -r "$staging"
    return 1
  fi
  printf '%s\n' ${marker_ids[@]+"${marker_ids[@]}"} > "$staging/markers" || return 1
  mv "$staging" "$DELIVERY/$TODAY" || return 1
}

prepare_delivery() {
  [[ $DRY_RUN -eq 1 || $ARCHIVE_ONLY -eq 1 ]] && return 0
  deliver_pending || return 1
  if [[ -f "$DELIVERY/$TODAY/sent.json" ]]; then
    log "already delivered for $TODAY — exiting"
    echo "$REPORT_NAME: already delivered for $TODAY"
    exit 0
  fi
}
