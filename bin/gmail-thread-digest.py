#!/usr/bin/env python3
"""Turn a dumped Gmail get_thread JSON into a compact chronological transcript.

Why this exists: reading a thread in its entirety is the only safe way to reason
about it (see feedback_gmail_search_returns_partial_threads), but raw get_thread
output is dominated by HTML signature blocks and quoted reply tails. A 14-message
thread came to 87k characters, blew the token cap, and pushed the agent back onto
partial search output. This strips the noise so full reads stay cheap.

Usage:
    gmail-thread-digest.py <file.json>          # full digest
    gmail-thread-digest.py <file.json> --index  # one line per message
    gmail-thread-digest.py <file.json> --chars 2000
"""
import argparse
import json
import re
import sys

# Quoted-reply tails. Everything from here down is a copy of earlier messages.
QUOTE_MARKERS = [
    re.compile(r"^On .{5,120}\bwrote:\s*$", re.I),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^_{10,}$"),
    re.compile(r"^From:\s.+@", re.I),
    re.compile(r"^Sent from (my |Outlook|Mail)", re.I),
    re.compile(r"^>{1,}\s?"),
]

# Signature furniture. Contact lines and mail-signature-generator spam.
SIG_LINE = [
    re.compile(r"^\[?created with MySignature", re.I),
    re.compile(r"^\s*[pwea h]:\s", re.I),          # p: w: e: a: h: contact rows
    re.compile(r"^\[.*\.(png|jpg|jpeg|gif)\]\s*$", re.I),
    re.compile(r"^\s*<https?://\S+>\s*$"),
    re.compile(r"^\s*\[image:[^\]]*\]\s*$", re.I),
    re.compile(r"^(Kind regards|Best regards|Many thanks|Regards|Thanks),?\s*$", re.I),
]

SIG_BLOCK_START = re.compile(
    r"^(Dr Shane McKeown|Founder, Aestheticc|--\s*$|Emma Caston|Sent from)", re.I
)


def clean(body: str) -> str:
    if not body:
        return ""
    body = re.sub(r"\r\n?", "\n", body)
    out = []
    for line in body.split("\n"):
        if any(p.match(line) for p in QUOTE_MARKERS):
            break                      # quoted tail: drop the rest
        if SIG_BLOCK_START.match(line.strip()):
            break                      # signature block: drop the rest
        if any(p.match(line) for p in SIG_LINE):
            continue                   # single noise line: skip it
        # strip inline mailto/url decorations Gmail leaves in plaintext
        line = re.sub(r"<(mailto:|tel:|https?://)[^>]*>", "", line)
        out.append(line.rstrip())
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--chars", type=int, default=1500,
                    help="max chars per message body (0 = unlimited)")
    ap.add_argument("--index", action="store_true",
                    help="one line per message, no bodies")
    args = ap.parse_args()

    with open(args.path) as fh:
        data = json.load(fh)

    msgs = data.get("messages") or []
    if not msgs:
        print("No messages found. Is this a get_thread dump?", file=sys.stderr)
        return 1

    print(f"THREAD {data.get('id', '?')} — {len(msgs)} messages\n")

    for i, m in enumerate(msgs, 1):
        date = m.get("date", "?")
        sender = m.get("sender", "?")
        if args.index:
            body = clean(m.get("plaintextBody") or m.get("snippet") or "")
            first = next((l for l in body.split("\n") if l.strip()), "")
            print(f"{i:>3}. {date} | {sender:<38} | {first[:90]}")
            continue

        body = clean(m.get("plaintextBody") or m.get("snippet") or "")
        if args.chars and len(body) > args.chars:
            body = body[: args.chars] + f"\n… [+{len(body) - args.chars} chars]"
        print(f"===== [{i}/{len(msgs)}] {date} | {sender}")
        print(body if body else "(empty after cleaning)")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
