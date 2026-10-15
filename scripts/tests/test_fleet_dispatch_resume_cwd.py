"""LUCY-31lki: a resumed worker starts in the worktree that already has its branch checked out,
so it never `cd`s / `git -C`s into a path outside its working dirs (9ift6 sat on that prompt).
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests"""
import importlib.util
import os
import unittest

try:
    import pytest  # noqa: F401
except ImportError:
    raise unittest.SkipTest("needs pytest: uvx pytest ~/.claude/scripts/tests")

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch_resume_cwd", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

WT = """/Users/shane/Documents/GitReBase/AestheticcNext  8459b6e1ed [main]
/Users/shane/.worktrees/AestheticcNext/b9-9ift6-r4  1a2b3c4d5e [goal/aestheticcnext-9ift6-r4]
/Users/shane/Documents/GitReBase/AestheticcNext/.claude/worktrees/goal+aestheticcnext-9ift6  0f0f0f0f0f [goal/aestheticcnext-9ift6]
/Users/shane/.worktrees/AestheticcNext/detached  abcdef1234 (detached HEAD)
"""


def test_picks_the_worktree_with_the_target_branch_checked_out():
    got = fd.resume_worktree(WT, "goal/aestheticcnext-9ift6-r4", isdir=lambda p: True)
    assert got == "/Users/shane/.worktrees/AestheticcNext/b9-9ift6-r4"


def test_exact_branch_match_not_prefix():
    got = fd.resume_worktree(WT, "goal/aestheticcnext-9ift6", isdir=lambda p: True)
    assert got.endswith("goal+aestheticcnext-9ift6")


def test_branch_only_or_missing_dir_falls_back_to_none():
    assert fd.resume_worktree(WT, "goal/aestheticcnext-zzzzz", isdir=lambda p: True) is None
    assert fd.resume_worktree(WT, "goal/aestheticcnext-9ift6-r4", isdir=lambda p: False) is None
    assert fd.resume_worktree("", "goal/x", isdir=lambda p: True) is None


def test_dispatch_launches_claude_bg_in_the_given_cwd(monkeypatch, tmp_path):
    calls = []
    mcp = tmp_path / "tier-a.json"
    mcp.write_text("{}")
    monkeypatch.setattr(fd, "TIER_A_MCP", str(mcp))
    monkeypatch.setattr(fd, "POLL_SECONDS", 1)
    rows = [[], [{"sessionId": "s-1", "id": "s1", "name": "AestheticcNext-9ift6"}]]
    monkeypatch.setattr(fd, "registry", lambda: rows.pop(0) if len(rows) > 1 else rows[0])

    def fake_sh(cmd, cwd=None, timeout=60, bead=None):
        calls.append((cmd, cwd))
        return 0, "", ""

    monkeypatch.setattr(fd, "sh", fake_sh)
    wt = "/Users/shane/.worktrees/AestheticcNext/b9-9ift6-r4"
    session = fd.dispatch("AestheticcNext-9ift6", False, "test", cwd=wt)
    assert session["sessionId"] == "s-1"
    bg = [c for c in calls if c[0].startswith("claude --bg")]
    assert bg and bg[0][1] == wt


def test_dispatch_defaults_to_repo(monkeypatch, tmp_path):
    calls = []
    mcp = tmp_path / "tier-a.json"
    mcp.write_text("{}")
    monkeypatch.setattr(fd, "TIER_A_MCP", str(mcp))
    monkeypatch.setattr(fd, "POLL_SECONDS", 1)
    monkeypatch.setattr(fd, "registry", lambda: [{"sessionId": "s-2", "name": "AestheticcNext-x"}]
                        if calls else [])
    monkeypatch.setattr(fd, "sh", lambda cmd, cwd=None, timeout=60, bead=None: calls.append((cmd, cwd)) or (0, "", ""))
    fd.dispatch("AestheticcNext-x", False, "test")
    assert calls[0][1] == fd.REPO


def test_main_launches_the_resume_in_its_worktree_and_says_so(monkeypatch):
    wt = "/Users/shane/.worktrees/AestheticcNext/b9-9ift6-r4"
    calls, got = [], {}
    monkeypatch.setattr(fd, "run_gate", lambda b, e: (fd._GATE_STATE.update(
        resume_target="goal/aestheticcnext-9ift6-r4", resume_cwd=wt) or (False, {})))
    monkeypatch.setattr(fd, "sh", lambda cmd, **k: calls.append(cmd) or (0, "", ""))
    monkeypatch.setattr(fd, "dispatch", lambda *a: got.setdefault("cwd", a[3]) and None)
    monkeypatch.setattr(fd, "write_manifest", lambda *a, **k: got.setdefault("manifest_cwd", k.get("cwd")))
    monkeypatch.setattr(fd.sys, "argv", ["fleet-dispatch.py", "AestheticcNext-9ift6", "--pre-claimed"])
    try:
        with pytest.raises(SystemExit):
            fd.main()
    finally:
        fd._GATE_STATE.clear()
    assert got == {"cwd": wt, "manifest_cwd": wt}
    assert any("RESUME: goal/aestheticcnext-9ift6-r4" in c and f"worktree {wt}" in c for c in calls), calls
