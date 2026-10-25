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
  - a pre_existing act_on / consider item lacks a follow_up bead title;
  - a `fixed` grade has no "closed" prior entry in a review of the current tip (LUCY-e3opf).
The review-verdict grammar (LUCY-e3opf, attempts 3-4), each refused:
  - G1: more than one top-level review-verdict block in a file (fences nest CommonMark-style, so a
    block quoted inside a longer fence is content), an unclosed one, or a review-verdict opener that is
    not at column 0 or sits inside HTML (indented, list item, quote, <!-- -->, <div>): refused, never dropped;
  - G2: a key missing, extra, null or empty: top level {verdict, findings[, prior]}, a finding
    {severity, file_line, text}, a prior entry {ref, status, evidence}. A raising review may use the
    legacy {severity, text}; a review of the current tip may not, whatever its findings are graded;
  - G3: a prior ref matching no round-N finding. Every finding a ref could name alongside another
    distinct one escalates, whatever it is graded, unless it is graded fixed and every newest-round seat
    closes that ref with evidence (LUCY-unykv, every_seat_closes);
  - G4: an input file without exactly one well-formed REVIEWING echo line, unconditionally (builder
    evidence excepted: it carries none, D1);
  - G5: a prose severity + path, anywhere in the file (code spans read as plain text), that matches no
    block finding or prior ref by severity and file (and line, when given). Wording is never compared.
    Lines that start like a finding but name no file, and severities whose only path is a weak slash-word
    ("user/owned/member", LUCY-16n7i), may not outnumber the block's findings + prior refs of that
    severity. Frozen: LEAD.md "Declared prose limits".
A `fixed` grade counts only when EVERY review of the newest round (at the current tip) closes it in its prior
array (C5). Any review of the tip marking it "open" escalates it; a newest-round one with no entry leaves it not
fixed (blocking). Prose never grades. Inputs (attempt 5), each refused:
  - C1: lines split on "\n" only, never str.splitlines();
  - C2: a review-verdict block in a builder-evidence file (mutants-round-N.txt, qa-*-round-N.log); builder
    evidence is never a seat and never fixed_by;
  - C3: with 2+ inputs, a file whose name states no round (_r<N>_ or round-<N>), or a newest round on two
    commits. The mtime never orders reviews.
  - C4: an ambiguous ref escalates whatever the grade, act_on included (a fixed grade every newest-round
    seat closes excepted, G3). C6: an earlier (non-tip) review's
    unmatched prose note is printed as NOTE, not refused.
Names never decide identity (attempt 6), each refused:
  - D1: a builder-evidence name carrying a REVIEWING echo or a review-verdict block. Builder evidence is
    echo-free, so it is exempt from G4;
  - D2: a name stating its round more than once (_r<N>_ and round-<N>, or two of one).
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
# The file-length ratchet names the files it counts, and the lander regenerates its baseline, so a credit or
# cron file name must not make it protected (LUCY-mz6fk). Exempt from the keyword backstop by SHAPE only: a P3
# declared at the ratchet test whose text, once its "<source file> [is] N/M [lines]" counts and the fixed
# phrases below are removed, is only punctuation and RATCHET_WORDS. No word bag can say anything about money,
# booking, sends or tenancy; anything else (a claim, another path, a digit, non-ASCII) keeps the backstop.
# A protected category still wins.
RATCHET_TEST = "tests/meta/file-length-ratchet.test.ts"
# A path, as FILE_LINE writes one. A "(group)/" segment is a Next.js route group: app/(home)/page.tsx:12
# (LUCY-e3opf). The ratchet counts paths in the same grammar: app/(booking)/page.tsx 588/578 (LUCY-mz6fk).
PATH = r"(?<![\w./\[\])-])(?=[\w./\[\]-]*[A-Za-z_/])(?:[\w./\[\]-]|\([\w.-]+\)(?=/))+"
RATCHET_COUNT = re.compile(PATH + r"\.(?:tsx?|jsx?|mjs|cjs|py|sh|sql|css)"
                           r"\s+(?:is\s+)?\d+\s*/\s*\d+(?:\s+lines?)?(?![\w/])")
RATCHET_PHRASES = re.compile(r"\bno (?:real )?clinic scenario\b|\bno runtime effect\b|\bbuild[- ]gate failure\b"
                             r"|\bbuild hygiene\b|\bfile[- ]length ratchet\b"
                             r"|\bas explicitly assigned in the bead['\u2019]s comments\b", re.I)
RATCHET_WORDS = frozenset("a an the and of for in is as during ratchet baseline regenerate regenerates regenerated regen "
                          "lander lander's landing line lines".split())


def ratchet_only(src):
    loc = leading_loc(src)
    if not src.startswith("P3") or not loc or loc.rsplit(":", 1)[0] != RATCHET_TEST:
        return False
    rest = RATCHET_PHRASES.sub(" ", RATCHET_COUNT.sub(" ", src[3:].replace(loc, " ", 1)))
    if not re.fullmatch(r"[A-Za-z'\u2019\s,;:.()`*_\u2013\u2014-]*", rest):
        return False
    return all(w in RATCHET_WORDS for w in re.findall(r"[a-z]+(?:['\u2019][a-z]+)?", rest.lower().replace("\u2019", "'")))


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
FILE_LINE = re.compile(PATH + r":\d+")
# A finding's location as written, keeping a line range ("lib/t.ts:78-83"); compared exactly (LUCY-l1s6g c).
LOC = re.compile(FILE_LINE.pattern + r"(?:-\d+)?(?![\w-])")
# A count is a number standing alone: "1 P1", "(0 P1", never the 1 in "Round-1 P1" (LUCY-l1s6g e).
SUMMARY_COUNT = re.compile(r"(?<![\w-])(\d+)\s*(?:x\s*)?(P[123])\b")
# Structured verdict (LUCY-p4pjf): a fenced ```review-verdict block holding JSON
#   {"verdict": "PASS"|"FAIL",
#    "findings": [{"severity": "P2", "file_line": "lib/x.ts:12", "text": "failure scenario — fix"}],
#    "prior": [{"ref": "r1 P2 lib/x.ts:12", "status": "closed"|"open", "evidence": "how checked"}]}
# A file holds at most one, at top level; it is the whole source of that file's findings, and its prose
# is checked against it by location only (LUCY-e3opf G1, G5).
# `prior` (LUCY-e3opf) is the only thing that grades an earlier finding fixed: prose "closed:" lines
# are never graded, because negation-word rules could not be both fail-closed and accepting.
# A fence opener (LUCY-e3opf G1): n>=3 backticks or tildes, then the info string. It closes only on a
# line of >=n of the same character, CommonMark-style, so a 4-backtick example can hold a 3-backtick
# review-verdict block that is content, not the reviewer's answer.
# Indented 4+ spaces or a tab, it is an indented code block, not a fence (LUCY-e3opf A1).
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# HTML (A1), CommonMark 0.31 HTML blocks, recognised at line start only: types 1-5 run to their own end
# marker (`</pre>`, `-->`, `?>`, `>`, `]]>`), which may sit on the start line; any other line starting with a
# tag (types 6-7, `<div>`) runs to the next blank line. A `<!--` mid-line is inline text, not a block.
HTML_ENDS = ((re.compile(r" {0,3}<(?:pre|script|style|textarea)(?:[ \t>]|$)", re.I),
              re.compile(r"</(?:pre|script|style|textarea)>", re.I)),
             (re.compile(r" {0,3}<!--"), re.compile(r"-->")),
             (re.compile(r" {0,3}<\?"), re.compile(r"\?>")),
             (re.compile(r" {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
             (re.compile(r" {0,3}<![A-Za-z]"), re.compile(r">")))
HTML_START = re.compile(r"^ {0,3}<[A-Za-z/]")
BLANK_ENDS = re.compile(r"^[ \t]*$")
# Builder evidence (/goal Phase 4): the mutants file and QA logs. Its findings (surviving mutants) are covered
# like any other, but it is never a review seat under B1 and never fixed_by, and it may not carry a review-verdict
# block: a review under a builder-evidence name is refused, so a name can't take a seat out of the roster
# (LUCY-e3opf C2).
BUILDER_EVIDENCE = re.compile(r"(?:mutants|qa-[\w-]+)-round-\d+\.(?:txt|log)")


def is_builder_evidence(name):
    return bool(BUILDER_EVIDENCE.fullmatch(Path(str(name)).name))


def has_review_content(p, text):
    """A REVIEWING line anywhere, or a review-verdict block, is what makes a file a review, whatever its name
    (LUCY-e3opf D1). Builder evidence has neither. one_block refuses any opener that isn't one clean block."""
    return any(re.match(r"\s*REVIEWING:", ln) for ln in lines_of(text)) or bool(one_block(p, text))


def builder_evidence_errors(files):
    """A file named like builder evidence that carries a REVIEWING echo or a review-verdict block is a review in
    disguise: refused, so a name can't take a seat (a blockless one included) out of the roster or the tip
    (LUCY-e3opf C2, D1)."""
    return [f"{Path(x[0]).name}: review content in a builder-evidence file (a REVIEWING echo or a review-verdict "
            "block in a file named like the mutants file or a QA log): pass the review under its own review name; "
            "builder evidence carries neither"
            for x in files if is_builder_evidence(x[0]) and has_review_content(x[0], x[2])]
# A review-verdict opener behind any indent or container marker (list item, blockquote). Only one at column 0,
# outside HTML, is the reviewer's answer; any other is refused, never silently dropped (A1).
VERDICT_OPENER = re.compile(r"^[ \t>*+\-.)\d]*(?:<(?:!--|[A-Za-z][^>]*>)[ \t]*)*(?:`{3,}|~{3,})[ \t]*review-verdict\b",
                            re.I)
# The exact key sets of a review-verdict block (LUCY-e3opf G2). No aliases: priority is not severity.
TOP_KEYS, TOP_OPTIONAL = ("verdict", "findings"), ("prior",)
FINDING_KEYS = ("severity", "file_line", "text")
LEGACY_FINDING_KEYS = ("severity", "text")  # written before file_line existed: raising reviews only
PRIOR_KEYS = ("ref", "status", "evidence")


def lines_of(text, keepends=False):
    """The file's lines, split on "\\n" only, one trailing "\\r" dropped (LUCY-e3opf C1). Never str.splitlines(),
    which also breaks on VT, FF, U+0085, U+2028 and U+2029: CommonMark does not, so a fence after one of
    those is not at a line start."""
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    if keepends:
        return [p + "\n" for p in parts[:-1]] + ([parts[-1] + ("\n" if text.endswith("\n") else "")] if parts else [])
    return [p[:-1] if p.endswith("\r") else p for p in parts]


class Block:
    """One top-level review-verdict block: start/end of the whole fence in the file, and its JSON body."""

    def __init__(self, start, end, body):
        self.start, self.end, self.body = start, end, body


def html_left_open(ends, s, at):
    """The HTML block's end pattern if line s (from index at) leaves it open, else None. A comment stays open
    when a `<!--` follows its last `-->` on the line: the raw HTML passes through and the browser hides
    everything up to the next `-->` (Opus own-gate r2 :174)."""
    if ends is BLANK_ENDS:
        return None if ends.match(s) else ends
    if not ends.search(s, at):
        return ends
    if ends.pattern == "-->":
        return ends if s.rfind("<!--") > s.rfind("-->") else None
    return None


def verdict_blocks(p, text):
    """The review-verdict blocks at top level, outside any other fence or HTML block (LUCY-e3opf G1,
    A1). An unclosed review-verdict fence, an unclosed fence that swallows one, or a file whose only
    review-verdict blocks sit inside another fence, refuses the file."""
    out, fence, pos, html, nested = [], None, 0, None, False
    for line in lines_of(text, keepends=True):
        start, pos = pos, pos + len(line)
        s = line.rstrip("\r\n")
        if fence is None and VERDICT_OPENER.match(s) and (html or not re.match(r"`{3,}|~{3,}", s)):
            raise ValueError(f"{p}: a review-verdict block is not top-level (indented, in a list or quote, or "
                             f"inside HTML): put it at column 0: {s.strip()[:80]}")
        if html:
            html = html_left_open(html, s, 0)
            continue
        if fence is None:
            for opens, ends in HTML_ENDS:
                m = opens.match(s)
                if m:
                    html = html_left_open(ends, s, m.end())
                    break
            else:
                if HTML_START.match(s):
                    html = BLANK_ENDS
                    continue
                m = FENCE.match(s)
                if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                    info = m.group(2).split()
                    fence = (m.group(1)[0], len(m.group(1)), bool(info) and info[0].lower() == "review-verdict",
                             start, pos)
            continue
        ch, n, is_verdict, ostart, bstart = fence
        if re.fullmatch(rf" {{0,3}}{re.escape(ch)}{{{n},}}[ \t]*", s):
            if is_verdict:
                out.append(Block(ostart, pos, text[bstart:start]))
            fence = None
        elif VERDICT_OPENER.match(s):
            nested = True
    if fence is not None and (fence[2] or re.search(r"(?m)^ {0,3}(?:`{3,}|~{3,})[ \t]*review-verdict\b",
                                                     text[fence[4]:])):
        raise ValueError(f"{p}: a review-verdict block is not closed (no closing fence after it)")
    if nested and not out:
        raise ValueError(f"{p}: the only review-verdict block sits inside another fence: put the answer's block "
                         "at column 0, outside any fence")
    return out


def one_block(p, text):
    """The file's single review-verdict block, or None. Two or more is two responses: refused, never
    merged or last-one-wins (LUCY-e3opf G1)."""
    blocks = verdict_blocks(p, text)
    if len(blocks) > 1:
        raise ValueError(f"{p}: {len(blocks)} review-verdict blocks; one response per file")
    return blocks[0] if blocks else None


def key_error(what, d, need, optional=()):
    """'' when dict d has exactly the keys `need` (plus any of `optional`), else a message naming the
    missing and unexpected keys (LUCY-e3opf G2)."""
    keys = set(d)
    missing = [k for k in need if k not in keys]
    extra = sorted(keys - set(need) - set(optional))
    if not missing and not extra:
        return ""
    parts = ([f"missing keys {','.join(missing)}"] if missing else []) + \
            ([f"unexpected keys {','.join(extra)}"] if extra else [])
    return f"{what}: {'; '.join(parts)}; expected {','.join(need + tuple(optional))}"
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
# A later round's prose note on an earlier finding, in its VERIFIED OK section: "- r9 P3 lib/x.ts:37
# closed: <how it was checked>". It only lets such a note through the parser silently; it never grades a
# finding fixed (the block's `prior` array does that, LUCY-e3opf).
CONFIRM_FIXED = re.compile(r"^\s*[-*]\s+(?:\*\*)?r(\d+)\s+(P[123])\s+(\S+:\d+(?:-\d+)?)(?:\*\*)?\s+(?:closed|fixed|resolved):\s+"
                           r"(?=\S)[^?]{8,}$", re.I)
CONFIRM_NEGATION = re.compile(r"\b(not|no|never|but|however|yet|partial|partially|unable|cannot|"
                              r"unconfirmed|unverified|untested|fails?|failed|broken|regressed|reopen\w*|"
                              r"unresolved|outstanding|pending|intermittent\w*|flaky|sometimes)\b"
                              r"|n't\b", re.I)
# "still" / "remains" are adverse unless the state that follows is a passing one ("the control remains
# green"); "removing the fix makes it fail" is mutant evidence, not a failing fix (LUCY-l1s6g d).
CONFIRM_STATE = re.compile(r"\b(still|remains?|remaining)\b(?!\s+(?:green|passing|passes|pass|correct|clean|"
                           r"intact|unchanged|closed|fixed|resolved)\b)", re.I)
MUTANT_FAIL = re.compile(r"\bmakes?\s+(?:it|them)\s+fail\b", re.I)
# "open" is adverse unless a named thing other than the finding is what stays open ("the held thread
# now stays open", the fix's own behaviour). "stays open", "left open", "the bug is open" stay adverse.
OPEN_SUBJECT = re.compile(r"\b([A-Za-z_]\w*)\s+(?:\([^)]*\)\s+)?(?:now\s+)?(?:is\s+|are\s+)?"
                          r"(?:stays?|stayed|left|kept|remains?)?\s*open\b", re.I)
FINDING_REFERENTS = {"it", "this", "that", "which", "finding", "findings", "issue", "issues", "bug", "bugs",
                     "problem", "defect", "gap", "bead", "ticket", "one", "is", "are", "now", "still",
                     "closed", "fixed", "resolved", "stays", "stay", "stayed", "left", "kept", "remains", "remain"}
# A quoted test-cell name ("... is still herself on her next WhatsApp" is green) is evidence naming,
# not adverse wording (LUCY-l1s6g h). Severity, location and question checks still read the quotes.
QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”|`[^`]*`")


def adverse(tail):
    """True when the evidence tail of a 'closed:' line says the finding is not (fully) fixed."""
    t = MUTANT_FAIL.sub(" ", QUOTED.sub(" ", tail))
    if CONFIRM_NEGATION.search(t) or CONFIRM_STATE.search(t):
        return True
    for m in re.finditer(r"\bopen\b", t, re.I):
        clause = re.split(r"[;:,.]", t[:m.end()])[-1]
        subj = OPEN_SUBJECT.search(clause)
        if not subj or subj.end() != len(clause) or subj.group(1).lower() in FINDING_REFERENTS:
            return True
    return False


def is_confirmation(line):
    """Affirmative only: "closed:" then evidence, no question, no negative or unresolved wording."""
    m = CONFIRM_FIXED.match(line)
    if not m:
        return False
    # The evidence tail is evidence only: no question, no adverse wording, and no severity or
    # location (that would be a new finding). Paths before it may contain any word.
    tail = line[line.index(":", m.end(3)) + 1:]
    return not ("?" in tail or adverse(tail) or SEVERITY_TOKEN.search(tail)
                or FILE_LINE.search(tail) or EXPLICIT_LABEL.search(tail))


ROUND_IN_NAME = re.compile(r"_r(\d+)(?=[_.])")
# The /goal workflow's own names: astra-verdict-round-2.log, opus-h2h-round-3.md, mutants-round-2.txt.
ROUND_NATIVE = re.compile(r"(?:^|[_.-])round-(\d+)(?=[_.-]|$)")


def round_of(name):
    """The review round a file's name states: `_r<N>_` / `_r<N>.`, or `round-<N>` in a workflow-native name;
    None when it states neither (LUCY-e3opf C3, folds LUCY-888ru). Never a default, never the mtime: a copy
    resets the mtime, and every unnamed file used to be round 1. Two different rounds in one name refuse."""
    base = Path(str(name)).name
    got = [int(m.group(1)) for rx in (ROUND_IN_NAME, ROUND_NATIVE) for m in rx.finditer(base)]
    if len(got) > 1:  # every match counts, not distinct rounds: _r1_ + round-1 hides a namespace (D2)
        raise ValueError(f"{base}: the name states a round {len(got)} times ({', '.join(map(str, got))}); one "
                         "round token per file, _r<N>_ or round-<N>, never both")
    return got[0] if got else None


def review_seats(files):
    """The inputs that are reviews: every one except builder evidence (C2), which never sets the round or tip."""
    return [x for x in files if not is_builder_evidence(x[0])]


def check_rounds(files):
    """Refusals for a run whose inputs can't be ordered (LUCY-e3opf C3). With more than one input, every file
    names its round, and the newest round reviewed one commit: no mtime tie-break picks between two tips.
    Every name, even a lone input's, states its round at most once (D2)."""
    twice = []
    for x in files:
        try:
            round_of(x[0])
        except ValueError as e:
            twice.append(str(e))
    if twice or len(files) < 2:
        return twice
    unnamed = [Path(x[0]).name for x in files if round_of(x[0]) is None]
    if unnamed:
        return [f"{n}: the file name states no round (_r<N>_ or round-<N>): pass it under a name with its round "
                "(a copy is fine, e.g. REVIEW_<bead>_<seat>_r1_<date>.md); the round is never guessed" for n in unnamed]
    seats = review_seats(files)
    # Ops/reviews names count rounds across the bead (_r<N>_); /goal's own names restart each attempt
    # (round-<N>). Mixed, an older commit's file can outrank a newer one (LUCY-e3opf C3, own-gate r1 Opus :374).
    styles = {"_r<N>_" if ROUND_IN_NAME.search(Path(x[0]).name) else "round-<N>" for x in seats}
    if len(styles) > 1:
        return ["the review files mix _r<N>_ (rounds across the bead) and round-<N> (rounds within one /goal "
                "attempt) names, so their rounds can't be compared: pass every review under one scheme (copies "
                "renamed to _r<N>_ in bead order)"]
    if not seats:
        return ["no review among the inputs: builder evidence alone can't be graded"]
    # round-<N> restarts at 1 in every /goal attempt's evidence dir: one run = one round namespace (own-gate r2,
    # Astra :398, Opus :391). Two attempts' native files can't be ordered by their names.
    dirs = {str(Path(x[0]).resolve().parent) for x in files if ROUND_NATIVE.search(Path(x[0]).name)}
    if len(dirs) > 1:
        return [f"round-<N> files from {len(dirs)} directories ({', '.join(sorted(dirs))}): each /goal attempt "
                "restarts its rounds at 1, so one run takes one attempt's files; pass the others as _r<N>_ copies in "
                "bead order"]
    top = max(round_of(x[0]) for x in seats)
    shas = {echo_of(x[2]) for x in seats if round_of(x[0]) == top} - {""}
    if any(not same_commit(a, b) for a in shas for b in shas):
        return [f"the newest round {top} echoes different commits ({', '.join(sorted(s[:10] for s in shas))}): the "
                "current tip is ambiguous; give each re-review of a new commit its own round"]
    return []


# A later round's line about an earlier round's finding: "- r1 P2 <file:line>[ and ...] closed: ...".
EARLIER_REF = re.compile(r"^\s*[-*]\s+(?:\*\*)?r(\d+)\s+(P[123])\s+(\S+:\d+.*?)(?:\*\*)?\s+"
                         r"(?:closed|fixed|resolved):\s*(.*)$", re.I)


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
# {review path: {(round, severity, path, line, raw)}}: earlier-round "open" notes a review-verdict file's
# block does not carry; orphan_open_priors requires their raising review as an input (LUCY-e3opf G5).
NOTE_REFS = {}
# Printed after the counts: things the run ignored on purpose, so the lead and orchestrator still see them.
RUN_NOTES = []


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
    ref = EARLIER_REF.match(line)
    if ref and finding_shaped(line):
        # About an earlier round's finding, but not a strict fix confirmation (e.g. "closed:
        # ORCHESTRATOR CLEARED ... the open P2 bead exists"). Never silently dropped: the lead
        # adjudicates it in verified_notes, and it never grades the finding fixed. Refused when the
        # tail carries a location, a labelled finding, another severity or a never-dismiss area
        # (LUCY-l1s6g f).
        tail = ref.group(4)
        if (FILE_LINE.search(tail) or EXPLICIT_LABEL.search(tail) or PROTECTED.search(line)
                or any(t.group(0) != ref.group(2).upper() for t in SEVERITY_TOKEN.finditer(tail))):
            return False
        UNBOUND_NOTES[clean(line)] = line.strip()
        return True
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


def leading_loc(finding):
    """The declared location of a canonical finding line ("P2 lib/x.ts:3 — ..."), or None. A location
    further into the text is evidence, never the finding's own (LUCY-e3opf)."""
    m = LEADING_LOC.match(finding[3:])
    return m.group(1) if m else None


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


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ValueError(f"duplicate key(s) {dup} in a review-verdict block: one would silently erase the other")
    return dict(pairs)


def load_block(block):
    """A review-verdict block's JSON. A repeated key (two "prior" or "findings" arrays) is refused, never
    last-one-wins (LUCY-e3opf)."""
    return json.loads(block, object_pairs_hook=_no_duplicate_keys)


def block_data(p, block):
    """A review-verdict block's JSON object, its top-level keys exact (LUCY-e3opf G2)."""
    try:
        data = load_block(block)
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}: review-verdict block is not valid JSON ({e})")
    if not isinstance(data, dict):
        raise ValueError(f"{p}: review-verdict block must be a JSON object")
    bad = key_error("review-verdict block", data, TOP_KEYS, TOP_OPTIONAL)
    if bad:
        raise ValueError(f"{p}: {bad}")
    if data["verdict"] not in ("PASS", "FAIL"):
        raise ValueError(f"{p}: review-verdict block needs \"verdict\": \"PASS\" or \"FAIL\"")
    if not isinstance(data["findings"], list):
        raise ValueError(f"{p}: review-verdict \"findings\" must be a list")
    return data


def block_findings(p, block):
    """[{sev, canon, loc, strict}] for a review-verdict block's findings (LUCY-e3opf G2). A finding has
    exactly {severity, file_line, text}; a raising review written before file_line existed may give
    exactly {severity, text}, located by the text's leading file:line (after an optional 'r<N> ' carry
    prefix). Any other key set, or a missing, null or empty value, refuses the file, naming the keys."""
    data = block_data(p, block)
    out = []
    for i, f in enumerate(data["findings"]):
        what = f"review-verdict finding #{i + 1}"
        if not isinstance(f, dict):
            raise ValueError(f"{p}: {what} must be an object with keys {','.join(FINDING_KEYS)}")
        strict = set(f) == set(FINDING_KEYS)
        if not strict and set(f) != set(LEGACY_FINDING_KEYS):
            raise ValueError(f"{p}: {key_error(what, f, FINDING_KEYS)}")
        sev, text = f["severity"], f["text"]
        if sev not in ("P1", "P2", "P3"):
            raise ValueError(f"{p}: {what} severity {sev!r} is not P1, P2 or P3")
        if not isinstance(text, str) or not norm(text):
            raise ValueError(f"{p}: {what} text must be a non-empty string")
        text = norm(text)
        # The text may repeat the severity ("P2 lib/x.ts:3 ..." with severity P2): one prefix, not
        # "P2 P2 ..." (LUCY-l1s6g a). A different leading severity contradicts the field: refused.
        text = re.sub(rf"^(?:\*\*)?{sev}(?:\*\*)?[\s:]+", "", text)
        if re.match(r"^(?:\*\*)?P[123]\b", text):
            raise ValueError(f"{p}: {what} text starts with a severity other than {sev}")
        lead_loc = LEADING_LOC.match(text)
        if strict:
            fl = f["file_line"]
            if not isinstance(fl, str) or not LOC.fullmatch(fl.strip()):
                raise ValueError(f"{p}: {what} file_line {fl!r} is not <file>:<line> or <file>:<line>-<line>")
            fl = fl.strip()
            if lead_loc and lead_loc.group(1) != fl:
                raise ValueError(f"{p}: {what} file_line {fl} contradicts its text ({lead_loc.group(1)})")
            if not lead_loc:
                text = f"{fl} — {text}"
            target = path_parts(fl)
        else:
            # Prose cross-check only (G5): its first repo path ("r1 lib/x.ts:172 + ...", "ACCEPTED (...):
            # lib/x.ts:221 — ..."). Grading still reads only a leading file:line (leading_loc).
            pm = next(iter(repo_paths(text)), None)
            target = (pm.group(1), pm.group(2)) if pm else None
        out.append({"sev": sev, "canon": clean(f"{sev} {text}"), "target": target, "strict": strict})
    if data["verdict"] == "FAIL" and not out:
        raise ValueError(f"{p}: review-verdict is FAIL but lists no findings")
    structured_prior(p, data)
    return out


def structured_findings(p, block):
    """Canonical finding lines ("P2 lib/x.ts:12 — ...") of a review-verdict block."""
    return [f["canon"] for f in block_findings(p, block)]


# A finding's leading location in block text, past optional `code` / **bold** markup.
LEADING_LOC = re.compile(r"^[`*]*(" + LOC.pattern + r")")
PRIOR_REF = re.compile(r"^r(\d+)\s+(P[123])\s+(" + LOC.pattern + r")$")


def structured_prior(p, data):
    """{(round, severity, file:line): "closed"|"open"} from a block's `prior` array (LUCY-e3opf).
    Absent means the review claims no closures. Anything malformed refuses the file, never skipped."""
    prior = data.get("prior", [])
    if not isinstance(prior, list):
        raise ValueError(f"{p}: review-verdict \"prior\" must be a list")
    out = {}
    for i, e in enumerate(prior):
        what = f"review-verdict prior #{i + 1}"
        if not isinstance(e, dict):
            raise ValueError(f"{p}: {what} must be an object with keys {','.join(PRIOR_KEYS)}")
        bad = key_error(what, e, PRIOR_KEYS)
        if bad:
            raise ValueError(f"{p}: {bad}")
        ref = norm(e["ref"]) if isinstance(e["ref"], str) else ""
        m = PRIOR_REF.match(ref)
        if not m:
            raise ValueError(f"{p}: {what} ref {ref!r} is not 'r<N> P<1-3> <file>:<line>'")
        status, ev = e["status"], e["evidence"]
        if status not in ("closed", "open"):
            raise ValueError(f"{p}: {what} status {status!r} is not \"closed\" or \"open\"")
        if not isinstance(ev, str) or not norm(ev) or (status == "closed" and len(norm(ev)) < 8):
            raise ValueError(f"{p}: {what} needs evidence (how it was checked)")
        key = (int(m.group(1)), m.group(2), m.group(3))
        if out.get(key, status) != status:
            raise ValueError(f"{p}: review-verdict prior marks {ref} both closed and open")
        out[key] = status
    return out


def file_prior(p, text):
    """The prior entries of the file's one top-level review-verdict block; {} for a file without one
    (LUCY-e3opf G1: no merge across blocks, a second block refuses the file)."""
    b = one_block(p, text)
    return structured_prior(p, block_data(p, b.body)) if b else {}


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
        block = one_block(p, text)
        if block:
            # The block is the final answer. A verdict or finding after it means the reviewer
            # changed its mind (or the block is a stale template): refuse, never pick one.
            trailing = lines_of(text[block.end:])
            for line in trailing:
                if VERDICT_HEAD.match(line) or VERDICT_DECL.search(line) or finding_shaped(line):
                    raise ValueError(f"{p}: verdict content after the last review-verdict block: "
                                     f"{line.strip()[:100]}")
            meta = block_findings(p, block.body)
            mine = [f["canon"] for f in meta]
            check_summary_counts(p, trailing, mine)
            # The final response before the block (from its last REVIEWING echo, else the file
            # start) must agree with it: same verdict word and summary counts that match the block.
            head = text[:block.start]
            echo = list(re.finditer(r"(?m)^\s*REVIEWING:.*$", head))
            final = head[echo[-1].end():] if echo else head
            verdict = load_block(block.body)["verdict"]
            words = decl_words(lines_of(final))
            if words - {verdict}:
                raise ValueError(f"{p}: prose verdict {sorted(words)} contradicts the review-verdict block "
                                 f"({verdict})")
            check_summary_counts(p, lines_of(final), mine)
            sweep_whole_response(p, text, mine, verdict, block, meta)
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
        words = decl_words(lines_of(text[final_from:]))
        if len(words) > 1:
            raise ValueError(f"{p}: conflicting verdict lines in the final response ({sorted(words)})")
        # With an echo, parse from the FIRST verdict line of the final response, so a later quoted
        # "Verdict: ..." can't restart parsing past a finding. Without one (older codex logs repeat
        # the final message), keep the last verdict block, up to its trailer.
        final_heads = [h for h in heads if h.start() >= final_from]
        last = final_heads[0] if echo and final_heads else heads[-1]
        body = text[last.end():]
        stop = re.search(r"(?m)^\s*(?:#+\s*)?(?:\*\*)?(HAZARDS|VERIFIED OK|UNVERIFIED)\b", body)
        lines = lines_of(body[:stop.start()] if stop else body)
        mine = []
        for line in lines:
            if FINDING.match(line):
                mine.append(clean(line))
            elif finding_shaped(line):
                raise ValueError(f"{p}: unrecognised finding format: {line.strip()[:100]}")
        # Past the trailer, evidence prose may cite hazard ids (H-P1) and file:line freely, but a
        # finding-shaped line there is one the parser would drop: refuse. Counts must agree too.
        after = lines_of(body[stop.start():]) if stop else []
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


def sweep_whole_response(p, text, mine, verdict, block=None, meta=None):
    """Nothing the parsed segment left out may sit elsewhere in the file (LUCY-p4pjf, Astra r15).

    The whole file, before and after the echo (LUCY-e3opf B2): no verdict line disagrees with
    `verdict` and no tally disagrees with the findings. Without a block, every canonical finding line
    is one the parser kept (verbatim) and no other finding-shaped line appears. With one, the block is
    the single source of findings and the prose is checked by location only (prose_claim_errors)."""
    region = text[:block.start] + text[block.end:] if block else text
    plines = lines_of(region)
    if block:
        prose_claim_errors(p, plines, meta, file_prior(p, text))
        kept = plines
    else:
        ver = verified_lines(plines)
        kept, used = [], set()
        for i, line in enumerate(plines):
            if i in ver and tolerated_note(line, mine):
                continue
            kept.append(line)
            if FINDING.match(line):
                j = next((k for k, m in enumerate(mine) if k not in used and m == clean(line)), None)
                used.add(j)
                if j is None:
                    raise ValueError(f"{p}: finding outside the parsed response: {line.strip()[:100]}")
            elif finding_shaped(line):
                raise ValueError(f"{p}: finding-shaped line outside the parsed response: {line.strip()[:100]}")
    # Every declaration and tally anywhere in the response (not only the parsed segment) must agree.
    words = decl_words(kept)
    check_summary_counts(p, [ln for ln in kept if not FINDING.match(ln)], mine)
    if words - {verdict}:
        raise ValueError(f"{p}: verdicts {sorted(words)} in the file disagree with the final {verdict}")


# A repo path in prose (LUCY-e3opf G5 AMENDED, B2), optional :line or :line-line, after a space or "(":
#  - any token with a slash: "lib/x.ts", "services/worker", "app/(dashboard)/page.tsx:12", "~/x/lib/pay.ts",
#    "/Users/s/repo/lib/x.ts:4". So "and/or" is a path token too; weak_path then reads it as a slash-word
#    that locates nothing, and its severity is counted as unlocated (LUCY-16n7i). A token whose
#    every segment is a severity, a number or a hazard id ("P1/P2/P3", "9/10", "H-U1/H-U3") is a tally,
#    never a file (TALLY);
#  - a name with a 2-10 character extension starting with a letter: "tokens.ts", "schema.prisma",
#    "Next.js" (a false refusal). One-letter extensions ("e.g.", "main.c") only with a line;
#  - a dotfile or Capitalised name with a line ("Dockerfile:12", ".gcloudignore:5").
# Never "H-P0/P1" (no standalone severity), "localhost:3000". Group 1 is the path, group 2 the line.
_SEG = r"(?:\([\w.-]+\)|[\w.\[\]@+-]+)"
_LN = r":\d+(?:-\d+)?"
_NAME = r"[\w\[\]@-](?:[\w.\[\]@-]*[\w\[\]@-])?"  # never ends in "." (sentence punctuation)
PATH_TOKEN = re.compile(
    r"(?<![\w./\[\]@+-])("
    rf"(?:~?/)?(?:{_SEG}/)+(?:{_NAME})?"
    rf"|~?/{_NAME}"
    rf"|{_NAME}\.[A-Za-z][A-Za-z0-9]{{0,9}}(?={_LN})"
    rf"|{_NAME}\.[A-Za-z][A-Za-z0-9]{{1,9}}"
    rf"|(?:\.[\w-][\w.-]*|[A-Z][\w.-]*)(?={_LN})"
    r")(?::(\d+(?:-\d+)?))?(?![\w/-])")
TALLY = re.compile(r"(?:P[123]|\d+|[A-Z]{1,4}(?:-[A-Z]{1,2})?-?\d{1,3})(?:/(?:P[123]|\d+|[A-Z]{1,4}(?:-[A-Z]{1,2})?-?\d{1,3}))+")


def repo_paths(line):
    """PATH_TOKEN matches in a line, less the tallies."""
    return [m for m in PATH_TOKEN.finditer(line) if not TALLY.fullmatch(m.group(1))]


# Top-level dirs a slash-word may start with and still be a repo path (LUCY-16n7i). The checker never sees
# the reviewed repo (its inputs live in Ops/reviews), so this is a fixed list: the AestheticcNext and ~/.claude
# top-level dirs plus common app roots. Matched case-insensitively. A word listed here stays a strong path,
# so a bigger list only fails closed.
KNOWN_TOP_DIRS = frozenset("""
    __tests__ aestheticc-zapier agents app archived attached_assets bin cache clinic-voice-service commands
    components config content contexts csai docs docs-dev drizzle e2e home-images hooks invariants launchd
    learning lib mcp-tiers migrations mobile mobile-portal ops pages prisma product prompts public references
    scheduled-tasks scripts services skills skills-archived src strategy styles supabase templates tests
    types utils voice-service
""".split())
_WORD = re.compile(r"[A-Za-z][A-Za-z-]*")


def weak_path(m):
    """True when a repo_paths match is a slash-word that may be prose ("user/owned/member", "and/or"), not a
    file (LUCY-16n7i): no :line, two or more segments, every segment a plain word (letters and hyphens only:
    no dot, digit, underscore, (group), [param], @, ~, or empty segment from a leading or trailing /), and a
    first segment that is not a known top-level dir. Anything else is a strong path. A weak path never locates
    a claim; its severity is counted as unlocated."""
    segs = m.group(1).split("/")
    return (not m.group(2) and len(segs) > 1 and all(_WORD.fullmatch(s) for s in segs)
            and segs[0].lower() not in KNOWN_TOP_DIRS)


# A severity standing alone: not the P1 in H-P1 or H-P0/P1, nor a count ("0 P1").
CLAIM_SEV = re.compile(r"(?<![\w/-])(?<!\d\s)(?<!\dx\s)P[123]\b")
# A note on an earlier round's finding: "- r1 P3 lib/x.ts:172 + :206-241 open: ...".
EARLIER_NOTE = re.compile(r"^\s*(?:[-*•]\s+)?(?:\*\*)?r(\d+)\s+(P[123])\s+(.+?)(?:\*\*)?\s+([A-Za-z]+):(.*)$")


def path_parts(loc):
    """('lib/x.ts', '12-20') from 'lib/x.ts:12-20'."""
    path, _, line = loc.rpartition(":")
    return (path, line) if path and re.fullmatch(r"\d+(?:-\d+)?", line) else (loc, None)


def claim_sevs(line):
    """Standalone severity tokens, code spans read like plain text (LUCY-e3opf B2): a quoted example
    `P2 lib/x.ts:12` is a claim, so a real finding can't hide in backticks."""
    return list(CLAIM_SEV.finditer(line))


def line_claims(line, skip=None):
    """([(severity, path, line or None)], [severity]) for a prose line: each standalone severity with the
    first path after it (before the next severity), else the nearest path before it. No path, no claim.
    Weak slash-words (weak_path) locate nothing (LUCY-16n7i): a severity whose own clause holds a strong
    path claims the first one; one whose clause holds only weak ones, or that falls back to a weak one, comes
    back in the second list, to be counted as unlocated, never ignored. It never borrows another clause's
    strong path past its own slash-word. The severity token starting at `skip` (an earlier-round note's own
    reference) is not a claim."""
    sevs = claim_sevs(line)
    paths = repo_paths(line)
    if not sevs or not paths:
        return [], []
    out, weak = [], []
    for k, s in enumerate(sevs):
        if s.start() == skip:
            continue
        stop = sevs[k + 1].start() if k + 1 < len(sevs) else len(line)
        after = [m for m in paths if s.end() <= m.start() < stop]
        before = [m for m in paths if m.end() <= s.start()]
        strong = [m for m in after if not weak_path(m)]
        m = strong[0] if strong else after[0] if after else (before[-1] if before else paths[0])
        if weak_path(m):
            weak.append(s.group(0))
        else:
            out.append((s.group(0), m.group(1), m.group(2)))
    return out, weak


def same_file(a, b):
    """One path names the other's file: equal, or a '/'-anchored suffix either way (tokens.ts and
    lib/auth/tokens.ts; /Users/s/repo/lib/x.ts and lib/x.ts). kens.ts is not tokens.ts."""
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def claim_error(sev, path, line, targets):
    """Why a prose claim matches no block finding or prior ref, else None (LUCY-e3opf G5). Matched on
    severity + file (equal, or a '/'-anchored suffix: tokens.ts matches lib/auth/tokens.ts, kens.ts
    does not) + line when the claim gives one."""
    cands = [t for t in targets if same_file(t[1], path) and (line is None or t[2] == line)]
    where = f"{path}:{line}" if line else path
    if not cands:
        return f"no review-verdict finding or prior ref at {where}"
    if len({t[1] for t in cands}) > 1 and len({t[0] for t in cands}) > 1:
        return f"{where} is ambiguous: it matches {', '.join(sorted({f'{t[0]} {t[1]}' for t in cands}))}"
    if sev not in {t[0] for t in cands}:
        return f"severity mismatch: the block has {'/'.join(sorted({t[0] for t in cands}))} at {where}, not {sev}"
    if all(t[3:] == ("closed",) for t in cands if t[0] == sev):
        return (f"this review's prior closes {sev} {where}, so the prose contradicts it: raise it under "
                "findings, or mark it open")
    return None


def prose_claim_errors(p, lines, meta, prior):
    """The block is the single source of findings (LUCY-e3opf G5). A prose line is a finding claim when
    it carries a standalone severity AND a repo path; each claim must match a block finding or a prior
    ref, or the file is refused, so a P1 written only in prose is never dropped. Wording is never
    compared, except that a line repeating a block finding verbatim is that finding. A severity with no
    path (carry or summary text) is ignored. An earlier-round note "- rN Px <loc> closed|fixed|resolved:
    ..." claims nothing by its own reference (prose never grades, the prior array does). Any other
    earlier-round note ("open:") names round N's finding: it must match a block finding or prior ref
    here, or else the review of round N that raised it must be an input to the run (NOTE_REFS, checked
    by orphan_open_priors), so the lead covers it. A severity in the note's own text with a path of its
    own is a claim like any other; one without ("Earlier P3 grading retained.", "same as the P2") names
    a finding at the note's location and is checked the same way as an open note."""
    targets = [(f["sev"],) + f["target"] + ("finding",) for f in meta if f["target"]]
    targets += [(sev,) + path_parts(loc) + (st,) for (_r, sev, loc), st in prior.items()]
    carried = [tg[:3] + ("finding",) for tg in targets]  # a note's own ref: carried in any status
    verbatim = {f["canon"] for f in meta}
    NOTE_REFS[str(p)] = set()
    unlocated = {"P1": [], "P2": [], "P3": []}
    for line in lines:
        if clean(line) in verbatim:
            continue
        claims = []
        note = EARLIER_NOTE.match(line)
        if note:
            ref = next(iter(repo_paths(note.group(3))), None)
            tail = note.group(5)
            claims, weak = line_claims(tail)
            # The note's status word is never read: prose neither grades nor disputes (LUCY-e3opf B1, the
            # block is authoritative). Its reference, and any path-free severity in its text (a slash-word
            # locates nothing, so it counts as path-free), names round N's finding, so the block carries it
            # or the raising review is an input (orphan_open_priors). With no reference, a slash-word
            # severity is unlocated (LUCY-16n7i).
            refs = [note.group(2)] if ref else []
            if ref and not claims:
                refs += [s.group(0) for s in claim_sevs(tail)]
            else:
                for sev in weak:
                    unlocated[sev].append(line.strip()[:100])
            for sev in refs:
                if claim_error(sev, ref.group(1), ref.group(2), carried):
                    NOTE_REFS[str(p)].add((int(note.group(1)), sev, ref.group(1), ref.group(2), line.strip()[:100]))
        else:
            claims, weak = line_claims(line)
            start = FINDING_START.match(line) or EXPLICIT_LABEL.match(line.lstrip(" -*•#>"))
            # A severity whose only path is a weak slash-word ("P1 user/owned/member leak") is unlocated:
            # counted against the block below, never ignored, so a prose P1 can't hide behind prose
            # slashes (LUCY-16n7i). The line's starting severity is counted once.
            for sev in weak:
                unlocated[sev].append(line.strip()[:100])
            if start and not claims:
                first = re.search(r"P[123]", start.group(0)).group(0)
                if first not in weak:
                    unlocated[first].append(line.strip()[:100])
        for sev, path, ln in claims:
            why = claim_error(sev, path, ln, targets)
            if why:
                raise ValueError(f"{p}: prose finding not in the review-verdict block ({why}): {line.strip()[:100]}")
    # A line that starts like a finding but names no file (its location wrapped to the next line, or
    # missing): the block must hold at least as many findings and prior refs of that severity.
    for sev, found in unlocated.items():
        have = sum(f["sev"] == sev for f in meta) + sum(k[1] == sev for k in prior)
        if len(found) > have:
            raise ValueError(f"{p}: {len(found)} prose line(s) start with {sev} but name no file, and the "
                             f"review-verdict block has {have} {sev} finding(s) or prior ref(s): put every "
                             f"finding in the block with its file_line: {found[0]}")


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
    for line in lines_of(text or ""):
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
            first = lines_of(body)[0].strip() if body else ""
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
    f["_carve_out"] = is_carve_out(f, protected) or f.get("_duplicate_path") is True or bool(f.get("_disputed"))
    f["_demoted"] = (f.get("verdict") in ("act_on", "consider") and not f["_carve_out"]
                     and not has_ordinary_use(f))
    return f["_demoted"]


def routed_out(f, scope_lock):
    """An act_on item that leaves this branch: out of scope under the lock, or pre-existing."""
    return f.get("verdict") == "act_on" and not f.get("_demoted") and (
        f.get("pre_existing") is True or (scope_lock and f.get("in_scope") is False))


ECHO_SHA = re.compile(r"[ \t]*REVIEWING:[ \t]*\S+[ \t]*@[ \t]*([0-9a-fA-F]{7,40})\.?[ \t]*")


def echo_of(text):
    """The commit a review echoes; "" unless the file has exactly ONE REVIEWING line, of the form
    "<branch> @ <bare sha>" (LUCY-e3opf G4, A2). Two lines, even identical ones, are two responses."""
    lines = [ln for ln in lines_of(text) if re.match(r"[ \t]*REVIEWING:", ln)]
    m = ECHO_SHA.fullmatch(lines[0]) if len(lines) == 1 else None  # one physical line
    return m.group(1).lower() if m else ""


def current_tip(files):
    """(sha, file) of the newest review, by the round in its name only (LUCY-e3opf C3: never the mtime;
    check_rounds refuses a run whose newest round echoes two commits)."""
    newest = max(review_seats(files) or files, key=lambda x: round_of(x[0]) or 0)
    return echo_of(newest[2]), newest


def tip_statuses(files, tip, rounds, sev, loc):
    """{review path: {round: status}} from the prior entries naming sev + loc in one of `rounds`,
    for every review of the current tip that has one."""
    out = {}
    for x in files:
        if not same_commit(echo_of(x[2]), tip):
            continue
        prior = file_prior(x[0], x[2])
        got = {r: prior[(r, sev, loc)] for r in rounds if (r, sev, loc) in prior}
        if got:
            out[x[0]] = got
    return out


def dispute(src, files):
    """Why reviews of the current tip disagree on an earlier finding (one closes a round that raised
    it, one keeps a round open), else None. Checked whatever the lead graded it (LUCY-e3opf)."""
    loc = leading_loc(src)
    tip = current_tip(files)[0] if files else ""
    if not loc or not tip:
        return None
    rounds = sorted({round_of(x[0]) for x in files if src in x[1]})
    st = tip_statuses(files, tip, rounds, src[:2], loc)
    closers = sorted(Path(k).name for k, v in st.items() if "closed" in v.values())
    openers = sorted(Path(k).name for k, v in st.items() if "open" in v.values())
    if closers and openers:
        return (f"seats disagree at {tip[:10]} on {src[:2]} {loc}: {', '.join(closers)} close(s) it, "
                f"{', '.join(openers)} keep(s) it open")
    return None


def orphan_open_priors(files, items=()):
    """Every prior entry names a finding the lead must have in front of it: the review that raised it
    must be an input, so the lead covers it (open) or grades it from this entry (closed). Checked on
    every input review.

    Echoes (LUCY-e3opf G4, A3): EVERY input echoes exactly one commit, whatever the grades, because a
    seat whose commit is unknown would silently drop out of the current tip. Strict shape (G2, A4):
    every input that reviewed the current tip and has a block lists only {severity, file_line, text}
    findings, whatever the lead graded them."""
    errs = []
    priors = {x[0]: file_prior(x[0], x[2]) for x in files}
    tip = current_tip(files)[0] if files else ""
    for x in files:
        if is_builder_evidence(x[0]):
            pass  # echo-free by D1 (builder_evidence_errors): never a seat, never fixed_by, never the tip
        elif not echo_of(x[2]):
            errs.append(f"{Path(x[0]).name}: no single well-formed REVIEWING echo ('REVIEWING: <branch> @ <sha>' "
                        "naming one commit): every review must echo the commit it reviewed")
        elif same_commit(echo_of(x[2]), tip):
            b = one_block(x[0], x[2])
            if b and any(not f["strict"] for f in block_findings(x[0], b.body)):
                errs.append(f"{Path(x[0]).name}: reviewed the current tip {tip[:10]} but has a finding without "
                            "file_line (the legacy {severity, text} shape): re-run that seat with the strict "
                            "review-verdict shape")
        for (r, sev, loc), st in priors[x[0]].items():
            if not any(round_of(y[0]) == r and any(o[:2] == sev and leading_loc(o) == loc
                                                                    for o in y[1]) for y in files):
                errs.append(f"{Path(x[0]).name}: prior ref r{r} {sev} {loc} ({st}) matches no round-{r} finding "
                            f"in the --verdict reviews: pass the review that raised it so the lead covers it")
        for r, sev, path, line, raw in sorted(NOTE_REFS.get(str(x[0]), ())):
            raised = [t for y in files if round_of(y[0]) == r for t in finding_targets(y)]
            if claim_error(sev, path, line, raised):
                why = (f"{Path(x[0]).name}: note on r{r} {sev} {path}{':' + line if line else ''} matches no "
                       f"round-{r} finding in the --verdict reviews, and its review-verdict block does not carry it")
                # An earlier review's prose note grades nothing and its code is gone (LUCY-e3opf C6): printed, not
                # refused. A review of the current tip is what the lead grades from, so there it still refuses.
                if same_commit(echo_of(x[2]), tip):
                    errs.append(f"{why}: pass the review that raised it, or list it in the block: {raw}")
                else:
                    RUN_NOTES.append(f"{why} (earlier review, not the current tip: ignored): {raw}")
    return errs


def finding_targets(x):
    """[(severity, path, line)] where a review's findings sit: its block's, else its prose lines' leading
    file:line."""
    b = one_block(x[0], x[2])
    if b:
        return [(f["sev"],) + f["target"] for f in block_findings(x[0], b.body) if f["target"]]
    return [(o[:2],) + path_parts(leading_loc(o)) for o in x[1] if leading_loc(o)]


def ref_ambiguity(files, r, sev, loc):
    """The distinct round-r findings a ref 'r<r> <sev> <loc>' could name (LUCY-e3opf G3). The same text
    in two seats is one finding; two texts are two, and one ref can never close both."""
    return sorted({o for y in files if round_of(y[0]) == r for o in y[1] if o[:2] == sev and leading_loc(o) == loc})


def ambiguous_refs(files):
    """{finding line: why} for every round-N finding that a prior ref in any input could name alongside
    another distinct one (LUCY-e3opf G3, A5). Computed before grading: whatever the lead graded such a
    finding (fixed, dismissed, noted, open), it escalates."""
    out = {}
    for x in files:
        for (r, sev, loc) in file_prior(x[0], x[2]):
            same = ref_ambiguity(files, r, sev, loc)
            if len(same) > 1:
                why = (f"prior ref r{r} {sev} {loc} in {Path(x[0]).name} is ambiguous: it matches {len(same)} "
                       f"distinct round-{r} findings ({' | '.join(s[:80] for s in same)}); one entry cannot "
                       "speak for both")
                for s in same:
                    out.setdefault(s, why)
    return out


def every_seat_closes(src, files):
    """True when a `fixed` grade may skip the G3/A5 ambiguity escalation (LUCY-unykv, Orchestrator23 25 Oct):
    every review of the newest round at the current tip has a closed prior entry (structured_prior already
    refuses one without evidence) at src's severity + location for every round that raised it. One entry then
    speaks for every finding at that location, because every seat closed the location. A missing, open or
    mixed one keeps the escalation. fixed_errors still runs first: any review of the tip (an earlier round's
    included) marking it open is a dispute (escalated), and one raising it again refuses the run. The roster
    and statuses are fixed_errors' (B1, C5)."""
    loc, sev = leading_loc(src), src[:2]
    tip, newest = current_tip(files) if files else ("", None)
    rounds = sorted({round_of(x[0]) for x in files if src in x[1]})
    if not loc or not tip or not rounds or None in rounds:
        return False
    at_tip = [x for x in files if same_commit(echo_of(x[2]), tip)]
    roster = [x for x in at_tip if round_of(x[0]) == round_of(newest[0]) and not is_builder_evidence(x[0])]
    st = tip_statuses(files, tip, rounds, sev, loc)
    return bool(roster) and all(st.get(x[0], {}).get(r) == "closed" for x in roster for r in rounds)


def same_commit(a, b):
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def scheme_of(name):
    return "_r<N>_" if ROUND_IN_NAME.search(Path(str(name)).name) else "round-<N>"


def builder_at_tip(x, files):
    """Builder evidence is echo-free (D1), so it is at the current tip by its round: the newest seat round or
    later, or an earlier round whose seats reviewed the tip (or that has none). Under the other naming scheme its round can't be compared, so it counts as at the tip (fails closed:
    its survivors veto, and it can't be an earlier raiser that a later review closes)."""
    newest = current_tip(files)[1]
    if is_builder_evidence(newest[0]):
        return True
    if scheme_of(x[0]) != scheme_of(newest[0]):
        return True
    if (round_of(x[0]) or 0) >= (round_of(newest[0]) or 0):
        return True
    # An earlier round: its commit is its round's. A seat of that round on the current tip (a no-new-commit
    # re-review) puts it at the tip too; a round with no seat has no known commit (own-gate r2 Opus :1172).
    mine = [y for y in review_seats(files) if round_of(y[0]) == round_of(x[0])]
    return not mine or any(same_commit(echo_of(y[2]), echo_of(newest[2])) for y in mine)


def fixed_errors(tag, f, src, files):
    """A finding graded `fixed` resolves only when a later independent review of the CURRENT tip
    confirms it (LUCY-p4pjf, LUCY-e3opf): `fixed_by` is one of the --verdict files, newer than the
    file that raised the finding and echoing a different commit, and it reviewed the commit the
    newest review reviewed. No review at that tip raises the same severity at the same file:line
    again, and fixed_by's review-verdict block has a prior entry {"ref": "r<N> <sev> <file:line>",
    "status": "closed"}, N being a round that raised it (_r<N>_ in the name). Prose is never graded.
    Another review at the tip marking it "open" makes it disputed: set on f["_disputed"], escalated."""
    if not files:
        return [f"{tag}: fixed needs the reviewer files (run through main)"]
    by = (f.get("fixed_by") or "").strip()
    if not by:
        return [f"{tag}: fixed needs fixed_by: the later review file that confirms it"]
    later = next((x for x in files if str(Path(x[0]).resolve()) == str(Path(by).expanduser().resolve())), None)
    if later is None:
        return [f"{tag}: fixed_by {by} is not one of the --verdict files"]
    if is_builder_evidence(later[0]):
        return [f"{tag}: fixed_by {Path(later[0]).name} is builder evidence (mutants file or QA log), not an "
                "independent review: it can never confirm a finding fixed"]
    source = next((x for x in files if src in x[1]), None)
    if source is None or source is later:
        return [f"{tag}: fixed_by must be a different review from the one that raised the finding"]
    b_ = echo_of(later[2])
    # Every input that raises it (not just the first) must be an older review of another commit.
    if not b_:
        return [f"{tag}: fixed_by {Path(later[0]).name} has no single REVIEWING echo"]
    for x in files:
        if src not in x[1]:
            continue
        if is_builder_evidence(x[0]):  # echo-free (D1): ordered by its round, never by an echo
            if builder_at_tip(x, files):
                return [f"{tag}: {Path(x[0]).name} (builder evidence) raises it at the current tip: not fixed"]
        else:
            a_ = echo_of(x[2])
            if x is later or not a_ or a_.startswith(b_) or b_.startswith(a_):
                return [f"{tag}: {Path(x[0]).name} raises it at the commit fixed_by reviewed (REVIEWING echoes): not fixed"]
        # Order by the round each name states, never the mtime: a copy or checkout resets it (LUCY-l1s6g g,
        # LUCY-e3opf C3). Only a later round confirms an earlier one.
        r_later, r_x = round_of(later[0]), round_of(x[0])
        if r_later is None or r_x is None or r_later <= r_x:
            return [f"{tag}: fixed_by {Path(later[0]).name} is not newer than {Path(x[0]).name} "
                    f"(round {r_later} vs {r_x})"]
    loc, sev = leading_loc(src), src[:2]
    if not loc:
        return [f"{tag}: fixed needs the finding to start with its file:line"]
    # The current tip is what the newest review (by round) reviewed. A confirmation of an
    # older commit says nothing about the code that would land.
    tip, newest = current_tip(files)
    if not tip:
        return [f"{tag}: the newest review {Path(newest[0]).name} has no single REVIEWING echo: the current "
                "tip is unknown"]
    if not same_commit(b_, tip):
        return [f"{tag}: fixed_by {Path(later[0]).name} reviewed {b_[:10]}, not the current tip {tip[:10]} "
                f"(newest review {Path(newest[0]).name})"]
    at_tip = [x for x in files if same_commit(echo_of(x[2]), tip)]
    for x in at_tip + [y for y in files if is_builder_evidence(y[0]) and builder_at_tip(y, files)]:
        for other in x[1]:
            if other[:2] == sev and loc in {leading_loc(other)} | {o.group(0) for o in LOC.finditer(other)}:
                return [f"{tag}: {Path(x[0]).name} raises {sev} at {loc} again at the current tip: not fixed"]
    # Raised in several rounds: fixed_by must close every one of them.
    rounds = sorted({round_of(x[0]) for x in files if src in x[1]})
    st = tip_statuses(files, tip, rounds, sev, loc)
    mine = st.get(later[0], {})
    missing = [r for r in rounds if mine.get(r) != "closed"]
    if missing:
        refs = " and ".join(f"{{\"ref\": \"r{r} {sev} {loc}\", \"status\": \"closed\"}}" for r in missing)
        return [f"{tag}: fixed_by {Path(later[0]).name} has no review-verdict prior entry {refs}"
                + (" (it says open)" if "open" in mine.values() else "")
                + ": not fixed. Prose 'closed:' lines are not graded"]
    f["_disputed"] = dispute(src, files)
    # Every seat (LUCY-e3opf B1): each review of the tip must itself close every raising round. One that
    # says nothing about it leaves it not fixed (blocking), without refusing the run; one that keeps it
    # open is a dispute (above). A seat's block is authoritative over its own prose.
    # The seats are the NEWEST round's reviews (C5), a block-less one included (it closes nothing). An earlier
    # round at the same tip vetoes only by an explicit open entry (dispute, above) or by raising the finding
    # again (the at_tip check above): its silence doesn't, because it reviewed before the finding was re-checked.
    # Builder evidence (C2) is never a seat.
    top = round_of(newest[0])
    silent = [Path(x[0]).name for x in at_tip if round_of(x[0]) == top and not is_builder_evidence(x[0])
              and any(st.get(x[0], {}).get(r) != "closed" for r in rounds)]
    # Backstop: the newest seat is always in its own roster, so this never fires unless the tip rules change.
    roster = [x for x in at_tip if round_of(x[0]) == top and not is_builder_evidence(x[0])]
    if not roster and not f["_disputed"]:
        silent = ["(none: no review of the newest round at the tip)"]
    if silent and not f["_disputed"]:
        refs = " and ".join(f"r{r} {sev} {loc}" for r in rounds)
        f["_not_fixed"] = (f"seat {', '.join(silent)} has no closed prior for {refs}: every review of the current "
                           f"tip {tip[:10]} must close it before it counts fixed")
    return []


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
    amb = ambiguous_refs(files) if files else {}
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
            if src in amb and not every_seat_closes(src, files):
                f["_disputed"] = amb[src]
                f.pop("_not_fixed", None)
            continue
        if files:
            f["_disputed"] = amb.get(src) or dispute(src, files)
        protected = cat in PROTECTED_CATEGORIES or (bool(PROTECTED.search(src)) and not ratchet_only(src))
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
    errs += orphan_open_priors(files or [], items)
    # A `fixed` grade that some review of the tip does not close stays blocking (LUCY-e3opf B1).
    blocking = [f for f in items if f.get("_not_fixed") or (
        f.get("verdict") == "act_on" and not f.get("_demoted")
        and not routed_out(f, scope_lock) and (not scope_lock or f.get("in_scope") is True))]
    if len(blocking) > ACT_ON_CAP and not norm(lead.get("act_on_over_cap_reason") or ""):
        errs.append(f"{len(blocking)} blocking act_on items (> {ACT_ON_CAP}): not filtering; filter harder "
                    "or give act_on_over_cap_reason")
    if round_no >= 2 and not isinstance(lead.get("premise"), dict):
        errs.append("round >= 2: premise block required (attack the premise)")
    return errs, blocking


def action_entry(f):
    if f.get("_not_fixed"):
        return f"{f['source_line']}\n  graded fixed, but NOT FIXED: {f['_not_fixed']}\n"
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
    NOTE_REFS.clear()
    RUN_NOTES.clear()
    try:
        lead = json.loads(Path(a.lead).read_text())
        reviewer_lines = reviewer_findings(a.verdict)
        files = [(v, reviewer_findings([v]), Path(v).read_text(errors="ignore")) for v in a.verdict]
        pre = check_rounds(files) + builder_evidence_errors(files)
    except Exception as e:
        print(f"review-lead: REFUSED: cannot read inputs ({e})", file=sys.stderr)
        return 1
    if pre:
        print("review-lead: REFUSED, fix the inputs:", file=sys.stderr)
        for e in pre:
            print(f"  - {e}", file=sys.stderr)
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
    # An earlier finding one seat closes and another keeps open at the current tip is the orchestrator's
    # call, whatever the lead graded it (LUCY-e3opf).
    def must_escalate(f):
        return (f.get("_protected") or f.get("_disputed")
                or (f.get("_duplicate_path") and f.get("pre_existing") is not True))
    # Whatever the grade (LUCY-e3opf C4): an in-scope act_on that is disputed stays blocking AND escalates.
    disputed = [f for f in items if f.get("_disputed") and f not in consider and f not in out_of_scope]
    escalate = [f for f in consider if must_escalate(f)] + \
               [f for f in out_of_scope if must_escalate(f)] + disputed
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
        "".join(f"{f['source_line']}\n  why not fixed here: "
                f"{f.get('_disputed') or f.get('reason') or f.get('follow_up')}\n" for f in escalate))
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
        if f in blocking:
            continue
        if f.get("_demoted"):
            rows.append(f"- **p4 note** (was {f['verdict']}; no ordinary-use path, not carve-out; no fix, "
                        f"no re-review): {f['source_line']}\n  - reason: {f.get('reason') or 'ordinary-use test'}")
            continue
        if f["verdict"] == "fixed":
            label = "fixed, DISPUTED (escalated)" if f.get("_disputed") else "fixed"
            rows.append(f"- **{label}**: {f['source_line']}\n  - confirmed by: {f.get('fixed_by')}"
                        + (f"\n  - {f['_disputed']}" if f.get("_disputed") else ""))
            continue
        if f["verdict"] == "act_on":
            label = "act_on (pre-existing)" if f.get("pre_existing") is True else "act_on (out of scope)"
        else:
            label = f["verdict"]
        why = f.get("reason") or f"{'pre-existing' if f.get('pre_existing') is True else 'scope lock'}: " \
                                 f"{f.get('follow_up')}"
        pat = f" [pattern: {f['pattern']}]" if f.get("pattern") else ""
        if f.get("_disputed"):
            why += f" | DISPUTED (escalated): {f['_disputed']}"
        if f.get("id") in cleared:
            why += f" | cleared by the orchestrator: {cleared[f['id']]}"
        rows.append(f"- **{label}**{pat}: {f['source_line']}\n  - reason: {why}")
    for nt in lead.get("verified_notes") or []:
        if isinstance(nt, dict):
            rows.append(f"- **verified note** (reviewer's own, outside its finding): {nt.get('line')}\n"
                        f"  - reason: {nt.get('reason')}")
    (out / f"lead-dismissals-round-{n}.md").write_text("\n".join(rows) + "\n")
    counts = {k: sum(1 for f in items if f["verdict"] == k and not f.get("_disputed") and not f.get("_not_fixed"))
              for k in ("dismissed", "noted", "fixed")}
    print(f"BLOCKING={len(blocking)} ESCALATE={len(escalate)} FOLLOWUPS={len(follow)} "
          f"DISMISSED={counts['dismissed']} NOTED={counts['noted']} P4={len(demoted)} CLEARED={len(cleared)} "
          f"FIXED={counts['fixed']}")
    for cid, line in cleared.items():
        print(f"CLEARED {cid}: {line}")
    for nt in RUN_NOTES:
        print(f"NOTE {nt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
