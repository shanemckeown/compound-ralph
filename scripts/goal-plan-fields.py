#!/usr/bin/env python3
"""goal-plan-fields.py: /goal Phase 2b check that a plan carries the aestheticc-review plan fields.

  goal-plan-fields.py .goal/PLAN.md [.goal/plans/<child>.md ...]

Exit 0 when every plan has the seven fields, in order, and its EXISTING CAPABILITY field
(LUCY-fuu2h) is filled in: at least one `EXTEND <file:line>` line, or `NEW PATH <file>` lines that
each carry a written reason, or `none — <why no existing path fits>`. Exit 1 with reasons otherwise.
"""
import re
import sys
from pathlib import Path

FIELDS = ["OUTCOME PROMISE", "PREMISE CHECK", "EXISTING CAPABILITY", "TOUCHPOINT MAP", "HAZARDS",
          "SHANE QUESTIONS", "ACCEPTANCE TESTS"]
LEAD = re.compile(r"^\s*(?:#+\s*|[-*]\s*|\d+[.)]\s*)*(?:\*\*)?\s*")
# A declaration starts its line (after any bullet): EXTEND <path>, NEW PATH <path> — <why>, none — <why>.
DECL = re.compile(r"^\s*(?:[-*]\s*|\d+[.)]\s*)*`?(EXTEND|NEW PATH|none)\b`?\s*(.*)$", re.I)
SEP = " :—–-`*;,."
PATHLIKE = re.compile(r"/|\w\.\w|:\d+$")  # lib/x.ts, x.py, Makefile:12


def field_at(line):
    head = LEAD.sub("", line).upper()
    return next((f for f in FIELDS if head.startswith(f)), None)


def check(text):
    errs, where = [], {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        f = field_at(line)
        if f and f not in where:
            where[f] = i
    missing = [f for f in FIELDS if f not in where]
    errs += [f"missing field: {f}" for f in missing]
    found = [f for f in FIELDS if f in where]
    if [where[f] for f in found] != sorted(where[f] for f in found):
        errs.append(f"fields out of order (want: {', '.join(FIELDS)})")
    if "EXISTING CAPABILITY" not in where:
        return errs
    start = where["EXISTING CAPABILITY"]
    later = [where[f] for f in FIELDS if f in where and where[f] > start]
    first = LEAD.sub("", lines[start])[len("EXISTING CAPABILITY"):].lstrip(SEP)
    body = [first] + lines[start + 1:min(later or [len(lines)])]
    decls = [(m.group(1).upper(), m.group(2).strip(), l) for l in body for m in [DECL.match(l)] if m]
    if not decls:
        errs.append("EXISTING CAPABILITY: list each existing path as `EXTEND <file:line>` or `NEW PATH <file> — "
                    "<why extending fails>`, or write `none — <why no existing path fits>`")
    for kind, rest, l in decls:
        # The path is the whole backticked span when the rest starts with one, else the first word.
        if rest.startswith("`") and "`" in rest[1:]:
            token, _, why = rest[1:].partition("`")
        else:
            token, _, why = rest.partition(" ")
        token = token.strip("`*")
        if kind == "NONE":
            if not re.search(r"[A-Za-z]", rest.strip(SEP)):
                errs.append(f"EXISTING CAPABILITY: `none` needs a reason (`none — <why no existing path fits>`): "
                            f"{l.strip()[:100]}")
        elif not token.strip(SEP) or (kind == "EXTEND" and not PATHLIKE.search(token)):
            errs.append(f"EXISTING CAPABILITY: {kind} line names no file path: {l.strip()[:100]}")
        elif kind == "NEW PATH" and not re.search(r"[A-Za-z]", why.strip(SEP)):
            errs.append(f"EXISTING CAPABILITY: NEW PATH with no written reason why extending fails: {l.strip()[:100]}")
    return errs


def main(argv):
    if not argv:
        print("usage: goal-plan-fields.py PLAN.md [PLAN.md ...]", file=sys.stderr)
        return 2
    bad = 0
    for p in argv:
        try:
            errs = check(Path(p).read_text())
        except OSError as e:
            errs = [f"cannot read: {e}"]
        for e in errs:
            print(f"{p}: {e}", file=sys.stderr)
        bad += bool(errs)
    if bad:
        print("plan fields: FAIL, back to Phase 2", file=sys.stderr)
        return 1
    print(f"plan fields: OK ({len(argv)} plan{'s' if len(argv) != 1 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
