"""Land trains + withdraw (LUCY-z3ykz.4).
Run: uvx --python 3.13 pytest ~/.claude/skills/land-batch/tests/test_land_trains.py -q"""
import importlib.util
import json
import os
import subprocess
import sys
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
CONFIG = trains.load()


def at(hh, mm, day=14, month=10):
    return datetime(2026, month, day, hh, mm, tzinfo=LONDON)


# ------------------------------------------------------------------ window check
@pytest.mark.parametrize("when,train", [(at(12, 30), "midday"), (at(13, 29), "midday"),
                                        (at(18, 30), "pre-deploy"), (at(18, 59), "pre-deploy")])
def test_inside_window_allowed(when, train):
    assert trains.check(CONFIG, when) == {"allowed": True, "train": train}


@pytest.mark.parametrize("when", [at(9, 0), at(12, 29), at(13, 30), at(19, 0), at(23, 0)])
def test_outside_window_refused_and_names_next_train(when):
    result = trains.check(CONFIG, when)
    assert result["allowed"] is False and result["next_train"] in ("midday", "pre-deploy")


def test_next_train_rolls_to_tomorrow_after_last_window():
    result = trains.check(CONFIG, at(21, 0))
    assert result["next_train"] == "midday" and result["next_opens"].startswith("2026-10-15T12:30")


def test_now_with_reason_allowed_and_recorded():
    result = trains.check(CONFIG, at(9, 0), land_now=True, reason="P0: NY Skin booking down")
    assert result == {"allowed": True, "train": "now", "reason": "P0: NY Skin booking down"}


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_now_without_reason_refused(reason):
    result = trains.check(CONFIG, at(9, 0), land_now=True, reason=reason)
    assert result["allowed"] is False and "--reason" in result["why"]


def test_window_is_london_time_across_utc_offsets():
    # 11:45 UTC in October (BST) is 12:45 London: inside midday. In December (GMT) it is not.
    assert trains.current_train(CONFIG, datetime(2026, 10, 14, 11, 45, tzinfo=ZoneInfo("UTC"))) == "midday"
    assert trains.current_train(CONFIG, datetime(2026, 12, 14, 11, 45, tzinfo=ZoneInfo("UTC"))) is None


def test_pre_deploy_train_closes_before_the_evening_deploy():
    deploy = at(*(int(x) for x in CONFIG["deploy_not_before"].split(":")))
    for name, _, closes in trains.windows(CONFIG, at(0, 0).date()):
        assert (deploy - closes).total_seconds() >= 30 * 60, name


def test_check_cli_exit_codes():
    def run(*args):
        return subprocess.run([sys.executable, str(BIN / "trains.py"), "check", *args],
                              capture_output=True, text=True)
    inside = run("--at", "2026-10-14T12:45:00+01:00")
    assert inside.returncode == 0 and json.loads(inside.stdout)["train"] == "midday"
    assert run("--at", "2026-10-14T09:00:00+01:00").returncode == 1
    assert run("--at", "2026-10-14T09:00:00+01:00", "--now", "--reason", "P0").returncode == 0


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


def test_announce_relays_once_per_train_per_day(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(trains.subprocess, "run",
                        lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0))
    log = tmp_path / "land-train.log"
    assert trains.announce(CONFIG, at(9, 0), log=log) == 0 and calls == []
    trains.announce(CONFIG, at(12, 31), log=log)
    trains.announce(CONFIG, at(12, 46), log=log)
    trains.announce(CONFIG, at(18, 31), log=log)
    trains.announce(CONFIG, at(12, 31, day=15), log=log)
    assert len(calls) == 3


def test_announce_failure_is_retried_next_tick(tmp_path, monkeypatch):
    codes = iter([3, 0])
    monkeypatch.setattr(trains.subprocess, "run",
                        lambda args, **kw: subprocess.CompletedProcess(args, next(codes)))
    log = tmp_path / "land-train.log"
    assert trains.announce(CONFIG, at(12, 31), log=log) == 3
    assert trains.announce(CONFIG, at(12, 46), log=log) == 0
    assert "train=midday relay_exit=0" in log.read_text()


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
