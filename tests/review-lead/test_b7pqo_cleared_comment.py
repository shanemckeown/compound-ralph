"""LUCY-b7pqo (Orchestrator23 ruling (a)): review-gate.sh --cleared-comment removes exactly one
bead comment from the comment vetoes, orchestrator only, and fails closed otherwise.

Replays the real AestheticcNext-9kwlw independent r2 gate (fixtures/replay-9kwlw: the four
Ops/reviews files, the clean lead, and the bead's real `bd comments`), whose 01:10 lead-escalation
status comment names the tip beside "Astra FAIL".
"""
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "references" / "review-lead"))
import review_gate as gate  # noqa: E402

FIX = HERE / "fixtures" / "replay-9kwlw"
BEAD = "AestheticcNext-9kwlw"
TIP = "5f45b3c9d2812c0ba70556c3279fbbff8ab3f6db"
LIVE_REVIEWS = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/reviews/"
MTIMES = {  # the real files' mtimes (UTC 2026-10-24 23:58:33, 01:07:54, 00:02:44, 01:07:30)
    "REVIEW_9kwlw_astra-xhigh_r1_2026-10-25.md": 1792886313,
    "REVIEW_9kwlw_astra-xhigh_r2_2026-10-25.md": 1792890474,
    "REVIEW_9kwlw_opus-h2h_r1_2026-10-25.md": 1792886564,
    "REVIEW_9kwlw_opus-h2h_r2_2026-10-25.md": 1792890450,
}
STATUS_TS = "2026-10-25 01:10"
ORCH_ID = "0000aaaa-1111-2222-3333-444455556666"
SUB_ID = "bbbb0000-1111-2222-3333-444455556666"


def comment(ts, body):
    return f"[Encrypted-S] at {ts}\n" + "".join(f"  {ln}\n" for ln in body.splitlines()) + "\n"


def status_body(text):
    hits = [b for ts, b in gate.lbs._parse_comments(text) if ts == STATUS_TS]
    assert len(hits) == 1 and hits[0].startswith(f"needs input: {BEAD} escalated 2 protected item(s)"), hits
    return hits[0]


class ClearedComment(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(dir="/private/tmp"))
        self.reviews = self.tmp / "reviews"
        self.reviews.mkdir()
        self.verdicts = []
        for name, mtime in MTIMES.items():
            dst = self.reviews / name
            shutil.copy(FIX / name, dst)
            os.utime(dst, (mtime, mtime))
            self.verdicts.append(dst)
        self.lead = self.tmp / "lead-round-2.json"
        self.lead.write_text((FIX / "lead-round-2.json").read_text().replace(LIVE_REVIEWS, f"{self.reviews}/"))
        self.out = self.tmp / "lead-out"
        self.out.mkdir()
        (self.out / "review-file.md5").write_text(
            "".join(hashlib.md5(v.read_bytes()).hexdigest() + "\n" for v in self.verdicts))
        # The 01:08 FAIL mirror names the live Ops/reviews file; point it at the copy (the 01:10 status
        # comment names an evidence path, so its body and hash are untouched).
        self.real = (FIX / "comments.txt").read_text().replace(LIVE_REVIEWS, f"{self.reviews}/")
        self.status = status_body(self.real)
        self.prefix = gate.comment_hash(self.status)[:12]
        self.clear_arg = f"{STATUS_TS}:{self.prefix}"
        self.orch_line = comment("2026-10-25 06:30", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg} (status line)")
        self.comments = self.tmp / "comments.txt"
        # Stub fleet-role: ORCHESTRATOR for ORCH_ID only, SUB otherwise.
        self.role = self.tmp / "fleet-role.py"
        self.role.write_text("import sys\nok = sys.argv[1:] == [%r]\nprint('ORCHESTRATOR' if ok else 'SUB')\n"
                             "sys.exit(0 if ok else 1)\n" % ORCH_ID)
        self._saved = (gate.REVIEWS_DIR, gate.FLEET_ROLE, gate.chk.bead_comments_text, gate.time.sleep)
        gate.REVIEWS_DIR, gate.FLEET_ROLE = self.reviews, self.role
        self._env = {k: os.environ.pop(k, None) for k in (*gate.AGENT_ID_VARS, "PATH")}
        os.environ["PATH"] = self._env["PATH"] or ""

    def tearDown(self):
        gate.REVIEWS_DIR, gate.FLEET_ROLE, gate.chk.bead_comments_text, gate.time.sleep = self._saved
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_gate(self, text, *clears, dry=True):
        self.comments.write_text(text)
        argv = [BEAD, TIP, "--lead", str(self.lead), "--out-dir", str(self.out), "--round", "2", "--scope-lock"]
        for v in self.verdicts:
            argv += ["--verdict", str(v)]
        for c in clears:
            argv += ["--cleared-comment", c]
        if dry:
            argv += ["--dry-run", "--comments-file", str(self.comments)]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gate.main(argv)
        return code, out.getvalue() + err.getvalue()

    def assertRefused(self, res, needle):
        code, out = res
        self.assertEqual(code, 1, out)
        self.assertIn("REFUSED", out)
        self.assertIn(needle, out)
        self.assertEqual(sorted(p.name for p in self.reviews.glob("*_gate_*.md")), [])

    # --- the replay -------------------------------------------------------------------------
    def test_without_the_flag_the_status_comment_refuses_and_names_its_id(self):
        self.assertRefused(self.run_gate(self.real), f"bead comment {self.clear_arg}: needs input: {BEAD}")

    def test_flag_plus_orchestrator_line_passes(self):
        code, out = self.run_gate(self.real + self.orch_line, self.clear_arg)
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN, clean", out)
        self.assertIn(f"- CLEARED-COMMENT {self.clear_arg}", out)  # the record
        self.assertIn(f"\nCLEARED-COMMENT {self.clear_arg}", out.split("--- bd comment", 1)[1])  # the PASS comment

    def test_full_prefix_also_works(self):
        full = f"{STATUS_TS}:{gate.comment_hash(self.status)}"
        orch = comment("2026-10-25 06:30", f"ORCHESTRATOR CLEARED COMMENT {full}")
        self.assertEqual(self.run_gate(self.real + orch, full)[0], 0)

    # --- fail closed ------------------------------------------------------------------------
    def test_wrong_prefix_refuses(self):
        bad = f"{STATUS_TS}:{'0' * 12}" if not self.prefix.startswith("0" * 12) else f"{STATUS_TS}:{'1' * 12}"
        orch = comment("2026-10-25 06:30", f"ORCHESTRATOR CLEARED COMMENT {bad}")
        self.assertRefused(self.run_gate(self.real + orch, bad), "0 bead comments at 2026-10-25 01:10")

    def test_short_or_malformed_prefix_refuses(self):
        for arg in (f"{STATUS_TS}:{self.prefix[:11]}", f"{STATUS_TS}:{self.prefix.upper()}", self.prefix,
                    f"2026-10-25T01:10:{self.prefix}"):
            self.assertRefused(self.run_gate(self.real + self.orch_line, arg), "is not 'YYYY-MM-DD HH:MM:")

    def test_edited_body_refuses(self):
        edited = self.real.replace("escalated 2 protected", "escalated 3 protected", 1)
        self.assertNotEqual(edited, self.real)
        self.assertRefused(self.run_gate(edited + self.orch_line, self.clear_arg), "0 bead comments at")

    def test_missing_orchestrator_line_refuses(self):
        self.assertRefused(self.run_gate(self.real, self.clear_arg), "no bead comment whose first line starts")

    def test_orchestrator_line_not_first_line_or_other_prefix_refuses(self):
        for body in (f"note\nORCHESTRATOR CLEARED COMMENT {self.clear_arg}",
                     f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}0",
                     f"ORCHESTRATOR CLEARED {self.clear_arg}"):
            res = self.run_gate(self.real + comment("2026-10-25 06:30", body), self.clear_arg)
            self.assertRefused(res, "no bead comment whose first line starts")

    def test_orchestrator_line_in_the_same_minute_or_before_the_verdicts_refuses(self):
        for ts in (STATUS_TS, "2026-10-25 01:07"):
            orch = comment(ts, f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}")
            self.assertRefused(self.run_gate(self.real + orch, self.clear_arg), "no bead comment whose first line")

    def test_two_matching_comments_in_the_minute_refuse(self):
        twice = self.real + comment(STATUS_TS, self.status) + self.orch_line
        self.assertRefused(self.run_gate(twice, self.clear_arg), "2 bead comments at 2026-10-25 01:10")

    def test_clearing_a_comment_that_is_not_a_veto_refuses(self):
        ts, body = "2026-10-25 06:20", "plain note about the tip 5f45b3c9d2, nothing else"
        arg = f"{ts}:{gate.comment_hash(body)[:12]}"
        text = self.real + comment(ts, body) + comment("2026-10-25 06:30", f"ORCHESTRATOR CLEARED COMMENT {arg}")
        self.assertRefused(self.run_gate(text, arg), "is not a veto of tip 5f45b3c9d2")

    def test_another_veto_comment_at_the_tip_still_refuses(self):
        later = comment("2026-10-25 06:31", f"FAIL @{TIP}\nnew P2 lib/x.ts:1 — something else")
        res = self.run_gate(self.real + self.orch_line + later, self.clear_arg)
        self.assertRefused(res, "bead comment 2026-10-25 06:31:")

    def test_another_veto_in_the_same_minute_still_refuses(self):
        """Scope is the one hashed comment, never the minute."""
        same = comment(STATUS_TS, f"VOID @{TIP[:10]}: superseded")
        res = self.run_gate(self.real + same + self.orch_line, self.clear_arg)
        self.assertRefused(res, f"bead comment {STATUS_TS}:")
        self.assertNotIn(self.clear_arg + ":", res[1].split("not among the inputs (", 1)[1][:40])

    # --- orchestrator only (wqn96 identity rule) ---------------------------------------------
    def test_non_orchestrator_session_refuses(self):
        for env in ({"CLAUDE_CODE_SESSION_ID": SUB_ID}, {"CODEX_THREAD_ID": SUB_ID},
                    {"CLAUDE_CODE_SESSION_ID": ORCH_ID, "CODEX_THREAD_ID": SUB_ID},
                    {"CLAUDE_CODE_SESSION_ID": ""}, {"CLAUDE_CODE_SESSION_ID": "--who"}):
            os.environ.update(env)
            try:
                res = self.run_gate(self.real + self.orch_line, self.clear_arg)
            finally:
                for k in env:
                    os.environ.pop(k, None)
            self.assertRefused(res, "for the orchestrator only")

    def test_orchestrator_session_passes_and_a_broken_checker_refuses(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = ORCH_ID
        self.assertEqual(self.run_gate(self.real + self.orch_line, self.clear_arg)[0], 0)
        gate.FLEET_ROLE = self.tmp / "missing.py"
        self.assertRefused(self.run_gate(self.real + self.orch_line, self.clear_arg), "for the orchestrator only")

    # --- end to end through the lander ------------------------------------------------------
    def test_real_write_lands_and_without_the_gate_pass_the_status_still_holds(self):
        sys.path.insert(0, str(HERE.parent.parent / "skills" / "land-batch" / "bin"))
        import land_batch_safety as lbs
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        bd_log = self.tmp / "bd.log"
        (bin_dir / "bd").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{bd_log}'\n")
        (bin_dir / "bd").chmod(0o755)
        os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        text = self.real + self.orch_line
        gate.chk.bead_comments_text = lambda bead: text
        gate.time.sleep = lambda s: None
        before = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=text)
        self.assertFalse(before[0], before)
        code, out = self.run_gate(text, self.clear_arg, dry=False)
        self.assertEqual(code, 0, out)
        logged = bd_log.read_text().splitlines()
        self.assertEqual(logged[:3], ["comment", BEAD, f"PASS @{TIP}"])
        self.assertIn(f"CLEARED-COMMENT {self.clear_arg}", logged)
        gate_file = next(self.reviews.glob("REVIEW_9kwlw_gate_*.md"))
        self.assertIn(f"- CLEARED-COMMENT {self.clear_arg}", gate_file.read_text())
        landed = text + comment("2026-10-25 06:45", "\n".join(logged[2:]))
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=landed)
        self.assertTrue(ok, ev)
        # The gate file alone (no PASS comment) does not land; the 01:10 comment still holds the tip.
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=text)
        self.assertFalse(ok, ev)
        # A PASS comment older than the status comment does not beat it.
        early = comment("2026-10-25 01:09", "\n".join(logged[2:])) + self.real.split("Comments on", 1)[1].split("\n", 1)[1]
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=early + self.orch_line)
        self.assertFalse(ok, ev)
        self.assertIn("needs input", ev)


if __name__ == "__main__":
    unittest.main()
