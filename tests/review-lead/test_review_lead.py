"""Tests for the /goal Phase 4 review lead (LUCY-oawgi).

Run: python3 -m unittest discover -s ~/.claude/tests/review-lead -v

Two layers:
1. Replay: the tiy3q round-3 Astra verdict (7 P1s) was put through a fresh-context Opus lead
   following LEAD.md; its JSON is recorded in fixtures/tiy3q-r3/lead.json. The two-device findings
   must come out Dismissed with reasons; the single-device photo-switch P1 must stay Act on.
   The expected verdicts below were written BEFORE the replay ran.
2. The checker (review_lead_check.py): the deterministic rules that hold without Shane.
"""
import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "references" / "review-lead"))
import review_lead_check as chk  # noqa: E402

FIX = HERE / "fixtures" / "tiy3q-r3"

# Decided from the verdict text before the replay: each of these needs a second device or user
# writing the same face map at the same time (conflict / 409 / "both devices" / overlapping saves).
TWO_DEVICE = [
    "components/face-mapping/face-mapping-save.ts:57",
    "lib/stroke-queue-send.ts:85",
    "components/face-mapping/face-mapping-save.ts:83",
    "pages/api/photos/[photoId]/face-mapping.ts:201",
]
# One practitioner, one device: switch photo while a save is pending and B's map lands on A.
SINGLE_DEVICE_P1 = "lib/stroke-queue-send.ts:67"


def run_check(lead, verdict_text, round_no=1, scope_lock=False):
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        d = Path(d)
        (d / "lead.json").write_text(json.dumps(lead))
        (d / "verdict.txt").write_text(verdict_text)
        argv = ["--lead", str(d / "lead.json"), "--verdict", str(d / "verdict.txt"),
                "--out-dir", str(d / "out"), "--round", str(round_no)] + (["--scope-lock"] if scope_lock else [])
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = chk.main(argv)
        files = {p.name: p.read_text() for p in (d / "out").glob("*")} if (d / "out").exists() else {}
        return code, out.getvalue() + err.getvalue(), files


class TiY3QReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lead = json.loads((FIX / "lead.json").read_text())
        cls.verdict = (FIX / "astra-verdict-r3.txt").read_text()
        cls.by_loc = {}
        for f in cls.lead["findings"]:
            loc = f["source_line"].split()[1]
            cls.by_loc[loc] = f

    def test_checker_accepts_the_recorded_lead(self):
        code, out, files = run_check(self.lead, self.verdict, round_no=3, scope_lock=True)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=", out)

    def test_every_round3_finding_is_classified(self):
        self.assertEqual(len(self.lead["findings"]), 7)

    def test_two_device_findings_are_dismissed_with_reasons(self):
        for loc in TWO_DEVICE:
            f = self.by_loc[loc]
            self.assertEqual(f["verdict"], "dismissed", f"{loc}: {f}")
            self.assertGreater(len(f.get("reason", "")), 20, loc)

    def test_single_device_photo_switch_stays_act_on(self):
        f = self.by_loc[SINGLE_DEVICE_P1]
        self.assertEqual(f["verdict"], "act_on", f)
        self.assertTrue(f.get("clinic_scenario"))
        self.assertTrue(f.get("demonstrated"))

    def test_builder_input_contains_the_real_p1_and_no_two_device_item(self):
        code, out, files = run_check(self.lead, self.verdict, round_no=3, scope_lock=True)
        self.assertIn(SINGLE_DEVICE_P1, files["lead-actions-round-3.txt"])
        self.assertIs(self.by_loc[SINGLE_DEVICE_P1].get("in_scope"), True)
        self.assertNotIn(SINGLE_DEVICE_P1, files["lead-followups-round-3.txt"])
        for loc in TWO_DEVICE:
            self.assertNotIn(loc, files["lead-actions-round-3.txt"])
            self.assertIn(loc, files["lead-dismissals-round-3.md"])

    def test_premise_is_attacked_by_round_3(self):
        prem = self.lead.get("premise") or {}
        self.assertTrue(prem.get("premise"), prem)


VERDICT = """FAIL

P1 lib/photo-editor.ts:40 — Practitioner switches photo while Save is pending; B's map is written to A. Reproduced. — bind save to photo id
P1 lib/booking/slots.ts:88 — Online booking and reception grab the same slot at once; both succeed. Reproduced. — atomic claim
P2 components/map.tsx:12 — Two devices edit one map at once; last write wins. — merge
P3 lib/util.ts:5 — variable name unclear — rename
"""
LINES = [l for l in VERDICT.splitlines() if l.startswith("P")]


def base_lead():
    return {
        "bead": "AestheticcNext-test", "round": 1, "scope_lock": False,
        "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
        "findings": [
            {"id": "F1", "source_line": LINES[0], "verdict": "act_on",
             "clinic_scenario": "practitioner on the room iPad flips between before-photos mid-treatment",
             "demonstrated": "reviewer reproduced", "in_scope": True, "category": "single_device_data_loss"},
            {"id": "F2", "source_line": LINES[1], "verdict": "act_on",
             "clinic_scenario": "Saturday: client books online while reception books by phone",
             "demonstrated": "reviewer reproduced", "in_scope": False, "follow_up": "Atomic slot claim",
             "category": "booking"},
            {"id": "F3", "source_line": LINES[2], "verdict": "dismissed",
             "pattern": "multi-device concurrent edit of one clinical record",
             "reason": "needs two devices writing one face map at once; one practitioner, one device",
             "category": "clinical_record_concurrency"},
            {"id": "F4", "source_line": LINES[3], "verdict": "noted", "reason": "naming nit, not user-facing",
             "category": "style"},
        ],
    }


class CheckerRules(unittest.TestCase):
    def assertRefused(self, lead, needle, **kw):
        code, out, _ = run_check(lead, VERDICT, **kw)
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)

    def test_valid_lead_passes_and_only_act_on_reaches_builder(self):
        code, out, files = run_check(base_lead(), VERDICT)
        self.assertEqual(code, 0, out)
        acts = files["lead-actions-round-1.txt"]
        self.assertIn("photo-editor.ts:40", acts)
        self.assertNotIn("map.tsx:12", acts)
        self.assertIn("map.tsx:12", files["lead-dismissals-round-1.md"])

    def test_dismissal_without_reason_refused(self):
        lead = base_lead(); lead["findings"][2]["reason"] = ""
        self.assertRefused(lead, "needs a written reason")

    def test_booking_finding_cannot_be_dismissed(self):
        lead = base_lead()
        lead["findings"][1] = {"id": "F2", "source_line": LINES[1], "verdict": "dismissed", "category": "other",
                               "pattern": "multi-device concurrent edit of one clinical record",
                               "reason": "two actors at once never happens"}
        self.assertRefused(lead, "never-dismiss area")

    def test_payment_finding_cannot_be_noted(self):
        v = VERDICT + "P1 lib/pos/checkout.ts:9 — Terminal tap retried; client charged twice. — idempotency key\n"
        lead = base_lead()
        lead["findings"].append({"id": "F5", "source_line": v.splitlines()[-1], "verdict": "noted", "category": "other",
                                 "reason": "rare double tap, ignore"})
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_protected_consider_escalates(self):
        lead = base_lead()
        lead["findings"][1] = {"id": "F2", "source_line": LINES[1], "verdict": "consider", "category": "booking",
                               "reason": "not reproduced against real pg yet", "follow_up": "Slot claim repro"}
        code, out, files = run_check(lead, VERDICT)
        self.assertEqual(code, 0, out)
        self.assertIn("slots.ts:88", files["lead-escalate-round-1.txt"])
        self.assertIn("ESCALATE=1", out)

    def test_missing_finding_refused(self):
        lead = base_lead(); lead["findings"].pop(3)
        self.assertRefused(lead, "covered 0x")

    def test_invented_finding_refused(self):
        lead = base_lead()
        lead["findings"].append({"id": "F9", "source_line": "P1 lib/x.ts:1 — made up", "verdict": "noted", "category": "style",
                                 "reason": "this finding does not exist"})
        self.assertRefused(lead, "no reviewer made")

    def test_act_on_needs_scenario_and_demonstration(self):
        lead = base_lead(); lead["findings"][0]["demonstrated"] = ""
        self.assertRefused(lead, "needs 'demonstrated'")
        lead = base_lead(); lead["findings"][0]["clinic_scenario"] = " "
        self.assertRefused(lead, "needs a clinic_scenario")

    def test_unknown_pattern_refused(self):
        lead = base_lead(); lead["findings"][2]["pattern"] = "things that feel unlikely"
        self.assertRefused(lead, "not a heading")

    def test_scope_lock_out_of_scope_protected_escalates_not_builder(self):
        lead = base_lead(); lead["scope_lock"] = True
        code, out, files = run_check(lead, VERDICT, scope_lock=True)
        self.assertEqual(code, 0, out)
        self.assertNotIn("slots.ts:88", files["lead-actions-round-1.txt"])
        self.assertIn("slots.ts:88", files["lead-escalate-round-1.txt"])
        self.assertNotIn("slots.ts:88", files["lead-followups-round-1.txt"])
        self.assertIn("BLOCKING=1", out)

    def test_scope_lock_out_of_scope_unprotected_goes_to_follow_up(self):
        v = VERDICT + "P2 components/list.tsx:3 — pre-existing: list jumps on refresh. Reproduced. — keep scroll\n"
        lead = base_lead(); lead["scope_lock"] = True
        lead["findings"].append({"id": "F6", "source_line": v.splitlines()[-1], "verdict": "act_on",
                                 "category": "other", "clinic_scenario": "reception scrolling the day list",
                                 "demonstrated": "repro", "in_scope": False, "follow_up": "Keep list scroll"})
        code, out, files = run_check(lead, v, scope_lock=True)
        self.assertEqual(code, 0, out)
        self.assertIn("Keep list scroll", files["lead-followups-round-1.txt"])
        self.assertNotIn("list.tsx:3", files["lead-actions-round-1.txt"])

    def test_scope_lock_requires_in_scope_flag(self):
        lead = base_lead(); lead["scope_lock"] = True
        del lead["findings"][0]["in_scope"]
        self.assertRefused(lead, "needs in_scope", scope_lock=True)

    def test_scope_lock_mismatch_refused(self):
        self.assertRefused(base_lead(), "scope_lock", scope_lock=True)

    def test_more_than_five_blocking_refused_without_reason(self):
        extra = "".join(f"P2 lib/f{i}.ts:{i} — thing {i} breaks. Reproduced. — fix\n" for i in range(5))
        v = VERDICT + extra
        lead = base_lead()
        for i, line in enumerate(extra.splitlines()):
            lead["findings"].append({"id": f"X{i}", "source_line": line, "verdict": "act_on", "category": "other",
                                     "clinic_scenario": "reception at check-in", "demonstrated": "repro"})
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("not filtering", out)
        lead["act_on_over_cap_reason"] = "each is a separate reproduced break on the bead's own path"
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 0, out)

    def test_round_two_requires_premise(self):
        lead = base_lead(); del lead["premise"]
        self.assertRefused(lead, "premise block required", round_no=2)

    def test_p1_cannot_be_noted(self):
        # Astra r2: Noted was a back door for dropping a P1 with no pattern.
        lead = base_lead()
        lead["findings"][0] = {"id": "F1", "source_line": LINES[0], "verdict": "noted",
                               "category": "other", "reason": "unusual tap sequence, leave it"}
        self.assertRefused(lead, "only a P3 may be Noted")

    def test_missing_category_refused(self):
        lead = base_lead(); del lead["findings"][3]["category"]
        self.assertRefused(lead, "category")

    def test_protected_category_cannot_be_dismissed_even_without_keywords(self):
        lead = base_lead()
        lead["findings"][0] = {"id": "F1", "source_line": LINES[0], "verdict": "dismissed",
                               "category": "single_device_data_loss",
                               "pattern": "multi-device concurrent edit of one clinical record",
                               "reason": "the reviewer framed it as a race so it is two-device"}
        self.assertRefused(lead, "never-dismiss area")

    def test_p1_dismissal_needs_a_pattern(self):
        lead = base_lead()
        lead["findings"][0] = {"id": "F1", "source_line": LINES[0], "verdict": "dismissed",
                               "category": "other", "reason": "feels unlikely in a clinic setting"}
        self.assertRefused(lead, "needs a DISMISSAL_PATTERNS heading")

    def test_mislabelled_keyword_finding_still_protected(self):
        v = VERDICT + "P2 lib/x.ts:4 — reception sees another clinic's businessId in the list — scope query\n"
        lead = base_lead()
        lead["findings"].append({"id": "F7", "source_line": v.splitlines()[-1], "verdict": "dismissed",
                                 "category": "style", "reason": "cosmetic, not important for clinics"})
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_empty_verdict_refused(self):
        code, out, _ = run_check(base_lead(), "")
        self.assertEqual(code, 1, out)
        self.assertIn("no PASS/FAIL", out)

    def test_fail_with_no_parseable_findings_refused(self):
        code, out, _ = run_check(base_lead(), "FAIL\n\nSee above.\n")
        self.assertEqual(code, 1, out)
        self.assertIn("no finding lines parsed", out)

    def test_bold_severity_is_parsed(self):
        v = VERDICT.replace("P3 lib/util.ts:5", "**P3** lib/util.ts:5")
        code, out, _ = run_check(base_lead(), v)
        self.assertEqual(code, 0, out)

    def test_unrecognised_finding_format_refused(self):
        v = VERDICT + "Finding (P1): lib/z.ts:2 deletes a payment\n"
        code, out, _ = run_check(base_lead(), v)
        self.assertEqual(code, 1, out)
        self.assertIn("unrecognised finding format", out)

    def test_every_seed_pattern_parses(self):
        names = chk.pattern_names()
        self.assertIn("multi-device concurrent edit of one clinical record", names)


if __name__ == "__main__":
    unittest.main()
