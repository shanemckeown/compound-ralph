"""Land trains (windows retired, LUCY-x0mop) + withdraw (LUCY-z3ykz.4).
Run: uvx --python 3.13 pytest ~/.claude/skills/land-batch/tests/test_land_trains.py -q"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

BIN = Path(__file__).resolve().parent.parent / "bin"


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trains = _load("land_trains", "trains.py")
land_state = _load("land_state_withdraw", "land-state.py")
LONDON = ZoneInfo("Europe/London")
CONFIG = json.loads((BIN.parent / "TRAINS.json").read_text())


def at(hh, mm, day=14, month=10):
    return datetime(2026, month, day, hh, mm, tzinfo=LONDON)


# ------------------------------------------------------------------ no clock windows
@pytest.mark.parametrize("when", [at(3, 0), at(9, 0), at(12, 29), at(19, 0), at(21, 0), at(23, 0)])
def test_check_always_allowed(when):
    assert trains.check(CONFIG, when) == {"allowed": True, "train": "open"}


def test_now_and_reason_are_noops_but_reason_is_echoed():
    assert trains.check(CONFIG, at(9, 0), land_now=True) == {"allowed": True, "train": "open"}
    assert trains.check(CONFIG, at(9, 0), land_now=True, reason="P0")["reason"] == "P0"


def test_check_cli_exits_0_at_any_hour_without_now():
    def run(*args):
        return subprocess.run([sys.executable, str(BIN / "trains.py"), "check", *args],
                              capture_output=True, text=True)
    for hhmm in ("03:00", "21:00"):
        out = run("--at", f"2026-10-14T{hhmm}:00+01:00")
        assert out.returncode == 0 and json.loads(out.stdout)["allowed"] is True
    assert run("--now", "--reason", "P0").returncode == 0
    assert run("--now").returncode == 0


def test_announce_is_a_noop():
    assert trains.announce() == 0


def test_trains_json_keeps_deploy_embargo_only():
    assert CONFIG["deploy_not_before"] == "19:30" and CONFIG["trains"] == []


# ---------------------------------------------------------------------- withdraw
def _ticket(state, run_id, pid):
    return land_state.register_ticket(state, {"run_id": run_id, "mode": "land", "claude_pid": pid,
                                              "pid_start_time": land_state.pid_start_time(pid)})


def test_withdraw_removes_own_ticket_and_logs_it(tmp_path):
    mine = _ticket(tmp_path, "run-mine", os.getpid())
    result = land_state.withdraw_ticket(tmp_path, mine["ticket_file"], "run-mine")
    assert result == {"withdrawn": True, "ticket_file": mine["ticket_file"]}
    assert land_state.queue_entries(tmp_path) == []
    log = [json.loads(l) for l in (tmp_path / "withdrawals.jsonl").read_text().splitlines()]
    assert log[0]["run_id"] == "run-mine" and log[0]["ticket_file"] == mine["ticket_file"]


def test_withdraw_by_run_id_works(tmp_path):
    _ticket(tmp_path, "run-mine", os.getpid())
    assert land_state.withdraw_ticket(tmp_path, "run-mine", "run-mine")["withdrawn"] is True


def test_withdraw_refuses_another_runs_ticket(tmp_path):
    theirs = _ticket(tmp_path, "run-theirs", os.getpid())
    result = land_state.withdraw_ticket(tmp_path, theirs["ticket_file"], "run-mine")
    assert result["withdrawn"] is False and result["reason"] == "not-own-ticket"
    assert [e["run_id"] for e in land_state.queue_entries(tmp_path)] == ["run-theirs"]
    assert not (tmp_path / "withdrawals.jsonl").exists()


def test_withdraw_unknown_ticket_and_cli_exit_code(tmp_path):
    out = subprocess.run([sys.executable, str(BIN / "land-state.py"), "--state-dir", str(tmp_path),
                          "withdraw", "nope", "--run-id", "run-mine"], capture_output=True, text=True)
    assert out.returncode == 1 and json.loads(out.stdout)["reason"] == "no-such-ticket"


def test_withdrawn_run_cannot_requeue_or_acquire(tmp_path):
    # Review round 1: the detached wait worker wakes after withdraw and re-registers.
    identity = {"run_id": "run-mine", "mode": "land", "claude_pid": os.getpid(),
                "pid_start_time": land_state.pid_start_time(os.getpid())}
    land_state.register_ticket(tmp_path, identity)
    assert land_state.withdraw_ticket(tmp_path, "run-mine", "run-mine")["withdrawn"] is True
    assert land_state.register_ticket(tmp_path, identity).get("withdrawn") is True
    assert land_state.queue_entries(tmp_path) == []
    assert land_state.try_acquire_lock(tmp_path, identity) == {"acquired": False, "reason": "withdrawn"}
    assert not (tmp_path / "LOCK.d").exists()
    slept = []
    result = land_state.wait_for_turn(tmp_path, identity, poll_min=0, poll_max=0, sleep=slept.append)
    assert result["reason"] == "withdrawn" and slept == []


def test_wait_cli_exits_4_with_empty_stdout_when_withdrawn(tmp_path):
    land_state.register_ticket(tmp_path, {"run_id": "run-x", "mode": "land", "claude_pid": os.getpid(),
                                          "pid_start_time": land_state.pid_start_time(os.getpid())})
    land_state.withdraw_ticket(tmp_path, "run-x", "run-x")
    out = subprocess.run([sys.executable, str(BIN / "land-state.py"), "--state-dir", str(tmp_path), "wait",
                          "--run-id", "run-x", "--mode", "land", "--claude-pid", str(os.getpid()),
                          "--poll-min", "0", "--poll-max", "0"], capture_output=True, text=True)
    assert out.returncode == 4 and out.stdout == "" and "withdrawn" in out.stderr


def _identity(run_id):
    return {"run_id": run_id, "mode": "land", "claude_pid": os.getpid(),
            "pid_start_time": land_state.pid_start_time(os.getpid())}


def test_withdraw_landing_between_check_and_mkdir_still_wins(tmp_path, monkeypatch):
    # Astra round 2: withdraw after the first is_withdrawn check let the worker take LOCK.d.
    identity = _identity("run-race")
    land_state.register_ticket(tmp_path, identity)
    real = land_state.is_withdrawn
    calls = {"n": 0}

    def racing(p, run_id):
        calls["n"] += 1
        if calls["n"] == 1:          # the pre-check: withdraw has not happened yet
            assert land_state.withdraw_ticket(tmp_path, run_id, run_id)["withdrawn"] is True
            return False
        return real(p, run_id)

    monkeypatch.setattr(land_state, "is_withdrawn", racing)
    assert land_state.try_acquire_lock(tmp_path, identity) == {"acquired": False, "reason": "withdrawn"}
    assert not (tmp_path / "LOCK.d").exists()


def test_withdraw_reports_lock_held_when_admitted_in_the_same_instant(tmp_path, monkeypatch):
    identity = _identity("run-same")
    land_state.register_ticket(tmp_path, identity)
    real = land_state.holder_record
    reads = {"n": 0}

    def holder(state_dir=None):
        reads["n"] += 1
        return {"run_id": "run-same"} if reads["n"] > 1 else real(state_dir)

    monkeypatch.setattr(land_state, "holder_record", holder)
    result = land_state.withdraw_ticket(tmp_path, "run-same", "run-same")
    assert result["withdrawn"] is True and result["lock_held"] is True


def test_withdrawn_back_out_never_removes_a_successors_lock(tmp_path, monkeypatch):
    # Astra round 3: A withdraws (lock_held), A's caller releases, B acquires, then A's worker
    # reaches its post-holder re-check. It must leave B's LOCK.d alone.
    # Since .8 A writes its holder inside the recovery guard, so the caller's release (another
    # process in real use) runs concurrently and waits for that guard, as it would for real.
    a, b = _identity("run-a"), _identity("run-b")
    land_state.register_ticket(tmp_path, a)
    real_write = land_state.atomic_write_json
    got = {}

    def caller_releases_then_b_acquires():
        got["release"] = land_state.release_lock(tmp_path, "run-a")
        got["b"] = land_state.try_acquire_lock(tmp_path, b)

    caller = threading.Thread(target=caller_releases_then_b_acquires)

    def write_then_interleave(path, value):
        real_write(path, value)
        if Path(path).name == "holder.json" and value.get("run_id") == "run-a" and "started" not in got:
            got["started"] = True
            # withdraw marked A after its first holder read (so it reported lock_held) ...
            (tmp_path / "WITHDRAWN.d").mkdir(exist_ok=True)
            (tmp_path / "WITHDRAWN.d" / "run-a").write_text("x")
            for ticket in (tmp_path / "QUEUE.d").glob("*run-a.json"):
                ticket.unlink()
            # ... and its caller followed SKILL.md and released.
            monkeypatch.setattr(land_state, "atomic_write_json", real_write)
            caller.start()

    monkeypatch.setattr(land_state, "atomic_write_json", write_then_interleave)
    assert land_state.try_acquire_lock(tmp_path, a)["reason"] == "withdrawn"
    caller.join(timeout=30)
    assert not caller.is_alive()
    assert got["b"]["acquired"] is True, got
    assert land_state.holder_record(tmp_path)["run_id"] == "run-b"
