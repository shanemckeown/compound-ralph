#!/usr/bin/env python3
"""Show the current Codex factory worker and branch status."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


HOME = Path.home()
WORKERS_DIR = HOME / ".claude" / "fleet" / "codex-workers"
WORKTREES_DIR = HOME / ".claude" / "worktrees"
FLEET_SLOTS = HOME / ".claude" / "scripts" / "fleet-slots.py"
AESTHETICC_REPO = Path("/Users/shane/Documents/GitReBase/AestheticcNext")

STATUS_RE = re.compile(
    r"^(DONE|FAILED|BLOCKED) "
    r"finished_at=(\S+) "
    r"duration_seconds=(\d+) "
    r"exit_code=(-?\d+)$"
)
TOTAL_RE = re.compile(r"^\s*TOTAL:\s*(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE)
QUEUE_HEADER_RE = re.compile(r"^\s*queue(?:d)?\b", re.IGNORECASE)
QUEUE_COUNT_RE = re.compile(
    r"^\s*queue(?:d)?(?:\s+depth)?(?:\s*\((\d+)\))?\s*:\s*(\d+)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WorkerStatus:
    state: str
    finished_at: datetime
    duration_seconds: int
    exit_code: int


@dataclass
class WorkerRow:
    bead_id: str
    state: str
    branch: str
    age: str = ""
    detail: str = ""


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_iso8601(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc)


def read_worker_status(path: Path) -> WorkerStatus | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None

    match = STATUS_RE.fullmatch(raw)
    if match is None:
        return None

    finished_at = parse_iso8601(match.group(2))
    if finished_at is None:
        return None

    return WorkerStatus(
        state=match.group(1),
        finished_at=finished_at,
        duration_seconds=int(match.group(3)),
        exit_code=int(match.group(4)),
    )


def human_age(then: datetime, now: datetime) -> str:
    seconds = max(0, int((now - then).total_seconds()))
    minutes = seconds // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def first_nonempty_line(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                return " ".join(line.split())
    except (OSError, UnicodeError):
        pass
    return ""


def blocked_time(blocked_file: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(blocked_file.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def fleet_slot_status(worker_ids: list[str]) -> tuple[int, int, int, dict[str, int]]:
    result = run(["python3", str(FLEET_SLOTS), "status"])
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"fleet-slots.py status failed: {detail}")

    total_match = TOTAL_RE.search(result.stdout)
    if total_match is None:
        raise RuntimeError("could not parse TOTAL from fleet-slots.py status")
    slots_used, slot_capacity = map(int, total_match.groups())

    lines = result.stdout.splitlines()
    queue_header_index: int | None = None
    declared_depth: int | None = None
    for index, line in enumerate(lines):
        if not QUEUE_HEADER_RE.match(line):
            continue
        queue_header_index = index
        count_match = QUEUE_COUNT_RE.match(line)
        if count_match is not None:
            count = count_match.group(1) or count_match.group(2)
            if count is not None:
                declared_depth = int(count)
        break

    if queue_header_index is None:
        return slots_used, slot_capacity, 0, {}

    queue_lines = [line for line in lines[queue_header_index + 1 :] if line.strip()]
    queue_positions: dict[str, int] = {}
    ids_by_length = sorted(worker_ids, key=len, reverse=True)
    for fallback_position, line in enumerate(queue_lines, start=1):
        explicit_position = re.match(r"^\s*(?:[-*]\s*)?#?(\d+)[.):]?\s+", line)
        position = int(explicit_position.group(1)) if explicit_position else fallback_position
        for bead_id in ids_by_length:
            bead_pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(bead_id)}(?![A-Za-z0-9_.-])"
            if re.search(bead_pattern, line, re.IGNORECASE):
                queue_positions.setdefault(bead_id.casefold(), position)
                break

    queue_depth = declared_depth if declared_depth is not None else len(queue_lines)
    return slots_used, slot_capacity, queue_depth, queue_positions


def remote_branch_tips(done_beads: list[str]) -> dict[str, str]:
    if not done_beads:
        return {}

    refs = [f"refs/heads/goal/{bead_id.lower()}" for bead_id in done_beads]
    result = run(["git", "ls-remote", "origin", *refs], cwd=AESTHETICC_REPO)
    if result.returncode != 0:
        return {}

    tips: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[1].startswith("refs/heads/goal/"):
            continue
        bead_id = fields[1].removeprefix("refs/heads/goal/")
        tips[bead_id.casefold()] = fields[0]
    return tips


def branch_is_landed(tip_sha: str | None) -> bool:
    if not tip_sha:
        return False
    result = run(
        ["git", "merge-base", "--is-ancestor", tip_sha, "origin/main"],
        cwd=AESTHETICC_REPO,
    )
    return result.returncode == 0


def bead_is_closed(bead_id: str) -> bool | None:
    """Fallback signal when the branch is already gone (goal/* deleted).

    The night's actual landing pattern is cherry-pick into a scratch worktree
    (to dodge a background dolt-export race on .beads/issues.jsonl), then delete
    the original branch -- a NEW commit reaches origin/main with different
    content-identical-but-different-sha, so branch_is_landed()'s ancestor check
    can never find it even when the bead genuinely landed. A missing branch with
    a CLOSED bead is exactly that case: treat as landed, not unlanded. A missing
    branch with a bead that is still OPEN is a genuinely different, more worrying
    state (cleaned up without landing) -- surfaced separately, not conflated.
    Returns None if bd itself can't be queried (never guess).
    """
    result = run(["bd", "show", bead_id, "--json"], cwd=AESTHETICC_REPO)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    import json

    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None
    # `bd show <id> --json` returns a JSON array of matching issues, not a bare
    # object -- confirmed live: [{"id": ..., "status": "closed", ...}].
    if isinstance(data, list):
        data = data[0] if data else None
    status = data.get("status") if isinstance(data, dict) else None
    if status is None:
        return None
    return str(status).lower() == "closed"


def render_table(rows: list[WorkerRow]) -> str:
    headers = ("bead-id", "state", "branch", "age", "detail")
    values = [
        (row.bead_id, row.state, row.branch, row.age, row.detail)
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in values))
        if values
        else len(headers[index])
        for index in range(len(headers))
    ]

    def format_row(fields: tuple[str, ...]) -> str:
        return " | ".join(field.ljust(widths[index]) for index, field in enumerate(fields)).rstrip()

    divider = "-+-".join("-" * width for width in widths)
    return "\n".join([format_row(headers), divider, *(format_row(row) for row in values)])


def main() -> None:
    worker_dirs = sorted(
        (path for path in WORKERS_DIR.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    )
    worker_ids = [path.name for path in worker_dirs]
    parsed_statuses = {
        path.name: read_worker_status(path / "status.txt") for path in worker_dirs
    }

    slots_used, slot_capacity, queue_depth, queue_positions = fleet_slot_status(worker_ids)
    done_beads = [
        bead_id
        for bead_id, status in parsed_statuses.items()
        if status is not None and status.state == "DONE"
    ]
    branch_tips = remote_branch_tips(done_beads)
    now = datetime.now(timezone.utc)
    rows: list[WorkerRow] = []

    for worker_dir in worker_dirs:
        bead_id = worker_dir.name
        status = parsed_statuses[bead_id]
        branch = f"goal/{bead_id.lower()}"
        blocked_file = WORKTREES_DIR / f"goal-codex-{bead_id.lower()}" / "BLOCKED.md"

        if blocked_file.is_file():
            state = "BLOCKED"
            age_from = status.finished_at if status is not None else blocked_time(blocked_file)
            age = human_age(age_from, now) if age_from is not None else ""
            detail = first_nonempty_line(blocked_file)
        elif status is None:
            state = "RUNNING"
            age = ""
            position = queue_positions.get(bead_id.casefold())
            detail = f"queued #{position}" if position is not None else ""
        elif status.state == "DONE":
            tip_sha = branch_tips.get(bead_id.casefold())
            landed = branch_is_landed(tip_sha)
            detail = ""
            if landed:
                state = "DONE-landed"
            else:
                # Ancestor check can say False for a genuinely-landed bead: the
                # night's actual landing pattern is cherry-pick into a scratch
                # worktree (dodges a background dolt-export race on
                # .beads/issues.jsonl), not a direct merge of the worker's own
                # branch -- a NEW commit with different content-identical-but-
                # different-sha reaches origin/main, so the original branch tip
                # (even if it still exists on origin, as branches often do --
                # only the LOCAL branch gets deleted post-land) will never be
                # its ancestor. Fall back to the bead's own close status, which
                # is independent evidence.
                closed = bead_is_closed(bead_id)
                if closed is True:
                    state = "DONE-landed"
                elif closed is False and tip_sha is None:
                    state = "DONE-cleaned-not-closed"
                    detail = "branch gone, bead still open -- verify manually"
                else:
                    state = "DONE-unlanded"
                    if closed is None:
                        detail = "bd status unknown"
            age = "" if state == "DONE-landed" else human_age(status.finished_at, now)
        elif status.state == "BLOCKED":
            state = "BLOCKED"
            age = human_age(status.finished_at, now)
            detail = ""
        else:
            state = "FAILED"
            age = ""
            detail = ""

        rows.append(WorkerRow(bead_id, state, branch, age, detail))

    state_priority = {
        "DONE-cleaned-not-closed": 0,
        "DONE-unlanded": 1,
        "BLOCKED": 2,
        "RUNNING": 3,
        "FAILED": 4,
        "DONE-landed": 5,
    }
    rows.sort(key=lambda row: (state_priority[row.state], row.bead_id.casefold()))
    unlanded = [row.bead_id for row in rows if row.state == "DONE-unlanded"]

    print(f"slots in use/capacity: {slots_used}/{slot_capacity} | queue depth: {queue_depth}")
    print(render_table(rows))
    print("\nDONE, pushed, unlanded -- pick up here")
    if unlanded:
        for bead_id in unlanded:
            print(bead_id)
    else:
        print("none")


if __name__ == "__main__":
    main()
