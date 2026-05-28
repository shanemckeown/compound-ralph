# /land-batch v0.2 — Locked Build Spec (eng-reviewed: Claude + Codex, 2026-05-28)

This supersedes the open questions in `UPGRADE_PLAN.md`. Where the two disagree, **this file wins.**

---

## Locked decisions

### D1 — Integration branch (NOT merge-to-main during the loop)

The loop never touches `main`. All landing, gating, deploy, QA, and codex-fix happen on a
throwaway integration branch:

- Branch `land-batch/<ts>` off `origin/main` (ts = `date +%Y%m%d-%H%M%S`).
- Merge the finished set into it `--no-ff` (each feature = one revertable merge commit).
- Green-gate (typecheck + lint + test), staging deploy, consolidated QA, and the codex-fix
  loop **all run on `land-batch/<ts>`** — never on `main`.
- `main` is touched **only at the human gate**:
  - approve → `git checkout main && git merge --ff-only land-batch/<ts>` → deploy prod
  - reject → `git branch -D land-batch/<ts>` (main never moved, nothing to revert)

This replaces UPGRADE_PLAN building-block #3's "codex fixes on main" — codex fixes land on
the integration branch, so a rejected batch leaves `main` pristine with no revert needed.

### D2 — Marker file is canonical; chat tail is veto-only

Canonical finish signal is a marker the worktree's own session drops when it's done:

`.claude/land-ready.json` in the worktree:
```json
{ "branch": "...", "base_sha": "...", "tests_run": true,
  "known_issues": [], "touched_paths": ["..."], "ready": true }
```

A candidate is **auto-landable** only when ALL of:
- `.claude/land-ready.json` exists with `ready: true`
- git-clean (`status --porcelain` empty)
- `ahead >= 1`

The **chat tail can only veto, never solely approve.** If the tail contains a blocker
phrase (open question / `needs input:` / `failed:` / "shall I" / "which would you" /
unanswered AskUserQuestion), exclude the candidate even if the marker says ready.

**Legacy worktrees without a marker:** fall back to git-clean + `result:` sentinel in the
tail, flagged **low-confidence**, and **not auto-landed on the first run** — surfaced for
explicit opt-in instead.

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
per-feature revert offer. Never promote to prod with open p0–p2.

## Prod gate

Single `AskUserQuestion` after staging QA is clean. On yes: ff `main` → prod cloudbuild →
**both** verifications (build SUCCESS in correct region; `traffic[0].revisionName ==
latestCreatedRevisionName`). On no: stop at staging, `main` untouched.

## --dry-run (build-order #6)

Add a `--dry-run` flag. **Default the first runs to dry-run** — print the full plan
(finished set, integration-branch name, deploy/QA/gate sequence) and mutate nothing.

## Verify-before-building (must confirm, do not assume)

1. `cloudbuild-staging.yaml` can deploy an **arbitrary branch SHA** (the integration
   branch), not only `main`. If it hard-codes `main`, the deploy step needs adjustment.
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

1. `bin/sessions.py` + pytest (session join, sessionId-glob transcript, 64KB tail, ACTIVE
   truth table).
2. Merge session data into `discover.sh` contract; fix `/tmp` clobber →
   `${CLAUDE_JOB_DIR:-$(mktemp -d)}/discover.json`.
3. SKILL.md rewrite: marker-gated auto finish-judgment → integration branch → green-gate →
   staging-from-branch-SHA + traffic verify → consolidated QA → codex-fix loop → prod gate
   → Close-N rename (cosmetic; report mapping is the source of truth).
4. Severity rubric.
5. Sensitive-path exclusion (incl. dependency-on-held-branch).
6. `--dry-run`, default-on for first runs.
7. Memory reconciliation.

Verify items (#1, #2 above) happen before the steps that depend on them.
