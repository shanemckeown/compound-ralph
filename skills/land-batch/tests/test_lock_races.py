"""Lock and queue races from the z3ykz independent review (LUCY-z3ykz.8).

Each test forces the interleaving the review reproduced and fails on the code at b7e58fa.
Run: uvx pytest skills/land-batch/tests/test_lock_races.py -q
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"
_spec = importlib.util.spec_from_file_location("land_state_races", BIN / "land-state.py")
land_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(land_state)


def _identity(run_id):
    return {"run_id": run_id, "mode": "land", "claude_pid": os.getpid(),
            "pid_start_time": land_state.pid_start_time(os.getpid())}


def _ident(path):
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_dev, st.st_ino)


def _holder_run(state_dir):
    record = land_state.holder_record(state_dir)
    return record.get("run_id") if isinstance(record, dict) else None


# P1-a: release atomicity -------------------------------------------------------------------

def test_second_release_is_a_noop_and_never_touches_a_successor(tmp_path):
    assert land_state.try_acquire_lock(tmp_path, _identity("run-a"))["acquired"] is True
    assert land_state.release_lock(tmp_path, "run-a") is True
    assert land_state.try_acquire_lock(tmp_path, _identity("run-b"))["acquired"] is True
    assert land_state.release_lock(tmp_path, "run-a") is False
    assert _holder_run(tmp_path) == "run-b"


def test_two_releasers_of_one_run_cannot_delete_the_successors_lock(tmp_path, monkeypatch):
    # The review's P1: releaser 2 reads holder=A; meanwhile releaser 1 removes A's lock and B
    # acquires; releaser 2 then rmtree'd B's live LOCK.d. Releaser 1 + B run on a thread that
    # starts at releaser 2's holder read; we give it a second to finish.
    assert land_state.try_acquire_lock(tmp_path, _identity("run-a"))["acquired"] is True
    real = land_state.holder_record
    main = threading.current_thread()
    results = {}

    def other_releaser_then_successor():
        results["release_1"] = land_state.release_lock(tmp_path, "run-a")
        results["b"] = land_state.try_acquire_lock(tmp_path, _identity("run-b"))

    worker = threading.Thread(target=other_releaser_then_successor)

    def holder_record(state_dir=None):
        record = real(state_dir)
        if threading.current_thread() is main and not worker.is_alive() and "started" not in results:
            results["started"] = True
            worker.start()
            worker.join(timeout=1.0)
        return record

    monkeypatch.setattr(land_state, "holder_record", holder_record)
    release_2 = land_state.release_lock(tmp_path, "run-a")
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert [release_2, results["release_1"]].count(True) == 1  # exactly one releaser released
    assert results["b"]["acquired"] is True
    assert _holder_run(tmp_path) == "run-b"  # B's lock survived the second release


def test_release_cli_is_idempotent_exit_zero(tmp_path):
    assert land_state.try_acquire_lock(tmp_path, _identity("run-cli"))["acquired"] is True
    cmd = [sys.executable, str(BIN / "land-state.py"), "--state-dir", str(tmp_path), "release", "--run-id", "run-cli"]
    first = subprocess.run(cmd, capture_output=True, text=True)
    second = subprocess.run(cmd, capture_output=True, text=True)
    assert (first.returncode, json.loads(first.stdout)) == (0, {"released": True})
    assert (second.returncode, json.loads(second.stdout)) == (0, {"released": False})


# P1-b: a holder-less LOCK.d is an acquirer mid-write, not a crash -------------------------

def test_holderless_lock_is_not_stolen_from_a_live_acquirer(tmp_path, monkeypatch):
    # The review's P1: B's recover_stale_lock read A's fresh holder-less LOCK.d as dead, removed
    # it and acquired; A then wrote holder.json into B's dir and both returned acquired=True.
    real_write = land_state.atomic_write_json
    seen = {}

    def write(path, value):
        if Path(path).name == "holder.json" and value.get("run_id") == "run-a" and "b" not in seen:
            seen["b"] = land_state.try_acquire_lock(tmp_path, _identity("run-b"))
        real_write(path, value)

    monkeypatch.setattr(land_state, "atomic_write_json", write)
    a = land_state.try_acquire_lock(tmp_path, _identity("run-a"))
    assert [a["acquired"], seen["b"]["acquired"]] == [True, False]
    assert _holder_run(tmp_path) == "run-a"


def test_acquirer_paused_past_the_grace_is_not_stolen_from(tmp_path, monkeypatch):
    # Astra .8 round 3: A paused >30 s between mkdir and its holder write (a sleeping laptop);
    # B out-graced the holder-less dir, recovered it and acquired, then A resumed and also
    # returned acquired=True. Here A's dir is aged past the grace before B tries.
    real_write = land_state.atomic_write_json
    seen = {}

    def write(path, value):
        if Path(path).name == "holder.json" and value.get("run_id") == "run-a" and "b" not in seen:
            old = time.time() - land_state.HOLDERLESS_GRACE_SECONDS - 5
            os.utime(tmp_path / "LOCK.d", (old, old))
            seen["b"] = land_state.try_acquire_lock(tmp_path, _identity("run-b"))
        real_write(path, value)

    monkeypatch.setattr(land_state, "atomic_write_json", write)
    a = land_state.try_acquire_lock(tmp_path, _identity("run-a"))
    # B's recovery cannot take the guard A holds (non-blocking), so it sees LOCK.d as held.
    # Before the fix B stole it, and A's holder then overwrote B's: a lock nobody could release.
    assert [a["acquired"], seen["b"]["acquired"]] == [True, False]
    assert _holder_run(tmp_path) == "run-a"


def test_concurrent_acquirers_with_a_slow_holder_write_admit_exactly_one(tmp_path, monkeypatch):
    real_write = land_state.atomic_write_json

    def slow_holder(path, value):
        if Path(path).name == "holder.json" and Path(path).parent.name == "LOCK.d":
            time.sleep(0.3)
        real_write(path, value)

    monkeypatch.setattr(land_state, "atomic_write_json", slow_holder)
    start = threading.Barrier(6)
    outcomes = []

    def contend(n):
        start.wait()
        for _ in range(10):  # keep trying through the slow write window
            outcomes.append(land_state.try_acquire_lock(tmp_path, _identity(f"run-{n}")))
            time.sleep(0.05)

    threads = [threading.Thread(target=contend, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    winners = [o["holder"]["run_id"] for o in outcomes if o["acquired"]]
    assert len(winners) == 1, winners
    assert _holder_run(tmp_path) == winners[0]


def test_recoverer_never_retires_a_fresh_lock_made_after_a_release(tmp_path, monkeypatch):
    # Astra .8 round 1: B passes recover_stale_lock's unlocked is_dir check on A's lock; A
    # releases; B takes the recovery lock and finds no LOCK.d; A2 mkdirs a fresh holder-less
    # one; B's retire matched holder None == None and deleted A2's dir, so both were admitted.
    # Here A2's mkdir lands at B's first look inside the recovery lock.
    assert land_state.try_acquire_lock(tmp_path, _identity("run-a"))["acquired"] is True
    lock = tmp_path / "LOCK.d"
    real_acquire, real_init = land_state._acquire_recovery_lock, land_state._initialising
    real_dir_id = getattr(land_state, "_dir_id", None) or (lambda path: _ident(path))
    state = {"releasing": False, "released": False, "a2": None}

    def acquire(p):
        if not state["releasing"]:
            state["releasing"] = True
            assert land_state.release_lock(tmp_path, "run-a") is True
            state["released"] = True
        return real_acquire(p)

    def a2_arrives():
        if state["released"] and state["a2"] is None:
            lock.mkdir()
            state["a2"] = real_dir_id(lock)

    def dir_id(path):  # the new code's first look under the recovery lock
        found = real_dir_id(path)
        if Path(path) == lock:
            a2_arrives()
        return found

    def initialising(path):  # the old code's first look at a missing LOCK.d
        found = real_init(path)
        if Path(path) == lock:
            a2_arrives()
        return found

    monkeypatch.setattr(land_state, "_acquire_recovery_lock", acquire)
    monkeypatch.setattr(land_state, "_dir_id", dir_id, raising=False)
    monkeypatch.setattr(land_state, "_initialising", initialising)
    b = land_state.try_acquire_lock(tmp_path, _identity("run-b"))
    assert state["a2"] is not None
    assert b["acquired"] is False  # A2 made LOCK.d first; B must not take it from under it
    assert real_dir_id(lock) == state["a2"]


def test_guards_are_flocks_freed_by_their_holders_death(tmp_path):
    # Astra .8 round 2: a poller dying inside the old mkdir recovery guard left a dir two
    # releasers could both steal. A flock has no stale owner: the kernel frees it on exit.
    for guard in ("RECOVERY.lock", "COUNTER.lock"):
        holder = subprocess.Popen([sys.executable, "-c", (
            "import fcntl,sys,time; f=open(sys.argv[1],'a'); fcntl.flock(f, fcntl.LOCK_EX); "
            "print('held', flush=True); time.sleep(60)"), str(tmp_path / guard)],
            stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout.readline().strip() == "held"
            assert land_state._flock(tmp_path / guard, block=False) is None  # live holder: busy
        finally:
            holder.kill()
            holder.wait()
        handle = land_state._flock(tmp_path / guard, block=False)  # dead holder: free at once
        assert handle is not None
        handle.close()
        assert (tmp_path / guard).is_file()  # never unlinked


def test_release_waits_for_a_live_recovery_and_then_releases(tmp_path):
    assert land_state.try_acquire_lock(tmp_path, _identity("run-r"))["acquired"] is True
    guard = land_state._acquire_recovery_lock(land_state.paths(tmp_path))
    result = {}
    releaser = threading.Thread(target=lambda: result.update(r=land_state.release_lock(tmp_path, "run-r")))
    releaser.start()
    releaser.join(timeout=0.5)
    assert releaser.is_alive() and (tmp_path / "LOCK.d").is_dir()  # held off by the guard
    guard.close()
    releaser.join(timeout=10)
    assert result == {"r": True} and not (tmp_path / "LOCK.d").exists()


def test_holderless_lock_past_the_grace_is_recovered(tmp_path):
    (tmp_path / "LOCK.d").mkdir(parents=True)  # a crash between mkdir and the holder write
    old = time.time() - land_state.HOLDERLESS_GRACE_SECONDS - 5
    os.utime(tmp_path / "LOCK.d", (old, old))
    got = land_state.try_acquire_lock(tmp_path, _identity("run-heal"))
    assert got["acquired"] is True and got["takeover"]["liveness"]["state"] == "invalid"


def test_holderless_lock_inside_the_grace_is_held(tmp_path):
    (tmp_path / "LOCK.d").mkdir(parents=True)
    got = land_state.try_acquire_lock(tmp_path, _identity("run-wait"))
    assert got["acquired"] is False and got["reason"] == "held"
    assert (tmp_path / "LOCK.d").is_dir()


# P2-a: register_ticket after withdraw -----------------------------------------------------

def _withdraw_after_first_check(monkeypatch, state_dir, run_id):
    real = land_state.is_withdrawn
    calls = {"n": 0}

    def racing(p, rid):
        calls["n"] += 1
        if calls["n"] == 1:  # the unlocked pre-check passes; withdraw lands right after it
            assert land_state.withdraw_ticket(state_dir, run_id, run_id)["withdrawn"] is True
            return False
        return real(p, rid)

    monkeypatch.setattr(land_state, "is_withdrawn", racing)


def test_register_ticket_never_recreates_a_withdrawn_ticket(tmp_path, monkeypatch):
    identity = _identity("run-w")
    land_state.register_ticket(tmp_path, identity)
    _withdraw_after_first_check(monkeypatch, tmp_path, "run-w")
    assert land_state.register_ticket(tmp_path, identity) == {"run_id": "run-w", "withdrawn": True}
    assert list((tmp_path / "QUEUE.d").glob("*run-w.json")) == []


def test_wait_for_turn_exits_withdrawn_without_leaving_a_ticket(tmp_path, monkeypatch):
    assert land_state.try_acquire_lock(tmp_path, _identity("run-holder"))["acquired"] is True
    identity = _identity("run-w2")
    land_state.register_ticket(tmp_path, identity)
    real_register = land_state.register_ticket

    def register(state_dir, ident):
        _withdraw_after_first_check(monkeypatch, tmp_path, ident["run_id"])
        return real_register(state_dir, ident)

    monkeypatch.setattr(land_state, "register_ticket", register)
    got = land_state.wait_for_turn(tmp_path, identity, poll_min=0, poll_max=0, sleep=lambda _: None)
    assert got == {"acquired": False, "reason": "withdrawn"}
    assert list((tmp_path / "QUEUE.d").glob("*run-w2.json")) == []
    assert _holder_run(tmp_path) == "run-holder"
