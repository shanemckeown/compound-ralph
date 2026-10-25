"""LUCY-unykv: an ambiguous prior ref (two distinct round-N findings at one <sev> <file:line>) counts fixed only
when EVERY newest-round seat at the tip closes it with evidence; a missing, open, mixed or re-raised entry keeps
the G3/A5 escalation. Replays the real 9kwlw independent r2 inputs (BLOCKING=0 ESCALATE=0).
"""
import unittest

from test_16n7i_slashwords import replay
from test_e3opf_grammar import REF, G3RefIdentity, r1_seat
from test_review_lead import r2_file, run_named

A, B = G3RefIdentity.A, G3RefIdentity.B


def run(astra_prior, opus_prior, grade="fixed", opus_findings=()):
    lead = G3RefIdentity().lead([A, B])
    if grade != "fixed":
        for f in lead["findings"]:
            f.update(verdict=grade, reason="reviewer agreed it is cosmetic only", follow_up="tidy",
                     ordinary_use="reception opens the client form")
            f.pop("fixed_by")
    opus_verdict = "FAIL" if opus_findings else "PASS"
    return run_named(lead, [
        ("REVIEW_x_astra_r1_a.md", r1_seat(A), 1000),
        ("REVIEW_x_opus_r1_a.md", r1_seat(B), 1100),
        ("REVIEW_x_astra_r2_b.md", r2_file(prior=astra_prior), 2000),
        ("REVIEW_x_opus_r2_b.md", r2_file(prior=opus_prior, verdict=opus_verdict, findings=opus_findings), 2100)])


class EverySeatClosesTheAmbiguousRef(unittest.TestCase):
    def test_both_seats_closed_both_fixed(self):
        code, out = run([(REF, "closed")], [(REF, "closed")])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=2", out)

    def test_one_closed_one_open_escalates(self):
        code, out = run([(REF, "closed")], [(REF, "open")])
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=2", out)
        self.assertIn("FIXED=0", out)

    def test_one_closed_one_missing_is_not_fixed(self):
        code, out = run([(REF, "closed")], [])
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=2", out)
        self.assertIn("FIXED=0", out)

    def test_closed_but_raised_again_is_not_fixed(self):
        again = [("P2", "lib/pay.ts:37 — retry still charges twice — guard")]
        code, out = run([(REF, "closed")], [(REF, "closed")], opus_findings=again)
        self.assertEqual(code, 1, out)
        self.assertIn("again at the current tip: not fixed", out)
        self.assertNotIn("FIXED=", out)

    def test_earlier_round_open_at_the_same_tip_escalates(self):
        # r2 reviewed the tip and kept it open; both r3 seats re-reviewed the same tip and closed it: mixed.
        lead = G3RefIdentity().lead([A, B])
        for f in lead["findings"]:
            f["fixed_by"] = "@D@/REVIEW_x_astra_r3_c.md"
        code, out = run_named(lead, [
            ("REVIEW_x_astra_r1_a.md", r1_seat(A), 1000),
            ("REVIEW_x_opus_r1_a.md", r1_seat(B), 1100),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(REF, "open")]), 2000),
            ("REVIEW_x_astra_r3_c.md", r2_file(prior=[(REF, "closed")]), 3000),
            ("REVIEW_x_opus_r3_c.md", r2_file(prior=[(REF, "closed")]), 3100)])
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=2", out)
        self.assertIn("FIXED=0", out)

    def test_only_a_fixed_grade_is_relieved(self):
        code, out = run([(REF, "closed")], [(REF, "closed")], grade="consider")
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=2", out)

    def test_non_ambiguous_path_unchanged(self):
        code, out = G3RefIdentity().run_pair(A)  # same text in two seats: one finding, no ambiguity
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=0", out)
        self.assertIn("FIXED=2", out)


class Replay9kwlw(unittest.TestCase):
    def test_independent_r2_inputs_reach_the_gate(self):
        code, out = replay("9kwlw", scope_lock=True)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)


if __name__ == "__main__":
    unittest.main()
