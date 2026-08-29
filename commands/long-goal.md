# /long-goal — Autonomous Epic Execution for Well-Scoped, Large Epics

Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly.

## Why this exists, vs `/goal`

`/goal` is right for single beads and small-to-medium epics. Two of its Phase 0e checks measure something real (`requires-shane-eyes`, "deploy to prod" in scope — both structural, not risk estimates) and stay exactly as-is below. The rest of `/goal`'s safety model is **avoidance-based**: refuse to touch Stripe/auth/payments/migrations at all, refuse the whole run if an epic has more than 10 children. That model assumes the founder is the fallback reviewer if avoidance fails — but the founder can't read code, so avoidance was mostly just removing autonomous help from the two places he needs it most, while the count cap blocked well-scoped work for a reason unrelated to risk.

His actual risk model, used everywhere else this session (Codex QA, blind multi-model panels, adversarial review), is **verification, not avoidance**. And the real backstop was never "don't let AI touch sensitive code" — it's structural and absolute regardless of what a child touches: **nothing merges to main, nothing deploys, ever, from this command.** Everything sits on an unmerged branch until Shane consciously merges it, which is exactly where `/review` + `/ship` + staging QA catch anything that slipped past autonomous review. `/long-goal` leans on that backstop and replaces avoidance with escalation: sensitive-path and migration work is allowed, but gets a second independent adversarial pass (Codex + GLM, blind to each other) instead of being refused outright. The **`> 10 open children` hard refusal** gets replaced entirely — it never measured risk, just count — with a **scoping-quality gate**: an epic is eligible if every child is *actually* well-specified (real acceptance criteria, real file references, real dependency notes), the same bar the founder+model-panel process already holds beads to when it does the job properly.

The other structural difference: `/goal` bundles succeed or fail as one unit — one QA pass at the end, one branch, done or `needs input:`. Over an 8-hour unattended run, **that's the wrong failure shape.** If child #4 of 15 trips something and can't get QA-clean, the founder wants to wake up to 14 finished children and one clearly-flagged exception — not a run that silently stopped at child #4 and did nothing else all night. `/long-goal` isolates faults per child (see Phase 4/6) so one bad apple doesn't cost the whole night, and collects everything worth a human glance — judgment calls, sensitive-path work — into one "Considerations for review" section rather than blocking on any of it mid-run.

Same Plan+Build (Opus) → adversarial QA partnership as `/goal`. Same "never merge, never deploy" rule, absolute, unchanged — that's the guarantee the whole redesign leans on. Same worktree-per-run, branch-per-run model. This is `/goal`'s engine with a safety gate that measures the right thing and a failure mode that survives the night.

## Usage

```
/long-goal AestheticcNext-257da     # epic — fans out across all open children, one worktree, one branch
```

Takes an epic ID only. For a single bead, use `/goal` — there's no throughput problem to solve there.

## Phases

### Phase 0 — Load and validate

#### 0a. Resolve BEADS_DIR by prefix

```bash
case "$BEAD_ID" in
  LUCY-*)
    export BEADS_DIR=/Users/shane/Documents/Obsidian/.beads
    REPO_ROOT=/Users/shane/Documents/Obsidian
    ;;
  AestheticcNext-*)
    unset BEADS_DIR  # let bd auto-discover from the code repo
    REPO_ROOT=/Users/shane/Documents/GitReBase/AestheticcNext
    ;;
  *)
    echo "failed: unknown bead prefix in $BEAD_ID" && exit 1
    ;;
esac
```

#### 0b. Load the epic

```bash
cd "$REPO_ROOT"
bd show "$BEAD_ID"
```

Refuse if the bead has no `CHILDREN` listed — this command is for epics. Point the founder at `/goal` for a singleton.

Refuse immediately if:
- Epic is closed
- Any in-scope child's status is in_progress and claimed by another active session
- Epic or any child's `updated_at` is >60 days old without a re-validation note. Post `bd comment <ID> "verified still relevant $(date +%F)"` first and re-evaluate (never `--notes` — see Phase 1).

#### 0c. Collect scope

Every open child of the epic. Closed children are ignored and not re-run.

#### 0d. Extract affected paths + dependency notes, per child

For each child, pull:
1. Affected paths — any `## Files` section, any path-shaped string in the body (`lib/foo/bar.ts` etc.), any label naming a specific surface.
2. **Dependency notes** — most children written by a real scoping pass state their dependency in prose ("Depends on X", "No dependencies", "Blocks Y"). Extract this per child; it drives the execution order in Phase 2. If a child states no dependency and none is inferable from shared files with another child, treat it as parallel-eligible within the sequential plan (still one commit at a time on one branch — "parallel-eligible" here means order-flexible, not concurrently executed).

Build `AFFECTED_PATHS` per child and a combined set for the epic.

#### 0d.1. Invariant injection — push, don't wait to be asked

🔴 **Added 2026-08-23, extended same day — see `/goal` Phase 0d.1 for the full rationale and
both sources: (A) `invariants/*.yaml` — why "book two treatments together" shipped and
blocked every multi-treatment booking, nothing surfaced `no_double_booking` before the data
model got designed; (B) `Product/Architecture/promise_inventory.jsonl` — 563 already-audited
promises, matched by `surface`/`trigger_path`/`persists_to`, injecting the WHOLE connected
cluster sharing a persist target or runtime consumer, not just the touched row.** Same
mechanism here, per child, using each child's own `AFFECTED_PATHS` from 0d — paste matches
into that child's Phase 2 planning context, require "Invariant interactions" and "Promise
interactions" fields in that child's PLAN.md (inherited automatically, Phase 2 below already
says child plans use the same fields as `/goal`), and if a child's plan adds a new
save/persist action, it must add its own `promise_inventory.jsonl` row before that child is
considered plan-complete (0d.1B in `/goal`). No matches (including missing files/dirs) is a
no-op for either source.

#### 0e. The scoping-quality gate (replaces `/goal`'s child-count cap)

An epic is eligible for `/long-goal` only if **every** open child has:
- A non-empty, concrete `ACCEPTANCE CRITERIA` field (not a placeholder, not "TBD") — this is what makes an 8-hour unattended run safe: Codex's QA pass in Phase 4 has something real to check the diff against.
- At least one concrete file/path reference in the description (from 0d) — an epic child with no named surface is a research task, not a build task, and doesn't belong in this mode.

If any child fails this check: **do not refuse the whole epic.** Exclude that child from scope, note why on the bead (`bd comment <id> "excluded from /long-goal run $(date -Iseconds): missing concrete acceptance criteria / file references — needs scoping before autonomous execution"` — never `--notes`), and continue with the remaining eligible children. Report the exclusion in Phase 7.

No hard cap on child count. If the eligible set is unusually large (>25), say so in the Phase 7 report as a `note:` — not a refusal — so the founder has visibility, but don't stop.

#### 0f. Sensitive-path and migration handling — verification-escalated, not excluded

**The founder's stated risk model is verification, not avoidance** — he can't personally review code, so the safety mechanism he actually relies on is adversarial review (Codex, blind second-model panels), not "don't let AI touch this." The real backstop that makes this safe to run fully unattended is structural and absolute regardless of what a child touches: **`/long-goal` never merges to main and never deploys, anywhere, ever.** Everything it produces sits on an unmerged branch until Shane consciously merges it — which is exactly where `/review` + `/ship` + staging QA catch anything that slipped past autonomous review, before it's anywhere near production. Given that containment already holds, avoidance-based exclusion for sensitive paths was doing less work than it looked like — it was mostly costing the founder the autonomous help he needs most, in the two places he's least able to provide it himself.

So: sensitive-path and migration work is **allowed**, not excluded — but it gets *more* verification, not less, as the trade:

- A child touching `lib/stripe/`, `lib/payments/`, `lib/auth/`, or `pages/api/auth/` → proceeds, but Phase 4 becomes **dual-model**: Codex QA (as normal) **plus** a second, independent adversarial pass via the `glm` wrapper (`glm review`, blind — it does not see Codex's verdict, and Codex does not see GLM's) against the same diff. Both must reach a clean/no-S1/S2 verdict before the child is considered QA-passed. Before treating anything as real, independently re-apply `qa.md`'s calibration gate to every S1/S2 candidate from either reviewer — don't just trust either model's severity label. A finding that fails calibration (no real entry point for a normal user or a never-dismissible category, or a trivial out-of-band bypass exists) is dismissed, not fixed — log `dismissed (not-the-police): <finding> — <one-line why>` in the child's notes and don't let it block the child. If a *calibrated* finding survives from either reviewer, treat it as real and fix before proceeding — don't average two calibrated verdicts down to "probably fine."
- A child touching `drizzle/migrations/` and adding tables/columns → proceeds. Same dual-model QA. Additionally, the plan (Phase 2) must state explicitly whether the migration is additive-only (new tables/nullable columns) or touches existing data (NOT NULL without backfill, column drops, type changes) — the latter gets flagged in the Phase 7 report under "Considerations for review" (see below) even if both reviewers pass it clean, because schema changes to live data are the one category where "the branch never deployed" isn't a full safety net if Shane merges without re-reading the migration himself.
- A child carrying a `requires-shane-eyes` label → still **excluded**, unchanged. That label is Shane explicitly telling a bead to wait for him — the one signal this mode should never override, because it's not a heuristic, it's a direct instruction.
- Bead body says "deploy to prod" / "production deploy" anywhere in scope → still **excluded** (mis-scoped for autonomous execution regardless of mode; this isn't about code risk, it's about a bead asking for an action `/long-goal` structurally cannot take).

**Per-run (not per-child) hard refusal — these still stop everything:**
- Total estimated diff across the *entire eligible* bundle > 8000 LOC (a generous ceiling for a genuinely large epic; if you're here, re-read the epic — something is probably mis-scoped as one epic that should be two).
- Fewer than 1 child survives 0e (nothing eligible to run).

**Warn but proceed** (same as `/goal`): P1 priority in scope, `client-reported` label for a named clinic, sensitive keyword in body with no sensitive path.

**Taste and clinical-judgment calls — resolved in planning, never a blocking gate.** Some beads carry a genuine judgment fork (a UX taste call, a clinical/compliance framing choice) rather than a technical risk. The founder can make these calls but would rather they weren't blocking, since he's not watching. Two rules:
1. If the fork is something the *scoping* pass should already have resolved (i.e., it's answerable from the bead's own acceptance criteria, the linked scope doc, or prior decisions in this epic's history), resolve it that way — don't re-litigate a settled decision mid-build.
2. If it's genuinely novel and unresolved, apply the Shane Decision Frame from `Reference/HEADLESS_MODE` (most robust long-term choice; don't overscope for imaginary scenarios; overscope only when cheap and clearly beneficial), make the call, and log it plainly in that child's PLAN.md under a `Judgment call:` line — one sentence on what was decided and why. Never block Build waiting for an answer nobody's there to give. Every `Judgment call:` line across the run gets collected into the Phase 7 report so Shane reviews them as a batch in the morning — a considerations list, not a gate.

#### 0g. Epic-level completeness check — does the eligible child set actually cover the epic's own ask?

🔴 **Added 2026-08-23, alongside Phase 4b.** Phase 4b (below) verifies, per child, that
work claiming to be user-facing actually produced a reachable frontend. That's necessary
but not sufficient: it can only judge a child that exists. If a "build X for users"-shaped
epic gets scoped into children that are all backend/API/schema and **no child ever touches
a frontend surface**, every one of those children can pass Phase 4b cleanly (each
legitimately *is* backend-only) while the epic as a whole ships with zero UI — exactly the
"reported done, frontend is literally zero" pattern this exists to stop. This check catches
that gap before Build starts, not after.

1. Read the epic's own title/description — the founder's original ask, not any child's.
   Apply the same UI-signal check as `/goal` Phase 4b's tightened backend-only rule: does it
   contain `UI`, `frontend`, `screen`, `page`, `dashboard`, `studio`, `portal`, `flow`, `for
   users`, `let clients/users`, `client-facing`, or similar signals of intended user-facing
   capability?
2. If yes: check whether **at least one** child surviving 0e/0f has an affected path (from
   0d) touching a frontend surface — `pages/`, `app/`, `components/`, `*.tsx`/`*.jsx`, or an
   explicit UI-surface label. Backend/API/schema paths alone don't count, no matter how many
   children there are.
3. If no eligible child touches a frontend surface despite the epic clearly asking for one:
   **do not silently proceed as if the scope is complete.** File a new child bead against
   the epic scoping the missing frontend work as concretely as you can from the epic's own
   description (apply the Shane Decision Frame — most robust long-term choice, don't
   overscope — the same way Phase 0f already resolves judgment forks). Run it back through
   0e:
   - If it clears 0e's bar (real acceptance criteria, real file references), it's now part
     of this run's scope — proceed with it included.
   - If it doesn't (the frontend need is real but can't be scoped precisely enough to build
     blind), it stays open and excluded, same as any other 0e exclusion. This is the
     important part: because it's a genuine open child of the epic, Phase 6's existing rule
     ("close the epic itself only if zero children remain open") now correctly keeps the
     epic open instead of letting it close as done with no frontend ever tracked.
4. Note the check's outcome on the epic (`bd comment "$EPIC_ID" "0g completeness
   check $(date -Iseconds): <passed clean | filed child <id> for missing frontend
   coverage>"` — never `--notes`) so this is visible in the epic's own history, not just
   buried in a run log.

This does not replace Phase 4b — it catches a scoping gap Phase 4b structurally cannot see
(no child means nothing for 4b to check), while 4b catches the case a frontend was promised
in one specific child's plan but not actually delivered.

### Phase 1 — Worktree + claim

1. `EnterWorktree` if not already in one.
2. Branch name: `long-goal/<epic-id-lowercase>`.
3. For each eligible child:
   ```bash
   bd update <ID> --claim --status=in_progress
   bd comment <ID> "started /long-goal $(date -Iseconds)"
   ```
   🔴 **Never `--notes` here (fixed 2026-08-23, same as `/goal`).** `--notes` REPLACES
   the field wholesale — a child bead's prior scoping notes or close reason would be lost
   the instant this ran. `bd comment` is append-only. See
   `reference_bd_update_notes_replaces_use_comment`.

### Phase 2 — Plan (Opus, in-context)

One `PLAN.md` per eligible child under `.plans/long-goal/<child-id>.md`, plus a `BUNDLE.md` at the worktree root that:
- Lists every eligible child and every excluded child (with exclusion reason, from 0e/0f).
- States the execution order, derived from the dependency notes extracted in 0d — topological, not "smallest first": a child whose description says "depends on X" runs after X's commit lands, full stop. Children with no stated dependency and no shared-file overlap with another pending child can run in any relative order; break ties by priority (P1 before P2) then smallest estimated diff first, to surface problems early.
- Notes the checkpoint convention (Phase 3.5): the epic's own notes field gets a one-line progress update after every child closes, so the founder can check status mid-run without waiting for Phase 7.

Each child PLAN contains the same fields as `/goal`: Goal, bead context, affected files, approach, risk, rollback, acceptance criteria.

**Plan review gate — MANDATORY, runs before Build.** Use `/goal` Phase 2b verbatim: it
holds the canonical classification signals, routing table, evidence capture, and verdict
handling. Do not duplicate or fork the routing table here — one source of truth.

Epic-specific: classify and route **per child plan**, not once for the whole epic. An
epic where one child touches payments and five touch copy should not put all six through
the payments gate, nor let the payments child ride the copy child's lighter review.

### Phase 3 — Build (Opus)

Implement child plans in `BUNDLE.md` order. **One commit per child**, referencing its bead ID. All commits on the same feature branch, same worktree.

After each child commits: `npx tsc --noEmit && npx next lint` (scoped to changed files where possible). Fix in the same commit or a fixup before moving to the next child — don't let type/lint debt accumulate across an 8-hour run.

If Husky pre-push tsc hangs cold: warm via `bun run typecheck` first.

**If a child turns out, mid-build, to touch a sensitive path or migration shape the Phase 0f estimate missed:** stop building that child, re-run the 0f check against what the plan actually revealed, and if it now trips, treat it as excluded (revert/discard that child's partial work, note why on the bead, leave it open) rather than pushing a sensitive change that was never budget-checked. Continue to the next child.

#### Phase 3.5 — Checkpoint (new — this is the "wake up to visible progress" mechanism)

After each child's commit + typecheck/lint pass:

```bash
bd comment "$EPIC_ID" "long-goal progress $(date -Iseconds): <N>/<TOTAL_ELIGIBLE> children committed. Last: <child-id> — <one-line summary>."
```

🔴 **Never `--notes` here (fixed 2026-08-23) — this call runs after EVERY child, so
`--notes` wasn't just risking one overwrite, it was silently erasing the previous
checkpoint on every single commit, defeating the entire point of a progress trail.**
`bd comment` is append-only, so the founder gets the full run history, not just the
latest line.

This is the state the founder needs to check mid-run (`bd show <epic-id>`, comments
section) without disturbing the session.

### Phase 4 — QA (adversarial) — per-child fault isolation, escalated for sensitive paths

Unlike `/goal`'s single end-of-bundle QA pass, run QA **after each child's commit**, against that child's diff plus everything already landed on the branch so far (`git diff origin/main...HEAD`) — catching cross-child interaction issues as they accumulate, not just at the very end. This costs more Codex calls than `/goal`'s bundle mode; that's the right trade for an unattended run, since a bad interaction caught at child 12 instead of child 4 is much more expensive to unwind.

**Standard children:** Codex only, same as `/goal` Phase 4 — identical `EVIDENCE_DIR` convention, same `codex exec --sandbox read-only` pattern (routed through `fleet-guarded.sh`, same as `/goal` — see its Phase 4 note), same stdin-piping, same PASS/NEEDS_CHANGES/BLOCK parsing, same "valid evidence is mandatory" rule.

**Sensitive-path children (Stripe/payments/auth) and migration children (per 0f): dual-model.** Run the standard Codex pass, and separately run `glm review` against the same diff (`~/.claude/scripts/fleet-guarded.sh glm "$EPIC_ID/$CHILD_ID phase4 dual-model" glm review "<one-line focus matching the bead's acceptance criteria>" --cd "$(pwd)" --base origin/main -o "$EVIDENCE_DIR/glm-verdict-round-N.txt"`) — give GLM the same qa.md calibration text, not just a one-line acceptance-criteria focus. 🔴 The `fleet-guarded.sh` wrapper claims/releases against the same 11-total fleet budget as Agent View dispatch — with two dual-model children potentially running Codex+GLM concurrently across several worktrees, this is exactly where uncounted load would otherwise pile up fastest. Neither reviewer sees the other's verdict before producing its own. Both artifacts go in `$EVIDENCE_DIR`. A child is QA-clean only when **both** are PASS with no unresolved *calibrated* S1/S2 (re-apply the calibration gate to each finding before counting it — see above). If they disagree after calibration, resolve toward whichever raised the surviving issue — do not average two verdicts into a pass, and do not skip calibration just because two models happened to agree.

**Per-child round cap: 3, same as `/goal`** (for dual-model children, 3 rounds applies to the pair together, not 3 each). If a child hits the cap still not clean:
- **Do not abort the run.** Revert or leave that child's commit isolated (don't build subsequent children on top of a known-broken one unless nothing else depends on it), file a follow-up bead (`--parent <epic-id>`, label `qa-followup`), note the state on the original child bead, leave it open.
- If a later child's plan genuinely depends on the failed child's output, skip that dependent child too (same treatment: note, leave open, follow-up bead referencing the blocking child) rather than building on unverified ground.
- Continue to the next independent child in `BUNDLE.md` order.

### Phase 4b — User-facing change note (MANDATORY for ANY user surface, per child)

🔴 **Added 2026-08-23 — this phase did not exist in `/long-goal` before.** `/goal` has had a
mandatory frontend-completeness gate since 2026-07-25 (see its Phase 4b: reachability diff,
hard FAIL conditions, the whole mechanism built specifically to stop "feature built, no
frontend, reported done"). `/long-goal` forked from `/goal` without carrying it over, so
every epic run since then — which is exactly where a "build me a marketing studio"-shaped
ask lands, being multi-surface by definition — shipped children with **zero mechanism
checking whether a frontend existed at all.** Found live, not theoretical: confirmed against
`PROMISE_DEBT_REGISTER.md`'s 258 live broken/undelivered promises, a meaningful share of
which are exactly this shape.

Use `/goal` Phase 4b verbatim for the check itself (same four questions, same reachability
diff, same hard FAIL conditions, same "backend-only requires evidence" rule above — one
source of truth, do not fork the checklist here). Epic-specific adaptation:

- Runs **per child**, after that child's QA passes (Phase 4), before its commit is
  considered final — same fault-isolation principle as Phase 4 itself. A child with an
  unresolved Phase 4b FAIL is treated exactly like a child that hit the QA round cap: leave
  it open, file a follow-up bead, do not build later children on top of it if they depend on
  it, continue with the rest.
- Write one `CHANGE-NOTE-<child-id>.md` per child at the worktree root (or "none — backend
  only" per the evidence rule above).
- At Phase 7, concatenate every child's note into the single `USER-FACING CHANGES` section —
  Shane reads one report per epic, not one per child.

Precondition for Phase 5 (below) is unchanged in spirit from `/goal`: no child ships with an
unresolved Phase 4b FAIL.

### Phase 5 — Ship to branch (NO deploy) — unchanged from `/goal`

`/ship` to push the feature branch once, after all eligible/surviving children are committed and QA-clean (not once per child). No merge to main. No deploy, ever, in this command.

### Phase 6 — Close beads (HARD GATE — same rule as `/goal`, applied per child)

Close every child that reached a clean QA state, individually, with its specific summary. Do not close children that were excluded (0e/0f) or that failed QA past the round cap (Phase 4) — those stay open with notes.

Close the epic itself only if **zero children remain open** (i.e., everything eligible either closed or was explicitly excluded with a follow-up bead filed). If any child is still open, the epic stays open too — that's the accurate state.

**Verify every close stuck**, same as `/goal`:

```bash
bd show "$ID" 2>&1 | grep -qiE "^Status:.*closed|\[CLOSED\]|· CLOSED" \
  || { echo "FAILED to close $ID"; }
```

Collect failures; report them in Phase 7 rather than silently proceeding.

### Phase 6.5 — Leave the worktree for `/land-batch` — unchanged from `/goal`

🔴 **Reversed 2026-08-26, same as `/goal`.** Do NOT remove the worktree. A worktree-less
("remote-only") branch is hard-quarantined by `/land-batch` discovery no matter how
complete its evidence — it forces a human `git merge`, which is exactly the workflow
Shane does not want. Leave the worktree intact once the branch is pushed and every
closeable child is verified closed; only note the `git status --porcelain` cleanliness
check in the report. See `/goal` Phase 6.5 for the full reasoning.

### Phase 6.6 — Release fleet slot + advance the queue — same as `/goal`

🔴 **Added 2026-08-23.** Identical to `/goal` Phase 6.6 — this epic run claimed one of 5
Agent View slots at dispatch time. Release it unconditionally (even on a partial-success
report with some children left open), then attempt to advance the queue:

```bash
python3 ~/.claude/scripts/fleet-slots.py release-agent-view <THIS_EPIC_ID>
NEXT=$(python3 ~/.claude/scripts/fleet-slots.py dequeue-next)
if [ "$NEXT" != "NONE" ]; then
  EPIC_FLAG=""
  bd show "$NEXT" 2>/dev/null | head -1 | grep -qi "\[EPIC\]" && EPIC_FLAG="--epic"
  python3 ~/.claude/scripts/fleet-dispatch.py "$NEXT" $EPIC_FLAG --pre-claimed
  RC=$?
  if [ $RC -ne 0 ]; then
    # fleet-dispatch.py already released the pre-claimed slot on any failure path now
    # (see the --pre-claimed contract) -- just re-enqueue, nothing to release here.
    python3 ~/.claude/scripts/fleet-slots.py enqueue "$NEXT" "$([ -n "$EPIC_FLAG" ] && echo long-goal || echo goal)"
  fi
fi
```

Note in the Phase 7 report if you advanced the queue (which bead, if any), or if an advance
was attempted but the gate refused it: `attempted <bead>, refused by gate (<reason>), re-queued`.

### Phase 7 — Report

```
result: epic <EPIC_ID> — <N> of <TOTAL> children closed, <M> excluded (scoping/budget, see notes), <K> QA-capped (follow-up beads filed). Branch long-goal/<id> pushed. Evidence: <artifact paths, one set per child>. Worktree left intact for /land-batch to discover and land.
```

If everything closed cleanly, `<M>` and `<K>` are 0 — say so plainly, don't pad the report.

**Considerations for review** (always include this section, even if empty — say "none this run" rather than omitting it):
- Every `dismissed (not-the-police):` line logged across the run — a finding either reviewer raised that failed the calibration gate and was correctly not fixed/filed. Shane spot-checks these, not the reviewers' theoretical attacks.
- Every `Judgment call:` line logged across all child PLAN.md files during Phase 2, verbatim with its child ID — taste/clinical-judgment forks resolved autonomously, for a quick after-the-fact look, not a gate.
- Every sensitive-path or migration child that went through dual-model QA (Phase 4), whether it passed clean or not, with both verdicts' locations — even a clean pass is worth a fast confirmation given what it touched.
- Any migration flagged in Phase 2 as touching existing data (not additive-only) — named explicitly, not buried in the diff.

For a run where nothing was eligible:

```
failed: epic <EPIC_ID> — 0 eligible children (all excluded at 0e, see notes). Epic untouched beyond exclusion notes.
```

## Cheat sheet — what's genuinely unchanged from `/goal`, and what changed

| Check | `/goal` | `/long-goal` |
|---|---|---|
| Stripe/payments diff > 300 LOC | Refuse whole run | **Allowed — escalates to dual-model QA (Codex + GLM), flagged in report** |
| Auth diff > 300 LOC | Refuse whole run | **Allowed — same dual-model escalation** |
| Migration > 3 tables / NOT NULL no backfill | Refuse whole run | **Allowed — dual-model QA, plus flagged in "Considerations for review" if non-additive** |
| `requires-shane-eyes` label | Refuse whole run | **Still excluded — the one signal this mode never overrides** |
| Epic > 10 open children | Refuse whole run | No cap — gated on scoping quality instead (0e) |
| Total bundle diff > 2000 LOC | Refuse whole run | Soft ceiling raised to 8000 LOC, still a hard stop past that |
| Missing acceptance criteria / file refs on a child | Not checked | New: exclude that child, continue (0e) |
| Taste / clinical judgment fork | Not addressed | **New: resolved via Shane Decision Frame in planning, logged as a `Judgment call:`, never blocks (0f)** |
| QA pass timing | Once, end of bundle | Per child, as it lands |
| QA reviewer(s) | Codex only | **Codex only for standard children; Codex + GLM (blind, both must pass) for sensitive-path/migration children** |
| One child un-QA-clean after 3 rounds | Whole bundle `needs input:` | That child (+ dependents) excluded; rest of run continues |
| Merge to main / deploy anywhere | Never | Never — unchanged, non-negotiable |
| Worktree / branch model | One worktree, one branch | Same |
| Progress visibility mid-run | None until Phase 7 | New: epic notes updated after every child (Phase 3.5) |
| Post-hoc review surface | None specific | **New: "Considerations for review" in Phase 7 — judgment calls + sensitive-path/migration work, for a fast morning look, not a gate** |
| Frontend completeness gate (Phase 4b, per child) | Mandatory since 2026-07-25 | 🔴 **Was MISSING entirely until 2026-08-23 — every prior epic run shipped children with zero check for "backend built, no reachable frontend." Now mandatory, per child.** |
| Epic-level scope completeness (0g) | N/A (single beads don't split scope) | 🔴 **New 2026-08-23 — catches the case Phase 4b can't: an epic that never scoped a frontend-building child at all, so every child passes 4b clean while the epic ships with zero UI.** |
| Invariant + promise injection (0d.1) | New 2026-08-23, same mechanism | Same — per-child, using each child's own `AFFECTED_PATHS` from 0d; promise clusters pull in every connected row, not just the one touched |

## What this does NOT do

- Does not merge to main. Ever.
- Does not deploy anywhere. No production, no staging, no worktree revision, no `--tag`, no `--no-traffic` revision.
- Does not fire `@deploy-staging`.
- Does not bypass `/review` or `/ship` gates.
- Does not avoid Stripe/auth/migration work — it verifies it harder (dual-model QA) instead of refusing to touch it, because the real safety backstop is "never merges, never deploys," not "never attempted."
- Does not touch a `requires-shane-eyes` bead regardless of anything else — that label is a direct instruction, not a heuristic.
- Does not block mid-run for taste or clinical judgment calls — resolves them in planning, logs them for after-the-fact review.
- Does not pick its own epic. Shane chooses what to fire this on.
- Does not run epics that haven't been through a real scoping pass — 0e enforces that as a per-child gate, not a vibe check.
