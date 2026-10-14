#!/usr/bin/env python3
"""shane-presence.py — UserPromptSubmit hook. Records Shane's last GENUINE prompt and sets/clears NIGHT MODE.

Shane, 2026-10-14 08:28: "how do we enforce no questions in code somehow???" (an AskUserQuestion at
02:45 blocked the orchestrator until 08:25.) Then 08:45: "it would be better if it was aligned to when
i say i'm going to sleep. during the day maybe less strict?" ask-guard.py reads what this writes.

~/.claude/state/shane-last-prompt.json
  {"global": {"ts", "session_id", "transcript_path"}, "sessions": {<sid>: {"ts", "transcript_path"}}}
~/.claude/state/night-mode.json (global, all sessions)
  - a genuine prompt matching a "sleep" regex in ~/.claude/state/night-mode-phrases.json (e.g. "i'm going
    to bed. it's 22:51, please go into night mode") sets {on: true, since, excerpt};
  - the next genuine prompt that does not match clears it {on: false, cleared_at, transcript_path}
    ("wake" regexes, e.g. "turn off night mode", are checked first and always clear).

NOT genuine (never refreshes presence, never sets or clears night mode):
  1. Structural provenance in the payload, when present: origin.kind other than "human", promptSource /
     prompt_source of system/sdk, isMeta true. ask-guard.py also re-checks the transcript's
     origin.kind == "human" entries, so a payload without these fields is still covered.
  2. A prompt matching a regex in ~/.claude/state/presence-ignore.json {"patterns": [...]}
     (seeded with cron/loop, limit-reset, notification and cross-session texts).

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
NIGHT = os.path.join(STATE_DIR, "night-mode.json")
PHRASES = os.path.join(STATE_DIR, "night-mode-phrases.json")
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
SEED_SLEEP = [
    r"(?i)\b(going|off|heading|head|gone) to (bed|sleep)\b",
    r"(?i)\bnight[- ]?mode\b",
    r"(?i)\bgood ?night\b",
    r"(?i)\bnight,? night\b",
    r"(?i)\b(i'?m|i am|im) (now )?asleep\b",
    r"(?i)\bsigning off for the night\b",
]
SEED_WAKE = [   # checked first: these clear night mode even though they mention it
    r"(?i)\b(turn|switch)(ed|ing)? off night[- ]?mode\b",
    r"(?i)\b(turn|switch)(ed|ing)? night[- ]?mode off\b",
    r"(?i)\b(exit|end|leave|stop|disable|out of|no more)( the)? night[- ]?mode\b",
    r"(?i)\bnight[- ]?mode (is )?(off|over)\b",
]
NOT_HUMAN_SOURCES = {"system", "sdk"}


def _compile(pats):
    out = []
    for p in pats:
        try:
            out.append(re.compile(p))
        except (re.error, TypeError):
            pass
    return out


def _load_config(path, seed):
    """Read a JSON config, seeding it on first run; fall back to the seed on any error."""
    try:
        if not os.path.exists(path):
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(path, "w") as f:
                json.dump(seed, f, indent=2)
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else seed
    except Exception:
        return seed


def load_patterns():
    d = _load_config(IGNORE, {"_doc": "Prompts matching any regex never count as Shane being present and "
                                      "never set or clear night mode (shane-presence.py / ask-guard.py).",
                              "patterns": SEED_PATTERNS})
    return _compile(d.get("patterns") or SEED_PATTERNS)


def load_phrases():
    d = _load_config(PHRASES, {"_doc": "A genuine Shane prompt matching 'sleep' sets night mode (ask-guard.py "
                                       "then denies AskUserQuestion). 'wake' is checked first and clears it; "
                                       "any other genuine prompt also clears it.",
                               "sleep": SEED_SLEEP, "wake": SEED_WAKE})
    return _compile(d.get("sleep") or SEED_SLEEP), _compile(d.get("wake") or SEED_WAKE)


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


def is_sleep(prompt):
    sleep, wake = load_phrases()
    if any(p.search(prompt) for p in wake):
        return False
    return any(p.search(prompt) for p in sleep)


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


def _atomic_write(path, obj):
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".tmp-" + os.path.basename(path) + ".")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def _read(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def record(inp, now):
    sid = str(inp.get("session_id") or "unknown")
    tp = inp.get("transcript_path") or ""
    st = _read(STATE)
    sessions = st.get("sessions") if isinstance(st.get("sessions"), dict) else {}
    sessions = {k: v for k, v in sessions.items()
                if isinstance(v, dict) and now - float(v.get("ts", 0) or 0) < PRUNE_DAYS * 86400}
    sessions[sid] = {"ts": now, "transcript_path": tp}
    _atomic_write(STATE, {"global": {"ts": now, "session_id": sid, "transcript_path": tp},
                          "sessions": sessions})


def update_night_mode(inp, now):
    prompt = inp.get("prompt") or ""
    sid = str(inp.get("session_id") or "")
    local = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    if is_sleep(prompt):
        _atomic_write(NIGHT, {"on": True, "since": now, "since_local": local,
                              "excerpt": prompt.strip()[:120], "session_id": sid,
                              "transcript_path": inp.get("transcript_path") or ""})
        return
    cur = _read(NIGHT)
    if cur.get("on") is True or not cur:
        _atomic_write(NIGHT, {"on": False, "cleared_at": now, "cleared_local": local, "session_id": sid,
                              "transcript_path": inp.get("transcript_path") or "",
                              "previous_since": cur.get("since")})


def main():
    try:
        inp = json.load(sys.stdin)
        if not isinstance(inp, dict):
            return
        genuine = is_genuine(inp)
        debug(inp, genuine)
        if not genuine:
            return
        os.makedirs(STATE_DIR, exist_ok=True)
        now = time.time()
        with open(os.path.join(STATE_DIR, ".presence.lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            record(inp, now)
            update_night_mode(inp, now)
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
