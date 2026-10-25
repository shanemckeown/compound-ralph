#!/usr/bin/env python3
"""review_gate.py: the one sanctioned way to record an independent review PASS (LUCY-p4pjf).

  ~/.claude/scripts/review-gate.sh <bead> <full-tip-sha> --lead lead-round-N.json \
      --verdict ~/Documents/Obsidian/Aestheticc/Ops/reviews/REVIEW_<...>.md [--verdict ...] \
      --out-dir "$EVIDENCE_DIR" --round N [--scope-lock] [--cleared-escalation F1 ...] [--dry-run]

Runs review_lead_check.py on the reviewers' files exactly as they are, and only when it prints
BLOCKING=0 ESCALATE=0 writes the two records the lander reads (land-batch independent_pass_for):
  1. Ops/reviews/REVIEW_<bead>_gate_<tip10>_<UTC stamp>.md with a `Verdict: PASS` line;
  2. a bd comment whose first line is exactly `PASS @<tip>`.
Anything else writes nothing and exits 1. It never edits a verdict file, and it refuses when:
  - the tip is not a full 40-hex sha;
  - no --verdict is an Ops/reviews file (an own-gate review in an evidence dir is not
    independent), a verdict is an earlier gate record, or an Ops/reviews verdict does not name
    this bead, or has no `REVIEWING: <branch> @ <sha>` echo, or any echo names another commit;
  - `<out-dir>/review-file.md5` is missing, or lacks the md5 of any Ops/reviews verdict: the lead
    writes it (`md5 -q <file> >> review-file.md5`) when it reads the files, so a reviewer file
    changed after that is caught;
  - --comments-file is given without --dry-run (a real PASS always reads the live bead);
  - an Ops/reviews verdict written after 2026-10-23 12:00 UTC has no ```review-verdict block.
It always waits for the next UTC minute before writing, then re-checks every verdict md5:
bd comment times are minute-resolution and the lander (land_batch_safety.independent_pass_for)
orders the gate's records after every FAIL file and FAIL comment by time.
--dry-run runs every check and prints what it would write.
"""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import review_lead_check as chk  # noqa: E402
sys.path.insert(0, str(HERE.parent.parent / "skills" / "land-batch" / "bin"))
import land_batch_safety as lbs  # noqa: E402  (the lander's own veto rules: one definition)

# Fixed, no env override: an alternate root would let an own-gate file pass as independent.
REVIEWS_DIR = Path("~/Documents/Obsidian/Aestheticc/Ops/reviews").expanduser()
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
# Reviews written from now on are gated only from their structured ```review-verdict block: prose
# parsing never converged against an adversarial reviewer of this gate (11 rounds, 23 Oct). Files
# written before this cutoff (q41ar r10 and the rest of the 22 Oct queue) may still be gated from
# prose; the checker keeps prose tolerance for leads and own-gate triage, which write no PASS.
PROSE_GATE_CUTOFF = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).timestamp()
# The whole revision token is a bare sha: "<tip>^", "<tip>~1" or "<a>..<b>" never match.
# A review withdrawn by its author says so on a line of its own ("VOID: superseded by r11"); the
# word inside a finding ("VOID invoices can still charge") is never a withdrawal (Astra r14, r22).
WITHDRAWAL = re.compile(r"(?mi)^\W*(VOID|VOIDED|REVOKED|WITHDRAWN|RETRACTED)\b(?:\W*[:—–-]|\W*$)")


def withdrawal(body):
    return next((m for m in WITHDRAWAL.finditer(body) if not chk.FINDING.match(body[m.start():].split("\n", 1)[0])),
                None)


REVIEWING = re.compile(r"(?m)^\s*REVIEWING:\s*\S+\s*@\s*([0-9a-fA-F]{7,40})[ \t]*$")
GATE_SIGNATURE = lbs.GATE_SIGNATURE
COUNTS = re.compile(r"^BLOCKING=(\d+) ESCALATE=(\d+)\b.*$", re.M)


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def refuse(msg):
    print(f"review-gate: REFUSED: {msg}", file=sys.stderr)
    return 1


def bd_comment(bead, text):
    env = dict(os.environ)
    if not env.get("BEADS_DIR"):
        for prefix, path in chk.BEADS_DIRS_BY_PREFIX:
            if bead.lower().startswith(prefix):
                env["BEADS_DIR"] = os.path.expanduser(path)
    return subprocess.run(["bd", "comment", bead, text], capture_output=True, text=True, timeout=60, env=env)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bead")
    ap.add_argument("tip")
    ap.add_argument("--lead", required=True)
    ap.add_argument("--verdict", action="append", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--scope-lock", action="store_true")
    ap.add_argument("--cleared-escalation", action="append", default=[], metavar="FINDING_ID")
    ap.add_argument("--comments-file", help="bd comments text to read instead of calling bd (tests)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.comments_file and not a.dry_run:
        return refuse("--comments-file is for --dry-run only; a real PASS reads the live bead comments")
    tip = a.tip.lower()
    if not FULL_SHA.match(tip):
        return refuse(f"tip {a.tip!r} is not a full 40-hex sha")
    verdicts = [Path(v).expanduser().resolve() for v in a.verdict]
    for v in verdicts:
        if not v.is_file():
            return refuse(f"verdict file not found: {v}")
    reviews_root = REVIEWS_DIR.resolve()
    # Directly in Ops/reviews, the one directory the lander scans (no subfolders).
    independent = [v for v in verdicts if v.parent == reviews_root and re.fullmatch(r"REVIEW_.+\.md", v.name)]
    if not independent:
        return refuse(f"no --verdict is an {reviews_root}/REVIEW_*.md file: an own-gate review cannot record a PASS")
    for v in verdicts:
        if lbs.is_gate_record(v, v.read_text(errors="ignore")):
            return refuse(f"{v.name} is a review-gate record, not a reviewer's verdict")

    def newer_fail():
        """Vetoes the gated reviews don't account for, so a stale or hand-picked input set can't
        launder them: any other FAIL review of this tip newer than the OLDEST input (a later input
        never supersedes another reviewer's findings), and any veto comment on the bead newer than
        it that isn't the mirror of an input (a FAIL comment naming the input file); a VOID always
        counts."""
        oldest = min(v.stat().st_mtime for v in independent)
        found = []
        for m, p in lbs.review_file_vetoes(reviews_root, a.bead, tip, exclude=independent):
            body_p = Path(p).read_text(errors="ignore")
            # review_file_vetoes matched it as a review of bead@tip; with an echo, by that echo.
            echoed = bool(lbs._REVIEWING_BRANCH_RE.search(body_p))
            # Only an explicit declaration at the start of a line, never the word inside a finding.
            withdrawn = withdrawal(body_p)
            # A FAIL review OF this tip is never superseded by a later one (the code didn't change):
            # it must be an input, so the lead adjudicates it, unless its author withdrew it.
            # Other vetoes only count when newer than the oldest input.
            if m > oldest or (echoed and not withdrawn):
                found.append(p)
        if a.comments_file:
            text = Path(a.comments_file).read_text()
        else:
            text = chk.bead_comments_text(a.bead)
        for key, first, body in lbs.comment_vetoes(text, tip):
            if key + 60 > oldest and not is_mirror(body):
                found.append(f"bead comment: {first[:100]}")
        return found

    def is_mirror(body):
        """The documented copy of an input review on the bead (aestheticc-review Step 5):
        line 1 exactly `FAIL @<tip>`, line 2 exactly an input file's path, no withdrawal word,
        and every finding line in it present in that file. Anything else is a veto."""
        lines = [ln.strip() for ln in body.strip().splitlines()]
        if len(lines) < 2 or not re.fullmatch(rf"FAIL @{tip[:7]}[0-9a-f]{{0,33}}", lines[0]) \
                or not tip.startswith(lines[0][6:]):
            return False
        src = next((v for v in independent if lines[1] in (str(v), str(Path(v).name))), None)
        if src is None or withdrawal(body) or re.search(r"\bBLOCKING=[1-9]", body):
            return False
        # Matched by severity + first file:line, so a paraphrased copy is still the mirror but a
        # finding at any other location (a new one) is a veto.
        # Every "P<n> <file:line>" in the comment, wherever it sits on a line (Step 5 puts the
        # findings after the summary on one line).
        pair = re.compile(r"\b(P[123])\b\W{0,3}((?=[\w./\[\]-]*[A-Za-z_/])[\w./\[\]-]+:\d+)")
        try:
            canon = chk.reviewer_findings([str(src)])   # the checker's own reading, prose or structured
        except ValueError:
            return False
        have = {m.groups() for ln in canon for m in pair.finditer(ln)}
        for ln in lines[2:]:
            found = [m.groups() for m in pair.finditer(ln)]
            if any(f not in have for f in found) or (chk.finding_shaped(ln) and not found):
                return False
            # What's left once the matched pairs are gone may not hold another finding
            # ("... P2 lib/x.ts:138 — race. Finding (P1): retry charges twice").
            rest = pair.sub(" ", ln)
            if chk.EXPLICIT_LABEL.search(rest) or chk.SEVERITY_TOKEN.search(rest):
                return False
        # Its tallies ("FAIL — 1 P1, 1 P2") must be the input's, or it reports something else.
        try:
            chk.check_summary_counts("mirror", lines[2:], canon)
        except ValueError:
            return False
        return True
    earlier = []
    for v in independent:
        body = v.read_text(errors="ignore")
        try:
            has_block = bool(chk.verdict_blocks(v.name, body))
        except ValueError as e:
            return refuse(str(e))
        if not has_block and v.stat().st_mtime >= PROSE_GATE_CUTOFF:
            return refuse(f"{v.name} has no ```review-verdict block: a review written after "
                          "2026-10-23 12:00 UTC is gated only from its structured verdict")
        withdrawn = withdrawal(body) or re.search(r"\bBLOCKING=[1-9]", body)
        if withdrawn:
            return refuse(f"{v.name} says {withdrawn.group(0)}: a withdrawn or blocking review can't be gated")
        if not lbs.mentions_bead(body, a.bead):
            return refuse(f"{v.name} does not name {a.bead}")
        # The reviewer's own echo of what it reviewed, not any sha in the text (a delta review
        # also names its base commit).
        echo_lines = re.findall(r"(?m)^\s*REVIEWING:.*$", body)
        echoed = [m.group(1).lower() for m in REVIEWING.finditer(body)]
        if not echo_lines:
            return refuse(f"{v.name} has no 'REVIEWING: <branch> @ <sha>' line")
        if len(echoed) != len(echo_lines):
            bad = next(ln for ln in echo_lines if not REVIEWING.match(ln))
            return refuse(f"{v.name} has a REVIEWING line that names no single commit: {bad.strip()[:100]}")
        if len(set(echoed)) > 1:
            return refuse(f"{v.name} echoes more than one commit ({sorted(set(echoed))[:2]}): one review, one commit")
        if not tip.startswith(echoed[0]):
            earlier.append(v)
    # A review of an earlier round's commit may ride along only to have its findings graded (a
    # carried escalation, or "fixed" by a review of the tip); approval needs a review of the tip
    # itself, and an earlier review must predate every review of the tip.
    at_tip = [v for v in independent if v not in earlier]
    if not at_tip:
        return refuse(f"no input review echoes tip {tip[:10]}: a review of an earlier commit can't approve this one")
    late = [v for v in earlier if v.stat().st_mtime >= min(x.stat().st_mtime for x in at_tip)]
    if late:
        return refuse(f"{late[0].name} reviewed another commit and is not older than the review of the tip")
    try:
        lead_items = json.loads(Path(a.lead).read_text()).get("findings") or []
    except (OSError, ValueError, AttributeError):
        return refuse(f"cannot read lead {a.lead}")
    at_tip_paths = {str(v.resolve()) for v in at_tip}
    for f in lead_items:
        if isinstance(f, dict) and f.get("verdict") == "fixed":
            by = str(Path(str(f.get("fixed_by") or "")).expanduser().resolve())
            if by not in at_tip_paths:
                return refuse(f"finding {f.get('id') or '?'} is graded fixed by {f.get('fixed_by')}, which is not a "
                              f"review of tip {tip[:10]} among the inputs")
            src = chk.clean(f.get("source_line", ""))
            raised = [v for v in verdicts if src in chk.reviewer_findings([str(v)])]
            if not raised or any(v not in earlier for v in raised):
                return refuse(f"finding {f.get('id') or '?'} is graded fixed but was raised by a review of tip "
                              f"{tip[:10]} itself (or by no input): only an earlier commit's finding can be fixed")
    sums = {str(v): md5(v) for v in verdicts}
    if newer_fail():
        return refuse(f"a FAIL review of this tip is not among the inputs ({newer_fail()[0]}): "
                      "gate every review of the tip together")
    recorded = Path(a.out_dir) / "review-file.md5"
    if not recorded.is_file():
        return refuse(f"{recorded} missing: the lead records each reviewer file's md5 when it reads it")
    want = set(re.findall(r"\b[0-9a-f]{32}\b", recorded.read_text()))
    for v in independent:
        if sums[str(v)] not in want:
            return refuse(f"{v.name} md5 {sums[str(v)]} is not in {recorded}: the reviewer file changed "
                          "after the lead read it")

    argv_chk = ["--lead", a.lead, "--out-dir", a.out_dir, "--round", str(a.round), "--bead", a.bead]
    for v in verdicts:
        argv_chk += ["--verdict", str(v)]
    if a.scope_lock:
        argv_chk.append("--scope-lock")
    for c in a.cleared_escalation:
        argv_chk += ["--cleared-escalation", c]
    if a.comments_file:
        argv_chk += ["--comments-file", a.comments_file]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = chk.main(argv_chk)
    sys.stdout.write(out.getvalue())
    sys.stderr.write(err.getvalue())
    counts = COUNTS.search(out.getvalue())
    if code != 0 or not counts:
        return refuse("review_lead_check.py refused (above); nothing written")
    if counts.group(1) != "0" or counts.group(2) != "0":
        return refuse(f"checker is not clean ({counts.group(0)}); nothing written")
    line = counts.group(0)
    # Ids and follow-up beads only: the orchestrator's free text stays on the bead, so it can't put
    # a veto word ("FAIL") into the gate's own records.
    cleared = []
    for ln in out.getvalue().splitlines():
        m = re.match(r"^CLEARED (\S+): ", ln)
        if m:
            bead_ids = chk.BEAD_ID.findall(ln)
            cleared.append(f"CLEARED {m.group(1)} ({bead_ids[0] if bead_ids else '?'})")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = a.bead.split("-", 1)[-1]
    review_path = reviews_root / f"REVIEW_{short}_gate_{tip[:10]}_{stamp}.md"
    record = "\n".join([
        f"REVIEWING: {a.bead} @ {tip}",
        "",
        f"# {a.bead} review gate, round {a.round}",
        "",
        f"- Bead: {a.bead}",
        f"- Tip: {tip}",
        f"- Checker: review_lead_check.py via review-gate.sh: {line}",
        *(f"- {c}" for c in cleared),
        "- Reviewer files, read unchanged (md5):",
        *(f"  - {p} {s}" for p, s in sums.items()),
        f"- Lead: {Path(a.lead).resolve()}",
        f"- Lead output: {Path(a.out_dir).resolve()}",
        "",
        "Verdict: PASS",
        "",
    ])
    comment = "\n".join([f"PASS @{tip}", str(review_path), f"review-gate round {a.round}: {line}", *cleared])
    if a.dry_run:
        print(f"review-gate: DRY RUN, clean; would write {review_path}\n--- review file\n{record}"
              f"--- bd comment on {a.bead}\n{comment}")
        return 0
    time.sleep(60 - time.time() % 60 + 1)
    # A reviewer file that changed during the wait invalidates every check above.
    changed = [str(v) for v in verdicts if md5(v) != sums[str(v)]]
    if changed:
        return refuse(f"verdict file changed while waiting: {changed[0]}; nothing written")
    if newer_fail():
        return refuse(f"a FAIL review arrived while waiting ({newer_fail()[0]}); nothing written")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    review_path = reviews_root / f"REVIEW_{short}_gate_{tip[:10]}_{stamp}.md"
    if review_path.exists():
        return refuse(f"{review_path} already exists")
    comment = "\n".join([f"PASS @{tip}", str(review_path), f"review-gate round {a.round}: {line}", *cleared])
    review_path.write_text(record)
    res = bd_comment(a.bead, comment)
    if res.returncode != 0:
        return refuse(f"wrote {review_path} but bd comment failed (no PASS line recorded, so the lander "
                      f"still holds): {(res.stderr or res.stdout).strip()[:200]}")
    print(f"review-gate: PASS recorded for {a.bead} @{tip}\n  {review_path}\n  bd comment: PASS @{tip}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
