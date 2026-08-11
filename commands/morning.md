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

**1a. Read last session state — BOTH halves:**
- Read `LUCY_SESSION_STATE.md` in Obsidian vault root (the compacted, rolled-up history)
- **Then read every fragment in `Sessions/closeouts/*.md`** — these are closeouts written since
  the last `/rollup` and are NOT yet in the state file. They are the most recent work, so they
  matter most. Missing them means briefing Shane on yesterday while last night is invisible.
- Note: what was worked on, what's next, any open questions
- If more than ~10 fragments are pending, mention it — that's a nudge to run `/rollup`

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

**1c-bis. Auto-bead PRs ready to land (read-only — surfacing only, NEVER auto-land):**
The overnight auto-bead pipeline emits fixes as `auto/*` GitHub PRs. They only reach
main via an interactive `/land-batch` or a PR-based batch — nothing lands them
automatically (by design: no autonomous deploys). This surfaces the backlog so it
never silently stacks up (it hit 19 unmerged once). Count + list; do not merge.
```bash
git -C /Users/shane/Documents/GitReBase/AestheticcNext status >/dev/null 2>&1 && \
gh pr list -R shanemckeown/AestheticcNext --state open --limit 80 \
  --json number,title,headRefName \
  --jq '[.[] | select(.headRefName|startswith("auto/"))] | "\(length) auto-bead PRs open & unmerged"' \
  2>/dev/null || echo "gh unavailable"
```
- If the count is **> 6**, add a **"Ready to Land"** line to the Priority Stack:
  "N auto-bead PRs green and unmerged — run `/land-batch` (or PR-based batch) with eyes on it."
- Flag any that touch clinical/consent/prescribing/auth/stripe/migration paths as
  **hold-for-review**, not batch-eligible.

**1d. Check for staleness:**
- Is STATE_OF_THE_BUSINESS.md more than 7 days old? (it's the canonical Layer-1 status file)
- Any in_progress beads older than 5 days?
- Any P0 beads older than 3 days?

**1e. Read current context:**
- Read `STATE_OF_THE_BUSINESS.md` for current phase, runway, MRR, customer count, active strategic flags (this is canonical — NOT LUCY_START_HERE.md, which is deprecated/stale)
- Check if any Ralph processes are running:
  ```bash
  ls ~/.worktrees/AestheticcNext/*/.compound/status.md 2>/dev/null
  ```

**1j. Three-Week Plan — today's card (added 2026-07-21, per Shane: "I need you to see it every day"):**
- Read `Strategy/FOUNDER_TRAJECTORY_2026-07/THREE_WEEK_PLAN_2026-07-20.md` (covers Mon 20 Jul – Fri 7 Aug 2026). This doc exists precisely because five prior "selling is the bottleneck" diagnoses produced zero behavior change — the whole point is that it gets surfaced, not re-discovered.
- Work out which plan-day today is:
  - Mon 20 – Fri 24 Jul: pull that exact day's card verbatim from §4 (each day is named and fully scripted).
  - Sat 25 / Sun 26 Jul: weekend note from §4 (Sat max 2h CRM hygiene only; Sun zero business work).
  - Mon 27 – Fri 31 Jul (Week 2) or Mon 3 – Fri 7 Aug (Week 3): use the daily template from §3 (sales block 08:30-11:00, dispatch window 11:00-12:00, demos/onboarding 13:30-16:00, flex 16:00-17:30, nightly log 21:00), then layer in any dated milestone from §5 that lands on today specifically (e.g. Mon 27 Jul = NY Skin go-live/cutover day + Jim/Marcelo day-5 bumps if no reply; Tue 28 Jul = cold calling starts, 10 dials/day, + Ansh Dhir slot; Tue 4 Aug = partnership slot; Fri 7 Aug = three-week review + Week 4 decision).
  - Before 20 Jul or after 7 Aug: the plan window has ended or not started — say so plainly, check whether a retro/Week-4 decision was ever written (§7's fallback trigger: <3 demos delivered by Fri 31 Jul or the Fri 7 Aug bar unmet means the founder-does-outbound motion is falsified at its current design — flag if that threshold was ever evaluated).
- **Dispatch-gate status (M2, the load-bearing mechanism):** "No fleet dispatches, land-batches, or build sessions before today's log shows ≥5 outbound touches. Sole exception: S1 production incidents." There is currently **no dedicated touch-log file** — the plan's own "nightly log" ritual (§3, §6) has no file to write to yet. Best available proxy until one exists: count today-dated entries in `Growth/LEAD_TRACKER.md` (the real CRM) plus any Gmail sends visible via Gmail search `after:` today to known warm contacts. State the count as a proxy explicitly, don't imply false precision.
- **Say it plainly, don't bury it in review-length prose:** one line, e.g. `Dispatch gate: 2/5 warm touches today (proxy count, verify manually) — fleet dispatch should stay CLOSED until 5.` If it's already past 11:00 BST and the gate reads under 5, call that out as a live breach happening *today*, not something for tomorrow's CEO review to discover after the fact — that's the entire reason this section exists.
- Cross-check against any live CEO-review response (`Brain/CEO_Reviews/Responses/[today].md` if one exists yet) — if the gate was already flagged bypassed there, don't re-litigate, just carry the status forward.

**1i. Outreach data (PRIMARY during the outreach sprint):**
Run these whenever the phase is an outreach/sales sprint — they lead the briefing.
- **Instantly campaigns** (via `instantly` MCP): `count_unread_emails` (new replies waiting), `get_campaign_analytics` for the live campaigns (sent today, reply rate, **bounce rate** — the ramp is gated on bounce <3%), and `list_emails` to surface any positive replies needing a human.
- **Warm-lead CRM:** read `Aestheticc/Growth/Outreach/OUTBOUND_CRM.md` — who's drafted-but-unsent, who's due a follow-up, the 📞 call-candidates, and any website signups owed a same-day hello.
- If the `instantly` MCP is not connected, say so loudly (don't silently skip — replies = revenue) and fall back to the CRM file.

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
- **Runway:** ~X days (from STATE_OF_THE_BUSINESS.md or last known)
- **MRR:** £X (or £0 if pre-revenue)
- **Phase:** [current phase from STATE_OF_THE_BUSINESS.md]
- **Beads:** X open across both DBs | X in progress | X blocked | X ready

### Three-Week Plan — Today's Card
[Plan-day identity, e.g. "Week 1, Tue 21 Jul — warm calls + the academy"]
- **Dispatch gate (M2):** X/5 warm touches logged today (proxy — see 1j) — [OPEN for fleet dispatch / CLOSED, work the card first]
- **Today's blocks:** [the actual time-blocked card for today, pulled from §3/§4/§5 per 1j — sales block first, always]
- **Named actions due today:** [anyone specifically named for today — day-5 bumps, a go-live date, a partnership-slot contact, a milestone from §5]
- [If the plan window hasn't started or has ended, or a scoreboard threshold (§6/§7) was due and never evaluated: say so directly]

### Capture Check — 🔴 MANDATORY, NEVER OMIT
Pull any new client-call transcripts, then say what landed. **If it fails, print literally `ZOOM FETCH FAILED: <reason>`** — never omit the line.

```bash
node ~/.config/aestheticc/capture-watcher/zoom-fetch-transcripts.mjs --days 7
```

- **Zoom:** N of M instances had a transcript, K newly downloaded [name the participants — the topic is always the useless "Personal Meeting Room", so read the `<v Name>` speaker tags to say *who*]
- **Unprocessed in `~/Downloads`:** count of `*.vtt` / `*transcript*.txt` / `WhatsApp Chat*.zip` not yet run through `/capture-call` — **these carry unextracted client promises**
- **capture-watcher:** last run time. 🔴 **If over 24h, say so loudly** — it was dead 30 hours with an empty error log and five client captures piling up (`LUCY-fdf18`).

> Zoom fetching lives here rather than on a timer for the same reason as the Fleet Check below: a timer joins the population of unwatched timers. This runs on a path Shane opens for his own reasons. The script keeps its own watermark (`~/.config/aestheticc/zoom-fetch-state.json`), so re-running is free and never re-downloads.

### Fleet Check — 🔴 MANDATORY, NEVER OMIT
Run `python3 ~/.claude/scripts/fleet-supervisor.py` and report it here. **If it fails, cannot run, or you skip it, print literally `FLEET CHECK DID NOT RUN: <reason>`.** An absent section is not permitted — the whole point is that its absence is visible.

- **Coverage:** N sessions (from `claude agents --json`), M blocked and triaged
- 🔴 **Finished but NOT landed:** [bead + branch + commits, or "none"]
- 🔴 **Awaiting Shane:** [sessions genuinely stopped on a decision, with how long]
- **Interrupted:** [sessions cut off mid-response — untrustworthy until re-verified]
- **Watchers alive?** capture-watcher last run, bd-reap, night-batch — say when each last fired, and **say so loudly if any has not run in over 24h**

> **Why this lives here and not in a timer** (decided 2026-08-11, after Codex and Fable split on it): a launchd job would join `bd-reap`, `capture-watcher` and `ops-daemon` — all of which run unwatched, and not one has ever reported itself down. `capture-watcher` was dead for 30 hours with an empty error log and five unprocessed client captures piling up, and nothing said a word. `/morning` is the one recurring trigger attached to something Shane wants **for himself**, so it will not quietly stop being run the way bedtime did. The who-watches-the-watcher regress terminates at a human's own recurring want — that is the only place it can terminate. Blocked work rots over days, so there is no latency argument for a timer.

### Last Session
[1-2 sentences from `LUCY_SESSION_STATE.md` — what was done, what was queued]
- **Un-rolled closeouts:** N fragments in `Sessions/closeouts/`, oldest X days, state file Y KB — from `ls` and `stat`, no rollup logic. **If N > 10 or the state file is over 60 KB, say "run `/rollup`".** `/rollup` fires on Shane's memory, which is the same trigger class that killed `/night`; this line is what makes forgetting it visible.

### Outreach (lead during a sales/outreach sprint — skip if phase isn't outreach)
- **Replies waiting:** X unread in Instantly [flag any positive/booking replies by name]
- **Campaign health:** sent today X | reply rate X% | **bounce X%** [if ≥3%, ramp is blocked — say so]
- **Warm leads due:** [drafted-but-unsent, follow-ups owed, call-candidates from OUTBOUND_CRM]
- **Signups owed a hello:** [new website signups not yet contacted]

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
- [ ] **Follow up warm leads** — Check Instantly for replies + work the `Aestheticc/Growth/Outreach/OUTBOUND_CRM.md` queue. Anyone who opened/clicked but didn't respond gets a manual nudge. Sequence: `Aestheticc/Growth/Playbooks/WARM_LEAD_FOLLOWUP_SEQUENCE.md`
- [ ] **Cold walk-in planned?** — If near clinic areas today, which 2-3 are on the list?
[Lucy: flag if any of these haven't happened in 3+ days based on LUCY_SESSION_STATE.md]

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
- STATE_OF_THE_BUSINESS.md (canonical phase / runway / MRR / customer count — replaces deprecated LUCY_START_HERE.md)
- `instantly` MCP (for reply + campaign health during outreach sprints)
- Aestheticc/Growth/Outreach/OUTBOUND_CRM.md (warm-lead + signup queue)
- LUCY_ADVISORY_CADENCE.json (for domain review tracking)
- AestheticcNext/Product/QA/ (for QA health — dashboard, findings, methodology)
- Google Sheets MCP (optional — for CFO domain, pending setup)

## Sprint Emphasis

During an **outreach/sales sprint** (check STATE_OF_THE_BUSINESS.md phase), the briefing leads with **Outreach** + **Priority Stack** + **Ops Health**. The **QA Health**, **Beads/git**, and code-dev sections drop to background — show QA only if an S1 is open (it's still a deploy gate) or a weekly audit is overdue; otherwise one line or skip. Don't let code-dev detail bury the sales picture.
