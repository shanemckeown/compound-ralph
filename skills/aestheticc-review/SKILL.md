---
name: aestheticc-review
description: Aestheticc's own plan and code review for AestheticcNext beads and branches. It replaces the generic /review and /plan-eng-review for AestheticcNext work. It loads the four review personas and the Aestheticc hazard list, which is built from real review failures. Use when asked to "review this branch", "review this bead", "plan this bead", "adversarial review", "Astra review", "is this ready to land", or "/aestheticc-review", and in /goal Phase 2 (plan) and after every push (code). Modes are plan and code.
argument-hint: "plan <bead-id> | code <bead-id> [branch|sha]"
---

# /aestheticc-review: Aestheticc's review for AestheticcNext

This is Aestheticc's own review. It checks a plan or branch against Aestheticc's personas and a hazard list built from real failed reviews. Generic reviews miss these hazards. The skill has two modes:

- **`plan <bead>`**: run before any code is written. /goal Phase 2 runs it, and so does whoever writes the bead.
- **`code <bead> [branch|sha]`**: run after every push, before landing.

Repo: `/Users/shane/Documents/GitReBase/AestheticcNext`. Run `bd` inside the repo.

## Load first (every run, both modes)

1. **The four personas.** Read them in full and cite rule IDs (TM-, COM-, DR-, UI-). Do not paraphrase them here.
   - `/Users/shane/Documents/Obsidian/Aestheticc/Ops/personas/tenancy-and-money.md`
   - `/Users/shane/Documents/Obsidian/Aestheticc/Ops/personas/comms.md`
   - `/Users/shane/Documents/Obsidian/Aestheticc/Ops/personas/data-and-reliability.md`
   - `/Users/shane/Documents/Obsidian/Aestheticc/Ops/personas/ui.md`
1b. **Clinic reality:** `/Users/shane/Documents/Obsidian/Aestheticc/Ops/personas/CLINIC_REALITY.md`. For every finding, name the real clinic scenario: who does it, on which device, at what moment in the clinic day. A finding with no real scenario is not P1/P2. Two devices editing one clinical record at once is not a clinic scenario; booking slots, payments, sends and crons are.
2. **Vault rules** in `/Users/shane/Documents/Obsidian/Aestheticc/CLAUDE.md`. Use these sections: the review line (P1/P2/P3), the plan contract, the settings contract, the model table and the code gates. Where CLAUDE.md and this skill disagree, CLAUDE.md wins.
3. **The bead in full:** `bd show <id>` and `bd comments <id>`. Every comment counts: Shane's decisions, earlier REFLECTIONs, earlier reviews. A Shane decision in a comment overrides the bead description.
4. **The hazard list below.** Answer every item that applies.

**Severity** (see CLAUDE.md for the exact wording):
- **P1:** money, patient data, tenancy or security. Blocks landing.
- **P2:** any user-facing broken promise, where a button or workflow does not do what it says. Also an **H-D0 duplicate path** (below), with no clinic-scenario filter. Blocks landing.
- **P3:** only for issues that are not user-facing. It may land unfixed if the builder rejects it in one written line.
- Do not grade a defect down because its patch is small.

---

## Mode: `plan`

Write the plan to `.goal/PLAN.md`, or to the bead with `bd comment`. Never use `--notes`: it replaces the existing text. The plan needs these seven fields, in this order:

1. **OUTCOME PROMISE.** Shane's words, in the form "pressing X does Y for Z". This is the acceptance criterion. It is verified by driving the app, not by reading code.
2. **PREMISE CHECK.** Show on prod that the problem is real before building anything.
   - Use read-only SELECTs only. The recipe is in the memory file `reference_prod_db_local_access_recipe.md`. Check which port the proxy is on, and introspect `information_schema` first.
   - Read naive timestamps with `::text` or `TZ=UTC`. node-pg on the laptop shows them 1h early in BST (`reference_prod_naive_timestamps_read_1h_early_in_bst.md`).
   - `treatments.updated_at` is not bumped by the PUT or by soft-delete, so do not use it to date a change. Use row `xmin` and the capture notes instead.
   - Record the query and its result. oa58j and 0mwa0 were both filed on premises that prod disproved.
   - If the premise is false, rescope or downgrade the bead before planning.
3. **EXISTING CAPABILITY.** Before designing anything, grep for every table, module, setting, predicate, util or send path that already does part of the outcome *for the same actor and data*. List each as `file:line` with one of:
   - `EXTEND <file:line>`: the default. "Extend X with one column or setting" is the expected answer.
   - `NEW PATH <file> — <why extending the existing path fails>`: a new path needs this written reason. A NEW PATH with no reason fails plan review.
   - `none — <why no existing path fits>` when nothing similar exists. A bare `none` fails.
   - Client-shaped work adds one line `Generic: <feature name>, first used by <client>`.
   - `python3 ~/.claude/scripts/goal-plan-fields.py <plan>` checks this field is there and filled in (/goal Phase 2b runs it). A reviewer who later finds an unrecorded second path raises H-D0. *(mbow6-r2: ~2,500 lines and 5 schema changes beside account credit, where account credit plus an expiry column was the design)*
4. **TOUCHPOINT MAP.** Grep every call site and every copy of the rule, predicate or writer being changed, and list them as `file:line`. Cover:
   - every write path: web, diary, v1 API, client appointments, portal, voice, concierge, waitlist claim, package auto-book, mobile
   - every read path, plus the readers of any field whose writer changes (COM-02, UI-05)
   - "fixed at two sites" is a finding waiting to happen. o2lrj needed 10 sites across 3 rounds.
5. **HAZARDS.** Quote each hazard ID below that applies, with a one-line answer ("H-M3: POS settle voids the link at pos/checkout.ts:NNN"). Name the ones that do not apply.
6. **SHANE QUESTIONS.** Put the outcome, UI, business and clinical questions in ONE batched list. Code questions never go to Shane. If one would, the plan is not finished; decide it with the Shane Decision Frame. Typical questions:
   - Does every booking get this, or is it a tick-box?
   - Should it auto-release or only flag?
   - Which channels should it use?
   - What is the lifetime or cutoff?
   - Does "switched off" include "hidden"?
   - Does the rule apply to new bookings only, or to reschedules too?
7. **ACCEPTANCE TESTS.** Name the runnable tests that prove the promise. For each one, state:
   - the real path it drives: the handler, the real SQL on pg, or the rendered email. A mocked writer does not count.
   - the mutation it must catch ("revert X → this test fails")
   - the Settings save/reload and clinic-isolation cell, if a clinic control is involved (settings contract, UI-06)
   - that the suite is not quarantined. Check `testPathIgnorePatterns` in `jest.config.js`, and see H-D4.

---

## Mode: `code`

Run after every push. You need the bead ID, the branch and the tip SHA. The diff base is `origin/main`, unless the bead names a stacked base.

**Step 1: the one independent seat (Shane, 26 Oct: one seat only, no second Claude read).** First run
`python3 ~/.claude/scripts/claude_only_guard.py route <bead>`. If line 1 is `claude` (label
`patient-data`, or anything in `~/.claude/scripts/claude-only-beads.json`; client business details
alone may go to Astra), skip codex: the seat is ONE fresh-context Opus Agent on the same brief,
not two (LUCY-b3hf5, LUCY-nd3qz). `~/.claude/bin/codex` refuses the call anyway (exit 3),
including from a detached scratch at the branch tip. Otherwise the seat is Astra, run in the
foreground, read-only, with stdin closed. The effort is computed, never chosen:
`python3 ~/.claude/scripts/review-effort.py <bead> <tip>` (run in the scratch at the tip) prints `high`
only when risk_tier.py's classifier puts the diff in its `light` tier (UI, styling or copy only) and no money /
tenancy / patient-data label applies; otherwise `xhigh`. Any
error, or any first word but `high`, is `xhigh`. Put its output line in the review file's VERIFIED OK:

```bash
cd <scratch worktree at tip> && EFFORT=$(python3 ~/.claude/scripts/review-effort.py <bead> <tip> | tee "$SCRATCH/effort-<bead>.txt" | cut -d' ' -f1)
[ "$EFFORT" = high ] || EFFORT=xhigh
codex exec -c model_reasoning_effort="$EFFORT" -s read-only \
  "$(cat /path/to/review-brief.md)" </dev/null > "$SCRATCH/astra-<bead>.txt" 2>&1
```

**Parallel reviews never share a brief path (22 Oct, ul5ts).** Two Astra review subagents wrote the same
scratchpad `brief.md`, and the first run reviewed the wrong branch. Each review gets its own
`$SCRATCH/<bead>-<tip7>/review-brief.md` and its own detached worktree. The brief's first line is
`REVIEWING: <branch> @ <full tip sha>`, and the reviewer's verdict must echo that line back. If the echo
differs from the intended branch/tip, the review is void: discard it and re-run.

The brief must include:
- bead + comments
- `git diff <base>...<tip>`
- the four persona paths
- the CLINIC_REALITY path
- the path to this skill's hazard list
- "answer every applicable hazard ID; trace real code paths; P1/P2/P3 with file:line + failure scenario"

If the 10-minute tool limit hits, resume the SAME session in the foreground:

```bash
codex exec resume <session-id> "continue; finish the verdict" </dev/null
```

Take the session ID from the output. Never start a fresh session. Never background it (`feedback_codex_exec_background_stdin_hang`).

**Tests reviewers run: targeted only (LUCY-z3ykz.3).** To check a claim, run `python3 ~/.claude/scripts/targeted-tests.py` (related suites, by exact path) or a single named test file. Never run the full pg lane (the whole `pg-tests` project) or the Cloud Build simulation (`scripts/ci/simulate-cloudbuild-test.sh`): the heavy set runs once per landing batch, chosen by risk tier, on the combined tree. A scratch pg test of one SQL path (Step 2) is a single file, not the lane. A reviewer who genuinely needs the full suite (a claim only the whole lane can settle) runs `python3 ~/.claude/scripts/builder-remote-qa.py` from the branch worktree instead (LUCY-ll8dh: Cloud Build, both land-gate lanes on the committed tip). `REMOTE RED @<sha>` + `FAILED LANE` is a real red; `FALLBACK local: <reason>` is infra and the local lane's `LOCAL GREEN/RED @<sha>` is the verdict; infra is never a finding against the branch.

**Step 2: depth on money, patient data or tenancy (no second reviewer).** On those beads the seat traces code paths from each entry point to the write. Astra is read-only, so the Manager running the review (or the builder, in /goal) runs the scratch pg test against the real schema where SQL decides the outcome, in a detached scratch worktree, and hands the command, output and exit code to the seat as evidence. The seat checks that evidence; it never claims to have run it.

**Step 3: non-vacuous check (Manager or builder runs it; the seat checks it).** For each new guard, revert it in a scratch worktree and confirm a test goes red. Also confirm that a control case goes green both ways.
- A test whose mock always throws proves nothing (0mwa0 round 3).
- A test that feeds a helper its inputs, and never drives the loop that produces them, proves nothing (c96ck round 4).
- Record which mutants survived, and pass the record to the seat as evidence.

**Step 4: landing check.** Run `git merge-tree $(git merge-base origin/main <tip>) origin/main <tip>` and also check the other pushed-not-landed branches. List the conflicts. VERSION, CHANGELOG, file-length-baseline and migration-number collisions are expected but must be named.

**Step 5: verdict.** Line 1 is `PASS` or `FAIL`. Then one finding per line, in the form `P1|P2|P3 file:line — failure scenario (who does what, what goes wrong) — fix`. After that, list VERIFIED OK and UNVERIFIED items; unverified items are never called verified. Notes under VERIFIED OK may discuss a listed finding, but never state a new one there. **End with the structured block. It is required:** `review-gate.sh` refuses any review written after 2026-10-23 12:00 UTC that lacks it (LUCY-p4pjf). The block is the single source of findings, and the checker refuses the file when it breaks any of these rules (LUCY-e3opf):

- **One response per file (G1, G4).** The file starts with one `REVIEWING: <branch> @ <sha>` line and holds exactly one top-level review-verdict block. Every file needs that echo, and only one: a second `REVIEWING:` line, even an identical one, refuses the file. Never save a review under a builder-evidence name (`mutants-round-N.txt`, `qa-*-round-N.log`): one with an echo or a block refuses the run (D1). Name its round once, `_r<N>_` or `round-<N>`, never both (D2). Never append a second response to an old file; write a new file. Put the block at column 0: one indented, inside a list item or quote, or inside HTML (`<!-- -->`, `<div>`, `<pre>`) refuses the file. A block quoted as an example inside a longer top-level fence (four backticks, or `~~~~`) is content and does not count, but the answer's own block must still be at top level: a file whose only block is inside another fence is refused. Every review of the tip is a seat, so a review with no block leaves earlier findings not fixed.
- **Exact keys (G2).** The top level is `verdict` and `findings`, plus `prior`. A finding is exactly `severity`, `file_line` and `text`. A prior entry is exactly `ref`, `status` and `evidence`. Use no other key (`priority`, `file`, `line` and `description` are refused), and no null or empty value.
- **The prose is checked by location only (G5).** Word the prose however you like. Any prose line naming a severity and a repo path (`P1 in tokens.ts`, `P2 lib/x.ts:12`) must match a block finding or prior ref with the same severity and file: the same path or its file name, and the same line when you give one. A severity named with no path is ignored, except on a line that starts like a finding (`- P1 …`, `### P1:`, `Finding (P1):`): the block must hold at least as many findings and prior refs of that severity, so write each finding's file on its own line. The whole file is checked, before and after the echo, and a `code span` is read like plain text, so a quoted example with a severity and a path is a claim too. A path is any token with a `/` or a file extension, so a severity on the same line as `and/or` or `user.id` is refused unless the block holds it; reword the line. Prose about a finding your `prior` marks `closed` must not raise it again: a new problem there goes under `findings`, and an earlier finding still failing is `open` in `prior`. Prose never grades or disputes anything: your block is authoritative over your prose.

- `findings`: each entry has `severity` (P1/P2/P3), `file_line` (`<file>:<line>`, a range such as `:78-83` allowed) and `text` (failure scenario — fix).
- `prior` (**required in every re-review**): one entry for **every** finding from earlier rounds you were given, `{"ref": "r<N> <sev> <file:line>", "status": "closed" | "open", "evidence": "<how you checked>"}`. `r<N>` is the round of the review file that raised it, from its name (`_r<N>_` or `round-<N>`; name every review file with its round, round 1 included: an unnamed file refuses the run), and `<sev> <file:line>` is copied exactly from that finding. Use `closed` only when you checked the fix at this tip yourself. Otherwise use `open`, and also raise it again under `findings` if it still fails. **`prior` is the only thing that lets the lead grade a finding `fixed` (LUCY-e3opf).** Prose `- rN ... closed:` or `open:` lines are never graded. **Every** seat of the newest round must carry an entry for every earlier finding: if one seat has no entry, the finding stays not fixed, and blocking (LUCY-e3opf B1). If one seat says closed and another says open, it escalates to the orchestrator. A ref must name exactly one round-N finding (G3): if two seats raised different findings at the same `<sev> <file:line>`, it escalates rather than grading both fixed, so say in `evidence` which one you checked. A ref that matches no round-N finding refuses the run. Give every finding you raise a `file_line`. An earlier finding raised with no line number, written exactly `<sev> <path> — …` with a bare path (no backticks, bold, link, anchor, `./` or `a/` prefix, or spaces, and no `:N`, `#L` or `(N)` after that path anywhere in its text), takes a path-only ref `r<N> <sev> <path>`, with the path copied byte for byte (LUCY-cwpxu). Any other finding, in any round, that names that path or its file name anywhere escalates it, and a finding in any other shape can't be closed by path at all. First-round reviews write `"prior": []`.

````
```review-verdict
{"verdict": "FAIL",
 "findings": [{"severity": "P2", "file_line": "lib/x.ts:12", "text": "failure scenario — fix"}],
 "prior": [{"ref": "r1 P1 lib/pay.ts:37", "status": "closed", "evidence": "regression test drives the retry path, green; reverting the guard makes it fail"},
           {"ref": "r1 P3 lib/y.ts:9", "status": "open", "evidence": "label still reads Saved when the write fails"}]}
```
````

Then record it:

```bash
bd comment <id> "<PASS|FAIL> @<full tip sha>
<path of the Ops/reviews file>
<ASTRA HIGH|ASTRA XHIGH|OPUS> REVIEW <YYYY-MM-DD> (tip <sha>): <PASS|FAIL> — <n> P1, <n> P2, <n> P3. <findings, copied verbatim>"
```

A FAIL comment in exactly that shape is the review's mirror, and `review-gate.sh` lets the review be gated after the lead's clearance. Any other FAIL, VOID or withdrawal comment on the tip vetoes it. A reviewer's raw `PASS @<tip>` line on its own never lands: `review-gate.sh` writes the landable pair.

**Before acting on a FAIL**, run the review lead on it: a fresh-context Opus agent following `~/.claude/references/review-lead/LEAD.md`, gated by `review_lead_check.py` (the same step as /goal Phase 4, Step 4). Only its `lead-actions` file counts toward a re-dispatch. A FAIL whose lead output is BLOCKING=0 and ESCALATE=0 is landable, and its dismissals file goes in the bead comment. The PASS itself is recorded only by `~/.claude/scripts/review-gate.sh` (it writes the `Ops/reviews` gate file and the `PASS @<tip>` first-line comment the lander requires; see /goal Phase 4 Step 4).

**On FAIL**, the orchestrator writes `REFLECTION <date>: attempt N (tip <sha>)` before any re-dispatch. It covers:
- **Preserve:** what was proven right
- **Change:** each P1/P2 fix
- **Why:** the failure scenario
- **Shane decisions:** any needed, asked as one batch

**Pre-existing issues found in review:** file each one as its own bead (`bd create`, add a `discovered-from` link). It does not block this branch unless the bead's own promise depends on it. vg5rs's cancelled-link P1 was "pre-existing", but the feature made cancel the release path, so it blocked.

**The builder's in-session review is not the independent review.** c96ck and oa58j both reported "Astra PASS, 0 findings" from inside /goal and then failed the independent review.

**Log it.** Record the first independent verdict for the bead in the metric log (see the end of this skill).

---

## Aestheticc hazard list

Each hazard has an ID, a checkable question, and the bead(s) where it was learned. The first persona rule named is where it lives.

### Money (TM-03, TM-04, TM-05)
- **H-M1 Every way to pay, closed together.** List every live payment instrument for the appointment: auto link, manual "Request deposit", shareable link, POS till settle, voice link. When one settles, are all the others voided and their open Stripe Checkout sessions expired? *(vg5rs: the POS settled in full while the deposit link stayed payable; "Request deposit" minted a second link)*
- **H-M2 Price change with open money.** If the service or price is edited or rescheduled while a link or session is open, is the amount capped at `calculateCardBookingPaymentAmount(new basket)` and the expiry recomputed? *(vg5rs: a £50 deposit stayed on a £30 service)*
- **H-M3 Cancelled means unpayable.** On cancel, no-show or release:
  - Are the pending AND expired link rows voided and their sessions expired?
  - Does `/checkout` refuse a cancelled appointment?
  - Does a late webhook avoid settling or reviving it, and flag `captureNeedsReview` instead?
  - Check the failed→succeeded promotion paths. *(vg5rs P1; jdfqy P3-2)*
- **H-M4 Retry and replay.** Does a decline followed by a retry in the same session end paid? Are success side effects (settle, loyalty, QuickBooks, receipts) gated by ONE compare-and-swap (CAS) winner, scoped by `id + businessId` with `.returning()`? *(jdfqy; y1qmk: the cron won the race and skipped the winner effects)*
- **H-M5 Zero and negative amounts.** A sale covered in full by a deposit, account credit or a 100% discount must not send a "£0.00 receipt" or create a £0 link. *(lmmle attempt 3)*
- **H-M6 Stripe session lifetime.** Is `expires_at` capped at the business cutoff, not Stripe's 24h default? *(vg5rs)*
- **H-M7 Stripe Customer on manual links.** Is it attached (guest-charge rule), or is it deliberately omitted and the reason recorded (2vt56 Link guard)? *(vg5rs: a reviewer disagreement that was resolved in the bead)*

### Tenancy and access (TM-01, UI-06)
- **H-T1 Settings endpoints are owner/admin only, server-side.** A settings PATCH that only denies third-party users lets any staff member flip clinic behaviour. *(vg5rs deposit-settings; compare cancellation-policy.ts)*
- **H-T2 A new clinic ability has a Settings switch that is OFF by default.** It must also save and reload, and stay isolated per clinic. Automatic charging defaults off. *(settings contract; vg5rs; lmmle P3(c) auto-receipts for every clinic with no switch)*
- **H-T3 Every new query is business-scoped in SQL.** A post-filter in code is not enough. This covers dedupe selects, name lookups and deletes. *(1o8a1, lmmle)*

### Patient data (TM-07)
- **H-P1 No PII in logs, Sentry or analytics.**
  - Drizzle's `error.message` carries the query params. Log IDs and the error class only, via `lib/utils/sentry-report.ts` `reportError`. *(1o8a1 P1)*
  - URL-borne tokens and emails must be kept out of PostHog/GA. Extend the bearer-URL guard in `lib/payments/saved-card-analytics.ts` and strip the query string. *(c01s5 P1)*
  - `lib/sentry/scrub.ts` misses a `%40`-encoded email. *(c01s5 P3)*
- **H-P2 Staff-internal text never reaches a client.** Check till notes, memos and internal descriptions against every client email, SMS and PDF slot. Use a constant for the slot, not a filter, and add a marker-string test on the rendered body. *(lmmle CRITICAL ×2)*
- **H-P3 Deletion covers new stores.** A new table or card holding a client name must be removed by GDPR hard delete, by the v1 OAuth client DELETE, and by `requestClientDeletion`. *(1o8a1)*

### Comms (COM-01, COM-05, COM-06, COM-07)
- **H-C1 Master pause and opt-out on every send.** Every automated SMS or email, including crons and reminders, calls `assertNotPausedForSend` and honours email and marketing opt-out. The pattern is `lib/payments/client-receipts.ts`. *(vg5rs P2)*
- **H-C2 Count SMS segments, not characters.** Use `lib/sms/segment-calculator.ts` on the FINAL text, including the gateway footer. A curly apostrophe or an emoji switches the message to UCS-2, where 1 segment becomes 3. *(vg5rs P1)*
- **H-C3 A send is marked done only after it succeeds.**
  - Is `sentAt` or the claim stamp set after a successful send?
  - Does the send path re-check eligibility at send time: paid, settled at the till, cancelled, cutoff passed?
  - *(vg5rs reminder)*
- **H-C4 Activation is separate from code.** Does the feature need Cloud Scheduler, a cron or a flag flip before the promise is live? Name the activation bead. *(vg5rs → ul0x1)*

### Booking (UI-04, UI-05, DR-05)
- **H-B1 One predicate, every copy.** Grep the rule's shape across `pages/api`, `lib`, voice, portal and mobile. Put it in one helper and add a source-text guard that bans the old shape. Check the mover as well as the blocker. *(o2lrj: 10 sites, 3 defects)*
- **H-B2 Behaviour matches main unless the change is intended.** For any refactor near booking or auto-book, prove the booking decisions are identical to main (a scripted diff or a loop-driving test), and change only what the bead promises. *(c96ck round 3: dropped a booking main would make)*
- **H-B3 A reschedule is not a new booking.** New-booking rules (switched off, hidden, notice) do not block moving an existing appointment unless Shane says they do. Pin this with a test. *(0mwa0 Shane decision)*
- **H-B4 Hidden is not switched off.**
  - Hidden (`active`, `public_bookable=false`) can be booked only via its direct link. The AI receptionist, concierge and waitlist must never offer it.
  - Inactive can never be booked online.
  - Check consult-first substitution and package pickers. *(0mwa0 ×3 rounds)*
- **H-B5 Existing data under a new rule.** An existing booking of a now-invalid thing must still display, edit and reschedule. Refuse only additions and switches. *(oa58j)*
- **H-B6 Numbering and counts stay within real totals.** A skipped or remaining session never exceeds the package total, not even on a fallback or error path. *(c96ck ×4)*

### UI (UI-01, UI-02, UI-07)
- **H-U1 Distinct states look distinct.** Unpaid, deposit paid and paid must not share one amber dot. Reception must be able to scan the diary. *(vg5rs P2-2)*
- **H-U2 A control shown must work.** Never show a button whose API then 404s. Carry flags through every picker path. *(0mwa0 P2-1; lmmle synthetic POS rows)*
- **H-P0 🔴 Privacy to third parties is DENY-BY-DEFAULT, never a blocklist (24 Sep: Sentry qqzwb and PostHog epik4 each failed review because a named list of "bad" paths/keys missed new ones).** For any data leaving to Sentry, PostHog, GA, pixels, logs or vendors: strip all query strings, redact every dynamic path segment, send no request bodies or console breadcrumbs, check property *names* as well as values, turn off optional capture features explicitly, and allowlist the fields that may go. A plan that adds "one more path to the list" fails this hazard.
- **H-U0 🔴 The screen must tell the truth (Shane, 23 Sep: "the code is correct but it doesn't fulfil the promise the front end makes").** For every label, button, toggle, help text, empty state, success toast, confirmation message, email or SMS copy, and Settings description the change touches or relies on: write down the promise it makes to the user in plain words, then trace the back end and prove it happens exactly — and only — that way (right trigger, right recipient, right amount, right timing, right clinic). Correct code that breaks a UI promise is a **P2**. In `plan` mode, list every UI promise in the outcome section; in `code` mode, verify each one by tracing (or driving the app), not by reading the component. *(lmmle "Email to client" 404; ve3bb Aftercare "Enabled" that never sent; vg5rs diary state; Refine Room "£30 redeemable" copy with no automatic credit)*
- **H-U3 A staff alert stays until it is acted on, and names the thing.** It is not dated "today" only, it is deduped on a stable key across replays and days, and it names the package or session. *(1o8a1)*

### Data, tests and evidence (DR-02, DR-06, DR-07)
- **H-D0 Duplicate path (P2, blocks landing; both modes, every review answers it).** Does the change add a table, column set, setting, module, predicate, util or send path that does a job an existing one already does for the same actor and data, without a refused-extend reason in the plan's EXISTING CAPABILITY field? Cite the existing path as `file:line`. Tiebreak: could this outcome have shipped by adding at most one column or setting to the thing that already does the job? Yes, and the plan gives no reason → P2. Label the finding `H-D0` and grade it P2 even if it looks small. The CLINIC_REALITY "no scenario, no finding" filter does not apply: it is structural, and the cost two months later is the scenario. `review_lead_check.py` accepts it without a clinic scenario and refuses to Note it. *(mbow6-r2 consult credit beside account credit; comms kernel beside 5 legacy send paths; 9 phone normalisers; Shane 15 Oct: "Yes, P2 blocks")*
- **H-D1 Tests are non-vacuous.** Revert the guard and the test must fail. A control must pass both ways. Mocks must not always throw. *(0mwa0, c96ck, lmmle)*
- **H-D2 Tests exercise the real path.** Drive the real handler or SQL on pg. Render the real email, mocking only the transport. A pg test covers reminder or selection SQL that a unit test fakes. *(vg5rs findDue, jdfqy, lmmle)*
- **H-D3 Fixtures do not encode the bug.** Use realistic values: a staff memo that reads like a memo, not a treatment name. *(lmmle)*
- **H-D4 Quarantined suites never run.** A new assertion in a suite listed in `jest.config.js` `testPathIgnorePatterns` proves nothing. Put an equivalent in an active suite. *(c96ck, oa58j, 0mwa0, lmmle)*
- **H-D5 Evidence is not inflated.**
  - A killed pg lane is "INCOMPLETE", not a pass. *(lmmle)*
  - Never relay "verified" when a sub-report says "not re-checked".
  - A builder's claim ("filed as pre-existing P3", "Astra PASS") is re-checked, not trusted.
- **H-D6 Naive timestamps.** Read them with `::text` or `TZ=UTC`, and cast writes with `::timestamp` (memory: `reference_naive_timestamp_writes_need_explicit_cast`). Appointment wall time is not the same as UTC `created_at` (DR-02).
- **H-D7 Migrations.** Renumber after main moves (vg5rs 0317→0318). Chain the snapshot, and add a column as a nullable ADD COLUMN or with a default. Mirror it into fixture DDL (DR-03).

---

## Adding a hazard (how this skill learns)

When a review finds a failure pattern that could recur, meaning one another bead could plausibly repeat:

1. Append it to the right persona group above. Give it the next ID (e.g. `H-M8`), a checkable question, and `*(bead-id: one-line what happened)*`.
2. If an existing hazard already covers it, add the bead ID to that hazard. Do not add a near-duplicate.
3. Keep this file under 250 lines. If it grows past that, merge hazards before adding more.
4. If a hazard fires on 3 or more beads, propose a structural check for it (a meta test or a lint rule) and file it as a bead.

Hazards live here, not in the personas. The personas are checked source documents.

## Metric: first independent review pass rate

After every FIRST independent review of a bead (Astra, or the Opus seat on a patient-data bead), append one line to `/Users/shane/Documents/Obsidian/Aestheticc/Ops/REVIEW_FIRST_PASS_LOG.md`:

```
| <YYYY-MM-DD> | <bead-id> | <PASS|FAIL> | <reviewer> | <tip sha> | <top hazard IDs hit> |
```

Do not log re-reviews. Commit the file by name in `/Users/shane/Documents/Obsidian`, never with `-A`.
