"""Pytest coverage for /land-batch's cross-session admission state."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


LAND_STATE_PATH = Path(__file__).resolve().parent.parent / "bin" / "land-state.py"
SPEC = importlib.util.spec_from_file_location(
    "land_batch_state",
    LAND_STATE_PATH,
)
land_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(land_state)


def _identity(run_id, mode="land"):
    return {
        "run_id": run_id,
        "mode": mode,
        "claude_pid": os.getpid(),
        "pid_start_time": land_state.pid_start_time(os.getpid()),
        "session_id": "test-session",
        "agent_view_name": "test agent",
        "stage": "preflight",
        "integration_branch": None,
        "scratch_path": None,
        "evidence_dir": None,
    }


def _wait_process(state_dir, identity):
    """Run the CLI wait command with a short poll interval for subprocess tests."""
    return subprocess.Popen(
        [
            sys.executable,
            str(LAND_STATE_PATH),
            "--state-dir",
            str(state_dir),
            "wait",
            "--run-id",
            identity["run_id"],
            "--mode",
            identity["mode"],
            "--claude-pid",
            str(identity["claude_pid"]),
            "--pid-start-time",
            identity["pid_start_time"] or "",
            "--stage",
            identity["stage"],
            "--poll-min",
            "0.01",
            "--poll-max",
            "0.01",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_lock_acquire_heartbeat_and_release(tmp_path):
    state_dir = tmp_path / "state"
    identity = _identity("run-lock")

    acquired = land_state.try_acquire_lock(state_dir, identity)

    assert acquired["acquired"] is True
    holder = land_state.heartbeat(state_dir, "run-lock", "discovery")
    assert holder["stage"] == "discovery"
    assert land_state.release_lock(state_dir, "run-lock") is True
    assert land_state.status_payload(state_dir)["lock"] is None


def test_dead_holder_is_forensically_taken_over_and_recorded_in_ledger(tmp_path):
    state_dir = tmp_path / "state"
    p = land_state.ensure_state_dir(state_dir)
    p["lock"].mkdir()
    land_state.atomic_write_json(
        p["holder"],
        {
            "run_id": "dead-run",
            "mode": "land",
            "claude_pid": 999_999_999,
            "pid_start_time": "Thu Jan  1 00:00:00 1970",
            "stage": "scoped-gate",
            "heartbeat_at": "2026-07-21T10:00:00Z",
            "scratch_path": "/tmp/land-batch-dead-run",
            "integration_branch": "land-batch/dead-run",
            "evidence_dir": "/tmp/evidence/dead-run",
        },
    )

    acquired = land_state.try_acquire_lock(state_dir, _identity("takeover-run"))

    assert acquired["acquired"] is True
    assert acquired["takeover"]["dead_run"]["run_id"] == "dead-run"
    assert land_state.holder_record(state_dir)["run_id"] == "takeover-run"
    assert land_state.read_ledger(state_dir)["takeovers"][0]["dead_run"]["stage"] == "scoped-gate"


def test_queue_tickets_are_fifo_across_simulated_sessions(tmp_path):
    state_dir = tmp_path / "state"
    first = _identity("first-run")
    second = _identity("second-run", mode="ship")

    first_ticket = land_state.register_ticket(state_dir, first)
    second_ticket = land_state.register_ticket(state_dir, second)

    assert first_ticket["order"] < second_ticket["order"]
    assert [entry["run_id"] for entry in land_state.queue_entries(state_dir)] == ["first-run", "second-run"]
    assert land_state.try_acquire_lock(state_dir, second)["reason"] == "waiting-for-earlier-ticket"
    assert land_state.try_acquire_lock(state_dir, first)["acquired"] is True
    assert land_state.release_lock(state_dir, "first-run") is True
    assert land_state.try_acquire_lock(state_dir, second)["acquired"] is True


def test_killed_wait_cli_resumes_same_ticket_and_fifo_position(tmp_path):
    state_dir = tmp_path / "state"
    holder = _identity("current-holder")
    earlier = _identity("earlier-ticket")
    resumed = _identity("resumed-run")
    first_wait = None
    resumed_wait = None

    try:
        assert land_state.try_acquire_lock(state_dir, holder)["acquired"] is True
        earlier_ticket = land_state.register_ticket(state_dir, earlier)

        first_wait = _wait_process(state_dir, resumed)
        assert _wait_until(
            lambda: any(entry.get("run_id") == resumed["run_id"] for entry in land_state.queue_entries(state_dir))
        )
        initial_tickets = [
            entry for entry in land_state.queue_entries(state_dir) if entry.get("run_id") == resumed["run_id"]
        ]
        assert len(initial_tickets) == 1
        initial_order = initial_tickets[0]["order"]
        assert initial_order > earlier_ticket["order"]

        first_wait.kill()
        first_wait.communicate(timeout=2)

        # The caller's Claude PID is still alive, so the ticket survives the
        # killed tool subprocess and a new wait worker must reuse it.
        resumed_wait = _wait_process(state_dir, resumed)
        assert _wait_until(
            lambda: len(
                [entry for entry in land_state.queue_entries(state_dir) if entry.get("run_id") == resumed["run_id"]]
            ) == 1
        )
        restarted_ticket = next(
            entry for entry in land_state.queue_entries(state_dir) if entry.get("run_id") == resumed["run_id"]
        )
        assert restarted_ticket["order"] == initial_order

        # Let the earlier queued run take its turn, then the restarted wait
        # process must acquire the lock and print the admission JSON.
        assert land_state.release_lock(state_dir, holder["run_id"]) is True
        assert land_state.try_acquire_lock(state_dir, earlier)["acquired"] is True
        assert land_state.release_lock(state_dir, earlier["run_id"]) is True
        stdout, stderr = resumed_wait.communicate(timeout=2)
        assert resumed_wait.returncode == 0, stderr
        assert json.loads(stdout)["acquired"] is True
        assert land_state.holder_record(state_dir)["run_id"] == resumed["run_id"]
        assert not any(entry.get("run_id") == resumed["run_id"] for entry in land_state.queue_entries(state_dir))
        assert land_state.release_lock(state_dir, resumed["run_id"]) is True
    finally:
        for process in (first_wait, resumed_wait):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=2)


def test_ledger_read_write_round_trip(tmp_path):
    state_dir = tmp_path / "state"
    record = {
        "run_id": "land-run",
        "base_sha": "abc123",
        "features": [
            {
                "branch": "fix/qa-ledger",
                "merge_sha": "def456",
                "sources": {
                    "session_tail": ["Built the settings control."],
                    "bead_acceptance": ["Persist the selected value."],
                    "diffstat": " app/settings.tsx | 12 ++++++++++++",
                },
                "checks": [{"expected": "Selection persists after refresh."}],
            }
        ],
    }

    land_state.append_ledger(state_dir, record, "# Batch land-run\n\n- Verify settings persist.")

    ledger = land_state.read_ledger(state_dir)
    assert ledger["pending"] == [
        {
            "run_id": "land-run",
            "base_sha": "abc123",
            "branch": "fix/qa-ledger",
                "merge_sha": "def456",
                "sources": record["features"][0]["sources"],
                "checks": record["features"][0]["checks"],
                "checklist_markdown": "# Batch land-run\n\n- Verify settings persist.",
                "landed_at": ledger["pending"][0]["landed_at"],
        }
    ]
    assert "Verify settings persist." in (state_dir / "pending-qa.md").read_text(encoding="utf-8")


def test_prod_archive_preserves_pending_evidence_then_resets_ledger(tmp_path):
    state_dir = tmp_path / "state"
    evidence_dir = tmp_path / "evidence"
    land_state.append_ledger(
        state_dir,
        {"features": [{"branch": "fix/archive", "merge_sha": "merge-1", "checks": [{"expected": "works"}]}]},
        "# Pending archive check",
    )

    reset = land_state.archive_ledger_after_prod(state_dir, evidence_dir, "prod-123")

    archived = land_state.read_json(evidence_dir / "ledger.json")
    assert archived["pending"][0]["merge_sha"] == "merge-1"
    assert "Pending archive check" in (evidence_dir / "pending-qa.md").read_text(encoding="utf-8")
    assert reset["prod_sha"] == "prod-123"
    assert land_state.read_ledger(state_dir)["pending"] == []
