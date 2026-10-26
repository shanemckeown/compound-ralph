"""LUCY-nd3qz (Shane, 26 Oct: one review seat only, no second Claude read): a round with ONE seat is a complete
roster. Its closed prior entry alone grades `fixed`, a one-seat PASS gates through review-gate, and every refusal
that keeps an unreviewed or self-reviewed tip from landing still refuses with one seat.

Run: python3 -m unittest discover -s ~/.claude/tests/review-lead -v
"""
import copy
import hashlib
import json
import os
import unittest

from test_review_lead import LEAD_R2, R1_RAISE, R1_REF, SHA_A, SHA_B, r2_file, run_named
from test_review_gate import BEAD, R10_MTIME, TIP, _GateCase


def one_seat(r2_text, lead=None, extra=()):
    """r1 and r2 each have exactly one seat (Astra), as every round now does."""
    return run_named(lead or copy.deepcopy(LEAD_R2), [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                                                      ("REVIEW_x_astra_r2_b.md", r2_text, 2000), *extra])


class OneSeatGradesFixed(unittest.TestCase):
    def test_one_seat_closed_prior_grades_fixed(self):
        code, out = one_seat(r2_file(prior=[(R1_REF, "closed")]))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_one_seat_with_builder_evidence_still_grades_fixed(self):
        # The mutants file rides along every round; it is never a seat, so it never joins the roster.
        code, out = one_seat(r2_file(prior=[(R1_REF, "closed")]),
                             extra=[("mutants-round-2.txt", "PASS\n", 2050)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_claude_route_native_names_one_opus_seat_grades_fixed(self):
        lead = copy.deepcopy(LEAD_R2)
        lead["findings"][0]["fixed_by"] = "@D@/claude-seat1-round-2.md"
        code, out = run_named(lead, [("claude-seat1-round-1.md", R1_RAISE, 1000),
                                     ("claude-seat1-round-2.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                                     ("mutants-round-2.txt", "PASS\n", 2050)])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)


class OneSeatRefusalsStillRefuse(unittest.TestCase):
    def test_one_seat_with_no_entry_is_not_fixed(self):
        code, out = one_seat(r2_file(prior=[]))
        self.assertEqual(code, 1, out)
        self.assertIn("has no review-verdict prior entry", out)

    def test_one_seat_open_entry_is_not_fixed(self):
        code, out = one_seat(r2_file(prior=[(R1_REF, "open")], verdict="FAIL",
                                     findings=[("P2", "lib/pay.ts:37 — retry still charges twice — guard")]))
        self.assertEqual(code, 1, out)
        self.assertNotIn("FIXED=1", out)

    def test_one_seat_prose_closed_line_never_grades(self):
        code, out = one_seat(r2_file(confirm=f"- {R1_REF} closed: regression test drives the retry, green"))
        self.assertEqual(code, 1, out)
        self.assertNotIn("FIXED=1", out)

    def test_one_seat_closed_but_raised_again_refuses(self):
        again = [("P2", "lib/pay.ts:37 — retry still charges twice — guard")]
        code, out = one_seat(r2_file(prior=[(R1_REF, "closed")], verdict="FAIL", findings=again))
        self.assertEqual(code, 1, out)
        self.assertIn("again at the current tip: not fixed", out)

    def test_builder_evidence_is_never_the_one_seat(self):
        # Mutants file as fixed_by: builder evidence never confirms a fix, even when no other seat exists.
        lead = copy.deepcopy(LEAD_R2)
        lead["findings"][0]["fixed_by"] = "@D@/mutants-round-2.txt"
        code, out = run_named(lead, [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                                     ("mutants-round-2.txt", "PASS\n", 2050)])
        self.assertEqual(code, 1, out)
        self.assertIn("is builder evidence", out)

    def test_one_seat_of_the_same_commit_cannot_confirm(self):
        code, out = one_seat(r2_file(prior=[(R1_REF, "closed")], sha=SHA_A))
        self.assertEqual(code, 1, out)
        self.assertNotIn("FIXED=1", out)


def one_seat_pass(tip=TIP, echo=True):
    head = f"REVIEWING: goal/aestheticcnext-q41ar @ {tip}\n" if echo else ""
    return (head + "PASS\nVERIFIED OK\n- erasure cell green at this tip\nUNVERIFIED\n- live\n"
            "```review-verdict\n" + json.dumps({"verdict": "PASS", "findings": [], "prior": []}) + "\n```\n")


class OneSeatPassGates(_GateCase):
    """review-gate over a round whose only seat is one structured PASS review of the tip."""

    def use_one_seat(self, text):
        self.verdict.write_text(text)
        os.utime(self.verdict, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(self.verdict.read_bytes()).hexdigest() + "\n")
        lead = json.loads(self.lead.read_text())
        lead["findings"] = []
        self.lead.write_text(json.dumps(lead))

    def test_one_seat_pass_writes_the_pass_line(self):
        self.use_one_seat(one_seat_pass())
        code, out = self.run_gate(cleared=())
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.gate_files()), 1, out)
        logged = self.bd_log.read_text().splitlines()
        self.assertEqual(logged[:3], ["comment", BEAD, f"PASS @{TIP}"])

    def test_one_seat_pass_of_another_tip_refused(self):
        self.use_one_seat(one_seat_pass(tip=SHA_B.replace("7", "8")))
        code, out = self.run_gate(cleared=())
        self.assertEqual(code, 1, out)
        self.assertEqual(self.gate_files(), [])
        self.assertFalse(self.bd_log.exists())

    def test_one_seat_pass_without_echo_refused(self):
        self.use_one_seat(one_seat_pass(echo=False))
        code, out = self.run_gate(cleared=())
        self.assertEqual(code, 1, out)
        self.assertEqual(self.gate_files(), [])

    def test_one_seat_pass_without_md5_record_refused(self):
        self.use_one_seat(one_seat_pass())
        (self.out / "review-file.md5").unlink()
        code, out = self.run_gate(cleared=())
        self.assertEqual(code, 1, out)
        self.assertEqual(self.gate_files(), [])

    def test_one_seat_own_gate_pass_outside_ops_reviews_refused(self):
        own = self.tmp / "astra-verdict-round-1.log"
        own.write_text(one_seat_pass())
        os.utime(own, (R10_MTIME, R10_MTIME))
        (self.out / "review-file.md5").write_text(hashlib.md5(own.read_bytes()).hexdigest() + "\n")
        lead = json.loads(self.lead.read_text())
        lead["findings"] = []
        self.lead.write_text(json.dumps(lead))
        code, out = self.run_gate(verdicts=[own], cleared=())
        self.assertEqual(code, 1, out)
        self.assertEqual(self.gate_files(), [])


if __name__ == "__main__":
    unittest.main()
