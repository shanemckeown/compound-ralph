"""LUCY-xyjxs: a prose file:line that no review-verdict finding or prior ref carries is an UNLISTED item the
lead must rule on in writing, not a 'cannot read inputs' refusal.

25 Oct, two unedited reviewer files jammed the gate: 0laj4 r1 opus-h2h (a VERIFIED OK note naming
index.tsx:87, "P3 at most"; byte copy in fixtures/xyjxs) and b7pqo r3 Astra (a quoted bypass example
`[P2] review_gate.py:319`). Wording never decides quoted-example vs finding: the lead's `unlisted` lines do.
"""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from test_review_lead import SHA_B, chk

FIX = Path(__file__).resolve().parent / "fixtures" / "xyjxs"
OLAJ4 = "pages/dashboard/business-studio/website/index.tsx:87"
B7_REAL = "skills/land-batch/bin/land_batch_safety.py:327"
# b7pqo astra r3: the line the checker refused on 25 Oct (its block carries the :327 finding, not :319).
B7_LINE = (f"P2 {B7_REAL} — Builder pastes `[P2] review_gate.py:319 — …` beneath a PASS status line and the "
           "safety check vetoes the landing")


def lead(unlisted=None, findings=()):
    out = {"bead": "LUCY-x", "round": 2, "scope_lock": False,
           "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None}, "findings": list(findings)}
    if unlisted is not None:
        out["unlisted"] = unlisted
    return out


def seat(prose, findings=()):
    verdict = "FAIL" if findings else "PASS"
    block = {"verdict": verdict, "findings": [{"severity": s, "file_line": fl, "text": t} for s, fl, t in findings]}
    return (f"REVIEWING: goal/x @ {SHA_B}\n{verdict}\n{prose}\nVERIFIED OK\n- tests\n"
            "```review-verdict\n" + json.dumps(block) + "\n```\n")


def run(lead_obj, text, comments=None, cleared=()):
    """main() over one verdict file; returns (exit code, stdout+stderr, {out file name: text})."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        d = Path(d)
        (d / "lead.json").write_text(json.dumps(lead_obj))
        (d / "astra-verdict-round-2.log").write_text(text)
        os.utime(d / "astra-verdict-round-2.log", (2000, 2000))
        argv = ["--lead", str(d / "lead.json"), "--verdict", str(d / "astra-verdict-round-2.log"),
                "--out-dir", str(d / "out"), "--round", "2"]
        if comments is not None:
            (d / "comments.txt").write_text(comments)
            argv += ["--bead", "LUCY-x", "--comments-file", str(d / "comments.txt")]
        for cid in cleared:
            argv += ["--cleared-escalation", cid]
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = chk.main(argv)
        files = {p.name: p.read_text() for p in (d / "out").glob("*")} if (d / "out").exists() else {}
        return code, out.getvalue() + err.getvalue(), files


B7_FINDING = {"id": "F1", "source_line": f"P2 {B7_REAL} — the safety check vetoes a PASS landing",
              "verdict": "dismissed", "category": "other", "reason": "replayed shape only, graded elsewhere",
              "clinic_scenario": "none: tooling", "ordinary_use": "none: tooling", "carve_out": False}


class RealShapes(unittest.TestCase):
    def test_0laj4_unruled_refuses_naming_the_item_not_the_inputs(self):
        code, out, _ = run(lead(), (FIX / "0laj4-opus-h2h-round-1.md").read_text())
        self.assertEqual(code, 1, out)
        self.assertIn(f"UNLISTED {OLAJ4}: a prose P3 line cites it", out)
        self.assertNotIn("cannot read inputs", out)

    def test_0laj4_dismissed_passes(self):
        code, out, files = run(lead([f"UNLISTED {OLAJ4}: DISMISSED — VERIFIED OK note on why staff can't "
                                     "reach the importer, not a finding"]),
                               (FIX / "0laj4-opus-h2h-round-1.md").read_text())
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("UNLISTED=1", out)
        self.assertIn(f"UNLISTED dismissed**: UNLISTED {OLAJ4}", files["lead-dismissals-round-2.md"])

    def b7(self, unlisted=None, **kw):
        return run(lead(unlisted, [B7_FINDING]),
                   seat(B7_LINE, [("P2", B7_REAL, "the safety check vetoes a PASS landing")]), **kw)

    def test_b7pqo_unruled_refuses(self):
        code, out, _ = self.b7()
        self.assertEqual(code, 1, out)
        self.assertIn("UNLISTED review_gate.py:319: a prose P2 line cites it", out)
        self.assertNotIn("cannot read inputs", out)

    def test_b7pqo_dismissed_escalates_because_the_line_carries_a_p2(self):
        code, out, files = self.b7(["UNLISTED review_gate.py:319: DISMISSED — quoted bypass example in "
                                    "the :327 finding's text"])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=1", out)
        self.assertIn("UNLISTED review_gate.py:319 (P2", files["lead-escalate-round-2.txt"])

    def test_b7pqo_escalation_cleared_by_the_orchestrator(self):
        dismissed = ["UNLISTED review_gate.py:319: DISMISSED — quoted bypass example in the :327 finding's text"]
        comment = ("[Encrypted-S] at 2026-10-25 06:30\n"
                   "  ORCHESTRATOR CLEARED UNLISTED:review_gate.py:319 quoted example, not a finding\n")
        code, out, files = self.b7(dismissed, comments=comment, cleared=["UNLISTED:review_gate.py:319"])
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("CLEARED UNLISTED:review_gate.py:319: ORCHESTRATOR CLEARED", out)
        self.assertIn("UNLISTED:review_gate.py:319", files["lead-cleared-round-2.txt"])
        # A comment naming another item does not clear it.
        other = comment.replace("review_gate.py:319", "review_gate.py:31")
        code, out, _ = self.b7(dismissed, comments=other, cleared=["UNLISTED:review_gate.py:319"])
        self.assertEqual(code, 1, out)


    def test_clearing_a_line_id_does_not_clear_its_bare_file_id(self):
        """Own-gate r1 Astra P2 (:1246): 'UNLISTED:lib/x.ts' must not be cleared by a comment naming
        'UNLISTED:lib/x.ts:9'."""
        prose = "Example P2 lib/x.ts:9 — quoted\nAnother P2 lib/x.ts — quoted too"
        dismissed = ["UNLISTED lib/x.ts:9: DISMISSED — a quoted example, not a finding",
                     "UNLISTED lib/x.ts: DISMISSED — a quoted example, not a finding"]
        comment = "[Encrypted-S] at 2026-10-25 06:30\n  ORCHESTRATOR CLEARED UNLISTED:lib/x.ts:9 quoted example\n"
        both = ["UNLISTED:lib/x.ts:9", "UNLISTED:lib/x.ts"]
        code, out, _ = run(lead(dismissed), seat(prose), comments=comment, cleared=both)
        self.assertEqual(code, 1, out)
        self.assertIn("--cleared-escalation UNLISTED:lib/x.ts: no bead comment", out)
        code, out, _ = run(lead(dismissed), seat(prose), comments=comment, cleared=both[:1])
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=1", out)


class Rulings(unittest.TestCase):
    PROSE = "Also P2 lib/pay.ts:40 — the retry charges the card twice on a double tap"

    def test_real_prose_p2_with_no_lead_line_blocks(self):
        code, out, _ = run(lead(), seat(self.PROSE))
        self.assertEqual(code, 1, out)
        self.assertIn("UNLISTED lib/pay.ts:40", out)

    def test_finding_p2_blocks_like_an_act_on(self):
        code, out, files = run(lead(["UNLISTED lib/pay.ts:40: FINDING P2"]), seat(self.PROSE))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=0", out)
        self.assertIn("UNLISTED lib/pay.ts:40 (P2", files["lead-actions-round-2.txt"])

    def test_finding_p3_is_noted_with_a_reason(self):
        prose = "Nit P3 lib/copy.ts:4 — the label says Save twice"
        code, out, files = run(lead(["UNLISTED lib/copy.ts:4: FINDING P3 — cosmetic copy, filed nowhere"]),
                               seat(prose))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("UNLISTED noted**", files["lead-dismissals-round-2.md"])
        code, out, _ = run(lead(["UNLISTED lib/copy.ts:4: FINDING P3"]), seat(prose))
        self.assertEqual(code, 1, out)
        self.assertIn("needs a written reason", out)

    def test_a_p3_grade_of_a_p2_line_escalates(self):
        code, out, _ = run(lead(["UNLISTED lib/pay.ts:40: FINDING P3 — only on a double tap, rare"]),
                           seat(self.PROSE))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=1", out)

    def test_a_p1_anywhere_on_the_line_makes_a_dismissal_escalate(self):
        """The bead: 'if the prose names P1/P2 on the same line'. The item's own claim is P3; the line carries a P1."""
        prose = "P1 lib/pay.ts:37 — retry charges twice, the same shape as the P3 lib/eg.ts:9 example"
        code, out, _ = run(lead(["UNLISTED lib/eg.ts:9: DISMISSED — an example the reviewer quoted, not a finding"],
                                [{"id": "F1", "source_line": "P1 lib/pay.ts:37 — retry charges twice — guard",
                                  "verdict": "act_on", "category": "money", "clinic_scenario": "reception, iPad, "
                                  "checkout", "demonstrated": "red test"}]),
                           seat(prose, [("P1", "lib/pay.ts:37", "retry charges twice — guard")]))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=1", out)

    def test_lead_lines_must_be_well_formed_unique_and_real(self):
        for bad, why in ((["UNLISTED lib/other.ts:1: DISMISSED — not raised by any reviewer prose"],
                          "which no reviewer prose raised"),
                         (["UNLISTED lib/pay.ts:40: DISMISSED — quoted example only", "UNLISTED lib/pay.ts:40: "
                           "FINDING P2"], "twice"),
                         (["lib/pay.ts:40 is fine"], "unlisted entry not"),
                         (["UNLISTED lib/pay.ts:40: DISMISSED — ok"], "needs a written reason"),
                         ("UNLISTED lib/pay.ts:40: FINDING P2", "is not a list")):
            code, out, _ = run(lead(bad), seat(self.PROSE))
            self.assertEqual(code, 1, (bad, out))
            self.assertIn(why, out, bad)

    def test_every_file_line_on_a_prose_line_needs_its_own_ruling(self):
        """Independent r1 P2 (attempt 2): a severity binds one path, so the second file:line on the line (another
        file, or another line of the same file) was never registered and one ruling waved it through."""
        for first, second in (("lib/copy.ts:10", "lib/pay.ts:40"), ("lib/pay.ts:10", "lib/pay.ts:40")):
            prose = f"P3 {first} has a cosmetic label typo; {second} retries the charge and charges twice."
            one = [f"UNLISTED {first}: DISMISSED — cosmetic, not a finding"]
            code, out, _ = run(lead(one), seat(prose))
            self.assertEqual(code, 1, (second, out))
            self.assertIn(f"UNLISTED {second}: a prose P3 line cites it", out)
            # One ruling never covers another location.
            code, out, _ = run(lead([f"UNLISTED {second}: DISMISSED — cosmetic, not a finding"]), seat(prose))
            self.assertEqual(code, 1, out)
            self.assertIn(f"UNLISTED {first}: a prose P3 line cites it", out)
            code, out, _ = run(lead(one + [f"UNLISTED {second}: FINDING P2"]), seat(prose))
            self.assertEqual(code, 0, out)
            self.assertIn("BLOCKING=1 ESCALATE=0", out)
            self.assertIn("UNLISTED=2", out)

    def test_an_earlier_round_note_head_location_needs_its_own_ruling(self):
        """Independent r2 P2 (attempt 3): the sweep read only the note's tail, so a second file:line before
        'open:' escaped and ruling only on the tail's quoted example passed."""
        prose = ("r1 P3 lib/copy.ts:10 + lib/pay.ts:40 open: label typo and retry double charge remain; "
                 "P3 lib/example.ts:9 is only a quoted example.")
        f1 = {"id": "F1", "source_line": "P3 lib/copy.ts:10 — label typo", "verdict": "dismissed",
              "category": "other", "reason": "cosmetic label typo only", "clinic_scenario": "none: cosmetic",
              "ordinary_use": "none: cosmetic", "carve_out": False}
        block = [("P3", "lib/copy.ts:10", "label typo")]
        tail = ["UNLISTED lib/example.ts:9: DISMISSED — quoted example only"]
        code, out, _ = run(lead(tail, [f1]), seat(prose, block))
        self.assertEqual(code, 1, out)
        self.assertIn("UNLISTED lib/pay.ts:40: a prose P3 line cites it", out)
        code, out, _ = run(lead(tail + ["UNLISTED lib/pay.ts:40: FINDING P2"], [f1]), seat(prose, block))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=0", out)
        self.assertIn("UNLISTED=2", out)

    def test_a_second_location_carried_by_the_block_needs_no_ruling(self):
        prose = "P3 lib/copy.ts:10 typo; lib/pay.ts:40 double charge, as in the block."
        code, out, _ = run(lead(["UNLISTED lib/copy.ts:10: DISMISSED — cosmetic, not a finding"],
                                [{"id": "F1", "source_line": "P2 lib/pay.ts:40 — double charge — guard",
                                  "verdict": "act_on", "category": "money", "clinic_scenario": "reception, iPad, "
                                  "checkout", "demonstrated": "red test"}]),
                           seat(prose, [("P2", "lib/pay.ts:40", "double charge — guard")]))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=0", out)
        self.assertIn("UNLISTED=1", out)

    def test_severity_mismatch_still_refuses_the_inputs(self):
        code, out, _ = run(lead(findings=[{"id": "F1", "source_line": "P2 lib/pay.ts:40 — retry charges twice — fix",
                                           "verdict": "act_on", "category": "money", "clinic_scenario": "x",
                                           "demonstrated": "y"}]),
                           seat("P1 lib/pay.ts:40 — retry charges twice", [("P2", "lib/pay.ts:40", "retry charges twice — fix")]))
        self.assertEqual(code, 1, out)
        self.assertIn("cannot read inputs", out)
        self.assertIn("severity mismatch", out)


if __name__ == "__main__":
    unittest.main()
