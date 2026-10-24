#!/usr/bin/env python3
"""Write settings-live.patch: route settings.json hook commands through run-gate (LUCY-wqn96 L4).

Usage: python3 scripts/git-hooks/make-settings-patch.py [settings.json] [repo] > settings-live.patch
       (defaults: ~/.claude/settings.json, ~/.claude)

Every hook command that runs a gate file committed on the repo's main (`/abs/.claude/<rel>`,
optionally after `python3 ` or `node `) becomes `<repo>/.git/live/run-gate <rel> ...`, with
--fail-exit 2 on PreToolUse (Claude Code blocks only on exit 2). A gate file main has no
committed copy of cannot be exported, so it stays on the checkout path and is listed in the
header. Only the "command" strings change.
"""
import difflib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))


def load_hook():
    loader = SourceFileLoader("wqn96_reference_transaction", os.path.join(HERE, "reference-transaction"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def command_re(repo):
    return re.compile(r"(?:python3 |node )?" + re.escape(repo) + r"/(\S+)(.*)", re.S)


def route(settings, repo):
    """(patched text, [uncommitted gate commands left alone])."""
    hook = load_hook()
    src = open(settings).read()
    data = json.loads(src)
    committed = set(subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", "main"],
                                   capture_output=True, text=True, check=True).stdout.splitlines())
    run_gate = os.path.join(repo, ".git/live/run-gate")
    pattern = command_re(repo)
    out, left, done = src, [], set()
    for event, matchers in data.get("hooks", {}).items():
        for matcher in matchers:
            for h in matcher.get("hooks", []):
                cmd = h.get("command", "")
                m = pattern.fullmatch(cmd)
                if not m or not hook.is_gate_path(m.group(1)) or (event, cmd) in done:
                    continue
                done.add((event, cmd))
                rel, rest = m.groups()
                if rel not in committed:
                    left.append("%s: %s" % (event, cmd))
                    continue
                fail = "--fail-exit 2 " if event == "PreToolUse" else ""
                new = "%s %s%s%s" % (run_gate, fail, rel, rest)
                out = out.replace('"command": ' + json.dumps(cmd), '"command": ' + json.dumps(new))
    json.loads(out)
    return out, left


def main(argv):
    settings = argv[0] if argv else os.path.expanduser("~/.claude/settings.json")
    repo = argv[1] if len(argv) > 1 else os.path.expanduser("~/.claude")
    patched, left = route(settings, repo)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    sys.stdout.write(
        "settings-live.patch (LUCY-wqn96 L4): route settings.json hook commands through the live export.\n\n"
        "Apply ONLY after scripts/git-hooks/install.sh has run (run-gate must exist):\n"
        "  patch -p1 -d ~/.claude < ~/.claude/scripts/git-hooks/settings-live.patch\n"
        "Generated %s by scripts/git-hooks/make-settings-patch.py against the LIVE settings.json\n"
        "(it may differ from committed main). If it no longer applies, regenerate it:\n"
        "  python3 ~/.claude/scripts/git-hooks/make-settings-patch.py > /tmp/settings-live.patch\n\n"
        "- PreToolUse guards get --fail-exit 2: Claude Code blocks on exit 2 and only warns on any\n"
        "  other code, so a drifted export must block, not wave the tool through.\n"
        "- Left on the checkout path, because main has no committed copy to export (commit them\n"
        "  first, then regenerate):\n%s\n"
        % (stamp, "".join("    %s\n" % c for c in left) or "    (none)\n"))
    sys.stdout.writelines(difflib.unified_diff(
        open(settings).read().splitlines(True), patched.splitlines(True),
        "a/settings.json", "b/settings.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
