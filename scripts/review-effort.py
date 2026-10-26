#!/usr/bin/env python3
"""review-effort.py: the Astra review effort for a bead's tip, computed, never the builder's call (LUCY-nd3qz).

    review-effort.py <bead> <tip> [--repo PATH] [--base origin/main]

Prints one line: `high — <reason>` or `xhigh — <reason>`. Read the first word; record the whole line in the
review evidence. Shane, 26 Oct: one seat, `high` for ordinary beads, `xhigh` on money, tenancy and patient data.

The rule (fail closed): `xhigh` UNLESS both hold
  (i)  no changed path (git diff --no-renames --name-only <base>...<tip>) matches risk_tier.py's SENSITIVE or
       PATIENT_DOMAINS (imported, never copied) or the touchesSensitive surfaces below (auth, migrations,
       consent/PII, GDPR), and
  (ii) the bead, its dotted-id ancestors and its parent chain carry no money / tenancy / patient-data label
       (HAZARD_LABEL, or a Claude-only label from claude-only-beads.json).
Anything unknown is `xhigh`: risk_tier not importable, git failing, an empty diff, bd down, a bead not found.
A caller that gets no output, or anything but `high` as the first word, uses `xhigh` too.
"""
import argparse
import importlib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# touchesSensitive (commands/goal.md routing table) surfaces that risk_tier's SENSITIVE does not already name:
# migrations / SQL, PII and erasure, sessions, roles and permissions. Additive only: risk_tier stays the source.
TOUCHES_SENSITIVE = re.compile(
    r"migrat|drizzle/|\.sql$|pii|personal|erasure|erase|session|login|password|middleware|permission|rbac|roles?[/._-]",
    re.IGNORECASE,
)
HAZARD_LABEL = re.compile(
    r"money|payment|stripe|billing|refund|deposit|invoice|pric|tenan|patient|phi\b|gdpr|consent|pii|auth|security"
    r"|sensitive",
    re.IGNORECASE,
)
MAX_BEADS = 12


def changed_paths(repo, base, tip):
    out = subprocess.run(["git", "-C", repo, "diff", "--no-renames", "--name-only", f"{base}...{tip}"],
                         check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    return [p.strip() for p in out.split("\n") if p.strip()]


def path_hit(paths):
    """The first changed path that needs xhigh, with why; None when every path misses."""
    rt = importlib.import_module("risk_tier")
    for p in paths:
        if p in rt.NEUTRAL_EXACT:
            continue
        m = rt.SENSITIVE.search(p) or TOUCHES_SENSITIVE.search(p)
        if m:
            return f"{p} names a sensitive surface ({m.group(0)})"
        if p.startswith(rt.PATIENT_DOMAINS):
            return f"{p} is in a patient-data domain"
    return None


def bead_labels(bead):
    """Every label on the bead, its dotted-id ancestors and its parent chain. Raises on bd failure or not found."""
    guard = importlib.import_module("claude_only_guard")
    labels, seen, todo = [], set(), list(guard.lineage(bead))
    while todo and len(seen) < MAX_BEADS:
        cur = todo.pop(0)
        if cur.lower() in seen:
            continue
        seen.add(cur.lower())
        info = guard.bd_show(cur)
        if info is None:
            if cur == bead:
                raise LookupError(f"bead {bead} not found")
            continue
        cur_labels, parent = info
        labels += [(cur, l) for l in cur_labels]
        if parent:
            todo.extend(guard.lineage(str(parent)))
    return labels


def label_hit(bead):
    guard = importlib.import_module("claude_only_guard")
    why = guard.why_claude_only(bead)
    if why:
        return why
    for cur, label in bead_labels(bead):
        m = HAZARD_LABEL.search(label)
        if m:
            return f"{cur} carries label {label}"
    return None


def decide(bead, tip, repo=".", base="origin/main"):
    """(effort, reason). Any error is xhigh."""
    try:
        if str(HERE) not in sys.path:
            sys.path.insert(0, str(HERE))
        paths = changed_paths(repo, base, tip)
        if not paths:
            return "xhigh", f"empty diff {base}...{tip[:10]}: nothing to classify (fail closed)"
        why = path_hit(paths)
        if why:
            return "xhigh", why
        why = label_hit(bead)
        if why:
            return "xhigh", why
        return "high", f"{len(paths)} changed path(s), none sensitive; no money/tenancy/patient-data label on {bead}"
    except Exception as exc:  # noqa: BLE001 - every failure is the safe answer
        return "xhigh", f"could not classify ({type(exc).__name__}: {exc}) (fail closed)"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bead")
    ap.add_argument("tip")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", default="origin/main")
    args = ap.parse_args(argv)
    effort, reason = decide(args.bead, args.tip, args.repo, args.base)
    print(f"{effort} — {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
