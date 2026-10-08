#!/usr/bin/env python3
"""gate-watch.py - everything waiting on Shane, and how long since he was last asked (LUCY-3pfwv).

Shane, 7 Oct 2026: "this needs to be an actual rule in code, not a dream: when something is waiting on a
gate, re-ask me instead of waiting quietly."  Five sensitive-path branches sat 28-48h with a PASS and an
opt-in on record because nothing computed "waiting on Shane" or re-asked.

WAITING items:
  land:<branch>   LAND-READY branch (unlanded-sweep.py) not on main. NOT a Shane question (CLAUDE.md: "an
                  independently PASSed branch is landed, never asked about"): it is an ORCHESTRATOR TO-DO line.
                  Only a sensitive-path branch with no recorded "SENSITIVE-PATH OPT-IN" is asked of Shane.
  shift:<rev>     Cloud Run revision created but traffic not shifted to it for > 30 min
  <id>            free-form items in Aestheticc/Ops/GATE_LEDGER.jsonl (add / ask / defer / resolve)
A Shane item is OVERDUE when now - (last_asked_at, else asked_at, else first_seen_at) > 60 min, unless
deferred (defer --until). Auto-resolved (LUCY gate-watch false-asks fix, 8 Oct 2026):
  - land:* whose tip is an ancestor of origin/main (checked by gate-watch itself, every compute)
  - shift:<rev> whose rev is already serving traffic[0], or superseded by a newer latestCreatedRevision
A resolved derived item stays resolved while its tip (land) / revision (shift) is unchanged, so a manual
`resolve` sticks; a new push to the branch reopens it.

Usage:
  gate-watch.py [--json] [--no-fetch]      live compute, print RE-ASK block; exit 1 if anything overdue
  gate-watch.py add <id> --what W --question Q
  gate-watch.py ask <id>                   record that Shane was just re-asked (resets the clock)
  gate-watch.py defer <id> --until ISO [--reason R]   Shane already answered with a time: silent until then
  gate-watch.py resolve <id> [--reason R]  item is done / answered / not Shane's
  gate-watch.py --hook                     UserPromptSubmit hook: reads stdin payload, silent unless this
                                           session is the orchestrator AND something is overdue
  gate-watch.py --refresh                  recompute derived items into the cache (hook spawns this)

Fails OPEN: any read/compute error means "nothing to report", never a block. Code cannot make the model
ask; it makes the reminder unmissable on every turn and logs the asks.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
LEDGER = os.environ.get("GATE_LEDGER", os.path.join(HOME, "Documents/Obsidian/Aestheticc/Ops/GATE_LEDGER.jsonl"))
CACHE = os.environ.get("GATE_WATCH_CACHE", os.path.join(HOME, ".claude/state/gate-watch-cache.json"))
SWEEP = os.environ.get("GATE_WATCH_SWEEP", os.path.join(HOME, ".claude/scripts/unlanded-sweep.py"))
ROLE = os.environ.get("GATE_WATCH_ROLE", os.path.join(HOME, ".claude/scripts/fleet-role.py"))
SELF = os.path.abspath(__file__)
OVERDUE_MIN = 60
SHIFT_GRACE_MIN = 30
CACHE_FRESH_S = 600
SERVICE, REGION = "aestheticc-next", "europe-west2"
LIVE_STATE = "Aestheticc/Ops/ORCHESTRATOR_LIVE_STATE.md"
REPO = os.environ.get("AESTHETICC_REPO", "/Users/shane/Documents/GitReBase/AestheticcNext")


def now_ts():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def human(seconds):
    m = int(max(0, seconds) // 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h{m:02d}m"
    return f"{h // 24}d{h % 24}h"


# ---------------------------------------------------------------- ledger
class LedgerLock:
    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
            self.f = open(LEDGER + ".lock", "w")
            fcntl.flock(self.f, fcntl.LOCK_EX)
        except Exception:
            self.f = None
        return self

    def __exit__(self, *a):
        try:
            if self.f:
                fcntl.flock(self.f, fcntl.LOCK_UN)
                self.f.close()
        except Exception:
            pass


def read_ledger():
    """(records dict, ok). ok False = unreadable/corrupt; callers must not overwrite the file then."""
    recs = {}
    if not os.path.exists(LEDGER):
        return recs, True
    try:
        for ln in open(LEDGER, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            if isinstance(r, dict) and r.get("id"):
                recs[r["id"]] = r
        return recs, True
    except Exception:
        return {}, False


def write_ledger(recs):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs.values():
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(tmp, LEDGER)


# ---------------------------------------------------------------- derive
def land_items(sweep_rows, now):
    items = []
    for r in sweep_rows:
        if r.get("status") != "LAND-READY":
            continue
        bead = (r.get("bead") or {}).get("id") or r.get("key") or "no bead"
        since = parse_iso(r.get("pass_at")) or now - float(r.get("age_days") or 0) * 86400
        sens = bool(r.get("sensitive"))
        optin = bool(r.get("opt_in_recorded"))
        if sens and not optin:  # the ONE land case that is Shane's: a sensitive-path opt-in
            audience = "shane"
            what = f"{r['branch']} ({bead}) review PASS at {r['tip']}; SENSITIVE-PATH, needs Shane opt-in"
            q = (f"Sensitive-path branch {r['branch']} ({bead}) has a review PASS at {r['tip']} but no recorded "
                 f"opt-in from you. Opt in to land it (yes/no)?")
        else:  # lander's job, never a question to Shane
            audience = "orchestrator"
            tail = " (sensitive-path, opt-in on record)" if sens else ""
            what = f"{r['branch']} ({bead}) review PASS at {r['tip']}, not landed{tail}"
            q = (f"Land {r['branch']} via /land-batch (or file why it cannot land: stale PASS, conflict, "
                 f"renumber) then `gate-watch.py resolve land:{r['branch']} --reason ...`")
        items.append({"id": f"land:{r['branch']}", "kind": "land", "audience": audience, "what": what,
                      "question": q, "tip": r.get("tip"), "waiting_since": iso(since)})
    return items


def shift_item(service_json, rev_created_iso, now):
    """Cloud Run: latest created revision not receiving traffic[0] for > grace."""
    try:
        status = service_json["status"]
        latest = status["latestCreatedRevisionName"]
        traffic = status.get("traffic") or []
        live = traffic[0]["revisionName"] if traffic else None
        if not live or live == latest:
            return None
        created = parse_iso(rev_created_iso)
        if created is None or now - created < SHIFT_GRACE_MIN * 60:
            return None
        return {"id": f"shift:{latest}", "kind": "shift", "audience": "shane", "rev": latest,
                "what": f"Cloud Run revision {latest} built but prod traffic is still on {live}",
                "question": f"Revision {latest} is built and ready; prod traffic is still on {live}. "
                            f"Shift 100% to {latest} now (yes/no)?",
                "waiting_since": iso(created)}
    except Exception:
        return None


def _run(args, timeout):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout


def derive_live(fetch=True, now=None):
    """-> (items, sweep_ok, shift_ok, shift_state). Slow (sweep ~30s): hook never calls this inline."""
    now = now or now_ts()
    items, sweep_ok, shift_ok, shift_state = [], False, False, None
    try:
        cmd = [sys.executable, SWEEP, "--json"] + ([] if fetch else ["--no-fetch"])
        rc, out = _run(cmd, 170)
        if rc in (0, 1) and out.strip():
            items += land_items(json.loads(out).get("candidates") or [], now)
            sweep_ok = True
    except Exception:
        pass
    try:
        rc, out = _run(["gcloud", "run", "services", "describe", SERVICE, "--region", REGION, "--format=json"], 40)
        if rc == 0 and out.strip():
            svc = json.loads(out)
            latest = svc["status"]["latestCreatedRevisionName"]
            traffic = svc["status"].get("traffic") or []
            shift_state = {"latest": latest, "serving": (traffic[0].get("revisionName") if traffic else None)}
            created = None
            if traffic and traffic[0].get("revisionName") != latest:
                rc2, o2 = _run(["gcloud", "run", "revisions", "describe", latest, "--region", REGION,
                                "--format=value(metadata.creationTimestamp)"], 40)
                created = o2.strip() if rc2 == 0 else None
            it = shift_item(svc, created, now)
            if it:
                items.append(it)
            shift_ok = True
    except Exception:
        pass
    return items, sweep_ok, shift_ok, shift_state


def write_cache(items, sweep_ok, shift_ok, now=None, shift_state=None):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        json.dump({"computed_at": now or now_ts(), "items": items, "sweep_ok": sweep_ok, "shift_ok": shift_ok,
                   "shift_state": shift_state}, open(tmp, "w"))
        os.replace(tmp, CACHE)
    except Exception:
        pass


def read_cache():
    try:
        return json.load(open(CACHE))
    except Exception:
        return None


def tip_on_main(tip, repo=None):
    """True only when git positively says tip is an ancestor of origin/main. Any error -> False (keep item)."""
    if not tip:
        return False
    try:
        r = subprocess.run(["git", "-C", repo or REPO, "merge-base", "--is-ancestor", str(tip), "origin/main"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def audience_of(r):
    """Legacy ledger rows predate the audience field: land rows are the orchestrator's unless they ask opt-in."""
    if r.get("audience"):
        return r["audience"]
    if (r.get("kind") == "land" or str(r.get("id", "")).startswith("land:")) \
            and "needs Shane opt-in" not in (r.get("what") or ""):
        return "orchestrator"
    return "shane"


def _resolve(r, now, reason):
    r["resolved_at"] = iso(now)
    r["resolved_reason"] = reason


# ---------------------------------------------------------------- evaluate (pure)
def evaluate(recs, derived, sweep_ok, shift_ok, now, overdue_min=OVERDUE_MIN, is_landed=None, shift_state=None):
    """Merge derived items + ledger. Returns (items, new_recs). Does no I/O itself (is_landed is injected).
    A derived item that has vanished is resolved only when its source was read OK (fail open: a failed
    read changes nothing). A resolved derived item stays resolved while its tip / revision is unchanged."""
    recs = {k: dict(v) for k, v in recs.items()}
    seen = set()
    for d in derived:
        seen.add(d["id"])
        r = recs.get(d["id"])
        if r is not None and r.get("resolved_at"):
            same = (d.get("kind") == "shift"  # the id IS the revision: resolved once, resolved for good
                    or (d.get("tip") and r.get("tip") == d.get("tip")))
            if same:
                continue
            r = None  # new push since it was resolved: reopen
        if r is None:
            r = {"id": d["id"], "first_seen_at": iso(now), "asked_at": None, "last_asked_at": None,
                 "resolved_at": None}
        r.update({"derived": True, "kind": d["kind"], "what": d["what"], "question": d["question"],
                  "waiting_since": d["waiting_since"]})
        for f in ("audience", "tip", "rev"):
            if d.get(f) is not None:
                r[f] = d[f]
        recs[d["id"]] = r
    for k, r in recs.items():
        if r.get("resolved_at"):
            continue
        kind = r.get("kind") or ("land" if k.startswith("land:") else "shift" if k.startswith("shift:") else "")
        if r.get("derived") and k not in seen:
            ok = sweep_ok if kind == "land" else shift_ok if kind == "shift" else False
            if ok:
                _resolve(r, now, "no longer derived (source read OK)")
                continue
        if kind == "land" and is_landed and r.get("tip") and is_landed(r["tip"]):
            _resolve(r, now, f"tip {r['tip']} is an ancestor of origin/main (landed)")
            continue
        if kind == "shift" and shift_state:
            rev = r.get("rev") or k[len("shift:"):]
            if rev and rev == shift_state.get("serving"):
                _resolve(r, now, f"{rev} is already serving prod traffic")
            elif rev and shift_state.get("latest") and rev != shift_state["latest"]:
                _resolve(r, now, f"{rev} superseded by {shift_state['latest']}")
    items = []
    for r in recs.values():
        if r.get("resolved_at"):
            continue
        base = (parse_iso(r.get("last_asked_at")) or parse_iso(r.get("asked_at"))
                or parse_iso(r.get("first_seen_at")) or now)
        since = parse_iso(r.get("waiting_since")) or parse_iso(r.get("first_seen_at")) or now
        aud = audience_of(r)
        deferred = (parse_iso(r.get("deferred_until")) or 0) > now
        due = (now - base) > overdue_min * 60 and not deferred
        items.append({"id": r["id"], "kind": r.get("kind") or "manual", "audience": aud,
                      "what": r.get("what") or r["id"],
                      "question": r.get("question") or f"{r.get('what') or r['id']}: your call?",
                      "waiting_s": int(now - since), "since_asked_s": int(now - base),
                      "last_asked_at": r.get("last_asked_at"), "deferred_until": r.get("deferred_until"),
                      # overdue = Shane must be re-asked; todo_due = orchestrator must act. Never both.
                      "overdue": aud == "shane" and due,
                      "todo_due": aud == "orchestrator" and (now - since) > overdue_min * 60})
    items.sort(key=lambda i: -i["waiting_s"])
    return items, recs


def render_block(items):
    over = [i for i in items if i.get("overdue")]
    todo = [i for i in items if i.get("todo_due")]
    lines = []
    if over:
        shane_n = sum(1 for i in items if i.get("audience", "shane") == "shane")
        ask = f"python3 {os.path.join(HOME, '.claude/scripts/gate-watch.py')} ask <id>"
        lines += [f"RE-ASK SHANE NOW ({len(over)} overdue of {shane_n} waiting on him; threshold {OVERDUE_MIN} min since last ask)",
                  f"Put each question to Shane THIS turn: AskUserQuestion if he is present, otherwise write it to "
                  f"{LIVE_STATE} and send PushNotification. Then run `{ask}` for each so the clock resets "
                  "(or `defer <id> --until ISO` if he already gave a time). Do not wait quietly; do not decide for him.",
                  ""]
        for n, i in enumerate(over, 1):
            last = f"last asked {human(i['since_asked_s'])} ago" if i["last_asked_at"] else "never asked"
            lines.append(f"{n}. [{i['id']}] {i['what']}")
            lines.append(f"   waiting {human(i['waiting_s'])}, {last}")
            lines.append(f"   Ask: {i['question']}")
    if todo:
        if lines:
            lines.append("")
        lines.append(f"ORCHESTRATOR TO-DO ({len(todo)}; lander's job, NOT a question for Shane - never ask him these)")
        for i in todo:
            lines.append(f"- [{i['id']}] {i['what']} (waiting {human(i['waiting_s'])}). {i['question']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- commands
def compute(use_cache, fetch=True, now=None):
    """Run evaluate against live (or cached) derived items and persist ledger updates. -> items"""
    now = now or now_ts()
    if use_cache:
        c = read_cache() or {}
        derived, sweep_ok, shift_ok = c.get("items") or [], bool(c.get("sweep_ok")), bool(c.get("shift_ok"))
        shift_state = c.get("shift_state") if shift_ok else None
    else:
        derived, sweep_ok, shift_ok, shift_state = derive_live(fetch, now)
        write_cache(derived, sweep_ok, shift_ok, now, shift_state)
    with LedgerLock():
        recs, ok = read_ledger()
        items, new = evaluate(recs, derived, sweep_ok, shift_ok, now, is_landed=tip_on_main,
                              shift_state=shift_state)
        if ok:
            try:
                write_ledger(new)
            except Exception:
                pass
    return items


def cmd_mutate(verb, gid, what=None, question=None, now=None, reason=None, until=None):
    now = now or now_ts()
    with LedgerLock():
        recs, ok = read_ledger()
        if not ok:
            print("gate-watch: ledger unreadable; not touching it", file=sys.stderr)
            return 2
        r = recs.get(gid)
        if verb == "add":
            if r and not r.get("resolved_at"):
                print(f"gate-watch: {gid} already open")
                return 0
            recs[gid] = {"id": gid, "what": what or gid, "question": question or f"{what or gid}: your call?",
                         "first_seen_at": iso(now), "asked_at": None, "last_asked_at": None, "resolved_at": None}
        elif verb == "ask":
            if r is None:
                r = recs[gid] = {"id": gid, "first_seen_at": iso(now), "asked_at": None,
                                 "last_asked_at": None, "resolved_at": None}
            r["asked_at"] = r.get("asked_at") or iso(now)
            r["last_asked_at"] = iso(now)
        elif verb == "defer":
            if r is None:
                print(f"gate-watch: no such item {gid}", file=sys.stderr)
                return 1
            u = parse_iso(until)
            if u is None:
                print("gate-watch: defer needs --until ISO-8601 (e.g. 2026-10-08T18:30:00Z)", file=sys.stderr)
                return 2
            r["deferred_until"] = iso(u)
            r["deferred_reason"] = reason or ""
        elif verb == "resolve":
            if r is None:
                print(f"gate-watch: no such item {gid}", file=sys.stderr)
                return 1
            _resolve(r, now, reason or "resolved by hand")
        write_ledger(recs)
    print(f"gate-watch: {verb} {gid} recorded")
    return 0


def is_orchestrator(session_id):
    try:
        r = subprocess.run([sys.executable, ROLE, session_id, "--provider", "claude"], capture_output=True,
                           text=True, timeout=3)
        return r.returncode == 0
    except Exception:
        return False


def spawn_refresh():
    """Detached background refresh; at most one per 5 min (lock mtime)."""
    try:
        lock = CACHE + ".refreshing"
        if os.path.exists(lock) and now_ts() - os.path.getmtime(lock) < 300:
            return
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        open(lock, "w").close()
        subprocess.Popen([sys.executable, SELF, "--refresh"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        pass


def cmd_hook(stdin_text):
    """UserPromptSubmit hook. Silent (no output, exit 0) on anything but 'orchestrator AND overdue'."""
    try:
        sid = json.loads(stdin_text or "{}").get("session_id") or ""
        if not sid or not is_orchestrator(sid):
            return 0
        c = read_cache()
        if not c or now_ts() - float(c.get("computed_at") or 0) > CACHE_FRESH_S:
            spawn_refresh()
        block = render_block(compute(use_cache=True))
        if block:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "additionalContext": block}}))
    except Exception:
        pass
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("add", "ask", "defer", "resolve"):
        p = argparse.ArgumentParser(prog="gate-watch.py " + argv[0])
        p.add_argument("verb")
        p.add_argument("id")
        p.add_argument("--what")
        p.add_argument("--question")
        p.add_argument("--reason")
        p.add_argument("--until")
        a = p.parse_args(argv)
        return cmd_mutate(a.verb, a.id, a.what, a.question, reason=a.reason, until=a.until)
    if "--hook" in argv:
        return cmd_hook(sys.stdin.read())
    if "--refresh" in argv:
        try:
            items, s_ok, h_ok, st = derive_live(True)
            write_cache(items, s_ok, h_ok, shift_state=st)
            compute(use_cache=True)
        except Exception:
            pass
        finally:
            try:
                os.remove(CACHE + ".refreshing")
            except Exception:
                pass
        return 0
    try:
        items = compute(use_cache=False, fetch="--no-fetch" not in argv)
    except Exception as e:  # fail open
        print(f"gate-watch: could not compute ({e}); reporting nothing", file=sys.stderr)
        items = []
    over = [i for i in items if i["overdue"]]
    if "--json" in argv:
        print(json.dumps({"waiting": items, "overdue": [i["id"] for i in over],
                          "todo": [i["id"] for i in items if i.get("todo_due")], "exit": 1 if over else 0}, indent=1))
    else:
        shane_n = sum(1 for i in items if i.get("audience") == "shane")
        print(render_block(items) or f"gate-watch: {shane_n} item(s) waiting on Shane, none overdue; "
                                     f"{len(items) - shane_n} orchestrator to-do(s), none due")
    return 1 if over else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"gate-watch: error {e}", file=sys.stderr)
        sys.exit(0)
