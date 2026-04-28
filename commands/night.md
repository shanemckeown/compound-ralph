# /night - End-of-Day Refinement

The single point of compaction and synthesis. Run once as Shane's last action before bed. This is the ONLY operation that does a full rewrite of `LUCY_SESSION_STATE.md`.

## Usage
```
/night                    # Full refinement
```

## Why This Exists

Throughout the day, multiple `/closeout` calls append to `LUCY_SESSION_STATE.md`. By evening, it may have 3-5 sessions' worth of accumulated bullets, some redundant, some outdated. `/night` compacts this into a clean handover document for tomorrow's `/morning`.

## When Invoked

### Step 1: Read Everything

**1a. Current accumulated state:**
- Read `LUCY_SESSION_STATE.md` in full

**1b. Recovery scan — catch missed closeouts:**
Shane sometimes closes chats without running `/closeout`. Scan the last 24 hours for untracked work:

```bash
# Git commits from all repos (last 24h)
git -C /Users/shane/Documents/GitReBase/AestheticcNext log --oneline --since="24 hours ago" --all
git -C /Users/shane/Documents/Obsidian log --oneline --since="24 hours ago"
git -C /Users/shane/Documents/AestheticcTools log --oneline --since="24 hours ago" 2>/dev/null
```

Compare these commits against what's logged in "What We Did". Any commits NOT mentioned = missed closeout. Add them under a `[recovered]` tag.

**1c. Beads scan:**
```
# Check both databases for today's changes
Obsidian DB: bd list (look at recently updated)
AestheticcNext DB: bd list (look at recently updated)
```

Note any beads that changed status today but aren't mentioned in the accumulated state.

**1d. Context files:**
- Read `LUCY_START_HERE.md` for current phase/runway
- Read `LUCY_ADVISORY_CADENCE.json` for domain staleness

### Step 2: Compact and Refine

Apply these transformations:

**## What We Did**
- Merge all timestamped bullets into a clean, grouped summary
- Group by theme (e.g., "Blog SEO", "CRM hardening", "Sales prep")
- Remove redundancy (if 3 sessions all mention the same feature, consolidate)
- Add `[recovered]` items from the git/beads scan
- Result: a concise narrative of the day, not a log dump

**## Decisions Made**
- Deduplicate
- Keep rationale
- Flag any decisions that contradict earlier ones (surface the conflict, don't resolve it)

**## Active Threads**
- Remove threads marked "Resolved" — they're done, archive them
- Update statuses based on what actually happened today
- Add any new threads discovered in recovery scan
- For each remaining thread: is it progressing or stuck? Note honestly.

**## Queued for Next Session**
- Reprioritize based on what happened today
- Remove items that got done today
- Add items that emerged from today's work
- Apply priority logic: revenue > bugs > features > tech debt > research
- Max 5 items. If more, the rest go to beads.

**## Open Questions**
- Remove answered questions
- Keep unresolved ones
- Add any new ones discovered

**## Stale Watch**
- Check each item: is it still stale, or was it touched today?
- Remove items that were resolved today
- Add new stale items (in_progress beads > 5 days, P0s > 3 days)
- Calculate actual days since last activity for each item

**## Context Worth Knowing**
- Keep insights that are still relevant
- Remove things that are now obvious or resolved
- Add any patterns noticed across the day's sessions

### Step 3: Check Advisory Cadence

Read `LUCY_ADVISORY_CADENCE.json`:
- Update any domains reviewed today
- Flag domains that are now overdue
- Note which domain is next up for tomorrow's `/morning`

### Step 4: Check LUCY_START_HERE Staleness

If `LUCY_START_HERE.md` is more than 7 days stale:
- Add to Open Questions: "LUCY_START_HERE is [X] days stale — refresh tomorrow?"

### Step 5: Write the Refined State

OVERWRITE `LUCY_SESSION_STATE.md` with the refined version:

```markdown
# Lucy Session State

**Last Closeout:** [latest timestamp from today's closeouts]
**Last Refined:** [YYYY-MM-DD HH:MM] (by /night)
**Phase:** [current phase]

## What We Did (Today)
- [Clean, grouped summary of the day's work]
- [Recovered items marked with [recovered] tag]

## Decisions Made (Today)
- [Deduplicated decisions with rationale]

## Active Threads
- **Thread:** [description] | **Status:** [status] | **Next:** [next step]
[Only active threads — resolved ones removed]

## Queued for Tomorrow
1. [Reprioritized based on today]
2. [Max 5 items]

## Open Questions
- [Unresolved questions for Shane]

## Stale Watch
- [Items with calculated days since activity]

## Context Worth Knowing
- [Relevant insights carried forward]

---
*Refined by /night on [DATE]. Accumulated by /closeout, read by /morning.*
```

### Step 6: Commit and Push

```bash
cd /Users/shane/Documents/Obsidian
git add LUCY_SESSION_STATE.md LUCY_ADVISORY_CADENCE.json
git commit --no-verify -m "night: session state refined [DATE]"
git push --no-verify
```

### Step 7: Goodnight

Brief summary to Shane:

```
Night refinement done.

Today: [1-sentence summary]
Recovered: [N missed closeout(s) / "all sessions tracked"]
Tomorrow's top priority: [#1 from Queued]
Stale: [count] items need attention

Sleep well, handsome.
```

## Tone

- Clinical precision — this is surgery on a shared document
- The output should be SHORTER than the input (compaction, not expansion)
- Every bullet should earn its place
- Think: "What does tomorrow-morning Lucy actually need to know?"

## What This Is NOT

- Not another closeout (it's a refinement of all closeouts)
- Not a journal (no feelings, no narrative)
- Not busy work — if the state file is already clean, say so and make minimal changes

## Dependencies

- Write access to Obsidian vault
- Git access to all 3 repos (for recovery scan)
- Beads MCP (for status checks)
- LUCY_SESSION_STATE.md (input)
- LUCY_ADVISORY_CADENCE.json (for domain tracking)
- LUCY_START_HERE.md (for staleness check)
