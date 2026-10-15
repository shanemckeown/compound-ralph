#!/usr/bin/env python3
"""claude_only_guard.py: keep Claude-only clients' beads away from codex/Astra/GLM (LUCY-b3hf5).

Standing rule: some clients' work is reviewed by Claude only. On 15 Oct a /long-goal
builder ran Astra six times on an Anita branch because the rule was prose. This makes
it structure: one allowlist (claude-only-clients.json next to this file), read by every
codex/GLM caller.

A bead is Claude-only when it, its parent chain, or a dotted-id ancestor carries a
listed label, or is a listed bead id.

Usage:
  claude_only_guard.py route <bead>              # prints "claude" or "astra" + reason (dry run)
  claude_only_guard.py check <bead>...           # exit 3 if any bead is Claude-only
  claude_only_guard.py exec-check [--label L] -- <argv...>
      # used by bin/codex and fleet-guarded.sh: finds bead ids in L, $BEAD_ID/$EPIC_ID/
      # $CHILD_ID, and the git branch of any --cd/-C dir and the cwd. Exit 3 = refuse.

Fails closed when a bead is attributed but its labels can't be read (bd down, bad
allowlist): the call refuses with the reason. With no bead attributable there is no
client context, so the call passes.

Subprocesses get stdin=DEVNULL: behind bin/codex, stdin is the review brief for codex.

Env: CLAUDE_ONLY_ALLOWLIST (path override), CLAUDE_ONLY_BD (bd binary override).
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST = os.environ.get("CLAUDE_ONLY_ALLOWLIST") or os.path.join(HERE, "claude-only-clients.json")
BD = os.environ.get("CLAUDE_ONLY_BD") or "bd"
# Mirrors fleet-dispatch.py beads_dir(): LUCY-* is the vault, everything else the code repo.
VAULT_BEADS = "/Users/shane/Documents/Obsidian/.beads"
REPO_BEADS = "/Users/shane/Documents/GitReBase/AestheticcNext/.beads"
BEAD_RE = re.compile(r"(?<![A-Za-z0-9])(aestheticcnext|lucy)-([a-z0-9]+(?:\.[0-9]+)*)", re.I)
ENV_VARS = ("BEAD_ID", "EPIC_ID", "CHILD_ID")
REFUSE = 3
MAX_DEPTH = 6


class LookupFailed(Exception):
    pass


def canon(prefix, rest):
    return ("LUCY-" if prefix.lower() == "lucy" else "AestheticcNext-") + rest.lower()


def find_beads(text):
    return [canon(m.group(1), m.group(2)) for m in BEAD_RE.finditer(text or "")]


def load_allowlist():
    try:
        with open(ALLOWLIST) as f:
            data = json.load(f)
        labels = {str(x).lower() for x in data.get("labels", [])}
        beads = {str(x).lower() for x in data.get("beads", [])}
    except (OSError, ValueError, AttributeError) as exc:
        raise LookupFailed(f"allowlist {ALLOWLIST} unreadable: {exc}")
    return labels, beads


def bd_show(bead):
    """Return (labels, parent) or None when the bead doesn't exist."""
    env = dict(os.environ, BEADS_DIR=VAULT_BEADS if bead.upper().startswith("LUCY-") else REPO_BEADS)
    try:
        r = subprocess.run([BD, "show", bead, "--json"], capture_output=True, text=True,
                           timeout=30, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LookupFailed(f"bd show {bead} failed: {exc}")
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        if "no issue found" in out.lower() or "no issues found" in out.lower():
            return None
        raise LookupFailed(f"bd show {bead} exited {r.returncode}: {out.strip()[:200]}")
    try:
        data = json.loads(r.stdout)
    except ValueError as exc:
        raise LookupFailed(f"bd show {bead} returned non-JSON: {exc}")
    if isinstance(data, list):
        data = data[0] if data else {}
    return [str(l) for l in (data.get("labels") or [])], data.get("parent")


def lineage(bead):
    """The bead, then each dotted-id ancestor (a.b.c -> a.b -> a)."""
    out = [bead]
    while "." in bead:
        bead = bead.rsplit(".", 1)[0]
        out.append(bead)
    return out


def why_claude_only(bead):
    """Reason string if the bead is Claude-only, else None. Raises LookupFailed."""
    labels, ids = load_allowlist()
    seen = set()
    todo = list(lineage(bead))
    while todo and len(seen) < MAX_DEPTH * 2:
        cur = todo.pop(0)
        if cur.lower() in seen:
            continue
        seen.add(cur.lower())
        if cur.lower() in ids:
            return f"{cur} is on the Claude-only list"
        info = bd_show(cur)
        if info is None:
            continue
        cur_labels, parent = info
        hit = sorted(l for l in cur_labels if l.lower() in labels)
        if hit:
            via = "" if cur == bead else f" (via {cur})"
            return f"{bead} carries Claude-only label {hit[0]}{via}"
        if parent:
            todo.extend(lineage(str(parent)))
    return None


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                           timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def git_branch(path):
    """Branch name(s) for the checkout at path. A detached HEAD (a review scratch worktree
    at a branch tip, since git won't check out a branch another worktree holds) falls back
    to every local branch containing HEAD, so a scratch at an older tip still counts.
    A detached HEAD already on main carries no client diff, so it is not attributed:
    otherwise every branch cut from main would match a scratch at main."""
    name = _git(path, "symbolic-ref", "--short", "-q", "HEAD")
    if name:
        return name
    for main in ("origin/main", "main"):
        if _git(path, "rev-parse", "-q", "--verify", main + "^{commit}"):
            if _is_ancestor(path, main):
                return ""
            return _git(path, "branch", "--contains", "HEAD", "--format=%(refname:short)")
    return _git(path, "for-each-ref", "--points-at", "HEAD", "--format=%(refname:short)", "refs/heads")


def _is_ancestor(path, ref):
    try:
        r = subprocess.run(["git", "-C", path, "merge-base", "--is-ancestor", "HEAD", ref],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def dirs_from_argv(argv):
    out = []
    for i, a in enumerate(argv):
        if a in ("--cd", "-C") and i + 1 < len(argv):
            out.append(argv[i + 1])
        elif a.startswith("--cd="):
            out.append(a.split("=", 1)[1])
    return out


def context_beads(label, argv):
    found = find_beads(label)
    for var in ENV_VARS:
        found += find_beads(os.environ.get(var, ""))
    for d in dirs_from_argv(argv) + [os.getcwd()]:
        found += find_beads(git_branch(d))
    uniq = []
    for b in found:
        if b.lower() not in {u.lower() for u in uniq}:
            uniq.append(b)
    return uniq


def refusal(beads):
    """Message if any bead is Claude-only or can't be checked, else None."""
    for b in beads:
        try:
            why = why_claude_only(b)
        except LookupFailed as exc:
            return (f"cannot confirm {b} is not a Claude-only client ({exc}). "
                    "Refusing to send it to a non-Claude model; fix the lookup and retry.")
        if why:
            return (f"{why}. This client's work is reviewed by Claude only: no codex/Astra/GLM. "
                    "Use a fresh-context Opus reviewer instead (/goal Phase 4, claude route).")
    return None


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 64
    cmd, rest = argv[0], argv[1:]
    if cmd == "route":
        if len(rest) != 1:
            print("usage: claude_only_guard.py route <bead>", file=sys.stderr)
            return 64
        try:
            why = why_claude_only(rest[0])
        except LookupFailed as exc:
            print(f"claude\t(lookup failed, routing to the safe side: {exc})")
            return 0
        print(f"claude\t{why}" if why else f"astra\t{rest[0]} is not a Claude-only client")
        return 0
    if cmd == "check":
        msg = refusal(rest)
        if msg:
            print(f"claude-only-guard: REFUSED. {msg}", file=sys.stderr)
            return REFUSE
        return 0
    if cmd == "exec-check":
        label = ""
        if rest[:1] == ["--label"] and len(rest) >= 2:
            label, rest = rest[1], rest[2:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        msg = refusal(context_beads(label, rest))
        if msg:
            print(f"claude-only-guard: REFUSED. {msg}", file=sys.stderr)
            return REFUSE
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
