#!/usr/bin/env bash
# auto-bead.sh — headless executor for a single auto-eligible bead.
# Replaces /do-bead+ralph-loop for our small-scope (≤200 LOC, ≤5 files,
# ≤1 day) auto-eligible work. /do-bead's setup-bead.sh has an interactive
# Phase 0 that breaks under `claude -p` — see observer log 2026-04-26.
#
# Bead: LUCY-llp1 (replaces /do-bead invocation in dispatch.sh)
#
# Usage: auto-bead.sh <BEAD_ID>
# Exit:
#   0 = work completed, PR opened (or attempted with noted reason)
#   1 = setup or dispatch failed
#   2 = bad usage

set -u
BEAD="${1:-}"
if [[ -z "$BEAD" ]]; then
  echo "Usage: $0 <BEAD_ID>" >&2
  exit 2
fi

REPO="${PROJECT_PATH:-/Users/shane/Documents/GitReBase/AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/AestheticcNext}"
WORKTREE="$WORKTREE_BASE/$BEAD"
BRANCH="auto/$BEAD"
export BEADS_DIR="${BEADS_DIR:-/Users/shane/Documents/Obsidian/.beads}"

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] auto-bead: $*"; }

log "starting bead=$BEAD repo=$REPO worktree=$WORKTREE"

# 1. Pull bead description (single source of truth — no template scaffolding)
BEAD_INFO=$(bd show "$BEAD" 2>&1)
if ! echo "$BEAD_INFO" | head -1 | grep -q "$BEAD"; then
  log "ERROR: bd show $BEAD failed"
  exit 1
fi
# Title: row 1 looks like "○ LUCY-XXXX · TITLE   [● P? · STATUS]".
# Strip the leading status+id prefix (up to first " · "), then trim trailing
# bracketed metadata. Use a non-greedy approach via awk-style field split.
TITLE=$(echo "$BEAD_INFO" | head -1 | sed -E 's/^[^·]*·[[:space:]]*//; s/[[:space:]]*\[●.*$//')
log "title: $TITLE"

# 2. Force-clean any existing worktree (collision-proof — see today's
# Meta-publishing-content incident; old .compound state at the same path
# poisoned a fresh run)
if [[ -d "$WORKTREE" ]]; then
  log "WARN: pre-existing worktree at $WORKTREE — force-removing for clean start"
  git -C "$REPO" worktree remove --force "$WORKTREE" 2>&1 | sed 's/^/  /'
fi
git -C "$REPO" branch -D "$BRANCH" 2>/dev/null || true

# 3. Create fresh worktree off main
log "creating worktree on branch $BRANCH from main"
if ! git -C "$REPO" worktree add -b "$BRANCH" "$WORKTREE" main 2>&1 | sed 's/^/  /'; then
  log "ERROR: worktree add failed"
  exit 1
fi

# 3b. Symlink node_modules from the main repo so pre-commit hooks (prettier,
# lint-staged) work without a full `npm install`. The first real run found
# this is required — claude inside the worktree had to symlink manually
# before commits would pass. Doing it here saves the agent the work + tokens.
if [[ -d "$REPO/node_modules" ]] && [[ ! -e "$WORKTREE/node_modules" ]]; then
  ln -s "$REPO/node_modules" "$WORKTREE/node_modules"
  log "symlinked node_modules from main repo (for pre-commit hooks)"
fi

# 4. Compose the focused implementation prompt — single shot, no ralph loop.
# Include the full bead body so claude has all context inline.
BEAD_BODY=$(echo "$BEAD_INFO" | sed -n '/^DESCRIPTION$/,/^[A-Z][A-Z]*$/p' | head -200)

PROMPT_FILE="$WORKTREE/.auto-bead-prompt.md"
cat > "$PROMPT_FILE" <<EOF
# Headless bead execution — $BEAD

You are running in headless \`claude -p\` mode against a fresh git worktree.
Cwd: $WORKTREE
Branch: $BRANCH (off main)

## Bead

$BEAD_INFO

## Your task

1. Read the bead's DESCRIPTION above.
2. Implement it directly by editing files in this worktree. Make the smallest
   diff that satisfies the acceptance criteria. NO scope creep — if the bead
   says "add a single line", add a single line, nothing else.
3. Stage + commit your changes with a clear message referencing the bead ID.
4. Run \`/review\` against your diff. If it returns BLOCKER-grade issues,
   fix them and re-commit. If only nits/concerns, proceed.
5. Run \`/ship\` to push and open a PR. Use the bead title as the PR title.
6. Stop. Do NOT iterate forever — if you hit a blocker you can't resolve in
   2-3 attempts, leave a final commit message describing the blocker and exit.

## Constraints (Headless Decision Frame — see CLAUDE.md HEADLESS MODE)

- Do NOT modify files outside the bead's stated scope.
- Do NOT touch: lib/db/, drizzle/migrations/, lib/stripe/, lib/auth/,
  lib/payments/, pages/api/{auth,admin,webhooks}/. If the bead implies
  any of these, ABORT with a final commit explaining the scope mismatch.
- Decision frame for ambiguity: most robust + long-term + no imaginary
  overscope. Don't ask questions — pick the best default and document
  the choice in the commit message.

## Acceptance for this run

A real diff against main, a real commit, a real PR open against the
AestheticcNext repo. If you produce no diff, the run is a failure —
log the blocker explicitly so the morning report can surface it.
EOF

log "prompt written to $PROMPT_FILE"

# 5. Invoke claude -p. Cwd to the worktree so claude's tool calls
# (Bash, Edit, etc) operate on the right repo by default.
log "invoking claude -p (synchronous)"
cd "$WORKTREE"
claude --dangerously-skip-permissions --add-dir "$WORKTREE" -p "$(cat "$PROMPT_FILE")"
exit_code=$?

log "claude -p exited with code=$exit_code"
log "diff stat:"
git -C "$WORKTREE" diff --stat main..HEAD 2>&1 | tail -5 | sed 's/^/  /'
log "commits:"
git -C "$WORKTREE" log --oneline main..HEAD 2>&1 | head -5 | sed 's/^/  /'

# Cleanup the prompt file (don't leave it in the worktree)
rm -f "$PROMPT_FILE"

exit $exit_code
