# /closeout — Session Handoff (one fragment, no merge)

Write **one new file** describing this session. Do not read, merge, or rewrite `LUCY_SESSION_STATE.md`.

Target: **under 30 seconds**. This must be cheap enough to fire into 20 open sessions at once.

## Usage
```
/closeout            # full fragment
/closeout quick      # "Did" bullets + frontmatter only
```

## 🔴 Why this is fragment-based (do not "helpfully" revert to merging)

`LUCY_SESSION_STATE.md` is ~390 KB. The old skill read it in full and merged 8 sections on
every run — ~98k tokens of input per closeout, growing daily, plus a race whenever two
sessions overlapped. That made closeout cost 4-6 minutes, so it got skipped, so work went
stale. **Each session now writes its own file. Two sessions can never touch the same file,
so there is nothing to merge and nothing to race.** Reconciliation happens in `/rollup`,
which Shane fires when an arc of work completes.

If you catch yourself about to `Read` the state file during a closeout — stop. You don't need it.

## 🔴 Non-interactive. Always.

This skill is dispatched by the orchestrator via `SendMessage` to many sessions at once.
**Never** call `AskUserQuestion`, never wait for confirmation, never end with "shall I…".
If something is ambiguous, write your best reading of it into the fragment and move on.
An unanswered question in a fanned-out closeout stalls a session Shane has already walked away from.

## 🔴 Caveats ride with the headline

Any "done / clean / shipped / verified" claim MUST carry its coverage + confidence caveats in
the SAME bullet — what was NOT covered, static-trace vs runtime, verified vs assumed. A "clean"
headline with caveats dropped propagates into CEO reviews and handovers as more-complete-than-true.
If you can't state the caveat, you haven't earned the "done".
(Origin: the 2026-06-14 "broken-promise resweep CLEAN" headline — see
`Aestheticc/Strategy/PROMISE_AUDIT_COVERAGE_REVIEW_2026-06-17.md`.)

---

## Step 1 — Write the fragment

Path: `/Users/shane/Documents/Obsidian/Sessions/closeouts/<YYYY-MM-DD>T<HHMM>--<slug>.md`

`<slug>` is 2-4 kebab-case words naming the session's work (`closeout-rearchitecture`,
`ny-skin-voice-outage`). The timestamp prefix plus the slug makes collisions effectively
impossible; if the exact filename already exists, append `-2`.

```markdown
---
closed: 2026-08-11T20:40
started: 2026-08-11T20:15        # approximate, from the first message
slug: <same as filename slug>
initiative: LUCY-r2zp            # see list below, or "none — <reason>"
beads_completed: []
beads_created: []
commits: []                      # short SHAs
deployed: false
threads_resolved: []             # names/ids of Active Threads this session finished
questions_answered: []           # Open Questions from the state file this session resolved
---

## Did
- concrete outputs, not topics. 3-5 bullets. Specific.

## Decided
- what Shane decided or approved, with the rationale. Omit the section if nothing.

## Threads
- **<name>** | **Status:** <status> | **Next:** <next step>
  New or updated multi-session work. Omit if none.

## Queued
- next steps this session identified. Omit if none.

## Open
- unresolved things needing Shane. Omit if none.

## Stale
- newly spotted stale items. Omit if none.

## Context
- non-obvious insights, risks, patterns. Omit if none.
```

**Omit empty sections entirely.** Never write "None".

`/closeout quick` → frontmatter + `## Did` only.
**Sub-5-minute session** → frontmatter + a single `## Did` bullet.

### Initiative alignment
- `LUCY-z6pf` — INIT-1: MRR
- `LUCY-osi9` — INIT-2: Ship features for pipeline
- `LUCY-rvdk` — INIT-3: Platform reliability
- `LUCY-cfqq` — INIT-4: Growth engine
- `LUCY-r2zp` — INIT-5: Founder Operating System

If the work serves none, write `initiative: none — <reason>`. That's a real signal, not a failure — record it honestly rather than stretching to fit one.

### Resolving threads and questions
You cannot edit the canonical state file. Instead **declare the intent** in frontmatter —
`threads_resolved: [receptionist-prototype]`, `questions_answered: ["whether to pin prod traffic"]` —
and `/rollup` applies it. Name them closely enough that rollup can match them.

## Step 2 — Commit

```bash
~/.claude/scripts/lucy-closeout-commit.sh "closeout: <short summary>" \
  Sessions/closeouts/<the-file-you-just-wrote>.md [<other files this session touched>]
```

Name **only** files this session actually created or modified. Never `git add -A` — the vault
carries hundreds of unrelated dirty paths. The script takes a lock, so concurrent closeouts
serialise safely; if it can't push it keeps the commit locally and says so. Report its last
line and stop.

## Step 3 — Report, in one line

`Closed out → Sessions/closeouts/<file>.md (<commit>)`

Nothing else. No summary of the summary.

---

## What this is NOT
- Not a conversation summary
- Not a merge into `LUCY_SESSION_STATE.md` — that's `/rollup`
- Not a to-do list (beads handles that)
- Not optional — run at the end of every session with meaningful work

## Related
- **`/rollup`** — folds accumulated fragments into `LUCY_SESSION_STATE.md` and compacts it.
  Fired manually when an arc of work completes, not on a clock.
- **`/morning`** — reads the compacted state file *and* any fragments not yet rolled up.
