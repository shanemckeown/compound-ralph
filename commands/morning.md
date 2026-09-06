# /morning - Lucy's Daily Briefing

Generate a structured morning briefing for Shane. Pulls live data from all systems and produces an actionable priority stack.

## Usage
```
/morning                  # Full briefing
/morning quick            # Just priorities + blockers (shorter)
```

## When Invoked

### Step 0: Claim Orchestrator Role — 🔴 MANDATORY, NEVER SKIP (added 2026-08-25)

> **Shane, 2026-08-25:** *"does /morning include taking on the role of orchestrator. I think it should."*

`/morning` is the one recurring trigger Shane runs in his own terminal for himself — same
reasoning that keeps Fleet Check unscheduled applies here: this is where "am I the orchestrator"
gets settled, not left to a separate manual step someone has to remember to do first.

```bash
python3 ~/.claude/scripts/fleet-role.py <this-session-id> --check 2>&1
```

- **No live claim exists, or the claim is stale** → claim it:
  `python3 ~/.claude/scripts/fleet-role.py <this-session-id> --claim`. Report "Orchestrator role
  claimed" in one line at the top of the briefing.
- **A DIFFERENT session already holds a live claim** → do NOT steal it. Surface it plainly
  instead: "Orchestrator is already held by `<session>` — this session stays SUB." Default-deny
  holds; a session that isn't sure is not the orchestrator (CLAUDE.md "Orchestrator vs
  sub-Claude").
- Never silently skip this step, and never claim over a live, contested holder without telling
  Shane.

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

**1k. Active engagements — 🔴 MANDATORY, NEVER OMIT (added 2026-08-12 at Shane's request):**

> **Shane, 2026-08-12:** *"we've got more and more clients joining now and so me trying to juggle them is getting difficult, so this morning call needs to basically be aware of anyone who we're actively working for, and by actively working for I mean in the process of onboarding or creating something for them, like an existing client who wants a new feature or whatever else."*

The briefing was code-shaped and client-blind. A client mid-onboarding, or one waiting on a feature we promised, was invisible unless a bead happened to surface. Run:

```bash
node /Users/shane/Documents/Obsidian/Aestheticc/Ops/scripts/active-engagements.mjs
```

**Derived, never hand-maintained** — it reads `Clients/PROMISE_TRACKER.md` (rows at 🔴/⏳/🔨/🧩), open client-labelled beads in both DBs, and each `Clients/<Name>/INDEX.md` status line. All three update as a side effect of doing the work, so this cannot go stale the way `STATE_OF_THE_BUSINESS.md` did (84 days, because its maintenance contract lived only in its own frontmatter).

Report the **top 5–6 by score**, not all 17 — the point is triage, not a directory. For each: client, overdue count, what's owed *today or tomorrow*, and the single next physical act. Then:

- **🔴 Lead with anything dated today or tomorrow.** A promise with a date attached is the one that turns into a broken promise overnight.
- **Call out meetings agreed in chat but absent from both calendars** — highest-frequency failure in the whole taxonomy, and it has already cost a real week with The Refine Room.
- **Name new revenue asks.** An existing client asking for something we don't sell yet (NY Skin asking about managed ads, 12 Aug) is a revenue line, not a support ticket. Never let one sit as an unanswered WhatsApp.
- **Flag any active client with no `INDEX.md`** — Chris Flanagan and Tom Welby both had live promises and no index. `Clients/README.md` is the map; a client missing from it gets worked on blind.
- If a client has gone quiet **while we owe them something**, say so. Their silence is not the same as no debt.

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

**1l. Repo health — 🔴 MANDATORY, NEVER OMIT (added 2026-08-24, after a real incident):**

> **Shane, 2026-08-24, on discovering it:** *"jesus that's crazy... I think we likely need /morning modified so this sort of buildup is limited to at least alerting me when I use /morning."*

The AestheticcNext trunk checkout (`/Users/shane/Documents/GitReBase/AestheticcNext`) is meant to stay clean between sessions — all real work happens in isolated worktrees, per its own CLAUDE.md. It didn't: local `main` was found 375 commits behind `origin/main` (a stale checkout, not "unpushed work" — a session that morning misread the divergence direction and initially reported the opposite), and ~90 real files (client onboarding/import scripts going back 10+ days) were sitting uncommitted in that same checkout, one `git clean -fd` from being unrecoverable. Nothing had ever checked for either condition. Run:

```bash
python3 ~/.claude/scripts/repo-health-check.py
```

- Report exactly what it prints — ahead count, behind count, uncommitted-file count, with its own directional wording (it exists specifically so this never gets eyeballed and misread again).
- **If it exits non-zero, this is not a background note — lead with it**, above Priority Stack if the count is large (multi-day drift or >10 uncommitted files). Small/expected noise (a handful of files mid-session) doesn't need alarm framing, but say the number.
- Never resolve it yourself as part of `/morning` — `/morning` is read-only. Surface it; let Shane decide the fix (fast-forward, push, or — if genuinely uncommitted client work is found — whether to commit it, since a prior session may have deliberately deferred exactly that decision, as one did on 23 Aug for ~80 of these scripts).

**1m. Ready to Deploy — 🔴 MANDATORY, NEVER OMIT (added 2026-08-25, after a real miss):**

> **Shane, 2026-08-25, on finding out by accident:** *LUCY-bubgs (Adela's Chelmsford location page) was merged to main 24 Aug but never deployed — nothing surfaced this proactively.*

`/land-batch` already tracks exactly this: `~/.claude/state/land-batch/ledger.json` is the canonical
record of everything merged to main but not yet in a shipped/deployed prod revision (since the last
prod SHA), and `pending-qa.md` is its human-readable projection — both written by LAND's Step 3
harvest. The data already exists; it was just never surfaced outside a manual `/land-batch --status`
call. Run:

```bash
python3 ~/.claude/skills/land-batch/bin/land-state.py status 2>/dev/null | python3 -c "
import json, sys, datetime
d = json.load(sys.stdin)
ledger = d['ledger']
pending = ledger['pending']
now = datetime.datetime.now(datetime.timezone.utc)
print(f\"prod_sha={ledger.get('prod_sha')} last_successful_prod_at={ledger.get('last_successful_prod_at')} pending_count={len(pending)}\")
for f in pending:
    bead = f.get('bead_id') or f.get('bead_title') or 'unknown'
    branch = f.get('branch', 'unknown')
    landed = f.get('landed_at')
    days = '?'
    if landed:
        try:
            dt = datetime.datetime.fromisoformat(landed.replace('Z', '+00:00'))
            days = (now - dt).days
        except Exception:
            days = '?'
    print(f'{bead}\t{branch}\t{landed}\t{days}d')
"
```

- Report every row: bead ID, branch, landed date, days pending.
- 🔴 **If `pending_count` is 0, say so explicitly** — "Ledger clean — nothing pending deploy since prod
  SHA `<prod_sha>`" — never omit the section just because it's empty.
- Flag anything sitting **2+ days pending** — that's customer-visible value already merged and just
  not live; it should read the same as an alarm, not a footnote.

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
- 🔴 **Stale Manager claims:** [work-id + how long dead + handoff doc, or "none"] — a Manager-tab job (`fleet-role.py manager`) whose driving session died. Distinct from the headless fleet above: a dead claim doesn't show up as "blocked," the session is just gone, so this is the only thing that catches it. Reclaim per the command the report prints, or hand it to Shane to route via `/take`.
- **Watchers alive?** capture-watcher last run, bd-reap, night-batch — say when each last fired, and **say so loudly if any has not run in over 24h**

> **Why this lives here and not in a timer** (decided 2026-08-11, after Codex and Fable split on it): a launchd job would join `bd-reap`, `capture-watcher` and `ops-daemon` — all of which run unwatched, and not one has ever reported itself down. `capture-watcher` was dead for 30 hours with an empty error log and five unprocessed client captures piling up, and nothing said a word. `/morning` is the one recurring trigger attached to something Shane wants **for himself**, so it will not quietly stop being run the way bedtime did. The who-watches-the-watcher regress terminates at a human's own recurring want — that is the only place it can terminate. Blocked work rots over days, so there is no latency argument for a timer.

### Repo Health — 🔴 MANDATORY, NEVER OMIT
[From `repo-health-check.py` per 1l. If it fails to run, print literally `REPO HEALTH CHECK DID NOT RUN: <reason>` rather than omitting the section.]
- **AestheticcNext trunk:** [✅ clean, or the exact ahead/behind/uncommitted counts it printed]
- [If issues found: one line on what it means and what unblocks it — e.g. "375 behind = stale checkout, fast-forward it" or "12 uncommitted files = real work at risk, needs a commit decision"]

### Ready to Deploy — 🔴 MANDATORY, NEVER OMIT
[From `land-state.py status` per 1m. If the command fails, print literally `READY TO DEPLOY DID NOT RUN: <reason>` rather than omitting the section. If pending is empty, print "Ledger clean — nothing pending deploy since prod SHA `<prod_sha>`" rather than omitting the section.]
- **Pending since prod:** N feature(s), last successful prod at [last_successful_prod_at]

| Bead | Branch | Landed | Days pending |
|---|---|---|---|
| [bead_id] | [branch] | [landed_at date] | [N] |

- [If any row is 2+ days pending, lead with it: "N days pending" reads as merged-but-not-live customer value, not a footnote]

### 🔴 Live Work In Flight — DO NOT DUPLICATE
Read every `Sessions/closeouts/*--ACTIVE.md` (frontmatter `status: in-progress`). For each, check its `session_id` against `claude agents --json`:
- **Session still live** → list it as "OWNED by `<active_session>`, do not dispatch: `<the DO NOT DUPLICATE items>`". If you are the orchestrator, this is the authoritative list of what's already claimed — do not re-file or re-dispatch any of it.
- **Session dead** (not in `claude agents --json`, or heartbeat > ~3h stale) → the claim is abandoned. Surface its "If I've died" section as recoverable work.

An ACTIVE fragment is a live claim on a body of work; the whole point is that a fresh `/morning` knows what's being driven elsewhere before it acts.

### Last Session
[1-2 sentences from `LUCY_SESSION_STATE.md` — what was done, what was queued]
- **Un-rolled closeouts:** N fragments in `Sessions/closeouts/`, oldest X days, state file Y KB — from `ls` and `stat`, no rollup logic. **If N > 10 or the state file is over 60 KB, say "run `/rollup`".** `/rollup` fires on Shane's memory, which is the same trigger class that killed `/night`; this line is what makes forgetting it visible. **Never fold an `--ACTIVE` fragment into the state file while its session is alive.**

### 🔴 Active Engagements — who we're working FOR right now
[From `active-engagements.mjs` per 1k. Top 5–6 by score, never the full list. This section is MANDATORY — if the script fails, print `ACTIVE ENGAGEMENTS DID NOT RUN: <reason>` rather than omitting it.]

**Owed today or tomorrow:**
- **[Client]** — [the dated thing, and the single next physical act]

**Live engagements:**
| Client | Where they are | Overdue | Next act |
|---|---|---|---|
| [Name] | [onboarding step / building X / waiting on Y] | [N] | [specific act, not "follow up"] |

- **🔴 Agreed in chat, missing from both calendars:** [meeting + who + when, or "none"]
- **💷 New revenue asks:** [an existing client asking for something we don't sell yet — price it, don't let it rot in WhatsApp]
- **No INDEX.md:** [active clients with no `Clients/<Name>/INDEX.md` — we're working on them blind]

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

## Handoff Protocol — after Shane picks priorities (added 2026-08-25)

> **Shane, 2026-08-25:** *"I run morning, then I say 'what are our priorities' you say x y z.
> I say cool let's work on z and y. You should then say 'open up two terminal tabs with lucy
> in them' and once I confirm you communicate with them and send the context and directions.
> rather than your chat getting bogged down in dispatches and updates."*

Once Shane names which priorities to work now, this session's job is dispatch, not execution —
the standing orchestrator rule (CLAUDE.md "The orchestrator dispatches; it does not do the work
itself"), made concrete for the /morning → work handoff specifically:

1. **Count the work, ask for tabs, don't spawn them yourself.** Opening a terminal tab is a UI
   action only Shane can do — say plainly "open N Agent View tabs" (one per genuinely
   independent priority; don't over-split work that's really one thread). Wait for his
   confirmation before sending anything.
2. **Once tabs exist, `ListAgents` to find them, then `SendMessage` each one full context** —
   the specific priority, the relevant facts already gathered in this briefing (don't make the
   new session re-derive what `/morning` already found), and what "done" looks like. Don't
   summarize down to a one-liner — a session with thin context re-investigates from scratch,
   the exact waste this protocol exists to avoid.
3. **This session becomes a relay, not a worker.** Investigation, DB scripts, drafting, browser
   automation — all of that belongs in the dispatched tabs, visible to Shane in their own
   windows, not run inline here. Report their findings back when they land; don't do the
   digging in this chat.
4. **A background `/goal <bead>` dispatch (`fleet-dispatch.py`) is still the right tool** for a
   single, already-scoped, code-only bead with clear acceptance criteria — that doesn't need an
   interactive tab. This protocol is for open-ended work (a rescue plan, a client cleanup, an
   investigation with judgment calls along the way) that benefits from Shane watching it happen
   and being able to jump in.

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
- ~/.claude/state/land-batch/ledger.json (for Ready to Deploy — pending QA/deploy ledger since last prod SHA)

## Sprint Emphasis

During an **outreach/sales sprint** (check STATE_OF_THE_BUSINESS.md phase), the briefing leads with **Outreach** + **Priority Stack** + **Ops Health**. The **QA Health**, **Beads/git**, and code-dev sections drop to background — show QA only if an S1 is open (it's still a deploy gate) or a weekly audit is overdue; otherwise one line or skip. Don't let code-dev detail bury the sales picture.
