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
- Epic or any child's `updated_at` is >60 days old without a re-validation note. Post `bd update --notes "verified still relevant $(date +%F)"` first and re-evaluate.

#### 0c. Collect scope

Every open child of the epic. Closed children are ignored and not re-run.

#### 0d. Extract affected paths + dependency notes, per child

For each child, pull:
1. Affected paths — any `## Files` section, any path-shaped string in the body (`lib/foo/bar.ts` etc.), any label naming a specific surface.
2. **Dependency notes** — most children written by a real scoping pass state their dependency in prose ("Depends on X", "No dependencies", "Blocks Y"). Extract this per child; it drives the execution order in Phase 2. If a child states no dependency and none is inferable from shared files with another child, treat it as parallel-eligible within the sequential plan (still one commit at a time on one branch — "parallel-eligible" here means order-flexible, not concurrently executed).

Build `AFFECTED_PATHS` per child and a combined set for the epic.

#### 0e. The scoping-quality gate (replaces `/goal`'s child-count cap)

An epic is eligible for `/long-goal` only if **every** open child has:
- A non-empty, concrete `ACCEPTANCE CRITERIA` field (not a placeholder, not "TBD") — this is what makes an 8-hour unattended run safe: Codex's QA pass in Phase 4 has something real to check the diff against.
- At least one concrete file/path reference in the description (from 0d) — an epic child with no named surface is a research task, not a build task, and doesn't belong in this mode.

If any child fails this check: **do not refuse the whole epic.** Exclude that child from scope, note why on the bead (`bd update <id> --notes "excluded from /long-goal run $(date -Iseconds): missing concrete acceptance criteria / file references — needs scoping before autonomous execution"`), and continue with the remaining eligible children. Report the exclusion in Phase 7.

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

### Phase 1 — Worktree + claim

1. `EnterWorktree` if not already in one.
2. Branch name: `long-goal/<epic-id-lowercase>`.
3. For each eligible child: `bd update <ID> --claim --status=in_progress --notes "started /long-goal $(date -Iseconds)"`.

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
bd update "$EPIC_ID" --notes "long-goal progress $(date -Iseconds): <N>/<TOTAL_ELIGIBLE> children committed. Last: <child-id> — <one-line summary>."
```

This is the only piece of state the founder needs to check mid-run (`bd show <epic-id>`) without disturbing the session.

### Phase 4 — QA (adversarial) — per-child fault isolation, escalated for sensitive paths

Unlike `/goal`'s single end-of-bundle QA pass, run QA **after each child's commit**, against that child's diff plus everything already landed on the branch so far (`git diff origin/main...HEAD`) — catching cross-child interaction issues as they accumulate, not just at the very end. This costs more Codex calls than `/goal`'s bundle mode; that's the right trade for an unattended run, since a bad interaction caught at child 12 instead of child 4 is much more expensive to unwind.

**Standard children:** Codex only, same as `/goal` Phase 4 — identical `EVIDENCE_DIR` convention, same `codex exec --sandbox read-only` pattern, same stdin-piping, same PASS/NEEDS_CHANGES/BLOCK parsing, same "valid evidence is mandatory" rule.

**Sensitive-path children (Stripe/payments/auth) and migration children (per 0f): dual-model.** Run the standard Codex pass, and separately run `glm review` against the same diff (`glm review "<one-line focus matching the bead's acceptance criteria>" --cd "$(pwd)" --base origin/main -o "$EVIDENCE_DIR/glm-verdict-round-N.txt"`) — give GLM the same qa.md calibration text, not just a one-line acceptance-criteria focus. Neither reviewer sees the other's verdict before producing its own. Both artifacts go in `$EVIDENCE_DIR`. A child is QA-clean only when **both** are PASS with no unresolved *calibrated* S1/S2 (re-apply the calibration gate to each finding before counting it — see above). If they disagree after calibration, resolve toward whichever raised the surviving issue — do not average two verdicts into a pass, and do not skip calibration just because two models happened to agree.

**Per-child round cap: 3, same as `/goal`** (for dual-model children, 3 rounds applies to the pair together, not 3 each). If a child hits the cap still not clean:
- **Do not abort the run.** Revert or leave that child's commit isolated (don't build subsequent children on top of a known-broken one unless nothing else depends on it), file a follow-up bead (`--parent <epic-id>`, label `qa-followup`), note the state on the original child bead, leave it open.
- If a later child's plan genuinely depends on the failed child's output, skip that dependent child too (same treatment: note, leave open, follow-up bead referencing the blocking child) rather than building on unverified ground.
- Continue to the next independent child in `BUNDLE.md` order.

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

### Phase 6.5 — Reclaim the worktree — unchanged from `/goal`

Same preconditions, same `ExitWorktree`/`git worktree remove` steps, only after the branch is pushed and every closeable child is verified closed.

### Phase 7 — Report

```
result: epic <EPIC_ID> — <N> of <TOTAL> children closed, <M> excluded (scoping/budget, see notes), <K> QA-capped (follow-up beads filed). Branch long-goal/<id> pushed. Evidence: <artifact paths, one set per child>. Ready for Shane to merge, then staging QA.
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
