#!/usr/bin/env python3
"""ask-guard.py — PreToolUse hook on AskUserQuestion. No blocking questions while Shane is away.

Shane, 2026-10-14 08:28: "fuck me i explicitly told you not to waste 5 hours again and you did!!!
how do we enforce no questions in code somehow???" At 02:45 the orchestrator asked a question while
he slept; the session blocked until 08:25 and nothing landed. Memory rules had already failed.

ALLOW only when Shane's last genuine prompt (this session, or any session) is under the window
(default 10 min; env ASK_GUARD_WINDOW_MIN or "window_minutes" in ~/.claude/state/ask-guard.json).
Presence comes from shane-presence.py's state file, then is re-checked against that session's
transcript: a record counts only up to the transcript's latest origin.kind == "human" entry, so a
cron/loop prompt the regex list missed still cannot keep the window open.

DENY otherwise, and always when headless (SPAWNED_SESSION=true, or an sdk entrypoint).
Fails toward NOT BLOCKING: any error denies with the same instructions. Never blocks the session.
"""
import json
import os
import sys
import time
from datetime import datetime

STATE_DIR = os.environ.get("PRESENCE_STATE_DIR") or os.path.expanduser("~/.claude/state")
STATE = os.path.join(STATE_DIR, "shane-last-prompt.json")
CONFIG = os.path.join(STATE_DIR, "ask-guard.json")
QUEUE = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/SHANE_QUEUE.md"
TAIL_BYTES = 8 * 1024 * 1024
HEADLESS_ENTRYPOINTS = {"sdk-cli", "sdk-ts", "sdk-py", "sdk"}


def deny(last_ts=None, why=""):
    when = datetime.fromtimestamp(last_ts).strftime("%H:%M") if last_ts else "unknown"
    reason = (
        f"Shane is away (last genuine prompt at {when}). Do NOT block on a question. "
        f"(1) Append the question with its options and your recommendation to {QUEUE} "
        "(create it if missing; date-stamp each entry). (2) Send ONE PushNotification summarising it. "
        "(3) Run `gate-watch.py ask <id>` if it is a gate. (4) Continue every piece of work that does "
        "not depend on the answer, this turn. Hard gates (traffic shift, deploy, client send, "
        "patient-data write) stay held, but only that item."
    )
    if why:
        reason += f" [ask-guard: {why}]"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.stdout.flush()
    os._exit(0)


def allow():
    os._exit(0)


def window_seconds():
    v = os.environ.get("ASK_GUARD_WINDOW_MIN")
    if v is None and os.path.exists(CONFIG):
        with open(CONFIG) as f:
            v = json.load(f).get("window_minutes")
    return float(v if v is not None else 10) * 60


def headless(inp):
    if os.environ.get("SPAWNED_SESSION", "").lower() == "true":
        return True
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").lower() in HEADLESS_ENTRYPOINTS:
        return True
    if str(inp.get("entrypoint", "")).lower() in HEADLESS_ENTRYPOINTS:
        return True
    return False


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def transcript_human_ts(path):
    """Latest origin.kind=='human' user entry in the transcript tail.
    Returns ('none', None) if the file can't be read or has no provenance fields (unverifiable),
    else ('ok', ts-or-None)."""
    if not path or not os.path.isfile(path):
        return "none", None
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > TAIL_BYTES:
            f.seek(size - TAIL_BYTES)
            f.readline()
        data = f.read().decode("utf-8", "replace")
    saw_provenance, best = False, None
    for line in data.splitlines():
        if '"type":"user"' not in line and '"type": "user"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "user":
            continue
        o = d.get("origin")
        if isinstance(o, dict) or "promptSource" in d:
            saw_provenance = True
        if isinstance(o, dict) and o.get("kind") == "human" and d.get("timestamp"):
            t = _ts(d["timestamp"])
            best = t if best is None or t > best else best
    return ("ok", best) if saw_provenance else ("none", None)


def effective(rec):
    """A presence record's time, capped by what its transcript proves (if it can)."""
    if not isinstance(rec, dict):
        return None
    ts = float(rec.get("ts"))
    status, human = transcript_human_ts(rec.get("transcript_path") or "")
    if status == "ok":
        if human is None:
            return None
        return min(ts, human + 60)   # a minute's slack for the hook firing before the write
    return ts


def main():
    raw = sys.stdin.read()
    inp = json.loads(raw) if raw.strip() else {}
    if not isinstance(inp, dict):
        inp = {}
    if headless(inp):
        deny(None, "headless session: never ask")
    with open(STATE) as f:
        st = json.load(f)
    if not isinstance(st, dict):
        raise ValueError("state not an object")
    sid = str(inp.get("session_id") or "")
    candidates = []
    sess = (st.get("sessions") or {}).get(sid) if sid else None
    for rec in (sess, st.get("global")):
        e = effective(rec)
        if e is not None:
            candidates.append(e)
    last = max(candidates) if candidates else None
    if last is not None and 0 <= time.time() - last < window_seconds():
        allow()
    deny(last)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:   # fail toward not blocking: deny, never hang
        deny(None, f"error {type(e).__name__}: {e}")
