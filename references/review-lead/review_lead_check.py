#!/usr/bin/env python3
"""review_lead_check.py: the deterministic gate on the review lead's output (LUCY-oawgi).

The lead (LEAD.md) is a model; this is the part that must hold without Shane. It checks the
lead's JSON against the reviewer verdicts, then writes the ONLY file the builder reads.

  review_lead_check.py --lead lead-round-N.json --verdict astra-verdict-round-N.log \
      [--verdict opus-h2h-round-N.md] --out-dir "$EVIDENCE_DIR" --round N [--scope-lock]

Refuses (exit 1, reasons on stderr) when:
  - a reviewer finding line is missing from the lead, duplicated, or invented;
  - a verdict file is empty, truncated, or has a finding line in an unrecognised format;
  - a verdict is not act_on / consider / noted / dismissed;
  - a non-act_on item has no reason, or an act_on item has no clinic scenario / demonstration;
  - a finding in a never-dismiss area (booking slots, money, sends/crons, webhooks, tenancy/auth,
    GDPR) is Dismissed or Noted;
  - a cited pattern is not a heading in DISMISSAL_PATTERNS.md;
  - scope lock is on and an act_on item lacks in_scope, or an out-of-scope / consider item lacks
    a follow_up title;
  - more than 5 blocking act_on items without act_on_over_cap_reason;
  - round >= 2 and no premise block;
  - a pre_existing act_on / consider item lacks a follow_up bead title.
Duplicate path, H-D0 (LUCY-fuu2h, Shane 15 Oct 2026: "Yes, P2 blocks"):
  - a finding with category `duplicate_path`, or `H-D0` / "duplicate path" in its line, is graded P2
    whatever severity the reviewer wrote, so it can never be Noted;
  - it is structural: an act_on needs no clinic_scenario, and it is never demoted to a P4 note;
  - one the branch added that is Consider, or Act on but out of scope under the lock, escalates
    (blocks landing) instead of becoming a follow-up; a pre_existing one is a follow-up.
Ordinary-use triage (LUCY-r1lyy, Shane 7 Oct 2026, CLINIC_REALITY "The ordinary-use test"):
  - every finding states `ordinary_use` (how ONE person using the app normally triggers it with
    one or two ordinary actions, or "none: <why>") and `carve_out` (true when it touches money,
    patient data, auth, cross-tenancy, or what a user sees or is told);
  - an act_on / consider finding with no ordinary-use path that is not in the carve-out is
    auto-demoted to a P4 note: no fix, no follow-up bead, no re-review;
  - a carve-out finding (lead says so, protected category, user_facing category, or a protected
    keyword) is never demoted, however rare;
  - a `pre_existing: true` finding never blocks the branch under review: it goes to a filed
    follow-up bead (or to escalate when protected), with or without scope lock.
On success (exit 0) writes, into --out-dir:
  lead-actions-round-N.txt     the blocking Act-on findings: the builder's whole input
  lead-escalate-round-N.txt    protected-area Consider items: orchestrator decides, blocks push
  lead-followups-round-N.txt   follow-up bead titles (non-blocking)
  lead-dismissals-round-N.md   every non-Act-on decision with its reason (the audit trail)
and prints `BLOCKING=n ESCALATE=n FOLLOWUPS=n DISMISSED=n NOTED=n P4=n`.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATTERNS = HERE / "DISMISSAL_PATTERNS.md"
VERDICTS = {"act_on", "consider", "noted", "dismissed", "fixed"}
ACT_ON_CAP = 5
# The lead puts every finding in one category. Protected categories can never be Dismissed or
# Noted; the keyword regex below is a second, independent backstop in case the lead mislabels.
PROTECTED_CATEGORIES = {"money", "booking", "sends", "webhooks", "tenancy_auth", "patient_data_exposure",
                        "gdpr", "prescribing", "single_device_data_loss"}
OPEN_CATEGORIES = {"clinical_record_concurrency", "timing_window", "crafted_request", "style", "other",
                   # what a user sees or is told (broken promise, wrong screen): carve-out from the
                   # ordinary-use demotion, but the lead may still dismiss a hypothetical one.
                   "user_facing"}
# H-D0: a second implementation of a job an existing table/module/setting/send path already does.
# Not protected (the lead may dismiss it with a reason, e.g. the plan recorded why extending failed).
STRUCTURAL_CATEGORIES = {"duplicate_path"}
CATEGORIES = PROTECTED_CATEGORIES | OPEN_CATEGORIES | STRUCTURAL_CATEGORIES
CARVE_OUT_CATEGORIES = PROTECTED_CATEGORIES | {"user_facing"} | STRUCTURAL_CATEGORIES
DUPLICATE_PATH = re.compile(r"\bH-D0\b|\bduplicate[- ]path\b", re.I)
# An ordinary_use value that says there is no ordinary path counts as absent.
NO_ORDINARY_USE = re.compile(r"^\s*(none|n/?a|no\b|not (an )?ordinary|unknown|-+)\b", re.I)

# Findings in these areas are real concurrency / real harm in a clinic (CLINIC_REALITY
# "never dismiss"). Narrow on purpose: words like "delete" or "authorize" alone are not here,
# because ordinary record edits use them.
PROTECTED = re.compile(
    r"\b(book(ing|ings|ed)?|slots?|availability|double[- ]book\w*|diary|waitlist"
    r"|pay(ment|ments|ing)?|charg(e|es|ed|ing)|refund\w*|stripe|terminal|pos\b|invoice\w*|deposit\w*|payout\w*"
    r"|reminder\w*|cron\w*|sms|webhook\w*|twilio|send(s|ing)? (an? )?(email|sms|message)"
    r"|debit\w*|credit(s|ed)?|balance|ledger|package (credit|session)s?"
    r"|tenan\w*|business_?id|businessId|cross[- ]clinic|another clinic|unauthori[sz]ed|unauthenticated"
    r"|authz|authori[sz]ation|auth(entication)? bypass|impersonat\w*"
    r"|gdpr|erasure|prescri\w*|contraindicat\w*"
    r"|H-M\d|H-B\d|H-C\d|H-T\d)\b",
    re.I,
)
FINDING = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(P[123])(?:\*\*)?[\s:]+\S+.*$")
# A line that STARTS like a finding (after bullets, numbering, bold, brackets) but isn't one in
# the FINDING shape is refused, because it could hide a finding; so is any line with a severity
# token AND a file:line ("Finding (P1): lib/z.ts:2 ..."). A severity token in path-free prose
# ("(no P1/P2 findings)", "FAIL — 0 P1, 1 P2") is a summary, not a finding, and is ignored.
FINDING_START = re.compile(r"^[\s\-*•>#]*(?:\d+[.)]\s*)?[\s\-*•]*(?:\*\*|__)?[\[(]?\s*P[123]\b")
# A severity, not the P1 inside a hazard id (H-P1, COM-P2) nor a count ("0 P1 found": counts are
# checked by check_summary_counts).
SEVERITY_TOKEN = re.compile(r"(?<![\w-])(?<!\d\s)(?<!\dx\s)P[123]\b")
# An explicitly labelled finding, even with its location wrapped onto the next line:
# "Finding (P1): Retry charges twice", "[P2] ...", "Issue P1 - ...".
EXPLICIT_LABEL = re.compile(r"\b(?:finding|issue|bug|defect|blocker)s?\b\W{0,4}[\[(]?P[123]\b"
                            r"|[\[(]P[123][\])]|(?<![\w-])P[123]\s*:\s", re.I)
FILE_LINE = re.compile(r"(?<![\w./\[\]-])(?=[\w./\[\]-]*[A-Za-z_/])[\w./\[\]-]+:\d+")
SUMMARY_COUNT = re.compile(r"\b(\d+)\s*(?:x\s*)?(P[123])\b")
# Structured verdict (LUCY-p4pjf): a fenced ```review-verdict block holding JSON
#   {"verdict": "PASS"|"FAIL", "findings": [{"severity": "P2", "text": "file:line — ..."}]}
# When a file has one, the last block is the whole input from that file; prose is not parsed.
STRUCTURED = re.compile(r"(?ms)^\s*```\s*review-verdict\s*\n(.*?)^\s*```\s*$")
# bd comment header, as `bd comments` prints it (timestamps are UTC).
COMMENT_HEADER = re.compile(r"^\[(?P<author>[^\]]*)\] at (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$")
BEAD_ID = re.compile(r"(?<![A-Za-z0-9-])(?:AestheticcNext|LUCY)-[a-z0-9]+(?:\.\d+)*(?![A-Za-z0-9-]|\.\d)", re.I)
BEADS_DIRS_BY_PREFIX = (("aestheticcnext-", "~/Documents/GitReBase/AestheticcNext/.beads"),
                        ("lucy-", "~/Documents/Obsidian/.beads"))


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def clean(line):
    """Canonical form of a finding line: bullets and **bold** severity stripped."""
    return norm(re.sub(r"^(?:\*\*)?(P[123])(?:\*\*)?[\s:]+", r"\1 ", norm(line).lstrip("-* ")))


# "PASS", "**FAIL**", "Verdict: FAIL", "**Verdict: PASS**", "**Verdict:** FAIL", "Verdict — FAIL".
VERDICT_HEAD = re.compile(r"^\s*(?:[*_]{1,2})?(?:Verdict\s*(?:[*_]{1,2})?\s*[:—–-]\s*(?:[*_]{1,2})?\s*)?"
                          r"(PASS|FAIL)(?:[*_]{1,2})?\s*$", re.I)
# Any line declaring a verdict, in whatever markup: counted for agreement, never parsed for findings.
VERDICT_DECL = re.compile(r"\bverdict\b[^\n]*?\b(PASS|FAIL)\b", re.I)


SECTION = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?(HAZARDS|VERIFIED OK|UNVERIFIED)\b")
# A later round's reviewer confirming an earlier finding fixed (LUCY-p4pjf, orchestrator 23 Oct), in its
# VERIFIED OK section, exactly: "- r9 P3 lib/x.ts:37 closed: <how it was checked>".
CONFIRM_FIXED = re.compile(r"^\s*[-*]\s+(?:\*\*)?r(\d+)\s+(P[123])\s+(\S+:\d+)(?:\*\*)?\s+(?:closed|fixed|resolved):\s+"
                           r"(?=\S)[^?]{8,}$", re.I)
CONFIRM_NEGATION = re.compile(r"\b(not|no|never|still|but|however|yet|partial|partially|unable|cannot|"
                              r"unconfirmed|unverified|untested|fails?|failed|broken|regressed|reopen\w*|open|remains?|"
                              r"remaining|unresolved|outstanding|pending|intermittent\w*|flaky|sometimes)\b"
                              r"|n't\b", re.I)


def is_confirmation(line):
    """Affirmative only: "closed:" then evidence, no question, no negative or unresolved wording."""
    m = CONFIRM_FIXED.match(line)
    if not m:
        return False
    # The evidence tail is evidence only: no question, no adverse wording, and no severity or
    # location (that would be a new finding). Paths before it may contain any word.
    tail = line[line.index(":", m.end(3)) + 1:]
    return not ("?" in tail or CONFIRM_NEGATION.search(tail) or SEVERITY_TOKEN.search(tail)
                or FILE_LINE.search(tail) or EXPLICIT_LABEL.search(tail))


def verified_lines(lines):
    """Indexes of the lines inside a VERIFIED OK section (until the next section, verdict or echo)."""
    cur, out = None, set()
    for i, line in enumerate(lines):
        m = SECTION.match(line)
        if m:
            cur = m.group(1).upper()
            continue
        if VERDICT_HEAD.match(line) or re.match(r"^\s*REVIEWING:", line):
            cur = None
        if cur == "VERIFIED OK":
            out.add(i)
    return out


# Finding-shaped VERIFIED OK notes the parser let through: each must be adjudicated by the lead in
# `verified_notes` with a reason, never silently dropped. Reset by main() before parsing.
UNBOUND_NOTES = {}


def tolerated_note(line, mine):
    """A VERIFIED OK bullet discussing a listed finding ("**Astra's P2, my own check.**
    pages/api/clients/[id].ts ...") is never excused by the parser alone (Astra r17-r19: syntax
    can't tell evidence from a second finding). It parses only if it is outside the never-dismiss
    areas, every severity it cites names a listed finding's file, and the lead then adjudicates it
    in verified_notes. A strict fixed-confirmation line parses as itself."""
    if is_confirmation(line):
        return True
    if not re.match(r"^\s*[-*\u2022]\s", line):
        return False
    if FINDING.match(line) or FINDING_START.match(line) or EXPLICIT_LABEL.search(line) or PROTECTED.search(line):
        return False
    sev = {m.group(0) for m in SEVERITY_TOKEN.finditer(line)}
    if not sev:
        return False
    for s in sev:
        own = {loc.group(0) for f in mine if f[:2] == s for loc in [FILE_LINE.search(f)] if loc}
        if not any(loc.rsplit(":", 1)[0] in line for loc in own):
            return False
    UNBOUND_NOTES[clean(line)] = line.strip()
    return True


def decl_words(lines):
    """Verdict words declared in prose, ignoring a canonical finding's own description."""
    words = set()
    for line in lines:
        if FINDING.match(line):
            continue
        h = VERDICT_HEAD.match(line)
        if h:
            words.add(h.group(1).upper())
        words |= {m.group(1).upper() for m in VERDICT_DECL.finditer(line)}
    return words


def finding_shaped(line):
    """One rule everywhere: a line that is, or might be, a finding."""
    return bool(FINDING.match(line) or FINDING_START.match(line) or EXPLICIT_LABEL.search(line)
                or ((SEVERITY_TOKEN.search(line) or any(int(m.group(1)) for m in SUMMARY_COUNT.finditer(line)))
                    and FILE_LINE.search(line)))


def structured_findings(p, block):
    try:
        data = json.loads(block)
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}: review-verdict block is not valid JSON ({e})")
    if not isinstance(data, dict) or data.get("verdict") not in ("PASS", "FAIL"):
        raise ValueError(f"{p}: review-verdict block needs \"verdict\": \"PASS\" or \"FAIL\"")
    items = data.get("findings", [])
    if not isinstance(items, list):
        raise ValueError(f"{p}: review-verdict \"findings\" must be a list")
    mine = []
    for i, f in enumerate(items):
        sev = f.get("severity") if isinstance(f, dict) else None
        text = norm(f.get("text", "")) if isinstance(f, dict) else ""
        if sev not in ("P1", "P2", "P3") or not text:
            raise ValueError(f"{p}: review-verdict finding #{i + 1} needs severity P1/P2/P3 and text")
        mine.append(clean(f"{sev} {text}"))
    if data["verdict"] == "FAIL" and not mine:
        raise ValueError(f"{p}: review-verdict is FAIL but lists no findings")
    return mine


def check_summary_counts(p, lines, mine):
    """A summary that states counts must agree with the findings parsed: a full tally ("0 P1, 1 P2,
    2 P3") exactly, a single count ("Summary: 1 P1", maybe one reviewer's share) as a floor.
    Catches a finding the parser missed without refusing ordinary prose."""
    got = {s: sum(1 for m in mine if m.startswith(s)) for s in ("P1", "P2", "P3")}
    for line in lines:
        if FINDING.match(line):
            continue  # a finding's own description ("two P1 findings disappear") is not a tally
        counts = SUMMARY_COUNT.findall(line)
        for n, sev in counts:
            if int(n) > got[sev] or (len(counts) >= 2 and int(n) != got[sev]):
                raise ValueError(f"{p}: summary says {n} {sev} but {got[sev]} {sev} finding line(s) "
                                 f"parsed: {line.strip()[:100]}")


def reviewer_findings(paths):
    """Finding lines from each verdict file. Raises ValueError on anything that could hide a
    finding: no PASS/FAIL line, FAIL with nothing parsed, a line that starts like a finding but
    can't be parsed, or a count summary that disagrees with the lines parsed."""
    found = []
    for p in paths:
        text = Path(p).read_text(errors="ignore")
        blocks = list(STRUCTURED.finditer(text))
        openers = list(re.finditer(r"(?m)^\s*```\s*review-verdict\s*$", text))
        if openers and (not blocks or openers[-1].start() >= blocks[-1].end()):
            raise ValueError(f"{p}: a review-verdict block is not closed (no ``` after it)")
        if blocks:
            # The block is the final answer. A verdict or finding after it means the reviewer
            # changed its mind (or the block is a stale template): refuse, never pick one.
            trailing = text[blocks[-1].end():].splitlines()
            for line in trailing:
                if VERDICT_HEAD.match(line) or VERDICT_DECL.search(line) or finding_shaped(line):
                    raise ValueError(f"{p}: verdict content after the last review-verdict block: "
                                     f"{line.strip()[:100]}")
            mine = structured_findings(p, blocks[-1].group(1))
            check_summary_counts(p, trailing, mine)
            # The final response before the block (from its last REVIEWING echo, else the file
            # start) must agree with it: same verdict word, no finding the block leaves out, and
            # summary counts that match the block.
            head = text[:blocks[-1].start()]
            echo = list(re.finditer(r"(?m)^\s*REVIEWING:.*$", head))
            final = head[echo[-1].end():] if echo else head
            verdict = json.loads(blocks[-1].group(1))["verdict"]
            words = decl_words(final.splitlines())
            if words - {verdict}:
                raise ValueError(f"{p}: prose verdict {sorted(words)} contradicts the review-verdict block "
                                 f"({verdict})")
            flines = final.splitlines()
            ver = verified_lines(flines)
            for i, line in enumerate(flines):
                if i in ver and tolerated_note(line, mine):
                    continue
                if FINDING.match(line):
                    if clean(line) not in mine:
                        raise ValueError(f"{p}: finding missing from the review-verdict block: "
                                         f"{line.strip()[:100]}")
                elif finding_shaped(line):
                    raise ValueError(f"{p}: unrecognised finding format before the review-verdict block: "
                                     f"{line.strip()[:100]}")
            check_summary_counts(p, final.splitlines(), mine)
            sweep_whole_response(p, text, mine, verdict)
            found += mine
            continue
        # "PASS", "**FAIL**", "Verdict: FAIL", "**Verdict: PASS**" on a line of their own.
        heads = list(re.finditer(VERDICT_HEAD.pattern, text, re.M | re.I))
        if not heads:
            raise ValueError(f"{p}: no PASS/FAIL verdict line (empty or truncated review?)")
        # The final response (after the last REVIEWING echo, if any) gives one verdict. A second,
        # different verdict line there (quoted test output, a changed mind) is refused, never picked.
        echo = list(re.finditer(r"(?m)^\s*REVIEWING:.*$", text))
        final_from = echo[-1].end() if echo else 0
        words = decl_words(text[final_from:].splitlines())
        if len(words) > 1:
            raise ValueError(f"{p}: conflicting verdict lines in the final response ({sorted(words)})")
        # With an echo, parse from the FIRST verdict line of the final response, so a later quoted
        # "Verdict: ..." can't restart parsing past a finding. Without one (older codex logs repeat
        # the final message), keep the last verdict block, up to its trailer.
        final_heads = [h for h in heads if h.start() >= final_from]
        last = final_heads[0] if echo and final_heads else heads[-1]
        body = text[last.end():]
        stop = re.search(r"(?m)^\s*(?:#+\s*)?(?:\*\*)?(HAZARDS|VERIFIED OK|UNVERIFIED)\b", body)
        lines = (body[:stop.start()] if stop else body).splitlines()
        mine = []
        for line in lines:
            if FINDING.match(line):
                mine.append(clean(line))
            elif finding_shaped(line):
                raise ValueError(f"{p}: unrecognised finding format: {line.strip()[:100]}")
        # Past the trailer, evidence prose may cite hazard ids (H-P1) and file:line freely, but a
        # finding-shaped line there is one the parser would drop: refuse. Counts must agree too.
        after = body[stop.start():].splitlines() if stop else []
        ver = verified_lines(after)
        for i, line in enumerate(after):
            if i in ver and tolerated_note(line, mine):
                continue
            if finding_shaped(line):
                raise ValueError(f"{p}: finding line after the {stop.group(1)} section: {line.strip()[:100]}")
        check_summary_counts(p, after, mine)
        if last.group(1).upper() == "FAIL" and not mine:
            raise ValueError(f"{p}: verdict is FAIL but no finding lines parsed")
        check_summary_counts(p, lines, mine)
        sweep_whole_response(p, text, mine, last.group(1).upper())
        found += mine
    return found


def sweep_whole_response(p, text, mine, verdict):
    """Nothing the parsed segment left out may sit elsewhere in the file (LUCY-p4pjf, Astra r15).

    From the first REVIEWING echo (else the file start) to the end: every canonical finding line is
    one the parser kept, no other finding-shaped line appears, and no verdict line or review-verdict
    block disagrees with `verdict`. Two appended responses for one tip, or a finding written above
    the verdict line, refuse the file instead of losing the finding."""
    echo = re.search(r"(?m)^\s*REVIEWING:.*$", text)
    region = text[echo.end():] if echo else text
    blocks = list(STRUCTURED.finditer(region))
    prose = STRUCTURED.sub("", region)
    plines = prose.splitlines()
    ver = verified_lines(plines)
    kept = []
    for i, line in enumerate(plines):
        if i in ver and tolerated_note(line, mine):
            continue
        kept.append(line)
        if FINDING.match(line):
            if clean(line) not in mine:
                raise ValueError(f"{p}: finding outside the parsed response: {line.strip()[:100]}")
        elif finding_shaped(line):
            raise ValueError(f"{p}: finding-shaped line outside the parsed response: {line.strip()[:100]}")
    # Every declaration and tally anywhere in the response (not only the parsed segment) must agree.
    words = decl_words(kept)
    check_summary_counts(p, [ln for ln in kept if not FINDING.match(ln)], mine)
    for b in blocks:
        try:
            words.add(str(json.loads(b.group(1)).get("verdict", "")).upper())
        except (ValueError, AttributeError):
            raise ValueError(f"{p}: unreadable review-verdict block")
        for f in structured_findings(p, b.group(1)):
            if f not in mine:
                raise ValueError(f"{p}: review-verdict block finding outside the parsed response: {f[:100]}")
    if words - {verdict}:
        raise ValueError(f"{p}: verdicts {sorted(words)} in the file disagree with the final {verdict}")


def bead_comments_text(bead):
    """`bd comments <bead>` from the beads database that owns the bead's prefix."""
    env = dict(os.environ)
    if not env.get("BEADS_DIR"):
        for prefix, path in BEADS_DIRS_BY_PREFIX:
            if bead.lower().startswith(prefix):
                env["BEADS_DIR"] = os.path.expanduser(path)
    out = subprocess.run(["bd", "comments", bead], capture_output=True, text=True, timeout=30, env=env)
    if out.returncode != 0:
        raise ValueError(f"bd comments {bead} failed: {(out.stderr or out.stdout).strip()[:200]}")
    return out.stdout


def parse_comments(text):
    """[(utc datetime, body)] from `bd comments` output, oldest first."""
    records, cur = [], None
    for line in (text or "").splitlines():
        m = COMMENT_HEADER.match(line)
        if m:
            cur = [datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc), []]
            records.append(cur)
        elif cur is not None:
            cur[1].append(line)
    return [(ts, "\n".join(body).strip()) for ts, body in records]


def token_in(token, text):
    """`token` as a whole token: F1 is not F11, F1.2 or F1-old; AestheticcNext-e1xgj is not
    LUCY-e1xgj, AestheticcNext-e1xgj.1 or AestheticcNext-e1xgj-old."""
    return re.search(rf"(?<![A-Za-z0-9.-]){re.escape(token)}(?![A-Za-z0-9-]|\.[A-Za-z0-9])", text, re.I) is not None


def verify_clearances(cleared_ids, escalate, comments, not_before):
    """Return ({finding id: clearing comment line}, errors) for --cleared-escalation ids.

    Honoured only when all hold (LUCY-p4pjf):
      - the id is one of this round's escalated findings (by the lead's `id` field);
      - the finding is graded pre_existing: true and its follow_up names a bead id;
      - a bead comment whose first line starts `ORCHESTRATOR CLEARED` names the finding id AND
        that follow-up bead (ids repeat across rounds, follow-up beads don't);
      - that comment's minute starts after the newest verdict file's mtime (bd times are UTC
        minutes, so a comment in the verdict's own minute may predate it and does not count).
    """
    by_id = {f.get("id"): f for f in escalate if f.get("id")}
    honoured, errs = {}, []
    for cid in cleared_ids:
        f = by_id.get(cid)
        if f is None:
            errs.append(f"--cleared-escalation {cid}: not an escalated finding this round "
                        f"(escalated ids: {sorted(by_id) or 'none'})")
            continue
        if f.get("pre_existing") is not True:
            errs.append(f"--cleared-escalation {cid}: only a pre_existing finding can be cleared; this one "
                        "is the branch's own and must be fixed or graded again")
            continue
        follow = BEAD_ID.search(f.get("follow_up") or "")
        if not follow:
            errs.append(f"--cleared-escalation {cid}: follow_up names no bead id (file the follow-up first)")
            continue
        follow_id = follow.group(0)
        match = None
        for ts, body in comments:
            first = body.splitlines()[0].strip() if body else ""
            if (first.upper().startswith("ORCHESTRATOR CLEARED") and token_in(cid, first)
                    and token_in(follow_id, first) and ts >= not_before):
                match = first
        if match is None:
            errs.append(f"--cleared-escalation {cid}: no bead comment whose first line is 'ORCHESTRATOR "
                        f"CLEARED {cid} ... {follow_id}' dated after the verdict files "
                        f"(minute {not_before:%Y-%m-%d %H:%M} UTC or later)")
            continue
        honoured[cid] = match
    return honoured, errs


def pattern_names():
    return {norm(m.group(1)).lower() for m in re.finditer(r"(?m)^###\s+(.+)$", PATTERNS.read_text())}


def is_duplicate_path(f, src):
    return f.get("category") in STRUCTURAL_CATEGORIES or bool(DUPLICATE_PATH.search(src))


def has_ordinary_use(f):
    text = norm(f.get("ordinary_use") or "")
    return len(text) >= 12 and not NO_ORDINARY_USE.match(text)


def is_carve_out(f, protected):
    return protected or f.get("carve_out") is True or f.get("category") in CARVE_OUT_CATEGORIES


def triage_ordinary_use(f, protected):
    """Mark an act_on / consider finding `_demoted` (P4 note) when it has no ordinary-use path
    and is not in the carve-out. Dismissed / noted items are already non-blocking."""
    f["_carve_out"] = is_carve_out(f, protected) or f.get("_duplicate_path") is True
    f["_demoted"] = (f.get("verdict") in ("act_on", "consider") and not f["_carve_out"]
                     and not has_ordinary_use(f))
    return f["_demoted"]


def routed_out(f, scope_lock):
    """An act_on item that leaves this branch: out of scope under the lock, or pre-existing."""
    return f.get("verdict") == "act_on" and not f.get("_demoted") and (
        f.get("pre_existing") is True or (scope_lock and f.get("in_scope") is False))


ECHO_SHA = re.compile(r"(?m)^\s*REVIEWING:\s*\S+\s*@\s*([0-9a-fA-F]{7,40})[ \t]*$")


def fixed_errors(tag, f, src, files):
    """A finding graded `fixed` resolves only when a later independent review of another commit
    confirms it (LUCY-p4pjf): `fixed_by` is one of the --verdict files, newer than the file that
    raised the finding and echoing a different commit; it does not raise the same severity at the
    same file:line again; and its VERIFIED OK section has "- r<N> <sev> <file:line> closed: ...",
    N being the round in the raising file's name (_r<N>_)."""
    if not files:
        return [f"{tag}: fixed needs the reviewer files (run through main)"]
    by = (f.get("fixed_by") or "").strip()
    if not by:
        return [f"{tag}: fixed needs fixed_by: the later review file that confirms it"]
    later = next((x for x in files if str(Path(x[0]).resolve()) == str(Path(by).expanduser().resolve())), None)
    if later is None:
        return [f"{tag}: fixed_by {by} is not one of the --verdict files"]
    source = next((x for x in files if src in x[1]), None)
    if source is None or source is later:
        return [f"{tag}: fixed_by must be a different review from the one that raised the finding"]
    l_sha = ECHO_SHA.search(later[2])
    # Every input that raises it (not just the first) must be an older review of another commit.
    if not l_sha:
        return [f"{tag}: fixed_by {Path(later[0]).name} has no REVIEWING echo"]
    b_ = l_sha.group(1).lower()
    for x in files:
        if src not in x[1]:
            continue
        x_sha = ECHO_SHA.search(x[2])
        a_ = x_sha.group(1).lower() if x_sha else ""
        if x is later or not a_ or a_.startswith(b_) or b_.startswith(a_):
            return [f"{tag}: {Path(x[0]).name} raises it at the commit fixed_by reviewed (REVIEWING echoes): not fixed"]
        if Path(later[0]).stat().st_mtime <= Path(x[0]).stat().st_mtime:
            return [f"{tag}: fixed_by {Path(later[0]).name} is not newer than {Path(x[0]).name}"]
    rnd = re.search(r"_r(\d+)(?=[_.])", Path(source[0]).name)
    loc = FILE_LINE.search(src)
    if not rnd or not loc:
        return [f"{tag}: fixed needs a round in {Path(source[0]).name} (_r<N>_) and a file:line in the finding"]
    sev = src[:2]
    for other in later[1]:
        o = FILE_LINE.search(other)
        if other[:2] == sev and o and o.group(0) == loc.group(0):
            return [f"{tag}: {Path(later[0]).name} raises {sev} at {loc.group(0)} again: not fixed"]
    lines = later[2].splitlines()
    for i in verified_lines(lines):
        m = CONFIRM_FIXED.match(lines[i])
        if (m and is_confirmation(lines[i]) and m.group(1) == rnd.group(1) and m.group(2).upper() == sev
                and m.group(3) == loc.group(0)):
            return []
    return [f"{tag}: {Path(later[0]).name} has no VERIFIED OK line '- r{rnd.group(1)} {sev} {loc.group(0)} "
            "closed: ...' confirming the fix"]


def check(lead, reviewer_lines, round_no, scope_lock, files=None):
    errs = []
    items = lead.get("findings")
    if not isinstance(items, list):
        return ["lead JSON has no findings list"], None
    known = pattern_names()
    seen = {}
    ids = [f.get("id") for f in items if isinstance(f, dict) and f.get("id")]
    for dup_id in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(f"finding id {dup_id!r} is used {ids.count(dup_id)}x: ids must be unique (clearance is by id)")
    for i, f in enumerate(items):
        tag = f.get("id") or f"#{i + 1}"
        src = clean(f.get("source_line", ""))
        if not src:
            errs.append(f"{tag}: no source_line")
            continue
        seen[src] = seen.get(src, 0) + 1
        v = f.get("verdict")
        if v not in VERDICTS:
            errs.append(f"{tag}: verdict {v!r} is not one of {sorted(VERDICTS)}")
            continue
        cat = f.get("category")
        if cat not in CATEGORIES:
            errs.append(f"{tag}: category {cat!r} is not one of {sorted(CATEGORIES)}")
        if v == "fixed":
            errs += fixed_errors(tag, f, src, files)
            continue
        protected = cat in PROTECTED_CATEGORIES or bool(PROTECTED.search(src))
        f["_protected"] = protected
        dup = f["_duplicate_path"] = is_duplicate_path(f, src)
        if triage_ordinary_use(f, protected):
            pass  # P4 note: no scenario, demonstration, reason or follow-up demanded
        elif v == "act_on":
            if not dup and not norm(f.get("clinic_scenario", "")):
                errs.append(f"{tag}: act_on needs a clinic_scenario (who, which device, what moment)")
            if not norm(f.get("demonstrated", "")):
                errs.append(f"{tag}: act_on needs 'demonstrated' (red test, repro or traced path)")
            if scope_lock:
                if not isinstance(f.get("in_scope"), bool):
                    errs.append(f"{tag}: scope lock is on, act_on needs in_scope true/false")
                elif f["in_scope"] is False and not norm(f.get("follow_up", "")):
                    errs.append(f"{tag}: out-of-scope act_on needs a follow_up bead title")
            if f.get("pre_existing") is True and not norm(f.get("follow_up", "")):
                errs.append(f"{tag}: pre_existing act_on needs a follow_up bead title (it goes to a new "
                            "bead, not this branch)")
        else:
            if len(norm(f.get("reason", ""))) < 12:
                errs.append(f"{tag}: {v} needs a written reason")
            if v == "consider" and not norm(f.get("follow_up", "")):
                errs.append(f"{tag}: consider needs a follow_up bead title")
            if protected and v in ("dismissed", "noted"):
                errs.append(f"{tag}: never-dismiss area (booking/money/sends/webhooks/tenancy/GDPR/"
                            f"prescribing/single-device data loss): must be act_on or consider, not {v}")
            if v == "noted" and dup:
                errs.append(f"{tag}: H-D0 duplicate path is graded P2 and cannot be Noted; act_on, consider "
                            "or dismissed")
            elif v == "noted" and not src.startswith("P3"):
                errs.append(f"{tag}: only a P3 may be Noted; a P1/P2 is act_on, consider or dismissed")
            if v == "dismissed" and src.startswith("P1") and not f.get("pattern"):
                errs.append(f"{tag}: dismissing a P1 needs a DISMISSAL_PATTERNS heading; a new shape stays "
                            "Consider (follow-up) until a pattern is written")
        pat = f.get("pattern")
        if pat and norm(pat).lower() not in known:
            errs.append(f"{tag}: pattern {pat!r} is not a heading in DISMISSAL_PATTERNS.md")
    acked = {clean(n.get("line", "")) for n in (lead.get("verified_notes") or [])
             if isinstance(n, dict) and len(norm(n.get("reason", ""))) >= 12}
    for key, raw in UNBOUND_NOTES.items():
        if key not in acked:
            errs.append(f"VERIFIED OK note discusses a finding; list it in verified_notes with a reason, or "
                        f"have the reviewer state it as a finding: {raw[:100]}")
    want = {}
    for line in reviewer_lines:
        want[line] = want.get(line, 0) + 1
    for line, n in want.items():
        if seen.get(line, 0) != n:
            errs.append(f"reviewer finding covered {seen.get(line, 0)}x (want {n}x): {line[:100]}")
    for line in seen:
        if line not in want:
            errs.append(f"lead lists a finding no reviewer made: {line[:100]}")
    blocking = [f for f in items if f.get("verdict") == "act_on" and not f.get("_demoted")
                and not routed_out(f, scope_lock) and (not scope_lock or f.get("in_scope") is True)]
    if len(blocking) > ACT_ON_CAP and not norm(lead.get("act_on_over_cap_reason") or ""):
        errs.append(f"{len(blocking)} blocking act_on items (> {ACT_ON_CAP}): not filtering; filter harder "
                    "or give act_on_over_cap_reason")
    if round_no >= 2 and not isinstance(lead.get("premise"), dict):
        errs.append("round >= 2: premise block required (attack the premise)")
    return errs, blocking


def action_entry(f):
    if f.get("_duplicate_path"):
        scenario = norm(f.get("clinic_scenario", "")) or "n/a: structural, no clinic-scenario filter"
        grade = "P1" if clean(f["source_line"]).startswith("P1") else "P2"
        return (f"{f['source_line']}\n  graded: {grade} (H-D0 duplicate path, blocks landing)\n"
                f"  scenario: {scenario}\n  shown by: {f['demonstrated']}\n")
    return f"{f['source_line']}\n  scenario: {f['clinic_scenario']}\n  shown by: {f['demonstrated']}\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", required=True)
    ap.add_argument("--verdict", action="append", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--scope-lock", action="store_true")
    ap.add_argument("--bead", help="bead id; required with --cleared-escalation")
    ap.add_argument("--cleared-escalation", action="append", default=[], metavar="FINDING_ID",
                    help="an escalated pre-existing finding the orchestrator cleared on the bead")
    ap.add_argument("--comments-file", help="`bd comments` output to read instead of calling bd")
    a = ap.parse_args(argv)
    if a.cleared_escalation and not a.bead:
        print("review-lead: REFUSED: --cleared-escalation needs --bead", file=sys.stderr)
        return 1
    UNBOUND_NOTES.clear()
    try:
        lead = json.loads(Path(a.lead).read_text())
        reviewer_lines = reviewer_findings(a.verdict)
        files = [(v, reviewer_findings([v]), Path(v).read_text(errors="ignore")) for v in a.verdict]
    except Exception as e:
        print(f"review-lead: REFUSED: cannot read inputs ({e})", file=sys.stderr)
        return 1
    if not isinstance(lead, dict):
        print("review-lead: REFUSED: lead JSON is not an object", file=sys.stderr)
        return 1
    if bool(lead.get("scope_lock")) != a.scope_lock:
        print(f"review-lead: REFUSED: lead scope_lock={lead.get('scope_lock')} but gate says {a.scope_lock}",
              file=sys.stderr)
        return 1
    errs, blocking = check(lead, reviewer_lines, a.round, a.scope_lock, files)
    if errs:
        print("review-lead: REFUSED, re-run the lead with these fixed:", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    items = lead["findings"]
    out_of_scope = [f for f in items if routed_out(f, a.scope_lock)]
    demoted = [f for f in items if f.get("_demoted")]
    consider = [f for f in items if f["verdict"] == "consider" and not f.get("_demoted")]
    # Protected items not fixed on this branch go to the orchestrator, never silently into a bead.
    # So does an unresolved H-D0 the branch added: a P2 duplicate path blocks landing (LUCY-fuu2h).
    def must_escalate(f):
        return f.get("_protected") or (f.get("_duplicate_path") and f.get("pre_existing") is not True)
    escalate = [f for f in consider if must_escalate(f)] + \
               [f for f in out_of_scope if must_escalate(f)]
    cleared = {}
    if a.cleared_escalation:
        try:
            text = Path(a.comments_file).read_text() if a.comments_file else bead_comments_text(a.bead)
        except Exception as e:
            print(f"review-lead: REFUSED: cannot read bead comments ({e})", file=sys.stderr)
            return 1
        newest = max(Path(v).stat().st_mtime for v in a.verdict)
        floor = datetime.fromtimestamp(newest, timezone.utc).replace(second=0, microsecond=0)
        not_before = floor if floor.timestamp() >= newest else floor + timedelta(minutes=1)
        cleared, cerrs = verify_clearances(a.cleared_escalation, escalate, parse_comments(text), not_before)
        if cerrs:
            print("review-lead: REFUSED, clearance not verified:", file=sys.stderr)
            for e in cerrs:
                print(f"  - {e}", file=sys.stderr)
            return 1
        escalate = [f for f in escalate if f.get("id") not in cleared]
    follow = [f for f in consider if not must_escalate(f)] + \
             [f for f in out_of_scope if not must_escalate(f)]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    n = a.round
    (out / f"lead-actions-round-{n}.txt").write_text("".join(action_entry(f) for f in blocking))
    (out / f"lead-escalate-round-{n}.txt").write_text(
        "".join(f"{f['source_line']}\n  why not fixed here: {f.get('reason') or f.get('follow_up')}\n"
                for f in escalate))
    (out / f"lead-cleared-round-{n}.txt").write_text(
        "".join(f"{cid}\t{line}\t{f['source_line']}\n" for f in items
                for cid, line in cleared.items() if f.get("id") == cid))
    (out / f"lead-followups-round-{n}.txt").write_text(
        "".join(f"{f['follow_up']}\t{f['source_line']}\n" for f in follow))
    rows = [f"# Review lead, round {n}: {lead.get('bead', '?')}", ""]
    prem = lead.get("premise") or {}
    if prem.get("premise"):
        rows += [f"**Premise attacked:** {prem['premise']} (rounds failed on it: {prem.get('rounds_failed_on_it')})",
                 f"**Instead:** {prem.get('action')}", ""]
    for f in items:
        if f["verdict"] == "act_on" and f in blocking:
            continue
        if f.get("_demoted"):
            rows.append(f"- **p4 note** (was {f['verdict']}; no ordinary-use path, not carve-out; no fix, "
                        f"no re-review): {f['source_line']}\n  - reason: {f.get('reason') or 'ordinary-use test'}")
            continue
        if f["verdict"] == "fixed":
            rows.append(f"- **fixed**: {f['source_line']}\n  - confirmed by: {f.get('fixed_by')}")
            continue
        if f["verdict"] == "act_on":
            label = "act_on (pre-existing)" if f.get("pre_existing") is True else "act_on (out of scope)"
        else:
            label = f["verdict"]
        why = f.get("reason") or f"{'pre-existing' if f.get('pre_existing') is True else 'scope lock'}: " \
                                 f"{f.get('follow_up')}"
        pat = f" [pattern: {f['pattern']}]" if f.get("pattern") else ""
        if f.get("id") in cleared:
            why += f" | cleared by the orchestrator: {cleared[f['id']]}"
        rows.append(f"- **{label}**{pat}: {f['source_line']}\n  - reason: {why}")
    for nt in lead.get("verified_notes") or []:
        if isinstance(nt, dict):
            rows.append(f"- **verified note** (reviewer's own, outside its finding): {nt.get('line')}\n"
                        f"  - reason: {nt.get('reason')}")
    (out / f"lead-dismissals-round-{n}.md").write_text("\n".join(rows) + "\n")
    counts = {k: sum(1 for f in items if f["verdict"] == k) for k in ("dismissed", "noted", "fixed")}
    print(f"BLOCKING={len(blocking)} ESCALATE={len(escalate)} FOLLOWUPS={len(follow)} "
          f"DISMISSED={counts['dismissed']} NOTED={counts['noted']} P4={len(demoted)} CLEARED={len(cleared)} "
          f"FIXED={counts['fixed']}")
    for cid, line in cleared.items():
        print(f"CLEARED {cid}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
