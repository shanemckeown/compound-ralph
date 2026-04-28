#!/usr/bin/env bash
# blind-claude-review.sh — Gate 3: independent adversarial review.
# Spawns a fresh `claude -p` with no repo context — feed it ONLY the diff
# and an adversarial framing. The implementing claude knows the codebase;
# this one starts cold and can't rationalise its own changes. Same pattern
# Codex challenge would give us, no extra dependency.
#
# Bead: LUCY-4vox
#
# Usage: blind-claude-review.sh <BEAD_ID> <WORKTREE_PATH>
# Exit:
#   0 = no BLOCKERs found (CONCERN/NIT/clean all pass)
#   1 = BLOCKER(s) found — bead should not ship without human review
#   2 = bad usage / setup error

set -u
BEAD="${1:-}"
WORKTREE="${2:-}"
if [[ -z "$BEAD" || -z "$WORKTREE" ]]; then
  echo "FAIL: usage: $0 <BEAD_ID> <WORKTREE_PATH>" >&2
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

# Cap the diff at 30k chars so the prompt stays small. Auto-eligible beads
# are ≤200 LOC anyway — this cap is the safety belt for outliers.
DIFF=$(git diff "$BASE"..HEAD 2>/dev/null | head -c 30000)
if [[ -z "$DIFF" ]]; then
  echo "PASS: no diff to review (Gate 2 should have caught this)"
  exit 0
fi

# Adversarial prompt — the "blind" framing. No repo context provided.
PROMPT_FILE=$(mktemp -t blind-claude-XXXXXX.md)
cat > "$PROMPT_FILE" <<EOF
# Independent diff review

You have NO context about this repository. You have NEVER seen this code
before. You are a skeptical, experienced reviewer whose only job is to
catch real-world failure modes in the diff below.

Categorise each issue you find:

- **BLOCKER**: a correctness bug that would cause a real-world failure
  (null deref, wrong condition, broken contract, security hole, race,
  data-loss path). Anything that needs fixing before merge.
- **CONCERN**: worth fixing but won't break production immediately
  (poor error handling, logic that's correct but fragile, missing edge case
  that might not occur in practice).
- **NIT**: taste / style only. Don't list these unless you have nothing else.

For BLOCKERs, name the file:line and explain the failure mode in one line.
Be skeptical — assume the implementing engineer was overconfident.

Output format (strict — the gating script greps for these literal markers):

\`\`\`
SUMMARY: <one line — overall verdict>
BLOCKERS: <count>
CONCERNS: <count>
NITS: <count>

[BLOCKER] file:line — <one-line failure mode>
[BLOCKER] ...
[CONCERN] file:line — <one-line>
[NIT] ...
\`\`\`

If the diff is clean, output:
\`\`\`
SUMMARY: clean — no BLOCKER-grade issues
BLOCKERS: 0
\`\`\`

Do NOT suggest improvements unless they prevent a real-world bug. Do NOT
explain what the code does — only what could break.

---

## Diff to review (against main)

\`\`\`diff
$DIFF
\`\`\`

---

End of input. Provide your verdict above the diff line, in the strict
output format. No preamble.
EOF

# Run blind claude. Critically: do NOT pass --add-dir or any context flags.
# The whole point is that this claude has zero project knowledge.
RESPONSE_FILE=$(mktemp -t blind-claude-resp-XXXXXX.txt)
claude --dangerously-skip-permissions -p "$(cat "$PROMPT_FILE")" > "$RESPONSE_FILE" 2>&1
claude_exit=$?

# Parse the response
SUMMARY=$(grep -E '^SUMMARY:' "$RESPONSE_FILE" | head -1 | sed 's/^SUMMARY: //')
BLOCKER_COUNT=$(grep -cE '^\[BLOCKER\]' "$RESPONSE_FILE" 2>/dev/null || echo 0)
CONCERN_COUNT=$(grep -cE '^\[CONCERN\]' "$RESPONSE_FILE" 2>/dev/null || echo 0)
NIT_COUNT=$(grep -cE '^\[NIT\]' "$RESPONSE_FILE" 2>/dev/null || echo 0)

# Stash the full response in the worktree's `.compound-review/` so morning
# report (LUCY-fef4) can surface CONCERN/NIT lines without re-running claude.
mkdir -p "$WORKTREE/.compound-review" 2>/dev/null
cp "$RESPONSE_FILE" "$WORKTREE/.compound-review/blind-claude-${BEAD}.txt" 2>/dev/null

rm -f "$PROMPT_FILE"

if [[ $claude_exit -ne 0 ]]; then
  echo "FAIL: blind claude exited code=$claude_exit (review was incomplete; defaulting to gated)" >&2
  echo "First lines of response:" >&2
  head -10 "$RESPONSE_FILE" | sed 's/^/  /' >&2
  rm -f "$RESPONSE_FILE"
  exit 1
fi
rm -f "$RESPONSE_FILE"

echo "blind-claude verdict: ${SUMMARY:-(no summary parsed)}"
echo "  BLOCKERS=$BLOCKER_COUNT  CONCERNS=$CONCERN_COUNT  NITS=$NIT_COUNT"
if [[ ${BLOCKER_COUNT:-0} -gt 0 ]]; then
  echo "FAIL: $BLOCKER_COUNT BLOCKER(s) flagged — review .compound-review/blind-claude-${BEAD}.txt" >&2
  exit 1
fi

echo "PASS: no BLOCKER-grade issues"
exit 0
