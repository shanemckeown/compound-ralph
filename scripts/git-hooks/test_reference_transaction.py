"""Tests for scripts/git-hooks/reference-transaction (LUCY-wqn96).

Each test builds a throwaway repo whose core.hooksPath is this directory, with a stub
fleet-role (FLEET_ROLE_BIN) standing in for scripts/fleet-role.py.
Run: python3 -m pytest scripts/git-hooks/test_reference_transaction.py -q
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import textwrap

import pytest

HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HOOKS_DIR, "reference-transaction")
GATE_FILE = "references/review-lead/x.py"
AGENT_ID = "11111111-2222-3333-4444-555555555555"


def load_hook():
    loader = importlib.machinery.SourceFileLoader("reference_transaction", HOOK)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Repo:
    def __init__(self, tmp_path):
        self.root = tmp_path / "repo"
        self.stub = tmp_path / "fleet-role-stub.py"
        self.seen = tmp_path / "fleet-role-seen.txt"
        self.set_role("sub")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "FLEET_ROLE")
                    and not k.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "FLEET_ROLE_BIN": str(self.stub),
        })
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "core.hooksPath", HOOKS_DIR)
        # The baseline commit is made as a human (no agent id), so the hook allows it.
        self.write(GATE_FILE, "v0\n")
        self.write("commands/closeout.md", "v0\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def set_role(self, role):
        body = {
            "orchestrator": "print('ORCHESTRATOR'); sys.exit(0)",
            "claude-orchestrator": "sys.exit(0 if sys.argv[-1] == 'claude' else 1)",
            "sub": "print('SUB'); sys.exit(1)",
            "error": "sys.exit(2)",
            "hang": "time.sleep(30)",
        }[role]
        self.stub.write_text(textwrap.dedent(f"""\
            import sys, time
            open({str(self.seen)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')
            {body}
        """))

    def as_agent(self, key="CLAUDE_CODE_SESSION_ID"):
        self.env[key] = AGENT_ID

    def git(self, *args, cwd=None, check=True):
        res = subprocess.run(["git", *args], cwd=cwd or self.root, env=self.env,
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

    def branch_with_gate_change(self, name="e3"):
        """A branch one commit ahead of main that edits GATE_FILE (made on the branch, so allowed)."""
        self.git("checkout", "-q", "-b", name)
        res = self.commit(GATE_FILE, "branch\n")
        assert res.returncode == 0, res.stderr
        tip = self.rev(name)
        self.git("checkout", "-q", "main")
        return tip


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def assert_refused(res, repo, main_before):
    assert res.returncode != 0
    assert "REFUSED (LUCY-wqn96)" in res.stderr
    assert GATE_FILE in res.stderr
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
    repo.git("checkout", "-q", "-b", "docs")
    assert repo.commit("commands/closeout.md", "docs\n").returncode == 0
    tip = repo.rev("docs")
    repo.git("checkout", "-q", "main")
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
    repo.as_agent()
    tip = repo.branch_with_gate_change()
    repo.set_role("orchestrator")
    res = repo.git("merge", "--ff-only", "e3", check=False)
    assert res.returncode == 0, res.stderr
    assert repo.rev("main") == tip


# (g) a non-gate path on main by a SUB is allowed
def test_g_non_gate_path_allowed(repo):
    repo.as_agent()
    res = repo.commit("commands/closeout.md", "fine\n")
    assert res.returncode == 0, res.stderr
    assert not repo.seen.exists()  # identity is only checked when a gate path moves


# (h) fleet-role erroring or hanging fails closed
@pytest.mark.parametrize("role", ["error", "hang"])
def test_h_fleet_role_failure_refused(repo, role):
    repo.as_agent()
    repo.set_role(role)
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_h_missing_fleet_role_refused(repo, tmp_path):
    repo.as_agent()
    repo.env["FLEET_ROLE_BIN"] = str(tmp_path / "nope.py")
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


def test_codex_thread_id_is_an_agent(repo):
    repo.as_agent("CODEX_THREAD_ID")
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_identity_passes_provider(repo):
    repo.as_agent()
    repo.commit(GATE_FILE, "bad\n")
    assert repo.seen.read_text().split() == [AGENT_ID, "--provider", "claude"]


def test_codex_worker_inheriting_orchestrator_claude_id_refused(repo):
    """A Codex worker spawned from the Claude orchestrator carries both ids."""
    repo.as_agent("CLAUDE_CODE_SESSION_ID")
    repo.env["CODEX_THREAD_ID"] = "codex-worker-thread"
    repo.set_role("claude-orchestrator")
    before = repo.rev("main")
    assert_refused(repo.commit(GATE_FILE, "bad\n"), repo, before)


def test_claude_orchestrator_alone_allowed(repo):
    repo.as_agent("CLAUDE_CODE_SESSION_ID")
    repo.set_role("claude-orchestrator")
    assert repo.commit(GATE_FILE, "ok\n").returncode == 0


def test_non_ascii_gate_path_refused(repo):
    repo.as_agent()
    before = repo.rev("main")
    res = repo.commit("hooks/résumé.py", "bad\n")
    assert res.returncode != 0 and "REFUSED (LUCY-wqn96)" in res.stderr
    assert repo.rev("main") == before


def test_branch_rename_onto_checked_out_main_blocked_by_git(repo):
    """branch -M/-C bypass the transaction, but git refuses them while main is checked
    out, and ~/.claude always has main checked out at its root."""
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
              "scripts/fleet-role.py"):
        assert hook.is_gate_path(p), p
    for p in ("commands/closeout.md", "hooks-old/x.sh", "settings.json.bak",
              "skills/land-batchx/a", "scripts/review-gate.sh.bak", "foo/settings.json"):
        assert not hook.is_gate_path(p), p
