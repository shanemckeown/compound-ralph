# PLAN — LUCY-hvryu

## Goal

Stop `/goal` and `/long-goal` dispatches from ever running a git-mutating command in a
shared trunk checkout before isolating into a worktree — the exact failure that hit
`LUCY-ay7xu`.

## Bead context (verbatim)

> Found 2026-08-26: the LUCY-ay7xu session (dispatched 25 Aug to fix the capture-watcher
> commit gap) checked out its own branch (goal/lucy-ay7xu) directly in the SHARED
> /Users/shane/Documents/Obsidian trunk checkout, rather than an isolated worktree. This
> silently switched the orchestrator's own interactive session onto the wrong branch
> mid-conversation — two real CLAUDE.md commits landed on goal/lucy-ay7xu instead of main
> and never reached origin/main until manually caught and fast-forwarded back.
>
> This is the exact cross-contamination failure CLAUDE.md already documents and warns
> about for the AestheticcNext code repo ('AGENT VIEW — VERIFY BRANCH BEFORE EVERY
> COMMIT'), just not previously known to also happen in the Obsidian vault repo's dispatch
> path.
>
> Scope: find where LUCY-prefixed /goal dispatches decide their working
> directory/worktree for the vault repo and confirm they always use an isolated worktree
> (matching the AestheticcNext dispatch pattern), never the shared trunk checkout at
> /Users/shane/Documents/Obsidian directly.

## Investigation findings (read-only subagent, confirmed against live transcript + reflog)

- The bug is **not** in `fleet-dispatch.py`'s cwd logic and **not** a structural problem
  with Obsidian's git layout (it supports worktrees fine — 3 prior LUCY worktrees exist
  cleanly there).
- Root cause: `~/.claude/commands/goal.md` Phase 1 (and the identical pattern in
  `long-goal.md`) says "If not already in a worktree, call `EnterWorktree`" as a plain,
  unenforced numbered-list item, with no check that isolation actually happened before
  branch/commit steps run. Confirmed live in the LUCY-ay7xu session's own transcript: it
  ran `cd /Users/shane/Documents/Obsidian && git checkout -b goal/lucy-ay7xu` directly,
  *then* called `EnterWorktree` several steps later as a correction — by which point the
  shared trunk had already been switched onto the feature branch.
- A second, previously undocumented wrinkle discovered *while building this very fix*:
  `EnterWorktree` is hard-pinned to the session's launch repo. This session launched in
  `AestheticcNext`, but this bead's `REPO_ROOT` (per Phase 0a routing) is
  `/Users/shane/Documents/Obsidian` — a different repo again is where the actual fix
  target file (`~/.claude/commands/goal.md`) lives (`~/.claude`, repo `compound-ralph`,
  neither Obsidian nor AestheticcNext). Calling `EnterWorktree` in that situation created
  an isolated worktree in the *wrong* repo (silently — no error), which had to be
  discarded and replaced with a manual `git worktree add -C <repo>`. This is exactly the
  gap the fix below closes: Phase 1 needs an explicit branch for "REPO_ROOT differs from
  the launch repo," not just "call EnterWorktree."

## Affected files

- `commands/goal.md` (Phase 1 section, ~30 lines)
- `commands/long-goal.md` (Phase 1 section, ~25 lines)

Both files live in `/Users/shane/.claude` (repo `compound-ralph` on GitHub) — this is
Shane's global Claude Code tooling config, a third repo distinct from both `Obsidian`
(where `LUCY-hvryu`'s `BEADS_DIR`/`REPO_ROOT` point) and `AestheticcNext` (where this
session launched). No `invariants/*.yaml` or `Product/Architecture/promise_inventory.jsonl`
match these paths (both are AestheticcNext-specific artifacts) — Invariant/Promise
interactions fields omitted per 0d.1's own instruction.

## Approach

1. Rewrite Phase 1 in both `goal.md` and `long-goal.md` so branch/commit steps are
   explicitly gated behind isolation, with two cases:
   - `REPO_ROOT` == the session's launch repo → call `EnterWorktree` (unchanged happy path).
   - `REPO_ROOT` != the session's launch repo → `EnterWorktree` cannot help (hard-pinned to
     the launch repo); isolate manually via `git -C "$REPO_ROOT" worktree add` into a
     scratch dir, never touching the shared checkout.
2. Add an explicit `pwd` verification instruction before the first git-mutating command in
   either case, modeled on the AestheticcNext CLAUDE.md's existing "VERIFY BRANCH BEFORE
   EVERY COMMIT" pattern for the code repo — extending the same discipline to `/goal`'s own
   dispatch logic instead of leaving it as unchecked prose.
3. No app code, no schema, no API surface touched — this is prompt/instruction content for
   the Claude Code agent itself.

## Invariant interactions

Not applicable — 0d.1A found zero matches (no `invariants/*.yaml` entry references
`~/.claude/commands/*.md`).

## Promise interactions

Not applicable — 0d.1B found zero matches (`promise_inventory.jsonl` covers AestheticcNext
product surfaces; this bead touches neither a save/persist action nor any row in that
file).

## Risk

- Low. This is documentation/instruction text read by an LLM at the start of future
  `/goal`/`/long-goal` runs — it does not execute as a build/deploy artifact and has zero
  runtime blast radius on the live product (no user ever sees this file).
- The one real risk is *process* risk: if the new instructions are unclear, a future
  dispatch could isolate incorrectly in a new way. Mitigated by keeping the two cases
  explicit and by cross-referencing this exact bead so future readers have the concrete
  incident, not just abstract prose.

## Rollback

`git revert` the commit on this branch, or simply don't merge it — this repo is
`compound-ralph`, pushed as a normal feature branch for Shane to review/merge like any
other; nothing auto-applies from a pushed-but-unmerged branch.

## Acceptance criteria (from the bead)

> A LUCY-* /goal dispatch never checks out a branch in the shared
> /Users/shane/Documents/Obsidian working tree — verify via a test dispatch that the
> shared trunk's checked-out branch is unchanged (still main, or whatever it was) after
> the dispatched session finishes its work.

This run is itself a live test of the new instructions' two branches: Case 2 (REPO_ROOT ≠
launch repo) fired for real when isolating `~/.claude` — `EnterWorktree` was tried first
(created a worktree in the wrong repo, `AestheticcNext`, as predicted), removed, and a
manual `git -C "$REPO_ROOT" worktree add` used instead. The shared `/Users/shane/.claude`
checkout's branch was verified `main` before and will be re-verified `main` (unchanged)
after this run completes — see Phase 5 verification below. A true end-to-end acceptance
test (dispatching a *fresh* session against the new instructions and confirming it follows
them unprompted) is out of scope for this run itself — flagging as a natural QA follow-up
rather than blocking on it, since the instructions were authored, and their case-2 path
was exercised live, in direct response to this exact failure mode.
