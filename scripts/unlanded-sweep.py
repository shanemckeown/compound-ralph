#!/usr/bin/env python3
"""unlanded-sweep.py - the fact about finished-but-unlanded work.

Why (2 Oct 2026): a P0 consent fix sat pushed+reviewed but unlanded ~20h because nothing looked at
pushed BRANCHES (fleet-supervisor only sees sessions) and a rollup fragment saying "NOT landed" was
folded into prose. This script looks at origin itself.

For every remote branch (except main / mobile-android / HEAD) whose tip is not an ancestor of
origin/main and whose last commit is within --days, decide whether it is REALLY unlanded, then give a
status. Also scans recent closeout fragments for "not landed" lines and flags any that git says are
still unlanded.

It also lists non-closed beads whose work is already on main (`landed_open`); --close-landed closes
the PUSHED ones `LANDED @<sha>` (LUCY-g1u0e; land-batch runs it after every push).

Exit 0 only when nothing is LAND-READY / NEEDS-REVIEW / UNKNOWN and no fragment is flagged.
Usage: unlanded-sweep.py [--json] [--days N] [--no-fetch] [--close-landed]
"""
import glob
import json
import os
import re
import subprocess
import sys
import time

REPO = os.environ.get("AESTHETICC_REPO", "/Users/shane/Documents/GitReBase/AestheticcNext")
REVIEWS = os.path.expanduser("~/Documents/Obsidian/Aestheticc/Ops/reviews")
CLOSEOUTS = os.path.expanduser("~/Documents/Obsidian/Sessions/closeouts")
BLOCKING = {"LAND-READY", "NEEDS-REVIEW", "UNKNOWN"}
KEY_RE = re.compile(r"(?:aestheticcnext|lucy)-([a-z0-9]+(?:\.\d+)?)", re.I)
FRAG_RE = re.compile(r"not landed|unlanded|Next:.*land-batch", re.I)


def sh(args, timeout=60, env=None, cwd=REPO):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, r.stdout


def git(*a, timeout=60):
    return sh(["git", *a], timeout)


def branch_key(name):
    m = KEY_RE.search(name)
    return m.group(1).lower() if m else None


def has_key(text, key):
    return bool(re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text, re.I))


def land_commits():
    rc, out = git("log", "origin/main", "-i", "--grep=^land", "--grep=patch method",
                  "--format=%H\t%ct\t%s", "-n", "3000")
    rows = []
    for line in out.splitlines():
        p = line.split("\t", 2)
        if len(p) == 3:
            rows.append((p[0], int(p[1]), p[2]))
    return rows


def landed_elsewhere(branch, tip, ct, key, land, land_subjects):
    """Why a branch whose tip is not an ancestor of origin/main is landed anyway, or None.
    Shared with fleet-dispatch.py's WIP cap so both count "unlanded" the same way."""
    if tip[:9] in land_subjects:
        return "a land commit names this exact tip"
    if key and any(t >= ct and has_key(s, key) for _, t, s in land):
        return "landed via a later -rN / sibling land commit"
    rc, cherry = git("cherry", "origin/main", f"origin/{branch}")  # slowest test last
    if rc == 0 and cherry.strip() and "+" not in cherry:
        return "every commit patch-equivalent on main"
    return None


def bead(key, env):
    if not key:
        return None
    for prefix in ("AestheticcNext-", "LUCY-"):
        try:
            rc, out = sh(["bd", "show", prefix + key, "--json"], 30, env, cwd=REPO)
            if rc == 0 and out.strip():
                d = json.loads(out)
                d = d[0] if isinstance(d, list) else d
                if d and d.get("id"):
                    return d
        except Exception:
            pass
    return None


def bead_text(d):
    parts = [str(d.get(k) or "") for k in ("description", "notes", "close_reason")]
    for c in d.get("comments") or []:
        parts.append(str(c.get("text") or c.get("body") or c))
    return "\n".join(parts)


def sha_landed_in_text(text, key):
    """bead closed with a merge sha that is a land commit on main."""
    for m in set(re.findall(r"\b[0-9a-f]{7,40}\b", text)):
        if not re.search(r"[a-f]", m) or not re.search(r"\d", m):
            continue
        rc, subj = git("log", "-1", "--format=%s", m)
        if rc != 0 or not subj.strip():
            continue
        rc, _ = git("merge-base", "--is-ancestor", m, "origin/main")
        if rc == 0 and (subj.lower().startswith("land") or (key and has_key(subj, key))):
            return m[:9]
    return None


def review_verdict(key, branch, tip):
    best = None
    if not os.path.isdir(REVIEWS):
        return None
    for f in glob.glob(os.path.join(REVIEWS, "REVIEW_*.md")):
        base = os.path.basename(f)
        try:
            txt = open(f, errors="replace").read()
        except Exception:
            continue
        if not ((key and has_key(base, key)) or (key and re.search(
                r"(?:AestheticcNext|LUCY)-" + re.escape(key) + r"(?![a-z0-9])", txt[:600], re.I))
                or branch in txt[:1500]):
            continue
        m = re.search(r"Verdict:\s*\**\s*(PASS|FAIL)", txt, re.I) or re.search(r"\b(PASS|FAIL)\b", txt)
        if not m:
            continue
        tm = re.search(r"\btip\s+([0-9a-f]{7,40})", txt[:400])
        stale = bool(tm and not tip.startswith(tm.group(1)[:7]) and not tm.group(1).startswith(tip[:7]))
        mt = os.path.getmtime(f)
        if best is None or mt > best["mtime"]:
            best = {"file": base, "verdict": m.group(1).upper(), "stale_tip": stale, "mtime": mt}
    return best


def _load_safety():
    """land-batch's own review_pass_for / is_sensitive_path, so the sweep and the lander agree. None on any error."""
    try:
        import importlib.util
        f = os.path.expanduser("~/.claude/skills/land-batch/bin/land_batch_safety.py")
        spec = importlib.util.spec_from_file_location("land_batch_safety", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


OPT_IN_RE = re.compile(r"^\W*SENSITIVE-PATH OPT-IN", re.I | re.M)


def comments_of(d):
    return "\n".join(str(c.get("text") or c.get("body") or c) for c in (d.get("comments") or []))


def comment_review(d, tip, safety=None):
    """(verdict, evidence, pass_at) from bead comments at the exact tip via land-batch's review_pass_for.
    verdict: 'PASS' | 'FAIL' | None (nothing recorded / cannot tell). Never raises."""
    try:
        safety = safety or _load_safety()
        if not safety or not d or not d.get("id"):
            return None, "", None
        ok, ev = safety.review_pass_for(d["id"], tip, comments_text=comments_of(d))
        if not ok:
            return ("FAIL" if ev.startswith("FAIL recorded") else None), ev, None
        pass_at = None
        for c in d.get("comments") or []:
            if isinstance(c, dict) and safety.review_pass_for(d["id"], tip, comments_text=str(c.get("text") or ""))[0]:
                pass_at = c.get("created_at")
        return "PASS", ev, pass_at
    except Exception:
        return None, "", None


def live_sessions():
    try:
        rc, out = sh(["claude", "agents", "--json"], 30)
        rows = json.loads(out)
        return rows if isinstance(rows, list) else []
    except Exception:
        return None


def owner_of(key, agents):
    if not agents or not key:
        return None
    for a in agents:
        hay = f"{a.get('name','')} {a.get('cwd','')}"
        if has_key(hay, key) and a.get("state") == "working" and a.get("status") != "idle":
            return a.get("name") or a.get("id")
    return None


def sweep(days, fetch=True):
    if fetch:
        git("fetch", "-q", "origin", timeout=120)
    env = dict(os.environ, BEADS_DIR=os.path.join(REPO, ".beads"))
    land = land_commits()
    land_subjects = "\n".join(s for _, _, s in land)
    agents = live_sessions()
    cutoff = time.time() - days * 86400
    rc, refs = git("for-each-ref", "--format=%(refname:short)\t%(objectname)\t%(committerdate:unix)",
                   "--sort=-committerdate", "refs/remotes/origin")
    cands = []
    for line in refs.splitlines():
        ref, sha, ct = line.split("\t")
        name = ref[len("origin/"):] if ref.startswith("origin/") else ref
        if name in ("HEAD", "main", "mobile-android") or ref == "origin":
            continue
        if int(ct) < cutoff:
            continue
        if git("merge-base", "--is-ancestor", sha, "origin/main")[0] == 0:
            continue
        cands.append({"branch": name, "tip": sha, "ct": int(ct)})

    for c in cands:
        c["key"] = branch_key(c["branch"])
        if not c["key"]:  # tip commit text can name the bead when the branch name does not
            _, msg = git("log", "-5", "--format=%s%n%b", f"origin/{c['branch']}")
            c["key"] = branch_key(msg)

    out = []
    for c in cands:
        b, tip, key = c["branch"], c["tip"], c["key"]
        r = {"branch": b, "tip": tip[:9], "age_days": round((time.time() - c["ct"]) / 86400, 1),
             "key": key, "bead": None, "review": None, "owner": None, "status": None, "why": ""}
        # --- is it really unlanded? ---
        if landed_elsewhere(b, tip, c["ct"], key, land, land_subjects):
            continue
        d = bead(key, env)
        if d:
            r["bead"] = {"id": d.get("id"), "title": (d.get("title") or "")[:90],
                         "priority": d.get("priority"), "status": d.get("status")}
            r["_closed_dead"] = (d.get("status") == "closed" and bool(re.search(
                r"supersed|obsolete|duplicate|wontfix|abandon|not needed", bead_text(d), re.I)))
            if sha_landed_in_text(bead_text(d), key):
                continue  # bead records a merge sha that is on main
        r["review"] = review_verdict(key, b, tip)
        r["comment_review"] = comment_review(d, tip) if d else (None, "", None)
        r["opt_in_recorded"] = bool(d and OPT_IN_RE.search(comments_of(d)))
        r["sensitive"] = False
        try:
            sf = _load_safety()
            if sf:
                _, names = git("diff", "--name-only", f"origin/main...origin/{b}")
                r["sensitive"] = bool(sf.sensitive_paths(names.split()))
        except Exception:
            pass
        r["owner"] = owner_of(key, agents)
        out.append(r)

    # newest sibling per key wins; older unlanded siblings are superseded by it
    by_key = {}
    for r in out:
        if r["key"]:
            by_key.setdefault(r["key"], []).append(r)
    for k, rs in by_key.items():
        if len(rs) > 1:
            rs.sort(key=lambda x: x["age_days"])
            for old in rs[1:]:
                old["status"] = "SUPERSEDED/DEAD"
                old["why"] = f"newer unlanded sibling {rs[0]['branch']} carries this bead"

    for r in out:
        if r["status"]:
            continue
        rv = r["review"]
        if r["owner"]:
            r["status"], r["why"] = "OWNED", f"live session: {r['owner']}"
        elif r.get("_closed_dead"):
            r["status"], r["why"] = "SUPERSEDED/DEAD", "bead closed as superseded/obsolete/duplicate"
        elif rv and rv["verdict"] == "FAIL" and not rv["stale_tip"]:
            r["status"], r["why"] = "NEEDS-REWORK", f"review FAIL ({rv['file']})"
        elif rv and rv["verdict"] == "PASS" and not rv["stale_tip"]:
            r["status"], r["why"] = "LAND-READY", f"review PASS ({rv['file']})"
        elif r["comment_review"][0] == "PASS":
            r["status"], r["why"] = "LAND-READY", f"review PASS at exact tip ({r['comment_review'][1][:100]})"
        elif r["comment_review"][0] == "FAIL" and re.search(r"\bPASS\b", r["comment_review"][1]):
            # review_pass_for lets any FAIL word on a tip-naming line veto it, so "MANAGER PASS @tip ... after an
            # earlier Opus FAIL" reads as FAIL. Not rework: a human must reconcile the comment wording.
            r["status"] = "NEEDS-REVIEW"
            r["why"] = ("AMBIGUOUS: a bead comment names this exact tip with PASS and also the word FAIL (an earlier "
                        "round); land-batch review_pass_for will refuse it until a clean PASS line is recorded. "
                        f"Evidence: {r['comment_review'][1][:120]}")
        elif r["comment_review"][0] == "FAIL":
            r["status"], r["why"] = "NEEDS-REWORK", f"bead-comment FAIL at exact tip ({r['comment_review'][1][:100]})"
        elif r["bead"] or rv:
            r["status"] = "NEEDS-REVIEW"
            r["why"] = (f"review verdict is on an older tip ({rv['file']})" if rv else
                        "no review file in Ops/reviews (bead notes may claim an own-gate PASS)")
        else:
            r["status"], r["why"] = "UNKNOWN", "no bead and no review found for this branch"
        r.pop("_closed_dead", None)
    for r in out:
        r.pop("_closed_dead", None)
        cr = r.pop("comment_review", (None, "", None))
        r["review_evidence"] = cr[1] if cr[0] else None
        r["pass_at"] = cr[2]
    return out


# --- landed but not closed (LUCY-g1u0e) ---------------------------------------------------------
# A branch that IS on main never reaches sweep(), so its PUSHED bead sat open for weeks (24 Oct: 21 of
# 134 PUSHED beads had a land commit on main). Only PUSHED beads auto-close: a long-goal epic lands one
# child at a time and stays open on purpose, and in_progress means a session may be re-working it.
LAND_SUBJ_RE = re.compile(r"^land:\s+(\S+)\s+@\s+([0-9a-f]{7,40})", re.I)
EXACT_KEY_RE = re.compile(r"(?:^|[/-])aestheticcnext-([a-z0-9]+(?:\.\d+)*)(?:-r\d+)?$", re.I)
REDISPATCH_RE = re.compile(r"^\s*(?:started /goal|started /long-goal|RESUME:)", re.I)


def exact_key(branch):
    """Bead key a branch carries exactly: goal/aestheticcnext-x, ...-x-r2, worktree-goal-..., auto/AestheticcNext-x.
    None for long-goal child branches (aestheticcnext-1b6a3-32): those name a child, not the epic."""
    m = EXACT_KEY_RE.search(branch or "")
    return m.group(1).lower() if m else None


def landed_index(land):
    """key -> newest (land sha, commit time, subject) among `land: <branch> @ <tip>` commits on main."""
    idx = {}
    for sha, ct, subj in land:
        m = LAND_SUBJ_RE.match(subj)
        k = exact_key(m.group(1)) if m else None
        if k and (k not in idx or ct > idx[k][1]):
            idx[k] = (sha, ct, subj)
    return idx


def _ts(s):
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def decide_landed(d, land_hit, refs):
    """What to do with one non-closed bead. land_hit: (sha, ct, subject) or None. refs: [{branch, tip, ct,
    on_main, names_bead}] for remote branches carrying this bead's exact key. Returns None (no landing
    evidence) or {action: close|report|skip, sha, ct, evidence, why}."""
    ev = None
    if land_hit:
        ev = {"sha": land_hit[0], "ct": land_hit[1], "evidence": land_hit[2][:140]}
    else:  # no land commit, but the bead's own branch tip is on main (fast-forward / manual land)
        on = [r for r in refs if r["on_main"] and r["names_bead"]]
        if on:
            r = max(on, key=lambda x: x["ct"])
            ev = {"sha": r["tip"], "ct": r["ct"], "evidence": f"branch {r['branch']} tip is an ancestor of origin/main"}
    if not ev:
        return None
    newer = [r for r in refs if not r["on_main"] and r["ct"] > ev["ct"]]
    if newer:
        return dict(ev, action="skip", why=f"unlanded branch {newer[0]['branch']} is newer than the land")
    for c in d.get("comments") or []:
        t = _ts(c.get("created_at")) if isinstance(c, dict) else None
        if t and t > ev["ct"] and REDISPATCH_RE.search(str(c.get("text") or "")):
            return dict(ev, action="skip", why="re-dispatched after the land (a newer started /goal or RESUME: comment)")
    if d.get("status") != "pushed":
        return dict(ev, action="report", why=f"status {d.get('status')}: only PUSHED beads auto-close; check by hand")
    return dict(ev, action="close", why="PUSHED and its land is on origin/main")


def landed_open(env, land):
    """Non-closed AestheticcNext- beads whose work is already on origin/main."""
    rc, out = sh(["bd", "list", "--json", "--limit", "0"], 120, env)
    if rc != 0:
        raise RuntimeError("bd list failed")
    rows = [b for b in json.loads(out or "[]") if str(b.get("id", "")).startswith("AestheticcNext-")
            and b.get("status") != "closed"]
    idx = landed_index(land)
    rc, refs_out = git("for-each-ref", "--format=%(refname:short)\t%(objectname)\t%(committerdate:unix)",
                       "refs/remotes/origin")
    by_key = {}
    for line in refs_out.splitlines():
        p = line.split("\t")
        if len(p) == 3 and p[0].startswith("origin/"):
            k = exact_key(p[0][len("origin/"):])
            if k:
                by_key.setdefault(k, []).append((p[0][len("origin/"):], p[1], int(p[2])))
    res = []
    for b in rows:
        k = b["id"].split("-", 1)[1].lower()
        if k not in idx and k not in by_key:
            continue
        refs = []
        for br, tip, ct in by_key.get(k, []):
            _, msg = git("log", "-1", "--format=%s%n%b", tip)
            refs.append({"branch": br, "tip": tip, "ct": ct, "names_bead": has_key(msg, k),
                         "on_main": git("merge-base", "--is-ancestor", tip, "origin/main")[0] == 0})
        if k not in idx and not any(r["on_main"] for r in refs):
            continue
        d = bead(k, env) or b
        r = decide_landed(d, idx.get(k), refs)
        if r:
            res.append({"bead": b["id"], "status": b.get("status"), "priority": b.get("priority"),
                        "title": (b.get("title") or "")[:90], "sha": r["sha"][:10], "evidence": r["evidence"],
                        "action": r["action"], "why": r["why"]})
    return res


def close_landed(items, env):
    """bd close every action=close item, re-verifying ancestry first. Sets item['closed'] True/False."""
    for it in items:
        if it["action"] != "close":
            continue
        if git("merge-base", "--is-ancestor", it["sha"], "origin/main")[0] != 0:
            it["closed"], it["why"] = False, "sha no longer an ancestor of origin/main; not closed"
            continue
        rc, _ = sh(["bd", "close", it["bead"], "--reason",
                    f"LANDED @{it['sha']}: {it['evidence']} (verified ancestor of origin/main; "
                    "unlanded-sweep --close-landed, LUCY-g1u0e)"], 60, env)
        it["closed"] = rc == 0
    return items


def fragments(real):
    """closeout lines saying unlanded; flag those git still says are unlanded."""
    res = []
    files = []
    for pat in ("*.md", "rolled/*/*.md"):
        files += glob.glob(os.path.join(CLOSEOUTS, pat))
    cut = time.time() - 3 * 86400
    live = [r for r in real if r["status"] != "SUPERSEDED/DEAD"]
    for f in files:
        if os.path.basename(f) == "README.md" or os.path.getmtime(f) < cut:
            continue
        try:
            lines = open(f, errors="replace").read().splitlines()
        except Exception:
            continue
        for i, ln in enumerate(lines, 1):
            if not FRAG_RE.search(ln):
                continue
            hits = [r for r in live if (r["key"] and has_key(ln, r["key"])) or r["branch"] in ln]
            ids = sorted(set(re.findall(r"(?:AestheticcNext|LUCY)-[a-z0-9.]+", ln)))
            res.append({"file": os.path.relpath(f, CLOSEOUTS), "line": i, "text": ln.strip()[:200],
                        "names": ids or [h["branch"] for h in hits],
                        "flag": "FRAGMENT SAYS UNLANDED" if hits else None,
                        "still_unlanded": [h["branch"] for h in hits]})
    return res


def main():
    args = sys.argv[1:]
    days = 10
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    real = sweep(days, fetch="--no-fetch" not in args)
    frags = fragments(real)
    flagged = [f for f in frags if f["flag"]]
    blocking = [r for r in real if r["status"] in BLOCKING]
    code = 1 if (blocking or flagged) else 0  # landed_open never changes the exit: callers gate on unlanded work
    env = dict(os.environ, BEADS_DIR=os.path.join(REPO, ".beads"))
    try:
        lo = landed_open(env, land_commits())
        if "--close-landed" in args:
            close_landed(lo, env)
    except Exception as e:
        lo = [{"bead": None, "action": "error", "why": f"landed-open check failed: {e}"}]
    if "--json" in args:
        print(json.dumps({"days": days, "candidates": real, "fragments": frags, "landed_open": lo,
                          "blocking": [r["branch"] for r in blocking], "exit": code}, indent=1))
        return code
    order = {"LAND-READY": 0, "NEEDS-REVIEW": 1, "UNKNOWN": 2, "NEEDS-REWORK": 3, "OWNED": 4, "SUPERSEDED/DEAD": 5}
    print(f"UNLANDED SWEEP (origin/*, last {days}d, vs origin/main): "
          f"{len(blocking)} blocking, {len(real)} unlanded candidate(s)")
    for r in sorted(real, key=lambda x: (order.get(x["status"], 9), x["age_days"])):
        bd = r["bead"]
        bl = f"{bd['id']} P{bd['priority']} {bd['status']} - {bd['title']}" if bd else "no bead"
        print(f"  [{r['status']}] {r['branch']} @ {r['tip']} ({r['age_days']}d)  {bl}")
        print(f"      {r['why']}")
    if flagged:
        print("FRAGMENT SAYS UNLANDED (closeout lines naming work git says is still unlanded):")
        for f in flagged:
            print(f"  {f['file']}:{f['line']} -> {', '.join(f['still_unlanded'])}: {f['text'][:140]}")
    unflagged = [f for f in frags if not f["flag"]]
    if unflagged:
        print(f"({len(unflagged)} other fragment line(s) mention 'not landed' but name work that is landed/not found)")
    if lo:
        n = sum(1 for i in lo if i["action"] == "close" and not i.get("closed"))
        print(f"LANDED BUT NOT CLOSED (work on origin/main, bead still open): {len(lo)} "
              f"({n} PUSHED to close{'' if '--close-landed' in args else '; run with --close-landed'})")
        for i in lo:
            tag = "CLOSED" if i.get("closed") else i["action"].upper()
            print(f"  [{tag}] {i['bead']} {i.get('status', '')} @ {i.get('sha', '')}  {i['why']}")
    print("RESULT:", "CLEAN" if code == 0 else "BLOCKING (exit 1)")
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # never silent
        print(f"UNLANDED SWEEP ERROR: {e}", file=sys.stderr)
        sys.exit(2)
