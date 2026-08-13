#!/usr/bin/env python3
"""Join live Claude sessions to worktrees and read transcript tails.

Stdout: by default, a JSON object mapping ``realpath(cwd)`` -> preferred session
record. ``--all`` emits every session record so branch-level joins do not lose
sessions that share a cwd. Consumed by ``discover.sh`` for both joins.

Locked contract (ENG_REVIEW.md, 2026-05-28):
- transcript located by sessionId glob under ~/.claude/projects (NO newest-jsonl
  fallback — that grabbed the wrong session's transcript).
- 64KB tail read, not the whole file.
- ACTIVE = pid_alive(kill -0) AND (status==busy OR transcript_mtime < 3min). A
  dead pid stuck in status=busy is NOT active.
- subagents/*.jsonl are scanned for blocker sentinels only (a subagent can be
  mid-question while the main stream looks idle).
"""

import glob
import json
import os
import sys
import time
from pathlib import Path

HOME = Path.home()
SESSIONS_DIR = HOME / ".claude" / "sessions"
PROJECTS_DIR = HOME / ".claude" / "projects"

TAIL_BYTES = 64 * 1024
SUBAGENT_TAIL_BYTES = 16 * 1024
ACTIVE_IDLE_SECS = 3 * 60
TAIL_BLOCK_CHARS = 600
MAX_ASSISTANT_BLOCKS = 3

# Phrases in the LAST assistant block that mean "still waiting on a human".
# The marker file is the only thing that grants auto-land; the chat tail can
# only VETO, so over-matching here is the safe direction (it just forces an
# explicit opt-in).
OPEN_LOOP_PHRASES = (
    "needs input:",
    "failed:",
    "shall i",
    "which would you",
    "let me know",
    "waiting for",
    "could you confirm",
    "could you clarify",
    "could you provide",
    "should i proceed",
    "do you want",
)
DONE_SENTINELS = ("failed:", "needs input:", "result:")


def pid_alive(pid):
    """True if the process exists. EPERM (exists, not ours) counts as alive."""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


def find_transcript(session_id):
    """Locate the transcript by sessionId glob. No newest-jsonl fallback."""
    if not session_id:
        return None
    pattern = str(PROJECTS_DIR / "**" / f"{session_id}.jsonl")
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None
    # If a sessionId somehow appears in more than one path, prefer the freshest.
    return max(matches, key=lambda p: _safe_mtime(p) or 0)


def _safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def read_tail(path, nbytes=TAIL_BYTES):
    """Read the last ``nbytes`` of a file, dropping a partial leading line."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if size > nbytes:
            handle.seek(size - nbytes)
            handle.readline()  # discard the partial line we landed in the middle of
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def extract_text(content):
    """Flatten a transcript message's content to its text, dropping tool_use."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
    return "\n".join(part for part in parts if part).strip()


def parse_tail(text):
    """Return (last <=3 assistant text blocks, last user text) from tail JSONL."""
    assistant_texts = []
    last_user = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = record.get("message") or {}
        role = message.get("role") or record.get("type")
        block_text = extract_text(message.get("content"))
        if not block_text:
            continue
        if role == "assistant":
            assistant_texts.append(block_text)
        elif role == "user":
            last_user = block_text
    return assistant_texts[-MAX_ASSISTANT_BLOCKS:], last_user


def detect_sentinel(assistant_tail):
    """First explicit completion/blocker sentinel found in the assistant tail."""
    joined = "\n".join(assistant_tail).lower()
    for sentinel in DONE_SENTINELS:
        if sentinel in joined:
            return sentinel.rstrip(":")
    return None


def detect_open_loop(assistant_tail):
    """True if the LAST assistant block reads as waiting on a human."""
    if not assistant_tail:
        return False
    last = assistant_tail[-1].strip()
    if last.endswith("?"):
        return True
    low = last.lower()
    return any(phrase in low for phrase in OPEN_LOOP_PHRASES)


def scan_subagents_for_blockers(transcript_path):
    """A subagent mid-question vetoes the parent even if its main stream is idle."""
    subagent_dir = Path(transcript_path).parent / "subagents"
    if not subagent_dir.is_dir():
        return False
    for sub in subagent_dir.glob("*.jsonl"):
        try:
            text = read_tail(str(sub), SUBAGENT_TAIL_BYTES)
        except OSError:
            continue
        assistant_tail, _ = parse_tail(text)
        if detect_open_loop(assistant_tail):
            return True
        if detect_sentinel(assistant_tail) in ("needs input", "failed"):
            return True
    return False


def is_active(pid, status, transcript_mtime, now=None):
    """ACTIVE = pid alive AND (busy OR transcript touched < 3 min ago)."""
    if not pid_alive(pid):
        return False
    if status == "busy":
        return True
    if transcript_mtime is None:
        return False
    now = time.time() if now is None else now
    return (now - transcript_mtime) < ACTIVE_IDLE_SECS


def build_session_record(meta, now=None):
    now = time.time() if now is None else now
    pid = meta.get("pid")
    session_id = meta.get("sessionId")
    status = meta.get("status")

    transcript = find_transcript(session_id)
    transcript_mtime = _safe_mtime(transcript) if transcript else None

    assistant_tail = []
    last_user = None
    sentinel = None
    open_loop = False
    subagent_blocked = False

    if transcript:
        try:
            text = read_tail(transcript)
            assistant_tail, last_user = parse_tail(text)
            assistant_tail = [block[:TAIL_BLOCK_CHARS] for block in assistant_tail]
            last_user = last_user[:TAIL_BLOCK_CHARS] if last_user else None
            sentinel = detect_sentinel(assistant_tail)
            open_loop = detect_open_loop(assistant_tail)
            subagent_blocked = scan_subagents_for_blockers(transcript)
        except OSError:
            pass

    idle_min = round((now - transcript_mtime) / 60, 1) if transcript_mtime else None

    process_alive = pid_alive(pid)
    return {
        "pid": pid,
        "session_id": session_id,
        "name": meta.get("name"),  # interactive sessions have no name (only bg jobs do)
        "kind": meta.get("kind"),
        "status": status,
        "updated_at": meta.get("updatedAt"),
        "transcript": transcript,
        "transcript_mtime": transcript_mtime,
        "idle_min": idle_min,
        "active": is_active(pid, status, transcript_mtime, now=now),
        "process_alive": process_alive,
        "last_assistant_tail": assistant_tail,
        "last_user": last_user,
        "sentinel": sentinel,
        "has_open_loop": bool(open_loop or subagent_blocked),
        "subagent_blocked": subagent_blocked,
    }


def _prefer(new, old):
    """When two sessions share a cwd, keep the active one, else the most recent."""
    if new["active"] != old["active"]:
        return new["active"]
    return (new.get("updated_at") or 0) > (old.get("updated_at") or 0)


def collect_all(sessions_dir=SESSIONS_DIR, now=None):
    now = time.time() if now is None else now
    out = []
    for session_file in sorted(Path(sessions_dir).glob("*.json")):
        try:
            meta = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cwd = meta.get("cwd")
        if not cwd:
            continue
        try:
            key = os.path.realpath(cwd)
        except OSError:
            key = cwd
        record = build_session_record(meta, now=now)
        record["cwd"] = cwd
        record["real_cwd"] = key
        out.append(record)
    return out


def collect(sessions_dir=SESSIONS_DIR, now=None):
    out = {}
    for record in collect_all(sessions_dir=sessions_dir, now=now):
        key = record["real_cwd"]
        existing = out.get(key)
        if existing is None or _prefer(record, existing):
            out[key] = record
    return out


def main():
    payload = collect_all() if "--all" in sys.argv[1:] else collect()
    json.dump(payload, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
