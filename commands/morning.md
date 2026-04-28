# /morning - Lucy's Daily Briefing

Generate a structured morning briefing for Shane. Pulls live data from all systems and produces an actionable priority stack.

## Usage
```
/morning                  # Full briefing
/morning quick            # Just priorities + blockers (shorter)
```

## When Invoked

Run these data-gathering steps IN PARALLEL, then synthesize into the briefing format below.

### Step 1: Gather State (All Parallel)

**1a. Read last session state:**
- Read `LUCY_SESSION_STATE.md` in Obsidian vault root
- Note: what was worked on, what's next, any open questions

**1b. Pull beads from BOTH databases:**
```
Obsidian DB (/Users/shane/Documents/Obsidian):
- mcp beads stats
- mcp beads list --status=in_progress
- mcp beads ready --priority=0 (P0 only)
- mcp beads blocked

AestheticcNext DB (/Users/shane/Documents/GitReBase/AestheticcNext):
- mcp beads stats
- mcp beads list --status=in_progress
- mcp beads ready --priority=0 (P0 only)
- mcp beads blocked
```

**1c. Check git activity (last 48h):**
```bash
# AestheticcNext
git -C /Users/shane/Documents/GitReBase/AestheticcNext log --oneline --since="2 days ago" | head -20
git -C /Users/shane/Documents/GitReBase/AestheticcNext status --short

# Obsidian
git -C /Users/shane/Documents/Obsidian log --oneline --since="2 days ago" | head -10
```

**1d. Check for staleness:**
- Is LUCY_START_HERE.md more than 7 days old? (check "Last Updated" line)
- Any in_progress beads older than 5 days?
- Any P0 beads older than 3 days?

**1e. Read current context:**
- Skim LUCY_START_HERE.md for current phase and runway
- Check if any Ralph processes are running:
  ```bash
  ls ~/.worktrees/AestheticcNext/*/.compound/status.md 2>/dev/null
  ```

**1h. Pull live platform data (via Aestheticc Ops MCP):**
If the `aestheticc-ops` MCP server is available, call these tools:
- `get_platform_health` → live business counts, payments, SMS, Stripe/Twilio status
- `get_onboarding_status` → which clinics need attention, who's stuck
- `run_health_check` → any failed payments, Twilio errors, dormant clinics, SMS failures

Inject results into the briefing:
- **Pulse section:** Replace static MRR/business counts with live data from `get_platform_health`
- **After Priority Stack:** Add an "Ops Health" section with any findings from `run_health_check`
- **Stale Items:** Include stuck onboarding clinics from `get_onboarding_status`

If the MCP server is not connected, skip this step silently (don't error).

**1f. Read advisory cadence tracker:**
- Read `LUCY_ADVISORY_CADENCE.json` in Obsidian vault root
- Compare each domain's `next_review` against today's date
- Flag any domain where status is "overdue" or `next_review` <= today
- Note which domains have `last_reviewed: null` (never done)

**1g. QA health check (weekly — run if last check > 7 days ago):**
- Read `AestheticcNext/Product/QA/QA_DASHBOARD.md` for current scores
- Count open S1 and S2 findings from `AestheticcNext/Product/QA/QA_FINDINGS.md`:
  ```bash
  grep -c "OPEN" /Users/shane/Documents/GitReBase/AestheticcNext/Product/QA/QA_FINDINGS.md
  ```
- Check when each layer was last audited (from dashboard "Last Audited" column)
- Flag if: any S1 is OPEN, any layer not audited in >14 days, or any fixed findings unverified
- Check last pass date from `QA_PASSES.md` — if >7 days since last pass, flag for audit
- Note: Full methodology at `AestheticcNext/Product/QA/QA_METHODOLOGY.md`

### Step 2: Synthesize Briefing

Output the briefing directly to the conversation (DO NOT write to a file unless Shane asks). Use this exact format:

```markdown
## Morning Briefing — [DATE]

### Pulse
- **Runway:** ~X days (from LUCY_START_HERE or last known)
- **MRR:** £X (or £0 if pre-revenue)
- **Phase:** [current phase from LUCY_START_HERE]
- **Beads:** X open across both DBs | X in progress | X blocked | X ready

### Last Session
[1-2 sentences from LUCY_SESSION_STATE.md — what was done, what was queued]

### Priority Stack (Today)
1. **[P0]** [Most critical item — customer-facing or revenue-blocking]
2. **[P0/P1]** [Second priority]
3. **[P1]** [Third priority]

### Ops Health
[From run_health_check MCP tool. Only show if findings exist. Skip if all clear.]
- **Findings:** X total (Y HIGH, Z MEDIUM)
- [List each finding: severity + message]
- [Or "All clear — no issues detected"]

### Clinic Status
[From get_onboarding_status. Only show clinics that need attention.]
- [Clinic Name] — stuck at [stuckAt] (created [X days ago])
- [Or "All clinics progressing normally"]

### Stale Items
- [Any items that are drifting — in_progress too long, P0 untouched, etc.]
- [Or "None — everything looks current"]

### Drift Check
[Honest assessment: Is current work aligned with stated phase?
 e.g., "Last 48h was 90% technical, 10% sales. Phase says SALES SPRINT."
 This is NOT judgment — it's information. Sometimes technical work IS the priority.]

### Blocked
- [List blocked beads with what's blocking them, or "None"]

### QA Health
[From QA_DASHBOARD.md — only show if issues or audit overdue. Skip if all green.]
- **Open S1:** X | **Open S2:** X | **Unverified fixes:** X
- **Overdue layers:** [layers not audited in >14 days, or "All current"]
- **Last audit pass:** [date] ([X days ago])
- [If S1 > 0: "DEPLOY GATE: S1 findings block new feature deploys. Fix first."]
- [If last pass > 7 days: "Suggest: run `/audit` on Layers 1-3 (highest impact)"]
- [If all clean: "QA green — 0 S1, X S2 remaining, last pass [date]"]

### Advisory Domains Due
[Read from LUCY_ADVISORY_CADENCE.json. List any domains where review is due or overdue.]
- **[Domain]**: Last reviewed [date] ([X days ago]). Due for [frequency] review.
  Suggested action: [what Lucy would do — e.g., "Run CFO report from Stripe + Finance Tracker"]
- [Or "All domains current — next review: [domain] on [date]"]

### Background Suggestions
[1-2 things Lucy could work on in background while Shane does human-only tasks.
 Prioritize overdue advisory domains, then research, then autonomous coding.]

### Daily Promoter Checklist
Shane is the promoter. These non-negotiable daily activities take <30 mins total:
- [ ] **LinkedIn** (1 post, 10 mins) — 42 packs ready at `~/Documents/AestheticcTools/project_files/linkedin/`. Use `CONTENT_PILLARS.md` for today's pillar. Mon=Industry, Tue=Founder, Wed=Compliance, Thu=Ops, Fri=Hot Take, Sat=Soft Sell, Sun=Building in Public.
- [ ] **Follow up warm leads** — Check Instantly for replies. Anyone who opened/clicked but didn't respond gets a manual nudge. Sequence: `Aestheticc/Growth/Playbooks/WARM_LEAD_FOLLOWUP_SEQUENCE.md`
- [ ] **Cold walk-in planned?** — If near clinic areas today, which 2-3 are on the list?
[Lucy: flag if any of these haven't happened in 3+ days based on SESSION_STATE.md]

### One Thing You Might Not Have Thought Of
[A genuinely useful observation — could be about the business, a pattern in the data,
 something from a previous session that's relevant, or a strategic consideration.
 NOT filler. If nothing genuinely useful, skip this section.]
```

### Step 3: Save Briefing (Optional)

Only if Shane says "save it" or the `/morning` argument is not "quick":
- Create `MORNING_BRIEFINGS/[YYYY-MM-DD].md` with the output
- This creates a historical record that future sessions can reference

## Tone

- Direct, not performative
- Data first, opinions second
- Accountability without guilt — runway is a fact, not a judgment
- If everything is going well, say so briefly and move on
- If something is drifting, name it clearly but without drama

## Priority Logic

When stack-ranking priorities:
1. **Revenue/customers** always wins (unless literal fire)
2. **App store rejections** are blocking revenue, so they count as revenue
3. **QA S1 findings** — open S1 = deploy gate. These block everything until resolved.
4. **P0 bugs in production** that users would hit
5. **Security/compliance** items blocking launch (QA S2 findings that affect live users)
6. **Feature gaps** that lost a specific deal (not hypothetical)
7. **Technical debt** only if it's actively causing problems
8. **Research/strategy** is important but never urgent

## What This Is NOT

- Not a to-do list (beads handles that)
- Not a status report (it's a decision-support tool)
- Not comprehensive (it's the TOP things, not ALL things)
- Not a replacement for /audit, /do-bead, or other execution skills

## After the Briefing

If an advisory domain review is due and Shane has time/interest:
1. Run the review (see domain-specific protocols below)
2. Update `LUCY_ADVISORY_CADENCE.json` with new `last_reviewed` date and recalculate `next_review`
3. Save the review output to `MORNING_BRIEFINGS/[domain]/[YYYY-MM-DD].md`

### Domain Review Protocols

**CFO:** Read LP/Finance Tracker.md, LP/Loan_Runway_Plan_Dec2025.md, check Stripe MCP for revenue data, Google Sheets if MCP available. Output: cash position, burn rate, runway, tax considerations, financial risks/opportunities.

**Health:** Read LP/Personal_Habits.md, LP/Morning Routine.md, LP/Evening Routine.md, LP/Meditation*.md. Output: what's working, what's slipping, one evidence-based suggestion. Keep it practical, not preachy.

**Life Strategy:** Read LP/INDEX.md, LP/Moving Out Plan.md, LP/What Do I Want Out Of Life?.md, LP/Primary Goal.md. Output: progress against goals, decision points approaching, things that need attention.

**Research:** Web search for competitor news, industry trends, relevant tech. Check CSAI/CTO/Moonshots/ for open research threads. Output: 3-5 bullet findings with sources.

**Holistic:** Read across ALL domains. Output: "Here's what I see when I look at everything together" — connections, tensions, opportunities that only appear when you zoom out.

## Dependencies

- Beads MCP plugin (for stats, list, ready, blocked)
- Git (for recent activity)
- LUCY_SESSION_STATE.md (for session continuity)
- LUCY_START_HERE.md (for phase and runway context)
- LUCY_ADVISORY_CADENCE.json (for domain review tracking)
- AestheticcNext/Product/QA/ (for QA health — dashboard, findings, methodology)
- Google Sheets MCP (optional — for CFO domain, pending setup)
