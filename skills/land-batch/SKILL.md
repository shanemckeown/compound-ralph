---
name: land-batch
version: 0.3.0
description: |
  Serialises parallel Claude worktree landing with a durable lock, FIFO queue,
  and cross-run QA ledger. Default /land-batch cheaply discovers, curates, and
  lands finished worktrees to main without deploying. /land-batch --ship runs
  the full gate, staging QA, and attended prod promotion.
triggers:
  - merge deploy and qa
  - batch land
  - land the finished worktrees
  - reconcile worktrees
  - land-batch
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

# /land-batch — Land often; ship once

Audit every progress claim against tool evidence from this session. A transcript,
browser narration, or deploy-agent narration is input, never evidence.

Modes:

- **/land-batch** — LAND: discover, multiSelect curation, scoped gate, merge and
  fast-forward-push to main, then harvest QA checks to a cross-run ledger. Never
  deploy staging or prod.
- **/land-batch --ship** — attended SHIP: full gate, one staging deployment,
  QA all ledger checks, Codex fix/revert loop, then one human production gate.
- **/land-batch --status** — read-only lock/queue/ledger dashboard; no lock.
- **--dry-run** — discovery and full plan only; preserve the precedent that it
  mutates nothing: no ticket, lock, scratch, ledger, merge, or deployment.

> **Design reversal — 2026-07-21.** D1 and former Guardrail #3 said main must
> not move until staging QA passed. That is deliberately reversed. LAND moves
> main behind a cheap per-feature scoped gate so parallel sessions land cheaply
> without overwriting staging QA. The new invariant is **prod never serves an
> unshipped SHA**, enforced by SHIP's full gate, one staging deployment,
> fail-closed ledger QA, and attended promotion. Do not silently restore the old
> integration-branch-only flow.

ENG_REVIEW.md is the locked record wherever it does not conflict with this dated
reversal.

## Hard guardrails

1. **Attendance.** LAND is safe in background Agent View because it never
   deploys, but its multiSelect gate still needs Shane's selection. SHIP is
   interactive/attended only. Refuse SHIP for SPAWNED_SESSION, OPENCLAW_SESSION,
   claude -p, cron, or a background agent: it deploys and holds a human prod gate.
2. **Source repo:** /Users/shane/Documents/GitReBase/AestheticcNext. Never
   operate on a feature worktree's main or checkout/stash/clean the dirty trunk.
3. **New main/prod invariant.** LAND may push green --no-ff feature merges to
   main but never deploys. SHIP alone promotes traffic. Never force-push. If
   main moved, fetch, rebase, re-gate, retry; abort and hold any rebase conflict.
4. **Direct evidenced deploys only.** One AskUserQuestion at the prod gate,
   defaulting to post-19:30 BST except explicit S1. Never trust deploy agents.
5. **Never auto-resolve conflicts.** Abort/hold conflicts; never touch conflicts
   in lib/db/, drizzle/migrations/, lib/stripe/, lib/auth/, lib/payments/,
   pages/api/auth|admin|webhooks/, or lib/email/templates/. .beads/ gets only
   the local merge=ours generated-churn driver; that is not conflict resolution.
6. **Sensitive paths never auto-land.** Require explicit Shane opt-in and exclude
   a candidate depending on a held sensitive branch.
7. **Finish gate unchanged.** Require effectively_clean, ahead >=1, non-active
   session, premise OK, non-sensitive, non-conflicting, non-retired. Optional
   .claude/land-ready.json boosts confidence and supplies bead_id; tail vetoes
   only, never approves.
8. **Evidence fail-closed and traffic verified.** Every command/output/exit-code
   artifact must be cited. Build SUCCESS alone does not prove Cloud Run traffic.
9. **Production is pinned.** Since 2026-06-15, never use --to-latest on prod.
   Only explicit --to-revisions <new-revision>=100 plus exact status verification.

## Shared mutex, queue, and ledger

LAND and SHIP share ~/.claude/state/land-batch/. The atomic mutex is mkdir
LOCK.d. holder.json contains run_id, mode, claude_pid, pid_start_time,
session_id, agent_view_name, started_at, stage, heartbeat_at,
integration_branch, scratch_path, and evidence_dir. QUEUE.d contains sortable
counter-prefixed ticket JSON. ledger.json is canonical pending QA; pending-qa.md
is its human-readable projection.

### Step 0 — Admission

For a non-dry run, create a stable run/evidence identity and fetch origin/main.
Dry-run runs read-only discovery/plan first and skips this admission step.
Resolve the actual current Claude PID and its ps lstart value from session
metadata; never use an ephemeral shell PID that dies after one tool command.

~~~bash
REPO=/Users/shane/Documents/GitReBase/AestheticcNext
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-<current-claude-pid>"
EVIDENCE_DIR="$HOME/.claude/evidence/land-batch/$RUN_ID"
mkdir -p "$EVIDENCE_DIR"
git -C "$REPO" fetch origin main --quiet
python3 ~/.claude/skills/land-batch/bin/land-state.py wait \
  --run-id "$RUN_ID" --mode "$MODE" --claude-pid "$CLAUDE_PID" \
  --pid-start-time "$CLAUDE_PID_START" --session-id "$SESSION_ID" \
  --agent-view-name "$AGENT_VIEW_NAME" --stage preflight \
  --evidence-dir "$EVIDENCE_DIR" --repo "$REPO" > "$EVIDENCE_DIR/admission.json"
~~~

When locked, wait registers one ticket then polls forever with a randomized
120–180-second delay. It may acquire only when the lock is free and its ticket
is oldest surviving; a mkdir race re-enters the loop. There is no timeout.
Update heartbeat at every step boundary:

~~~bash
python3 ~/.claude/skills/land-batch/bin/land-state.py heartbeat \
  --run-id "$RUN_ID" --stage "<step>" --integration-branch "$INT" \
  --scratch-path "$SCRATCH" --evidence-dir "$EVIDENCE_DIR"
~~~

Liveness uses kill -0 plus ps lstart, matching sessions.py and defeating PID
reuse. A poller removes a dead holder only after recording full forensics
(run/stage/heartbeat/scratch/branch state/cleanup) in takeovers.jsonl and
ledger.json. It safely cleans a known orphan land-batch scratch/branch when
repo was supplied. Dead tickets are collected using the same rule. Do not
release while waiting for selection, testing, QA, or deploy.

~~~bash
python3 ~/.claude/skills/land-batch/bin/land-state.py release --run-id "$RUN_ID"
~~~

Release only during final cleanup. --status never takes a lock.

## LAND mode

### Step 1 — Discovery and unchanged curation

~~~bash
bash ~/.claude/skills/land-batch/bin/discover.sh "$REPO" > "$EVIDENCE_DIR/discover.json"
~~~

Discovery is read-only and preserves its current candidate contract and complete
WILL LAND / HELD / BLOCKED / SKIP / SIBLING CONFLICTS rendering. It now excludes
all land-batch/* branches, preventing in-progress scratch branches from becoming
candidates, and emits top-level lock_queue state for dashboards.

Keep the multiSelect curation gate exactly:

- Offer auto_land=true candidates only, with recognisable labels and honest
  one-line safety/caveat descriptions.
- At most four options/question and four questions/call. Chunk >4; above 16,
  offer the highest-confidence 16 and visibly log every remainder.
- Print HELD/BLOCKED/SKIP beside the selector. Deselection is held by Shane.
- Sensitive candidates require separate explicit opt-in; choose at most one of a
  sibling-conflict pair; offer only safe clean ahead=0/retired bulk pruning.

Dry-run stops after that plan without admission/mutation.

### Step 2 — Per-feature merge, cheap scoped gate, direct main push

Create fresh scratch off origin/main on local branch land-batch/RUN_ID. Configure
the existing .beads merge=ours driver in common git info attributes. For every
selected branch, merge sequentially:

~~~bash
git merge --no-ff --no-verify "$BR" -m "land: $BR (batch $RUN_ID via /land-batch)"
MERGE_SHA="$(git rev-parse HEAD)"
~~~

A merge conflict means abort and hold it. For each merge, run typecheck, lint,
and Jest scoped to **that feature's changed files**. Derive files from the merge
and use package-supported scoped typecheck/lint plus Jest --findRelatedTests
<files>, or Jest --changedSince=origin/main where that is the narrower accurate
form. Never run full Jest in LAND. Save separate command, raw output, and numeric
exit-code artifacts under scoped-gate-<branch>-*.

Any red/missing/malformed artifact means:

~~~bash
git revert -m 1 "$MERGE_SHA" --no-edit
~~~

Kick that feature back and continue. For a green feature:

~~~bash
git push origin HEAD:main
~~~

If push is rejected because main moved, fetch, rebase --rebase-merges origin/main,
re-gate, retry. Abort/hold rebase conflicts. Never force-push. The no-ff merge
message ties every land to RUN_ID.

### Step 3 — Harvest QA at land time

Immediately after every green push, move the former three-source QA assembly
here, rather than delaying it until SHIP:

1. candidate.session.last_assistant_tail and optional marker known_issues,
   planning input only;
2. bd show bead acceptance criteria;
3. git show --stat MERGE_SHA to derive a smoke check when needed.

Write harvested-checks.json and harvested-checks.md in evidence, with one feature
entry containing branch, merge_sha, landed_at, sources, concrete checks, and
per-feature checklist markdown. These plans are not QA evidence. Append them:

~~~bash
python3 ~/.claude/skills/land-batch/bin/land-state.py ledger-append \
  --record "$EVIDENCE_DIR/harvested-checks.json" \
  --checklist "$EVIDENCE_DIR/harvested-checks.md"
~~~

This atomically updates ledger.json and pending-qa.md before tails can be pruned.
LAND never touches staging. Clean scratch/branch after ledger writes, then release.

## SHIP mode

SHIP enters the same FIFO queue and cannot pass active/earlier LAND work. Once
admitted, it reads ledger.pending so it includes everything landed before its
turn. Empty ledger means report/release; do not deploy.

### Step 4 — Fresh scratch and detached full Jest

Create fresh scratch at current origin/main. Run typecheck/lint with full
evidence. Run full Jest **once**, detached so V8 heap OOM kills the subprocess,
not Claude's Node process:

~~~bash
FULL_LOG="$EVIDENCE_DIR/full-jest.log"
FULL_EXIT="$EVIDENCE_DIR/full-jest.exit"
nohup bash -o pipefail -c '
  npm run test
  code=$?
  printf "%s\n" "$code" > "$1"
  exit "$code"
' bash "$FULL_EXIT" > "$FULL_LOG" 2>&1 </dev/null &
printf '%s\n' "$!" > "$EVIDENCE_DIR/full-jest.pid"
~~~

Poll log and exit file; do not foreground-wait. Save command, PID, poll evidence,
full log, and parsed exit. Missing/malformed exit, dead child without zero, or
nonzero is red. Do not deploy. This is the one full Jest run.

### Step 5 — One staging deploy, then full ledger QA

Submit scratch with cloudbuild-staging.yaml directly. If permission is blocked,
surface it and obtain explicit in-chat authorization; never edit settings to
self-authorize. For each attempt capture command/output/exit-code and prove:

1. Cloud Build SUCCESS from global, not an incorrect regional build query.
2. Structured describe of aestheticc-next-staging in europe-west2 proves
   status.traffic[0].revisionName == latestCreatedRevisionName.

Record 100% staging revision as last-known-good. If no shift, diagnose pinned
staging traffic (surface its deliberate shared state before unpinning) or startup
failure with Cloud Logging evidence. Never QA stale traffic.

Execute every pending ledger row, not merely this run's features. Use login-as-qa
and qa-impersonate; re-check session.businessId and cache-bust because parallel
sessions can stomp shared impersonation state. Every /browse QA row must record
exact URL, expected/observed result, severity, and readable evidence artifact.
Write EVIDENCE_DIR/qa-checklist.md. No cited artifact is FAILED.

Severity remains locked:

- **p0:** data loss, security/auth/payment failure, or core-flow outage.
- **p1:** core path broken; wrong price/legal copy, responsive or accessibility
  failure.
- **p2:** notable/wrong or partial behavior, including wrong trust copy.
- **p3:** genuinely cosmetic only; price/legal/trust, accessibility, responsive
  failures are never p3.

### Step 6 — Codex fix loop and revert

Keep three iterations; stop immediately on p0. For p1/p2 run Codex directly in a
**fresh scratch off current origin/main**, scoped to offending files; surface any
sensitive path. Commit atomically, then direct ff-push to main with the same
fetch/rebase/re-gate/retry, never-force mechanics as LAND. Redeploy staging with
both gates and re-run affected QA.

If a feature cannot be clean, verify MERGE_SHA^2 exists (real --no-ff merge),
then on fresh current-origin/main scratch:

~~~bash
git revert -m 1 "$MERGE_SHA" --no-edit
git push origin HEAD:main
python3 ~/.claude/skills/land-batch/bin/land-state.py ledger-remove --merge-sha "$MERGE_SHA"
~~~

Apply the same rejected-push/rebase/re-gate rules. Ledger removal excludes the
reverted feature from later SHIP runs. If p0 or residual p0–p2 after three loops,
redeploy last-known-good staging, stop, surface residuals/revert offers, and
never open prod gate. p3 documents only.

### Step 7 — One attended prod gate; explicit traffic pin

Only after clean staging, complete evidence, and post-19:30 window, print report
and ask the one AskUserQuestion to deploy PROD. On no/no response leave ledger
pending and release.

On yes submit fresh current-main scratch with cloudbuild.yaml, prove global build
SUCCESS, and prove new production revision Ready. Then explicitly pin traffic:

~~~bash
gcloud run services describe aestheticc-next --region europe-west2 --format=json \
  > "$EVIDENCE_DIR/prod-service-before-traffic.json"
NEW_REV="$(jq -r '.status.latestCreatedRevisionName' "$EVIDENCE_DIR/prod-service-before-traffic.json")"
test -n "$NEW_REV" && test "$NEW_REV" != null
gcloud run revisions describe "$NEW_REV" --region europe-west2 --format=json \
  > "$EVIDENCE_DIR/prod-new-revision.json"
jq -e '.status.conditions[] | select(.type == "Ready" and .status == "True")' \
  "$EVIDENCE_DIR/prod-new-revision.json" > /dev/null
# NEW_REV is aestheticc-next-<NEW>; never use --to-latest.
gcloud run services update-traffic aestheticc-next --region europe-west2 \
  --to-revisions "$NEW_REV=100" > "$EVIDENCE_DIR/prod-traffic-update.log" 2>&1
gcloud run services describe aestheticc-next --region europe-west2 --format=json \
  > "$EVIDENCE_DIR/prod-service-after-traffic.json"
~~~

The readiness artifact must prove aestheticc-next-<NEW> Ready=True. Final status
must prove status.traffic[0].revisionName == NEW_REV. Never infer traffic from
build output. Run cold paths: aestheti.cc/api/health, /auth/login -> 200, and
/dashboard -> 307.

On prod success, archive pre-reset state then reset ledger with prod SHA:

~~~bash
PROD_SHA="$(git -C "$SCRATCH" rev-parse HEAD)"
python3 ~/.claude/skills/land-batch/bin/land-state.py archive-ledger \
  --evidence-dir "$EVIDENCE_DIR" --prod-sha "$PROD_SHA"
~~~

This archives pending-qa.md and ledger.json in evidence before reset.

## Status and final report

/status runs:

~~~bash
bash ~/.claude/skills/land-batch/bin/lock-status.sh
~~~

It never locks. It shows holder liveness/run/mode/stage/heartbeat age, complete
FIFO queue and liveness, plus every pending feature since prod_sha. Discovery's
top-level lock_queue exposes the same state.

Never rename, close, or kill Agent View tabs. Report existing tab names mapped to
branch/bead, landed/held/conflict/reverted items, scratch cleanup, ledger count,
staging/prod revision or NOT touched, cited evidence paths, and exact next action.

Run tests with:
~/.claude/skills/land-batch/.venv/bin/python -m pytest ~/.claude/skills/land-batch/tests/ -q
