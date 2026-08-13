"""Unit tests for land-batch bin/sessions.py.

Run: python3 -m pytest ~/.claude/skills/land-batch/tests/ -q
"""

import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "land_batch_sessions",
    Path(__file__).resolve().parent.parent / "bin" / "sessions.py",
)
sessions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sessions)


# ---------------------------------------------------------------------------
# transcript line helpers
# ---------------------------------------------------------------------------

def _line(role, text):
    return json.dumps({"type": role, "message": {"role": role, "content": [{"type": "text", "text": text}]}})


def _write_jsonl(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# transcript resolution by sessionId glob (incl. nested <uuid>/ subdir)
# ---------------------------------------------------------------------------

def test_find_transcript_direct(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    sid = "abc-123"
    t = projects / "-Users-shane-foo" / f"{sid}.jsonl"
    _write_jsonl(t, [_line("assistant", "hi")])
    monkeypatch.setattr(sessions, "PROJECTS_DIR", projects)
    assert sessions.find_transcript(sid) == str(t)


def test_find_transcript_nested_subdir(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    sid = "nested-456"
    t = projects / "-Users-shane-bar" / "deadbeef-uuid" / f"{sid}.jsonl"
    _write_jsonl(t, [_line("assistant", "done")])
    monkeypatch.setattr(sessions, "PROJECTS_DIR", projects)
    assert sessions.find_transcript(sid) == str(t)


def test_find_transcript_missing_returns_none_no_fallback(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    # a DIFFERENT session's transcript exists; we must NOT fall back to it.
    _write_jsonl(projects / "-x" / "someone-else.jsonl", [_line("assistant", "nope")])
    monkeypatch.setattr(sessions, "PROJECTS_DIR", projects)
    assert sessions.find_transcript("does-not-exist") is None


# ---------------------------------------------------------------------------
# 64KB tail read + parse
# ---------------------------------------------------------------------------

def test_read_tail_drops_partial_first_line(tmp_path):
    p = tmp_path / "big.jsonl"
    filler = "x" * 100_000
    lines = [_line("assistant", filler), _line("assistant", "LAST_BLOCK")]
    _write_jsonl(p, lines)
    tail = sessions.read_tail(p, nbytes=2000)
    # the 100KB block can't fit in a 2KB tail; the recent small one must survive.
    assert "LAST_BLOCK" in tail
    # and we must not have returned a corrupt half-line as valid JSON
    parsed, _ = sessions.parse_tail(tail)
    assert parsed and parsed[-1] == "LAST_BLOCK"


def test_parse_tail_last_three_assistant_and_last_user():
    text = "\n".join([
        _line("assistant", "a1"),
        _line("user", "u1"),
        _line("assistant", "a2"),
        _line("assistant", "a3"),
        _line("user", "u2"),
        _line("assistant", "a4"),
    ])
    assistant_tail, last_user = sessions.parse_tail(text)
    assert assistant_tail == ["a2", "a3", "a4"]
    assert last_user == "u2"


def test_extract_text_drops_tool_use():
    content = [
        {"type": "tool_use", "name": "Bash", "input": {}},
        {"type": "text", "text": "actual words"},
    ]
    assert sessions.extract_text(content) == "actual words"


def test_parse_tail_skips_garbage_lines():
    text = "not json\n" + _line("assistant", "good") + "\n{broken"
    assistant_tail, _ = sessions.parse_tail(text)
    assert assistant_tail == ["good"]


# ---------------------------------------------------------------------------
# open-loop / sentinel detection (chat tail is veto-only)
# ---------------------------------------------------------------------------

def test_open_loop_trailing_question():
    assert sessions.detect_open_loop(["Which approach would you prefer?"]) is True


def test_open_loop_phrase():
    assert sessions.detect_open_loop(["Done implementing. Let me know if you want changes."]) is True


def test_no_open_loop_on_clean_done():
    assert sessions.detect_open_loop(["Implementation complete. result: all tests green."]) is False


def test_sentinel_detection():
    assert sessions.detect_sentinel(["foo", "result: ok"]) == "result"
    assert sessions.detect_sentinel(["needs input: which db?"]) == "needs input"
    assert sessions.detect_sentinel(["just chatting"]) is None


def test_subagent_blocker_vetoes(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    main_t = projects / "-proj" / "main.jsonl"
    _write_jsonl(main_t, [_line("assistant", "looks idle and done")])
    # a subagent is mid-question
    _write_jsonl(projects / "-proj" / "subagents" / "sub1.jsonl",
                 [_line("assistant", "Should I proceed with the destructive step?")])
    assert sessions.scan_subagents_for_blockers(str(main_t)) is True


# ---------------------------------------------------------------------------
# ACTIVE truth table — the load-bearing safety logic
# ---------------------------------------------------------------------------

def test_active_busy_and_alive(monkeypatch):
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: True)
    assert sessions.is_active(123, "busy", None) is True


def test_dead_pid_stuck_busy_is_not_active(monkeypatch):
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: False)
    # the whole point: a crashed session left status=busy must read inactive.
    assert sessions.is_active(123, "busy", time.time()) is False


def test_idle_alive_recent_transcript_is_active(monkeypatch):
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: True)
    now = 1_000_000.0
    assert sessions.is_active(123, "idle", now - 60, now=now) is True


def test_idle_alive_stale_transcript_is_inactive(monkeypatch):
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: True)
    now = 1_000_000.0
    assert sessions.is_active(123, "idle", now - 600, now=now) is False


def test_idle_alive_no_transcript_is_inactive(monkeypatch):
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: True)
    assert sessions.is_active(123, "idle", None) is False


# ---------------------------------------------------------------------------
# cwd -> session join + collect() preference rules
# ---------------------------------------------------------------------------

def _session_file(dir_, pid, sid, cwd, status, updated_at, kind="bg", name=None):
    rec = {"pid": pid, "sessionId": sid, "cwd": cwd, "status": status,
           "updatedAt": updated_at, "kind": kind}
    if name is not None:
        rec["name"] = name
    (dir_ / f"{pid}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_collect_joins_by_realpath_cwd(monkeypatch, tmp_path):
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    projects = tmp_path / "projects"
    cwd = tmp_path / "wt"
    cwd.mkdir()
    _write_jsonl(projects / "-enc" / "sid-1.jsonl", [_line("assistant", "result: done")])
    monkeypatch.setattr(sessions, "PROJECTS_DIR", projects)
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: False)
    _session_file(sdir, 111, "sid-1", str(cwd), "idle", 1000)
    out = sessions.collect(sessions_dir=sdir)
    key = os.path.realpath(str(cwd))
    assert key in out
    assert out[key]["session_id"] == "sid-1"
    assert out[key]["sentinel"] == "result"


def test_collect_prefers_active_then_recent(monkeypatch, tmp_path):
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(sessions, "PROJECTS_DIR", tmp_path / "noprojects")
    cwd = str(tmp_path / "shared")
    os.makedirs(cwd, exist_ok=True)
    # pid 200 is alive+busy (active); pid 100 is dead but newer updatedAt
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: int(pid) == 200)
    _session_file(sdir, 100, "sid-100", cwd, "busy", 5000)
    _session_file(sdir, 200, "sid-200", cwd, "busy", 1000)
    out = sessions.collect(sessions_dir=sdir)
    key = os.path.realpath(cwd)
    assert out[key]["pid"] == 200  # active beats newer-but-dead


def test_collect_all_preserves_sessions_that_share_a_cwd(monkeypatch, tmp_path):
    sdir = tmp_path / "sessions"
    sdir.mkdir()
    monkeypatch.setattr(sessions, "PROJECTS_DIR", tmp_path / "noprojects")
    monkeypatch.setattr(sessions, "pid_alive", lambda pid: True)
    cwd = tmp_path / "shared"
    cwd.mkdir()
    _session_file(sdir, 100, "sid-parent", str(cwd), "busy", 1000, name="goal AestheticcNext-abcde")
    _session_file(sdir, 200, "sid-child", str(cwd), "busy", 2000, name="goal AestheticcNext-abcde.2")

    all_records = sessions.collect_all(sessions_dir=sdir)
    preferred = sessions.collect(sessions_dir=sdir)

    assert [record["session_id"] for record in all_records] == ["sid-parent", "sid-child"]
    assert len(preferred) == 1
    assert preferred[os.path.realpath(str(cwd))]["session_id"] == "sid-child"
