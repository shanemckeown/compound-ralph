"""Tests for scripts/git-hooks/reference-transaction (LUCY-wqn96).

Each test builds a throwaway repo, installs the hook with install.sh (the exact config
the orchestrator will use), and commits a stub scripts/fleet-role.py to main: the hook
only ever runs the checker committed on main, never one from the working tree.
Run: ~/.claude/skills/land-batch/.venv/bin/python3 -m pytest scripts/git-hooks/ -q
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import textwrap
import time

import pytest

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HOOKS_DIR, "reference-transaction")
INSTALL = os.path.join(HOOKS_DIR, "install.sh")
GATE_FILE = "references/review-lead/x.py"
CHECKER = "scripts/fleet-role.py"
# A gate script that imports a sibling, for the run-gate tests.
PROBE = "references/review-lead/probe.py"
PROBE_SIBLING = "references/review-lead/probe_sib.py"
PROBE_SOURCE = textwrap.dedent("""\
    import os, sys
    from probe_sib import WHO
    print(WHO, sys.argv[1:], os.getcwd(), sys.flags.isolated)
""")
AGENT_ID = "11111111-2222-3333-4444-555555555555"
AGENT_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def load_hook():
    loader = importlib.machinery.SourceFileLoader("reference_transaction", HOOK)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def stub_source(role, seen):
    body = {
        "orchestrator": "print('ORCHESTRATOR'); sys.exit(0)",
        "claude-orchestrator": f"print('ORCHESTRATOR' if sys.argv[1:] == [{AGENT_ID!r}] else 'SUB');"
                               f" sys.exit(0 if sys.argv[1:] == [{AGENT_ID!r}] else 1)",
        "silent-zero": "sys.exit(0)",
        "slow-orchestrator": "time.sleep(6); print('ORCHESTRATOR'); sys.exit(0)",
        "sub": "print('SUB'); sys.exit(1)",
        "error": "sys.exit(2)",
        "hang": "time.sleep(120)",
    }[role]
    return textwrap.dedent(f"""\
        import sys, time
        open({str(seen)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')
        {body}
    """)


class Repo:
    def __init__(self, tmp_path, role="sub"):
        self.tmp = tmp_path
        self.root = tmp_path / "repo"
        self.seen = tmp_path / "fleet-role-seen.txt"
        self.env = {k: v for k, v in os.environ.items()
                    if k not in AGENT_VARS and k not in ("FLEET_ROLE", "FLEET_ROLE_BIN")
                    and not k.startswith("GIT_") and k != "WQN96_TEST_TIMEOUT_S"}
        self.env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        # The baseline commit (made as a human, before any hook) carries the hook files:
        # install.sh installs main's committed blobs, never the working tree.
        self.write(GATE_FILE, "v0\n")
        self.write("commands/closeout.md", "v0\n")
        self.write(CHECKER, stub_source(role, self.seen))
        self.write(PROBE, PROBE_SOURCE)
        self.write(PROBE_SIBLING, "WHO = 'committed'\n")
        for name in ("reference-transaction", "run-gate"):
            dest = self.root / "scripts/git-hooks" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(os.path.join(HOOKS_DIR, name), dest)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        res = self.install()
        assert res.returncode == 0, res.stderr
        self.install_output = res.stdout
        self.common = self.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
        self.live = os.path.join(self.common, "live")
        self.run_gate = os.path.join(self.live, "run-gate")

    def install(self, env=None):
        return subprocess.run(["bash", INSTALL, str(self.root)], env=env or self.env,
                              capture_output=True, text=True)

    def gate(self, *args, cwd=None, env=None):
        return subprocess.run([self.run_gate, *args], cwd=cwd or self.root, env=env or self.env,
                              capture_output=True, text=True)

    def current(self):
        return os.path.basename(os.readlink(os.path.join(self.live, "current")))

    def human_env(self):
        return {k: v for k, v in self.env.items() if k not in AGENT_VARS}

    def set_role(self, role):
        """Commit a new checker stub to main as a human (the hook trusts main's blob only)."""
        self.write(CHECKER, stub_source(role, self.seen))
        self.git("add", CHECKER, env=self.human_env())
        self.git("commit", "-q", "-m", f"role {role}", env=self.human_env())

    def as_agent(self, key="CLAUDE_CODE_SESSION_ID", value=AGENT_ID):
        self.env[key] = value

    def git(self, *args, cwd=None, check=True, env=None):
        res = subprocess.run(["git", *args], cwd=cwd or self.root, env=env or self.env,
                             capture_output=True, text=True)
        if check and res.returncode != 0:
            raise AssertionError(f"git {args} failed: {res.stderr}")
        return res

    def write(self, path, text, root=None):
        p = (root or self.root) / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def rev(self, ref):
        return self.git("rev-parse", ref).stdout.strip()

    def commit(self, path, text, *extra, cwd=None):
        self.write(path, text, root=cwd)
        self.git("add", path, cwd=cwd)
        return self.git("commit", "-q", "-m", f"edit {path}", *extra, cwd=cwd, check=False)

    def branch_with_gate_change(self, name="e3", path=GATE_FILE, text="branch\n"):
        """A branch one commit ahead of main that edits `path` (made on the branch, so allowed)."""
        self.git("checkout", "-q", "-b", name)
        res = self.commit(path, text)
        assert res.returncode == 0, res.stderr
        tip = self.rev(name)
        self.git("checkout", "-q", "main")
        return tip

    def branch_with_docs_change(self, name="docs"):
        return self.branch_with_gate_change(name, "commands/closeout.md", "docs\n")


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def assert_refused(res, repo, main_before, what=GATE_FILE):
    assert res.returncode != 0
    assert "REFUSED (LUCY-wqn96)" in res.stderr
    assert what in res.stderr
    assert repo.rev("main") == main_before


# (a) SUB agent commit on main touching a gate path
def test_a_sub_commit_on_main_refused(repo):
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)
    assert AGENT_ID in repo.seen.read_text()


# (b) --no-verify does not skip the reference-transaction hook
def test_b_no_verify_commit_refused(repo):
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n", "--no-verify"), repo, before)


# (c) the same change on a branch is allowed
def test_c_branch_commit_allowed(repo):
    repo.as_agent()
    before = repo.rev("main")
    repo.git("checkout", "-q", "-b", "e3")
    res = repo.commit(GATE_FILE, "branch\n")
    assert res.returncode == 0, res.stderr
    assert repo.rev("main") == before
    assert repo.rev("e3") != before


# (d) fast-forward merge of that branch into main
def test_d_ff_merge_refused(repo):
    repo.as_agent()
    repo.branch_with_gate_change()
    before = repo.rev("main")
    res = repo.git("merge", "--ff-only", "e3", check=False)
    assert_refused(res, repo, before)


# (e) update-ref, with and without an expected old value
@pytest.mark.parametrize("with_old", [False, True])
def test_e_update_ref_refused(repo, with_old):
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    before = repo.rev("main")
    args = ["update-ref", "refs/heads/main", tip] + ([before] if with_old else [])
    assert_refused(repo.git(*args, check=False), repo, before)


def test_update_ref_without_old_non_gate_allowed(repo):
    """A zero old-OID is diffed against main's current tip, not the whole tree."""
    repo.as_agent()
    tip = repo.branch_with_docs_change()
    res = repo.git("update-ref", "refs/heads/main", tip, check=False)
    assert res.returncode == 0, res.stderr
    assert repo.rev("main") == tip


# (e') other routes: reset and cherry-pick (separate repos: a refused reset --hard
# still rewrites the working tree, which would block the cherry-pick)
@pytest.mark.parametrize("route", [["reset", "-q", "--hard"], ["reset", "-q", "--soft"], ["cherry-pick"]])
def test_reset_and_cherry_pick_refused(repo, route):
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    before = repo.rev("main")
    assert_refused(repo.git(*route, tip, check=False), repo, before)


# (f) the orchestrator may merge
def test_f_orchestrator_merge_allowed(repo):
    repo.set_role("orchestrator")
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    res = repo.git("merge", "--ff-only", "e3", check=False)
    assert res.returncode == 0, res.stderr
    assert repo.rev("main") == tip


# (g) a non-gate path on main by a SUB is allowed
def test_g_non_gate_path_allowed(repo):
    repo.as_agent()
    res = repo.commit("commands/closeout.md", "fine\n")
    assert res.returncode == 0, res.stderr
    assert not repo.seen.exists()  # identity is only checked when a gate path moves


# (h) the checker erroring, hanging, or exiting 0 without saying ORCHESTRATOR fails closed
@pytest.mark.parametrize("role", ["error", "hang", "silent-zero"])
def test_h_fleet_role_failure_refused(repo, role):
    repo.set_role(role)
    repo.as_agent()
    repo.env["WQN96_TEST_TIMEOUT_S"] = "2"
    before = repo.rev("main")
    started = time.monotonic()
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)
    assert time.monotonic() - started < 30


def test_h_missing_checker_blob_refused(repo):
    repo.git("rm", "-q", CHECKER, env=repo.human_env())
    repo.git("commit", "-q", "-m", "no checker", env=repo.human_env())
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


# (i) no agent env = a human terminal = allowed
def test_i_human_allowed(repo):
    res = repo.commit(GATE_FILE, "shane\n")
    assert res.returncode == 0, res.stderr
    assert not repo.seen.exists()


# (j) commit from a linked worktree whose HEAD is main
def test_j_linked_worktree_on_main_refused(repo, tmp_path):
    repo.as_agent()
    repo.git("checkout", "-q", "-b", "other")
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", str(wt), "main")
    before = repo.rev("main")
    res = repo.commit(GATE_FILE, "bad\n", cwd=wt)
    assert_refused(res, repo, before)


# (k) a MANAGER claim is not the orchestrator: the plain check says SUB
def test_k_manager_is_not_orchestrator(repo):
    repo.as_agent()  # the base stub answers SUB, as fleet-role.py does for a MANAGER
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


# R1: the incoming change cannot authorise itself by rewriting the checker
@pytest.mark.parametrize("replacement", ["", "import sys; print('ORCHESTRATOR'); sys.exit(0)\n"])
def test_r1_merge_replacing_checker_refused(repo, replacement):
    repo.as_agent()
    repo.git("checkout", "-q", "-b", "e3")
    repo.write(CHECKER, replacement)
    repo.write(GATE_FILE, "branch\n")
    repo.git("add", CHECKER, GATE_FILE)
    repo.git("commit", "-q", "-m", "self-authorise")
    repo.git("checkout", "-q", "main")
    before = repo.rev("main")
    assert_refused(repo.git("merge", "--ff-only", "e3", check=False), repo, before)


def test_r1_working_tree_checker_not_trusted(repo):
    repo.as_agent()
    repo.write(CHECKER, "import sys; print('ORCHESTRATOR'); sys.exit(0)\n")  # uncommitted
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_r1_fleet_role_bin_override_ignored(repo, tmp_path):
    stub = tmp_path / "orch.py"
    stub.write_text("import sys; print('ORCHESTRATOR'); sys.exit(0)\n")
    repo.as_agent()
    repo.env["FLEET_ROLE_BIN"] = str(stub)
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


# R2: the install config covers linked worktrees and local pushes
def test_r2_install_sets_absolute_common_hooks_dir(repo):
    common = repo.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    hooks_path = repo.git("config", "--get", "core.hooksPath").stdout.strip()
    assert hooks_path == os.path.join(common, "hooks")
    installed = os.path.join(hooks_path, "reference-transaction")
    assert os.path.isfile(installed) and not os.path.islink(installed)
    assert os.access(installed, os.X_OK)
    assert hooks_path in repo.install_output


def test_r2_update_ref_from_linked_worktree_refused(repo, tmp_path):
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", str(wt), "e3")
    before = repo.rev("main")
    res = repo.git("update-ref", "refs/heads/main", tip, cwd=wt, check=False)
    assert_refused(res, repo, before)


def test_r2_local_push_refused(repo):
    repo.as_agent()
    repo.branch_with_gate_change()
    repo.git("checkout", "-q", "e3")  # main not checked out, so receive-pack may update it
    before = repo.rev("main")
    res = repo.git("push", ".", "e3:main", check=False)
    assert res.returncode != 0 and "REFUSED (LUCY-wqn96)" in res.stderr
    assert repo.rev("main") == before


# R3: deleting or creating main needs the orchestrator, whatever the paths
def test_r3_delete_main_refused(repo):
    repo.as_agent()
    before = repo.rev("main")
    res = repo.git("update-ref", "-d", "refs/heads/main", check=False)
    assert_refused(res, repo, before, what="(deletes main)")


def test_r3_recreate_main_at_gateless_tip_refused(repo):
    tip = repo.branch_with_docs_change()
    repo.git("checkout", "-q", "docs")
    repo.git("update-ref", "-d", "refs/heads/main", env=repo.human_env())
    repo.as_agent()
    res = repo.git("update-ref", "refs/heads/main", tip, check=False)
    assert res.returncode != 0 and "(creates main)" in res.stderr
    assert repo.git("rev-parse", "--verify", "-q", "refs/heads/main", check=False).returncode != 0


def test_r3_orchestrator_may_delete_main(repo):
    repo.set_role("orchestrator")
    repo.git("checkout", "-q", "-b", "other")
    repo.as_agent()
    res = repo.git("update-ref", "-d", "refs/heads/main", check=False)
    assert res.returncode == 0, res.stderr


def test_r3_branch_rename_away_from_main_refused(repo):
    repo.as_agent()
    before = repo.rev("main")
    res = repo.git("branch", "-M", "main", "old-main", check=False)
    assert res.returncode != 0
    assert repo.rev("main") == before


# R4: a set-but-junk id is not a human and never reaches the checker as a flag
@pytest.mark.parametrize("bad_id", ["", " ", "--who", "--release", "x\ny", "--------", "not-a-uuid!"])
def test_r4_malformed_id_refused(repo, bad_id):
    repo.set_role("orchestrator")  # even a yes-checker must not be asked
    repo.as_agent(value=bad_id)
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)
    assert not repo.seen.exists()


def test_r4_codex_thread_id_shape_accepted():
    hook = load_hook()
    for good in (AGENT_ID, "01a16330-4837-7d91-b39d-d8fb744e887c"):
        assert hook.AGENT_ID_RE.fullmatch(good)


# R5/R9: the outer timeout outlasts fleet-role's probes (three 15 s Codex budgets), and
# tests may only lower it
def test_r5_slow_orchestrator_allowed(repo):
    repo.set_role("slow-orchestrator")
    repo.as_agent()
    res = repo.commit(GATE_FILE, "ok\n")
    assert res.returncode == 0, res.stderr


def test_r5_timeout_bounds(monkeypatch):
    hook = load_hook()
    assert hook.TIMEOUT_MAX_S == 60
    for value, want in (("2", 2.0), ("100", 60.0), ("0", 60.0), ("-1", 60.0), ("junk", 60.0)):
        monkeypatch.setenv("WQN96_TEST_TIMEOUT_S", value)
        assert hook.timeout_s() == want, value
    monkeypatch.delenv("WQN96_TEST_TIMEOUT_S")
    assert hook.timeout_s() == 60.0


# R6: replacement objects cannot hide the real diff
def test_r6_replace_objects_ignored(repo):
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    before = repo.rev("main")
    repo.git("replace", before, tip)
    res = repo.git("update-ref", "refs/heads/main", tip, check=False)
    assert res.returncode != 0 and "REFUSED (LUCY-wqn96)" in res.stderr
    assert repo.git("--no-replace-objects", "rev-parse", "main").stdout.strip() == before


# R7: the committed checker can import its committed sibling (python3 -E -s, not -I)
def test_r7_checker_imports_committed_sibling(repo):
    sibling = "scripts/fleet_codex_rpc.py"
    repo.write(sibling, "ROLE = 'ORCHESTRATOR'\n")
    repo.write(CHECKER, textwrap.dedent(f"""\
        import sys
        open({str(repo.seen)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')
        from fleet_codex_rpc import ROLE
        print(ROLE)
        sys.exit(0 if ROLE == 'ORCHESTRATOR' else 1)
    """))
    repo.git("add", CHECKER, sibling, env=repo.human_env())
    repo.git("commit", "-q", "-m", "checker with sibling", env=repo.human_env())
    # An uncommitted SUB sibling in the working tree must not be the one imported.
    repo.write(sibling, "ROLE = 'SUB'\n")
    repo.as_agent()
    res = repo.commit(GATE_FILE, "ok\n")
    assert res.returncode == 0, res.stderr


# R7 clarify: the checker comes from LOCAL main's old value, never origin/main
def test_r7_permissive_origin_main_checker_ignored(repo):
    repo.git("checkout", "-q", "-b", "permissive")
    repo.write(CHECKER, stub_source("orchestrator", repo.seen))
    repo.git("add", CHECKER)
    repo.git("commit", "-q", "-m", "permissive")
    repo.git("update-ref", "refs/remotes/origin/main", repo.rev("permissive"))
    repo.git("checkout", "-q", "main")
    repo.git("branch", "-q", "-D", "permissive")
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


# Live recurrence 24 Oct (LUCY-nd0r5, b0da2da): exactly that path set, committed on main
def test_nd0r5_land_batch_commit_on_main_refused(repo):
    paths = ("skills/land-batch/SKILL.md", "skills/land-batch/bin/tier_gates.py",
             "skills/land-batch/tests/test_tier_gates.py")
    repo.as_agent()
    before = repo.rev("main")
    for path in paths:
        repo.write(path, "unreviewed\n")
    repo.git("add", *paths)
    res = repo.git("commit", "-q", "-m", "nd0r5", check=False)
    assert_refused(res, repo, before, what="skills/land-batch/bin/tier_gates.py")
    for path in paths:
        assert path in res.stderr


def test_codex_thread_id_is_an_agent(repo):
    repo.as_agent("CODEX_THREAD_ID")
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_identity_is_bare_session_id(repo):
    """main's committed fleet-role.py takes only the id (an extra flag exits 2)."""
    repo.as_agent()
    repo.commit(GATE_FILE, "bad\n")
    assert repo.seen.read_text().split() == [AGENT_ID]


def test_update_ref_case_variant_of_main_refused(repo):
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    before = repo.rev("main")
    res = repo.git("update-ref", "refs/heads/MAIN", tip, check=False)
    assert res.returncode != 0 and "REFUSED (LUCY-wqn96)" in res.stderr
    assert repo.rev("main") == before


def test_codex_worker_inheriting_orchestrator_claude_id_refused(repo):
    """A Codex worker spawned from the Claude orchestrator carries both ids."""
    repo.set_role("claude-orchestrator")
    repo.as_agent("CLAUDE_CODE_SESSION_ID")
    repo.as_agent("CODEX_THREAD_ID", "22222222-3333-4444-5555-666666666666")
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_claude_orchestrator_alone_allowed(repo):
    repo.set_role("claude-orchestrator")
    repo.as_agent("CLAUDE_CODE_SESSION_ID")
    assert repo.commit(GATE_FILE, "ok\n").returncode == 0


def test_non_ascii_gate_path_refused(repo):
    repo.as_agent()
    before = repo.rev("main")
    res = repo.commit("hooks/résumé.py", "bad\n")
    assert res.returncode != 0 and "REFUSED (LUCY-wqn96)" in res.stderr
    assert repo.rev("main") == before


def test_branch_rename_onto_checked_out_main_blocked_by_git(repo):
    """branch -M/-C onto main bypass the transaction (residual, LUCY-4oxj3), but git
    refuses them while main is checked out, and ~/.claude keeps main checked out."""
    repo.as_agent()
    repo.branch_with_gate_change()
    before = repo.rev("main")
    for flag in ("-M", "-C"):
        assert repo.git("branch", flag, "e3", "main", check=False).returncode != 0
        assert repo.rev("main") == before


def test_fleet_role_env_var_not_trusted(repo):
    repo.as_agent()
    repo.env["FLEET_ROLE"] = "orchestrator"
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_gate_path_matching():
    hook = load_hook()
    for p in ("references/review-lead/review_lead_check.py", "scripts/review-gate.sh",
              "skills/land-batch/bin/land_batch_safety.py", "skills/land-batch/bin/discover.sh",
              "hooks/x.sh", "settings.json", "commands/goal.md", "scripts/git-hooks/reference-transaction",
              "scripts/git-hooks/install.sh", "scripts/fleet-role.py", "bin/bd", "hooks",
              "skills/land-batch", "references/review-lead"):
        assert hook.is_gate_path(p), p
    for p in ("commands/closeout.md", "hooks-old/x.sh", "settings.json.bak",
              "skills/land-batchx/a", "scripts/review-gate.sh.bak", "foo/settings.json",
              "bin/codex", "bin/bdx"):
        assert not hook.is_gate_path(p), p


# ---- Round 3 (S1-S5): install and startup trust anchors ----

# S1: a per-worktree core.hooksPath would override the shared one
def test_s1_per_worktree_hooks_path_refused(repo, tmp_path):
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    repo.git("config", "extensions.worktreeConfig", "true")
    repo.git("config", "--worktree", "core.hooksPath", str(tmp_path / "elsewhere"), cwd=wt)
    hooks_path_before = repo.git("config", "--get", "core.hooksPath").stdout
    res = repo.install()
    assert res.returncode == 1
    # Refused up front, naming the worktree, before anything is installed or reconfigured.
    assert f"worktree {wt} sets its own core.hooksPath" in res.stderr
    assert repo.git("config", "--get", "core.hooksPath").stdout == hooks_path_before


def test_s1_per_worktree_hooks_path_equal_to_common_allowed(repo, tmp_path):
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    repo.git("config", "extensions.worktreeConfig", "true")
    repo.git("config", "--worktree", "core.hooksPath", os.path.join(repo.common, "hooks"), cwd=wt)
    res = repo.install()
    assert res.returncode == 0, res.stderr


def test_s1_every_worktree_sees_the_common_hooks_dir(repo, tmp_path):
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    assert repo.install().returncode == 0
    want = os.path.join(repo.common, "hooks")
    for where in (repo.root, wt):
        assert repo.git("config", "--get", "core.hooksPath", cwd=where).stdout.strip() == want


# S2: install.sh installs main's committed blobs, from main, for the orchestrator only
def test_s2_install_uses_committed_blob_not_working_tree(repo):
    permissive = "#!/bin/sh\nexit 0\n"
    for name in ("reference-transaction", "run-gate"):
        repo.write(f"scripts/git-hooks/{name}", permissive)  # uncommitted
    assert repo.install().returncode == 0
    for name, where in (("reference-transaction", "hooks"), ("run-gate", "live")):
        installed = open(os.path.join(repo.common, where, name)).read()
        assert installed == open(os.path.join(HOOKS_DIR, name)).read()
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_s2_install_refuses_when_head_is_not_main(repo):
    repo.git("checkout", "-q", "-b", "other")
    res = repo.install()
    assert res.returncode == 1 and "not refs/heads/main" in res.stderr


@pytest.mark.parametrize("role,code", [("sub", 1), ("orchestrator", 0)])
def test_s2_install_needs_the_orchestrator(repo, role, code):
    if role != "sub":  # the base checker already answers SUB
        repo.set_role(role)
    repo.as_agent()
    res = repo.install()
    assert res.returncode == code, res.stderr
    if code:
        assert "not the orchestrator" in res.stderr


def test_s2_install_refuses_a_malformed_agent_id(repo):
    repo.set_role("orchestrator")
    repo.as_agent(value="--who")
    assert repo.install().returncode == 1


# S3: python startup files and PYTHONPATH cannot pre-empt the hook
def test_s3_startup_injection_ignored(repo, tmp_path):
    inject = tmp_path / "inject"
    inject.mkdir()
    for name in ("sitecustomize.py", "usercustomize.py"):
        for where in (inject, repo.root):
            (where / name).write_text("import os; os._exit(0)\n")
    (inject / "subprocess.py").write_text("import os; os._exit(0)\n")
    repo.env["PYTHONPATH"] = f"{inject}:."
    repo.env["PYTHONSTARTUP"] = str(inject / "sitecustomize.py")
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_s3_non_isolated_start_re_execs(repo, tmp_path):
    """Started as plain `python3 hook` with a shadowing subprocess.py on PYTHONPATH, the hook
    re-execs under -I -S before importing it, so the shadow never runs."""
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "subprocess.py").write_text("import os; os._exit(0)\n")
    tip = repo.branch_with_gate_change()
    env = dict(repo.env, PYTHONPATH=str(inject), CLAUDE_CODE_SESSION_ID=AGENT_ID)
    res = subprocess.run(["python3", HOOK, "prepared"], cwd=repo.root, env=env, text=True,
                         input=f"{repo.rev('main')} {tip} refs/heads/main\n", capture_output=True)
    assert res.returncode == 1 and "REFUSED (LUCY-wqn96)" in res.stderr


def test_s3_shebang_is_isolated():
    for name in ("reference-transaction", "run-gate"):
        with open(os.path.join(HOOKS_DIR, name)) as fh:
            assert fh.readline().strip() == "#!/usr/bin/env -S python3 -I -S"


# S4: main must be a direct ref
def test_s4_commit_on_symbolic_mains_target_refused(repo):
    repo.git("checkout", "-q", "-b", "e3")
    repo.git("symbolic-ref", "refs/heads/main", "refs/heads/e3")  # as a human: allowed
    repo.as_agent()
    before = repo.rev("e3")
    res = repo.commit("commands/closeout.md", "moves main via its target\n")
    assert res.returncode != 0 and "(main is a symbolic ref to refs/heads/e3)" in res.stderr
    assert repo.rev("e3") == before


def test_s4_making_main_symbolic_refused(repo):
    repo.git("checkout", "-q", "-b", "e3")
    repo.as_agent()
    res = repo.git("symbolic-ref", "refs/heads/main", "refs/heads/e3", check=False)
    assert res.returncode != 0 and "(makes main a symbolic ref)" in res.stderr
    assert repo.git("symbolic-ref", "-q", "refs/heads/main", check=False).returncode != 0


def test_s4_orchestrator_may_make_main_symbolic(repo):
    repo.set_role("orchestrator")
    repo.git("checkout", "-q", "-b", "e3")
    repo.as_agent()
    assert repo.git("symbolic-ref", "refs/heads/main", "refs/heads/e3", check=False).returncode == 0


# S5 / T2: recreating main takes the checker from main's reflog only. git drops the reflog with
# the ref and cannot read a deleted ref's log, so after a delete only a human recreates main.
def test_s5_orchestrator_deletes_main_and_a_human_recreates_it(repo):
    repo.set_role("orchestrator")
    tip = repo.rev("main")
    repo.git("checkout", "-q", "-b", "other")
    repo.as_agent()
    assert repo.git("update-ref", "-d", "refs/heads/main", check=False).returncode == 0
    res = repo.git("update-ref", "refs/heads/main", tip, check=False)
    assert res.returncode != 0 and "(creates main)" in res.stderr
    res = repo.git("update-ref", "refs/heads/main", tip, check=False, env=repo.human_env())
    assert res.returncode == 0, res.stderr
    assert repo.rev("main") == tip


def test_s5_sub_recreate_refused_with_orchestrator_checker_on_a_branch(repo):
    """The anchor is main's last value (SUB checker), not a branch carrying a yes-checker."""
    repo.git("checkout", "-q", "-b", "permissive")
    repo.write(CHECKER, stub_source("orchestrator", repo.seen))
    repo.git("add", CHECKER)
    repo.git("commit", "-q", "-m", "permissive")
    repo.git("update-ref", "-d", "refs/heads/main")  # human
    repo.as_agent()
    res = repo.git("update-ref", "refs/heads/main", repo.rev("permissive"), check=False)
    assert res.returncode != 0 and "(creates main)" in res.stderr


def test_s5_no_anchor_refused_even_for_orchestrator(repo):
    repo.set_role("orchestrator")
    tip = repo.rev("main")
    repo.git("checkout", "-q", "-b", "other")
    repo.git("update-ref", "-d", "refs/heads/main")  # human
    os.unlink(os.path.join(repo.live, "current"))
    repo.as_agent()
    res = repo.git("update-ref", "refs/heads/main", tip, check=False)
    assert res.returncode != 0 and "(creates main)" in res.stderr


# ---- Round 3 (L1-L5): the live export and run-gate ----

def gate_files(repo, ref="main"):
    hook = load_hook()
    out = repo.git("ls-tree", "-r", "--name-only", ref).stdout.split()
    return sorted(p for p in out if hook.is_gate_path(p))


def export_files(repo, sha=None):
    root = os.path.join(repo.live, sha or repo.current())
    found = []
    for dirpath, _, files in os.walk(root):
        found.extend(os.path.relpath(os.path.join(dirpath, f), root) for f in files)
    return sorted(found)


# l1: the export is exactly main's gate files, read-only, and nothing else
def test_l1_export_matches_main_and_is_read_only(repo):
    assert repo.current() == repo.rev("main")
    files = export_files(repo)
    assert files == gate_files(repo)
    assert "commands/closeout.md" not in files and GATE_FILE in files
    root = os.path.join(repo.live, repo.current())
    for rel in files:
        with open(os.path.join(root, rel), "rb") as fh:
            assert fh.read() == repo.git("show", f"main:{rel}").stdout.encode()
        assert not os.access(os.path.join(root, rel), os.W_OK)
    assert not os.access(root, os.W_OK)


# l2: only a main move refreshes the export
def test_l2_orchestrator_merge_advances_export(repo):
    repo.set_role("orchestrator")
    tip = repo.branch_with_gate_change()
    repo.as_agent()
    assert repo.git("merge", "--ff-only", "e3", check=False).returncode == 0
    assert repo.current() == tip
    with open(os.path.join(repo.live, tip, GATE_FILE)) as fh:
        assert fh.read() == "branch\n"


def test_l2_sub_non_gate_commit_advances_export_gate_files_unchanged(repo):
    before = repo.current()
    repo.as_agent()
    assert repo.commit("commands/closeout.md", "fine\n").returncode == 0
    assert repo.current() == repo.rev("main") != before
    old_root, new_root = (os.path.join(repo.live, s) for s in (before, repo.current()))
    assert export_files(repo, before) == export_files(repo)
    for rel in export_files(repo):
        assert open(os.path.join(old_root, rel), "rb").read() == open(os.path.join(new_root, rel), "rb").read()


def test_l2_branch_commit_does_not_move_export(repo):
    before = repo.current()
    repo.branch_with_gate_change()
    assert repo.current() == before


def test_l2_keeps_three_exports(repo):
    for i in range(5):
        assert repo.commit("commands/closeout.md", f"x{i}\n").returncode == 0
    exports = [d for d in os.listdir(repo.live) if len(d) == 40]
    assert len(exports) == 3 and repo.current() in exports


# l3: an uncommitted edit in the checkout never runs
def test_l3_run_gate_runs_the_committed_version(repo, tmp_path):
    repo.write(PROBE, "print('tampered')\n")
    repo.write(PROBE_SIBLING, "WHO = 'tampered'\n")
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    res = repo.gate(PROBE, "a", "b c", cwd=elsewhere)
    assert res.returncode == 0, res.stderr
    who, rest = res.stdout.split(" ", 1)
    assert who == "committed"
    assert "['a', 'b c']" in rest and str(elsewhere.resolve()) in rest and rest.strip().endswith("1")


def test_l3_run_gate_runs_shell_entries_by_shebang(repo):
    repo.write("scripts/review-gate.sh", "#!/bin/sh\necho committed \"$@\"\n")
    os.chmod(repo.root / "scripts/review-gate.sh", 0o755)
    repo.git("add", "scripts/review-gate.sh")
    repo.git("commit", "-q", "-m", "gate sh")  # human
    repo.write("scripts/review-gate.sh", "#!/bin/sh\necho tampered\n")
    res = repo.gate("scripts/review-gate.sh", "x")
    assert res.returncode == 0 and res.stdout == "committed x\n", res.stderr


# l4: run-gate fails closed on any drift
def test_l4_tampered_export_refused(repo):
    root = os.path.join(repo.live, repo.current())
    target = os.path.join(root, PROBE_SIBLING)
    os.chmod(os.path.dirname(target), 0o755)
    os.chmod(target, 0o644)
    with open(target, "w") as fh:
        fh.write("WHO = 'tampered'\n")
    res = repo.gate(PROBE)
    assert res.returncode == 3 and "live export drift; refuse" in res.stderr
    assert "changed " + PROBE_SIBLING in res.stderr


def test_l4_extra_file_in_export_refused(repo):
    root = os.path.join(repo.live, repo.current())
    os.chmod(os.path.join(root, "references/review-lead"), 0o755)
    with open(os.path.join(root, "references/review-lead/sitecustomize.py"), "w") as fh:
        fh.write("x = 1\n")
    res = repo.gate(PROBE)
    assert res.returncode == 3 and "extra file" in res.stderr


def test_l4_dangling_current_refused(repo):
    link = os.path.join(repo.live, "current")
    os.unlink(link)
    os.symlink("f" * 40, link)
    res = repo.gate(PROBE)
    assert res.returncode == 3 and "no live/current export" in res.stderr


def test_l4_missing_export_refused(repo):
    os.unlink(os.path.join(repo.live, "current"))
    assert repo.gate(PROBE).returncode == 3


def test_l4_current_behind_main_refused(repo):
    before = repo.current()
    assert repo.commit("commands/closeout.md", "moved\n").returncode == 0
    link = os.path.join(repo.live, "current")
    os.unlink(link)
    os.symlink(before, link)  # an export that did not catch up
    started = time.monotonic()
    res = repo.gate(PROBE)
    assert res.returncode == 3 and "is not main" in res.stderr
    assert time.monotonic() - started >= 1.5  # waited for the hook's refresh first


@pytest.mark.parametrize("rel", ["commands/closeout.md", "../outside.py", "/etc/passwd",
                                 "references/review-lead/nope.py"])
def test_l4_non_gate_or_unknown_path_refused(repo, rel):
    assert repo.gate(rel).returncode == 3


def test_l4_fail_exit_for_blocking_hooks(repo):
    os.unlink(os.path.join(repo.live, "current"))
    assert repo.gate("--fail-exit", "2", PROBE).returncode == 2
    assert repo.gate("--fail-exit", "0", PROBE).returncode == 3


def test_l4_export_failure_leaves_current(repo, monkeypatch):
    """A refresh that cannot write leaves the last good export in place, and says so."""
    before = repo.current()
    os.chmod(repo.live, 0o555)
    try:
        res = repo.commit("commands/closeout.md", "moved\n")
    finally:
        os.chmod(repo.live, 0o755)
    assert res.returncode == 0  # the commit itself stands
    assert "LIVE EXPORT FAILED" in res.stderr
    assert repo.current() == before


def test_l4_live_export_sh_refreshes(repo):
    before = repo.current()
    assert repo.commit("commands/closeout.md", "moved\n").returncode == 0
    link = os.path.join(repo.live, "current")
    os.unlink(link)
    os.symlink(before, link)
    script = os.path.join(os.path.dirname(HOOKS_DIR), "live-export.sh")
    res = subprocess.run(["bash", script, str(repo.root), "main"], env=repo.env,
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert repo.current() == repo.rev("main")
    assert repo.gate(PROBE).returncode == 0


# l5: every listed caller runs gates through run-gate, never a checkout path
REPO_ROOT = os.path.dirname(os.path.dirname(HOOKS_DIR))
CALLERS = ["scripts/review-gate.sh", "references/review-lead/review_gate.py",
           "references/review-lead/review_lead_check.py", "scripts/fleet-land-codex.py",
           "skills/land-batch/SKILL.md", "scripts/gate-watch.py", "scripts/fleet-dispatch.py",
           "skills/aestheticc-review/SKILL.md", "commands/goal.md", "commands/long-goal.md"]
CHECKOUT_GATE_RE = r"\.claude/((?:scripts|references|skills|hooks)/[\w./-]+?\.(?:py|sh)|skills/land-batch/bin/[\w.-]+)"


# install.sh is the one gate script run from the checkout: the orchestrator runs it once, and it
# installs only main's committed blobs. Callers name it when run-gate is missing (T6).
CHECKOUT_OK = ("scripts/git-hooks/install.sh",)


def checkout_gate_refs(text):
    import re
    hook = load_hook()
    return [m.group(1) for m in re.finditer(CHECKOUT_GATE_RE, text)
            if hook.is_gate_path(m.group(1)) and m.group(1) not in CHECKOUT_OK]


def test_l5_callers_use_run_gate():
    bin_dir = os.path.join(REPO_ROOT, "skills/land-batch/bin")
    files = CALLERS + ["skills/land-batch/bin/" + f for f in sorted(os.listdir(bin_dir))
                       if os.path.isfile(os.path.join(bin_dir, f)) and not f.endswith(".pyc")]
    offenders = {}
    for rel in files:
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
            hits = checkout_gate_refs(fh.read())
        if hits:
            offenders[rel] = hits
    assert offenders == {}


def test_l5_settings_patch_repoints_committed_hooks():
    with open(os.path.join(HOOKS_DIR, "settings-live.patch"), encoding="utf-8") as fh:
        patch = fh.read()
    added = "\n".join(l for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++"))
    assert "/.git/live/run-gate" in added
    assert checkout_gate_refs(added) == []


def test_l5_detector_catches_checkout_paths():
    assert checkout_gate_refs('exec python3 "$HOME/.claude/references/review-lead/review_gate.py"')
    assert checkout_gate_refs("python3 ~/.claude/skills/land-batch/bin/land-state.py wait")
    assert not checkout_gate_refs("~/.claude/.git/live/run-gate references/review-lead/review_gate.py")
    assert not checkout_gate_refs("python3 ~/.claude/scripts/gated-test-run.py jest")


# Round 6 (lead action): node hooks route through run-gate too
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_l3_run_gate_runs_node_entries_from_the_export(repo):
    repo.write("hooks/jev-x/hook.mjs", textwrap.dedent("""\
        import { WHO } from "../jev-common/who.mjs";
        console.log(WHO, process.argv.slice(2).join(","));
    """))
    repo.write("hooks/jev-common/who.mjs", 'export const WHO = "committed";\n')
    repo.git("add", "hooks")
    repo.git("commit", "-q", "-m", "node hook")  # human
    repo.write("hooks/jev-common/who.mjs", 'export const WHO = "tampered";\n')
    res = repo.gate("--fail-exit", "2", "hooks/jev-x/hook.mjs", "a")
    assert res.returncode == 0 and res.stdout == "committed a\n", res.stderr


def load_patch_maker():
    loader = importlib.machinery.SourceFileLoader(
        "make_settings_patch", os.path.join(HOOKS_DIR, "make-settings-patch.py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


LIVE_REPO = os.path.expanduser("~/.claude")
LIVE_SETTINGS = os.path.join(LIVE_REPO, "settings.json")


@pytest.mark.skipif(not os.path.isfile(LIVE_SETTINGS), reason="no live ~/.claude/settings.json")
def test_l5_patched_live_settings_route_every_committed_gate_hook(tmp_path):
    """Every hook command in the live settings that runs a gate file committed on main goes
    through run-gate once the patch is applied; the rest are named in the patch header."""
    with open(os.path.join(HOOKS_DIR, "settings-live.patch"), encoding="utf-8") as fh:
        patch = fh.read()
    settings = LIVE_SETTINGS
    if "/.git/live/run-gate" not in open(LIVE_SETTINGS).read():
        (tmp_path / "settings.json").write_text(open(LIVE_SETTINGS).read())
        res = subprocess.run(["patch", "-s", "-p1", "-d", str(tmp_path)], input=patch, text=True,
                             capture_output=True)
        assert res.returncode == 0, "settings-live.patch no longer applies; regenerate it with " \
                                    "make-settings-patch.py: " + res.stdout + res.stderr
        settings = str(tmp_path / "settings.json")
    maker, hook = load_patch_maker(), load_hook()
    committed = set(subprocess.run(["git", "-C", LIVE_REPO, "ls-tree", "-r", "--name-only", "main"],
                                   capture_output=True, text=True).stdout.splitlines())
    pattern = maker.command_re(LIVE_REPO)
    unrouted = []
    for matchers in json.load(open(settings)).get("hooks", {}).values():
        for matcher in matchers:
            for h in matcher.get("hooks", []):
                m = pattern.fullmatch(h.get("command", ""))
                if m and hook.is_gate_path(m.group(1)) and not m.group(1).startswith(".git/live/"):
                    unrouted.append((m.group(1), h["command"]))
    assert [c for rel, c in unrouted if rel in committed] == []
    for rel, cmd in unrouted:
        assert cmd in patch.split("--- a/settings.json")[0], cmd


# ---- Round 4 (T1-T7) ----

def hook_cli(repo, *args, stdin=None, env=None):
    hook = os.path.join(repo.common, "hooks", "reference-transaction")
    return subprocess.run([hook, *args], cwd=repo.root, env=env or repo.env, input=stdin,
                          capture_output=True, text=True)


def export_path(repo, rel):
    return os.path.join(repo.live, repo.current(), rel)


def writable(path):
    os.chmod(os.path.dirname(path), 0o755)


# T1: every refusal, exceptions included, uses the caller's --fail-exit code
def test_t1_unreadable_export_file_uses_fail_exit(repo):
    target = export_path(repo, PROBE_SIBLING)
    os.chmod(target, 0)
    try:
        res = repo.gate("--fail-exit", "2", PROBE)
        assert res.returncode == 2 and "live export drift; refuse" in res.stderr, res.stderr
        assert repo.gate(PROBE).returncode == 3
    finally:
        os.chmod(target, 0o444)


def test_t1_broken_file_symlink_uses_fail_exit(repo):
    target = export_path(repo, PROBE_SIBLING)
    writable(target)
    os.unlink(target)
    os.symlink(str(repo.tmp / "nowhere.py"), target)
    res = repo.gate("--fail-exit", "2", PROBE)
    assert res.returncode == 2 and "symlink " + PROBE_SIBLING in res.stderr, res.stderr


def test_t1_directory_in_place_of_a_file_uses_fail_exit(repo):
    target = export_path(repo, PROBE_SIBLING)
    writable(target)
    os.unlink(target)
    os.mkdir(target)
    assert repo.gate("--fail-exit", "2", PROBE).returncode == 2


def test_t1_unloadable_hook_uses_fail_exit(repo):
    hook = os.path.join(repo.common, "hooks", "reference-transaction")
    saved = open(hook).read()
    try:
        with open(hook, "w") as fh:
            fh.write("raise SystemExit(0)\n")
        assert repo.gate("--fail-exit", "2", PROBE).returncode == 2
    finally:
        with open(hook, "w") as fh:
            fh.write(saved)


# T2: only main's tip, by the orchestrator / a human or a main move, is ever exported
def test_t2_sub_export_of_a_feature_sha_refused(repo):
    before = repo.current()
    tip = repo.branch_with_gate_change()
    repo.as_agent()
    res = hook_cli(repo, "--export", tip)
    assert res.returncode == 1 and "REFUSED" in res.stderr
    assert repo.current() == before
    assert not os.path.exists(os.path.join(repo.live, tip))


def test_t2_sub_export_of_main_refused(repo):
    repo.as_agent()
    res = hook_cli(repo, "--export", "main")
    assert res.returncode == 1 and "only the orchestrator" in res.stderr


def test_t2_orchestrator_exports_main_only(repo):
    repo.set_role("orchestrator")
    tip = repo.branch_with_gate_change()
    repo.as_agent()
    res = hook_cli(repo, "--export", tip)
    assert res.returncode == 1 and "is not main's tip" in res.stderr
    res = hook_cli(repo, "--export", "main")
    assert res.returncode == 0, res.stderr
    assert repo.current() == repo.rev("main")


def test_t2_hand_fed_committed_line_cannot_move_the_export(repo):
    before = repo.current()
    tip = repo.branch_with_gate_change()
    repo.as_agent()
    res = hook_cli(repo, "committed", stdin="%s %s refs/heads/main\n" % (before, tip))
    assert res.returncode == 0
    assert repo.current() == before


def test_t2_live_export_sh_refused_for_a_sub(repo):
    tip = repo.branch_with_gate_change()
    repo.as_agent()
    script = os.path.join(os.path.dirname(HOOKS_DIR), "live-export.sh")
    for sha in (tip, "main"):
        res = subprocess.run(["bash", script, str(repo.root), sha], env=repo.env,
                             capture_output=True, text=True)
        assert res.returncode != 0 and "REFUSED" in res.stderr


def test_t2_poisoned_link_is_not_a_recreate_anchor(repo):
    """Main is deleted; the current link now names a commit with a yes-checker; a SUB recreates."""
    repo.git("checkout", "-q", "-b", "permissive")
    repo.write(CHECKER, stub_source("orchestrator", repo.seen))
    repo.git("add", CHECKER)
    repo.git("commit", "-q", "-m", "permissive")
    permissive = repo.rev("permissive")
    repo.git("update-ref", "-d", "refs/heads/main")  # human
    link = os.path.join(repo.live, "current")
    os.unlink(link)
    os.symlink(permissive, link)
    repo.as_agent()
    res = repo.git("update-ref", "refs/heads/main", permissive, check=False)
    assert res.returncode != 0 and "(creates main)" in res.stderr


# T3: every link of main's symbolic chain is watched
def make_chain(repo):
    """main -> middle -> leaf, made by a human; HEAD on another branch."""
    repo.git("checkout", "-q", "-b", "other")
    repo.git("branch", "leaf", "main")
    env = repo.human_env()
    repo.git("symbolic-ref", "refs/heads/middle", "refs/heads/leaf", env=env)
    repo.git("symbolic-ref", "refs/heads/main", "refs/heads/middle", env=env)
    assert repo.rev("main") == repo.rev("leaf")


def test_t3_sub_redirecting_an_intermediate_link_refused(repo):
    tip = repo.branch_with_gate_change()
    repo.git("checkout", "-q", "main")
    make_chain(repo)
    before = repo.rev("main")
    repo.as_agent()
    res = repo.git("symbolic-ref", "refs/heads/middle", "refs/heads/e3", check=False)
    assert res.returncode != 0 and "REFUSED (LUCY-wqn96)" in res.stderr
    assert repo.rev("main") == before != tip


def test_t3_orchestrator_may_redirect_an_intermediate_link(repo):
    repo.set_role("orchestrator")
    tip = repo.branch_with_gate_change()
    repo.git("checkout", "-q", "main")
    make_chain(repo)
    repo.as_agent()
    assert repo.git("symbolic-ref", "refs/heads/middle", "refs/heads/e3", check=False).returncode == 0
    assert repo.rev("main") == tip


def test_t3_symref_chain_walks_every_link(repo):
    make_chain(repo)
    hook = load_hook()
    env = dict(os.environ, GIT_DIR=repo.common)
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        assert hook.symref_chain() == ["refs/heads/middle", "refs/heads/leaf"]
    finally:
        os.environ.clear()
        os.environ.update(saved)


# T4: exported gates resolve their siblings inside the export, never in the checkout
LIVE_HOOKS = os.path.join(os.path.dirname(os.path.dirname(HOOKS_DIR)), "hooks")
DIRTY_CHECKER = textwrap.dedent("""\
    import sys
    open({marker!r}, 'w').write('dirty checkout fleet-role ran')
    print('ORCHESTRATOR'); sys.exit(0)
""")


def commit_real_hook(repo, name):
    dest = repo.root / "hooks" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.path.join(LIVE_HOOKS, name), dest)
    os.chmod(repo.root / CHECKER, 0o755)  # as on the real main
    repo.git("add", "hooks/" + name, CHECKER, env=repo.human_env())
    repo.git("commit", "-q", "-m", "hook " + name, env=repo.human_env())
    marker = repo.tmp / "dirty-ran"
    repo.write(CHECKER, DIRTY_CHECKER.format(marker=str(marker)))
    return marker


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_t4_sessionstart_hook_runs_the_committed_checker(repo):
    marker = commit_real_hook(repo, "fleet-role-sessionstart.sh")
    res = subprocess.run([repo.run_gate, "hooks/fleet-role-sessionstart.sh"], cwd=repo.root,
                         env=repo.env, input=json.dumps({"session_id": AGENT_ID}),
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "this session is SUB" in res.stdout
    assert not marker.exists()
    assert AGENT_ID in repo.seen.read_text()


def test_t4_sendmessage_guard_runs_the_committed_checker(repo, tmp_path):
    marker = commit_real_hook(repo, "sendmessage-guard.py")
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    claude = home / ".local/bin/claude"
    claude.write_text("#!/bin/sh\necho '[{\"name\": \"worker1\", \"kind\": \"interactive\", "
                      "\"sessionId\": \"sid-1\", \"startedAt\": 0}]'\n")
    claude.chmod(0o755)
    proj = home / ".claude/projects/p"
    proj.mkdir(parents=True)
    (proj / "sid-1.jsonl").write_text(json.dumps(
        {"type": "assistant", "message": {"model": "claude-x", "usage": {"input_tokens": 1000}}}) + "\n")
    env = dict(repo.env, HOME=str(home))
    payload = {"tool_name": "SendMessage", "session_id": AGENT_ID,
               "tool_input": {"to": "worker1", "message": "NEW WORK: do a thing"}}
    res = subprocess.run([repo.run_gate, "--fail-exit", "2", "hooks/sendmessage-guard.py"],
                         cwd=repo.root, env=env, input=json.dumps(payload), capture_output=True, text=True)
    assert "live export drift" not in res.stderr, res.stderr
    assert "--who --json" in repo.seen.read_text()  # the committed checker answered
    assert not marker.exists()


def test_t4_exported_hooks_name_no_checkout_checker():
    for name in ("sendmessage-guard.py", "fleet-role-sessionstart.sh"):
        with open(os.path.join(LIVE_HOOKS, name), encoding="utf-8") as fh:
            code = [l for l in fh if not l.lstrip().startswith("#") and "CTX=" not in l]
        assert not [l for l in code if ".claude/scripts/fleet-role.py" in l], name


# T5: the export is built from blobs, so .gitattributes cannot change it
def test_t5_export_ignore_attribute_does_not_drop_a_gate_file(repo):
    repo.as_agent()
    res = repo.commit(".gitattributes", PROBE + " export-ignore\n" + PROBE_SIBLING + " export-subst\n")
    assert res.returncode == 0, res.stderr
    assert repo.current() == repo.rev("main")
    assert export_files(repo) == gate_files(repo)
    res = repo.gate(PROBE)
    assert res.returncode == 0 and res.stdout.startswith("committed "), res.stderr


def test_t5_export_keeps_the_exec_bit(repo):
    mode = os.stat(export_path(repo, "scripts/git-hooks/run-gate")).st_mode
    assert mode & 0o111 and not mode & 0o222
    assert not os.stat(export_path(repo, GATE_FILE)).st_mode & 0o111


def test_t5_gate_symlink_is_never_exported(repo):
    before = repo.current()
    os.symlink(str(repo.tmp / "outside.sh"), repo.root / "hooks-link.sh")
    os.makedirs(repo.root / "hooks", exist_ok=True)
    os.rename(repo.root / "hooks-link.sh", repo.root / "hooks" / "link.sh")
    repo.git("add", "hooks/link.sh")
    res = repo.git("commit", "-q", "-m", "gate symlink", check=False)  # human
    assert res.returncode == 0 and "gate path is a symlink" in res.stderr
    assert repo.current() == before
    assert repo.gate(PROBE).returncode == 3


# T6: before install, the review gate refuses with the install command
def test_t6_review_gate_without_run_gate_says_install(tmp_path):
    script = os.path.join(os.path.dirname(HOOKS_DIR), "review-gate.sh")
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    res = subprocess.run(["bash", script, "LUCY-x", "0" * 40], env=env, capture_output=True, text=True)
    assert res.returncode == 3
    assert "run-gate not installed" in res.stderr and "install.sh" in res.stderr


# T7: the gate runs with a scrubbed environment and a fixed PATH; the link must be exact
ENV_PROBE = "references/review-lead/envprobe.py"


def test_t7_caller_environment_is_scrubbed(repo, tmp_path):
    repo.write(ENV_PROBE, textwrap.dedent("""\
        import json, os, shutil
        keys = ("BASH_ENV", "ENV", "NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONSTARTUP",
                "PATH", "CLAUDE_LIVE_ROOT")
        print(json.dumps({k: os.environ.get(k) for k in keys} | {"node": shutil.which("node")}))
    """))
    repo.git("add", ENV_PROBE)
    repo.git("commit", "-q", "-m", "env probe")  # human
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "node").write_text("#!/bin/sh\necho fake\n")
    (fake / "node").chmod(0o755)
    env = dict(repo.env, BASH_ENV="/x", ENV="/x", NODE_OPTIONS="--require /x", NODE_PATH="/x",
               PYTHONPATH="/x", PYTHONSTARTUP="/x", PATH=str(fake) + ":" + repo.env["PATH"],
               HOME=str(tmp_path))
    res = repo.gate(ENV_PROBE, env=env)
    assert res.returncode == 0, res.stderr
    seen = json.loads(res.stdout)
    assert all(seen[k] is None for k in ("BASH_ENV", "ENV", "NODE_OPTIONS", "NODE_PATH",
                                         "PYTHONPATH", "PYTHONSTARTUP"))
    path = seen["PATH"].split(":")
    assert path[0] == os.path.join(repo.live, repo.rev("main"), "bin")
    assert path[1:6] == ["/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    assert str(fake) not in path and path[6:] == [str(tmp_path / d) for d in
                                                  (".local/bin", ".claude/bin", ".npm-global/bin")]
    assert seen["node"] != str(fake / "node")
    assert seen["CLAUDE_LIVE_ROOT"] == os.path.join(repo.live, repo.rev("main"))


def test_t7_current_naming_a_missing_dir_refused(repo):
    sha = repo.current()
    os.rename(os.path.join(repo.live, sha), os.path.join(repo.live, "moved"))
    res = repo.gate("--fail-exit", "2", PROBE)
    assert res.returncode == 2 and "no live/current export" in res.stderr


@pytest.mark.parametrize("form", ["./{sha}", "{live}/{sha}", "{sha}/"])
def test_t7_current_must_be_a_bare_oid(repo, form):
    sha = repo.current()
    link = os.path.join(repo.live, "current")
    os.unlink(link)
    os.symlink(form.format(sha=sha, live=repo.live), link)
    assert repo.gate(PROBE).returncode == 3


def test_t7_symlink_inside_the_export_refused(repo):
    target = export_path(repo, PROBE_SIBLING)
    writable(target)
    os.unlink(target)
    os.symlink(os.path.join(repo.root, PROBE_SIBLING), target)  # same content, but a link
    res = repo.gate(PROBE)
    assert res.returncode == 3 and "symlink " + PROBE_SIBLING in res.stderr



# Round 4 own-gate (F1): a gate's child python never imports from the caller's cwd
import re as _re
CWD_PYTHON_RE = _re.compile(r"\bpython3?\s+(?!-I\b)(?:-[A-Za-z]+\s+)*(?:-c\b|-\s|<<)")


def routed_shell_gates():
    """Exported .sh entries run through run-gate: land-batch bin/ and the patch's hook commands."""
    root = os.path.dirname(os.path.dirname(HOOKS_DIR))
    bin_dir = os.path.join(root, "skills/land-batch/bin")
    rels = ["skills/land-batch/bin/" + f for f in sorted(os.listdir(bin_dir)) if f.endswith(".sh")]
    with open(os.path.join(HOOKS_DIR, "settings-live.patch"), encoding="utf-8") as fh:
        rels += sorted(set(_re.findall(r"run-gate (?:--fail-exit \d+ )?(hooks/\S+\.sh)", fh.read())))
    return root, rels


def test_f1_routed_shell_gates_isolate_child_python():
    root, rels = routed_shell_gates()
    assert "skills/land-batch/bin/discover.sh" in rels and "hooks/gbrain-ingest/stop-hook.sh" in rels
    offenders = {}
    for rel in rels:
        with open(os.path.join(root, rel), encoding="utf-8") as fh:
            hits = [l.strip() for l in fh if not l.lstrip().startswith("#") and CWD_PYTHON_RE.search(l)]
        if hits:
            offenders[rel] = hits
    assert offenders == {}


def test_f1_detector():
    assert CWD_PYTHON_RE.search("python3 <<'PY'") and CWD_PYTHON_RE.search('x=$(python3 -c "')
    assert CWD_PYTHON_RE.search("python3 - \"$A\" <<'PY'")
    assert not CWD_PYTHON_RE.search("python3 -I <<'PY'") and not CWD_PYTHON_RE.search('python3 -I -c "')
    assert not CWD_PYTHON_RE.search('exec python3 "$SCRIPT_DIR/land-state.py" status')


def test_f1_stop_hook_ignores_a_planted_cwd_module(repo, tmp_path):
    dest = repo.root / "hooks/gbrain-ingest/stop-hook.sh"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.path.join(LIVE_HOOKS, "gbrain-ingest/stop-hook.sh"), dest)
    repo.git("add", "hooks/gbrain-ingest/stop-hook.sh")
    repo.git("commit", "-q", "-m", "stop hook")  # human
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    marker = tmp_path / "planted-ran"
    (cwd / "json.py").write_text("open(%r, 'w').write('x')\n" % str(marker))
    home = tmp_path / "home"
    (home / ".claude/hooks/gbrain-ingest/logs").mkdir(parents=True)
    (home / ".claude/hooks/gbrain-ingest/markers").mkdir(parents=True)
    res = subprocess.run([repo.run_gate, "hooks/gbrain-ingest/stop-hook.sh"], cwd=cwd,
                         env=dict(repo.env, HOME=str(home)),
                         input=json.dumps({"session_id": "s1", "transcript_path": "/nonexistent"}),
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert not marker.exists()
    log = (home / ".claude/hooks/gbrain-ingest/logs/hook.log").read_text()
    assert "transcript not found" in log  # the JSON was parsed by the real json module


# ---- Layer A tests merged from main @1025d7c (S1 includeIf, E1 gitlinks, E2 newline paths) ----

def test_s1_worktree_seeing_another_hooks_path_refused(repo, tmp_path):
    """Without extensions.worktreeConfig, an includeIf scoped to one worktree's branch can still
    give it another core.hooksPath. Only the post-install check sees that."""
    wt = tmp_path / "wt"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    inc = tmp_path / "other-hooks.cfg"
    inc.write_text(f"[core]\n\thooksPath = {tmp_path / 'elsewhere'}\n")
    repo.git("config", "includeIf.onbranch:wtb.path", str(inc))
    res = repo.install()
    assert res.returncode == 1
    assert f"worktree {wt} sees core.hooksPath='{tmp_path / 'elsewhere'}'" in res.stderr


def test_e2_newline_worktree_with_own_hooks_path_refused(repo, tmp_path):
    wt = tmp_path / "wt\nx"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    repo.git("config", "extensions.worktreeConfig", "true")
    repo.git("config", "--worktree", "core.hooksPath", str(tmp_path / "elsewhere"), cwd=wt)
    res = repo.install()
    assert res.returncode == 1
    assert f"worktree {wt} sets its own core.hooksPath" in res.stderr


def test_e2_newline_worktree_seeing_another_hooks_path_refused(repo, tmp_path):
    wt = tmp_path / "wt\nx"
    repo.git("worktree", "add", "-q", "-b", "wtb", str(wt))
    inc = tmp_path / "other-hooks.cfg"
    inc.write_text(f"[core]\n\thooksPath = {tmp_path / 'elsewhere'}\n")
    repo.git("config", "includeIf.onbranch:wtb.path", str(inc))
    res = repo.install()
    assert res.returncode == 1
    assert f"worktree {wt} sees core.hooksPath=" in res.stderr


def branch_with_gitlink(repo, path="hooks/new-module", ignore_all=True, name="sm"):
    """A branch adding a gitlink at `path` (and a .gitmodules marking it ignore=all)."""
    sub = repo.tmp / "sub"
    sub.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "s")):
        subprocess.run(["git", *args], cwd=sub, env=repo.env, check=True, capture_output=True)
    sub_tip = subprocess.run(["git", "rev-parse", "HEAD"], cwd=sub, env=repo.env,
                             capture_output=True, text=True, check=True).stdout.strip()
    repo.git("checkout", "-q", "-b", name)
    if ignore_all:
        repo.write(".gitmodules", f'[submodule "m"]\n\tpath = {path}\n\turl = {sub}\n\tignore = all\n')
        repo.git("add", ".gitmodules")
    repo.git("update-index", "--add", "--cacheinfo", f"160000,{sub_tip},{path}")
    repo.git("commit", "-q", "-m", "gitlink")
    tip = repo.rev(name)
    repo.git("checkout", "-q", "main")
    return tip


def test_e1_gitmodules_ignore_all_gitlink_ff_merge_refused(repo):
    repo.as_agent()
    branch_with_gitlink(repo)
    before = repo.rev("main")
    res = repo.git("merge", "--ff-only", "sm", check=False)
    assert_refused(res, repo, before, what="hooks/new-module")


def test_e1_repo_config_ignore_submodules_all_refused(repo):
    repo.git("config", "diff.ignoreSubmodules", "all")
    repo.as_agent()
    branch_with_gitlink(repo, ignore_all=False)
    before = repo.rev("main")
    res = repo.git("merge", "--ff-only", "sm", check=False)
    assert_refused(res, repo, before, what="hooks/new-module")


def test_e1_gitlink_at_a_gate_dir_itself_refused(repo):
    """A gitlink named exactly a gate directory (hooks, no trailing slash) is a gate change."""
    repo.as_agent()
    branch_with_gitlink(repo, path="hooks")
    before = repo.rev("main")
    res = repo.git("merge", "--ff-only", "sm", check=False)
    assert_refused(res, repo, before, what="hooks")


def test_e1_non_gate_gitlink_allowed(repo):
    repo.as_agent()
    tip = branch_with_gitlink(repo, path="vendor/mod")
    res = repo.git("merge", "--ff-only", "sm", check=False)
    assert res.returncode == 0, res.stderr
    assert repo.rev("main") == tip


def test_e1_diff_relative_config_cannot_hide_a_gate_path(repo):
    """diff.relative, with git run from a subdirectory, would drop paths outside it."""
    repo.git("config", "diff.relative", "true")
    repo.as_agent()
    repo.branch_with_gate_change()
    before = repo.rev("main")
    res = repo.git("merge", "--ff-only", "e3", check=False, cwd=repo.root / "commands")
    assert_refused(res, repo, before)



# ---- Layer B round 5 (LUCY-p122j B1-B4): the gate's trust anchors ----
REAL_ROOT = os.path.dirname(os.path.dirname(HOOKS_DIR))
ORCH_ID = "99999999-8888-7777-6666-555555555555"


def commit_files(repo, files):
    """Commit {repo path: text or (source path,)} to main as a human."""
    for rel, src in files.items():
        dest = repo.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(src, tuple):
            shutil.copy2(src[0], dest)
        else:
            dest.write_text(src)
        repo.git("add", rel, env=repo.human_env())
    repo.git("commit", "-q", "-m", "files", env=repo.human_env())
    assert repo.current() == repo.rev("main")


# B1: a gate's bd is main's committed close-guard wrapper, never Homebrew's bd
BD_STUB = textwrap.dedent("""\
    #!/bin/sh
    echo "wrapper $*" >> {log}
""")
LANDER = textwrap.dedent("""\
    import subprocess, sys
    # as fleet-land-codex.py:383 does: bd looked up on PATH
    r = subprocess.run(["bd", "close", "LUCY-zzzzz", "--reason", "landed"], capture_output=True, text=True)
    sys.exit(r.returncode)
""")


def test_b1_gate_bd_close_goes_through_the_exported_wrapper(repo, tmp_path):
    log = tmp_path / "bd.log"
    commit_files(repo, {"bin/bd": BD_STUB.format(log=log), "scripts/fleet-land-codex.py": LANDER})
    os.chmod(repo.root / "bin/bd", 0o755)
    repo.git("add", "bin/bd", env=repo.human_env())
    repo.git("commit", "-q", "-m", "x", env=repo.human_env())
    assert "bin/bd" in export_files(repo)
    # BEADS_DIR at an empty dir: if the wrapper were skipped, a real bd finds no database.
    env = dict(repo.env, BEADS_DIR=str(tmp_path / "no-beads"))
    res = repo.gate("scripts/fleet-land-codex.py", env=env)
    assert res.returncode == 0, res.stderr
    assert log.read_text() == "wrapper close LUCY-zzzzz --reason landed\n"


def test_b1_checkout_bd_edit_never_runs(repo, tmp_path):
    log = tmp_path / "bd.log"
    commit_files(repo, {"bin/bd": BD_STUB.format(log=log), "scripts/fleet-land-codex.py": LANDER})
    os.chmod(repo.root / "bin/bd", 0o755)
    repo.git("add", "bin/bd", env=repo.human_env())
    repo.git("commit", "-q", "-m", "x", env=repo.human_env())
    repo.write("bin/bd", "#!/bin/sh\necho tampered >> %s\n" % log)  # uncommitted
    assert repo.gate("scripts/fleet-land-codex.py").returncode == 0
    assert log.read_text().startswith("wrapper close")


def test_b1_bd_is_a_gate_path(repo):
    repo.as_agent()
    before = repo.rev("main")
    assert_refused(repo.commit("bin/bd", "#!/bin/sh\nexit 0\n"), repo, before, what="bin/bd")


# B2: the exported, provider-aware fleet-role.py accepts the flags its exported callers pass
def fleet_home(repo, tmp_path, live_ids):
    """HOME whose ~/.claude is the repo (so gate-watch's ~/.claude/.git/live/run-gate is this
    repo's), an orchestrator marker for ORCH_ID and a `claude agents` stub listing live_ids."""
    commit_files(repo, {rel: (os.path.join(REAL_ROOT, rel),) for rel in (
        "scripts/fleet-role.py", "scripts/fleet_codex_rpc.py", "scripts/gate-watch.py",
        "hooks/sendmessage-guard.py")})
    home = tmp_path / "home"
    (home / ".local/bin").mkdir(parents=True)
    os.symlink(repo.root, home / ".claude")
    (repo.root / "fleet").mkdir(exist_ok=True)
    (repo.root / "fleet/ORCHESTRATOR").write_text(json.dumps(
        {"session_id": ORCH_ID, "name": "orch", "provider": "claude", "claimed_at": "2026-10-25T00:00:00Z"}))
    agents = [{"pid": 4242, "kind": "interactive", "sessionId": sid, "name": name, "id": sid[:8],
               "startedAt": int((time.time() - 6 * 3600) * 1000)} for name, sid in live_ids]
    claude = home / ".local/bin/claude"
    claude.write_text("#!/bin/sh\ncat <<'EOF'\n" + json.dumps(agents) + "\nEOF\n")
    claude.chmod(0o755)
    return home, dict(repo.env, HOME=str(home))


def shanes_conversation(home, sid):
    """A busy tab Shane has been talking to: without the orchestrator exemption, NEW WORK is denied."""
    rows = [{"type": "user", "message": {"role": "user", "content": f"Shane prompt {i}"}} for i in range(4)]
    rows += [{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}}]}} for i in range(40)]
    rows.append({"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [],
                 "usage": {"input_tokens": 100_000}}})
    proj = home / ".claude/projects/p"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.mark.parametrize("target,decision", [("orch", "allow"), ("shane", "deny")])
def test_b2_sendmessage_guard_recognises_the_orchestrator_target(repo, tmp_path, target, decision):
    other = "22222222-3333-4444-5555-666666666666"
    home, env = fleet_home(repo, tmp_path, [("orch", ORCH_ID), ("shane", other)])
    shanes_conversation(home, ORCH_ID)
    shanes_conversation(home, other)
    payload = {"tool_name": "SendMessage", "session_id": AGENT_ID,
               "tool_input": {"to": target, "message": "NEW WORK: do a thing"}}
    res = subprocess.run([repo.run_gate, "--fail-exit", "2", "hooks/sendmessage-guard.py"],
                         cwd=repo.root, env=env, input=json.dumps(payload), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"] if res.stdout.strip() else "allow"
    assert got == decision, res.stdout


def gate_watch_env(tmp_path, env):
    ledger = tmp_path / "L.jsonl"
    ledger.write_text(json.dumps({"id": "m1", "what": "w", "question": "q?",
                                  "first_seen_at": "2020-01-01T00:00:00Z", "resolved_at": None}) + "\n")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"computed_at": time.time(), "items": [], "sweep_ok": True, "shift_ok": True}))
    env = {k: v for k, v in env.items() if k != "GATE_WATCH_ROLE"}
    return dict(env, GATE_LEDGER=str(ledger), GATE_WATCH_CACHE=str(cache))


@pytest.mark.parametrize("sid,speaks", [(ORCH_ID, True), ("22222222-3333-4444-5555-666666666666", False)])
def test_b2_gate_watch_recognises_the_owner_through_run_gate(repo, tmp_path, sid, speaks):
    _, env = fleet_home(repo, tmp_path, [("orch", ORCH_ID), ("other", "22222222-3333-4444-5555-666666666666")])
    env = gate_watch_env(tmp_path, env)
    res = subprocess.run([repo.run_gate, "scripts/gate-watch.py", "--hook"], cwd=repo.root, env=env,
                         input=json.dumps({"session_id": sid}), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    if speaks:
        ctx = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
        assert ctx.startswith("RE-ASK SHANE NOW")
    else:
        assert res.stdout.strip() == ""


def test_b2_exported_checker_accepts_its_callers_flags(repo, tmp_path):
    _, env = fleet_home(repo, tmp_path, [("orch", ORCH_ID)])
    who = subprocess.run([repo.run_gate, "scripts/fleet-role.py", "--who", "--json"], cwd=repo.root,
                         env=env, capture_output=True, text=True)
    assert who.returncode == 0 and json.loads(who.stdout)["record"]["session_id"] == ORCH_ID
    me = subprocess.run([repo.run_gate, "scripts/fleet-role.py", ORCH_ID, "--provider", "claude"],
                        cwd=repo.root, env=env, capture_output=True, text=True)
    assert me.returncode == 0 and me.stdout.strip() == "ORCHESTRATOR", me.stderr


# B3: an authorised move of any link of main's symbolic chain refreshes the export
def test_b3_orchestrator_redirecting_an_intermediate_link_refreshes_the_export(repo):
    repo.set_role("orchestrator")
    tip = repo.branch_with_gate_change()
    repo.git("checkout", "-q", "main")
    make_chain(repo)
    repo.as_agent()
    assert repo.git("symbolic-ref", "refs/heads/middle", "refs/heads/e3", check=False).returncode == 0
    assert repo.rev("main") == tip
    assert repo.current() == tip
    res = repo.gate(PROBE)
    assert res.returncode == 0, res.stderr
    with open(os.path.join(repo.live, tip, GATE_FILE)) as fh:
        assert fh.read() == "branch\n"


def test_b3_orchestrator_commit_on_the_chain_leaf_refreshes_the_export(repo):
    repo.set_role("orchestrator")
    make_chain(repo)
    repo.git("checkout", "-q", "leaf")
    repo.as_agent()
    assert repo.commit(GATE_FILE, "via leaf\n").returncode == 0
    assert repo.current() == repo.rev("main") == repo.rev("leaf")
    assert repo.gate(PROBE).returncode == 0


def test_b3_unrelated_branch_does_not_refresh(repo):
    make_chain(repo)
    before = repo.current()
    repo.git("checkout", "-q", "-b", "side")
    assert repo.commit("commands/closeout.md", "side\n").returncode == 0
    assert repo.current() == before


# B4: exported bash functions never reach a gate
def test_b4_exported_bash_function_is_scrubbed(repo, tmp_path):
    marker = tmp_path / "fn-ran"
    probe = "references/review-lead/fnprobe.sh"
    commit_files(repo, {probe: "#!/usr/bin/env bash\nnode -e 'process.stdout.write(\"real\")' 2>/dev/null || echo nonode\n"})
    os.chmod(repo.root / probe, 0o755)
    repo.git("add", probe, env=repo.human_env())
    repo.git("commit", "-q", "-m", "x", env=repo.human_env())
    body = "() {  echo fn > %s; echo hijacked\n}" % marker
    env = dict(repo.env, **{"BASH_FUNC_node%%": body, "BASH_FUNC_node()": body})
    res = repo.gate(probe, env=env)
    assert res.returncode == 0, res.stderr
    assert "hijacked" not in res.stdout and not marker.exists()
