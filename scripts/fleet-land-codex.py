#!/usr/bin/env python3
"""Land a completed Codex fleet worker branch onto AestheticcNext main."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO = Path("/Users/shane/Documents/GitReBase/AestheticcNext")
WORKER_ROOT = Path.home() / ".claude" / "fleet" / "codex-workers"
WORKTREE_ROOT = Path.home() / ".claude" / "worktrees"
BEAD_FILES = (".beads/issues.jsonl", ".beads/export-state.json")
CODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx")
OUTPUT_TAIL_LINES = 40


def run_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, f"{exc}\n")


def print_output_tail(command: list[str], output: str) -> None:
    print(f"command failed: {shlex.join(command)}")
    lines = output.rstrip().splitlines()
    if not lines:
        print("(no output)")
        return
    print("\n".join(lines[-OUTPUT_TAIL_LINES:]))


def cleanup_scratch(scratch: Path, branch: str) -> bool:
    ok = True
    remove = run_command(
        ["git", "worktree", "remove", str(scratch), "--force"], cwd=REPO
    )
    if remove.returncode != 0 and scratch.exists():
        print_output_tail(remove.args, remove.stdout)
        ok = False

    delete_branch = run_command(["git", "branch", "-D", branch], cwd=REPO)
    if delete_branch.returncode != 0:
        # A failed worktree add may not have created the scratch branch.
        branch_exists = run_command(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=REPO,
        )
        if branch_exists.returncode == 0:
            print_output_tail(delete_branch.args, delete_branch.stdout)
            ok = False
    return ok


def changed_files(worktree: Path) -> tuple[list[str] | None, str]:
    base = run_command(["git", "merge-base", "origin/main", "HEAD"], cwd=worktree)
    if base.returncode != 0 or not base.stdout.strip():
        return None, base.stdout

    diff = run_command(
        ["git", "diff", "--name-only", f"{base.stdout.strip()}..HEAD"],
        cwd=worktree,
    )
    if diff.returncode != 0:
        return None, diff.stdout
    return [line for line in diff.stdout.splitlines() if line], ""


def is_sensitive(path: str) -> bool:
    if path.startswith("drizzle/"):
        return path.endswith(".sql")
    return path.startswith(
        (
            "lib/db/",
            "lib/stripe/",
            "lib/auth/",
            "lib/payments/",
            "pages/api/auth/",
            "pages/api/admin/",
            "pages/api/webhooks/",
            "lib/email/templates/",
        )
    )


def run_gate(worktree: Path, files: list[str]) -> bool:
    commands = [["npm", "run", "typecheck"]]
    related = [path for path in files if path.endswith(CODE_SUFFIXES)]
    if related:
        commands.append(["npx", "jest", "--findRelatedTests", *related])

    for command in commands:
        result = run_command(command, cwd=worktree)
        if result.returncode == 0:
            continue
        run_command(["git", "reset", "--hard", "HEAD^"], cwd=worktree)
        print_output_tail(command, result.stdout)
        return False
    return True


def restore_bead_files(worktree: Path) -> bool:
    restored: list[str] = []
    for path in BEAD_FILES:
        result = run_command(["git", "checkout", "origin/main", "--", path], cwd=worktree)
        if result.returncode == 0:
            restored.append(path)

    if not restored:
        return True

    diff = run_command(
        ["git", "diff", "--cached", "--quiet", "HEAD", "--", *restored],
        cwd=worktree,
    )
    if diff.returncode == 0:
        return True
    if diff.returncode != 1:
        print_output_tail(diff.args, diff.stdout)
        return False

    amend = run_command(
        ["git", "commit", "--amend", "--no-edit", "--no-verify"], cwd=worktree
    )
    if amend.returncode != 0:
        print_output_tail(amend.args, amend.stdout)
        return False
    return True


def conflict_paths(worktree: Path) -> str:
    result = run_command(
        ["git", "diff", "--name-only", "--diff-filter=U"], cwd=worktree
    )
    return result.stdout.strip() or "(no unmerged paths reported)"


def is_non_fast_forward_rejection(output: str) -> bool:
    lowered = output.lower()
    return "non-fast-forward" in lowered or (
        "[rejected]" in lowered and "fetch first" in lowered
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        usage="%(prog)s <bead-id> [--dry-run] [--allow-sensitive]"
    )
    parser.add_argument("bead_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-sensitive", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bead_id = args.bead_id
    bead_lower = bead_id.lower()
    worker_dir = WORKER_ROOT / bead_id
    status_path = worker_dir / "status.txt"
    worker_worktree = WORKTREE_ROOT / f"goal-codex-{bead_lower}"
    blocked_path = worker_worktree / "BLOCKED.md"
    remote_branch = f"goal/{bead_lower}"

    try:
        status_line = status_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        print(f"status file not found: {status_path}")
        return 1
    except OSError as exc:
        print(f"could not read status file {status_path}: {exc}")
        return 1

    status = status_line.split(maxsplit=1)[0] if status_line else ""
    if status != "DONE":
        print(f"worker status is {status or '(empty)'}, not DONE")
        return 1

    review_path = worker_dir / "review.txt"
    # Warn, don't refuse: scripts/glm-review-branch.sh is a standalone quick-win
    # (quick-win 4), not yet wired into the dispatch queue -- most beads landed
    # today have no review.txt at all, and that must stay landable. This is
    # visibility for when a review WAS run, not a new gate.
    if not review_path.exists():
        print(f"note: no review.txt for {bead_id} (run scripts/glm-review-branch.sh {bead_id} first if you want one) -- proceeding without it")
    else:
        try:
            review_first_line = review_path.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            review_first_line = ""
        if review_first_line == "FAIL":
            print(f"WARNING: {review_path} says FAIL -- read it before landing. Proceeding anyway (this is a warning, not a gate).")
        elif review_first_line != "PASS":
            print(f"note: {review_path} exists but its first line is not PASS or FAIL ({review_first_line!r}) -- proceeding without treating it as a verdict")

    if blocked_path.exists():
        print(f"BLOCKED.md exists: {blocked_path}")
        try:
            contents = blocked_path.read_text(encoding="utf-8", errors="replace")
            print(contents, end="" if contents.endswith("\n") else "\n")
        except OSError as exc:
            print(f"could not read BLOCKED.md: {exc}")
        return 1

    remote = run_command(
        ["git", "ls-remote", "origin", f"refs/heads/{remote_branch}"], cwd=REPO
    )
    if remote.returncode != 0:
        print_output_tail(remote.args, remote.stdout)
        return 1
    if not remote.stdout.strip():
        print("branch not found on origin")
        return 1
    remote_sha = remote.stdout.split()[0]

    timestamp = int(time.time())
    land_branch = f"land-batch/{bead_id}-{timestamp}"
    scratch = REPO / ".claude" / "worktrees" / f"land-batch-{bead_id}-{timestamp}"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    clean_scratch = True
    preserve_scratch = False

    try:
        add = run_command(
            [
                "git",
                "worktree",
                "add",
                str(scratch),
                "-b",
                land_branch,
                "origin/main",
            ],
            cwd=REPO,
        )
        if add.returncode != 0:
            print_output_tail(add.args, add.stdout)
            return 1

        # A fresh worktree has no node_modules -- npm run typecheck fails with
        # MODULE_NOT_FOUND on tsc otherwise (confirmed live: this is exactly
        # what happened the first time this script ran against a real bead).
        # Symlink the main checkout's install rather than reinstalling --
        # matches the deps already used to build/test everything else tonight.
        main_node_modules = REPO / "node_modules"
        if main_node_modules.is_dir():
            try:
                (scratch / "node_modules").symlink_to(
                    main_node_modules, target_is_directory=True
                )
            except OSError as exc:
                print(f"warning: could not symlink node_modules: {exc}")

        merge = run_command(
            ["git", "merge", "--no-ff", "--no-verify", remote_sha], cwd=scratch
        )
        if merge.returncode != 0:
            print("merge conflict; unmerged paths:")
            print(conflict_paths(scratch))
            print(f"scratch worktree left for inspection: {scratch}")
            preserve_scratch = True
            return 1

        files, diff_error = changed_files(scratch)
        if files is None:
            print("could not determine changed files")
            if diff_error.strip():
                print(diff_error.rstrip())
            return 1

        sensitive = [path for path in files if is_sensitive(path)]
        if sensitive and not args.allow_sensitive:
            print("sensitive paths require --allow-sensitive:")
            print("\n".join(sensitive))
            return 1

        if not run_gate(scratch, files):
            return 1

        if args.dry_run:
            head = run_command(["git", "rev-parse", "HEAD"], cwd=scratch).stdout.strip()
            print("dry-run passed; would:")
            print("- restore .beads/issues.jsonl and .beads/export-state.json from origin/main, then amend if changed")
            print(f"- push {head or 'HEAD'} to origin/main, rebasing and retesting once on a non-fast-forward rejection")
            print(f"- remove scratch worktree and branch {land_branch}")
            print(f"- remove worker worktree {worker_worktree} and branch {remote_branch}")
            print(f'- run bd close {bead_id} --reason "Fix landed to main (<sha>). Landed via fleet-land-codex.py."')
            print(f"- append the landed record to {status_path}")
            return 0

        if not restore_bead_files(scratch):
            return 1

        push = run_command(["git", "push", "origin", "HEAD:main"], cwd=scratch)
        if push.returncode != 0:
            if not is_non_fast_forward_rejection(push.stdout):
                print_output_tail(push.args, push.stdout)
                return 1

            fetch = run_command(["git", "fetch", "origin", "main"], cwd=scratch)
            if fetch.returncode != 0:
                print_output_tail(push.args, push.stdout)
                print_output_tail(fetch.args, fetch.stdout)
                print(f"scratch worktree left for inspection: {scratch}")
                preserve_scratch = True
                return 1

            rebase = run_command(["git", "rebase", "origin/main"], cwd=scratch)
            if rebase.returncode != 0:
                print_output_tail(rebase.args, rebase.stdout)
                print("rebase conflict; unmerged paths:")
                print(conflict_paths(scratch))
                print(f"scratch worktree left for inspection: {scratch}")
                preserve_scratch = True
                return 1

            files, diff_error = changed_files(scratch)
            if files is None:
                print("could not determine changed files after rebase")
                if diff_error.strip():
                    print(diff_error.rstrip())
                return 1
            if not run_gate(scratch, files):
                return 1

            push = run_command(["git", "push", "origin", "HEAD:main"], cwd=scratch)
            if push.returncode != 0:
                print_output_tail(push.args, push.stdout)
                print(f"second push rejected; scratch worktree left for inspection: {scratch}")
                preserve_scratch = True
                return 1

        rev_parse = run_command(["git", "rev-parse", "HEAD"], cwd=scratch)
        if rev_parse.returncode != 0 or not rev_parse.stdout.strip():
            print_output_tail(rev_parse.args, rev_parse.stdout)
            return 1
        pushed_sha = rev_parse.stdout.strip()

        scratch_ok = cleanup_scratch(scratch, land_branch)
        clean_scratch = False

        post_land_ok = scratch_ok
        remove_worker = run_command(
            ["git", "worktree", "remove", str(worker_worktree), "--force"], cwd=REPO
        )
        if remove_worker.returncode != 0 and worker_worktree.exists():
            print_output_tail(remove_worker.args, remove_worker.stdout)
            post_land_ok = False

        delete_worker_branch = run_command(
            ["git", "branch", "-D", remote_branch], cwd=REPO
        )
        if delete_worker_branch.returncode != 0:
            branch_exists = run_command(
                [
                    "git",
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{remote_branch}",
                ],
                cwd=REPO,
            )
            if branch_exists.returncode == 0:
                print_output_tail(delete_worker_branch.args, delete_worker_branch.stdout)
                post_land_ok = False

        reason = f"Fix landed to main ({pushed_sha}). Landed via fleet-land-codex.py."
        close = run_command(["bd", "close", bead_id, "--reason", reason], cwd=REPO)
        if close.returncode != 0:
            print_output_tail(close.args, close.stdout)
            post_land_ok = False

        landed_at = datetime.now(timezone.utc).isoformat()
        try:
            with status_path.open("a", encoding="utf-8") as status_file:
                status_file.write(f"landed: {bead_id} @ {pushed_sha} at {landed_at}\n")
        except OSError as exc:
            print(f"could not append landed record to {status_path}: {exc}")
            post_land_ok = False

        if post_land_ok:
            print(f"landed: {bead_id} @ {pushed_sha}")
            return 0
        print(f"main was updated to {pushed_sha}, but one or more cleanup steps failed")
        return 1
    finally:
        if clean_scratch and not preserve_scratch:
            cleanup_scratch(scratch, land_branch)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:  # Keep fleet automation failures concise and traceback-free.
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
