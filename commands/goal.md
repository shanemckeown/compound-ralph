# /goal — Autonomous Bead Execution via Plan-Build-QA Partnership

Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly.

Take a bead ID (single or epic), run the full Lucy+Codex partnership flow end-to-end: plan, build, adversarial QA, iterate until clean, push the feature branch, mark the bead(s) `pushed` (AestheticcNext- code beads) or close them (LUCY- vault/tooling beads — see Phase 6). **Never merges to main. Never deploys anywhere — not staging, not production.** QA happens on the main staging service AFTER `/land-batch` lands the branch to main and the normal staging deploy runs. Production deploys require Shane's explicit sayso in a foreground chat.

🔴 **B-1 (AI Fleet Grand Plan, 2026-09-01): Workers do not close AestheticcNext- code beads — ever, for any reason, including "verified myself" or "QA clean."** A closed bead is a claim; only a landed one (verified by `/land-batch` against `origin/main`) is a fact — see `Aestheticc/Product/AI_FLEET_GRAND_PLAN_2026-09-01.md` Part 1/Part 3 Q1 for why (`AestheticcNext-n708r`: seven review rounds, bead closed, prod unfixed in 2 of 3 files, five weeks). A `bd-close-guard` wrapper (`~/.claude/bin/bd`, wired via `~/.zshrc` — reaches every session started after 2026-09-01, not sessions already running before then) refuses `bd close`/`update --status closed` on an AestheticcNext- bead with no commit reachable from `origin/main`, but treat this instruction as the primary mechanism, not the guard — the guard is a backstop, not a substitute for typing the right command.

🔴 **NO PER-WORKTREE STAGING/QA DEPLOYS.** /goal must NOT run `gcloud builds submit` for any staging build, must NOT create a `--no-traffic` worktree revision, and must NOT add a Cloud Run `--tag`. Those spin up billable always-on revisions — the exact cost leak that nearly sank the runway (see `feedback_cloud_run_warm_instance_cost_model.md`). The only deploy surface is the main staging service, reached via `/land-batch`, then the normal staging deploy. /goal stops at "branch pushed + bead closed + worktree left intact for discovery."

## 🔴 HARD RULE: No `result:` until every in-scope bead is verified PUSHED (or CLOSED for LUCY-)

The recurring failure mode of /goal runs is: work ships, branch pushes, but the bead stays OPEN because Phase 6 got skipped or the status update silently failed. Future Shane then can't tell what's done. A second, more dangerous failure mode (B-1, `AestheticcNext-n708r`): the bead gets CLOSED, and everyone downstream — Shane, a Manager, a future session — trusts that word instead of checking `main`. `pushed` is the honest word for "branch is done and QA-clean, ancestry not yet verified"; `closed` means `/land-batch` has already confirmed it's an ancestor of `origin/main`. Workers don't get to skip that distinction by picking whichever status is more convenient.

You are NOT allowed to write the `result:` line in Phase 7 until you have run `bd show <ID>` for every bead in scope (singleton OR every child of an epic) and verified the output shows `pushed`/`PUSHED` (AestheticcNext-) or `closed`/`CLOSED` (LUCY-). If the verification fails for any bead, write `needs input:` and list the beads that wouldn't update. See Phase 6 for the exact verification command.

Observed 2026-05-13 on `AestheticcNext-sdait` — fix shipped via Plan-Build-QA but bead stayed open. This rule is the fix.

## Usage

```
/goal LUCY-1234                     # single bead
/goal AestheticcNext-az50           # single bead in code repo
/goal AestheticcNext-kh9cy          # epic — fans out across all open children, one branch
/goal LUCY-1234 --strict            # reserved for future strict-mode (not yet implemented)
```

## Why this exists, vs other patterns

`/do-bead` is retired (Ralph one-prompt loop, single model). Use `/goal` for everything from single beads to epic bundles.

Plan + Build with Claude Opus, QA with OpenAI Codex 5.5 (adversarial, model-independent). The partnership loop catches what one-model loops miss.

Fired from a fresh /agent chat. The /agent view is the supervisor. Shane keeps 3-5 running, fires another when one drops out.

## Phases

### Phase 0 — Load and validate

#### 0a. Resolve BEADS_DIR by prefix

Per-prefix routing fixes the vault-vs-code confusion:

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

#### 0b. Load the bead

```bash
cd "$REPO_ROOT"
bd show "$BEAD_ID"
```

Refuse immediately if:
- Bead is closed
- Status is in_progress and claimed by another active session
- `updated_at` is >60 days old without a re-validation note. Post `bd comment <ID> "verified still relevant $(date +%F)"` first and re-evaluate (never `--notes` — it replaces the field wholesale, see Phase 1).

#### 0c. Identify scope: single bead or epic-bundle

If the bead's `bd show` output lists `CHILDREN`, this is an epic. Collect every open child. Closed children are ignored.

If no children, treat as a single bead. The rest of the validation runs on either one bead (singleton) or the full child set (bundle).

#### 0d. Extract affected paths

For each bead in scope (singleton or every child), pull affected paths from:
1. Any `## Files` section in the description
2. Any path-shaped string in the body (`lib/foo/bar.ts`, `components/baz/qux.tsx` etc.)
3. Labels mentioning specific surfaces (`label=auth` etc.)

Build a set: `AFFECTED_PATHS`.

#### 0d.1. Invariant injection — push, don't wait to be asked

🔴 **Added 2026-08-23.** A graph or doc the agent has to decide to go read fails exactly
when it matters most, because the agent doesn't know it needs to look — confirmed live:
a "book two treatments in one visit" feature shipped and blocked *every* multi-treatment
booking, because nothing surfaced the existing `no_double_booking` constraint to the agent
designing the new data model, before it decided how to model it. Checking after the fact
can't fix a design-time miss like that; the fact has to arrive unasked, before Build.

Two independent sources feed this, matched separately (they answer different questions —
one is "does this violate a rule," the other is "does this actually deliver what it claims"):

**A. `invariants/*.yaml`** (see `AestheticcNext-1luoq` — starts as one pilot file,
`scheduling.no_double_booking`, grows from there; missing dir or no match = no-op):

1. Grep each invariant file's `enforced_at`/`object`/table/path fields against
   `AFFECTED_PATHS`.
2. For every match, paste that invariant file's **full content** — statement, enforced_at,
   relaxed_by, never_relaxed_for, tests — directly into the context Phase 2 plans from. Not
   a pointer, not a filename — the actual text, so there's nothing left to decide to go read.

**B. `Product/Architecture/promise_inventory.jsonl`** (563 rows, built by the `zuqrk`
promise-audit epic — already exists, do not rebuild it):

1. Match each row's `surface`/`trigger_path`/`persists_to` fields against `AFFECTED_PATHS`.
2. 🔴 **On any match, don't inject just that one row — pull every other row that shares the
   same `persists_to` table/column or any `runtime_consumers` entry, and inject the whole
   connected cluster.** This is the literal mechanism for "touching one promise should light
   up every promise connected to it" (Shane, 2026-08-23) — a save action and the code that's
   supposed to read it back out are two different rows in this file, and both need to be in
   front of the agent, not just whichever one its diff happens to touch.
3. If a matched row's `status` is `BROKEN` or `SUSPICIOUS`, say so explicitly in what gets
   injected — a plan that's about to touch code already known to break a promise needs that
   fact in its face, not buried in a severity field it wasn't told to check.

Phase 2's PLAN.md must then include **"Invariant interactions"** and **"Promise
interactions"** fields (see Phase 2 below) explicitly addressing every injected item from
either source: does this change respect it unmodified, deliberately interact with it via a
stated mechanism, or genuinely not apply and why. A plan that touches a matched
invariant/promise without the corresponding field populated fails Phase 0f's re-check below.

**Keeping B honest — updating is part of the work, not a separate sweep.** If this bead's
plan adds or changes a user-facing save/persist action (a new setting, a new "Save X"
control, a new template field) that isn't already a row in `promise_inventory.jsonl`, Phase
2's plan must add one — `surface`, `promise` (plain English, what a clinic owner would think
it does), `trigger_path`, `persists_to`, `runtime_consumers` (the actual file(s) that read it
back out — if the plan can't name one, that's the ghost-setting bug being built in real
time; stop and fix the design before Build, don't ship it and let a future audit catch it),
`status: VERIFIED`. Phase 0f's re-check enforces the row exists before Build proceeds. This
is what keeps the file current without a separate periodic re-audit: every future save
action that goes through `/goal` or `/long-goal` writes its own row as a condition of
shipping, the same way Phase 4b already makes a CHANGE-NOTE.md a condition of shipping.

#### 0e. Sensitive-path budgets (the only hard caps)

Refuse if **any** of:

- `AFFECTED_PATHS` includes anything under `lib/stripe/` OR `lib/payments/` AND the bead body says or implies estimated diff > 300 LOC in those paths (look for stated LOC, file counts, "rewrite" / "refactor" language)
- `AFFECTED_PATHS` includes anything under `lib/auth/` OR `pages/api/auth/` AND estimated diff > 300 LOC in those paths
- `AFFECTED_PATHS` includes `drizzle/migrations/` AND the migration touches > 3 tables OR adds a NOT NULL column without an explicit backfill described in the bead
- Total estimated bundle diff > 2000 LOC (sum across all beads in scope, where stated; if not stated, trust the bead)
- Epic has > 10 open children
- Bead carries a `requires-shane-eyes` label (manual escalation lever)

If refused: post a refusal note on the bead via `bd comment <ID> "..."` (never `--notes`), print a `failed:` line with the specific budget that tripped, exit cleanly.

Trust the bead's own size estimate where present. Do not pre-emptively re-estimate. If Shane wrote "small fix", believe it.

**Warn but proceed** if:
- Priority is P1 (humans usually look at these, but the work itself is fine)
- Bead carries `client-reported` label for a named clinic (Viso, Omorphia, Awlin, Dr Prash, etc.)
- Sensitive keyword appears in body but no sensitive path in `AFFECTED_PATHS` (e.g. a UI bead that mentions "payment" because it renders payment state)

Warnings are surfaced in the final result line, not blockers.

#### 0f. Re-check after Phase 2 plan is written

After Phase 2 produces PLAN.md (per child for bundles), re-extract affected paths from the plan. If the plan reveals sensitive paths or sizes the bead description didn't, run Phase 0e again. If it now trips, refuse there and surface — do not proceed to Build.

Also re-run 0d.1's invariant AND promise-inventory match against the plan's own *Affected
files* list, not just the original bead body — a design can reveal it touches an invariant's
table, or a promise's persist/read path, that wasn't obvious from the bead description
alone. Any new match must get its corresponding "Invariant interactions" / "Promise
interactions" line added to PLAN.md before Build proceeds. If the plan adds a new
save/persist action, confirm PLAN.md names the new `promise_inventory.jsonl` row (see 0d.1B)
— missing row is treated the same as a missing CHANGE-NOTE.md: do not push.

### Phase 1 — Worktree + claim

1. If not already in a worktree, call `EnterWorktree`. Otherwise work in place.
2. Branch name: `goal/<bead-id-lowercase>` for singletons, `goal/<epic-id-lowercase>` for bundles.
3. For each bead in scope:
   ```bash
   bd update <ID> --claim --status=in_progress
   bd comment <ID> "started /goal $(date -Iseconds)"
   ```
   🔴 **Never `--notes` here (fixed 2026-08-23).** `--notes` REPLACES the field wholesale
   — a bead claimed mid-lifecycle (a prior close reason, prior scoping notes) would lose
   that history the instant this ran. `bd comment` is append-only; use it for anything
   that isn't meant to overwrite. See `reference_bd_update_notes_replaces_use_comment`.

### Phase 2 — Plan (Opus, in-context)

**Single bead:** compose one PLAN.md at the worktree root.

**Bundle:** compose one PLAN.md per child under `.plans/goal/<child-id>.md`, plus a `BUNDLE.md` at the worktree root that links all child plans and states the execution order.

Each PLAN contains:
- Goal — one sentence
- Bead context — verbatim description quote
- Affected files — list (this is what Phase 0f re-checks against)
- Invariant interactions — one line per invariant injected at 0d.1A: respected unmodified /
  deliberately interacts via [mechanism] / doesn't apply because [reason]. Omit the field
  entirely only when 0d.1A found zero matches.
- Promise interactions — one line per promise_inventory.jsonl row injected at 0d.1B (the
  full connected cluster, not just the directly-touched row): stays VERIFIED / now BROKEN
  because [reason, fix before Build] / not affected because [reason]. If this bead adds a
  new save/persist action, name the new row being added (surface, promise, persists_to,
  runtime_consumers) — see 0d.1B. Omit the field entirely only when 0d.1B found zero matches
  and nothing new is being persisted.
- Approach — bullet list of changes
- Risk — what could break, especially boundary-crossing
- Rollback — how to undo
- Acceptance criteria — concrete pass/fail checks

For bundles, execution order should batch tightly-coupled children together but otherwise run smallest-first to surface failures early.

### Phase 2b — Plan review gate (MANDATORY, runs before Build)

CLAUDE.md declares the plan-review pipeline mandatory. This phase is what makes that
true — before 2026-07-25 the gate existed only as prose and was silently skipped on
every `/goal` dispatch. Do not proceed to Phase 3 without running this.

**1. Classify the change.** From PLAN.md's *Affected files* plus the bead labels, derive:

| Signal | How to read it |
|---|---|
| `touchesCore` | `lib/`, or any API surface |
| `touchesSensitive` | auth, multi-tenancy, payments/Stripe, migrations, consent/PII, GDPR deletion |
| `userSurfaceNew` | a net-new page, flow, or feature entry point |
| `userSurfaceOverhaul` | restructures surface users already have: step or page count changes, navigation/IA changes, large layout shift, or replaces a page they already use |
| `userSurfaceMinor` | everything else user-visible — copy, one control, styling, a fix inside an existing flow |
| `scopeDivergence` | the plan is materially bigger or more heavy-handed than the bead asked for |
| `isHotfix` | bead is S1/S2 |
| `isDocsOnly` | only `.md` / non-executing assets |

> `fileCount` was deliberately removed as a routing signal on 2026-07-25. It was the old
> `>3 files` heuristic and it does no useful work: a programmatic change across 100 files
> can be safer than a 3-file change to payments. Risk lives in *what* is touched, not how
> much. File count still belongs in the Phase 4b note as context (a large mechanical change
> should say so and name the pattern), just not as a gate.

**2. Route to reviews.** Keep this gate light on purpose — see the note below.

```
touchesSensitive       -> /plan-eng-review      # exemption-list domains; over-caution is
                                               # correct and cheap here
scopeDivergence        -> /plan-ceo-review      # the taste call worth interrupting for:
                                               # AI ballooning work for no reason
userSurfaceNew         -> /plan-design-review   # the plan DOES contain the IA and flow
userSurfaceOverhaul    -> /plan-design-review   # decisions here — review them while they
                                               # are still cheap to change
userSurfaceMinor       -> none                  # Phase 4b catches this class
isDocsOnly             -> none
isHotfix (S1/S2)       -> none                  # /review and /ship still run. Never skipped.
everything else        -> none. Go straight to Build.
                          The gate that matters for this class is Phase 4b.
```

🔴 **For `userSurfaceOverhaul`, the design review must answer one question explicitly:
where does every capability that exists today land in the new structure?** Require a
literal old → new mapping, one row per existing capability, with "removed" as a permitted
answer only if it is stated deliberately rather than by omission.

This is the plan-time twin of the Phase 4b reachability diff. 4b asks *where did everything
actually land* once the code exists; this asks *where is everything supposed to land* while
it is still cheap to move. The Marketing-Studio regression is precisely the class that
escapes when only one of the two runs — it was an overhaul, the destinations were never
mapped, and capabilities fell through into a legacy escape hatch.

> 🔴 **Why this gate is deliberately light (2026-07-25, Shane).** Shane has built Aestheticc
> with AI since Claude 3 and has pushed through *almost every* pre-build decision as a yes.
> A gate whose output is "a question before we build" mostly manufactures friction and
> collects a rubber stamp — and a gate that produces friction without value is a gate that
> gets skipped, which is how the last one got orphaned. The decisions genuinely worth
> stopping him for are narrow: heavy-handed scope ballooning, the sensitive domains, and
> new-or-overhauled user surface. Everything else is caught better *after* the work exists,
> by **Phase 4b**, where there is real evidence to look at instead of a prediction.
>
> **The user-surface split is load-bearing (Shane's refinement, same day).** The first draft
> of this gate excluded design review entirely, on the reasoning that front-end failures
> emerge during Build and a plan can't predict a control being demoted to a text link. That
> is true for *minor* changes and wrong for the other two: when the surface is **new** or
> being **overhauled**, the plan genuinely does carry the information architecture, the flow,
> the step and page counts. Those are exactly the decisions that are cheap to change on paper
> and expensive to change afterwards. Review them there; leave everything else to 4b.

**3. Run them.** If more than one review fires, invoke `/autoplan` — it reads the CEO,
design, eng and DX review skills from disk and runs them with auto-decisions, which is
one call instead of N. If exactly one fires, invoke that skill directly.

`/autoplan` is session-aware (it detects `SESSION_KIND` spawned/headless/interactive).
In a spawned `/goal` it must not block on `AskUserQuestion` — apply the **Shane Decision
Frame** from CLAUDE.md (most robust long-term choice; don't overscope for imaginary
scenarios; overscope only when easy AND clearly beneficial).

**4. Capture evidence.** Write each review's output to the run's evidence directory
alongside the QA evidence, so `/land-batch` and any later audit can see what was gated
and what it said. A review that ran but left no artifact did not run.

**5. Act on the verdict.**
- **Blocking findings** → revise PLAN.md, re-run *that* review once. Still blocking →
  stop, surface to Shane, do not proceed to Build. A refusal here is the gate working.
- **Non-blocking findings** → record them in PLAN.md under a *Review notes* heading and
  proceed.

**Proportionality applies to review findings too.** A plan review that objects on
grounds failing the Part 5 rubric in `Aestheticc/Reference/GRAPH_ARCHITECTURE.md`
(no two adversarial parties, doesn't break a normal user, trivial real-world remedy) is
not a blocking finding — note it and move on. The exemption list still bypasses that
filter and always blocks: cross-tenant leakage, auth bypass, PII exposure, payment
correctness, GDPR deletion, data loss, **and clinical-safety gates on the prescribing
pathway** (BMI verification, contraindication blocks, dosing limits).

🔴 **When "a human wouldn't do this check" collides with clinical safety, clinical safety
wins.** A receptionist wouldn't demand ID over the phone — that correctly kills an
age-verification build. A receptionist wouldn't check BMI either, but that must NOT kill a
contraindication block, because on the prescribing pathway our software *is* the clinical
record, not a general-purpose tool. Ask whose responsibility the surface carries: user-
initiated marketing output is theirs (don't gate); the clinical record is ours (gate).
Detail: `feedback_dont_over_gate_user_responsible_features`.

### Phase 3 — Build (Opus)

**Single bead:** implement PLAN.md as one or more atomic commits on the feature branch.

**Bundle:** implement child plans in the order from BUNDLE.md. **One commit per child**, each commit message referencing its child bead ID. All commits on the same feature branch.

After each child commits, run quick checks per repo:
- Code repo (AestheticcNext): `npx tsc --noEmit && npx next lint` (scoped to changed files where possible)
- Vault: skip

If checks break: fix in the same commit (amend) or a fixup commit. Don't proceed to the next child until the current one is green.

If Husky pre-push tsc hangs cold: warm via `bun run typecheck` first (per `feedback_husky_tsc_cold_cache.md`).

### Phase 4 — QA (GLM primary / Codex fallback, read-only, adversarial)

**One QA pass against the combined diff at the end**, not one per child. Saves reviewer quota and lets it see cross-child interactions.

Create one evidence directory for the /goal run. In every QA round, run the
project's real test/QA gate first and capture its exact command, combined output,
and exit code. For AestheticcNext the default is shown below; for another repo,
derive runnable validation commands from its project instructions and PLAN.md.
If there is no runnable command, the round cannot PASS.

```bash
: "${RUN_ID:=$(date -u +%Y%m%dT%H%M%SZ)-$$}"
EVIDENCE_DIR="$HOME/.claude/evidence/goal/$RUN_ID"
mkdir -p "$EVIDENCE_DIR"
QA_COMMAND='npm run typecheck && npm run lint && npm run test'
printf '%s\n' "$QA_COMMAND" > "$EVIDENCE_DIR/qa-command-round-N.txt"
set +e
{
  printf '$ %s\n\n' "$QA_COMMAND"
  bash -o pipefail -c "$QA_COMMAND"
} > "$EVIDENCE_DIR/qa-output-round-N.log" 2>&1
QA_EXIT=$?
set -e
printf '%s\n' "$QA_EXIT" > "$EVIDENCE_DIR/qa-exit-code-round-N.txt"
```

Follow `~/.claude/skills/plan-build-qa/SKILL.md` Pattern B (structured JSON). Pipe the prompt via stdin (never `$TMPDIR` in nested subshell, per `feedback_codex_exec_prompt_passing.md`):

```bash
set +e
{
  cat ~/.claude/skills/plan-build-qa/prompts/qa.md
  echo "---"
  echo "PROJECT CLAUDE.md (calibration rules — apply before assigning S1/S2):"
  [ -f CLAUDE.md ] && cat CLAUDE.md
  echo "---"
  echo "PLANS:"
  for f in PLAN.md .plans/goal/*.md; do
    [ -f "$f" ] && echo "## $f" && cat "$f" && echo
  done
  echo "---"
  echo "DIFF (committed):"
  git fetch origin main --quiet
  git diff origin/main...HEAD   # NOT local main — stale local main sweeps in already-merged PRs (feedback_goal_qa_diff_base_stale_main)
  echo "---"
  echo "DIFF (uncommitted, staged + unstaged):"
  git diff --cached
  git diff
  echo "---"
  echo "Untracked:"
  git ls-files --others --exclude-standard
  echo "---"
  echo "EVIDENCE ARTIFACTS (the verdict must cite all three):"
  echo "$EVIDENCE_DIR/qa-command-round-N.txt"
  echo "$EVIDENCE_DIR/qa-output-round-N.log"
  echo "$EVIDENCE_DIR/qa-exit-code-round-N.txt"
} > "$EVIDENCE_DIR/qa-prompt-round-N.txt"
```

🔴 **B-10 (AI Fleet Grand Plan, 2026-09-01): GLM is the primary Phase 4 reviewer, Codex is the fallback.** Shane's decision — GLM (`glm review`) is the cheap-first resource (~£80/mo flat-rate vs ~£200/mo Codex+Claude combined), not the fallback. Codex only runs when `glm review` itself exits non-zero (a genuine call failure — auth/network against z.ai — same GLM-outage doctrine as everywhere else in `CLAUDE.md`). A clean GLM run that found nothing wrong still exits 0 with a PASS verdict; that is NOT a fallback trigger.

```bash
set +e
glm review --cd "$(pwd)" < "$EVIDENCE_DIR/qa-prompt-round-N.txt" \
  > "$EVIDENCE_DIR/glm-verdict-round-N.log" \
  2> "$EVIDENCE_DIR/glm-stderr-round-N.log"
GLM_EXIT=$?
set -e
printf '%s\n' "$GLM_EXIT" > "$EVIDENCE_DIR/glm-exit-code-round-N.txt"

if [[ "$GLM_EXIT" -eq 0 ]]; then
  VERDICT_LOG="$EVIDENCE_DIR/glm-verdict-round-N.log"
  REVIEWER="glm"
else
  echo "GLM unreachable (exit $GLM_EXIT) — falling back to Codex for this round. Note this in Phase 7's report: GLM unreachable, Codex fallback used." >&2
  set +e
  cat "$EVIDENCE_DIR/qa-prompt-round-N.txt" \
    | ~/.claude/scripts/fleet-guarded.sh codex "$BEAD_ID phase4 QA round-N" codex exec \
      --cd "$(pwd)" \
      --ephemeral \
      --sandbox read-only \
      - > "$EVIDENCE_DIR/codex-verdict-round-N.log" \
      2> "$EVIDENCE_DIR/codex-stderr-round-N.log"
  CODEX_EXIT=$?
  set -e
  printf '%s\n' "$CODEX_EXIT" > "$EVIDENCE_DIR/codex-exit-code-round-N.txt"
  VERDICT_LOG="$EVIDENCE_DIR/codex-verdict-round-N.log"
  REVIEWER="codex (GLM fallback)"
fi
# NO `-c model="gpt-5-codex"` (unsupported on ChatGPT acct) and NO `--output-schema`
# (verdict.json not strict-compatible) — both make Phase 4 fail before reviewing
# (feedback_goal_qa_codex_flags_drift). Ask for JSON in the prompt (same qa.md prompt
# for both reviewers); parse it from the output between the `^codex$`/model-name
# marker and `^tokens used$`.
#
# 🔴 Added 2026-08-23: the Codex fallback path routes through fleet-guarded.sh, which
# claims a slot against the same 11-total fleet budget Agent View dispatch uses (see
# fleet-slots.py) before running Codex, and releases it on exit regardless of outcome.
# Fails open after 90s at capacity rather than blocking a QA gate indefinitely — never
# remove this wrapper to "simplify" the command; that's exactly how this budget leaks
# back to uncounted. GLM does NOT need this — it's a separate flat-rate z.ai account,
# not competing for the same Codex/Claude budget.
# NO `-c model="gpt-5-codex"` (unsupported on ChatGPT acct) and NO `--output-schema`
# (verdict.json not strict-compatible) — both make Phase 4 fail before reviewing
# (feedback_goal_qa_codex_flags_drift). Ask for JSON in the prompt; parse it from
# the output between the `^codex$` and `^tokens used$` markers.
#
# 🔴 Added 2026-08-23: routed through fleet-guarded.sh, which claims a slot against
# the same 11-total fleet budget Agent View dispatch uses (see fleet-slots.py) before
# running Codex, and releases it on exit regardless of outcome. Fails open after 90s
# at capacity rather than blocking a QA gate indefinitely — never remove this wrapper
# to "simplify" the command; that's exactly how this budget leaks back to uncounted.
```

**Before acting on any S1/S2 finding, independently re-apply qa.md's calibration gate yourself** — don't just trust the reviewer's ($REVIEWER — GLM or Codex) severity label. For each S1/S2: does the finding's own text actually establish (a) a real entry point a normal user or the never-dismissible categories (cross-tenant/unauthenticated/payment/GDPR) reach, and (b) that the proposed fix stops real harm rather than a theoretical one with a trivial out-of-band bypass? A finding that fails this re-check is **dismissed, not fixed** — log `dismissed (not-the-police): <finding> — <one-line why>` in the round notes, don't fix it, don't file it as a follow-up bead, and don't let it count toward NEEDS_CHANGES. This is a second, independent check on top of qa.md's own instructions — both reviewers still sometimes over-flag even when told not to.

Parse the (calibrated) verdict:
- **PASS + zero surviving S1/S2 + valid evidence:** go to Phase 5.
- **PASS + only S3 + valid evidence:** log S3s as follow-up beads, go to Phase 5.
- **NEEDS_CHANGES or BLOCK, with surviving findings:** ingest findings, fix S1/S2 in the working tree, commit as a fixup (or amend if the fix belongs to a specific child), GOTO Phase 4. Hard cap: 3 rounds.
- **After 3 rounds still not clean:** file each remaining finding **that survives calibration** as a follow-up bead with `--parent <epic-id-or-singleton-id>`, label `qa-followup`, write a summary in the parent's notes (including any dismissed findings, for a fast audit trail), and STOP. This is `failed:` not `result:`.

**Valid evidence is mandatory:** the verdict must cite the round's command,
captured output, and exit-code artifacts under `$EVIDENCE_DIR`; those files must
exist, the output must record the command that actually ran, and both the QA and
Codex exit-code files must contain `0`. A model-returned PASS without those
artifacts is QA FAILED. "Suite did not run" never equals "suite passed."

### Phase 4b — User-facing change note (MANDATORY for ANY user surface)

*Fires on `userSurfaceNew`, `userSurfaceOverhaul` **or** `userSurfaceMinor` — all three.
Unlike the Phase 2b routing, this gate does not grade by size: a one-control change is
exactly how a capability quietly gets demoted.*

Runs after QA passes, before the branch is pushed. **Backend-only change → one line, skip
the rest.** Shane can't read the backend anyway and is waiting on tests and expected
outcomes; this phase exists entirely for the front end.

🔴 **The "backend-only" call cannot be self-asserted — it needs evidence, same bar as
everything else in this phase.** Confirmed 2026-08-23: this was a live escape hatch — an
agent could write "backend only" in one line and skip the entire reachability check, hard
FAILs included, even when the bead's own intent was obviously user-facing. Before writing
"backend only," grep the bead's title/description/acceptance criteria for signs of intended
user-facing capability (`UI`, `frontend`, `screen`, `page`, `dashboard`, `studio`, `portal`,
`flow`, `for users`, `let clients/users`, `client-facing`, or similar). If any such signal is
present, "backend only" requires an explicit one-line justification naming where the
frontend work is tracked instead (a sibling/child bead ID) — an unqualified "backend only"
on a bead whose own ask implied a UI is not acceptable and the full reachability check must
run instead. When genuinely uncertain, default to running the full check — the check is
cheap; a shipped-and-reported "done" feature with a real frontend gap is not.

Write `CHANGE-NOTE.md` at the worktree root, addressed to Shane, in plain language — not
engineer language, no file paths, no component names. If a sentence would mean nothing to
a clinic owner, rewrite it.

**The four questions:**

1. **What can a user do now that they couldn't before?** Give the exact click path from
   landing. "Dashboard → Marketing → New post → Before/after" — not "added to the studio".
2. **What changed about something they already did?** Same path as before, or moved?
3. 🔴 **What got harder to reach, or disappeared?** Enumerate every entry point that
   existed before this change and does not now, or now takes more clicks. **This is the
   silent-drop catcher and it is the most important line in the note.**
4. **What did we promise that isn't wired?** Any control added whose label implies more
   than it does — a button that saves nothing, a link to a page that doesn't exist yet.

**Reachability check — evidence, not vibes.** List every route and nav entry the touched
feature is reachable from, before and after. Diff the two lists. Then apply:

🔴 **Hard FAIL conditions — these are blocking, not notes:**
- The feature is built but reachable from **no** navigation path.
- The only route to it is a link labelled some variant of *"old version" / "classic" /
  "legacy" / "previous"*. That link is proof the migration did not complete — folding the
  old thing in was the job, and an escape hatch means it wasn't done.
- A control that previously had a first-class home is now a text link, below the fold, or
  buried one level deeper with nothing left at the original location.

A FAIL here does not get written up as a caveat. Fix it, or stop and surface it.

🔴 **THRESHOLD-SCOPE check (added 2026-08-28, `AestheticcNext-nd9ro`).** Fires on ANY diff —
backend-only included — that changes a conditional or comparison as a bug fix (`>`, `>=`,
`===`, `.length`, a guard clause, a filter predicate). Before the note is done, write two
predicates side by side: (a) the **root-cause condition** from the bead — the state under
which the bug occurs, not the instance that was reported — and (b) the **fix predicate** as
actually written in the diff. Confirm (b) covers (a). If the fix predicate is narrower than
the root condition, that is a hard FAIL: the bug was narrowed, not fixed, and the un-fixed
remainder is often the *default* state. Worked example: `AestheticcNext-brzhi` — day-view
calendar gutter invisible in dark mode. The reported case was Rooms (more than one resource
column), so the fix was written as `resources > 1`; the actual condition was *any* resource
view (`resources > 0`), which left the single-visible-staff default of a solo clinic still
broken. Source: `Product/QA/PROACTIVE_BUG_CLASSES_2026-08-28.md` class 3.

> **Why this phase exists (2026-07-25, Shane).** Documented recurring failure: code
> technically works — a button does *something* — but not the thing it promised, or a whole
> feature ships with no path to it in the UI. Live case: the Growth-engine/Marketing-Studio
> upgrade silently dropped functionality; before/after photos ended up buried in Library and
> "create with AI" was demoted to a small text link at the bottom behind a "look at the old
> version" escape hatch, when the whole intent was to fold it in. See
> `feedback_code_blindness_ghost_features`, `project_promise_debt_feature_does_not_exist`
> (BET-8: 127 broken promises), `feedback_buttons_dont_disappear_on_empty`.
>
> This is the harness that replaces asking Shane questions *before* the work. He says yes to
> nearly everything up front; what he actually needs is a truthful account of what moved,
> after, with the evidence in front of him.

### Phase 5 — Ship to branch (NO deploy)

🔴 **/goal NEVER deploys anywhere.** Not production, not staging, not a worktree revision. No `gcloud builds submit`. No `gcloud run deploy`. No `--tag`. No `--no-traffic` revision. Per-worktree QA deploys spin up billable always-on Cloud Run revisions — the cost leak that nearly ended the runway (`feedback_cloud_run_warm_instance_cost_model.md`). If the bead body contains "deploy to prod" / "production deploy", refuse with `failed:` — mis-scoped for /goal.

🔴 **NO MERGE TO MAIN.** Agents never merge to main and never fire `@deploy-staging` themselves. /goal's job ends at a pushed, QA-clean feature branch.

Steps:

0. **Precondition:** if the change touched any user surface, `CHANGE-NOTE.md` must exist at the worktree root
   and contain no unresolved Phase 4b FAIL. Missing note or open FAIL → do not push; go
   back to 4b. *(This precondition is what stops 4b becoming another orphaned gate — the
   exact failure mode that silently killed the plan-review gate for six weeks.)*
1. `/ship` to push the feature branch (runs tsc + lint + tests). Do NOT pass any flag that would merge to main.
2. That's it. **No deploy step.** The branch is now ready for `/land-batch` to discover and land (worktree stays intact — see Phase 6.5), then staging QA on the **main staging service** (the only QA surface). Vault-repo beads also just push (no deploy was ever relevant there).

If `/ship` (tests/lint/tsc) fails and can't be fixed within the QA loop, write `needs input:` not `result:` in Phase 7 — the bead stays open.

### Phase 6 — Mark beads done (HARD GATE — see top-of-skill rule)

**AestheticcNext- code beads: `pushed`, never `closed`.** Per B-1: this is not optional and not a judgement call, regardless of how many QA rounds ran or how confident you are. `/land-batch` is the only thing that closes these, after it verifies `git merge-base --is-ancestor <landed-sha> origin/main` for real.

**LUCY- vault/tooling beads still close normally** (different repo, not in B-1's scope) — use `bd close <ID>` as before.

**Single AestheticcNext- bead:**

```bash
bd update <ID> --status pushed --append-notes "completed via /goal $(date -Iseconds). Branch: goal/<id> pushed (QA rounds: <N>). Worktree left intact for /land-batch. No autonomous deploy."
```

**Bundle:** mark every child `pushed` individually with its specific summary, then mark the parent epic `pushed` too with the overall summary + branch name + list of children.

**Then verify every update stuck.** Per-bead:

```bash
bd show "$ID" 2>&1 | grep -qiE "^Status:.*pushed|\[PUSHED\]|· PUSHED" \
  || { echo "FAILED to mark $ID pushed — aborting Phase 7"; exit 1; }
```

If even one verification fails, do NOT proceed to Phase 7's `result:` line. Write `needs input:` instead, listing the bead IDs that wouldn't update and the `bd` stderr. Common failure: `bd` couldn't find the bead because `BEADS_DIR` drifted between phases (re-check Phase 0a routing). If `status.custom` doesn't yet have `pushed` registered in this repo's beads DB, run `bd config set status.custom "pushed:wip"` once — don't fall back to `closed` instead.

If QA failed at the 3-round cap: leave beads open with notes documenting state, don't close, and write `needs input:` not `result:`.

### Phase 6.5 — Leave the worktree for `/land-batch` (do NOT remove it)

🔴 **Reversed 2026-08-26.** This phase used to remove the worktree here as disk hygiene,
reasoning "the branch ref is enough, Shane can still merge from that." That was true but
wrong: `/land-batch` discovery treats a worktree-backed branch and a worktree-*less*
("remote-only") branch very differently. A worktree-backed candidate can become
`auto_land=true` and land itself through the normal multiSelect gate. A remote-only
candidate is **hard-quarantined — `held-branch-only-*`, never offered, no matter how
complete the evidence** (exact bead CLOSED + pinned patch-unique tip is not enough) —
because discovery can no longer independently confirm the tree was clean when the
session finished; it would have to trust the session's own self-report, which is exactly
what `/land-batch`'s own first principle refuses to do ("narration is input, never
evidence"). Deleting the worktree here was quietly forcing every single `/goal` run onto
the dead-end path that needs a human to run raw `git merge` — the thing Shane has never
had to do in 18 months of running this system, and does not want to start doing now.
Landing is `/land-batch`'s job; `/goal` handing it a live worktree is what lets it do that
job without a human touching git.

So: **do not remove the worktree.** Its only remaining purpose here is proving the
precondition below, for the report — not for deletion.

Precondition to note in the report (informational only — do NOT act on a failure by
removing the worktree; a dirty tree is `/land-batch` discovery's problem to flag, not this
phase's problem to clean up):
- `git -C <worktree> status --porcelain --untracked-files=no` — empty means clean, which is
  exactly the signal that lets `/land-batch` classify this as auto-landable later.

Nothing else in this phase mutates anything. Proceed straight to Phase 6.6.

**Disk hygiene still exists — it just happens AFTER landing, not before.** Once
`/land-batch` has actually merged a worktree's branch to main, `scripts/cleanup-merged-worktrees.sh`
(dry-run, then `--apply`, run periodically from the trunk) reclaims it — it only removes
worktrees whose work already landed on main, preserves branch refs, never touches DIRTY
(uncommitted) worktrees, and flags STALE ones for manual review. `/land-batch`'s own
discovery also offers safe pruning for exactly this shape (`source_kind=worktree`,
clean, `ahead=0` after landing) through the same multiSelect gate. A worktree living from
"branch pushed" to "branch landed" is the cost of `/land-batch` being able to act without
a human — a few hundred MB for a few hours/days, not indefinitely.

### Phase 6.6 — Release fleet slot + advance the queue

🔴 **Added 2026-08-23.** This session was dispatched through `fleet-dispatch.py`, which
claimed one of 5 Agent View slots (of an 11-total fleet budget) before starting. Release
it now, unconditionally — even if earlier phases failed or wrote `needs input:` — a
session that exits without releasing leaks a slot until the next stale-holder reap:

```bash
python3 ~/.claude/scripts/fleet-slots.py release-agent-view <THIS_BEAD_ID>
```

Then check whether the next queued bead can now take a slot, and dispatch it through the
path recorded by its queue kind (`codex`, `goal`, or `long-goal`). This shared, resource-aware
drain is what makes the queue self-driving instead of requiring the orchestrator to poll:

```bash
NEXT_INFO=$(python3 ~/.claude/scripts/fleet-slots.py dequeue-next --with-kind)
if [ "$NEXT_INFO" != "NONE" ]; then
  read -r NEXT NEXT_KIND NEXT_TOKEN <<< "$NEXT_INFO"
  case "$NEXT_KIND" in
    codex)
      python3 ~/.claude/scripts/fleet-dispatch-codex.py dispatch "$NEXT" --pre-claimed "$NEXT_TOKEN"
      ;;
    goal)
      python3 ~/.claude/scripts/fleet-dispatch.py "$NEXT" --pre-claimed
      ;;
    long-goal)
      python3 ~/.claude/scripts/fleet-dispatch.py "$NEXT" --epic --pre-claimed
      ;;
  esac
  RC=$?
  if [ $RC -ne 0 ]; then
    # Both dispatchers release their pre-claimed slot on any failure path.
    python3 ~/.claude/scripts/fleet-slots.py enqueue "$NEXT" "$NEXT_KIND"
  fi
fi
```

Note in the Phase 7 report if you advanced the queue (which bead, if any), or if an advance
was attempted but the gate refused it: `attempted <bead>, refused by gate (<reason>), re-queued`.
This is now genuinely the LAST mutating step — only the Phase 7 report follows.

### Phase 7 — Report

Final line:

```
result: <BEAD_ID> (or epic <EPIC_ID> with N children) closed — branch goal/<id> pushed, <N> QA rounds clean. Evidence: <absolute command, output, and exit-code artifact paths>. Worktree left intact for /land-batch to discover and land. Warnings: <list or none>.

USER-FACING CHANGES (Phase 4b):
<paste CHANGE-NOTE.md in full, or "none — backend only">
```

🔴 **Paste the change note in full, do not link to it.** Shane reads the report; a link to a
file in a worktree he will never open is the same as not writing it. If any user surface was touched and
this section is missing, the run is not reportable.

QA happens after `/land-batch` lands the branch to main and the normal staging deploy runs (the main staging service is the only QA surface). **Never fire `@deploy-staging`** — that subagent is banned per CLAUDE.md (deploy subagents hallucinate "queued/monitoring/completed" without submitting). /goal does not deploy.

For partial/failed runs:

```
needs input: <BEAD_ID> hit 3-round QA cap, <N> follow-up beads filed (<bead-ids>). Shane review needed.
```

```
failed: <BEAD_ID> ineligible — <specific budget that tripped>. Bead untouched, refusal note added.
```

## Hard-cap cheat sheet (Phase 0e summarised)

| Condition | Action |
|---|---|
| `lib/stripe/` or `lib/payments/` diff > 300 LOC | `failed:` |
| `lib/auth/` or `pages/api/auth/` diff > 300 LOC | `failed:` |
| Migration touches > 3 tables OR NOT NULL without backfill | `failed:` |
| Total bundle diff > 2000 LOC | `failed:` |
| Epic > 10 open children | `failed:` |
| `requires-shane-eyes` label on any bead in scope | `failed:` |
| P1 priority | Warn, proceed |
| `client-reported` label for named clinic | Warn, proceed |
| Sensitive keyword in body but no sensitive path | Warn, proceed |
| Stale >60d without re-validation | Re-validate first, then re-evaluate |
| In-progress by another active session | `failed:` |
| Bead closed | `failed:` |

Stripe + tests + frontend display + small migration in one bundle: **fine**. Each piece is small. Total bundle stays under budget. No single sensitive path goes over 300 LOC.

Full `lib/stripe/` rewrite alone: **refused**. Sensitive-path budget blown.

## What this does NOT do

- Does not merge to main. Ever.
- **Does not deploy anywhere.** No production, no staging, no worktree revision. No `gcloud builds submit`, no `gcloud run deploy`, no `--tag`, no `--no-traffic` revision. Per-worktree QA deploys spin up billable always-on revisions (`feedback_cloud_run_warm_instance_cost_model.md`) — banned.
- Does not fire `@deploy-staging`. QA happens on the main staging service AFTER `/land-batch` lands the branch and the normal staging deploy runs.
- Does not refuse-vs-allow on staging deploys — it simply never deploys. (Still refuses with `failed:` if the bead body says "deploy to prod"/"production deploy" — mis-scoped.)
- Does not bypass `/review` or `/ship` gates. They run inside Phase 5.
- Does not pick its own bead. Shane (or a future supervisor cron) chooses what to fire on.
- Does not re-estimate effort. Trusts the bead's stated size.

## Future: --strict mode

Reserved for the day a bead really does need Shane's eyes on every decision. Triggered by:
- `--strict` flag, OR
- `requires-shane-eyes` label (currently a hard refuse; under strict mode it'd flip to interactive)

Strict mode behaviour (not yet implemented, design only):
- Forces `AskUserQuestion` at each meaningful decision (no Shane Decision Frame auto-pick)
- Smaller QA round limit (2 instead of 3)
- Will not auto-close beads, only stage them for manual close

For now: `--strict` flag is parsed but unused. If you set `requires-shane-eyes` label on a bead, `/goal` refuses with a `failed:` line saying so.

## Background mode

Not supported in /goal itself. /goal runs synchronously inside a fresh /agent chat. The /agent view is the supervisor. For background bead execution, use the night-batch (autonomous, runs from `~/.gstack/overnight-queue/`).

## Failure modes to watch

1. **Codex output is dual-formatted.** Extract between `^codex$` and `^tokens used$` markers per `feedback_plan_build_qa.md`.
2. **`bd` commands fail silently in worktrees.** Phase 0a handles the per-prefix BEADS_DIR routing. If `bd show` still returns empty, that's the cause.
3. **Husky pre-push tsc hangs cold.** Per `feedback_husky_tsc_cold_cache.md`, warm via `bun run typecheck` before /ship.
4. **Plan disagrees with what bead actually wants.** Re-read the bead description before each phase. Don't drift onto a different problem.
5. **Bundle Phase 4 finding maps to a single child.** When QA returns a finding, identify which child's commit introduced it (via `git log --diff-filter=M <file>`), fix in a fixup commit referencing that child's bead ID.
6. **Estimated LOC missing from bead.** Trust the bead. Proceed. Phase 0f will catch it if reality is much bigger than implied.
