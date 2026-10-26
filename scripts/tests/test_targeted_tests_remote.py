"""targeted-tests.py (LUCY-ll8dh): heavy steps go to builder-remote-qa.py; related stays local.
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests/test_targeted_tests_remote.py -q"""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "targeted-tests.py"
spec = importlib.util.spec_from_file_location("targeted_tests", SCRIPT)
tt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tt)
SHA = "0123456789abcdef0123456789abcdef01234567"
LOCK = str(tt.SCRIPTS / "pg-lane-lock.py")
PG = [f"/r/tests/pg-integration/s{i}.test.ts" for i in range(10)]

CASES = {
    "broad": dict(files=[], broad=["package.json: outside"], node_related=0, pg_related=PG[:2],
                  pg_total=10, touched_pg=[]),
    "related-only": dict(files=["lib/a.ts"], broad=[], node_related=3, pg_related=PG[:2],
                         pg_total=10, touched_pg=[]),
    "over-cap-locked": dict(files=["lib/schema.ts"], broad=[], node_related=3, pg_related=PG,
                            pg_total=10, touched_pg=PG),
    "over-cap-edited-few": dict(files=["lib/schema.ts"], broad=[], node_related=3, pg_related=PG,
                                pg_total=10, touched_pg=PG[:1]),
    "no-pg": dict(files=[], broad=["x.sql: outside"], node_related=0, pg_related=None,
                  pg_total=0, touched_pg=[]),
}


def labels(steps):
    return [s[0] for s in steps]


@pytest.mark.parametrize("case", CASES)
def test_no_remote_sha_is_todays_plan(case):
    assert tt.plan(**CASES[case]) == tt.local_plan(**CASES[case])


def test_broad_goes_remote_and_drops_the_pg_step():
    steps, notes = tt.plan(**CASES["broad"], remote_sha=SHA)
    assert labels(steps) == ["heavy set on Cloud Build"]
    assert steps[0][1][1:] == [str(tt.REMOTE_QA), "--sha", SHA]
    assert any("Cloud Build via builder-remote-qa.py" in n for n in notes)
    assert any("BROAD set" in n for n in notes)  # the why stays printed


def test_over_cap_locked_pg_goes_remote_related_node_stays_local():
    steps, _ = tt.plan(**CASES["over-cap-locked"], remote_sha=SHA)
    assert labels(steps) == ["related jest", "heavy set on Cloud Build"]
    assert not any(LOCK in s[1] for s in steps)


@pytest.mark.parametrize("case", ["related-only", "over-cap-edited-few"])
def test_light_plans_stay_local_even_with_remote(case):
    assert tt.plan(**CASES[case], remote_sha=SHA) == tt.local_plan(**CASES[case])


def test_no_pg_broad_goes_remote():
    steps, _ = tt.plan(**CASES["no-pg"], remote_sha=SHA)
    assert labels(steps) == ["heavy set on Cloud Build"]


def run_main(monkeypatch, env_value, clean=True):
    monkeypatch.setenv("BUILDER_QA_REMOTE", env_value)
    monkeypatch.setattr(sys, "argv", ["targeted-tests.py", "--dry-run"])
    monkeypatch.setattr(tt, "changed_paths", lambda base: ["package.json"])
    monkeypatch.setattr(tt, "list_tests", lambda args, env: PG[:2] if "--findRelatedTests" in args else PG)
    calls = []

    def fake_git(*args):
        calls.append(args)
        if args[0] == "status":
            return "" if clean else " M lib/a.ts\n"
        if args[0] == "rev-parse":
            return SHA + "\n"
        raise AssertionError(args)
    monkeypatch.setattr(tt, "git", fake_git)
    out = io.StringIO()
    with redirect_stdout(out):
        code = tt.main()
    return code, out.getvalue(), calls


def test_main_remote_0_never_touches_git_and_plans_locally(monkeypatch):
    code, out, calls = run_main(monkeypatch, "0")
    assert calls == [] and code == 0
    assert "Cloud Build" not in out and "==> broad jest (node + dom)" in out


def test_main_remote_clean_dry_run_prints_the_remote_command(monkeypatch):
    code, out, _ = run_main(monkeypatch, "1")
    assert f"==> heavy set on Cloud Build: {sys.executable} {tt.REMOTE_QA} --sha {SHA}" in out


def test_main_remote_dirty_stays_local_and_says_why(monkeypatch):
    _, out, calls = run_main(monkeypatch, "", clean=False)
    assert "uncommitted changes" in out and "==> broad jest (node + dom)" in out
    assert ("rev-parse", "HEAD") not in calls


def test_worktree_clean_ignores_goal_and_beads(monkeypatch):
    monkeypatch.setattr(tt, "git", lambda *a: "?? .goal/PLAN.md\n M .beads/issues.jsonl\n")
    assert tt.worktree_clean()
    monkeypatch.setattr(tt, "git", lambda *a: "?? .goal/PLAN.md\n?? lib/new.ts\n")
    assert not tt.worktree_clean()
