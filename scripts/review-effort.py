#!/usr/bin/env python3
"""review-effort.py: the Astra review effort for a bead's tip, computed, never the builder's call (LUCY-nd3qz).

    review-effort.py <bead> <tip> [--repo PATH] [--base origin/main]

Prints one line: `high — <reason>` or `xhigh — <reason>`. Read the first word; record the whole line in the
review evidence. Shane, 26 Oct: one seat, `high` for ordinary beads, `xhigh` on money, tenancy and patient data.

The rule (fail closed): `xhigh` UNLESS both hold
  (i)  risk_tier.classify() (imported, never re-implemented) puts the changed paths (git diff --no-renames
       --name-only <base>...<tip>) in its `light` tier: every path UI, styling or copy, no sensitive surface, each
       component source read at the tip. risk_tier's allowlist fails closed, so anything else (lib/, pages/api/,
       migrations, a patient-data domain) is `full`, and `full` is xhigh; and
  (ii) the bead, its dotted-id ancestors and its parent chain carry no money / tenancy / patient-data label
       (HAZARD_LABEL, or a Claude-only label from claude-only-beads.json). A label only ever raises to xhigh.
Anything unknown is `xhigh`: risk_tier not importable, git failing, an empty diff, bd down, a bead not found.
A caller that gets no output, or anything but `high` as the first word, uses `xhigh` too.
"""
import argparse
import importlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

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


def path_hit(paths, repo, tip):
    """Why the diff needs xhigh, or None when risk_tier.classify() says light. The component sources classify
    reads are each changed path's content at the tip, exported to a scratch tree (a path absent there, i.e.
    deleted, reads as unreadable, which classify makes full)."""
    rt = importlib.import_module("risk_tier")
    with tempfile.TemporaryDirectory() as tree:
        for p in paths:
            got = subprocess.run(["git", "-C", repo, "show", f"{tip}:{p}"], capture_output=True,
                                 stdin=subprocess.DEVNULL)
            if got.returncode == 0:
                dest = Path(tree) / p
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(got.stdout)
        tier, reasons = rt.classify(paths, tree)
    if tier != "light":
        return f"risk tier {tier}: " + ("; ".join(reasons[:3]) or "no reason given (fail closed)")
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
        why = path_hit(paths, repo, tip)
        if why:
            return "xhigh", why
        why = label_hit(bead)
        if why:
            return "xhigh", why
        return "high", f"risk tier light ({len(paths)} path(s)); no money/tenancy/patient-data label on {bead}"
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
