"""Check 14 of fleet-dispatch.py (LUCY-z3ykz.3): one feature = one branch. A single /goal of a
child of an open epic is refused and pointed at `--epic`; a standalone bead and the epic itself
dispatch. Also covers the check 3 BEADS_DIR fix (LUCY-* epics falsely reported childless).
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests/test_fleet_dispatch_epic_child.py -q"""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "fleet-dispatch.py"
spec = importlib.util.spec_from_file_location("fleet_dispatch_epic_child", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

EPIC = {"id": "LUCY-ep1", "issue_type": "epic", "status": "open"}


def loader(records):
    return lambda bead: (records[bead], None) if bead in records else (None, f"no {bead}")


def test_child_of_open_epic_is_refused_and_pointed_at_the_epic():
    problems = fd.check_not_epic_child("LUCY-ep1.2", {"parent": "LUCY-ep1"},
                                       loader({"LUCY-ep1": EPIC}))
    assert "dispatch the epic: fleet-dispatch.py LUCY-ep1 --epic" in problems[0]


@pytest.mark.parametrize("status", ["open", "in_progress", "blocked"])
def test_any_unclosed_epic_parent_refuses(status):
    parent = dict(EPIC, status=status)
    assert fd.check_not_epic_child("LUCY-ep1.2", {"parent": "LUCY-ep1"}, loader({"LUCY-ep1": parent}))


def test_standalone_bead_is_allowed():
    assert fd.check_not_epic_child("LUCY-solo", {"id": "LUCY-solo"}, loader({})) == []


def test_child_of_closed_epic_or_non_epic_parent_is_allowed():
    closed = dict(EPIC, status="closed")
    assert fd.check_not_epic_child("x.1", {"parent": "LUCY-ep1"}, loader({"LUCY-ep1": closed})) == []
    task = dict(EPIC, issue_type="task")
    assert fd.check_not_epic_child("x.1", {"parent": "LUCY-ep1"}, loader({"LUCY-ep1": task})) == []


def test_unreadable_parent_fails_closed():
    problems = fd.check_not_epic_child("x.1", {"parent": "LUCY-gone"}, loader({}))
    assert problems and "cannot verify parent LUCY-gone" in problems[0]


def _gate(bead, is_epic, record):
    stubs = {
        "check_bead_is_dispatchable": lambda b: [],
        "check_no_inflight_work": lambda b: [],
        "check_dependencies": lambda b: [],
        "check_headless_eligible": lambda b, out=None: [],
        "check_bead_brief": lambda r: [],
        "check_epic_has_children": lambda b: [],
        "check_client_triage_tree": lambda b, r=None: [],
        "check_premise_tree": lambda b, r=None: [],
        "load_bead_record": loader({bead: record, "LUCY-ep1": EPIC}),
        "check_resources": lambda snap=None: ({}, {}, {}),
        "check_credit_pace": lambda b, r=None: ([], None, None),
        "check_wip": lambda: ([], None, None),
    }
    saved = {name: getattr(fd, name) for name in stubs}
    try:
        for name, fn in stubs.items():
            setattr(fd, name, fn)
        return fd.run_gate(bead, is_epic=is_epic)
    finally:
        for name, fn in saved.items():
            setattr(fd, name, fn)


def test_run_gate_refuses_single_dispatch_of_epic_child():
    failed, failures = _gate("LUCY-ep1.2", False, {"id": "LUCY-ep1.2", "parent": "LUCY-ep1"})
    assert failed and fd.EPIC_CHILD_CHECK in failures


def test_run_gate_allows_standalone_bead():
    failed, failures = _gate("LUCY-solo", False, {"id": "LUCY-solo"})
    assert not failed and fd.EPIC_CHILD_CHECK not in failures


def test_run_gate_allows_the_epic_itself_with_epic_flag():
    failed, failures = _gate("LUCY-ep1", True, EPIC)
    assert not failed and fd.EPIC_CHILD_CHECK not in failures


def test_force_past_check_14_needs_p0_and_reason():
    failures = {fd.EPIC_CHILD_CHECK: ["x"]}
    assert "P0" in fd.force_refusal(failures, True, "client down", priority=1)
    assert "P0" in fd.force_refusal(failures, True, "  ", priority=0)
    assert "P0" in fd.force_refusal(failures, True, "client down")
    assert fd.force_refusal(failures, True, "prod booking broken", priority=0) is None


def test_check_3_queries_the_beads_db_for_the_epics_prefix(monkeypatch):
    seen = {}

    def fake_sh(cmd, cwd=None, timeout=60, bead=None):
        seen["dir"] = fd.beads_dir(bead) if bead else fd.REPO_BEADS
        return 0, "LUCY-z3ykz.1 child", ""

    monkeypatch.setattr(fd, "sh", fake_sh)
    assert fd.check_epic_has_children("LUCY-z3ykz") == []
    assert seen["dir"] == fd.VAULT_BEADS
