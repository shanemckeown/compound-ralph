# /night — RETIRED (2026-08-11) → use `/rollup`

**Do not run this. Run `/rollup` instead, then tell Shane that's what you did.**

`/night` was named after *when* it ran rather than *what it did*, and that killed it. Its real job
was compaction, but its trigger was bedtime — and Shane's work doesn't end at bedtime, so it
stopped being run. Nothing failed loudly. `LUCY_SESSION_STATE.md` grew unchecked to 392 KB, and
because the old `/closeout` read that file in full on every run, every closeout in every one of
20 parallel chats paid for it — 4-6 minutes each. That's what made closeouts get skipped, which
is what let work go stale.

Everything it did now lives in **`/rollup`**, fired on work rhythm instead of clock rhythm:

| `/night` step | Now |
|---|---|
| Recovery scan (git + beads, catch missed closeouts) | `/rollup` Step 1 |
| Read accumulated state | `/rollup` Step 2 |
| Compact + refine | `/rollup` Steps 3-4 |
| Write + commit | `/rollup` Step 5 |
| — *(new)* fan out `/closeout` to every idle session first | `/rollup` Step 0 |

## The lesson worth keeping

**Name a skill after its operation, not its schedule.** A skill named for a clock can only be
triggered by that clock, so when the clock stops matching real life the operation dies silently
and its cost migrates somewhere worse. Applies to anything else here named `/morning`-shaped —
if the trigger drifts from the need, rename it and re-trigger it on the need.

See also: `/closeout` (per-chat, cheap, non-interactive), `/rollup` (once per arc of work).
