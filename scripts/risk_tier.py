#!/usr/bin/env python3
"""risk_tier.py: which landing gate set a diff needs, decided from its paths (LUCY-z3ykz.1).

    risk_tier.py --repo PATH [--base origin/main] [--json]   # paths = git diff base...HEAD
    git diff --name-only origin/main...HEAD | risk_tier.py [--json]   # paths on stdin

Prints `tier=light` or `tier=full` plus one reason line per path that forced full.
classify(paths) -> (tier, reasons) is the importable form; /land-batch calls it on the
combined batch diff, so the agent never picks the tier itself.

light: targeted jest + tsc + lint. full: that plus the full pg lane + test:cloudbuild.
The default is full (fail closed). A diff is light ONLY when every path is UI, styling,
copy or a test of those, and no path names a sensitive surface (money, patient data,
consent, tenancy, comms/sending, auth). An empty diff is full: nothing to classify means
something went wrong upstream, not that nothing can break.

This is deliberately not gate-tier.py: that one classifies an ACTION's consequence
(T0/T1/T2) for dispatch and deploy decisions. This one only picks a test set.
"""
import argparse
import json
import re
import subprocess
import sys

# UI / styling / copy, plus the test trees that cover them.
LIGHT_PREFIXES = (
    "components/",
    "styles/",
    "content/",
    "copy/",
    "tests/components/",
    "__tests__/components/",
)
LIGHT_SUFFIXES = (".css", ".scss")
# public/ static text and images only: public/*.js (a service worker, embed.js) is code.
PUBLIC_TEXT_SUFFIXES = (".txt", ".md", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico")
# Bookkeeping every /ship or bd commit carries; it changes no code and no test input.
NEUTRAL_EXACT = {"CHANGELOG.md", "VERSION", "issues.jsonl"}
NEUTRAL_PREFIXES = (".beads/",)

# Any path naming one of these is full, even under components/ (components/payments/X.tsx).
SENSITIVE = re.compile(
    r"stripe|payment|billing|consent|patient|clinical|tenant|business_id|sms|email|send|comms|auth",
    re.IGNORECASE,
)


def path_reason(path):
    """None if this path is light, else why it forces full."""
    if path in NEUTRAL_EXACT or path.startswith(NEUTRAL_PREFIXES):
        return None
    hit = SENSITIVE.search(path)
    if hit:
        return f"{path}: names a sensitive surface ({hit.group(0)})"
    if path.startswith(LIGHT_PREFIXES) or path.endswith(LIGHT_SUFFIXES):
        return None
    if path.startswith("public/") and path.lower().endswith(PUBLIC_TEXT_SUFFIXES):
        return None
    return f"{path}: not UI/style/copy (default full)"


def classify(paths):
    """Return (tier, reasons). tier is 'light' or 'full'; reasons lists what forced full."""
    paths = [p.strip() for p in paths if p and p.strip()]
    if not paths:
        return "full", ["empty diff: nothing to classify (fail closed)"]
    reasons = [r for r in (path_reason(p) for p in paths) if r]
    return ("full", reasons) if reasons else ("light", [])


def diff_paths(repo, base):
    out = subprocess.run(
        ["git", "-C", repo, "diff", "--no-renames", "--name-only", f"{base}...HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
    return out.split("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="git checkout to diff; without it, paths are read from stdin")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    paths = diff_paths(args.repo, args.base) if args.repo else sys.stdin.read().split("\n")
    tier, reasons = classify(paths)
    if args.json:
        print(json.dumps({"tier": tier, "reasons": reasons,
                          "path_count": len([p for p in paths if p.strip()])}))
    else:
        print(f"tier={tier}")
        for r in reasons:
            print(f"  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
