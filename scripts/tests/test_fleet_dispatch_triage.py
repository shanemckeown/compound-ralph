"""Check 10 of fleet-dispatch.py: a client-reported bead needs 'Triage: regression|promise'
before a Worker builds it; by-design / own-request means reply, don't build (LUCY-nzmtr).
Run: python3 -m unittest discover -s ~/.claude/scripts/tests"""
import importlib.util
import json
import os
import unittest

SCRIPT = os.path.expanduser("~/.claude/scripts/fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)


def bead(labels, description="Leah can't book two rooms", comments=()):
    return {"id": "AestheticcNext-t1", "labels": list(labels), "description": description,
            "comments": [{"text": c} for c in comments]}


class ClientTriageCheck(unittest.TestCase):
    def test_untriaged_client_labels_refused(self):
        for labels in (["client-reported"], ["capture-watcher"], ["client:viso"],
                       ["harness", "client:city-medispa"]):
            problems = fd.check_client_triage(bead(labels))
            self.assertEqual(len(problems), 1, labels)
            self.assertIn("no 'Triage:' line", problems[0])

    def test_non_client_bead_unaffected(self):
        for labels in ([], ["harness"], ["client-portal"], ["headless-eligible"]):
            self.assertEqual(fd.check_client_triage(bead(labels)), [], labels)

    def test_by_design_and_own_request_refused_reply_dont_build(self):
        for line in ("Triage: by-design — one-room rule since 22 Sep",
                     "Triage: by design, Settings → Rooms covers it",
                     "**Triage:** own-request (Viso asked 22 Sep)",
                     "Triage: own request"):
            problems = fd.check_client_triage(bead(["client-reported"], "report\n" + line))
            self.assertEqual(len(problems), 1, line)
            self.assertIn("reply, don't build", problems[0])

    def test_regression_and_promise_pass(self):
        for line in ("Triage: regression — worked until v0.22.58, designed to allow it",
                     "Triage: promise — promised NY Skin for 3 Oct",
                     "- Triage: Regression"):
            self.assertEqual(fd.check_client_triage(bead(["client:viso"], "report\n" + line)), [], line)

    def test_triage_by_comment_and_latest_wins(self):
        self.assertEqual(fd.check_client_triage(
            bead(["client-reported"], comments=["Triage: promise — dated 1 Oct"])), [])
        problems = fd.check_client_triage(
            bead(["client-reported"], "Triage: regression",
                 comments=["Triage: own-request — Viso asked for this on 22 Sep"]))
        self.assertIn("reply, don't build", problems[0])

    def test_mid_sentence_mention_does_not_count(self):
        problems = fd.check_client_triage(
            bead(["client-reported"], "needs a 'Triage: regression' line before dispatch"))
        self.assertIn("no 'Triage:' line", problems[0])

    def test_unknown_verdict_refused(self):
        problems = fd.check_client_triage(bead(["client-reported"], "Triage: maybe"))
        self.assertIn("unrecognised", problems[0])

    def test_legacy_clinic_label_spellings_count(self):
        for labels in (["client-city-medispa"], ["client-viso"], ["client_helena_fimpel"],
                       ["client-request"]):
            self.assertIn("no 'Triage:' line", fd.check_client_triage(bead(labels))[0], labels)
        for labels in (["client-portal"], ["client-onboarding"], ["client-facing"], ["clients"]):
            self.assertEqual(fd.check_client_triage(bead(labels)), [], labels)

    def test_markdown_formatted_verdicts(self):
        self.assertEqual(fd.check_client_triage(
            bead(["client-reported"], "Triage: `regression` — broke in 0.22.58")), [])
        problems = fd.check_client_triage(
            bead(["client-reported"], "Triage: regression",
                 comments=["Triage: `own-request` — Viso asked for this"]))
        self.assertIn("reply, don't build", problems[0])

    def test_unfinished_or_uncertain_verdict_refused(self):
        for line in ("Triage: regression|promise|by-design|own-request — <evidence>",
                     "Triage: regression?", "Triage: promised nothing", "Triage: regression / promise"):
            problems = fd.check_client_triage(bead(["client-reported"], line))
            self.assertIn("unrecognised", problems[0], line)

    def test_invalid_latest_line_does_not_keep_older_permission(self):
        problems = fd.check_client_triage(
            bead(["client-reported"], "Triage: regression", comments=["Triage: not sure yet"]))
        self.assertIn("unrecognised", problems[0])

    def _epic_triage(self, epic, children, records, record=None):
        saved = (fd.load_bead_record, fd.sh)
        calls = []
        fd.load_bead_record = lambda b: (records[b], None)
        fd.sh = lambda cmd, **kw: (calls.append(cmd), (0, json.dumps(children), ""))[1]
        try:
            return fd.check_client_triage_tree(epic, record)
        finally:
            fd.load_bead_record, fd.sh = saved
            self.last_calls = calls

    def test_epic_refuses_untriaged_client_child(self):
        epic = {"id": "E", "labels": [], "description": "City MediSpa bundle"}
        child_ok = dict(bead(["client:city-medispa"], "Triage: regression — x"), id="E.1")
        child_bad = dict(bead(["client-reported"]), id="E.2")
        plain = {"id": "E.3", "labels": ["harness"], "description": ""}
        records = {"E": epic, "E.1": child_ok, "E.2": child_bad}
        problems = self._epic_triage("E", [child_ok, child_bad, plain], records)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("E.2:"))
        records["E.2"] = dict(child_bad, comments=[{"text": "Triage: promise — 3 Oct"}])
        self.assertEqual(self._epic_triage("E", [child_ok, child_bad, plain], records), [])

    def test_child_listing_is_uncapped(self):
        # bd list returns 50 rows by default; AestheticcNext-r8gzp has 56 open children.
        self._epic_triage("E", [], {"E": {"id": "E", "labels": []}})
        self.assertIn("--limit 0", self.last_calls[0])

    def test_epic_client_label_itself_needs_triage(self):
        epic = dict(bead(["client-reported"]), id="E")
        problems = self._epic_triage("E", [], {"E": epic})
        self.assertTrue(problems[0].startswith("E:"))

    def test_child_listing_failure_refuses(self):
        saved = fd.sh
        fd.sh = lambda cmd, **kw: (1, "", "dolt down")
        try:
            problems = fd.check_client_triage_tree("E", {"id": "E", "labels": []})
        finally:
            fd.sh = saved
        self.assertIn("cannot list", problems[0])

    def test_gate_runs_triage_for_epics(self):
        stubs = {
            "check_bead_is_dispatchable": lambda b: [],
            "check_no_inflight_work": lambda b: [],
            "check_dependencies": lambda b: [],
            "check_epic_has_children": lambda b: [],
            "load_bead_record": lambda b: (dict(bead(["client-reported"]), id=b) if b == "E.2"
                                           else {"id": b, "labels": []}, None),
            "list_open_children": lambda b: [dict(bead(["client-reported"]), id="E.2")],
            "check_resources": lambda snap=None: ({}, {}, {}),
            "check_wip": lambda: ([], None, None),  # check 13: own test file
        }
        saved = {name: getattr(fd, name) for name in stubs}
        try:
            for name, fn in stubs.items():
                setattr(fd, name, fn)
            failed, failures = fd.run_gate("E", is_epic=True)
        finally:
            for name, fn in saved.items():
                setattr(fd, name, fn)
        self.assertTrue(failed)
        self.assertIn("10 client triage", failures)

    def _gate(self, record, children=()):
        stubs = {
            "check_bead_is_dispatchable": lambda b: [],
            "check_no_inflight_work": lambda b: [],
            "check_dependencies": lambda b: [],
            "check_headless_eligible": lambda b, out=None: [],
            "check_bead_brief": lambda r: [],
            "load_bead_record": lambda b: (record if b == record["id"] else
                                           next(c for c in children if c["id"] == b), None),
            "list_open_children": lambda b: list(children),
            "check_resources": lambda snap=None: ({}, {}, {}),
            "check_wip": lambda: ([], None, None),  # check 13: own test file
        }
        saved = {name: getattr(fd, name) for name in stubs}
        try:
            for name, fn in stubs.items():
                setattr(fd, name, fn)
            return fd.run_gate(record["id"], is_epic=False)
        finally:
            for name, fn in saved.items():
                setattr(fd, name, fn)

    def test_gate_refuses_untriaged_and_passes_regression(self):
        # Mutation guard: if run_gate stops calling check 10, the first assert goes red.
        failed, failures = self._gate(bead(["client-reported"]))
        self.assertTrue(failed)
        self.assertIn("10 client triage", failures)
        failed, failures = self._gate(bead(["client-reported"], "Triage: regression — x"))
        self.assertFalse(failed, failures)

    def test_single_bead_goal_checks_its_children(self):
        # /goal also expands an epic's open children when dispatched without --epic.
        parent = {"id": "P", "labels": [], "description": "small bundle"}
        child = dict(bead(["client:viso"], "Triage: own-request — Viso asked 22 Sep"), id="P.1")
        failed, failures = self._gate(parent, [child])
        self.assertTrue(failed)
        self.assertIn("reply, don't build", failures["10 client triage"][0])


if __name__ == "__main__":
    unittest.main()
