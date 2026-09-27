"""Check 5 of fleet-dispatch.py: a single bead needs Outcome, Premise, acceptance and a
file path before a Worker sees it (LUCY-2w17x). Run: python3 -m unittest discover -s
~/.claude/scripts/tests"""
import importlib.util
import os
import unittest

SCRIPT = os.path.expanduser("~/.claude/scripts/fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

COMPLETE = {
    "id": "AestheticcNext-test1",
    "description": "Outcome: pressing Cancel voids every open deposit link.\n"
                   "Premise: 3 prod appointments cancelled with a pending link (SELECT pasted).\n"
                   "Touches lib/payments/deposit-links.ts:120",
    "acceptance_criteria": "npx jest tests/payments/deposit-cancel.test.ts passes",
}


def with_(**overrides):
    record = dict(COMPLETE)
    record.update(overrides)
    return record


class BeadBriefCheck(unittest.TestCase):
    def test_complete_brief_passes(self):
        self.assertEqual(fd.check_bead_brief(COMPLETE), [])

    def test_missing_outcome_refused(self):
        problems = fd.check_bead_brief(with_(description=COMPLETE["description"].replace("Outcome:", "Goal:")))
        self.assertEqual(len(problems), 1)
        self.assertIn("Outcome", problems[0])

    def test_missing_premise_refused(self):
        problems = fd.check_bead_brief(with_(description=COMPLETE["description"].replace("Premise:", "Why:")))
        self.assertIn("Premise", problems[0])

    def test_empty_acceptance_refused(self):
        problems = fd.check_bead_brief(with_(acceptance_criteria="   "))
        self.assertIn("acceptance_criteria", problems[0])

    def test_no_file_path_refused(self):
        record = with_(description="Outcome: x does y\nPremise: n/a — new capability",
                       acceptance_criteria="drive the diary and see it")
        self.assertIn("file path", fd.check_bead_brief(record)[0])

    def test_all_missing_names_all_four(self):
        problems = fd.check_bead_brief({"id": "b", "description": "fix the thing"})
        for word in ("Outcome", "Premise", "acceptance_criteria", "file path"):
            self.assertIn(word, problems[0])

    def test_premise_added_by_comment_counts(self):
        record = with_(description="Outcome: pressing X does Y\nlib/a.ts",
                       comments=[{"text": "Premise: 12 rows on prod (query in comment)"}])
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_markdown_decorated_labels_count(self):
        record = with_(description="**Outcome promise:** pressing X does Y\n"
                                   "## Premise check (prod)\nrows found\nlib/a.ts")
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_label_word_mid_sentence_does_not_count(self):
        record = with_(description="The outcome: unclear. Our premise: vague.\nlib/a.ts")
        problems = fd.check_bead_brief(record)
        self.assertIn("Outcome", problems[0])
        self.assertIn("Premise", problems[0])

    def test_gate_refuses_single_bead_on_incomplete_brief(self):
        # Mutation guard: if run_gate stops calling check 5, this goes red.
        stubs = {
            "check_bead_is_dispatchable": lambda b: [],
            "check_no_inflight_work": lambda b: [],
            "check_dependencies": lambda b: [],
            "check_headless_eligible": lambda b, out=None: [],
            "load_bead_record": lambda b: ({"id": b, "description": "fix it"}, None),
            "list_open_children": lambda b: [],  # check 10: own test file
            "check_resources": lambda snap=None: ({}, {}, {}),  # host checks 6-9: own test file
        }
        saved = {name: getattr(fd, name) for name in stubs}
        try:
            for name, fn in stubs.items():
                setattr(fd, name, fn)
            failed, failures = fd.run_gate("AestheticcNext-test2", is_epic=False)
        finally:
            for name, fn in saved.items():
                setattr(fd, name, fn)
        self.assertTrue(failed)
        self.assertIn("5 bead brief complete", failures)

    def test_pre_cutover_bead_needs_only_acceptance_and_path(self):
        record = {"id": "old", "created_at": "2026-09-20T10:00:00Z",
                  "description": "fix lib/a.ts", "acceptance_criteria": "npx jest a.test.ts"}
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_pre_cutover_bead_still_needs_acceptance(self):
        record = {"id": "old", "created_at": "2026-09-20T10:00:00Z", "description": "fix lib/a.ts"}
        problems = fd.check_bead_brief(record)
        self.assertIn("acceptance_criteria", problems[0])
        self.assertNotIn("Outcome", problems[0])

    def test_cutover_day_bead_needs_outcome(self):
        record = {"id": "new", "created_at": "2026-09-25T00:01:00Z",
                  "description": "fix lib/a.ts", "acceptance_criteria": "npx jest a.test.ts"}
        self.assertIn("Outcome", fd.check_bead_brief(record)[0])

    # --- Astra review 2026-09-25 counterexamples ---
    def test_placeholder_acceptance_refused(self):
        for value in ("TBD", "todo", "n/a", "???", "see above"):
            self.assertIn("acceptance_criteria", fd.check_bead_brief(with_(acceptance_criteria=value))[0], value)

    def test_version_and_domain_are_not_file_paths(self):
        record = with_(description="Outcome: x does y\nPremise: rows on prod\nbump to 2.0 on example.com",
                       acceptance_criteria="drive the diary and see the version")
        self.assertIn("file path", fd.check_bead_brief(record)[0])

    def test_directory_path_without_extension_counts(self):
        record = with_(description="Outcome: x does y\nPremise: rows on prod\nsee mobile/src/terminal")
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_empty_labels_refused(self):
        record = with_(description="Outcome:\nPremise:\nlib/a.ts",
                       acceptance_criteria="drive cancel in the diary")
        problems = fd.check_bead_brief(record)
        self.assertIn("Outcome", problems[0])
        self.assertIn("Premise", problems[0])

    def test_empty_heading_followed_by_next_label_refused(self):
        record = with_(description="## Outcome\n## Premise\nrows on prod\nlib/a.ts",
                       acceptance_criteria="drive cancel in the diary")
        self.assertIn("Outcome", fd.check_bead_brief(record)[0])

    def test_numbered_labels_count(self):
        record = with_(description="1. Outcome promise: pressing X does Y\n"
                                   "2. Premise check (prod): 4 rows\nlib/a.ts")
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_premise_na_needs_a_reason(self):
        bare = with_(description="Outcome: x does y\nPremise: n/a\nlib/a.ts")
        self.assertIn("Premise", fd.check_bead_brief(bare)[0])
        reasoned = with_(description="Outcome: x does y\nPremise: n/a — new capability, no prod state\nlib/a.ts")
        self.assertEqual(fd.check_bead_brief(reasoned), [])

    def test_malformed_created_at_is_treated_as_new(self):
        record = {"id": "x", "created_at": "0000-not-a-date",
                  "description": "fix lib/a.ts", "acceptance_criteria": "npx jest a.test.ts"}
        self.assertIn("Outcome", fd.check_bead_brief(record)[0])

    def test_fix_hint_has_no_literal_backslash_n(self):
        problems = fd.check_bead_brief({"id": "X", "description": "fix it"})
        self.assertNotIn("\\n", problems[0])
        self.assertIn('bd comment X "Premise: …"', problems[0])

    # --- Astra review round 2 ---
    def test_more_placeholder_acceptance_refused_and_short_real_command_passes(self):
        for value in ("works as expected", "to be determined", "it works"):
            self.assertIn("acceptance_criteria", fd.check_bead_brief(with_(acceptance_criteria=value))[0], value)
        self.assertEqual(fd.check_bead_brief(with_(acceptance_criteria="make qa")), [])

    def test_extensionless_build_files_count_and_urls_do_not(self):
        base = "Outcome: x does y\nPremise: rows on prod\n"
        ok = with_(description=base + "edit the Dockerfile", acceptance_criteria="docker build passes")
        self.assertEqual(fd.check_bead_brief(ok), [])
        url = with_(description=base + "see https://aestheti.cc/settings", acceptance_criteria="drive it")
        self.assertIn("file path", fd.check_bead_brief(url)[0])

    def test_empty_heading_does_not_borrow_next_populated_label(self):
        record = with_(description="## Outcome\nPremise: 4 rows on prod\nlib/a.ts",
                       acceptance_criteria="drive cancel in the diary")
        problems = fd.check_bead_brief(record)
        self.assertIn("Outcome", problems[0])
        self.assertNotIn("Premise", problems[0])

    def test_empty_colon_heading_does_not_borrow_bold_label(self):
        record = with_(description="## Outcome:\n**Premise**: 4 rows on prod\nlib/a.ts",
                       acceptance_criteria="drive cancel in the diary")
        problems = fd.check_bead_brief(record)
        self.assertIn("Outcome", problems[0])
        self.assertNotIn("Premise", problems[0])

    def test_real_bd_timestamp_before_cutover_is_old(self):
        record = {"id": "x", "created_at": "2026-09-23T15:08:47Z",
                  "description": "fix lib/a.ts", "acceptance_criteria": "npx jest a.test.ts"}
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_colon_ended_heading_with_body_counts(self):
        record = with_(description="## Outcome:\npressing X does Y\n## Premise:\n4 rows on prod\nlib/a.ts")
        self.assertEqual(fd.check_bead_brief(record), [])

    def test_invalid_dates_are_treated_as_new(self):
        for created in ("0000-00-00", "2026-02-30T12:00:00Z", "2026-09-24garbage", "1999-01-01T00:00:00Z",
                        "2026-09-24T99:99:99Z", "2026-09-24Tgarbage"):
            record = {"id": "x", "created_at": created,
                      "description": "fix lib/a.ts", "acceptance_criteria": "npx jest a.test.ts"}
            self.assertIn("Outcome", fd.check_bead_brief(record)[0], created)

    def test_epic_skips_brief_check(self):
        stubs = {
            "check_bead_is_dispatchable": lambda b: [],
            "check_no_inflight_work": lambda b: [],
            "check_dependencies": lambda b: [],
            "check_epic_has_children": lambda b: [],
            "check_client_triage_tree": lambda b: [],  # check 10: own test file
            "check_premise_tree": lambda b: [],  # check 11: own test file
            "check_resources": lambda snap=None: ({}, {}, {}),  # host checks 6-9: own test file
        }
        saved = {name: getattr(fd, name) for name in stubs}
        try:
            for name, fn in stubs.items():
                setattr(fd, name, fn)
            failed, _ = fd.run_gate("AestheticcNext-epic1", is_epic=True)
        finally:
            for name, fn in saved.items():
                setattr(fd, name, fn)
        self.assertFalse(failed)


if __name__ == "__main__":
    unittest.main()
