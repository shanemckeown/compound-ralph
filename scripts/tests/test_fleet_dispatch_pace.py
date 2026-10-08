"""Check 12 of fleet-dispatch.py: when Claude weekly use runs ahead of elapsed time, P2+ dispatch
is refused; P0/P1 and dated client commitments pass; a missing/unreadable/stale file never blocks
(LUCY-r1lyy, Shane 7 Oct 2026).
Run: python3 -m pytest ~/.claude/scripts/tests/test_fleet_dispatch_pace.py"""
import datetime
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

SCRIPT = os.path.expanduser("~/.claude/scripts/fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

UTC = datetime.timezone.utc
START = datetime.datetime(2026, 10, 6, 13, 20, tzinfo=UTC)       # 14:20 BST
NOW = START + datetime.timedelta(hours=21)                         # 11:20 BST 7 Oct: 12.5% elapsed


def bead(priority=2, labels=(), description="", comments=(), **extra):
    return dict({"id": "AestheticcNext-p1", "priority": priority, "labels": list(labels),
                 "description": description, "comments": [{"text": c} for c in comments]}, **extra)


class Pace(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.path = os.path.join(self.dir.name, "CREDIT_PACE.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, used_pct=22, updated=NOW, start=START, hours=168, raw=None):
        with open(self.path, "w") as fh:
            fh.write(raw if raw is not None else json.dumps({
                "window_start": start.isoformat(), "window_hours": hours,
                "used_pct": used_pct, "updated": updated.isoformat()}))

    def check(self, record, now=NOW):
        return fd.check_credit_pace(record["id"], record, pace_path=self.path, now=now)

    # ---- the rule -------------------------------------------------------------------------
    def test_seed_numbers_are_over_pace(self):
        self.write()
        pace, note = fd.credit_pace(self.path, NOW)
        self.assertIsNone(note)
        self.assertAlmostEqual(pace["elapsed_pct"], 12.5, places=1)
        self.assertAlmostEqual(pace["ratio"], 1.76, places=2)

    def test_over_pace_refuses_p2_p3_p4(self):
        self.write()
        for p in (2, 3, 4):
            problems, warning, _ = self.check(bead(priority=p))
            self.assertEqual(len(problems), 2, p)
            self.assertIn(f"P{p} work waits", problems[0])
            self.assertIn("--force-reason", problems[1])
            self.assertIsNone(warning)

    def test_over_pace_p0_p1_pass(self):
        self.write()
        for p in (0, 1):
            problems, warning, detail = self.check(bead(priority=p))
            self.assertEqual(problems, [], p)
            self.assertIn(f"P{p} passes", detail)

    def test_over_pace_dated_client_commitment_passes(self):
        self.write()
        for record in (bead(labels=["client:viso"], comments=["Triage: promise — promised for 10 Oct"]),
                       bead(priority=3, labels=["client-request"], due_at="2026-10-10T09:00:00Z")):
            problems, _, detail = self.check(record)
            self.assertEqual(problems, [], record)
            self.assertIn("dated client commitment", detail)

    def test_undated_client_request_still_refused(self):
        self.write()
        problems, _, _ = self.check(bead(labels=["client-request"], comments=["Triage: regression"]))
        self.assertTrue(problems)
        # a promise line without a client label is not a client commitment
        problems, _, _ = self.check(bead(comments=["Triage: promise"]))
        self.assertTrue(problems)

    def test_at_or_under_pace_passes(self):
        self.write(used_pct=15)          # 15 / 12.5 = 1.2x
        problems, warning, detail = self.check(bead(priority=3))
        self.assertEqual(problems, [])
        self.assertIsNone(warning)
        self.assertIn("1.20x", detail)

    def test_early_window_noise_never_refuses(self):
        self.write(used_pct=5, updated=START + datetime.timedelta(hours=2))
        problems, _, _ = self.check(bead(priority=3), now=START + datetime.timedelta(hours=2))
        self.assertEqual(problems, [])

    # ---- fail open ------------------------------------------------------------------------
    def test_missing_file_fails_open(self):
        problems, warning, _ = self.check(bead(priority=3))
        self.assertEqual(problems, [])
        self.assertIn("not enforced", warning)

    def test_unreadable_file_fails_open(self):
        for raw in ("{not json", json.dumps({"used_pct": 50}), json.dumps(
                {"window_start": "yesterday", "used_pct": 50, "updated": NOW.isoformat()})):
            self.write(raw=raw)
            problems, warning, _ = self.check(bead(priority=3))
            self.assertEqual(problems, [], raw)
            self.assertIn("not enforced", warning)

    def test_naive_timestamp_fails_open(self):
        self.write(raw=json.dumps({"window_start": "2026-10-06T14:20:00", "window_hours": 168,
                                   "used_pct": 90, "updated": "2026-10-07T11:00:00"}))
        problems, warning, _ = self.check(bead(priority=3))
        self.assertEqual(problems, [])
        self.assertIn("not enforced", warning)

    def test_stale_file_fails_open(self):
        self.write(used_pct=90, updated=NOW - datetime.timedelta(hours=25))
        problems, warning, _ = self.check(bead(priority=3))
        self.assertEqual(problems, [])
        self.assertIn("25h ago", warning)

    def test_stale_file_says_the_guard_is_blind(self):
        self.write(used_pct=90, updated=NOW - datetime.timedelta(hours=25))
        _, warning, _ = self.check(bead(priority=3))
        self.assertIn(fd.PACE_BLIND, warning)

    def test_run_gate_prints_blind_warning_line_when_stale(self):
        self.write(used_pct=90, updated=NOW - datetime.timedelta(hours=30))
        failed, _, printed = self.run_gate(bead(priority=3))
        self.assertFalse(failed)
        self.assertIn(f"WARNING: {fd.PACE_BLIND}", printed)

    def test_fresh_file_prints_no_blind_warning(self):
        self.write()
        _, _, printed = self.run_gate(bead(priority=1))
        self.assertNotIn("WARNING:", printed)

    def test_window_over_fails_open(self):
        self.write(used_pct=99, updated=START + datetime.timedelta(hours=170))
        problems, warning, _ = self.check(bead(priority=3), now=START + datetime.timedelta(hours=170))
        self.assertEqual(problems, [])
        self.assertIn("outside the window", warning)

    def test_unreadable_priority_fails_open(self):
        self.write()
        problems, warning, _ = self.check(bead(priority=None))
        self.assertEqual(problems, [])
        self.assertIn("no readable priority", warning)

    def test_record_load_error_fails_open(self):
        self.write()
        with mock.patch.object(fd, "load_bead_record", return_value=(None, "bd down")):
            problems, warning, _ = fd.check_credit_pace("AestheticcNext-x", None, self.path, NOW)
        self.assertEqual(problems, [])
        self.assertIn("bd down", warning)

    def test_under_pace_never_loads_the_record(self):
        self.write(used_pct=10)
        with mock.patch.object(fd, "load_bead_record", side_effect=AssertionError("loaded")):
            problems, _, _ = fd.check_credit_pace("AestheticcNext-x", None, self.path, NOW)
        self.assertEqual(problems, [])

    # ---- wiring ---------------------------------------------------------------------------
    def test_force_past_check_12_needs_a_reason(self):
        failures = {fd.CREDIT_PACE_CHECK: ["x"]}
        self.assertIn("check 12", fd.force_refusal(failures, True, None))
        self.assertIsNone(fd.force_refusal(failures, True, "Shane: clinic demo tomorrow"))
        self.assertIsNone(fd.force_refusal(failures, False, None))

    def run_gate(self, record, is_epic=False):
        stubs = dict(check_bead_is_dispatchable=lambda b: [], check_no_inflight_work=lambda b: [],
                     check_dependencies=lambda b: [], check_epic_has_children=lambda b: [],
                     check_client_triage_tree=lambda *a: [], check_headless_eligible=lambda *a: [],
                     check_bead_brief=lambda r: [], check_premise_tree=lambda *a: [],
                     load_bead_record=lambda b: (record, None), list_open_children=lambda b: [],
                     check_resources=lambda: ({}, {}, {}), CREDIT_PACE_FILE=self.path,
                     check_wip=lambda: ([], None, "0 pushed-not-landed goal/* branches (cap 10)"))
        with mock.patch.multiple(fd, **stubs), mock.patch("builtins.print") as out, \
             mock.patch.object(fd.datetime, "datetime", wraps=datetime.datetime) as dt:
            dt.now.return_value = NOW
            failed, failures = fd.run_gate(record["id"], is_epic)
        return failed, failures, " ".join(str(c.args[0]) for c in out.call_args_list if c.args)

    def test_run_gate_refuses_p3_over_pace_single_and_epic(self):
        self.write()
        for is_epic in (False, True):
            failed, failures, printed = self.run_gate(bead(priority=3), is_epic)
            self.assertTrue(failed, is_epic)
            self.assertIn(fd.CREDIT_PACE_CHECK, failures)
            self.assertIn("✗ 12 credit pace", printed)

    def test_run_gate_passes_p1_and_missing_file(self):
        self.write()
        failed, _, printed = self.run_gate(bead(priority=1))
        self.assertFalse(failed)
        self.assertIn("✓ 12 credit pace", printed)
        os.remove(self.path)
        failed, _, printed = self.run_gate(bead(priority=3))
        self.assertFalse(failed)
        self.assertIn("⚠ 12 credit pace — WARN (not refusing)", printed)


if __name__ == "__main__":
    unittest.main()
