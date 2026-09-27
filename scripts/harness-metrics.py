#!/usr/bin/env python3
"""harness-metrics.py — one weekly page: is the harness making us cheaper and more reliable?

LUCY-o7otj (Shane, 27 Sep). Five numbers over a rolling window (default 7 days):
  (a) first independent review PASS rate (every pushed branch already passed its own gate,
      so this is own-gate PASS -> independent PASS), from bead comments
  (b) qa-followup beads filed vs closed
  (c) Shane questions raised by review leads, tagged product vs harness
  (d) Jev hook denies/blocks and how many were true hits
  (e) beads closed FALSE-ALARM, NO-CODE or by-design (work avoided, or work that was never real)

Reads only local files: the bd JSONL exports (code repo + vault), /goal evidence lead-round
JSON, and the Jev hooks' decisions.log. Prints bead ids and counts, never comment text
(comments can name clients).

Usage:
  harness-metrics.py                 # last 7 days → Aestheticc/Ops/HARNESS_METRICS.md
  harness-metrics.py --days 14 --end 2026-09-27 --dry-run   # print, write nothing
"""
import argparse, datetime, glob, importlib.util, json, os, re, sys

HOME = os.path.expanduser("~")
BEAD_EXPORTS = ["/Users/shane/Documents/GitReBase/AestheticcNext/.beads/issues.jsonl",
                "/Users/shane/Documents/Obsidian/.beads/issues.jsonl"]
LEAD_GLOB = os.path.join(HOME, ".claude/evidence/goal/*/lead-round-*.json")
JEV_DIR = os.path.join(HOME, ".claude/hooks")
REPORT = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/HARNESS_METRICS.md"

_spec = importlib.util.spec_from_file_location(
    "fleet_dispatch", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fleet-dispatch.py"))
_fd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fd)
review_verdict, parse_ts = _fd.review_verdict, _fd._parse_ts

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Hook test suites write to the live decisions.log with these ids; they are not real sessions.
FIXTURE_SESSIONS = {"11111111-2222-3333-4444-555555555555"}
SHANE_Q_RE = re.compile(r"(?i)shane question\s*[:—-]\s*(.+)")
HARNESS_WORDS = re.compile(
    r"(?i)\b(review(?:er)?|lead|gate|mutants?|pg lane|dispatch|round|scope[- ]lock|worktree|"
    r"astra|codex|opus|evidence|harness|bead|ci|cloud build|jest|test suite|quarantin\w*)\b")
# The disposition leads the close reason. "SUPERSEDED: fixed on main … by design" and
# "shipped, not by design" are not no-build closures.
CLOSE_KINDS = (("FALSE-ALARM", re.compile(r"(?i)^\W*false[- ]alarm")),
               ("NO-CODE", re.compile(r"(?i)^\W*no[- ]code")),
               ("by-design", re.compile(r"(?i)^\W*(?:closed\W+)?(?:(?:this is|it's|working)\s+)?"
                                        r"(?:as\s+)?by[- ]design\b")))


CLOSE_NOT_LANDED_RE = re.compile(r"(?i)^\W*(?:superseded|stale|duplicate|false[- ]alarm|no[- ]code"
                                 r"|deferred|moot|wont[- ]?fix|won't)")


def in_window(stamp, start, end):
    return stamp is not None and start <= stamp < end


def load_beads(paths):
    rows = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        with open(path) as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("id") and row.get("_type", "issue") == "issue":
                    rows.append(row)
    return rows


def review_first_pass(beads, start, end):
    """(a) Landed beads whose FIRST independent review comment falls in the window. Closed
    stands in for landed: the bd close guard refuses a code-bead close with no commit on
    origin/main. Beads still in review count separately, not in the rate."""
    firsts, in_review = [], 0
    for b in beads:
        reviews = sorted((parse_ts(c.get("created_at")), v) for c in b.get("comments") or []
                         if isinstance(c, dict) and parse_ts(c.get("created_at"))
                         and (v := review_verdict(c.get("text"))))
        if not reviews or not in_window(reviews[0][0], start, end):
            continue
        if b.get("status") != "closed" or CLOSE_NOT_LANDED_RE.match(b.get("close_reason") or ""):
            in_review += 1
            continue
        firsts.append({"bead": b["id"], "verdict": reviews[0][1]})
    reflections = sum(1 for b in beads for c in b.get("comments") or [] if isinstance(c, dict)
                      and re.match(r"(?i)^\W*reflection\b", c.get("text") or "")
                      and in_window(parse_ts(c.get("created_at")), start, end))
    passed = [f for f in firsts if f["verdict"] == "PASS"]
    return {"reviewed": len(firsts), "passed": len(passed),
            "in_review": in_review, "reflections": reflections,
            "failed_beads": sorted(f["bead"] for f in firsts if f["verdict"] == "FAIL")}


def qa_followups(beads, start, end):
    """(b) qa-followup beads filed vs closed in the window."""
    qa = [b for b in beads if "qa-followup" in (b.get("labels") or [])]
    return {"filed": sum(in_window(parse_ts(b.get("created_at")), start, end) for b in qa),
            "closed": sum(b.get("status") == "closed"
                          and in_window(parse_ts(b.get("closed_at")), start, end) for b in qa),
            "open_total": sum(b.get("status") != "closed" for b in qa)}


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def shane_questions(lead_paths, start, end):
    """(c) Distinct 'Shane question: …' lines in lead-round JSON modified in the window, per
    bead (a question repeated across rounds counts once). Tagged harness when it is about the
    review/dispatch machinery, product otherwise. Keyword tagging: a first cut, not a judge."""
    seen = {}
    for path in lead_paths:
        stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc)
        if not in_window(stamp, start, end):
            continue
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        bead = data.get("bead") if isinstance(data, dict) else None
        for text in _strings(data):
            m = SHANE_Q_RE.search(text)
            if m:
                q = m.group(1).strip()
                seen[(bead, q[:80].lower())] = "harness" if HARNESS_WORDS.search(q[:200]) else "product"
    tags = list(seen.values())
    return {"total": len(tags), "product": tags.count("product"), "harness": tags.count("harness"),
            "beads": len({b for b, _ in seen})}


def _jev_rows(path, start, end):
    rows, fixtures = [], 0
    if not os.path.isfile(path):
        return None, 0
    with open(path) as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not in_window(parse_ts(row.get("ts")), start, end):
                continue
            session = row.get("session") or ""
            if not UUID_RE.match(session) or session in FIXTURE_SESSIONS:
                fixtures += 1
                continue
            rows.append(row)
    return rows, fixtures


def jev_hits(jev_dir, start, end):
    """(d) draft-check: a deny is a true hit unless the same session overrode it with an
    identical retry. stop-verify: a block is a true hit when the same session later earns an
    'allowed' verdict (it went back, ran the checks and they passed)."""
    out = {}
    rows, fixtures = _jev_rows(os.path.join(jev_dir, "jev-draft-check/decisions.log"), start, end)
    if rows is None:
        out["draft"] = None
    else:
        denies = overrides = 0
        pending = {}
        for r in rows:
            s = r.get("session")
            if r.get("allow") is False:
                denies += 1
                pending[s] = pending.get(s, 0) + 1
            elif "identical retry" in (r.get("why") or "") and pending.get(s):
                overrides += 1
                pending[s] -= 1
        out["draft"] = {"checks": len(rows), "denies": denies, "overridden": overrides,
                        "true_hits": denies - overrides, "fixture_rows": fixtures}
    rows, fixtures = _jev_rows(os.path.join(jev_dir, "jev-stop-verify/decisions.log"), start, end)
    if rows is None:
        out["stop"] = None
    else:
        blocks, cleared, open_blocks = 0, 0, {}
        for r in rows:
            s, why = r.get("session"), (r.get("why") or "")
            if r.get("exit") == 2:
                blocks += 1
                open_blocks[s] = open_blocks.get(s, 0) + 1
            elif why == "allowed" and open_blocks.get(s):
                cleared += open_blocks.pop(s)
        out["stop"] = {"checks": len(rows), "blocks": blocks, "true_hits": cleared,
                       "fixture_rows": fixtures}
    return out


def closed_without_build(beads, start, end):
    """(e) Beads closed in the window as FALSE-ALARM, NO-CODE or by-design."""
    counts = {k: [] for k, _ in CLOSE_KINDS}
    for b in beads:
        if b.get("status") != "closed" or not in_window(parse_ts(b.get("closed_at")), start, end):
            continue
        reason = b.get("close_reason") or ""
        for kind, pattern in CLOSE_KINDS:
            if pattern.search(reason):
                counts[kind].append(b["id"])
                break
    return counts


def pct(n, d):
    return "n/a" if not d else f"{100 * n / d:.0f}%"


def render(m, start, end, sources):
    a, b, c, d, e = m["a"], m["b"], m["c"], m["d"], m["e"]
    day = lambda t: t.strftime("%Y-%m-%d")
    lines = [f"## Week {day(start)} → {day(end - datetime.timedelta(seconds=1))}",
             f"_Generated {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%MZ} by "
             f"`~/.claude/scripts/harness-metrics.py` (LUCY-o7otj). Beads data as of {sources}._", "",
             "| # | Question | This week |", "|---|---|---|",
             f"| a | Own-gate PASS → first independent review PASS (landed beads; approximate, "
             f"parsed from free-text review comments) | "
             f"{a['passed']}/{a['reviewed']} = **{pct(a['passed'], a['reviewed'])}** "
             f"({a['in_review']} more first-reviewed, not landed yet; "
             f"{a['reflections']} REFLECTION rework comments) |",
             f"| b | qa-followup beads filed vs closed | filed **{b['filed']}**, closed "
             f"**{b['closed']}** ({b['open_total']} open in total) |",
             f"| c | Shane questions from review leads | **{c['total']}** ({c['product']} product, "
             f"{c['harness']} harness) across {c['beads']} beads |"]
    dd, ds = d.get("draft"), d.get("stop")
    jev = []
    jev.append("draft-check: no log" if dd is None else
               f"draft-check {dd['denies']} denies / {dd['checks']} checks, **{dd['true_hits']} true "
               f"hits** ({dd['overridden']} overridden)")
    jev.append("stop-verify: no log" if ds is None else
               f"stop-verify {ds['blocks']} blocks / {ds['checks']} checks, **{ds['true_hits']} "
               f"true hits** (block later cleared by an `allowed` re-check)")
    lines.append(f"| d | Jev hooks | {'; '.join(jev)} |")
    lines.append(f"| e | Closed without a build | FALSE-ALARM **{len(e['FALSE-ALARM'])}**, NO-CODE "
                 f"**{len(e['NO-CODE'])}**, by-design **{len(e['by-design'])}** |")
    lines += ["", "Detail:"]
    if a["failed_beads"]:
        lines.append(f"- (a) first review FAILED: {', '.join(a['failed_beads'])}")
    for kind, ids in e.items():
        if ids:
            lines.append(f"- (e) {kind}: {', '.join(sorted(ids))}")
    fixtures = sum(x["fixture_rows"] for x in (dd, ds) if x)
    if fixtures:
        lines.append(f"- (d) excluded {fixtures} Jev log rows written by hook test fixtures "
                     f"(non-UUID or placeholder session ids)")
    lines.append("- Read: (a) rising = builds pass independent review first time more often. "
                 "(b) filed ≫ closed = review is generating debt. (e) is wrong work caught; "
                 "compare against check 11 refusals to see how much was caught before a build.")
    return "\n".join(lines) + "\n"


HEADER = ("# Harness metrics\n\nOne section per week, newest first, written by "
          "`~/.claude/scripts/harness-metrics.py` (LUCY-o7otj). Question it answers: is the "
          "harness making us cheaper and more reliable?\n\n")


def write_report(path, section):
    """Newest week first; re-running the same week replaces its section."""
    body = ""
    if os.path.isfile(path):
        with open(path) as fh:
            body = fh.read()
        body = body[len(HEADER):] if body.startswith(HEADER) else body
    heading = section.splitlines()[0]
    kept = [s for s in re.split(r"(?m)^(?=## Week )", body) if s.strip() and not s.startswith(heading)]
    with open(path, "w") as fh:
        fh.write(HEADER + section + "".join("\n" + s.lstrip("\n") for s in kept))


def collect(start, end, bead_paths=BEAD_EXPORTS, lead_glob=LEAD_GLOB, jev_dir=JEV_DIR):
    beads = load_beads(bead_paths)
    return {"a": review_first_pass(beads, start, end), "b": qa_followups(beads, start, end),
            "c": shane_questions(sorted(glob.glob(lead_glob)), start, end),
            "d": jev_hits(jev_dir, start, end), "e": closed_without_build(beads, start, end)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", help="last day included, YYYY-MM-DD (default today, UTC)")
    ap.add_argument("--out", default=REPORT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    last = datetime.date.fromisoformat(args.end) if args.end else today
    end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time(),
                                    datetime.timezone.utc)
    start = end - datetime.timedelta(days=args.days)
    sources = ", ".join(
        f"{os.path.basename(os.path.dirname(os.path.dirname(p)))} "
        f"{datetime.datetime.fromtimestamp(os.path.getmtime(p)):%Y-%m-%d %H:%M}"
        for p in BEAD_EXPORTS if os.path.isfile(p)) or "no bead exports found"
    section = render(collect(start, end), start, end, sources)
    print(section)
    if not args.dry_run:
        write_report(args.out, section)
        print(f"✓ written → {args.out}")


if __name__ == "__main__":
    sys.exit(main())
