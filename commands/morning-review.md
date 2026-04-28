# /morning-review — interactive overnight review

Drive the close-out of last night's night-batch results. Pull what shipped, summarise per-bead, then let Shane decide merge / explore / skip / deploy per-bead in chat.

## When to use

- Every morning after night-batch ran (22:00 BST → ~06:00 BST overnight)
- After a manual night-batch invocation
- Any time you want to see "what's in the queue waiting for me"

## What this skill does

1. **Discover.** Read every overnight-run state marker:
   - `~/.claude/hooks/night-batch/state/*.complete` — Gate 2 + Gate 3 PASS, ready to merge
   - `~/.claude/hooks/night-batch/state/*.reviewed` — Gate 2 PASS, Gate 3 found BLOCKERs
   - `~/.claude/hooks/night-batch/state/*.guarded` — Gate 2 FAIL (forbidden path / oversize / empty diff)
   - `~/.claude/hooks/night-batch/state/processed/$(date +%Y-%m-%d)/*` — today's already-archived (still review-relevant)
   - Dedupe by bead ID; processed/ wins (it's been actioned).

2. **Gather context per bead.** For each:
   - Title via `BEADS_DIR=/Users/shane/Documents/Obsidian/.beads bd show <BEAD_ID>` (need `dangerouslyDisableSandbox: true`)
   - Diff stat: `git -C ~/.worktrees/AestheticcNext/<BEAD_ID> diff --shortstat $(git ... merge-base HEAD main)..HEAD`
   - Commits: `git -C <worktree> log --oneline main..HEAD | head -5`
   - **Codex (Gate 3 gating) verdict**: read `<worktree>/.compound-review/codex-<BEAD_ID>.txt` — surface SUMMARY + counts; quote BLOCKER lines verbatim if present
   - **Blind-claude (parallel A/B) verdict**: read `<worktree>/.compound-review/blind-claude-<BEAD_ID>.txt` — secondary comparison data only, NOT a gate. Show counts inline; only quote BLOCKER lines if Codex disagreed.
   - PR state: `gh pr list --head auto/<BEAD_ID> --json url,state,title,number --jq '.[0]'`

3. **Present rollup.** Single message, scannable:
   ```
   ## Overnight review — YYYY-MM-DD

   3 ready to merge · 1 needs your call · 1 guard failed

   🟢 LUCY-XXXX: <title> — 4 files, 87 LOC, /review clean, codex clean (blind 0)
   🟢 LUCY-YYYY: <title> — 1 file, 12 LOC, /review clean, codex clean (blind 1 NIT)
   🟡 LUCY-ZZZZ: <title> — 6 files, 142 LOC, codex flagged 2 BLOCKERs (null deref, missing await) — blind agreed
   🔴 LUCY-AAAA: <title> — Gate 2 fail: touched lib/stripe/checkout.ts (forbidden)

   What's the call? You can say:
   • merge LUCY-XXXX  (or "merge all green" for the 🟢 bucket)
   • explore LUCY-YYYY  (show full diff + review file)
   • skip LUCY-ZZZZ   (archive without merging)
   • deploy           (after merges, kick @deploy to push main → prod staging then prod)
   • done             (close out, nothing to do)
   ```

4. **Wait for Shane's reply.** This is interactive. Don't pre-execute merges.

## Action handlers

When Shane responds, execute carefully:

### `merge <BEAD>` or `merge all green`
For each target:
1. `cd ~/.worktrees/AestheticcNext/<BEAD>`
2. `git push -u origin auto/<BEAD>` (if not already pushed)
3. `gh pr create --base main --title "<bead-title>" --body "Auto-bead run for <BEAD>. Gate 2 PASS, Gate 3 PASS. Generated $(date)."` (if no PR)
4. `gh pr merge <number> --squash --delete-branch` (or `--merge` if Shane prefers)
5. Append a one-liner to `Aestheticc/Ops/Hermes/SHANE_TODO.md` under "Done (most recent 5)": `[YYYY-MM-DD HH:MM UTC] Merged auto-bead <BEAD>: <title> (PR #N)`
6. Move the marker from `state/<BEAD>.complete` to `state/processed/$(date)/<BEAD>.complete` (or just `rm` if already archived)
7. Close the bead: `BEADS_DIR=/Users/shane/Documents/Obsidian/.beads bd close <BEAD>` (sandbox off)

If push fails (network, auth), surface the exact error — don't pretend success.

### `explore <BEAD>`
Show in this chat (don't open separate files):
- Full `git diff main..HEAD` for the bead's worktree (truncate at 200 lines, offer to show more)
- Full Codex (Gate 3 gating) review text from `<worktree>/.compound-review/codex-<BEAD>.txt`
- Full blind-claude (parallel A/B) review text from `<worktree>/.compound-review/blind-claude-<BEAD>.txt` — only if Shane wants the comparison; otherwise mention "blind-claude verdict matched / disagreed"
- Bead description
- Any per-bead log output from `~/.claude/hooks/night-batch/logs/<BEAD>.log`

Then re-offer the action menu.

### `skip <BEAD>`
1. Move marker to `state/processed/$(date)/<BEAD>.skipped` (preserve the original status by appending `.skipped`)
2. Append to SHANE_TODO.md: `[TIMESTAMP] Skipped auto-bead <BEAD>: <reason if Shane gave one>`
3. Don't close the bead (it's still open for retry / human pickup).
4. Worktree is left in place — Shane can `git worktree prune` if he wants.

### `deploy`
Only after at least one merge. Spawn `@deploy-staging` agent (this checks main is clean), then on Shane's word `@deploy` for prod. Don't auto-chain — staging-first is mandatory.

### `done`
Close out — confirm nothing left in state/, summarise what merged/skipped/lingered, exit.

## Edge cases

- **Empty state directories.** Reply: "Nothing in the overnight queue this morning — no markers in `state/`. Either night-batch didn't fire, no eligible beads were tagged, or you've already cleared everything."
- **Worktree missing for a marker.** Surface as "marker present but no worktree — auto-bead.sh failed to create one, see `~/.claude/hooks/night-batch/logs/<BEAD>.log` for why." Offer to investigate.
- **PR already exists.** Skip the create step, jump to merge.
- **Branch already merged.** Skip; just archive the marker + close the bead.
- **Stale markers (>48h old).** Flag at the top: "⚠️ N markers are >48h old — stale runs?" Don't auto-archive; let Shane review.

## Tools needed

- Bash (with `dangerouslyDisableSandbox: true` for `bd` calls — beads always needs it per memory)
- Read (for reviewing diff files, log files, Codex + blind-claude review texts)
- AskUserQuestion: NO — this is conversational, Shane drives directly via chat replies.

## What NOT to do

- Do NOT auto-merge anything without Shane saying so. Even `.complete` beads (both gates passed) need explicit go-ahead.
- Do NOT skip /review or /qa concerns silently. If **Codex** flagged BLOCKERs (Codex is the gating reviewer per LUCY-llp1 closeout 2026-04-28) and Shane says "merge anyway," echo the BLOCKERs back and ask "merge despite the BLOCKERs? (yes/no)" — blind-claude BLOCKERs that Codex did NOT also flag are advisory, not gating, but still mention if they raise something Codex missed.
- Do NOT auto-deploy after merges. Staging first, prod on explicit second go-ahead.
- Do NOT delete the worktrees automatically. Shane may want to inspect after merge.
