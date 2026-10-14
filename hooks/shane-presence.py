#!/usr/bin/env python3
"""shane-presence.py — UserPromptSubmit hook. Records when Shane last GENUINELY typed a prompt.

Shane, 2026-10-14 08:28: "fuck me i explicitly told you not to waste 5 hours again and you did!!!
how do we enforce no questions in code somehow???" (an AskUserQuestion at 02:45 blocked the
orchestrator until 08:25.) ask-guard.py reads what this writes and denies AskUserQuestion when
Shane has not typed anything for a while.

State: ~/.claude/state/shane-last-prompt.json
  {"global": {"ts", "session_id", "transcript_path"}, "sessions": {<sid>: {"ts", "transcript_path"}}}

NOT genuine (never refreshes presence):
  1. Structural provenance, when the hook payload carries it: origin.kind other than "human"
     (cron/loop prompts have none and are isMeta; task-notification; peer; auto-continuation),
     promptSource/prompt_source of system/sdk, isMeta true. These are the transcript's fields;
     ask-guard.py re-checks the transcript itself, so a payload without them is still covered.
  2. A prompt matching any regex in ~/.claude/state/presence-ignore.json {"patterns": [...]}
     (seeded on first run with cron/loop/limit-reset/notification/cross-session texts).

Never blocks, never errors: any failure exits 0 silently.
"""
import fcntl
import json
import os
import re
import sys
import tempfile
import time

STATE_DIR = os.environ.get("PRESENCE_STATE_DIR") or os.path.expanduser("~/.claude/state")
STATE = os.path.join(STATE_DIR, "shane-last-prompt.json")
IGNORE = os.path.join(STATE_DIR, "presence-ignore.json")
DEBUG = os.path.join(STATE_DIR, "presence-debug.log")
PRUNE_DAYS = 7

SEED_PATTERNS = [
    # scheduled / loop prompts (texts found in ~/.claude transcripts' CronCreate calls)
    r"^ANITA POLL",
    r"^<<autonomous-loop",
    r"^MORNING PREP",
    r"(?i)^[^\n]{0,80}\b(poll|self-check)\b",
    r"(?i)^Overnight\b",
    r"(?i)^Disk watch",
    r"(?i)^Credit hold has ended",
    r"(?i)^Finish \S+ land\b",
    r"(?i)^[^\n]{0,60}\((orchestrator|ORCHESTRATOR)[,)]",
    # limit reset auto-continue
    r"^Your claude\.ai usage limit has reset",
    # notifications and cross-session traffic
    r"^\s*<task-notification",
    r"^\s*<cross-session-message",
    r"^Another Claude session sent a message",
    r"^\s*<agent-message",
    r"^\s*<system-reminder",
    r"^This session is being continued from a previous conversation",
    r"^\[Request interrupted",
    r"^\s*<local-command",
    r"^\s*<bash-(input|stdout|stderr)",
    r"^\s*<user-memory-input",
    r"^\s*<artifact-content",
    r"(?i)^Caveat:",
]
NOT_HUMAN_SOURCES = {"system", "sdk"}


def load_patterns():
    try:
        if not os.path.exists(IGNORE):
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(IGNORE, "w") as f:
                json.dump({"_doc": "Prompts matching any regex never count as Shane being present "
                                   "(shane-presence.py / ask-guard.py).",
                           "patterns": SEED_PATTERNS}, f, indent=2)
        with open(IGNORE) as f:
            pats = json.load(f).get("patterns") or []
    except Exception:
        pats = SEED_PATTERNS
    out = []
    for p in pats:
        try:
            out.append(re.compile(p))
        except re.error:
            pass
    return out


def structurally_not_human(inp):
    o = inp.get("origin")
    kind = o.get("kind") if isinstance(o, dict) else (o if isinstance(o, str) else None)
    if kind is not None and kind != "human":
        return True
    for k in ("promptSource", "prompt_source", "source"):
        v = inp.get(k)
        if isinstance(v, str) and v.lower() in NOT_HUMAN_SOURCES:
            return True
    if inp.get("isMeta") is True or inp.get("is_meta") is True:
        return True
    return False


def is_genuine(inp):
    if structurally_not_human(inp):
        return False
    prompt = inp.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    return not any(p.search(prompt) for p in load_patterns())


def debug(inp, genuine):
    # Payload keys only (never content): lets us tighten the structural check if Claude Code adds
    # provenance fields to the hook payload. Bounded to ~200 KB.
    try:
        if os.path.exists(DEBUG) and os.path.getsize(DEBUG) > 200_000:
            os.replace(DEBUG, DEBUG + ".1")
        extra = {k: inp.get(k) for k in ("origin", "promptSource", "prompt_source", "source", "isMeta")
                 if k in inp}
        with open(DEBUG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "sid": str(inp.get("session_id"))[:12],
                                "keys": sorted(inp.keys()), "prov": extra, "genuine": genuine,
                                "head": str(inp.get("prompt", ""))[:24]}) + "\n")
    except Exception:
        pass


def record(inp, now):
    os.makedirs(STATE_DIR, exist_ok=True)
    sid = str(inp.get("session_id") or "unknown")
    tp = inp.get("transcript_path") or ""
    with open(STATE + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            with open(STATE) as f:
                st = json.load(f)
            if not isinstance(st, dict):
                st = {}
        except Exception:
            st = {}
        sessions = st.get("sessions") if isinstance(st.get("sessions"), dict) else {}
        sessions = {k: v for k, v in sessions.items()
                    if isinstance(v, dict) and now - float(v.get("ts", 0) or 0) < PRUNE_DAYS * 86400}
        sessions[sid] = {"ts": now, "transcript_path": tp}
        st = {"global": {"ts": now, "session_id": sid, "transcript_path": tp}, "sessions": sessions}
        fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".shane-last-prompt.")
        with os.fdopen(fd, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, STATE)


def main():
    try:
        inp = json.load(sys.stdin)
        if not isinstance(inp, dict):
            return
        genuine = is_genuine(inp)
        debug(inp, genuine)
        if genuine:
            record(inp, time.time())
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
