#!/usr/bin/env python3
"""Shared sensitive-path policy for /land-batch.

Keep path configuration here so discovery and kickback classification cannot
silently drift apart.  ``drizzle/`` is intentionally special: generated SQL
migrations are sensitive, while README and generated metadata are handled by
the dedicated migration reconciliation gate.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SENSITIVE_PREFIXES = (
    "lib/db/",
    "drizzle/",
    "lib/stripe/",
    "lib/auth/",
    "lib/payments/",
    "pages/api/auth/",
    "pages/api/admin/",
    "pages/api/webhooks/",
    "lib/email/templates/",
)

MIGRATION_SQL_RE = re.compile(r"^drizzle/\d{4}_[^/]+\.sql$")


def is_sensitive_path(path: str) -> bool:
    """Return whether a repository-relative path is sensitive (it lands on a recorded review PASS, never a question)."""
    if MIGRATION_SQL_RE.fullmatch(path):
        return True
    return any(
        path.startswith(prefix)
        for prefix in SENSITIVE_PREFIXES
        if prefix != "drizzle/"
    )


def sensitive_paths(paths_to_check: Iterable[str] | None) -> list[str]:
    return sorted(
        {
            path
            for path in (paths_to_check or [])
            if isinstance(path, str) and is_sensitive_path(path)
        }
    )


def validate_sensitive_prefixes(
    repo: str | Path,
    prefixes: Iterable[str] = SENSITIVE_PREFIXES,
) -> list[str]:
    """Return configured prefixes that do not map to a real target path.

    The old ``drizzle/migrations/`` value survived because configuration was
    only exercised against changed filenames.  This structural check makes a
    stale directory prefix fail before discovery can approve a candidate.
    """
    root = Path(repo)
    missing = []
    for prefix in prefixes:
        relative = prefix.rstrip("/")
        target = root / relative
        if not target.exists():
            missing.append(prefix)
            continue
        if prefix == "drizzle/" and not any(
            MIGRATION_SQL_RE.fullmatch(path.relative_to(root).as_posix())
            for path in target.glob("*.sql")
        ):
            missing.append(prefix)
    return missing


# --- Recorded independent review (2026-10-03: replaces the sensitive-path opt-in) ---
# A sensitive-path branch lands without a question when an independent review recorded
# PASS for its exact tip. Set True to require the same of ordinary branches.
REQUIRE_REVIEW_PASS_FOR_ALL = False

DEFAULT_REVIEWS_DIR = Path("~/Documents/Obsidian/Aestheticc/Ops/reviews").expanduser()
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def _matches_tip(text: str, tip_sha: str) -> bool:
    return any(tip_sha.startswith(sha) for sha in _SHA_RE.findall(text.lower()))


# Bead prefix -> the beads database that owns it. An explicit BEADS_DIR always wins.
BEADS_DIRS_BY_PREFIX = (
    ("aestheticcnext-", Path("~/Documents/GitReBase/AestheticcNext/.beads")),
    ("lucy-", Path("~/Documents/Obsidian/.beads")),
)
DEFAULT_BEADS_DIR = Path("~/Documents/Obsidian/.beads")


def beads_dir_for(bead_id: str, env=None) -> str:
    """Return the beads directory to query for ``bead_id``.

    ``BEADS_DIR`` (if set) overrides; otherwise the bead prefix decides, so an
    AestheticcNext bead is never looked up in the vault database (where it has no
    comments and so would read as "no PASS").
    """
    env = os.environ if env is None else env
    explicit = env.get("BEADS_DIR")
    if explicit:
        return explicit
    lowered = (bead_id or "").lower()
    for prefix, path in BEADS_DIRS_BY_PREFIX:
        if lowered.startswith(prefix):
            return str(path.expanduser())
    return str(DEFAULT_BEADS_DIR.expanduser())


def _bead_comments(bead_id: str) -> str:
    if os.environ.get("LAND_SKIP_BEAD_COMMENTS"):
        return ""
    # Offline fixtures (tests): <dir>/<bead_id>.txt holds that bead's ``bd comments`` output.
    env = dict(os.environ)
    env["BEADS_DIR"] = beads_dir_for(bead_id)
    try:
        out = subprocess.run(["bd", "comments", bead_id], capture_output=True, text=True,
                             timeout=20, env=env)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


# ``bd comments`` prints each comment as a header line followed by indented body lines.
_COMMENT_HEADER_RE = re.compile(r"^\[(?P<author>[^\]]*)\] at (?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*$")
# The review lead's counts, as review_lead_check.py prints them
# ("BLOCKING=n ESCALATE=n FOLLOWUPS=n ...") and as Managers copy them into the bead
# ("BLOCKING=0, ESCALATE=3, NOTED=1"). BLOCKING must be followed by ESCALATE.
_LEAD_COUNTS_RE = re.compile(r"\bBLOCKING=(?P<blocking>\d+)\s*,?\s*ESCALATE=(?P<escalate>\d+)\b")
# A review-lead record names itself: the /goal + aestheticc-review shape is
# "REVIEW LEAD <date> (tip <sha>, review_lead_check.py passed): BLOCKING=0, ESCALATE=n, ...".
_LEAD_RECORD_RE = re.compile(r"^\s*REVIEW LEAD\b|review_lead_check(?:\.py)?\s+passed", re.M)
# The canonical landable line: "PASS @<sha>" at the start of a comment line.
_PASS_AT_RE = re.compile(r"^\s*PASS\s+@(?P<sha>[0-9a-f]{7,40})\b(?P<rest>.*)$", re.I)
# "PASS @<sha> VOID: <reason>" withdraws an earlier PASS for that tip.
_VOID_RE = re.compile(r"^\W*(VOID(?:ED)?|REVOKED|WITHDRAWN|RETRACTED)\b", re.I)
# A reviewer verdict word in verdict position: "...: FAIL — 0 P1", "Sonnet FAIL 1 P2",
# "PASS 0/0/0", "FAIL (2 P2)", or at line end. Prose such as "the word FAIL inside" or
# "FAIL recorded" is not a verdict.
# Quoted text ('...' or "...") is someone quoting a verdict, not giving one.
_QUOTED_RE = re.compile(r"(?<![A-Za-z])'[^'\n]*'(?![A-Za-z])|\"[^\"\n]*\"")
# /land-batch's own records ("LAND <run-id>: ...", "Landed ...") narrate a land; never a review.
_LANDER_RECORD_RE = re.compile(r"^\s*(LAND\s+\d{8}T\d{6}Z|Landed\b)")
_VERDICT_WORD_RE = re.compile(r"\b(PASS|FAIL)\b(?=\s*(?:[\u2014\u2013:;,(\-]|\d|$))", re.M)


def _parse_comments(text: str):
    """Split ``bd comments`` output into (timestamp, body) records, oldest first.

    Text without bd headers (hand-written fixtures) is treated as one comment per line,
    in order, with no timestamp.
    """
    records, current = [], None
    saw_header = False
    for line in (text or "").splitlines():
        header = _COMMENT_HEADER_RE.match(line)
        if header:
            saw_header = True
            current = [header.group("ts"), []]
            records.append(current)
        elif current is not None:
            current[1].append(line)
    if not saw_header:
        return [(None, line) for line in (text or "").splitlines()
                if line.strip() and not line.startswith("Comments on ")]
    return [(ts, "\n".join(body).strip()) for ts, body in records]


def _classify_comment(body: str, tip_sha: str):
    """Classify one comment against the tip. Returns (kind, detail) or None.

    kind is one of:
      "void"     a ``PASS @<tip> VOID: ...`` line withdrawing the PASS
      "pass_at"  a ``PASS @<tip>`` line; detail = True when the comment itself cites a
                 review-lead verdict with BLOCKING=0
      "lead"     a review-lead record for the tip; detail = (blocking, escalate)
      "fail"     a raw reviewer FAIL for the tip
      "pass"     a raw reviewer PASS for the tip
    """
    lines = body.splitlines()
    pass_at = [m for ln in lines if (m := _PASS_AT_RE.match(ln)) and tip_sha.startswith(m.group("sha").lower())]
    if any(_VOID_RE.match(m.group("rest")) for m in pass_at):
        return "void", None
    if pass_at:
        counts = _LEAD_COUNTS_RE.search(body)
        cites_lead = bool(counts and int(counts.group("blocking")) == 0)
        return "pass_at", cites_lead
    if not _matches_tip(body, tip_sha):
        return None
    if _LANDER_RECORD_RE.match(body):
        return None
    counts = _LEAD_COUNTS_RE.search(body)
    if counts and _LEAD_RECORD_RE.search(body):
        return "lead", (int(counts.group("blocking")), int(counts.group("escalate")))
    # Raw reviewer verdicts: a line's FIRST verdict word decides it, so a PASS line that
    # merely quotes an earlier "raw FAIL" never reads as a FAIL.
    line_verdicts = []
    for ln in lines:
        word = _VERDICT_WORD_RE.search(_QUOTED_RE.sub(" ", ln))
        if word:
            line_verdicts.append(word.group(1))
    if "FAIL" in line_verdicts:
        return "fail", None
    if "PASS" in line_verdicts:
        return "pass", None
    return None


def review_pass_for(bead_id, tip_sha, reviews_dir=None, comments_text=None):
    """Return (passed, evidence) for a recorded independent review of this exact tip.

    Sources, in time order: Ops/reviews/REVIEW_*.md (names the bead and tip sha, has a
    ``Verdict: PASS|FAIL`` line; ordered by mtime; LAND_REVIEWS_DIR overrides the directory)
    and bead comments (``bd comments`` records, oldest first).

    A raw reviewer verdict is input to the review lead; the lead decides (CLAUDE.md "Review
    findings go through the lead"). So for this tip:
      * a raw reviewer FAIL holds the branch;
      * a LATER review-lead record (``REVIEW LEAD ... BLOCKING=0, ESCALATE=n``) clears it;
        with ESCALATE=0 that record is itself a pass, with ESCALATE>0 a ``PASS @<tip>``
        line must follow (the orchestrator's clearance);
      * a later lead record with BLOCKING>0 holds;
      * a ``PASS @<tip>`` line passes, unless an un-cleared raw FAIL precedes it and the
        PASS comment does not itself cite a lead verdict with BLOCKING=0;
      * a raw FAIL after the last PASS/lead verdict holds again, and so does a later
        ``PASS @<tip> VOID: <reason>`` line.
    Mentions of "FAIL" inside a PASS line never count. A PASS on an older tip does not count.
    """
    if not bead_id or not tip_sha:
        return False, "no bead or tip to match"
    tip_sha = tip_sha.lower()
    events = []  # (sort_key, seq, kind, detail, evidence)
    root = Path(reviews_dir or os.environ.get("LAND_REVIEWS_DIR") or DEFAULT_REVIEWS_DIR)
    if root.is_dir():
        for path in sorted(root.glob("REVIEW_*.md")):
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if bead_id.lower() not in body.lower() or not _matches_tip(body, tip_sha):
                continue
            verdict = re.search(r"^\W*Verdict:\s*(PASS|FAIL)", body, re.I | re.M)
            if verdict:
                kind = "pass" if verdict.group(1).upper() == "PASS" else "fail"
                events.append((mtime, len(events), kind, None, str(path)))
    comments = _bead_comments(bead_id) if comments_text is None else comments_text
    for ts, body in _parse_comments(comments):
        classified = _classify_comment(body, tip_sha)
        if classified is None:
            continue
        kind, detail = classified
        if ts is None:
            key = float("inf")  # undated fixture lines keep their order after any file
        else:
            key = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
        first = body.strip().splitlines()[0] if body.strip() else ""
        events.append((key, len(events), kind, detail, f"bead comment: {first.strip()[:120]}"))
    events.sort(key=lambda e: (e[0], e[1]))

    state, evidence = None, None   # state: None | "pass" | "fail" | "cleared"
    for _, _, kind, detail, ev in events:
        if kind in ("fail", "void"):
            state, evidence = "fail", ev
        elif kind == "lead":
            blocking, escalate = detail
            if blocking > 0:
                state, evidence = "fail", ev
            elif escalate == 0:
                state, evidence = "pass", ev
            else:
                state, evidence = "cleared", ev
        elif kind == "pass_at":
            if state == "fail" and not detail:
                continue  # an un-cleared raw FAIL is not overridden by a bare PASS line
            state, evidence = "pass", ev
        elif kind == "pass":
            if state in (None, "pass"):
                state, evidence = "pass", (evidence if state == "pass" else ev)
            # a raw PASS never overrides a FAIL, nor completes an escalated lead verdict
    if state == "pass":
        return True, evidence
    if state == "fail":
        return False, "FAIL recorded for this tip: " + evidence
    if state == "cleared":
        return False, "review lead BLOCKING=0 but escalations not cleared (no PASS @tip after it): " + evidence
    return False, "no recorded review PASS for this tip"


# The lander contract (LUCY-qymwm, 22 Oct): the comment's FIRST line is exactly "PASS @<sha>".
_EXACT_PASS_LINE_RE = re.compile(r"^PASS @(?P<sha>[0-9a-f]{7,40})$")
# What review-gate.sh (references/review-lead/review_gate.py) writes into its Ops/reviews record.
GATE_SIGNATURE = "- Checker: review_lead_check.py via review-gate.sh:"
_REVIEWING_RE = re.compile(r"(?m)^\s*REVIEWING:\s*\S+\s*@\s*([0-9a-fA-F]{7,40})[ \t]*$")
# A veto token, deliberately broad: over-holding costs one fresh gate run, under-holding lands
# unreviewed code. Upper-case words only, so prose like "failed" or "void" doesn't trip it.
_VETO_RE = re.compile(r"\b(FAIL|FAILED|VOID|VOIDED|REVOKED|WITHDRAWN|RETRACTED)\b|\bBLOCKING=[1-9]")
# A review withdrawn by its author says so on a line of its own ("VOID: superseded by r11"); the
# word inside a finding ("VOID invoices can still charge") is never a withdrawal (Astra r14, r22).
# review_gate.py reads it from here: one definition.
WITHDRAWAL_RE = re.compile(r"(?mi)^\W*(VOID|VOIDED|REVOKED|WITHDRAWN|RETRACTED)\b(?:\W*[:—–-]|\W*$)")
# goal.md's lead-escalation status line (LUCY-b7pqo). Starts-with, not fullmatch: the Manager
# writes a summary after the path on the same line.
_LEAD_ESCALATION_RE = re.compile(r"^needs input: (?P<bead>\S+) escalated \d+ protected item\(s\): (?P<path>/\S+)")
# Verdict shapes that keep a status-shaped comment a veto. Matched on _normalise(body), so they
# catch what a careless writer produces ("**FAIL** @ **<sha>**", "fail @<sha>"), not only the
# canonical spelling (LUCY-b7pqo r1).
_VERDICT_SHAPE_RES = (
    re.compile(r"\bfail(?:ed)?\s*[@:]"),                       # FAIL @<sha>, FAILED: <sha>
    re.compile(r"\bfail(?:ed)?[^\w\n]{0,4}[0-9a-f]{7,40}\b"),  # FAIL — <sha>
    re.compile(r"(?m)^\W*fail(?:ed)?\b"),                      # a review's own FAIL line
    re.compile(r"\bverdict\W{0,4}fail"),                       # Verdict: FAIL, "verdict": "FAIL"
    re.compile(r"(?m)^\W*reviewing\s*:"),
    re.compile(r"\bblocking\s*=\s*[1-9]"),
    # a finding line: P1/P2 then a path-like token (lib/x.ts, x.ts:9, review_gate.py)
    re.compile(r"(?<![\w-])p[12](?![\w-])[^\S\n]*[:|—–-]?[^\S\n]*[^\s,;()]*"
               r"(?:/|\.[a-z][a-z0-9]{0,5}\b|:\d)"),
)
EVIDENCE_ROOT = Path("~/.claude/evidence").expanduser()


def is_gate_record(path, body: str) -> bool:
    """review-gate.sh's own record, by structure: its file name, its REVIEWING first line, and
    the signature as a line of its own. Quoting the signature inside a review does not make one."""
    lines = body.splitlines()
    return bool(re.match(r"^REVIEW_.+_gate_[0-9a-f]{10}_\d{8}T\d{6}Z\.md$", Path(path).name)
                and lines and _REVIEWING_RE.match(lines[0])
                and any(ln.startswith(GATE_SIGNATURE) for ln in lines))


def review_file_vetoes(root, bead_id, tip_sha, exclude=()):
    """[(mtime, path)] of Ops/reviews files for this bead that name the tip with a veto token,
    other than valid gate records and ``exclude``. Shared with review_gate.py."""
    out = []
    root = Path(root)
    if not root.is_dir():
        return out
    excluded = {str(Path(e).resolve()) for e in exclude}
    for path in sorted(root.glob("REVIEW_*.md")):
        if str(path.resolve()) in excluded:
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if not mentions_bead(body, bead_id) or not _names_tip(body, tip_sha):
            continue
        if is_gate_record(path, body) and not _VETO_RE.search(body):
            continue
        if _VETO_RE.search(body):
            out.append((mtime, str(path)))
    return out


def mentions_bead(body: str, bead_id: str) -> bool:
    """The bead's full id, or its short suffix as a whole token (``goal/aestheticcnext-q41ar``,
    ``q41ar``). One rule for the gate's input check and both lander veto scans."""
    suffix = bead_id.split("-", 1)[-1]
    return any(re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9]|\.\d)", body, re.I)
               for t in (bead_id, suffix))


def _normalise(body: str) -> str:
    """A copy for the verdict-shape scan: markdown emphasis/code/heading/quote marks and quotes
    gone, whitespace inside each line collapsed, casefolded. Line breaks stay (a line that opens
    with ``fail`` or ``reviewing:`` is a verdict)."""
    text = re.sub(r"[*_`#>\"']", "", body)
    return "\n".join(" ".join(ln.split()) for ln in text.splitlines()).casefold()


def is_lead_escalation_status(body: str, bead_id: str) -> bool:
    """The review lead's escalation status comment for this bead (goal.md Phase 4 Step 4): line 1
    starts ``needs input: <bead_id> escalated <N> protected item(s): <path under ~/.claude/evidence/>``
    and nowhere does the comment carry a verdict-shaped veto (``FAIL @<sha>``, ``Verdict: FAIL``,
    a ``REVIEWING:`` line, a P1/P2 finding line, a withdrawal line, ``BLOCKING>=1``, in any case or
    markdown) or any upper-case veto token other than the bare prose word FAIL. A real review verdict
    always reaches the bead as review-gate.sh's ``FAIL @<tip>`` mirror, which still vetoes."""
    lines = body.strip().splitlines()
    m = _LEAD_ESCALATION_RE.match(lines[0].strip()) if lines else None
    if not m or m.group("bead") != bead_id:
        return False
    path = Path(m.group("path"))
    # realpath on both sides: a symlink under the root that leaves it is outside it.
    root = Path(os.path.realpath(EVIDENCE_ROOT))
    if ".." in path.parts or root not in Path(os.path.realpath(path)).parents:
        return False
    norm = _normalise(body)
    if any(r.search(norm) for r in _VERDICT_SHAPE_RES) or any(
            WITHDRAWAL_RE.search(t) for t in (body, norm)):
        return False
    # Any veto word other than a bare FAIL (FAILED, VOID, ...) keeps it a veto; BLOCKING is above.
    return all(t.group(1) in (None, "FAIL") for t in _VETO_RE.finditer(body))


def _comment_vetoes_tip(body: str, tip_sha: str, bead_id: str) -> bool:
    return (_names_tip(body, tip_sha) and bool(_VETO_RE.search(body))
            and not is_lead_escalation_status(body, bead_id))


def comment_vetoes(comments_text, tip_sha, bead_id):
    """[(utc epoch of the comment's minute, first line, body)] of bead comments that veto this tip:
    they name it and carry a veto token, and are not a clean exact ``PASS @<tip>`` line nor the
    lead-escalation status line."""
    tip_sha = tip_sha.lower()
    out = []
    for ts, body in _parse_comments(comments_text):
        if ts is None:
            continue
        key = datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
        first = (body.strip().splitlines() or [""])[0].strip()
        if _comment_vetoes_tip(body, tip_sha, bead_id):
            out.append((key, first, body))
    return out


def _names_tip(text: str, tip_sha: str) -> bool:
    return any(len(sha) >= 7 and tip_sha.startswith(sha) for sha in _SHA_RE.findall(text.lower()))


def independent_pass_for(bead_id, tip_sha, reviews_dir=None, comments_text=None):
    """Return (passed, evidence): may discovery mark this tip auto_land?

    Approval comes from review-gate.sh's records only, never from prose (ul5ts @4be6c71efe,
    22 Oct: a builder's own-gate note read as a review PASS). For this exact tip:
      1. approval = a bead comment whose first line is exactly ``PASS @<tip>`` (full sha or a
         7+ prefix) and with no veto token anywhere in it, AND an Ops/reviews/REVIEW_*.md that
         carries the review-gate signature, names the bead, has ``Verdict: PASS``, and whose
         every ``REVIEWING: <branch> @ <sha>`` echo is this tip;
      2. veto = any other bead comment, or any non-gate Ops/reviews file naming the bead, that
         names the tip and carries an upper-case FAIL / VOID / REVOKED / WITHDRAWN / RETRACTED
         or BLOCKING>=1, in any format;
      3. both halves of the approval must be later than every veto. Comment times are UTC
         minutes: a comment beats a file only if its minute starts after the file's mtime; a
         file beats a comment only if written after that comment's whole minute.
    """
    if not bead_id or not tip_sha:
        return False, "no bead or tip to match"
    tip_sha = tip_sha.lower()
    vetoes, passes = [], []   # vetoes: (kind, time, seq, evidence); passes: (minute, seq, line)
    comments = _bead_comments(bead_id) if comments_text is None else comments_text
    for seq, (ts, body) in enumerate(_parse_comments(comments)):
        key = (float("inf") if ts is None else
               datetime.strptime(ts, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp())
        lines = body.strip().splitlines()
        first = lines[0].strip() if lines else ""
        exact = _EXACT_PASS_LINE_RE.match(first)
        if exact and tip_sha.startswith(exact.group("sha")) and not _VETO_RE.search(body):
            passes.append((key, seq, first))
        elif _comment_vetoes_tip(body, tip_sha, bead_id):
            vetoes.append(("comment", key, seq, f"bead comment: {first[:120]}"))
    root = Path(reviews_dir or DEFAULT_REVIEWS_DIR)
    file_passes = []
    if root.is_dir():
        for path in sorted(root.glob("REVIEW_*.md")):
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if not mentions_bead(body, bead_id):
                continue
            echoes = [m.group(1).lower() for m in _REVIEWING_RE.finditer(body)]
            if (is_gate_record(path, body) and echoes and all(tip_sha.startswith(e) for e in echoes)
                    and re.search(r"(?m)^Verdict: PASS$", body) and not _VETO_RE.search(body)):
                file_passes.append((mtime, str(path)))
            elif _names_tip(body, tip_sha) and _VETO_RE.search(body):
                vetoes.append(("file", mtime, 0, str(path)))

    def comment_after(c, veto):
        kind, t, seq, _ = veto
        return (c[0], c[1]) > (t, seq) if kind == "comment" else c[0] > t

    def file_after(mtime, veto):
        kind, t, _, _ = veto
        return mtime >= t + 60 if kind == "comment" else mtime > t

    if not passes:
        return False, "no bead comment whose first line is exactly PASS @<tip>"
    if not file_passes:
        return False, f"no review-gate record (Ops/reviews, Verdict: PASS) for this tip ({passes[-1][2]} alone)"
    good_comments = [c for c in passes if all(comment_after(c, v) for v in vetoes)]
    good_files = [f for f in file_passes if all(file_after(f[0], v) for v in vetoes)]
    if not good_comments or not good_files:
        last = max(vetoes, key=lambda v: (v[1], v[2]))
        return False, "FAIL/VOID recorded after the last review-gate PASS for this tip: " + last[3]
    return True, f"{good_comments[-1][2]} + {good_files[-1][1]}"
