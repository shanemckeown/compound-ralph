#!/usr/bin/env python3
"""ask-guard.py — PreToolUse hook on AskUserQuestion. No blocking questions while Shane is asleep or away.

Shane, 2026-10-14 08:28: "how do we enforce no questions in code somehow???" At 02:45 the orchestrator
asked a question while he slept; the session blocked until 08:25 and nothing landed. 08:45: "it would be
better if it was aligned to when i say i'm going to sleep. during the day maybe less strict?"

DENY when:
  - headless (SPAWNED_SESSION=true, or an sdk entrypoint): always;
  - night mode is on (~/.claude/state/night-mode.json, set by shane-presence.py when Shane says he is
    going to sleep, cleared by his next genuine prompt). A clear that the clearing session's transcript
    cannot back with an origin.kind == "human" entry is ignored, so an unlisted cron prompt cannot end it;
  - safety net: no genuine prompt for more than 3h (env ASK_GUARD_SILENCE_HOURS, or "silence_hours" in
    ~/.claude/state/ask-guard.json): he walked away without saying goodnight. The last-prompt time is
    shane-last-prompt.json, capped by that session's transcript's latest origin.kind == "human" entry.
ALLOW otherwise (daytime). Never blocks: a missing state file is daytime with an unknown last prompt
(allow), and any unexpected error allows rather than crash the session.
"""
import json
import os
import sys
import time
from datetime import datetime

STATE_DIR = os.environ.get("PRESENCE_STATE_DIR") or os.path.expanduser("~/.claude/state")
STATE = os.path.join(STATE_DIR, "shane-last-prompt.json")
NIGHT = os.path.join(STATE_DIR, "night-mode.json")
CONFIG = os.path.join(STATE_DIR, "ask-guard.json")
QUEUE = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/SHANE_QUEUE.md"
TAIL_BYTES = 8 * 1024 * 1024
HEADLESS_ENTRYPOINTS = {"sdk-cli", "sdk-ts", "sdk-py", "sdk"}
INSTRUCTIONS = (
    "Do NOT block on a question. "
    f"(1) Append the question with its options and your recommendation to {QUEUE} "
    "(create it if missing; date-stamp each entry). (2) Send ONE PushNotification summarising it. "
    "(3) Run `gate-watch.py ask <id>` if it is a gate. (4) Continue every piece of work that does "
    "not depend on the answer, this turn. Hard gates (traffic shift, deploy, client send, "
    "patient-data write) stay held, but only that item."
)


def hhmm(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown"


def deny(lead):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": f"{lead}. {INSTRUCTIONS}"}}))
    sys.stdout.flush()
    os._exit(0)


def allow():
    os._exit(0)


def _read(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def silence_seconds():
    v = os.environ.get("ASK_GUARD_SILENCE_HOURS")
    if v is None:
        v = _read(CONFIG).get("silence_hours")
    try:
        return float(v if v is not None else 3) * 3600
    except (TypeError, ValueError):
        return 3 * 3600


def headless(inp):
    if os.environ.get("SPAWNED_SESSION", "").lower() == "true":
        return True
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").lower() in HEADLESS_ENTRYPOINTS:
        return True
    return str(inp.get("entrypoint", "")).lower() in HEADLESS_ENTRYPOINTS


def transcript_human_ts(path):
    """Latest origin.kind=='human' user entry in the transcript tail.
    ('none', None) if unreadable or the transcript has no provenance fields (unverifiable);
    else ('ok', ts-or-None)."""
    try:
        if not path or not os.path.isfile(path):
            return "none", None
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(size - TAIL_BYTES)
                f.readline()
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return "none", None
    saw_provenance, best = False, None
    for line in data.splitlines():
        if '"user"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "user":
            continue
        o = d.get("origin")
        if isinstance(o, dict) or "promptSource" in d:
            saw_provenance = True
        if isinstance(o, dict) and o.get("kind") == "human" and d.get("timestamp"):
            try:
                t = datetime.fromisoformat(str(d["timestamp"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            best = t if best is None or t > best else best
    return ("ok", best) if saw_provenance else ("none", None)


def effective(rec):
    """A presence record's time, capped by what its transcript proves. None = no record."""
    if not isinstance(rec, dict):
        return None
    try:
        ts = float(rec.get("ts"))
    except (TypeError, ValueError):
        return None
    status, human = transcript_human_ts(rec.get("transcript_path") or "")
    if status == "ok":
        # no human entry in the tail: the record was not Shane (or is long past): treat as old
        return 0.0 if human is None else min(ts, human + 60)   # 60s: hook fires before the write
    return ts


def night_since(night):
    """Night-mode start time if night mode is in force, else None."""
    if night.get("on") is True:
        return night.get("since", 0)
    prev, cleared = night.get("previous_since"), night.get("cleared_at")
    if prev is None or cleared is None:
        return None
    status, human = transcript_human_ts(night.get("transcript_path") or "")
    if status == "ok" and (human is None or human < float(cleared) - 120):
        return prev   # the "clear" was not a human prompt: still night
    return None


def main():
    raw = sys.stdin.read()
    try:
        inp = json.loads(raw) if raw.strip() else {}
    except ValueError:
        inp = {}
    if not isinstance(inp, dict):
        inp = {}
    if headless(inp):
        deny("Headless session: never ask (Shane is not watching)")
    since = night_since(_read(NIGHT))
    if since is not None:
        deny(f"Shane is asleep (night mode since {hhmm(since)})")
    st = _read(STATE)
    sid = str(inp.get("session_id") or "")
    sessions = st.get("sessions") if isinstance(st.get("sessions"), dict) else {}
    times = [t for t in (effective(sessions.get(sid)) if sid else None, effective(st.get("global")))
             if t is not None]
    if times:
        last = max(times)
        if time.time() - last > silence_seconds():
            shown = hhmm(last) if last > 0 else "unknown"
            deny(f"Shane has been silent since {shown} (no genuine prompt for over "
                 f"{silence_seconds() / 3600:g}h; he may have walked away without saying goodnight)")
    allow()   # daytime: recent prompt, or no record yet (cold start)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        allow()   # never block or crash the session
