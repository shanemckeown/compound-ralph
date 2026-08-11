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
import json, os, subprocess, sys, datetime

MARKER = os.path.expanduser("~/.claude/fleet/ORCHESTRATOR")


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
    sys.exit(main())
