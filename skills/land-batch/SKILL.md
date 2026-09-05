---
name: land-batch
version: 0.4.0
description: |
  Serialises parallel Claude worktree landing with a durable lock, FIFO queue,
  and cross-run QA ledger. Default /land-batch cheaply discovers, curates, and
  lands finished branches to main without deploying. /land-batch --ship runs
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
5. **Never auto-resolve authored conflicts.** Abort/hold conflicts; never touch
   conflicts in lib/db/, authored drizzle/NNNN_*.sql, lib/stripe/, lib/auth/,
   lib/payments/, pages/api/auth|admin|webhooks/, or lib/email/templates/.
   .beads/ gets only the local merge=ours generated-churn driver. The dedicated
   migration gate may mechanically rebuild generated journal/snapshot metadata;
   it never changes migration SQL and does not license resolving an SQL conflict.
6. **Sensitive paths never auto-land.** Require explicit Shane opt-in and exclude
   a candidate depending on a held sensitive branch.
7. **Worktree finish gate preserved; branch-only completion quarantined.** A
   worktree source still requires effectively_clean, ahead >=1, non-active
   session, premise OK, non-sensitive, non-conflicting, and non-retired.
   A remote-only source is discoverable but never auto-landable in this release,
   even when exact bead CLOSED + pinned patch-unique tip proves it finished.
   Optional .claude/land-ready.json remains confidence metadata; tail vetoes
   only, never approves.
8. **Evidence fail-closed and traffic verified.** Every command/output/exit-code
   artifact must be cited. Build SUCCESS alone does not prove Cloud Run traffic.
9. **Production is pinned.** Since 2026-06-15, never use --to-latest on prod.
   Only explicit --to-revisions <new-revision>=100 plus exact status verification.
10. **A reported-clean merge is not proof the file merged correctly** — this is
    stronger than guardrail 8's "cite evidence" and deserves its own line
    because it defeats the instinct that "no CONFLICT marker" means safe.
    Confirmed live 2026-09-05 (`AestheticcNext-gzpj0` landing, `AestheticcNext-cozxa`):
    git reported `Auto-merging package.json` with zero conflict markers, but
    the actual merge RESULT silently reverted two real security fixes back to
    the vulnerable state (next-auth downgraded 4.24.15→4.24.13, a removed dead
    dependency came back) — an intervening commit had touched the same file
    between the branch's fork point and the merge, so git's line-based diff3
    resolved the ambiguity wrong with no warning at all. Caught only by
    diffing `git show HEAD^1:<file>` and `HEAD^2:<file>` against the merge
    result by hand, not by trusting the merge output. This is the *same root
    cause* (git's 3-way merge has no semantic understanding of the file) as
    the CHANGELOG.md/VERSION drops several `feedback_*` fixes already restored
    (`feedback_git_merge_silently_drops_changelog_entry`,
    `feedback_stale_branch_merge_silently_deletes`) — those were caught
    *after the fact*, sometimes days later, by a human or a later session
    noticing content missing; none were caught by an automated check at
    land time, across at least 7 separate historical incidents on
    CHANGELOG.md/VERSION/package.json/config-array files. That history is
    itself the evidence that "remember to diff by hand" does not reliably
    happen — hence the mechanical check below is wired into Step 2, not left
    as a reminder.
    **Practice:** for `package.json`, `VERSION`, or `CHANGELOG.md` — any file
    more than one recent branch has plausibly touched — explicitly diff both
    merge parents against the merge result for the specific lines the source
    branch was supposed to change. Do not accept "Auto-merging `<file>`" with
    no `CONFLICT` line as proof of a correct result.
    **Mechanical check (Step 2 wires this in automatically — see below):** for
    this fixed sensitive-file list, after every merge, verify that (a) every
    line the source branch's own diff *added* relative to the merge-base is
    present somewhere in the merge result, and (b) every line the source
    branch's own diff *removed* is absent from the merge result. Either
    violation is content loss and HOLDs the candidate for Shane's eyes — it is
    not auto-resolved, matching guardrail 5's "never auto-resolve" spirit even
    though git itself reported no conflict.

## Shared mutex, queue, and ledger

LAND and SHIP share ~/.claude/state/land-batch/. The atomic mutex is mkdir
LOCK.d. holder.json contains run_id, mode, claude_pid, pid_start_time,
session_id, agent_view_name, started_at, stage, heartbeat_at,
integration_branch, scratch_path, and evidence_dir. QUEUE.d contains sortable
counter-prefixed ticket JSON. ledger.json is canonical pending QA; pending-qa.md
is its human-readable projection. kickbacks.json is the branch-lineage record
for autonomous red-gate fixes. Its lineages map is keyed by original branch;
each current entry has bead_id, session_name, fix_branch, attempt,
dispatched_at, failure_summary, and signature. It also retains prior attempt
evidence in history so a repeated signature can be surfaced with both evidence
sets. Read/write it only through land-state.py, whose atomic write matches
ledger.json.

### Step 0 — Admission

For a non-dry run, create a stable run/evidence identity and acquire admission.
Dry-run runs read-only discovery/plan first and skips this admission step. The
authoritative fetch now occurs after admission, immediately before discovery in
Step 1, so the admitted run pins one coherent main + goal/* snapshot.
Resolve the actual current Claude PID and its ps lstart value from session
metadata; never use an ephemeral shell PID that dies after one tool command.

~~~bash
REPO=/Users/shane/Documents/GitReBase/AestheticcNext
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-<current-claude-pid>"
# land-batch-<RUN_ID> is a REQUIRED prefix: bin/land-state.py's orphan cleanup
# (_cleanup_orphan_worktree) only ever `git worktree remove`s a path whose
# basename starts with "land-batch-", and only ever `git branch -D`s a branch
# under "land-batch/". Renaming either convention silently defeats cleanup.
SCRATCH="$REPO/.claude/worktrees/land-batch-$RUN_ID"
EVIDENCE_DIR="$HOME/.claude/evidence/land-batch/$RUN_ID"
mkdir -p "$EVIDENCE_DIR"
ADMISSION_RESULT="$EVIDENCE_DIR/admission.json"
ADMISSION_EXIT="$EVIDENCE_DIR/admission.exit"
ADMISSION_LOG="$EVIDENCE_DIR/admission.log"
ADMISSION_PID="$EVIDENCE_DIR/admission.pid"
nohup bash -o pipefail -c '
  python3 ~/.claude/skills/land-batch/bin/land-state.py wait \
    --run-id "$1" --mode "$2" --claude-pid "$3" \
    --pid-start-time "$4" --session-id "$5" \
    --agent-view-name "$6" --stage preflight \
    --evidence-dir "$7" --repo "$8" > "$9"
  code=$?
  printf "%s\n" "$code" > "${10}"
  exit "$code"
' bash "$RUN_ID" "$MODE" "$CLAUDE_PID" "$CLAUDE_PID_START" "$SESSION_ID" \
  "$AGENT_VIEW_NAME" "$EVIDENCE_DIR" "$REPO" "$ADMISSION_RESULT" \
  "$ADMISSION_EXIT" > "$ADMISSION_LOG" 2>&1 </dev/null &
printf '%s\n' "$!" > "$ADMISSION_PID"
~~~

When locked, wait registers one ticket then polls forever with a randomized
120–180-second delay. It may acquire only when the lock is free and its ticket
is oldest surviving; a mkdir race re-enters the loop. There is no timeout. The
wait must run detached: a foreground Claude Code Bash tool call has its own
short execution timeout and will kill it before its next poll.

Poll the result, exit, and PID files with short, bounded tool calls; do not
foreground-wait. For example, each separate future turn/wakeup may run:

~~~bash
if test -s "$ADMISSION_RESULT"; then
  cat "$ADMISSION_RESULT"
elif test -s "$ADMISSION_PID" \
  && kill -0 "$(cat "$ADMISSION_PID")" 2>/dev/null; then
  printf 'still queued, waiting\n'
elif test -s "$ADMISSION_EXIT"; then
  printf 'admission exited %s\n' "$(cat "$ADMISSION_EXIT")"
else
  printf 'admission worker exited without a result; restart it with the same RUN_ID\n'
fi
~~~

If the PID is alive and there is no result, report **“still queued, waiting”**
and check again on a future turn/wakeup. Claude Code sessions are turn-based,
not one indefinitely blocking process. Do not launch a second worker while that
PID is alive. If the worker exited without a successful result, re-run the
detached block with the **same `RUN_ID`**: `register_ticket()` is idempotent for
a live run ID, so this resumes its original FIFO ticket without a duplicate or
queue-position change. A nonempty `admission.json` is the successful
`wait_for_turn()` JSON return: the run now holds the lock and may proceed with
the normal, fast one-shot heartbeat and release calls below using that same
`RUN_ID`.

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

### Step 1 — Branch discovery and curation

~~~bash
# Non-dry only, after admission. Keep discovery immediately after this fetch.
git -C "$REPO" fetch origin main \
  '+refs/heads/goal/*:refs/remotes/origin/goal/*' --quiet
LAND_BATCH_REF_SNAPSHOT=fetched-after-admission \
  bash ~/.claude/skills/land-batch/bin/discover.sh "$REPO" \
  > "$EVIDENCE_DIR/discover.json"
BASE_SHA="$(jq -er '.base_sha' "$EVIDENCE_DIR/discover.json")"
~~~

Strict dry-run performs no fetch and uses the cached remote-tracking snapshot:

~~~bash
DISCOVERY_JSON="$(LAND_BATCH_REF_SNAPSHOT=cached-remote-tracking \
  bash ~/.claude/skills/land-batch/bin/discover.sh "$REPO")"
~~~

Discovery never fetches or mutates refs itself. Verify `ref_snapshot.mode` in
its JSON and print **“dry-run: cached remote-tracking snapshot; no fetch”** for
dry-run. It enumerates the union of eligible attached worktrees and cached
`refs/remotes/origin/goal/*`; a matching worktree preserves the legacy source
and gate, while an otherwise invisible remote branch is nullable enrichment:
`source_kind=remote-branch`, `worktree_path/root/clean/effectively_clean=null`.
It batch-classifies ancestry first and omits already-merged remote-only refs
before candidate construction or per-candidate enrichment, reporting only the
top-level `skipped_merged_count`; a worktree-backed branch remains visible as
`skip-merged` even when its tip is already in main. Every candidate carries
`source_ref` and `tip_sha`. It classifies fully landed
patches as `skip-patch-equivalent` before merge analysis, explicit retired.txt
matches as hard `skip-retired`, age >45 as loud `held-stale`, and base conflicts
as loud `held-conflict` with `conflicting_files`.

An exact/uniquely aliased CLOSED bead plus a pinned patch-unique remote tip is
finish evidence only. Render it exactly as **HELD — branch-only completion;
premise review required** and never offer it in this release. Unresolved or
ambiguous bead identity is also HELD, never guessed. Discovery still renders
complete WILL LAND / HELD / BLOCKED / SKIP / SIBLING CONFLICTS sections,
excludes all land-batch/* branches, and emits top-level lock_queue state. It
reads kickbacks.json through that state and adds only a presentation label:

- an original branch with a lineage is "KICKED BACK — fix in flight (bead <id>,
  session <name>)";
- a candidate whose resolved bead_id, marker bead_id, or recorded fix_branch
  matches a lineage is "KICKBACK FIX — <original branch> (bead <id>)".

That is presentation, not a rewrite of candidate facts. Do not offer a
presentation.role == kicked-back-original candidate in multiSelect even if its
Git facts would otherwise make it auto-landable. A KICKBACK FIX remains a
normal candidate once it independently satisfies the usual gate.

Discovery also hashes every candidate-added `drizzle/NNNN_*.sql` at its pinned
tip. `migration_analysis.collisions` groups a number with more than one content
hash and mirrors every differing-hash branch pair into `sibling_conflicts` with
`kind=migration-number-collision`. Render these as **MIGRATION COLLISION —
NNNN** beside Git conflicts. `migration_analysis.inheritance` is the opposite:
multiple branches carry one byte-identical hash. That is stacked-branch
inheritance, not a blocker; never infer identity from filename alone.

`sensitive_prefix_validation.valid` must be true. Discovery fails closed before
candidate construction if any configured directory is absent from the target
repo. The `drizzle/` rule matches only top-level `NNNN_*.sql`; generated metadata
is protected by the reconciliation/proof gate below.

After admission, and only outside dry-run, update a discovered fix branch while
keeping discover.sh read-only:

~~~bash
python3 ~/.claude/skills/land-batch/bin/land-state.py kickback-fix-branch \
  --original-branch "<presentation.original_branch>" \
  --fix-branch "<candidate.branch>"
~~~

Keep the multiSelect curation gate exactly:

- Offer auto_land=true candidates only, with recognisable labels and honest
  one-line safety/caveat descriptions.
- Render the kickback presentation labels above before normal candidate labels;
  KICKED BACK originals are surfaced, never selected. KICKBACK FIX candidates
  identify their original branch but otherwise follow normal curation.
- At most four options/question and four questions/call. Chunk >4; above 16,
  offer the highest-confidence 16 and visibly log every remainder.
- Print HELD/BLOCKED/SKIP beside the selector. Deselection is held by Shane.
- Sensitive candidates require separate explicit opt-in. Choose at most one of
  an authored/Git sibling-conflict pair. A `migration-number-collision` pair may
  be jointly selected only through the all-or-nothing migration transaction in
  Step 2; it is blocked from the ordinary per-feature push path.
- Offer safe pruning only for `source_kind=worktree` records that satisfy the
  existing clean ahead=0 or explicit-retired rules. A `remote-branch` record
  must never enter pruning: pruning removes worktrees, and it has none.

Dry-run stops after that plan without admission/mutation.

### Step 2 — Per-feature merge, cheap scoped gate, direct main push

🔴 **Hard guardrail 2 is enforced here, structurally, not just stated.** Every git
command below runs inside `$SCRATCH` — an isolated worktree — and NEVER bare
against `$REPO`. `$REPO` is the shared trunk checkout: other sessions run
`codex exec`/interactive work directly inside it, so a bare `git merge`, `git
reset --hard`, or `git checkout` there can destroy another session's uncommitted
work (confirmed harm, `LUCY-3sb80`, 2026-08-12). Create `$SCRATCH` as its own
worktree first — do not reuse or `cd` into `$REPO`'s own working tree for any
part of Step 2:

~~~bash
git -C "$REPO" worktree add "$SCRATCH" -b "land-batch/$RUN_ID" origin/main
~~~

Configure the existing .beads merge=ours driver in common git info attributes
(inside `$SCRATCH`, not `$REPO`'s own `.git/info/attributes`, since a worktree
shares the parent's git dir — this is written once and covers both). For every
selected branch, use the candidate's pinned `source_ref` and `tip_sha`. Verify
the ref again immediately before its merge; if it is missing or moved, HOLD it
as **source-moved — rediscovery required** and do not merge any other ref or a
same-named local branch in its place:

~~~bash
BR="<candidate.branch>"
SOURCE_REF="<candidate.source_ref>"
SOURCE_SHA="<candidate.tip_sha>"
if ! CURRENT_SOURCE_SHA="$(
  ~/.claude/skills/land-batch/bin/verify-pinned-source.sh \
    "$REPO" "$SOURCE_REF" "$SOURCE_SHA"
)"; then
  printf 'HELD source missing/moved; rediscover: %s\n' "$SOURCE_REF"
  continue
fi
git -C "$SCRATCH" merge --no-ff --no-verify "$SOURCE_SHA" \
  -m "land: $BR @ $SOURCE_SHA (batch $RUN_ID via /land-batch)"
MERGE_SHA="$(git -C "$SCRATCH" rev-parse HEAD)"
~~~

A merge conflict means abort and hold it. Even a clean (no-conflict) merge is
not yet trusted — per Hard guardrail 10, run the sensitive-file check before
the scoped gate:

~~~bash
SENSITIVE_MERGE_LOG="$EVIDENCE_DIR/sensitive-merge-check-$BR.log"
if ! ~/.claude/skills/land-batch/bin/verify-sensitive-file-merge.sh \
  "$SCRATCH" "$MERGE_SHA" > "$SENSITIVE_MERGE_LOG" 2>&1; then
  cat "$SENSITIVE_MERGE_LOG"
  git -C "$SCRATCH" reset --hard "$MERGE_SHA^1"
  printf 'HELD %s — clean merge silently dropped/reverted content in package.json/VERSION/CHANGELOG.md (see %s). Not auto-resolved.\n' \
    "$BR" "$SENSITIVE_MERGE_LOG"
  continue
fi
~~~

This is a narrow, mechanical presence/absence check (fixed file list:
`package.json`, `VERSION`, `CHANGELOG.md`) — a hit means "a human needs to
look," not "this merge is definitely wrong": a later, deliberate edit to the
same line by an already-landed candidate in this same batch can trip it too.
Treat it exactly like guardrail 5's other HOLD categories: preserve the log,
do not touch the file to "fix" it yourself, surface it in the final
HELD/BLOCKED report.

For each merge that passes both checks, run typecheck, lint,
and Jest scoped to **that feature's changed files**. Derive files from the merge
and use package-supported scoped typecheck/lint plus Jest --findRelatedTests
<files>, or Jest --changedSince=origin/main where that is the narrower accurate
form. Never run full Jest in LAND. Save separate command, raw output, and numeric
exit-code artifacts under scoped-gate-<branch>-*.

Any red/missing/malformed artifact means:

~~~bash
git -C "$SCRATCH" reset --hard "$MERGE_SHA^"
~~~

This reset is LAND-only. At this moment the scratch's bad merge has not been
pushed, so resetting to its first parent removes the merge completely from the
eventual main history. Do not replace SHIP Step 6's pushed-main revert with a
reset: SHIP still uses `git revert -m 1 "$MERGE_SHA" --no-edit`.

Keep every original scoped-gate-<branch>-command/raw-output/exit-code artifact
exactly as captured; those artifacts remain the failure record. Immediately
after the reset, collect a kickback decision while the precise pre-merge
baseline is available:

1. Re-run the exact failed scoped command(s), unchanged, at the scratch's
   current HEAD. That is origin/main plus only earlier green merges already
   pushed in this batch. Write separate
   scoped-baseline-<branch>-command/raw-output/exit-code artifacts. A red or
   malformed baseline is held as **baseline-red** (or malformed evidence): it
   is not safe to blame or dispatch against this branch.
2. Use the exact sensitive-path list in Hard guardrail 5 against both the
   feature's changed files and files named by the gate failure. A candidate
   selected through explicit sensitive-path opt-in is categorically ineligible
   for autonomous dispatch, even if the apparent failure is elsewhere.
3. A normal TypeScript/lint diagnostic or Jest assertion is eligible only after
   a green baseline. Exit 134/137, heap-out-of-memory text, SIGABRT-style
   crashes, or missing/malformed gate evidence are not code failures. For an
   infra-shaped failure, re-run the identical failed command once and preserve
   its separate retry artifacts. If it is still infra-shaped, hold it as
   **infra-after-retry**; never create a code-fix bead for it.
4. Build a stable signature from the failing files/tests and consult
   kickbacks.json. Attempt 1 may dispatch. At attempts 2 and 3, dispatch only
   if the signature differs from the previous attempt; unchanged signatures
   are held with both current and prior evidence paths. Once three attempts
   have been dispatched, hold every later failure as **attempt-cap-exceeded**.
   Use land-state.py's `classify_kickback()` and
   `kickback_attempt_decision()` helpers rather than improvising the
   classification/cap logic.

Collect qualifying candidates in the in-memory KICKBACKS list; do not create a
bead or launch Claude yet. For every non-qualifying result (baseline-red,
sensitive, conflict, malformed evidence, infra-after-retry, same signature,
cap, or a live fix session), preserve the evidence and put it in the final
HELD/BLOCKED report for Shane's judgment.

For a green feature:

~~~bash
git -C "$SCRATCH" push origin HEAD:main
~~~

🔴 **B-1 (AI Fleet Grand Plan, 2026-09-01): this push is the moment a bead's status changes from `pushed` to `closed` — land-batch is now the ONLY place that happens for AestheticcNext- code beads.** Workers stop closing their own beads in `/goal` Phase 6; they leave them `pushed`. Immediately after the push above succeeds, for every AestheticcNext- bead ID this candidate's branch corresponds to (derive from the branch name, e.g. `goal/aestheticcnext-<id>` → `AestheticcNext-<id>`; a bundle/epic closes every child then the parent):

~~~bash
git -C "$SCRATCH" fetch origin main --quiet
if git -C "$SCRATCH" merge-base --is-ancestor "$MERGE_SHA" origin/main; then
  bd close "$BEAD_ID" --reason "landed via /land-batch $RUN_ID @ $MERGE_SHA (verified ancestor of origin/main)."
  CLOSE_RESULT="closed"
else
  echo "HELD: $BEAD_ID pushed to main but $MERGE_SHA is not (yet) an ancestor — do not close, investigate before the next run." >&2
  CLOSE_RESULT="held"
fi
~~~

This ancestry check is intentionally redundant with `bd-close-guard` (`~/.claude/bin/bd`) — land-batch's own close is expected to always pass it, since the push just happened; if it ever doesn't, that is itself a signal something is wrong (a rejected/rebased push, a stale `$MERGE_SHA`) and the bead correctly stays `pushed`, not closed. A bead already sitting `closed` from before B-1 shipped (or a LUCY- vault bead) is left alone here.

🔴 **B-2 (AI Fleet Grand Plan, 2026-09-01): post-land goal-state review, a different model family asking a different question than Phase 4 already did.** `/goal` Phase 4 (Codex, or GLM per B-10) already reviewed the DIFF — is this change sound. This is the SECOND, independent check, run only after a real close above, asking the GOAL-STATE question instead: does this bug-shape exist anywhere ELSE in the repo, fixed or not. This is exactly the check that would have caught `AestheticcNext-n708r`'s third unfixed copy at land time instead of three days later as a separately-filed `zrexy`. Only runs for a bead that was actually closed just now (skip entirely on HELD):

~~~bash
if [[ "$CLOSE_RESULT" == "closed" ]]; then
  GLM_GOALSTATE_PROMPT="This fix landed at $MERGE_SHA (bead $BEAD_ID). Diff below. Enumerate every OTHER site in the repo that implements the same operation/state as this fix, and state for each whether the fix applies there too — i.e. does the same bug-shape still exist somewhere unfixed. Do NOT review the diff itself for correctness; a different reviewer already did that in /goal Phase 4. Answer ONLY the goal-state question: other sites, fixed or not, one line each. If there are none, say so plainly.

DIFF:
$(git -C "$SCRATCH" show "$MERGE_SHA")"

  set +e
  echo "$GLM_GOALSTATE_PROMPT" | glm review --cd "$SCRATCH" \
    > "/tmp/land-batch-goalstate-$BEAD_ID.log" 2>&1
  GLM_GS_EXIT=$?
  set -e

  if [[ "$GLM_GS_EXIT" -ne 0 ]]; then
    echo "GLM unreachable for post-land goal-state review of $BEAD_ID (exit $GLM_GS_EXIT) — falling back to Opus in-session: read $MERGE_SHA's diff yourself right now and answer the same goal-state question directly, don't skip this silently." >&2
    # (Opus fallback is you, the orchestrating session, doing the read+answer inline —
    # there is no separate CLI call for this leg, same as every other GLM-outage path.)
  fi

  bd comment "$BEAD_ID" "Post-land goal-state review ($([ "$GLM_GS_EXIT" -eq 0 ] && echo glm || echo 'opus, GLM unreachable')): $(cat "/tmp/land-batch-goalstate-$BEAD_ID.log" 2>/dev/null | head -c 4000)"

  # Any line naming a real, still-unfixed other site: auto-file a child bead rather
  # than requiring a human to notice it in the comment. Same prefix as $BEAD_ID,
  # type=bug, --parent "$BEAD_ID", priority inherited from $BEAD_ID. Read the
  # goal-state review's own text to decide whether it actually found a real
  # unfixed site (not "no other sites found") before filing — don't file a bead
  # for a clean review.
fi
~~~

Latency/budget note: this is one GLM call per CLOSED bead, not per candidate — a 20-branch LAND run with 15 closes is 15 calls. If that proves too slow/expensive in practice, batch multiple closed beads' diffs into one GLM call per land-batch run instead of one-per-bead, and say so plainly rather than silently skipping the check.

If push is rejected because main moved, `git -C "$SCRATCH" fetch origin main` and
`git -C "$SCRATCH" rebase --rebase-merges origin/main`, re-gate, retry. Abort/hold
rebase conflicts. Never force-push. The no-ff merge message ties every land to
RUN_ID. Every command in this retry stays scoped to `$SCRATCH` — same guardrail
as Step 2's merge loop, for the same reason.

#### Mandatory migration-batch transaction

Selected candidates with any `migration_claims` are a single integration
transaction and run after ordinary candidates. Sensitive-path opt-in still
applies to each one. Do not push the first migration feature while another
selected migration feature is still unintegrated: parallel branches commonly
claim the same next number, and only the complete selected set can be safely
numbered.

Merge and scoped-gate each migration feature sequentially in deterministic
candidate-branch order. A conflict in authored SQL or application code is still
held under Guardrail 5. Generated `drizzle/meta/_journal.json` and numeric
`drizzle/meta/NNNN_snapshot.json` conflicts are the narrow mechanical exception:
verify the complete unmerged-path list contains no authored path, keep the
integration-side generated files temporarily, and finish the merge. Never take
one branch's generated snapshot as the final answer. The wrapper below rebuilds
those files from every selected candidate's pinned `tip_sha`; a missing or moved
source, SQL hash change, SQL path/tag collision, or byte-identical SQL with a
divergent snapshot body is red.

Write `MIGRATION_CANDIDATES_JSON` from the already-curated selection only (never
the complete discovery report):

~~~bash
MIGRATION_CANDIDATES_JSON="$EVIDENCE_DIR/migration-candidates.json"
# SELECTED_MIGRATION_BRANCHES_JSON is the JSON array accepted at curation.
jq --argjson selected "$SELECTED_MIGRATION_BRANCHES_JSON" \
  '{candidates: [.candidates[] |
    select(.branch as $branch | $selected | index($branch))]}' \
  "$EVIDENCE_DIR/discover.json" > "$MIGRATION_CANDIDATES_JSON"
jq -e '.candidates | length > 0' "$MIGRATION_CANDIDATES_JSON" >/dev/null
~~~

Once every surviving migration feature is present, run the repository's
documented land-time recipe through the wrapper (it calls
`scripts/reconcile-drizzle-migrations.mjs` directly because `package.json` may
itself have suffered a clean-merge overwrite):

~~~bash
MIGRATION_RECONCILE_JSON="$EVIDENCE_DIR/migration-reconcile.json"
MIGRATION_RECONCILE_EXIT=0
python3 ~/.claude/skills/land-batch/bin/migration_batch.py reconcile \
  --repo "$SCRATCH" --base-ref "$BASE_SHA" \
  --candidates "$MIGRATION_CANDIDATES_JSON" \
  > "$MIGRATION_RECONCILE_JSON" || MIGRATION_RECONCILE_EXIT="$?"
test "$MIGRATION_RECONCILE_EXIT" -eq 0
jq -e '.valid == true and .added_entry_count == .snapshots_rebuilt' \
  "$MIGRATION_RECONCILE_JSON" >/dev/null
~~~

The wrapper hash-verifies and reconstructs each selected migration from its
pinned source, collapses byte-identical inheritance to one artifact, preserves
the documented deterministic `(when, tag)` order, renumbers added entries into
the next free contiguous indices, safely stages rename destinations, invokes
the target's reconciliation implementation, then three-way rebuilds every added
snapshot cumulatively from its original `prevId`. Incompatible concurrent
snapshot edits fail loudly. Never hand-edit
`drizzle/meta/_journal.json`; never accept a reconcile result with a missing SQL
file, non-increasing `when`, duplicate tag, index gap, new tag prefix that differs
from its index, or fewer rebuilt snapshots than added entries. Historical prefix
mismatches already present on the pinned base are baseline evidence, not silently
rewritten by LAND.

Now run the load-bearing gate before any migration batch push:

~~~bash
MIGRATION_PROOF_JSON="$EVIDENCE_DIR/migration-proof.json"
python3 ~/.claude/skills/land-batch/bin/migration_batch.py prove \
  --repo "$SCRATCH" --base-ref "$BASE_SHA" \
  --evidence "$MIGRATION_PROOF_JSON"
jq -e '.valid == true and .scratch_database_from_empty == true
  and .new_failure_count == 0 and (.object_errors | length) == 0' \
  "$MIGRATION_PROOF_JSON" >/dev/null
~~~

This creates separate empty scratch databases, replays both the full pinned-base
chain and the full integration chain, rejects every new replay failure, and
derives the expected added/changed tables, columns, constraints, and indexes
from the rebuilt snapshot delta before checking the integration database object
by object. Native PostgreSQL is primary; PostgreSQL-WASM is an allowed sandbox
fallback, not a mock. Missing PostgreSQL capability is red evidence, never a
skip. Commit the generated reconciliation as
`chore(migrations): reconcile <RUN_ID> migration batch`, rerun the scoped gates
against the reconciled names, then make one normal non-force fast-forward push.
If main moved, rebase, rerun reconciliation and the entire from-empty proof, and
only then retry the push.

### Step 2.5 — Dispatch qualifying kickback fixes after the final LAND push

Run this only after the Step 2 loop's final push to main, while LAND still holds
LOCK.d. It is intentionally a short, non-blocking operation. A fix session
never acquires LAND's lock, edits QUEUE.d, or touches ledger.json: it is an
ordinary /goal session that stops at a green, pushed feature branch.

For each member of KICKBACKS, first call:

~~~bash
python3 ~/.claude/skills/land-batch/bin/land-state.py kickback-status
~~~

It uses sessions.py's existing kill-0 plus PID-identity liveness contract and
matches the recorded session_name. If the lineage is in-flight, skip it and
report **fix already in flight**, rather than creating a duplicate bead/session.
Also re-check `claude agents --json` by session name immediately before launch;
it is a read-only background-session listing, not a Claude work invocation.

Before Beads creation, capture that final live-agent check as evidence.
EXISTING_SESSION is the prior lineage's session_name, if there is one; an entry
in the active array is a live session and must be held as **fix already in
flight**. Do not create a bead or dispatch this lineage in that case:

~~~bash
claude agents --json > "$EVIDENCE_DIR/kickback-agents-$BR.json"
if test -n "${EXISTING_SESSION:-}" \
  && jq -e --arg name "$EXISTING_SESSION" \
    '.[] | select(.name == $name)' "$EVIDENCE_DIR/kickback-agents-$BR.json" > /dev/null; then
  printf 'fix already in flight: %s\n' "$EXISTING_SESSION" \
    > "$EVIDENCE_DIR/kickback-$BR-held.txt"
  continue  # Skip this KICKBACKS entry.
fi
~~~

Create a new P1 Beads issue from the target repository, never from the vault:

~~~bash
cd /Users/shane/Documents/GitReBase/AestheticcNext
# ORIGINAL_LABELS is the comma-separated labels from bd show --json when the
# original bead is known; otherwise it is empty.
# Use type=task only when the original work was a task-like operational item;
# normal gate regressions are type=bug.
KICKBACK_DEP_ARGS=()
if test -n "$ORIGINAL_BEAD"; then
  KICKBACK_DEP_ARGS=(--deps "discovered-from:$ORIGINAL_BEAD")
fi
KICKBACK_BEAD="$(bd create --silent \
  --title "LAND kickback: <original-branch> (<original-bead-or-no-bead>)" \
  --type "<bug-or-task>" --priority P1 \
  --labels "land-batch-kickback${ORIGINAL_LABELS:+,$ORIGINAL_LABELS}" \
  "${KICKBACK_DEP_ARGS[@]}" \
  --description "<description below>" \
  --acceptance "<acceptance below>")"
~~~

The description and acceptance criteria must match normal /goal bead tone:
self-contained, concrete, and ending at a pushed branch. They must include the
original branch name; its pre-reset tip SHA; RUN_ID; absolute paths for each
scoped-gate-<branch>-command/raw-output/exit-code artifact; the failure
summary; and the exact failed typecheck/lint/Jest command(s). The acceptance
text must include this exact recipe (with placeholders expanded):

> Start from current origin/main in your own worktree. `git merge
> <original-branch-tip-SHA>` (merge, NOT cherry-pick — this matters for how the
> original worktree/branch gets cleaned up later, do not deviate). Make these
> exact scoped gate commands green: <the exact typecheck/lint/jest commands
> that failed>. Push your branch. Write .claude/land-ready.json with bead_id set
> to this kickback bead's id. Do NOT merge to main. Do NOT deploy anything. Stop
> once your branch is pushed and green.

After the live-session check passes, record the bead and planned Agent View
session name atomically before launch:

~~~bash
KICKBACK_SESSION="kickback-$KICKBACK_BEAD"
KICKBACK_RECORD_JSON="$EVIDENCE_DIR/kickback-$BR-record.json"
jq -n \
  --arg bead_id "$KICKBACK_BEAD" \
  --arg session_name "$KICKBACK_SESSION" \
  --arg failure_summary "$FAILURE_SUMMARY" \
  --arg signature "$FAILURE_SIGNATURE" \
  --arg evidence_path "$PRIMARY_FAILURE_EVIDENCE" \
  --arg dispatched_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{bead_id:$bead_id,session_name:$session_name,fix_branch:null,
    failure_summary:$failure_summary,signature:$signature,
    evidence_path:$evidence_path,dispatched_at:$dispatched_at}' \
  > "$KICKBACK_RECORD_JSON"
python3 ~/.claude/skills/land-batch/bin/land-state.py kickback-record \
  --original-branch "$BR" --record "$KICKBACK_RECORD_JSON"
cd /Users/shane/Documents/GitReBase/AestheticcNext
claude --bg --name "$KICKBACK_SESSION" "/goal $KICKBACK_BEAD"
~~~

KICKBACK_RECORD_JSON contains bead_id, session_name, null fix_branch,
failure_summary, signature, dispatched_at, and the primary failure evidence
path. The command intentionally runs from the target repository; never launch
it from a vault-rooted cwd. If Beads creation or Claude launch fails, preserve
the error as evidence and surface it; do not list it under AUTO-DISPATCHED
FIXES. A pre-launch state record is then visibly **stalled**, rather than being
silently retried. After the last candidate, heartbeat, then proceed to
Step 3/cleanup/release normally.

### Step 3 — Harvest QA at land time

Immediately after every green push, move the former three-source QA assembly
here, rather than delaying it until SHIP:

1. candidate.session.last_assistant_tail and optional marker known_issues,
   planning input only. Either may be null for branch-only records; do not
   fabricate a tail/marker or treat absence as failure;
2. bd show bead acceptance criteria;
3. git show --stat MERGE_SHA to derive a smoke check when needed.

Write harvested-checks.json and harvested-checks.md in evidence, with one feature
entry containing branch, `source_kind`, `source_ref`, `source_sha` (the pinned
candidate `tip_sha`), merge_sha, landed_at, sources, concrete checks, and
per-feature checklist markdown. When session/marker data is null, derive the
plan from bead acceptance + the pinned source diff/stat. These plans are not QA
evidence. Append them:

~~~bash
python3 ~/.claude/skills/land-batch/bin/land-state.py ledger-append \
  --record "$EVIDENCE_DIR/harvested-checks.json" \
  --checklist "$EVIDENCE_DIR/harvested-checks.md"
~~~

This atomically updates ledger.json and pending-qa.md before tails can be pruned.
LAND never touches staging. Clean scratch/branch after ledger writes, then release:

~~~bash
git -C "$REPO" worktree remove "$SCRATCH" --force
git -C "$REPO" branch -D "land-batch/$RUN_ID"
~~~

This is the normal-completion path — the same convention (`land-batch-` worktree
prefix, `land-batch/` branch prefix) that `land-state.py`'s orphan cleanup relies
on for a *crashed* run, so a run that dies mid-Step-2/3 is still recoverable by
that mechanism even though this explicit cleanup didn't get to run.

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

Keep three iterations; stop immediately on p0. Same guardrail as LAND Step 2:
every command below runs inside an isolated worktree, never bare against
`$REPO`. Each iteration gets its own:

~~~bash
FIX_SCRATCH="$REPO/.claude/worktrees/land-batch-$RUN_ID-fix$ITERATION"
git -C "$REPO" worktree add "$FIX_SCRATCH" origin/main
~~~

For p1/p2 run Codex directly inside `$FIX_SCRATCH`, scoped to offending files;
surface any sensitive path. Commit atomically, then direct ff-push to main with
the same fetch/rebase/re-gate/retry, never-force mechanics as LAND (all
`-C "$FIX_SCRATCH"`). Redeploy staging with both gates and re-run affected QA.

If a feature cannot be clean, verify MERGE_SHA^2 exists (real --no-ff merge),
then inside a fresh `$FIX_SCRATCH` off current origin/main:

~~~bash
git -C "$FIX_SCRATCH" revert -m 1 "$MERGE_SHA" --no-edit
git -C "$FIX_SCRATCH" push origin HEAD:main
python3 ~/.claude/skills/land-batch/bin/land-state.py ledger-remove --merge-sha "$MERGE_SHA"
~~~

Remove `$FIX_SCRATCH` (`git -C "$REPO" worktree remove "$FIX_SCRATCH" --force`)
once its iteration is done, same as LAND's scratch cleanup.

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
FIFO queue and liveness, plus every pending feature since prod_sha. It also
shows every kickback lineage as **in-flight** (recorded session live), **ready**
(fix branch discovered/pushed and eligible to re-enter normal discovery), or
**stalled** (session dead with no discovered fix branch). Stalled is explicit:
never silently retry a dead session. Discovery's top-level lock_queue exposes
the same state.

Never rename, close, or kill Agent View tabs. Report existing tab names mapped to
branch/bead, landed/held/conflict/reverted items, scratch cleanup, ledger count,
staging/prod revision or NOT touched, cited evidence paths, and exact next action.
For every migration batch, report collision groups, inheritance groups, the
renumber map, snapshot rebuild count, base/integration replay counts, proof
backend, expected-object counts, and the migration-proof evidence path.
For LAND, add a separate **AUTO-DISPATCHED FIXES** section listing original
branch, kickback bead ID, Agent View session name, one-line failure summary,
attempt number, and primary evidence path. Keep it strictly separate from the
HELD/BLOCKED/SKIP judgment section, which contains baseline-red, sensitive,
conflict, malformed, infra-after-retry, same-signature, cap-exceeded, stalled,
and launch-failure cases. Do not send Slack/Hermes or other proactive notices;
the report, discovery label, and --status are the brief.

Run tests with:
~/.claude/skills/land-batch/.venv/bin/python -m pytest ~/.claude/skills/land-batch/tests/ -q
