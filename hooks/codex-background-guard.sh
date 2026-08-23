#!/bin/bash
# PreToolUse guard (Bash): blocks `codex exec` when it's being backgrounded.
#
# Backgrounding `codex exec` (nohup ... &, a trailing &, or run_in_background:true)
# is a known-broken pattern for the Codex CLI specifically: it silently dies/hangs
# with zero output and exit code 0 — no error to react to. Confirmed twice
# (2026-08-18, ~1.5 hours lost on one run). `codex exec` must run in the foreground;
# use Bash's `timeout` parameter for long calls instead (e.g. `timeout 550 codex exec ...`).

input=$(cat)
command=$(echo "$input" | jq -r '.tool_input.command // empty')
run_in_bg=$(echo "$input" | jq -r '.tool_input.run_in_background // false')

# Not a Bash call, or no command — nothing to check.
if [[ -z "$command" ]]; then
  exit 0
fi

# Does this command invoke `codex exec`?
if ! echo "$command" | grep -qE 'codex[[:space:]]+exec\b'; then
  exit 0
fi

backgrounded=false

if echo "$command" | grep -qi 'nohup'; then
  backgrounded=true
fi

# A lone trailing `&` (not part of `&&`) at the end of the command = backgrounding.
if echo "$command" | grep -qE '(^|[^&])&[[:space:]]*$'; then
  backgrounded=true
fi

if [[ "$run_in_bg" == "true" ]]; then
  backgrounded=true
fi

if [[ "$backgrounded" == "true" ]]; then
  jq -n '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: "codex exec must run in the FOREGROUND. Backgrounding it (nohup/&/run_in_background) is a known-broken pattern for this CLI: it silently dies or hangs with zero output and exit code 0 - no error to react to (confirmed twice, ~1.5 hours lost on one run, 2026-08-18). Use Bash'\''s `timeout` parameter for long-running calls instead, e.g. `timeout 550 codex exec ...`, or split the work into a smaller foreground call."
    }
  }'
  exit 0
fi

exit 0
