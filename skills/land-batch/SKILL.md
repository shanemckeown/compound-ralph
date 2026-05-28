---
name: land-batch
version: 0.2.1
description: |
  Meta-orchestrator for the parallel-worktree mess. Auto-discovers FINISHED
  worktrees across all roots (Agent View, Conductor, ~/.worktrees) using each
  worktree's own .claude/land-ready.json marker + live Claude session state,
  lands the finished set onto a throwaway INTEGRATION BRANCH (never main),
  green-gates + deploys it ONCE to staging, QAs every feature on that single
  deploy, auto-fixes p0–p2 via codex until clean, then promotes to prod behind
  ONE explicit human gate. Use when Shane says "merge, deploy and qa", "batch
  land", "land the finished worktrees", "reconcile worktrees", or "land-batch".
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

**What it replaces:** the cumbersome loop of rebasing/merging/deploying/QA-ing
each Agent View / Conductor worktree one at a time. This picks up the whole set
of *finished* worktrees, reconciles them together on an integration branch,
deploys **once**, QAs **all of them on that single staging deploy**, fixes what's
broken, and promotes to prod behind one confirm gate.

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
6. **Sensitive paths are never auto-landed.** Branches touching the paths above
   are held for explicit opt-in, even in auto mode. Also exclude any candidate
   whose merge *depends on* a held sensitive branch.
7. **Marker is canonical; chat tail only vetoes.** A worktree auto-lands only if
   it has `.claude/land-ready.json` with `ready:true` (+ clean + ahead≥1). The
   transcript tail can *veto* (open question / blocker) but never *approve*.
8. **Branch hygiene (CLAUDE.md AGENT VIEW rule):** before branching, `git fetch
   origin main`; if origin/main moved, re-run discovery.
9. **Always verify traffic shift, not just build SUCCESS** (`feedback_verify_cloud_run_traffic`).
   Cloud Run silently rolls back failed-startup containers.

---

## Step 0 — Preflight

```bash
REPO=/Users/shane/Documents/GitReBase/AestheticcNext
[ -n "$SPAWNED_SESSION$OPENCLAW_SESSION" ] && { echo "REFUSE: land-batch is interactive-only."; exit 0; }
cd "$REPO"
git branch --show-current        # expect: main
git fetch origin main --quiet
git log --oneline origin/main..HEAD | head   # local ahead of origin?
git log --oneline HEAD..origin/main | head   # origin ahead of local?
git status --porcelain | head                # main worktree must be clean
```

- If main is **not** clean or **not** on `main`, STOP and surface — do not
  stash/discard. Sibling sessions may have left state.
- If origin/main is ahead, `git pull --ff-only` first (or surface if it won't
  fast-forward).

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
  ignores junk dirt — build output, lockfiles, beads churn, `lib/manifests/`,
  `PLAN.md` — and is what the **finish gate uses**, since uncommitted junk
  doesn't travel when a branch is merged.)
  - `has_marker` — `.claude/land-ready.json` with `ready:true` present
  - `land_ready` — the parsed marker object (or null)
  - `session` — live Claude session joined to this worktree (or null); has
    `active, status, idle_min, sentinel, has_open_loop, last_assistant_tail`
  - `auto_land` — **the deterministic finish verdict** (bool)
  - `finish_signal` ∈ `marker | held-sensitive | blocked-session-active |
    blocked-open-loop | blocked-not-clean-or-no-commits | legacy-low-confidence |
    no-marker-no-signal | skip-merged | skip-conflict | skip-retired`
- `auto_land_count`, `active_sessions[]`, `sibling_conflicts[]`.

The discovery engine is strictly read-only (`merge-tree` conflict detection, no
working-tree mutation) — running it just to *see* the queue is a safe dashboard.

If discovery errors or returns zero candidates, report and stop.

## Step 2 — Autonomous finish-judgment (NO confirm gate)

> ⚠️ **Reality as of first live run (2026-05-28):** nothing yet writes the
> `.claude/land-ready.json` marker, so `auto_land` is **always 0** — the
> autonomous "WILL LAND" path never fires, and the **opt-in surfacing below
> carries every run.** Treat opt-in as the primary UX until a marker *producer*
> is built. This is open design decision #1 in `REVIEW_4.7.md` (for the 4.8 pass).

The finished set = `candidates` where `auto_land == true`. This is deterministic
(computed in `discover.sh` per the marker gate) — **do not re-litigate it with
prose.** Render a scannable table for the record, then proceed:

```
WILL LAND (auto_land=true, marker present)
  ✓ comms-editability        LUCY-…   PR#…   +4/-0   marker ✓   idle 22m
SURFACED — finished but no marker (legacy, opt-in only this run)
  ◐ goal/aestheticnext-cjf4p  —       PR#329  +3/-0   result: sentinel, clean   ← opt in?
HELD — touches sensitive paths (explicit opt-in required)
  ⚠ fix/glp1-migration-bp6sr  —       —       drizzle/migrations/
BLOCKED — still active / open loop
  ⏳ goal/…-x8ftu   session busy        |  ⏳ goal/…-1ms7s  dirty tree
SKIP — already merged (ahead=0) / conflict / retired
  ✗ <branch> …    (offer bulk prune of the ahead=0 + retired set)
SIBLING CONFLICTS (can't both land this round)
  ⚡ branch-a ↔ branch-b : app/shared.tsx
```

- `auto_land=true` items proceed **without a confirm gate** (Shane's locked
  decision: fully autonomous finish-judgment).
- `legacy-low-confidence` and `held-sensitive` items are **not** auto-landed.
  Surface them; only include on explicit Shane opt-in (use AskUserQuestion *only*
  for these opt-ins, not for the auto set).
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

`main` is never touched here. Land everything onto a throwaway branch:

```bash
TS=$(date +%Y%m%d-%H%M%S)
INT="land-batch/$TS"
git fetch origin main --quiet
git branch "$INT" origin/main
git checkout "$INT"

for BR in $FINISHED_BRANCHES; do
  if ! git merge-tree --write-tree HEAD "$BR" >/dev/null 2>&1; then
    echo "SKIP $BR — conflicts with integration branch after prior landings"; SKIPPED+=("$BR"); continue
  fi
  git merge --no-ff "$BR" -m "land: $BR (batch via /land-batch)" || { git merge --abort; SKIPPED+=("$BR"); continue; }
  echo "$BR $(git rev-parse HEAD)"   # record merge SHA per feature → per-feature revert later
done
```

- `--no-ff` always → each feature = one revertable merge commit.
- Re-check each branch against the **evolving integration branch** (a prior
  landing may have introduced a conflict).
- Any conflict → `git merge --abort`, add to SKIPPED, keep going. Never resolve
  by hand. Record each landed feature's merge SHA.

## Step 4 — Green gate on the integration branch

```bash
npm run typecheck && npm run lint && npm run test
```

- All three green → proceed. (Warm tsc first if husky cold-cache bites —
  `feedback_husky_tsc_cold_cache`.)
- Red → revert the most-recent merge commit on `$INT`, re-run, repeat until
  green. Surface which feature broke the build; land the green subset, kick the
  red one back to its worktree. Never deploy red.

## Step 5 — ONE staging deploy FROM the integration branch

`cloudbuild-staging.yaml` builds the **local working tree** it's submitted with
(trailing `.` = upload context) and tags the image `:staging` — it is **not**
pinned to `main`. So deploying the integration branch is just: be checked out on
`$INT`, then submit. **No yaml change needed.** Run the build directly — do not
trust a deploy sub-agent (`feedback_never_use_deploy_agents`,
`feedback_deploy_agents_lie`):

```bash
git checkout "$INT"   # ensure the integration branch is the build context
gcloud builds submit --config cloudbuild-staging.yaml --project aestheticc .
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

1. Build ran: `gcloud builds list --region europe-west1 --limit 3` shows SUCCESS
   for *this* build. **Builds run in `europe-west1`** (default region = global =
   empty — always pass `--region europe-west1`). Note the regions differ: builds
   are `europe-west1`, the Cloud Run *services* are `europe-west2`.
2. **Traffic shifted:** `gcloud run services describe aestheticc-next-staging
   --region europe-west2 --format='value(status.traffic)'` (or the structured
   form) → `traffic[0].revisionName == latestCreatedRevisionName`. SUCCESS ≠
   live; Cloud Run silently rolls back failed-startup containers.

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
2. Per landed feature, pull acceptance criteria: `bd show <bead_id>` (or derive a
   smoke check from `git show --stat <merge-sha>` when there's no bead).
3. Assemble ONE checklist, run each feature's checks via `/browse` against
   staging. Capture evidence (screenshot / state assertion) per feature.

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

1. Classify every finding p0–p3; document with evidence, keyed to the feature's
   merge SHA (so any feature stays individually revertable).
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

## Step 8 — Prod promotion (behind ONE gate)

Only when staging QA is clean (zero open p0–p2):

1. Print the final report (landed features, fixes applied, staging revision
   serving 100%, integration branch name).
2. **AskUserQuestion: "Deploy this batch to PROD?"** — the only interactive gate.
   (Respect deploy windows / `feedback_no_self_deploy_staging` — if outside the
   window, say so and offer to hold.)
3. On **yes**:
   ```bash
   git checkout main && git merge --ff-only "$INT"   # main moves only now
   git push origin main
   gcloud builds submit --config cloudbuild.yaml --project aestheticc .   # prod config
   ```
   then the **same two verifications** — build SUCCESS in **`europe-west1`**, and
   traffic shifted on the **prod service `aestheticc-next` in `europe-west2`**
   (`traffic[0].revisionName == latestCreatedRevisionName`). SUCCESS ≠ live. Prod
   traffic was `latestRevision: true` (not pinned) as of 2026-05-28, so it
   auto-shifts — but if it ever doesn't, apply the same pin-diagnosis as Step 5.
   `gcloud builds submit --config cloudbuild.yaml .` uploads the working tree, so
   be on `main` (post-ff) when you submit. Prod cold path: `aestheti.cc/api/health`,
   `/auth/login` → 200, `/dashboard` → 307 (auth redirect).
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

- **Session→worktree join is best-effort.** `/goal` bg jobs run with
  `cwd=$HOME`, not the worktree, so the cwd join misses them; there's a
  secondary bead-short-id-in-session-name fallback, but it only fires when the
  branch name yields a parseable bead id (the lowercase `goal/aestheticnext-*`
  branches don't). **The real finish guards are the marker + clean-tree +
  ahead≥1 checks** — the session-active flag is an *additional* veto, not the
  primary gate. Nothing auto-lands without a marker regardless.
- **Tests:** `bin/sessions.py` is covered by `tests/test_sessions.py`. pytest
  isn't on system python; run via the skill-local venv:
  `~/.claude/skills/land-batch/.venv/bin/python -m pytest ~/.claude/skills/land-batch/tests/ -q`.
  `sessions.py` itself is pure stdlib, so `discover.sh` invokes it with plain
  `python3`.
- Retired/stale patterns live in `~/.claude/skills/land-batch/retired.txt` — add
  branch substrings or bead IDs there to keep dead moonshots out of the batch.
- The **finish marker** a worktree's own session should drop when done:
  `.claude/land-ready.json` = `{branch, base_sha, tests_run, known_issues,
  touched_paths, ready:true}`.
