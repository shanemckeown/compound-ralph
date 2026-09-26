#!/usr/bin/env python3
"""sendmessage-guard.py — PreToolUse hook on SendMessage.

HARD RULE (Shane, 2026-09-14, after a Manager handoff was pushed into a tab he was
using for a cold-calling conversation): a session may message an INTERACTIVE session
only if that target is (a) the current ORCHESTRATOR (escalation up) or (b) a session
holding a MANAGER claim (a fresh tab Shane opened and ran /take in, or that he
confirmed). Every other interactive tab is a human's conversation. Manager handoffs go
to a fresh terminal Shane opens: ask him in one line, wait for his confirmation, then
send the instruction. Never SendMessage work into an existing tab.

Tabs already working together may always reply (Shane, 2026-09-26): if the target messaged
this session, or this session successfully sent it work, within 24h, the send is allowed.
The guard is for loading NEW work into a heavy tab, not for conversations already under way.

Background sessions (claude --bg workers), in-process subagents (agentIds), and
unresolvable names are allowed — this guard only protects human-facing tabs.
Fails CLOSED on any error resolving the target: if we cannot prove the target is a
worker or a claimed Manager, we deny. Deny is loud and says what to do instead.
"""
import json, os, subprocess, sys, glob, re

def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)

def allow():
    sys.exit(0)

# A crashing hook is a non-blocking error to Claude Code, i.e. the send goes through.
# Any unexpected exception must deny instead (fail-closed).
def _crash(etype, value, tb):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
          "permissionDecisionReason": f"sendmessage-guard: internal error ({etype.__name__}: {value}); denying (fail-closed)."}}))
    sys.stdout.flush()
    os._exit(0)
sys.excepthook = _crash

# The hook is registered with a 30s timeout, and a killed hook is also a non-blocking error.
# Deny on our own at 25s so the answer is always ours.
import signal
def _deadline(signum, frame):
    _crash(TimeoutError, "guard exceeded its 25s internal deadline", None)
signal.signal(signal.SIGALRM, _deadline)
try:  # tests may shorten it; nothing may lengthen it
    _limit = max(1, min(25, int(os.environ.get("SMGUARD_DEADLINE_S", "25"))))
except ValueError:
    _limit = 25
signal.alarm(_limit)

try:
    inp = json.load(sys.stdin)
except Exception as e:
    # Registered only on SendMessage, so unreadable input is a SendMessage we cannot vet.
    deny(f"sendmessage-guard: could not read the hook input ({e}); denying (fail-closed).")

if not isinstance(inp, dict):
    deny("sendmessage-guard: hook input is not a JSON object; denying (fail-closed).")

if inp.get("tool_name") != "SendMessage":
    allow()

to = (inp.get("tool_input") or {}).get("to", "") or ""
ref_m = re.search(r"\[([0-9a-f]+)\]\s*$", to)
ref = ref_m.group(1) if ref_m else ""
name = re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", to).strip()
if not name or name == "main":
    allow()
# in-process subagent ids look like a41bded05a06a442d (hex, no dash prefix pattern of names)
if re.fullmatch(r"a[0-9a-f]{16}", name):
    allow()

# Resolve the target among live sessions.
try:
    proc = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(f"exit {proc.returncode}")
    agents = json.loads(proc.stdout) if proc.stdout.strip() else []
    if not isinstance(agents, list) or not all(isinstance(a, dict) for a in agents):
        raise ValueError("unexpected inventory shape")
except Exception as e:
    deny(f"sendmessage-guard: could not resolve '{name}' (claude agents --json failed: {e}). "
         "Rule 2026-09-14: never message an interactive tab you cannot prove is the orchestrator or a claimed Manager. "
         "If this is a Manager handoff: ask Shane to open a fresh terminal, wait for his confirmation, then send.")

matches = [a for a in agents if a.get("name") == name or a.get("id") == name or a.get("sessionId", "").startswith(name)]
if ref:
    matches = [a for a in matches if str(a.get("id", "")).startswith(ref) or str(a.get("sessionId", "")).startswith(ref)]
if not matches:
    allow()  # not a live session on this machine (remote, cloud, or already gone) — not a human tab we can see
if len(matches) > 1 and any(a.get("kind") != "background" for a in matches):
    deny(f"sendmessage-guard: '{name}' matches {len(matches)} live sessions; resend with its [ref] from ListAgents.")
target = matches[0]
kind = target.get("kind", "")
sid = target.get("sessionId", "")

if kind == "background":
    allow()  # background worker — fine
if kind != "interactive":
    deny(f"sendmessage-guard: '{name}' has unknown session kind {kind!r}; denying (fail-closed).")

# /closeout requests (from /rollup) are allowed into any tab: closeout only writes a fragment
# about that tab, it loads no new work. Shane, 2026-09-22: the 09-14 rule is about pushing NEW
# WORK into a tab mid-conversation, not about blocking a rollup. Anything carrying a claim or
# handoff is still treated as work and falls through to the checks below.
msg = (inp.get("tool_input") or {}).get("message", "")
msg = msg if isinstance(msg, str) else json.dumps(msg)
if "/closeout" in msg and not re.search(r"--claim|HANDOFF|/take\b|fleet-role\.py", msg, re.I):
    allow()

# Interactive: allowed only if orchestrator or a claimed Manager.
try:
    who = subprocess.run(["python3", os.path.expanduser("~/.claude/scripts/fleet-role.py"), "--who", "--json"],
                         capture_output=True, text=True, timeout=10).stdout
    orch = (json.loads(who).get("record") or {}).get("session_id", "") if who.strip() else ""
except Exception:
    orch = ""
if sid and sid == orch:
    allow()

for f in glob.glob(os.path.expanduser("~/.claude/fleet/managers/*.json")):
    try:
        rec = json.load(open(f))
    except Exception:
        continue
    if rec.get("session_id") == sid:
        allow()

# Shane, 2026-09-26: the guard stops NEW, unrelated work landing in a heavy tab. It must never stop
# two tabs that already work together from replying (it blocked the orchestrator answering
# aestheticc-18's report on work the orchestrator gave it). So: allow if, within RELATION_HOURS,
# the target messaged THIS session, or this session successfully messaged the target.
# Evidence comes only from this session's own transcript, and only from entries the harness wrote:
#   - incoming: an isMeta user entry whose text starts (after at most one harness preamble line) with
#     <cross-session-message from="uds:.../<pid>.sock">,
#     and that pid must be the target's live pid (names can be reused, a live pid can't);
#   - outgoing: a SendMessage tool_use to the target whose tool_result reports success (a denied
#     send never counts as a relationship).
# Both must postdate the target's startedAt, so a different session that took the same name later
# doesn't inherit the relationship. Unreadable transcript = no relationship = fall through (fail-closed).
import time
from datetime import datetime
RELATION_HOURS = 24

def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None

def _texts(content):
    if isinstance(content, str):
        return [content]
    out = []
    for x in content if isinstance(content, list) else []:
        if isinstance(x, dict) and x.get("type") == "text" and isinstance(x.get("text"), str):
            out.append(x["text"])
    return out

def _receipt_ok(body):
    """True only when the SendMessage receipt itself says success: true (parsed, not grepped)."""
    if isinstance(body, list):
        body = "".join(x.get("text", "") for x in body if isinstance(x, dict) and x.get("type") == "text")
    try:
        r = json.loads(body) if isinstance(body, str) else body
    except Exception:
        return False
    return isinstance(r, dict) and r.get("success") is True


def related(transcript, target_name, target_pid, target_started, now, target_ids=(), name_unique=True):
    floor = max(now - RELATION_HOURS * 3600, target_started or 0)
    sent = {}          # tool_use_id -> timestamp of a SendMessage to the target
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
        if d.get("type") == "user" and d.get("isMeta") is True and target_pid:
            for text in _texts(content):
                # The harness writes one preamble line ("Another Claude session sent a message:")
                # before the tag; allow at most that one tag-free line, nothing more.
                m = re.match(r'\s*(?:[^<\n]*\n)?\s*<cross-session-message from="uds:[^"]*/(\d+)\.sock"', text)
                if m and int(m.group(1)) == int(target_pid):
                    return "it messaged this session"
        if d.get("type") == "assistant" and isinstance(content, list):
            for x in content:
                if isinstance(x, dict) and x.get("type") == "tool_use" and x.get("name") == "SendMessage":
                    to_ = str((x.get("input") or {}).get("to", ""))
                    r = re.search(r"\[([0-9a-f]+)\]\s*$", to_)
                    if re.sub(r"\s*\[[0-9a-f]+\]\s*$", "", to_).strip() != target_name:
                        continue
                    # A send addressed with a [ref] only counts for the session that ref names.
                    if r and not any(str(i).startswith(r.group(1)) for i in target_ids if i):
                        continue
                    # A bare-name send can't prove which of two same-named sessions got it.
                    if not r and not name_unique:
                        continue
                    sent[x.get("id")] = t
        if d.get("type") == "user" and isinstance(content, list):
            for x in content:
                if (isinstance(x, dict) and x.get("type") == "tool_result" and x.get("tool_use_id") in sent
                        and not x.get("is_error") and _receipt_ok(x.get("content"))):
                    return "this session already sent it work"
    return None

now = time.time()
transcript = inp.get("transcript_path") or ""
if not transcript and inp.get("session_id"):
    found = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{inp['session_id']}.jsonl"))
    transcript = found[0] if found else ""
if transcript and os.path.isfile(transcript):
    try:
        why = related(transcript, target.get("name", name), target.get("pid"),
                      (target.get("startedAt") or 0) / 1000.0, now,
                      (target.get("id"), target.get("sessionId")),
                      name_unique=sum(1 for a in agents if a.get("name") == target.get("name", name)) <= 1)
    except Exception:
        why = None
    if why:
        allow()

# Shane, 2026-09-22: the rule's PURPOSE is "don't load new work into a tab that has already done a
# lot of work / used a lot of context". A tab that has barely been used (Shane said hello, told it
# it's a Manager) is fair game. Measure used-ness by its transcript size, not by who spoke in it.
import time
now = time.time()
# Byte size is useless (a brand-new tab is ~440 KB of startup context), so count WORK:
# assistant tool calls made in the tab. A tab Shane only said hello to has ~0.
LIGHT_TOOL_CALLS = 25
if sid:
    tx = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
    try:
        calls = 0
        for path in tx:
            for line in open(path, errors="ignore"):
                if '"type":"tool_use"' in line or '"type": "tool_use"' in line:
                    calls += line.count('"tool_use"')
        # No transcript yet = a brand-new tab nobody has used = 0 calls = fair game (Shane, 2026-09-24).
        if calls < LIGHT_TOOL_CALLS:
            allow()
    except Exception:
        pass
# Otherwise (a heavily-used tab): the old fresh-tab + audited-confirmation path still applies.
started = (target.get("startedAt") or 0) / 1000.0
fresh = started and (now - started) < 20 * 60
ok_path = os.path.expanduser("~/.claude/fleet/handoff-ok.jsonl")
confirmed = False
if fresh and os.path.exists(ok_path):
    for line in open(ok_path):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("session_id") == sid and (now - float(rec.get("ts", 0))) < 30 * 60 and rec.get("shane_said"):
            confirmed = True
if fresh and confirmed:
    allow()

deny(f"sendmessage-guard: '{name}' is an unclaimed interactive tab that has already done real work "
     f"(>= {LIGHT_TOOL_CALLS} tool calls). Rule (Shane, 2026-09-22/24): don't load NEW work into a tab that has done "
     "substantial work — fresh or barely-used tabs, claimed Managers and the orchestrator are all fine to message. "
     "Ask Shane for a fresh tab (one line), or route through the orchestrator.")
