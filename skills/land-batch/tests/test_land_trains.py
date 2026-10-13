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
