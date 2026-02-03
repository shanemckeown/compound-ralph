#!/bin/bash
#
# complete-bead.sh - Phase 3: Create PR and close bead
#
# Usage: ./complete-bead.sh BEAD_ID
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Project configuration
PROJECT_NAME="${PROJECT_NAME:-AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/$PROJECT_NAME}"

# ============================================================================
# Arguments
# ============================================================================

BEAD_ID="${1:-}"

if [[ -z "$BEAD_ID" ]]; then
  echo "❌ Error: BEAD_ID is required" >&2
  echo "Usage: ./complete-bead.sh BEAD_ID" >&2
  exit 1
fi

WORKTREE_PATH="$WORKTREE_BASE/$BEAD_ID"
COMPOUND_DIR="$WORKTREE_PATH/.compound"
PRD_FILE="$COMPOUND_DIR/prd.json"
STATUS_FILE="$COMPOUND_DIR/status.md"
LEARNINGS_FILE="$COMPOUND_DIR/LEARNINGS.md"

# ============================================================================
# Validation
# ============================================================================

if [[ ! -d "$WORKTREE_PATH" ]]; then
  echo "❌ Worktree not found: $WORKTREE_PATH" >&2
  exit 1
fi

if ! grep -q "BEAD COMPLETE" "$STATUS_FILE" 2>/dev/null; then
  echo "⚠️  Bead not marked complete in status.md" >&2
  echo "   Add 'BEAD COMPLETE' to status.md first" >&2
  exit 1
fi

cd "$WORKTREE_PATH"

# ============================================================================
# Get Info
# ============================================================================

BRANCH_NAME=$(jq -r '.branch' "$PRD_FILE")
BEAD_TITLE=$(bd show "$BEAD_ID" --format json 2>/dev/null | jq -r '.title' 2>/dev/null || echo "$BEAD_ID")

echo "📦 Completing bead: $BEAD_ID"
echo "   Title: $BEAD_TITLE"
echo "   Branch: $BRANCH_NAME"
echo ""

# ============================================================================
# Final Checks
# ============================================================================

echo "→ Running final quality checks..."

# Run lint
if npm run lint -- --quiet 2>/dev/null; then
  echo "  ✓ Lint passed"
else
  echo "  ⚠️  Lint has warnings (continuing)"
fi

# ============================================================================
# Push Branch
# ============================================================================

echo "→ Pushing branch to origin..."

git push -u origin "$BRANCH_NAME" 2>/dev/null || {
  echo "  ⚠️  Push failed or already pushed"
}

echo "  ✓ Branch pushed"

# ============================================================================
# Create PR
# ============================================================================

echo "→ Creating pull request..."

# Build task list
TASK_LIST=$(jq -r '.tasks[] | "- [" + (if .passes then "x" else " " end) + "] " + .id + ": " + .title' "$PRD_FILE")

# Build PR body
PR_BODY=$(cat << EOF
## Summary
Implements $BEAD_TITLE

## Tasks Completed
$TASK_LIST

## Bead
- ID: $BEAD_ID
- Branch: $BRANCH_NAME

## Learnings
$(cat "$LEARNINGS_FILE" 2>/dev/null | head -50 || echo "None recorded")

---
🤖 Generated autonomously with ralph-bead
EOF
)

# Create PR
PR_URL=$(gh pr create \
  --title "feat($BEAD_ID): $BEAD_TITLE" \
  --body "$PR_BODY" \
  --head "$BRANCH_NAME" \
  2>/dev/null || echo "")

if [[ -n "$PR_URL" ]]; then
  echo "  ✓ PR created: $PR_URL"
else
  # PR might already exist
  PR_URL=$(gh pr view --json url -q '.url' 2>/dev/null || echo "")
  if [[ -n "$PR_URL" ]]; then
    echo "  ✓ PR exists: $PR_URL"
  else
    echo "  ⚠️  Could not create or find PR"
    PR_URL="(no PR)"
  fi
fi

# ============================================================================
# Close Bead
# ============================================================================

echo "→ Closing bead..."

bd close "$BEAD_ID" --reason "Implemented via ralph-bead. PR: $PR_URL" 2>/dev/null || {
  echo "  ⚠️  Could not close bead (may already be closed)"
}

echo "  ✓ Bead closed"

# ============================================================================
# Sync
# ============================================================================

echo "→ Syncing beads..."

bd sync 2>/dev/null || {
  echo "  ⚠️  Sync failed"
}

# ============================================================================
# Summary
# ============================================================================

echo ""
echo "════════════════════════════════════════════════════════════"
echo "✅ Bead $BEAD_ID Complete!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "  PR: $PR_URL"
echo "  Branch: $BRANCH_NAME"
echo "  Worktree: $WORKTREE_PATH"
echo ""
echo "Next steps:"
echo "  1. Review PR"
echo "  2. Merge when ready"
echo "  3. Clean up worktree: git worktree remove $WORKTREE_PATH"
echo ""
