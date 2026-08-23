#!/usr/bin/env python3
"""PostToolUse guard for Gmail search_threads.

search_threads returns ONLY the messages that matched the query, wrapped in a
thread object that looks complete. The dropped messages are the short ones --
"Thanks Shane.", "Can we please book the 4pm slot on Tuesday 11th August." --
which carry the decisions. Burned 2026-08-11 on Persona Medical: 5 of 14
messages returned, conclusion was "ghosted, 15 days silent", reality was a
confirmed meeting that afternoon.

This hook fires after every search_threads call and injects a non-optional
instruction naming the exact thread IDs that must be fetched in full.
"""
import json
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

resp = payload.get("tool_response")
if isinstance(resp, str):
    try:
        resp = json.loads(resp)
    except Exception:
        resp = None
if not isinstance(resp, dict):
    sys.exit(0)

threads = resp.get("threads") or []
if not threads:
    sys.exit(0)

# Ignore obvious bulk/newsletter noise so the guard stays signal.
NOISE = (
    "beehiiv.com", "substack.com", "smol.ai", "producthunt.com",
    "no-reply@", "noreply@", "mailer-daemon", "notifications@",
)

interesting = []
for t in threads:
    msgs = t.get("messages") or []
    senders = " ".join((m.get("sender") or "").lower() for m in msgs)
    if any(n in senders for n in NOISE) and len(msgs) <= 1:
        continue
    interesting.append((t.get("id"), len(msgs)))

if not interesting:
    sys.exit(0)

lines = [
    "GMAIL SEARCH GUARD -- search_threads returned PARTIAL threads.",
    "",
    "It returns only the messages that MATCHED the query, inside a thread object",
    "that looks complete. Short messages carrying decisions ('Thanks', 'yes go",
    "ahead', an agreed date) match nothing and are silently dropped.",
    "",
    "You may NOT conclude anything about what was agreed, what is outstanding,",
    "whether someone replied, or whether a thread went quiet, from the output",
    "above. For any thread you intend to reason about, call get_thread first:",
    "",
]
for tid, n in interesting[:12]:
    lines.append(f"  get_thread({tid})   [search showed {n} message(s) -- expect more]")

lines += [
    "",
    "If get_thread exceeds the token cap it dumps to a file. Do NOT give up and",
    "fall back to the search output. Run:",
    "",
    "  ~/.claude/bin/gmail-thread-digest.py <dumped-file>",
    "",
    "which prints the full thread chronologically with signatures and quoted",
    "tails stripped (typically 10x smaller than the raw JSON).",
]

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": "\n".join(lines),
    }
}))
