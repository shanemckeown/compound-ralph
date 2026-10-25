"""LUCY-b7pqo (Orchestrator23 ruling (a)): review-gate.sh --cleared-comment removes exactly one
bead comment from the comment vetoes, orchestrator only, and fails closed otherwise.

Replays the real AestheticcNext-9kwlw independent r2 gate (fixtures/replay-9kwlw: the four
Ops/reviews files, the clean lead, and the bead's real `bd comments`), whose 01:10 lead-escalation
status comment names the tip beside "Astra FAIL".
"""
import hashlib
import io
import json
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


def comment(ts, body, cid=None):
    """One comment as `bd comments --json` returns it: the text is exactly as written."""
    return [{"id": cid or hashlib.sha1(f"{ts}{body}".encode()).hexdigest(), "issue_id": BEAD,
             "author": "Encrypted-S", "text": body, "created_at": ts.replace(" ", "T") + ":07Z"}]


def as_text(records):
    """The same comments as `bd comments` prints them, for the lander."""
    return f"Comments on {BEAD}:\n\n" + "".join(gate.render(r["created_at"][:16].replace("T", " "), r["text"])
                                                for r in records)


def status_body(records):
    hits = [r["text"] for r in records if r["created_at"].startswith(STATUS_TS.replace(" ", "T"))]
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
        real_text = (FIX / "comments.txt").read_text().replace(LIVE_REVIEWS, f"{self.reviews}/")
        self.real = [c for i, (_, ts, text) in enumerate(gate.snapshot_from_text(real_text))
                     for c in comment(ts, text, cid=f"real-{i}")]
        self.status = status_body(self.real)
        self.prefix = gate.comment_hash(self.status)[:12]
        self.clear_arg = f"{STATUS_TS}:{self.prefix}"
        self.orch_line = comment("2026-10-25 06:30", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg} (status line)")
        self.comments = self.tmp / "comments.json"
        # Stub fleet-role: ORCHESTRATOR for ORCH_ID only, SUB otherwise.
        self.role = self.tmp / "fleet-role.py"
        self.role.write_text("import sys\nok = sys.argv[1:] == [%r]\nprint('ORCHESTRATOR' if ok else 'SUB')\n"
                             "sys.exit(0 if ok else 1)\n" % ORCH_ID)
        self._saved = (gate.REVIEWS_DIR, gate.FLEET_ROLE, gate.bead_comments_json, gate.time.sleep)
        gate.REVIEWS_DIR, gate.FLEET_ROLE = self.reviews, self.role
        self._env = {k: os.environ.pop(k, None) for k in (*gate.AGENT_ID_VARS, "PATH")}
        os.environ["PATH"] = self._env["PATH"] or ""

    def tearDown(self):
        gate.REVIEWS_DIR, gate.FLEET_ROLE, gate.bead_comments_json, gate.time.sleep = self._saved
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_gate(self, records, *clears, dry=True):
        self.comments.write_text(json.dumps(records))
        argv = [BEAD, TIP, "--lead", str(self.lead), "--out-dir", str(self.out), "--round", "2", "--scope-lock"]
        for v in self.verdicts:
            argv += ["--verdict", str(v)]
        for c in clears:
            argv += ["--cleared-comment", c]
        if dry:
            argv += ["--dry-run", "--comments-json-file", str(self.comments)]
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
        edited = json.loads(json.dumps(self.real).replace("escalated 2 protected", "escalated 3 protected", 1))
        self.assertNotEqual(edited, self.real)
        self.assertRefused(self.run_gate(edited + self.orch_line, self.clear_arg), "0 bead comments at")

    def test_whitespace_or_crlf_edit_refuses(self):
        """The hash is of the text exactly as bd stores it: nothing is stripped."""
        for edit in (lambda t: t + " ", lambda t: t + "\n", lambda t: t + "\r\n", lambda t: " " + t,
                     lambda t: t.replace(" — ", "\r\n— ", 1)):
            edited = [dict(r, text=edit(r["text"])) if r["text"] == self.status else r for r in self.real]
            self.assertNotEqual(edited, self.real)
            self.assertRefused(self.run_gate(edited + self.orch_line, self.clear_arg), "0 bead comments at")

    def test_clearance_never_reads_the_stripped_text_fixture(self):
        self.comments.write_text("")
        argv = [BEAD, TIP, "--lead", str(self.lead), "--out-dir", str(self.out), "--round", "2",
                "--cleared-comment", self.clear_arg, "--dry-run", "--comments-file", str(self.comments)]
        for v in self.verdicts:
            argv += ["--verdict", str(v)]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(gate.main(argv), 1)
        self.assertIn("--comments-json-file", err.getvalue())

    def test_refusal_lists_the_exact_hashes_at_that_minute(self):
        edited = [dict(r, text=r["text"] + " ") if r["text"] == self.status else r for r in self.real]
        res = self.run_gate(edited + self.orch_line, self.clear_arg)
        self.assertRefused(res, gate.comment_hash(self.status + " ")[:12])

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

    def test_orchestrator_line_must_postdate_the_newest_verdict_file(self):
        """After the cleared comment is not enough: a verdict written after the clearance voids it."""
        newest = self.reviews / "REVIEW_9kwlw_opus-h2h_r2_2026-10-25.md"
        t = 1792906530  # 2026-10-25 05:35:30 UTC, after the 01:10 comment
        os.utime(newest, (t, t))
        between = comment("2026-10-25 05:20", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}")
        same_minute = comment("2026-10-25 05:35", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}")
        after = comment("2026-10-25 05:36", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}")
        for orch in (between, same_minute):
            self.assertRefused(self.run_gate(self.real + orch, self.clear_arg), "after the verdict files")
        self.assertEqual(self.run_gate(self.real + after, self.clear_arg)[0], 0)

    def test_verdict_written_exactly_on_a_minute_boundary_needs_a_later_minute(self):
        """Postdate is strict: a verdict at 05:35:00 and a clearance at 05:35 may be either order."""
        newest = self.reviews / "REVIEW_9kwlw_opus-h2h_r2_2026-10-25.md"
        t = 1792906500  # 2026-10-25 05:35:00 UTC exactly
        os.utime(newest, (t, t))
        same_minute = comment("2026-10-25 05:35", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}")
        self.assertRefused(self.run_gate(self.real + same_minute, self.clear_arg), "05:36 UTC or later")
        after = comment("2026-10-25 05:36", f"ORCHESTRATOR CLEARED COMMENT {self.clear_arg}")
        self.assertEqual(self.run_gate(self.real + after, self.clear_arg)[0], 0)

    def test_two_matching_comments_in_the_minute_refuse(self):
        twice = self.real + comment(STATUS_TS, self.status, cid="dup") + self.orch_line
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
        same = comment(STATUS_TS, f"VOID @{TIP[:10]}: superseded", cid="void")
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

    def fake_bd(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        bd_log = self.tmp / "bd.log"
        (bin_dir / "bd").write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> '{bd_log}'\n")
        (bin_dir / "bd").chmod(0o755)
        os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        return bd_log

    def live(self, records):
        """Live reads return whatever `records` holds at the time of the read."""
        gate.bead_comments_json = lambda bead: json.dumps(records)

    def assertNothingWritten(self, res, needle, bd_log):
        self.assertRefused(res, needle)
        self.assertIn("nothing written", res[1])
        self.assertFalse(bd_log.exists())

    def test_verdict_rewritten_during_the_wait_revalidates_the_clearance(self):
        """Same bytes, new mtime after the orchestrator line: the gate re-checks and writes nothing."""
        bd_log = self.fake_bd()
        records = self.real + self.orch_line  # orchestrator line at 06:30
        self.live(records)
        target = self.reviews / "REVIEW_9kwlw_opus-h2h_r2_2026-10-25.md"

        def rewrite(_):
            target.write_bytes(target.read_bytes())
            os.utime(target, (1792910130, 1792910130))  # 2026-10-25 06:35:30 UTC
        gate.time.sleep = rewrite
        self.assertNothingWritten(self.run_gate(records, self.clear_arg, dry=False), "after the verdict files", bd_log)

    def test_role_revoked_during_the_wait_writes_nothing(self):
        bd_log = self.fake_bd()
        records = self.real + self.orch_line
        self.live(records)
        os.environ["CLAUDE_CODE_SESSION_ID"] = ORCH_ID

        def revoke(_):
            self.role.write_text("import sys\nprint('SUB')\nsys.exit(1)\n")
        gate.time.sleep = revoke
        res = self.run_gate(records, self.clear_arg, dry=False)
        self.assertNothingWritten(res, "no longer the orchestrator", bd_log)

    def test_session_env_changed_during_the_wait_writes_nothing(self):
        bd_log = self.fake_bd()
        records = self.real + self.orch_line
        self.live(records)
        gate.time.sleep = lambda _: os.environ.__setitem__("CLAUDE_CODE_SESSION_ID", ORCH_ID)
        res = self.run_gate(records, self.clear_arg, dry=False)  # a human terminal before the wait
        self.assertNothingWritten(res, "no longer the orchestrator", bd_log)

    def test_duplicate_posted_during_the_wait_writes_nothing(self):
        bd_log = self.fake_bd()
        records = self.real + self.orch_line
        self.live(records)
        gate.time.sleep = lambda _: records.extend(comment(STATUS_TS, self.status, cid="late-dup"))
        res = self.run_gate(list(records), self.clear_arg, dry=False)
        self.assertNothingWritten(res, "2 bead comments at 2026-10-25 01:10", bd_log)

    def test_veto_and_clearance_use_one_read_after_the_wait(self):
        """The read that verifies the clearance is the read the veto scan judges: a duplicate that
        appears only on a later read can't be both missed by the check and skipped by the scan."""
        bd_log = self.fake_bd()
        base = self.real + self.orch_line
        reads = []

        def reader(bead):
            reads.append(1)
            # Pre-wait read, then post-wait read; any third read would see the duplicate.
            extra = comment(STATUS_TS, self.status, cid="third-read") if len(reads) > 2 else []
            return json.dumps(base + extra)
        gate.bead_comments_json = reader
        gate.time.sleep = lambda s: None
        code, out = self.run_gate(base, self.clear_arg, dry=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(reads), 2)
        self.assertTrue(bd_log.exists())

    # --- end to end through the lander ------------------------------------------------------
    def test_real_write_lands_and_without_the_gate_pass_the_status_still_holds(self):
        sys.path.insert(0, str(HERE.parent.parent / "skills" / "land-batch" / "bin"))
        import land_batch_safety as lbs
        bd_log = self.fake_bd()
        records = self.real + self.orch_line
        self.live(records)
        text = as_text(records)
        gate.time.sleep = lambda s: None
        before = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=text)
        self.assertFalse(before[0], before)
        code, out = self.run_gate(records, self.clear_arg, dry=False)
        self.assertEqual(code, 0, out)
        logged = bd_log.read_text().splitlines()
        self.assertEqual(logged[:3], ["comment", BEAD, f"PASS @{TIP}"])
        self.assertIn(f"CLEARED-COMMENT {self.clear_arg}", logged)
        gate_file = next(self.reviews.glob("REVIEW_9kwlw_gate_*.md"))
        self.assertIn(f"- CLEARED-COMMENT {self.clear_arg}", gate_file.read_text())
        landed = as_text(records + comment("2026-10-25 06:45", "\n".join(logged[2:])))
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=landed)
        self.assertTrue(ok, ev)
        # The gate file alone (no PASS comment) does not land; the 01:10 comment still holds the tip.
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=text)
        self.assertFalse(ok, ev)
        # A PASS comment older than the status comment does not beat it.
        early = as_text(comment("2026-10-25 01:09", "\n".join(logged[2:])) + self.real + self.orch_line)
        ok, ev = lbs.independent_pass_for(BEAD, TIP, reviews_dir=self.reviews, comments_text=early)
        self.assertFalse(ok, ev)
        self.assertIn("needs input", ev)


if __name__ == "__main__":
    unittest.main()
