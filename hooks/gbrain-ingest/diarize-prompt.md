# Diarization instructions

You are producing a single-page judgment of a Claude Code session transcript, for
storage in GBrain (the personal knowledge brain on the Hermes VPS).

## Input

You will be given a path to a JSONL transcript. Read it with the Read tool.
The transcript is a sequence of user turns, model responses, tool calls, and
tool results.

## Output format

Respond with a single fenced JSON block (and nothing else), shape:

```json
{
  "slug": "claude-session-YYYY-MM-DD-short-topic-keyword",
  "body": "...markdown body...",
  "entities": ["Kelly-Ann Reed", "Omorphia Aesthetics", "LUCY-feaj", ...]
}
```

- `slug`: kebab-case, start with `claude-session-`, include the date and
  two-to-four words that capture the gist. Avoid session UUIDs.
- `body`: markdown, roughly 200-600 words. See structure below.
- `entities`: canonical names of people, clinics, bead IDs, file paths, or
  projects that appeared meaningfully. Skip chatter mentions.

## Body structure

```markdown
# <Short title>

**Date:** YYYY-MM-DD · **Session:** <session_id first 8>

## What we did
Two or three sentences on the core thread of the session.

## Decisions
- Bullet list of concrete decisions made this session, with rationale where
  non-obvious. Skip tentative ideas.

## Open threads
- Bullet list of questions, follow-ups, or work that was not closed out.

## Files / beads touched
- Inline paths: `path/to/file.md`, `LUCY-abcd`, etc.

## Context for future-me
Two or three sentences a future Claude session would need to pick up cold,
without re-reading the whole transcript. Point at the canonical files, not
the conversation scroll.
```

## Rules — non-negotiable

1. **Redact secrets** on sight. Any token, API key, password, database URL,
   session cookie, HMAC secret, or signed URL → replace with `[REDACTED]`.
   Examples: `whsec_...`, `Bearer ...`, `sk-ant-...`, `postgres://...`,
   cookie values, service-account JSON contents.
2. **No speculation.** If the transcript didn't say something happened, don't
   claim it did.
3. **Kelly-Ann not "Kelly"** — use canonical full names for people when
   available, so GBrain's graph can wire them to the right entity.
4. **Bead IDs as entities** — any `LUCY-xxxx` or `AestheticcNext-xxxx`
   mentioned meaningfully goes in the entities array.
5. **Terse.** Future-me reads fast. Three sharp sentences beats a paragraph.
6. **No emoji** in the body unless the transcript itself discussed emoji as
   a topic.
