# /land-batch v0.3 — Locked Build Spec (eng-reviewed: Claude + Codex, revised 2026-07-21)

This supersedes the open questions in `UPGRADE_PLAN.md`. Where the two disagree, **this file wins.**

---

## Locked decisions

### D1 — LAND/SHIP split (reversal of the 2026-05-28 integration-only decision)

**Dated reversal, 2026-07-21:** the former D1 said `main` never moved until
staging QA passed. That was correct for a one-run merge → staging → QA → prod
workflow, but it caused concurrent runs to overwrite each other's staging QA.
It is deliberately superseded by a two-mode design:

- **LAND (default)** serialises candidates through a durable lock/FIFO queue,
  preserves the multiSelect curation gate, merges each selected feature with
  `--no-ff`, runs typecheck + lint + Jest scoped to its changed files, and
  fast-forward-pushes that green merge directly to `main`. Each message is
  `land: <branch> (batch <run-id> via /land-batch)`, retaining per-feature
  reversion. LAND never deploys staging or production.
- **SHIP (`--ship`)** takes the same queue ticket, starts fresh from current
  `origin/main`, runs the full Jest suite once in a detached subprocess, deploys
  staging once, and QAs the entire pending cross-run ledger. It then uses the
  existing bounded Codex-fix/revert loop and one attended production gate.

The new non-negotiable invariant is **prod never serves an unshipped SHA**,
enforced by SHIP's full gate, evidenced staging QA, explicit human approval, and
verified production traffic—not “main never moves before QA.” Do not silently
restore the prior integration-branch-only architecture.

### D2 — Heuristic finish gate; marker and chat tail are veto-only metadata

Optional marker format a worktree session may drop when it is done:

`.claude/land-ready.json` in the worktree:
```json
{ "branch": "...", "base_sha": "...", "tests_run": true,
  "known_issues": [], "touched_paths": ["..."], "ready": true }
```

A candidate is **auto-landable** only when all of:
- its tree is `effectively_clean` (real uncommitted source edits still block),
- `ahead >= 1`, its Claude session is non-active, and the premise check passes,
- it is not sensitive, conflicting, retired, or dependent on held sensitive work.

The marker is an optional confidence booster and `bead_id` carrier; nothing
currently writes it, so it is not the finish gate.

The **chat tail can only veto, never solely approve.** If it contains a blocker
phrase (open question / `needs input:` / `failed:` / "shall I" / "which would you" /
unanswered AskUserQuestion), exclude the candidate even if all Git state looks
finished.

---

## sessions.py contract (build-order #1)

`bin/sessions.py` joins live Claude sessions to worktrees and reads transcript tails.

- Scan `~/.claude/sessions/*.json` → map `realpath(cwd)` → record
  (`pid, sessionId, cwd, name, status, updatedAt`).
- Transcript lookup by **sessionId glob**: `~/.claude/projects/*/**/<sessionId>.jsonl`.
  **No newest-jsonl fallback** (the old fallback grabbed the wrong session's transcript).
  If the glob misses, transcript is `null` — do not guess.
- Read a **64KB tail** of the transcript (not the whole file).
- **ACTIVE** = `pid_alive(kill -0)` AND (`status == busy` OR `transcript_mtime < 3min`).
  A dead pid stuck in `status=busy` is **NOT active** (stale record).
- Also scan `subagents/*.jsonl` under the session dir **for blocker sentinels only**
  (a subagent can be mid-question even if the main stream looks idle).
- pytest coverage: cwd→session join, sessionId-glob transcript resolution (incl. nested
  `<uuid>/` subdir), 64KB tail parse, ACTIVE truth table (dead-pid-busy = inactive).

## Severity rubric (build-order #4)

Wrong **price**, **legal copy**, **trust copy**, **a11y** failures, and **broken
responsive** layout are **NOT p3**. They are p1/p2 and block prod. p3 is genuinely
cosmetic only.

## Sensitive paths (build-order #5)

`lib/stripe / lib/auth / lib/payments / lib/db / drizzle/migrations /
pages/api/auth|admin|webhooks / lib/email/templates` are **never auto-landed.** Also:
**exclude any candidate whose merge depends on a held sensitive branch** (don't land a
feature that needs the sensitive one to make sense).

## codex-fix loop

Cap **3 iterations**. **Stop immediately on any p0.** If the cap is exceeded with p0–p2
still open, **redeploy the last-known-good revision** and surface the residual list +
per-feature revert offer. Never promote to prod with open p0–p2. Fixes start in a
fresh scratch from current `origin/main`, commit atomically, and fast-forward-push
with LAND's fetch → rebase → re-gate → retry mechanics. A QA-failed feature is
reverted from main with `git revert -m 1 <merge_sha>` only after confirming it is
the recorded `--no-ff` merge; remove it from the pending ledger.

## Prod gate

Single attended `AskUserQuestion` after clean staging QA and in the post-19:30
window. On yes: prod Cloud Build → verify new revision is Ready → explicitly
pin traffic with:

```bash
gcloud run services update-traffic aestheticc-next --region europe-west2 \
  --to-revisions aestheticc-next-<NEW>=100
```

Then prove `status.traffic[0].revisionName == aestheticc-next-<NEW>`. Production
has been pinned since 2026-06-15: **never use `--to-latest` in this skill.** On
success, archive `pending-qa.md` and `ledger.json` in evidence, reset pending
ledger state, and record `prod_sha`.

## --dry-run (build-order #6)

Add a `--dry-run` flag. **Default the first runs to dry-run** — print the full plan
(finished set, integration-branch name, deploy/QA/gate sequence) and mutate nothing.

## Verify-before-building (must confirm, do not assume)

1. `cloudbuild-staging.yaml` submits the SHIP scratch's local tree at current
   `origin/main`, rather than silently checking out another ref.
2. What an **interactive (non-bg) Agent View session record** looks like in
   `~/.claude/sessions/*.json` (the rename-to-Close-N + ACTIVE logic depend on the real
   field shape, not the bg-job shape).

## Memory reconciliation (build-order #7)

Annotate `feedback_no_self_deploy_staging`, `feedback_never_use_deploy_agents`,
`feedback_deploy_agents_lie` with the gated-prod policy. Preserve the spirit: **no
headless self-deploy, no trusting deploy agents, always verify traffic shift.** Only the
"this skill never touches prod" clause changes → "prod behind one explicit interactive
gate."

---

## Build order

1. Preserve `bin/sessions.py` and its pytest contract (session join,
   sessionId-glob transcript, 64KB tail, ACTIVE truth table).
2. Use `bin/land-state.py` as the shared mkdir mutex, FIFO queue, stale-run
   recovery, ledger, and status implementation; cover it with pytest.
3. Extend `discover.sh` with state visibility and land-batch branch exclusion;
   preserve its read-only discovery behavior.
4. LAND: multiSelect → per-feature --no-ff merge → scoped gate → safe ff-push
   → harvest QA plan into ledger. SHIP: detached full Jest → one staging deploy
   → full ledger QA → bounded fixes/reverts → pinned attended prod promotion.
5. Preserve severity, sensitive-path, dry-run, and evidence guardrails.

Verify items (#1, #2 above) happen before the steps that depend on them.
