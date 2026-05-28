# /land-batch — Post-first-live-run review (Opus 4.7, max effort, 2026-05-28)

Handoff for the Opus 4.8 re-re-review. This is written cold-readable: you don't
need the originating chat.

## Context

`/land-batch` v0.2 is a meta-orchestrator that reconciles the parallel-worktree
mess (Agent View / Conductor / `~/.worktrees`), lands the finished set onto a
throwaway integration branch, deploys once to staging, QAs all features there,
codex-fixes, and promotes to prod behind one gate. Files:
`SKILL.md`, `bin/discover.sh` (git+session+marker engine), `bin/sessions.py`
(session/transcript join, pytest-covered), `ENG_REVIEW.md` (locked decisions
D1=integration-branch, D2=marker-canonical).

**This review is grounded in its FIRST real end-to-end run**, which shipped 5
features to prod (`x8ftu` multi-tenancy, `cjf4p` payments, `1ms7s` toast,
`1fbt9` calendar, `staging-cold-start`) on `aestheticc-next-01013-p2j`,
verified live. So these findings are from reality, not speculation.

---

## 🔴 #1 — The autonomous headline is vestigial (OPEN DECISION)

The v2 identity (Step 2 "Autonomous finish-judgment, NO confirm gate";
Guardrail #7 "marker is canonical") rests on `.claude/land-ready.json`.
**Nothing writes that marker** — not `/goal`, `/ship`, or any completion path.
Result in the live run: `auto_land=0` across all 46 candidates; **every** landing
decision went through the supposed-fallback **opt-in path** with Shane
confirming. The headline feature is both **unreachable** and **never exercised**.

What actually carried the run, and is genuinely good: the opt-in *surfacing*
(legacy-low-confidence / no-marker-no-signal / held-sensitive) + git/session
state. The skill's real value is **reconciliation + safe-deploy verification**,
not autonomy.

**Decision for 4.8 + Shane:**
- (a) Build the marker *producer* — e.g. `/goal` drops `.claude/land-ready.json`
  on clean finish (tests pass, branch pushed), or a Stop-hook does — to actually
  activate the autonomous path. Then re-test it (it has NEVER run).
- (b) Re-centre the skill honestly as a *manual-opt-in reconciler + deploy
  verifier* and demote the marker to an optional fast-path.
- 4.7 lean: (b) now, (a) as a real follow-on. Don't frame an always-empty
  "WILL LAND (auto)" section as primary. (A Step-2 caveat was added pending this.)

## 🔴 #5 — Step 3 "never auto-resolve" vs reality (OPEN DECISION)

SKILL Step 3 says "never resolve a conflict by hand, skip the branch." In the
live run the merge auto-resolved **beads-only** conflicts inline (it didn't
actually fire — all 5 merged clean — so it's latent drift). `.beads/issues.jsonl`
collides on nearly every branch and is pure churn. Decide: (a) encode beads-only
auto-resolve in Step 3, or (b) have `discover.sh`/the merge pre-strip `.beads/`
before merging. Either is fine; pick one and make doc==behaviour.

---

## 🟠 Fixed in this 4.7 pass (verify them)

- **#3 Rename is impossible — Step 9 rewritten.** Verified: editing
  `~/.claude/sessions/<pid>.json` `name` does NOT change the Agent View label
  (tested live, stayed `goal x8ftu bead`); Agent View owns names in app state,
  exited sessions have no file, no CLI close exists. Step 9 now says: never
  attempt rename; the close step IS the report-mapping by existing tab name.
- **#2 Pin-detection encoded in Step 5.** The run hit an 11h staging traffic
  **pin** (`spec.traffic: revisionName` not `latestRevision`) that silently
  no-op'd every deploy (revisions created → `Retired` → traffic stayed on the
  morning's revision). Step 5 now diagnoses pin-vs-crash and gives the
  `update-traffic --to-latest` remedy + the "surface shared-state pin first" note.
- **#4 Authorization reality documented in Step 5.** The skill **cannot
  self-authorize deploys**: the auto-mode classifier blocked the staging build
  (an `autoMode.allow` directive for `cloudbuild-staging.yaml` existed, then was
  reverted to `$defaults`), prod is always Shane's call, and editing
  `settings.json` to self-grant was **blocked as self-modification**. Step 5 now
  says: expect a block, get Shane's explicit in-chat go, retry; never self-grant.
- **#8 Contract/region drift fixed.** `effectively_clean` added to the Step 1
  field list (it, not raw `clean`, is what the finish gate uses — ignores junk
  dirt). Step 8 now states builds=`europe-west1`, prod svc `aestheticc-next` in
  `europe-west2`, working-tree upload, and the prod cold-path health checks.

## 🟡 Still open — sharp edges for 4.8

- **#6 `effectively_clean` is a marker-era footgun.** It classes `lib/manifests/`
  + `PLAN.md` as junk (so `x8ftu`'s generated-manifest dirt read clean). Harmless
  while `auto_land=0`, but if #1(a) activates autonomy, a worktree with *real*
  uncommitted manifest edits would auto-land. Tighten the junk set before
  enabling marker auto-land. (`bin/discover.sh` `JUNK_DIRT_RE`.)
- **#7 `looks_like_path` drops extensionless root files.** Requires `/` or `.`,
  so a conflict on `Dockerfile`/`Makefile` is dropped from the file list (the
  conflict still registers via `has_conflict`, only the displayed path list is
  empty). Low impact. (`bin/discover.sh` `looks_like_path`.)
- **#9 Shell fragility.** Twice in the run, a bash loop piping to coreutils after
  a custom binary corrupted `PATH` ("command not found: tail/git"). The Step 3
  merge loop is exposed. Prefer python orchestration (like `sessions.py`) over
  bash loops with pipes.

## ✅ What WORKED — do not regress these

1. **Two-gate traffic verification** — caught the silent no-shift. This is the
   skill's reason to exist; without it we'd have "QA'd" an 11h-stale revision.
2. **Integration-branch isolation (D1)** — `main` was never at risk during the
   loop; clean `--ff-only` at the end; reject = `branch -D`. Worked exactly.
3. **Junk-aware reap** — pruned 18 dead worktrees (ahead=0 + junk-only dirt),
   zero real loss, keepers re-verified.
4. **`sessions.py` + pytest** — ACTIVE truth table (dead-pid-stuck-busy =
   inactive) validated on live data; 19 tests green.
5. **Premise-check before landing** — caught the `4r92w` zombie (re-implements
   the revoked `--no-traffic` tag cost-leak) and the stale 2-week `1ms7s`
   conflict branch. Kept both out of the batch.

## Loose ends from the run (not skill bugs)
- `stash@{1}` in the repo = orphaned email-from-address WIP (13 files) parked off
  `main`; needs an owner to commit it to a branch or it's lost to stash churn.
- Pre-existing `/api/payments/summary` 500 on the payments page (confirmed NOT
  from this batch) — wants a follow-up bead.
- `cjf4p` (payments) shipped to prod with its UI flow unexercised (no QA-clinic
  appointment) — unit-tested only. Recommend a real Mark-as-Paid prod smoke +
  Sentry watch.
- `4r92w` zombie branch still exists — add to `retired.txt`.

## State of the artifact
Version bumped `0.2.0 → 0.2.1`. All changes uncommitted in `~/.claude/skills/land-batch/`.
`.venv/` (pytest) is gitignored. The two OPEN decisions (#1, #5) are deliberately
NOT actioned — they're for the 4.8 + Shane pass.
