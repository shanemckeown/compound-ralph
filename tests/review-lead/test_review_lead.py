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
from datetime import datetime, timezone
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
                                 "ordinary_use": "reception refreshes the day list once while scrolled down",
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
                                     "clinic_scenario": "reception at check-in", "demonstrated": "repro",
                                     "ordinary_use": "reception checks a client in with one tap"})
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


class OrdinaryUseTriage(unittest.TestCase):
    """LUCY-r1lyy (Shane, 7 Oct 2026): a finding blocks only if one person using the app normally
    can trigger it; money, patient data, auth, tenancy and what a user sees stay strict however rare."""
    RARE = "P2 lib/notes/save.ts:30 — Two receptionists edit one client's notes in the same second; last write wins. — merge"
    SEND = "P2 lib/sms/compose.ts:12 — Second quick click on Send queues a duplicate reminder SMS to the client. — debounce"
    SCREEN = "P3 components/client/header.tsx:8 — After two unusual tab switches the header shows the previous client's name. — key by id"
    OLD = "P2 lib/export/csv.ts:4 — pre-existing: CSV export drops the last row (not touched by this branch). Reproduced. — fix loop"

    def lead_with(self, *extra):
        v = VERDICT + "".join(line + "\n" for line, _ in extra)
        lead = base_lead()
        lead["findings"] += [f for _, f in extra]
        return lead, v

    def test_no_ordinary_use_path_is_demoted_to_p4(self):
        lead, v = self.lead_with((self.RARE, {
            "id": "R1", "source_line": self.RARE, "verdict": "act_on", "category": "clinical_record_concurrency",
            "clinic_scenario": "two receptionists, two PCs, same client, same second",
            "demonstrated": "reviewer traced it", "carve_out": False,
            "ordinary_use": "none: needs two people editing the same record at the same moment"}))
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertNotIn("notes/save.ts:30", files["lead-actions-round-1.txt"])
        self.assertNotIn("notes/save.ts:30", files["lead-followups-round-1.txt"])
        self.assertNotIn("notes/save.ts:30", files["lead-escalate-round-1.txt"])
        self.assertIn("p4 note", files["lead-dismissals-round-1.md"])
        self.assertIn("notes/save.ts:30", files["lead-dismissals-round-1.md"])
        self.assertIn("P4=1", out)

    def test_missing_both_fields_is_demoted(self):
        lead, v = self.lead_with((self.RARE, {
            "id": "R1", "source_line": self.RARE, "verdict": "consider", "category": "other",
            "reason": "might matter one day", "follow_up": "Merge note edits"}))
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertNotIn("Merge note edits", files["lead-followups-round-1.txt"])
        self.assertIn("P4=1", out)

    def test_carve_out_is_never_demoted_however_rare(self):
        # user-facing (what the user is told), flagged by the lead and by category
        lead, v = self.lead_with((self.SCREEN, {
            "id": "S1", "source_line": self.SCREEN, "verdict": "act_on", "category": "user_facing",
            "clinic_scenario": "practitioner flips between two client tabs on the room laptop",
            "demonstrated": "repro", "carve_out": True,
            "ordinary_use": "none: needs an unusual sequence of tab switches"}))
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertIn("header.tsx:8", files["lead-actions-round-1.txt"])
        self.assertIn("P4=0", out)

    def test_carve_out_by_keyword_not_demoted_even_if_lead_says_no(self):
        # SMS send is protected by keyword: the lead's carve_out=false and missing path can't demote it.
        lead, v = self.lead_with((self.SEND, {
            "id": "D1", "source_line": self.SEND, "verdict": "act_on", "category": "other",
            "clinic_scenario": "receptionist double-clicks Send on a reminder",
            "demonstrated": "repro", "carve_out": False}))
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertIn("compose.ts:12", files["lead-actions-round-1.txt"])

    def test_ordinary_use_path_keeps_a_non_carve_out_finding(self):
        lead, v = self.lead_with((self.RARE, {
            "id": "R1", "source_line": self.RARE, "verdict": "act_on", "category": "other",
            "clinic_scenario": "practitioner saves a note then edits again", "demonstrated": "repro",
            "carve_out": False, "ordinary_use": "one practitioner saves a note twice in a row on one device"}))
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertIn("notes/save.ts:30", files["lead-actions-round-1.txt"])
        self.assertIn("P4=0", out)

    def test_pre_existing_finding_goes_to_a_new_bead_not_the_branch(self):
        lead, v = self.lead_with((self.OLD, {
            "id": "O1", "source_line": self.OLD, "verdict": "act_on", "category": "other",
            "clinic_scenario": "owner exports clients at month end", "demonstrated": "reproduced",
            "ordinary_use": "owner presses Export once", "carve_out": False,
            "pre_existing": True, "follow_up": "CSV export drops the last row"}))
        code, out, files = run_check(lead, v)  # no scope lock: still routed out
        self.assertEqual(code, 0, out)
        self.assertNotIn("csv.ts:4", files["lead-actions-round-1.txt"])
        self.assertIn("CSV export drops the last row", files["lead-followups-round-1.txt"])
        self.assertIn("act_on (pre-existing)", files["lead-dismissals-round-1.md"])

    def test_pre_existing_protected_finding_escalates(self):
        old = "P2 lib/pos/refund.ts:9 — pre-existing: refund records twice on webhook retry. Reproduced. — idempotency"
        lead, v = self.lead_with((old, {
            "id": "O2", "source_line": old, "verdict": "act_on", "category": "money",
            "clinic_scenario": "reception refunds a client", "demonstrated": "reproduced",
            "pre_existing": True, "follow_up": "Refund idempotency on webhook retry"}))
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertNotIn("refund.ts:9", files["lead-actions-round-1.txt"])
        self.assertIn("refund.ts:9", files["lead-escalate-round-1.txt"])

    def test_pre_existing_without_follow_up_refused(self):
        lead, v = self.lead_with((self.OLD, {
            "id": "O1", "source_line": self.OLD, "verdict": "act_on", "category": "other",
            "clinic_scenario": "owner exports clients", "demonstrated": "reproduced",
            "ordinary_use": "owner presses Export once", "pre_existing": True}))
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("pre_existing act_on needs a follow_up", out)

    def test_demotion_does_not_reopen_never_dismiss(self):
        # A protected finding the lead tries to Note is still refused; demotion is not a back door.
        v = VERDICT + "P2 lib/pos/checkout.ts:9 — Terminal retry charges twice. — idempotency key\n"
        lead = base_lead()
        lead["findings"].append({"id": "F5", "source_line": v.splitlines()[-1], "verdict": "noted",
                                 "category": "other", "carve_out": False,
                                 "ordinary_use": "none: rare", "reason": "rare double charge, ignore"})
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)



class DuplicatePathH_D0(unittest.TestCase):
    """LUCY-fuu2h: H-D0 duplicate path is P2, blocks landing, and has no clinic-scenario filter."""
    DUP = ("P3 lib/contacts/format-phone.ts:1 — H-D0 duplicate path: a tenth phone normaliser beside "
           "lib/utils/phone.ts:12, which already normalises UK numbers — call the existing one")

    def lead_with(self, finding):
        v = VERDICT + self.DUP + "\n"
        lead = base_lead()
        lead["findings"].append(dict({"id": "D0", "source_line": self.DUP}, **finding))
        return lead, v

    def test_h_d0_act_on_without_scenario_passes_and_blocks_as_p2(self):
        lead, v = self.lead_with({"verdict": "act_on", "category": "duplicate_path",
                                  "demonstrated": "existing path lib/utils/phone.ts:12 does the same job",
                                  "ordinary_use": "none: structural", "carve_out": False})
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        acts = files["lead-actions-round-1.txt"]
        self.assertIn("format-phone.ts:1", acts)
        self.assertIn("graded: P2 (H-D0", acts)
        self.assertIn("BLOCKING=3", out)  # base lead blocks 2 (no scope lock) + the H-D0
        self.assertIn("P4=0", out)

    def test_h_d0_found_by_text_even_if_category_is_other(self):
        lead, v = self.lead_with({"verdict": "act_on", "category": "other",
                                  "demonstrated": "traced", "ordinary_use": "none: structural"})
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertIn("format-phone.ts:1", files["lead-actions-round-1.txt"])

    def test_h_d0_cannot_be_noted_even_when_reviewer_wrote_p3(self):
        lead, v = self.lead_with({"verdict": "noted", "category": "duplicate_path",
                                  "reason": "small, tidy it up later"})
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("H-D0 duplicate path is graded P2", out)

    def test_h_d0_can_be_dismissed_with_a_reason(self):
        lead, v = self.lead_with({"verdict": "dismissed", "category": "duplicate_path",
                                  "reason": "plan's EXISTING CAPABILITY recorded why extending lib/utils/phone.ts fails"})
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertNotIn("format-phone.ts:1", files["lead-actions-round-1.txt"])

    def test_money_h_d0_still_cannot_be_dismissed(self):
        line = ("P2 lib/consult/ledger.ts:1 — H-D0 duplicate path: consult credit table beside account "
                "credit at lib/account-credit.ts:40 — extend account credit with an expiry column")
        v = VERDICT + line + "\n"
        lead = base_lead()
        lead["findings"].append({"id": "D1", "source_line": line, "verdict": "dismissed",
                                 "category": "duplicate_path", "reason": "it is only a second ledger, fine"})
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)

    def test_h_d0_consider_added_by_branch_escalates_not_follow_up(self):
        lead, v = self.lead_with({"verdict": "consider", "category": "duplicate_path",
                                  "reason": "not sure the existing normaliser covers mobiles",
                                  "follow_up": "Fold format-phone into lib/utils/phone.ts"})
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertIn("format-phone.ts:1", files["lead-escalate-round-1.txt"])
        self.assertNotIn("format-phone.ts:1", files["lead-followups-round-1.txt"])

    def test_h_d0_out_of_scope_under_lock_escalates(self):
        lead, v = self.lead_with({"verdict": "act_on", "category": "duplicate_path", "in_scope": False,
                                  "demonstrated": "existing lib/utils/phone.ts:12",
                                  "follow_up": "Fold format-phone into lib/utils/phone.ts"})
        lead["scope_lock"] = True
        for f in lead["findings"][:2]:
            f["in_scope"] = f.get("in_scope", True)
        code, out, files = run_check(lead, v, scope_lock=True)
        self.assertEqual(code, 0, out)
        self.assertIn("format-phone.ts:1", files["lead-escalate-round-1.txt"])
        self.assertNotIn("format-phone.ts:1", files["lead-followups-round-1.txt"])

    def test_pre_existing_h_d0_consider_is_a_follow_up(self):
        lead, v = self.lead_with({"verdict": "consider", "category": "duplicate_path", "pre_existing": True,
                                  "reason": "the duplicate predates this branch",
                                  "follow_up": "Fold format-phone into lib/utils/phone.ts"})
        code, out, files = run_check(lead, v)
        self.assertEqual(code, 0, out)
        self.assertIn("format-phone.ts:1", files["lead-followups-round-1.txt"])
        self.assertNotIn("format-phone.ts:1", files["lead-escalate-round-1.txt"])

    def test_control_non_h_d0_act_on_still_needs_a_scenario(self):
        lead = base_lead(); lead["findings"][0]["clinic_scenario"] = ""
        code, out, _ = run_check(lead, VERDICT)
        self.assertEqual(code, 1, out)
        self.assertIn("needs a clinic_scenario", out)


if __name__ == "__main__":
    unittest.main()


# --- LUCY-p4pjf: structured verdicts, prose tolerance, orchestrator clearance -----------------

FIX_Q31 = HERE / "fixtures" / "q31af-r5"
FIX_Q41 = HERE / "fixtures" / "q41ar-r10"
CLEARED_E1XGJ = "ORCHESTRATOR CLEARED F1 (AestheticcNext-e1xgj) @7649f85281: pre-existing race class, rounds stopped"


def run_files(lead, verdicts, round_no=1, extra=(), comments=None, verdict_mtime=None):
    """Like run_check, but takes real verdict file texts, extra flags and bd comment text."""
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        d = Path(d)
        (d / "lead.json").write_text(json.dumps(lead))
        argv = ["--lead", str(d / "lead.json"), "--out-dir", str(d / "out"), "--round", str(round_no)]
        for i, text in enumerate(verdicts):
            vp = d / f"verdict-{i}.txt"
            vp.write_text(text)
            if verdict_mtime is not None:
                import os
                os.utime(vp, (verdict_mtime, verdict_mtime))
            argv += ["--verdict", str(vp)]
        if comments is not None:
            (d / "comments.txt").write_text(comments)
            argv += ["--comments-file", str(d / "comments.txt")]
        argv += list(extra)
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = chk.main(argv)
        files = {p.name: p.read_text() for p in (d / "out").glob("*")} if (d / "out").exists() else {}
        return code, out.getvalue() + err.getvalue(), files


def bd_comment(ts, body):
    return f"[Encrypted-S] at {ts}\n" + "".join(f"  {line}\n" for line in body.splitlines()) + "\n"


# 22 Oct 21:14 UTC: the r10 review file's real mtime; comments are stamped in UTC minutes.
R10_MTIME = datetime(2026, 10, 22, 21, 14, 30, tzinfo=timezone.utc).timestamp()


class ProseTolerance(unittest.TestCase):
    def test_q31af_unnormalised_sonnet_file_is_accepted(self):
        """Refused twice on 22 Oct for its '(no P1/P2 findings)' line; people then edited it."""
        lead = json.loads((FIX_Q31 / "lead-round-5.json").read_text())
        lead["findings"] = [f for f in lead["findings"] if f.get("reviewer") != "astra-xhigh"]
        code, out, _ = run_files(lead, [(FIX_Q31 / "sonnet.orig.txt").read_text()], round_no=5)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)

    def test_bold_verdict_line_is_a_verdict(self):
        v = VERDICT.replace("FAIL\n", "**Verdict: FAIL**\n", 1)
        code, out, _ = run_check(base_lead(), v)
        self.assertEqual(code, 0, out)

    def test_summary_counts_must_agree_with_parsed_findings(self):
        v = VERDICT.replace("FAIL\n", "FAIL\n0 P1, 1 P2, 1 P3 overall\n", 1)
        code, out, _ = run_check(base_lead(), v)
        self.assertEqual(code, 1, out)
        self.assertIn("summary says 0 P1 but 2 P1", out)

    def test_numbered_or_bracketed_finding_still_refused(self):
        for line in ("1. [P1] lib/z.ts:2 deletes a payment\n", "- (P2) lib/z.ts:2 races\n"):
            code, out, _ = run_check(base_lead(), VERDICT + line)
            self.assertEqual(code, 1, line)
            self.assertIn("unrecognised finding format", out)


class StructuredVerdict(unittest.TestCase):
    def block(self, data):
        return "Reviewer prose about the diff, no findings here.\n```review-verdict\n" + \
               json.dumps(data) + "\n```\n"

    def test_structured_block_is_the_whole_input(self):
        findings = [{"severity": l.split()[0], "text": l.split(" ", 1)[1]} for l in LINES]
        code, out, _ = run_check(base_lead(), self.block({"verdict": "FAIL", "findings": findings}))
        self.assertEqual(code, 0, out)

    def test_structured_pass_with_no_findings(self):
        lead = base_lead()
        lead["findings"] = []
        code, out, _ = run_check(lead, self.block({"verdict": "PASS", "findings": []}))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)

    def test_structured_fail_without_findings_or_bad_json_refused(self):
        for text, needle in ((self.block({"verdict": "FAIL", "findings": []}), "lists no findings"),
                             ("```review-verdict\n{not json\n```\n", "not valid JSON"),
                             (self.block({"verdict": "MAYBE"}), "needs \"verdict\""),
                             (self.block({"verdict": "FAIL", "findings": [{"severity": "P9", "text": "x"}]}),
                              "needs severity")):
            code, out, _ = run_check(base_lead(), text)
            self.assertEqual(code, 1, text)
            self.assertIn(needle, out)

    def test_structured_finding_missing_from_lead_refused(self):
        lead = base_lead()
        lead["findings"] = lead["findings"][:3]
        findings = [{"severity": l.split()[0], "text": l.split(" ", 1)[1]} for l in LINES]
        code, out, _ = run_check(lead, self.block({"verdict": "FAIL", "findings": findings}))
        self.assertEqual(code, 1, out)
        self.assertIn("covered 0x", out)


class ClearedEscalation(unittest.TestCase):
    """q41ar @7649f85281 r10: one pre-existing webhook-race P2 (F1 -> e1xgj) escalates forever."""

    @classmethod
    def setUpClass(cls):
        cls.lead = json.loads((FIX_Q41 / "lead-round-10.json").read_text())
        cls.review = (FIX_Q41 / "review.md").read_text()

    def run_r10(self, comments=None, cleared=("F1",), lead=None):
        extra = ["--bead", "AestheticcNext-q41ar"] + [x for c in cleared for x in ("--cleared-escalation", c)]
        return run_files(lead or copy.deepcopy(self.lead), [self.review], round_no=10, extra=extra,
                         comments=comments, verdict_mtime=R10_MTIME)

    def test_real_r10_escalates_without_clearance(self):
        code, out, _ = run_files(copy.deepcopy(self.lead), [self.review], round_no=10, verdict_mtime=R10_MTIME)
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=1", out)

    def test_matching_orchestrator_comment_clears_it(self):
        code, out, files = self.run_r10(bd_comment("2026-10-23 09:40", CLEARED_E1XGJ))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("CLEARED=1", out)
        self.assertIn("CLEARED F1: ORCHESTRATOR CLEARED F1", out)
        self.assertEqual(files["lead-escalate-round-10.txt"], "")
        self.assertIn("e1xgj", files["lead-cleared-round-10.txt"])
        self.assertIn("cleared by the orchestrator", files["lead-dismissals-round-10.md"])

    def test_same_minute_as_the_verdict_does_not_count(self):
        """Astra r2: bd times are minutes; 21:14 may be before a verdict modified at 21:14:30."""
        code, out, _ = self.run_r10(bd_comment("2026-10-22 21:14", CLEARED_E1XGJ))
        self.assertEqual(code, 1, out)
        code, out, _ = self.run_r10(bd_comment("2026-10-22 21:15", CLEARED_E1XGJ))
        self.assertEqual(code, 0, out)

    def test_no_comment_refuses(self):
        code, out, _ = self.run_r10(bd_comment("2026-10-23 09:40", "PASS @7649f85281"))
        self.assertEqual(code, 1, out)
        self.assertIn("clearance not verified", out)

    def test_comment_for_a_different_finding_or_follow_up_refuses(self):
        for body in ("ORCHESTRATOR CLEARED F2 (AestheticcNext-e1xgj)",   # other finding id
                     "ORCHESTRATOR CLEARED F1 (LUCY-e1xgj)",             # other bead prefix
                     "ORCHESTRATOR CLEARED F1 (AestheticcNext-e1xgj.1)", # a child bead
                     "ORCHESTRATOR CLEARED F1 (e1xgj)",                  # suffix alone
                     "ORCHESTRATOR CLEARED F1 (AestheticcNext-hgyyd)",   # r9's F1, other follow-up
                     "ORCHESTRATOR CLEARED F11 (AestheticcNext-e1xgj)",  # id must be a whole token
                     "Note: ORCHESTRATOR CLEARED F1 (AestheticcNext-e1xgj)"):  # not the first line
            code, out, _ = self.run_r10(bd_comment("2026-10-23 09:40", body))
            self.assertEqual(code, 1, body)

    def test_comment_older_than_the_verdict_refuses(self):
        code, out, _ = self.run_r10(bd_comment("2026-10-22 21:13", CLEARED_E1XGJ))
        self.assertEqual(code, 1, out)
        self.assertIn("dated after the verdict files", out)

    def test_branch_own_finding_cannot_be_cleared(self):
        lead = copy.deepcopy(self.lead)
        lead["findings"][0]["pre_existing"] = False
        lead["findings"][0]["verdict"] = "consider"
        code, out, _ = self.run_r10(bd_comment("2026-10-23 09:40", CLEARED_E1XGJ), lead=lead)
        self.assertEqual(code, 1, out)
        self.assertIn("only a pre_existing finding", out)

    def test_follow_up_without_bead_id_cannot_be_cleared(self):
        lead = copy.deepcopy(self.lead)
        lead["findings"][0]["follow_up"] = "Instagram webhook vs erasure race"
        code, out, _ = self.run_r10(bd_comment("2026-10-23 09:40", CLEARED_E1XGJ), lead=lead)
        self.assertEqual(code, 1, out)
        self.assertIn("names no bead id", out)

    def test_unknown_id_and_missing_bead_refuse(self):
        code, out, _ = self.run_r10(bd_comment("2026-10-23 09:40", CLEARED_E1XGJ), cleared=("F9",))
        self.assertEqual(code, 1, out)
        self.assertIn("not an escalated finding", out)
        code, out, _ = run_files(copy.deepcopy(self.lead), [self.review], round_no=10,
                                 extra=["--cleared-escalation", "F1"])
        self.assertEqual(code, 1, out)
        self.assertIn("needs --bead", out)

    def test_booking_dismissal_backstop_unchanged_by_clearance(self):
        lead = copy.deepcopy(self.lead)
        lead["findings"][0]["verdict"] = "dismissed"
        code, out, _ = self.run_r10(bd_comment("2026-10-23 09:40", CLEARED_E1XGJ), lead=lead)
        self.assertEqual(code, 1, out)
        self.assertIn("never-dismiss area", out)


class AstraR2Checker(unittest.TestCase):
    """Astra high review round 2, 23 Oct: each reproduced a silent drop or a wrong clearance."""

    def test_duplicate_finding_ids_refused(self):
        lead = base_lead()
        lead["findings"][1]["id"] = "F1"
        code, out, _ = run_check(lead, VERDICT)
        self.assertEqual(code, 1, out)
        self.assertIn("finding id 'F1' is used 2x", out)

    def test_verdict_after_the_structured_block_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\nFAIL\nP1 lib/x.ts:1 — late — fix\n'
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)
        self.assertIn("after the last review-verdict block", out)

    def test_finding_on_a_file_without_extension_refused(self):
        code, out, _ = run_check(base_lead(), VERDICT + "Finding (P1): Dockerfile:12 runs as root\n")
        self.assertEqual(code, 1, out)
        self.assertIn("unrecognised finding format", out)

    def test_time_of_day_is_not_a_file_line(self):
        code, out, _ = run_check(base_lead(), VERDICT.replace("FAIL\n", "FAIL\n(no P1/P2 at 21:14)\n", 1))
        self.assertEqual(code, 0, out)

    def test_single_count_summary_is_a_floor(self):
        code, out, _ = run_check(base_lead(), VERDICT.replace("FAIL\n", "FAIL\nSummary: 3 P1\n", 1))
        self.assertEqual(code, 1, out)
        self.assertIn("summary says 3 P1", out)
        code, out, _ = run_check(base_lead(), VERDICT.replace("FAIL\n", "FAIL\nAstra: 1 P1\n", 1))
        self.assertEqual(code, 0, out)


class AstraR3Checker(unittest.TestCase):
    """Astra high review round 3, 23 Oct."""

    def test_finding_line_after_a_bold_hazards_heading_refused(self):
        lead = base_lead()
        lead["findings"] = []
        code, out, _ = run_check(lead, "PASS\n**HAZARDS**\nP1 lib/pay.ts:9 — double charge — fix\n")
        self.assertEqual(code, 1, out)
        self.assertIn("finding line after the HAZARDS section", out)

    def test_evidence_prose_after_verified_ok_still_accepted(self):
        lead = base_lead()
        lead["findings"] = []
        text = "PASS\nVERIFIED OK\n- H-P2/H-C1: unchanged send path at lib/sms.ts:40; 0 P1 found\n"
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 0, out)

    def test_trailing_content_after_structured_block_refused(self):
        lead = base_lead()
        lead["findings"] = []
        block = '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n'
        for tail in ("Finding (P1): Dockerfile:12 runs as root\n", "Summary: 1 P1\n"):
            code, out, _ = run_check(lead, block + tail)
            self.assertEqual(code, 1, tail)

    def test_hyphen_suffixed_ids_do_not_match(self):
        self.assertFalse(chk.token_in("F1", "ORCHESTRATOR CLEARED F1-old (AestheticcNext-e1xgj)"))
        self.assertFalse(chk.token_in("LUCY-abc12", "ORCHESTRATOR CLEARED F1 (LUCY-abc12-old)"))
        self.assertTrue(chk.token_in("LUCY-abc12", "ORCHESTRATOR CLEARED F1 (LUCY-abc12): ok"))


class AstraR4Checker(unittest.TestCase):
    def test_wrapped_labelled_finding_without_location_refused(self):
        v = VERDICT + "Finding (P1): Retry charges the patient twice\n  at lib/pay.ts:9\n"
        code, out, _ = run_check(base_lead(), v)
        self.assertEqual(code, 1, out)
        lead = base_lead()
        lead["findings"] = []
        code, out, _ = run_check(lead, "PASS\nUNVERIFIED\nFinding (P1): lib/pay.ts:9 double charge\n")
        self.assertEqual(code, 1, out)

    def test_hazard_ids_with_colons_are_not_findings(self):
        lead = base_lead()
        lead["findings"] = []
        code, out, _ = run_check(lead, "PASS\nHAZARDS\n- H-P0, H-P1: no new sink; H-B2: unchanged\n")
        self.assertEqual(code, 0, out)


class AstraR5Checker(unittest.TestCase):
    def test_unclosed_structured_block_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = ('```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n'
                '```review-verdict\n{\n  "verdict": "FAIL",\n  "findings": [{"severity": "P1", "text": "x"}]\n}\n')
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)
        self.assertIn("not closed", out)


class AstraR6Checker(unittest.TestCase):
    def test_prose_fail_before_a_json_pass_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = ("REVIEWING: goal/x @ abcdef1234\nFAIL\nP1 lib/pay.ts:9 — double charge — fix\n1 P1, 0 P2, 0 P3\n"
                '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n')
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)

    def test_agreeing_prose_and_block_accepted(self):
        findings = [{"severity": l.split()[0], "text": l.split(" ", 1)[1]} for l in LINES]
        text = "REVIEWING: goal/x @ abcdef1234\n" + VERDICT + "```review-verdict\n" + \
               json.dumps({"verdict": "FAIL", "findings": findings}) + "\n```\n"
        code, out, _ = run_check(base_lead(), text)
        self.assertEqual(code, 0, out)


class AstraR7Checker(unittest.TestCase):
    def test_odd_shaped_prose_finding_before_block_refused(self):
        lead = base_lead()
        lead["findings"] = []
        for line in ("Finding (P1): retry charges twice", "[P1] lib/pay.ts:9 double charge", "1. P1 lib/pay.ts:9 x"):
            text = (f"REVIEWING: goal/x @ abcdef1234\nFAIL\n{line}\n"
                    '```review-verdict\n{"verdict": "FAIL", "findings": [{"severity": "P3", "text": "lib/u.ts:1 — n — r"}]}\n```\n')
            code, out, _ = run_check(lead, text)
            self.assertEqual(code, 1, line)


class AstraR8Checker(unittest.TestCase):
    def test_real_severity_after_a_hazard_id_after_stop_refused(self):
        lead = base_lead()
        lead["findings"] = []
        code, out, _ = run_check(lead, "PASS\nHAZARDS\n- H-M4: P1 lib/pay.ts:9 — retry charges twice — fix\n")
        self.assertEqual(code, 1, out)

    def test_hazard_id_evidence_with_location_accepted(self):
        lead = base_lead()
        lead["findings"] = []
        code, out, _ = run_check(lead, "PASS\nVERIFIED OK\n- H-P1: lib/log.ts:12 emits only IDs\n")
        self.assertEqual(code, 0, out)
        block = '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n'
        code, out, _ = run_check(lead, "REVIEWING: goal/x @ abcdef1234\nPASS\nVERIFIED OK\n- H-P1: lib/log.ts:12 ids\n" + block)
        self.assertEqual(code, 0, out)


class AstraR9Checker(unittest.TestCase):
    def test_quoted_pass_output_after_a_fail_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = ("REVIEWING: goal/x @ abcdef1234\nFAIL\nP1 lib/pay.ts:9 — double charge — fix\nVERIFIED OK\n"
                "```\nVerdict: PASS\n```\n")
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)
        self.assertIn("conflicting verdict lines", out)

    def test_counts_inside_a_finding_description_are_not_a_tally(self):
        lead = base_lead()
        v = VERDICT.replace("P2 components/map.tsx:12 — Two devices edit one map at once; last write wins. — merge",
                            "P2 components/map.tsx:12 — Two devices edit one map at once; 2 P1 rows vanish. — merge")
        lead["findings"][2]["source_line"] = [l for l in v.splitlines() if l.startswith("P2")][0]
        code, out, _ = run_check(lead, v)
        self.assertEqual(code, 0, out)


class AstraR10Checker(unittest.TestCase):
    def test_later_quoted_same_verdict_cannot_restart_parsing(self):
        lead = base_lead()
        lead["findings"] = []
        text = ("REVIEWING: goal/x @ abcdef1234\nFAIL\nP1 lib/pay.ts:9 — double charge — fix\nVERIFIED OK\n"
                "**Verdict: FAIL**\nP3 lib/u.ts:1 — name — rename\n")
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)


class AstraR12Checker(unittest.TestCase):
    def test_markdown_verdict_label_contradicting_the_block_refused(self):
        lead = base_lead()
        lead["findings"] = []
        for decl in ("**Verdict:** FAIL", "Verdict — FAIL", "Final verdict is FAIL here"):
            text = f"REVIEWING: goal/x @ abcdef1234\n{decl}\n" + '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n'
            code, out, _ = run_check(lead, text)
            self.assertEqual(code, 1, decl)


class AstraR13Checker(unittest.TestCase):
    def test_trailing_verdict_declaration_after_block_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\nFinal verdict: FAIL — retry still charges twice.\n'
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)


class AstraR15Checker(unittest.TestCase):
    TIP = "7649f85281ef8012cae27cd56d1a3f6cea301c2c"

    def test_appended_fail_then_pass_responses_refused(self):
        lead = base_lead()
        lead["findings"] = []
        echo = f"REVIEWING: goal/aestheticcnext-q41ar @ {self.TIP}\n"
        fail = (echo + "FAIL\nP1 lib/pay.ts:9 — retry charges twice — guard\n"
                '```review-verdict\n{"verdict": "FAIL", "findings": [{"severity": "P1", '
                '"text": "lib/pay.ts:9 — retry charges twice — guard"}]}\n```\n')
        ok = echo + 'PASS\n```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n'
        code, out, _ = run_check(lead, fail + ok)
        self.assertEqual(code, 1, out)
        self.assertIn("outside the parsed response", out)

    def test_finding_above_the_verdict_line_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = (f"REVIEWING: goal/aestheticcnext-q41ar @ {self.TIP}\n"
                "P1 lib/pay.ts:9 — retry charges twice — guard\nVerdict: PASS\n")
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)
        self.assertIn("outside the parsed response", out)

    def test_repeated_identical_answer_still_accepted(self):
        lead = base_lead()
        lead["findings"] = []
        answer = f"REVIEWING: goal/aestheticcnext-q41ar @ {self.TIP}\nPASS\nVERIFIED OK\n- tests\n"
        code, out, _ = run_check(lead, answer + answer)
        self.assertEqual(code, 0, out)


R9_FIX = Path(__file__).resolve().parent / "fixtures" / "q41ar-r9" / "review.md"
SHA_A = "d60331cb15581e5dc1ff7f6551f050e10e6c5550"
SHA_B = "7649f85281ef8012cae27cd56d1a3f6cea301c2c"


def run_named(lead, named, extra=()):
    """run_check over verdict files with real names and mtimes: [(name, text, mtime)]."""
    import os
    with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
        d = Path(d)
        (d / "lead.json").write_text(json.dumps(lead))
        argv = ["--lead", str(d / "lead.json"), "--out-dir", str(d / "out"), "--round", "2"]
        for name, text, mtime in named:
            (d / name).write_text(text)
            os.utime(d / name, (mtime, mtime))
            argv += ["--verdict", str(d / name)]
        argv = [a.replace("@D@", str(d)) for a in argv + list(extra)]
        lead_s = json.dumps(lead).replace("@D@", str(d))
        (d / "lead.json").write_text(lead_s)
        err, out = io.StringIO(), io.StringIO()
        with redirect_stderr(err), redirect_stdout(out):
            code = chk.main(argv)
        return code, out.getvalue() + err.getvalue()


class OrchestratorGapVerifiedNotes(unittest.TestCase):
    """Orchestrator20, 23 Oct gap (1): the r9 author's own VERIFIED OK note is not a hidden finding."""

    def test_real_r9_file_parses(self):
        found = chk.reviewer_findings([str(R9_FIX)])
        self.assertEqual([f[:2] for f in found], ["P2", "P3"])

    def note_file(self, note):
        return (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP3 lib/a.ts:3 — minor — fix\nVERIFIED OK\n{note}\n"
                "UNVERIFIED\n- live\n")

    def test_note_with_unlisted_severity_still_refused(self):
        p = Path(tempfile.mkdtemp(dir="/private/tmp")) / "v.md"
        p.write_text(self.note_file("- **my check.** P1 lib/pay.ts:9 still charges twice"))
        with self.assertRaises(ValueError):
            chk.reviewer_findings([str(p)])

    def test_canonical_finding_in_verified_ok_still_refused(self):
        p = Path(tempfile.mkdtemp(dir="/private/tmp")) / "v.md"
        p.write_text(self.note_file("- P3 lib/b.ts:4 — another minor — fix"))
        with self.assertRaises(ValueError):
            chk.reviewer_findings([str(p)])

    def test_note_about_a_listed_severity_tolerated(self):
        p = Path(tempfile.mkdtemp(dir="/private/tmp")) / "v.md"
        p.write_text(self.note_file("- **The P3, my own check.** `lib/a.ts:3` is unchanged vs main"))
        self.assertEqual(len(chk.reviewer_findings([str(p)])), 1)


class OrchestratorGapFixedLater(unittest.TestCase):
    """Orchestrator20, 23 Oct gap (2): a finding round N+1's independent review confirms fixed resolves."""
    R9 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP2 lib/pay.ts:37 — retry charges twice — guard\nVERIFIED OK\n- tests\n"

    def r10(self, confirm="- r9 P2 lib/pay.ts:37 closed: red test now green", findings=""):
        verdict = "FAIL" if findings else "PASS"
        return f"REVIEWING: goal/x @ {SHA_B}\n{verdict}\n{findings}VERIFIED OK\n{confirm}\nUNVERIFIED\n- live\n"

    def lead(self, by="@D@/REVIEW_x_astra_r10_b.md"):
        return {"bead": "AestheticcNext-x", "round": 2, "scope_lock": False,
                "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
                "findings": [{"id": "F1", "source_line": "P2 lib/pay.ts:37 — retry charges twice — guard",
                              "verdict": "fixed", "fixed_by": by, "category": "money"}]}

    def run_pair(self, r10_text, lead=None, r10_mtime=2000):
        return run_named(lead or self.lead(), [("REVIEW_x_astra_r9_a.md", self.R9, 1000),
                                                ("REVIEW_x_astra_r10_b.md", r10_text, r10_mtime)])

    def test_confirmed_fix_resolves_without_clearance(self):
        code, out = self.run_pair(self.r10())
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=0", out)
        self.assertIn("FIXED=1", out)

    def test_no_confirmation_line_refused(self):
        code, out = self.run_pair(self.r10(confirm="- tests pass"))
        self.assertEqual(code, 1, out)
        self.assertIn("confirming the fix", out)

    def test_negated_confirmation_refused(self):
        code, out = self.run_pair(self.r10(confirm="- r9 P2 lib/pay.ts:37 closed but still charges on retry"))
        self.assertEqual(code, 1, out)

    def test_reraised_finding_is_not_fixed(self):
        code, out = self.run_pair(self.r10(findings="P2 lib/pay.ts:37 — still charges twice — guard\n"))
        self.assertEqual(code, 1, out)

    def test_fixed_by_the_raising_review_refused(self):
        code, out = self.run_pair(self.r10(), lead=self.lead(by="@D@/REVIEW_x_astra_r9_a.md"))
        self.assertEqual(code, 1, out)

    def test_fixed_by_an_older_review_refused(self):
        code, out = self.run_pair(self.r10(), r10_mtime=500)
        self.assertEqual(code, 1, out)
        self.assertIn("not newer", out)

    def test_fixed_by_same_commit_refused(self):
        code, out = self.run_pair(self.r10().replace(SHA_B, SHA_A))
        self.assertEqual(code, 1, out)


class AstraR16Checker(unittest.TestCase):
    def test_earlier_fail_declaration_before_a_structured_pass_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = (f"REVIEWING: goal/x @ {SHA_B}\nOverall verdict for the payment path is FAIL.\n"
                f"REVIEWING: goal/x @ {SHA_B}\nPASS\n"
                '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n')
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)

    def test_tally_anywhere_in_the_response_must_match(self):
        lead = base_lead()
        lead["findings"] = []
        text = (f"REVIEWING: goal/x @ {SHA_B}\nSummary: 1 P1 on payments.\n"
                f"REVIEWING: goal/x @ {SHA_B}\nPASS\n"
                '```review-verdict\n{"verdict": "PASS", "findings": []}\n```\n')
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)

    def test_verdict_word_inside_a_finding_is_not_a_declaration(self):
        src = "P2 lib/pay.ts:9 — the verdict is PASS when a charge bug remains — fix"
        lead = base_lead()
        lead["findings"] = [{"id": "F1", "source_line": src, "verdict": "act_on", "category": "money",
                             "clinic_scenario": "reception takes a deposit on the front desk iPad",
                             "demonstrated": "reviewer reproduced", "in_scope": True}]
        text = (f"REVIEWING: goal/x @ {SHA_B}\nFAIL\n{src}\n"
                '```review-verdict\n{"verdict": "FAIL", "findings": [{"severity": "P2", "text": '
                '"lib/pay.ts:9 — the verdict is PASS when a charge bug remains — fix"}]}\n```\n')
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 0, out)


class AstraR17Checker(unittest.TestCase):
    def test_note_with_a_location_of_its_own_needs_the_lead(self):
        r9 = (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP3 lib/a.ts:3 — minor — fix\nVERIFIED OK\n"
              "- **The P3, my own check.** lib/a.ts is fine, and lib/z.ts:9 drops the audit row\n")
        lead = {"bead": "AestheticcNext-x", "round": 2, "scope_lock": False,
                "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
                "findings": [{"id": "F1", "source_line": "P3 lib/a.ts:3 — minor — fix", "verdict": "noted",
                              "category": "style", "reason": "cosmetic, not user-facing at all"}]}
        code, out = run_named(lead, [("REVIEW_x_r9.md", r9, 1000)])
        self.assertEqual(code, 1, out)
        self.assertIn("verified_notes", out)
        lead["verified_notes"] = [{"line": r9.splitlines()[4], "reason": "checked: audit row is written by the caller"}]
        code, out = run_named(lead, [("REVIEW_x_r9.md", r9, 1000)])
        self.assertEqual(code, 0, out)

    def test_protected_note_is_never_tolerated(self):
        p = Path(tempfile.mkdtemp(dir="/private/tmp")) / "v.md"
        p.write_text(f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP3 lib/a.ts:3 — minor — fix\nVERIFIED OK\n"
                     "- **The P3, my own check.** lib/a.ts is fine; lib/pay.ts:9 charges twice\n")
        with self.assertRaises(ValueError):
            chk.reviewer_findings([str(p)])

    def test_abbreviated_echo_of_the_same_commit_is_not_later(self):
        t = OrchestratorGapFixedLater()
        code, out = t.run_pair(t.r10().replace(SHA_B, SHA_A[:10]))
        self.assertEqual(code, 1, out)

    def test_unresolved_confirmation_refused(self):
        t = OrchestratorGapFixedLater()
        for bad in ("- r9 P2 lib/pay.ts:37 closed? Cannot confirm: retry fails.",
                    "- r9 P2 lib/pay.ts:37 closed",
                    "- r9 P2 lib/pay.ts:37 closed: unconfirmed on prod"):
            code, out = t.run_pair(t.r10(confirm=bad))
            self.assertEqual(code, 1, bad + out)


class AstraR18Checker(unittest.TestCase):
    def parse(self, note, finding="P3 lib/a.ts:3 — minor — fix"):
        p = Path(tempfile.mkdtemp(dir="/private/tmp")) / "v.md"
        p.write_text(f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{finding}\nVERIFIED OK\n{note}\n")
        chk.UNBOUND_NOTES.clear()
        return chk.reviewer_findings([str(p)])

    def test_protected_location_in_the_same_file_refused(self):
        with self.assertRaises(ValueError):
            self.parse("- **The P2, my check.** lib/pay.ts:3 is fine; lib/pay.ts:90 charges twice",
                       finding="P2 lib/pay.ts:3 — rounding — fix")

    def test_other_line_of_the_same_file_needs_the_lead(self):
        self.parse("- **The P3, my check.** lib/a.ts:3 fine; lib/a.ts:40 drops a row")
        self.assertEqual(len(chk.UNBOUND_NOTES), 1)

    def test_confirmation_tail_carrying_a_finding_refused(self):
        self.assertFalse(chk.is_confirmation(
            "- r9 P2 lib/pay.ts:37 closed: verified, and P1 lib/refund.ts:5 double refunds"))
        self.assertFalse(chk.is_confirmation(
            "- r9 P2 lib/pay.ts:37 closed: verified green; Finding (P1) on refunds"))
        self.assertTrue(chk.is_confirmation("- r9 P2 lib/pay.ts:37 closed: red test now green on both paths"))


class AstraR19Checker(unittest.TestCase):
    def test_protected_note_at_a_listed_location_refused(self):
        p = Path(tempfile.mkdtemp(dir="/private/tmp")) / "v.md"
        p.write_text(f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP3 lib/a.ts:3 — label typo — fix\nVERIFIED OK\n"
                     "- **The P3, my check.** lib/a.ts:3 also double charges the deposit, reproduced\n")
        with self.assertRaises(ValueError):
            chk.reviewer_findings([str(p)])

    def test_any_note_on_a_listed_finding_needs_the_lead(self):
        r9 = (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP3 lib/a.ts:3 — minor — fix\nVERIFIED OK\n"
              "- **The P3, my own check.** lib/a.ts:3 is unchanged vs main\n")
        lead = {"bead": "AestheticcNext-x", "round": 2, "scope_lock": False,
                "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
                "findings": [{"id": "F1", "source_line": "P3 lib/a.ts:3 — minor — fix", "verdict": "noted",
                              "category": "style", "reason": "cosmetic, not user-facing at all"}]}
        code, out = run_named(lead, [("REVIEW_x_r9.md", r9, 1000)])
        self.assertEqual(code, 1, out)
        self.assertIn("verified_notes", out)

    def test_affirmative_regression_evidence_and_open_in_a_path_accepted(self):
        self.assertTrue(chk.is_confirmation("- r9 P2 lib/pay.ts:37 closed: regression test now green"))
        self.assertTrue(chk.is_confirmation("- r9 P2 lib/open-hours.ts:37 closed: hours test green on both paths"))


class AstraR20Checker(unittest.TestCase):
    def test_unresolved_wording_in_the_tail_refused(self):
        for bad in ("- r9 P2 lib/pay.ts:37 closed: issue remains open on retry",
                    "- r9 P2 lib/pay.ts:37 closed: still pending a prod check",
                    "- r9 P2 lib/pay.ts:37 closed: green, flaky on retry"):
            self.assertFalse(chk.is_confirmation(bad), bad)

    def test_same_finding_also_raised_at_the_later_commit_refused(self):
        t = OrchestratorGapFixedLater()
        own = f"REVIEWING: goal/x @ {SHA_B}\nFAIL\nP2 lib/pay.ts:37 — retry charges twice — guard\n"
        code, out = run_named(t.lead(), [("REVIEW_x_astra_r9_a.md", t.R9, 1000),
                                         ("REVIEW_x_astra_r10_b.md", t.r10(), 2000),
                                         ("own-gate.md", own, 1500)])
        self.assertEqual(code, 1, out)


class AstraR21Checker(unittest.TestCase):
    def test_counted_severity_with_a_location_is_finding_shaped(self):
        self.assertTrue(chk.finding_shaped("Also 1 P2 lib/pay.ts:9 double charges on retry"))

    def test_alphabetic_dot_continuation_is_not_the_id(self):
        self.assertFalse(chk.token_in("F1", "ORCHESTRATOR CLEARED F1.r9 (AestheticcNext-e1xgj)"))
        self.assertTrue(chk.token_in("F1", "ORCHESTRATOR CLEARED F1 (AestheticcNext-e1xgj)."))
