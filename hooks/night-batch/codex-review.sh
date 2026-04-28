#!/usr/bin/env bash
# codex-review.sh — Gate 3 (gating): independent adversarial review via OpenAI Codex.
# Sibling to blind-claude-review.sh — same contract, different reviewer family.
# Runs Codex via `codex exec --output-schema` for structured JSON verdict, then
# emits SUMMARY/BLOCKERS/[BLOCKER]/[CONCERN]/[NIT] lines mirroring the blind-claude
# format so morning-report.sh and grep-based parsing work uniformly.
#
# Bead: LUCY-llp1 (Codex Gate 3 swap, Shane decision 2026-04-27)
#
# Usage: codex-review.sh <BEAD_ID> <WORKTREE_PATH>
# Exit:
#   0 = no BLOCKERs found (clean — bead can ship)
#   1 = BLOCKER(s) found (bead should not ship without human review)
#   2 = bad usage / setup error (codex CLI missing, bad worktree, etc.)

set -u
BEAD="${1:-}"
WORKTREE="${2:-}"

if [[ -z "$BEAD" || -z "$WORKTREE" ]]; then
  echo "FAIL: usage: $0 <BEAD_ID> <WORKTREE_PATH>" >&2
  exit 2
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "FAIL: codex CLI not found on PATH (install with: brew install codex / npm install -g @openai/codex)" >&2
  exit 2
fi

if [[ ! -d "$WORKTREE/.git" ]] && [[ ! -f "$WORKTREE/.git" ]]; then
  echo "FAIL: $WORKTREE is not a git worktree" >&2
  exit 2
fi

cd "$WORKTREE" || { echo "FAIL: cannot cd to $WORKTREE" >&2; exit 2; }

BASE=$(git merge-base HEAD main 2>/dev/null || git merge-base HEAD origin/main 2>/dev/null || echo "")
if [[ -z "$BASE" ]]; then
  echo "FAIL: cannot find merge-base against main" >&2
  exit 2
fi

# Cap diff at 30k chars (mirrors blind-claude). Auto-eligible beads ≤200 LOC.
DIFF=$(git diff "$BASE"..HEAD 2>/dev/null | head -c 30000)
if [[ -z "$DIFF" ]]; then
  echo "PASS: no diff to review (Gate 2 should have caught this)"
  exit 0
fi

# Adversarial prompt — same format as blind-claude so output is parseable
# uniformly. The Codex review subcommand handles the diff context itself
# via --base; we still inline the diff in the prompt because Codex's review
# mode + a custom prompt gives more control over output format.
PROMPT_FILE=$(mktemp -t codex-review-XXXXXX.md)
cat > "$PROMPT_FILE" <<'EOF'
# Independent diff review (adversarial)

You are an experienced reviewer for an AI-native CRM startup (Aestheticc).
You have NOT seen this code before. You are skeptical, looking only for
real-world failure modes that would bite in production.

Aestheticc-specific high-priority concerns (any of these = BLOCKER):
- Multi-tenancy / RLS leaks (cross-clinic data exposure)
- Auth boundary violations (JWT vs DB session drift, role bypass)
- Stripe / payment correctness (signature verify, idempotency, race conditions)
- GDPR / cascade-delete completeness
- Race conditions, missing transactions, double-execution

Categorise issues:
- BLOCKER: real-world failure mode (correctness bug, security hole, data loss)
- CONCERN: should fix soon, won't break prod immediately
- NIT: taste / style only — omit unless nothing else to report

Output format (STRICT — gating script greps for these literal markers):

```
SUMMARY: <one line — overall verdict>
BLOCKERS: <count>
CONCERNS: <count>
NITS: <count>

[BLOCKER] file:line — <one-line failure mode>
[BLOCKER] ...
[CONCERN] file:line — <one-line>
[NIT] ...
```

If clean:
```
SUMMARY: clean — no BLOCKER-grade issues
BLOCKERS: 0
```

Bias toward finding problems. The implementer was Claude Opus — assume
plausible-looking but may have missed something subtle. Spend most attention
on:
- Anything the diff doesn't test
- Boundaries: frontend↔backend, app↔DB, web↔mobile, prod↔staging
- Side effects in shared utilities

Do NOT explain what code does — only what could break.

EOF

# Run codex review against main with the strict-format prompt.
# Use --ephemeral so no session bloat. read-only sandbox: Codex can read
# repo files for context but can't modify anything (review-only).
RESPONSE_FILE=$(mktemp -t codex-resp-XXXXXX.txt)
codex exec \
  --cd "$WORKTREE" \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  "$(cat "$PROMPT_FILE")

---

## Diff to review (against main)

\`\`\`diff
$DIFF
\`\`\`

End of input. Provide your verdict in the strict output format above.
No preamble, no markdown fencing around the verdict block." \
  > "$RESPONSE_FILE" 2>&1
codex_exit=$?

# Parse the response.
# Codex's session log echoes the full prompt (which contains template
# SUMMARY/BLOCKERS markers), then has the actual response after a `codex`
# section marker, then a `tokens used` footer (sometimes followed by a
# duplicate verdict). Extract ONLY between `^codex$` and `^tokens used$`
# to avoid counting the template markers.
RESPONSE_START=$(grep -n '^codex$' "$RESPONSE_FILE" | tail -1 | cut -d: -f1)
RESPONSE_END=$(grep -n '^tokens used$' "$RESPONSE_FILE" | head -1 | cut -d: -f1)

if [[ -n "$RESPONSE_START" && -n "$RESPONSE_END" && "$RESPONSE_END" -gt "$RESPONSE_START" ]]; then
  RESPONSE_BODY=$(sed -n "${RESPONSE_START},${RESPONSE_END}p" "$RESPONSE_FILE")
else
  # Fallback: try to find verdict in last 30 lines (post-prompt echo)
  RESPONSE_BODY=$(tail -30 "$RESPONSE_FILE")
fi

SUMMARY=$(echo "$RESPONSE_BODY" | grep -E '^SUMMARY:' | head -1 | sed 's/^SUMMARY: //')
# tr -d strips trailing whitespace/newlines from grep -c output that otherwise
# break the [[ -gt ]] arithmetic test downstream (LUCY-gt32 fix 2026-04-27)
BLOCKER_COUNT=$(echo "$RESPONSE_BODY" | grep -cE '^\[BLOCKER\]' 2>/dev/null | tr -d '[:space:]' || echo 0)
CONCERN_COUNT=$(echo "$RESPONSE_BODY" | grep -cE '^\[CONCERN\]' 2>/dev/null | tr -d '[:space:]' || echo 0)
NIT_COUNT=$(echo "$RESPONSE_BODY" | grep -cE '^\[NIT\]' 2>/dev/null | tr -d '[:space:]' || echo 0)

# Stash full response for morning-report.sh
mkdir -p "$WORKTREE/.compound-review" 2>/dev/null
cp "$RESPONSE_FILE" "$WORKTREE/.compound-review/codex-${BEAD}.txt" 2>/dev/null

rm -f "$PROMPT_FILE"

if [[ $codex_exit -ne 0 ]]; then
  echo "FAIL: codex exited code=$codex_exit (review incomplete; defaulting to gated)" >&2
  echo "First lines of response:" >&2
  head -10 "$RESPONSE_FILE" | sed 's/^/  /' >&2
  rm -f "$RESPONSE_FILE"
  exit 1
fi
rm -f "$RESPONSE_FILE"

echo "codex verdict: ${SUMMARY:-(no summary parsed)}"
echo "  BLOCKERS=$BLOCKER_COUNT  CONCERNS=$CONCERN_COUNT  NITS=$NIT_COUNT"

# Stricter gate (Codex-flagged 2026-04-27): fail-closed on malformed output.
# If we don't have a SUMMARY, Codex's response was malformed — don't trust the
# zero-BLOCKER signal as a PASS.
if [[ -z "$SUMMARY" ]]; then
  echo "FAIL: no SUMMARY line in Codex output — verdict unparseable, gating defensively" >&2
  exit 1
fi

# If "BLOCKERS: N" header claims a count but [BLOCKER] line count differs,
# the output was truncated or malformed. Don't pass.
HEADER_BLOCKER_COUNT=$(echo "$RESPONSE_BODY" | grep -E '^BLOCKERS:' | head -1 | sed 's/^BLOCKERS:[[:space:]]*//' | tr -d '[:space:]')
if [[ -n "$HEADER_BLOCKER_COUNT" && "$HEADER_BLOCKER_COUNT" != "$BLOCKER_COUNT" ]]; then
  echo "FAIL: Codex BLOCKERS header ($HEADER_BLOCKER_COUNT) != [BLOCKER] line count ($BLOCKER_COUNT) — malformed verdict" >&2
  exit 1
fi

if [[ ${BLOCKER_COUNT:-0} -gt 0 ]]; then
  echo "FAIL: $BLOCKER_COUNT BLOCKER(s) flagged — review .compound-review/codex-${BEAD}.txt" >&2
  exit 1
fi

echo "PASS: no BLOCKER-grade issues (codex)"
exit 0
