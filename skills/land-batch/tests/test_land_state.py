"""Pytest coverage for /land-batch's cross-session admission state."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time


LAND_STATE_PATH = Path(__file__).resolve().parent.parent / "bin" / "land-state.py"
VERIFY_PINNED_SOURCE = Path(__file__).resolve().parent.parent / "bin" / "verify-pinned-source.sh"
SPEC = importlib.util.spec_from_file_location(
    "land_batch_state",
    LAND_STATE_PATH,
)
land_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(land_state)


def _run(args, cwd=None, check=True):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def _git(repo, *args, check=True):
    return _run(["git", "-C", str(repo), *args], check=check)


def _init_git_repo(path, bare=False):
    args = ["git", "init"]
    if bare:
        args.append("--bare")
    else:
        args.extend(["-b", "main"])
    args.append(str(path))
    _run(args)
    if not bare:
        _git(path, "config", "user.email", "test@example.com")
        _git(path, "config", "user.name", "Test User")


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


def test_remote_only_pinned_sha_merges_and_appends_source_evidence_to_ledger(tmp_path):
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    feature = tmp_path / "feature"
    scratch = tmp_path / "scratch"
    state_dir = tmp_path / "state"
    _init_git_repo(origin, bare=True)
    _init_git_repo(repo)
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")

    _git(repo, "worktree", "add", "-b", "goal/aestheticcnext-pin01", str(feature))
    (feature / "remote-only.txt").write_text("pinned work\n", encoding="utf-8")
    _git(feature, "add", ".")
    _git(feature, "commit", "-m", "remote-only feature")
    source_sha = _git(feature, "rev-parse", "HEAD").stdout.strip()
    source_ref = "refs/remotes/origin/goal/aestheticcnext-pin01"
    _git(feature, "push", "origin", "HEAD:refs/heads/goal/aestheticcnext-pin01")
    _git(repo, "worktree", "remove", "--force", str(feature))
    _git(repo, "branch", "-D", "goal/aestheticcnext-pin01")
    _git(repo, "fetch", "origin")

    verified = _run(["bash", str(VERIFY_PINNED_SOURCE), str(repo), source_ref, source_sha])
    assert verified.stdout.strip() == source_sha

    _git(repo, "worktree", "add", "-b", "land-batch/integration-test", str(scratch), "origin/main")
    _git(scratch, "merge", "--no-ff", "--no-verify", source_sha, "-m", "land pinned remote source")
    merge_sha = _git(scratch, "rev-parse", "HEAD").stdout.strip()
    assert (scratch / "remote-only.txt").read_text(encoding="utf-8") == "pinned work\n"

    record = {
        "run_id": "pinned-integration",
        "features": [
            {
                "branch": "goal/aestheticcnext-pin01",
                "source_kind": "remote-branch",
                "source_ref": source_ref,
                "source_sha": source_sha,
                "merge_sha": merge_sha,
                "checks": [{"expected": "Pinned remote-only content is present."}],
            }
        ],
    }
    land_state.append_ledger(state_dir, record, "- Verify pinned remote-only content.")
    pending = land_state.read_ledger(state_dir)["pending"]

    assert pending[0]["source_kind"] == "remote-branch"
    assert pending[0]["source_ref"] == source_ref
    assert pending[0]["source_sha"] == source_sha
    assert pending[0]["merge_sha"] == merge_sha


def test_pinned_source_verifier_holds_when_remote_tracking_ref_moves(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "app.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    old_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    source_ref = "refs/remotes/origin/goal/aestheticcnext-move1"
    _git(repo, "update-ref", source_ref, old_sha)
    (repo / "app.txt").write_text("moved\n", encoding="utf-8")
    _git(repo, "commit", "-am", "move source")
    new_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", source_ref, new_sha)

    result = _run(
        ["bash", str(VERIFY_PINNED_SOURCE), str(repo), source_ref, old_sha],
        check=False,
    )

    assert result.returncode == 3
    assert f"was {old_sha} now {new_sha}; rediscover" in result.stderr


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


def test_kickback_record_round_trip_increments_attempt_and_keeps_evidence_history(tmp_path):
    state_dir = tmp_path / "state"
    first = land_state.record_kickback(
        state_dir,
        "fix/original",
        {
            "bead_id": "AestheticcNext-kb01",
            "session_name": "kickback-AestheticcNext-kb01",
            "failure_summary": "src/form.tsx",
            "signature": "files=src/form.tsx;tests=-",
            "evidence_path": "/evidence/first/scoped-gate.log",
        },
    )
    second = land_state.record_kickback(
        state_dir,
        "fix/original",
        {
            "bead_id": "AestheticcNext-kb02",
            "session_name": "kickback-AestheticcNext-kb02",
            "failure_summary": "tests/form.test.ts",
            "signature": "files=tests/form.test.ts;tests=submit saves",
            "evidence_path": "/evidence/second/scoped-gate.log",
        },
    )

    stored = land_state.read_kickbacks(state_dir)["lineages"]["fix/original"]
    assert first["attempt"] == 1
    assert second["attempt"] == 2
    assert stored["bead_id"] == "AestheticcNext-kb02"
    assert stored["evidence_path"] == "/evidence/second/scoped-gate.log"
    assert stored["history"][0]["evidence_path"] == "/evidence/first/scoped-gate.log"


def test_kickback_attempt_cap_and_signature_backstop():
    first = land_state.kickback_attempt_decision(None, "files=a.ts;tests=-")
    same = land_state.kickback_attempt_decision(
        {"attempt": 1, "signature": "files=a.ts;tests=-", "history": []},
        "files=a.ts;tests=-",
    )
    changed = land_state.kickback_attempt_decision(
        {"attempt": 1, "signature": "files=a.ts;tests=-", "history": []},
        "files=b.ts;tests=-",
    )
    cap = land_state.kickback_attempt_decision(
        {"attempt": 3, "signature": "files=c.ts;tests=-", "history": []},
        "files=d.ts;tests=-",
    )

    assert first == {"dispatch": True, "reason": "first-attempt", "attempt": 1}
    assert same["dispatch"] is False
    assert same["reason"] == "signature-unchanged"
    assert changed == {"dispatch": True, "reason": "signature-changed", "attempt": 2}
    assert cap["dispatch"] is False
    assert cap["reason"] == "attempt-cap-exceeded"


def test_kickback_classification_holds_baseline_red_and_accepts_branch_introduced_tsc_error():
    baseline_red = land_state.classify_kickback(
        baseline_exit_code=1,
        baseline_artifacts_valid=True,
        gate_exit_code=1,
        gate_artifacts_valid=True,
        gate_output="src/widget.ts(8,4): error TS2322: Type 'string' is not assignable",
        feature_changed_files=["src/widget.ts"],
    )
    baseline_green = land_state.classify_kickback(
        baseline_exit_code=0,
        baseline_artifacts_valid=True,
        gate_exit_code=1,
        gate_artifacts_valid=True,
        gate_output="src/widget.ts(8,4): error TS2322: Type 'string' is not assignable",
        feature_changed_files=["src/widget.ts"],
    )

    assert baseline_red == {"dispatch": False, "reason": "baseline-red"}
    assert baseline_green["dispatch"] is True
    assert baseline_green["reason"] == "branch-introduced-deterministic-code-failure"
    assert baseline_green["signature"] == "files=src/widget.ts;tests=-"


def test_kickback_status_marks_in_flight_ready_and_stalled_without_retrying(tmp_path):
    state_dir = tmp_path / "state"
    land_state.record_kickback(
        state_dir,
        "fix/stalled",
        {
            "bead_id": "AestheticcNext-stall",
            "session_name": "kickback-stall",
            "failure_summary": "src/a.ts",
            "signature": "files=src/a.ts;tests=-",
        },
    )
    land_state.record_kickback(
        state_dir,
        "fix/ready",
        {
            "bead_id": "AestheticcNext-ready",
            "session_name": "kickback-ready",
            "fix_branch": "goal/aestheticcnext-ready",
            "failure_summary": "src/b.ts",
            "signature": "files=src/b.ts;tests=-",
        },
    )

    stalled = land_state.kickback_status(state_dir, active_session_names=set())
    statuses = land_state.kickback_status(state_dir, active_session_names={"kickback-stall"})

    assert stalled["lineages"]["fix/stalled"]["state"] == "stalled"
    assert statuses["lineages"]["fix/stalled"]["state"] == "in-flight"
    assert statuses["lineages"]["fix/ready"]["state"] == "ready"
