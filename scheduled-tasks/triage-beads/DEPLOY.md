# Deploy triage-beads to hermes-phase0

## Prerequisites
- SSH access: `ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29`
- `bd` installed on VPS (already there: v1.0.0 at /usr/local/bin/bd)
- Both repos cloned on VPS (already there)

## Step 1: Copy skill prompt to VPS

```bash
scp -i ~/.ssh/id_ed25519_aestheticc \
    ~/.claude/scheduled-tasks/triage-beads/SKILL.md \
    hermes@91.99.204.29:/home/hermes/hermes/ops-tasks/triage-beads.prompt.md
```

## Step 2: Copy run.sh to VPS

```bash
scp -i ~/.ssh/id_ed25519_aestheticc \
    ~/.claude/scheduled-tasks/triage-beads/run.sh \
    hermes@91.99.204.29:/home/hermes/hermes/ops-tasks/triage-beads.run.sh

# Make executable
ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
    'chmod +x /home/hermes/hermes/ops-tasks/triage-beads.run.sh'
```

## Step 3: Install systemd units (as root)

```bash
scp -i ~/.ssh/id_ed25519_aestheticc \
    ~/.claude/scheduled-tasks/triage-beads/hermes-triage-beads.service \
    ~/.claude/scheduled-tasks/triage-beads/hermes-triage-beads.timer \
    root@91.99.204.29:/etc/systemd/system/

ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29 << 'SSH'
systemctl daemon-reload
systemctl enable hermes-triage-beads.timer
systemctl start hermes-triage-beads.timer
systemctl list-timers hermes-triage-beads.timer
SSH
```

## Step 4: Test manually

```bash
ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29 \
    'sudo -u hermes bash /home/hermes/hermes/ops-tasks/triage-beads.run.sh'
```

Watch the run:
```bash
ssh -i ~/.ssh/id_ed25519_aestheticc root@91.99.204.29 \
    'sudo journalctl -u hermes-triage-beads.service -f'
```

## Step 5: Verify

Check Slack #social for the triage summary.
Check the vault: `Aestheticc/Ops/BEAD_TRIAGE.md` should have today's entry.
Check labels: `BEADS_DIR=/home/hermes/repos/obsidian/.beads bd label list-all` should show auto/decision/call.

## To force re-run (if already ran today)

```bash
ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
    'rm /home/hermes/hermes/state/triage-beads-last-run.txt'
```
