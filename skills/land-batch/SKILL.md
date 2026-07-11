---
name: land-batch
version: 0.2.2
description: |
  Meta-orchestrator for the parallel-worktree mess. Auto-discovers FINISHED
  worktrees across all roots (Agent View, Conductor, ~/.worktrees) from live
  git + Claude session state (effectively_clean + ahead≥1 + non-active session
  + premise check), lands the finished set onto a throwaway INTEGRATION BRANCH
  (never main), green-gates + deploys it ONCE to staging, QAs every feature on
  that single deploy, auto-fixes p0–p2 via codex until clean, then promotes to
  prod behind ONE explicit human gate (parked until the post-19:30 deploy
  window). Use when Shane says "merge, deploy and qa", "batch land", "land the
  finished worktrees", "reconcile worktrees", or "land-batch".
triggers:
  - merge deploy and qa
  - merge, deploy and qa
  - batch land
  - land the finished worktrees
  - reconcile worktrees
  - land-batch
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
---

# /land-batch — Batch-land finished worktrees, one deploy, one QA, gated prod

Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly.

**What it replaces:** the cumbersome loop of rebasing/merging/deploying/QA-ing
each Agent View / Conductor worktree one at a time. This picks up the whole set
of *finished* worktrees, reconciles them together on an integration branch,
deploys **once**, QAs **all of them on that single staging deploy**, fixes what's
broken, and promotes to prod behind one confirm gate.

**Two phases (timing).** Phase 1 — discover → merge → staging deploy → QA → fix
— is window-agnostic and runs *anytime* (staging is unrestricted). Phase 2 — the
single prod promotion (Step 8) — **parks until the post-19:30 BST deploy window**
when clinics are off the app, and resumes there. A typical run does all of Phase
1 in the afternoon and the prod gate in the evening.

**What "finished" means.** Finish-detection is *heuristic*, not marker-gated: a
worktree is finished when its session is non-active + the tree is clean + it is
ahead≥1 + it passes the premise check (and is not sensitive/conflicting). The
`.claude/land-ready.json` marker, when present, is an *optional* stronger signal
and the carrier of the `bead_id` for close-mapping — it is **not** required, and
its absence never blocks the auto path. (Nothing writes it yet; a producer is an
optional follow-on.)

> **Spec:** `ENG_REVIEW.md` (locked decisions) wins over `UPGRADE_PLAN.md` where
> they differ. Read it if anything here is ambiguous.

## 🔴 HARD GUARDRAILS — read before any step

1. **INTERACTIVE ONLY. NEVER HEADLESS.** This skill merges code and fires
   deploys. If `SPAWNED_SESSION="true"`, `OPENCLAW_SESSION` is set, or you were
   invoked via `claude -p` / a cron / a background agent → **REFUSE and exit.**
   Print: "land-batch is interactive-only — it merges and deploys. Run it in a
   live session with Shane." No exceptions.
   (Memory: `feedback_no_self_deploy_staging`, `feedback_never_use_deploy_agents`.)
2. **Source of truth is `/Users/shane/Documents/GitReBase/AestheticcNext`.** All
   work happens there. Never operate on a worktree's own copy of main.
3. **The loop never touches `main`.** All landing, green-gating, deploy, QA, and
   codex-fixes happen on a throwaway **integration branch** `land-batch/<ts>`.
   `main` moves **only** at the prod gate (Step 8), and only on explicit
   approval. Reject = `git branch -D` the integration branch; main never moved.
4. **Prod behind ONE explicit gate; never headless.** Staging deploy + QA are
   automatic on the integration branch. Production requires the Step-8
   `AskUserQuestion` confirm. Direct verified `gcloud` deploy only — never trust
   a deploy sub-agent's narration (`feedback_deploy_agents_lie`).
5. **Never auto-resolve a merge conflict.** Conflict → skip that branch, report,
   continue. Never touch a conflict in `lib/db/ drizzle/migrations/ lib/stripe/
   lib/auth/ lib/payments/ pages/api/auth|admin|webhooks/ lib/email/templates/`.
   (`.beads/` is the one exception-that-isn't: it is excluded from the merge via a
   local `merge=ours` driver in Step 3, so it never *produces* a conflict — its
   truth lives in Dolt, the JSONL is regenerated churn. Not an auto-resolve; the
   conflict simply never arises.)
6. **Sensitive paths are never auto-landed.** Branches touching the paths above
   are held for explicit opt-in, even in auto mode. Also exclude any candidate
   whose merge *depends on* a held sensitive branch.
7. **Heuristic finish-gate; marker is an optional booster, not the gate.** A
   worktree auto-lands when its session is non-active + tree `effectively_clean` +
   ahead≥1 + passes the premise check + not sensitive + not conflicting. The
   `.claude/land-ready.json` marker (if present) raises confidence and supplies
   the `bead_id` for close-mapping, but its absence never blocks. The transcript
   tail can *veto* (open question / blocker) but never *approve*.
8. **Branch hygiene (CLAUDE.md AGENT VIEW rule):** before branching, `git fetch
   origin main`; if origin/main moved, re-run discovery.
9. **Always verify traffic shift, not just build SUCCESS** (`feedback_verify_cloud_run_traffic`).
   Cloud Run silently rolls back failed-startup containers.

---

## Step 0 — Preflight

```bash
REPO=/Users/shane/Documents/GitReBase/AestheticcNext
[ -n "$SPAWNED_SESSION$OPENCLAW_SESSION" ] && { echo "REFUSE: land-batch is interactive-only."; exit 0; }
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
EVIDENCE_DIR="$HOME/.claude/evidence/land-batch/$RUN_ID"
mkdir -p "$EVIDENCE_DIR"
git -C "$REPO" fetch origin main --quiet
git -C "$REPO" rev-parse --short origin/main   # the base everything lands on
```

- **We never operate in the trunk's working tree.** It sits on whatever branch
  the last sibling session left it on, with untracked scripts + `.beads/` churn —
  requiring it to be "clean on main" just blocks every run (and trying to
  `git checkout` in it aborts on dirty `.beads`, which silently merged onto the
  wrong branch in a manual run 2026-06-02). So Step 0 does **not** read the
  trunk's branch or status. The landing happens in a **dedicated scratch
  worktree** (Step 3) cut fresh from `origin/main`; the trunk's checkout state is
  irrelevant and never touched.
- The only precondition is a fetchable `origin/main`. Everything lands on top of
  that exact SHA.

## Step 1 — Discover candidates (read-only, safe)

```bash
OUT="${CLAUDE_JOB_DIR:-$(mktemp -d)}/land-batch-discover.json"   # never a fixed /tmp path — parallel jobs clobber it
bash ~/.claude/skills/land-batch/bin/discover.sh "$REPO" > "$OUT"
echo "$OUT"
```

Read `$OUT`. The contract:

- `candidates[]` each with `branch, bead_id, pr_number, root, ahead, behind,
  clean, effectively_clean, age_days, conflicts_with_base, conflicting_files,
  touches_sensitive, sensitive_paths, retired, recommendation` **plus the v0.2
  finish fields:** (note: `clean` is raw `git status`; `effectively_clean`
  ignores only genuinely non-travelling churn — build output, lockfiles, `.beads/`
  churn — and is what the **finish gate uses**. As of v0.2.2 `lib/manifests/` and
  `PLAN.md` are NO LONGER junk: they can be real uncommitted work, and a worktree
  with real uncommitted edits must NOT auto-land, since they would not travel in
  the merge.)
  - `has_marker` — `.claude/land-ready.json` with `ready:true` present (optional
    booster; NOT required)
  - `land_ready` — the parsed marker object (or null); carries `bead_id` + summary
  - `session` — live Claude session joined to this worktree (or null); has
    `active, status, idle_min, sentinel, has_open_loop, last_assistant_tail`
  - `live_session` (v0.3) — **true if a live `claude` process is cwd'd inside
    this worktree**, detected by scanning processes directly (ps + lsof), NOT by
    the `~/.claude/sessions/*.json` cwd-join that `session` uses. This is the fix
    for the **Agent View blind spot**: harness-isolated Agent View sessions don't
    register a worktree cwd in the session files, so the old join always returned
    `session=null` and every worktree looked finished. When `live_session` is
    true the candidate is forced `session.active=true` → `blocked-session-active`.
    Strictly additive: it can only BLOCK a worktree, never approve one.
  - `auto_land` — **the deterministic heuristic finish verdict** (bool):
    `effectively_clean` + ahead≥1 + non-active session + premise-ok +
    not-sensitive + not-conflicting + not-retired
  - `finish_signal` ∈ `finished | held-sensitive | blocked-session-active |
    blocked-open-loop | blocked-not-clean-or-no-commits | skip-merged |
    skip-conflict | skip-retired`
- `auto_land_count`, `active_sessions[]`, `sibling_conflicts[]`.

The discovery engine is strictly read-only (`merge-tree` conflict detection, no
working-tree mutation) — running it just to *see* the queue is a safe dashboard.

If discovery errors or returns zero candidates, report and stop.

## Step 2 — Finish-judgment + batch selection (autonomous up to staging; ONE prod gate)

> **Design (resolved 2026-05-28, 4.8 pass).** Auto-proceed is gated on the
> **heuristics** — non-active session + `effectively_clean` + ahead≥1 + premise
> check + not sensitive + not conflicting — **not** on a marker. The old
> marker-gate made `auto_land` always 0 (nothing writes the marker), so nothing
> auto-proceeded; basing it on heuristics is what makes the autonomous-up-to-
> staging path actually fire. The `.claude/land-ready.json` marker is now an
> optional confidence booster + `bead_id` carrier (Guardrail #7). **`main` never
> moves here** — only at the Step 8 prod gate, which parks until the evening
> window. So "autonomous" = autonomous *through staging + QA*; prod is always
> your one explicit gate.
>
> **Amended 2026-06-02:** there is now also a *selection* gate before landing —
> the eligible set is shown as an AskUserQuestion multiSelect so Shane curates
> which finished Claudes ship (he may know a clean-looking tab isn't ready). So
> the flow is: deterministic eligibility → **Shane multi-selects** → land subset
> → staging → QA → ONE prod gate. Two human touchpoints (select, prod-yes),
> everything between them automatic.

The finished set = `candidates` where `auto_land == true`. This is deterministic
(computed in `discover.sh` from the heuristic gate) — **do not re-litigate it
with prose.** Render a scannable table for the record, then proceed:

```
WILL LAND (auto_land=true — finished via heuristic gate)
  ✓ comms-editability        LUCY-…   PR#…   +4/-0   clean   idle 22m   (marker ✓ optional)
HELD — touches sensitive paths (explicit opt-in required)
  ⚠ fix/glp1-migration-bp6sr  —       —       drizzle/migrations/
BLOCKED — still active / open loop / dirty
  ⏳ goal/…-x8ftu   session busy        |  ⏳ goal/…-1ms7s  dirty tree
SKIP — already merged (ahead=0) / conflict / retired
  ✗ <branch> …    (offer bulk prune of the ahead=0 + retired set)
SIBLING CONFLICTS (can't both land this round)
  ⚡ branch-a ↔ branch-b : app/shared.tsx
```

- **Present `auto_land=true` items as a SELECTABLE list — do not auto-proceed.**
  (Decision 2026-06-02, Shane: he wants to curate which finished Claudes land —
  he may know a tab like "Connect WisePad 3" isn't actually ready even when its
  worktree looks clean.) The heuristic gate decides what's *eligible*; Shane
  decides what *ships* from that eligible set via an **AskUserQuestion
  `multiSelect: true`**. One option per eligible candidate:
    - `label`: the shortest recognisable handle — bead_id if present, else the
      branch's distinctive tail (`helena violet theme`, `comms toggle epny2`).
    - `description`: a ONE-LINE why-safe, generated from discovery fields:
      `clean · 0 behind · no live Claude · no sensitive · 2 commits/4 files`.
      Be honest about caveats in the same line so the choice is informed:
      `clean but ~240 behind — 3-way safe, green gate catches staleness`, or
      `mobile-only build fixes — lands to repo but won't reach devices via web`,
      or `touches lib/auth+stripe — extra review on staging`.
    Land **only** the selected subset; deselected = "held by Shane", not landed.
  - **AskUserQuestion caps at 4 options/question, 4 questions/call (16 max).**
    >4 eligible → chunk across multiple questions in ONE call ("Batch A — land
    which?", "Batch B — …"). >16 → offer the 16 highest-confidence, `log()` the
    remainder for a follow-up round. **Never silently drop a candidate.**
  - Always PRINT the full HELD / BLOCKED / SKIP table beside the selector so
    Shane can see WHY an expected tab isn't offered (active Claude, sensitive
    path, conflict, already-merged). The selector lists only eligible items; the
    table explains every exclusion. (`main` untouched until Step 8.)
- `held-sensitive` items are **not** auto-landed. Surface them; include only on
  explicit Shane opt-in (use AskUserQuestion *only* for these opt-ins, not for
  the auto set). A missing marker no longer downgrades anything.
- For sibling-conflict pairs, include at most one (the loser rebases next round).
- Offer bulk prune of `skip-merged` (ahead=0) + `skip-retired` per
  `feedback_worktree_cleanup` (only reap ahead=0 + clean, `--force`, re-verify
  keepers survived). This is usually the highest-volume win.

### 🅳 --dry-run (default on for the first few runs)

If invoked `/land-batch --dry-run` (**or this is one of the skill's first live
runs**): print the full plan — the finished set, the integration-branch name,
and the deploy → QA → gate sequence that *would* run — and **STOP. Mutate
nothing.** Only drop `--dry-run` once the discovery + finish-judgment have been
eyeballed and trusted a few times.

## Step 3 — Create integration branch + merge the finished set

`main` is never touched here. Land everything in a **dedicated scratch worktree**
checked out on a throwaway integration branch — the shared trunk is never
checked out, stashed, or cleaned:

```bash
TS=$(date +%Y%m%d-%H%M%S)
INT="land-batch/$TS"
SCRATCH="${TMPDIR:-/tmp}/land-batch-$TS"            # NOT the trunk working dir
git -C "$REPO" fetch origin main --quiet
git -C "$REPO" worktree add -b "$INT" "$SCRATCH" origin/main   # fresh tree @ origin/main
cd "$SCRATCH"                                       # all merges/build/deploy happen here

# decision #5 — never merge .beads/ (pure churn; truth lives in Dolt). merge=ours
# means .beads/ conflicts never arise (keeps Guardrail #5 absolute). Write the
# attribute to the COMMON git dir so the linked worktree honours it.
git config merge.ours.driver true
ATTR="$(git rev-parse --git-common-dir)/info/attributes"; mkdir -p "$(dirname "$ATTR")"
grep -qxF '.beads/** merge=ours' "$ATTR" 2>/dev/null || echo '.beads/** merge=ours' >> "$ATTR"

for BR in $FINISHED_BRANCHES; do
  # Merge sequentially onto the EVOLVING integration head (a prior landing may
  # introduce a conflict). --no-ff = one revertable merge commit per feature.
  # A 3-way merge preserves main's recent work even for a behind/stale branch;
  # semantic breakage from staleness is caught by the Step 4 green gate, not here.
  if ! git merge --no-ff --no-verify "$BR" -m "land: $BR (batch via /land-batch)"; then
    git merge --abort; echo "SKIP $BR — conflicts with integration head"; SKIPPED+=("$BR"); continue
  fi
  echo "$BR -> $(git rev-parse HEAD)"   # record merge SHA per feature → per-feature revert later
done
```

> **Cleanup contract.** The scratch worktree is removed at the end of the run
> (pass or fail): `git -C "$REPO" worktree remove "$SCRATCH" --force`. On a
> reject/abort also drop the branch: `git -C "$REPO" branch -D "$INT"`. `main`
> never moved, the trunk was never touched.

- `--no-ff` always → each feature = one revertable merge commit.
- Re-check each branch against the **evolving integration branch** (a prior
  landing may have introduced a conflict).
- Any conflict → `git merge --abort`, add to SKIPPED, keep going. Never resolve
  by hand. Record each landed feature's merge SHA.

## Step 4 — Green gate on the integration branch

```bash
GREEN_COMMAND='npm run typecheck && npm run lint && npm run test'
GREEN_ATTEMPT="${GREEN_ATTEMPT:-1}"
printf '%s\n' "$GREEN_COMMAND" > "$EVIDENCE_DIR/green-gate-command-attempt-$GREEN_ATTEMPT.txt"
set +e
{
  printf '$ %s\n\n' "$GREEN_COMMAND"
  bash -o pipefail -c "$GREEN_COMMAND"
} > "$EVIDENCE_DIR/green-gate-output-attempt-$GREEN_ATTEMPT.log" 2>&1
GREEN_EXIT=$?
set -e
printf '%s\n' "$GREEN_EXIT" > "$EVIDENCE_DIR/green-gate-exit-code-attempt-$GREEN_ATTEMPT.txt"
# Increment before a re-run so failed evidence is never overwritten.
GREEN_ATTEMPT=$((GREEN_ATTEMPT + 1))
```

- All three green **and the command/output/exit-code artifacts above exist and
  are cited** → proceed. A missing artifact or a non-zero/malformed exit code is
  red, regardless of narrative. (Warm tsc first if husky cold-cache bites —
  `feedback_husky_tsc_cold_cache`.)
- Red → revert the most-recent merge commit on `$INT`, re-run, repeat until
  green. Surface which feature broke the build; land the green subset, kick the
  red one back to its worktree. Never deploy red.

## Step 5 — ONE staging deploy FROM the integration branch  ·  *(Phase 1, anytime)*

> **Phase 1 (this step + QA + fix, Steps 5–7) is window-agnostic** — staging is
> unrestricted, so run it whenever the batch is ready (typically the afternoon).
> **Phase 2 (the prod gate, Step 8) parks until the post-19:30 BST window.** Do
> all of Phase 1 now; resume Step 8 in the evening.

`cloudbuild-staging.yaml` builds the **local working tree** it's submitted with
(trailing `.` = upload context) and tags the image `:staging` — it is **not**
pinned to `main`. We're already inside `$SCRATCH` (checked out on `$INT` from
Step 3), so the build context is correct with no checkout needed. **No yaml
change needed.** Run the build directly — do not trust a deploy sub-agent
(`feedback_never_use_deploy_agents`, `feedback_deploy_agents_lie`):

```bash
cd "$SCRATCH"   # the scratch worktree IS the integration branch's tree
DEPLOY_ATTEMPT="${DEPLOY_ATTEMPT:-1}"
printf '%s\n' 'gcloud builds submit --config cloudbuild-staging.yaml --project aestheticc .' \
  > "$EVIDENCE_DIR/staging-build-command-attempt-$DEPLOY_ATTEMPT.txt"
set +e
gcloud builds submit --config cloudbuild-staging.yaml --project aestheticc . \
  > "$EVIDENCE_DIR/staging-build-output-attempt-$DEPLOY_ATTEMPT.log" 2>&1
STAGING_BUILD_EXIT=$?
set -e
printf '%s\n' "$STAGING_BUILD_EXIT" > "$EVIDENCE_DIR/staging-build-exit-code-attempt-$DEPLOY_ATTEMPT.txt"
# Keep DEPLOY_ATTEMPT unchanged through the describe checks; increment only
# immediately before a later redeploy.
```

**Authorization (don't assume you can fire it).** The skill **cannot
self-authorize a deploy.** The auto-mode classifier may block the build —
staging-deploy permission has flip-flopped (an `autoMode.allow` directive once
allowed `cloudbuild-staging.yaml`, then was reverted to `$defaults`), and prod is
always Shane's explicit call. If a deploy is blocked, **surface it and get
Shane's explicit in-chat go, then retry** — that explicit go is the
authorization. **Never edit `settings.json` to self-grant** the permission: that
is blocked as self-modification (2026-05-28). Staging deploys during clinic hours
are fine (staging is unrestricted); only prod waits for the deploy window.

Then the **two non-negotiable verifications** (`feedback_verify_cloud_run_traffic`):

1. Build ran: `gcloud builds describe <id> --project aestheticc --format='value(status)'`
   shows SUCCESS for *this* build. **Builds run in `global`, NOT a regional
   location** (verified 2026-07-11 via the build resource's own `name` field:
   `projects/.../locations/global/builds/...`). Passing `--region europe-west1`
   to `gcloud builds list`/`describe` silently returns empty/NOT_FOUND — no
   error, so a region-filtered monitoring loop will poll forever without ever
   seeing the real terminal status. Query with **no `--region` flag** (or
   `--region global` if a region flag is required). Note the Cloud Run
   *services* ARE regional — `europe-west2` — so verification #2 below still
   needs `--region europe-west2`.
2. **Traffic shifted:** `gcloud run services describe aestheticc-next-staging
   --region europe-west2 --format='value(status.traffic)'` (or the structured
   form) → `traffic[0].revisionName == latestCreatedRevisionName`. SUCCESS ≠
   live; Cloud Run silently rolls back failed-startup containers.

Capture both verification commands, their raw output, and their exit codes under
`$EVIDENCE_DIR` using the same attempt number (for example
`build-describe-attempt-N.json`, `build-describe-exit-code-attempt-N.txt`,
`staging-service-describe-attempt-N.json`, and
`staging-service-describe-exit-code-attempt-N.txt`). Do not proceed to QA unless the build
submit, build describe, and service describe exit codes are all `0` and the raw
describe artifacts prove SUCCESS plus the traffic shift. A claimed deploy with
no cited describe artifacts is not deployed.

Record the staging revision serving 100% as **last-known-good** before QA.

**If traffic did NOT shift** (`traffic[0] != latestCreated`; the new revision
shows `Retired` with no app logs), the deploy silently no-op'd. Diagnose the
cause before QA — the two seen in the wild:

1. **Pinned traffic** (hit 2026-05-28: staging was pinned to an 11h-old revision,
   so every deploy created a revision that got retired without ever serving).
   Check the *spec*, not just status: `gcloud run services describe
   aestheticc-next-staging --region europe-west2 --format='value(spec.traffic)'`.
   If it shows `revisionName: …` instead of `latestRevision: true`, it's
   **pinned**. Remedy: `gcloud run services update-traffic aestheticc-next-staging
   --region europe-west2 --to-latest` (reactivates the latest revision + unpins
   permanently). The pin is **shared state someone set deliberately** — surface
   it to Shane before unpinning.
2. **App startup crash** (container imports but fails its health probe) — there
   WILL be app error logs: `gcloud logging read
   'resource.labels.revision_name=<rev> AND severity>=ERROR' --freshness=20m`.
   Fix the cause; never QA the stale revision.

## Step 6 — Consolidated QA (all features, one deploy)

The payoff: QA every landed feature on the single staging deploy.

1. Auth the QA session: `~/.gstack/bin/login-as-qa <staging-url>` then pin the
   business with `qa-impersonate` (`feedback_qa_clinic_multi_business_getuserbusinessid`
   — parallel sessions stomp the shared impersonation column; re-check
   `session.businessId` before asserting, cache-bust availability).
2. Per landed feature, build its check from THREE sources, best-first:
   - **The session's own conversation (input only, never evidence)** — `candidate.session.last_assistant_tail`
     (the finishing Claude's last words: what it built + any "known issue" it
     flagged) and `candidate.land_ready.known_issues` if a marker exists. This is
     the "QA info from the convo" — the session that did the work tells you what
     to verify and what it already knows is shaky.
   - **The bead** — `bd show <bead_id>` for acceptance criteria.
   - **The diff** — `git show --stat <merge-sha>` to derive a smoke check when
     there's neither a transcript hint nor a bead.
3. Assemble ONE checklist, run each feature's checks via `/browse` against
   staging. Every checklist row must record the exact URL exercised, expected
   result, observed result, and one or more concrete artifact paths under
   `$EVIDENCE_DIR` (captured response/text output, screenshot, console output, or
   command output plus exit code). Store the checklist itself at
   `$EVIDENCE_DIR/qa-checklist.md`.

**Fail-closed checklist gate:** a finishing-session transcript or narrative may
suggest what to test, but it cannot satisfy a checklist item. An item without a
cited, readable evidence artifact is FAILED. Staging QA is clean only when every
landed feature has exercised evidence and zero open p0-p2 findings; otherwise do
not enter the production gate.

### Severity rubric (NOT everything is p3)

- **p0** = data loss / security / auth / payment / total outage of a core flow.
- **p1** = a landed feature's core path is broken; **wrong price, wrong legal
  copy, broken responsive layout, a11y failure**.
- **p2** = notable bug / wrong behavior, feature partially works; **wrong trust
  copy**.
- **p3** = genuinely cosmetic only. **Price/legal/trust copy, a11y, and broken
  responsive are never p3.**

## Step 7 — codex-fix loop (cap 3, stop on any p0)

After QA on the integration branch's staging deploy:

1. Classify every finding p0–p3; document with cited `$EVIDENCE_DIR` artifacts,
   keyed to the feature's merge SHA (so any feature stays individually
   revertable). Transcript claims do not count as evidence.
2. If any **p0–p2** remain:
   - Dispatch the **codex** skill to fix them **on the integration branch**
     (`$INT`), scoped to the offending feature's files. Commit each fix
     atomically (`fix(land-batch-qa): …`). Run codex directly, not via a
     sub-agent (`feedback_deploy_agents_lie`). Never let codex touch sensitive
     paths without surfacing.
   - Redeploy staging from `$INT` (same two-gate traffic verification).
   - Re-run QA for the affected features. Loop.
   - **Hard cap: 3 iterations. Stop immediately on any p0.** If p0–p2 still
     remain after 3 (or a p0 appears), **STOP — redeploy the last-known-good
     staging revision**, do NOT promote to prod, surface the residual list +
     offer per-feature revert (`git revert -m 1 <merge-sha>` on `$INT`).
3. p3 cosmetic: documented only, never blocks prod.

## Step 8 — Prod promotion (behind ONE gate)  ·  *(Phase 2, post-19:30 BST)*

Only when staging QA is clean (zero open p0–p2):

1. Print the final report (landed features, fixes applied, staging revision
   serving 100%, integration branch name) and cite the green-gate command/output/
   exit-code files, deploy describe artifacts, and every feature's QA artifacts.
   If any required path is absent, do not render the production approval gate.
2. **AskUserQuestion: "Deploy this batch to PROD?"** — the only interactive gate.
   **Default to parking until the post-19:30 BST window** (clinics live
   08:00–19:30; `feedback_no_self_deploy_staging` + CLAUDE.md deploy windows). If
   it's before 19:30, say so and hold the green integration branch for the
   evening rather than asking to deploy now (S1 hotfix is the only exception,
   with explicit Shane confirmation).
3. On **yes** — move `main` by fast-forward **push from the scratch worktree**,
   never by checking out `main` in the dirty trunk:
   ```bash
   cd "$SCRATCH"                                 # on $INT = origin/main + the landed merges
   git fetch origin main --quiet
   git push origin "HEAD:main"                   # ff-push the integration head to main
   #  ^ rejected if origin/main moved under us → re-fetch, `git rebase origin/main`,
   #    re-green-gate, retry. NEVER force-push.
   gcloud builds submit --config cloudbuild.yaml --project aestheticc .   # prod, context = $SCRATCH
   ```
   then the **same two verifications** — build SUCCESS in **`global`** (no
   `--region` flag; see Step 5), and traffic shifted on the **prod service
   `aestheticc-next` in `europe-west2`**
   (`traffic[0].revisionName == latestCreatedRevisionName`). SUCCESS ≠ live. Prod
   traffic was `latestRevision: true` (not pinned) as of 2026-05-28, so it
   auto-shifts — but if it ever doesn't, apply the same pin-diagnosis as Step 5.
   The build context is `$SCRATCH` (the just-pushed integration tree). Prod cold
   path: `aestheti.cc/api/health`, `/auth/login` → 200, `/dashboard` → 307 (auth
   redirect). After verifying, run the Step-3 cleanup contract (remove `$SCRATCH`).
4. On **no / no-response**: stop at staging. **`main` untouched.** Either keep
   `$INT` for later (`git checkout main`) or `git branch -D "$INT"` to discard.
   Report what's parked.

## Step 9 — Final report + Agent View close-mapping

After landing + passing (and the prod gate handled), help Shane reap worktrees in
Agent View.

- **Renaming Agent View tabs is NOT possible — do not attempt it.** Verified
  2026-05-28: editing `~/.claude/sessions/<pid>.json` `name` does **not** change
  the Agent View label (it stayed `goal x8ftu bead` after a hand-edit), even for
  a live session. Agent View holds its tab / Completed-list names in its own app
  state, exited sessions have no file at all, and there is no CLI/filesystem hook
  to rename or close a chat (`claude` has no job-close subcommand; no on-disk
  registry). **Never** auto-close or kill a session — Shane closes them himself.
- So the close step **IS** the report mapping: tell Shane which Agent View tabs
  to close **by their existing names** (the Completed list shows name +
  description inline, so he closes them straight from the list without opening
  each). Map each landed branch → the session name Agent View already shows.
- Emit the canonical mapping:

```
LANDED & PASSED: <n> — <branch>, <branch>, …
LANDED, QA FAILED → reverted on integration branch: <branches> (+ bead links)
SURFACED (no marker, not landed): <branches>   |  HELD sensitive: <branches>
SKIPPED — conflict: <branches>   |  retired/stale: <branches>
INTEGRATION BRANCH: land-batch/<ts>  (merged to main: yes/no)
DEPLOY: staging rev <revision> 100% (verified)  |  prod: <deployed rev / NOT touched>
CLOSE THESE AGENT VIEW TABS (by name): "<agent-view-name>" → <branch>, …
  (leave any non-code / "Needs input" tabs open; only the landed-feature tabs)
NEEDS YOU: <sensitive opt-ins / sibling-conflict losers / residual p-items>
```

End by stating plainly what (if anything) needs Shane next.

---

## Notes / limitations

- **Worktree→branch→bead is the reliable join.** Transcript dirs under
  `~/.claude/projects/` encode the worktree path (e.g.
  `-Users-shane--worktrees-AestheticcNext-AestheticcNext-kn5`), and `git -C
  <worktree> branch --show-current` → branch → `bead_id` (parse fixed in v0.2.2 to
  handle `goal/(a|A)estheticcnext-<id>`). **The Agent View tab *name* is NOT on
  disk** (recon 2026-05-28: `~/.claude/sessions/<pid>.json` has no `name` field),
  so close-mapping keys on `bead_id`, which Agent View shows in the tab
  name/description. **The finish guards are `effectively_clean` + ahead≥1 +
  non-active session + premise-check**; the marker, when present, is an additional
  confidence booster + `bead_id` source, never required.
- **Tests:** `bin/sessions.py` is covered by `tests/test_sessions.py`. pytest
  isn't on system python; run via the skill-local venv:
  `~/.claude/skills/land-batch/.venv/bin/python -m pytest ~/.claude/skills/land-batch/tests/ -q`.
  `sessions.py` itself is pure stdlib, so `discover.sh` invokes it with plain
  `python3`.
- Retired/stale patterns live in `~/.claude/skills/land-batch/retired.txt` — add
  branch substrings or bead IDs there to keep dead moonshots out of the batch.
- The **finish marker** (OPTIONAL — nothing writes it yet; a producer is a
  follow-on, see the LUCY marker-producer bead) a worktree's own session *may*
  drop when done: `.claude/land-ready.json` = `{branch, bead_id, summary,
  base_sha, tests_run, known_issues, touched_paths, ready:true}`. Its jobs:
  stronger finish-attestation than the heuristics alone, and carrying `bead_id`
  for clean close-mapping. The skill works fully without it.
