"""Check 11 of fleet-dispatch.py: a bug brief needs 'Premise verified: <evidence>' newer than
the bead before a Worker builds it; 'disproved' means close FALSE-ALARM (LUCY-o7otj).
Run: python3 -m unittest discover -s ~/.claude/scripts/tests"""
import importlib.util
import os
import unittest
from unittest import mock

SCRIPT = os.path.expanduser("~/.claude/scripts/fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

NEW = "2026-09-28T09:00:00Z"   # filed after the cutover
OLD = "2026-09-20T09:00:00Z"   # filed before it


def bead(comments=(), issue_type="bug", labels=(), created=NEW, bid="AestheticcNext-t1"):
    return {"id": bid, "issue_type": issue_type, "labels": list(labels), "created_at": created,
            "comments": [{"text": t, "created_at": at} for t, at in comments]}


LATER = "2026-09-28T10:00:00Z"
EARLIER = "2026-09-28T08:00:00Z"


class PremiseCheck(unittest.TestCase):
    def test_bug_without_premise_line_refused(self):
        problems = fd.check_premise(bead())
        self.assertEqual(len(problems), 1)
        self.assertIn("no 'Premise verified:'", problems[0])
        self.assertIn("bd comment AestheticcNext-t1", problems[0])

    def test_verified_premise_passes(self):
        for line in ("Premise verified: prod SELECT on appointments returned 3 overlapping rows",
                     "**Premise verified:** drove the app on Demo Clinic iPad, Save showed 'edited elsewhere'",
                     "Premise verified: reproduced with a failing jest test in face-mapping-save.test.ts",
                     "Premise verified: Sentry error AESTHETICC-4XY fired 12 times since 25 Sep",
                     # 22qtw's real PROD CHECK: 'pending' is a payment status, not unfinished work
                     "Premise verified: PROD CHECK read-only SELECT — card_reader/tap_to_pay rows: "
                     "succeeded 228, cancelled 32+14, failed 5, expired 2, pending 1 (tap_to_pay)"):
            self.assertEqual(fd.check_premise(bead([(line, LATER)])), [], line)

    def test_disproved_refused_with_false_alarm(self):
        problems = fd.check_premise(bead([
            ("Premise verified: disproved — every 'duplicate' was deposit + balance on prod", LATER)]))
        self.assertIn("disproved", problems[0])
        self.assertIn("FALSE-ALARM", problems[0])

    def test_latest_premise_line_wins(self):
        problems = fd.check_premise(bead([
            ("Premise verified: prod SELECT showed 3 duplicate rows", LATER),
            ("Premise verified: disproved — rows are separate payments per prod query",
             "2026-09-28T11:00:00Z")]))
        self.assertIn("disproved", problems[0])
        self.assertEqual(fd.check_premise(bead([
            ("Premise verified: disproved — first look", LATER),
            ("Premise verified: re-checked, prod SELECT shows 2 real duplicates",
             "2026-09-28T11:00:00Z")])), [])

    def test_line_older_than_bead_does_not_count(self):
        problems = fd.check_premise(bead([("Premise verified: prod SELECT shows it", EARLIER)]))
        self.assertIn("no 'Premise verified:'", problems[0])

    def test_line_in_description_does_not_count(self):
        record = bead()
        record["description"] = "Premise verified: prod SELECT shows it"
        self.assertIn("no 'Premise verified:'", fd.check_premise(record)[0])

    def test_sourceless_or_placeholder_evidence_refused(self):
        for line in ("Premise verified: yes",
                     "Premise verified: Shane says it's definitely broken"):
            problems = fd.check_premise(bead([(line, LATER)]))
            self.assertIn("doesn't name a concrete source", problems[0], line)

    def test_pending_evidence_refused(self):
        for line in ("Premise verified: TODO run prod SELECT on appointments",
                     "Premise verified: prod result still pending", "Premise verified: TBD",
                     "Premise verified: will run the jest repro after lunch"):
            problems = fd.check_premise(bead([(line, LATER)]))
            self.assertIn("still pending", problems[0], line)

    def test_mid_sentence_mention_does_not_count(self):
        problems = fd.check_premise(bead([("needs a 'Premise verified: prod SELECT' line", LATER)]))
        self.assertIn("no 'Premise verified:'", problems[0])

    def test_non_bug_unlabelled_and_lucy_bugs_exempt(self):
        self.assertEqual(fd.check_premise(bead(issue_type="task")), [])
        self.assertEqual(fd.check_premise(bead(issue_type="feature", labels=["headless-eligible"])), [])
        self.assertEqual(fd.check_premise(bead(bid="LUCY-abc")), [])

    def test_qa_followup_and_sweep_found_need_premise_whatever_type(self):
        for labels, bid in ((["qa-followup"], "AestheticcNext-t2"), (["sweep-found"], "LUCY-t3")):
            problems = fd.check_premise(bead(issue_type="task", labels=labels, bid=bid))
            self.assertIn("no 'Premise verified:'", problems[0], labels)

    def test_pre_cutover_bead_exempt_only_with_reflection_or_passed_review(self):
        self.assertIn("no 'Premise verified:'", fd.check_premise(bead(created=OLD))[0])
        for text in ("REFLECTION attempt 5 (orchestrator, 26 Sep): the independent review FAILED",
                     "Review: PASS (Codex xhigh). Landing in batch 2.",
                     "ASTRA XHIGH REVIEW 2026-09-25 (tip 496cdd5bd): PASS — 0 P1, 0 P2, 0 P3.",
                     "OPUS HEAD-TO-HEAD REVIEW 2026-09-25 (tip 88a240588): PASS — 0 P1, 0 P2",
                     "INDEPENDENT REVIEW 2026-09-25 (tip f6ce51cd5): PASS — 0 P1, 0 P2"):
            self.assertEqual(fd.check_premise(bead([(text, "2026-09-21T00:00:00Z")], created=OLD)), [], text)
        for text in ("INDEPENDENT REVIEW 2026-09-26 (tip 25a7fcc25): FAIL — 1 P1. Later PASS expected",
                     "Review: FAIL — two P2s", "started /goal attempt 2",
                     "BUILDER IN-SESSION REVIEW 2026-09-24 (NOT the independent review): PASS"):
            problems = fd.check_premise(bead([(text, "2026-09-21T00:00:00Z")], created=OLD))
            self.assertIn("no 'Premise verified:'", problems[0], text)

    def test_disproved_refuses_even_an_exempt_bead(self):
        problems = fd.check_premise(bead([
            ("REFLECTION attempt 2", "2026-09-21T00:00:00Z"),
            ("Premise verified: disproved — by design per prod settings row", "2026-09-22T00:00:00Z")],
            created=OLD))
        self.assertIn("disproved", problems[0])

    def test_post_cutover_reflection_does_not_exempt(self):
        problems = fd.check_premise(bead([("REFLECTION attempt 2", LATER)]))
        self.assertIn("no 'Premise verified:'", problems[0])

    def test_unparseable_created_at_fails_closed(self):
        self.assertIn("cannot read", fd.check_premise(bead(created="yesterday"))[0])

    def test_tree_checks_bug_children(self):
        parent = bead(issue_type="epic")
        child_ok = bead([("Premise verified: prod SELECT shows 4 rows", LATER)], bid="AestheticcNext-c1")
        child_bad = bead(bid="AestheticcNext-c2")
        rows = [{"id": "AestheticcNext-c1", "issue_type": "bug", "labels": []},
                {"id": "AestheticcNext-c2", "issue_type": "bug", "labels": []},
                {"id": "AestheticcNext-c3", "issue_type": "task", "labels": []}]
        records = {"AestheticcNext-c1": child_ok, "AestheticcNext-c2": child_bad}
        with mock.patch.object(fd, "list_open_children", return_value=rows), \
             mock.patch.object(fd, "load_bead_record", side_effect=lambda b: (records[b], None)):
            problems = fd.check_premise_tree("AestheticcNext-t1", parent)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("AestheticcNext-c2:"))

    def test_tree_fails_closed_when_children_unlistable(self):
        with mock.patch.object(fd, "list_open_children", side_effect=RuntimeError("dolt down")):
            problems = fd.check_premise_tree("AestheticcNext-t1", bead(issue_type="task"))
        self.assertIn("cannot list", problems[0])


class ForceReason(unittest.TestCase):
    def test_force_past_check_11_needs_a_reason(self):
        failures = {fd.PREMISE_CHECK: ["x"]}
        self.assertIn("--force-reason", fd.force_refusal(failures, True, None))
        self.assertIn("--force-reason", fd.force_refusal(failures, True, "   "))
        self.assertIsNone(fd.force_refusal(failures, True, "Shane asked: hotfix now, verify after"))

    def test_other_checks_and_unforced_runs_unaffected(self):
        self.assertIsNone(fd.force_refusal({"4 headless eligible": ["x"]}, True, None))
        self.assertIsNone(fd.force_refusal({fd.PREMISE_CHECK: ["x"]}, False, None))

    def test_run_gate_includes_check_11_for_single_and_epic(self):
        with mock.patch.multiple(fd, check_bead_is_dispatchable=lambda b: [],
                                 check_no_inflight_work=lambda b: [],
                                 check_dependencies=lambda b: [],
                                 check_epic_has_children=lambda b: [],
                                 check_client_triage_tree=lambda *a: [],
                                 check_headless_eligible=lambda *a: [],
                                 check_bead_brief=lambda r: [],
                                 load_bead_record=lambda b: (bead(), None),
                                 list_open_children=lambda b: [],
                                 check_resources=lambda: ({}, {}, {})), \
             mock.patch("builtins.print"):
            for is_epic in (False, True):
                failed, failures = fd.run_gate("AestheticcNext-t1", is_epic)
                self.assertTrue(failed, is_epic)
                self.assertIn(fd.PREMISE_CHECK, failures, is_epic)


class CodexLaneForceReason(unittest.TestCase):
    """fleet-dispatch-codex.py runs the same gate; forcing past check 11 needs a reason there too."""

    @classmethod
    def setUpClass(cls):
        path = os.path.expanduser("~/.claude/scripts/fleet-dispatch-codex.py")
        cspec = importlib.util.spec_from_file_location("fleet_dispatch_codex", path)
        cls.codex = importlib.util.module_from_spec(cspec)
        cspec.loader.exec_module(cls.codex)

    def run_dispatch(self, **kw):
        failures = {self.codex.DISPATCHER.PREMISE_CHECK: ["no premise"]}
        with mock.patch.object(self.codex.DISPATCHER, "run_gate", return_value=(True, failures)), \
             mock.patch.object(self.codex, "release_codex_slot") as release, \
             mock.patch("builtins.print"):
            code = self.codex.dispatch_command("AestheticcNext-t1", dry_run=True, **kw)
        return code, release

    def test_bare_force_refused_and_preclaimed_slot_released(self):
        code, release = self.run_dispatch(force=True, pre_claimed="tok")
        self.assertEqual(code, 1)
        release.assert_called_once_with("tok")

    def test_force_with_reason_proceeds(self):
        code, _ = self.run_dispatch(force=True, force_reason="Shane: hotfix now, verify after")
        self.assertEqual(code, 0)

    def test_cli_parses_force_reason(self):
        with mock.patch.object(self.codex, "dispatch_command", return_value=0) as dc:
            self.codex.main(["dispatch", "AestheticcNext-t1", "--force", "--force-reason=why"])
        self.assertEqual(dc.call_args.kwargs["force_reason"], "why")


class ReviewVerdict(unittest.TestCase):
    def test_real_comment_shapes(self):
        cases = {
            "Review: PASS (Codex xhigh + fresh Opus).": "PASS",
            "INDEPENDENT REVIEW rv-gb8rn-20260926 (Astra xhigh, tip 7b856262a): VERDICT PASS.": "PASS",
            "ASTRA XHIGH INDEPENDENT REVIEW 2026-09-26 (tip 447e3b29d) + lead sort: FAIL — 1 blocking": "FAIL",
            "INDEPENDENT REVIEW 2026-09-26 of 01668fe1b (unpushed). COMBINED: FAIL -> NOT PUSHED": "FAIL",
            "started /goal 2026-09-26": None,
            "ATTEMPT 5 RESULT: pushed b80f3ae03. PASS on own gate": None,
            "OWN-GATE REVIEW (builder's gate, NOT the independent review) tip d47c4e0f8: PASS": None,
            # real shapes: xc0p8, 40n0p (x2), 1x8p.19 (x2)
            "ASTRA XHIGH REVIEW 2026-09-25 (tip 496cdd5bd): PASS — 0 P1, 0 P2, 0 P3.": "PASS",
            "ASTRA XHIGH, 24 Sep (tip c13a6f810): PASS, 0 P1, 0 P2, 0 P3 (independent review)": "PASS",
            "BUILDER IN-SESSION REVIEW 2026-09-24 (pre-ship, staged diff on base d403a2ee8; "
            "NOT the independent review): PASS — 0 P1": None,
            "OWN-GATE REVIEW 2026-09-25 (tip 88a240588): PASS (not the independent review).": None,
            "OPUS HEAD-TO-HEAD REVIEW 2026-09-25 (tip 88a240588): PASS — 0 P1, 0 P2, 2 P3.": "PASS",
            "REFLECTION attempt 5: the independent review of 447e3b29d FAILED on F1; PASS next": None,
            # real shapes: b7kjs builder record, LUCY-2w17x measurement note, 6tl2s.4 re-review
            "BUILDER REVIEW RECORD 2026-09-24 (tip 386377440). In-session reviews (NOT the "
            "orchestrator's independent review, which still applies): Astra xhigh r1 PASS": None,
            "MEASUREMENT 2026-09-26: 3 of the first 5 post-cutover beads passed their first "
            "independent review (60%). PASS: ft3o6, wonlt, 22qtw. FAIL: 6tl2s.3": None,
            "Astra xhigh in-session review 2026-09-24 (builder, pre-ship, tip 386377440): PASS": None,
            "INDEPENDENT RE-REVIEW 74f40d342 (delta ffd4227ae..74f40d342): PASS — Astra xhigh": "PASS",
            "OPUS HEAD-TO-HEAD (INDEPENDENT) REVIEW 2026-09-26 (tip 464967846): PASS — 0 P1": "PASS",
        }
        for text, want in cases.items():
            self.assertEqual(fd.review_verdict(text), want, text)


if __name__ == "__main__":
    unittest.main()
