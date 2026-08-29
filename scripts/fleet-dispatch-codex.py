#!/usr/bin/env python3
"""Dispatch scoped beads to detached Codex CLI workers.

Claude Code's PreToolUse guard rejects a raw Bash command that contains both a
``codex exec`` invocation and a backgrounding marker. The safe, empirically
validated pattern is deliberately two-step: this dispatcher writes a standalone
executable ``run.sh`` whose Codex invocation is foregrounded inside that file,
then starts only the wrapper path with ``subprocess.Popen`` in a new session. A
normal Python process is not itself constrained by the Bash hook, but retaining
the wrapper boundary makes the launch safe when this dispatcher is invoked from
Claude Code and lets the worker outlive the dispatcher.

Usage:
  fleet-dispatch-codex.py dispatch <bead-id> [--dry-run] [--force]
                                  [--pre-claimed <codex-slot-token>]
  fleet-dispatch-codex.py status [<bead-id>]
  fleet-dispatch-codex.py list
"""
import datetime
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys


HOME = os.path.expanduser("~")
EXISTING_DISPATCHER = os.path.join(HOME, ".claude/scripts/fleet-dispatch.py")
SLOTS = os.path.join(HOME, ".claude/scripts/fleet-slots.py")
CLAUDE_DISPATCHER = os.path.join(HOME, ".claude/scripts/fleet-dispatch.py")
CODEX_DISPATCHER = os.path.join(HOME, ".claude/scripts/fleet-dispatch-codex.py")
WORKTREES_ROOT = os.path.join(HOME, ".claude/worktrees")
WORKERS_ROOT = os.path.join(HOME, ".claude/fleet/codex-workers")
MANIFEST = os.path.join(WORKERS_ROOT, "manifest.jsonl")
BEAD_ID_RE = re.compile(r"^(?:AestheticcNext|LUCY)-[a-z0-9.]+$", re.I)


def load_existing_dispatcher():
    spec = importlib.util.spec_from_file_location("fleet_dispatch", EXISTING_DISPATCHER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load existing dispatcher: {EXISTING_DISPATCHER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISPATCHER = load_existing_dispatcher()
REPO = DISPATCHER.REPO


def sh(cmd, cwd=None, timeout=60, env=None):
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            env=run_env,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 127, "", str(exc)


def utc_now():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def validate_bead_id(bead):
    if not BEAD_ID_RE.fullmatch(bead):
        raise ValueError(
            f"invalid bead id {bead!r}; expected AestheticcNext-... or LUCY-..."
        )


def fetch_bead(bead):
    env = {"BEADS_DIR": DISPATCHER.beads_dir(bead)}
    code, out, err = sh(
        ["bd", "show", bead, "--json"], cwd=REPO, timeout=60, env=env
    )
    if code != 0 or not out:
        raise RuntimeError(f"bd show {bead} failed: {err or out or 'no output'}")
    try:
        records = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bd show {bead} returned invalid JSON: {exc}") from exc
    record = records[0] if isinstance(records, list) and records else records
    if not isinstance(record, dict):
        raise RuntimeError(f"bd show {bead} did not return an issue object")
    return {
        "title": record.get("title") or "",
        "description": record.get("description") or "",
        "acceptance_criteria": record.get("acceptance_criteria") or "",
    }


def bead_slug(bead):
    return re.sub(r"[^a-z0-9]+", "-", bead.lower()).strip("-")


def run_checked(cmd, cwd=None, timeout=120):
    code, out, err = sh(cmd, cwd=cwd, timeout=timeout)
    if code != 0:
        rendered = " ".join(shlex.quote(part) for part in cmd)
        raise RuntimeError(f"{rendered} failed: {err or out or 'no output'}")
    return out


def create_worktree(bead, branch):
    worktree = os.path.join(WORKTREES_ROOT, f"goal-codex-{bead_slug(bead)}")
    run_checked(["git", "fetch", "origin", "main"], cwd=REPO, timeout=180)
    os.makedirs(WORKTREES_ROOT, exist_ok=True)
    if os.path.lexists(worktree):
        raise RuntimeError(f"worktree path already exists: {worktree}")
    run_checked(
        ["git", "worktree", "add", "-b", branch, worktree, "origin/main"],
        cwd=REPO,
        timeout=180,
    )

    cwd_modules = os.path.join(os.getcwd(), "node_modules")
    repo_modules = os.path.join(REPO, "node_modules")
    source_modules = None
    if (
        os.path.realpath(os.getcwd()) == os.path.realpath(REPO)
        and os.path.isdir(cwd_modules)
    ):
        source_modules = cwd_modules
    elif os.path.isdir(repo_modules):
        source_modules = repo_modules
    if source_modules:
        os.symlink(
            os.path.abspath(source_modules),
            os.path.join(worktree, "node_modules"),
            target_is_directory=True,
        )
    return worktree


def build_prompt(bead, fields, worktree, branch):
    return f"""You are the implementation worker for bead {bead}.

Working directory: {worktree}
Branch: {branch}

The bead fields below are reproduced verbatim.

--- TITLE START ---
{fields['title']}
--- TITLE END ---

--- DESCRIPTION START ---
{fields['description']}
--- DESCRIPTION END ---

--- ACCEPTANCE CRITERIA START ---
{fields['acceptance_criteria']}
--- ACCEPTANCE CRITERIA END ---

Read and follow the repository's AGENTS.md and other applicable project instructions.
Implement the requested fix completely in the working directory above. Run the relevant
tests and typecheck needed to verify the implementation. Commit the changes with a commit
message that references {bead}, then push the branch to origin with the same branch name
using this exact refspec command:

git push origin HEAD:{branch}

Do not merge to main. Do not deploy anything.

NEVER run `next build` (or `npm run build` / any production build step). It is not required
for verification here, and a production build under Next 15 can use several GB of RAM per
worker — with several workers running at once this has genuinely brought the host machine down.
Typecheck + lint + targeted/relevant Jest is the complete gate; nothing here needs a build. This
is enforced technically (a watchdog kills any `next build` process it finds), so attempting it
just wastes your own time.

If you are truly blocked, or the task needs a judgment call that only a human or Claude
session can make, do not guess. Write a clear explanation to {worktree}/BLOCKED.md that
states exactly what is blocking the task, then stop without pushing.
"""


def write_wrapper(bead, worktree, branch, prompt, slot_token):
    worker_dir = os.path.join(WORKERS_ROOT, bead)
    prompt_path = os.path.join(worker_dir, "prompt.txt")
    wrapper_path = os.path.join(worker_dir, "run.sh")
    status_path = os.path.join(worker_dir, "status.txt")
    output_path = os.path.join(worker_dir, "output.log")
    os.makedirs(worker_dir, exist_ok=True)

    with open(prompt_path, "w", encoding="utf-8") as handle:
        handle.write(prompt)

    q = shlex.quote
    wrapper = f"""#!/bin/bash
set -u -o pipefail

WORKTREE={q(worktree)}
BRANCH={q(branch)}
BEAD_ID={q(bead)}
PROMPT_FILE={q(prompt_path)}
STATUS_FILE={q(status_path)}
SLOTS={q(SLOTS)}
CLAUDE_DISPATCHER={q(CLAUDE_DISPATCHER)}
CODEX_DISPATCHER={q(CODEX_DISPATCHER)}
REPO_GIT_DIR={q(REPO)}/.git
slot_token={q(slot_token)}
started_epoch=$(date +%s)

finish_worker() {{
  worker_exit=$?
  trap - EXIT INT TERM

  if [[ -n "${{watchdog_pid:-}}" ]]; then
    kill "$watchdog_pid" 2>/dev/null || true
  fi

  if [[ -n "$slot_token" ]]; then
    python3 "$SLOTS" release-codex-glm "$slot_token" || true
  fi

  echo "fleet auto-drain: $BEAD_ID released its codex_glm slot; checking shared queue"
  next_info=$(python3 "$SLOTS" dequeue-next --with-kind)
  dequeue_exit=$?
  if [[ $dequeue_exit -ne 0 ]]; then
    echo "fleet auto-drain: dequeue-next failed (exit $dequeue_exit)" >&2
  elif [[ "$next_info" == "NONE" ]]; then
    echo "fleet auto-drain: queue empty or next item does not fit"
  else
    read -r next_bead next_kind next_token <<< "$next_info"
    echo "fleet auto-drain: dequeued $next_bead (kind=$next_kind); redispatching"
    dispatch_exit=0
    case "$next_kind" in
      codex)
        python3 "$CODEX_DISPATCHER" dispatch "$next_bead" --pre-claimed "$next_token" || dispatch_exit=$?
        ;;
      goal)
        python3 "$CLAUDE_DISPATCHER" "$next_bead" --pre-claimed || dispatch_exit=$?
        ;;
      long-goal)
        python3 "$CLAUDE_DISPATCHER" "$next_bead" --epic --pre-claimed || dispatch_exit=$?
        ;;
      *)
        echo "fleet auto-drain: unsupported queue kind '$next_kind'" >&2
        dispatch_exit=64
        ;;
    esac
    if [[ $dispatch_exit -ne 0 ]]; then
      # Both dispatchers release their pre-claimed slot on every failure path.
      # Restore the exact queue kind so a later drainer can try it again.
      python3 "$SLOTS" enqueue "$next_bead" "$next_kind"
      echo "fleet auto-drain: redispatch failed (exit $dispatch_exit); re-enqueued $next_bead (kind=$next_kind)" >&2
    else
      echo "fleet auto-drain: redispatched $next_bead (kind=$next_kind)"
    fi
  fi

  finished_epoch=$(date +%s)
  duration_seconds=$((finished_epoch - started_epoch))
  finished_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  final_status="FAILED"

  if [[ -f "$WORKTREE/BLOCKED.md" ]]; then
    final_status="BLOCKED"
  else
    # Trust origin, not the worktree's own on-disk HEAD: Codex's sandbox has been
    # observed pushing via alternate git metadata when it can't write the linked
    # worktree's shared .git/worktrees/<name>/ directory (fixed below via --add-dir,
    # but keep this origin-first check as the durable source of truth regardless --
    # a pushed branch is what /land-batch actually cares about, not local HEAD).
    remote_head=$(git -C "$WORKTREE" ls-remote --heads origin "refs/heads/$BRANCH" 2>/dev/null | awk 'NR == 1 {{print $1}}')
    if [[ -n "$remote_head" ]]; then
      final_status="DONE"
    fi
  fi

  printf '%s finished_at=%s duration_seconds=%s exit_code=%s\n' \
    "$final_status" "$finished_at" "$duration_seconds" "$worker_exit" > "$STATUS_FILE"
  exit "$worker_exit"
}}

trap finish_worker EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$WORKTREE" || exit 1
python3 "$SLOTS" attach-codex-glm-pid "$slot_token" "$$" >/dev/null || exit 1

# `next build` (production build) has repeatedly brought the host down when several
# workers run it concurrently -- each one can pull several GB of RAM under Next 15,
# and nothing in this worker's actual job (typecheck/lint/targeted-jest) needs it.
# The prompt above tells Codex not to; this watchdog makes it genuinely can't: poll
# for any `next build`/`next start` process rooted in THIS worktree and kill it the
# moment it appears, before it gets far enough to matter.
(
  while true; do
    sleep 5
    pids=$(pgrep -f "next (build|start)" 2>/dev/null || true)
    for pid in $pids; do
      pwdx_path=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep '^n' | cut -c2-)
      if [[ "$pwdx_path" == "$WORKTREE"* ]]; then
        echo "watchdog: killing forbidden next build/start (pid $pid) in $WORKTREE" >> {q(output_path)}
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
  done
) &
watchdog_pid=$!

echo "starting Codex worker for $BEAD_ID on $BRANCH"
# A linked git worktree shares almost everything with the main repo's .git/ --
# not just per-worktree metadata (HEAD, index, locks under .git/worktrees/<name>/),
# but the OBJECT DATABASE itself (.git/objects/, .git/refs/, packed-refs) that every
# worktree writes into on commit. codex exec's workspace-write sandbox only grants
# the worktree dir + /tmp by default, so git operations fail piecemeal depending on
# which shared path it touches first: "Unable to create .../index.lock" (confirmed,
# AestheticcNext-jq7vf) or "unable to create temporary file... failed to insert into
# database" when it reaches .git/objects (confirmed, AestheticcNext-cvpck) -- a
# narrower --add-dir scoped to just .git/worktrees/<name>/ fixed the first failure
# mode but not the second. Grant the WHOLE main repo .git/ dir instead: that's where
# every shared write actually lands, and it still doesn't touch the worktree's own
# checked-out files outside REPO_GIT_DIR, which stay sandboxed as normal.
codex exec -s workspace-write --add-dir "$REPO_GIT_DIR" "$(cat "$PROMPT_FILE")"
worker_exit=$?
exit "$worker_exit"
"""
    with open(wrapper_path, "w", encoding="utf-8") as handle:
        handle.write(wrapper)
    os.chmod(wrapper_path, 0o755)
    try:
        os.unlink(status_path)
    except FileNotFoundError:
        pass
    return wrapper_path, output_path


def append_manifest(row):
    os.makedirs(WORKERS_ROOT, exist_ok=True)
    with open(MANIFEST, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def launch_wrapper(wrapper_path, output_path):
    with open(output_path, "ab", buffering=0) as output:
        process = subprocess.Popen(
            [wrapper_path],
            stdout=output,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return process.pid


def release_codex_slot(slot_token):
    if not slot_token:
        return
    sh([sys.executable, SLOTS, "release-codex-glm", slot_token])


def dispatch_command(bead, dry_run=False, force=False, pre_claimed=None):
    validate_bead_id(bead)
    failed, _failures = DISPATCHER.run_gate(bead, False)
    if failed and not force:
        print(f"\nREFUSED. {bead} is not dispatchable.")
        print("Fix the failed pre-dispatch checks, or re-run with --force.")
        if pre_claimed:
            release_codex_slot(pre_claimed)
            print("Released pre-claimed codex_glm slot after gate refusal.")
        return 1
    if failed and force:
        print("\nWARNING: gate failed but --force was given; continuing.")
    if dry_run:
        print("\n--dry-run: gate only, nothing dispatched.")
        if pre_claimed:
            release_codex_slot(pre_claimed)
            print("Released pre-claimed codex_glm slot; dry-run started no worker.")
        return 0

    slot_token = pre_claimed
    if not slot_token:
        claim_code, claim_out, _claim_err = sh(
            [sys.executable, SLOTS, "claim-codex-glm", "codex", bead]
        )
        if claim_code != 0 or not claim_out:
            sh([sys.executable, SLOTS, "enqueue", bead, "codex"])
            print(f"\n⏸ AT CAPACITY — {bead} queued, not dispatched.")
            print(f"   Run `python3 {SLOTS} status` to see current load.")
            print("   A slot frees when any running Claude or Codex worker finishes;")
            print("   its closing phase dispatches the next queued bead automatically.")
            return 3
        slot_token = claim_out

    try:
        fields = fetch_bead(bead)
        branch = f"goal/{bead.lower()}"
        worktree = create_worktree(bead, branch)
        prompt = build_prompt(bead, fields, worktree, branch)
        wrapper_path, output_path = write_wrapper(
            bead, worktree, branch, prompt, slot_token
        )
        pid = launch_wrapper(wrapper_path, output_path)
    except Exception:
        release_codex_slot(slot_token)
        raise
    row = {
        "bead_id": bead,
        "worktree_path": worktree,
        "branch": branch,
        "wrapper_pid": pid,
        "started_at": utc_now(),
        "status": "running",
    }
    append_manifest(row)
    status_path = os.path.join(WORKERS_ROOT, bead, "status.txt")
    print(
        f"{bead}: worktree={worktree} branch={branch} pid={pid} "
        f"status={status_path}"
    )
    return 0


def read_manifest():
    if not os.path.isfile(MANIFEST):
        return []
    rows = []
    with open(MANIFEST, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"warning: skipping malformed manifest line {line_number}: {exc}",
                    file=sys.stderr,
                )
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def pid_alive(pid):
    if not isinstance(pid, int) and not str(pid).isdigit():
        return False
    code, _, _ = sh(["ps", "-p", str(pid), "-o", "pid="], timeout=5)
    return code == 0


STATUS_RE = re.compile(
    r"^(DONE|FAILED|BLOCKED)\b.*?duration_seconds=(\d+)(?:\s|$)"
)


def read_final_status(bead):
    path = os.path.join(WORKERS_ROOT, bead, "status.txt")
    try:
        with open(path, encoding="utf-8") as handle:
            line = handle.read().strip().splitlines()[-1]
    except (FileNotFoundError, IndexError):
        return "FAILED", None, "status.txt is missing"
    match = STATUS_RE.search(line)
    if not match:
        return "FAILED", None, f"unrecognized status line: {line}"
    return match.group(1), int(match.group(2)), line


def parse_started_at(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def elapsed_seconds(row, final_duration=None):
    if final_duration is not None:
        return final_duration
    started = parse_started_at(row.get("started_at"))
    if started is None:
        return None
    return max(
        0,
        int((datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()),
    )


def format_duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def effective_status(row):
    if pid_alive(row.get("wrapper_pid")):
        return "running", elapsed_seconds(row), "wrapper PID is alive"
    status, duration, detail = read_final_status(row.get("bead_id", ""))
    return status, elapsed_seconds(row, duration), detail


def print_table(headers, rows):
    if not rows:
        print("(none)")
        return
    widths = [len(header) for header in headers]
    rendered = [[str(value) for value in row] for row in rows]
    for row in rendered:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rendered:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def status_all(rows):
    table_rows = []
    for row in rows:
        status, elapsed, _ = effective_status(row)
        table_rows.append(
            (
                row.get("bead_id", ""),
                status,
                format_duration(elapsed),
                row.get("branch", ""),
            )
        )
    print_table(("bead_id", "status", "elapsed", "branch"), table_rows)
    return 0


def tail_lines(path, count=20):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.readlines()[-count:]
    except FileNotFoundError:
        return []


def remote_branch_head(worktree, branch):
    code, out, _ = sh(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=worktree,
        timeout=60,
    )
    if code != 0 or not out:
        return None
    return out.split()[0]


def status_one(rows, bead):
    matches = [
        row for row in rows if row.get("bead_id", "").lower() == bead.lower()
    ]
    if not matches:
        print(f"no manifest entry for {bead}", file=sys.stderr)
        return 1
    row = matches[-1]
    status, elapsed, status_detail = effective_status(row)
    worktree = row.get("worktree_path", "")
    branch = row.get("branch", "")
    worker_dir = os.path.join(WORKERS_ROOT, row.get("bead_id", bead))
    output_path = os.path.join(worker_dir, "output.log")
    blocked_path = os.path.join(worktree, "BLOCKED.md")

    print(f"bead_id: {row.get('bead_id')}")
    print(f"status: {status}")
    print(f"elapsed: {format_duration(elapsed)}")
    print(f"started_at: {row.get('started_at')}")
    print(f"wrapper_pid: {row.get('wrapper_pid')}")
    print(f"worktree: {worktree}")
    print(f"branch: {branch}")
    print(f"status detail: {status_detail}")

    print("\noutput.log (last 20 lines):")
    lines = tail_lines(output_path)
    if lines:
        for line in lines:
            print(line.rstrip("\n"))
    else:
        print("(no output)")

    if os.path.isfile(blocked_path):
        print("\nBLOCKED.md: present")
        with open(blocked_path, encoding="utf-8", errors="replace") as handle:
            print(handle.read().rstrip())
    else:
        print("\nBLOCKED.md: absent")

    local_head = None
    ahead = None
    if os.path.isdir(worktree):
        code, out, _ = sh(["git", "rev-parse", "HEAD"], cwd=worktree)
        if code == 0:
            local_head = out
        code, out, _ = sh(
            ["git", "rev-list", "--count", "origin/main..HEAD"], cwd=worktree
        )
        if code == 0 and out.isdigit():
            ahead = int(out)
    remote_head = remote_branch_head(worktree, branch) if os.path.isdir(worktree) else None
    print(f"commits ahead of origin/main: {ahead if ahead is not None else 'unknown'}")
    print(
        "pushed to origin with the same branch name: "
        + ("yes" if local_head and local_head == remote_head else "no")
    )
    if local_head:
        print(f"local HEAD: {local_head}")
    if remote_head:
        print(f"origin/{branch}: {remote_head}")
    return 0


def status_command(bead=None):
    rows = read_manifest()
    if bead:
        return status_one(rows, bead)
    return status_all(rows)


def list_command():
    rows = read_manifest()
    table_rows = []
    for row in rows:
        status, _, _ = effective_status(row)
        table_rows.append(
            (
                row.get("bead_id", ""),
                status,
                row.get("started_at", ""),
                row.get("branch", ""),
            )
        )
    print_table(("bead_id", "status", "started_at", "branch"), table_rows)
    return 0


def usage():
    print(__doc__.strip())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        usage()
        return 2
    command = argv[0]
    if command == "dispatch":
        dry_run = False
        force = False
        pre_claimed = None
        operands = []
        index = 1
        while index < len(argv):
            arg = argv[index]
            if arg == "--dry-run":
                dry_run = True
            elif arg == "--force":
                force = True
            elif (
                arg == "--pre-claimed"
                and index + 1 < len(argv)
                and not argv[index + 1].startswith("--")
            ):
                index += 1
                pre_claimed = argv[index]
            elif arg.startswith("--"):
                usage()
                return 2
            else:
                operands.append(arg)
            index += 1
        if len(operands) != 1:
            usage()
            return 2
        return dispatch_command(
            operands[0], dry_run=dry_run, force=force, pre_claimed=pre_claimed
        )
    if command == "status":
        if len(argv) > 2 or (len(argv) == 2 and argv[1].startswith("--")):
            usage()
            return 2
        return status_command(argv[1] if len(argv) == 2 else None)
    if command == "list":
        if len(argv) != 1:
            usage()
            return 2
        return list_command()
    usage()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
