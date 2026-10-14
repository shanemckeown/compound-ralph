"""Tests for ask-guard.py + shane-presence.py (night-mode spec, 14 Oct).
Run: uvx pytest -q ~/.claude/hooks/tests/test_ask_guard.py"""
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
CRON = ["ANITA POLL (orchestrator). Goal: Anita Dhunna's go-live",
        "<<autonomous-loop-dynamic>> continue",
        "MORNING PREP (orchestrator, 06:17 on 14 Oct).",
        "Overnight orchestrator poll (Shane asleep). Check fleet",
        "Your claude.ai usage limit has reset. Continue the task you were working on",
        "<task-notification>\n<task-id>abc</task-id>",
        "Another Claude session sent a message:\n<cross-session-message from=x>"]


def env(state_dir, **extra):
    e = {k: v for k, v in os.environ.items()
         if k not in ("SPAWNED_SESSION", "CLAUDE_CODE_ENTRYPOINT", "ASK_GUARD_SILENCE_HOURS")}
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


def night(state_dir):
    return json.loads((state_dir / "night-mode.json").read_text())


def write_state(state_dir, ts, transcript=""):
    (state_dir / "shane-last-prompt.json").write_text(json.dumps(
        {"global": {"ts": ts, "session_id": SID, "transcript_path": transcript},
         "sessions": {SID: {"ts": ts, "transcript_path": transcript}}}))


def transcript_line(kind, ts, text):
    d = {"type": "user", "timestamp": datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z"),
         "message": {"role": "user", "content": text}}
    if kind == "human":
        d.update(origin={"kind": "human"}, promptSource="typed")
    else:
        d.update(isMeta=True, promptSource="system")
    return json.dumps(d)


def test_sleep_phrase_sets_night_mode_and_denies(tmp_path):
    prompt(tmp_path, "i'm going to bed. it's 22:51, please go into night mode")
    n = night(tmp_path)
    assert n["on"] is True and "going to bed" in n["excerpt"]
    verdict, reason = ask(tmp_path)
    assert verdict == "DENY"
    assert f"Shane is asleep (night mode since {datetime.fromtimestamp(n['since']).strftime('%H:%M')})" in reason
    assert "SHANE_QUEUE.md" in reason and "PushNotification" in reason and "gate-watch.py ask" in reason


@pytest.mark.parametrize("text", ["off to bed", "goodnight lucy", "Good night!", "night night",
                                  "heading to bed now", "I'm asleep, keep going", "signing off for the night"])
def test_seeded_sleep_phrases(tmp_path, text):
    prompt(tmp_path, text)
    assert night(tmp_path)["on"] is True


def test_next_genuine_prompt_clears_night_mode_and_allows(tmp_path):
    prompt(tmp_path, "going to sleep")
    assert ask(tmp_path)[0] == "DENY"
    prompt(tmp_path, "morning, what landed overnight?")
    assert night(tmp_path)["on"] is False
    assert ask(tmp_path)[0] == "ALLOW"


def test_turn_off_night_mode_clears_it(tmp_path):
    prompt(tmp_path, "night mode please")
    prompt(tmp_path, "ok turn off night mode")
    assert night(tmp_path)["on"] is False


def test_cron_prompts_neither_set_nor_clear(tmp_path):
    prompt(tmp_path, "going to bed")
    since = night(tmp_path)["since"]
    for text in CRON:
        prompt(tmp_path, text)
    prompt(tmp_path, "free text cron prompt", origin={"kind": "task-notification"})
    prompt(tmp_path, "free text cron prompt", promptSource="system", isMeta=True)
    assert night(tmp_path) ["on"] is True and night(tmp_path)["since"] == since
    assert ask(tmp_path)[0] == "DENY"
    # and in the day, a cron prompt whose text says "night mode" does not set it
    (tmp_path / "night-mode.json").unlink()
    prompt(tmp_path, "Overnight poll: if night mode, keep going", promptSource="system", isMeta=True)
    prompt(tmp_path, "ANITA POLL (orchestrator). Shane said good night")
    assert not (tmp_path / "night-mode.json").exists()


def test_unlisted_cron_prompt_cannot_clear_night_mode_via_transcript(tmp_path):
    now = time.time()
    tp = tmp_path / "t.jsonl"
    tp.write_text(transcript_line("human", now - 3600, "going to bed") + "\n")
    prompt(tmp_path, "going to bed", transcript=str(tp))
    with open(tp, "a") as f:
        f.write(transcript_line("cron", now, "Brand new cron text nobody listed") + "\n")
    prompt(tmp_path, "Brand new cron text nobody listed", transcript=str(tp))   # regex misses it
    assert night(tmp_path)["on"] is False                                       # file says cleared...
    assert ask(tmp_path)[0] == "DENY"                                           # ...transcript says no
    with open(tp, "a") as f:
        f.write(transcript_line("human", time.time(), "i'm up") + "\n")
    prompt(tmp_path, "i'm up", transcript=str(tp))
    assert ask(tmp_path)[0] == "ALLOW"


def test_daytime_recent_prompt_allows(tmp_path):
    prompt(tmp_path, "land the p3unl branch please")
    assert ask(tmp_path)[0] == "ALLOW"


def test_daytime_over_3h_silence_denies(tmp_path):
    old = time.time() - 4 * 3600
    write_state(tmp_path, old)
    verdict, reason = ask(tmp_path)
    assert verdict == "DENY"
    assert f"Shane has been silent since {datetime.fromtimestamp(old).strftime('%H:%M')}" in reason
    assert "SHANE_QUEUE.md" in reason


def test_silence_window_is_configurable(tmp_path):
    write_state(tmp_path, time.time() - 2 * 3600)
    assert ask(tmp_path)[0] == "ALLOW"
    assert ask(tmp_path, ASK_GUARD_SILENCE_HOURS="1")[0] == "DENY"
    (tmp_path / "ask-guard.json").write_text(json.dumps({"silence_hours": 1.5}))
    assert ask(tmp_path)[0] == "DENY"


def test_only_cron_prompts_do_not_refresh_presence(tmp_path):
    old = time.time() - 4 * 3600
    write_state(tmp_path, old)
    for text in CRON:
        prompt(tmp_path, text)
    st = json.loads((tmp_path / "shane-last-prompt.json").read_text())
    assert st["global"]["ts"] == old and st["sessions"][SID]["ts"] == old
    assert ask(tmp_path)[0] == "DENY"


def test_custom_ignore_regex(tmp_path):
    prompt(tmp_path, "seed")   # creates the config files
    cfg = json.loads((tmp_path / "presence-ignore.json").read_text())
    assert r"^ANITA POLL" in cfg["patterns"] and r"^<<autonomous-loop" in cfg["patterns"]
    cfg["patterns"].append(r"^CUSTOM WATCH")
    (tmp_path / "presence-ignore.json").write_text(json.dumps(cfg))
    prompt(tmp_path, "going to bed")
    prompt(tmp_path, "CUSTOM WATCH: check the build")
    assert night(tmp_path)["on"] is True
    phrases = json.loads((tmp_path / "night-mode-phrases.json").read_text())
    assert any("bed" in p for p in phrases["sleep"])


@pytest.mark.parametrize("extra", [{"SPAWNED_SESSION": "true"}, {"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}])
def test_headless_always_denies(tmp_path, extra):
    prompt(tmp_path, "land it")
    assert ask(tmp_path)[0] == "ALLOW"
    assert ask(tmp_path, **extra)[0] == "DENY"


def test_missing_state_file_allows(tmp_path):
    assert ask(tmp_path)[0] == "ALLOW"   # cold start: daytime, unknown last prompt


def test_corrupt_state_files_allow_in_the_day(tmp_path):
    (tmp_path / "shane-last-prompt.json").write_text("{not json")
    (tmp_path / "night-mode.json").write_text("[[[")
    assert ask(tmp_path)[0] == "ALLOW"


def test_presence_hook_never_fails_on_garbage(tmp_path):
    for junk in ["", "not json", "[]", json.dumps({"prompt": None})]:
        r = subprocess.run([sys.executable, PRESENCE], input=junk, capture_output=True, text=True,
                           env=env(tmp_path), timeout=20)
        assert r.returncode == 0 and r.stdout == ""
