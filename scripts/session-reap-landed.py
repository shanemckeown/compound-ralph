#!/usr/bin/env python3
"""session-reap-landed.py — auto-close Agent View sessions whose worktree has landed.

Why this exists (LUCY-mwb83): /goal used to delete its own worktree once it
finished, which is why a completed session could never auto-land via
/land-batch (fixed 2026-08-26, commit 94535d5 in ~/.claude — see
commands/goal.md + commands/long-goal.md). But leaving the worktree in place
was not sufficient on its own: /land-batch's discover.sh (finish_gate(),
skills/land-batch/bin/discover.sh:917-953) refuses auto_land whenever the
candidate's session shows active=true or has_open_loop=true — and a finished
/goal session goes idle without ever exiting, so `claude agents --json` keeps
listing it (state="blocked") long after the work landed. Nothing was closing
that session. This script is that missing step, run on a schedule instead of
by hand.

Mechanism, deliberately NOT a new heuristic:
  fleet-supervisor.py (~/.claude/scripts/fleet-supervisor.py) already triages
  this exact class, read-only: it reads `claude agents --json`, keeps only
  background sessions in state="blocked", and classifies each by the LAST
  transcript turn. "DONE_UNCLOSED" is its name for "finished, branch pushed,
  never closed — verify it LANDED, then `claude rm`" — literally this script's
  job description, already vetted against a real 17-session backlog on
  2026-08-11. This script imports fleet-supervisor.py directly (agents(),
  manifest(), transcript(), last_turn(), classify()) rather than
  reimplementing "is this session actually finished" — a second heuristic
  built independently would be the exact drift fleet-supervisor.py's own
  docstring warns about ("state: blocked does NOT mean waiting on a
  permission prompt... classification has to come from the transcript").

  On top of that (per the bead's explicit instruction to reuse land-batch's
  own signals, not invent new ones), discover.sh is also invoked read-only
  and used as a SECOND, independent veto: if discover.sh has a live candidate
  for the resolved branch and its session shows active / has_open_loop /
  subagent_blocked, this script skips even though fleet-supervisor.py alone
  would have proceeded. Two independent "is this really finished" signals,
  either one can hold, neither alone is trusted to release.

Landed check — the one irreversible-mistake gate:
  `claude rm <id>` deletes the session's WORKTREE, not just the session
  entry (confirmed via `claude rm --help`). A session may only be rm'd once
  its branch's tip commit is confirmed to already be an ancestor of
  origin/main, checked FRESH (this script fetches immediately beforehand,
  never trusts a cached ref). Preference order for "tip": discover.sh's own
  tip_sha for a live worktree candidate (the real local HEAD, which can be
  ahead of an unpushed origin/<branch>) before falling back to
  origin/<branch> itself. Whichever source, verify_landed() re-runs
  `git merge-base --is-ancestor <tip> origin/main` itself — it does not
  reuse fleet-supervisor.py's landed() blindly, because that helper checks
  origin/<branch> only and a not-yet-pushed local worktree tip is exactly
  the case this bead calls out as a real data-loss risk.

Fails closed. Any of the following means SKIP + log, never `claude rm`:
  - not a background session in state="blocked" per a fresh `claude agents
    --json`
  - the last transcript turn does not classify as DONE_UNCLOSED (fleet-
    supervisor.py's own classifier — no bespoke "looks finished" regex here)
  - no trustworthy branch: not in FLEET_MANIFEST.jsonl (fleet-dispatch.py's
    session_id -> expected_branch record) AND no corroborating discover.sh
    candidate whose bead_id matches the session name
  - the resolved tip is not a fresh ancestor of origin/main
  - discover.sh has a matching candidate and its session reads active,
    has_open_loop, or subagent_blocked
  - `git fetch origin` itself fails (refuses to judge "landed" against a
    ref that might be stale) — the whole run aborts, nothing is touched

Usage:
  session-reap-landed.py                 # dry run (default) — prints what WOULD be closed
  session-reap-landed.py --apply         # actually run `claude rm <id>` on qualifying sessions
  session-reap-landed.py --json          # machine-readable report (respects --apply)

Env:
  SESSION_REAP_DRY_RUN=1   forces dry run even if --apply is passed (defense
                           in depth for the LaunchAgent — see the .plist).

🔴 KNOWN BLOCKER, verified live 2026-08-27, needs Shane (System Settings, not
scriptable): under this LaunchAgent, `git fetch origin` in this repo hangs
indefinitely and times out — every time, regardless of binary. Diagnosed with
`sample(1)`: the very first `getcwd()` git does in main() blocks inside the
kernel's open_nocancel for the full sample window. A raw `/bin/bash -c "git
fetch ..."` LaunchAgent (same shape as cc.aestheti.bd-reap, which has run
372 times without issue) reproduces the identical hang — so it is not about
python3, not about SSH auth (SSH_AUTH_SOCK resolution and BatchMode below are
real hardening but were NOT the cause; a directly-supplied, verified-correct
SSH_AUTH_SOCK still hung), and not about system load (load average ~1.1,
94%+ CPU idle when it hung). bd-reap's own job never chdir()s into a repo —
it only inspects other processes via `lsof`. Every hypothesis converges on
one thing: launchd background jobs cannot chdir()/open() under this Mac's
TCC-protected ~/Documents tree (AestheticcNext lives at
~/Documents/GitReBase/AestheticcNext) until the invoked binary has been
granted Full Disk Access — and because a background LaunchAgent has no
window session to show the consent prompt on, the syscall hangs rather than
failing fast. Fix: System Settings → Privacy & Security → Full Disk Access →
add the binary this .plist invokes (currently /opt/homebrew/bin/python3;
switching to /bin/bash makes no difference, it hits the same gate). One-time,
requires Shane at the keyboard — not something this script can grant itself.
Until then this script's own behavior stays correct and safe: fetch times
out, it logs "ABORT: git fetch origin failed", and it touches nothing.
"""

import argparse
import glob
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
REPO = "/Users/shane/Documents/GitReBase/AestheticcNext"
DISCOVER_SH = os.path.join(HOME, ".claude/skills/land-batch/bin/discover.sh")
FLEET_SUPERVISOR_PATH = os.path.join(HOME, ".claude/scripts/fleet-supervisor.py")
LOG_PATH = os.path.join(HOME, ".claude/session-reap.log")
EVIDENCE_ROOT = os.path.join(HOME, ".claude/evidence/session-reap-landed")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_fleet_supervisor():
    """Import fleet-supervisor.py by path (hyphenated filename, not a package)
    so its agents()/manifest()/transcript()/last_turn()/classify() are the
    single source of truth for "is this session actually finished"."""
    if not os.path.exists(FLEET_SUPERVISOR_PATH):
        raise RuntimeError(f"fleet-supervisor.py not found at {FLEET_SUPERVISOR_PATH}")
    spec = importlib.util.spec_from_file_location("fleet_supervisor_lib", FLEET_SUPERVISOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(cmd, cwd=None, timeout=60, env=None):
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env
    )


def resolve_ssh_auth_sock(log_lines):
    """LaunchAgents run outside the interactive login session and do not
    inherit its SSH_AUTH_SOCK — confirmed live during this script's own
    verification (2026-08-27): a plain `git fetch` under launchd hung for
    the full 120s timeout with no output, because ssh had no agent to talk
    to and no TTY to prompt on. The socket path is per-boot-session
    (/private/tmp/com.apple.launchd.<random>/Listeners) so it is not safe
    to hardcode in the .plist — resolve it fresh on every run instead."""
    sock = os.environ.get("SSH_AUTH_SOCK")
    if sock and os.path.exists(sock):
        return sock
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}"],
            capture_output=True, text=True, timeout=15,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("SSH_AUTH_SOCK =>"):
                candidate = line.split("=>", 1)[1].strip()
                if os.path.exists(candidate):
                    return candidate
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_lines.append(f"launchctl print gui/{os.getuid()} failed while resolving SSH_AUTH_SOCK: {exc}")
    for candidate in sorted(glob.glob("/private/tmp/com.apple.launchd.*/Listeners"), reverse=True):
        if os.path.exists(candidate):
            return candidate
    return None


def fetch_repo(log_lines):
    """Bare fetch (matches fleet-supervisor.py's own `git fetch -q origin`) so
    every remote-tracking ref — including origin/goal/* discover.sh reads and
    origin/<branch> the landed-check reads — is current before anything below
    is judged."""
    env = os.environ.copy()
    sock = resolve_ssh_auth_sock(log_lines)
    if sock:
        env["SSH_AUTH_SOCK"] = sock
        log_lines.append(f"resolved SSH_AUTH_SOCK={sock}")
    else:
        log_lines.append("warning: could not resolve a live SSH_AUTH_SOCK — fetch will fail fast if auth is needed")
    # BatchMode=yes: if the agent still can't be reached, fail FAST and loud
    # (git exits non-zero within seconds) instead of hanging on an
    # interactive prompt with no TTY to answer it — exactly what happened
    # before this fix, once per cron cycle, forever.
    env["GIT_SSH_COMMAND"] = f"{env.get('GIT_SSH_COMMAND', 'ssh')} -o BatchMode=yes -o ConnectTimeout=15"
    try:
        r = run(["git", "fetch", "origin", "--quiet"], cwd=REPO, timeout=45, env=env)
    except subprocess.TimeoutExpired as exc:
        log_lines.append(f"git fetch origin FAILED: {exc}")
        partial_err = (exc.stderr or b"")
        if isinstance(partial_err, bytes):
            partial_err = partial_err.decode("utf-8", errors="replace")
        if partial_err.strip():
            log_lines.append(f"  partial stderr (last 4000 chars): {partial_err[-4000:]}")
        return False
    except OSError as exc:
        log_lines.append(f"git fetch origin FAILED: {exc}")
        return False
    log_lines.append(f"git fetch origin --quiet exit={r.returncode}")
    if r.returncode != 0:
        log_lines.append(f"  stderr: {r.stderr.strip()[:500]}")
        return False
    return True


def discover_candidates(log_lines):
    """Read-only invocation of land-batch's own discover.sh. Returns
    {branch: candidate_dict}. Empty dict (not an exception) on any failure —
    discover.sh is a corroborating veto, not a required input; its absence
    only removes a safety net, it never becomes a reason to proceed."""
    env = os.environ.copy()
    env["LAND_BATCH_REF_SNAPSHOT"] = "fetched-after-admission"
    try:
        r = run(["bash", DISCOVER_SH, REPO], timeout=240, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_lines.append(f"discover.sh invocation failed: {exc}")
        return {}
    if r.returncode != 0:
        log_lines.append(f"discover.sh exited {r.returncode}: {r.stderr.strip()[:500]}")
        return {}
    try:
        report = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        log_lines.append(f"discover.sh produced invalid JSON: {exc}")
        return {}
    by_branch = {}
    for cand in report.get("candidates", []):
        branch = cand.get("branch")
        if branch:
            by_branch[branch] = cand
    log_lines.append(f"discover.sh: {len(by_branch)} candidates")
    return by_branch


def verify_landed(branch, cand, log_lines):
    """Fresh, tip-based ancestor check — the one gate that must never be
    wrong. Prefers discover.sh's tip_sha for a live worktree (the real local
    HEAD, which can be ahead of an unpushed origin/<branch>) over the
    remote-tracking ref. Returns (ok: bool, tip: str|None, source: str)."""
    tip = None
    source = None
    if cand and cand.get("tip_sha"):
        tip = cand["tip_sha"]
        source = f"discover.sh {cand.get('source_kind')} tip_sha"
    if tip is None:
        r = run(["git", "-C", REPO, "rev-parse", "--verify", f"origin/{branch}"], timeout=30)
        if r.returncode != 0:
            return False, None, "no discover.sh tip_sha and no origin/<branch> ref"
        tip = r.stdout.strip()
        source = f"origin/{branch}"
    r = run(["git", "-C", REPO, "merge-base", "--is-ancestor", tip, "origin/main"], timeout=30)
    ok = r.returncode == 0
    if r.returncode not in (0, 1):
        log_lines.append(
            f"  merge-base --is-ancestor for {branch} exited {r.returncode} (neither yes/no): {r.stderr.strip()[:300]}"
        )
        return False, tip, source
    return ok, tip, source


def evaluate(fs, discover_by_branch, log_lines):
    """Build the full candidate list with verdicts. Never calls `claude rm` —
    that happens only in main(), only for entries marked action == 'rm'."""
    manifest = fs.manifest()
    fleet = fs.agents()
    blocked = [a for a in fleet if a.get("kind") == "background" and a.get("state") == "blocked"]
    log_lines.append(f"claude agents --json: {len(fleet)} sessions, {len(blocked)} background+blocked")

    results = []
    for a in blocked:
        sid = a.get("sessionId") or ""
        short_id = a.get("id")
        name = a.get("name") or "(unnamed)"
        entry = {
            "id": short_id,
            "session_id": sid,
            "name": name,
            "cwd": a.get("cwd"),
            "age_days": round((time.time() * 1000 - a.get("startedAt", time.time() * 1000)) / 86_400_000, 1),
        }

        row = manifest.get(sid, {})
        in_manifest = sid in manifest
        entry["in_manifest"] = in_manifest
        branch = row.get("expected_branch")
        branch_confidence = "manifest" if branch else None

        path = fs.transcript(sid)
        _kind, text, _ts = fs.last_turn(path) if path else (None, "", None)
        cls, _advice = fs.classify(text) if text else ("NO_TRANSCRIPT", "")
        entry["class"] = cls

        if cls != "DONE_UNCLOSED":
            entry["action"] = "skip"
            entry["reason"] = f"transcript last-turn class={cls} (only DONE_UNCLOSED is treated as finished)"
            results.append(entry)
            continue

        if not branch:
            # Not dispatched via fleet-dispatch.py (or dispatched before it
            # existed) — the branch name would otherwise be a guess. Only
            # accept a discover.sh match by exact bead_id == session name.
            for cand_branch, cand in discover_by_branch.items():
                if (cand.get("bead_id") or "").casefold() == name.casefold():
                    branch = cand_branch
                    branch_confidence = "discover.sh bead_id match"
                    break

        if not branch:
            entry["action"] = "skip"
            entry["reason"] = "no trustworthy branch: not in FLEET_MANIFEST.jsonl and no discover.sh bead_id match"
            results.append(entry)
            continue

        entry["branch"] = branch
        entry["branch_confidence"] = branch_confidence

        cand = discover_by_branch.get(branch)
        landed_ok, tip, tip_source = verify_landed(branch, cand, log_lines)
        entry["tip_sha"] = tip
        entry["tip_source"] = tip_source
        entry["landed"] = landed_ok
        if not landed_ok:
            entry["action"] = "skip"
            entry["reason"] = f"branch tip ({tip_source}) is not a fresh ancestor of origin/main"
            results.append(entry)
            continue

        if cand is not None:
            sess = cand.get("session") or {}
            flags = {k: sess.get(k) for k in ("active", "has_open_loop", "subagent_blocked")}
            entry["discover_session_flags"] = flags
            if flags["active"] or flags["has_open_loop"] or flags["subagent_blocked"]:
                entry["action"] = "skip"
                entry["reason"] = f"discover.sh corroborating veto: session flags {flags}"
                results.append(entry)
                continue
            entry["discover_corroboration"] = "clean"
        else:
            entry["discover_corroboration"] = "no-matching-candidate (worktree likely already gone/reclaimed)"

        entry["action"] = "rm"
        entry["reason"] = "DONE_UNCLOSED + branch tip landed on origin/main + no open-loop veto"
        results.append(entry)

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Actually run `claude rm <id>`. Default is dry run.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a text report.")
    args = parser.parse_args()

    apply = args.apply and os.environ.get("SESSION_REAP_DRY_RUN") != "1"

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
    evidence_dir = os.path.join(EVIDENCE_ROOT, run_id)
    os.makedirs(evidence_dir, exist_ok=True)
    os.chmod(evidence_dir, 0o700)

    log_lines = [f"session-reap-landed run {run_id}", f"dry_run={not apply}"]

    try:
        fs = load_fleet_supervisor()
    except Exception as exc:  # noqa: BLE001 - fail closed, report, exit
        log_lines.append(f"ABORT: could not load fleet-supervisor.py: {exc}")
        _finish(log_lines, [], evidence_dir, apply, ok=False)
        print(f"session-reap-landed: ABORT — {exc}", file=sys.stderr)
        sys.exit(2)

    if not fetch_repo(log_lines):
        log_lines.append("ABORT: git fetch origin failed — refusing to judge 'landed' against a possibly stale ref")
        _finish(log_lines, [], evidence_dir, apply, ok=False)
        print("session-reap-landed: ABORT — git fetch origin failed, no sessions touched", file=sys.stderr)
        sys.exit(2)

    discover_by_branch = discover_candidates(log_lines)
    results = evaluate(fs, discover_by_branch, log_lines)

    to_act = [e for e in results if e["action"] == "rm"]
    for entry in to_act:
        if not apply:
            entry["action"] = "would-rm"
            continue
        try:
            r = run(["claude", "rm", entry["id"]], timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            entry["action"] = "rm-error"
            entry["rm_error"] = str(exc)
            continue
        entry["rm_exit"] = r.returncode
        entry["rm_stdout"] = r.stdout.strip()[:500]
        entry["rm_stderr"] = r.stderr.strip()[:500]
        entry["action"] = "rm" if r.returncode == 0 else "rm-failed"

    errors = [e for e in results if e["action"] in ("rm-failed", "rm-error")]
    acted = [e for e in results if e["action"] == "rm"]
    _finish(log_lines, results, evidence_dir, apply, ok=not errors)

    if args.json:
        print(json.dumps({"dry_run": not apply, "run_id": run_id, "results": results}, indent=2))
    else:
        _print_report(results, apply)

    sys.exit(1 if errors else 0)


def _finish(log_lines, results, evidence_dir, apply, ok):
    evidence_path = os.path.join(evidence_dir, "results.json")
    try:
        with open(evidence_path, "w", encoding="utf-8") as fh:
            json.dump({"dry_run": not apply, "log": log_lines, "results": results}, fh, indent=2)
    except OSError:
        pass
    acted = [e for e in results if e.get("action") == "rm"]
    would = [e for e in results if e.get("action") == "would-rm"]
    failed = [e for e in results if e.get("action") in ("rm-failed", "rm-error")]
    summary = (
        f"[{now_iso()}] {'APPLY' if apply else 'DRY-RUN'} "
        f"closed={len(acted)} would_close={len(would)} failed={len(failed)} "
        f"evaluated={len(results)} ok={ok}"
    )
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
            for line in log_lines:
                fh.write(f"  {line}\n")
            for e in acted + would + failed:
                fh.write(f"  {e.get('action')}: {e.get('name')} ({e.get('id')}) branch={e.get('branch')} reason={e.get('reason')}\n")
            fh.write(f"  evidence: {evidence_path}\n")
    except OSError:
        pass


def _print_report(results, apply):
    label = "APPLY" if apply else "DRY-RUN"
    print(f"\n=== session-reap-landed ({label}) ===")
    by_action = {}
    for e in results:
        by_action.setdefault(e["action"], []).append(e)

    for action, title in (
        ("rm", "CLOSED"),
        ("would-rm", "WOULD CLOSE"),
        ("rm-failed", "FAILED (claude rm returned non-zero)"),
        ("rm-error", "FAILED (could not invoke claude rm)"),
        ("skip", "SKIPPED"),
    ):
        rows = by_action.get(action, [])
        if not rows:
            continue
        print(f"\n-- {title} — {len(rows)} --")
        for e in rows:
            branch = e.get("branch") or "-"
            print(f"  {e['id']}  {e['name']:<40s} branch={branch}  ({e.get('reason', '')})")

    if not results:
        print("\n  (no background sessions in state=blocked found)")
    print()
    if not apply:
        print("Dry run. Re-run with --apply to actually close the CLOSED-eligible sessions above.")


if __name__ == "__main__":
    main()
