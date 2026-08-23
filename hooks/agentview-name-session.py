#!/usr/bin/env python3
"""Agent View session auto-namer (fail-open).

Sets the session title to the bead id (AestheticcNext-<id>) so the Agent View
row is identifiable. Runs on SessionStart and UserPromptSubmit. Never errors,
never blocks. Emits the control JSON only when it is confident.

Safety rules:
- Prompt-based naming fires ONLY when the prompt is a real `/goal '<id>'` or
  `/long-goal '<id>'` dispatch (starts with /goal or /long-goal). A chat that
  merely mentions a bead id in prose (e.g. an orchestrator) is left alone.
- Branch/cwd naming fires only when the ref literally encodes the bead.
- Each session is named at most once (marker file keyed on session_id).
- Always logs to ~/.claude/hooks/agentview-name.log for verification.
"""
import sys, os, re, json, subprocess, datetime

LOG = os.path.expanduser("~/.claude/hooks/agentview-name.log")
MARKERS = os.path.expanduser("~/.claude/hooks/.av-named")
BEAD_TOKEN = r"[A-Za-z0-9]{4,5}(?:\.[A-Za-z0-9]{1,5})*"


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(datetime.datetime.now().isoformat(timespec="seconds") + "\t" + msg + "\n")
    except Exception:
        pass


def clean(s):
    return s.rstrip(".").strip() if s else s


def prompt_bead(prompt):
    # Only a genuine /goal or /long-goal dispatch may name a session from its
    # prompt. Accept any bead id form: prefixed (AestheticcNext-, LUCY-, MJ-,
    # ...) or a bare slug (cyeiy, rdp7l, 6vgi3). Capture the first token after
    # /goal|/long-goal and validate it looks like a bead id (so prose after it
    # is never grabbed).
    m = re.match(r"\s*/(?:long-)?goal\s+['\"]?([A-Za-z0-9][A-Za-z0-9._-]*)['\"]?(?:\s|$)", prompt or "")
    if not m:
        return None
    tok = clean(m.group(1))
    if re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9][A-Za-z0-9.]*", tok) or \
       re.fullmatch(r"[A-Za-z0-9]{4,6}(?:\.[A-Za-z0-9]{1,5})*", tok):
        return tok
    return None


def ref_bead(*texts):
    for t in texts:
        if not t:
            continue
        m = re.search(r"aestheticcnext[-/](" + BEAD_TOKEN + r")(?:[-/]|$)", t, re.I)
        if m:
            return "AestheticcNext-" + clean(m.group(1))
        m = re.match(r"^goal-(" + BEAD_TOKEN + r")$", t)
        if m:
            return "AestheticcNext-" + clean(m.group(1))
    return None


def main():
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        pass
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}

    event = data.get("hook_event_name") or data.get("hookEventName") or "?"
    cwd = data.get("cwd") or os.getcwd()
    prompt = data.get("prompt") or ""
    sid = data.get("session_id") or data.get("sessionId") or ""

    bead = prompt_bead(prompt)
    branch = ""
    base = os.path.basename(cwd.rstrip("/")) if cwd else ""
    if not bead:
        try:
            branch = subprocess.check_output(
                ["git", "-C", cwd, "branch", "--show-current"],
                stderr=subprocess.DEVNULL, timeout=3).decode().strip()
        except Exception:
            branch = ""
        bead = ref_bead(branch, base)

    log("event=%s sid=%s cwd=%s branch=%s prompt=%r bead=%s"
        % (event, sid[:12], cwd, branch, prompt[:80], bead))

    if not bead:
        return

    # name each session at most once
    marker = None
    if sid:
        try:
            os.makedirs(MARKERS, exist_ok=True)
            marker = os.path.join(MARKERS, re.sub(r"[^A-Za-z0-9_-]", "_", sid))
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            log("skip (already named) sid=%s" % sid[:12])
            return
        except Exception:
            marker = None

    out = {"hookSpecificOutput": {
        "hookEventName": event if event != "?" else "SessionStart",
        "sessionTitle": bead}}
    try:
        sys.stdout.write(json.dumps(out))
    except Exception:
        return


try:
    main()
except Exception as e:
    log("FATAL " + repr(e))
sys.exit(0)
