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
import re
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
        "kickbacks": root / "kickbacks.json",
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


# Keep this list in lockstep with SKILL.md's sensitive-path guardrail. It is
# intentionally prefix based so changed paths and failing-gate paths receive
# the same conservative treatment.
SENSITIVE_PREFIXES = (
    "lib/db/",
    "drizzle/migrations/",
    "lib/stripe/",
    "lib/auth/",
    "lib/payments/",
    "pages/api/auth/",
    "pages/api/admin/",
    "pages/api/webhooks/",
    "lib/email/templates/",
)


def sensitive_paths(paths_to_check: list[str] | tuple[str, ...] | None) -> list[str]:
    return sorted(
        {
            path
            for path in (paths_to_check or [])
            if isinstance(path, str) and path.startswith(SENSITIVE_PREFIXES)
        }
    )


def read_kickbacks_from_value(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise the branch-keyed kickback store without trusting its input."""
    raw = raw if isinstance(raw, dict) else {}
    lineages = raw.get("lineages")
    if not isinstance(lineages, dict):
        lineages = {}
    normalised: dict[str, dict[str, Any]] = {}
    for branch, value in lineages.items():
        if not isinstance(branch, str) or not isinstance(value, dict):
            continue
        history = value.get("history") if isinstance(value.get("history"), list) else []
        try:
            attempt = max(0, int(value.get("attempt", 0)))
        except (TypeError, ValueError):
            attempt = 0
        normalised[branch] = {
            "bead_id": value.get("bead_id"),
            "session_name": value.get("session_name"),
            "fix_branch": value.get("fix_branch"),
            "attempt": attempt,
            "dispatched_at": value.get("dispatched_at"),
            "failure_summary": value.get("failure_summary"),
            "signature": value.get("signature"),
            "evidence_path": value.get("evidence_path"),
            # Retaining prior evidence is needed when a same-signature retry is
            # surfaced instead of dispatched. The required top-level fields
            # remain the canonical current attempt.
            "history": [entry for entry in history if isinstance(entry, dict)],
        }
    return {"version": 1, "lineages": normalised}


def read_kickbacks(state_dir: Path | None = None) -> dict[str, Any]:
    return read_kickbacks_from_value(read_json(paths(state_dir)["kickbacks"], default={}))


def write_kickbacks(state_dir: Path | None, kickbacks: dict[str, Any]) -> None:
    p = ensure_state_dir(state_dir)
    atomic_write_json(p["kickbacks"], read_kickbacks_from_value(kickbacks))


def kickback_attempt_decision(previous: dict[str, Any] | None, signature: str) -> dict[str, Any]:
    """Apply the lineage cap before a new fix bead/session is created.

    Attempts one through three may be dispatched. Once three sessions have
    been recorded, every later failure is held regardless of signature.
    """
    previous = previous if isinstance(previous, dict) else None
    if previous is None:
        return {"dispatch": True, "reason": "first-attempt", "attempt": 1}
    try:
        prior_attempt = int(previous.get("attempt", 0))
    except (TypeError, ValueError):
        prior_attempt = 0
    if prior_attempt >= 3:
        return {"dispatch": False, "reason": "attempt-cap-exceeded", "attempt": prior_attempt}
    if previous.get("signature") == signature:
        return {
            "dispatch": False,
            "reason": "signature-unchanged",
            "attempt": prior_attempt,
            "previous_evidence": [
                item.get("evidence_path")
                for item in previous.get("history", [])
                if isinstance(item, dict) and item.get("evidence_path")
            ],
        }
    return {"dispatch": True, "reason": "signature-changed", "attempt": prior_attempt + 1}


def normalise_failure_signature(
    failed_files: list[str] | tuple[str, ...] | None,
    failed_tests: list[str] | tuple[str, ...] | None,
) -> str:
    """Stable, human-readable signature for the retry backstop."""
    files = sorted({path.strip() for path in (failed_files or []) if isinstance(path, str) and path.strip()})
    tests = sorted({test.strip() for test in (failed_tests or []) if isinstance(test, str) and test.strip()})
    return f"files={','.join(files) or '-'};tests={','.join(tests) or '-'}"


def extract_failure_targets(output: str) -> tuple[list[str], list[str]]:
    """Pull only stable file/test identifiers out of normal tsc/lint/Jest text."""
    files: set[str] = set()
    tests: set[str] = set()
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        # tsc: src/a.ts(4,2): error TS2322 ...; Jest: FAIL tests/a.test.ts
        ts_match = re.match(r"(.+?\.(?:[cm]?[jt]sx?|json))\(\d+,\d+\):\s+error\s+TS\d+", line)
        jest_file = re.match(r"FAIL\s+(.+?\.(?:[cm]?[jt]sx?))\b", line)
        lint_file = re.match(r"(.+?\.(?:[cm]?[jt]sx?))\s*$", line)
        if ts_match:
            files.add(ts_match.group(1).strip())
        elif jest_file:
            files.add(jest_file.group(1).strip())
        elif lint_file and "/" in lint_file.group(1):
            files.add(lint_file.group(1).strip())
        jest_test = re.match(r"(?:●|\u25cf)\s+(.+)$", line)
        if jest_test:
            tests.add(jest_test.group(1).strip())
    return sorted(files), sorted(tests)


def is_infra_failure(exit_code: int | None, output: str) -> bool:
    low = (output or "").lower()
    return exit_code in (134, 137) or any(
        marker in low
        for marker in (
            "heap out of memory",
            "javascript heap out of memory",
            "sigabrt",
            "fatal process out of memory",
        )
    )


def is_deterministic_code_failure(exit_code: int | None, output: str) -> bool:
    if exit_code is None or exit_code == 0 or is_infra_failure(exit_code, output):
        return False
    text = output or ""
    return bool(
        re.search(
            r"error\s+TS\d+|\b(?:eslint|lint)\b.*\berror\b|\b\d+\s+errors?\b|"
            r"^FAIL\s+|^\s*(?:●|\u25cf)\s+|\bAssertionError\b|\bExpected:",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )


def classify_kickback(
    *,
    baseline_exit_code: int | None,
    baseline_artifacts_valid: bool,
    gate_exit_code: int | None,
    gate_artifacts_valid: bool,
    gate_output: str,
    feature_changed_files: list[str] | None = None,
    failed_files: list[str] | None = None,
    failed_tests: list[str] | None = None,
    sensitive_opt_in: bool = False,
    infra_retry_performed: bool = False,
) -> dict[str, Any]:
    """Classify a reset LAND gate without performing git or dispatch I/O."""
    if not baseline_artifacts_valid or baseline_exit_code is None:
        return {"dispatch": False, "reason": "baseline-evidence-malformed"}
    if baseline_exit_code != 0:
        return {"dispatch": False, "reason": "baseline-red"}
    if not gate_artifacts_valid or gate_exit_code is None:
        return {"dispatch": False, "reason": "gate-evidence-malformed"}
    if gate_exit_code == 0:
        return {"dispatch": False, "reason": "gate-not-red"}
    if sensitive_opt_in or sensitive_paths((feature_changed_files or []) + (failed_files or [])):
        return {"dispatch": False, "reason": "sensitive-path"}
    if is_infra_failure(gate_exit_code, gate_output):
        return {
            "dispatch": False,
            "reason": "infra-after-retry" if infra_retry_performed else "infra-retry-required",
            "retry_gate": not infra_retry_performed,
        }
    if not is_deterministic_code_failure(gate_exit_code, gate_output):
        return {"dispatch": False, "reason": "non-deterministic-gate-failure"}
    extracted_files, extracted_tests = extract_failure_targets(gate_output)
    files = sorted(set((failed_files or []) + extracted_files))
    tests = sorted(set((failed_tests or []) + extracted_tests))
    signature = normalise_failure_signature(files, tests)
    summary_targets = ", ".join(tests or files) or "deterministic scoped gate error"
    return {
        "dispatch": True,
        "reason": "branch-introduced-deterministic-code-failure",
        "signature": signature,
        "failed_files": files,
        "failed_tests": tests,
        "failure_summary": summary_targets,
    }


def record_kickback(state_dir: Path | None, original_branch: str, record: dict[str, Any]) -> dict[str, Any]:
    """Atomically append a dispatched attempt for one original-branch lineage."""
    if not original_branch:
        raise ValueError("original branch is required")
    p = ensure_state_dir(state_dir)
    kickbacks = read_kickbacks(p["root"])
    previous = kickbacks["lineages"].get(original_branch)
    try:
        prior_attempt = int(previous.get("attempt", 0)) if previous else 0
    except (TypeError, ValueError):
        prior_attempt = 0
    history = list(previous.get("history", [])) if previous else []
    if previous:
        history.append(
            {
                "attempt": prior_attempt,
                "bead_id": previous.get("bead_id"),
                "session_name": previous.get("session_name"),
                "signature": previous.get("signature"),
                "failure_summary": previous.get("failure_summary"),
                "evidence_path": previous.get("evidence_path"),
                "dispatched_at": previous.get("dispatched_at"),
            }
        )
    entry = {
        "bead_id": record.get("bead_id"),
        "session_name": record.get("session_name"),
        "fix_branch": record.get("fix_branch") or (previous or {}).get("fix_branch"),
        "attempt": prior_attempt + 1,
        "dispatched_at": record.get("dispatched_at") or utc_now(),
        "failure_summary": record.get("failure_summary"),
        "signature": record.get("signature"),
        "history": history,
    }
    # The current evidence path is deliberately retained as an extra field: it
    # is required to show both evidence sets when a same-signature retry stops.
    if record.get("evidence_path"):
        entry["evidence_path"] = record["evidence_path"]
    kickbacks["lineages"][original_branch] = entry
    write_kickbacks(p["root"], kickbacks)
    return entry


def update_kickback_fix_branch(state_dir: Path | None, original_branch: str, fix_branch: str) -> dict[str, Any] | None:
    """Record a discovered, pushed fix branch without creating another attempt."""
    p = ensure_state_dir(state_dir)
    kickbacks = read_kickbacks(p["root"])
    entry = kickbacks["lineages"].get(original_branch)
    if not entry:
        return None
    entry["fix_branch"] = fix_branch
    write_kickbacks(p["root"], kickbacks)
    return entry


def _active_session_names() -> set[str]:
    """Use sessions.py's kill-0/liveness contract, never a loose process grep."""
    script = Path(__file__).with_name("sessions.py")
    if not script.exists():
        return set()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if proc.returncode != 0:
        return set()
    try:
        sessions = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return set()
    if not isinstance(sessions, dict):
        return set()
    return {
        record.get("name")
        for record in sessions.values()
        if isinstance(record, dict) and record.get("active") and isinstance(record.get("name"), str)
    }


def kickback_status(state_dir: Path | None = None, active_session_names: set[str] | None = None) -> dict[str, Any]:
    """Describe a lineage as in-flight, ready, or explicitly stalled."""
    active_names = _active_session_names() if active_session_names is None else active_session_names
    lineages = read_kickbacks(state_dir)["lineages"]
    statuses: dict[str, dict[str, Any]] = {}
    for original_branch, entry in lineages.items():
        view = dict(entry)
        if entry.get("session_name") in active_names:
            state = "in-flight"
        elif entry.get("fix_branch"):
            state = "ready"
        else:
            state = "stalled"
        view["state"] = state
        statuses[original_branch] = view
    return {"lineage_count": len(statuses), "lineages": statuses}


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
    kickbacks = kickback_status(p["root"])
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
        "kickbacks": kickbacks,
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
    kickbacks = status["kickbacks"]
    lines.append(f"KICKBACKS: {kickbacks['lineage_count']} lineage(s)")
    for original_branch, entry in kickbacks["lineages"].items():
        lines.append(
            "  - {branch}: {state} bead={bead} session={session} attempt={attempt} fix_branch={fix_branch}".format(
                branch=original_branch,
                state=entry.get("state"),
                bead=entry.get("bead_id") or "unknown",
                session=entry.get("session_name") or "unknown",
                attempt=entry.get("attempt", 0),
                fix_branch=entry.get("fix_branch") or "not-yet-discovered",
            )
        )
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
    kickback_record = sub.add_parser("kickback-record")
    kickback_record.add_argument("--original-branch", required=True)
    kickback_record.add_argument("--record", type=Path, required=True)
    kickback_status_parser = sub.add_parser("kickback-status")
    kickback_status_parser.add_argument("--human", action="store_true")
    kickback_fix_branch = sub.add_parser("kickback-fix-branch")
    kickback_fix_branch.add_argument("--original-branch", required=True)
    kickback_fix_branch.add_argument("--fix-branch", required=True)
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
    if args.command == "kickback-record":
        record = read_json(args.record, default=None)
        if not isinstance(record, dict):
            print(f"invalid kickback record: {args.record}", file=sys.stderr)
            return 1
        print(json.dumps(record_kickback(state_dir, args.original_branch, record), separators=(",", ":")))
        return 0
    if args.command == "kickback-status":
        result = kickback_status(state_dir)
        if args.human:
            for original_branch, entry in result["lineages"].items():
                print(
                    f"{original_branch}: {entry['state']} bead={entry.get('bead_id') or 'unknown'} "
                    f"session={entry.get('session_name') or 'unknown'} attempt={entry.get('attempt', 0)} "
                    f"fix_branch={entry.get('fix_branch') or 'not-yet-discovered'}"
                )
        else:
            print(json.dumps(result, separators=(",", ":")))
        return 0
    if args.command == "kickback-fix-branch":
        result = update_kickback_fix_branch(state_dir, args.original_branch, args.fix_branch)
        if result is None:
            print(f"unknown kickback lineage: {args.original_branch}", file=sys.stderr)
            return 1
        print(json.dumps(result, separators=(",", ":")))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
