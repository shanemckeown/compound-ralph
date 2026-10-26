"""Tests for review_gate.py / scripts/review-gate.sh (LUCY-p4pjf).

Run: python3 -m unittest discover -s ~/.claude/tests/review-lead -v
A fake `bd` on PATH records the comment instead of writing a real bead.
"""
import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "references" / "review-lead"))
import review_gate as gate  # noqa: E402

FIX_Q41 = HERE / "fixtures" / "q41ar-r10"
TIP = "7649f85281ef8012cae27cd56d1a3f6cea301c2c"
BEAD = "AestheticcNext-q41ar"
R10_MTIME = datetime(2026, 10, 22, 21, 14, 30, tzinfo=timezone.utc).timestamp()
CLEARED = ("[Encrypted-S] at 2026-10-23 09:40\n"
           "  ORCHESTRATOR CLEARED F1 (AestheticcNext-e1xgj) @7649f85281: pre-existing race class\n\n")


class _GateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/private/tmp"))
        self.reviews = self.tmp / "reviews"
        self.reviews.mkdir()
        self.verdict = self.reviews / "REVIEW_q41ar_astra-xhigh_r10_2026-10-22.md"
        shutil.copy(FIX_Q41 / "review.md", self.verdict)
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        self.lead = self.tmp / "lead-round-10.json"
        shutil.copy(FIX_Q41 / "lead-round-10.json", self.lead)
        self.out = self.tmp / "lead-out"
        self.out.mkdir()
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        self.comments = self.tmp / "comments.txt"
        self.comments.write_text(CLEARED)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        self.bd_log = self.tmp / "bd.log"
        (bin_dir / "bd").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{self.bd_log}'\n")
        (bin_dir / "bd").chmod(0o755)
        self._env = {k: os.environ.get(k) for k in ("PATH",)}
        os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        self._reviews_dir = gate.REVIEWS_DIR
        gate.REVIEWS_DIR = self.reviews
        # A real (non-dry) run reads live bead comments; the reader is mocked, never --comments-file.
        self._reader = gate.chk.bead_comments_text
        self._sleep = gate.time.sleep
        gate.time.sleep = lambda s: None  # the gate always waits for the next minute
        gate.chk.bead_comments_text = lambda bead: self.comments.read_text()

    def tearDown(self):
        gate.REVIEWS_DIR = self._reviews_dir
        gate.chk.bead_comments_text = self._reader
        gate.time.sleep = self._sleep
        os.environ["PATH"] = self._env["PATH"]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_structured(self):
        """The r10 review plus its structured block (a post-cutoff review must carry one)."""
        finding = json.loads((FIX_Q41 / "lead-round-10.json").read_text())["findings"][0]["source_line"]
        self.verdict.write_text(self.verdict.read_text() + "\n```review-verdict\n" + json.dumps(
            {"verdict": "FAIL", "findings": [{"severity": "P2", "file_line": finding.split()[1], "text": finding[3:]}]}) + "\n```\n")
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")

    def run_gate(self, *extra, verdicts=None, tip=TIP, cleared=("F1",)):
        argv = [BEAD, tip, "--lead", str(self.lead), "--out-dir", str(self.out), "--round", "10"]
        for v in verdicts or [self.verdict]:
            argv += ["--verdict", str(v)]
        for c in cleared:
            argv += ["--cleared-escalation", c]
        argv += list(extra)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gate.main(argv)
        return code, out.getvalue() + err.getvalue()

    def gate_files(self):
        return sorted(p.name for p in self.reviews.glob("REVIEW_q41ar_gate_*.md"))


class ReviewGate(_GateCase):
    def test_clean_checker_writes_review_file_and_exact_pass_line(self):
        code, out = self.run_gate()
        self.assertEqual(code, 0, out)
        files = self.gate_files()
        self.assertEqual(len(files), 1, out)
        body = (self.reviews / files[0]).read_text()
        self.assertIn(f"REVIEWING: {BEAD} @ {TIP}", body)
        self.assertIn("\nVerdict: PASS\n", body)
        self.assertIn("CLEARED F1", body)
        logged = self.bd_log.read_text().splitlines()
        self.assertEqual(logged[:2], ["comment", BEAD])
        self.assertEqual(logged[2], f"PASS @{TIP}")  # the comment's first line, exactly

    def test_reviewer_file_is_never_modified(self):
        before = self.verdict.read_bytes()
        self.run_gate()
        self.assertEqual(self.verdict.read_bytes(), before)

    def test_lands_through_land_batch_independent_pass(self):
        """End to end: what the gate writes is what discovery accepts."""
        self.assertEqual(self.run_gate()[0], 0)
        sys.path.insert(0, str(HERE.parent.parent / "skills" / "land-batch" / "bin"))
        import land_batch_safety as lbs
        lines = self.bd_log.read_text().splitlines()[2:]
        comment = "[Encrypted-S] at 2026-10-23 09:45\n" + "".join(f"  {ln}\n" for ln in lines)
        real = (FIX_Q41 / "review.md").read_text()  # the raw FAIL file is in the same dir
        self.assertIn("Verdict: FAIL", real)
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=comment)
        self.assertTrue(ok, ev)

    def test_dry_run_writes_nothing(self):
        code, out = self.run_gate("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN, clean", out)
        self.assertEqual(self.gate_files(), [])
        self.assertFalse(self.bd_log.exists())

    def test_unclean_checker_writes_nothing(self):
        code, out = self.run_gate(cleared=())
        self.assertEqual(code, 1, out)
        self.assertIn("not clean (BLOCKING=0 ESCALATE=1", out)
        self.assertEqual(self.gate_files(), [])
        self.assertFalse(self.bd_log.exists())

    def test_own_gate_verdict_outside_ops_reviews_refused(self):
        own = self.tmp / "evidence" / "astra-verdict-round-10.log"
        own.parent.mkdir()
        shutil.copy(self.verdict, own)
        code, out = self.run_gate(verdicts=[own])
        self.assertEqual(code, 1, out)
        self.assertIn("own-gate review cannot record a PASS", out)

    def test_short_or_other_tip_refused(self):
        code, out = self.run_gate(tip=TIP[:10])
        self.assertEqual(code, 1, out)
        self.assertIn("not a full 40-hex sha", out)
        code, out = self.run_gate(tip="d60331cb15581e5dc1ff7f6551f050e10e6c5550")
        self.assertEqual(code, 1, out)
        self.assertIn("a review of an earlier commit can't approve", out)

    def test_changed_reviewer_file_refused(self):
        (self.out / "review-file.md5").write_text("0" * 32 + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("changed after the lead read it", out)
        self.assertEqual(self.gate_files(), [])


if __name__ == "__main__":
    unittest.main()


class ReviewGateAstraR1(_GateCase):
    """Astra high review of this gate, 23 Oct: four P1s and a P2, each reproduced."""

    def test_comments_file_only_with_dry_run(self):
        code, out = self.run_gate("--comments-file", str(self.comments))
        self.assertEqual(code, 1, out)
        self.assertIn("--dry-run only", out)
        code, out = self.run_gate("--comments-file", str(self.comments), "--dry-run")
        self.assertEqual(code, 0, out)

    def test_earlier_gate_record_is_not_a_reviewer_verdict(self):
        self.assertEqual(self.run_gate()[0], 0)
        record = self.reviews / self.gate_files()[0]
        (self.out / "review-file.md5").write_text(hashlib.md5(record.read_bytes()).hexdigest() + "\n")
        empty = self.tmp / "empty-lead.json"
        empty.write_text(json.dumps({"bead": BEAD, "scope_lock": False, "premise": {}, "findings": []}))
        self.lead = empty
        code, out = self.run_gate(verdicts=[record], cleared=())
        self.assertEqual(code, 1, out)
        self.assertIn("is a review-gate record", out)

    def test_conflicting_echo_in_one_file_refused(self):
        other = "d60331cb15581e5dc1ff7f6551f050e10e6c5550"
        self.verdict.write_text(self.verdict.read_text() + f"\nREVIEWING: goal/x @ {other}\nPASS\n")
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("one review, one commit", out)

    def test_missing_md5_record_refused(self):
        (self.out / "review-file.md5").unlink()
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("review-file.md5 missing", out)
        self.assertEqual(self.gate_files(), [])

    def test_fresh_verdict_waits_for_the_next_minute(self):
        """bd comments are minute-resolution: the PASS must sort after a FAIL file written this minute."""
        self.make_structured()
        os.utime(self.verdict, None)
        slept = []
        real_sleep = gate.time.sleep
        gate.time.sleep = lambda s: slept.append(s)
        try:
            code, out = self.run_gate()
        finally:
            gate.time.sleep = real_sleep
        # the clearance comment is dated 09:40 on 23 Oct, before a verdict touched now: refused first
        if code == 0:
            self.assertTrue(slept and 0 < slept[0] <= 61, slept)
        else:
            self.assertIn("dated after the verdict files", out)
            nxt = datetime.fromtimestamp(time.time() + 60, timezone.utc).strftime("%Y-%m-%d %H:%M")
            self.comments.write_text(CLEARED.replace("2026-10-23 09:40", nxt))
            gate.time.sleep = lambda s: slept.append(s)
            try:
                code, out = self.run_gate()
            finally:
                gate.time.sleep = real_sleep
            self.assertEqual(code, 0, out)
            self.assertTrue(slept and 0 < slept[0] <= 61, slept)


    def test_verdict_changed_during_the_wait_refused(self):
        """Astra r2: the reviewer file flips during the next-minute wait."""
        self.make_structured()
        os.utime(self.verdict, None)
        nxt = datetime.fromtimestamp(time.time() + 60, timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.comments.write_text(CLEARED.replace("2026-10-23 09:40", nxt))
        real_sleep = gate.time.sleep
        gate.time.sleep = lambda s: self.verdict.write_text(self.verdict.read_text() + "\nP1 lib/x.ts:1 — late\n")
        try:
            code, out = self.run_gate()
        finally:
            gate.time.sleep = real_sleep
        self.assertEqual(code, 1, out)
        self.assertIn("changed while waiting", out)
        self.assertEqual(self.gate_files(), [])
        self.assertFalse(self.bd_log.exists())

    def test_newer_fail_review_for_the_tip_blocks_regating_an_older_one(self):
        """Astra r4: re-gating an old review laundered a newer FAIL into a fresh approval."""
        newer = self.reviews / "REVIEW_q41ar_astra-xhigh_r11_2026-10-23.md"
        newer.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nAestheticcNext-q41ar\nFAIL\n"
                         "P1 lib/pay.ts:9 — double charge — fix\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("not among the inputs", out)
        self.assertEqual(self.gate_files(), [])

    def test_void_comment_after_the_review_blocks_regating(self):
        """Astra r5: PASS @tip VOID on the bead, then the old review re-gated."""
        self.comments.write_text(CLEARED + "[Encrypted-S] at 2026-10-23 10:00\n"
                                 f"  PASS @{TIP} VOID: wrong brief\n\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("bead comment", out)

    def test_mirror_fail_comment_of_the_input_review_does_not_block(self):
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n"
                                 f"  FAIL @{TIP}\n  {self.verdict}\n  ASTRA: FAIL — 0 P1, 1 P2\n\n" + CLEARED)
        code, out = self.run_gate()
        self.assertEqual(code, 0, out)

    def test_hand_picked_inputs_cannot_hide_a_fail_between_them(self):
        """Astra r5: PASS at T1 and T3 selected, an unselected FAIL at T2."""
        t1 = self.reviews / "REVIEW_q41ar_sonnet_r10_2026-10-22.md"
        t1.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nAestheticcNext-q41ar\nPASS\n")
        os.utime(t1, (R10_MTIME - 600, R10_MTIME - 600))
        t2 = self.reviews / "REVIEW_q41ar_opus_r10_2026-10-22.md"
        t2.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nAestheticcNext-q41ar\nFAIL\nP1 lib/pay.ts:9 — x — y\n")
        os.utime(t2, (R10_MTIME - 300, R10_MTIME - 300))
        (self.out / "review-file.md5").write_text(
            "".join(hashlib.md5(f.read_bytes()).hexdigest() + "\n" for f in (t1, self.verdict)))
        code, out = self.run_gate(verdicts=[t1, self.verdict])
        self.assertEqual(code, 1, out)
        self.assertIn("not among the inputs", out)


class ReviewGateAstraR6(_GateCase):
    def mirror(self, *extra):
        return ("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(f"  {ln}\n" for ln in
                (f"FAIL @{TIP}", str(self.verdict), "ASTRA: FAIL — 0 P1, 1 P2", *extra)) + "\n" + CLEARED)

    def test_paraphrased_mirror_at_the_same_location_does_not_block(self):
        self.comments.write_text(self.mirror("P2 lib/concierge/erase-client-meta-concierge.ts:138 (Astra): race"))
        self.assertEqual(self.run_gate()[0], 0)

    def test_withdrawal_or_new_finding_in_a_mirror_shaped_comment_blocks(self):
        for extra in ("REVOKED: wrong brief", "WITHDRAWN", "P1 lib/pay.ts:9 — missed charge path — fix"):
            self.comments.write_text(self.mirror(extra))
            code, out = self.run_gate()
            self.assertEqual(code, 1, extra)
            self.assertIn("bead comment", out)

    def test_short_bead_id_fail_review_is_a_veto(self):
        newer = self.reviews / "REVIEW_q41ar_opus_r11_2026-10-23.md"
        newer.write_text(f"REVIEWING: goal/q41ar @ {TIP}\nFAIL\nP1 lib/pay.ts:9 — x — y\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)


class ReviewGateAstraR7(_GateCase):
    def test_documented_one_line_mirror_does_not_block(self):
        line = ("ASTRA XHIGH REVIEW 2026-10-22 (tip 7649f85281): FAIL — 0 P1, 1 P2, 0 P3. "
                "P2 lib/concierge/erase-client-meta-concierge.ts:138 — race")
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(
            f"  {ln}\n" for ln in (f"FAIL @{TIP}", str(self.verdict), line)) + "\n" + CLEARED)
        code, out = self.run_gate()
        self.assertEqual(code, 0, out)

    def test_one_line_mirror_with_a_new_location_blocks(self):
        line = "ASTRA: FAIL — 1 P1, 1 P2. P2 lib/concierge/erase-client-meta-concierge.ts:138 — race; P1 lib/pay.ts:9 — charge"
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(
            f"  {ln}\n" for ln in (f"FAIL @{TIP}", str(self.verdict), line)) + "\n" + CLEARED)
        self.assertEqual(self.run_gate()[0], 1)

    def test_withdrawn_input_review_refused(self):
        self.verdict.write_text(self.verdict.read_text() + "\nWITHDRAWN: wrong review brief\n")
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("withdrawn or blocking review", out)


class ReviewGateAstraR8(_GateCase):
    def test_unparseable_second_reviewing_line_refused(self):
        self.verdict.write_text(self.verdict.read_text() + "\nREVIEWING: goal/another (working tree) @ d60331cb15\nPASS\n")
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("names no single commit", out)


class ReviewGateAstraR9(_GateCase):
    def test_parent_revision_echo_refused(self):
        self.verdict.write_text(self.verdict.read_text().replace(f"@ {TIP}", f"@ {TIP}^", 1))
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)

    def test_mirror_reporting_an_extra_p1_by_tally_blocks(self):
        line = "ASTRA: FAIL — 1 P1, 1 P2, 0 P3. P2 lib/concierge/erase-client-meta-concierge.ts:138 — race"
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(
            f"  {ln}\n" for ln in (f"FAIL @{TIP}", str(self.verdict), line)) + "\n" + CLEARED)
        self.assertEqual(self.run_gate()[0], 1)


class ReviewGateAstraR10(_GateCase):
    def test_echo_with_trailing_words_refused(self):
        self.verdict.write_text(self.verdict.read_text().replace(f"@ {TIP}", f"@ {TIP} (working tree includes uncommitted fixes)", 1))
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        self.assertEqual(self.run_gate()[0], 1)

    def test_mirror_of_a_structured_only_review_does_not_block(self):
        finding = json.loads((FIX_Q41 / "lead-round-10.json").read_text())["findings"][0]["source_line"]
        self.verdict.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nAestheticcNext-q41ar\n"
                                '```review-verdict\n' + json.dumps({"verdict": "FAIL", "findings": [
                                    {"severity": "P2", "file_line": finding.split()[1], "text": finding[3:]}]}) + "\n```\n")
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        lead = json.loads(self.lead.read_text())
        lead["findings"][0]["source_line"] = finding
        self.lead.write_text(json.dumps(lead))
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(
            f"  {ln}\n" for ln in (f"FAIL @{TIP}", str(self.verdict), f"ASTRA: FAIL — 0 P1, 1 P2. {finding}")) + "\n" + CLEARED)
        code, out = self.run_gate()
        self.assertEqual(code, 0, out)


class ReviewGateAstraR11(_GateCase):
    def test_new_prose_only_review_cannot_be_gated(self):
        os.utime(self.verdict, None)  # written now, after the cutoff
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("no ```review-verdict block", out)

    def test_mirror_with_a_blocking_count_is_a_veto(self):
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(
            f"  {ln}\n" for ln in (f"FAIL @{TIP}", str(self.verdict), "ASTRA: FAIL — 0 P1, 1 P2",
                                   "lead: BLOCKING=1 ESCALATE=0")) + "\n" + CLEARED)
        self.assertEqual(self.run_gate()[0], 1)


class ReviewGateAstraR12(_GateCase):
    def test_older_fail_review_of_the_same_tip_must_be_an_input(self):
        older = self.reviews / "REVIEW_q41ar_opus_r10_2026-10-22.md"
        older.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nFAIL\nP1 lib/pay.ts:9 — charge — fix\n")
        os.utime(older, (R10_MTIME - 3600, R10_MTIME - 3600))
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("not among the inputs", out)

    def test_blocking_count_inside_an_input_refused(self):
        self.verdict.write_text(self.verdict.read_text() + "\nBLOCKING=1 ESCALATE=0\n")
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)


class ReviewGateAstraR13(_GateCase):
    def test_input_in_a_subfolder_refused(self):
        nested = self.reviews / "round-13" / self.verdict.name
        nested.parent.mkdir()
        shutil.copy(self.verdict, nested)
        os.utime(nested, (R10_MTIME, R10_MTIME))
        code, out = self.run_gate(verdicts=[nested])
        self.assertEqual(code, 1, out)

    def test_old_withdrawn_review_of_the_tip_does_not_block(self):
        old = self.reviews / "REVIEW_q41ar_opus_r10_2026-10-22.md"
        old.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nFAIL\nP1 lib/pay.ts:9 — x — y\nWITHDRAWN: wrong brief\n")
        os.utime(old, (R10_MTIME - 3600, R10_MTIME - 3600))
        self.assertEqual(self.run_gate()[0], 0)


class ReviewGateAstraR14(_GateCase):
    def test_void_inside_a_finding_is_not_a_withdrawal(self):
        old = self.reviews / "REVIEW_q41ar_opus_r10_2026-10-22.md"
        old.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nFAIL\nP1 lib/pay.ts:9 — VOID invoices can still charge — fix\n")
        os.utime(old, (R10_MTIME - 3600, R10_MTIME - 3600))
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)
        self.assertIn("not among the inputs", out)

    def test_input_not_named_review_md_refused(self):
        log = self.reviews / "astra-review.log"
        shutil.copy(self.verdict, log)
        os.utime(log, (R10_MTIME, R10_MTIME))
        code, out = self.run_gate(verdicts=[log])
        self.assertEqual(code, 1, out)


class ReviewGateFixedLater(_GateCase):
    """Orchestrator20, 23 Oct gap (2) at the gate: an earlier round's review may ride along only to be
    graded; approval still needs a review of the tip, and fixed_by must be one."""

    def add_r9(self, mtime=R10_MTIME - 3600):
        r9 = self.reviews / "REVIEW_q41ar_astra-xhigh_r9_2026-10-22.md"
        shutil.copy(HERE / "fixtures" / "q41ar-r9" / "review.md", r9)
        os.utime(r9, (mtime, mtime))
        body = self.verdict.read_text().replace(
            "VERIFIED OK\n\n", "VERIFIED OK\n\n- r9 P3 lib/concierge/erase-client-meta-concierge.ts:37 closed: "
            "the IG cell deletes the state rows (10/10)\n", 1)
        # LUCY-e3opf: only the block's prior array grades the r9 finding fixed; the prose line above doesn't.
        # The tip review is read strictly (G2): its finding carries file_line.
        p2 = next(ln for ln in body.splitlines() if ln.startswith("P2 "))
        body += "```review-verdict\n" + json.dumps({
            "verdict": "FAIL",
            "findings": [{"severity": "P2", "file_line": gate.chk.LEADING_LOC.match(p2[3:]).group(1), "text": p2[3:]}],
            "prior": [{"ref": "r9 P3 lib/concierge/erase-client-meta-concierge.ts:37", "status": "closed",
                       "evidence": "the IG cell deletes the state rows (10/10)"}]}) + "\n```\n"
        self.verdict.write_text(body)
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text("".join(
            hashlib.md5(p.read_bytes()).hexdigest() + "\n" for p in (self.verdict, r9)))
        return r9

    def grade_r9(self, r9, p2_verdict, fixed_by):
        lead = json.loads(self.lead.read_text())
        src = gate.chk.reviewer_findings([str(r9)])
        lead["findings"].append({"id": "F8", "source_line": src[0], "verdict": "consider", "category": "other",
                                 "reason": "pre-existing change-note wording, owned elsewhere",
                                 "pre_existing": True,
                                 "follow_up": "AestheticcNext-hgyyd change note"} if p2_verdict == "consider"
                                else {"id": "F8", "source_line": src[0], "verdict": p2_verdict})
        note = next(ln for ln in r9.read_text().splitlines() if "my own check" in ln)
        lead["verified_notes"] = [{"line": note, "reason": "author's trace of the listed P2, cites the dialog copy"}]
        lead["findings"].append({"id": "F9", "source_line": src[1], "verdict": "fixed",
                                 "fixed_by": str(fixed_by), "category": "patient_data_exposure"})
        self.lead.write_text(json.dumps(lead))

    def test_r9_finding_fixed_by_r10_gates_clean(self):
        r9 = self.add_r9()
        self.grade_r9(r9, "consider", self.verdict)
        self.comments.write_text(CLEARED + "[Encrypted-S] at 2026-10-23 09:41\n"
                                 "  ORCHESTRATOR CLEARED F8 (AestheticcNext-hgyyd) @7649f85281: change note\n\n")
        code, out = self.run_gate("--dry-run", verdicts=[self.verdict, r9], cleared=("F1", "F8"))
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)

    def test_fixed_by_the_earlier_review_refused(self):
        r9 = self.add_r9()
        self.grade_r9(r9, "consider", r9)
        code, out = self.run_gate("--dry-run", verdicts=[self.verdict, r9])
        self.assertEqual(code, 1, out)

    def test_earlier_review_newer_than_the_tip_review_refused(self):
        r9 = self.add_r9(mtime=R10_MTIME + 60)
        self.grade_r9(r9, "consider", self.verdict)
        code, out = self.run_gate("--dry-run", verdicts=[self.verdict, r9])
        self.assertEqual(code, 1, out)
        self.assertIn("not older than the review of the tip", out)

    def test_only_earlier_reviews_refused(self):
        r9 = self.add_r9()
        code, out = self.run_gate("--dry-run", verdicts=[r9])
        self.assertEqual(code, 1, out)
        self.assertIn("can't approve", out)

    def test_fixed_finding_raised_at_the_tip_refused(self):
        r9 = self.add_r9()
        self.grade_r9(r9, "consider", self.verdict)
        lead = json.loads(self.lead.read_text())
        f1 = next(f for f in lead["findings"] if f["id"] == "F1")
        f1.update({"verdict": "fixed", "fixed_by": str(self.verdict)})
        self.lead.write_text(json.dumps(lead))
        code, out = self.run_gate("--dry-run", verdicts=[self.verdict, r9], cleared=("F8",))
        self.assertEqual(code, 1, out)

    def test_fixed_finding_raised_by_a_non_independent_tip_input_refused(self):
        r9 = self.add_r9()
        self.grade_r9(r9, "consider", self.verdict)
        src = gate.chk.reviewer_findings([str(r9)])[1]
        own = self.tmp / "own-gate-r10.md"
        own.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nFAIL\n{src}\n")
        self.comments.write_text(CLEARED + "[Encrypted-S] at 2026-10-23 09:41\n"
                                 "  ORCHESTRATOR CLEARED F8 (AestheticcNext-hgyyd) @7649f85281: change note\n\n")
        code, out = self.run_gate("--dry-run", verdicts=[self.verdict, r9, own], cleared=("F1", "F8"))
        self.assertEqual(code, 1, out)


class ReviewGateAstraR22(_GateCase):
    def test_void_inside_an_input_finding_is_not_a_withdrawal(self):
        body = self.verdict.read_text().replace(
            "VERIFIED OK\n", "VERIFIED OK\n\n- VOID invoices are out of scope for this delta (no invoice code touched)\n", 1)
        self.verdict.write_text(body)
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate("--dry-run")
        self.assertEqual(code, 0, out)

    def test_declared_withdrawal_of_an_input_refused(self):
        self.verdict.write_text("VOID: superseded by r11\n" + self.verdict.read_text())
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        code, out = self.run_gate("--dry-run")
        self.assertEqual(code, 1, out)



class ReviewGateAstraR25(_GateCase):
    def test_extra_labelled_finding_after_a_matched_pair_blocks(self):
        line = ("ASTRA XHIGH REVIEW 2026-10-22 (tip 7649f85281): FAIL — 0 P1, 1 P2, 0 P3. "
                "P2 lib/concierge/erase-client-meta-concierge.ts:138 — race. Finding (P1): retry charges twice")
        self.comments.write_text("[Encrypted-S] at 2026-10-22 21:14\n" + "".join(
            f"  {ln}\n" for ln in (f"FAIL @{TIP}", str(self.verdict), line)) + "\n" + CLEARED)
        code, out = self.run_gate()
        self.assertEqual(code, 1, out)


class OwnGateEarlierInputs(_GateCase):
    """LUCY-shb7s: own-gate seat files in the /goal evidence dir count as earlier-finding inputs only. An
    independent Ops/reviews seat at the tip that closes their refs gates; they never approve on their own."""

    SHA2 = "2222222222222222222222222222222222222222"
    SHA3 = "3333333333333333333333333333333333333333"
    F2 = "P2 lib/pay.ts:37 — reception's card retry charges the client twice — guard the retry"
    F3 = "P2 lib/x.ts:12 — the receipt shows the wrong total — recompute it"

    def own_gate(self, name, sha, finding, mtime):
        ev = self.tmp / "evidence"
        ev.mkdir(exist_ok=True)
        p = ev / name
        p.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {sha}\nFAIL\n{finding}\nVERIFIED OK\n- tests\n")
        os.utime(p, (mtime, mtime))
        return p

    def setup_history(self, r7_status=("closed", "closed")):
        r2 = self.own_gate("REVIEW_q41ar_astra_r2_x.md", self.SHA2, self.F2, R10_MTIME - 7200)
        r3 = self.own_gate("REVIEW_q41ar_astra_r3_x.md", self.SHA3, self.F3, R10_MTIME - 3600)
        self.verdict.unlink()
        self.verdict = self.reviews / "REVIEW_q41ar_astra-high_r7_2026-10-26.md"
        prior = [{"ref": ref, "status": st, "evidence": "pg test drives the path at this tip, green"}
                 for ref, st in zip(("r2 P2 lib/pay.ts:37", "r3 P2 lib/x.ts:12"), r7_status)]
        self.verdict.write_text(
            f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nPASS\nVERIFIED OK\n- both earlier findings re-checked\n"
            "UNVERIFIED\n- live\n```review-verdict\n"
            + json.dumps({"verdict": "PASS", "findings": [], "prior": prior}) + "\n```\n")
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        self.lead.write_text(json.dumps({
            "bead": BEAD, "round": 7, "scope_lock": False,
            "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
            "findings": [{"id": f"F{i}", "source_line": src, "verdict": "fixed", "fixed_by": str(self.verdict),
                          "category": "other"} for i, src in ((1, self.F2), (2, self.F3))]}))
        self.comments.write_text("")
        return r2, r3

    def test_mixed_history_closed_by_independent_seat_passes(self):
        r2, r3 = self.setup_history()
        code, out = self.run_gate(verdicts=[r2, r3, self.verdict], cleared=())
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=2", out)
        self.assertEqual(self.bd_log.read_text().splitlines()[2], f"PASS @{TIP}")

    def test_own_gate_only_history_refused(self):
        r2, r3 = self.setup_history()
        code, out = self.run_gate("--dry-run", verdicts=[r2, r3], cleared=())
        self.assertEqual(code, 1, out)
        self.assertIn("no --verdict is an", out)
        self.assertIn("REVIEW_*.md file", out)

    def test_independent_seat_leaving_an_own_gate_ref_open_blocks(self):
        r2, r3 = self.setup_history(r7_status=("closed", "open"))
        lead = json.loads(self.lead.read_text())
        lead["findings"][1] = {"id": "F2", "source_line": self.F3, "verdict": "act_on", "category": "other",
                               "clinic_scenario": "reception prints the receipt at checkout and the total is wrong",
                               "ordinary_use": "one receptionist, one checkout",
                               "demonstrated": "the r7 seat keeps r3 P2 lib/x.ts:12 open at this tip"}
        self.lead.write_text(json.dumps(lead))
        code, out = self.run_gate("--dry-run", verdicts=[r2, r3, self.verdict], cleared=())
        self.assertEqual(code, 1, out)
        self.assertIn("checker is not clean (BLOCKING=1", out)
        self.assertNotIn("DRY RUN, clean", out)
        self.assertEqual(self.gate_files(), [])

    def test_own_gate_seat_alone_never_passes(self):
        # One own-gate PASS of the tip itself, no findings: still not an independent review.
        own = self.tmp / "evidence" / "claude-seat1-round-7.md"
        own.parent.mkdir(exist_ok=True)
        own.write_text(f"REVIEWING: goal/aestheticcnext-q41ar @ {TIP}\nPASS\nVERIFIED OK\n- tests\nUNVERIFIED\n- live\n"
                       "```review-verdict\n" + json.dumps({"verdict": "PASS", "findings": [], "prior": []}) + "\n```\n")
        os.utime(own, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(own.read_bytes()).hexdigest() + "\n")
        lead = json.loads(self.lead.read_text())
        lead["findings"] = []
        self.lead.write_text(json.dumps(lead))
        code, out = self.run_gate(verdicts=[own], cleared=())
        self.assertEqual(code, 1, out)
        self.assertIn("own-gate review cannot record a PASS", out)
        self.assertEqual(self.gate_files(), [])
        self.assertFalse(self.bd_log.exists())

    def test_own_gate_seat_at_the_tip_cannot_be_graded_fixed(self):
        # An own-gate file echoing the tip is not an earlier input: a finding it raised can't be fixed at the tip.
        r2, r3 = self.setup_history()
        r3.write_text(r3.read_text().replace(self.SHA3, TIP))
        os.utime(r3, (R10_MTIME - 3600, R10_MTIME - 3600))
        code, out = self.run_gate("--dry-run", verdicts=[r2, r3, self.verdict], cleared=())
        self.assertEqual(code, 1, out)
        self.assertIn("raised by a review of tip", out)
