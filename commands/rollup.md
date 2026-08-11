# /rollup — Fold closeout fragments into session state, and compact

The single point of merge and compaction. `/closeout` is deliberately cheap and never touches
`LUCY_SESSION_STATE.md`; **this** is the skill that does.

Fire it when an **arc of work completes** — not on a clock. Shane's day doesn't end at bedtime,
so a nightly auto-rollup would clobber threads he intends to keep working the next morning.

## Usage
```
/rollup              # fold fragments in, compact if needed
/rollup --hard       # fold in + aggressive compaction regardless of size
/rollup --dry        # show what would change, write nothing
```

## 🔴 Size ceiling — this is the job

`LUCY_SESSION_STATE.md` must come out of this **under 60 KB**. It hit 392 KB / 1,697 lines by
2026-08-11 because compaction lived inside `/night`, `/night` stopped being run, and nothing
failed loudly. That growth is what made `/closeout` cost 4-6 minutes.

If the file is over 60 KB when you finish, you have not finished. Compact harder.

---

## Step 1 — Inventory

```bash
ls -1 /Users/shane/Documents/Obsidian/Sessions/closeouts/*.md 2>/dev/null | wc -l
wc -c /Users/shane/Documents/Obsidian/LUCY_SESSION_STATE.md
```

No fragments and the file is under 60 KB → say so and stop. Don't manufacture work.

## Step 2 — Read fragments, then state

Read every fragment in `Sessions/closeouts/`, oldest first. Then read `LUCY_SESSION_STATE.md`.
This is the one skill allowed to read it in full.

## Step 3 — Merge

**`## What We Did`** — one line per fragment, `- [MM-DD HH:MM] <the fragment's headline>`.
Collapse a session's 3-5 bullets into one line unless two are genuinely independent. Carry the
caveats — a "done" that loses its qualifier here is exactly the failure this file guards against.

**`## Decisions Made`** — append each fragment's `## Decided` entries. Decisions are durable;
compact wording, never drop one.

**`## Active Threads`** — add new threads; update status/next for touched ones; for anything named
in a fragment's `threads_resolved`, mark `Status: Resolved <date>` and drop it in the *next* rollup
(one cycle of visibility, so `/morning` sees it land).

**`## Queued for Next Session`** — merge, dedupe, drop anything the fragments show as done.

**`## Open Questions`** — add new ones; **delete** anything in a fragment's `questions_answered`.
This is the one section where deletion is correct.

**`## Stale Watch`** — add new items. Only remove one after actually verifying it's resolved;
say in the rollup summary which you verified and how.

**`## Context Worth Knowing`** — append genuinely non-obvious insights. Ruthless here: this section
is where bloat accumulates. If it reads as generic, it doesn't go in.

### Metadata
Aggregate fragment frontmatter into **one** `# --- Rollup Metadata [date] ---` yaml block at the end
of `## What We Did` — combined bead/commit lists, sessions rolled, and an initiative tally
(`LUCY-r2zp: 4, none: 1`). Replace the per-session blocks; do not accumulate 40 of them again.

## Step 4 — Compact

Over 60 KB (or `--hard`):
- Fold anything older than **14 days** in `What We Did` into a single dated summary line per week.
- Delete resolved threads that have already had their one cycle of visibility.
- Drop `Context Worth Knowing` items now encoded somewhere durable (CLAUDE.md, a memory file, a bead) — **link to it instead**.
- Collapse old rollup metadata blocks into one historical block.
- **Never touch the 📌 PINNED block at the top.**

Output must be shorter than input. Compaction, not restatement.

## Step 5 — Write, prune, commit

1. Write the merged `LUCY_SESSION_STATE.md`.
2. Update `LUCY_ADVISORY_CADENCE.json` for any advisory domain the fragments show as reviewed
   (`last_reviewed` → date, recalc `next_review` from `frequency_days`, `status: current`).
3. Move rolled-up fragments to `Sessions/closeouts/rolled/<YYYY-MM>/`. Move, don't delete —
   they're the audit trail, and they compress well in git.
4. Commit:
```bash
~/.claude/scripts/lucy-closeout-commit.sh "rollup: <N> sessions folded, state <before>KB → <after>KB" \
  LUCY_SESSION_STATE.md LUCY_ADVISORY_CADENCE.json Sessions/closeouts
```

## Step 6 — Report

```
Rolled up N sessions · state 392KB → 48KB
Threads: +2 new, 3 resolved · Questions: 1 answered, 2 new
Initiatives: LUCY-r2zp ×4, LUCY-osi9 ×2, none ×1
```

Flag any fragment with `initiative: none` — unfocused work is worth Shane seeing.

## Related
- **`/closeout`** — writes the fragments. Cheap, non-interactive, safe to fan out to 20 sessions.
- **`/morning`** — reads this file plus any fragments not yet rolled up.
