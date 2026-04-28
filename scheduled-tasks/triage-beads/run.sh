#!/usr/bin/env bash
# triage-beads runner — thin wrapper for hermes-phase0
# Invoked by hermes-triage-beads.timer at 07:30 Europe/London daily
# All judgment lives in SKILL.md — this script only does deterministic orchestration.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source /home/hermes/hermes/config.env

LOG_DIR="/home/hermes/hermes/logs"
LOG_FILE="${LOG_DIR}/triage-beads-$(date +%Y-%m-%d).log"
STATE_DIR="/home/hermes/hermes/state"
STATE_FILE="${STATE_DIR}/triage-beads-last-run.txt"

OBSIDIAN_DIR="/home/hermes/repos/obsidian"
AESTHETICCNEXT_DIR="/home/hermes/repos/AestheticcNext"

mkdir -p "$LOG_DIR" "$STATE_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== triage-beads run started $(date -Iseconds) ==="

# --- Dedup: skip if already ran today ---
TODAY=$(date +%Y-%m-%d)
if [[ -f "$STATE_FILE" ]] && grep -q "$TODAY" "$STATE_FILE" 2>/dev/null; then
    echo "Already ran today ($TODAY). Skipping."
    /home/hermes/hermes/bin/post.sh "BEAD TRIAGE — $TODAY — already ran today, skipping."
    exit 0
fi

# --- Pull latest from both repos ---
echo "Pulling obsidian vault..."
cd "$OBSIDIAN_DIR" && git pull --ff-only 2>&1 || echo "WARN: obsidian pull failed"

echo "Pulling AestheticcNext..."
cd "$AESTHETICCNEXT_DIR" && git pull --ff-only 2>&1 || echo "WARN: AestheticcNext pull failed"

# --- Resolve skill prompt (Mac path -> VPS path rewrite) ---
SKILL_FILE="/home/hermes/hermes/ops-tasks/triage-beads.prompt.md"
if [[ ! -f "$SKILL_FILE" ]]; then
    echo "ERROR: Skill file not found at $SKILL_FILE"
    /home/hermes/hermes/bin/post.sh "BEAD TRIAGE — ERROR: skill file missing at $SKILL_FILE"
    exit 1
fi

# --- Export paths for the skill prompt ---
export OBSIDIAN_DIR
export AESTHETICCNEXT_DIR

# --- Invoke Claude with the skill prompt ---
echo "Invoking Claude (model: sonnet, budget: $0.75)..."
cd /home/hermes
claude -p \
    --dangerously-skip-permissions \
    --max-budget-usd 0.75 \
    --model sonnet \
    --add-dir "$OBSIDIAN_DIR" \
    --add-dir "$AESTHETICCNEXT_DIR" \
    "$(cat "$SKILL_FILE")" \
    2>&1 || {
        echo "ERROR: Claude invocation failed (exit $?)"
        /home/hermes/hermes/bin/post.sh "BEAD TRIAGE — ERROR: Claude failed. Check logs."
        exit 1
    }

# --- Record successful run ---
echo "$TODAY" > "$STATE_FILE"

# --- Commit vault changes (triage log) ---
cd "$OBSIDIAN_DIR"
if [[ -n "$(git status --porcelain Aestheticc/Ops/BEAD_TRIAGE.md 2>/dev/null)" ]]; then
    echo "Committing triage log..."
    git add Aestheticc/Ops/BEAD_TRIAGE.md
    git -c user.name="Hermes (hermes-phase0)" \
        -c user.email="hermes@aestheti.cc" \
        commit -m "triage: daily bead classification $(date +%Y-%m-%d)"
    git push 2>&1 || echo "WARN: obsidian push failed"
fi

echo "=== triage-beads run completed $(date -Iseconds) ==="
