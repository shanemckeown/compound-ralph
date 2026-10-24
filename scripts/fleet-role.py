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

Claude claims use `claude agents --json`; Codex claims use a thread-specific
app-server/desktop socket probe. Missing evidence is UNKNOWN and reserves the
claim. Historical transcripts and a generic app-server PID never prove LIVE.
Only a verified owner endpoint can establish that a Codex thread is unloaded.
Legacy markers with no provider field retain their Claude interpretation.

HOW A SESSION KNOWS ITS OWN ID: Claude uses the UUID directory in its scratchpad
path (`/private/tmp/claude-*/<project>/<SESSION-UUID>/scratchpad`). Codex exposes
CODEX_THREAD_ID. These identify a claimant; neither proves it is currently live.
`ListAgents` omits the calling Claude session, so use the CLI registry for probes.

Usage:
  fleet-role.py <session-id>            # ORCHESTRATOR | SUB  (exit 0 | 1)
  fleet-role.py <session-id> --check    # synonym for no flag (the default identity test)
  fleet-role.py <session-id> --claim    # take the role (refuses if another holder is LIVE/UNKNOWN)
  fleet-role.py <session-id> --claim --steal   # take it anyway, recording the takeover
  fleet-role.py --release               # give it up
  fleet-role.py --who                   # who holds it, and are they still alive?
  fleet-role.py --who --json            # {record, status, provider} for relays
  fleet-role.py <thread-id> --claim --provider codex [--codex-socket PATH]

Codex socket defaults to the managed app-server control socket when present,
otherwise the version-checked desktop IPC adapter. Neither starts/resumes a
thread or calls a model. An UNKNOWN claim is reserved, not asserted LIVE.
"""
import json, os, re, subprocess, sys, time, datetime, fcntl
from fleet_codex_rpc import probe_thread

FLEET_DIR = os.path.expanduser("~/.claude/fleet")
MARKER = os.path.join(FLEET_DIR, "ORCHESTRATOR")
MANAGERS_DIR = os.path.join(FLEET_DIR, "managers")
LOCK_DIR = os.path.join(FLEET_DIR, "LOCK.d")

LOCK_TIMEOUT_S = 15
LOCK_POLL_S = 0.2
UNSET = object()
PROVIDERS = {"claude", "codex"}
DEFAULT_CODEX_SOCKET = os.path.expanduser("~/.codex/app-server-control/app-server-control.sock")
DESKTOP_CODEX_SOCKET = os.path.expanduser("~/.codex/ipc/ipc.sock")

MANAGER_USAGE = """Usage:
  fleet-role.py manager <work-id> <session-id>
  fleet-role.py manager <work-id> <session-id> --claim [--steal] [--handoff-doc PATH]
  fleet-role.py manager <work-id> --release
  fleet-role.py manager <work-id> --who
  fleet-role.py manager --list
  Add --provider codex [--codex-socket PATH] to a Codex claim.
  Add --json to --who for the {record, status, provider} interface."""


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def live_sessions():
    try:
        r = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None
        rows = json.loads(r.stdout)
        if not isinstance(rows, list) or any(
                not isinstance(a, dict) or not isinstance(a.get("sessionId"), str)
                or not a["sessionId"] for a in rows):
            return None
        return {a["sessionId"]: a for a in rows}
    except Exception:
        return None


def record_provider(rec):
    # Existing Claude markers remain compatible. Invalid/new providers are never
    # silently sent to the Claude registry or treated as a dead Claude session.
    return rec.get("provider", "claude") if rec else None


def default_codex_socket():
    return DEFAULT_CODEX_SOCKET if os.path.exists(DEFAULT_CODEX_SOCKET) else DESKTOP_CODEX_SOCKET


def session_status(rec, sessions=UNSET):
    if not rec or not rec.get("session_id"):
        return "VACANT"
    provider = record_provider(rec)
    if provider == "claude":
        sessions = live_sessions() if sessions is UNSET else sessions
        if sessions is None:
            return "UNKNOWN"
        return "LIVE" if rec["session_id"] in sessions else "STALE"
    if provider == "codex":
        try:
            result = probe_thread(rec["session_id"], rec.get("codex_socket") or default_codex_socket())
        except Exception:
            return "UNKNOWN"
        if result.get("status") == "LIVE":
            return "LIVE"
        # A different server's empty inventory proves nothing about this thread.
        # Only a socket verified to own the thread at claim-time can prove it
        # unloaded. Failed/missing sockets remain UNKNOWN, never free to steal.
        if result.get("status") == "NOT_LOADED" and rec.get("codex_socket_verified") is True:
            return "STALE"
    return "UNKNOWN"


def parse_provider_options(argv):
    rest, options = [], {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in {"--provider", "--codex-socket"}:
            if i + 1 >= len(argv) or argv[i + 1].startswith("--") or arg in options:
                raise ValueError(f"{arg} requires exactly one value")
            options[arg] = argv[i + 1]
            i += 2
        else:
            rest.append(arg)
            i += 1
    provider = options.get("--provider")
    if provider is not None and provider not in PROVIDERS:
        raise ValueError("--provider must be claude or codex")
    if "--codex-socket" in options and "--claim" not in rest:
        raise ValueError("--codex-socket is valid only with --claim")
    return rest, options


def claim_identity(me, rec, options):
    previous = rec if rec and rec.get("session_id") == me else None
    provider = options.get("--provider") or record_provider(previous) or "claude"
    if provider not in PROVIDERS:
        raise ValueError("Unknown marker provider; pass --provider explicitly")
    if provider == "claude":
        if "--codex-socket" in options:
            raise ValueError("--codex-socket requires --provider codex")
        sessions = live_sessions()
        return provider, (sessions or {}).get(me, {}).get("name", "unknown"), {}
    socket_path = os.path.abspath(os.path.expanduser(
        options.get("--codex-socket") or (previous or {}).get("codex_socket") or default_codex_socket()))
    try:
        result = probe_thread(me, socket_path)
    except Exception:
        result = {"status": "UNKNOWN"}
    verified = result.get("status") == "LIVE" or bool(
        previous and previous.get("codex_socket_verified") is True
        and previous.get("codex_socket") == socket_path)
    return provider, result.get("name") or f"Codex {me[:8]}", {
        "codex_socket": socket_path, "codex_socket_verified": verified}


def print_status_json(rec, state):
    print(json.dumps({"record": rec, "status": state, "provider": record_provider(rec)}))


_lock_handle = None


def _acquire_lock():
    """Kernel lock survives slow provider probes and releases on process death.

    A running pre-upgrade script may still hold the legacy LOCK.d directory;
    wait for that owner rather than tearing its lock down by age.
    """
    global _lock_handle
    os.makedirs(FLEET_DIR, exist_ok=True)
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    handle = open(LOCK_DIR + ".flock", "a+")
    while time.monotonic() < deadline:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not os.path.exists(LOCK_DIR):
                _lock_handle = handle
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
        except BlockingIOError:
            pass
        time.sleep(LOCK_POLL_S)
    handle.close()
    return False


def _release_lock():
    global _lock_handle
    if _lock_handle is not None:
        fcntl.flock(_lock_handle, fcntl.LOCK_UN)
        _lock_handle.close()
        _lock_handle = None


def _write_json(path, rec):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(rec, fh, indent=2)
    os.replace(tmp, path)


def read_marker():
    return read_role_marker(MARKER)


def read_role_marker(path):
    try:
        with open(path) as fh:
            rec = json.load(fh)
        if not isinstance(rec, dict) or not isinstance(rec.get("session_id"), str) or not rec["session_id"]:
            raise ValueError("invalid role record")
        return rec
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # An unreadable claim is not evidence that nobody holds the role.
        return {"session_id": "unreadable-marker", "provider": "unknown", "name": "unreadable marker"}


def write_marker(session_id, name, stolen_from=None, provider="claude", runtime=None):
    rec = {"session_id": session_id, "name": name, "claimed_at": now(), "provider": provider}
    rec.update(runtime or {})
    if stolen_from:
        rec["stole_from"] = stolen_from
    _write_json(MARKER, rec)
    return rec


def holder_status():
    """(record, 'VACANT'|'LIVE'|'STALE'|'UNKNOWN'). STALE means the role is free."""
    rec = read_marker()
    if not rec or not rec.get("session_id"):
        return None, "VACANT"
    return rec, session_status(rec)


def sanitize_work_id(work_id):
    return re.sub(r"[^a-z0-9._-]+", "-", work_id.lower()).strip("-")


def manager_marker_path(work_id):
    return os.path.join(MANAGERS_DIR, f"{sanitize_work_id(work_id)}.json")


def read_manager_marker(work_id):
    return read_role_marker(manager_marker_path(work_id))


def write_manager_marker(work_id, session_id, name, handoff_doc_path=None, stolen_from=None,
                         provider="claude", runtime=None):
    rec = {"work_id": work_id, "session_id": session_id, "name": name,
           "claimed_at": now(), "handoff_doc_path": handoff_doc_path, "provider": provider}
    rec.update(runtime or {})
    if stolen_from:
        rec["stole_from"] = stolen_from
    _write_json(manager_marker_path(work_id), rec)
    return rec


def manager_holder_status(work_id, sessions=UNSET):
    """(record, 'VACANT'|'LIVE'|'STALE'|'UNKNOWN'). STALE means work is free."""
    rec = read_manager_marker(work_id)
    if not rec or not rec.get("session_id"):
        return None, "VACANT"
    return rec, session_status(rec, sessions)


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
        rec = read_role_marker(path)
        work_id = rec.get("work_id") if rec else None
        if not isinstance(work_id, str):
            work_id = filename[:-5]
        if not rec or not rec.get("session_id"):
            rows.append((work_id, None, "VACANT"))
        else:
            rows.append((work_id, rec, session_status(rec, sessions)))

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
    argv, provider_options = parse_provider_options(sys.argv[2:])
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

    known_flags = {"--claim", "--steal", "--release", "--who", "--list", "--json"}
    verbs = flags & {"--claim", "--release", "--who", "--list"}
    if (flags - known_flags or len(verbs) > 1 or
            ("--steal" in flags and "--claim" not in flags) or
            (handoff_doc_path is not None and "--claim" not in flags) or
            ("--json" in flags and "--who" not in flags)):
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
        if "--json" in flags:
            print_status_json(rec, state)
            return 0
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
        print(f"  provider:   {record_provider(rec)}")
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
            held_by_other = rec and (rec["session_id"] != me or
                provider_options.get("--provider", record_provider(rec)) != record_provider(rec))
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
            provider, name, runtime = claim_identity(me, rec, provider_options)
            new = write_manager_marker(work_id, me, name, handoff_doc_path, stolen, provider, runtime)
            print(f"MANAGER for {work_id} — claimed by {new['name']} ({me[:8]}) at {new['claimed_at']}")
            if state == "STALE":
                print(f"  (took over from {rec.get('name')}, whose session is gone)")
            if stolen and state == "UNKNOWN":
                print(f"  ⚠ STOLE from {rec.get('name')}, whose liveness could not be verified — "
                      f"recorded in the marker")
            elif stolen:
                print(f"  ⚠ STOLE from a live session {rec.get('name')} — recorded in the marker")
            if provider == "codex" and not runtime["codex_socket_verified"]:
                print("  liveness: UNKNOWN — claim reserved; Codex owner socket not verified")
            return 0
        finally:
            _release_lock()

    # Default: the test itself.
    rec, state = manager_holder_status(work_id)
    if (state == "LIVE" and rec["session_id"] == me and
            provider_options.get("--provider", record_provider(rec)) == record_provider(rec)):
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
    argv, provider_options = parse_provider_options(sys.argv[1:])
    flags = {a for a in argv if a.startswith("--")}
    ids = [a for a in argv if not a.startswith("--")]
    known_flags = {"--check", "--claim", "--steal", "--release", "--who", "--json"}
    if flags - known_flags or ("--json" in flags and "--who" not in flags):
        print(__doc__)
        return 2

    if "--who" in flags:
        rec, state = holder_status()
        if "--json" in flags:
            print_status_json(rec, state)
            return 0
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
        print(f"  provider:   {record_provider(rec)}")
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
            held_by_other = rec and (rec["session_id"] != me or
                provider_options.get("--provider", record_provider(rec)) != record_provider(rec))
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
            provider, name, runtime = claim_identity(me, rec, provider_options)
            new = write_marker(me, name, stolen, provider, runtime)
            print(f"ORCHESTRATOR — claimed by {new['name']} ({me[:8]}) at {new['claimed_at']}")
            if state == "STALE":
                print(f"  (took over from {rec.get('name')}, whose session is gone)")
            if stolen and state == "UNKNOWN":
                print(f"  ⚠ STOLE from {rec.get('name')}, whose liveness could not be verified — "
                      f"recorded in the marker")
            elif stolen:
                print(f"  ⚠ STOLE from a live session {rec.get('name')} — recorded in the marker")
            if provider == "codex" and not runtime["codex_socket_verified"]:
                print("  liveness: UNKNOWN — claim reserved; Codex owner socket not verified")
            return 0
        finally:
            _release_lock()

    # Default: the test itself.
    rec, state = holder_status()
    if (state == "LIVE" and rec["session_id"] == me and
            provider_options.get("--provider", record_provider(rec)) == record_provider(rec)):
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
    try:
        sys.exit(manager_main() if len(sys.argv) > 1 and sys.argv[1] == "manager" else main())
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(2)
