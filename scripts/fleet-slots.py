#!/usr/bin/env python3
"""fleet-slots.py — a counting semaphore + FIFO queue for total concurrent fleet load.

Why this exists (2026-08-23, Shane): a dispatched-session count that "should" stay at
6-8 was pure convention — nothing enforced it, and nothing accounted for what each
session itself spawns (Codex/GLM calls). This gives fleet-dispatch.py (and any script)
an atomic claim/release/enqueue primitive against one shared budget, so "at capacity"
becomes a real refusal instead of a memory note nobody re-reads under pressure.

Budget (tunable via env, see CAPS below):
  - TOTAL cap: 11 concurrent "heavy" things at once, INCLUDING the orchestrator's own
    implicit slot (always counted as 1 — this script never claims/releases it, callers
    just get one fewer slot in every count).
  - AGENT_VIEW cap: 5 of that 11 — dispatched `/goal`/`/long-goal` Agent View sessions,
    kept smaller than the total budget on purpose so there's always room for Codex/GLM
    calls (either the orchestrator's own, or ones a dispatched session makes during its
    own QA phase) without a full Agent View session hogging every remaining slot.
  - CODEX_GLM has no separate sub-cap — it draws from whatever's left of TOTAL after
    orchestrator (1) + current agent_view count are subtracted.

Locking: the same primitive land-state.py already validates in this environment —
`mkdir LOCK.d` is atomic and portable, no flock/fcntl platform quirks. Held only for the
brief read-modify-write of state.json, never for the duration of a claimed slot.

Stale-slot reaping: before every count, each held slot is checked against reality —
agent_view entries against `claude agents --json`, codex_glm entries against `ps -p`.
A slot whose owning process/session is gone is reaped automatically. This is the
direct fix for the known failure mode (`reference_laptop_sleep_kills_bg_sessions`):
a session that died silently (laptop sleep, crash) must not permanently eat a slot.

CLI:
  fleet-slots.py claim-agent-view <bead-id>        # exit 0 = claimed, 2 = queued
  fleet-slots.py release-agent-view <bead-id>
  fleet-slots.py claim-codex-glm <kind> <label>    # kind: codex|glm. prints a token on stdout
  fleet-slots.py release-codex-glm <token>
  fleet-slots.py dequeue-next                      # pop+return oldest queued bead if a slot now fits
  fleet-slots.py status                            # human-readable dump, for `bd`-style checking
"""
import json, os, re, subprocess, sys, time, uuid, datetime

ROOT = os.path.expanduser("~/.claude/fleet/slots")
LOCK_DIR = os.path.join(ROOT, "LOCK.d")
STATE_FILE = os.path.join(ROOT, "state.json")

TOTAL_CAP = int(os.environ.get("FLEET_TOTAL_CAP", "11"))
AGENT_VIEW_CAP = int(os.environ.get("FLEET_AGENT_VIEW_CAP", "5"))
ORCHESTRATOR_SLOTS = 1  # always subtracted; never claimed/released via this script

LOCK_TIMEOUT_S = 15
LOCK_POLL_S = 0.2
STALE_AGENT_VIEW_S = 60 * 60 * 12  # 12h: a /long-goal run can legitimately run for hours
STALE_CODEX_GLM_S = 60 * 30        # 30m: no single codex/glm call should run longer


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _ensure_root():
    os.makedirs(ROOT, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        _write_state({"agent_view": [], "codex_glm": [], "queue": []})


def _acquire_lock():
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


def _read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"agent_view": [], "codex_glm": [], "queue": []}


def _write_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def _live_agent_view_session_ids():
    try:
        out = subprocess.run("claude agents --json", shell=True, capture_output=True,
                              text=True, timeout=15).stdout
        agents = json.loads(out) if out.strip() else []
        return {a.get("sessionId") for a in agents if a.get("sessionId")}
    except Exception:
        # can't verify liveness right now -- don't reap on a shaky read, that would
        # silently free slots for sessions that are actually still alive.
        return None


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def _reap(state):
    live_ids = _live_agent_view_session_ids()
    now = time.time()

    kept_av = []
    for e in state.get("agent_view", []):
        age = now - e.get("_ts", 0)
        if live_ids is not None and e.get("session_id") not in live_ids and age > 60:
            continue  # gone from the registry and not brand new -- reap
        if age > STALE_AGENT_VIEW_S:
            continue  # ran unreasonably long without releasing -- reap, log via return
        kept_av.append(e)
    state["agent_view"] = kept_av

    kept_cg = []
    for e in state.get("codex_glm", []):
        pid = e.get("pid")
        age = now - e.get("_ts", 0)
        if pid and not _pid_alive(pid):
            continue
        if age > STALE_CODEX_GLM_S:
            continue
        kept_cg.append(e)
    state["codex_glm"] = kept_cg
    return state


def _counts(state):
    av = len(state.get("agent_view", []))
    cg = len(state.get("codex_glm", []))
    total = ORCHESTRATOR_SLOTS + av + cg
    return av, cg, total


def claim_agent_view(bead):
    _ensure_root()
    if not _acquire_lock():
        print("LOCK_TIMEOUT — could not acquire slots lock in time", file=sys.stderr)
        return False
    try:
        state = _reap(_read_state())
        av, cg, total = _counts(state)
        if av < AGENT_VIEW_CAP and total < TOTAL_CAP:
            state["agent_view"].append({"bead": bead, "claimed_at": _now(), "_ts": time.time(),
                                         "session_id": None})
            _write_state(state)
            return True
        return False
    finally:
        _release_lock()


def attach_session_id(bead, session_id):
    """Called after dispatch returns a real session id, so reaping can check liveness."""
    _ensure_root()
    if not _acquire_lock():
        return
    try:
        state = _read_state()
        for e in state.get("agent_view", []):
            if e.get("bead") == bead and e.get("session_id") is None:
                e["session_id"] = session_id
                break
        _write_state(state)
    finally:
        _release_lock()


def release_agent_view(bead):
    _ensure_root()
    if not _acquire_lock():
        print("LOCK_TIMEOUT on release — slot may leak until next reap", file=sys.stderr)
        return
    try:
        state = _read_state()
        state["agent_view"] = [e for e in state.get("agent_view", []) if e.get("bead") != bead]
        _write_state(state)
    finally:
        _release_lock()


def claim_codex_glm(kind, label):
    _ensure_root()
    if not _acquire_lock():
        print("LOCK_TIMEOUT", file=sys.stderr)
        return None
    try:
        state = _reap(_read_state())
        av, cg, total = _counts(state)
        if total < TOTAL_CAP:
            token = uuid.uuid4().hex[:12]
            state["codex_glm"].append({"token": token, "kind": kind, "label": label,
                                        "pid": os.getpid(), "claimed_at": _now(), "_ts": time.time()})
            _write_state(state)
            return token
        return None
    finally:
        _release_lock()


def release_codex_glm(token):
    _ensure_root()
    if not _acquire_lock():
        return
    try:
        state = _read_state()
        state["codex_glm"] = [e for e in state.get("codex_glm", []) if e.get("token") != token]
        _write_state(state)
    finally:
        _release_lock()


def enqueue(bead, kind):
    _ensure_root()
    if not _acquire_lock():
        return
    try:
        state = _read_state()
        state["queue"].append({"bead": bead, "kind": kind, "queued_at": _now()})
        _write_state(state)
    finally:
        _release_lock()


def dequeue_next_if_fits():
    """Pop the oldest queued bead ONLY if a slot genuinely fits right now (peek+claim
    atomically under one lock, so a racing claim can't steal the slot between check and
    pop)."""
    _ensure_root()
    if not _acquire_lock():
        return None
    try:
        state = _reap(_read_state())
        if not state.get("queue"):
            return None
        av, cg, total = _counts(state)
        if av < AGENT_VIEW_CAP and total < TOTAL_CAP:
            item = state["queue"].pop(0)
            state["agent_view"].append({"bead": item["bead"], "claimed_at": _now(),
                                         "_ts": time.time(), "session_id": None})
            _write_state(state)
            return item["bead"]
        return None
    finally:
        _release_lock()


def status():
    _ensure_root()
    state = _reap(_read_state())
    av, cg, total = _counts(state)
    print(f"orchestrator: 1 (implicit)")
    print(f"agent_view:   {av}/{AGENT_VIEW_CAP}")
    print(f"codex_glm:    {cg}")
    print(f"TOTAL:        {total}/{TOTAL_CAP}")
    if state["agent_view"]:
        print("\nactive agent_view:")
        for e in state["agent_view"]:
            print(f"  {e['bead']}  session={e.get('session_id')}  since {e['claimed_at']}")
    if state["codex_glm"]:
        print("\nactive codex_glm:")
        for e in state["codex_glm"]:
            print(f"  {e['kind']} pid={e['pid']}  {e['label']}  since {e['claimed_at']}")
    if state["queue"]:
        print(f"\nqueued ({len(state['queue'])}):")
        for i, e in enumerate(state["queue"]):
            print(f"  [{i}] {e['bead']} ({e['kind']})  queued {e['queued_at']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "claim-agent-view":
        ok = claim_agent_view(sys.argv[2])
        print("CLAIMED" if ok else "AT_CAPACITY")
        sys.exit(0 if ok else 2)
    elif cmd == "release-agent-view":
        release_agent_view(sys.argv[2])
        print("RELEASED")
    elif cmd == "attach-session-id":
        attach_session_id(sys.argv[2], sys.argv[3])
        print("ATTACHED")
    elif cmd == "claim-codex-glm":
        token = claim_codex_glm(sys.argv[2], sys.argv[3])
        if token:
            print(token)
            sys.exit(0)
        else:
            print("AT_CAPACITY", file=sys.stderr)
            sys.exit(2)
    elif cmd == "release-codex-glm":
        release_codex_glm(sys.argv[2])
        print("RELEASED")
    elif cmd == "enqueue":
        enqueue(sys.argv[2], sys.argv[3])
        print("QUEUED")
    elif cmd == "dequeue-next":
        bead = dequeue_next_if_fits()
        print(bead or "NONE")
    elif cmd == "status":
        status()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
