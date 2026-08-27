#!/usr/bin/env python3
"""fleet-dispatch.py — the ONE way to dispatch a bead into Agent View.

Does two jobs that were previously left to discipline and kept getting skipped:

1. THE PRE-DISPATCH VALIDATOR, AS A GATE THAT REFUSES.
   `Aestheticc/CLAUDE.md` lists mandatory pre-dispatch checks and notes they are
   "prose enforced by discipline, which is why they get skipped" — twice in one
   session, at real cost. They are enforced here instead. A single-bead /goal
   also requires the explicit `headless-eligible` label; epic /long-goal runs
   its own per-child scoping-quality gate instead.

2. THE DISPATCH MANIFEST.
   `claude --bg` returns no session id, `ListAgents` under-reports, and session
   names are NOT unique (verified 2026-08-11: aestheticc-92 resolved to two
   different ids, and the name is the SendMessage address). So this captures the
   real session id by diffing the registry across the dispatch and records it.
   Every later operation — supervise, message, close — addresses by id from the
   manifest, never by name.

Manifest: Aestheticc/Ops/FLEET_MANIFEST.jsonl (vault, committed, one JSON row
per dispatch, append-only).

Usage:
  fleet-dispatch.py <bead-id>              # /goal <bead-id>
  fleet-dispatch.py <epic-id> --epic       # /long-goal <epic-id>
  fleet-dispatch.py <bead-id> --dry-run    # run the gate, dispatch nothing
  fleet-dispatch.py <bead-id> --force      # dispatch despite a failed check (recorded)
"""
import io, json, os, re, subprocess, sys, time, datetime

SLOTS = os.path.expanduser("~/.claude/scripts/fleet-slots.py")

REPO = "/Users/shane/Documents/GitReBase/AestheticcNext"
VAULT_BEADS = "/Users/shane/Documents/Obsidian/.beads"
REPO_BEADS = "/Users/shane/Documents/GitReBase/AestheticcNext/.beads"
MANIFEST = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/FLEET_MANIFEST.jsonl"
POLL_SECONDS = 45

# Tier A: spawned code workers load NO MCP servers. See ~/.claude/mcp-tiers/README.md
# for the evidence. Deliberately NOT in /tmp — a config that can evaporate between
# reboots would take the restriction with it, silently.
TIER_A_MCP = os.path.expanduser("~/.claude/mcp-tiers/tier-a-code-worker.json")


def beads_dir(bead):
    """Two databases. LUCY-* is the vault; everything else is the code repo.
    Getting this wrong makes `bd show` fail, which the gate would otherwise
    report as 'bead may not exist' — a false refusal."""
    return VAULT_BEADS if bead.upper().startswith("LUCY-") else REPO_BEADS


def sh(cmd, cwd=None, timeout=60, bead=None):
    env = dict(os.environ, BEADS_DIR=beads_dir(bead) if bead else REPO_BEADS)
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           cwd=cwd, timeout=timeout, env=env)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def registry(include_all=False):
    code, out, _ = sh(f"claude agents --json{' --all' if include_all else ''}")
    if code != 0 or not out:
        return []
    try:
        return json.loads(out)
    except Exception:
        return []


# ---------------------------------------------------------------- the gate ---
def check_no_inflight_work(bead):
    """Check 1: no worktree or branch already exists for this bead or its children."""
    problems = []
    _, wt, _ = sh("git worktree list", cwd=REPO, bead=bead)
    for line in wt.splitlines():
        if bead.lower().split("-", 1)[-1] in line.lower():
            problems.append(f"worktree already exists: {line.strip()}")
    _, br, _ = sh(f"git branch -a --list '*{bead.lower().split('-', 1)[-1]}*'", cwd=REPO, bead=bead)
    for line in br.splitlines():
        ref = line.strip().lstrip("* ").strip()
        if not ref:
            continue
        code, _, _ = sh(f"git merge-base --is-ancestor {ref} origin/main", cwd=REPO)
        state = "LANDED" if code == 0 else "UNLANDED"
        if state == "UNLANDED":
            problems.append(f"unlanded branch already exists: {ref}")
    return problems


BEAD_RE = re.compile(r"\b((?:AestheticcNext|LUCY)-[a-z0-9.]+)\b", re.I)


def bead_status(bead):
    """`bd show`'s first line carries the status: `... [● P1 · CLOSED]`."""
    code, out, _ = sh(f"bd show {bead}", cwd=REPO, bead=bead)
    if code != 0 or not out:
        return None, out
    first = out.splitlines()[0]
    for state in ("CLOSED", "IN_PROGRESS", "OPEN", "BLOCKED"):
        if state in first.upper():
            return state, out
    return "UNKNOWN", out


def check_bead_is_dispatchable(bead):
    """Check 0 (added 2026-08-11): don't dispatch a bead that is already CLOSED.

    Found while testing the gate: AestheticcNext-3dof9 is CLOSED, its close
    reason reads "Branch pushed… Ready for Shane to merge", and it has sat
    unmerged for 22 days. Re-dispatching that rebuilds finished work. A closed
    bead with an unlanded branch needs LANDING, not another /goal run."""
    state, out = bead_status(bead)
    if state is None:
        return [f"`bd show {bead}` failed — bead does not exist in {beads_dir(bead)}"]
    if state == "CLOSED":
        return [f"bead is already CLOSED — it likely needs LANDING, not re-dispatch",
                f"close reason: {' '.join(out.splitlines()[3:4])[:160]}"]
    if state == "IN_PROGRESS":
        return [f"bead is already IN_PROGRESS — another session may be on it right now"]
    return []


DEP_SECTION_HEADERS = {"DEPENDS ON", "BLOCKED BY"}
# Any other bare all-caps section header ends a DEPENDS ON/BLOCKED BY section.
# `bd show`'s own vocabulary, kept explicit rather than inferred from case —
# inferring "looks like a header" nearly re-created this exact bug one layer up.
ALL_SECTION_HEADERS = DEP_SECTION_HEADERS | {
    "DESCRIPTION", "NOTES", "ACCEPTANCE CRITERIA", "PARENT", "BLOCKS",
    "CHILDREN", "COMMENTS",
}


def check_dependencies(bead):
    """Check 2: every dependency must be CLOSED. A clean worktree is not enough —
    a sibling that must land first makes this undispatchable regardless.

    🔴 Fixed 2026-08-19 — was silently a no-op. `bd show`'s DEPENDS ON is a
    SECTION: the header ("DEPENDS ON") is its own line; each dependency is on
    a following indented line ("  → ◐ AestheticcNext-x: ..."), not the same
    line as the header. The old per-line `"depends on" in line.lower()` check
    only ever matched the header line itself, which contains no bead id — so
    `BEAD_RE.findall(line)` always found zero ids and this check always
    reported "closed" with nothing actually checked. Confirmed on
    AestheticcNext-6644b.4.4: reported clean while its real dependency
    (AestheticcNext-6644b.4.2) was IN_PROGRESS. This is section-scoped instead."""
    _, out = bead_status(bead)
    if not out:
        return []
    problems = []
    current_section = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped in ALL_SECTION_HEADERS:
            current_section = stripped
            continue
        if current_section not in DEP_SECTION_HEADERS or not stripped:
            continue
        for dep in BEAD_RE.findall(line):
            if dep.lower() == bead.lower():
                continue
            dstate, _ = bead_status(dep)
            if dstate != "CLOSED":
                problems.append(f"dependency {dep} is {dstate or 'MISSING'}, not CLOSED")
    return problems


def check_epic_has_children(bead):
    """Check 3: /long-goal hard-refuses a childless epic. Verify before calling it ready."""
    code, out, _ = sh(f"bd list --parent {bead}", cwd=REPO)
    if code != 0 or not out.strip() or "no issues" in out.lower():
        return [f"epic {bead} has NO children — /long-goal will hard-refuse it"]
    return []


FILE_PATH_RE = re.compile(r"[\w./-]+\.\w{1,5}(:\d+)?")


def check_headless_eligible(bead, diagnostic_output=None):
    """Check 4 (added 2026-08-27): refuse a single-bead /goal dispatch unless the bead is
    explicitly tagged `headless-eligible`. Mirrors the existing `auto-eligible` label
    convention (a DIFFERENT label with a different meaning — auto-eligible gates the
    unsupervised Sentry/night-batch pipeline; headless-eligible gates whether THIS bead is
    scoped enough for a Worker /goal session at all, per CLAUDE.md's Manager/Worker split).
    Explicit opt-in only — never auto-inferred, so a heuristic misfire can't silently let an
    open-ended bead through. See AestheticcNext-fgxkg (2026-08-27): a genuinely novel,
    multi-day, open-question-bearing bead got headless-dispatched 3x anyway."""
    code, out, err = sh(f"bd show {bead} --json", cwd=REPO, bead=bead)
    if code != 0 or not out:
        detail = err or out or "no output"
        return [f"`bd show {bead} --json` failed — cannot verify the required "
                f"'headless-eligible' label: {detail}"]
    try:
        records = json.loads(out)
        record = records[0] if isinstance(records, list) and records else None
    except Exception as exc:
        return [f"`bd show {bead} --json` returned invalid JSON — cannot verify the required "
                f"'headless-eligible' label: {exc}"]
    if not isinstance(record, dict):
        return [f"`bd show {bead} --json` did not return a one-object array — cannot verify "
                f"the required 'headless-eligible' label"]

    labels = record.get("labels") or []
    if "headless-eligible" in labels:
        return []

    acceptance = record.get("acceptance_criteria") or ""
    scope_text = "\n".join((record.get("description") or "", acceptance))
    diagnostics = [
        f"acceptance_criteria is empty: {'YES' if not acceptance.strip() else 'NO'}",
        f"description + acceptance_criteria cites a file path: "
        f"{'YES' if FILE_PATH_RE.search(scope_text) else 'NO'}",
    ]
    diagnostic_output = sys.stdout if diagnostic_output is None else diagnostic_output
    for diagnostic in diagnostics:
        print(f"   ↳ diagnostic (not blocking): {diagnostic}", file=diagnostic_output)
    return [
        f"bead is not tagged 'headless-eligible' — /goal cannot run this headless. Either: "
        f"(a) if it's genuinely a small, pre-scoped, no-open-design-decisions task, tag it: "
        f"bd tag {bead} headless-eligible, then re-run; or (b) open a Manager tab and run "
        f"/take instead — see Aestheticc/CLAUDE.md 'Orchestrator vs sub-Claude'.",
    ]


def run_gate(bead, is_epic):
    print(f"── pre-dispatch gate: {bead}")
    headless_diagnostics = None
    results = {
        "0 bead is dispatchable": check_bead_is_dispatchable(bead),
        "1 no in-flight work": check_no_inflight_work(bead),
        "2 dependencies closed": check_dependencies(bead),
    }
    if is_epic:
        results["3 epic has children"] = check_epic_has_children(bead)
    else:
        headless_diagnostics = io.StringIO()
        results["4 headless eligible"] = check_headless_eligible(bead, headless_diagnostics)
    failed = False
    for name, problems in results.items():
        if problems:
            failed = True
            print(f"   ✗ {name}")
            if name == "4 headless eligible" and headless_diagnostics is not None:
                print(headless_diagnostics.getvalue(), end="")
            for p in problems:
                print(f"       {p}")
        else:
            print(f"   ✓ {name}")
    return failed, {k: v for k, v in results.items() if v}


# ------------------------------------------------------------- the dispatch ---
def dispatch(bead, is_epic, dispatched_by):
    before = {a.get("sessionId") for a in registry()}

    # 🔴 TIER A: a spawned code worker loads NO MCP servers.
    #
    # `--strict-mcp-config` makes the config below the ONLY source, ignoring
    # ~/.claude.json (global) and every project .mcp.json. Measured 2026-08-14:
    # each spawned session was starting 6 MCP servers and 2 persistent autossh
    # tunnels to the Hermes VPS — times ~10 concurrent sessions — and calling
    # none of them. MCP servers spawn at launch, not on first use, so the cost
    # is paid whether or not a tool is ever invoked.
    #
    # The config is empty ON EVIDENCE, not on caution: /goal and /long-goal
    # contain no mcp__ reference, and grepping the whole skill chain they invoke
    # (/autoplan, /codex, /review, /ship, /plan-build-qa, /plan-*-review) turns up
    # mcp__conductor__ ONLY — dead references to a tool retired on 2026-05-31.
    # A worker needing graph traversal escalates to its orchestrator, which is
    # already the documented sub-Claude route and costs no tunnel.
    #
    # Do NOT add servers here without re-running that two-hop grep. Full reasoning:
    # ~/.claude/mcp-tiers/README.md
    if not os.path.isfile(TIER_A_MCP):
        print(f"\n   ✗ REFUSING TO DISPATCH: Tier A MCP config missing at {TIER_A_MCP}")
        print("     Without it the worker would silently fall back to the FULL global")
        print("     MCP set — 6 servers + 2 SSH tunnels it does not use. Restore the")
        print("     file (see ~/.claude/mcp-tiers/README.md) and re-run.")
        sys.exit(1)

    cmd = (f'claude --bg --name "{bead}" '
           f'--mcp-config "{TIER_A_MCP}" --strict-mcp-config '
           f'"/{"long-goal" if is_epic else "goal"} {bead}"')
    print(f"\n── dispatching: {cmd}")
    code, out, err = sh(cmd, cwd=REPO, timeout=120)
    if code != 0:
        print(f"   ✗ dispatch failed: {err or out}")
        return None

    # claude --bg returns no session id, so identify the new session by diffing
    # the registry. Match on name too, in case something else dispatches at the
    # same moment.
    session = None
    for _ in range(POLL_SECONDS):
        for a in registry():
            if a.get("sessionId") not in before and a.get("name") == bead:
                session = a
                break
        if session:
            break
        time.sleep(1)

    if not session:
        print(f"   ⚠ dispatched, but could not identify the new session within {POLL_SECONDS}s.")
        print("     Record it by hand — an unrecorded session is one nothing can supervise.")
        return None
    print(f"   ✓ session {session['sessionId']} ({session.get('id')})")
    return session


def write_manifest(bead, is_epic, session, gate_failures, forced, dispatched_by):
    os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
    row = {
        "dispatched_at": datetime.datetime.now(datetime.timezone.utc)
                                 .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "bead": bead,
        "command": f"/{'long-goal' if is_epic else 'goal'} {bead}",
        "session_id": session.get("sessionId") if session else None,
        "short_id": session.get("id") if session else None,
        "name": bead,
        "cwd": REPO,
        # 🔴 An epic branches as long-goal/<bead>, NOT goal/<bead>. Lines above already
        # branch on is_epic for the command; this one did not, so every epic dispatched
        # before 2026-08-14 recorded a branch that never existed.
        # This is load-bearing, not cosmetic: /long-goal runs `git worktree remove` after
        # pushing, and /land-batch discovers finished work by SCANNING WORKTREES
        # (reference_goal_land_batch_worktree_discovery_gap). So an epic goes invisible at
        # the exact moment it is ready, and this manifest row is the only durable record
        # left pointing at it. A wrong branch name here means finished work nobody can find.
        "expected_branch": f"{'long-goal' if is_epic else 'goal'}/{bead.lower()}",
        "dispatched_by": dispatched_by,
        "gate_failures": gate_failures or None,
        "forced": bool(forced),
    }
    with open(MANIFEST, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    print(f"   ✓ manifest row written → {MANIFEST}")
    return row


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        sys.exit(2)

    bead = args[0]
    is_epic = "--epic" in flags
    force = "--force" in flags
    dry = "--dry-run" in flags
    dispatched_by = os.environ.get("CLAUDE_SESSION_NAME", "unknown")

    failed, failures = run_gate(bead, is_epic)

    if failed and not force:
        print(f"\n🔴 REFUSED. {bead} is not dispatchable.")
        print("   These are the dispatch checks CLAUDE.md requires, including explicit")
        print("   headless eligibility for single beads. Fix the cause, or re-run with --force")
        print("   (which dispatches anyway and records the override in the manifest).")
        sys.exit(1)
    if failed and force:
        print("\n⚠ gate failed but --force given — dispatching and recording the override.")
    if dry:
        print("\n--dry-run: gate only, nothing dispatched.")
        sys.exit(0)

    # 🔴 Concurrency gate (added 2026-08-23). Total fleet load — Agent View sessions
    # PLUS Codex/GLM calls PLUS the orchestrator's own implicit slot — was capped only
    # by a memory note nobody re-checks under pressure ("~6-8 max"), while the actual
    # laptop-grinding-to-a-halt failure mode is oversubscription: N sessions each
    # spawning Jest's own worker fan-out with zero cross-session awareness. This makes
    # the Agent View half of that budget (5 of 11 total) a real refusal instead of
    # prose. See ~/.claude/scripts/fleet-slots.py for the full design and the other
    # half (Codex/GLM claiming, Jest maxWorkers) that shares this budget.
    claim = sh(f"python3 {SLOTS} claim-agent-view {bead}")
    if claim[0] != 0:
        sh(f"python3 {SLOTS} enqueue {bead} {'long-goal' if is_epic else 'goal'}")
        print(f"\n⏸ AT CAPACITY — {bead} queued, not dispatched.")
        print(f"   Run `python3 {SLOTS} status` to see current load.")
        print(f"   A slot frees when a running session finishes (goal.md/long-goal.md's")
        print(f"   closing phase releases its own slot and dispatches the next queued")
        print(f"   bead automatically — no polling needed).")
        sys.exit(3)

    session = dispatch(bead, is_epic, dispatched_by)
    if session:
        sh(f"python3 {SLOTS} attach-session-id {bead} {session['sessionId']}")
    else:
        # dispatch itself failed -- don't let a phantom slot sit claimed forever.
        sh(f"python3 {SLOTS} release-agent-view {bead}")
    write_manifest(bead, is_epic, session, failures, force, dispatched_by)
    if not session:
        sys.exit(1)


if __name__ == "__main__":
    main()
