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


def _commit_all_at(repo, message, committed_at):
    _git(repo, "add", ".")
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = committed_at
    env["GIT_COMMITTER_DATE"] = committed_at
    _run(["git", "-C", str(repo), "commit", "-m", message], env=env)


def _write_beads(path, records):
    _write(path, "".join(json.dumps(record) + "\n" for record in records))


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


def _add_origin(repo, tmp_path):
    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", str(origin)])
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    return origin


def _push_remote_only_branch(repo, tmp_path, branch, filename="feature.txt"):
    wt = _add_worktree(repo, tmp_path, branch)
    _write(wt / filename, f"{branch}\n")
    _commit_all(wt, f"add {branch}")
    tip_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    _git(wt, "push", "origin", f"HEAD:refs/heads/{branch}")
    _git(repo, "worktree", "remove", "--force", str(wt))
    _git(repo, "branch", "-D", branch)
    _git(repo, "fetch", "origin")
    return tip_sha


def _discover(repo, home, extra_env=None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
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


def _named_active_session(home, cwd, session_id, name, updated_at):
    sessions = home / ".claude" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    record = {
        "pid": os.getpid(),
        "sessionId": session_id,
        "cwd": str(cwd),
        "status": "busy",
        "updatedAt": updated_at,
        "kind": "bg",
        "name": name,
    }
    (sessions / f"{session_id}.json").write_text(json.dumps(record), encoding="utf-8")


def test_goal_aestheticcnext_branch_extracts_canonical_bead_id(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "goal/aestheticcnext-x8ftu")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "goal/aestheticcnext-x8ftu")

    assert candidate["bead_id"] == "AestheticcNext-x8ftu"
    assert candidate["bead_resolution"] == "none"


def test_dotted_child_resolves_exactly_instead_of_to_parent(tmp_path):
    repo = _init_repo(tmp_path)
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [
            {"id": "AestheticcNext-6644b", "status": "open"},
            {"id": "AestheticcNext-6644b.3", "status": "closed"},
        ],
    )
    wt = _add_worktree(repo, tmp_path, "goal/aestheticcnext-6644b.3")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")

    candidate = _candidate(_discover(repo, tmp_path / "home"), "goal/aestheticcnext-6644b.3")

    assert candidate["bead_id"] == "AestheticcNext-6644b.3"
    assert candidate["bead_resolution"] == "exact"
    assert candidate["bead_status"] == "closed"


def test_legacy_hyphen_child_slug_resolves_as_alias(tmp_path):
    repo = _init_repo(tmp_path)
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [
            {"id": "AestheticcNext-9z6d6", "status": "open"},
            {"id": "AestheticcNext-9z6d6.2", "status": "closed"},
        ],
    )
    wt = _add_worktree(repo, tmp_path, "goal/9z6d6-2")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")

    candidate = _candidate(_discover(repo, tmp_path / "home"), "goal/9z6d6-2")

    assert candidate["bead_id"] == "AestheticcNext-9z6d6.2"
    assert candidate["bead_resolution"] == "alias"
    assert candidate["bead_status"] == "closed"


def test_ambiguous_legacy_slug_is_held_without_guessing(tmp_path):
    repo = _init_repo(tmp_path)
    home = tmp_path / "home"
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [{"id": "AestheticcNext-same1.2", "status": "closed"}],
    )
    _write_beads(
        home / "Documents" / "Obsidian" / ".beads" / "issues.jsonl",
        [{"id": "LUCY-same1.2", "status": "closed"}],
    )
    wt = _add_worktree(repo, tmp_path, "goal/same1-2")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")

    candidate = _candidate(_discover(repo, home), "goal/same1-2")

    assert candidate["bead_id"] is None
    assert candidate["bead_resolution"] == "ambiguous"
    assert candidate["bead_candidates"] == ["AestheticcNext-same1.2", "LUCY-same1.2"]
    assert candidate["bead_status"] is None
    assert candidate["auto_land"] is False


def test_remote_only_goal_branch_is_discovered_with_nullable_worktree_fields(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    tip_sha = _push_remote_only_branch(repo, tmp_path, "goal/aestheticcnext-rmt01")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, "goal/aestheticcnext-rmt01")

    assert report["ref_snapshot"] == {
        "mode": "cached-remote-tracking",
        "mutated_by_discovery": False,
        "goal_ref_count": 1,
    }
    assert candidate["source_kind"] == "remote-branch"
    assert candidate["source_ref"] == "refs/remotes/origin/goal/aestheticcnext-rmt01"
    assert candidate["tip_sha"] == tip_sha
    assert candidate["worktree_path"] is None
    assert candidate["root"] is None
    assert candidate["clean"] is None
    assert candidate["effectively_clean"] is None
    assert candidate["auto_land"] is False
    assert candidate["finished"] is False
    assert candidate["finish_signal"] == "held-branch-only-bead-unresolved"


def test_worktree_source_pins_local_tip_when_same_named_remote_tip_differs(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-div01"
    wt = _add_worktree(repo, tmp_path, branch)
    _write(wt / "remote.txt", "remote tip\n")
    _commit_all(wt, "remote tip")
    remote_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    _git(wt, "push", "origin", f"HEAD:refs/heads/{branch}")
    _write(wt / "local.txt", "local-only tip\n")
    _commit_all(wt, "local tip")
    local_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "fetch", "origin")

    candidate = _candidate(_discover(repo, tmp_path / "home"), branch)

    assert remote_sha != local_sha
    assert candidate["source_kind"] == "worktree"
    assert candidate["source_ref"] == f"refs/heads/{branch}"
    assert candidate["tip_sha"] == local_sha
    assert candidate["tip_sha"] != _git(repo, "rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()


def test_merged_remote_only_ref_is_omitted_but_merged_worktree_is_retained(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)

    remote_branch = "goal/aestheticcnext-mrg01"
    _push_remote_only_branch(repo, tmp_path, remote_branch)
    _git(
        repo,
        "merge",
        "--no-ff",
        f"refs/remotes/origin/{remote_branch}",
        "-m",
        "merge remote-only branch",
    )

    worktree_branch = "goal/aestheticcnext-mrg02"
    wt = _add_worktree(repo, tmp_path, worktree_branch)
    _write(wt / "worktree-feature.txt", "merged but worktree remains\n")
    _commit_all(wt, "worktree feature")
    _git(
        repo,
        "merge",
        "--no-ff",
        f"refs/heads/{worktree_branch}",
        "-m",
        "merge worktree branch",
    )
    _git(repo, "push", "origin", "main")

    report = _discover(repo, tmp_path / "home")
    branches = {candidate["branch"] for candidate in report["candidates"]}

    assert remote_branch not in branches
    assert report["skipped_merged_count"] == 1
    candidate = _candidate(report, worktree_branch)
    assert candidate["source_kind"] == "worktree"
    assert candidate["recommendation"] == "skip-merged"


def test_closed_patch_unique_remote_branch_is_finished_but_never_auto_lands(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-done1"
    tip_sha = _push_remote_only_branch(repo, tmp_path, branch)
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [{"id": "AestheticcNext-done1", "status": "closed"}],
    )

    candidate = _candidate(_discover(repo, tmp_path / "home"), branch)

    assert candidate["tip_sha"] == tip_sha
    assert candidate["bead_resolution"] == "exact"
    assert candidate["bead_status"] == "closed"
    assert candidate["finish_evidence"]["qualified"] is True
    assert candidate["finished"] is True
    assert candidate["auto_land"] is False
    assert candidate["finish_signal"] == "held-branch-only-premise-review"
    assert candidate["premise_state"] == "review-required"
    assert candidate["held_labels"] == [
        "HELD — branch-only completion; premise review required"
    ]


def test_remote_child_branch_finds_exact_session_among_shared_cwd_sessions(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-sess1.2"
    _push_remote_only_branch(repo, tmp_path, branch)
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [
            {"id": "AestheticcNext-sess1", "status": "open"},
            {"id": "AestheticcNext-sess1.2", "status": "closed"},
        ],
    )
    home = tmp_path / "home"
    _named_active_session(home, home, "parent", "goal AestheticcNext-sess1", 2000)
    _named_active_session(home, home, "child", "goal AestheticcNext-sess1.2", 1000)

    candidate = _candidate(_discover(repo, home), branch)

    assert candidate["session"]["session_id"] == "child"
    assert candidate["session"]["joined_by"] == "bead-id"
    assert candidate["finish_signal"] == "blocked-session-active"
    assert candidate["finished"] is False
    assert candidate["auto_land"] is False


def test_closed_remote_branch_older_than_45_days_remains_loudly_stale(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-old01"
    wt = _add_worktree(repo, tmp_path, branch)
    _write(wt / "old.txt", "finished long ago\n")
    _commit_all_at(wt, "old finished work", "2000-01-01T00:00:00+00:00")
    _git(wt, "push", "origin", f"HEAD:refs/heads/{branch}")
    _git(repo, "worktree", "remove", "--force", str(wt))
    _git(repo, "branch", "-D", branch)
    _git(repo, "fetch", "origin")
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [{"id": "AestheticcNext-old01", "status": "closed"}],
    )

    candidate = _candidate(_discover(repo, tmp_path / "home"), branch)

    assert candidate["age_days"] > 45
    assert candidate["stale"] is True
    assert candidate["retired_by_pattern"] is False
    assert candidate["recommendation"] == "held-stale"
    assert candidate["finish_signal"] == "held-stale"
    assert candidate["finished"] is True
    assert candidate["auto_land"] is False


def test_explicit_retired_pattern_remains_a_hard_skip(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-ret01"
    _push_remote_only_branch(repo, tmp_path, branch)
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [{"id": "AestheticcNext-ret01", "status": "closed"}],
    )
    retired_file = tmp_path / "retired.txt"
    retired_file.write_text("AestheticcNext-ret01\n", encoding="utf-8")

    candidate = _candidate(
        _discover(
            repo,
            tmp_path / "home",
            {"LAND_BATCH_RETIRED_FILE": str(retired_file)},
        ),
        branch,
    )

    assert candidate["retired_by_pattern"] is True
    assert candidate["recommendation"] == "skip-retired"
    assert candidate["finish_signal"] == "skip-retired"
    assert candidate["auto_land"] is False


def test_remote_branch_wholly_present_by_patch_is_skipped_before_merge_analysis(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-peq01"
    wt = _add_worktree(repo, tmp_path, branch)
    _write(wt / "equivalent.txt", "same patch\n")
    _commit_all(wt, "feature patch")
    feature_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
    _git(wt, "push", "origin", f"HEAD:refs/heads/{branch}")

    _write(repo / "main-only.txt", "move main first\n")
    _commit_all(repo, "unrelated main change")
    _git(repo, "cherry-pick", feature_sha)
    _git(repo, "push", "origin", "main")
    _git(repo, "worktree", "remove", "--force", str(wt))
    _git(repo, "branch", "-D", branch)
    _git(repo, "fetch", "origin")

    report = _discover(repo, tmp_path / "home")
    candidate = _candidate(report, branch)

    assert candidate["ahead"] == 1
    assert candidate["patch_unique_count"] == 0
    assert candidate["patch_equivalent_count"] == 1
    assert candidate["recommendation"] == "skip-patch-equivalent"
    assert candidate["conflicts_with_base"] is False
    assert candidate["conflicting_files"] == []
    assert candidate["auto_land"] is False


def test_283_ref_fixture_discards_merged_remote_refs_before_candidate_analysis(tmp_path):
    repo = _init_repo(tmp_path)
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    for index in range(281):
        _git(repo, "update-ref", f"refs/remotes/origin/goal/merged-{index:03d}", base_sha)

    for branch, filename in (
        ("fix/AestheticcNext-pair1", "pair-one.txt"),
        ("fix/AestheticcNext-pair2", "pair-two.txt"),
    ):
        wt = _add_worktree(repo, tmp_path, branch)
        _write(wt / filename, f"{branch}\n")
        _commit_all(wt, branch)

    started = time.perf_counter()
    report = _discover(repo, tmp_path / "home")
    elapsed = time.perf_counter() - started

    assert report["ref_snapshot"]["goal_ref_count"] == 281
    assert report["skipped_merged_count"] == 281
    assert report["candidate_count"] == 2
    assert report["auto_land_count"] == 2
    assert report["sibling_analysis"] == {
        "selectable_candidate_count": 2,
        "pair_checks": 1,
    }
    # A generous regression ceiling: merged remote-only refs never enter the
    # candidate loop, and only one sibling pair requires merge analysis.
    assert elapsed < 20


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
    assert candidate["conflicting_files"] == ["app.txt"]
    assert candidate["hold_reasons"] == ["held-conflict"]
    assert candidate["auto_land"] is False
    assert candidate["recommendation"] == "held-conflict"
    assert candidate["finish_signal"] == "held-conflict"
    assert "HELD — conflicts with main; rebase required" in candidate["held_labels"]


def test_auto_land_blocked_by_modify_delete_base_conflict(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-delc1")
    _write(wt / "app.txt", "branch modifies file\n")
    _commit_all(wt, "modify file")
    _git(repo, "rm", "app.txt")
    _commit_all(repo, "delete file")

    candidate = _candidate(
        _discover(repo, tmp_path / "home"),
        "fix/AestheticcNext-delc1",
    )

    assert candidate["conflicts_with_base"] is True
    assert candidate["conflicting_files"] == ["app.txt"]
    assert candidate["recommendation"] == "held-conflict"
    assert candidate["auto_land"] is False


def test_alive_exact_worktree_process_blocks_even_when_transcript_is_stale(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "fix/AestheticcNext-live1")
    _write(wt / "feature.txt", "done\n")
    _commit_all(wt, "feature")
    home = tmp_path / "home"
    _named_active_session(home, wt, "stale-alive", "AestheticcNext-live1", 1)
    session_file = home / ".claude" / "sessions" / "stale-alive.json"
    record = json.loads(session_file.read_text(encoding="utf-8"))
    record["status"] = "waiting"
    session_file.write_text(json.dumps(record), encoding="utf-8")

    candidate = _candidate(_discover(repo, home), "fix/AestheticcNext-live1")

    assert candidate["session"]["process_alive"] is True
    assert candidate["live_session"] is True
    assert candidate["finish_signal"] == "blocked-session-active"
    assert candidate["auto_land"] is False


def test_land_batch_integration_branch_is_never_a_discovery_candidate(tmp_path):
    repo = _init_repo(tmp_path)
    wt = _add_worktree(repo, tmp_path, "land-batch/20260721-123456")
    _write(wt / "feature.txt", "in-progress integration work\n")
    _commit_all(wt, "integration merge")

    report = _discover(repo, tmp_path / "home")

    assert all(candidate["branch"] != "land-batch/20260721-123456" for candidate in report["candidates"])
    assert report["lock_queue"]["available"] is True


def test_kickback_lineage_labels_original_and_marker_identified_fix_branch(tmp_path):
    repo = _init_repo(tmp_path)
    original = _add_worktree(repo, tmp_path, "fix/AestheticcNext-orig1")
    _write(original / "feature.txt", "original feature\n")
    _commit_all(original, "original feature")

    fix = _add_worktree(repo, tmp_path, "goal/aestheticcnext-kb01")
    _write(fix / "fix.txt", "fix branch\n")
    _write(
        fix / ".claude" / "land-ready.json",
        json.dumps({"ready": True, "bead_id": "AestheticcNext-kb01"}),
    )
    _commit_all(fix, "kickback fix")

    home = tmp_path / "home"
    state_dir = home / ".claude" / "state" / "land-batch"
    state_dir.mkdir(parents=True)
    (state_dir / "kickbacks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "lineages": {
                    "fix/AestheticcNext-orig1": {
                        "bead_id": "AestheticcNext-kb01",
                        "session_name": "kickback-AestheticcNext-kb01",
                        "fix_branch": None,
                        "attempt": 1,
                        "dispatched_at": "2026-07-21T12:00:00Z",
                        "failure_summary": "src/form.tsx",
                        "signature": "files=src/form.tsx;tests=-",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = _discover(repo, home)
    original_candidate = _candidate(report, "fix/AestheticcNext-orig1")
    fix_candidate = _candidate(report, "goal/aestheticcnext-kb01")

    assert original_candidate["presentation"]["role"] == "kicked-back-original"
    assert original_candidate["presentation"]["label"] == (
        "KICKED BACK — fix in flight (bead AestheticcNext-kb01, "
        "session kickback-AestheticcNext-kb01)"
    )
    assert fix_candidate["presentation"] == {
        "role": "kickback-fix",
        "label": "KICKBACK FIX — fix/AestheticcNext-orig1 (bead AestheticcNext-kb01)",
        "original_branch": "fix/AestheticcNext-orig1",
        "state": "stalled",
    }


def test_remote_only_kickback_fix_is_identified_by_resolved_bead_without_marker(tmp_path):
    repo = _init_repo(tmp_path)
    _add_origin(repo, tmp_path)
    branch = "goal/aestheticcnext-kb02"
    _push_remote_only_branch(repo, tmp_path, branch)
    _write_beads(
        repo / ".beads" / "issues.jsonl",
        [{"id": "AestheticcNext-kb02", "status": "closed"}],
    )
    home = tmp_path / "home"
    state_dir = home / ".claude" / "state" / "land-batch"
    state_dir.mkdir(parents=True)
    (state_dir / "kickbacks.json").write_text(
        json.dumps(
            {
                "version": 1,
                "lineages": {
                    "fix/AestheticcNext-original": {
                        "bead_id": "AestheticcNext-kb02",
                        "session_name": "kickback-AestheticcNext-kb02",
                        "fix_branch": None,
                        "attempt": 1,
                        "failure_summary": "src/form.tsx",
                        "signature": "files=src/form.tsx;tests=-",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    candidate = _candidate(_discover(repo, home), branch)

    assert candidate["worktree_path"] is None
    assert candidate["land_ready"] is None
    assert candidate["bead_resolution"] == "exact"
    assert candidate["presentation"] == {
        "role": "kickback-fix",
        "label": "KICKBACK FIX — fix/AestheticcNext-original (bead AestheticcNext-kb02)",
        "original_branch": "fix/AestheticcNext-original",
        "state": "stalled",
    }
