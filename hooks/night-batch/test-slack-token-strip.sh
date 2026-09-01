#!/usr/bin/env bash
# Regression test for the SLACK_BOT_TOKEN quote-strip logic in sentry-triage.sh
# (AestheticcNext-cda5j). Same bug/fix shape as scripts/ops/nightly-bug-lane.sh's
# slack_dm() in the AestheticcNext repo (commit 94b6e22b1) — kept in sync manually,
# there's no shared lib between the two repos.
#
# The old single-regex form `s/^["']?(.*)["']?$/\1/` left the trailing quote in
# place: `.*` is greedy and consumes it before the optional trailing `["']?` gets
# a chance to match, so a token from a double-quoted .env line came out as
# `xoxb-...123"` — a literal quote character baked into the Bearer token.
#
# Run: bash hooks/night-batch/test-slack-token-strip.sh

set -euo pipefail

WORK="$(mktemp -d -p "${TMPDIR:-/tmp}")"
trap 'rm -rf "$WORK"' EXIT
FIXTURE="$WORK/fake-slack.env"

extract() {
  local env_file="$1"
  grep -E '^SLACK_BOT_TOKEN=' "$env_file" 2>/dev/null \
    | head -1 | cut -d= -f2- | sed -E 's/^["'\'']//; s/["'\'']$//' || true
}

fail=0

printf 'SLACK_BOT_TOKEN="xoxb-fake-quoted-token-123"\n' > "$FIXTURE"
got=$(extract "$FIXTURE")
[[ "$got" == "xoxb-fake-quoted-token-123" ]] \
  && echo "PASS: double-quoted token stripped -> [$got]" \
  || { echo "FAIL: double-quoted -> [$got]"; fail=1; }

printf 'SLACK_BOT_TOKEN=xoxb-fake-unquoted-456\n' > "$FIXTURE"
got=$(extract "$FIXTURE")
[[ "$got" == "xoxb-fake-unquoted-456" ]] \
  && echo "PASS: unquoted token unaffected -> [$got]" \
  || { echo "FAIL: unquoted -> [$got]"; fail=1; }

printf "SLACK_BOT_TOKEN='xoxb-fake-single-789'\n" > "$FIXTURE"
got=$(extract "$FIXTURE")
[[ "$got" == "xoxb-fake-single-789" ]] \
  && echo "PASS: single-quoted token stripped -> [$got]" \
  || { echo "FAIL: single-quoted -> [$got]"; fail=1; }

exit "$fail"
