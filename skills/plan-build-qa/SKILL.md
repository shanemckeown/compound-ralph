---
name: plan-build-qa
description: |
  Dual-model workflow for non-trivial code changes. Plan + Build with Claude Opus 4.7,
  QA with OpenAI Codex 5.5 (adversarial, model-independent). Replaces "Shane reviews
  every diff" with structured eval gates that catch bugs different model families miss.
trigger:
  - "/plan-build-qa <description>"
  - User mentions "non-trivial diff", "needs review", "complex change"
status: VALIDATED 2026-04-27 — first real fix loop on PR #291 (LUCY-whcq), 4 rounds, caught S1 security issue + race condition + filed merge-blocker follow-up bead. Ready for global install.
---

# Plan-Build-QA Skill

> **Why this exists.** The default Claude Code workflow is Plan → Build → Shane-reviews-everything. That doesn't scale past one human. This skill replaces the human-review step with a model-independent QA pass (Codex 5.5), so Shane only sees diffs that already passed adversarial review by a different model family.
>
> **Source thinking:** YC AI-native playbook (software factories) + Vtrivedy10's "traces + evals are the lifeblood" + the bookmark cluster on agent harnesses (rohit4verse, gregpr07).

## When to invoke

Use `/plan-build-qa <description>` for:

- **Non-trivial diffs** — anything > 50 LOC, anything touching shared logic, anything spanning > 2 files
- **Code where you'd normally want a second opinion** — schema-adjacent, payment-adjacent, auth-adjacent, race-condition-suspect
- **Anything you're not 100% sure about** — the marginal cost of QA is ~£0.10-0.50 in Codex tokens; the marginal cost of a prod bug is hours of debugging

Do **not** invoke for:

- One-line typo fixes
- Config-only changes
- Doc changes (use `/ship` directly)
- Anything where the auto-eligible night-batch is already going to handle it

## Phases

### Phase 1: Plan (Claude Opus 4.7)

Equivalent to `/plan-eng-review` if the change is architecturally significant; else inline planning.

Output: a `PLAN.md` file in the worktree containing:
- **Goal** — one sentence
- **Affected files** — list
- **Approach** — bullet list of what changes
- **Risk** — what could break
- **Rollback** — how to undo
- **Acceptance criteria** — concrete pass/fail checks

This file is the spec. It feeds Phase 3 QA.

### Phase 2: Build (Claude Opus 4.7)

Standard Claude Code work. Implements PLAN.md.

Outputs:
- The diff
- Any tests added
- Updated PLAN.md with notes on deviations from plan

### Phase 3: QA (OpenAI Codex)

Invoked via `codex` CLI v0.125.0+ (`codex --version` to confirm).

**Two viable patterns** depending on output need:

#### Pattern A — `codex review` (markdown output, simpler)

Best when you just want a review summary in chat / log.

```bash
codex review \
  --base main \
  --title "$(git log -1 --pretty=%s)" \
  "$(cat ~/.claude/skills/plan-build-qa/prompts/qa.md)"
```

Real flags (from `codex review --help`):
- `--base <BRANCH>` — review changes vs base branch (use `main`)
- `--uncommitted` — review staged/unstaged/untracked instead of branch diff
- `--commit <SHA>` — review a single commit
- `--title <TITLE>` — surfaces in the review header
- `-c key=value` — config override (e.g. `-c model="o3"` to pin model)
- `[PROMPT]` is positional; `-` reads from stdin

There is **no `--prompt-file`** and **no `--output-schema`** on `review`. For structured JSON, use Pattern B.

#### Pattern B — `codex exec --output-schema` (structured JSON, recommended for the auto-pipeline)

Best when downstream automation parses the verdict.

```bash
codex exec \
  --cd "$(pwd)" \
  --ephemeral \
  --output-schema ~/.claude/skills/plan-build-qa/schema/verdict.json \
  --sandbox read-only \
  -c model="gpt-5-codex" \
  "$(cat ~/.claude/skills/plan-build-qa/prompts/qa.md)
---
PLAN.md:
$(cat PLAN.md)
---
DIFF:
$(git diff main...HEAD)" \
  > .qa/codex-verdict.json
```

Real flags (from `codex exec --help`):
- `-C, --cd <DIR>` — working dir
- `--ephemeral` — no session persistence (good for one-shot QA)
- `--output-schema <FILE>` — JSON Schema describing the response shape
- `--sandbox read-only` — Codex can read but not write (right for review-only)
- `-c model="..."` — model override (currently `gpt-5-codex` is the default ChatGPT-5-tier reviewer; check `codex --help` for current options on your install)
- `--ignore-user-config` if you don't want `~/.codex/config.toml` profiles applied
- `[PROMPT]` positional; pipe stdin or pass directly

**`verdict.json` schema** (write to `~/.claude/skills/plan-build-qa/schema/verdict.json`):

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["verdict", "findings"],
  "properties": {
    "verdict": {"type": "string", "enum": ["PASS", "NEEDS_CHANGES", "BLOCK"]},
    "summary": {"type": "string", "maxLength": 1000},
    "findings": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["severity", "file", "issue"],
        "properties": {
          "severity": {"type": "string", "enum": ["S1", "S2", "S3"]},
          "file": {"type": "string"},
          "line": {"type": "integer"},
          "issue": {"type": "string"},
          "fix": {"type": "string"}
        }
      }
    },
    "plan_acceptance_criteria_met": {"type": "boolean"},
    "missing_tests": {"type": "array", "items": {"type": "string"}}
  }
}
```

#### Prompt template (`prompts/qa.md`)

```
You are an adversarial code reviewer for an AI-native CRM startup (Aestheticc).

You will receive:
1. PLAN.md — the spec the implementer was given
2. A git diff implementing the spec
3. Project context (CLAUDE.md if present in cwd)

Your job:
- Identify what breaks. Race conditions, missing null checks, SQL injection,
  N+1 queries, RLS bypasses, untested edge cases, regressions in unrelated
  code paths, security gaps, accessibility regressions, mobile-vs-web drift.
- Verify each acceptance criterion in PLAN.md is actually met by the diff.
- Output a verdict per the JSON schema: PASS, NEEDS_CHANGES, or BLOCK.
- For findings: severity S1 (must fix before merge) / S2 (should fix soon) / S3 (nice-to-have).

Bias toward finding problems. The implementer is Claude Opus 4.7 — plausible
but may miss subtle things. Pay most attention to:
- Anything the diff doesn't test
- Anything the PLAN didn't anticipate but the diff touches
- Boundaries: frontend↔backend, app↔DB, web↔mobile, prod↔staging
- Multi-tenancy / RLS — Aestheticc is per-clinic isolated; cross-tenant leaks are S1

Output ONLY the JSON object matching the schema. No prose around it.
```

### Phase 4: Ship gates

After Phase 3:

- **PASS + no S1/S2 findings:** auto-proceed to `/ship` (still runs lint + test + tsc; commit + push)
- **PASS with S2 findings:** show findings to Shane; he chooses fix-now / log-as-followup-bead / accept-risk
- **NEEDS_CHANGES:** Claude Opus reads findings, applies fixes, GOTO Phase 3 (max 3 iterations)
- **BLOCK:** stop. Surface in Slack DM. Shane manually inspects.

Hard rules:

- Never auto-merge if Codex returns BLOCK
- Never override S1 findings without explicit Shane confirmation
- After 3 QA iterations without PASS, escalate to Shane

## Implementation notes

### Relationship to gstack `/codex` skill

The gstack-installed `/codex` skill at `~/.claude/skills/codex/` provides three modes (review / challenge / consult). For Aestheticc QA we use the same `codex` CLI but invoke it from this skill with our specific schema + prompt. Two paths:

- **In-session, interactive use:** call gstack's `/codex review` directly (it wraps the same CLI). Output is markdown, fed back into the chat.
- **Automated pipeline (night-batch Gate 3, /plan-build-qa Phase 3):** invoke `codex exec --output-schema` directly per Pattern B above so we get parseable JSON.

If gstack updates and provides structured output natively, switch to using the gstack wrapper everywhere.

### Quota usage (not £-cost)

Shane's Codex runs via the **OpenAI ChatGPT subscription**, not pay-per-token API. So there's no marginal £-cost per invocation — the constraint is the **rolling subscription quota window**.

What this changes vs the API model:

- **No need for cost caps.** Just run it.
- **Quota burn is the real constraint.** If you blow through the window doing 50 QA passes overnight, you can't use Codex for live interactive work the next morning.
- **Per-skill burn budget is implicit.** Roughly: count invocations, not pounds. The night-batch should respect a max-invocations-per-window setting.

Mitigation patterns (apply when quota matters more than completeness):

- Skip Pattern B's structured output for trivial diffs — Pattern A's `codex review` markdown is cheaper in tokens
- Cap rebuild iterations at 3 (fewer round-trips through Codex)
- Use `--ephemeral` (no session bloat retained server-side)
- For the night-batch Gate 3 A/B period: run blind-claude on every bead, but only run Codex on beads where blind-claude returned NEEDS_CHANGES or BLOCK — this catches false-PASSes from blind-claude without burning Codex on the easy wins

Track usage retroactively: parse token counts from JSON output (Pattern B) or check ChatGPT's usage page weekly.

If quota becomes a real constraint during the sprint, the simplest fix is **don't run plan-build-qa during 9-7 clinic hours** — same window as the no-prod-deploy rule. Lets the quota refresh while you're out.

### Logging for evals

Every QA pass writes to `~/.claude/qa-log/<date>/<bead-id>.json` with:
- The diff
- Codex verdict
- Final disposition (merged / kicked back / blocked)
- (Eventually) ground truth: did a bug appear in this diff in production?

After 50 passes, this is an eval set. You can compare:
- Codex verdict accuracy (did BLOCKs catch real bugs? did PASSes have false negatives?)
- vs blind-claude verdict accuracy (during the A/B period)
- vs a future third reviewer

This is the Vtrivedy10 "traces + evals = lifeblood" pattern in concrete form.

## Integration with existing infra

| Existing | Relationship |
|---|---|
| `/review` (gstack) | Subset — `/review` is one model, structural-only. Plan-Build-QA adds Codex independence + plan-vs-diff verification. |
| `/ship` (gstack) | Plan-Build-QA invokes `/ship` after Phase 4 PASS; doesn't replace it. |
| Night-batch Gate 3 | Same pattern at the autonomous-overnight layer. Gate 3 currently uses blind-claude; A/B'ing with Codex (HANDOVER decision 6) is the same Codex-as-adversary pattern. |
| `/codex` skill | Plan-Build-QA wraps it. |
| Conductor workspaces | Plan-Build-QA runs inside any Conductor workspace; `PLAN.md` and `.qa/` live in the workspace. |

## Failure modes (known)

1. **Codex hallucinates findings.** Mitigation: log everything, after 50 passes prune findings that never surfaced as real bugs.
2. **Codex passes obviously broken code because it doesn't understand the project conventions.** Mitigation: include CLAUDE.md in the context bundle for QA. The `--sandbox read-only` flag is what lets Codex actually read those files — verified during the 2026-04-27 first loop.
3. **Quota runs hot on a recursive QA loop.** Mitigation: 3-iteration hard cap. Today's loop did 4; 4 is one too many.
4. **Codex API down or unavailable.** Mitigation: **fail-closed, do NOT silently fall back to blind-claude.** A silent fallback removes the model-independence guarantee that's the whole point of this skill. When Codex is unavailable: surface the error to Shane; if he wants to ship anyway, that's his explicit override (logged), not a silent degradation. (Codex review of this skill, 2026-04-27, corrected an earlier silent-fallback recommendation here.)
5. **Severity is non-deterministic across runs.** Same issue can be CONCERN on one run and BLOCKER on the next — sampled differently each time. Don't get whiplash; trust the underlying issue. **If Codex flags a security/correctness issue at any severity, file a follow-up bead at minimum.** Today's example: open-redirect risk on PR #291 was CONCERN on round-3 but BLOCKER on round-5 (same code).
6. **Codex's output file is dual-formatted.** It contains the prompt echo + the assistant response + a tokens-used line + a duplicate verdict at the end. Naïve grep matches the prompt template's literal markers ("BLOCKERS:", "[BLOCKER]") and over-counts. Always extract between `^codex$` and `^tokens used$` first. (Bug history: LUCY-gt32.)
7. **Iteration spiral on structural issues.** When Codex keeps surfacing the same architectural problem (e.g. "no allowlist" can't be fixed by a single LOC change), don't keep re-running the loop. File the issue as a follow-up bead and ship the bounded fixes that closed in earlier rounds.

8. **Pass big prompts via stdin, not argv (Codex review of this skill, 2026-04-27).** A `codex exec "$(cat huge-file.txt)"` invocation can hit the shell arg-list limit (`E2BIG`) on prompts >128KB, and even sub-limit prompts may cause codex to wait for stdin EOF after consuming the positional arg. Always pipe: `cat prompt.txt | codex exec --skip-git-repo-check --ephemeral --sandbox read-only -`. The trailing `-` tells Codex to read instructions from stdin. Verified during this skill's own self-review when the argv-form invocation hung indefinitely.

9. **Diff collection misses uncommitted state.** `git diff main...HEAD` only sees committed changes. If the agent's last build step has uncommitted output (e.g. format-on-save ran after the last commit, build artefacts), Codex doesn't see them. For a thorough review include `git diff --cached` (staged) AND `git diff` (unstaged) AND `git ls-files --others --exclude-standard` (new untracked). Or commit-before-review is the simplest invariant — auto-bead.sh already enforces this for the night-batch.

## Lessons from the first real loop (PR #291, 2026-04-27)

The first production use of Plan-Build-QA was Codex reviewing the booking redirect feature. 4 rounds, then declared done. Captured here so future Lucys don't re-discover.

### Round-by-round

| Round | Build action | Codex verdict | Lesson |
|---|---|---|---|
| 1 (initial) | Original LUCY-whcq feat commit | 0 BLOCKERS, 2 CONCERNS (race + 400-on-malformed) | Both fixable, both in the right severity. |
| 2 | Fixed both via spread-form transforms | 1 **BLOCKER** (different file: `booking-confirmation.tsx`, missing local URL validation), 1 CONCERN | Codex finds new issues each pass. Trust it. |
| 3 | Added `sanitizeRedirectUrl` to the new file | 1 **BLOCKER** still flagged | I'd missed a second `window.location.href = redirectUrl` site in the same file (the "Continue now" button). |
| 4 | Replaced ALL usages of `redirectUrl` with `safeRedirectUrl` (4 sites in 2 files) | 0 BLOCKERS, 2 CONCERNS (race in new file + structural allowlist) | Race fix mirrors payment-success.tsx; allowlist is structural → file as P1 follow-up bead, don't loop. |
| 5 | Re-ran post-parser-fix | **BLOCKER** flipped on the same allowlist issue | Severity volatility. Treat the underlying issue as the truth, not the label. |

### Iron rules from this loop

1. **When sanitizing a value via a wrapper function, grep ALL call sites in the file before declaring done.** `grep -n "window.location.href = redirectUrl" file.tsx` would have caught my round-2 oversight in 5 seconds.
2. **Stop the loop at 3 iterations** — by round 3, remaining issues are usually structural (need PRODUCT decisions or schema changes), not implementation gaps. File as beads, ship the rest.
3. **`--sandbox read-only` controls writes, not reads (Codex's own review of this skill, 2026-04-27, corrected my earlier wrong claim).** Codex always has read access to files inside its `--cd` directory regardless of sandbox flag. The `read-only` setting prevents Codex from MODIFYING files (the right choice for a reviewer — we don't want it editing what it's reviewing). The convention-context catches (duplicate "Last reviewed" markers, missed `window.location.href` site) come from Codex reading the surrounding code, which it can do at any sandbox level. **Use `read-only` because we don't want a reviewer that can write; not because it grants reads.**
4. **Severity != reality.** A CONCERN that flips to BLOCKER on a later run is the same issue, not an escalation. File the bead immediately, don't wait for the BLOCKER label.
5. **Document `--sandbox read-only` as a hard requirement.** Some Codex CLIs default to `workspace-write`; that lets Codex modify files (not what we want for a reviewer) AND can confuse the reviewer about what's the diff vs what's its own write.

## Iteration log

- **2026-04-26 (Lucy-Opus-1M, draft):** First draft. Not yet used. Move to `~/.claude/skills/plan-build-qa/` after Shane reviews + first real run. Add cost data and Codex-specific tuning notes after 5-10 invocations.
- **2026-04-27 (Lucy-Opus-1M, validated):** First production use on PR #291 (LUCY-whcq booking redirect). 4 rounds. Caught a real S1 (open-redirect XSS via raw `window.location.href`), a race condition mirroring payment-success.tsx, and surfaced a structural security gap (no clinic-domain allowlist) that became merge-blocker LUCY-zk3p. Fixed parser bug LUCY-gt32 in the codex-review.sh sibling. Added 7 failure modes + 5 iron rules + per-round lesson table. Ready for global install at `~/.claude/skills/plan-build-qa/`.
- **2026-04-27 (Lucy-Opus-1M, post-self-review):** Submitted this SKILL.md to Codex for an independent review. Verdict: REVISE. Three real corrections landed: (a) `--sandbox read-only` controls writes not reads (sibling-read claim was wrong); (b) Codex-down fallback to blind-claude must fail-closed, not silently degrade; (c) shell-argv invocation hits limits on big prompts — prefer stdin pipe. Added failure modes 8-9. Same skill, more honest about its own behavior.
