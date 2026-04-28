#!/bin/bash
# conductor-deploy-guard.sh
#
# PreToolUse guard: blocks `deploy-staging` invocations from a Conductor
# worktree. Two invocation paths are guarded:
#
#   1. Agent tool with subagent_type="deploy-staging"
#      Normal path when Claude decides to deploy. Primary guard.
#
#   2. Bash command that invokes scripts/deploy-staging.sh
#      Defense-in-depth — prevents an agent from shelling around the
#      subagent guard. Matches by literal substring "deploy-staging.sh"
#      which naturally excludes deploy-staging-preview.sh (different name).
#
# Why: Conductor runs 5-10 parallel worktrees. deploy-staging deploys the
# current worktree directory to the SHARED staging Cloud Run service,
# stomping every other workspace's QA and — since main is the prod
# pipeline — putting untested code a single merge away from production.
# deploy-staging-preview creates a per-branch tagged preview URL and is
# the correct path for worktree-based QA.
#
# Override paths (for when the user truly wants deploy-staging):
#   1. cd /Users/shane/Documents/GitReBase/AestheticcNext (main repo), re-run
#   2. Set env var CONDUCTOR_ALLOW_DEPLOY_STAGING=1 in the hook's shell
#      (advanced — usually easier to cd to the main repo)
#
# Bead: AestheticcNext-pwm1 follow-up.

set -eu

INPUT=$(cat)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")
CWD=$(pwd)

# Only guard inside Conductor worktrees. Everything else passes through.
case "$CWD" in
  /Users/shane/conductor/workspaces/*) ;;
  *) exit 0 ;;
esac

# Escape hatch for the user: set CONDUCTOR_ALLOW_DEPLOY_STAGING=1 to bypass.
if [ "${CONDUCTOR_ALLOW_DEPLOY_STAGING:-0}" = "1" ]; then
  exit 0
fi

emit_block() {
  local reason="$1"
  cat <<JSON_EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "$reason"
  }
}
JSON_EOF
  exit 0
}

# Guard path 1: Agent tool with deploy-staging subagent.
if [ "$TOOL" = "Agent" ]; then
  SUBAGENT=$(printf '%s' "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null || echo "")
  if [ "$SUBAGENT" = "deploy-staging" ]; then
    emit_block "BLOCKED: Conductor worktree detected. @deploy-staging deploys to the shared staging Cloud Run service and stomps every other parallel workspace's QA. Use @deploy-staging-preview instead (per-branch tagged preview URL).\n\nOverride: cd to /Users/shane/Documents/GitReBase/AestheticcNext and re-run, or invoke with CONDUCTOR_ALLOW_DEPLOY_STAGING=1 in env."
  fi
  exit 0
fi

# Guard path 2: Bash command invoking scripts/deploy-staging.sh.
# Literal substring match — deploy-staging-preview.sh doesn't contain
# "deploy-staging.sh" (the -preview- breaks the match), so preview is safe.
if [ "$TOOL" = "Bash" ]; then
  CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || echo "")
  if printf '%s' "$CMD" | grep -q 'deploy-staging\.sh'; then
    emit_block "BLOCKED: Conductor worktree detected. Running deploy-staging.sh directly stomps the shared staging Cloud Run service and affects every other parallel workspace's QA. Use @deploy-staging-preview instead (per-branch tagged preview URL).\n\nOverride: cd to /Users/shane/Documents/GitReBase/AestheticcNext and re-run, or invoke with CONDUCTOR_ALLOW_DEPLOY_STAGING=1 in env. To read the script content, use the Read tool instead of Bash."
  fi
  exit 0
fi

exit 0
