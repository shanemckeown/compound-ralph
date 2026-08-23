#!/usr/bin/env bash
# voice-check.sh — UserPromptSubmit hook
#
# Detects external-writing intent in the user's prompt and injects Shane's
# voice-checklist as additionalContext so Claude doesn't slip back into AI tells
# (em dashes, subject-less fragments, AI-bro jargon).
#
# Reads JSON from stdin: {"prompt": "...", ...}
# Outputs hookSpecificOutput JSON if a trigger matches; exit 0 silently otherwise.
#
# Full rules: ~/.claude/projects/-Users-shane-Documents-Obsidian/memory/writing_style.md
# Corpus:    ~/Documents/Obsidian/Aestheticc/Growth/Outreach/SentEmails/

set -euo pipefail

INPUT="$(cat)"
PROMPT="$(echo "$INPUT" | jq -r '.prompt // ""' 2>/dev/null || echo "")"
[ -z "$PROMPT" ] && exit 0

P_LOWER="$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]')"

# External-writing trigger: literal word "draft" only. Shane's call 2026-05-13 —
# narrower than before (was 13 broad patterns matching email/post/name/follow-up
# intents). Accepts harmless misfires on "I did a draft last week"; the cost of
# a misfire is one extra hook context block, the cost of a miss is the 30-min
# voice-rebuild cycle that /draft prevents.
TRIGGERS=(
  "\\bdraft\\b"
)

MATCHED=0
for pattern in "${TRIGGERS[@]}"; do
  if echo "$P_LOWER" | grep -Eq "$pattern"; then
    MATCHED=1
    break
  fi
done

[ "$MATCHED" -eq 0 ] && exit 0

# Emit additionalContext. Single-line JSON so we don't fight escaping.
jq -n --arg ctx "$(cat <<'CTX'
🎙 EXTERNAL-WRITING DETECTED — /draft skill required.

**MANDATORY before producing a single line of draft prose for an external recipient:**

1. **Invoke the `/draft` skill** via the Skill tool. It lives at `~/.claude/skills/draft/SKILL.md`.
2. **Run the five gates** the skill describes:
   - Gate 1: Verify GBrain MCP alive (`mcp__gbrain__get_health`). If disconnected, STOP and ask Shane to reconnect via `/mcp`. Do NOT silently fall back to vault reads.
   - Gate 2: Read `Aestheticc/STATE_OF_THE_BUSINESS.md` AND `Aestheticc/Strategy/PRICING_STRATEGY_LIVE.md` fully.
   - Gate 3: Grep `Aestheticc/Growth/Outreach/SentEmails/` for the closest audience exemplar. Print the path.
   - Gate 4: Read `~/.claude/projects/-Users-shane-Documents-Obsidian/memory/writing_style.md` Learning log, entries from the last 60 days.
   - Gate 5: Query GBrain for recipient + product facts I'm about to claim.
3. **Print the verification block** verbatim (see SKILL.md format). It must list:
   - GBrain status
   - STATE last_updated date + age in days
   - Current Solo / Team price from PRICING_STRATEGY_LIVE
   - SentEmails exemplar path
   - Facts I'm asserting (with source) AND facts I'd normally have written but am dropping because I can't verify.

**Voice anti-patterns (still apply, full list in writing_style.md):**

- NO em dashes (—). Hyphens only for number ranges.
- NO subject-less fragments. Every sentence has an "I", "we", "you", or concrete subject.
- NO AI-bro jargon ("AI-native", "load-bearing", "first-class", "reset the category", "10x", "moat", "wrappers", "vertical-SaaS").
- NO clever section names. "Current go-to-market strategy" not "Where the GTM is going". "Pricing" not "How we charge".
- NO sign-off filler ("looking forward", "don't hesitate") in cold/peer/exec. Use "Kind regards, Shane" or "Shane".
- Don't sell the concept back to the recipient. Don't issue commands. Don't promise future actions you haven't taken.
- "Pabau is the elephant" / "X is Y, also Z" / "the honest version, not the deck version" — all AI-tell phrasings. Just describe.

**If you skip the gates and start drafting prose, Shane will catch it and the rebuild costs both of you time. The whole point of /draft is the 60-second pre-flight that prevents the 30-minute rewrite.**

**Post-draft:** if Shane corrects voice or facts in this turn, APPEND a dated entry to writing_style.md learning log AND, if it's a product-fact correction, propose an update to STATE_OF_THE_BUSINESS.md.
CTX
)" '{
  hookSpecificOutput: {
    hookEventName: "UserPromptSubmit",
    additionalContext: $ctx
  }
}'
