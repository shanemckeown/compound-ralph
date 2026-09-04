#!/usr/bin/env python3
"""fleet-supervisor.py — triage the Claude session fleet.

Why this exists: `ListAgents` under-reports (a live 11h session was absent on
2026-08-11), session names are not unique, and a `blocked` session cannot call
SendMessage — so it can never escalate for itself. Nothing was watching, and 17
sessions had accumulated, the oldest 55 days old.

🔴 `state: "blocked"` does NOT mean "waiting on a permission prompt". Verified
2026-08-11: the oldest blocked session had FINISHED, said "Done.", and simply
was never closed. Classification has to come from the transcript's last turn,
never from the state field alone.

This tool only READS. It never answers a prompt, kills a session, or touches a
worktree — triage and escalate, per Shane's 2026-08-11 call.

Usage:
  fleet-supervisor.py              # triage report
  fleet-supervisor.py --json       # machine-readable
  fleet-supervisor.py --stale 7    # only sessions idle longer than N days
"""
import json, subprocess, sys, time, os, glob

PROJECTS = os.path.expanduser("~/.claude/projects")
REPO = "/Users/shane/Documents/GitReBase/AestheticcNext"
MANIFEST = "/Users/shane/Documents/Obsidian/Aestheticc/Ops/FLEET_MANIFEST.jsonl"


def manifest():
    """Rows written by fleet-dispatch.py, keyed by session id. Survives worktree
    deletion, which is exactly why /land-batch's worktree-based discovery can't
    see finished work and this can."""
    rows = {}
    try:
        with open(MANIFEST) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                    if r.get("session_id"):
                        rows[r["session_id"]] = r
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return rows


def landed(branch):
    """Did this branch actually reach origin/main?

    🔴 The load-bearing check. A finished session reports 'branch pushed', which
    is NOT 'landed' — 4 of 10 finished branches were still off main, one for 55
    days, and every other signal (bead closed, branch present, QA passed) read
    healthy. Pushed and landed are different claims."""
    if not branch:
        return None
    try:
        r = subprocess.run(f"git merge-base --is-ancestor origin/{branch} origin/main",
                           shell=True, cwd=REPO, capture_output=True, timeout=20)
        if r.returncode == 0:
            return "LANDED"
        chk = subprocess.run(f"git rev-parse --verify origin/{branch}",
                             shell=True, cwd=REPO, capture_output=True, timeout=20)
        if chk.returncode != 0:
            return "NO BRANCH"
        ahead = subprocess.run(f"git rev-list --count origin/main..origin/{branch}",
                               shell=True, cwd=REPO, capture_output=True, text=True, timeout=20)
        return f"🔴 NOT LANDED ({ahead.stdout.strip()} commits)"
    except Exception:
        return None

# Ordered: FIRST MATCH WINS, and the order is load-bearing.
#
# 🔴 Tuned against the real fleet on 2026-08-11, after a looser version put five
# completion reports in AWAITING_INPUT — a finished /goal summary routinely
# contains "?" and "confirm", so generic question markers are worthless here.
# Check for a completion signature FIRST; only then look for a real question.
CLASSES = [
    ("INTERRUPTED",       "the API cut out mid-response — the run may be incomplete, re-verify before trusting it",
     ("api error", "connection closed mid-response", "response above may be incomplete",
      "server error mid-response")),
    ("DONE_UNCLOSED",     "finished, branch pushed, never closed — verify it LANDED, then `claude rm`",
     ("result:", "closed —", "closed -", "bead closed", "branch pushed", "pushed (", "done.",
      "all state verified", "## summary", "final verdict")),
    ("AWAITING_DECISION", "genuinely stopped waiting on Shane — this is the bucket that needs you",
     ("blocked on shane", "needs shane", "awaiting shane", "shane's call", "nothing further can move",
      "option a/b", "awaiting your")),
    ("ASKED_A_QUESTION",  "ended on an unanswered question",
     ("which would you", "shall i", "would you like", "let me know which", "do you want me to")),
]


def agents():
    try:
        out = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30)
        return json.loads(out.stdout) if out.stdout.strip() else []
    except Exception as e:
        print(f"fleet-supervisor: cannot read fleet ({e})", file=sys.stderr)
        return []


MANAGERS_DIR = os.path.expanduser("~/.claude/fleet/managers")


def stale_manager_claims(fleet):
    """Manager-tab work claims (fleet-role.py's per-work-id markers, distinct
    from the headless agent_view fleet this file otherwise triages) whose
    claiming session is no longer live.

    🔴 Added 2026-09-04, after H-6 — the single largest remaining piece of the
    whole master plan — sat with a void claim for ~11 hours undetected. The
    claim mechanism itself already self-heals correctly (fleet-role.py catches
    a dead session's claim instantly on liveness check); the gap was that
    nothing polled for it. This is that poll, folded into the same triage pass
    Shane already runs via /morning rather than a new thing to remember."""
    live_ids = {a.get("sessionId") for a in fleet if a.get("sessionId")}
    now = time.time()
    stale = []
    for path in glob.glob(os.path.join(MANAGERS_DIR, "*.json")):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except Exception:
            continue
        sid = rec.get("session_id")
        if not sid or sid in live_ids:
            continue
        claimed_at = rec.get("claimed_at")
        age_days = None
        if claimed_at:
            try:
                ts = time.mktime(time.strptime(claimed_at, "%Y-%m-%dT%H:%M:%SZ"))
                age_days = round((now - ts) / 86400, 1)
            except Exception:
                pass
        stale.append({
            "work_id": rec.get("work_id") or os.path.splitext(os.path.basename(path))[0],
            "held_by_name": rec.get("name"), "held_by_session": sid,
            "claimed_at": claimed_at, "age_days": age_days,
            "handoff_doc_path": rec.get("handoff_doc_path"),
        })
    stale.sort(key=lambda r: -(r["age_days"] or 0))
    return stale


def transcript(session_id):
    hits = glob.glob(f"{PROJECTS}/*/{session_id}.jsonl")
    return hits[0] if hits else None


def last_turn(path):
    """Last assistant/user turn carrying real content. Returns (kind, text, ts)."""
    rows = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return None, "", None
    for r in reversed(rows):
        if r.get("type") not in ("assistant", "user"):
            continue
        msg = r.get("message") or {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else (
            [{"type": "text", "text": content}] if isinstance(content, str) else [])
        parts = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                parts.append(b["text"])
            elif b.get("type") == "tool_use":
                parts.append(f"[tool: {b.get('name')}]")
        if parts:
            return r.get("type"), " ".join(parts), r.get("timestamp")
    return None, "", None


def classify(text):
    low = text.lower()[:2000]
    for name, advice, markers in CLASSES:
        if any(m in low for m in markers):
            return name, advice
    return "UNKNOWN", "last turn is inconclusive — read the transcript"


def main():
    as_json = "--json" in sys.argv
    stale_days = 0.0
    if "--stale" in sys.argv:
        try:
            stale_days = float(sys.argv[sys.argv.index("--stale") + 1])
        except Exception:
            pass

    fleet = agents()
    man = manifest()
    subprocess.run("git fetch -q origin", shell=True, cwd=REPO, capture_output=True, timeout=90)
    now = time.time() * 1000
    findings = []

    for a in fleet:
        if a.get("state") != "blocked":
            continue
        age = (now - a.get("startedAt", now)) / 86_400_000
        if age < stale_days:
            continue
        sid = a.get("sessionId", "")
        path = transcript(sid)
        kind, text, ts = last_turn(path) if path else (None, "", None)
        cls, advice = classify(text) if text else ("NO_TRANSCRIPT", "no transcript found — likely already gone")
        row = man.get(sid, {})
        name = a.get("name", "")
        branch = row.get("expected_branch") or (
            f"goal/{name.lower()}" if name.lower().startswith(("aestheticcnext-", "lucy-")) else None)
        findings.append({
            "in_manifest": sid in man,
            "branch": branch,
            "landed": landed(branch) if cls in ("DONE_UNCLOSED", "INTERRUPTED") else None,
            "name": a.get("name", "(unnamed)"), "sessionId": sid, "id": a.get("id"),
            "age_days": round(age, 1), "cwd": a.get("cwd", "").split("/")[-1],
            "class": cls, "advice": advice,
            "last_turn_at": ts, "last_turn": " ".join(text.split())[:300],
        })

    findings.sort(key=lambda f: -f["age_days"])
    stale_managers = stale_manager_claims(fleet)

    if as_json:
        print(json.dumps({"findings": findings, "stale_manager_claims": stale_managers}, indent=2))
        return

    interactive = [a for a in fleet if a.get("kind") == "interactive"]
    print(f"FLEET: {len(fleet)} sessions — {len(interactive)} interactive, "
          f"{sum(1 for a in fleet if a.get('kind') == 'background')} background, "
          f"{len(findings)} blocked and triaged\n")

    buckets = {}
    for f in findings:
        buckets.setdefault(f["class"], []).append(f)

    for cls in ("AWAITING_DECISION", "ASKED_A_QUESTION", "INTERRUPTED", "UNKNOWN",
                "DONE_UNCLOSED", "NO_TRANSCRIPT"):
        rows = buckets.get(cls, [])
        if not rows:
            continue
        print(f"── {cls} ({len(rows)}) — {rows[0]['advice']}")
        for f in rows:
            flag = "" if f["in_manifest"] else "  [not in manifest]"
            print(f"   {f['age_days']:>5.1f}d  {f['name'][:42]:44s} {f['id']}{flag}")
            if f.get("landed"):
                print(f"          ├ branch {f['branch']}: {f['landed']}")
            print(f"          └ {f['last_turn'][:140]}")
        print()

    if stale_managers:
        print(f"── STALE MANAGER CLAIMS ({len(stale_managers)}) — claiming session is dead; "
              "work may be silently stalled\n"
              "   Reclaim: fleet-role.py manager <work-id> <new-session-id> --claim --steal "
              "[--handoff-doc PATH]")
        for m in stale_managers:
            age = f"{m['age_days']:.1f}d" if m["age_days"] is not None else "age unknown"
            print(f"   {age:>8s}  {m['work_id']:44s} held by {m['held_by_name'] or '(unnamed)'} "
                  f"({m['held_by_session'][:8] if m['held_by_session'] else '?'})")
            if m.get("handoff_doc_path"):
                print(f"          └ {m['handoff_doc_path']}")
        print()

    orphans = sum(1 for f in findings if not f["in_manifest"])
    if orphans:
        print(f"⚠ {orphans}/{len(findings)} sessions are NOT in the manifest — dispatched before")
        print("  fleet-dispatch.py existed, or by hand. For those the branch is GUESSED as")
        print("  `goal/<name>`, so a 'NO BRANCH' line may be a naming mismatch rather than")
        print("  missing work (goal/9disp-12 uses a hyphen where the bead uses a dot).")
        print("  Only manifest-recorded rows carry a branch you can trust.\n")

    print("🔴 Triage only. Nothing was answered, killed, or cleaned up — that is deliberate.")
    print("   AWAITING_DECISION needs Shane. INTERRUPTED needs re-verifying before it is trusted.")
    print("   DONE_UNCLOSED is NOT automatically safe to close — a finished session says its branch")
    print("   was pushed, which is not the same as landed. Check each one before `claude rm`:")
    print("     git merge-base --is-ancestor origin/goal/<branch> origin/main && echo LANDED")


if __name__ == "__main__":
    main()
