# /client-update — Draft a what-we-did reply for a named client

Pulls beads tagged for a specific client and drafts a copy-paste-ready WhatsApp reply covering: what shipped, what's in flight, what's still on the list, what we're waiting on from them.

## Usage

```
/client-update viso                          # default 4-day lookback
/client-update viso since:2026-05-08         # explicit date
/client-update viso days:7                   # custom window
/client-update awlin-beauty                  # different client
/client-update kelly                         # alias for omorphia (slug map below)
```

## Slug map (resolve aliases before querying)

| User says | Slug |
|---|---|
| Leah / Paddy / Skerrett / Viso | `viso` |
| Daniel / Clare / Dan / All In Beauty / Awlin | `awlin-beauty` |
| Kelly / Kelly-Ann / Omorphia | `omorphia` |
| Asthetik London | `asthetik-london` |

If the user names a client not in this map, search `bd list --label=client-reported --json` for related labels, ask if ambiguous.

## When invoked

### Step 1: Resolve slug + window

- Parse args: `<slug>` required, `since:<date>` OR `days:<n>` optional (default `days:4` — extra buffer beyond Fri→Mon)
- Convert relative `days:n` to absolute date for the queries
- Today's date from environment

### Step 2: Pull state from beads (AestheticcNext repo)

Run all four queries from `/Users/shane/Documents/GitReBase/AestheticcNext` (auto-discovers `.beads/`). Wrap bd calls in `dangerouslyDisableSandbox: true`.

```bash
# Bucket A: SHIPPED in window — these are the wins to lead with
bd list --label=<slug> --status=closed --json | jq '.[] | select(.closed_at >= "<since>")'

# Bucket B: IN PROGRESS — these are "this week" lines
bd list --label=<slug> --status=in_progress --json

# Bucket C: OPEN with recent activity — acknowledged but not shipped
bd list --label=<slug> --status=open --json | jq '.[] | select(.updated_at >= "<since>")'

# Bucket D: OPEN with NO activity in window — risk of "you forgot me" feeling
bd list --label=<slug> --status=open --json | jq '.[] | select(.updated_at < "<since>")'
```

### Step 3: Extract the verbatim quote from each bead

For each bead in buckets A/B/C, parse the `description` field. The QUOTE block has the shape:

```
QUOTE (verbatim — <source>, <timestamp>, <speaker>):
"<text>"
```

Grab the first quote per bead. If a bead has multiple quotes (re-raised items), grab the most recent one — that's what the client most recently said and is therefore what they expect to hear back about.

Beads with `QUOTE: n/a — discovered via …` are Lucy-discovered. **Do not include them in the client reply** — only mention if they directly fix a related complaint, and even then frame as "I also noticed X while looking at this and fixed it."

### Step 4: For shipped beads (Bucket A), pull merge SHA + commit summary

```bash
# From the close note or git log
git log --all --grep="<bead-id>" --oneline | head -3
```

This grounds the "we shipped X" lines in actual code so claims are verifiable.

### Step 5: Draft the reply (plain prose, no markdown)

Hard rule per `feedback_no_markdown_in_email_drafts.md`: NO `**bold**`, NO `>` quotes, NO `#` headers, NO `-` bullets. Numbered lists with "1." plain are OK in WhatsApp.

Hard rule per `feedback_never_name_customers_externally.md`: this reply IS direct comms to the client about their own clinic, so naming them is fine. Do NOT reference other clinics by name even if a fix benefits multiple tenants.

Format:

```
Hi <name> — quick update on your list since <Friday/last week>.

Shipped:
1. <plain-prose summary of fix 1, in language that echoes their verbatim wording>. <one short concrete usage hint, e.g. "Open the appointment, you'll see a new 'Fill on this device' button next to 'Send Form to Client'">.
2. <fix 2>
3. <fix 3>

Working on this week:
- <in-progress item, plain prose>
- <…>

On the list:
- <open item raised recently>
- <…>

Couple of things I'm still waiting on from you:
- <questions back to client — usually repro asks or clarifications>

Let me know how the shipped bits feel in practice.
```

### Step 6: Save the draft + offer copy

Write the draft to `Aestheticc/Growth/Clients/{ClinicName}_update_{YYYY-MM-DD}.md` (use the existing client file's folder convention — check `Aestheticc/Growth/Clients/` for the right `{ClinicName}_*.md` parent). Then print it back in the chat for Shane to copy-paste.

If any required quote is missing (bead's description doesn't have a QUOTE block — e.g. legacy bead created before the convention), flag it explicitly:

> ⚠️ Bead `AestheticcNext-XXX` has no verbatim quote — drafted from interpretation only. Add a QUOTE block before next /client-update run.

## Decision rules during drafting

- **Match Leah's tone**: she writes casual, no caps for sentence starts sometimes, "fab thank you" enthusiasm. Echo that warmth but don't mimic typos.
- **Use HER nouns**: if her verbatim said "medical form" don't translate to "consent form." If she said "the gallery" don't write "the photo gallery section." Her words land more naturally back at her.
- **Lead with her #1 priority** if she explicitly ranked things — find the rank in the bead notes or chat history.
- **Don't oversell**: if a fix is shipped but staging-only or behind a flag, say so. "Live this week" beats over-claiming and then under-delivering.
- **If nothing shipped in window**: open with "Quick honest update — I'm still working through your list, here's where we are." Don't pretend.

## Failure modes to avoid

- ❌ Listing every open bead — Leah doesn't want a backlog dump, she wants to know what's been done and what's next. Cap "On the list" at 3-5 items.
- ❌ Engineering jargon ("we patched the validation on the recipient picker") — translate every line back into her language ("the bulk email picker now has a search box").
- ❌ Quoting verbatim back at her ("you said 'For bulk email it doesn't let you search people'") — that's archive talk, not conversation. Use her language naturally, don't show your work.
- ❌ Forgetting the "waiting on you" section — chasing her for repros/clarifications is part of the cadence.

## Related skills

- `/closeout` — for end-of-session vault writeback
- `/morning` — daily briefing (different scope)
- Memory `feedback_client_bead_labelling.md` — the labelling + QUOTE/INTERPRETATION rule this skill depends on
