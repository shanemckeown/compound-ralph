# Deploy work-bead to hermes-phase0

## Prerequisites
- SSH access: `ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29`
- `bd` installed on VPS (already there: v1.0.0 at /usr/local/bin/bd)
- `gh` installed and authenticated on VPS (already there: v2.89.0)
- AestheticcNext repo cloned on VPS (already there)
- npm + node installed on VPS (already there: Node 20.20.2)

## Step 1: Ensure worktree directory exists on VPS

```bash
ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
    'mkdir -p /home/hermes/worktrees'
```

## Step 2: Copy skill prompt to VPS

```bash
scp -i ~/.ssh/id_ed25519_aestheticc \
    ~/.claude/scheduled-tasks/work-bead/SKILL.md \
    hermes@91.99.204.29:/home/hermes/hermes/ops-tasks/work-bead.prompt.md
```

## Step 3: Copy run.sh to VPS

```bash
scp -i ~/.ssh/id_ed25519_aestheticc \
    ~/.claude/scheduled-tasks/work-bead/run.sh \
    hermes@91.99.204.29:/home/hermes/hermes/ops-tasks/work-bead.run.sh

ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
    'chmod +x /home/hermes/hermes/ops-tasks/work-bead.run.sh'
```

## Step 4: Verify gh auth on VPS

```bash
ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
    'gh auth status'
```

If not authenticated:
```bash
ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29
# Then interactively: gh auth login
```

## Step 5: Test manually with a safe bead

Pick an XS/S AUTO bead that's non-customer-facing (e.g. docs, tests, internal tooling):

```bash
ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29 \
    'sudo -u hermes bash /home/hermes/hermes/ops-tasks/work-bead.run.sh <BEAD_ID>'
```

Watch live:
```bash
ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29 \
    'tail -f /home/hermes/hermes/logs/work-bead-*.log'
```

## Step 6: Verify

- Check Slack #social for the completion message
- Check `gh pr list` for the new PR
- Verify the PR links the bead and has the right description
- Check the worktree: `ls /home/hermes/worktrees/<BEAD_ID>/`

## Running locally (Mac)

For local testing without deploying to VPS:

```bash
# Set up paths
export AESTHETICCNEXT_DIR=/Users/shane/Documents/GitReBase/AestheticcNext
export BEADS_DIR=$AESTHETICCNEXT_DIR/.beads  # or Obsidian .beads for LUCY- beads

# Validate bead
bd show <BEAD_ID>

# Create worktree
BRANCH=hermes/<BEAD_ID>
WORKTREE=~/.worktrees/AestheticcNext/<BEAD_ID>
cd $AESTHETICCNEXT_DIR
git worktree add -b $BRANCH $WORKTREE main
cd $WORKTREE && npm install

# Invoke directly
cd $WORKTREE
claude -p \
    --dangerously-skip-permissions \
    --max-budget-usd 2.00 \
    --model sonnet \
    "$(cat ~/.claude/scheduled-tasks/work-bead/SKILL.md)

## Bead Details

\$(bd show <BEAD_ID>)

## Parameters
- BEAD_ID: <BEAD_ID>
- BRANCH_NAME: $BRANCH
- BEADS_DIR: $BEADS_DIR
"
```

## Overrides

Environment variables (set before running):
- `WORK_BEAD_BUDGET` — max API spend per run (default: $2.00)
- `WORK_BEAD_MODEL` — Claude model (default: sonnet)

## Cleanup

Worktrees are NOT auto-deleted after runs (preserved for PR review and debugging).
To clean up after a PR is merged:

```bash
# On VPS:
cd /home/hermes/repos/AestheticcNext
git worktree remove /home/hermes/worktrees/<BEAD_ID>
git branch -d hermes/<BEAD_ID>

# On Mac:
cd ~/Documents/GitReBase/AestheticcNext
git worktree remove ~/.worktrees/AestheticcNext/<BEAD_ID>
git branch -d hermes/<BEAD_ID>
```

## No systemd timer

This skill is on-demand (invoked manually or by the triage follow-up), not cron-scheduled.
No .service or .timer files needed.
