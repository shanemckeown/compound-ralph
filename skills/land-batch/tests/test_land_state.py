"""Pytest coverage for /land-batch's cross-session admission state."""

import importlib.util
import os
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "land_batch_state",
    Path(__file__).resolve().parent.parent / "bin" / "land-state.py",
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
