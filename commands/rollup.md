# /rollup — Close out every chat, fold them in, compact

**The one verb.** Shane says "roll up" (usually to the orchestrator) and this does the whole
end-of-arc cycle: closes out every live session, folds their fragments into
`LUCY_SESSION_STATE.md`, recovers work that never got closed out, and compacts.

Fire it when an **arc of work completes** — not on a clock. Shane's day doesn't end at bedtime,
so anything clock-triggered gets skipped, and a skipped compaction is what let the state file
reach 392 KB and made `/closeout` cost 4-6 minutes.

Supersedes `/night`, which is retired (2026-08-11) — its recovery scan and synthesis are Steps 1
and 5 here.

## Usage
```
/rollup                  # fan out closeout, then merge + compact
/rollup --local          # skip the fan-out; just merge fragments already on disk
/rollup --hard           # aggressive compaction regardless of size
/rollup --dry            # report what would change, write nothing
```

## 🔴 Division of labour — why this isn't one skill

`/closeout` **must** run inside each chat: only that session holds its own conversation, so
nobody else can write its fragment. `/rollup` **must** run in exactly one place, because it
merges. That's the entire reason there are two skills. Don't try to merge them.

```
/rollup (here, once)  ──SendMessage──▶  /closeout in session A ──▶ fragment A ─┐
                      ──SendMessage──▶  /closeout in session B ──▶ fragment B ─┼─▶ merge here
                      ──SendMessage──▶  /closeout in session C ──▶ fragment C ─┘
```

---

## Step 0 — Fan out `/closeout` to live sessions

Skip if `--local`.

```
ListAgents
```

Message **every `idle` interactive peer** with the single word `/closeout`.

- **Skip `busy` sessions and background `bg` workers** — a session mid-`/goal` is doing real work
  and an inbound message starts a new turn on receipt, which would interrupt it. Note which ones
  you skipped; they get closed out on the next rollup.
- **Do NOT filter by topic.** Session names (`aestheticc-d6`, `aestheticc-92`) carry no subject,
  and asking each one what it's about costs a full round trip per session. Closeout is
  **non-destructive** — it records the session, it doesn't end it, and Shane can carry on in that
  chat afterwards. So close out everything idle and let each *fragment* declare its own topic and
  initiative. Filtering belongs at merge time, where you actually have that information.
- If Shane named a subset ("the marketing chats"), still close out all idle sessions, then say
  which fragments matched his subset in the final report.

Then wait for fragments to land — poll `ls Sessions/closeouts/*.md` until the count stops rising,
or ~90s, whichever comes first. Sessions that don't produce a fragment go in the report as
`no fragment (session <name> did not respond)`. Don't block the whole rollup on one silent chat.

## Step 1 — Recovery scan (was `/night` 1b/1c)

Catch work that happened without a closeout — chats Shane closed cold, or a session that died.

```bash
git -C /Users/shane/Documents/GitReBase/AestheticcNext log --oneline --since="24 hours ago" --all
git -C /Users/shane/Documents/Obsidian log --oneline --since="24 hours ago"
git -C /Users/shane/Documents/AestheticcTools log --oneline --since="24 hours ago" 2>/dev/null
```

Plus beads changed today in both databases (Obsidian `LUCY-*`, AestheticcNext).

Anything not accounted for by a fragment or already in `What We Did` gets folded in tagged
`[recovered]`. This is the safety net for the whole system — without it, a chat closed without
`/closeout` vanishes silently.

## Step 2 — Read

Read every fragment in `Sessions/closeouts/`, oldest first. Then `LUCY_SESSION_STATE.md`.
This is the one skill allowed to read that file in full.

## Step 3 — Merge

**`## What We Did`** — one line per fragment, `- [MM-DD HH:MM] <headline>`. Collapse a session's
3-5 bullets into one unless two are genuinely independent. **Carry the caveats** — a "done" that
loses its qualifier here is exactly the failure this file exists to prevent
(`Aestheticc/Strategy/PROMISE_AUDIT_COVERAGE_REVIEW_2026-06-17.md`). Group by theme, not by session.

**`## Decisions Made`** — append each fragment's `## Decided`. Dedupe. Keep rationale. If a
decision contradicts an earlier one, **surface the conflict, don't resolve it.**

**`## Active Threads`** — add new; update touched; anything named in a fragment's
`threads_resolved` → `Status: Resolved <date>`, dropped at the *next* rollup so `/morning` sees
it land once.

**`## Queued for Next Session`** — merge, dedupe, drop what the fragments show as done.

**`## Open Questions`** — add new; **delete** anything in `questions_answered`. The one section
where deletion is correct.

**`## Stale Watch`** — add new. Only remove after actually verifying resolution, and say in the
report which you verified and how.

**`## Context Worth Knowing`** — append only genuinely non-obvious insight. This is where bloat
accumulates; be ruthless.

**Metadata** — aggregate all fragment frontmatter into **one** `# --- Rollup Metadata [date] ---`
block: combined bead/commit lists, sessions rolled, initiative tally (`LUCY-r2zp: 4, none: 1`).
Replace per-session blocks; never accumulate 40 again.

## Step 4 — Compact

**Hard ceiling: the file comes out under 60 KB.** If it doesn't, you haven't finished.

- Fold `What We Did` older than **14 days** into one summary line per week
- Delete resolved threads that have had their one cycle of visibility
- Drop `Context Worth Knowing` items now encoded durably (CLAUDE.md, a memory file, a bead) — **link instead**
- Collapse old metadata blocks into one historical block
- **Never touch the 📌 PINNED block at the top**

Output must be shorter than input.

## Step 5 — Write, prune, commit

1. Write the merged `LUCY_SESSION_STATE.md`.
2. Update `LUCY_ADVISORY_CADENCE.json` for advisory domains the fragments show as reviewed
   (`last_reviewed` → today, recalc `next_review` from `frequency_days`, `status: current`).
3. **Move** rolled fragments to `Sessions/closeouts/rolled/<YYYY-MM>/` — move, don't delete.
   They're the audit trail and they compress well.
4. Commit:
```bash
~/.claude/scripts/lucy-closeout-commit.sh "rollup: <N> sessions folded, state <before>KB → <after>KB" \
  LUCY_SESSION_STATE.md LUCY_ADVISORY_CADENCE.json Sessions/closeouts
```

## Step 6 — Report

```
Rolled up N sessions · state 392KB → 48KB
Closed out: 5 idle · Skipped: 2 busy (AestheticcNext-541gn mid-/goal) · No fragment: 0
Recovered: 2 commits with no closeout [recovered]
Threads: +2 new, 3 resolved · Questions: 1 answered, 2 new
Initiatives: LUCY-r2zp ×4, LUCY-osi9 ×2, none ×1
```

Flag every `initiative: none` — unfocused work is worth Shane seeing. Flag contradicting decisions.

## Related
- **`/closeout`** — writes the fragments. Cheap, non-interactive, safe to fan out.
- **`/morning`** — reads this file plus any fragments not yet rolled up.
- ~~`/night`~~ — retired 2026-08-11, absorbed here.
