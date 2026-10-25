"""run-gate's environment and tools are fixed before anything runs (LUCY-p122j layer-B r8).

E1: the gate sees only allowlisted caller variables (the r7 repro: PGLITE_ENTRY naming a
checkout module must not reach pglite-proof.cjs). E2: a git or python3 first on the caller's
PATH never runs, not even during verification. E3: sentry-triage.mjs hands claude the MCP
config beside itself. E4: morning-report.sh --archive-only works from the read-only export.
Run: ~/.claude/skills/land-batch/.venv/bin/python3 -m pytest scripts/git-hooks/ -q
"""
import json
import os
import subprocess
import sys
import textwrap

from test_reference_transaction import REAL_ROOT, Repo, commit_files

ENV_PROBE = "references/review-lead/env_probe.py"
ENV_PROBE_SOURCE = "import json, os\nprint(json.dumps(dict(os.environ)))\n"
DROPPED = {"PGLITE_ENTRY": "/x.cjs", "LAND_BATCH_PGLITE_TARBALL": "/x.tgz",
           "JAVA_TOOL_OPTIONS": "-javaagent:/x", "SOME_FUTURE_ENTRY": "/x",
           "GIT_EXEC_PATH": "/x", "LD_PRELOAD": "/x", "DYLD_INSERT_LIBRARIES": "/x"}
KEPT = {"LAND_BATCH_REF_SNAPSHOT": "cached-remote-tracking", "HTTPS_PROXY": "http://p:1",
        "LC_ALL": "en_GB.UTF-8", "CLAUDE_CODE_SESSION_ID": "s", "GIT_SSH_COMMAND": "ssh",
        "BEADS_DIR": "/b", "FLEET_TOTAL_CAP": "3", "GIT_CONFIG_KEY_0": "k"}


def probe_env(repo, extra):
    commit_files(repo, {ENV_PROBE: ENV_PROBE_SOURCE})
    res = repo.gate(ENV_PROBE, env=dict(repo.env, **extra))
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


# E1: allowlist, not denylist
def test_e1_gate_sees_only_allowlisted_variables(tmp_path):
    repo = Repo(tmp_path)
    seen = probe_env(repo, {**DROPPED, **KEPT})
    assert [k for k in DROPPED if k in seen] == []
    assert {k: seen.get(k) for k in KEPT} == KEPT


def test_e1_r7_repro_pglite_entry_cannot_name_checkout_code(tmp_path):
    repo = Repo(tmp_path)
    marker = tmp_path / "pglite.marker"
    commit_files(repo, {"skills/land-batch/bin/pglite-proof.cjs":
                        (os.path.join(REAL_ROOT, "skills/land-batch/bin/pglite-proof.cjs"),)})
    evil = repo.root / "skills/land-batch/bin/evil-pglite.cjs"  # uncommitted checkout module
    evil.write_text("require('fs').writeFileSync(%s, 'ran');\n"
                    "module.exports = { PGlite: class {} };\n" % json.dumps(str(marker)))
    (tmp_path / "req.json").write_text("{}")
    res = repo.gate("--fail-exit", "2", "skills/land-batch/bin/pglite-proof.cjs",
                    str(tmp_path / "req.json"), str(tmp_path / "resp.json"),
                    env=dict(repo.env, PGLITE_ENTRY=str(evil)))
    assert not marker.exists()
    assert res.returncode != 0 and "usage: PGLITE_ENTRY" in res.stderr


def fake_tools(tmp_path):
    fake = tmp_path / "fakebin"
    fake.mkdir()
    log = tmp_path / "fake.log"
    for tool, real in (("git", "/usr/bin/git"), ("python3", "/opt/homebrew/bin/python3")):
        (fake / tool).write_text('#!/bin/sh\necho %s >> %s\nexec %s "$@"\n' % (tool, log, real))
        (fake / tool).chmod(0o755)
    return fake, log


# E2: the caller's PATH selects nothing, verification included
def test_e2_caller_path_git_and_python3_never_run(tmp_path):
    repo = Repo(tmp_path)
    commit_files(repo, {ENV_PROBE: ENV_PROBE_SOURCE})
    fake, log = fake_tools(tmp_path)
    res = repo.gate(ENV_PROBE, env=dict(repo.env, PATH=str(fake) + ":" + repo.env["PATH"]))
    assert res.returncode == 0, res.stderr
    assert not log.exists(), log.read_text()


def test_e2_without_the_sh_line_verification_git_still_ignores_caller_path(tmp_path):
    # `<python> run-gate` skips the /bin/sh line: the env reset before load_hook is what holds
    repo = Repo(tmp_path)
    commit_files(repo, {ENV_PROBE: ENV_PROBE_SOURCE})
    fake, log = fake_tools(tmp_path)
    res = subprocess.run(["/opt/homebrew/bin/python3", repo.run_gate, ENV_PROBE], cwd=repo.root,
                         env=dict(repo.env, PATH=str(fake) + ":" + repo.env["PATH"]),
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert not log.exists(), log.read_text()


def test_e2_started_by_another_python_reexecs_into_the_fixed_one(tmp_path):
    repo = Repo(tmp_path)
    commit_files(repo, {ENV_PROBE: "import sys\nprint(sys.executable)\n"})
    # already isolated (-I -S): only the interpreter check makes it re-exec
    res = subprocess.run(["/usr/bin/python3", "-I", "-S", repo.run_gate, ENV_PROBE], cwd=repo.root,
                         env=repo.env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert os.path.realpath(res.stdout.strip()) == os.path.realpath("/opt/homebrew/bin/python3")


# E3: the MCP config (its command is code) comes from beside the runner
PRELOAD = textwrap.dedent("""\
    import cp from 'node:child_process';
    import fs from 'node:fs';
    import { syncBuiltinESMExports } from 'node:module';
    cp.spawnSync = (bin, args) => {
      fs.appendFileSync(process.env.SPAWN_LOG, JSON.stringify([bin, ...(args || [])]) + '\\n');
      return { status: 1, stdout: '', stderr: 'stub' };
    };
    syncBuiltinESMExports();
""")


def test_e3_sentry_triage_uses_the_mcp_config_beside_it(tmp_path):
    export = tmp_path / "export/hooks/night-batch"
    export.mkdir(parents=True)
    src = os.path.join(REAL_ROOT, "hooks/night-batch/sentry-triage.mjs")
    (export / "sentry-triage.mjs").write_text(open(src, encoding="utf-8").read())
    (tmp_path / "preload.mjs").write_text(PRELOAD)
    log = tmp_path / "spawn.log"
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "SPAWN_LOG": str(log),
           "STATE_FILE": str(tmp_path / "state.json"), "SENTRY_TRIAGE_FORCE": "1"}
    subprocess.run(["node", "--import", str(tmp_path / "preload.mjs"), str(export / "sentry-triage.mjs")],
                   env=env, capture_output=True, text=True, timeout=60)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    mcp = [c[c.index("--mcp-config") + 1] for c in calls if "--mcp-config" in c]
    assert mcp == [str(export / "sentry-triage-mcp.json")]


# E4: the archive step keeps its state and logs in the writable runtime dir
def test_e4_morning_report_archive_only_runs_from_the_export(tmp_path):
    repo = Repo(tmp_path)
    nb = "hooks/night-batch/"
    commit_files(repo, {nb + f: (os.path.join(REAL_ROOT, nb + f),)
                        for f in ("morning-report.sh", "morning-common.sh")})
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    env = dict(repo.env, HOME=str(home), NIGHT_BATCH_TODO=str(work / "TODO.md"),
               NIGHT_BATCH_DREAM_ROOT=str(work / "dreams"),
               NIGHT_BATCH_WORKTREES=str(work / "worktrees"))
    env.pop("NIGHT_BATCH_ROOT", None)
    res = repo.gate(nb + "morning-report.sh", "--archive-only", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "archive-only complete" in res.stdout
    runtime = home / ".claude/hooks/night-batch"
    assert (runtime / "logs/morning-report.log").is_file()
    assert (runtime / "state").is_dir()


# E1 own-gate r1: a lander variable a gate needs still crosses (LAND_GATE_REMOTE, SKILL.md:727)
REMOTE_PROBE = "skills/land-batch/bin/remote_probe.py"
REMOTE_PROBE_SOURCE = textwrap.dedent("""\
    import importlib.util, json, os
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("tg", os.path.join(here, "tier_gates.py"))
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    print(json.dumps(tg.plan(["scripts/migrate.mjs"]).get("cloudbuild_remote", False)))
""")


def test_e1_land_gate_remote_reaches_tier_gates_both_ways(tmp_path):
    repo = Repo(tmp_path)
    commit_files(repo, {"skills/land-batch/bin/tier_gates.py":
                        (os.path.join(REAL_ROOT, "skills/land-batch/bin/tier_gates.py"),),
                        "scripts/risk_tier.py": (os.path.join(REAL_ROOT, "scripts/risk_tier.py"),),
                        REMOTE_PROBE: REMOTE_PROBE_SOURCE})
    seen = {}
    for value in ("1", "0"):
        res = repo.gate(REMOTE_PROBE, env=dict(repo.env, LAND_GATE_REMOTE=value))
        assert res.returncode == 0, res.stderr
        seen[value] = json.loads(res.stdout)
    assert seen == {"1": True, "0": False}


# r8 independent P2: a pg-lane-lock holder whose child runs a gate that takes the same lock
# re-enters it. Dropping PG_LANE_LOCK_TOKEN made the nested lock wait on its own holder forever.
def test_e1_nested_pg_lane_lock_reenters_through_run_gate(tmp_path):
    repo = Repo(tmp_path)
    lock = "scripts/pg-lane-lock.py"
    commit_files(repo, {lock: (os.path.join(REAL_ROOT, lock),)})
    env = dict(repo.env, PG_LANE_LOCK_DIR=str(tmp_path / "fleet"))
    env.pop("PG_LANE_LOCK_TOKEN", None)
    res = subprocess.run([sys.executable, "-I", str(repo.root / lock), "--",
                          repo.run_gate, "--fail-exit", "2", lock, "--", "true"],
                         cwd=repo.root, env=env, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    assert "running nested lane directly" in res.stderr
