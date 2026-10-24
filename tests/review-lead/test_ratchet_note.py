"""LUCY-mz6fk: a file-length ratchet finding on files the lander regenerates is a non-escalating Note,
even when a counted file name (credit-service.ts, cron/...) trips the keyword backstop. The exemption keys
on the finding's SHAPE, never its wording: declared at the ratchet test, every other path in
"<path> [is] N/M [lines]" count form, nothing else path-like or protected left.

Run: python3 -m unittest discover -s ~/.claude/tests/review-lead -p 'test_*.py'
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent / "references" / "review-lead"))
import review_lead_check as chk  # noqa: E402
from test_review_lead import run_check  # noqa: E402

T = "tests/meta/file-length-ratchet.test.ts:218"
RATCHET = (f"P3 {T} — No real clinic scenario; build-gate failure: components/appointments/use-clinical-note-fill.ts "
           f"is 358/356 lines, lib/services/credit-service.ts 588/578, and pages/api/cron/stale-attempt-sweeper.ts "
           f"109/105 — regenerate the baseline during landing")
MONEY = "P1 lib/pos/checkout.ts:9 — Terminal tap retried; client charged twice. Reproduced. — idempotency key"


def lead_for(*findings):
    return {"bead": "AestheticcNext-test", "round": 1, "scope_lock": False,
            "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
            "findings": list(findings)}


def noted(src, fid="F1", category="other"):
    return {"id": fid, "source_line": src, "verdict": "noted", "category": category,
            "reason": "lander regenerates the file-length baseline; build hygiene, no clinic scenario"}


def consider(src, fid="F1"):
    return {"id": fid, "source_line": src, "verdict": "consider", "category": "other", "carve_out": True,
            "pre_existing": False, "reason": "lander regenerates the file-length baseline",
            "follow_up": "Lander: regenerate the file-length baseline"}


def money_act_on(fid="F2"):
    return {"id": fid, "source_line": MONEY, "verdict": "act_on", "category": "money", "in_scope": True,
            "clinic_scenario": "reception taps the card twice on a slow reader at checkout",
            "demonstrated": "reviewer reproduced"}


class RatchetShape(unittest.TestCase):
    def test_shape_predicate(self):
        self.assertTrue(chk.ratchet_only(RATCHET))
        for src in (
            f"P3 {T} — credit-service.ts 588/578; also the refund path charges twice — idempotency",
            f"P3 {T} — credit-service.ts 588/578; credit-service.ts drops the deduction on retry — fix",
            "P3 lib/services/credit-service.ts:12 — file-length ratchet: credit-service.ts 588/578 — regen",
            f"P3 {T} — credit-service.ts 588/578, see lib/pos/checkout.ts:9 — regen",
            f"P3 {T} — credit-service.ts 588/578; package credits go negative — regen",
            f"P3 tests/meta/other.test.ts:1 — credit-service.ts 588/578 — regen",
        ):
            self.assertFalse(chk.ratchet_only(src), src)


class RatchetNote(unittest.TestCase):
    def run_one(self, lead, *lines):
        return run_check(lead, "PASS\n\n" + "\n".join(lines) + "\n")

    def test_1_ratchet_only_finding_is_noted_no_escalation(self):
        code, out, files = self.run_one(lead_for(noted(RATCHET)), RATCHET)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("NOTED=1", out)
        self.assertEqual(files["lead-escalate-round-1.txt"], "")

    def test_1b_ratchet_consider_goes_to_follow_up_not_escalation(self):
        code, out, files = self.run_one(lead_for(consider(RATCHET)), RATCHET)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0 FOLLOWUPS=1", out)

    def test_1c_protected_category_still_wins(self):
        code, out, _ = self.run_one(lead_for(noted(RATCHET, category="money")), RATCHET)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_2_non_ratchet_credit_service_finding_still_escalates(self):
        src = "P3 lib/services/credit-service.ts:40 — package credit decremented before the visit is saved — move"
        code, out, _ = self.run_one(lead_for(noted(src)), src)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)
        code, out, files = self.run_one(lead_for(consider(src)), src)
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=1", out)

    def test_3_ratchet_mixed_with_money_claim_escalates(self):
        src = f"P3 {T} — lib/services/credit-service.ts 588/578; the refund charges the client twice — regen"
        code, out, _ = self.run_one(lead_for(noted(src)), src)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_4_ratchet_file_line_with_behaviour_claim_escalates(self):
        src = (f"P3 {T} — lib/services/credit-service.ts 588/578, and lib/services/credit-service.ts:120 skips "
               "the deduction on retry — regen")
        code, out, files = self.run_one(lead_for(consider(src)), src)
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=1", out)
        # The extra path carries no protected word itself: naming it outside count form is enough.
        src = (f"P3 {T} — lib/services/credit-service.ts 588/578; lib/utils/speech-batch.ts:149 swallows the "
               "transcript error — regen")
        code, out, _ = self.run_one(lead_for(noted(src)), src)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_4b_behaviour_claim_without_path_or_keyword_escalates(self):
        # Round-1 own-gate reviewers' reproductions (Astra P1, Opus P1 x4, Opus P3): residue outside the
        # ratchet vocabulary keeps the backstop, keyword or not.
        for src in (
            f"P3 {T} — credit-service.ts 588/578; retrying deducts twice — add an idempotency guard",
            f"P3 {T} — credit-service.ts 588/578; its retry path also skips the deduction so the client gets "
            "a free session — regen",
            f"P3 {T} — credit-service.ts 588/578; double-deducts every session on a retried visit — regen",
            f"P3 {T} — credit-service.ts 588/578 — regen; P1 lib/services/visit.ts deducts twice — guard",
            f"P3 {T} — credit-service.ts 588/578; staff at other clinics can open this client's notes — regen",
            f"P3 {T} — credit-service.ts 2/1 deductions per retry — regen",
            f"P3 {T} — credit-service.ts 3/5 retries deduct twice — regen",
        ):
            self.assertFalse(chk.ratchet_only(src), src)
            code, out, files = self.run_one(lead_for(consider(src)), src)
            self.assertEqual(code, 0, out)
            self.assertIn("ESCALATE=1", out, src)

    def test_4d_round2_reproductions_escalate(self):
        # Round-2 own-gate reproductions (Astra P1/P2, Opus P3 x3): allowed-looking words, fake paths,
        # extensionless "counts", non-ASCII and meaning-flipping words all keep the backstop.
        for src in (
            f"P3 {T} — credit-service.ts 588/578; runtime count exceeds cap — cap the count",
            f"P3 {T} — credit-service.ts 588/578; build/test:9 — regen",
            f"P3 {T} — booking.slots 2/1 — regen",
            f"P3 {T} — credit-service.ts 588/578; \u0434\u0432\u043e\u0439\u043d\u043e\u0435 \u0441\u043f\u0438\u0441\u0430\u043d\u0438\u0435 — regen",
            f"P3 {T} — credit-service.ts 588/578; not a ratchet failure: the real clinic gate fails — regen",
        ):
            self.assertFalse(chk.ratchet_only(src), src)
            code, out, files = self.run_one(lead_for(consider(src)), src)
            self.assertEqual(code, 0, out)
            self.assertIn("ESCALATE=1", out, src)

    def test_4e_honest_variants_still_exempt(self):
        for src in (
            f"P3 {T} — lib/services/credit-service.ts 588/578 — lander regen",
            f"P3 **{T}** \u2013 credit-service.ts 588 / 578 lines — regenerate the baseline during landing",
            f"P3 tests/meta/file-length-ratchet.test.ts:218-220 — file-length ratchet: pages/api/cron/x.ts is "
            "109/105 lines — lander regenerates",
        ):
            self.assertTrue(chk.ratchet_only(src), src)

    def test_4c_only_a_p3_is_exempt(self):
        src = f"P2 {T} — lib/services/credit-service.ts 588/578 — regenerate the baseline during landing"
        self.assertFalse(chk.ratchet_only(src))

    def test_5_ratchet_text_at_credit_service_file_line_escalates(self):
        src = "P3 lib/services/credit-service.ts:12 — file-length ratchet: credit-service.ts 588/578 — regen"
        code, out, _ = self.run_one(lead_for(noted(src)), src)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_6a_ratchet_note_does_not_cover_a_money_finding(self):
        code, out, _ = self.run_one(lead_for(noted(RATCHET)), RATCHET, MONEY)
        self.assertEqual(code, 1, out)
        self.assertIn("covered 0x", out)

    def test_6b_money_finding_beside_a_ratchet_note_still_blocks(self):
        code, out, files = self.run_one(lead_for(noted(RATCHET), money_act_on()), RATCHET, MONEY)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1", out)
        self.assertIn("checkout.ts:9", files["lead-actions-round-1.txt"])
        code, out, _ = self.run_one(lead_for(noted(RATCHET), noted(MONEY, "F2")), RATCHET, MONEY)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_6c_ratchet_line_naming_another_finding_location_is_not_exempt(self):
        src = f"P3 {T} — credit-service.ts 588/578; closes lib/pos/checkout.ts:9 — regen"
        code, out, _ = self.run_one(lead_for(noted(src)), src)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)


REPLAY = Path("/Users/shane/.claude/evidence/goal/9kwlw-20261024")


@unittest.skipUnless((REPLAY / "lead-round-8.json").exists(), "9kwlw round 8 evidence not on this machine")
class Replay9kwlwRound8(unittest.TestCase):
    """The round-8 review files predate e3opf's strict grammar, which refuses them as inputs for reasons
    unrelated to the ratchet (legacy finding shape, prose refs outside the block). So the recorded F1 is
    graded on its own: it was escalated only because "credit-service.ts" tripped the keyword backstop."""

    def test_recorded_f1_is_no_longer_protected(self):
        lead = json.loads((REPLAY / "lead-round-8.json").read_text())
        (f1,) = lead["findings"]
        errs, blocking = chk.check(lead, [chk.clean(f1["source_line"])], 8, True)
        self.assertEqual(errs, [])
        self.assertEqual(blocking, [])
        self.assertTrue(chk.PROTECTED.search(f1["source_line"]))
        self.assertFalse(f1["_protected"])


if __name__ == "__main__":
    unittest.main()
