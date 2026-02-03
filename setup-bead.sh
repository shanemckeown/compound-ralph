#!/bin/bash
#
# setup-bead.sh - Phase 1: Prepare worktree and generate files for Ralph loop
#
# Usage: ./setup-bead.sh BEAD_ID [OPTIONS]
#
# This script runs INTERACTIVELY (with Claude) to:
# 1. Validate the bead
# 2. Create a git worktree
# 3. Generate prd.json with tasks
# 4. Generate the stateless RALPH_PROMPT.md
# 5. Initialize status and learnings files
#

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/templates"

# Project configuration (override via environment or .claude/settings.local.json)
PROJECT_NAME="${PROJECT_NAME:-AestheticcNext}"
PROJECT_PATH="${PROJECT_PATH:-/Users/shane/Documents/GitReBase/AestheticcNext}"
WORKTREE_BASE="${WORKTREE_BASE:-$HOME/.worktrees/$PROJECT_NAME}"
BASE_BRANCH="${BASE_BRANCH:-main}"
BEAD_PREFIX="${BEAD_PREFIX:-LUCY}"

# ============================================================================
# Argument Parsing
# ============================================================================

show_help() {
  cat << 'EOF'
setup-bead.sh - Prepare worktree and files for Ralph loop

USAGE:
  ./setup-bead.sh BEAD_ID [OPTIONS]

ARGUMENTS:
  BEAD_ID          The bead ID to set up (e.g., LUCY-1234)

OPTIONS:
  --max-iterations N   Set max iterations for the loop (default: 50)
  --skip-worktree      Don't create worktree (use existing)
  --dry-run            Show what would be done without doing it
  -h, --help           Show this help

EXAMPLES:
  ./setup-bead.sh LUCY-1234
  ./setup-bead.sh LUCY-1234 --max-iterations 30
  ./setup-bead.sh LUCY-1234 --skip-worktree

OUTPUT:
  Creates the following in {WORKTREE}/.compound/:
  - spec.md              Design decisions (source of truth for "why")
  - implementation_plan.md  Checkbox tasks (source of truth for "what")
  - prd.json             Machine-readable task state
  - RALPH_PROMPT.md      Stateless prompt for the loop
  - status.md            Status tracking file
  - LEARNINGS.md         Accumulated learnings
  - CODEBASE_PATTERNS.md Critical patterns from previous Ralphs
  - TESTING_GUIDE.md     Two-phase testing documentation
EOF
}

BEAD_ID=""
MAX_ITERATIONS=50
SKIP_WORKTREE=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      show_help
      exit 0
      ;;
    --max-iterations)
      MAX_ITERATIONS="$2"
      shift 2
      ;;
    --skip-worktree)
      SKIP_WORKTREE=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -*)
      echo "❌ Unknown option: $1" >&2
      exit 1
      ;;
    *)
      if [[ -z "$BEAD_ID" ]]; then
        BEAD_ID="$1"
      else
        echo "❌ Unexpected argument: $1" >&2
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$BEAD_ID" ]]; then
  echo "❌ Error: BEAD_ID is required" >&2
  echo "" >&2
  echo "Usage: ./setup-bead.sh BEAD_ID" >&2
  exit 1
fi

# ============================================================================
# Validation
# ============================================================================

echo "📋 Phase 1: Setup for $BEAD_ID"
echo "================================"
echo ""

# Check bead exists
echo "→ Validating bead..."
BEAD_INFO=$(bd show "$BEAD_ID" 2>/dev/null) || {
  echo "❌ Bead $BEAD_ID not found" >&2
  exit 1
}

# Extract bead details (bd show --json outputs JSON array, take first element)
BEAD_TITLE=$(bd show "$BEAD_ID" --json 2>/dev/null | jq -r '.[0].title' 2>/dev/null || echo "Unknown")
BEAD_STATUS=$(bd show "$BEAD_ID" --json 2>/dev/null | jq -r '.[0].status' 2>/dev/null || echo "unknown")
BEAD_DESC=$(bd show "$BEAD_ID" --json 2>/dev/null | jq -r '.[0].description // ""' 2>/dev/null || echo "")

# Check not already closed
if [[ "$BEAD_STATUS" == "closed" ]]; then
  echo "❌ Bead $BEAD_ID is already closed" >&2
  exit 1
fi

# Check for blockers (dependencies array with dependency_type == "blocks")
BLOCKERS=$(bd show "$BEAD_ID" --json 2>/dev/null | jq -r '.[0].dependencies[]? | select(.dependency_type == "blocks") | .id' 2>/dev/null || echo "")
if [[ -n "$BLOCKERS" ]]; then
  echo "⚠️  Warning: Bead has blocking dependencies:" >&2
  echo "$BLOCKERS" >&2
  read -p "Continue anyway? (y/n) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

echo "  ✓ Bead validated: $BEAD_TITLE"
echo ""

# ============================================================================
# Create Worktree
# ============================================================================

# Generate slug from title
SLUG=$(echo "$BEAD_TITLE" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | cut -c1-30)
BRANCH_NAME="feature/${BEAD_ID}-${SLUG}"
WORKTREE_PATH="$WORKTREE_BASE/$BEAD_ID"

if [[ "$SKIP_WORKTREE" == "true" ]]; then
  echo "→ Skipping worktree creation (--skip-worktree)"
  if [[ ! -d "$WORKTREE_PATH" ]]; then
    echo "❌ Worktree not found at $WORKTREE_PATH" >&2
    exit 1
  fi
else
  echo "→ Creating worktree..."

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY RUN] Would create: $WORKTREE_PATH"
    echo "  [DRY RUN] Branch: $BRANCH_NAME"
  else
    # Ensure we're in the project directory
    cd "$PROJECT_PATH"

    # Fetch latest
    git fetch origin "$BASE_BRANCH"

    # Check if worktree already exists
    if [[ -d "$WORKTREE_PATH" ]]; then
      echo "  ⚠️  Worktree already exists, using existing"
    else
      # Create worktree
      mkdir -p "$(dirname "$WORKTREE_PATH")"
      git worktree add "$WORKTREE_PATH" -b "$BRANCH_NAME" "origin/$BASE_BRANCH" 2>/dev/null || {
        # Branch might already exist
        git worktree add "$WORKTREE_PATH" "$BRANCH_NAME" 2>/dev/null || {
          echo "❌ Failed to create worktree" >&2
          exit 1
        }
      }
    fi

    # Install dependencies
    cd "$WORKTREE_PATH"
    if [[ -f "package.json" ]]; then
      echo "  → Installing dependencies..."
      npm install --silent
    fi
  fi

  echo "  ✓ Worktree ready: $WORKTREE_PATH"
fi

echo ""

# ============================================================================
# Initialize .compound directory
# ============================================================================

COMPOUND_DIR="$WORKTREE_PATH/.compound"

echo "→ Initializing .compound directory..."

if [[ "$DRY_RUN" == "true" ]]; then
  echo "  [DRY RUN] Would create: $COMPOUND_DIR/"
else
  mkdir -p "$COMPOUND_DIR/logs"
fi

# ============================================================================
# Phase 0: Bidirectional Prompting (Interactive)
# ============================================================================

echo "→ Phase 0: Bidirectional Planning"
echo "  Claude will ask clarifying questions to surface assumptions."
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
  echo "  [DRY RUN] Would run bidirectional prompting phase"
  echo "  [DRY RUN] Claude asks 3-5 clarifying questions"
  echo "  [DRY RUN] User answers inform spec.md generation"
else
  # Change to worktree for codebase exploration
  cd "$WORKTREE_PATH"

  # Bidirectional prompting - Claude asks questions first
  echo "  Claude is analyzing the bead and will ask clarifying questions..."
  echo ""

  claude -p "You are planning bead $BEAD_ID: $BEAD_TITLE

DESCRIPTION: $BEAD_DESC

WORKTREE: $WORKTREE_PATH

Before generating any files, you need to ask the user clarifying questions.
Explore the codebase first to understand the context, then ask 3-5 questions about:

1. **Constraints** - Technical limitations or requirements not mentioned
2. **Design decisions** - Choices that could go multiple ways (data structures, patterns, etc.)
3. **Edge cases** - Error handling preferences, validation rules
4. **Integration points** - How this connects to existing code
5. **Out of scope** - What explicitly should NOT be done

Ask specific, contextual questions based on what you find in the codebase.
Format each question clearly numbered.

After I answer, you will generate spec.md with our design decisions documented."

fi

echo ""

# ============================================================================
# Generate files using Claude
# ============================================================================

echo "→ Generating spec.md, implementation_plan.md, prd.json, and RALPH_PROMPT.md..."
echo ""
echo "  This step uses Claude interactively to analyze the codebase"
echo "  and generate appropriate tasks for the bead."
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
  echo "  [DRY RUN] Would invoke Claude to generate:"
  echo "    - $COMPOUND_DIR/spec.md"
  echo "    - $COMPOUND_DIR/implementation_plan.md"
  echo "    - $COMPOUND_DIR/prd.json"
  echo "    - $COMPOUND_DIR/RALPH_PROMPT.md"
  echo "    - $COMPOUND_DIR/status.md"
  echo "    - $COMPOUND_DIR/LEARNINGS.md"
else
  # Change to worktree
  cd "$WORKTREE_PATH"

  # Use Claude to generate the files
  # This is the interactive part - Claude analyzes the bead and codebase
  claude -p "You are setting up bead $BEAD_ID for autonomous execution.

BEAD DETAILS:
- ID: $BEAD_ID
- Title: $BEAD_TITLE
- Description: $BEAD_DESC
- Branch: $BRANCH_NAME
- Worktree: $WORKTREE_PATH

Based on our earlier discussion (bidirectional prompting), generate all files.

YOUR TASK:
1. Analyze the bead requirements
2. Explore the codebase to understand existing patterns
3. Generate 8-15 granular tasks with machine-verifiable acceptance criteria
4. Create the following files:

FILE 1: .compound/spec.md
Use template from $TEMPLATE_DIR/spec.md. Fill in:
- Problem Statement: Why this bead exists
- Constraints: Technical limitations discovered
- Design Decisions: Choices made during our Q&A
- Out of Scope: What we explicitly won't do
- Open Questions (Resolved): Our Q&A from bidirectional prompting
- Success Criteria: User-visible outcomes

FILE 2: .compound/implementation_plan.md
Use template from $TEMPLATE_DIR/implementation_plan.md. Create:
- Phased task breakdown with checkboxes
- Each task has ID matching prd.json (T-001, T-002, etc.)
- Dependencies noted between phases
- Human-readable companion to prd.json

FILE 3: .compound/prd.json
{
  \"bead_id\": \"$BEAD_ID\",
  \"branch\": \"$BRANCH_NAME\",
  \"worktree\": \"$WORKTREE_PATH\",
  \"max_iterations\": $MAX_ITERATIONS,
  \"started_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
  \"tasks\": [
    {
      \"id\": \"T-001\",
      \"title\": \"[Verb] [specific target]\",
      \"description\": \"1-2 sentences\",
      \"acceptanceCriteria\": [
        \"Run \\\`npm run test -- --testPathPattern=FEATURE\\\` - exits with code 0\",
        \"File \\\`path\\\` contains \\\`text\\\`\"
      ],
      \"passes\": false,
      \"notes\": \"\"
    }
  ],
  \"qualityChecks\": [
    \"npm run lint -- --quiet\",
    \"npm run test -- --testPathPattern=FEATURE --passWithNoTests\"
  ],
  \"manualTestingRequired\": [
    {
      \"scenario\": \"[Scenario name]\",
      \"reason\": \"[Why this cannot be automated - e.g., requires real OAuth, real account, human judgment]\",
      \"steps\": \"See TESTING_GUIDE.md Phase 2\"
    }
  ]
}

FILE 4: .compound/status.md
---
iteration: 0
max_iterations: $MAX_ITERATIONS
started_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
last_updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
status: ready
---

## Current Task
None - setup complete

## Blockers
None

## Progress
[Will be updated by Ralph loop]

## Completion
[Write BEAD COMPLETE here when done]

FILE 5: .compound/LEARNINGS.md
# Learnings - $BEAD_ID

> Auto-updated during execution. Read at start of each iteration.

## Database
<!-- DB patterns, connection issues, etc. -->

## API Patterns
<!-- Endpoint patterns, middleware, etc. -->

## Build/Tooling
<!-- TypeScript, lint, build discoveries -->

## Codebase Patterns
<!-- Existing patterns to follow -->

FILE 6: .compound/RALPH_PROMPT.md
Copy the template from $TEMPLATE_DIR/RALPH_PROMPT.md and replace:
- {{BEAD_ID}} with $BEAD_ID
- {{BEAD_TITLE}} with $BEAD_TITLE
- {{BEAD_DESCRIPTION}} with the description
- {{BRANCH_NAME}} with $BRANCH_NAME
- {{WORKTREE_PATH}} with $WORKTREE_PATH
- {{MAX_ITERATIONS}} with $MAX_ITERATIONS
- {{ITERATION}} with the literal text \${ITERATION} (will be replaced at runtime)
- {{STARTED_AT}} with $(date -u +%Y-%m-%dT%H:%M:%SZ)

FILE 7: .compound/CODEBASE_PATTERNS.md
Copy from $TEMPLATE_DIR/CODEBASE_PATTERNS.md verbatim. This contains critical patterns
discovered from previous Ralph executions (API patterns, quality gates, encryption, etc.)

FILE 8: .compound/TESTING_GUIDE.md
Create a testing guide with:
- Prerequisites section (login, test data needed)
- Test scenarios derived from spec.md Success Criteria
- Each scenario has {{PLACEHOLDER}} markers for steps (to be filled by Ralph during UI tasks)
- Edge cases section
- Structure like:
  ### Scenario 1: [Success Criterion]
  **Goal:** [What user should achieve]
  **Starting point:** [URL or screen]
  | Step | Action | Expected Result |
  |------|--------|-----------------|
  | 1 | {{ACTION}} | {{EXPECTED}} |
  | 2 | {{ACTION}} | {{EXPECTED}} |

IMPORTANT:
- spec.md is the source of truth for DESIGN (why we're doing this)
- implementation_plan.md is the source of truth for TASKS (what to do)
- prd.json is machine-readable state tracking
- Tasks must have MACHINE-VERIFIABLE acceptance criteria
- Use patterns: \"Run \\\`cmd\\\` - exits with code 0\", \"File \\\`path\\\` contains \\\`text\\\`\"
- 8-15 tasks, ordered by dependency (schema before API, API before UI)
- FINAL TASK must be "Complete front-end testing guide" (fill in TESTING_GUIDE.md)
- Each task should do ONE thing

Write all 7 files now."

  # Verify files were created
  for file in spec.md implementation_plan.md prd.json status.md LEARNINGS.md RALPH_PROMPT.md CODEBASE_PATTERNS.md TESTING_GUIDE.md; do
    if [[ ! -f "$COMPOUND_DIR/$file" ]]; then
      echo "❌ Failed to create $file" >&2
      exit 1
    fi
  done

  echo "  ✓ Files generated"
fi

echo ""

# ============================================================================
# Update bead status
# ============================================================================

echo "→ Updating bead status..."

if [[ "$DRY_RUN" == "true" ]]; then
  echo "  [DRY RUN] Would run: bd update $BEAD_ID --status in_progress"
else
  bd update "$BEAD_ID" --status in_progress --notes "Autonomous execution started via ralph-bead"
  echo "  ✓ Bead marked as in_progress"
fi

echo ""

# ============================================================================
# Summary
# ============================================================================

echo "================================"
echo "✅ Setup complete!"
echo ""
echo "  Bead:      $BEAD_ID"
echo "  Branch:    $BRANCH_NAME"
echo "  Worktree:  $WORKTREE_PATH"
echo "  Max iter:  $MAX_ITERATIONS"
echo ""
echo "Next steps:"
echo "  1. Review generated files in $COMPOUND_DIR/"
echo "  2. Run the Ralph loop:"
echo "     ./ralph-loop.sh $BEAD_ID"
echo ""
echo "  Or run everything:"
echo "     ./ralph-bead.sh $BEAD_ID --skip-setup"
echo ""

# Output path for other scripts to use
echo "$WORKTREE_PATH" > "$COMPOUND_DIR/.worktree_path"
