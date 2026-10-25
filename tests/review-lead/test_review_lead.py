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
import re
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


# LUCY-e3opf A3: every input echoes the commit it reviewed. Single-file fixtures written before that rule
# get one here; a test about the echo itself writes its own REVIEWING line.
ECHO = "REVIEWING: goal/x @ 1111111111111111111111111111111111111111\n"


def run_check(lead, verdict_text, round_no=1, scope_lock=False):
    if not re.search(r"(?m)^\s*REVIEWING:", verdict_text):
        verdict_text = ECHO + verdict_text
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
        findings = [{"severity": l.split()[0], "file_line": l.split()[1], "text": l.split(" ", 1)[1]} for l in LINES]
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
                             (self.block({"verdict": "MAYBE", "findings": []}), "needs \"verdict\""),
                             (self.block({"verdict": "PASS"}), "missing keys findings"),
                             (self.block({"verdict": "FAIL", "findings": [{"severity": "P9", "text": "x"}]}),
                              "is not P1, P2 or P3")):
            code, out, _ = run_check(base_lead(), text)
            self.assertEqual(code, 1, text)
            self.assertIn(needle, out)

    def test_structured_finding_missing_from_lead_refused(self):
        lead = base_lead()
        lead["findings"] = lead["findings"][:3]
        findings = [{"severity": l.split()[0], "file_line": l.split()[1], "text": l.split(" ", 1)[1]} for l in LINES]
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
        findings = [{"severity": l.split()[0], "file_line": l.split()[1], "text": l.split(" ", 1)[1]} for l in LINES]
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
        self.assertIn("2 review-verdict blocks; one response per file", out)  # LUCY-e3opf G1

    def test_finding_above_the_verdict_line_refused(self):
        lead = base_lead()
        lead["findings"] = []
        text = (f"REVIEWING: goal/aestheticcnext-q41ar @ {self.TIP}\n"
                "P1 lib/pay.ts:9 — retry charges twice — guard\nVerdict: PASS\n")
        code, out, _ = run_check(lead, text)
        self.assertEqual(code, 1, out)
        self.assertIn("outside the parsed response", out)

    def test_repeated_identical_answer_refused(self):
        """LUCY-e3opf A2: exactly one echo line per file; an identical second one is a second response."""
        lead = base_lead()
        lead["findings"] = []
        answer = f"REVIEWING: goal/aestheticcnext-q41ar @ {self.TIP}\nPASS\nVERIFIED OK\n- tests\n"
        code, out, _ = run_check(lead, answer + answer)
        self.assertEqual(code, 1, out)
        self.assertIn("no single well-formed REVIEWING echo", out)


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

    def r10(self, confirm="- r9 P2 lib/pay.ts:37 closed: red test now green", findings="",
            prior=(("r9 P2 lib/pay.ts:37", "closed"),)):
        """LUCY-e3opf: the block's prior array grades; prior=None is a prose-only review."""
        verdict = "FAIL" if findings else "PASS"
        text = f"REVIEWING: goal/x @ {SHA_B}\n{verdict}\n{findings}VERIFIED OK\n{confirm}\nUNVERIFIED\n- live\n"
        if prior is None:
            return text
        return text + prior_block(verdict, [(l[:2], l[3:]) for l in findings.splitlines() if l], prior)

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
        code, out = self.run_pair(self.r10(confirm="- tests pass", prior=()))
        self.assertEqual(code, 1, out)
        self.assertIn("prior entry", out)

    def test_prose_confirmation_alone_no_longer_grades(self):
        # LUCY-e3opf: a strict prose "closed:" line with no block prior entry is not a fix. A prose-only
        # review of the tip has no prior array, so it cannot be fixed_by.
        code, out = self.run_pair(self.r10(prior=None))
        self.assertEqual(code, 1, out)
        self.assertIn("has no review-verdict prior entry", out)
        code, out = self.run_pair(self.r10(prior=()))
        self.assertEqual(code, 1, out)
        self.assertIn("Prose 'closed:' lines are not graded", out)

    def test_negated_confirmation_refused(self):
        code, out = self.run_pair(self.r10(confirm="- r9 P2 lib/pay.ts:37 closed but still charges on retry",
                                           prior=None))
        self.assertEqual(code, 1, out)

    def test_reraised_finding_is_not_fixed(self):
        code, out = self.run_pair(self.r10(findings="P2 lib/pay.ts:37 — still charges twice — guard\n"))
        self.assertEqual(code, 1, out)

    def test_fixed_by_the_raising_review_refused(self):
        code, out = self.run_pair(self.r10(), lead=self.lead(by="@D@/REVIEW_x_astra_r9_a.md"))
        self.assertEqual(code, 1, out)

    def test_fixed_by_an_older_review_refused(self):
        # LUCY-l1s6g (g): the round in the names orders reviews; mtime breaks a tie only. An older
        # review of the same round, or any review of an earlier round, still never confirms.
        code, out = run_named(self.lead(by="@D@/REVIEW_x_sonnet_r9_b.md"),
                              [("REVIEW_x_astra_r9_a.md", self.R9, 1000),
                               ("REVIEW_x_sonnet_r9_b.md", self.r10(), 500)])
        self.assertEqual(code, 1, out)
        self.assertIn("echoes different commits", out)

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
                '```review-verdict\n{"verdict": "FAIL", "findings": [{"severity": "P2", "file_line": "lib/pay.ts:9", "text": '
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
            code, out = t.run_pair(t.r10(confirm=bad, prior=None))
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


# ---------------------------------------------------------------------------------------------
# LUCY-l1s6g (24 Oct): real reviewer files the checker refused. Fixtures are byte copies of
# Ops/reviews files (md5 unchanged); their real mtimes are in mtimes.json.
FIX_Z0 = HERE / "fixtures" / "z0ote-r2"
FIX_3E = HERE / "fixtures" / "3epp9-r2"
Z0_R1_SONNET = "REVIEW_z0ote_sonnet-fresh_2026-10-23.md"
Z0_R1_OPUS = "REVIEW_z0ote_opus-fresh_2026-10-23.md"
Z0_R2 = ["REVIEW_z0ote_opus-fresh_r2_2026-10-24.md", "REVIEW_z0ote_opus-h2h_r2_2026-10-24.md",
         "REVIEW_z0ote_sonnet-fresh_r2_2026-10-24.md"]


def as_round_one(name):
    """LUCY-e3opf C3: a round is never guessed. The real 23 Oct round-1 files carry no _r1_, so a run gets them
    under a _r1_ name (byte copies), as a real run must: REVIEW_x_seat_2026-10-23.md -> REVIEW_x_seat_r1_2026-10-23.md."""
    if chk.round_of(name) is not None:
        return name
    head, _, date = name.rpartition("_")
    return f"{head}_r1_{date}"


def fixture_named(fix, names, mtimes=None):
    m = json.loads((fix / "mtimes.json").read_text())
    m.update(mtimes or {})
    return [(as_round_one(n), (fix / n).read_text(), m[n]) for n in names]


def parse_text(text, name="v.md"):
    p = Path(tempfile.mkdtemp(dir="/private/tmp")) / name
    p.write_text(text)
    chk.UNBOUND_NOTES.clear()
    return chk.reviewer_findings([str(p)])


def block(verdict, *findings):
    return ("```review-verdict\n" + json.dumps({"verdict": verdict, "findings": [
        {"severity": s, "text": t} for s, t in findings]}) + "\n```\n")


def prior_block(verdict, findings=(), prior=(), evidence="red test now green on both paths"):
    """A review-verdict block with a prior array (LUCY-e3opf): prior is [(ref, status)]. Findings take
    the strict shape (file_line from the text's leading file:line) when the text has one."""
    def finding(s, t):
        loc = chk.LEADING_LOC.match(t)
        return {"severity": s, "file_line": loc.group(1), "text": t} if loc else {"severity": s, "text": t}
    return ("```review-verdict\n" + json.dumps({
        "verdict": verdict, "findings": [finding(s, t) for s, t in findings],
        "prior": [{"ref": r, "status": st, "evidence": evidence} for r, st in prior]}) + "\n```\n")


def with_prior(text, prior, evidence="independently re-driven green at this tip"):
    """A real reviewer file re-run with the current template: a prior array in its review-verdict block,
    and each finding in the strict shape (file_line from the text's leading file:line, G2)."""
    b = chk.one_block("v", text)
    data = json.loads(b.body)
    for f in data["findings"]:
        loc = chk.LEADING_LOC.match(chk.norm(f["text"]))
        if "file_line" not in f and loc:
            f["file_line"] = loc.group(1)
    data["prior"] = [{"ref": r, "status": st, "evidence": evidence} for r, st in prior]
    return text[:b.start] + "```review-verdict\n" + json.dumps(data) + "\n```\n" + text[b.end:]


def fixed_lead(src, by, extra=()):
    return {"bead": "AestheticcNext-x", "round": 2, "scope_lock": False,
            "premise": {"premise": None, "rounds_failed_on_it": 0, "action": None},
            "findings": [{"id": "F1", "source_line": src, "verdict": "fixed", "fixed_by": by,
                          "category": "other"}] + list(extra)}


class L1s6gA_DoubleSeverityPrefix(unittest.TestCase):
    """(a) block severity P2 + text starting 'P2 ...' must not become 'P2 P2 ...'."""

    def test_real_3epp9_astra_r1_parses_once_prefixed(self):
        found = chk.reviewer_findings([str(FIX_3E / "REVIEW_3epp9_astra-xhigh_2026-10-23.md")])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("P2 components/clients/overview-tab.tsx:348"), found[0])
        self.assertFalse(found[0].startswith("P2 P2"), found[0])

    def test_matching_prefix_is_stripped(self):
        text = (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP2 lib/a.ts:3 — label wrong — fix\n"
                + block("FAIL", ("P2", "P2 lib/a.ts:3 — label wrong — fix")))
        self.assertEqual(parse_text(text), ["P2 lib/a.ts:3 — label wrong — fix"])

    def test_conflicting_prefix_still_refused(self):
        text = (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP1 lib/a.ts:3 — label wrong — fix\n"
                + block("FAIL", ("P2", "P1 lib/a.ts:3 — label wrong — fix")))
        with self.assertRaises(ValueError):
            parse_text(text)


R1_RAISE = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP2 lib/pay.ts:37 — retry charges twice — guard\nVERIFIED OK\n- tests\n"
R1_SRC = "P2 lib/pay.ts:37 — retry charges twice — guard"


def r2_confirm(confirm, prior=None):
    """A round-2 PASS; with prior [(ref, status)] it ends in a review-verdict block (LUCY-e3opf)."""
    text = f"REVIEWING: goal/x @ {SHA_B}\nPASS\nVERIFIED OK\n{confirm}\nUNVERIFIED\n- live\n"
    return text if prior is None else text + prior_block("PASS", (), prior)


class L1s6gB_RoundOneWithoutSuffix(unittest.TestCase):
    """(b) was: a review file with no _rN_ in its name is round 1. LUCY-e3opf C3: no round is ever guessed."""

    def test_round_of(self):
        self.assertIsNone(chk.round_of("REVIEW_z0ote_sonnet-fresh_2026-10-23.md"))
        self.assertEqual(chk.round_of("REVIEW_z0ote_sonnet-fresh_r2_2026-10-24.md"), 2)
        self.assertEqual(chk.round_of("REVIEW_x_astra_r10.md"), 10)
        self.assertEqual(chk.round_of("astra-verdict-round-2.log"), 2)
        self.assertEqual(chk.round_of("opus-h2h-round-3.md"), 3)
        with self.assertRaisesRegex(ValueError, "one round token per file"):
            chk.round_of("REVIEW_x_r2_round-3.md")

    def test_unsuffixed_round_one_graded_fixed(self):
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                               ("REVIEW_x_astra_r2_b.md", r2_confirm("- tests", [("r1 P2 lib/pay.ts:37", "closed")]), 2000)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)

    def test_wrong_round_in_the_confirmation_still_refused(self):
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                               ("REVIEW_x_astra_r2_b.md", r2_confirm("- tests", [("r2 P2 lib/pay.ts:37", "closed")]), 2000)])
        self.assertEqual(code, 1, out)


class L1s6gC_LineRanges(unittest.TestCase):
    """(c) CONFIRM_FIXED accepts file:line ranges such as :78-83, matched exactly."""
    SRC = "P1 lib/t.ts:78-83 — marked thread never split — fix"
    RAISE = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP1 lib/t.ts:78-83 — marked thread never split — fix\n"

    def run_confirm(self, ref):
        return run_named(fixed_lead(self.SRC, "@D@/REVIEW_x_r2_b.md"),
                         [("REVIEW_x_r1_a.md", self.RAISE, 1000),
                          ("REVIEW_x_r2_b.md", r2_confirm("- tests", [(ref, "closed")]), 2000)])

    def test_range_confirmation(self):
        self.assertTrue(chk.is_confirmation("- r1 P1 lib/t.ts:78-83 closed: red test now green"))
        code, out = self.run_confirm("r1 P1 lib/t.ts:78-83")
        self.assertEqual(code, 0, out)

    def test_a_different_range_or_line_still_refused(self):
        for bad in ("r1 P1 lib/t.ts:78", "r1 P1 lib/t.ts:78-90"):
            code, out = self.run_confirm(bad)
            self.assertEqual(code, 1, bad + out)


class L1s6gD_NegationGuard(unittest.TestCase):
    """(d) 'stays open' / 'remains green' describing the fix is not 'the finding is unfixed'."""

    def test_real_fix_descriptions_accepted(self):
        sonnet = (FIX_Z0 / Z0_R2[2]).read_text().splitlines()
        line = next(ln for ln in sonnet if ln.startswith("- r1 P2 lib/agent-core/thread-find-or-create.ts:78 closed:"))
        self.assertTrue(chk.is_confirmation(line), line)
        for ok in ("- r9 P2 lib/pay.ts:37 closed: a held thread now stays open and the turn goes on a new thread",
                   "- r9 P2 lib/pay.ts:37 closed: regression passes; the same-client control remains green"):
            self.assertTrue(chk.is_confirmation(ok), ok)

    def test_finding_left_open_still_refused(self):
        for bad in ("- r9 P2 lib/pay.ts:37 closed: stays open",
                    "- r9 P2 lib/pay.ts:37 closed: left open for now",
                    "- r9 P2 lib/pay.ts:37 closed: the finding stays open",
                    "- r9 P2 lib/pay.ts:37 closed: the bug is open on retry",
                    "- r9 P2 lib/pay.ts:37 closed: issue remains open on retry",
                    "- r9 P2 lib/pay.ts:37 closed: remains broken after the change",
                    "- r9 P2 lib/pay.ts:37 closed: still charges on retry"):
            self.assertFalse(chk.is_confirmation(bad), bad)


class L1s6gE_RoundCountHeading(unittest.TestCase):
    """(e) 'Round-1 P1 ...' is a heading, not a tally of one P1."""

    def test_real_sonnet_r2_parses(self):
        found = chk.reviewer_findings([str(FIX_Z0 / Z0_R2[2])])
        self.assertEqual([f[:2] for f in found], ["P3"])

    def test_round_label_is_not_a_count_but_a_tally_still_is(self):
        chk.check_summary_counts("x", ["- Round-1 P1 independently re-driven on pg"], [])
        with self.assertRaises(ValueError):
            chk.check_summary_counts("x", ["Summary: 1 P1 on payments"], [])
        with self.assertRaises(ValueError):
            chk.check_summary_counts("x", ["FAIL — 1 P1, 0 P2"], [])


class L1s6gF_EarlierRoundReferenceInVerifiedOk(unittest.TestCase):
    """(f) a VERIFIED OK '- rN SEV loc closed: <cleared, not fixed>' line in a review-verdict file. Since
    LUCY-e3opf G5 it parses with no lead note (a prose closure never grades); a new located severity on
    it is refused, and an unlocated one is resolved against the round that raised the location."""

    def test_real_opus_h2h_r2_parses_and_needs_the_lead(self):
        found = chk.reviewer_findings([str(FIX_Z0 / Z0_R2[1])])
        self.assertEqual([f[:2] for f in found], ["P3"])
        self.assertEqual(chk.UNBOUND_NOTES, {})

    def reference(self, line):
        return parse_text(f"REVIEWING: goal/x @ {SHA_B}\nPASS\nVERIFIED OK\n{line}\nUNVERIFIED\n- live\n"
                          + block("PASS", ))

    def test_reference_line_is_an_unbound_note(self):
        self.reference("- r1 P2 lib/a.ts:5 closed: cleared as pre-existing; the open P2 bead exists")
        self.assertEqual(len(chk.UNBOUND_NOTES), 0)

    def test_reference_carrying_a_new_finding_still_refused(self):
        with self.assertRaises(ValueError):
            self.reference("- r1 P2 lib/a.ts:5 closed: cleared; lib/b.ts:9 drops rows at P2")
        # No path of its own: it names a round-1 finding at lib/a.ts:5, so round 1 must raise one there.
        r1 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\nP2 lib/a.ts:5 — drops rows — fix\n"
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"] = [{"id": "F1", "source_line": "P2 lib/a.ts:5 — drops rows — fix", "verdict": "consider",
                             "category": "other", "follow_up": "Rows", "reason": "owned by the rows epic",
                             "ordinary_use": "reception saves a client on the desk PC", "carve_out": True}]
        for bad in ("- r1 P2 lib/a.ts:5 closed: cleared; a new P1 on the same path",
                    "- r1 P2 lib/a.ts:5 closed: cleared; Finding (P3): drops rows"):
            r2 = (f"REVIEWING: goal/x @ {SHA_B}\nPASS\nVERIFIED OK\n{bad}\nUNVERIFIED\n- live\n"
                  + prior_block("PASS"))
            self.reference(bad)
            code, out = run_named(copy.deepcopy(lead), [("REVIEW_x_r1_a.md", r1, 1000), ("REVIEW_x_r2_b.md", r2, 2000)])
            self.assertEqual(code, 1, out)
            self.assertIn("matches no round-1 finding", out)
        ok = (f"REVIEWING: goal/x @ {SHA_B}\nPASS\nVERIFIED OK\n- r1 P2 lib/a.ts:5 closed: cleared; the open P2 "
              f"still drops rows\nUNVERIFIED\n- live\n" + prior_block("PASS"))
        code, out = run_named(copy.deepcopy(lead), [("REVIEW_x_r1_a.md", r1, 1000), ("REVIEW_x_r2_b.md", ok, 2000)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=0", out)


class L1s6gG_Freshness(unittest.TestCase):
    """(g) 'not newer': the round in the file names orders reviews; mtime only breaks a tie."""

    def test_real_3epp9_r2_with_a_copied_r1_accepted(self):
        lead = json.loads((FIX_3E / "lead.json").read_text())
        lead["findings"][0]["fixed_by"] = "@D@/REVIEW_3epp9_astra-xhigh_r2_2026-10-23.md"
        # A copied round-1 file carries a fresh mtime, newer than the round-2 review.
        named = fixture_named(FIX_3E, ["REVIEW_3epp9_astra-xhigh_2026-10-23.md",
                                       "REVIEW_3epp9_astra-xhigh_r2_2026-10-23.md"],
                              {"REVIEW_3epp9_astra-xhigh_2026-10-23.md": 1792799999})
        code, out = run_named(lead, named, extra=["--scope-lock"])
        # LUCY-e3opf: its prose "closed:" line no longer grades; the structured prior entry does.
        self.assertEqual(code, 1, out)
        self.assertIn("prior entry", out)
        name, text, mtime = named[1]
        named[1] = (name, with_prior(text, [("r1 P2 components/clients/overview-tab.tsx:348", "closed")]), mtime)
        code, out = run_named(lead, named, extra=["--scope-lock"])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=1", out)

    def test_fixed_by_an_earlier_round_refused_even_if_newer_on_disk(self):
        raise_r10 = R1_RAISE.replace(SHA_A, SHA_B)
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_r9_b.md"),
                              [("REVIEW_x_r10_a.md", raise_r10, 1000),
                               ("REVIEW_x_r9_b.md", r2_confirm("- r10 P2 lib/pay.ts:37 closed: red test now green")
                                .replace(SHA_B, SHA_A), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("not newer", out)

    def test_same_round_older_on_disk_refused(self):
        # LUCY-e3opf C3: one round reviewing two commits is refused before any grade; mtime never orders.
        code, out = run_named(fixed_lead(R1_SRC, "@D@/REVIEW_x_sonnet_r1_b.md"),
                              [("REVIEW_x_astra_r1_a.md", R1_RAISE, 2000),
                               ("REVIEW_x_sonnet_r1_b.md", r2_confirm("- r1 P2 lib/pay.ts:37 closed: red test now green"), 1000)])
        self.assertEqual(code, 1, out)
        self.assertIn("echoes different commits", out)


class L1s6gH_QuotedCellNames(unittest.TestCase):
    """(h) a quoted test-cell name is evidence, not adverse wording; adverse words outside it still count."""

    def test_quoted_cell_name_accepted(self):
        sonnet = (FIX_Z0 / Z0_R2[2]).read_text().splitlines()
        line = next(ln for ln in sonnet if ln.startswith("- r1 P3 lib/agent-core/gateways/whatsapp.ts:168-180 closed:"))
        self.assertTrue(chk.is_confirmation(line), line)
        self.assertTrue(chk.is_confirmation(
            '- r9 P2 lib/pay.ts:37 closed: removing the guard makes it fail; the cell "never charges twice" is green'))

    def test_adverse_words_outside_quotes_still_refused(self):
        for bad in ('- r9 P2 lib/pay.ts:37 closed: "retry guard" added but still charges twice',
                    '- r9 P2 lib/pay.ts:37 closed: the cell "retry" fails on CI',
                    '- r9 P2 lib/pay.ts:37 closed: "green" is not what I saw'):
            self.assertFalse(chk.is_confirmation(bad), bad)


class L1s6gI_ProseParaphraseOfTheBlock(unittest.TestCase):
    """(i) a prose finding that paraphrases a block finding (same severity, same file:line set, same
    words) is that finding; a different finding is still missing from the block."""

    def test_real_sonnet_r1_parses_to_the_block(self):
        found = chk.reviewer_findings([str(FIX_Z0 / Z0_R1_SONNET)])
        self.assertEqual([f[:2] for f in found], ["P1", "P2", "P2"])
        self.assertEqual(chk.UNBOUND_NOTES, {})  # G5: a path-free severity back-reference is ignored

    def file(self, prose, *findings):
        return f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{prose}\n" + block("FAIL", *findings)

    def test_paraphrase_accepted(self):
        found = parse_text(self.file(
            "- P2 lib/a.ts:3 — the held thread keeps the new person's text (builder test) — fix: new thread",
            ("P2", "lib/a.ts:3 — the held thread keeps the new person's text — fix: open a new thread")))
        self.assertEqual(len(found), 1)

    def test_reworded_finding_at_the_same_location_pairs(self):
        # LUCY-e3opf: pairing is by severity + file:line, never wording (3bm73 r5 rewords its block).
        found = parse_text(self.file("- P2 lib/a.ts:3 — refunds go to the wrong card when a deposit is split",
                                     ("P2", "lib/a.ts:3 — the held thread keeps the new person's text — fix")))
        self.assertEqual(found, ["P2 lib/a.ts:3 — the held thread keeps the new person's text — fix"])

    def test_other_location_or_severity_refused(self):
        for prose in ("- P2 lib/a.ts:4 — the held thread keeps the new person's text — fix",
                      "- P1 lib/a.ts:3 — the held thread keeps the new person's text — fix"):
            with self.assertRaises(ValueError, msg=prose):
                parse_text(self.file(prose, ("P2", "lib/a.ts:3 — the held thread keeps the new person's text — fix")))

    def test_two_prose_findings_for_one_block_finding_refused(self):
        # LUCY-e3opf G5: the block is the single source of findings; prose is cross-checked by location
        # only, so two prose lines at the block finding's severity + location both stand for it.
        prose = ("- P2 lib/a.ts:3 — the held thread keeps the new person's text — fix\n"
                 "- P2 lib/a.ts:3 — the held thread keeps the new person's text, again — fix")
        found = parse_text(self.file(prose, ("P2", "lib/a.ts:3 — the held thread keeps the new person's text — fix")))
        self.assertEqual(len(found), 1)


class L1s6gJ_HazardSeverityBackReference(unittest.TestCase):
    """(j) a Hazards line ending '(P1)' that points at a listed P1 goes to the lead; an unlisted one is refused."""

    def file(self, hazard, sev="P1"):
        return (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n- {sev} lib/a.ts:3 — thread keeps old history — fix\n"
                f"## Hazards\n{hazard}\n" + block("FAIL", (sev, "lib/a.ts:3 — thread keeps old history — fix")))

    def test_back_reference_needs_the_lead(self):
        # LUCY-e3opf G5: a severity with no repo path on its line is carry/summary text, ignored.
        for line, sev in (("- H-U0: the promise holds except the marked-thread case (P1).", "P1"),
                          ("- H-U0: the promise fails for the marked-thread case (P1).", "P2"),
                          ("- H-M1: deposits are charged twice (P1).", "P1")):
            parse_text(self.file(line, sev))
            self.assertEqual(chk.UNBOUND_NOTES, {})

    def test_unlisted_or_protected_back_reference_refused(self):
        for bad, sev in (("- H-U0: fails at lib/b.ts:9 (P1).", "P1"),
                         ("- H-U0: lib/a.ts:3 fails for the marked-thread case (P1).", "P2")):
            with self.assertRaises(ValueError, msg=bad):
                parse_text(self.file(bad, sev))


class L1s6gZ0oteRound2EndToEnd(unittest.TestCase):
    """The z0ote r2 lead over the five real reviewer files (two r1 without _r1_, three r2), with the
    orchestrator's re-posted F3/F6 clearances: the checker validates it. The r2 P3s are protected by
    keyword ("SMS") and the branch's own, so they escalate and can't be cleared."""

    # LUCY-e3opf: the r2 files' prose closures no longer grade; these prior entries are what their
    # VERIFIED OK lines say, written the structured way.
    PRIOR = [("r1 P1 lib/agent-core/thread-find-or-create.ts:78-83", "closed"),
             ("r1 P2 lib/agent-core/thread-find-or-create.ts:78", "closed"),
             ("r1 P3 lib/voice/texter-identity.ts:32", "closed"),
             ("r1 P3 lib/agent-core/gateways/whatsapp.ts:168-180", "closed"),
             ("r1 P3 lib/agent-core/thread-find-or-create.ts:76", "closed")]

    def run_lead(self, lead, extra=("--cleared-escalation", "F3", "--cleared-escalation", "F6"), prior=True,
                 seats=(0, 2)):
        named = fixture_named(FIX_Z0, [Z0_R1_SONNET, Z0_R1_OPUS] + Z0_R2)
        # Every r2 seat reviewed the tip, so each is re-run with the strict template (G2). The real h2h seat
        # (Z0_R2[1]) wrote no prior; `seats` names the r2 seats that carry PRIOR.
        closing = [Z0_R2[i] for i in seats]
        named = [(n, with_prior(t, self.PRIOR if prior and n in closing else []) if n in Z0_R2 else t, m)
                 for n, t, m in named]
        return run_named(lead, named, extra=["--bead", "AestheticcNext-z0ote", "--comments-file",
                                             str(FIX_Z0 / "comments.txt"), *extra])

    def lead(self):
        return json.loads((FIX_Z0 / "lead.json").read_text())

    def test_validated(self):
        code, out = self.run_lead(self.lead())
        self.assertEqual(code, 0, out)
        # LUCY-e3opf G3: round 1 has two differently-worded P2s at thread-find-or-create.ts:78 (sonnet and
        # opus), so the one "r1 P2 ...:78" closed entry is ambiguous: both escalate, neither counts fixed.
        # B1: the h2h seat reviewed the same tip and wrote no prior, so the other four stay NOT fixed
        # (blocking), without refusing the run.
        self.assertIn("BLOCKING=4 ESCALATE=6 FOLLOWUPS=0 DISMISSED=0 NOTED=0 P4=0 CLEARED=2 FIXED=0", out)
        code, out = self.run_lead(self.lead(), seats=(0, 1, 2))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=0 ESCALATE=6 FOLLOWUPS=0 DISMISSED=0 NOTED=0 P4=0 CLEARED=2 FIXED=4", out)
        r1 = [(str(FIX_Z0 / as_round_one(n)), chk.reviewer_findings([str(FIX_Z0 / n)])) for n in (Z0_R1_SONNET, Z0_R1_OPUS)]
        self.assertEqual(len(chk.ref_ambiguity(r1, 1, "P2", "lib/agent-core/thread-find-or-create.ts:78")), 2)

    def test_prose_closures_alone_grade_nothing(self):
        code, out = self.run_lead(self.lead(), prior=False)
        self.assertEqual(code, 1, out)
        self.assertEqual(out.count("Prose 'closed:' lines are not graded"), 6, out)

    def test_round_two_p3s_cannot_be_cleared(self):
        code, out = self.run_lead(self.lead(), extra=("--cleared-escalation", "F9"))
        self.assertEqual(code, 1, out)
        self.assertIn("only a pre_existing finding can be cleared", out)

    def test_cleared_notes_need_the_lead(self):
        # LUCY-e3opf G5: the r2 seats' "closed: ORCHESTRATOR CLEARED ..." prose notes no longer need the
        # lead's adjudication: prose never grades, and the structured prior is what the checker reads.
        lead = self.lead()
        lead["verified_notes"] = []
        code, out = self.run_lead(lead, seats=(0, 1, 2))
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=4", out)


# ---------------------------------------------------------------------------------------------
# LUCY-e3opf (24 Oct, orchestrator re-plan): an earlier finding is graded fixed only from a structured
# prior entry {"ref": "r<N> <sev> <file:line>", "status": "closed"} in a review of the current tip.
# Prose wording is never graded, so neither negation-word hole (b1747d7's 8 fail-opens) nor the
# wording refusals (3bm73 r4) can recur.
SHA_C = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c"
FIX_3B = HERE / "fixtures" / "3bm73-r4"
# The tails Astra used to break b1747d7 (Ops/reviews/REVIEW_e3opf_astra-high_2026-10-24.md).
ASTRA_FAIL_OPEN_TAILS = [
    "tests pass; no fix was applied",
    "the fix was removed and the regression fails on the iPad.",
    "the test fails all assertions on the current code.",
    "this same iPad issue still reproduces, as separately reported above.",
    "tests confirm no fix remains in the branch.",
    "the regression still shows the original failure.",
    "the issue is UNVERIFIED on the iPad.",
    "the guard was removed. The regression and control pass.",
]
R1_REF = "r1 P2 lib/pay.ts:37"
LEAD_R2 = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r2_b.md")


def r2_file(prior=None, verdict="PASS", findings=(), sha=SHA_B, confirm="- tests", evidence=None):
    text = (f"REVIEWING: goal/x @ {sha}\n{verdict}\n" + "".join(f"{s} {t}\n" for s, t in findings)
            + f"VERIFIED OK\n{confirm}\nUNVERIFIED\n- live\n")
    if prior is None:
        return text
    return text + prior_block(verdict, findings, prior, **({"evidence": evidence} if evidence else {}))


def run_r2(*r2s, lead=None):
    """R1_RAISE at SHA_A, then (name, text, mtime) round-2 files."""
    return run_named(lead or copy.deepcopy(LEAD_R2), [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000), *r2s])


class E3opfStructuredPrior(unittest.TestCase):
    def test_astra_fail_open_tails_never_grade_as_prose(self):
        for tail in ASTRA_FAIL_OPEN_TAILS:
            confirm = f"- {R1_REF} closed: {tail}"
            code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(confirm=confirm), 2000))
            self.assertEqual(code, 1, tail + out)
            self.assertNotIn("FIXED=1", out)
            # The same words next to a block without the entry still grade nothing.
            code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[], confirm=confirm), 2000))
            self.assertEqual(code, 1, tail + out)
            self.assertNotIn("FIXED=1", out)

    def test_astra_fail_open_tails_as_structured_open_never_grade(self):
        for tail in ASTRA_FAIL_OPEN_TAILS:
            code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "open")], evidence=tail), 2000))
            self.assertEqual(code, 1, tail + out)
            self.assertIn("it says open", out)

    def test_closed_entry_grades_whatever_the_evidence_wording(self):
        # The 3bm73 r4 wording that the negation filter refused is just evidence now.
        for ev in ("the new fields do not populate from cached history",
                   "restoring smoking:false caused three tests to fail",
                   "merge-prefill remains separately open above"):
            code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")], evidence=ev), 2000))
            self.assertEqual(code, 0, ev + out)
            self.assertIn("FIXED=1", out)

    def test_missing_or_mismatched_entry_blocks(self):
        for ref in ("r1 P2 lib/pay.ts:38", "r1 P1 lib/pay.ts:37", "r2 P2 lib/pay.ts:37",
                    "r1 P2 lib/pay.ts:37-40", "r1 P2 lib/payment.ts:37"):
            code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(ref, "closed")]), 2000))
            self.assertEqual(code, 1, ref + out)
            self.assertIn("prior entry", out)

    def test_seats_disagreeing_at_the_tip_escalates(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as d:
            out_dir = Path(d) / "out"
            code, out = run_named(copy.deepcopy(LEAD_R2), [
                ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                ("REVIEW_x_sonnet_r2_b.md", r2_file(prior=[(R1_REF, "open")], evidence="still charges twice"), 2100)],
                extra=["--out-dir", str(out_dir)])
            self.assertEqual(code, 0, out)
            self.assertIn("BLOCKING=0 ESCALATE=1", out)
            self.assertIn("FIXED=0", out)
            self.assertIn("seats disagree", (out_dir / "lead-escalate-round-2.txt").read_text())
            self.assertIn("DISPUTED", (out_dir / "lead-dismissals-round-2.md").read_text())
        # fixed_by being the seat that says open is not a fix at all.
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_sonnet_r2_b.md")
        code, out = run_named(lead, [
            ("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
            ("REVIEW_x_sonnet_r2_b.md", r2_file(prior=[(R1_REF, "open")], evidence="still charges twice"), 2100)])
        self.assertEqual(code, 1, out)

    def test_silent_seat_at_the_tip_leaves_it_not_fixed(self):
        """LUCY-e3opf B1: every review of the tip must close it. A silent seat is not a dispute and does not
        refuse the run; the finding stays blocking."""
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_sonnet_r2_b.md", r2_file(prior=[]), 2100))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=1 ESCALATE=0", out)
        self.assertIn("FIXED=0", out)

    def test_fixed_by_not_at_the_current_tip_refused(self):
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_astra_r3_c.md", r2_file(prior=[], sha=SHA_C), 3000))
        self.assertEqual(code, 1, out)
        self.assertIn("not the current tip", out)
        # The newest review closing it is enough.
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r3_c.md")
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_astra_r3_c.md", r2_file(prior=[(R1_REF, "closed")], sha=SHA_C), 3000), lead=lead)
        self.assertEqual(code, 0, out)

    def test_another_seat_raising_it_at_the_tip_refused(self):
        raise_again = ("P2", "lib/pay.ts:37 — retry still charges twice — guard")
        lead = copy.deepcopy(LEAD_R2)
        lead["findings"].append({"id": "F2", "source_line": "P2 lib/pay.ts:37 — retry still charges twice — guard",
                                 "verdict": "act_on", "category": "money", "in_scope": True,
                                 "clinic_scenario": "reception retries a declined card at the desk",
                                 "demonstrated": "sonnet reproduced"})
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_sonnet_r2_b.md", r2_file(prior=[], verdict="FAIL", findings=[raise_again]), 2100),
                           lead=lead)
        self.assertEqual(code, 1, out)
        self.assertIn("again at the current tip", out)

    def test_malformed_prior_refuses_the_file(self):
        good = {"ref": R1_REF, "status": "closed", "evidence": "red test now green"}
        bad_priors = [
            {"x": 1},
            {},
            "",
            None,
            ["r1 P2 lib/pay.ts:37"],
            [dict(good, ref="r1 lib/pay.ts:37")],
            # A path-only ref parses since LUCY-cwpxu; a token that is not a bare path never does.
            [dict(good, ref="r1 P2 pay")],
            [dict(good, ref="r1 P2 lib/pay.ts:")],
            [dict(good, ref="r1 P2 lib/pay.ts more")],
            [dict(good, ref="r1 P2 P1/P2")],
            [dict(good, ref="P2 lib/pay.ts:37")],
            [dict(good, status="fixed")],
            [dict(good, status="Closed")],
            [dict(good, evidence="")],
            [{"ref": R1_REF, "status": "closed"}],
            [good, dict(good, status="open")],
        ]
        for prior in bad_priors:
            text = (f"REVIEWING: goal/x @ {SHA_B}\nPASS\n```review-verdict\n"
                    + json.dumps({"verdict": "PASS", "findings": [], "prior": prior}) + "\n```\n")
            with self.assertRaises(ValueError, msg=json.dumps(prior)):
                parse_text(text)


class E3opfBlockFindingFields(unittest.TestCase):
    def parse_block(self, finding, prose=None):
        prose = prose if prose is not None else ""
        return parse_text(f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{prose}```review-verdict\n"
                          + json.dumps({"verdict": "FAIL", "findings": [finding]}) + "\n```\n")

    def test_severity_is_required(self):
        with self.assertRaises(ValueError):
            self.parse_block({"file_line": "lib/a.ts:3", "text": "label wrong — fix"})

    def test_file_line_field_is_the_location(self):
        self.assertEqual(self.parse_block({"severity": "P1", "file_line": "lib/a.ts:3", "text": "label wrong — fix"}),
                         ["P1 lib/a.ts:3 — label wrong — fix"])
        self.assertEqual(self.parse_block({"severity": "P1", "file_line": "lib/a.ts:3",
                                           "text": "lib/a.ts:3 — label wrong — fix"}),
                         ["P1 lib/a.ts:3 — label wrong — fix"])

    def test_bad_or_contradicting_file_line_refused(self):
        for f in ({"severity": "P1", "file_line": "lib/a.ts", "text": "label wrong"},
                  {"severity": "P1", "file_line": 3, "text": "label wrong"},
                  {"severity": "P1", "file_line": "lib/a.ts:3", "text": "lib/b.ts:9 — label wrong"},
                  {"severity": "P1", "file_line": "lib/a.ts:3", "text": "P2 lib/a.ts:3 — label wrong"}):
            with self.assertRaises(ValueError, msg=json.dumps(f)):
                self.parse_block(f)

    def test_prose_pairs_with_the_block_by_severity_and_file_line(self):
        found = self.parse_block({"severity": "P1", "file_line": "lib/a.ts:3", "text": "reworded entirely"},
                                 prose="P1 lib/a.ts:3 — the original prose wording of it\n")
        self.assertEqual(found, ["P1 lib/a.ts:3 — reworded entirely"])
        for prose in ("P2 lib/a.ts:3 — same place, other severity\n", "P1 lib/a.ts:4 — other line\n"):
            with self.assertRaises(ValueError, msg=prose):
                self.parse_block({"severity": "P1", "file_line": "lib/a.ts:3", "text": "reworded entirely"}, prose)

    def test_a_location_later_in_the_text_is_not_the_finding_location(self):
        # Grading reads only the declared (leading) location: this legacy finding has none, so it can
        # never be graded fixed. The prose cross-check (G5) locates a legacy block finding by its first
        # repo path, so the prose may reword it; another severity or path there is still refused.
        f = {"severity": "P2", "text": "AestheticcNext-x.1 lib/a.ts:3 — label wrong"}
        self.assertIsNone(chk.leading_loc("P2 AestheticcNext-x.1 lib/a.ts:3 — label wrong"))
        self.assertEqual(self.parse_block(f, prose="P2 AestheticcNext-x.1 lib/a.ts:3 — label wrong\n"),
                         ["P2 AestheticcNext-x.1 lib/a.ts:3 — label wrong"])
        self.parse_block(f, prose="P2 lib/a.ts:3 — label wrong, reworded\n")
        for prose in ("P1 lib/a.ts:3 — label wrong\n", "P2 lib/b.ts:3 — label wrong\n"):
            with self.assertRaises(ValueError, msg=prose):
                self.parse_block(f, prose=prose)
        # The prose side: G5 reads location, not wording, wherever on the line the path sits.
        self.parse_block({"severity": "P1", "file_line": "lib/a.ts:3", "text": "label wrong"},
                         prose="P1 (see lib/a.ts:3) refunds go to the wrong card\n")

    def test_line_less_finding_needs_a_path_only_prior(self):
        """Raised with no line, it is graded fixed only by a path-only prior entry (LUCY-cwpxu)."""
        src = "P3 tests/a.test.ts — no failed-fetch test — add one"
        raise_r1 = (f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n" + src + "\n```review-verdict\n"
                    + json.dumps({"verdict": "FAIL", "findings": [{"severity": "P3", "text": src[3:]}]}) + "\n```\n")
        code, out = run_named(fixed_lead(src, "@D@/REVIEW_x_astra_r2_b.md"),
                              [("REVIEW_x_astra_r1_a.md", raise_r1, 1000),
                               ("REVIEW_x_astra_r2_b.md", r2_file(prior=[]), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn('no review-verdict prior entry {"ref": "r1 P3 tests/a.test.ts", "status": "closed"}', out)


class E3opfReal3bm73(unittest.TestCase):
    """Byte copies of the 3bm73 Ops/reviews files that deadlocked r1–r6."""
    R1 = ["REVIEW_3bm73_astra-xhigh_2026-10-23.md", "REVIEW_3bm73_sonnet-fresh_2026-10-23.md"]
    R2 = "REVIEW_3bm73_astra-xhigh_r2_2026-10-24.md"
    R4 = "REVIEW_3bm73_astra-xhigh_r4_2026-10-24.md"
    R5 = "REVIEW_3bm73_astra-xhigh_r5_2026-10-24.md"
    # What r4's VERIFIED OK prose says, written as the structured prior array.
    R4_PRIOR = [("r2 P1 components/appointments/use-consent-client.ts:73", "closed"),
                ("r2 P1 components/appointments/workflow-steps/merge-generated-prefill.ts:36", "open"),
                ("r2 P3 pages/api/clients/import/batch.ts:418", "closed"),
                ("r1 P1 components/appointments/workflow-steps/consent-step.tsx:256", "closed"),
                ("r1 P2 pages/api/clients/[id]/medical-history.ts:164", "closed")]

    def test_r5_reworded_block_finding_parses_with_p1(self):
        found = chk.reviewer_findings([str(FIX_3B / self.R5)])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("P1 components/appointments/use-consent-client.ts:57 — "), found[0])

    def test_r4_block_finding_parses_with_p1(self):
        found = chk.reviewer_findings([str(FIX_3B / self.R4)])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith(
            "P1 components/appointments/workflow-steps/merge-generated-prefill.ts:36 — Before treatment"), found[0])

    def run_r4(self, lead, prior=True):
        named = fixture_named(FIX_3B, self.R1 + [self.R2, self.R4])
        if prior:
            named = [(n, with_prior(t, self.R4_PRIOR) if n == self.R4 else t, m) for n, t, m in named]
        return run_named(lead, named)

    def lead(self):
        return json.loads((FIX_3B / "lead.json").read_text())

    def test_r4_prose_closures_alone_grade_nothing(self):
        code, out = self.run_r4(self.lead(), prior=False)
        self.assertEqual(code, 1, out)
        # The real r4 block is the legacy shape: refused at the tip whatever the grades (LUCY-e3opf A4).
        self.assertIn("REVIEW_3bm73_astra-xhigh_r4_2026-10-24.md: reviewed the current tip", out)
        self.assertIn("has a finding without file_line", out)
        named = [(n, with_prior(t, []) if n == self.R4 else t, m)
                 for n, t, m in fixture_named(FIX_3B, self.R1 + [self.R2, self.R4])]
        code, out = run_named(self.lead(), named)
        self.assertEqual(code, 1, out)
        for fid in ("F1", "F2", "F4", "F6"):
            self.assertIn(f"{fid}: fixed_by REVIEW_3bm73_astra-xhigh_r4_2026-10-24.md has no review-verdict prior", out)

    def test_r4_structured_prior_grades_the_four_and_keeps_merge_prefill_open(self):
        lead = self.lead()
        f3 = next(f for f in lead["findings"] if f["id"] == "F3")
        # The line-less r1 P3 is graded fixed only by a path-only prior (LUCY-cwpxu); r4 has none.
        code, out = self.run_r4(copy.deepcopy(lead))
        self.assertEqual(code, 1, out)
        self.assertIn('F3: fixed_by REVIEW_3bm73_astra-xhigh_r4_2026-10-24.md has no review-verdict prior entry '
                      '{"ref": "r1 P3 tests/components/appointments/workflow-consent-medical-history.test.tsx"', out)
        for fid in ("F1", "F2", "F4", "F6"):
            self.assertNotIn(f"{fid}:", out)
        f3.update(verdict="consider", follow_up="AestheticcNext-3bm73 failed-fetch test",
                  reason="line-less P3, kept open as a follow-up")
        f3.pop("fixed_by")
        code, out = self.run_r4(copy.deepcopy(lead))
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCKING=2 ESCALATE=1", out)
        self.assertIn("FIXED=4", out)
        # Grading the merge-prefill finding fixed from r4 is refused: r4 raises it again (and marks it open).
        f5 = next(f for f in lead["findings"] if f["id"] == "F5")
        f5.update(verdict="fixed", fixed_by="@D@/" + self.R4)
        code, out = self.run_r4(lead)
        self.assertEqual(code, 1, out)
        self.assertIn("F5: REVIEW_3bm73_astra-xhigh_r4_2026-10-24.md raises P1", out)


class E3opfOwnGateRound1(unittest.TestCase):
    """Astra own-gate round 1 (24 Oct): four fail-open shapes in the first prior-array build."""
    RAISE_R2 = ("P2", "lib/pay.ts:37 — retry charges twice — guard")

    def test_raised_in_two_rounds_needs_both_closed(self):
        lead = fixed_lead(R1_SRC, "@D@/REVIEW_x_astra_r3_c.md")
        lead["findings"].append(dict(lead["findings"][0], id="F2"))
        named = [("REVIEW_x_astra_r1_a.md", R1_RAISE, 1000),
                 ("REVIEW_x_astra_r2_b.md", r2_file(prior=[], verdict="FAIL", findings=[self.RAISE_R2]), 2000)]
        code, out = run_named(copy.deepcopy(lead), named + [
            ("REVIEW_x_astra_r3_c.md", r2_file(prior=[(R1_REF, "closed")], sha=SHA_C), 3000)])
        self.assertEqual(code, 1, out)
        self.assertIn("r2 P2 lib/pay.ts:37", out)
        code, out = run_named(lead, named + [
            ("REVIEW_x_astra_r3_c.md", r2_file(prior=[(R1_REF, "closed"), ("r2 P2 lib/pay.ts:37", "closed")],
                                               sha=SHA_C), 3000)])
        self.assertEqual(code, 0, out)
        self.assertIn("FIXED=2", out)

    def test_open_prior_without_its_raising_review_refused(self):
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"] = []
        code, out = run_named(lead, [("REVIEW_x_astra_r2_b.md",
                                      r2_file(prior=[(R1_REF, "open")], evidence="not rechecked at this tip"), 2000)])
        self.assertEqual(code, 1, out)
        self.assertIn("prior ref r1 P2 lib/pay.ts:37 (open) matches no round-1 finding", out)
        # Own-gate r3: a closed entry needs its origin too, or the finding drops out of coverage.
        code, out = run_named(lead, [("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                                     ("REVIEW_x_astra_r3_c.md", r2_file(prior=[], sha=SHA_C), 3000)])
        self.assertEqual(code, 1, out)
        self.assertIn("prior ref r1 P2 lib/pay.ts:37 (closed) matches no round-1 finding", out)

    def test_disagreement_escalates_whatever_the_lead_graded(self):
        src = "P2 lib/a.ts:3 — wrong total displayed — fix"
        r1 = f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{src}\n"
        ref = "r1 P2 lib/a.ts:3"
        for grade in ({"verdict": "consider", "follow_up": "Total display", "reason": "owned by the totals epic"},
                      {"verdict": "dismissed", "reason": "the total is recomputed on save anyway"}):
            lead = fixed_lead(src, "x")
            lead["findings"][0] = dict({"id": "F1", "source_line": src, "category": "user_facing",
                                        "ordinary_use": "reception opens the bill on the desk PC",
                                        "carve_out": True}, **grade)
            code, out = run_named(lead, [
                ("REVIEW_x_astra_r1_a.md", r1, 1000),
                ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(ref, "closed")]), 2000),
                ("REVIEW_x_sonnet_r2_b.md", r2_file(prior=[(ref, "open")], evidence="total still wrong"), 2100)])
            self.assertEqual(code, 0, out)
            self.assertIn("ESCALATE=1 FOLLOWUPS=0", out, grade)

    def test_conflicting_echoes_in_the_newest_review_leave_the_tip_unknown(self):
        r3 = f"REVIEWING: goal/x @ {SHA_B}\n" + r2_file(prior=[], sha=SHA_C)
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_astra_r3_c.md", r3, 3000))
        self.assertEqual(code, 1, out)
        self.assertIn("no single REVIEWING echo", out)


class E3opfOwnGateRound2(unittest.TestCase):
    """Astra own-gate round 2 (24 Oct): five more fail-open shapes."""
    SRC = "P2 lib/a.ts:3 — wrong total displayed — fix"
    REF = "r1 P2 lib/a.ts:3"

    def disputed_run(self, grade, extra=()):
        lead = fixed_lead(self.SRC, "x")
        lead["findings"][0] = dict({"id": "F1", "source_line": self.SRC, "category": "user_facing",
                                    "clinic_scenario": "reception opens the bill on the desk PC",
                                    "demonstrated": "seat reproduced"}, **grade)
        lead["scope_lock"] = "--scope-lock" in extra
        return run_named(lead, [
            ("REVIEW_x_astra_r1_a.md", f"REVIEWING: goal/x @ {SHA_A}\nFAIL\n{self.SRC}\n", 1000),
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(self.REF, "closed")]), 2000),
            ("REVIEW_x_sonnet_r2_b.md", r2_file(prior=[(self.REF, "open")], evidence="total still wrong"), 2100)],
            extra=list(extra))

    def test_open_prior_in_an_older_review_needs_its_origin(self):
        lead = fixed_lead(R1_SRC, "x")
        lead["findings"] = []
        code, out = run_named(lead, [
            ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "open")], evidence="not fixed at B"), 2000),
            ("REVIEW_x_astra_r3_c.md", r2_file(prior=[], sha=SHA_C), 3000)])
        self.assertEqual(code, 1, out)
        self.assertIn("matches no round-1 finding", out)

    def test_open_in_an_earlier_block_survives_a_later_block(self):
        # LUCY-e3opf G1 (supersedes the cross-block merge): a second block is a second response, and the
        # file is refused outright, so an earlier "open" can never be dropped by a later block.
        opus = (r2_file(prior=[(R1_REF, "open")], evidence="retry still charges twice")
                + "Second answer.\n" + prior_block("PASS", (), []))
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_opus_r2_b.md", opus, 2100))
        self.assertEqual(code, 1, out)
        self.assertIn("2 review-verdict blocks; one response per file", out)
        self.assertNotIn("FIXED=", out)
        contradictory = (r2_file(prior=[(R1_REF, "open")], evidence="retry still charges twice")
                         + prior_block("PASS", (), [(R1_REF, "closed")]))
        with self.assertRaises(ValueError):
            parse_text(contradictory)

    def test_routed_out_act_on_dispute_escalates(self):
        for grade, extra in (({"verdict": "act_on", "in_scope": False, "follow_up": "Totals"}, ("--scope-lock",)),
                             ({"verdict": "act_on", "pre_existing": True, "follow_up": "Totals"}, ())):
            code, out = self.disputed_run(grade, extra)
            self.assertEqual(code, 0, out)
            self.assertIn("ESCALATE=1 FOLLOWUPS=0", out, grade)

    def test_disputed_finding_is_never_demoted(self):
        code, out = self.disputed_run({"verdict": "consider", "category": "other", "carve_out": False,
                                       "ordinary_use": "none: deliberately crafted synthetic input",
                                       "reason": "synthetic only", "follow_up": "Totals"})
        self.assertEqual(code, 0, out)
        self.assertIn("ESCALATE=1", out)
        self.assertIn("P4=0", out)

    def test_malformed_second_echo_leaves_the_tip_unknown(self):
        r3 = f"REVIEWING: goal/x @ {SHA_B}\n" + r2_file(prior=[]).replace(
            f"REVIEWING: goal/x @ {SHA_B}", f"REVIEWING: goal/x @ {SHA_C}^")
        code, out = run_r2(("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2000),
                           ("REVIEW_x_astra_r3_c.md", r3, 3000))
        self.assertEqual(code, 1, out)
        self.assertIn("no single REVIEWING echo", out)


class E3opfOwnGateRound3(unittest.TestCase):
    """Astra own-gate round 3 (24 Oct): duplicate JSON keys, a malformed echo in a non-newest seat."""

    def test_duplicate_keys_in_a_block_refused(self):
        for body in ('{"verdict": "PASS", "findings": [], "prior": [{"ref": "r1 P2 lib/pay.ts:37", "status": '
                     '"open", "evidence": "checked at this tip"}], "prior": []}',
                     '{"verdict": "PASS", "findings": [{"severity": "P1", "file_line": "lib/a.ts:3", "text": "x"}],'
                     ' "findings": []}',
                     '{"verdict": "PASS", "findings": [], "prior": [{"ref": "r1 P2 lib/pay.ts:37", "status": '
                     '"open", "status": "closed", "evidence": "checked at this tip"}]}'):
            with self.assertRaises(ValueError, msg=body):
                parse_text(f"REVIEWING: goal/x @ {SHA_B}\nPASS\n```review-verdict\n{body}\n```\n")

    def test_malformed_echo_in_any_seat_refused(self):
        opus = r2_file(prior=[(R1_REF, "open")], evidence="retry still charges twice") + \
            f"REVIEWING: goal/x @ {SHA_B}^\n"
        code, out = run_r2(("REVIEW_x_opus_r2_b.md", opus, 2000),
                           ("REVIEW_x_astra_r2_b.md", r2_file(prior=[(R1_REF, "closed")]), 2100))
        self.assertEqual(code, 1, out)
        self.assertIn("REVIEW_x_opus_r2_b.md: no single well-formed REVIEWING echo", out)
