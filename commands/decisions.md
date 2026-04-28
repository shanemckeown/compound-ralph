---
description: Pull and work through the /decisions ops queue — morning ops routine. Walks through pending entries, discusses each with Shane, executes resolve + type-specific side-effects. Emits a GBrain diarization at close. LUCY-ve4u.
---

# /decisions — morning ops queue routine

This skill is Shane's primary interaction surface for the `/decisions` queue (per LUCY-850s ops refactor). Queue is populated by VPS ops crons (triage-beads, sentry-triage, mrr-tracker) plus future sources (walkabout_draft, bead_request).

## Invocation modes

Parse `$ARGUMENTS` to detect mode:

- **no args** — full walk (default). Urgent first, then by type.
- `quick` — list-only, no walk.
- `urgent` — filter to `priority=urgent`.
- `sentry` | `triage` | `mrr` | `walkabout` | `bead` — filter to that `decision_type`.

## Step 1 — Fetch token + queue

```bash
TOKEN=$(cat ~/.config/aestheticc/decisions-token 2>/dev/null || echo "")
if [[ -z "$TOKEN" ]]; then
  # Fallback: fetch from VPS.
  TOKEN=$(ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
    'grep -E "^DECISIONS_API_TOKEN=" /etc/corpus-gateway/env | cut -d= -f2' 2>/dev/null)
  mkdir -p ~/.config/aestheticc && echo "$TOKEN" > ~/.config/aestheticc/decisions-token && chmod 600 ~/.config/aestheticc/decisions-token
fi
BASE="https://hermes.aestheti.cc"

# Build query per mode
QUERY="status=pending&limit=200"
# (If filtering by type, append &decision_type=<type>)

curl -sS -H "Authorization: Bearer $TOKEN" "$BASE/decisions?$QUERY"
```

Parse JSON. Count by type + priority. **Present a summary** to Shane:

```
/decisions queue — {total} pending ({urgent} urgent)

  triage_attention: {n}  (needs your eyes on bead classifications)
  sentry_error:     {n}  (production errors — {n_urgent} critical)
  mrr_alert:        {n}  (revenue events)
  walkabout_draft:  {n}  (writeups to commit)
  bead_request:     {n}  (Slack asks to formalise)

Starting walk-through — URGENT first. Type `skip` at any prompt to defer.
```

If `quick` mode: stop here after printing the list with each entry's one-liner. Don't walk.

## Step 2 — Walk-through loop

Iterate through entries in this priority order: urgent first, then normal, then low. Within each priority, group by type to batch similar work.

For each entry, render type-specific context (Step 3), then present four options via `AskUserQuestion`:

- **Approve** — execute the "on approve" side-effect for this type, PATCH `approved`, move on
- **Edit** — adjust + execute (type-specific edit flow)
- **Reject** — PATCH `rejected` with a one-line note, move on
- **Defer** — leave pending, continue with next

**Always ask one at a time.** Don't batch multiple entries into a single question — the point is Shane-quality taste per entry.

## Step 3 — Type-specific handlers

### triage_attention

**Render:**
- `bd show {entity_ref.bead_id}` — pull full description + priority + labels
- Classification reason from `entity_ref.classification_reason`
- Related beads if the description references them

**On approve:** Shane has decided this bead needs his action. Open conversation: "What's your decision on this bead — work on it now / schedule it / close it?" Execute per answer. Do NOT apply labels from laptop unless Shane says so (keeps the signal-emitter/state-mutator separation clean).

**On edit:** Lucy proposes a refined scope or next-step, Shane confirms, then update the bead description or add a note via `bd comment`.

**On reject:** Lucy's classification was wrong — Shane doesn't think this needs his eyes. PATCH rejected + note why. Optionally `bd update {id} --add-label decision-rejected` so triage-beads won't re-alert (next run will 409 on idempotency anyway).

### sentry_error

**Render:**
- Title + culprit + severity + event count
- Open URL from `entity_ref.url` (just print it — Shane clicks if he wants)
- If CRITICAL, also check whether there's a related bead already open (grep `bd list --search "{issue_id}"`)

**On approve:** Shane acknowledges the error needs attention. Ask: create a bead to track the fix? If yes:
```bash
bd create --title="Sentry: {title}" --priority=1 --type=bug \
  --description="Sentry issue {short_id}, {count} events in last window. URL: {url}. Severity: {severity}. Reason classified {severity}: [culprit path match]."
```

**On edit:** Let Shane customise the bead title/description before creating.

**On reject:** PATCH rejected with note (e.g., "known issue, will self-resolve" or "already tracked as LUCY-xxxx"). No Sentry-side mute yet (that's a future feature).

### mrr_alert

**Render:**
- `entity_ref.change_type` (new_customer / churn / revenue_delta / platform_fee_*)
- Delta figures + current state
- If new customer: customer details
- If churn: urgency context (who cancelled, when)

**On approve:**
- new_customer → ask if onboarding follow-up needed. If yes, `bd create` with CALL type.
- churn → ALWAYS ask about retention follow-up. `bd create` priority P0 call.
- revenue_delta → log acknowledgement in GBrain via `ssh hermes 'gbrain put mrr-note-$(date +%Y%m%d-%H%M)'`.
- platform_fee_* → ack only unless Shane wants a bead.

**On reject:** Noise / false positive. PATCH rejected. No action.

### walkabout_draft

**Render:**
- `entity_ref.walkabout_date`
- Show full `entity_ref.body_markdown` (may be large — show first 30 lines + prompt to show rest)

**On approve:** Write `body_markdown` to `/Users/shane/Documents/Obsidian/Aestheticc/Growth/Pipeline/Local/WALKABOUT_{date}.md`. Do NOT auto-commit — Shane can commit as part of normal vault flow. (Writing-not-committing respects "laptop is editor" principle: Shane reviews the written file before staging.)

**On edit:** Open the markdown in Shane's editor (or write a draft to `/tmp/` and let him copy-edit), save to vault path on confirm.

**On reject:** Discard.

### bead_request

**Render:**
- `entity_ref.raw_text` (what Shane said to Slack-Lucy)
- `entity_ref.parsed.{title, description, priority_suggestion, type_suggestion, db_suggestion}`

**On approve:** Run `bd create` with the parsed fields. Report the bead ID.

**On edit:** Let Shane adjust any field before creating.

**On reject:** Discard. (Shane may have changed his mind.)

## Step 4 — PATCH the resolve

For every entry acted on (not deferred):

```bash
curl -sS -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"choice\":\"$CHOICE\",\"note\":\"$NOTE\"}" \
  "$BASE/decisions/$ID"
```

Where `$CHOICE` is one of `approved|edited|rejected`. `$NOTE` is a one-line summary of what happened.

If PATCH returns 409 (`already_resolved`), log and continue — shouldn't happen if we're the only consumer.

## Step 5 — Session closeout + GBrain diarization

At end of walk:

1. **Summary** to Shane:
   ```
   /decisions session summary:
     Resolved: {approved} approved / {edited} edited / {rejected} rejected
     Deferred: {deferred} (still pending)
     Beads spawned: {list of bd IDs}
     Vault files written: {list of paths}
   ```

2. **Diarize to GBrain** — shell out to `gbrain put` via SSH:
   ```bash
   cat <<EOF | ssh -i ~/.ssh/id_ed25519_aestheticc hermes@91.99.204.29 \
     'PATH=$HOME/.bun/bin:$PATH /home/hermes/.bun/bin/gbrain put ops-decisions-session-$(date +%Y-%m-%d-%H%M)'
   # /decisions session {date}

   {summary body — what was resolved, spawned, learned}

   ## Entries resolved
   {list of (id, type, choice, 1-line note)}

   ## Entries deferred
   {list}

   ## Follow-ups created
   {list of bd IDs with titles}
   EOF
   ```

   This lands in GBrain so the next `/decisions` session (or morning-briefing) can see what Shane decided yesterday.

## Tone guidance

- **Be fast + terse** — Shane's working a queue, not having a slow conversation. One short sentence per render, then the question.
- **Don't default-defer** — each entry needs a real choice (or explicit defer). Don't skip over.
- **Celebrate approvals** — "LUCY-xxxx closed ✓, moving on."
- **Flag surprises** — if an entry's entity_ref looks stale (bead already closed, Sentry issue resolved), say so and suggest reject.

## Error handling

- If `curl` fails: retry once, then abort with clear error. Don't loop.
- If PATCH 409: entry was already resolved (probably by another session). Move on.
- If `bd` fails: report, don't abort — continue the walk.
- If SSH fails: GBrain diarization is best-effort. Skip, don't fail the session.

## What this skill replaces

Before LUCY-ve4u:
- No way to work the queue except raw `curl`
- Morning briefing was a Slack scroll with no "what did I resolve yesterday" trail
- Every ops cron output was ad-hoc

After:
- `/decisions` is the morning ops routine (5-30 min depending on queue size)
- Laptop is canonical: all bead mutations, vault writes, PATCH resolutions happen here
- GBrain diarization builds a history of ops decisions over time

## Related beads / docs

- **LUCY-850s** — the ops crons refactor that populates this queue
- **LUCY-3igl** — Ops Centre v2 (browser visualization, same queue, post-sprint)
- **LUCY-xbdu** — Slack urgent escalation (ties to `priority=urgent`)
- **LUCY-0o2x** — Hermes-Lucy bead_request producer (feeds this skill's `bead_request` handler)
- `Aestheticc/Ops/Hermes/OPS_CRONS_REFACTOR_PLAN_2026-04-21.md` §5 — decision_type contract
- `Aestheticc/Ops/Hermes/OPS_CRON_PATTERN.md` — how producers emit

---

*This skill is the PRIMARY laptop surface for ops. Consumers of the queue are: Slack (push notifications for urgent), Ops Centre v2 (browser, post-sprint), this skill (laptop discussion). Keep them in sync on the decision_type contract.*
