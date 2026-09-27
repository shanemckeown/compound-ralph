"""Checks 6-9 of fleet-dispatch.py: host resource gate added after the 26 Sep 2026 kernel
panic (disk 90% full, 115 worktrees, ~300 sessions; memory_pressure read 40-60% free).
Run: uvx pytest ~/.claude/scripts/tests"""
import importlib.util
import os
import subprocess
import unittest

try:
    import pytest
except ImportError:  # `python3 -m unittest discover` in this dir: pytest-style, skip cleanly
    raise unittest.SkipTest("needs pytest: uvx pytest ~/.claude/scripts/tests")

SCRIPT = os.path.expanduser("~/.claude/scripts/fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch_resources", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)


def bg(state, pid=None, name="AestheticcNext-x"):
    row = {"kind": "background", "state": state, "id": "abcd1234", "sessionId": "abcd1234-0", "name": name}
    if pid:
        row["pid"] = pid
    return row


@pytest.fixture
def healthy(monkeypatch):
    monkeypatch.setattr(fd, "probe_disk_free_gb", lambda: 300.0)
    monkeypatch.setattr(fd, "probe_worktree_count", lambda: 10)
    monkeypatch.setattr(fd, "probe_memory_free_pct", lambda: 60)
    monkeypatch.setattr(fd, "probe_agents", lambda: [bg("working")] * 3)
    monkeypatch.setattr(fd, "_pid_alive", lambda pid: True)
    return monkeypatch


def failed(results):
    return {k for k, v in results.items() if v}


def test_all_healthy_passes(healthy):
    results, warnings, details = fd.check_resources()
    assert failed(results) == set()
    assert warnings == {}
    assert "300 GB" in details["6 disk free"]


def test_low_disk_refused_with_fix(healthy):
    healthy.setattr(fd, "probe_disk_free_gb", lambda: 119.0)
    results, _, _ = fd.check_resources()
    assert failed(results) == {"6 disk free"}
    assert any("remove merged+clean worktrees" in p for p in results["6 disk free"])


def test_too_many_worktrees_refused_with_fix(healthy):
    healthy.setattr(fd, "probe_worktree_count", lambda: 41)
    results, _, _ = fd.check_resources()
    assert failed(results) == {"7 worktree count"}
    assert any("git -C" in p and "worktree remove" in p for p in results["7 worktree count"])


def test_worktree_boundary_40_passes(healthy):
    healthy.setattr(fd, "probe_worktree_count", lambda: 40)
    assert failed(fd.check_resources()[0]) == set()


def test_too_many_resident_sessions_refused_and_listed(healthy):
    healthy.setattr(fd, "probe_agents", lambda: [bg("blocked", name=f"b{i}") for i in range(9)])
    results, _, _ = fd.check_resources()
    assert failed(results) == {"8 resident bg sessions"}
    text = "\n".join(results["8 resident bg sessions"])
    assert "claude rm" in text and "b8" in text


def test_resident_counting_rules(monkeypatch):
    monkeypatch.setattr(fd, "_pid_alive", lambda pid: pid == 111)
    rows = [bg("idle", pid=111), bg("done", pid=111),       # live pid: counted whatever the state
            bg("working", pid=222),                          # dead pid: not counted
            bg("working"), bg("blocked"),                    # no pid: working/blocked counted
            bg("idle"), bg("done"),                          # no pid, idle/done: not counted
            {"kind": "interactive", "status": "busy", "pid": 111}]
    assert len(fd.resident_background_sessions(rows)) == 4


def test_low_memory_refused(healthy):
    healthy.setattr(fd, "probe_memory_free_pct", lambda: 24)
    results, _, _ = fd.check_resources()
    assert failed(results) == {"9 memory free"}


def test_agents_timeout_warns_not_refuses(healthy):
    def boom():
        raise subprocess.TimeoutExpired("claude agents --json", fd.AGENTS_TIMEOUT_S)
    healthy.setattr(fd, "probe_agents", boom)
    results, warnings, _ = fd.check_resources()
    assert failed(results) == set()
    assert "timed out" in warnings["8 resident bg sessions"]


def test_agents_failure_warns_not_refuses(healthy):
    def boom():
        raise RuntimeError("registry unavailable")
    healthy.setattr(fd, "probe_agents", boom)
    results, warnings, _ = fd.check_resources()
    assert failed(results) == set()
    assert "registry unavailable" in warnings["8 resident bg sessions"]


def test_disk_probe_failure_fails_closed(healthy):
    def boom():
        raise OSError("no such volume")
    healthy.setattr(fd, "probe_disk_free_gb", boom)
    assert failed(fd.check_resources()[0]) == {"6 disk free"}


def test_status_line_has_all_four_numbers(healthy):
    line = fd.resource_status_line(fd.resource_snapshot())
    assert "300 GB" in line and "worktrees 10" in line and "sessions 3" in line and "60%" in line


def test_probes_are_fast():
    import time
    t = time.time()
    fd.resource_snapshot()
    assert time.time() - t < 2.0


def _stub_bead_checks(mp):
    for name in ("check_bead_is_dispatchable", "check_no_inflight_work",
                 "check_dependencies", "check_epic_has_children", "check_client_triage_tree"):
        mp.setattr(fd, name, lambda b: [])


def test_run_gate_refuses_on_resource_failure(healthy, capsys):
    _stub_bead_checks(healthy)
    healthy.setattr(fd, "probe_worktree_count", lambda: 115)
    failed_, failures = fd.run_gate("AestheticcNext-epic1", is_epic=True)
    assert failed_ and "7 worktree count" in failures
    assert "✗ 7 worktree count — 115 worktrees" in capsys.readouterr().out


def test_run_gate_agents_timeout_is_a_warning_line(healthy, capsys):
    _stub_bead_checks(healthy)
    def boom():
        raise subprocess.TimeoutExpired("claude agents --json", 5)
    healthy.setattr(fd, "probe_agents", boom)
    failed_, _ = fd.run_gate("AestheticcNext-epic1", is_epic=True)
    assert not failed_
    assert "⚠ 8 resident bg sessions — WARN (not refusing)" in capsys.readouterr().out
