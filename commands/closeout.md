# /closeout - Session Handoff (Append-Only)

Append this session's contributions to the shared state file. Parallel-safe — multiple sessions can close out without destroying each other's work.

## Usage
```
/closeout                 # Full closeout with state append
/closeout quick           # Minimal — just "What We Did" bullets
```

## Core Rule

**NEVER overwrite LUCY_SESSION_STATE.md.** Always READ first, then APPEND/UPDATE specific sections. Other sessions may have written to it since you started.

## When Invoked

### Step 1: Gather What Happened This Session

Review the current conversation to extract:

1. **What was worked on** — concrete outputs, not just topics discussed
2. **Decisions made** — anything Shane decided or approved
3. **Beads changed** — created, closed, updated
4. **Code changes** — commits, deploys
5. **New threads started** — multi-session work that began this session
6. **Threads resolved** — things that were in Active Threads that are now done
7. **Open questions** — anything unresolved that needs Shane's input
8. **New context** — non-obvious insights, risks spotted, patterns noticed

### Step 2: Read Existing State

Read `/Users/shane/Documents/Obsidian/LUCY_SESSION_STATE.md` in full. Understand what's already there from other sessions today.

### Step 3: Merge Changes Into State File

Apply these merge rules to each section:

**Header block:**
- Update `**Last Closeout:**` to current timestamp
- Do NOT change `**Last Refined:**` (only /night writes this)
- Do NOT change `**Phase:**` unless Shane explicitly changed it this session

**## What We Did**
- APPEND new bullets at the top of the section, prefixed with timestamp
- Format: `- [HH:MM] concrete thing that happened`
- Never remove existing bullets — /night handles compaction
- 3-5 bullets max per closeout. Be specific.

**## Decisions Made**
- APPEND any new decisions at the top
- Format: `- [HH:MM] Decision: what was decided (rationale)`
- If no decisions: don't add anything, don't write "None"

**## Active Threads**
- ADD new threads this session started
- UPDATE status/next of threads this session touched
- LEAVE threads you didn't touch completely alone
- If you RESOLVED a thread, change its status to "Resolved [date]" but don't delete it (/night cleans these up)

**## Queued for Next Session**
- ADD items if this session identified new next-steps
- Don't renumber or reprioritize existing items — /night handles that

**## Open Questions**
- ADD new questions
- REMOVE questions that got answered this session (this is the one place you CAN delete)

**## Stale Watch**
- ADD newly discovered stale items
- NEVER remove items — only /night can resolve stale items after verification

**## Context Worth Knowing**
- APPEND new insights
- Format: `- [HH:MM] insight text`

### Step 4: Write the Merged File

Write the merged result back to `LUCY_SESSION_STATE.md`.

### Step 5: Append Structured Session Metadata

After the prose sections, append a YAML metadata block to `LUCY_SESSION_STATE.md` inside a fenced code block. This gives structured audit data that's machine-parseable.

```yaml
# --- Session Metadata [YYYY-MM-DD HH:MM] ---
session:
  started: YYYY-MM-DDTHH:MM  # approximate from first message
  ended: YYYY-MM-DDTHH:MM    # now
  beads_completed: [LUCY-xxxx, AestheticcNext-yyyy]  # or []
  beads_created: [LUCY-zzzz]  # or []
  commits: [abc1234]  # short SHAs, or []
  deployed: false  # true if staging or prod deploy happened
  initiative_served: LUCY-xxxx  # primary initiative this session advanced (pick one)
  # If work didn't serve an initiative, write "none — <reason>" and flag it
```

Place this block at the very end of the "What We Did" section, after the prose bullets. One block per closeout — they accumulate and /night compacts them.

**Initiative alignment check:** Before writing the block, verify the session's work traces to one of:
- `LUCY-z6pf` — INIT-1: MRR
- `LUCY-osi9` — INIT-2: Ship features for pipeline
- `LUCY-rvdk` — INIT-3: Platform reliability
- `LUCY-cfqq` — INIT-4: Growth engine
- `LUCY-r2zp` — INIT-5: Founder Operating System

If it doesn't clearly serve any initiative, note `initiative_served: none — <reason>` — this is a signal that we may be doing unfocused work.

### Step 6: Update Advisory Cadence (if applicable)

Read `LUCY_ADVISORY_CADENCE.json`. If any advisory domain was reviewed during this session:
1. Update `last_reviewed` to today's date
2. Recalculate `next_review` based on `frequency_days`
3. Set `status` to "current"
4. Write the updated JSON back

### Step 7: Auto-Commit & Push

After writing the state file, **automatically** run git commit and push. Do NOT just show a checklist — execute the commands:

1. Run `git status` to identify changed files from this session
2. Stage relevant files: `LUCY_SESSION_STATE.md`, `.beads/issues.jsonl`, and any other files created/modified during this session. **Do NOT stage unrelated untracked files** — only files you touched.
3. Commit with message format: `closeout: <short summary of session work>`
   - Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>`
4. Push to remote: `git push`
5. Confirm success to the user

If any step fails, report the error but don't retry destructively. The user can fix manually.

## State File Template

If `LUCY_SESSION_STATE.md` doesn't exist or is empty, create it with this structure:

```markdown
# Lucy Session State

**Last Closeout:** [YYYY-MM-DD HH:MM]
**Last Refined:** never
**Phase:** [from LUCY_START_HERE]

## What We Did
- [HH:MM] [bullets from this session]

## Decisions Made
- [HH:MM] [decisions from this session]

## Active Threads
- **Thread:** [description] | **Status:** [status] | **Next:** [next step]

## Queued for Next Session
1. [item]

## Open Questions
- [questions or "None — clear path forward"]

## Stale Watch
- [items or empty]

## Context Worth Knowing
- [HH:MM] [insights]

---
*Accumulated by /closeout. Refined by /night. Read by /morning.*
```

## Tone

- Factual, concise — this file is read by machines (future Lucy sessions)
- Specific outputs, not vague descriptions ("committed blog SEO overhaul, 22 files" not "worked on blog")
- 3-5 bullets per section per closeout. Not a novel.

## What This Is NOT

- Not a conversation summary
- Not a full rewrite — you are APPENDING
- Not a to-do list (beads handles that)
- Not optional — run this at the end of EVERY session with meaningful work

## Edge Cases

**Short session (< 5 min, just a quick question):**
- Append a single bullet: `- [HH:MM] Quick: [one-liner]`

**State file looks corrupted or has weird formatting:**
- Preserve all existing content. Append your section at the end with a `---` separator.
- /night will clean it up.

**Can't read the state file (permissions, missing):**
- Create it fresh using the template above. Better than nothing.

## Dependencies

- Write access to Obsidian vault
- Current conversation context (reviews own conversation)
- LUCY_ADVISORY_CADENCE.json (for advisory tracking)
