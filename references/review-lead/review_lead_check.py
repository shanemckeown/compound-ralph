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
# A finding's location as written, keeping a line range ("lib/t.ts:78-83"); compared exactly (LUCY-l1s6g c).
LOC = re.compile(FILE_LINE.pattern + r"(?:-\d+)?(?![\w-])")
# A count is a number standing alone: "1 P1", "(0 P1", never the 1 in "Round-1 P1" (LUCY-l1s6g e).
SUMMARY_COUNT = re.compile(r"(?<![\w-])(\d+)\s*(?:x\s*)?(P[123])\b")
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
# The location may be a file with no line when the finding named one ("tests/x.test.tsx", LUCY-e3opf).
CONFIRM_FIXED = re.compile(r"^\s*[-*]\s+(?:\*\*)?r(\d+)\s+(P[123])\s+([\w./\[\]-]*[/.][\w./\[\]-]*?(?::\d+(?:-\d+)?)?)"
                           r"(?:\*\*)?\s+(?:closed|fixed|resolved):\s+(?=\S)[^?]{8,}$", re.I)
# Adverse wording is a judgement on the finding, not any negative word in the evidence (LUCY-e3opf):
# "tests confirm allergies do not populate", "restoring it caused the test to fail" and "no longer
# defaults smoking" are how a closure reads. Checked a sentence at a time:
#   - the first sentence opening on a non-closure ("not fixed", "but still ...", "partially");
CONFIRM_LEAD = re.compile(r"^\W*(?:not|no(?!\s+longer)|never|but|however|yet|partial|partially|partly|unable|cannot|"
                          r"can't|unconfirmed|unverified|untested|broken|regressed|reopen\w*|unresolved|outstanding|"
                          r"pending\W*$|intermittent\w*|flaky|sometimes|still|open|only partly)\b"
                          r"|^\W*fail(?:s|ed|ing)?\b(?!-)(?!\s+(?:fetch|read|request|response|get|load|refetch|call|"
                          r"network|every|each|all)\b)",
                          re.I)
#   - hedges anywhere;
CONFIRM_NEGATION = re.compile(r"\b(but|however|yet|partial|partially|partly|unconfirmed|unverified|untested|unfixed|"
                              r"incomplete|regressed|reopen\w*|unresolved|outstanding|intermittent\w*|flaky|sometimes|"
                              r"pending\s+(?:a\s+|the\s+)?(?:verification|review|confirmation|fix|deploy\w*|prod\w*|"
                              r"check|re-?test\w*|landing)|(?:is|are|still|remains?)\s+pending)\b", re.I)
#   - a negated status, or a test that does not catch the revert ("not fixed", "isn't verified",
#     "does not fail without the guard", "did not run");
CONFIRM_NEG_STATUS = re.compile(r"(?:\b(?:not|never)|n't)\b(?:\s+[\w-]+){0,3}?\s+(?:fixed|closed|resolved|"
                                r"addressed|verified|confirmed|tested|checked|covered|run|ran|exercised|caught|catch\w*|"
                                r"detect\w*|killed|kill|fail|fails|failed|work|works|working|hold|holds|match|matches|matched)\b"
                                r"|\bnot\s+(?:what|true|the case|so|as (?:claimed|described|stated|expected))\b"
                                r"|\b(?:unable to|cannot|can't|could not|couldn't)\s+(?:\w+\s+)?(?:verify|confirm|"
                                r"test|run|check|exercise)\b", re.I)
#   - "still" / "remains" followed by a bug state ("still reproduces", "remains broken", "still charges");
#     a passing state ("the control remains green", "Medication remained empty") is evidence;
CONFIRM_STATE = re.compile(r"\b(?:still|remains?|remaining)\b(?!\s+(?:green|passing|passes|pass|correct|clean|"
                           r"intact|unchanged|closed|fixed|resolved|empty|blank|unset|absent|safe|showed|shows|"
                           r"shown|reads?\s+as(?!\s+(?:broken|fail\w*|open|wrong|unfixed|stale|incorrect|buggy)\b))\b)", re.I)
#   - "fails" / "failed" outside mutant evidence ("restoring the default failed its regression") and
#     outside a failure the probe injected ("failed-fetch tests", "failed every GET").
FAIL_WORD = re.compile(r"(?<!-)(?<!\ba )(?<!\ban )\bfail(?:s|ed|ing)?\b(?!-)(?!\s+(?:fetch|read|request|response|get|load|refetch|call|"
                       r"network|every|each|all)\b)", re.I)
THEN_FAIL = re.compile(r"\bthen\s+fail(?:s|ed)?\b", re.I)
BROKEN = re.compile(r"\b(?:is|are|was|were|still|remains?|left)\s+broken\b", re.I)
MUTANT_FAIL = re.compile(r"\bmakes?\s+(?:it|them)\s+fail\b", re.I)
MUTANT_SENTENCE = re.compile(r"\b(?:restor(?:ing|ed)|remov(?:ing|ed)|revert\w*|replac(?:ing|ed)|mutat\w*|mutant\w*|"
                             r"undo(?:ing)?|bypass(?:ing|ed)|"
                             r"weaken\w*|without the (?:fix|guard|change|check))\b", re.I)
# A revert that changes nothing: the test does not catch the finding.
MUTANT_SURVIVED = re.compile(r"\bno (?:difference|change|effect)\b|\b(?:still|also|continues? to)\s+pass(?:es|ed)?\b"
                             r"|\b(?:stays?|stayed|remains?|remained)\s+green\b", re.I)
# A sentence pointing at a different finding the same file lists ("The cleared-answer defect remains
# separately open above") is not about this one.
SEPARATE_FINDING = re.compile(r"\b(?:separate(?:ly)?|distinct|different|another|other)\b.*"
                              r"\b(?:above|below|reported|listed|raised|tracked)\b", re.I)
# ... and only when it names that finding as such ("The distinct ... defect is reported above").
FINDING_NOUN = re.compile(r"\b(?:defect|finding|issue|bug|problem)s?\b", re.I)
THIS_FINDING = re.compile(r"\b(?:this|that|the same|our|my)\s+(?:\w+\s+)?(?:defect|finding|issue|bug|problem)\b|^\W*it\b", re.I)
CAUGHT = re.compile(r"\b(?:killed|caught|red|fail(?:s|ed|ing)?)\b", re.I)
FAILS_A_TEST = re.compile(r"\bfail(?:s|ed)?\s+(?:the|its|that|both|all|\d+|two|three|four)?\s*[\w\- ]{0,40}?"
                          r"\b(?:tests?|assertions?|cells?|regressions?)\b", re.I)
ABSENCE = re.compile(r"\bno\s+[\w.\-]+(?:\s+[\w.\-]+){0,2}\s+(?:remains?|remaining|left)\b", re.I)
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
    # "no .cjs references remain" is absence evidence; an all-caps UNVERIFIED is a label being quoted.
    t = re.sub(r"\bUNVERIFIED\b", " ", ABSENCE.sub(" ", t))
    if CONFIRM_LEAD.match(t):
        return True
    kept = []
    for sentence in re.split(r"(?<=[.!])\s+", t.strip()):
        mutant_before = False
        for s in re.split(r"(?<=;)\s+", sentence):
            if adverse_segment(s, kept, mutant_before):
                return True
            mutant_before = mutant_before or bool(MUTANT_SENTENCE.search(s))
            kept.append(s)
    t = " ".join(kept)
    for m in re.finditer(r"\bopen\b", t, re.I):
        clause = re.split(r"[;:,.]", t[:m.end()])[-1]
        subj = OPEN_SUBJECT.search(clause)
        if not subj or subj.end() != len(clause) or subj.group(1).lower() in FINDING_REFERENTS:
            return True
    return False


def adverse_segment(s, kept, mutant_before):
    """One sentence or ';' part of a closure tail; `kept` holds the parts before it."""
    if CONFIRM_NEGATION.search(s) or CONFIRM_NEG_STATUS.search(s):
        return True
    if SEPARATE_FINDING.search(s) and FINDING_NOUN.search(s) and not THIS_FINDING.search(s):
        return False  # "still" / "open" here describe the other finding the file lists
    if CONFIRM_STATE.search(s) or BROKEN.search(s):
        return True
    marked = bool(MUTANT_SENTENCE.search(s))
    # A revert that changes nothing ("no difference after reverting", "still passes") is a surviving
    # mutant, unless the same sentence reports it killed; a control is judged in its own clause.
    if marked and not CAUGHT.search(s) and any(MUTANT_SURVIVED.search(c) and not re.search(r"\bcontrols?\b", c, re.I)
                                               for c in re.split(r",|\band\b", s)):
        return True
    prev = kept[-1] if kept else ""
    if (MUTANT_SENTENCE.search(prev) and not CAUGHT.search(prev) and re.search(r"\bpass(?:es|ed)?\b", s, re.I)
            and not CAUGHT.search(s) and any(re.search(r"\bpass(?:es|ed)?\b", c, re.I)
                                             and not re.search(r"\bcontrols?\b", c, re.I)
                                             for c in re.split(r",|\band\b", s))):
        return True  # "The guard was removed. All regression tests pass."
    # Mutant evidence runs on: "I mutated X. Three tests then fail." / "removing A fails the X test;
    # dropping B fails the Y test".
    mutant = marked or (MUTANT_SENTENCE.search(prev) and THEN_FAIL.search(s)) or (mutant_before and FAILS_A_TEST.search(s))
    return bool(not mutant and FAIL_WORD.search(s))


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


def round_of(name):
    """The review round a file's name states (`_r<N>_`); a name without one is round 1 (LUCY-l1s6g b)."""
    m = ROUND_IN_NAME.search(Path(str(name)).name)
    return int(m.group(1)) if m else 1


# A later round's line about an earlier round's finding: "- r1 P2 <file:line>[ and ...] closed: ...".
EARLIER_REF = re.compile(r"^\s*[-*]\s+(?:\*\*)?r(\d+)\s+(P[123])\s+(\S+:\d+.*?)(?:\*\*)?\s+"
                         r"(?:closed|fixed|resolved):\s*(.*)$", re.I)
# A severity named only as a parenthetical back-reference, e.g. "... fails for the marked case (P1)."
BACK_REF = re.compile(r"\((P[123])\)")
HAZARDS_HEAD = re.compile(r"^\s*(?:#+\s*)?(?:\*\*)?hazards?\b", re.I)


def hazard_lines(lines):
    """Indexes of the lines inside a Hazards section, any case (until the next section or verdict)."""
    cur, out = False, set()
    for i, line in enumerate(lines):
        if HAZARDS_HEAD.match(line):
            cur = True
            continue
        if SECTION.match(line) or VERDICT_HEAD.match(line) or re.match(r"^\s*(?:#+\s*)?REVIEWING:", line):
            cur = False
        if cur:
            out.add(i)
    return out


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


def back_reference_note(line, mine):
    """A Hazards bullet whose only severity is a parenthetical back-reference to a severity the file
    lists ("- H-U0: ... fails for the marked-thread case (P1).") points at that finding. It goes to
    the lead (verified_notes), never silently dropped; one with a location, a never-dismiss area, a
    bare severity, or a severity the file does not list is refused (LUCY-l1s6g j)."""
    if not re.match(r"^\s*[-*\u2022]\s", line) or FINDING.match(line) or FINDING_START.match(line):
        return False
    refs = BACK_REF.findall(line)
    rest = BACK_REF.sub(" ", line)
    if (not refs or FILE_LINE.search(line) or PROTECTED.search(line) or SEVERITY_TOKEN.search(rest)
            or EXPLICIT_LABEL.search(rest) or any(not any(m.startswith(r) for m in mine) for r in refs)):
        return False
    UNBOUND_NOTES[clean(line)] = line.strip()
    return True


def prose_match(line, mine, used):
    """Index of the block finding a prose finding line stands for, or None. Verbatim first; else a
    paraphrase: same severity, the same set of file:line locations, nearly all the same words, and a
    block finding not already claimed by another prose line (LUCY-l1s6g i)."""
    c = clean(line)
    for i, m in enumerate(mine):
        if i not in used and m == c:
            return i
    locs = {x.group(0) for x in FILE_LINE.finditer(c)}
    words = set(re.findall(r"[a-z0-9_]{3,}", c.lower()))
    for i, m in enumerate(mine):
        if i in used or m[:2] != c[:2] or {x.group(0) for x in FILE_LINE.finditer(m)} != locs or not locs:
            continue
        mw = set(re.findall(r"[a-z0-9_]{3,}", m.lower()))
        if words and mw and len(words & mw) / min(len(words), len(mw)) >= 0.8:
            return i
    # A reworded block finding (LUCY-e3opf, 3bm73 r5: 0.71 overlap): the only unclaimed block finding
    # with this severity and file:line set is the one, if it still shares a fair part of its wording
    # (outside the locations), so a different finding at the same line stays refused.
    # The block may drop a second location the prose cites as evidence: the leading one decides.
    lead = finding_loc(c)
    same = [i for i, m in enumerate(mine) if i not in used and m[:2] == c[:2] and lead
            and finding_loc(m) == lead]
    if len(same) == 1:
        cw, mw = content_words(c), content_words(mine[same[0]])
        if cw and mw and len(cw & mw) / min(len(cw), len(mw)) >= REWORD_OVERLAP:
            return same[0]
    return None


REWORD_OVERLAP = 0.4
STOP_WORDS = {"the", "and", "for", "with", "that", "this", "from", "into", "then", "when", "are", "was", "its",
              "has", "have", "but", "not", "fix", "any", "all", "one", "its", "his", "her", "their", "our"}


def content_words(s):
    """Words of a finding outside its file:line locations and common stop words."""
    return set(re.findall(r"[a-z0-9_]{3,}", FILE_LINE.sub(" ", s).lower())) - STOP_WORDS


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
        # The text may repeat the severity ("P2 lib/x.ts:3 ..." with severity P2): one prefix, not
        # "P2 P2 ..." (LUCY-l1s6g a). A different leading severity is kept, so it never matches.
        text = re.sub(rf"^(?:\*\*)?{sev}(?:\*\*)?[\s:]+", "", text)
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
            haz = hazard_lines(flines)
            used = set()
            for i, line in enumerate(flines):
                if i in ver and tolerated_note(line, mine):
                    continue
                if i in haz and back_reference_note(line, mine):
                    continue
                if FINDING.match(line):
                    j = prose_match(line, mine, used)
                    used.add(j)
                    if j is None:
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
    haz = hazard_lines(plines)
    kept, used = [], set()
    # Without a block, prose lines are the findings themselves: verbatim only.
    paraphrase_ok = bool(blocks)
    for i, line in enumerate(plines):
        if i in ver and tolerated_note(line, mine):
            continue
        if blocks and i in haz and back_reference_note(line, mine):
            continue
        kept.append(line)
        if FINDING.match(line):
            j = prose_match(line, mine, used) if paraphrase_ok else (
                next((k for k, m in enumerate(mine) if k not in used and m == clean(line)), None))
            used.add(j)
            if j is None:
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


LEAD_LOC = re.compile(r"^P[123]\s+([\w./\[\]-]*[/.][\w./\[\]-]*?(?::\d+(?:-\d+)?)?)(?=[\s,;]|$)")


def finding_loc(src):
    """Where a finding is: its leading path, with or without a line ("P3 tests/x.test.tsx — ..."),
    else its first file:line (LUCY-e3opf)."""
    m = LEAD_LOC.match(src)
    if m and (":" in m.group(1) or not LOC.search(src) or "/" in m.group(1)):
        return m.group(1)
    loc = LOC.search(src)
    return loc.group(0) if loc else None


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
        # Order by the round each name states (no _rN_ = round 1); mtime only breaks a tie, because
        # a copy or checkout resets it (LUCY-l1s6g g). An earlier round never confirms a later one.
        r_later, r_x = round_of(later[0]), round_of(x[0])
        if r_later < r_x or (r_later == r_x and Path(later[0]).stat().st_mtime <= Path(x[0]).stat().st_mtime):
            return [f"{tag}: fixed_by {Path(later[0]).name} is not newer than {Path(x[0]).name} "
                    f"(round {r_later} vs {r_x})"]
    rnd = str(round_of(source[0]))
    loc = finding_loc(src)
    if not loc:
        return [f"{tag}: fixed needs a file:line in the finding"]
    sev = src[:2]
    if ":" not in loc and len({x for f_ in files if round_of(f_[0]) == round_of(source[0]) for x in f_[1]
                               if x[:2] == sev and finding_loc(x) == loc}) > 1:
        return [f"{tag}: round {rnd} has more than one {sev} at {loc} with no line: a confirmation "
                "can't tell them apart; grade it from a review that names the line"]
    for other in later[1]:
        if other[:2] == sev and finding_loc(other) == loc:
            return [f"{tag}: {Path(later[0]).name} raises {sev} at {loc} again: not fixed"]
    lines = later[2].splitlines()
    for i in verified_lines(lines):
        m = CONFIRM_FIXED.match(lines[i])
        if (m and is_confirmation(lines[i]) and m.group(1) == rnd and m.group(2).upper() == sev
                and m.group(3) == loc):
            return []
    return [f"{tag}: {Path(later[0]).name} has no VERIFIED OK line '- r{rnd} {sev} {loc} "
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
