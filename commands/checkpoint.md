# /checkpoint — "still steaming ahead, here's where I am" (the in-progress closeout)

The variant Shane asked for. `/closeout` says *this session is done*. `/checkpoint` says
**this session is ALIVE and mid-flight** — here is what I'm actively driving, what I've already
dispatched, and the plan — so another session (a fresh `/morning`, the orchestrator) has full
knowledge of the work **without duplicating it.**

It does NOT end the session. You keep going after writing it. Re-run it whenever the picture
changes materially — each run refreshes the heartbeat and the plan.

## Why this exists

Shane runs a fresh `/morning` in another chat while this session is still building. Without a
live signal, that session either (a) is blind to work in flight, or (b) re-dispatches something
already dispatched. A checkpoint is a **claim on a body of work**: "I've got this, here's the
state, stay off it."

## The claim expires — this is load-bearing

An ACTIVE fragment from a session that has since died would lock its work forever — the exact
dead-orchestrator failure the whole fleet design fights. So a checkpoint carries the writing
session's id, and any reader treats it as **abandoned** (work is free again) if that session is
no longer live in `claude agents --json`. A checkpoint is only a claim while its session breathes.

## Step 1 — Write / refresh the checkpoint fragment

Path: `Sessions/closeouts/<YYYY-MM-DD>T<HHMM>--<slug>--ACTIVE.md`

The `--ACTIVE` suffix and the `status: in-progress` frontmatter both mark it. Reuse the SAME
filename on re-run (same session, same arc) so it updates in place rather than spawning a second
claim. Your session id is the UUID in your scratchpad path.

```markdown
---
status: in-progress            # 🔴 NOT done. Do not fold into LUCY_SESSION_STATE.md.
active_session: aestheticc-55  # the session holding this claim
session_id: 089bc319-...       # full UUID — reader checks this against `claude agents --json`
opened: 2026-08-12T11:40
heartbeat: 2026-08-12T12:15     # bumped on every re-run; staleness = now - heartbeat
slug: <same as filename>
---

## 🔴 DO NOT DUPLICATE — actively being driven by this session
- <the specific things this session is hands-on with right now>

## Dispatched (do NOT re-dispatch)
- <bead / Codex task / worktree> → <where> → <what it's producing> → <how to check on it>

## Done this session (durable, already committed)
- <landed + pushed things, so the reader knows they're real>

## The plan from here
- <ordered next steps this session intends to take>

## Needs Shane (only he can do)
- <prod writes, client-facing sends, decisions>

## If this session has died (heartbeat stale / not in `claude agents --json`)
- <what a taking-over session should do: which dispatched work to check, what to pick up>
```

Omit any empty section. Keep it tight — it's a status beacon, not a novel.

## Step 2 — Commit (does not end the session)

```bash
~/.claude/scripts/lucy-closeout-commit.sh "checkpoint: <one-line status>" \
  Sessions/closeouts/<the-file>--ACTIVE.md [<anything else you committed this run>]
```

Then **keep working.** This is a beacon, not a goodbye.

## How other skills treat an ACTIVE fragment

- **`/morning`** lists it under "🔴 Live work in flight — do not dispatch these," having first
  checked the session is still live. If the session is dead, it reports the fragment's "if I've
  died" section as recoverable work instead.
- **`/rollup`** does NOT fold an in-progress fragment into `LUCY_SESSION_STATE.md` while its
  session is alive — the work isn't done. Once the session dies OR the fragment is replaced by a
  normal (`status: done`) closeout, it becomes eligible.
- **The orchestrator** reads ACTIVE fragments to know what's already owned before dispatching.

## When the work actually finishes

Run a normal `/closeout`. That writes a `status: done` fragment; delete the `--ACTIVE` one (or
let `/rollup` retire it). The claim is released by completion, or by the session dying.

## Related
- `/closeout` — the session is done. · `/rollup` — folds finished fragments in. · `/morning` — reads both.
