#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RETIRED_FILE_PATH="${LAND_BATCH_RETIRED_FILE:-$SCRIPT_DIR/../retired.txt}"
DEFAULT_REPO="/Users/shane/Documents/GitReBase/AestheticcNext"

if [[ $# -gt 0 && -n "${1:-}" ]]; then
  TARGET_REPO="$1"
else
  TARGET_REPO="${REPO:-$DEFAULT_REPO}"
fi

export TARGET_REPO
export RETIRED_FILE="$RETIRED_FILE_PATH"
export SESSIONS_SCRIPT="$SCRIPT_DIR/sessions.py"
export LAND_STATE_SCRIPT="$SCRIPT_DIR/land-state.py"
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
LAND_STATE_SCRIPT = os.environ.get("LAND_STATE_SCRIPT")
REF_SNAPSHOT_MODE = os.environ.get("LAND_BATCH_REF_SNAPSHOT", "cached-remote-tracking")
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
        "ref_snapshot": {
            "mode": REF_SNAPSHOT_MODE,
            "mutated_by_discovery": False,
            "goal_ref_count": 0,
        },
        "skipped_merged_count": 0,
        "candidate_count": 0,
        "auto_land_count": 0,
        "candidates": [],
        "sibling_conflicts": [],
        "sibling_analysis": {"selectable_candidate_count": 0, "pair_checks": 0},
        "active_sessions": [],
        "lock_queue": load_lock_queue_state(),
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
    if path is None:
        return None
    if "/.claude/worktrees/" in path:
        return "agentview"
    if "/conductor/workspaces/" in path:
        return "conductor"
    return "worktree"


CANONICAL_BEAD_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>aestheticcnext|lucy)-"
    r"(?P<suffix>[a-z0-9]{4,6}(?:\.[0-9]+)*)",
    re.IGNORECASE,
)


def bead_file_paths(repo):
    configured = os.environ.get("LAND_BATCH_BEADS_FILES")
    if configured:
        return [Path(path) for path in configured.split(os.pathsep) if path]
    return [
        Path(repo) / ".beads" / "issues.jsonl",
        Path.home() / "Documents" / "Obsidian" / ".beads" / "issues.jsonl",
    ]


def load_beads(repo):
    """Load current bead identities/status once from code and vault JSONL."""
    beads = {}
    for path in bead_file_paths(repo):
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    bead_id = record.get("id") if isinstance(record, dict) else None
                    if not isinstance(bead_id, str) or not bead_id:
                        continue
                    beads[bead_id.casefold()] = {
                        "id": bead_id,
                        "status": record.get("status"),
                        "source": str(path),
                    }
        except OSError as exc:
            diag(f"warning: could not read bead file {path}: {exc}")
    return beads


def _bead_aliases(bead_id):
    prefix, separator, suffix = bead_id.partition("-")
    if not separator or prefix.casefold() not in ("aestheticcnext", "lucy"):
        return []
    prefix = prefix.casefold()
    suffix = suffix.casefold()
    hyphen_suffix = suffix.replace(".", "-")
    aliases = [
        (f"{prefix}-{suffix}", "exact", True),
        (suffix, "alias", False),
    ]
    if hyphen_suffix != suffix:
        aliases.extend(
            [
                (f"{prefix}-{hyphen_suffix}", "alias", True),
                (hyphen_suffix, "alias", False),
            ]
        )
    return aliases


def _alias_present(source, alias):
    pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9]|[.-]\d)"
    return re.search(pattern, source.casefold()) is not None


def build_bead_alias_index(beads):
    """Index every accepted exact/legacy alias once for cheap branch joins."""
    index = {}
    for record in beads.values():
        bead_id = record["id"]
        segment_count = bead_id.count(".") + 1
        for alias, resolution, prefixed in _bead_aliases(bead_id):
            entry = {
                "score": (segment_count, int(prefixed), len(alias)),
                "bead_id": bead_id,
                "resolution": resolution,
                "record": record,
                "alias": alias,
            }
            index.setdefault(alias.casefold(), []).append(entry)
    return index


def _matching_alias_entries(source, alias_index):
    """Find indexed aliases with the same boundaries as ``_alias_present``."""
    text = source.casefold()
    ascii_alnum = set("abcdefghijklmnopqrstuvwxyz0123456789")
    matches = []
    seen = set()
    for start in range(len(text)):
        if start and text[start - 1] in ascii_alnum:
            continue
        for end in range(start + 1, len(text) + 1):
            if end < len(text):
                following = text[end]
                if following in ascii_alnum:
                    continue
                if following in ".-" and end + 1 < len(text) and text[end + 1].isdigit():
                    continue
            alias = text[start:end]
            for entry in alias_index.get(alias, ()):
                key = (entry["bead_id"].casefold(), entry["alias"])
                if key not in seen:
                    seen.add(key)
                    matches.append(entry)
    return matches


def resolve_bead(branch, worktree_path, alias_index):
    """Resolve exact/dotted and legacy-hyphen branch slugs without guessing."""
    sources = [branch]
    if worktree_path:
        sources.append(os.path.basename(worktree_path.rstrip(os.sep)))

    matches = []
    for source in sources:
        for entry in _matching_alias_entries(source, alias_index):
            matches.append(
                (
                    entry["score"],
                    entry["bead_id"],
                    entry["resolution"],
                    entry["record"],
                )
            )

    if matches:
        best_score = max(match[0] for match in matches)
        best_by_id = {}
        for score, bead_id, resolution, record in matches:
            if score != best_score:
                continue
            existing = best_by_id.get(bead_id.casefold())
            if existing is None or resolution == "exact":
                best_by_id[bead_id.casefold()] = (bead_id, resolution, record)
        best = list(best_by_id.values())
        if len(best) == 1:
            bead_id, resolution, record = best[0]
            return {
                "bead_id": bead_id,
                "bead_resolution": resolution,
                "bead_candidates": [bead_id],
                "bead_status": record.get("status"),
            }
        return {
            "bead_id": None,
            "bead_resolution": "ambiguous",
            "bead_candidates": sorted(item[0] for item in best),
            "bead_status": None,
        }

    # Preserve the existing syntactic bead_id enrichment when the bead stores
    # are absent/stale, but do not call it exact or trust it for CLOSED.
    for source in sources:
        match = CANONICAL_BEAD_RE.search(source)
        if match:
            prefix = "LUCY" if match.group("prefix").casefold() == "lucy" else "AestheticcNext"
            bead_id = f"{prefix}-{match.group('suffix')}"
            return {
                "bead_id": bead_id,
                "bead_resolution": "none",
                "bead_candidates": [],
                "bead_status": None,
            }
    return {
        "bead_id": None,
        "bead_resolution": "none",
        "bead_candidates": [],
        "bead_status": None,
    }


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


def batched_ref_metadata(repo, base_ref, source_refs):
    """Resolve ahead/behind and commit dates for all surviving refs at once."""
    if not source_refs:
        return {}
    proc = run_git(
        [
            "-C",
            repo,
            "for-each-ref",
            f"--format=%(refname)%00%(objectname)%00%(ahead-behind:{base_ref})%00%(committerdate:iso-strict)",
            *source_refs,
        ],
        timeout=120,
    )
    if proc.returncode != 0:
        diag(f"warning: batched ref metadata failed: {proc.stderr.strip()}")
        return {}
    metadata = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\0")
        if len(parts) != 4:
            continue
        ref, tip_sha, counts, committed_at = (part.strip() for part in parts)
        count_parts = counts.split()
        if not ref or not tip_sha or len(count_parts) != 2:
            continue
        try:
            ahead, behind = (int(value) for value in count_parts)
        except ValueError:
            continue
        metadata[ref] = {
            "tip_sha": tip_sha,
            "ahead": ahead,
            "behind": behind,
            "last_commit_date": committed_at or None,
        }
    return metadata


def patch_counts(repo, base_ref, branch_ref):
    """Count patch-unique/equivalent non-merge commits with ``git cherry``."""
    proc = run_git(["-C", repo, "cherry", base_ref, branch_ref], timeout=120)
    if proc.returncode != 0:
        diag(f"warning: git cherry failed for {branch_ref}: {proc.stderr.strip()}")
        return None, None
    unique = 0
    equivalent = 0
    for line in proc.stdout.splitlines():
        if line.startswith("+"):
            unique += 1
        elif line.startswith("-"):
            equivalent += 1
    return unique, equivalent


def remote_goal_refs(repo, base_ref):
    """Return cached origin/goal refs with one batched ancestry classification.

    Remote-only refs already merged into the pinned base are discarded before
    candidate construction. Keep this batched: per-ref ``rev-list`` here made
    the real repository's interactive discovery path several times slower.
    """
    proc = run_git(
        [
            "-C",
            repo,
            "for-each-ref",
            "--format=%(refname)%00%(objectname)",
            "refs/remotes/origin/goal/",
        ],
        timeout=30,
    )
    if proc.returncode != 0:
        diag(f"warning: remote goal ref enumeration failed: {proc.stderr.strip()}")
        return []
    merged_proc = run_git(
        [
            "-C",
            repo,
            "for-each-ref",
            f"--merged={base_ref}",
            "--format=%(refname)",
            "refs/remotes/origin/goal/",
        ],
        timeout=30,
    )
    if merged_proc.returncode != 0:
        # Fail open for visibility: an ancestry-query failure must not hide a
        # potentially unlanded branch.
        diag(f"warning: merged remote goal ref query failed: {merged_proc.stderr.strip()}")
        merged_refs = set()
    else:
        merged_refs = {ref.strip() for ref in merged_proc.stdout.splitlines() if ref.strip()}

    refs = []
    for line in proc.stdout.splitlines():
        ref, separator, tip_sha = line.partition("\0")
        ref = ref.strip()
        tip_sha = tip_sha.strip()
        if not separator or not ref or not tip_sha or ref.endswith("/HEAD"):
            continue
        branch = ref[len("refs/remotes/origin/") :] if ref.startswith("refs/remotes/origin/") else None
        if branch and tip_sha:
            refs.append(
                {
                    "branch": branch,
                    "source_ref": ref,
                    "tip_sha": tip_sha,
                    "merged_into_base": ref in merged_refs,
                }
            )
    return refs


# Incidental dirt that does NOT travel when a branch is merged (what lands is the
# committed branch, not the worktree's uncommitted state) and so must not block a
# landable worktree: build output, lockfile churn, and beads state churn.
JUNK_DIRT_RE = re.compile(
    r"(^|/)(node_modules|\.next|dist|build|coverage|\.turbo)(/|$)"
    r"|(^|/)(issues\.jsonl|export-state\.json|\.DS_Store|bun\.lock|bun\.lockb|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$"
    r"|^\.beads/"
    r"|(^|/)\.build-[^/]*$"
)


def _porcelain_lines(worktree_path):
    proc = run_git(["-C", worktree_path, "status", "--porcelain"], timeout=30)
    if proc.returncode != 0:
        diag(f"warning: status failed for {worktree_path}: {proc.stderr.strip()}")
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def worktree_cleanliness(worktree_path):
    """Return exact and effective cleanliness from one status snapshot."""
    if worktree_path is None:
        return None, None
    lines = _porcelain_lines(worktree_path)
    if lines is None:
        return False, False
    clean = lines == []
    effectively_clean = True
    for line in lines:
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ")[-1].strip()
        if not JUNK_DIRT_RE.search(path):
            effectively_clean = False
            break
    return clean, effectively_clean


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
    if token in ("tree.", "failure to merge"):
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
    has_conflict = any(
        marker in text
        for marker in ("<<<<<<<", "changed in both", "removed in local", "removed in remote", "CONFLICT")
    )
    paths = parse_conflict_paths_from_text(text) if has_conflict else []
    return has_conflict, paths


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


def _prefer_session(new, old):
    if bool(new.get("active")) != bool(old.get("active")):
        return bool(new.get("active"))
    return (new.get("updated_at") or 0) > (old.get("updated_at") or 0)


def load_sessions():
    """Run sessions.py (stdlib-only) and preserve every session record."""
    script = os.environ.get("SESSIONS_SCRIPT")
    if not script or not os.path.exists(script):
        diag("warning: sessions.py not found; session enrichment skipped")
        return {}, []
    try:
        proc = subprocess.run(
            [sys.executable, script, "--all"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        diag(f"warning: sessions.py failed to run: {exc}")
        return {}, []
    if proc.returncode != 0:
        diag(f"warning: sessions.py exited {proc.returncode}: {proc.stderr.strip()}")
        return {}, []
    try:
        payload = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        diag(f"warning: sessions.py returned invalid JSON: {exc}")
        return {}, []
    if isinstance(payload, list):
        sessions_all = [record for record in payload if isinstance(record, dict)]
    elif isinstance(payload, dict):
        # Backward-compatible fallback if an older sessions.py is supplied.
        sessions_all = []
        for cwd, record in payload.items():
            if isinstance(record, dict):
                sessions_all.append(dict(record, real_cwd=cwd, cwd=record.get("cwd") or cwd))
    else:
        sessions_all = []
    by_cwd = {}
    for record in sessions_all:
        cwd = record.get("real_cwd") or record.get("cwd")
        if not cwd:
            continue
        try:
            key = os.path.realpath(cwd)
        except OSError:
            key = cwd
        existing = by_cwd.get(key)
        if existing is None or _prefer_session(record, existing):
            by_cwd[key] = record
    return by_cwd, sessions_all


def session_for_bead(sessions_all, bead_id):
    if not bead_id:
        return None
    matches = []
    for record in sessions_all:
        name = record.get("name") or ""
        if any(_alias_present(name, alias) for alias, _resolution, _prefixed in _bead_aliases(bead_id)):
            matches.append(record)
    if not matches:
        return None
    selected = matches[0]
    for record in matches[1:]:
        if _prefer_session(record, selected):
            selected = record
    return dict(selected, joined_by="bead-id")


def load_lock_queue_state():
    """Expose admission state to every discovery consumer without mutating it."""
    if not LAND_STATE_SCRIPT or not os.path.exists(LAND_STATE_SCRIPT):
        return {"available": False, "error": "land-state.py not found"}
    try:
        proc = subprocess.run(
            [sys.executable, LAND_STATE_SCRIPT, "status"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc)}
    if proc.returncode != 0:
        return {"available": False, "error": proc.stderr.strip() or f"land-state.py exited {proc.returncode}"}
    try:
        state = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        return {"available": False, "error": f"invalid lock state JSON: {exc}"}
    return {"available": True, **state}


def kickback_presentation(branch, bead_id, marker, lineages):
    """Presentation only: preserve candidate facts while making kickbacks clear."""
    entry = lineages.get(branch)
    if isinstance(entry, dict):
        bead_id = entry.get("bead_id") or "unknown"
        session_name = entry.get("session_name") or "unknown"
        return {
            "role": "kicked-back-original",
            "label": f"KICKED BACK — fix in flight (bead {bead_id}, session {session_name})",
            "state": entry.get("state"),
        }

    marker_bead = marker.get("bead_id") if isinstance(marker, dict) else None
    candidate_bead = bead_id or marker_bead
    for original_branch, candidate in lineages.items():
        if not isinstance(candidate, dict):
            continue
        if branch == candidate.get("fix_branch") or (
            candidate_bead and candidate_bead.casefold() == (candidate.get("bead_id") or "").casefold()
        ):
            return {
                "role": "kickback-fix",
                "label": f"KICKBACK FIX — {original_branch} (bead {candidate.get('bead_id') or 'unknown'})",
                "original_branch": original_branch,
                "state": candidate.get("state"),
            }
    return None


def live_session_worktrees(worktree_paths):
    """Worktrees that currently host a LIVE ``claude`` process, keyed by cwd.

    The sessions.py join only sees ``~/.claude/sessions/*.json``, which Agent
    View's harness-isolated sessions do NOT register with a worktree cwd — so it
    goes blind to a Claude actively working inside a worktree and every candidate
    looks session-less (the autonomy bug). This scans live processes directly:
    any ``claude`` pid whose cwd is at/under a worktree path marks that worktree
    active. Strictly additive — it can only BLOCK a worktree from auto-landing,
    never approve one. Best-effort: if ps/lsof are unavailable it returns empty
    (degrades to today's behaviour, never worse)."""
    real_to_path = {}
    for path in worktree_paths:
        try:
            real_to_path[os.path.realpath(path)] = path
        except OSError:
            real_to_path[path] = path
    active = set()
    if not real_to_path:
        return active
    try:
        ps = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        diag(f"warning: ps scan for live sessions failed: {exc}")
        return active
    self_pid = str(os.getpid())
    pids = []
    for line in ps.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, cmd = line.partition(" ")
        if not pid.isdigit() or pid == self_pid:
            continue
        low = cmd.lower()
        # the Claude CLI process; skip our own tooling so we don't self-flag
        if "claude" in low and "discover.sh" not in low and "sessions.py" not in low and "land-batch" not in low:
            pids.append(pid)
    for pid in pids:
        try:
            lsof = subprocess.run(
                ["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in lsof.stdout.splitlines():
            if not line.startswith("n"):
                continue
            cwd = line[1:].strip()
            try:
                cwd_real = os.path.realpath(cwd)
            except OSError:
                cwd_real = cwd
            for wt_real in real_to_path:
                if cwd_real == wt_real or cwd_real.startswith(wt_real + os.sep):
                    active.add(wt_real)
    return active


def read_marker(worktree_path):
    """Read the worktree's .claude/land-ready.json finish marker (D2)."""
    if worktree_path is None:
        return None
    marker_path = os.path.join(worktree_path, ".claude", "land-ready.json")
    if not os.path.exists(marker_path):
        return None
    try:
        with open(marker_path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        diag(f"warning: unreadable land-ready marker {marker_path}: {exc}")
        return {"_invalid": True}


def finish_gate(
    recommendation,
    has_marker,
    session,
    clean,
    ahead,
    source_kind,
    finish_evidence,
    premise_state,
):
    """Deterministic finish judgment (ENG_REVIEW.md D2).

    Marker is optional confidence metadata; heuristic state grants auto-land.
    Branch-only completion evidence is reported but never grants auto-land in
    this release. Returns (auto_land: bool, finish_signal: str, finished: bool).
    """
    if recommendation == "review-sensitive":
        return False, "held-sensitive", bool(finish_evidence.get("qualified"))
    if recommendation != "land":
        return False, recommendation, bool(finish_evidence.get("qualified"))
    if session and session.get("active"):
        return False, "blocked-session-active", False
    if session and session.get("has_open_loop"):
        return False, "blocked-open-loop", False
    if source_kind == "remote-branch":
        if not finish_evidence.get("bead_resolved"):
            return False, "held-branch-only-bead-unresolved", False
        if not finish_evidence.get("bead_closed"):
            return False, "held-branch-only-bead-not-closed", False
        if not finish_evidence.get("patch_unique") or not finish_evidence.get("tip_pinned"):
            return False, "held-branch-only-finish-unverified", False
        if premise_state != "review-required":
            return False, "held-branch-only-premise-state-invalid", True
        return False, "held-branch-only-premise-review", True
    if not clean or ahead < 1:
        return False, "blocked-not-clean-or-no-commits", False
    return True, "finished", True


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
    sessions_map, sessions_all = load_sessions()
    lock_queue = load_lock_queue_state()
    kickback_lineages = (
        lock_queue.get("kickbacks", {}).get("lineages", {})
        if isinstance(lock_queue.get("kickbacks"), dict)
        else {}
    )
    retired_patterns = load_retired_patterns(RETIRED_FILE)
    beads = load_beads(REPO)
    bead_alias_index = build_bead_alias_index(beads)
    wt_proc = run_git(["-C", REPO, "worktree", "list", "--porcelain"], timeout=30)
    if wt_proc.returncode != 0:
        diag(f"warning: worktree list failed: {wt_proc.stderr.strip()}")
        print(json.dumps(empty_report(repo_field=repo_field, base_ref=base_ref, base_sha=base_sha), separators=(",", ":")))
        return

    try:
        main_repo_real = os.path.realpath(REPO)
    except OSError:
        main_repo_real = REPO

    worktree_records = parse_worktrees(wt_proc.stdout)
    live_worktrees = live_session_worktrees(
        [rec.get("worktree") for rec in worktree_records if rec.get("worktree")]
    )

    candidate_sources = []
    worktree_branches = set()
    for record in worktree_records:
        worktree_path = record.get("worktree")
        branch = branch_from_record(record)
        if not worktree_path or not branch:
            continue
        if "/.git/beads-worktrees/" in worktree_path:
            continue
        # A prior /land-batch scratch is never a candidate for another run.
        # Without this exclusion, parallel discovery can recurse into an active
        # integration branch and try to land a run's own in-progress work.
        if branch.startswith("land-batch/"):
            continue
        if os.path.realpath(worktree_path) == main_repo_real:
            continue

        branch_ref = f"refs/heads/{branch}"
        tip_sha = record.get("HEAD") or verify_commit(REPO, branch_ref)
        if not tip_sha:
            continue

        candidate_sources.append(
            {
                "branch": branch,
                "worktree_path": worktree_path,
                "source_kind": "worktree",
                "source_ref": branch_ref,
                "tip_sha": tip_sha,
            }
        )
        worktree_branches.add(branch)

    cached_remote_goals = remote_goal_refs(REPO, base_ref)
    skipped_merged_count = 0
    for remote in cached_remote_goals:
        if remote["branch"] in worktree_branches:
            continue
        if remote["merged_into_base"]:
            skipped_merged_count += 1
            continue
        candidate_sources.append(
            {
                "branch": remote["branch"],
                "source_ref": remote["source_ref"],
                "tip_sha": remote["tip_sha"],
                "worktree_path": None,
                "source_kind": "remote-branch",
            }
        )

    ref_metadata = batched_ref_metadata(
        REPO,
        base_ref,
        [source["source_ref"] for source in candidate_sources],
    )

    candidates = []
    for source in candidate_sources:
        worktree_path = source["worktree_path"]
        branch = source["branch"]
        branch_ref = source["source_ref"]
        tip_sha = source["tip_sha"]

        bead = resolve_bead(branch, worktree_path, bead_alias_index)
        bead_id = bead["bead_id"]
        metadata = ref_metadata.get(branch_ref)
        if metadata and metadata["tip_sha"] == tip_sha:
            ahead = metadata["ahead"]
            behind = metadata["behind"]
            last_commit_date = metadata["last_commit_date"]
            try:
                committed_at = datetime.fromisoformat(last_commit_date.replace("Z", "+00:00"))
                age_days = max(0, (generated_at - committed_at.astimezone(timezone.utc)).days)
            except (AttributeError, ValueError):
                age_days = None
        else:
            # A missing/moved ref fails back to exact per-source reads rather
            # than accepting incomplete batch metadata.
            ahead, behind = ahead_behind(REPO, base_ref, tip_sha)
            last_commit_date, age_days = tip_date_and_age(REPO, tip_sha, generated_at)
        clean, effectively_clean = worktree_cleanliness(worktree_path)
        retired_by_pattern = any(
            pattern in branch or (bead_id is not None and pattern in bead_id)
            for pattern in retired_patterns
        )
        stale = bool(age_days is not None and age_days > 45)
        retired = bool(retired_by_pattern)
        patch_unique_count = None
        patch_equivalent_count = None
        conflicts_with_base = False
        conflicting_files = []
        branch_changed_files = []
        sensitive_paths = []
        touches_sensitive = False
        hold_reasons = []

        if retired_by_pattern:
            recommendation = "skip-retired"
        elif ahead == 0:
            recommendation = "skip-merged"
        else:
            patch_unique_count, patch_equivalent_count = patch_counts(REPO, base_ref, branch_ref)
            if patch_unique_count is None:
                recommendation = "held-patch-analysis"
            elif patch_unique_count == 0:
                recommendation = "skip-patch-equivalent"
            elif bead["bead_resolution"] == "ambiguous":
                recommendation = "held-bead-ambiguous"
            else:
                conflicts_with_base, conflicting_files = merge_conflicts(REPO, base_ref, branch_ref)
                branch_changed_files = changed_files(REPO, base_ref, branch_ref)
                sensitive_paths = sensitive_files(branch_changed_files)
                touches_sensitive = bool(sensitive_paths)
                if conflicts_with_base:
                    hold_reasons.append("held-conflict")
                if stale:
                    hold_reasons.append("held-stale")
                if touches_sensitive:
                    hold_reasons.append("held-sensitive")
                if stale:
                    recommendation = "held-stale"
                elif conflicts_with_base:
                    recommendation = "held-conflict"
                elif touches_sensitive:
                    recommendation = "review-sensitive"
                else:
                    recommendation = "land"

        if worktree_path is not None:
            try:
                worktree_real = os.path.realpath(worktree_path)
            except OSError:
                worktree_real = worktree_path
        else:
            worktree_real = None
        session = sessions_map.get(worktree_real) if worktree_real is not None else None
        if session is None and bead_id:
            # bg /goal jobs often share cwd=$HOME. Match the fully resolved bead
            # identity across the all-session list rather than a collapsed cwd.
            session = session_for_bead(sessions_all, bead_id)
        exact_cwd_process = bool(
            source["source_kind"] == "worktree"
            and session
            and session.get("process_alive")
            and (session.get("real_cwd") or session.get("cwd")) == worktree_real
        )
        live_session = (
            worktree_real in live_worktrees if worktree_real is not None else False
        ) or exact_cwd_process
        if live_session:
            # A live `claude` process is cwd'd inside this worktree. Force active
            # even if sessions.py's cwd-join missed it (the Agent View blind spot).
            if session is None:
                session = {"active": True, "status": "live-process", "joined_by": "live-process"}
            else:
                session = dict(session, active=True, joined_by=session.get("joined_by") or "live-process")
        marker = read_marker(worktree_path)
        has_marker = bool(marker and not marker.get("_invalid") and marker.get("ready") is True)
        bead_closed = (
            bead["bead_resolution"] in ("exact", "alias")
            and isinstance(bead["bead_status"], str)
            and bead["bead_status"].casefold() == "closed"
        )
        finish_evidence = {
            "bead_resolved": bead["bead_resolution"] in ("exact", "alias"),
            "bead_closed": bead_closed,
            "tip_pinned": bool(branch_ref and tip_sha),
            "patch_unique": bool(patch_unique_count is not None and patch_unique_count > 0),
            "session_inactive": not bool(session and (session.get("active") or session.get("has_open_loop"))),
        }
        finish_evidence["qualified"] = bool(
            source["source_kind"] == "remote-branch"
            and finish_evidence["bead_resolved"]
            and finish_evidence["bead_closed"]
            and finish_evidence["tip_pinned"]
            and finish_evidence["patch_unique"]
            and finish_evidence["session_inactive"]
        )
        premise_state = "review-required" if source["source_kind"] == "remote-branch" else "preserved-worktree-gate"
        auto_land, finish_signal, finished = finish_gate(
            recommendation,
            has_marker,
            session,
            effectively_clean,
            ahead,
            source["source_kind"],
            finish_evidence,
            premise_state,
        )
        held_labels = []
        if source["source_kind"] == "remote-branch" and finish_evidence["qualified"]:
            held_labels.append("HELD — branch-only completion; premise review required")
        elif source["source_kind"] == "remote-branch" and patch_unique_count and patch_unique_count > 0:
            held_labels.append("HELD — branch-only source; completion evidence incomplete")
        if conflicts_with_base:
            held_labels.append("HELD — conflicts with main; rebase required")
        if stale:
            held_labels.append("HELD — stale branch; premise review required")
        if bead["bead_resolution"] == "ambiguous":
            held_labels.append("HELD — ambiguous bead identity; resolution required")
        presentation = kickback_presentation(branch, bead_id, marker, kickback_lineages)

        pr = pr_by_branch.get(branch, {})
        candidates.append(
            {
                "worktree_path": worktree_path,
                "branch": branch,
                "root": classify_root(worktree_path),
                "source_kind": source["source_kind"],
                "source_ref": branch_ref,
                "tip_sha": tip_sha,
                "bead_id": bead_id,
                "bead_resolution": bead["bead_resolution"],
                "bead_candidates": bead["bead_candidates"],
                "bead_status": bead["bead_status"],
                "pr_number": pr.get("number"),
                "pr_title": pr.get("title"),
                "ahead": ahead,
                "behind": behind,
                "patch_unique_count": patch_unique_count,
                "patch_equivalent_count": patch_equivalent_count,
                "clean": clean,
                "effectively_clean": effectively_clean,
                "last_commit_date": last_commit_date,
                "age_days": age_days,
                "conflicts_with_base": conflicts_with_base,
                "conflicting_files": conflicting_files,
                "touches_sensitive": touches_sensitive,
                "sensitive_paths": sensitive_paths,
                "hold_reasons": hold_reasons,
                "held_labels": held_labels,
                "retired": retired,
                "retired_by_pattern": retired_by_pattern,
                "stale": stale,
                "recommendation": recommendation,
                "has_marker": has_marker,
                "land_ready": marker,
                "session": session,
                "live_session": live_session,
                "auto_land": auto_land,
                "finish_signal": finish_signal,
                "finished": finished,
                "finish_evidence": finish_evidence,
                "premise_state": premise_state,
                "presentation": presentation,
                "_branch_ref": branch_ref,
            }
        )

    sibling_conflicts = []
    selectable = [
        candidate
        for candidate in candidates
        if candidate.get("auto_land")
        and (candidate.get("presentation") or {}).get("role") != "kicked-back-original"
    ]
    sibling_pair_checks = 0
    for index, left in enumerate(selectable):
        for right in selectable[index + 1 :]:
            sibling_pair_checks += 1
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
            "cwd": rec.get("real_cwd") or rec.get("cwd"),
            "pid": rec.get("pid"),
            "name": rec.get("name"),
            "kind": rec.get("kind"),
            "status": rec.get("status"),
            "idle_min": rec.get("idle_min"),
        }
        for rec in sessions_all
        if rec.get("active")
    ]

    report = {
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "repo": repo_field,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "ref_snapshot": {
            "mode": REF_SNAPSHOT_MODE,
            "mutated_by_discovery": False,
            "goal_ref_count": len(cached_remote_goals),
        },
        "skipped_merged_count": skipped_merged_count,
        "candidate_count": len(candidates),
        "auto_land_count": sum(1 for c in candidates if c.get("auto_land")),
        "candidates": candidates,
        "sibling_conflicts": sibling_conflicts,
        "sibling_analysis": {
            "selectable_candidate_count": len(selectable),
            "pair_checks": sibling_pair_checks,
        },
        "active_sessions": active_sessions,
        "lock_queue": lock_queue,
    }
    print(json.dumps(report, separators=(",", ":")))


if __name__ == "__main__":
    main()
PY
