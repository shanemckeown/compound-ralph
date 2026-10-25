"""LUCY-b7pqo: review-gate.sh does not refuse on the lead-escalation status comment, and still refuses
on a verdict-shaped veto in the same comment. Runs the real gate (q41ar r10 harness) in --dry-run."""
import unittest

from test_review_gate import BEAD, CLEARED, TIP, _GateCase, gate

HEAD = (f"needs input: {BEAD} escalated 1 protected item(s): "
        f"{gate.lbs.EVIDENCE_ROOT}/review/q41ar-independent-r10/lead-escalate-round-10.txt")
STATUS = f"{HEAD} — independent r10 @{TIP[:10]}: Opus PASS, Astra FAIL (1 P2). Lead BLOCKING=0 ESCALATE=1."


def _comment(ts, body):
    return f"[Encrypted-S] at {ts}\n" + "".join(f"  {ln}\n" for ln in body.splitlines()) + "\n"


class StatusCommentIsNotAVeto(_GateCase):
    def dry_run(self, body):
        self.comments.write_text(CLEARED + _comment("2026-10-23 09:50", body))
        return self.run_gate("--dry-run", "--comments-file", str(self.comments))

    def test_exact_status_comment_does_not_refuse(self):
        code, out = self.dry_run(STATUS)
        self.assertEqual(code, 0, out)

    def test_status_comment_carrying_a_verdict_still_refuses(self):
        for body in (f"{STATUS}\nFAIL @{TIP}", STATUS.replace("BLOCKING=0", "BLOCKING=1"),
                     f"{STATUS}\nWITHDRAWN: wrong brief", STATUS.replace(BEAD, BEAD + "2", 1),
                     f"needs input: FAIL @{TIP[:10]}", f"{STATUS}\n**FAIL** @ **{TIP[:10]}**",
                     f"{STATUS}\n## REVIEWING: x @ {TIP[:10]}\n**FAIL**\nP2 review_gate.py — y"):
            code, out = self.dry_run(body)
            self.assertEqual(code, 1, body)
            self.assertIn("not among the inputs", out)


if __name__ == "__main__":
    unittest.main()
