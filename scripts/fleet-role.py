#!/usr/bin/env python3
"""fleet-role.py — the orchestrator identity marker. Default-deny.

The 2026-08-09 voice-orchestration design (§8.3) asked for this:

    "Shane wants one Claude that knows it's the orchestrator, with every spawned
     session defaulting to sub-agent unless explicitly told otherwise.
     Default-deny. Needs a marker a session can actually TEST at startup —
     prose in CLAUDE.md drifts."

Prose can't be tested, so this is a file plus a check.

  ~/.claude/fleet/ORCHESTRATOR   {"session_id": "...", "claimed_at": "...", "name": "..."}

A session asks "am I the orchestrator?" and gets ORCHESTRATOR or SUB. It is SUB
unless it is *positively* proven otherwise, so every failure mode — missing file,
corrupt file, stale claim, someone else's claim — lands on SUB.

🔴 A claim is void if the claiming session is no longer live in
`claude agents --json`. Without that check the marker would rot exactly the way
everything else in this stack rotted: a dead orchestrator would keep holding the
role, sub-Claudes would keep escalating into a void, and nothing would say so.

HOW A SESSION KNOWS ITS OWN ID: it is the UUID directory in the session's
scratchpad path (`/private/tmp/claude-*/<project>/<SESSION-UUID>/scratchpad`).
`ListAgents` deliberately never returns the calling session, so self-identity
cannot come from there.

Usage:
  fleet-role.py <session-id>            # ORCHESTRATOR | SUB  (exit 0 | 1)
  fleet-role.py <session-id> --claim    # take the role (refuses if a LIVE session holds it)
  fleet-role.py <session-id> --claim --steal   # take it anyway, recording the takeover
  fleet-role.py --release               # give it up
  fleet-role.py --who                   # who holds it, and are they still alive?
"""
import json, os, re, subprocess, sys, datetime

MARKER = os.path.expanduser("~/.claude/fleet/ORCHESTRATOR")
MANAGERS_DIR = os.path.expanduser("~/.claude/fleet/managers")

MANAGER_USAGE = """Usage:
  fleet-role.py manager <work-id> <session-id>
  fleet-role.py manager <work-id> <session-id> --claim [--steal] [--handoff-doc PATH]
  fleet-role.py manager <work-id> --release
  fleet-role.py manager <work-id> --who
  fleet-role.py manager --list"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def live_sessions():
    try:
        r = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30)
        return {a.get("sessionId"): a for a in json.loads(r.stdout or "[]")}
    except Exception:
        return {}


def read_marker():
    try:
        with open(MARKER) as fh:
            return json.load(fh)
    except Exception:
        return None


def write_marker(session_id, name, stolen_from=None):
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    rec = {"session_id": session_id, "name": name, "claimed_at": now()}
    if stolen_from:
        rec["stole_from"] = stolen_from
    with open(MARKER, "w") as fh:
        json.dump(rec, fh, indent=2)
    return rec


def holder_status():
    """(record, 'VACANT'|'LIVE'|'STALE'). STALE means the holder is gone — role is free."""
    rec = read_marker()
    if not rec or not rec.get("session_id"):
        return None, "VACANT"
    return rec, ("LIVE" if rec["session_id"] in live_sessions() else "STALE")


def sanitize_work_id(work_id):
    return re.sub(r"[^a-z0-9._-]+", "-", work_id.lower()).strip("-")


def manager_marker_path(work_id):
    return os.path.join(MANAGERS_DIR, f"{sanitize_work_id(work_id)}.json")


def read_manager_marker(work_id):
    try:
        with open(manager_marker_path(work_id)) as fh:
            return json.load(fh)
    except Exception:
        return None


def write_manager_marker(work_id, session_id, name, handoff_doc_path=None, stolen_from=None):
    os.makedirs(MANAGERS_DIR, exist_ok=True)
    rec = {"work_id": work_id, "session_id": session_id, "name": name,
           "claimed_at": now(), "handoff_doc_path": handoff_doc_path}
    if stolen_from:
        rec["stole_from"] = stolen_from
    with open(manager_marker_path(work_id), "w") as fh:
        json.dump(rec, fh, indent=2)
    return rec


def manager_holder_status(work_id, sessions=None):
    """(record, 'VACANT'|'LIVE'|'STALE'). STALE means this work is free to claim."""
    rec = read_manager_marker(work_id)
    if not rec or not rec.get("session_id"):
        return None, "VACANT"
    sessions = live_sessions() if sessions is None else sessions
    return rec, ("LIVE" if rec["session_id"] in sessions else "STALE")


def list_manager_claims():
    try:
        marker_files = [name for name in os.listdir(MANAGERS_DIR) if name.endswith(".json")]
    except OSError:
        marker_files = []
    if not marker_files:
        print("no active MANAGER claims")
        return 0

    sessions = live_sessions()
    rows = []
    for filename in marker_files:
        path = os.path.join(MANAGERS_DIR, filename)
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except Exception:
            rec = None
        work_id = rec.get("work_id") if rec else None
        if not isinstance(work_id, str):
            work_id = filename[:-5]
        if not rec or not rec.get("session_id"):
            rows.append((work_id, None, "VACANT"))
        else:
            state = "LIVE" if rec["session_id"] in sessions else "STALE"
            rows.append((work_id, rec, state))

    for work_id, rec, state in sorted(rows, key=lambda row: row[0]):
        if state == "VACANT":
            print(f"MANAGER for {work_id}: VACANT — marker has no valid holder")
        elif state == "LIVE":
            print(f"MANAGER for {work_id}: LIVE — {rec.get('name')} ({rec['session_id']}), "
                  f"claimed_at {rec.get('claimed_at')}")
        else:
            print(f"MANAGER for {work_id}: STALE — {rec.get('name')} ({rec['session_id']}), "
                  f"claimed_at {rec.get('claimed_at')} — claim is void, free to claim")
    return 0


def manager_main():
    argv = sys.argv[2:]
    flags = set()
    ids = []
    handoff_doc_path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--handoff-doc":
            if i + 1 >= len(argv):
                print("--handoff-doc requires PATH", file=sys.stderr)
                print(MANAGER_USAGE)
                return 2
            handoff_doc_path = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            flags.add(arg)
        else:
            ids.append(arg)
        i += 1

    known_flags = {"--claim", "--steal", "--release", "--who", "--list"}
    verbs = flags & {"--claim", "--release", "--who", "--list"}
    if (flags - known_flags or len(verbs) > 1 or
            ("--steal" in flags and "--claim" not in flags) or
            (handoff_doc_path is not None and "--claim" not in flags)):
        print(MANAGER_USAGE)
        return 2

    if "--list" in flags:
        if ids:
            print(MANAGER_USAGE)
            return 2
        return list_manager_claims()

    if not ids:
        print(MANAGER_USAGE)
        return 2
    work_id = ids[0]

    if "--who" in flags:
        if len(ids) != 1:
            print(MANAGER_USAGE)
            return 2
        rec, state = manager_holder_status(work_id)
        if state == "VACANT":
            print(f"MANAGER for {work_id}: VACANT — no session holds this work")
            return 0
        alive = "still live" if state == "LIVE" else "🔴 NO LONGER LIVE — claim is void, work is free"
        print(f"MANAGER for {work_id}: {rec.get('name')} ({rec['session_id']})")
        print(f"  claimed_at: {rec.get('claimed_at')}")
        print(f"  status:     {alive}")
        return 0

    if "--release" in flags:
        if len(ids) != 1:
            print(MANAGER_USAGE)
            return 2
        rec = read_manager_marker(work_id)
        path = manager_marker_path(work_id)
        if os.path.exists(path):
            os.remove(path)
        print(f"MANAGER for {work_id}: released (was {rec.get('name') if rec else 'nobody'})")
        return 0

    if len(ids) != 2:
        print(MANAGER_USAGE)
        return 2
    me = ids[1]

    if "--claim" in flags:
        rec, state = manager_holder_status(work_id)
        if state == "LIVE" and rec["session_id"] != me and "--steal" not in flags:
            print(f"REFUSED — {rec.get('name')} ({rec['session_id'][:8]}) holds MANAGER for "
                  f"{work_id} and is still live.")
            print(f"Exactly one session is the MANAGER for {work_id}. Use --steal only if that is wrong.")
            return 1
        stolen = rec["session_id"] if (rec and state == "LIVE" and rec["session_id"] != me) else None
        name = live_sessions().get(me, {}).get("name", "unknown")
        new = write_manager_marker(work_id, me, name, handoff_doc_path, stolen)
        print(f"MANAGER for {work_id} — claimed by {new['name']} ({me[:8]}) at {new['claimed_at']}")
        if state == "STALE":
            print(f"  (took over from {rec.get('name')}, whose session is gone)")
        if stolen:
            print(f"  ⚠ STOLE from a live session {rec.get('name')} — recorded in the marker")
        return 0

    # Default: the test itself.
    rec, state = manager_holder_status(work_id)
    if state == "LIVE" and rec["session_id"] == me:
        print("MANAGER")
        return 0
    print("NOT-MANAGER")
    if state == "VACANT":
        print(f"  reason: no session holds MANAGER for {work_id}", file=sys.stderr)
    elif state == "STALE":
        print(f"  reason: {rec.get('name')} holds MANAGER for {work_id} but is no longer live — "
              f"the work is FREE to claim", file=sys.stderr)
    else:
        print(f"  reason: {rec.get('name')} ({rec['session_id'][:8]}) is MANAGER for {work_id}",
              file=sys.stderr)
    return 1


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    ids = [a for a in argv if not a.startswith("--")]

    if "--who" in flags:
        rec, state = holder_status()
        if state == "VACANT":
            print("orchestrator: VACANT — no session holds the role")
            return 0
        alive = "still live" if state == "LIVE" else "🔴 NO LONGER LIVE — claim is void, role is free"
        print(f"orchestrator: {rec.get('name')} ({rec['session_id']})")
        print(f"  claimed_at: {rec.get('claimed_at')}")
        print(f"  status:     {alive}")
        return 0

    if "--release" in flags:
        rec = read_marker()
        if os.path.exists(MARKER):
            os.remove(MARKER)
        print(f"orchestrator: released (was {rec.get('name') if rec else 'nobody'})")
        return 0

    if not ids:
        print(__doc__)
        return 2
    me = ids[0]

    if "--claim" in flags:
        rec, state = holder_status()
        if state == "LIVE" and rec["session_id"] != me and "--steal" not in flags:
            print(f"REFUSED — {rec.get('name')} ({rec['session_id'][:8]}) holds it and is still live.")
            print("Exactly one session is the orchestrator. Use --steal only if that is wrong.")
            return 1
        stolen = rec["session_id"] if (rec and state == "LIVE" and rec["session_id"] != me) else None
        name = live_sessions().get(me, {}).get("name", "unknown")
        new = write_marker(me, name, stolen)
        print(f"ORCHESTRATOR — claimed by {new['name']} ({me[:8]}) at {new['claimed_at']}")
        if state == "STALE":
            print(f"  (took over from {rec.get('name')}, whose session is gone)")
        if stolen:
            print(f"  ⚠ STOLE from a live session {rec.get('name')} — recorded in the marker")
        return 0

    # Default: the test itself.
    rec, state = holder_status()
    if state == "LIVE" and rec["session_id"] == me:
        print("ORCHESTRATOR")
        return 0
    print("SUB")
    if state == "VACANT":
        print("  reason: no session holds the orchestrator role", file=sys.stderr)
    elif state == "STALE":
        print(f"  reason: {rec.get('name')} holds it but is no longer live — the role is FREE to claim",
              file=sys.stderr)
    else:
        print(f"  reason: {rec.get('name')} ({rec['session_id'][:8]}) is the orchestrator", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "manager":
        sys.exit(manager_main())
    sys.exit(main())
