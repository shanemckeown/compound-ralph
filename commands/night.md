# /night — Shane's Wind-Down Ritual

**Manually triggered only, when Shane actually says he's going to bed** ("i'm off to bed", "night", etc.) — never on a clock, never scheduled. This is the exact distinction that killed the old `/night` (retired 2026-08-11): that one was named after *when* it ran and quietly rotted the moment bedtime stopped matching real life. This one is named after *what Shane asked for*, and only ever fires because he said so, the same way `/morning` only ever fires because he opened a terminal and typed it. If this ever gets wired to a cron or a schedule, that's the mistake repeating — don't.

This does **not** replace `/rollup`. `/rollup`'s compaction job (fold closeout fragments into `LUCY_SESSION_STATE.md`, keep it under 60 KB) is still fired on work-rhythm, separately, whenever an arc of work completes — not bundled into this, and **not automatically part of `/morning` either** (a real point of confusion, corrected 2026-08-27: `/morning` only *reads* the state file + un-rolled fragments, it doesn't compact them). If `/night` finds a large backlog of un-rolled fragments (roughly >10, use judgement), say so explicitly — don't run `/rollup` unprompted, just flag it as due.

## What it does

1. **Propagate "Shane's going to sleep."** `ListAgents`, then `SendMessage` every active peer session: run `/closeout` now; apply the Shane Decision Frame to anything non-critical for the rest of the night (hold only for genuine gates — a prod deploy, a hard client-facing call); if genuinely still mid-work on something that shouldn't stop (a live incident, an in-flight deploy), finish that first, then close out, and write **"STILL WORKING"** as the literal last line of the report back — so it's grep-able in the morning, not something to re-derive from a wall of text.
2. **Read the last few days of CEO reviews** (`mcp__gbrain__get_page`, slug `aestheticc/brain/ceo_review_YYYY-MM-DD`, today back 3-4 days) — pull the recurring "Do today" / "Decision needed from you" items, especially ones repeating across multiple days (that repetition IS the signal: it means the thing genuinely isn't getting done). This becomes part of the priorities list below and primes context for tomorrow's `/ceo-respond`, which still does its own fresh read — this isn't a substitute for that, just earlier visibility.
3. **Compile a single priorities list for tomorrow morning**, in this order:
   - CEO-review recurring commercial gaps (these carry real runway/MRR stakes — lead with them, not buried under product items, per the review's own repeated point that outreach keeps losing to product work)
   - Genuine open decisions surfaced today that only Shane can make (tag each with the bead/thread it belongs to)
   - Live incidents/deploys still in flight, with real status not a guess
4. **Report back**: the priorities list, confirmation the fleet's been told to wind down, and the fragment-backlog flag if relevant. Then stop — don't keep working the list yourself, that's what `/morning` and the day's dispatch decisions are for.

## Why this exists

Shane, 2026-08-27, dictating this exact sequence at bedtime: fan out closeouts with a STILL WORKING flag, get a priorities list, pull CEO-review context, and noted he never types `/night` or `/rollup` — only `/closeout` and `/morning`. This captures what he was already doing by hand into one verb, without repeating the old failure (this stays manually fired, always).
