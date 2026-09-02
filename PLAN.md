# PLAN — LUCY-h0ypj: /goal Phase 4 QA — GLM primary, Codex fallback on GLM outage

## Goal

Make `~/.claude/commands/goal.md`'s Phase 4 try GLM (`glm exec`) first as the adversarial QA
reviewer, falling back to the existing Codex invocation only on a genuine GLM call failure
(non-zero exit), without changing the verdict schema, evidence directory structure, or the
3-round cap logic.

## Bead context (verbatim)

> goal.md Phase 4 currently runs Codex exclusively as the adversarial QA reviewer (grep -n glm
> goal.md = 0 hits, confirmed 2026-09-01). Shane's decision (AI_FLEET_GRAND_PLAN_2026-09-01.md,
> 'Shane's calls' section, item 4): GLM is primary reviewer in /goal Phase 4; Codex is the
> fallback ONLY when 'glm review'/'glm exec' exits non-zero — mirrors the existing GLM-outage
> doctrine already documented elsewhere in Aestheticc/CLAUDE.md ('GLM fallback' section).
> Rationale given: ~$80/mo GLM flat-rate vs ~$200/mo Codex+Claude combined — GLM is the
> cheap-first resource, not the fallback.
>
> TASK: edit ~/.claude/commands/goal.md's Phase 4 ... so it:
> 1. Tries GLM first: pipe the SAME prompt/context currently sent to Codex ... to 'glm review'
>    or 'glm exec' (check ~/.local/bin/glm's actual interface first — it may not support the
>    exact same structured verdict.json contract Codex does; confirm before assuming parity).
> 2. On GLM exit 0: use its verdict, same PASS/NEEDS_CHANGES/BLOCK handling as today.
> 3. On GLM exit non-zero (genuine call failure, not 'no issues found' which is a clean exit 0):
>    fall back to the EXISTING Codex invocation unchanged, and note in the evidence/report 'GLM
>    unreachable, Codex fallback used'.
> 4. Do NOT change the verdict schema, evidence directory structure, or 3-round cap logic —
>    only which model produces the verdict.
>
> ... verify with a real, small test /goal-style QA round ... If glm's actual output format
> can't cleanly satisfy the existing verdict.json parsing, say so plainly rather than forcing a
> fit — that's a real finding to report back, not a blocker to route around silently.

## Affected files

- `~/.claude/commands/goal.md` (Phase 4 section, ~line 333-423; one added clause in "Failure
  modes to watch" §1 near line 701)

## Invariant interactions

None — `invariants/*.yaml` is an AestheticcNext code-repo concept (scheduling, data model).
This bead edits a tooling/skill file in the `~/.claude` config repo; no match.

## Promise interactions

None — `Product/Architecture/promise_inventory.jsonl` tracks AestheticcNext user-facing
save/persist promises. This bead touches no application code, no save action, no user surface.

## Interface findings (pre-Build spike — see `~/.local/bin/glm` read in full)

`glm`'s actual interface does **not** offer parity with `codex exec -`:

- `glm review "<focus>"` computes its own diff internally (`git diff "${BASE}...HEAD"; git diff`
  inside `--cd`) and wraps it in a **fixed** review template (its own severity/category list,
  free-text S1-S3 output) — it cannot accept our qa.md-driven prompt/diff/schema instead of its
  own. Wrong mode for this job.
- `glm exec "<question>"` takes an arbitrary free-form prompt with no built-in template — right
  mode — but the prompt is a **positional argv string**, not something the caller can pipe into
  the wrapper's own stdin (only its *inner* `claude -p` reads stdin, fed from `$TASK`, which
  `exec` mode always populates verbatim from argv, never from the caller's stdin). Piping our
  ~50-150KB qa.md+CLAUDE.md+PLANS+diff blob in as one argv string is possible in principle
  (typical macOS ARG_MAX is well above that) but fragile for the rare oversized diff (this repo
  has documented precedent of a diff blowing a 1MB cap — `feedback_codex_qa_input_limit_drizzle_snapshot`
  — for Codex's own stdin cap, a different limit but the same class of failure) and it's simply
  not "the same" delivery mechanism as stdin piping.
- **Resolution:** `glm exec`'s inner `claude -p` is granted the `Read` tool (see
  `ALLOWED_TOOLS` in the script for non-build modes). So: write the exact same assembled
  context (qa.md + CLAUDE.md + PLANS + diff + evidence-artifact paths — byte-identical to what
  Codex receives) to a file under `$EVIDENCE_DIR` once, then pass GLM a short instruction
  argv-string telling it to `Read` that file and reply with only the JSON verdict. This
  delivers the *same content* GLM would have gotten via stdin, sidesteps the ARG_MAX/positional
  limitation entirely, and — as a byproduct — Codex now reads the identical context from the
  same file too (via stdin redirect) instead of a separately-assembled heredoc, so the two
  reviewers are provably looking at identical bytes.
- **Output format:** `glm exec -o FILE` writes the raw text response verbatim (`--output-format
  text`, no `^codex$`/`^tokens used$` markers, no prompt echo). This is actually *simpler* to
  parse than Codex's dual-formatted output, provided GLM actually returns bare JSON as
  instructed. It sometimes won't (a chatty model may add prose or a ```json fence despite
  instructions) — added a `python3 -c "json.load(...)"` validation step: valid JSON with a
  `verdict` + `findings` key → trust it; anything else (non-zero exit OR invalid/missing JSON)
  → treat as a GLM failure and fall back to Codex, logging why. This does not change the verdict
  *schema* (the JSON shape required is identical either way) — it only adds a delivery-format
  safety net, which is squarely what the bead asked for ("say so plainly rather than forcing a
  fit").
- `fleet-guarded.sh` already supports `glm` as a first-class `KIND` (its own usage comment
  shows a `glm review` example) — no changes needed there, just call it with `glm` instead of
  `codex` for the primary attempt.

## Approach

1. Refactor Phase 4's context assembly (currently inline in the Codex heredoc) into a single
   `$EVIDENCE_DIR/qa-context-round-N.md` file, written once, appended with an explicit
   "return ONLY this JSON object" instruction.
2. Add a GLM attempt: `fleet-guarded.sh glm ... glm exec --cd "$(pwd)" -o
   "$EVIDENCE_DIR/glm-verdict-round-N.txt" "<Read-the-context-file instruction>"`, capture
   `GLM_EXIT`, write `glm-exit-code-round-N.txt`.
3. Validate: `GLM_EXIT -eq 0` AND the output file parses as JSON with `verdict`+`findings` →
   `VERDICT_SOURCE=glm`, use it directly (no marker extraction needed).
4. Else → log `GLM unreachable, Codex fallback used (round-N)` to a `reviewer-notes` file in
   `$EVIDENCE_DIR`, then run the **existing** Codex invocation unchanged (same flags, same
   `fleet-guarded.sh codex` wrapper, same marker-extraction parsing note), just reading the
   shared context file via stdin redirect instead of the old inline heredoc. `VERDICT_SOURCE=codex`.
5. Update the two downstream prose sections ("Before acting on any S1/S2..." and "Parse the
   (calibrated) verdict") to say "the reviewer (GLM or Codex fallback)" instead of assuming
   Codex, and to branch parsing on `$VERDICT_SOURCE`.
6. Add one clarifying sentence to "Failure modes to watch" §1 noting the dual-format marker
   extraction applies only when the Codex fallback path runs.
7. Section header renamed: "Phase 4 — QA (GLM primary, Codex fallback on GLM outage;
   adversarial)".
8. No change to: the qa-command/output/exit-code test-gate block, the 3-round cap, evidence
   directory layout/paths for the pre-existing three mandatory artifacts, verdict.json schema,
   PASS/NEEDS_CHANGES/BLOCK semantics, or the calibration-gate re-check instructions.

## Risk

- **Low blast radius, high frequency of use.** This is a hot-path file every `/goal` worker
  reads (per the bead's own framing). A syntax error in the bash blocks would break QA for
  every future `/goal` run system-wide. Mitigated by: `bash -n` syntax-checking every extracted
  bash block, and running a real end-to-end QA round against a tiny throwaway diff before
  calling this done (per the bead's explicit instruction, not skippable).
- **GLM cost/quota**: every `/goal` run now makes a GLM call it didn't make before (previously
  GLM was unused in this path). Bounded by the same `fleet-guarded.sh` concurrency budget as
  Codex; GLM is flat-rate per Shane's own stated rationale, so no incremental spend risk.
- **False "GLM failure" from an overly strict JSON validator** could waste real GLM calls and
  silently double every QA round's cost (GLM then Codex every time). Mitigated by testing the
  validator against both a clean JSON response and a deliberately malformed one.
- **This is not an AestheticcNext code change** — no multi-tenancy/SQL/Stripe/migration surface
  at all. `/review`'s AestheticcNext-specific gates don't apply; this repo's own tsc/lint/test
  commands don't apply either (it's a markdown skill file with embedded bash — "tsc/lint clean"
  per acceptance criteria means the embedded bash blocks are syntactically valid, verified via
  `bash -n`).

## Rollback

`git revert` the single commit on `goal/lucy-h0ypj`, or just don't land the branch — the file
this touches is read fresh by each future `/goal` invocation, so reverting is a plain file
revert with no migration/data-shape concerns.

## Acceptance criteria (from the bead, restated as pass/fail)

- [ ] goal.md Phase 4 tries GLM first (`glm exec`), falls back to Codex only on GLM non-zero
      exit (or, per the interface-finding above, on GLM exit-0-but-unparseable-JSON, logged
      explicitly as the same class of failure).
- [ ] Verdict schema, evidence-directory structure, and 3-round-cap logic are unchanged.
- [ ] Change is verified against a real (or closest-safe-realistic) QA round, not just a
      read-through — run the new Phase 4 bash against this very worktree's diff (this PLAN.md +
      the goal.md edit itself) as the test round.
- [ ] The embedded bash blocks in the edited Phase 4 section are syntactically sound
      (`bash -n` on each extracted block).
- [ ] Any place GLM's real interface can't cleanly satisfy the existing contract is stated
      plainly in the report (see "Interface findings" above) rather than silently routed around.
