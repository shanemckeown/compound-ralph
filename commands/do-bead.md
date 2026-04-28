# /do-bead - Autonomous Bead Execution

Execute a bead from planning through completion with full automation using headless Ralph loops.

## Usage
```
/do-bead LUCY-1234
/do-bead LUCY-1234 --background    # Run in tmux (walk away)
/do-bead LUCY-1234 --max-iterations 30
```

## Quick Start

When you invoke this command, I will run the ralph-bead system:

```bash
/Users/shane/Documents/GitReBase/compound-ralph/ralph-bead.sh {BEAD_ID} {OPTIONS}
```

## Arguments
- `BEAD_ID` (required): The bead ID to execute (e.g., LUCY-1234)
- `--background`: Run in tmux session (you can walk away)
- `--max-iterations N`: Override default 50 iteration limit
- `--dry-run`: Show what would be done without doing it
- `--skip-setup`: Resume existing worktree
- `--verbose`: Show Claude output in real-time

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: SETUP (Interactive)                                       │
│  Claude analyzes bead, creates worktree, generates prd.json         │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: RALPH LOOP (Headless)                                     │
│  while ! complete; do                                               │
│    claude -p --dangerously-skip-permissions "$(cat RALPH_PROMPT)"   │
│  done                                                               │
│  ────────────────────────────────────────────────────────────────── │
│  • Bash controls the loop (Claude can't choose to stop)             │
│  • Each iteration reads state from files (stateless prompt)         │
│  • Commits after each completed task                                │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3: COMPLETE (Interactive)                                    │
│  Create PR, close bead, notify                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Insight: Bash Controls Continuation

Unlike the previous approach where Claude could "decide" to stop, this system uses a **bash while loop** that forces continuation:

```bash
while ! grep "BEAD COMPLETE" status.md; do
  claude -p "$(cat RALPH_PROMPT.md)"
done
```

Claude literally cannot stop - bash just starts it again with the same prompt.

## Running in Background (Walk Away)

```bash
# Start in background tmux session
/do-bead LUCY-1234 --background

# Monitor progress
/Users/shane/Documents/GitReBase/compound-ralph/monitor-ralph.sh LUCY-1234

# Or attach to tmux
tmux attach -t ralph-LUCY-1234

# Stop gracefully
touch ~/.worktrees/AestheticcNext/LUCY-1234/.compound/STOP
```

## File Structure

After setup, the worktree contains:

```
~/.worktrees/AestheticcNext/LUCY-1234/
├── .compound/
│   ├── prd.json           # Tasks with acceptance criteria
│   ├── RALPH_PROMPT.md    # Stateless prompt for each iteration
│   ├── status.md          # Current status, completion marker
│   ├── LEARNINGS.md       # Accumulated discoveries
│   └── logs/
│       └── iteration-001.log
├── [source code]
└── [git worktree]
```

## Example

```
User: /do-bead LUCY-1234 --background

Lucy: Starting autonomous execution...

📋 Phase 1: Setup
   ✓ Bead validated: Gift Voucher System
   ✓ Worktree created: ~/.worktrees/AestheticcNext/LUCY-1234
   ✓ Generated 12 tasks in prd.json
   ✓ Bead marked as in_progress

🚀 Phase 2: Starting Ralph loop in background
   tmux session: ralph-LUCY-1234
   Max iterations: 50

   To monitor: /Users/shane/Documents/GitReBase/compound-ralph/monitor-ralph.sh LUCY-1234
   To stop: touch ~/.worktrees/AestheticcNext/LUCY-1234/.compound/STOP

✅ Background session started. You can walk away now!
```

## Safety

| Risk | Mitigation |
|------|------------|
| Infinite loop | `--max-iterations` (default: 50) |
| Bad code | Acceptance criteria must pass before commit |
| Quality issues | Lint runs after each task |
| Wrong branch | Isolated git worktree |
| Lost work | Commit after each completed task |
| Runaway | STOP file for graceful shutdown |

## Scripts Location

All scripts are in `/Users/shane/Documents/GitReBase/compound-ralph/`:

| Script | Purpose |
|--------|---------|
| `ralph-bead.sh` | Main entry point |
| `setup-bead.sh` | Phase 1: Create worktree, generate files |
| `ralph-loop.sh` | Phase 2: The headless bash loop |
| `complete-bead.sh` | Phase 3: Create PR, close bead |
| `monitor-ralph.sh` | Watch progress in real-time |
| `cancel-ralph.sh` | Stop a running loop |

## Requirements

- Bead must exist and not be closed
- Bead should have clear description/acceptance criteria
- No unresolved blocking dependencies
- `claude` CLI must be available
- `tmux` for background mode
