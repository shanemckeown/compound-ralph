#!/usr/bin/env python3
"""sendmessage-guard.py — PreToolUse hook on SendMessage.

Shane, 2026-09-26 ~23:00: "I don't want this to be about blocking conversation."

CONVERSATION IS NEVER BLOCKED. Replies, follow-ups, questions, status and reports always pass,
to any tab. The guard fails OPEN for them (bad input, crashes, lookup errors: all allow).

The only check is on NEW WORK: a message whose first non-empty line (after a relay's `reply-to:`
line) starts with `NEW WORK:`. Senders (the orchestrator, a Manager) MUST put it first when they hand
a tab a new job, including Manager hand-offs; nothing else is guessed from content. For new work into a live
interactive tab:
  1. Context: the target's actual context use (the last assistant turn's input + cache tokens
     against its window) must be under CONTEXT_LIMIT (60%). Denied with how full it is.
  2. Shane's conversations (2026-09-14 rule, kept): an unclaimed tab Shane has been talking to
     (>= HUMAN_PROMPTS typed prompts, or one prompt plus >= WORKED_TOOL_CALLS tool calls of work)
     with no working relationship to this session needs Shane's
     go first: ask him, then record it in ~/.claude/fleet/handoff-ok.jsonl. The orchestrator,
     claimed Managers, and tabs this session already works with are exempt.
The new-work check fails CLOSED: any error while checking it denies.
Background workers and sessions not visible on this machine (cloud, remote, Codex) are not
checked: there is no local transcript to measure.
"""
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime

CONTEXT_LIMIT = 0.60
HUMAN_PROMPTS = 3        # this many prompts from Shane = his conversation...
WORKED_TOOL_CALLS = 25   # ...or any prompt from him plus this much real work done in the tab
RELATION_HOURS = 24
STATE = {"new_work": None}   # None = not yet known; decides fail-open vs fail-closed


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    sys.stdout.flush()
    os._exit(0)


def allow():
    os._exit(0)


def _fail(why):
    # Fails closed only once the message is known to be a NEW WORK hand-off. Before that
    # (including a timeout while reading input) it fails open: conversation is never blocked.
    if STATE["new_work"] is not True:
        allow()
    deny(f"sendmessage-guard: could not check this NEW WORK hand-off ({why}); denying (fail-closed). "
         "Plain replies are never blocked; only messages starting 'NEW WORK:' are checked.")


sys.excepthook = lambda t, v, tb: _fail(f"{t.__name__}: {v}")
signal.signal(signal.SIGALRM, lambda s, f: _fail("guard exceeded its internal deadline"))
try:  # tests may shorten it; nothing may lengthen it past the 30s hook timeout
    signal.alarm(max(1, min(25, int(os.environ.get("SMGUARD_DEADLINE_S", "25")))))
except ValueError:
    signal.alarm(25)

NEW_WORK_FIRST_LINE = re.compile(r"^\s*(?:\*\*)?NEW WORK\b", re.I)
# Only the explicit marker counts. Guessing from content (claim commands, /take) wrongly caught
# reports that quote those commands, and conversation must never be blocked (Astra r2, 26 Sep).


def is_new_work(message):
    lines = [l for l in message.splitlines() if l.strip()]
    if lines and re.match(r"\s*reply-to:", lines[0], re.I):
        lines = lines[1:]   # the relay's envelope line (relay_to_orchestrator.py) is not content
    return bool(lines and NEW_WORK_FIRST_LINE.match(lines[0]))


raw = sys.stdin.read()
try:
    inp = json.loads(raw)
    if not isinstance(inp, dict):
        raise ValueError("not an object")
except Exception as e:
    STATE["new_work"] = bool(re.search(r'"message"\s*:\s*"(?:reply-to:[^"\\]*\\n)?\s*(?:\*\*)?NEW WORK', raw, re.I))
    _fail(f"unreadable hook input: {e}")

if inp.get("tool_name") != "SendMessage":
    allow()
tool_input = inp.get("tool_input") or {}
msg = tool_input.get("message", "") if isinstance(tool_input, dict) else ""
msg = msg if isinstance(msg, str) else json.dumps(msg)
STATE["new_work"] = is_new_work(msg)
if not STATE["new_work"]:
    allow()  # conversation: never blocked

# ---- From here on: a NEW WORK hand-off. Every failure denies. ----
to = tool_input.get("to", "")
if not isinstance(to, str):
    _fail("recipient is not a string")
ref_m = re.search(r"\[([0-9a-f]+)\]\s*$", to)
ref = ref_m.group(1) if ref_m else ""
name = re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", to).strip()
if not name or name == "main" or re.fullmatch(r"a[0-9a-f]{16}", name):
    allow()  # parent session or an in-process subagent

proc = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=10)
if proc.returncode != 0:
    _fail(f"claude agents --json exited {proc.returncode}")
agents = json.loads(proc.stdout) if proc.stdout.strip() else []
if not isinstance(agents, list) or not all(isinstance(a, dict) for a in agents):
    _fail("unexpected session inventory shape")
uds = re.fullmatch(r"uds:.*/(\d+)\.sock", name)
if uds:
    matches = [a for a in agents if str(a.get("pid")) == uds.group(1)]
    if not matches:
        _fail(f"no live session holds {name} (a relay's socket? reply to its reply-to: name)")
else:
    matches = [a for a in agents if a.get("name") == name or a.get("id") == name
               or str(a.get("sessionId", "")).startswith(name)]
if ref:
    matches = [a for a in matches if str(a.get("id", "")).startswith(ref)
               or str(a.get("sessionId", "")).startswith(ref)]
if not matches:
    allow()  # not a live session on this machine (cloud, remote, Codex): nothing to measure
if len(matches) > 1:
    deny(f"sendmessage-guard: '{name}' matches {len(matches)} live sessions; resend the NEW WORK with its [ref].")
target = matches[0]
kind = target.get("kind")
if kind == "background":
    allow()
if kind != "interactive":
    _fail(f"unknown session kind {kind!r}")
sid = target.get("sessionId") or ""
tname = target.get("name") or name
if not sid:
    _fail("target has no sessionId")

now_s = time.time()
entries = []
for path in glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl")):
    for line in open(path, errors="ignore"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if isinstance(d, dict):
            entries.append(d)


# 1. Context use: the latest assistant turn's prompt size against the window.
def window_for(model, used):
    try:
        forced = int(os.environ.get("SMGUARD_CONTEXT_WINDOW", "0"))
    except ValueError:
        forced = 0
    if forced > 0:
        return forced
    w = 200_000 if "haiku" in str(model).lower() else 1_000_000
    return max(w, used)  # a prompt larger than the assumed window proves the window is larger


def _prompt_tokens(u):
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    total = sum(int(u.get(k) or 0) for k in keys)
    if total == 0 and isinstance(u.get("iterations"), list):   # real turns can carry usage only here
        its = [sum(int(i.get(k) or 0) for k in keys) for i in u["iterations"] if isinstance(i, dict)]
        total = max(its or [0])
    return total


# The model comes from the latest real assistant turn; the size from whichever is newest: that
# turn's prompt, or a compaction boundary after it (postTokens is the context size right after).
used, model, measured = 0, "", False
for d in reversed(entries):
    m = d.get("message") if d.get("type") == "assistant" else None
    if isinstance(m, dict) and m.get("model") and m.get("model") != "<synthetic>":
        model = model or m["model"]
    if measured and model:
        break
    if measured:
        continue
    if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
        post = (d.get("compactMetadata") or {}).get("postTokens")
        if isinstance(post, int) and post > 0:
            used, measured = post, True
            continue
    u = m.get("usage") if isinstance(m, dict) else None
    if isinstance(u, dict) and _prompt_tokens(u) > 0:   # all-zero usage = synthetic record
        used, measured = _prompt_tokens(u), True
if not measured:
    young = now_s - (target.get("startedAt") or 0) / 1000.0 < 30 * 60
    had_turns = any(d.get("type") == "assistant" for d in entries)
    if had_turns or not young:   # only a brand-new tab with no turns yet is "empty" without evidence
        _fail(f"cannot measure {tname}'s context (no usable usage record)")
window = window_for(model, used)
pct = used / window if window else 0
if pct > CONTEXT_LIMIT:
    deny(f"sendmessage-guard: '{tname}' is {pct:.0%} full ({used:,} of {window:,} tokens); new work "
         f"needs under {CONTEXT_LIMIT:.0%}. Ask Shane for a fresh tab, or send it without 'NEW WORK:' "
         "if it continues that tab's current job.")

# 2. Shane's own conversations need his go. Exempt: orchestrator, claimed Managers, collaborators.
who = subprocess.run(["python3", os.path.expanduser("~/.claude/scripts/fleet-role.py"), "--who", "--json"],
                     capture_output=True, text=True, timeout=10).stdout
try:
    orch = ((json.loads(who) or {}).get("record") or {}).get("session_id", "") if who.strip() else ""
except ValueError:
    orch = ""
if sid == orch:
    allow()
for f in glob.glob(os.path.expanduser("~/.claude/fleet/managers/*.json")):
    try:
        if json.load(open(f)).get("session_id") == sid:
            allow()
    except (OSError, ValueError, AttributeError):
        continue


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _texts(content):
    if isinstance(content, str):
        return [content]
    return [x["text"] for x in (content if isinstance(content, list) else [])
            if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str)]


def _receipt_ok(body):
    if isinstance(body, list):
        body = "".join(x.get("text", "") for x in body if isinstance(x, dict) and x.get("type") == "text")
    try:
        r = json.loads(body) if isinstance(body, str) else body
    except Exception:
        return False
    return isinstance(r, dict) and r.get("success") is True


def related(transcript, now):
    """Did the target message this session, or did this session successfully send it work, within
    RELATION_HOURS and after the target started? Incoming counts when bound to the target by its
    live pid (a direct SendMessage) or by a relay's `reply-to: <name> (<sessionId>)` stamp."""
    floor = max(now - RELATION_HOURS * 3600, (target.get("startedAt") or 0) / 1000.0)
    name_unique = sum(1 for a in agents if a.get("name") == tname) <= 1
    pid = target.get("pid")
    sent = {}
    for line in open(transcript, errors="ignore"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        t = _ts(d.get("timestamp"))
        if t is None or t < floor:
            continue
        content = (d.get("message") or {}).get("content")
        incoming = []
        if d.get("type") == "user" and d.get("isMeta") is True:
            incoming = _texts(content)
        att = d.get("attachment")
        if d.get("type") == "attachment" and isinstance(att, dict) and att.get("type") == "queued_command" \
                and isinstance(att.get("prompt"), str):
            incoming = [att["prompt"]]   # delivered while the receiver was busy
        if incoming:
            for text in incoming:
                m = re.match(r'\s*(?:[^<\n]*\n)?\s*<cross-session-message from="uds:[^"]*/(\d+)\.sock"[^>]*>'
                             r'\s*(?:reply-to:[^\n(]*\(([0-9a-f-]{36})\))?', text)
                if m and ((pid and int(m.group(1)) == int(pid)) or m.group(2) == sid):
                    return True
        if d.get("type") == "assistant" and isinstance(content, list):
            for x in content:
                if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name") == "SendMessage":
                    to_ = str((x.get("input") or {}).get("to", ""))
                    r = re.search(r"\[([0-9a-f]+)\]\s*$", to_)
                    via_uds = re.fullmatch(r"uds:.*/(\d+)\.sock", to_.strip())
                    if via_uds:
                        if not (pid and via_uds.group(1) == str(pid)):
                            continue
                    elif re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", to_).strip() != tname:
                        continue
                    if r and not any(str(i).startswith(r.group(1)) for i in (target.get("id"), sid) if i):
                        continue
                    if not r and not via_uds and not name_unique:
                        continue
                    # Only a hand-off we made counts ("Thanks" to Shane's tab doesn't make it ours).
                    if not is_new_work(str((x.get("input") or {}).get("message", ""))):
                        continue
                    sent[x.get("id")] = t
        if d.get("type") == "user" and isinstance(content, list):
            for x in content:
                if (isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in sent
                        and not x.get("is_error") and _receipt_ok(x.get("content"))):
                    return True
    return False


now = time.time()
mine = inp.get("transcript_path") or ""
if not mine and inp.get("session_id"):
    g = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{inp['session_id']}.jsonl"))
    mine = g[0] if g else ""
if mine and os.path.isfile(mine) and related(mine, now):
    allow()

SYSTEM_PREFIXES = ("<command-", "<local-command", "<cross-session-message", "another claude session",
                   "<system-reminder", "caveat:", "[request interrupted", "<task-notification",
                   "<bash-", "<user-memory-input")
human = 0
tool_calls = sum(1 for d in entries if d.get("type") == "assistant"
                 for x in ((d.get("message") or {}).get("content") or [])
                 if isinstance(x, dict) and x.get("type") == "tool_use")
for d in entries:
    if d.get("type") != "user" or d.get("isMeta") or d.get("isCompactSummary"):
        continue
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
        continue
    origin = (d.get("origin") or {}).get("kind") if isinstance(d.get("origin"), dict) else None
    if origin is not None or d.get("promptSource") is not None:   # provenance recorded: trust it
        if origin == "human" or (origin is None and d.get("promptSource") not in ("sdk", "system", "queued")):
            human += 1
        continue
    if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "image" for x in c):
        human += 1   # legacy entry: a pasted screenshot is Shane
        continue
    text = " ".join(_texts(c)).strip().lower()
    if "<command-name>" in text and "<local-command-stdout" not in text:
        human += 1   # a slash command Shane typed
    elif text and not text.startswith(SYSTEM_PREFIXES):
        human += 1
if human == 0 or (human < HUMAN_PROMPTS and tool_calls < WORKED_TOOL_CALLS):
    allow()  # a fresh or barely-used tab Shane opened for work

ok_path = os.path.expanduser("~/.claude/fleet/handoff-ok.jsonl")
if os.path.exists(ok_path):
    for line in open(ok_path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("session_id") == sid and rec.get("shane_said") \
                and now - float(rec.get("ts", 0)) < 30 * 60:
            allow()

deny(f"sendmessage-guard: '{tname}' is Shane's own conversation ({human} prompts from him, {tool_calls} tool calls, no claim, no "
     "work shared with this session). New work there needs his go: ask him in one line, then record "
     "it in ~/.claude/fleet/handoff-ok.jsonl. Replies without 'NEW WORK:' are never blocked.")
