---
description: Start the ops monitoring daemon — sets up all recurring scheduled tasks for platform health, MRR, Sentry, campaigns, pipeline, and codebase monitoring. Run this once in a dedicated terminal session.
---

# Ops Daemon Setup

You are starting the Aestheticc ops monitoring daemon. This session will stay open and idle while scheduled tasks fire automatically in the background.

## Step 0 — Verify permissions

First, check that this session was started with `--dangerously-skip-permissions`. If permission prompts appear during task setup or execution, the daemon will stall and lose its autonomous nature.

If you see permission prompts, tell Shane:
> "This session needs to be restarted with: `claude --dangerously-skip-permissions` then run `/ops-daemon` again."

## What to do

Set up the following scheduled tasks using the CronCreate tool. For each task, read the prompt from the corresponding SKILL.md file at `~/.claude/scheduled-tasks/<task-name>/SKILL.md` and use that as the prompt for CronCreate.

### P0 — Create these first

1. **mrr-tracker** — Cron: `0 7 * * *` (7am daily), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/mrr-tracker/SKILL.md`

2. **morning-briefing** — Cron: `45 8 * * *` (8:45am daily), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/morning-briefing/SKILL.md`

3. **sentry-triage** — Cron: `0 */6 * * *` (every 6 hours), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/sentry-triage/SKILL.md`

### P1 — Create these next

4. **email-campaign-health** — Cron: `30 8 * * *` (8:30am daily), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/email-campaign-health/SKILL.md`

5. **email-triage** (morning) — Cron: `0 8 * * *` (8am daily), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/email-triage/SKILL.md`

6. **email-triage** (evening) — Cron: `0 18 * * *` (6pm daily), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/email-triage/SKILL.md`

7. **weekly-pipeline** — Cron: `0 9 * * 1` (Monday 9am), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/weekly-pipeline/SKILL.md`

8. **email-archive** — Cron: `0 8 * * 1` (Monday 8am), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/email-archive/SKILL.md`

### P2/P3 — Create these last

8. **codebase-health** — Cron: `0 20 * * 0` (Sunday 8pm), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/codebase-health/SKILL.md`

7. **competitor-sweep** — Cron: `0 10 * * 3` (Wednesday 10am), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/competitor-sweep/SKILL.md`

8. **help-insights** — Cron: `0 9 * * 1` (Monday 9am, after weekly-pipeline), recurring=true
   - Read prompt from `~/.claude/scheduled-tasks/help-insights/SKILL.md`

### Meta — Self-renewal (CRITICAL)

9. **ops-renewal** — Cron: `0 6 */2 * *` (every 2 days at 6am), recurring=true
   - Prompt: "Cron tasks expire after 3 days. Cancel all existing scheduled tasks (use CronList to find them, then CronDelete for each). Then re-read all SKILL.md files from ~/.claude/scheduled-tasks/ and recreate all 9 tasks plus this renewal task using CronCreate. Use the same cron expressions as defined in each task's original setup. After recreating, use CronList to confirm all 10 tasks exist."

## After setup

1. Use CronList to confirm all 10 tasks are registered
2. Report the task IDs and next fire times
3. Tell Shane the daemon is running and he can leave this session idle
4. Remind him: if this session ever dies, just run `/ops-daemon` again in a new Termius session

## Important notes

- **Start command:** `claude --dangerously-skip-permissions` then `/ops-daemon`
- This session must stay open and idle for tasks to fire
- Tasks fire between turns — they wait if Claude is mid-response
- All times are local timezone
- The self-renewal task at 6am every 2 days prevents the 3-day expiry from killing everything
- If Shane sends a message in this session, tasks queue until the response finishes — that's fine, but he should use Ghostty for real work
- The `--dangerously-skip-permissions` flag is safe here because this session ONLY runs trusted ops prompts from SKILL.md files we wrote
