# jev-draft-check (LUCY-4e6od)

A PreToolUse check on **client copy** (clinic owners and prospects). It runs before a Gmail
`create_draft` / `update_draft` / `reply` / `send_message`, or a write to a
`Today's replies/… - Email.md` or `… - WhatsApp.md` file. REPORT and index files are
skipped.

It enforces three of Shane's standing rules:

| Rule | Source memory |
|---|---|
| **blame**: no blame or apology ("that one's on us", "sorry for the delay", "nearly a month late") | `feedback_never_attribute_blame_in_client_copy` |
| **buildState**: never reveal build state ("coming soon", "to be transparent", "a couple of weeks behind") | `feedback_promises_are_build_commitments_never_retract` |
| **callOffer**: no unprompted call offers ("happy to jump on a quick call") | `feedback_no_call_offers_in_client_replies` |

**Two layers:**
1. A local phrase list. It always runs and never uses the network.
2. Jev on the paraphrases the list misses (threshold 0.8).

On a hit, the write is **denied once** with the reason. If the session checks and the copy is
right as written (the client asked for a call, or Shane wants a personal apology), writing
the **identical** text again goes through.

**Not covered (by design):**
- *Invented UI paths.* These need checking against the codebase.
- *Clinical claims.* Clinical words are never sent to Jev.

## Patient data
Rules: Shane, 26 Sep: no patient data to TypeSafe. Orchestrator, 26 Sep: a draft with a
patient name or treatment detail is never sent to Jev.
- A draft with a hard identifier, an honorific, the word "patient", or any clinical term
  **never goes to Jev**. The phrase list still runs.
- Anything that is sent passes the shared allow-list redaction (`../jev-common/privacy.mjs`
  plus `vocab.json`). So a bare name with no cue still cannot leave: only common lowercase
  dictionary words from Shane's own replies go out.
- Tests prove there are zero network calls for a patient name + treatment detail, a name
  with an honorific or "patient" cue, a treatment detail alone, and patient contact details.

## Evidence (27 Sep 2026)
- Labelled corpus: 26 real `Today's replies` drafts plus 8 paraphrases. **34/34 agree.**
  Labels come from Shane's rules and the incidents in his memory files. They were set after
  seeing outputs, so this is not a blind test.
- It flags the exact drafts behind Shane's corrections:
  - Robyn + Nicole, 19 Sep: "that one's on us".
  - Linzi, 24 Sep: the quick call offer.
- It also flags the 16 Sep all-clinics iOS launch message, which said "Tap to Pay is a couple
  of weeks behind it" (build state).
- 12 of the 26 real drafts are never sent to Jev because of patient risk; the phrase list
  covers them.
- Fixtures store phrase hits, Jev scores and *redacted* text only, never raw client drafts.

## Run
    node --test ~/.claude/hooks/jev-draft-check/test/hook.test.mjs   # offline
    node eval/build-vocab.mjs && node eval/corpus.mjs                  # rebuild vocab, re-measure (uses the key)

## Enable (Shane's call; not enabled)
Add this to the `PreToolUse` array in `~/.claude/settings.json`:

    { "matcher": "mcp__claude_ai_Gmail__create_draft|mcp__claude_ai_Gmail__update_draft|mcp__claude_ai_Gmail__reply|mcp__claude_ai_Gmail__send_message|Write|Edit|MultiEdit",
      "hooks": [ { "type": "command", "command": "node /Users/shane/.claude/hooks/jev-draft-check/hook.mjs", "timeout": 15 } ] }

It fails open: no key, an HTTP error, a bad response, the 8 s deadline, the 12 s watchdog,
or state it cannot write all allow the write.
