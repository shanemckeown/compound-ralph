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
  fleet-role.py <session-id> --check    # synonym for no flag (the default identity test)
  fleet-role.py <session-id> --claim    # take the role (refuses if another holder is LIVE/UNKNOWN)
  fleet-role.py <session-id> --claim --steal   # take it anyway, recording the takeover
  fleet-role.py --release               # give it up
  fleet-role.py --who                   # who holds it, and are they still alive?
"""
import json, os, re, subprocess, sys, time, datetime

FLEET_DIR = os.path.expanduser("~/.claude/fleet")
MARKER = os.path.join(FLEET_DIR, "ORCHESTRATOR")
MANAGERS_DIR = os.path.join(FLEET_DIR, "managers")
LOCK_DIR = os.path.join(FLEET_DIR, "LOCK.d")

LOCK_TIMEOUT_S = 15
LOCK_POLL_S = 0.2

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
        if r.returncode != 0:
            return None
        return {a.get("sessionId"): a for a in json.loads(r.stdout or "[]")}
    except Exception:
        return None


def _acquire_lock():
    os.makedirs(FLEET_DIR, exist_ok=True)
    deadline = time.time() + LOCK_TIMEOUT_S
    while time.time() < deadline:
        try:
            os.mkdir(LOCK_DIR)
            return True
        except FileExistsError:
            # stale lock guard: if LOCK.d is itself old (>30s), a prior holder likely
            # crashed mid-update — reclaim it rather than deadlock forever.
            try:
                age = time.time() - os.path.getmtime(LOCK_DIR)
                if age > 30:
                    os.rmdir(LOCK_DIR)
                    continue
            except OSError:
                pass
            time.sleep(LOCK_POLL_S)
    return False


def _release_lock():
    try:
        os.rmdir(LOCK_DIR)
    except OSError:
        pass


def _write_json(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rec, fh, indent=2)
    os.replace(tmp, path)


def read_marker():
    try:
        with open(MARKER) as fh:
            return json.load(fh)
    except Exception:
        return None


def write_marker(session_id, name, stolen_from=None):
    rec = {"session_id": session_id, "name": name, "claimed_at": now()}
    if stolen_from:
        rec["stole_from"] = stolen_from
    _write_json(MARKER, rec)
    return rec


def holder_status():
    """(record, 'VACANT'|'LIVE'|'STALE'|'UNKNOWN'). STALE means the role is free."""
    rec = read_marker()
    if not rec or not rec.get("session_id"):
        return None, "VACANT"
    sessions = live_sessions()
    if sessions is None:
        return rec, "UNKNOWN"
    return rec, ("LIVE" if rec["session_id"] in sessions else "STALE")


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
    rec = {"work_id": work_id, "session_id": session_id, "name": name,
           "claimed_at": now(), "handoff_doc_path": handoff_doc_path}
    if stolen_from:
        rec["stole_from"] = stolen_from
    _write_json(manager_marker_path(work_id), rec)
    return rec


def manager_holder_status(work_id, sessions=None):
    """(record, 'VACANT'|'LIVE'|'STALE'|'UNKNOWN'). STALE means work is free."""
    rec = read_manager_marker(work_id)
    if not rec or not rec.get("session_id"):
        return None, "VACANT"
    sessions = live_sessions() if sessions is None else sessions
    if sessions is None:
        return rec, "UNKNOWN"
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
        elif sessions is None:
            rows.append((work_id, rec, "UNKNOWN"))
        else:
            state = "LIVE" if rec["session_id"] in sessions else "STALE"
            rows.append((work_id, rec, state))

    for work_id, rec, state in sorted(rows, key=lambda row: row[0]):
        if state == "VACANT":
            print(f"MANAGER for {work_id}: VACANT — marker has no valid holder")
        elif state == "LIVE":
            print(f"MANAGER for {work_id}: LIVE — {rec.get('name')} ({rec['session_id']}), "
                  f"claimed_at {rec.get('claimed_at')}")
        elif state == "UNKNOWN":
            print(f"MANAGER for {work_id}: UNKNOWN — {rec.get('name')} ({rec['session_id']}), "
                  f"claimed_at {rec.get('claimed_at')} — holder's liveness could not be "
                  f"verified right now; treat as live until re-checked")
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
        # A punctuation-only work ID such as "---" starts with "--" but is still an ID
        # candidate; let the explicit empty-sanitized-ID guard below reject it clearly.
        if arg.startswith("--") and sanitize_work_id(arg):
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
    if not sanitize_work_id(work_id):
        print("work-id sanitizes to empty — pass a real, non-punctuation-only id", file=sys.stderr)
        print(MANAGER_USAGE)
        return 2

    if "--who" in flags:
        if len(ids) != 1:
            print(MANAGER_USAGE)
            return 2
        rec, state = manager_holder_status(work_id)
        if state == "VACANT":
            print(f"MANAGER for {work_id}: VACANT — no session holds this work")
            return 0
        if state == "LIVE":
            alive = "still live"
        elif state == "UNKNOWN":
            alive = "holder's liveness could not be verified right now — treat as live until re-checked"
        else:
            alive = "🔴 NO LONGER LIVE — claim is void, work is free"
        print(f"MANAGER for {work_id}: {rec.get('name')} ({rec['session_id']})")
        print(f"  claimed_at: {rec.get('claimed_at')}")
        print(f"  status:     {alive}")
        return 0

    if "--release" in flags:
        if len(ids) != 1:
            print(MANAGER_USAGE)
            return 2
        if not _acquire_lock():
            print("LOCK_TIMEOUT — could not acquire fleet-role lock in time", file=sys.stderr)
            return 1
        try:
            rec = read_manager_marker(work_id)
            path = manager_marker_path(work_id)
            if os.path.exists(path):
                os.remove(path)
            print(f"MANAGER for {work_id}: released (was {rec.get('name') if rec else 'nobody'})")
            return 0
        finally:
            _release_lock()

    if len(ids) != 2:
        print(MANAGER_USAGE)
        return 2
    me = ids[1]

    if "--claim" in flags:
        if not _acquire_lock():
            print("LOCK_TIMEOUT — could not acquire fleet-role lock in time", file=sys.stderr)
            return 1
        try:
            rec, state = manager_holder_status(work_id)
            held_by_other = rec and rec["session_id"] != me
            if state in {"LIVE", "UNKNOWN"} and held_by_other and "--steal" not in flags:
                if state == "UNKNOWN":
                    print(f"REFUSED — {rec.get('name')} ({rec['session_id'][:8]}) holds MANAGER "
                          f"for {work_id}, but the holder's liveness could not be verified.")
                    print("Use --steal only if you're sure this is safe.")
                else:
                    print(f"REFUSED — {rec.get('name')} ({rec['session_id'][:8]}) holds MANAGER "
                          f"for {work_id} and is still live.")
                    print(f"Exactly one session is the MANAGER for {work_id}. Use --steal only "
                          f"if that is wrong.")
                return 1
            stolen = rec["session_id"] if (held_by_other and state in {"LIVE", "UNKNOWN"}) else None
            sessions = live_sessions()
            name = (sessions or {}).get(me, {}).get("name", "unknown")
            new = write_manager_marker(work_id, me, name, handoff_doc_path, stolen)
            print(f"MANAGER for {work_id} — claimed by {new['name']} ({me[:8]}) at {new['claimed_at']}")
            if state == "STALE":
                print(f"  (took over from {rec.get('name')}, whose session is gone)")
            if stolen and state == "UNKNOWN":
                print(f"  ⚠ STOLE from {rec.get('name')}, whose liveness could not be verified — "
                      f"recorded in the marker")
            elif stolen:
                print(f"  ⚠ STOLE from a live session {rec.get('name')} — recorded in the marker")
            return 0
        finally:
            _release_lock()

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
    elif state == "UNKNOWN":
        print("  reason: cannot verify liveness of the current holder — registry probe failed, "
              "defaulting to not-the-role", file=sys.stderr)
    else:
        print(f"  reason: {rec.get('name')} ({rec['session_id'][:8]}) is MANAGER for {work_id}",
              file=sys.stderr)
    return 1


def main():
    argv = sys.argv[1:]
    flags = {a for a in argv if a.startswith("--")}
    ids = [a for a in argv if not a.startswith("--")]
    known_flags = {"--check", "--claim", "--steal", "--release", "--who"}
    if flags - known_flags:
        print(__doc__)
        return 2

    if "--who" in flags:
        rec, state = holder_status()
        if state == "VACANT":
            print("orchestrator: VACANT — no session holds the role")
            return 0
        if state == "LIVE":
            alive = "still live"
        elif state == "UNKNOWN":
            alive = "holder's liveness could not be verified right now — treat as live until re-checked"
        else:
            alive = "🔴 NO LONGER LIVE — claim is void, role is free"
        print(f"orchestrator: {rec.get('name')} ({rec['session_id']})")
        print(f"  claimed_at: {rec.get('claimed_at')}")
        print(f"  status:     {alive}")
        return 0

    if "--release" in flags:
        if not _acquire_lock():
            print("LOCK_TIMEOUT — could not acquire fleet-role lock in time", file=sys.stderr)
            return 1
        try:
            rec = read_marker()
            if os.path.exists(MARKER):
                os.remove(MARKER)
            print(f"orchestrator: released (was {rec.get('name') if rec else 'nobody'})")
            return 0
        finally:
            _release_lock()

    if not ids:
        print(__doc__)
        return 2
    me = ids[0]

    if "--claim" in flags:
        if not _acquire_lock():
            print("LOCK_TIMEOUT — could not acquire fleet-role lock in time", file=sys.stderr)
            return 1
        try:
            rec, state = holder_status()
            held_by_other = rec and rec["session_id"] != me
            if state in {"LIVE", "UNKNOWN"} and held_by_other and "--steal" not in flags:
                if state == "UNKNOWN":
                    print(f"REFUSED — {rec.get('name')} ({rec['session_id'][:8]}) holds it, but "
                          f"the holder's liveness could not be verified.")
                    print("Use --steal only if you're sure this is safe.")
                else:
                    print(f"REFUSED — {rec.get('name')} ({rec['session_id'][:8]}) holds it and "
                          f"is still live.")
                    print("Exactly one session is the orchestrator. Use --steal only if that is wrong.")
                return 1
            stolen = rec["session_id"] if (held_by_other and state in {"LIVE", "UNKNOWN"}) else None
            sessions = live_sessions()
            name = (sessions or {}).get(me, {}).get("name", "unknown")
            new = write_marker(me, name, stolen)
            print(f"ORCHESTRATOR — claimed by {new['name']} ({me[:8]}) at {new['claimed_at']}")
            if state == "STALE":
                print(f"  (took over from {rec.get('name')}, whose session is gone)")
            if stolen and state == "UNKNOWN":
                print(f"  ⚠ STOLE from {rec.get('name')}, whose liveness could not be verified — "
                      f"recorded in the marker")
            elif stolen:
                print(f"  ⚠ STOLE from a live session {rec.get('name')} — recorded in the marker")
            return 0
        finally:
            _release_lock()

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
    elif state == "UNKNOWN":
        print("  reason: cannot verify liveness of the current holder — registry probe failed, "
              "defaulting to not-the-role", file=sys.stderr)
    else:
        print(f"  reason: {rec.get('name')} ({rec['session_id'][:8]}) is the orchestrator", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "manager":
        sys.exit(manager_main())
    sys.exit(main())
