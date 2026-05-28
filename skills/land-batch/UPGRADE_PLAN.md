# /land-batch v0.2 — Upgrade Plan

Goal: turn `/land-batch` from a git-state batch-lander into a **chat-aware autonomous
loop** that reads each worktree's Claude session to decide what's finished, lands the
finished set, deploys once to staging, QAs all features on that deploy, auto-fixes
p0–p2 via codex until clean, then promotes to prod **behind one human gate**, and
renames landed worktrees to `Close-N` so Shane can reap them in Agent View.

Decisions locked with Shane (2026-05-28):
- **Prod:** deploy to prod, but only behind a final explicit confirm gate.
- **QA failures:** codex-fix loop (document → fix p0–p2 → redeploy staging → re-QA), not revert-on-fail.
- **Finish judgment:** fully autonomous — meta-claude decides the finished set from chat tails + git state, no confirm.
- **Process:** this plan → /plan-eng-review → build.

---

## What already exists and stays (the hard part is done)

`bin/discover.sh` is the git-reconciliation engine and is solid — keep it as the base:
- cross-roots Agent View (`.claude/worktrees/`), Conductor (`conductor/workspaces/`), `~/.worktrees`
- `merge-tree --write-tree` conflict detection (no working-tree mutation) vs base **and** sibling-vs-sibling
- ahead/behind, clean, age, sensitive-path detection (Stripe/auth/migrations/webhooks/email)
- bead-id + PR join, retired-list, recommendation classifier

SKILL.md Steps 3–6 (sequential `--no-ff` merge vs evolving main, green gate, one staging
deploy with **two-gate traffic verification**, consolidated QA) also stay.

---

## New building block #1 — session/chat state layer

**Discovery: `~/.claude/sessions/<pid>.json` is exactly what Agent View reads.** Fields:
`pid, sessionId, cwd, name, status, updatedAt, kind`. `status ∈ {busy, waiting, idle}`.
This is the join we were missing.

New script `bin/sessions.py` (invoked from `discover.sh`, merged into the JSON contract):

1. Scan `~/.claude/sessions/*.json` → map `realpath(cwd)` → session record. Compute
   `idle_min = (now - updatedAt)/60`.
2. Locate the transcript for each session:
   - Primary: `~/.claude/projects/<encode(cwd)>/<sessionId>.jsonl`
     where `encode` = replace every `/` and `.` with `-`
     (verified: `/Users/shane/conductor/workspaces/AestheticcNext/manila` →
     `-Users-shane-conductor-workspaces-AestheticcNext-manila`;
     `…/.claude/worktrees/goal-cjf4p` → `…--claude-worktrees-goal-cjf4p`).
   - Fallback 1: glob `~/.claude/projects/<encode(cwd)>/**/<sessionId>.jsonl`
     (some sessions nest the jsonl under a `<uuid>/` subdir).
   - Fallback 2: newest `*.jsonl` under the encoded dir.
   - Skip `subagents/*.jsonl` — those are sidechains, not the main turn stream.
3. Parse the transcript tail → emit:
   - `last_assistant_tail`: last ≤3 assistant **text** blocks (concatenate `type==text`,
     drop pure `tool_use`), truncated to ~600 chars each.
   - `last_user`: last user prompt text (helps detect "user just asked something").
   - `sentinel`: first of `result:` / `needs input:` / `failed:` found in the last
     assistant block (bg jobs emit these as explicit completion signals).
   - `transcript_mtime`.

**Merge into the candidate contract.** Each `candidates[]` entry gains a `session` object
(or `null` if no live session maps to that worktree — session exited; then try the
on-disk transcript by encoded path anyway for the tail). Add top-level `active_busy[]`
= sessions with `status==busy` for awareness.

---

## New building block #2 — autonomous finish-judgment (replaces the Step-2 confirm gate)

Fully auto per Shane. Meta-claude (in SKILL.md prose, reading the enriched JSON) selects
the **finished-implementing** set:

EXCLUDE a candidate when ANY of:
- `session.status == busy` (active right now), or `session.idle_min < 3` (race guard —
  just went idle, transcript may still be flushing)
- git: `ahead == 0` (nothing to land), `conflicts_with_base`, not `clean`, or `retired`
- chat tail shows an **open loop**: contains a question to the user / `needs input:` /
  `failed:` / "shall I" / "which would you" / "let me know" / unanswered AskUserQuestion

INCLUDE when ALL of:
- not excluded above
- git: `ahead ≥ 1`, `clean`, no base conflict, not retired
- chat tail reads as **done implementing**: `result:` sentinel, or completion language
  ("done", "landed", "PR created", "✅", "implementation complete") with no trailing open
  question. Meta-claude makes the judgment call from the tail — this is the "read the last
  few messages to confirm state" feature.

**Sensitive-path exception (kept, even in auto mode):** candidates touching
`lib/stripe|auth|payments|db / drizzle/migrations / pages/api/auth|admin|webhooks /
lib/email/templates` are NOT auto-included. They're listed and **held for explicit
opt-in** — this is a separate memory-backed safety rule (multi-tenancy/Stripe/auth), not
the finish gate, so "fully auto" doesn't override it. (Open question for eng review: is
this acceptable, or does Shane want sensitive auto-landed too?)

Print a scannable summary table of the decision (finished / excluded-why / held-sensitive)
for the record, then proceed without a confirm.

---

## New building block #3 — codex-fix loop (replaces revert-on-fail in Step 7)

After consolidated QA on the single staging deploy:

1. Classify each finding p0–p3:
   - p0 = data loss / security / auth / payment / total outage of a core flow
   - p1 = a landed feature's core path is broken
   - p2 = notable bug, wrong behavior, but feature partially works
   - p3 = cosmetic / copy / minor polish
2. Document all findings (per feature, with evidence: screenshot + repro), keyed to the
   merge-commit SHA so any feature stays individually revertable.
3. If any **p0–p2** remain:
   - Dispatch the **codex** skill to fix them on `main` (the merged result), scoped to the
     offending feature's files. Commit each fix atomically (`fix(land-batch-qa): …`),
     attributed to its feature.
   - Redeploy staging (same two-gate traffic verification).
   - Re-run QA for the affected features.
   - Loop. **Hard cap: 3 iterations.** If p0–p2 still remain after 3, STOP — do not
     promote to prod. Surface the residual list + offer per-feature revert.
4. p3 cosmetic: documented only, never blocks prod.

Safety: codex fixes are commits on main; each is revertable. Never let codex touch
sensitive paths without surfacing. Run codex directly (not narrated by a sub-agent) per
the deploy-agents-lie memory.

---

## New building block #4 — prod promotion (behind ONE gate)

When staging QA is clean (zero p0–p2):
1. Print the final report (landed features, fixes applied, staging revision serving 100%).
2. **AskUserQuestion: "Deploy this batch to PROD?"** — the only remaining interactive gate.
3. On yes: run the prod cloudbuild + the same two non-negotiable verifications
   (build SUCCESS for *this* build in the correct region; `traffic[0].revisionName ==
   latestCreatedRevisionName` on the prod service). SUCCESS ≠ live.
4. On no / no-response: stop at staging, leave prod untouched, report.

Guardrail change: hard-guardrail #3 goes from "Staging only. Never prod." →
"Prod only behind the final explicit confirm gate; never headless." Interactive-only
refuse (SPAWNED_SESSION / OPENCLAW / `claude -p`) **stays** — the loop still won't run
headless. Direct verified gcloud deploy (no deploy sub-agent) **stays**.

---

## New building block #5 — rename worktrees to Close-N

After a worktree's feature is landed + passed (and prod gate handled), rename it so Shane
can find and close it in Agent View:
- Rewrite the `name` field in `~/.claude/sessions/<pid>.json` → `Close-1`, `Close-2`, …
  (ordered by landing order). Only for landed+passed worktrees with a live session.
- **Never** auto-close/kill the session — Shane closes them himself in Agent View. We only
  relabel.
- **Never** rename a `busy` session.

⚠️ **Verification spike required before trusting this:** I can read `sessions/*.json` but
have NOT confirmed Agent View re-reads `name` after a hand-edit (it may cache in-memory or
own the file and clobber the edit). Plan: edit one name, confirm Agent View reflects it
live. **Fallback if it doesn't:** emit the `Close-N → worktree/branch` mapping in the final
report so Shane closes them manually. Build the report fallback regardless.

---

## Cross-cutting fixes
- **/tmp clobber:** Step 1 writes discovery to a fixed `/tmp/land-batch-discover.json`;
  parallel bg jobs share `/tmp`. Switch to `${CLAUDE_JOB_DIR:-$(mktemp -d)}/discover.json`.
- **Memory reconciliation (on build, not silently):** update/annotate
  `feedback_no_self_deploy_staging`, `feedback_never_use_deploy_agents`,
  `feedback_deploy_agents_lie` to record the new gated-prod policy. The spirit (no headless
  self-deploy, no trusting deploy agents, always verify traffic) is preserved — only the
  "this skill never touches prod" clause changes to "prod behind explicit gate."

---

## Build order (after eng review)
1. `bin/sessions.py` + merge into `discover.sh` contract (session join + transcript tail). Unit-test the encode + tail parse against live `sessions/*.json`.
2. Rewrite SKILL.md Steps 1–2: enriched discovery + autonomous finish-judgment (drop the confirm gate, keep the printed table).
3. Codex-fix loop (Step 7 rewrite).
4. Prod gate (new Step 8) + guardrail edits.
5. Rename-to-Close-N + the verification spike + report fallback.
6. Memory reconciliation.

## Open questions for eng review
- Sensitive-path branches in fully-auto mode: hold-for-opt-in (planned) vs auto-land?
- Codex fixing on `main` vs in the feature worktree then re-merge — which is safer/cleaner?
- Race guard threshold (idle_min < 3) — right value? Should we also check the pid is alive?
- Rename: is editing `sessions/*.json` `name` the supported path, or is there a CC API/command for it?
