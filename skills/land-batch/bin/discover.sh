#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETIRED_FILE_PATH="$SCRIPT_DIR/../retired.txt"
DEFAULT_REPO="/Users/shane/Documents/GitReBase/AestheticcNext"

if [[ $# -gt 0 && -n "${1:-}" ]]; then
  TARGET_REPO="$1"
else
  TARGET_REPO="${REPO:-$DEFAULT_REPO}"
fi

export TARGET_REPO
export RETIRED_FILE="$RETIRED_FILE_PATH"
export SESSIONS_SCRIPT="$SCRIPT_DIR/sessions.py"
export GIT_OPTIONAL_LOCKS=0

python3 <<'PY'
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ.get("TARGET_REPO") or "/Users/shane/Documents/GitReBase/AestheticcNext"
RETIRED_FILE = os.environ.get("RETIRED_FILE")
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


def diag(message):
    print(message, file=sys.stderr)


def run_git(args, cwd=None, timeout=60):
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
        check=False,
    )


def run_git_bytes(args, cwd=None, timeout=60):
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
        check=False,
    )


def now_utc():
    return datetime.now(timezone.utc)


def empty_report(repo_field=None, base_ref=None, base_sha=None):
    return {
        "generated_at": now_utc().isoformat().replace("+00:00", "Z"),
        "repo": repo_field,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "candidate_count": 0,
        "candidates": [],
        "sibling_conflicts": [],
    }


def parse_owner_repo(origin_url):
    if not origin_url:
        return None
    url = origin_url.strip()
    patterns = (
        r"^[\w.-]+@[^:]+:([^/]+)/(.+?)(?:\.git)?$",
        r"^https?://[^/]+/([^/]+)/(.+?)(?:\.git)?/?$",
        r"^ssh://[\w.-]+@[^/]+/([^/]+)/(.+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    return url[:-4] if url.endswith(".git") else url


def get_origin_repo(repo):
    proc = run_git(["-C", repo, "remote", "get-url", "origin"], timeout=10)
    if proc.returncode != 0:
        return None
    return parse_owner_repo(proc.stdout.strip())


def verify_commit(repo, ref):
    proc = run_git(["-C", repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], timeout=10)
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def choose_base(repo):
    origin_main = verify_commit(repo, "refs/remotes/origin/main")
    if origin_main:
        return "origin/main", origin_main
    local_main = verify_commit(repo, "refs/heads/main")
    if local_main:
        return "main", local_main
    return None, None


def parse_worktrees(output):
    worktrees = []
    current = {}
    for raw_line in output.splitlines():
        line = raw_line.rstrip("\n")
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    if current:
        worktrees.append(current)
    return worktrees


def branch_from_record(record):
    branch_ref = record.get("branch")
    if not branch_ref or branch_ref == "detached":
        return None
    if branch_ref.startswith("refs/heads/"):
        return branch_ref[len("refs/heads/") :]
    return None


def classify_root(path):
    if "/.claude/worktrees/" in path:
        return "agentview"
    if "/conductor/workspaces/" in path:
        return "conductor"
    return "worktree"


def parse_bead_id(branch, worktree_path):
    sources = [branch]
    if worktree_path:
        sources.append(os.path.basename(worktree_path.rstrip(os.sep)))

    for source in sources:
        lucy_match = re.search(r"(?<![A-Za-z0-9])(?i:lucy-)([a-z0-9]{4})\b", source)
        if lucy_match:
            return "LUCY-" + lucy_match.group(1)

        aestheticcnext_match = re.search(r"(?<![A-Za-z0-9])AestheticcNext-([a-z0-9]{4,5})\b", source)
        if aestheticcnext_match:
            return "AestheticcNext-" + aestheticcnext_match.group(1)

    return None


def load_retired_patterns(path):
    if not path:
        return []
    retired_path = Path(path)
    if not retired_path.exists():
        return []
    patterns = []
    try:
        for line in retired_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                patterns.append(stripped)
    except OSError as exc:
        diag(f"warning: could not read retired file: {exc}")
    return patterns


def ahead_behind(repo, base_ref, branch_ref):
    proc = run_git(["-C", repo, "rev-list", "--left-right", "--count", f"{base_ref}...{branch_ref}"], timeout=30)
    if proc.returncode != 0:
        diag(f"warning: rev-list failed for {branch_ref}: {proc.stderr.strip()}")
        return 0, 0
    parts = proc.stdout.strip().split()
    if len(parts) != 2:
        return 0, 0
    behind, ahead = int(parts[0]), int(parts[1])
    return ahead, behind


# Incidental dirt that does NOT travel when a branch is merged (what lands is the
# committed branch, not the worktree's uncommitted state) and so must not block a
# landable worktree: build output, lockfile churn, beads log, goal scaffolding,
# and this repo's generated content manifests.
JUNK_DIRT_RE = re.compile(
    r"(^|/)(node_modules|\.next|dist|build|coverage|\.turbo)(/|$)"
    r"|(^|/)(issues\.jsonl|export-state\.json|\.DS_Store|bun\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|PLAN\.md)$"
    r"|^\.beads/"
    r"|^\.plans/"
    r"|^lib/manifests/"
    r"|(^|/)\.build-[^/]*$"
)


def _porcelain_lines(worktree_path):
    proc = run_git(["-C", worktree_path, "status", "--porcelain"], timeout=30)
    if proc.returncode != 0:
        diag(f"warning: status failed for {worktree_path}: {proc.stderr.strip()}")
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def is_clean(worktree_path):
    lines = _porcelain_lines(worktree_path)
    return lines == []


def is_effectively_clean(worktree_path):
    """Clean once incidental junk is ignored — the real 'still mid-work' signal
    is uncommitted *source*, not build output / lockfile / beads churn."""
    lines = _porcelain_lines(worktree_path)
    if lines is None:
        return False
    for line in lines:
        path = line[3:].strip()
        if " -> " in path:  # rename: judge the destination
            path = path.split(" -> ")[-1].strip()
        if not JUNK_DIRT_RE.search(path):
            return False
    return True


def tip_date_and_age(repo, branch_ref, generated_at):
    proc = run_git(["-C", repo, "show", "-s", "--format=%cI", branch_ref], timeout=20)
    if proc.returncode != 0:
        diag(f"warning: show failed for {branch_ref}: {proc.stderr.strip()}")
        return None, None
    iso = proc.stdout.strip() or None
    if not iso:
        return None, None
    try:
        committed_at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        age_days = max(0, (generated_at - committed_at.astimezone(timezone.utc)).days)
    except ValueError:
        age_days = None
    return iso, age_days


def changed_files(repo, base_ref, branch_ref):
    proc = run_git(["-C", repo, "diff", "--name-only", f"{base_ref}...{branch_ref}"], timeout=60)
    if proc.returncode != 0:
        diag(f"warning: diff failed for {branch_ref}: {proc.stderr.strip()}")
        return []
    return [line for line in proc.stdout.splitlines() if line]


def sensitive_files(paths):
    return [path for path in paths if path.startswith(SENSITIVE_PREFIXES)]


def looks_like_path(token):
    """Reject merge-tree noise: tree OIDs, stage numbers, info messages."""
    token = (token or "").strip()
    if not token:
        return False
    if token.startswith("Auto-merging") or token.startswith("CONFLICT"):
        return False
    if re.fullmatch(r"[0-9a-f]{7,64}", token):  # bare SHA / tree OID
        return False
    if re.fullmatch(r"\d+", token):  # bare stage number
        return False
    return "/" in token or "." in token  # real paths have a dir sep or extension


def is_beads_noise(path):
    """Beads churn (issues.jsonl / export-state.json) collides on nearly every
    branch and is not a real code conflict — wherever git reports its path."""
    base = os.path.basename(path)
    return path.startswith(".beads/") or base in ("issues.jsonl", "export-state.json")


def parse_conflict_paths_from_text(text):
    paths = []
    for line in text.splitlines():
        match = re.search(r"CONFLICT \([^)]+\): .* in (.+)$", line)
        if match:
            paths.append(match.group(1).strip())
            continue
        match = re.match(r"^\d{6}\s+[0-9a-f]{40,64}\s+[123]\t(.+)$", line)
        if match:
            paths.append(match.group(1).strip())
            continue
        match = re.match(r"^\s*(?:base|our|their)\s+\d{6}\s+[0-9a-f]{40,64}\s+(.+)$", line)
        if match:
            paths.append(match.group(1).strip())
    return sorted({path for path in paths if looks_like_path(path)})


def merge_tree_write_tree(repo, left_ref, right_ref, merge_base=None):
    args = ["-C", repo, "merge-tree", "--write-tree", "--messages", left_ref, right_ref]
    if merge_base:
        args = ["-C", repo, "merge-tree", "--write-tree", "--messages", "--merge-base", merge_base, left_ref, right_ref]
    proc = run_git(args, timeout=120)
    unsupported = proc.returncode != 0 and (
        "unknown option" in proc.stderr.lower()
        or "usage:" in proc.stderr.lower()
        or "error: unknown" in proc.stderr.lower()
    )
    if unsupported:
        return None
    has_conflict = proc.returncode != 0 or "CONFLICT (" in proc.stdout or "CONFLICT (" in proc.stderr
    paths = parse_conflict_paths_from_text(proc.stdout + "\n" + proc.stderr)
    if proc.returncode != 0 and not paths and "CONFLICT (" not in proc.stdout and "CONFLICT (" not in proc.stderr:
        return None
    if has_conflict:
        name_args = ["-C", repo, "merge-tree", "--write-tree", "--name-only", "-z", left_ref, right_ref]
        if merge_base:
            name_args = [
                "-C",
                repo,
                "merge-tree",
                "--write-tree",
                "--name-only",
                "-z",
                "--merge-base",
                merge_base,
                left_ref,
                right_ref,
            ]
        name_proc = run_git_bytes(name_args, timeout=120)
        if name_proc.returncode != 0 and name_proc.stdout:
            # -z output leads with the written tree OID, then conflicted file
            # names, then informational messages ("Auto-merging …"). Skip the
            # leading OID and keep only tokens that look like real paths.
            for raw in name_proc.stdout.split(b"\0")[1:]:
                token = raw.decode("utf-8", errors="replace").strip()
                if looks_like_path(token):
                    paths.append(token)
    return has_conflict, sorted({path for path in paths if looks_like_path(path)})


def merge_tree_three_arg(repo, left_ref, right_ref, merge_base):
    proc = run_git(["-C", repo, "merge-tree", merge_base, left_ref, right_ref], timeout=120)
    text = proc.stdout + "\n" + proc.stderr
    has_conflict = "<<<<<<<" in text or "changed in both" in text or "CONFLICT" in text
    return has_conflict, parse_conflict_paths_from_text(text)


def merge_conflicts(repo, left_ref, right_ref, merge_base=None):
    modern = merge_tree_write_tree(repo, left_ref, right_ref, merge_base=merge_base)
    if modern is not None:
        return modern
    mb = merge_base
    if not mb:
        proc = run_git(["-C", repo, "merge-base", left_ref, right_ref], timeout=30)
        if proc.returncode != 0:
            diag(f"warning: merge-base failed for {left_ref} {right_ref}: {proc.stderr.strip()}")
            return True, []
        mb = proc.stdout.strip()
    if not mb:
        return True, []
    return merge_tree_three_arg(repo, left_ref, right_ref, mb)


def get_prs(repo):
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName", "--limit", "60"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        diag(f"warning: gh pr list unavailable: {exc}")
        return {}
    if proc.returncode != 0:
        diag(f"warning: gh pr list failed: {proc.stderr.strip()}")
        return {}
    try:
        prs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        diag(f"warning: gh pr list returned invalid JSON: {exc}")
        return {}
    return {pr.get("headRefName"): pr for pr in prs if pr.get("headRefName")}


def load_sessions_map():
    """Run sessions.py (stdlib-only) → {realpath(cwd): session record}."""
    script = os.environ.get("SESSIONS_SCRIPT")
    if not script or not os.path.exists(script):
        diag("warning: sessions.py not found; session enrichment skipped")
        return {}
    try:
        proc = subprocess.run(
            [sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        diag(f"warning: sessions.py failed to run: {exc}")
        return {}
    if proc.returncode != 0:
        diag(f"warning: sessions.py exited {proc.returncode}: {proc.stderr.strip()}")
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        diag(f"warning: sessions.py returned invalid JSON: {exc}")
        return {}


def read_marker(worktree_path):
    """Read the worktree's .claude/land-ready.json finish marker (D2)."""
    marker_path = os.path.join(worktree_path, ".claude", "land-ready.json")
    if not os.path.exists(marker_path):
        return None
    try:
        with open(marker_path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        diag(f"warning: unreadable land-ready marker {marker_path}: {exc}")
        return {"_invalid": True}


def finish_gate(recommendation, has_marker, session, clean, ahead):
    """Deterministic finish judgment (ENG_REVIEW.md D2).

    Marker is the only thing that grants auto-land; the chat tail can only veto.
    Returns (auto_land: bool, finish_signal: str).
    """
    if recommendation == "review-sensitive":
        return False, "held-sensitive"
    if recommendation != "land":
        return False, recommendation  # skip-merged / skip-conflict / skip-retired
    if session and session.get("active"):
        return False, "blocked-session-active"
    if session and session.get("has_open_loop"):
        return False, "blocked-open-loop"
    if not clean or ahead < 1:
        return False, "blocked-not-clean-or-no-commits"
    if has_marker:
        return True, "marker"
    # legacy worktree, no marker: clean + result sentinel = low-confidence, NOT
    # auto-landed on the first run — surfaced for explicit opt-in instead.
    if session and session.get("sentinel") == "result":
        return False, "legacy-low-confidence"
    return False, "no-marker-no-signal"


def main():
    generated_at = now_utc()
    repo_field = get_origin_repo(REPO)
    if run_git(["-C", REPO, "rev-parse", "--git-dir"], timeout=10).returncode != 0:
        diag(f"warning: not a git repo: {REPO}")
        print(json.dumps(empty_report(repo_field=repo_field), separators=(",", ":")))
        return

    base_ref, base_sha = choose_base(REPO)
    if not base_ref or not base_sha:
        diag("warning: neither origin/main nor local main could be resolved")
        print(json.dumps(empty_report(repo_field=repo_field, base_ref=base_ref, base_sha=base_sha), separators=(",", ":")))
        return

    pr_by_branch = get_prs(REPO)
    sessions_map = load_sessions_map()
    retired_patterns = load_retired_patterns(RETIRED_FILE)
    wt_proc = run_git(["-C", REPO, "worktree", "list", "--porcelain"], timeout=30)
    if wt_proc.returncode != 0:
        diag(f"warning: worktree list failed: {wt_proc.stderr.strip()}")
        print(json.dumps(empty_report(repo_field=repo_field, base_ref=base_ref, base_sha=base_sha), separators=(",", ":")))
        return

    try:
        main_repo_real = os.path.realpath(REPO)
    except OSError:
        main_repo_real = REPO

    candidates = []
    for record in parse_worktrees(wt_proc.stdout):
        worktree_path = record.get("worktree")
        branch = branch_from_record(record)
        if not worktree_path or not branch:
            continue
        if "/.git/beads-worktrees/" in worktree_path:
            continue
        if os.path.realpath(worktree_path) == main_repo_real:
            continue

        branch_ref = f"refs/heads/{branch}"
        tip_sha = verify_commit(REPO, branch_ref)
        if not tip_sha:
            continue

        bead_id = parse_bead_id(branch, worktree_path)
        ahead, behind = ahead_behind(REPO, base_ref, branch_ref)
        clean = is_clean(worktree_path)
        effectively_clean = is_effectively_clean(worktree_path)
        last_commit_date, age_days = tip_date_and_age(REPO, branch_ref, generated_at)
        conflicts_with_base, conflicting_files = merge_conflicts(REPO, base_ref, branch_ref)
        branch_changed_files = changed_files(REPO, base_ref, branch_ref)
        sensitive_paths = sensitive_files(branch_changed_files)
        touches_sensitive = bool(sensitive_paths)
        retired_by_pattern = any(
            pattern in branch or (bead_id is not None and pattern in bead_id)
            for pattern in retired_patterns
        )
        retired = bool(retired_by_pattern or (age_days is not None and age_days > 45))
        if retired:
            recommendation = "skip-retired"
        elif ahead == 0:
            recommendation = "skip-merged"
        elif conflicts_with_base:
            recommendation = "skip-conflict"
        elif touches_sensitive:
            recommendation = "review-sensitive"
        else:
            recommendation = "land"

        try:
            worktree_real = os.path.realpath(worktree_path)
        except OSError:
            worktree_real = worktree_path
        session = sessions_map.get(worktree_real)
        if session is None and bead_id:
            # bg /goal jobs run with cwd=$HOME, not the worktree, so the cwd join
            # misses them. Fall back to matching the bead short-id in the session
            # name (e.g. "goal x8ftu bead" → AestheticcNext-x8ftu).
            short = bead_id.split("-")[-1].lower()
            if short:
                for rec in sessions_map.values():
                    if short in (rec.get("name") or "").lower():
                        session = dict(rec, joined_by="bead-name")
                        break
        marker = read_marker(worktree_path)
        has_marker = bool(marker and not marker.get("_invalid") and marker.get("ready") is True)
        auto_land, finish_signal = finish_gate(recommendation, has_marker, session, effectively_clean, ahead)

        pr = pr_by_branch.get(branch, {})
        candidates.append(
            {
                "worktree_path": worktree_path,
                "branch": branch,
                "root": classify_root(worktree_path),
                "bead_id": bead_id,
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title"),
                "ahead": ahead,
                "behind": behind,
                "clean": clean,
                "effectively_clean": effectively_clean,
                "last_commit_date": last_commit_date,
                "age_days": age_days,
                "conflicts_with_base": conflicts_with_base,
                "conflicting_files": conflicting_files,
                "touches_sensitive": touches_sensitive,
                "sensitive_paths": sensitive_paths,
                "retired": retired,
                "recommendation": recommendation,
                "has_marker": has_marker,
                "land_ready": marker,
                "session": session,
                "auto_land": auto_land,
                "finish_signal": finish_signal,
                "_branch_ref": branch_ref,
            }
        )

    sibling_conflicts = []
    active = [c for c in candidates if not c["retired"] and not c["conflicts_with_base"]]
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            mb_proc = run_git(["-C", REPO, "merge-base", left["_branch_ref"], right["_branch_ref"]], timeout=30)
            if mb_proc.returncode != 0 or not mb_proc.stdout.strip():
                diag(f"warning: merge-base failed for {left['branch']} {right['branch']}: {mb_proc.stderr.strip()}")
                continue
            conflicts, files = merge_conflicts(REPO, left["_branch_ref"], right["_branch_ref"], merge_base=mb_proc.stdout.strip())
            if conflicts:
                real_files = [f for f in files if not is_beads_noise(f)]
                # Nearly every branch touches the beads log; a pair that collides
                # ONLY there is beads churn, not a real code conflict.
                if files and not real_files:
                    continue
                sibling_conflicts.append(
                    {
                        "branch_a": left["branch"],
                        "branch_b": right["branch"],
                        "files": real_files or files,
                    }
                )

    for candidate in candidates:
        candidate.pop("_branch_ref", None)

    active_sessions = [
        {
            "cwd": cwd,
            "pid": rec.get("pid"),
            "name": rec.get("name"),
            "kind": rec.get("kind"),
            "status": rec.get("status"),
            "idle_min": rec.get("idle_min"),
        }
        for cwd, rec in sorted(sessions_map.items())
        if rec.get("active")
    ]

    report = {
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "repo": repo_field,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "candidate_count": len(candidates),
        "auto_land_count": sum(1 for c in candidates if c.get("auto_land")),
        "candidates": candidates,
        "sibling_conflicts": sibling_conflicts,
        "active_sessions": active_sessions,
    }
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
PY
