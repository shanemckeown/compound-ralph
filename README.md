# Ralph Bead System

Autonomous bead execution using headless Claude loops.

## What Is This?

A bash-based system that executes beads (issues) from start to finish without human intervention. Based on the [Ralph Wiggum technique](https://ghuntley.com/ralph/) - a bash while loop that forces Claude to iterate until completion.

## Why Bash Loops?

**The problem:** When Claude runs interactively, it can "decide" to stop, summarize, and wait for input.

**The solution:** A bash while loop that forces continuation:

```bash
while ! grep "BEAD COMPLETE" status.md; do
  claude -p --dangerously-skip-permissions "$(cat RALPH_PROMPT.md)"
done
```

Claude literally cannot stop - bash just starts it again with the same prompt.

## Quick Start

```bash
# Full autonomous execution
./ralph-bead.sh LUCY-1234

# Run in background (walk away for hours)
./ralph-bead.sh LUCY-1234 --background

# Monitor a running loop
./monitor-ralph.sh LUCY-1234

# Stop gracefully
./cancel-ralph.sh LUCY-1234
```

## How It Works

### Phase 0: Bidirectional Prompting (Interactive)
Before generating any files, Claude asks 3-5 clarifying questions about:
- Constraints not mentioned in the bead description
- Design decisions that could go multiple ways
- Edge cases and error handling preferences
- Integration points with existing code
- What's explicitly out of scope

This surfaces assumptions early and ensures alignment.

### Phase 1: Setup (Interactive)
- Validates the bead exists and isn't blocked
- Creates a git worktree for isolated development
- Generates `spec.md` documenting design decisions from Phase 0
- Generates `implementation_plan.md` with phased checkbox tasks
- Uses Claude to analyze the bead and generate 8-15 tasks in `prd.json`
- Generates the stateless `RALPH_PROMPT.md`

### Phase 2: Ralph Loop (Headless)
- Runs in a bash while loop
- Each iteration:
  1. Reads state from files (prd.json, status.md)
  2. Finds next incomplete task
  3. Executes acceptance criteria
  4. Commits if passing
  5. Updates status
- Continues until all tasks complete or max iterations

### Phase 3: Completion (Interactive)
- Pushes branch to origin
- Creates PR with task summary
- Closes the bead with PR link

## Files Generated

```
~/.worktrees/AestheticcNext/LUCY-1234/
├── .compound/
│   ├── spec.md              # Design decisions (source of truth for "why")
│   ├── implementation_plan.md  # Checkbox tasks (source of truth for "what")
│   ├── prd.json             # Machine-readable task state
│   ├── RALPH_PROMPT.md      # Stateless prompt (same every iteration)
│   ├── status.md            # Iteration count, completion marker
│   ├── LEARNINGS.md         # Discoveries for this bead
│   ├── CODEBASE_PATTERNS.md # Critical patterns from previous Ralphs
│   ├── TESTING_GUIDE.md     # Two-phase testing documentation
│   └── logs/
│       ├── iteration-001.log
│       ├── iteration-002.log
│       └── latest.log -> iteration-002.log
```

### File Purposes

| File | Purpose | When to Read |
|------|---------|--------------|
| `CODEBASE_PATTERNS.md` | Critical patterns (APIs, quality gates, encryption) | **First** - before any code |
| `spec.md` | Design decisions, constraints, success criteria | Start of each iteration |
| `implementation_plan.md` | Phased task breakdown with checkboxes | Start of each iteration |
| `prd.json` | Machine-readable task state tracking | To check/update passes status |
| `LEARNINGS.md` | Discoveries specific to this bead | Start of each iteration |
| `TESTING_GUIDE.md` | Phase 1 (automated) / Phase 2 (manual) tests | When writing tests |
| `status.md` | Current iteration number, blockers | Start/end of iteration |

## Key Concepts

### Stateless Prompt
The prompt is the SAME every iteration. All state lives in files:
- `prd.json` - Which tasks are done
- `status.md` - Current iteration, blockers
- Git history - What's been committed
- Source files - Current implementation

### Machine-Verifiable Acceptance Criteria
Every task has criteria that can be checked programmatically:
- `Run \`npm run lint\` - exits with code 0`
- `File \`lib/schema.ts\` contains \`giftVouchers\``
- `POST /api/vouchers returns 201`

### Commit After Each Task
Progress is saved incrementally. If the loop crashes, you can resume with `--skip-setup`.

## Options

| Option | Description |
|--------|-------------|
| `--max-iterations N` | Limit iterations (default: 50) |
| `--background` | Run in tmux session |
| `--skip-setup` | Resume existing worktree |
| `--skip-complete` | Don't create PR |
| `--dry-run` | Show plan without executing |
| `--verbose` | Show Claude output in real-time |

## Monitoring

```bash
# Watch status in real-time
watch -n 5 'cat ~/.worktrees/AestheticcNext/LUCY-1234/.compound/status.md'

# Tail the latest log
tail -f ~/.worktrees/AestheticcNext/LUCY-1234/.compound/logs/latest.log

# Check task progress
jq '.tasks[] | {id, passes}' ~/.worktrees/AestheticcNext/LUCY-1234/.compound/prd.json

# Attach to tmux session
tmux attach -t ralph-LUCY-1234
```

## Stopping

```bash
# Graceful stop (finishes current iteration)
touch ~/.worktrees/AestheticcNext/LUCY-1234/.compound/STOP

# Or use the cancel script
./cancel-ralph.sh LUCY-1234

# Or kill tmux session
tmux kill-session -t ralph-LUCY-1234
```

## Two-Phase Testing

Ralph delivers **fully working features**, not just code that exists.

### Phase 1: Automated Tests (Ralph Does This)
- Tests written with mocked external services
- Acceptance criteria include `npm run test` commands
- All tests must PASS before Ralph marks complete
- Example: OAuth flow tested with mocked xero-node SDK

### Phase 2: Manual Tests (Human Does Later)
- Requires real accounts, real OAuth, human judgment
- Documented in `prd.json.manualTestingRequired` array
- Step-by-step instructions in `TESTING_GUIDE.md` Phase 2
- **BATCHED** - run all manual tests together after all Ralphs complete

### Why Two Phases?
| Benefit | Explanation |
|---------|-------------|
| Ralph efficiency | Automated tests catch bugs without human time |
| Human time batching | Manual tests for all integrations run together |
| Clear completion | Ralph knows when it's done (Phase 1 passes) |
| Quality assurance | Nothing ships without verification |

### Completion Criteria
A Ralph completes when:
- ✅ ALL Phase 1 automated tests pass
- ✅ ALL manual test scenarios documented
- ✅ TESTING_GUIDE.md Phase 2 filled in

A Ralph does NOT complete when:
- ❌ Tests exist but don't pass
- ❌ Code exists but isn't tested
- ❌ Manual testing requirements undocumented

## Safety

| Risk | Mitigation |
|------|------------|
| Infinite loop | `--max-iterations` flag |
| Bad code | Acceptance criteria gates + tests |
| Quality issues | Lint + test after each task |
| Wrong branch | Isolated worktree |
| Lost work | Commit after each task |
| Stuck on task | 3-attempt limit, move on |
| Runaway costs | Iteration limit |
| Untested features | Two-phase testing requirement |

## Inspiration

- [Ralph Wiggum technique](https://ghuntley.com/ralph/) by Geoffrey Huntley
- [Ralph Orchestrator](https://github.com/mikeyobrien/ralph-orchestrator)
- [Claude Code headless mode](https://docs.anthropic.com/en/docs/claude-code/cli-usage#headless-mode)

## Prerequisites

### Accept YOLO Mode (One-time Setup)

Before running headless Ralph loops, you must accept `--dangerously-skip-permissions` once:

```bash
# Run this ONCE in any terminal
claude --dangerously-skip-permissions

# Accept the safety warning when prompted
# Then exit (Ctrl+C)
```

This is a Claude CLI safety feature - it ensures you consciously accept headless mode before scripts can use it.

## Troubleshooting

### "--dangerously-skip-permissions must be accepted..."
Run the prerequisite step above to accept YOLO mode.

### "Setup not complete"
Run without `--skip-setup` to create the worktree and files.

### Loop stops immediately
Check `.compound/status.md` for blockers or errors.

### Claude exits with error
Check `.compound/logs/latest.log` for details. The loop will continue anyway.

### Tasks not progressing
Review acceptance criteria in `prd.json` - they may be too vague or impossible.

## Integration with Beads

This system integrates with the `bd` CLI:
- `bd show BEAD-ID` - Read bead details
- `bd update BEAD-ID --status in_progress` - Mark started
- `bd close BEAD-ID --reason "..."` - Mark complete
- `bd sync` - Push changes to remote
