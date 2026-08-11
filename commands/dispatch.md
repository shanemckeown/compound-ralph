# /dispatch — fire the auto-bead pipeline on demand from laptop

Single-command "build this thing for me, walk away" capability. Same harness pipeline as the overnight night-batch (Gate 1 eligibility → headless `claude -p` build → Gate 2 path/diff guard → Gate 3 Codex adversarial review → PR), but fires NOW rather than waiting for 22:00 BST.

LUCY-ceg7. Closes the gap between "I have a task right now" and "I want it built without sitting at the keyboard."

## When to use

- Shane has a discrete development task he wants done hands-off but doesn't want to wait until tomorrow's morning-review.
- The task fits the **auto-eligible** scope: no schema, no payments, no auth, no deletes, no migrations, ≤200 LOC, P2-P4 only. If it doesn't fit that scope, use a fresh chat or `/do-bead` instead — `/dispatch` will hard-fail.

## What this skill does

1. **Read the task description from Shane's invocation.** Either:
   - Inline: `/dispatch Add CSV export to Reports — see Aestheticc/Reference/REPORTS.md, must respect business_id RLS`
   - Or interactive: `/dispatch` (no args) — ask Shane for the task title + context.

2. **Create a bead** with the description + context, labelled `auto-eligible`:
   ```bash
   BEAD_ID=$(BEADS_DIR=/Users/shane/Documents/GitReBase/AestheticcNext/.beads bd create \
     --title "<task title from Shane's input>" \
     --description "<full task description + any reference paths from input>" \
     --type=task --priority=2 \
     --label=auto-eligible \
     --label=dispatch \
     2>&1 | grep -oE 'AestheticcNext-[a-z0-9]+' | head -1)
   ```
   Need `dangerouslyDisableSandbox: true` for `bd` calls.

3. **Run Gate 1 eligibility validator.** Hard-fail if it returns non-zero — don't bypass:
   ```bash
   if ! ~/.claude/hooks/night-batch/check-eligibility.sh "$BEAD_ID" >&2; then
     echo "❌ Gate 1 (eligibility) FAIL — task as described doesn't fit auto-eligible scope."
     echo "   Refine the task (smaller scope, no schema/payments/auth) or use a fresh chat / /do-bead instead."
     # Optionally: bd close the bead since it can't be auto-dispatched
     exit 1
   fi
   ```

4. **Concurrency sanity check.** Warn (don't block) if there are already ≥5 active auto-bead tmux sessions:
   ```bash
   ACTIVE=$(tmux list-sessions 2>/dev/null | grep -c '^auto-' || true)
   if [[ ${ACTIVE:-0} -ge 5 ]]; then
     echo "⚠️  ${ACTIVE} auto-bead sessions already active — proceeding anyway, but watch system load."
   fi
   ```

5. **Spawn dispatch.** Existing infrastructure:
   ```bash
   ~/.claude/hooks/night-batch/dispatch.sh "$BEAD_ID"
   ```

6. **Report back.** Echo a concise status block:
   ```
   🚀 Dispatched $BEAD_ID
      Title: <title>
      Worktree: ~/.worktrees/AestheticcNext/$BEAD_ID
      Log: ~/.claude/hooks/night-batch/logs/$BEAD_ID.log
      tmux session: auto-$BEAD_ID
      
   Watch progress: tmux attach -t auto-$BEAD_ID
   Or tail log:    tail -f ~/.claude/hooks/night-batch/logs/$BEAD_ID.log
   
   This bead will appear in your next /morning-review with the rest of the auto-bead output.
   ```

## Tools needed

- Bash (with `dangerouslyDisableSandbox: true` for `bd` calls per memory)
- Read (only if Shane points at reference files in the task description and you want to sanity-check before dispatching)
- AskUserQuestion: only if `/dispatch` was invoked with no args — ask for the title + context.

## What NOT to do

- **Do NOT bypass Gate 1.** If eligibility fails, the task is genuinely outside scope and needs Shane's hands. No `--vouch` flag, no override. Discipline.
- **Do NOT spawn the bead and forget.** Always echo the worktree path + log file + watch command so Shane can follow progress if he wants.
- **Do NOT auto-merge** the resulting PR. /dispatch ends at "PR opened." Merge happens in Shane's next /morning-review (where he can review Codex Gate 3 verdict + diff before approving).
- **Do NOT auto-deploy.** Even after PR merges in /morning-review, deploy is a separate step (Shane's call).

## Edge cases

- **`bd create` fails:** surface the error, don't dispatch. Likely cause: dolt server issue (run bd doctor or the bd-reap script).
- **Eligibility check fails:** show Shane WHICH check failed (forbidden path / oversize / wrong priority / wrong type). Suggest the smallest refinement that would pass.
- **dispatch.sh fails to spawn tmux:** check if tmux session name already exists (`auto-$BEAD_ID` collision is unlikely but possible on retries). Surface the actual error.
- **Shane provides VERY long context (e.g. pastes 500 lines of code):** truncate the bead description at ~5000 chars; suggest he point at a file path instead.

## Companion: /morning-review

Tasks dispatched via `/dispatch` show up in the next `/morning-review` exactly like overnight auto-bead output. Same Gate 2 / Gate 3 / Codex adversarial review path. Same merge/explore/skip action menu. The only difference is the trigger — overnight beads come from `bd ready --label auto-eligible` at 22:00, dispatch beads come from this command on demand.
