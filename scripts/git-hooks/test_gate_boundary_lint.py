"""Lint: no exported gate reaches back into the ~/.claude checkout (LUCY-p122j layer-B r7/r8).

run-gate executes main's committed export, but a gate that names a helper by absolute path
(~/.claude/scripts/x.py) still runs the checkout's copy, uncommitted edits included. D1: the
unlanded sweep, the one gate dependency that did this, now resolves beside its caller. D2 is a
LINT, not a guarantee: a regex sweep over every exported code file fails on any ~/.claude
reference that isn't data, the installed launcher, or an allow entry with a written reason (a
reference to a gate file is never allowable). It catches the common shapes; it can't prove a
dependency graph. The guarantee for the environment is structural instead (r8 E1): run-gate
passes only allowlisted variables, and the env lint below makes every variable an exported
gate reads a written decision, allowed or dropped.
Run: ~/.claude/skills/land-batch/.venv/bin/python3 -m pytest scripts/git-hooks/ -q
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import textwrap

import pytest

from test_reference_transaction import (HOOKS_DIR, REAL_ROOT, Repo, assert_refused,
                                        commit_files, load_hook)

CODE_EXT = (".py", ".sh", ".bash", ".mjs", ".js", ".cjs")
# First path segment under ~/.claude that only ever holds data or state, never code.
DATA_ROOTS = {"state", "fleet", "evidence", "projects", "sessions", "observer", "belay",
              "worktrees", "jobs", "mcp-tiers", "plans"}
DATA_PART_RE = re.compile(r"(?:^|/)(?:logs?|state|markers|seen|sessions)(?:/|$)")
DATA_EXT = (".log", ".json", ".jsonl", ".md", ".txt", ".lock", ".diarised")
LAUNCHER = ".git/live"  # the installed run-gate: the trust anchor, not checkout code

# (exported file, ~/.claude target) -> why it may stay. The one code target here is command
# text tier_gates emits for the lander's own shell; moving the lander's tier gates onto run-gate
# is the r7 follow-up bead.
ALLOWED = {
    ("skills/land-batch/bin/tier_gates.py", "scripts/cloudbuild-gate-remote.py"):
        "command text the lander's own shell runs after tier_gates exits, never under run-gate",
    ("scripts/git-hooks/run-gate", "bin"):
        "PATH entry for user tools (claude, codex wrapper); the export's bin/ (bd) comes first",
    ("scripts/git-hooks/install.sh", ""): "default repo argument: the repo it installs from",
    ("scripts/git-hooks/make-settings-patch.py", ""): "default repo argument: the repo it reads",
    ("scripts/git-hooks/make-settings-patch.py", "settings.json"): "the settings file it patches",
    ("scripts/git-hooks/make-settings-patch.py", "scripts/git-hooks/settings-live.patch"):
        "usage text for Shane",
    ("hooks/gmail-thread-guard.py", "bin/gmail-thread-digest.py"):
        "instruction text shown to the model, not run by the hook",
    ("hooks/voice-check.sh", "skills/draft/SKILL.md"): "instruction text shown to the model",
    ("scripts/git-hooks/reference-transaction", ""): "docstring prose about the checkout",
    ("scripts/review-gate.sh", ""): "message naming the repo install.sh takes",
    ("hooks/night-batch/night-batch.sh", "hooks/night-batch"):
        "logs/ and state/ root; sibling scripts run from SCRIPT_DIR",
    ("hooks/night-batch/sentry-triage.sh", "hooks/night-batch"):
        "logs/ and state/ root; sentry-triage.mjs runs from SCRIPT_DIR",
    ("hooks/night-batch/morning-common.sh", "hooks/night-batch"):
        "logs/ and state/ root; morning scripts source their siblings from SCRIPT_DIR",
    ("hooks/agentview-name-session.py", "hooks/.av-named"): "marker data dir",
    ("hooks/jev-draft-check/hook.mjs", "hooks/jev-draft-check"): "decisions.log and seen/ data dir",
    ("hooks/jev-stop-verify/hook.mjs", "hooks/jev-stop-verify"): "decisions.log and sessions/ data dir",
    ("hooks/jev-stop-verify/hook.mjs", ""): "path regex classifying edits, not a path it opens",
    ("skills/land-batch/bin/discover.sh", "land-ready.json"): "a worktree's own .claude marker",
}

# Text that names a gate file for a human or the model to run; the file itself never runs it.
# The only way a gate-file target may appear: anything else names the checkout's copy.
ALLOWED_TEXT = {
    ("hooks/fleet-role-sessionstart.sh", "scripts/fleet-role.py"):
        "the claim command shown to the model when Shane hands it the orchestrator role",
    ("scripts/review-gate.sh", "scripts/git-hooks/install.sh"):
        "message naming the install step when run-gate is missing (install.sh installs main's blobs)",
    ("scripts/git-hooks/make-settings-patch.py", "scripts/git-hooks/make-settings-patch.py"):
        "usage text for Shane",
    ("skills/land-batch/bin/tier_gates.py", "scripts/gated-test-run.py"):
        "command text the lander's own shell runs after tier_gates exits, never under run-gate",
    ("skills/land-batch/bin/tier_gates.py", "scripts/pg-lane-lock.py"):
        "command text the lander's own shell runs after tier_gates exits, never under run-gate",
}

# Env vars a gate reads whose value names a script or binary to run.
EXEC_ENV_RE = re.compile(r"_(?:SWEEP|ROLE|SCRIPT|BIN|CMD|EXE|CHECKER)$")
ENV_READ_RES = (re.compile(r"""environ\.get\(\s*["'](\w+)["']"""),
                re.compile(r"""environ\[\s*["'](\w+)["']\s*\]"""),
                re.compile(r"""getenv\(\s*["'](\w+)["']"""),
                re.compile(r"process\.env\.(\w+)"),
                re.compile(r"\$\{(\w+):?[-=]"),
                # (os.environ if env is None else env).get("X"), env["X"]: tier_gates.py:61
                re.compile(r"""\.get\(\s*["']([A-Z][A-Z0-9_]+)["']"""),
                re.compile(r"""(?:env|environ|environment)\[\s*["']([A-Z][A-Z0-9_]+)["']"""))
# Upper-case keys the wider patterns find that are not environment variables.
NOT_ENV = {"HEAD": "a dict key in discover.sh's inline python (ref name), not an env read"}

CLAUDE_RE = re.compile(r"(?<![\w.-])\.claude(?![\w-])")
TAIL_SLASH_RE = re.compile(r"/([\w.*{}$@/-]*)")
TAIL_PARTS_RE = re.compile(r"""["']((?:\s*[,/]\s*(?:["'][^"'\n]*["']|\w+\(\)))*)""")
PART_RE = re.compile(r"""["']([^"'\n]*)["']""")
COMMENT_RE = re.compile(r"^\s*(?:#|//|\*|/\*)")


def exported_code_files(root=REAL_ROOT):
    """Every committed gate file a gate run could execute: by extension or shebang, tests excluded."""
    hook = load_hook()
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, text=True)
    rels = []
    for rel in out.stdout.split("\0"):
        base = os.path.basename(rel)
        if (not rel or not hook.is_gate_path(rel) or "/markers/" in rel
                or "/tests/" in rel or "/test/" in rel or base.startswith("test_") or ".test." in base):
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        with open(path, "rb") as fh:
            first = fh.readline()
        if rel.endswith(CODE_EXT) or first.startswith(b"#!"):
            rels.append(rel)
    return rels


def claude_targets(line):
    """The path after each ~/.claude-style `.claude` in a line ("" for the root itself)."""
    line = line.replace("\\/", "/")
    found = []
    for m in CLAUDE_RE.finditer(line):
        rest = line[m.end():]
        if rest.startswith("/"):
            tail = TAIL_SLASH_RE.match(rest).group(1)
        else:
            parts = TAIL_PARTS_RE.match(rest)
            tail = "/".join(PART_RE.findall(parts.group(1))) if parts else ""
        if tail.count("}") > tail.count("{"):  # ${VAR:-$HOME/.claude/x}: the brace closes the default
            tail = tail[:tail.rfind("}")]
        found.append(tail.strip("/"))
    return found


def is_code_target(tail):
    return tail.endswith(CODE_EXT) or tail.startswith("bin/")


def is_data_target(tail):
    return (tail.split("/")[0] in DATA_ROOTS or bool(DATA_PART_RE.search(tail))
            or tail.endswith(DATA_EXT))


ASSIGN_RES = (re.compile(r"^\s*(?:local\s+|export\s+)?([A-Za-z_]\w*)=\S"),          # shell
              re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*(?!=)"),              # python
              re.compile(r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*="))               # js


def joined_code(var, text):
    """Lines that join `var` (a ~/.claude path) with a code file name."""
    ext = r"[\w.-]+(?:%s)\b" % "|".join(re.escape(e) for e in CODE_EXT)
    pat = re.compile(r"\$\{?%s\}?/%s|\b%s\s*/\s*[\"']%s|join\(\s*%s\s*,[^)]*[\"']%s"
                     % (var, ext, var, ext, var, ext))
    return [l.strip() for l in text.splitlines() if not COMMENT_RE.match(l) and pat.search(l)]


def boundary_problems(root=REAL_ROOT, allowed=ALLOWED, allowed_text=ALLOWED_TEXT):
    """(problems, the allow keys that matched)."""
    hook = load_hook()
    problems, used = [], set()
    for rel in exported_code_files(root):
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        roots = set()
        for line in text.splitlines():
            if COMMENT_RE.match(line):
                continue
            tails = claude_targets(line)
            for tail in tails:
                if tail.startswith(LAUNCHER):
                    continue
                if (rel, tail) in allowed_text:
                    used.add((rel, tail))
                    continue
                if is_code_target(tail) and hook.is_gate_path(tail):
                    problems.append(f"{rel}: names gate file {tail} in the checkout; resolve it "
                                    f"beside this file (Path(__file__).parent)")
                    continue
                if (rel, tail) in allowed:
                    used.add((rel, tail))
                elif is_code_target(tail) or not is_data_target(tail):
                    problems.append(f"{rel}: names ~/.claude/{tail} — add it to GATE_PATHS and "
                                    f"resolve it beside this file, or allow it with a reason")
                if tail and not is_code_target(tail) and not tail.startswith(LAUNCHER):
                    for r in ASSIGN_RES:
                        m = r.search(line)
                        if m:
                            roots.add(m.group(1))
        for var in sorted(roots):
            for hit in joined_code(var, text):
                problems.append(f"{rel}: runs code under the checkout path in {var}: {hit}")
    return problems, used


# D2: the sweep over the real branch
def test_d2_no_exported_gate_reaches_into_the_checkout():
    problems, used = boundary_problems()
    assert problems == []
    assert (set(ALLOWED) | set(ALLOWED_TEXT)) - used == set(), "stale allow entries"
    assert not [k for k in ALLOWED if is_code_target(k[1]) and load_hook().is_gate_path(k[1])]


def test_d2_sweep_covers_the_gates_that_escaped():
    rels = exported_code_files()
    for rel in ("scripts/gate-watch.py", "scripts/fleet-dispatch.py", "scripts/unlanded-sweep.py",
                "bin/bd", "scripts/git-hooks/run-gate", "hooks/night-batch/dispatch.sh",
                "skills/land-batch/bin/tier_gates.py"):
        assert rel in rels, rel


@pytest.mark.parametrize("line,tail", [
    ('SWEEP = os.path.join(HOME, ".claude/scripts/unlanded-sweep.py")', "scripts/unlanded-sweep.py"),
    ('f = os.path.expanduser("~/.claude/skills/land-batch/bin/land_batch_safety.py")',
     "skills/land-batch/bin/land_batch_safety.py"),
    ('AUTOBEAD="$HOME/.claude/hooks/night-batch/auto-bead.sh"', "hooks/night-batch/auto-bead.sh"),
    ('x = Path.home() / ".claude" / "scripts" / "fleet-slots.py"', "scripts/fleet-slots.py"),
    ('const H = join(homedir(), ".claude", "hooks", "x");', "hooks/x"),
    ('python3 /Users/shane/.claude/scripts/fleet-role.py', "scripts/fleet-role.py"),
    ('NOT = /\\/\\.claude\\/(?:evidence)/', ""),
    ('repo="${1:-$HOME/.claude}"', ""),
    ('OBS="${OBSERVER_DIR:-$HOME/.claude/observer}"', "observer"),
])
def test_d2_detector_extracts_targets(line, tail):
    assert claude_targets(line) == [tail]


def test_d2_detector_flags_each_escape_shape(tmp_path):
    hook = load_hook()
    bad = {
        "scripts/gate-watch.py": 'SWEEP = os.path.join(HOME, ".claude/scripts/unlanded-sweep.py")\n',
        "scripts/fleet-dispatch.py": 'S = os.path.expanduser("~/.claude/scripts/some-new-helper.py")\n',
        "hooks/night-batch/x.sh": '#!/bin/bash\nROOT="$HOME/.claude/hooks/night-batch"\n"$ROOT/dispatch.sh"\n',
        "hooks/y.mjs": 'const H = join(homedir(), ".claude", "hooks", "y");\nimport(join(H, "lib.mjs"));\n',
        "hooks/z.py": 'X = os.path.expanduser("~/.claude/state")\nrun(X / "evil.py")\n',
    }
    good = {
        "scripts/risk_tier.py": 'C = os.path.join(HOME, ".claude/state/gate-watch-cache.json")\n'
                                'R = os.path.join(HOME, ".claude/.git/live/run-gate")\n',
        "hooks/w.sh": '#!/bin/bash\nLOG="$HOME/.claude/hooks/w/logs/w.log"\n# ~/.claude/scripts/x.py\n',
    }
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in {**bad, **good}.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    problems, _ = boundary_problems(str(tmp_path), allowed={}, allowed_text={})
    for rel in bad:
        assert hook.is_gate_path(rel)
        assert any(p.startswith(rel + ":") for p in problems), (rel, problems)
    for rel in good:
        assert not any(p.startswith(rel + ":") for p in problems), (rel, problems)


def load_run_gate():
    loader = importlib.machinery.SourceFileLoader("p122j_run_gate", os.path.join(HOOKS_DIR, "run-gate"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# E1 lint: every variable an exported gate reads is a decision, passed or dropped
ENV_NAME_RE = re.compile(r"[A-Z_]*[A-Z][A-Z0-9_]*")  # env style; lowercase is a shell local


def env_reads(root=REAL_ROOT):
    names = {}
    for rel in exported_code_files(root):
        if os.path.basename(rel).startswith("test"):
            continue  # a test's own fakes, never run by a gate
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for r in ENV_READ_RES:
            for name in r.findall(text):
                if ENV_NAME_RE.fullmatch(name) and name not in NOT_ENV:
                    names.setdefault(name, set()).add(rel)
    return names


def test_e1_lint_every_env_var_a_gate_reads_is_allowed_or_dropped():
    rg = load_run_gate()
    names = env_reads()
    assert {"PGLITE_ENTRY", "LAND_BATCH_PGLITE_TARBALL", "LAND_BATCH_REF_SNAPSHOT",
            "LAND_GATE_REMOTE"} <= set(names)
    undecided = {n: sorted(r) for n, r in names.items()
                 if not rg.env_allowed(n) and n not in rg.ENV_DROPPED}
    assert undecided == {}


def test_e1_lint_no_dropped_or_code_naming_var_is_allowed():
    rg = load_run_gate()
    assert not [n for n in rg.ENV_DROPPED if rg.env_allowed(n)]
    code_like = [n for n in rg.ENV_ALLOW
                 if EXEC_ENV_RE.search(n) or re.search(r"_(?:ENTRY|TARBALL|LIB_DIR|OPTIONS)$", n)
                 or n.startswith(("PYTHON", "BASH_FUNC_", "NODE_", "LD_", "DYLD_"))
                 and n != "NODE_EXTRA_CA_CERTS"]
    assert code_like == []
    assert not any(rg.env_allowed(n) for n in ("PATH", "PYTHONPATH", "BASH_ENV", "NODE_OPTIONS",
                                               "BASH_FUNC_node%%", "DYLD_INSERT_LIBRARIES",
                                               "GIT_EXEC_PATH", "SOME_FUTURE_ENTRY"))


# D1: the r6 repro — an uncommitted edit to the checkout's sweep must not run
SWEEP_STUB = textwrap.dedent("""\
    import json, sys
    open({log!r}, "a").write("{who}\\n")
    print(json.dumps({{"candidates": []}}))
""")


def sweep_repo(tmp_path):
    repo = Repo(tmp_path)
    log = tmp_path / "sweep.log"
    commit_files(repo, {"scripts/gate-watch.py": (os.path.join(REAL_ROOT, "scripts/gate-watch.py"),),
                        "scripts/unlanded-sweep.py": SWEEP_STUB.format(log=str(log), who="committed")})
    repo.write("scripts/unlanded-sweep.py", SWEEP_STUB.format(log=str(log), who="CHECKOUT"))  # uncommitted
    home = tmp_path / "home"
    home.mkdir()
    os.symlink(repo.root, home / ".claude")
    env = dict(repo.env, HOME=str(home), GATE_LEDGER=str(tmp_path / "L.jsonl"),
               GATE_WATCH_CACHE=str(tmp_path / "cache.json"))
    return repo, log, env


def test_d1_gate_watch_refresh_runs_the_committed_sweep(tmp_path):
    repo, log, env = sweep_repo(tmp_path)
    res = repo.gate("--fail-exit", "2", "scripts/gate-watch.py", "--refresh", env=env)
    assert res.returncode == 0, res.stderr
    assert log.read_text() == "committed\n"
    assert json.loads((tmp_path / "cache.json").read_text())["sweep_ok"] is True


def test_d1_env_override_cannot_name_the_sweep_under_run_gate(tmp_path):
    repo, log, env = sweep_repo(tmp_path)
    env["GATE_WATCH_SWEEP"] = str(repo.root / "scripts/unlanded-sweep.py")  # the checkout copy
    res = repo.gate("--fail-exit", "2", "scripts/gate-watch.py", "--refresh", env=env)
    assert res.returncode == 0, res.stderr
    assert log.read_text() == "committed\n"


def test_d1_fleet_dispatch_loads_the_sweep_beside_it(tmp_path):
    dest = tmp_path / "export/scripts"
    dest.mkdir(parents=True)
    src = os.path.join(REAL_ROOT, "scripts/fleet-dispatch.py")
    (dest / "fleet-dispatch.py").write_text(open(src, encoding="utf-8").read())
    (dest / "unlanded-sweep.py").write_text("WHO = 'beside'\n")
    loader = importlib.machinery.SourceFileLoader("p122j_fleet_dispatch", str(dest / "fleet-dispatch.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod._load_unlanded_sweep().WHO == "beside"


def test_d1_sweep_loads_land_batch_safety_beside_it(tmp_path):
    root = tmp_path / "export"
    (root / "scripts").mkdir(parents=True)
    (root / "skills/land-batch/bin").mkdir(parents=True)
    (root / "scripts/unlanded-sweep.py").write_text(
        open(os.path.join(REAL_ROOT, "scripts/unlanded-sweep.py"), encoding="utf-8").read())
    (root / "skills/land-batch/bin/land_batch_safety.py").write_text("WHO = 'beside'\n")
    loader = importlib.machinery.SourceFileLoader("p122j_sweep", str(root / "scripts/unlanded-sweep.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    assert mod._load_safety().WHO == "beside"


@pytest.mark.parametrize("rel", ["scripts/unlanded-sweep.py", "scripts/fleet-slots.py",
                                 "scripts/gated-test-run.py", "scripts/pg-lane-lock.py"])
def test_d1_helpers_exported_gates_run_are_gate_paths(tmp_path, rel):
    repo = Repo(tmp_path)
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(rel, "print('x')\n"), repo, before, what=rel)


def load_from(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def copy_into(root, rel):
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(open(os.path.join(REAL_ROOT, rel), encoding="utf-8").read())
    return dest


def test_d1_fleet_dispatch_runs_the_slot_manager_beside_it(tmp_path):
    # every slot call is sh(f"python3 {SLOTS} ...")
    mod = load_from(copy_into(tmp_path, "scripts/fleet-dispatch.py"), "p122j_fd_slots")
    assert mod.SLOTS == str(tmp_path / "scripts/fleet-slots.py")


def test_d1_ratchet_runs_the_test_runner_beside_its_skill(tmp_path):
    mod = load_from(copy_into(tmp_path, "skills/land-batch/bin/file_length_ratchet.py"), "p122j_ratchet")
    assert mod.DEFAULT_COMMAND[1] == str((tmp_path / "scripts/gated-test-run.py").resolve())


def test_d1_ratchet_through_run_gate_ignores_a_checkout_runner_edit(tmp_path):
    repo = Repo(tmp_path)
    log = tmp_path / "runner.log"
    runner = "import sys\nopen(%r, 'a').write('{who}\\n')\n" % str(log)
    probe = ("import importlib.util, os, subprocess, sys\n"
             "here = os.path.dirname(os.path.abspath(__file__))\n"
             "spec = importlib.util.spec_from_file_location('r', os.path.join(here, 'file_length_ratchet.py'))\n"
             "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
             "sys.exit(subprocess.run([sys.executable, m.DEFAULT_COMMAND[1]]).returncode)\n")
    commit_files(repo, {
        "skills/land-batch/bin/file_length_ratchet.py":
            (os.path.join(REAL_ROOT, "skills/land-batch/bin/file_length_ratchet.py"),),
        "skills/land-batch/bin/ratchet_probe.py": probe,
        "scripts/gated-test-run.py": runner.format(who="committed")})
    repo.write("scripts/gated-test-run.py", runner.format(who="CHECKOUT"))  # uncommitted
    home = tmp_path / "home"
    home.mkdir()
    os.symlink(repo.root, home / ".claude")
    res = repo.gate("skills/land-batch/bin/ratchet_probe.py", env=dict(repo.env, HOME=str(home)))
    assert res.returncode == 0, res.stderr
    assert log.read_text() == "committed\n"
