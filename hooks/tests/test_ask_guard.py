"""Tests for ask-guard.py + shane-presence.py. Run: uvx pytest ~/.claude/hooks/tests/test_ask_guard.py"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(HOOKS, "ask-guard.py")
PRESENCE = os.path.join(HOOKS, "shane-presence.py")
SID = "sess-1"


def env(state_dir, **extra):
    e = {k: v for k, v in os.environ.items()
         if k not in ("SPAWNED_SESSION", "CLAUDE_CODE_ENTRYPOINT", "ASK_GUARD_WINDOW_MIN")}
    e["PRESENCE_STATE_DIR"] = str(state_dir)
    e.update(extra)
    return e


def prompt(state_dir, text, sid=SID, transcript="", **payload):
    p = {"hook_event_name": "UserPromptSubmit", "session_id": sid, "prompt": text,
         "transcript_path": transcript, **payload}
    r = subprocess.run([sys.executable, PRESENCE], input=json.dumps(p), capture_output=True,
                       text=True, env=env(state_dir), timeout=20)
    assert r.returncode == 0 and r.stdout == ""


def ask(state_dir, sid=SID, **extra_env):
    p = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": sid,
         "tool_input": {"questions": [{"question": "Land it?"}]}}
    r = subprocess.run([sys.executable, GUARD], input=json.dumps(p), capture_output=True,
                       text=True, env=env(state_dir, **extra_env), timeout=20)
    assert r.returncode == 0
    if not r.stdout.strip():
        return "ALLOW", ""
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return "DENY", out["permissionDecisionReason"]


def write_state(state_dir, ts, transcript=""):
    (state_dir / "shane-last-prompt.json").write_text(json.dumps(
        {"global": {"ts": ts, "session_id": SID, "transcript_path": transcript},
         "sessions": {SID: {"ts": ts, "transcript_path": transcript}}}))


def transcript_line(kind, ts, text):
    d = {"type": "user", "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z"),
         "message": {"role": "user", "content": text}}
    if kind == "human":
        d.update(origin={"kind": "human"}, promptSource="typed")
    elif kind == "cron":
        d.update(isMeta=True, promptSource="system")
    return json.dumps(d)


def test_recent_genuine_prompt_allows(tmp_path):
    prompt(tmp_path, "land the p3unl branch please")
    assert ask(tmp_path)[0] == "ALLOW"


def test_genuine_prompt_in_other_session_allows_via_global(tmp_path):
    prompt(tmp_path, "what's the status?", sid="other")
    assert ask(tmp_path, sid=SID)[0] == "ALLOW"


def test_stale_prompt_denies_with_time_and_instructions(tmp_path):
    old = time.time() - 30 * 60
    write_state(tmp_path, old)
    verdict, reason = ask(tmp_path)
    assert verdict == "DENY"
    assert f"last genuine prompt at {datetime.fromtimestamp(old).strftime('%H:%M')}" in reason
    assert "SHANE_QUEUE.md" in reason and "PushNotification" in reason and "gate-watch.py ask" in reason


def test_window_is_configurable(tmp_path):
    write_state(tmp_path, time.time() - 15 * 60)
    assert ask(tmp_path)[0] == "DENY"
    assert ask(tmp_path, ASK_GUARD_WINDOW_MIN="20")[0] == "ALLOW"
    (tmp_path / "ask-guard.json").write_text(json.dumps({"window_minutes": 30}))
    assert ask(tmp_path)[0] == "ALLOW"


def test_only_cron_prompts_recently_denies(tmp_path):
    write_state(tmp_path, time.time() - 3 * 3600)   # Shane last typed 3h ago
    for text in ["ANITA POLL (orchestrator). Goal: Anita Dhunna's go-live",
                 "<<autonomous-loop-dynamic>> continue",
                 "MORNING PREP (orchestrator, 06:17 on 14 Oct).",
                 "Overnight orchestrator poll (Shane asleep). Check fleet",
                 "Your claude.ai usage limit has reset. Continue the task you were working on",
                 "<task-notification>\n<task-id>abc</task-id>",
                 "Another Claude session sent a message:\n<cross-session-message from=x>"]:
        prompt(tmp_path, text)
    # structural provenance in the payload is honoured too, whatever the text says
    prompt(tmp_path, "free text cron prompt", origin={"kind": "task-notification"})
    prompt(tmp_path, "free text cron prompt", promptSource="system", isMeta=True)
    assert ask(tmp_path)[0] == "DENY"


def test_transcript_overrides_a_cron_prompt_the_regex_missed(tmp_path):
    now = time.time()
    tp = tmp_path / "t.jsonl"
    tp.write_text(transcript_line("human", now - 3 * 3600, "night night") + "\n"
                  + transcript_line("cron", now - 30, "Brand new cron text nobody listed") + "\n")
    prompt(tmp_path, "Brand new cron text nobody listed", transcript=str(tp))   # regex misses it
    assert ask(tmp_path)[0] == "DENY"
    with open(tp, "a") as f:
        f.write(transcript_line("human", now - 5, "ok go ahead") + "\n")
    assert ask(tmp_path)[0] == "ALLOW"


def test_missing_state_file_denies(tmp_path):
    verdict, reason = ask(tmp_path)
    assert verdict == "DENY" and "last genuine prompt at unknown" in reason


def test_corrupt_state_file_denies(tmp_path):
    (tmp_path / "shane-last-prompt.json").write_text("{not json")
    verdict, reason = ask(tmp_path)
    assert verdict == "DENY" and "SHANE_QUEUE.md" in reason


@pytest.mark.parametrize("extra", [{"SPAWNED_SESSION": "true"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}])
def test_headless_always_denies(tmp_path, extra):
    prompt(tmp_path, "land it")
    assert ask(tmp_path)[0] == "ALLOW"
    assert ask(tmp_path, **extra)[0] == "DENY"


def test_ignore_regex_prompt_does_not_refresh_presence(tmp_path):
    old = time.time() - 2 * 3600
    write_state(tmp_path, old)
    prompt(tmp_path, "seed")   # genuine: creates the ignore file; then restore the stale state
    write_state(tmp_path, old)
    cfg = json.loads((tmp_path / "presence-ignore.json").read_text())
    assert r"^ANITA POLL" in cfg["patterns"] and r"^<<autonomous-loop" in cfg["patterns"]
    cfg["patterns"].append(r"^CUSTOM WATCH")
    (tmp_path / "presence-ignore.json").write_text(json.dumps(cfg))
    prompt(tmp_path, "CUSTOM WATCH: check the build")
    prompt(tmp_path, "ANITA POLL (orchestrator).")
    st = json.loads((tmp_path / "shane-last-prompt.json").read_text())
    assert st["global"]["ts"] == old and st["sessions"][SID]["ts"] == old
    assert ask(tmp_path)[0] == "DENY"


def test_presence_hook_never_fails_on_garbage(tmp_path):
    for junk in ["", "not json", "[]", json.dumps({"prompt": None})]:
        r = subprocess.run([sys.executable, PRESENCE], input=junk, capture_output=True, text=True,
                           env=env(tmp_path), timeout=20)
        assert r.returncode == 0 and r.stdout == ""
