"""LUCY-shb7s at the checker: --earlier-input files (own-gate seats review-gate passes from the evidence dir) are
read for their findings only. Never a seat, never the tip, never fixed_by; older than the newest seat round; only
they may mix round-<N> names with _r<N>_ seats or carry words after an exact-sha echo.
"""
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_review_lead import LEAD_R2, R1_RAISE, R1_REF, SHA_A, SHA_B, chk, r2_file


def run(earlier, seats, lead=None):
    """earlier / seats: [(name, text, mtime)]; '@D@' in the lead is the temp dir."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        argv = ["--out-dir", str(Path(d) / "out"), "--round", "2"]
        for flag, named in (("--earlier-input", earlier), ("--verdict", seats)):
            for name, text, mtime in named:
                (Path(d) / name).write_text(text)
                os.utime(Path(d) / name, (mtime, mtime))
                argv += [flag, str(Path(d) / name)]
        (Path(d) / "lead.json").write_text(json.dumps(lead or LEAD_R2).replace("@D@", d))
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(err):
            code = chk.main(["--lead", str(Path(d) / "lead.json")] + argv)
        return code, err.getvalue()


def lead_by(name):
    lead = copy.deepcopy(LEAD_R2)
    lead["findings"][0]["fixed_by"] = f"@D@/{name}"
    return lead


SEAT = ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000)


class EarlierInput(unittest.TestCase):
    def test_native_name_with_trailing_echo_words_grades_fixed_under_an_r_seat(self):
        raise_ = R1_RAISE.replace(SHA_A, SHA_A + " (own-gate, round 1)")
        code, out = run([("astra-verdict-round-1.log", raise_, 1000)], [SEAT])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)

    def test_same_files_as_plain_verdicts_still_refuse_the_mix(self):
        code, out = run([], [("astra-verdict-round-1.log", R1_RAISE, 1000), SEAT])
        self.assertEqual(code, 1, out)
        self.assertIn("mix _r<N>_", out)

    def test_seat_echo_with_trailing_words_still_refused(self):
        seat = (SEAT[0], SEAT[1].replace(SHA_B, SHA_B + " (independent)", 1), 2000)
        code, out = run([("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000)], [seat])
        self.assertEqual(code, 1, out)
        self.assertIn("no single well-formed REVIEWING echo", out)

    def test_earlier_input_at_the_tip_refused(self):
        code, out = run([("REVIEW_x_astra_r1_a.md", R1_RAISE.replace(SHA_A, SHA_B), 1000)], [SEAT])
        self.assertEqual(code, 1, out)
        self.assertIn("other than the tip", out)

    def test_earlier_input_not_older_than_the_seat_round_refused(self):
        code, out = run([("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                         ("REVIEW_x_forged_r3_c.md", r2_file(prior=[(R1_REF, "closed")], sha="4" * 40), 1500)],
                        [SEAT])
        self.assertEqual(code, 1, out)
        self.assertIn("not older than the newest seat round 2", out)

    def test_earlier_input_as_fixed_by_refused(self):
        closer = ("REVIEW_x_own_r1_c.md", r2_file(prior=[(R1_REF, "closed")], sha="4" * 40), 1500)
        code, out = run([("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), closer], [SEAT],
                        lead=lead_by("REVIEW_x_own_r1_c.md"))
        self.assertEqual(code, 1, out)
        self.assertIn("is an earlier-finding input (own-gate)", out)

    def test_earlier_inputs_alone_have_no_seat(self):
        code, out = run([("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000)],
                        [("mutants-round-2.txt", "PASS\n", 2050)])
        self.assertEqual(code, 1, out)
        self.assertIn("no review among the inputs", out)

    def test_short_sha_with_trailing_words_is_not_accepted(self):
        raise_ = R1_RAISE.replace(SHA_A, SHA_A[:10] + " (own-gate)")
        code, out = run([("REVIEW_x_astra_r1_a.md", raise_, 1000)], [SEAT])
        self.assertEqual(code, 1, out)
        self.assertIn("naming an exact sha", out)

    def test_both_verdict_and_earlier_input_refused(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
            f = Path(d) / "REVIEW_x_astra_r1_a.md"
            f.write_text(R1_RAISE)
            (Path(d) / "lead.json").write_text(json.dumps(LEAD_R2))
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(err):
                code = chk.main(["--lead", str(Path(d) / "lead.json"), "--verdict", str(f), "--earlier-input", str(f),
                                 "--out-dir", str(Path(d) / "out"), "--round", "2"])
            self.assertEqual(code, 1, err.getvalue())
            self.assertIn("both a --verdict and an --earlier-input", err.getvalue())


if __name__ == "__main__":
    unittest.main()
