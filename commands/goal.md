# /goal — Autonomous Bead Execution via Plan-Build-QA Partnership

Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly.

Take a bead ID (single or epic), run the full Lucy+Codex partnership flow end-to-end: plan, build, adversarial QA, iterate until clean, push the feature branch, close the bead(s). **Never merges to main. Never deploys anywhere — not staging, not production.** QA happens on the main staging service AFTER Shane merges the branch and fires `@deploy-staging`. Production deploys require Shane's explicit sayso in a foreground chat.

🔴 **NO PER-WORKTREE STAGING/QA DEPLOYS.** /goal must NOT run `gcloud builds submit` for any staging build, must NOT create a `--no-traffic` worktree revision, and must NOT add a Cloud Run `--tag`. Those spin up billable always-on revisions — the exact cost leak that nearly sank the runway (see `feedback_cloud_run_warm_instance_cost_model.md`). The only deploy surface is the main staging service, reached by Shane merging to main → `@deploy-staging`. /goal stops at "branch pushed + bead closed."

## 🔴 HARD RULE: No `result:` until every in-scope bead is verified CLOSED

The recurring failure mode of /goal runs is: work ships, branch pushes, but the bead stays OPEN because Phase 6 got skipped or `bd close` silently failed. Future Shane then can't tell what's done.

You are NOT allowed to write the `result:` line in Phase 7 until you have run `bd show <ID>` for every bead in scope (singleton OR every child of an epic) and verified the output shows `closed` / `CLOSED`. If the verification fails for any bead, write `needs input:` and list the beads that wouldn't close. See Phase 6 for the exact verification command.

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
- `updated_at` is >60 days old without a re-validation note. Post `bd update --notes "verified still relevant $(date +%F)"` first and re-evaluate.

#### 0c. Identify scope: single bead or epic-bundle

If the bead's `bd show` output lists `CHILDREN`, this is an epic. Collect every open child. Closed children are ignored.

If no children, treat as a single bead. The rest of the validation runs on either one bead (singleton) or the full child set (bundle).

#### 0d. Extract affected paths

For each bead in scope (singleton or every child), pull affected paths from:
1. Any `## Files` section in the description
2. Any path-shaped string in the body (`lib/foo/bar.ts`, `components/baz/qux.tsx` etc.)
3. Labels mentioning specific surfaces (`label=auth` etc.)

Build a set: `AFFECTED_PATHS`.

#### 0e. Sensitive-path budgets (the only hard caps)

Refuse if **any** of:

- `AFFECTED_PATHS` includes anything under `lib/stripe/` OR `lib/payments/` AND the bead body says or implies estimated diff > 300 LOC in those paths (look for stated LOC, file counts, "rewrite" / "refactor" language)
- `AFFECTED_PATHS` includes anything under `lib/auth/` OR `pages/api/auth/` AND estimated diff > 300 LOC in those paths
- `AFFECTED_PATHS` includes `drizzle/migrations/` AND the migration touches > 3 tables OR adds a NOT NULL column without an explicit backfill described in the bead
- Total estimated bundle diff > 2000 LOC (sum across all beads in scope, where stated; if not stated, trust the bead)
- Epic has > 10 open children
- Bead carries a `requires-shane-eyes` label (manual escalation lever)

If refused: post a refusal note on the bead via `bd update --notes`, print a `failed:` line with the specific budget that tripped, exit cleanly.

Trust the bead's own size estimate where present. Do not pre-emptively re-estimate. If Shane wrote "small fix", believe it.

**Warn but proceed** if:
- Priority is P1 (humans usually look at these, but the work itself is fine)
- Bead carries `client-reported` label for a named clinic (Viso, Omorphia, Awlin, Dr Prash, etc.)
- Sensitive keyword appears in body but no sensitive path in `AFFECTED_PATHS` (e.g. a UI bead that mentions "payment" because it renders payment state)

Warnings are surfaced in the final result line, not blockers.

#### 0f. Re-check after Phase 2 plan is written

After Phase 2 produces PLAN.md (per child for bundles), re-extract affected paths from the plan. If the plan reveals sensitive paths or sizes the bead description didn't, run Phase 0e again. If it now trips, refuse there and surface — do not proceed to Build.

### Phase 1 — Worktree + claim

1. If not already in a worktree, call `EnterWorktree`. Otherwise work in place.
2. Branch name: `goal/<bead-id-lowercase>` for singletons, `goal/<epic-id-lowercase>` for bundles.
3. For each bead in scope: `bd update <ID> --claim --status=in_progress --notes "started /goal $(date -Iseconds)"`.

### Phase 2 — Plan (Opus, in-context)

**Single bead:** compose one PLAN.md at the worktree root.

**Bundle:** compose one PLAN.md per child under `.plans/goal/<child-id>.md`, plus a `BUNDLE.md` at the worktree root that links all child plans and states the execution order.

Each PLAN contains:
- Goal — one sentence
- Bead context — verbatim description quote
- Affected files — list (this is what Phase 0f re-checks against)
- Approach — bullet list of changes
- Risk — what could break, especially boundary-crossing
- Rollback — how to undo
- Acceptance criteria — concrete pass/fail checks

For bundles, execution order should batch tightly-coupled children together but otherwise run smallest-first to surface failures early.

If any single PLAN is architecturally significant (>3 files, touches lib/, touches API surface), run `/plan-eng-review` against it first.

### Phase 3 — Build (Opus)

**Single bead:** implement PLAN.md as one or more atomic commits on the feature branch.

**Bundle:** implement child plans in the order from BUNDLE.md. **One commit per child**, each commit message referencing its child bead ID. All commits on the same feature branch.

After each child commits, run quick checks per repo:
- Code repo (AestheticcNext): `npx tsc --noEmit && npx next lint` (scoped to changed files where possible)
- Vault: skip

If checks break: fix in the same commit (amend) or a fixup commit. Don't proceed to the next child until the current one is green.

If Husky pre-push tsc hangs cold: warm via `bun run typecheck` first (per `feedback_husky_tsc_cold_cache.md`).

### Phase 4 — QA (Codex 5.5, read-only, adversarial)

**One QA pass against the combined diff at the end**, not one per child. Saves Codex quota and lets the reviewer see cross-child interactions.

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
} | codex exec \
  --cd "$(pwd)" \
  --ephemeral \
  --sandbox read-only \
  - > "$EVIDENCE_DIR/codex-verdict-round-N.log" \
  2> "$EVIDENCE_DIR/codex-stderr-round-N.log"
CODEX_EXIT=$?
set -e
printf '%s\n' "$CODEX_EXIT" > "$EVIDENCE_DIR/codex-exit-code-round-N.txt"
# NO `-c model="gpt-5-codex"` (unsupported on ChatGPT acct) and NO `--output-schema`
# (verdict.json not strict-compatible) — both make Phase 4 fail before reviewing
# (feedback_goal_qa_codex_flags_drift). Ask for JSON in the prompt; parse it from
# the output between the `^codex$` and `^tokens used$` markers.
```

Parse the verdict:
- **PASS + zero S1/S2 + valid evidence:** go to Phase 5.
- **PASS + only S3 + valid evidence:** log S3s as follow-up beads, go to Phase 5.
- **NEEDS_CHANGES or BLOCK:** ingest findings, fix S1/S2 in the working tree, commit as a fixup (or amend if the fix belongs to a specific child), GOTO Phase 4. Hard cap: 3 rounds.
- **After 3 rounds still not clean:** file each remaining finding as a follow-up bead with `--parent <epic-id-or-singleton-id>`, label `qa-followup`, write a summary in the parent's notes, and STOP. This is `failed:` not `result:`.

**Valid evidence is mandatory:** the verdict must cite the round's command,
captured output, and exit-code artifacts under `$EVIDENCE_DIR`; those files must
exist, the output must record the command that actually ran, and both the QA and
Codex exit-code files must contain `0`. A model-returned PASS without those
artifacts is QA FAILED. "Suite did not run" never equals "suite passed."

### Phase 5 — Ship to branch (NO deploy)

🔴 **/goal NEVER deploys anywhere.** Not production, not staging, not a worktree revision. No `gcloud builds submit`. No `gcloud run deploy`. No `--tag`. No `--no-traffic` revision. Per-worktree QA deploys spin up billable always-on Cloud Run revisions — the cost leak that nearly ended the runway (`feedback_cloud_run_warm_instance_cost_model.md`). If the bead body contains "deploy to prod" / "production deploy", refuse with `failed:` — mis-scoped for /goal.

🔴 **NO MERGE TO MAIN.** Agents never merge to main and never fire `@deploy-staging` themselves. /goal's job ends at a pushed, QA-clean feature branch.

Steps:

1. `/ship` to push the feature branch (runs tsc + lint + tests). Do NOT pass any flag that would merge to main.
2. That's it. **No deploy step.** The branch is now ready for Shane to merge → `@deploy-staging` → QA on the **main staging service** (the only QA surface). Vault-repo beads also just push (no deploy was ever relevant there).

If `/ship` (tests/lint/tsc) fails and can't be fixed within the QA loop, write `needs input:` not `result:` in Phase 7 — the bead stays open.

### Phase 6 — Close beads (HARD GATE — see top-of-skill rule)

**Single bead:** `bd close <ID>` with summary + branch name.

**Bundle:** close every child individually with its specific summary, then close the parent epic with the overall summary + branch name + list of closed children.

```bash
bd close <ID> --reason "completed via /goal $(date -Iseconds). Branch: goal/<id> pushed (QA rounds: <N>). Ready for Shane to merge → @deploy-staging for staging QA. No autonomous deploy."
```

**Then verify every close stuck.** Per-bead:

```bash
bd show "$ID" 2>&1 | grep -qiE "^Status:.*closed|\[CLOSED\]|· CLOSED" \
  || { echo "FAILED to close $ID — aborting Phase 7"; exit 1; }
```

If even one verification fails, do NOT proceed to Phase 7's `result:` line. Write `needs input:` instead, listing the bead IDs that wouldn't close and the `bd close` stderr. Common failure: `bd` couldn't find the bead because `BEADS_DIR` drifted between phases (re-check Phase 0a routing).

If QA failed at the 3-round cap: leave beads open with notes documenting state, don't close, and write `needs input:` not `result:`.

### Phase 6.5 — Reclaim the worktree (disk hygiene)

The branch is pushed (Phase 5) and every bead is verified CLOSED (Phase 6), so all work is captured on the remote branch + branch ref. The worktree itself (a full checkout, often 100MB–2GB with node_modules) is now dead weight — remove it so /goal runs don't accumulate disk. **Removing a worktree does NOT delete the branch ref or the pushed remote branch**, so Shane can still merge.

Preconditions (ALL must hold — else SKIP and note why in the report):
- Branch was pushed to origin in Phase 5.
- `git -C <worktree> status --porcelain --untracked-files=no` is empty (no uncommitted tracked changes — never discard unsynced work).

Steps:
1. `cd` to the trunk first — you cannot remove the worktree you are standing in:
   `cd /Users/shane/Documents/GitReBase/AestheticcNext`
2. Remove it:
   - Worktree created via the **EnterWorktree** tool → call **ExitWorktree** with `action: "remove"` (harness-native cleanup).
   - Worktree created manually (`git worktree add`) → `git worktree remove --force <worktree-path> && git worktree prune`.
3. This is the LAST mutating step — only the Phase 7 report follows (run it from the trunk).

If a precondition fails, leave the worktree in place and say so in the report.

**Backlog backstop:** stale worktrees from older runs accumulate (they're what fill the disk — branch refs are 41 bytes, worktrees are GBs). Periodically run `scripts/cleanup-merged-worktrees.sh` from the trunk (dry-run, then `--apply`). It reclaims only worktrees whose work already landed on main, preserves branch refs, never touches DIRTY (uncommitted) worktrees, and flags STALE ones for manual review.

### Phase 7 — Report

Final line:

```
result: <BEAD_ID> (or epic <EPIC_ID> with N children) closed — branch goal/<id> pushed, <N> QA rounds clean. Evidence: <absolute command, output, and exit-code artifact paths>. Ready for Shane to merge → @deploy-staging for staging QA. Warnings: <list or none>.
```

QA happens after Shane merges the branch to main and fires `@deploy-staging` (the main staging service is the only QA surface). /goal does not deploy.

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
- Does not fire `@deploy-staging`. QA happens on the main staging service AFTER Shane merges the branch and fires `@deploy-staging` himself.
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
