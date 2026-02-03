# Ralph Bead System Architecture

## Overview

A headless automation system for executing beads from start to finish without human intervention.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ralph-bead.sh BEAD-ID                        │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 0: BIDIRECTIONAL PROMPTING (Interactive)                     │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ 1. Claude explores codebase for context                        │ │
│  │ 2. Claude asks 3-5 clarifying questions                        │ │
│  │ 3. User answers surface assumptions and constraints            │ │
│  │ 4. Design decisions documented for spec.md                     │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 1: SETUP (Interactive - runs once)                           │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ 1. Validate bead exists and is not blocked                     │ │
│  │ 2. Create git worktree: ~/.worktrees/Project/BEAD-ID           │ │
│  │ 3. Generate spec.md (design decisions from Phase 0)            │ │
│  │ 4. Generate implementation_plan.md (phased checkbox tasks)     │ │
│  │ 5. Generate prd.json with 8-15 machine-verifiable tasks        │ │
│  │ 6. Generate RALPH_PROMPT.md (stateless prompt)                 │ │
│  │ 7. Initialize status.md and LEARNINGS.md                       │ │
│  │ 8. Update bead to in_progress                                  │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 2: RALPH LOOP (Headless - runs until complete)               │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ while ! grep "BEAD COMPLETE" status.md; do                     │ │
│  │   claude -p --dangerously-skip-permissions \                   │ │
│  │     --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \          │ │
│  │     "$(cat .compound/RALPH_PROMPT.md)"                         │ │
│  │   ((iteration++))                                              │ │
│  │   [ $iteration -gt $MAX_ITER ] && break                        │ │
│  │ done                                                           │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  PHASE 3: COMPLETION (Interactive - runs once)                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │ 1. Push branch to origin                                       │ │
│  │ 2. Create PR with summary                                      │ │
│  │ 3. Close bead with PR link                                     │ │
│  │ 4. Send notification (optional)                                │ │
│  │ 5. Clean up status files                                       │ │
│  └────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

## File Structure

```
~/.claude/scripts/ralph-bead/
├── ARCHITECTURE.md          # This file
├── ralph-bead.sh            # Main entry point
├── setup-bead.sh            # Phase 0+1: Bidirectional prompting & file generation
├── ralph-loop.sh            # Phase 2: The actual headless loop
├── complete-bead.sh         # Phase 3: PR creation & cleanup
├── monitor-ralph.sh         # Helper: Watch progress in real-time
├── cancel-ralph.sh          # Helper: Stop a running loop
└── templates/
    ├── RALPH_PROMPT.md      # Template for stateless prompt
    ├── spec.md              # Template for design decisions
    └── implementation_plan.md  # Template for checkbox tasks

{WORKTREE}/.compound/
├── spec.md                  # Design decisions (source of truth for "why")
├── implementation_plan.md   # Phased checkbox tasks (source of truth for "what")
├── prd.json                 # Machine-readable task state
├── RALPH_PROMPT.md          # Generated stateless prompt for this bead
├── status.md                # Current status (iteration, blockers, completion)
├── LEARNINGS.md             # Accumulated discoveries
└── logs/
    └── iteration-{N}.log    # Output from each iteration
```

## Key Files

### 0. spec.md (Design Decisions)

The source of truth for WHY we're doing this and HOW we decided to do it.

```markdown
# Spec: LUCY-1234 - Add Gift Vouchers

## Problem Statement
Clients want to purchase gift vouchers...

## Constraints
- Must integrate with existing payment system
- Cannot modify existing checkout flow

## Design Decisions
| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | Extend products table | Vouchers are product-like |
| Redemption | New endpoint | Keep checkout clean |

## Out of Scope
- Physical voucher cards
- Multiple redemptions per voucher

## Open Questions (Resolved)
1. Q: Email delivery or in-app? A: Email first, in-app later
```

### 0b. implementation_plan.md (Checkbox Tasks)

Human-readable phased breakdown. Each checkbox maps to a prd.json task.

```markdown
# Implementation Plan: LUCY-1234

## Phase 1: Foundation
- [ ] **T-001**: Create vouchers table migration
- [ ] **T-002**: Generate TypeScript types

## Phase 2: API Layer
- [ ] **T-003**: GET /api/vouchers endpoint
- [ ] **T-004**: POST /api/vouchers/purchase endpoint
```

### 1. prd.json (Task Tracking)

```json
{
  "bead_id": "LUCY-1234",
  "branch": "feature/LUCY-1234-feature-name",
  "worktree": "~/.worktrees/AestheticcNext/LUCY-1234",
  "tasks": [
    {
      "id": "T-001",
      "title": "Create database migration",
      "acceptanceCriteria": [
        "File `lib/schema.ts` contains `myNewTable`",
        "Run `npm run db:generate` - exits with code 0"
      ],
      "passes": false,
      "notes": ""
    }
  ],
  "qualityChecks": ["npm run lint -- --quiet"]
}
```

### 2. status.md (Loop Control)

```markdown
---
iteration: 5
max_iterations: 50
started_at: 2026-01-23T10:00:00Z
last_updated: 2026-01-23T10:15:00Z
status: running
---

## Current Task
T-003: Implement API endpoint

## Blockers
None

## Progress
- [x] T-001: Database migration (3 iterations)
- [x] T-002: Schema types (1 iteration)
- [ ] T-003: API endpoint (in progress)

## Completion
<!-- Write "BEAD COMPLETE" here when all tasks pass -->
```

### 3. RALPH_PROMPT.md (Stateless Prompt)

The prompt that gets fed EVERY iteration. Must be:
- Self-contained (no memory between iterations)
- File-driven (reads all state from files)
- Machine-verifiable (clear pass/fail criteria)

See `templates/RALPH_PROMPT.md` for full template.

## Safety Mechanisms

| Risk | Mitigation |
|------|------------|
| Infinite loop | `--max-iterations` flag (default: 50) |
| Bad code | Acceptance criteria must pass before marking task complete |
| Quality issues | `qualityChecks` run after each task |
| Wrong branch | Isolated git worktree |
| Lost work | Commit after each completed task |
| Runaway costs | Iteration limit + optional cost tracking |
| Stuck on task | 5-attempt limit per task, then mark blocked |

## Usage

### Basic Usage
```bash
# Start a bead (runs in foreground)
./ralph-bead.sh LUCY-1234

# Start in background with tmux
./ralph-bead.sh LUCY-1234 --background

# Monitor progress
./monitor-ralph.sh LUCY-1234

# Cancel a running loop
./cancel-ralph.sh LUCY-1234
```

### Options
```
--max-iterations N    Maximum iterations (default: 50)
--background          Run in tmux session
--dry-run             Setup only, don't start loop
--skip-setup          Skip setup, resume existing loop
--notify slack        Send Slack notification on completion
```

## Integration with Beads

The system integrates with the beads issue tracker:

1. **Start**: `bd update BEAD-ID --status in_progress`
2. **During**: Notes updated with progress
3. **Complete**: `bd close BEAD-ID --reason "PR: {url}"`
4. **Blocked**: `bd update BEAD-ID --status blocked --notes "..."`

## Monitoring

### Real-time progress
```bash
# Watch status file
watch -n 5 'cat ~/.worktrees/AestheticcNext/LUCY-1234/.compound/status.md'

# Watch iteration logs
tail -f ~/.worktrees/AestheticcNext/LUCY-1234/.compound/logs/iteration-*.log

# Check task completion
jq '.tasks[] | {id, title, passes}' ~/.worktrees/AestheticcNext/LUCY-1234/.compound/prd.json
```

### From Claude Code
```bash
# Check what's running
ps aux | grep ralph-bead

# Check tmux sessions
tmux list-sessions
```
