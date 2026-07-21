#!/usr/bin/env python3
"""Atomic lock, FIFO queue, and pending-QA ledger for /land-batch.

The directory mutex is deliberately boring: ``mkdir LOCK.d`` is the only lock
acquisition primitive.  Queue ordering is a courtesy layered on top of that
atomic mutex, not a replacement for it.  This module uses only the standard
library so discovery, status, and tests all share the same state contract.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


POLL_MIN_SECONDS = 120
POLL_MAX_SECONDS = 180


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def state_dir_from_env() -> Path:
    configured = os.environ.get("LAND_BATCH_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude" / "state" / "land-batch"


def paths(state_dir: Path | None = None) -> dict[str, Path]:
    root = state_dir or state_dir_from_env()
    return {
        "root": root,
        "lock": root / "LOCK.d",
        "holder": root / "LOCK.d" / "holder.json",
        "queue": root / "QUEUE.d",
        "counter": root / "queue-counter",
        "counter_lock": root / "COUNTER-LOCK.d",
        "ledger": root / "ledger.json",
        "pending_qa": root / "pending-qa.md",
        "takeovers": root / "takeovers.jsonl",
        "recovery_lock": root / "RECOVERY-LOCK.d",
    }


def ensure_state_dir(state_dir: Path | None = None) -> dict[str, Path]:
    p = paths(state_dir)
    p["root"].mkdir(parents=True, exist_ok=True, mode=0o700)
    p["queue"].mkdir(exist_ok=True, mode=0o700)
    return p


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def pid_start_time(pid: int | str | None) -> str | None:
    """Return ps's lstart identity; it changes when a PID is reused."""
    if not pid:
        return None
    try:
        proc = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = proc.stdout.strip()
    return value or None


def pid_alive(pid: int | str | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def liveness(record: dict[str, Any] | None) -> dict[str, Any]:
    """Use sessions.py's kill-0 philosophy, strengthened against PID reuse."""
    record = record or {}
    pid = record.get("claude_pid", record.get("pid"))
    expected_start = record.get("pid_start_time")
    if not pid_alive(pid):
        return {"state": "dead", "alive": False, "pid": pid}
    actual_start = pid_start_time(pid)
    if expected_start and actual_start and expected_start != actual_start:
        return {
            "state": "pid-reused",
            "alive": False,
            "pid": pid,
            "expected_pid_start_time": expected_start,
            "actual_pid_start_time": actual_start,
        }
    return {
        "state": "live" if actual_start else "live-start-unavailable",
        "alive": True,
        "pid": pid,
        "pid_start_time": actual_start,
    }


def heartbeat_age_seconds(heartbeat_at: str | None, now: datetime | None = None) -> int | None:
    if not heartbeat_at:
        return None
    try:
        timestamp = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - timestamp.astimezone(timezone.utc)).total_seconds()))


def holder_record(state_dir: Path | None = None) -> dict[str, Any] | None:
    return read_json(paths(state_dir)["holder"], default=None)


def _ticket_paths(p: dict[str, Path]) -> list[Path]:
    if not p["queue"].is_dir():
        return []
    return sorted(path for path in p["queue"].glob("*.json") if path.is_file())


def queue_entries(state_dir: Path | None = None) -> list[dict[str, Any]]:
    p = paths(state_dir)
    entries = []
    for ticket_path in _ticket_paths(p):
        ticket = read_json(ticket_path, default=None)
        if not isinstance(ticket, dict):
            entries.append({"ticket_file": ticket_path.name, "invalid": True, "liveness": {"state": "invalid", "alive": False}})
            continue
        entry = dict(ticket)
        entry["ticket_file"] = ticket_path.name
        entry["liveness"] = liveness(ticket)
        entries.append(entry)
    return entries


def _write_counter_locked(p: dict[str, Path]) -> int:
    try:
        previous = int(p["counter"].read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        previous = 0
    value = previous + 1
    temporary = p["counter"].with_name(f".{p['counter'].name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(f"{value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, p["counter"])
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return value


def _remove_directory(path: Path) -> None:
    """Remove only a known, private state directory."""
    if path.is_dir():
        shutil.rmtree(path)


def _acquire_counter_lock(p: dict[str, Path], sleep: Callable[[float], None] = time.sleep) -> None:
    while True:
        try:
            p["counter_lock"].mkdir(mode=0o700)
        except FileExistsError:
            owner = read_json(p["counter_lock"] / "holder.json", default=None)
            if owner is None or not liveness(owner)["alive"]:
                _remove_directory(p["counter_lock"])
                continue
            sleep(random.uniform(0.05, 0.20))
            continue
        atomic_write_json(
            p["counter_lock"] / "holder.json",
            {"claude_pid": os.getpid(), "pid_start_time": pid_start_time(os.getpid()), "started_at": utc_now()},
        )
        return


def _release_counter_lock(p: dict[str, Path]) -> None:
    _remove_directory(p["counter_lock"])


def cleanup_stale_tickets(state_dir: Path | None = None) -> list[dict[str, Any]]:
    """Remove dead/invalid queue tickets so a crashed session cannot jam FIFO."""
    p = ensure_state_dir(state_dir)
    removed = []
    for ticket_path in _ticket_paths(p):
        ticket = read_json(ticket_path, default=None)
        status = liveness(ticket) if isinstance(ticket, dict) else {"state": "invalid", "alive": False}
        if status["alive"]:
            continue
        try:
            ticket_path.unlink()
        except FileNotFoundError:
            continue
        removed.append({"ticket_file": ticket_path.name, "ticket": ticket, "liveness": status})
    return removed


def register_ticket(state_dir: Path | None, identity: dict[str, Any]) -> dict[str, Any]:
    """Register a run exactly once; ticket creation happens while counter-locked."""
    p = ensure_state_dir(state_dir)
    cleanup_stale_tickets(p["root"])
    run_id = identity["run_id"]
    for entry in queue_entries(p["root"]):
        if entry.get("run_id") == run_id and entry.get("liveness", {}).get("alive"):
            return entry

    _acquire_counter_lock(p)
    try:
        # Another caller for this same run may have registered while we waited.
        for entry in queue_entries(p["root"]):
            if entry.get("run_id") == run_id and entry.get("liveness", {}).get("alive"):
                return entry
        order = _write_counter_locked(p)
        ticket = {
            "run_id": run_id,
            "mode": identity["mode"],
            "claude_pid": identity.get("claude_pid"),
            "pid_start_time": identity.get("pid_start_time"),
            "queued_at": utc_now(),
            "order": order,
        }
        ticket_path = p["queue"] / f"{order:020d}-{run_id}.json"
        atomic_write_json(ticket_path, ticket)
        ticket["ticket_file"] = ticket_path.name
        ticket["liveness"] = liveness(ticket)
        return ticket
    finally:
        _release_counter_lock(p)


def _run_git(repo: str | None, args: list[str]) -> tuple[int, str, str]:
    if not repo:
        return 1, "", "repo not supplied"
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _orphan_forensics(holder: dict[str, Any] | None, repo: str | None) -> dict[str, Any]:
    holder = holder or {}
    detail: dict[str, Any] = {
        "run_id": holder.get("run_id"),
        "mode": holder.get("mode"),
        "stage": holder.get("stage"),
        "last_heartbeat": holder.get("heartbeat_at"),
        "scratch_path": holder.get("scratch_path"),
        "integration_branch": holder.get("integration_branch"),
        "scratch_exists": bool(holder.get("scratch_path") and Path(holder["scratch_path"]).exists()),
    }
    branch = holder.get("integration_branch")
    if repo and branch:
        code, stdout, stderr = _run_git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}"])
        detail["branch_state"] = {"exists": code == 0, "sha": stdout or None, "error": stderr or None}
    else:
        detail["branch_state"] = {"exists": None, "sha": None, "error": "repo or integration branch unavailable"}
    return detail


def _cleanup_orphan_worktree(holder: dict[str, Any] | None, repo: str | None) -> list[dict[str, Any]]:
    """Best-effort cleanup, bounded to a dead run's known land-batch resources."""
    holder = holder or {}
    actions: list[dict[str, Any]] = []
    scratch = holder.get("scratch_path")
    branch = holder.get("integration_branch")
    if not repo:
        return [{"action": "cleanup-skipped", "reason": "repo not supplied"}]
    repo_ok, _, repo_error = _run_git(repo, ["rev-parse", "--git-dir"])
    if repo_ok != 0:
        return [{"action": "cleanup-skipped", "reason": f"repo invalid: {repo_error}"}]
    if scratch and Path(scratch).name.startswith("land-batch-") and Path(scratch).exists():
        code, stdout, stderr = _run_git(repo, ["worktree", "remove", scratch, "--force"])
        actions.append({"action": "worktree-remove", "path": scratch, "returncode": code, "stdout": stdout, "stderr": stderr})
    elif scratch:
        actions.append({"action": "worktree-remove-skipped", "path": scratch, "reason": "not an existing land-batch scratch path"})
    if branch and branch.startswith("land-batch/"):
        code, stdout, stderr = _run_git(repo, ["branch", "-D", branch])
        actions.append({"action": "branch-delete", "branch": branch, "returncode": code, "stdout": stdout, "stderr": stderr})
    elif branch:
        actions.append({"action": "branch-delete-skipped", "branch": branch, "reason": "not a land-batch branch"})
    return actions


def read_ledger(state_dir: Path | None = None) -> dict[str, Any]:
    raw = read_json(paths(state_dir)["ledger"], default={})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "version": 1,
        "prod_sha": raw.get("prod_sha"),
        "last_successful_prod_at": raw.get("last_successful_prod_at"),
        "pending": raw.get("pending") if isinstance(raw.get("pending"), list) else [],
        "takeovers": raw.get("takeovers") if isinstance(raw.get("takeovers"), list) else [],
    }


def write_ledger(state_dir: Path | None, ledger: dict[str, Any]) -> None:
    p = ensure_state_dir(state_dir)
    normalised = read_ledger_from_value(ledger)
    atomic_write_json(p["ledger"], normalised)


def read_ledger_from_value(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "version": 1,
        "prod_sha": raw.get("prod_sha"),
        "last_successful_prod_at": raw.get("last_successful_prod_at"),
        "pending": raw.get("pending") if isinstance(raw.get("pending"), list) else [],
        "takeovers": raw.get("takeovers") if isinstance(raw.get("takeovers"), list) else [],
    }


def append_ledger(state_dir: Path | None, record: dict[str, Any], checklist_text: str | None = None) -> dict[str, Any]:
    """Append harvested, pre-deploy QA checks for every landed feature."""
    p = ensure_state_dir(state_dir)
    ledger = read_ledger(p["root"])
    defaults = {key: value for key, value in record.items() if key != "features"}
    features = record.get("features") if isinstance(record.get("features"), list) else [record]
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        entry = dict(defaults)
        entry.update(feature)
        if checklist_text and "checklist_markdown" not in entry:
            # The intended record has one checklist markdown fragment per
            # feature. Retain the supplied combined artifact as a fallback
            # without duplicating it for every feature in a batch.
            if len(features) == 1:
                entry["checklist_markdown"] = checklist_text.rstrip()
            elif index == 0:
                entry["batch_checklist_markdown"] = checklist_text.rstrip()
        entry.setdefault("landed_at", utc_now())
        ledger["pending"].append(entry)
    write_ledger(p["root"], ledger)
    rebuild_pending_qa(p["root"], ledger)
    return ledger


def rebuild_pending_qa(state_dir: Path | None, ledger: dict[str, Any] | None = None) -> None:
    """Derive the human-readable checklist from the canonical JSON ledger."""
    p = ensure_state_dir(state_dir)
    ledger = ledger or read_ledger(p["root"])
    chunks = ["# Pending QA"]
    for feature in ledger.get("pending", []):
        heading = f"## {feature.get('branch', 'unknown branch')} @ {feature.get('merge_sha', 'unknown merge')}"
        detail = feature.get("checklist_markdown") or feature.get("batch_checklist_markdown")
        if not detail:
            checks = feature.get("checks") or []
            detail = "\n".join(f"- {check.get('expected', check)}" if isinstance(check, dict) else f"- {check}" for check in checks)
        chunks.extend((heading, detail or "- Checklist details unavailable; fail closed until reconstructed."))
    if len(chunks) == 1:
        chunks.append("_No landed features pending QA._")
    p["pending_qa"].write_text("\n\n".join(chunks).rstrip() + "\n", encoding="utf-8")


def remove_pending_merge(state_dir: Path | None, merge_sha: str) -> dict[str, Any]:
    """Drop a feature that has been reverted from the pending ship set."""
    p = ensure_state_dir(state_dir)
    ledger = read_ledger(p["root"])
    before = len(ledger["pending"])
    ledger["pending"] = [entry for entry in ledger["pending"] if entry.get("merge_sha") != merge_sha]
    write_ledger(p["root"], ledger)
    rebuild_pending_qa(p["root"], ledger)
    return {"removed": before - len(ledger["pending"]), "ledger": ledger}


def record_takeover(state_dir: Path | None, takeover: dict[str, Any]) -> None:
    p = ensure_state_dir(state_dir)
    with open(p["takeovers"], "a", encoding="utf-8") as handle:
        handle.write(json.dumps(takeover, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    ledger = read_ledger(p["root"])
    ledger["takeovers"].append(takeover)
    write_ledger(p["root"], ledger)


def _acquire_recovery_lock(p: dict[str, Path]) -> bool:
    try:
        p["recovery_lock"].mkdir(mode=0o700)
    except FileExistsError:
        owner = read_json(p["recovery_lock"] / "holder.json", default=None)
        if owner is None or not liveness(owner)["alive"]:
            _remove_directory(p["recovery_lock"])
            return _acquire_recovery_lock(p)
        return False
    atomic_write_json(
        p["recovery_lock"] / "holder.json",
        {"claude_pid": os.getpid(), "pid_start_time": pid_start_time(os.getpid()), "started_at": utc_now()},
    )
    return True


def recover_stale_lock(state_dir: Path | None = None, repo: str | None = None) -> dict[str, Any] | None:
    """Forensically reclaim a lock only after its Claude PID is confirmed dead."""
    p = ensure_state_dir(state_dir)
    if not p["lock"].is_dir():
        return None
    if not _acquire_recovery_lock(p):
        return None
    try:
        holder = read_json(p["holder"], default=None)
        status = liveness(holder) if isinstance(holder, dict) else {"state": "invalid", "alive": False}
        if status["alive"]:
            return None
        takeover = {
            "taken_over_at": utc_now(),
            "liveness": status,
            "dead_run": _orphan_forensics(holder if isinstance(holder, dict) else None, repo),
        }
        takeover["cleanup"] = _cleanup_orphan_worktree(holder if isinstance(holder, dict) else None, repo)
        record_takeover(p["root"], takeover)
        _remove_directory(p["lock"])
        return takeover
    finally:
        _remove_directory(p["recovery_lock"])


def _own_ticket_path(p: dict[str, Path], run_id: str) -> Path | None:
    for path in _ticket_paths(p):
        ticket = read_json(path, default={})
        if isinstance(ticket, dict) and ticket.get("run_id") == run_id:
            return path
    return None


def try_acquire_lock(state_dir: Path | None, identity: dict[str, Any], repo: str | None = None) -> dict[str, Any]:
    """Attempt admission. ``mkdir LOCK.d`` remains the final arbiter."""
    p = ensure_state_dir(state_dir)
    takeover = recover_stale_lock(p["root"], repo=repo)
    cleanup_stale_tickets(p["root"])
    if p["lock"].is_dir():
        return {"acquired": False, "reason": "held", "holder": holder_record(p["root"]), "takeover": takeover}

    entries = queue_entries(p["root"])
    own_path = _own_ticket_path(p, identity["run_id"])
    if entries and (own_path is None or entries[0].get("run_id") != identity["run_id"]):
        return {"acquired": False, "reason": "waiting-for-earlier-ticket", "queue": entries, "takeover": takeover}

    try:
        p["lock"].mkdir(mode=0o700)
    except FileExistsError:
        return {"acquired": False, "reason": "raced", "takeover": takeover}
    holder = {
        "run_id": identity["run_id"],
        "mode": identity["mode"],
        "claude_pid": identity.get("claude_pid"),
        "pid_start_time": identity.get("pid_start_time"),
        "session_id": identity.get("session_id"),
        "agent_view_name": identity.get("agent_view_name"),
        "started_at": utc_now(),
        "stage": identity.get("stage", "admitted"),
        "heartbeat_at": utc_now(),
        "integration_branch": identity.get("integration_branch"),
        "scratch_path": identity.get("scratch_path"),
        "evidence_dir": identity.get("evidence_dir"),
    }
    try:
        atomic_write_json(p["holder"], holder)
        own_path = _own_ticket_path(p, identity["run_id"])
        if own_path:
            own_path.unlink(missing_ok=True)
    except Exception:
        _remove_directory(p["lock"])
        raise
    return {"acquired": True, "holder": holder, "takeover": takeover}


def wait_for_turn(
    state_dir: Path | None,
    identity: dict[str, Any],
    repo: str | None = None,
    poll_min: float = POLL_MIN_SECONDS,
    poll_max: float = POLL_MAX_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """FIFO admission with deliberately unbounded, jittered polling."""
    if poll_min < 0 or poll_max < poll_min:
        raise ValueError("invalid polling interval")
    p = ensure_state_dir(state_dir)
    while True:
        outcome = try_acquire_lock(p["root"], identity, repo=repo)
        if outcome["acquired"]:
            return outcome
        register_ticket(p["root"], identity)
        sleep(random.uniform(poll_min, poll_max))


def heartbeat(state_dir: Path | None, run_id: str, stage: str, **updates: Any) -> dict[str, Any]:
    p = paths(state_dir)
    holder = holder_record(p["root"])
    if not holder or holder.get("run_id") != run_id:
        raise RuntimeError("this run does not hold LOCK.d")
    holder["stage"] = stage
    holder["heartbeat_at"] = utc_now()
    for key in ("integration_branch", "scratch_path", "evidence_dir"):
        if updates.get(key) is not None:
            holder[key] = updates[key]
    atomic_write_json(p["holder"], holder)
    return holder


def release_lock(state_dir: Path | None, run_id: str) -> bool:
    p = paths(state_dir)
    holder = holder_record(p["root"])
    if not holder or holder.get("run_id") != run_id:
        return False
    _remove_directory(p["lock"])
    return True


def archive_ledger_after_prod(state_dir: Path | None, evidence_dir: Path, prod_sha: str) -> dict[str, Any]:
    """Archive the exact pending payload before resetting it for the next ship."""
    p = ensure_state_dir(state_dir)
    ledger = read_ledger(p["root"])
    write_ledger(p["root"], ledger)  # materialise an empty ledger too
    if not p["pending_qa"].exists():
        p["pending_qa"].write_text("# Pending QA\n\n_No landed features pending QA._\n", encoding="utf-8")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p["ledger"], evidence_dir / "ledger.json")
    shutil.copy2(p["pending_qa"], evidence_dir / "pending-qa.md")
    reset = {
        "version": 1,
        "prod_sha": prod_sha,
        "last_successful_prod_at": utc_now(),
        "pending": [],
        "takeovers": [],
    }
    write_ledger(p["root"], reset)
    p["pending_qa"].write_text("# Pending QA\n\n_No landed features pending QA._\n", encoding="utf-8")
    return reset


def status_payload(state_dir: Path | None = None) -> dict[str, Any]:
    p = paths(state_dir)
    holder = holder_record(p["root"])
    holder_status = liveness(holder) if holder else None
    holder_view = None
    if holder:
        holder_view = dict(holder)
        holder_view["liveness"] = holder_status
        holder_view["heartbeat_age_seconds"] = heartbeat_age_seconds(holder.get("heartbeat_at"))
    ledger = read_ledger(p["root"])
    return {
        "state_dir": str(p["root"]),
        "lock": holder_view,
        "queue": queue_entries(p["root"]),
        "ledger": {
            "prod_sha": ledger.get("prod_sha"),
            "last_successful_prod_at": ledger.get("last_successful_prod_at"),
            "pending_feature_count": len(ledger.get("pending", [])),
            "pending": ledger.get("pending", []),
            "takeover_count": len(ledger.get("takeovers", [])),
        },
    }


def human_status(state_dir: Path | None = None) -> str:
    status = status_payload(state_dir)
    lock = status["lock"]
    lines = [f"LAND-BATCH STATE: {status['state_dir']}"]
    if lock:
        lines.append(
            "LOCK: {state} run={run_id} mode={mode} stage={stage} heartbeat_age={age}s pid={pid}".format(
                state=lock["liveness"]["state"],
                run_id=lock.get("run_id"),
                mode=lock.get("mode"),
                stage=lock.get("stage"),
                age=lock.get("heartbeat_age_seconds"),
                pid=lock.get("claude_pid"),
            )
        )
    else:
        lines.append("LOCK: free")
    if status["queue"]:
        lines.append("QUEUE:")
        for index, ticket in enumerate(status["queue"], start=1):
            lines.append(
                f"  {index}. {ticket.get('run_id')} ({ticket.get('mode')}) "
                f"{ticket.get('liveness', {}).get('state')} queued={ticket.get('queued_at')}"
            )
    else:
        lines.append("QUEUE: empty")
    ledger = status["ledger"]
    lines.append(
        f"PENDING LEDGER: {ledger['pending_feature_count']} feature(s) since prod {ledger.get('prod_sha') or 'unknown'}"
    )
    for feature in ledger["pending"]:
        lines.append(f"  - {feature.get('branch', 'unknown')} @ {feature.get('merge_sha', 'unknown')}")
    return "\n".join(lines)


def _identity_from_args(args: argparse.Namespace) -> dict[str, Any]:
    pid = args.claude_pid
    return {
        "run_id": args.run_id,
        "mode": args.mode,
        "claude_pid": pid,
        "pid_start_time": args.pid_start_time or pid_start_time(pid),
        "session_id": args.session_id,
        "agent_view_name": args.agent_view_name,
        "stage": args.stage,
        "integration_branch": args.integration_branch,
        "scratch_path": args.scratch_path,
        "evidence_dir": args.evidence_dir,
    }


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", required=True, choices=("land", "ship"))
    parser.add_argument("--claude-pid", required=True, type=int)
    parser.add_argument("--pid-start-time")
    parser.add_argument("--session-id")
    parser.add_argument("--agent-view-name")
    parser.add_argument("--stage", default="admitted")
    parser.add_argument("--integration-branch")
    parser.add_argument("--scratch-path")
    parser.add_argument("--evidence-dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "wait", "register-ticket"):
        command = sub.add_parser(name)
        _add_identity_arguments(command)
        command.add_argument("--repo")
        if name == "wait":
            command.add_argument("--poll-min", type=float, default=POLL_MIN_SECONDS)
            command.add_argument("--poll-max", type=float, default=POLL_MAX_SECONDS)
    release = sub.add_parser("release")
    release.add_argument("--run-id", required=True)
    beat = sub.add_parser("heartbeat")
    beat.add_argument("--run-id", required=True)
    beat.add_argument("--stage", required=True)
    beat.add_argument("--integration-branch")
    beat.add_argument("--scratch-path")
    beat.add_argument("--evidence-dir")
    status = sub.add_parser("status")
    status.add_argument("--human", action="store_true")
    append = sub.add_parser("ledger-append")
    append.add_argument("--record", type=Path, required=True)
    append.add_argument("--checklist", type=Path)
    remove = sub.add_parser("ledger-remove")
    remove.add_argument("--merge-sha", required=True)
    archive = sub.add_parser("archive-ledger")
    archive.add_argument("--evidence-dir", type=Path, required=True)
    archive.add_argument("--prod-sha", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    state_dir = args.state_dir
    if args.command == "status":
        if args.human:
            print(human_status(state_dir))
        else:
            print(json.dumps(status_payload(state_dir), separators=(",", ":")))
        return 0
    if args.command == "register-ticket":
        print(json.dumps(register_ticket(state_dir, _identity_from_args(args)), separators=(",", ":")))
        return 0
    if args.command == "acquire":
        result = try_acquire_lock(state_dir, _identity_from_args(args), repo=args.repo)
        print(json.dumps(result, separators=(",", ":")))
        return 0 if result["acquired"] else 2
    if args.command == "wait":
        result = wait_for_turn(
            state_dir,
            _identity_from_args(args),
            repo=args.repo,
            poll_min=args.poll_min,
            poll_max=args.poll_max,
        )
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.command == "release":
        return 0 if release_lock(state_dir, args.run_id) else 1
    if args.command == "heartbeat":
        try:
            result = heartbeat(
                state_dir,
                args.run_id,
                args.stage,
                integration_branch=args.integration_branch,
                scratch_path=args.scratch_path,
                evidence_dir=args.evidence_dir,
            )
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.command == "ledger-append":
        record = read_json(args.record, default=None)
        if not isinstance(record, dict):
            print(f"invalid ledger record: {args.record}", file=sys.stderr)
            return 1
        checklist = args.checklist.read_text(encoding="utf-8") if args.checklist else None
        print(json.dumps(append_ledger(state_dir, record, checklist), separators=(",", ":")))
        return 0
    if args.command == "ledger-remove":
        print(json.dumps(remove_pending_merge(state_dir, args.merge_sha), separators=(",", ":")))
        return 0
    if args.command == "archive-ledger":
        print(json.dumps(archive_ledger_after_prod(state_dir, args.evidence_dir, args.prod_sha), separators=(",", ":")))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
