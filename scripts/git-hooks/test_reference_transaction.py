"""Tests for scripts/git-hooks/reference-transaction (LUCY-wqn96).

Each test builds a throwaway repo, installs the hook with install.sh (the exact config
the orchestrator will use), and commits a stub scripts/fleet-role.py to main: the hook
only ever runs the checker committed on main, never one from the working tree.
Run: ~/.claude/skills/land-batch/.venv/bin/python3 -m pytest scripts/git-hooks/ -q
"""
import importlib.machinery
import importlib.util
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
        # The baseline commit (made as a human, before any hook) carries the hook file:
        # install.sh installs main's committed blob, never the working tree.
        self.write(GATE_FILE, "v0\n")
        self.write("commands/closeout.md", "v0\n")
        self.write(CHECKER, stub_source(role, self.seen))
        dest = self.root / "scripts/git-hooks/reference-transaction"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HOOK, dest)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        res = self.install()
        assert res.returncode == 0, res.stderr
        self.install_output = res.stdout
        self.common = self.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()

    def install(self, env=None):
        return subprocess.run(["bash", INSTALL, str(self.root)], env=env or self.env,
                              capture_output=True, text=True)

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
              "scripts/git-hooks/install.sh", "scripts/fleet-role.py", "hooks",
              "skills/land-batch", "references/review-lead"):
        assert hook.is_gate_path(p), p
    for p in ("commands/closeout.md", "hooks-old/x.sh", "settings.json.bak",
              "skills/land-batchx/a", "scripts/review-gate.sh.bak", "foo/settings.json"):
        assert not hook.is_gate_path(p), p


# ---- Layer A hardening (S1-S5, T3): install and startup trust anchors ----

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


# E2: worktree paths are read NUL-delimited, so a newline in a path can't hide a worktree
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

# S2: install.sh installs main's committed blobs, from main, for the orchestrator only
def test_s2_install_uses_committed_blob_not_working_tree(repo):
    repo.write("scripts/git-hooks/reference-transaction", "#!/bin/sh\nexit 0\n")  # uncommitted
    assert repo.install().returncode == 0
    installed = open(os.path.join(repo.common, "hooks", "reference-transaction")).read()
    assert installed == open(HOOK).read()
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
    with open(HOOK) as fh:
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
    repo.as_agent()
    res = repo.git("update-ref", "refs/heads/main", tip, check=False)
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


# E1: neither an incoming .gitmodules nor repo config can hide a gate-path gitlink
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
