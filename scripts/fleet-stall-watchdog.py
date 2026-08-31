#!/usr/bin/env python3
"""Alert when completed fleet work stalls before landing or dispatching."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys


HOME = Path.home()
WORKERS_DIR = HOME / ".claude/fleet/codex-workers"
WORKTREES_DIR = HOME / ".claude/worktrees"
FLEET_SLOTS = HOME / ".claude/scripts/fleet-slots.py"
AESTHETCC_NEXT = Path("/Users/shane/Documents/GitReBase/AestheticcNext")
NOTIFICATION_TITLE = "Aestheticc fleet stall watchdog"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--threshold-hours",
        type=float,
        default=3,
        metavar="N",
        help="hours a finished, unlanded branch may remain before alerting (default: 3)",
    )
    args = parser.parse_args()
    if args.threshold_hours < 0:
        parser.error("--threshold-hours must be non-negative")
    return args


def parse_iso8601(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_status(status_path: Path) -> tuple[str, dict[str, str]]:
    fields = status_path.read_text(encoding="utf-8").strip().split()
    if not fields:
        raise ValueError("empty status.txt")
    values: dict[str, str] = {}
    for field in fields[1:]:
        if "=" in field:
            key, value = field.split("=", 1)
            values[key] = value
    return fields[0], values


def notify(message: str) -> None:
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    escaped_title = NOTIFICATION_TITLE.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'display notification "{escaped_message}" '
        f'with title "{escaped_title}" sound name "Basso"'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        print(f"NOTIFICATION ERROR: {detail}")


def fire(message: str, alerts: list[str]) -> None:
    alerts.append(message)
    print(f"ALERT: {message}")
    notify(message)


def check_unlanded_done(threshold_hours: float, alerts: list[str]) -> None:
    now = datetime.now(timezone.utc)
    threshold_seconds = threshold_hours * 3600
    candidates: list[tuple[str, str, str, float]] = []

    for status_path in sorted(WORKERS_DIR.glob("*/status.txt")):
        bead_id = status_path.parent.name
        try:
            state, values = parse_status(status_path)
            if state != "DONE":
                continue
            finished_at_text = values["finished_at"]
            finished_at = parse_iso8601(finished_at_text)
        except (OSError, ValueError, KeyError) as exc:
            fire(f"Cannot parse {status_path}: {exc}", alerts)
            continue

        age_seconds = (now - finished_at).total_seconds()
        if age_seconds <= threshold_seconds:
            continue

        branch = f"goal/{bead_id.lower()}"
        candidates.append((bead_id, branch, finished_at_text, age_seconds))

    def inspect_candidate(candidate: tuple[str, str, str, float]) -> str | None:
        bead_id, branch, finished_at_text, age_seconds = candidate
        remote_ref = f"refs/heads/{branch}"
        try:
            lookup = subprocess.run(
                ["git", "ls-remote", "origin", remote_ref],
                cwd=AESTHETCC_NEXT,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return f"Timed out checking origin branch {branch}"
        if lookup.returncode != 0:
            detail = lookup.stderr.strip() or lookup.stdout.strip() or f"exit {lookup.returncode}"
            return f"Cannot check origin branch {branch}: {detail}"

        matching_lines = [line for line in lookup.stdout.splitlines() if line.strip()]
        if not matching_lines:
            return None
        sha = matching_lines[0].split()[0]

        try:
            landed = subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
                cwd=AESTHETCC_NEXT,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return f"Timed out checking whether {branch} is landed"
        if landed.returncode != 0:
            age_hours = age_seconds / 3600
            return (
                f"DONE worker {bead_id} has been unlanded for {age_hours:.1f}h "
                f"({branch} at {sha[:12]}, finished {finished_at_text})"
            )
        return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        for message in executor.map(inspect_candidate, candidates):
            if message is not None:
                fire(message, alerts)


def check_unacknowledged_blocked(alerts: list[str]) -> None:
    for blocked_path in sorted(WORKTREES_DIR.glob("goal-codex-*/BLOCKED.md")):
        try:
            current_mtime = str(blocked_path.stat().st_mtime_ns)
            seen_path = blocked_path.with_name("BLOCKED.md.seen")
            previous_mtime = seen_path.read_text(encoding="utf-8").strip() if seen_path.exists() else ""
        except OSError as exc:
            fire(f"Cannot inspect {blocked_path}: {exc}", alerts)
            continue

        if previous_mtime == current_mtime:
            continue

        message = f"Unacknowledged BLOCKED.md: {blocked_path.parent.name}"
        fire(message, alerts)
        try:
            seen_path.write_text(current_mtime + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"ACKNOWLEDGEMENT ERROR: cannot write {seen_path}: {exc}")


def parse_fleet_slots(output: str) -> tuple[int, int]:
    # fleet-slots.py's summary block always prints "codex_glm:    <n>" -- that count
    # alone is authoritative. The "active codex_glm:" listing section and the
    # "queued (<n>):" section are BOTH conditional: fleet-slots.py only prints them
    # when there is something to list (cg > 0, or the queue is non-empty). Their
    # absence means 0, not a parse failure -- confirmed live: a real `status` run
    # with codex_glm=0 prints no "active codex_glm:" line at all.
    active_match = re.search(r"^codex_glm:\s*(\d+)", output, re.IGNORECASE | re.MULTILINE)
    if not active_match:
        raise ValueError("codex_glm summary line not found")
    active_count = int(active_match.group(1))

    queue_match = re.search(r"\bqueued\s*\((\d+)\)\s*:", output, re.IGNORECASE)
    queue_depth = int(queue_match.group(1)) if queue_match else 0

    return active_count, queue_depth


def check_queue_stall(alerts: list[str]) -> None:
    try:
        result = subprocess.run(
            [sys.executable, str(FLEET_SLOTS), "status"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        fire("fleet-slots.py status timed out", alerts)
        return
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        fire(f"fleet-slots.py status failed: {detail}", alerts)
        return

    try:
        active_count, queue_depth = parse_fleet_slots(result.stdout)
    except ValueError as exc:
        fire(f"Cannot parse fleet-slots.py status: {exc}", alerts)
        return

    if queue_depth > 0 and active_count == 0:
        fire(
            f"Fleet queue is stalled: {queue_depth} queued, zero active codex_glm workers",
            alerts,
        )


def main() -> int:
    args = parse_args()
    alerts: list[str] = []

    check_unlanded_done(args.threshold_hours, alerts)
    check_unacknowledged_blocked(alerts)
    check_queue_stall(alerts)

    if alerts:
        print(f"SUMMARY: {len(alerts)} alert(s) fired")
        return 1
    print("SUMMARY: all clear; no fleet stalls detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
