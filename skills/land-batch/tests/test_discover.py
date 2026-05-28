"""Black-box tests for bin/discover.sh JSON discovery output."""

import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DISCOVER = ROOT / "bin" / "discover.sh"


def _run(args, cwd=None, env=None):
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _git(repo, *args):
    return _run(["git", "-C", str(repo), *args])


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_all(repo, message):
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _write(repo / "app.txt", "base\n")
    _commit_all(repo, "initial")
    return repo


def _add_worktree(repo, tmp_path, branch):
    path = tmp_path / branch.replace("/", "_")
    _git(repo, "worktree", "add", "-b", branch, str(path))
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    return path


def _discover(repo, home):
    env = os.environ.copy()
    env["HOME"] = str(home)
    result = _run(["bash", str(DISCOVER), str(repo)], env=env)
    return json.loads(result.stdout)


def _candidate(report, branch):
    return next(c for c in report["candidates"] if c["branch"] == branch)


def _active_session(home, cwd):
    sessions = home / ".claude" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    record = {
        "pid": os.getpid(),
        "sessionId": "active-session",
        "cwd": str(cwd),
        "status": "busy",
        "updatedAt": time.time(),
        "kind": "bg",
        "name": "active test session",
    }
    (sessions / "active-session.json").write_text(json.dumps(record), encoding="utf-8")


def test_goal_aestheticcnext_branch_extracts_canonical_bead_id(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "goal/aestheticcnext-x8ftu")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "goal/aestheticcnext-x8ftu")

    assert candidate["bead_id"] == "AestheticcNext-x8ftu"


def test_clean_ahead_non_active_non_sensitive_without_marker_auto_lands(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-l7ioe")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "fix/AestheticcNext-l7ioe")

    assert candidate["has_marker"] is False
    assert candidate["auto_land"] is True
    assert candidate["finish_signal"] == "finished"


def test_auto_land_blocked_by_sensitive_files(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-sens1")
    _write(wt / "lib" / "db" / "schema.ts", "export const changed = true;\n")
    _commit_all(wt, "touch sensitive file")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "fix/AestheticcNext-sens1")

    assert candidate["touches_sensitive"] is True
    assert candidate["auto_land"] is False
    assert candidate["finish_signal"] == "held-sensitive"


def test_auto_land_blocked_by_active_session(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-actv1")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")
    home = tmp_path / "home"
    _active_session(home, wt)

    report = _discover(repo, home)
    candidate = _candidate(report, "fix/AestheticcNext-actv1")

    assert candidate["session"]["active"] is True
    assert candidate["auto_land"] is False
    assert candidate["finish_signal"] == "blocked-session-active"


def test_auto_land_blocked_by_real_uncommitted_dirt(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-dirt1")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")
    _write(wt / "PLAN.md", "real uncommitted work\n")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "fix/AestheticcNext-dirt1")

    assert candidate["clean"] is False
    assert candidate["effectively_clean"] is False
    assert candidate["auto_land"] is False
    assert candidate["finish_signal"] == "blocked-not-clean-or-no-commits"


def test_auto_land_blocked_by_base_conflict(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-conf1")
    _write(wt / "app.txt", "branch change\n")
    _commit_all(wt, "branch change")
    _write(repo / "app.txt", "main change\n")
    _commit_all(repo, "main change")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "fix/AestheticcNext-conf1")

    assert candidate["conflicts_with_base"] is True
    assert candidate["auto_land"] is False
    assert candidate["finish_signal"] == "skip-conflict"
