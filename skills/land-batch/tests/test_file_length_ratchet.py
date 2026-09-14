"""Real Git merge/commit/conflict fixtures; only the Jest runner is substituted."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

HELPER = Path(__file__).resolve().parents[1] / "bin/file_length_ratchet.py"
BASELINE = "tests/meta/file-length-baseline.json"
BEAD = "AestheticcNext-dcbcv"
BRANCH = "goal/aestheticcnext-dcbcv"


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], text=True,
                          capture_output=True, check=check)


def write(repo, path, content):
    full = repo / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)


def baseline(repo):
    return json.loads((repo / BASELINE).read_text())


def commit(repo, *paths):
    git(repo, "add", "--", *paths)
    git(repo, "commit", "--allow-empty", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Ratchet Test")
    write(repo, "lib/schema.ts", "// first\n// second\n")
    write(repo, "lib/other.ts", "// other\n")
    write(repo, BASELINE, json.dumps({"version": 1, "files": {
        "lib/other.ts": 1, "lib/schema.ts": 2}, "growAllowances": {}}, indent=2) + "\n")
    commit(repo, "lib/schema.ts", "lib/other.ts", BASELINE)
    runner = tmp_path / "ratchet_fixture.py"
    runner.write_text('''import json, os, pathlib, subprocess
p = pathlib.Path("tests/meta/file-length-baseline.json")
b = json.loads(p.read_text())
tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "lib"]).decode().split("\\0")
actual = {f: len(pathlib.Path(f).read_text().splitlines()) for f in tracked if f and pathlib.Path(f).is_file()}
if os.environ.get("UPDATE_BASELINE") == "1":
    bead = os.environ["FILE_LENGTH_GROW_OK"]
    next_b = {"version": 1, "files": actual, "growAllowances": {}}
    for f, count in actual.items():
        owner = bead if count > b["files"].get(f, 350) else b["growAllowances"].get(f)
        if owner: next_b["growAllowances"][f] = owner
    p.write_text(json.dumps(next_b, indent=2) + "\\n")
    b = next_b
else:
    assert "FILE_LENGTH_GROW_OK" not in os.environ
assert all(count <= b["files"].get(f, 350) for f, count in actual.items()), "ratchet red"
''')
    return repo, runner


def invoke(fixture, tmp_path, action="regenerate", bead=BEAD, command=None, **kwargs):
    repo, runner = fixture
    evidence = tmp_path / f"evidence-{len(list(tmp_path.glob('evidence-*')))}"
    args = [sys.executable, str(HELPER), action, "--repo", str(repo),
            "--evidence-dir", str(evidence), "--command-json",
            json.dumps(command or [sys.executable, str(runner)])]
    if action == "regenerate":
        args += ["--merge", kwargs.get("merge", git(repo, "rev-parse", "HEAD").stdout.strip()),
                 "--branch", BRANCH]
        if bead is not None:
            args += ["--bead-id", bead]
    if "expected" in kwargs:
        args += ["--expected-head", kwargs["expected"]]
    proc = subprocess.run(args, text=True, capture_output=True)
    result = json.loads(proc.stdout)
    assert proc.returncode == (0 if result["valid"] else 1), proc.stderr
    assert json.loads((evidence / "result.json").read_text()) == result
    return result, evidence


def merge_growth(repo, path="lib/schema.ts", text="// third\n", branch=BRANCH):
    git(repo, "checkout", "-qb", branch)
    with (repo / path).open("a") as stream:
        stream.write(text)
    commit(repo, path)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-m", "merge fixture", branch)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_growth_creates_attributed_commit_and_full_evidence(fixture, tmp_path):
    repo, _ = fixture
    result, _ = invoke(fixture, tmp_path, "check", expected="HEAD")
    assert result["valid"]
    merge = merge_growth(repo)
    result, evidence = invoke(fixture, tmp_path)
    assert result["valid"]
    assert baseline(repo)["files"]["lib/schema.ts"] == 3
    assert baseline(repo)["growAllowances"] == {"lib/schema.ts": BEAD}
    assert git(repo, "log", "-1", "--format=%s").stdout.strip() == (
        f"chore(ratchet): regenerate file-length baseline for {BRANCH} ({BEAD})")
    assert git(repo, "rev-parse", "HEAD^").stdout.strip() == merge
    assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.strip() == BASELINE
    summary = json.loads((evidence / "baseline-summary.json").read_text())
    assert summary["raised"] == [{"path": "lib/schema.ts", "before": 2,
                                  "after": 3, "bead_id": BEAD}]
    assert '"lib/schema.ts": 3' in (evidence / "baseline.diff").read_text()
    for command in evidence.glob("*-command.txt"):
        stem = str(command).removesuffix("-command.txt")
        assert Path(stem + "-raw-output.txt").exists()
        assert Path(stem + "-exit-code.txt").read_text().strip() == "0"
    result, _ = invoke(fixture, tmp_path, "check")
    assert result["valid"]


@pytest.mark.parametrize("action", ["check", "regenerate"])
def test_beads_churn_is_preserved_by_check_and_regenerate(fixture, tmp_path, action):
    repo, _ = fixture
    modified, staged, untracked = (f".beads/{name}" for name in (
        "export-state.json", "issues.jsonl", "new-export.json"))
    for path in (modified, staged):
        write(repo, path, "{}\n")
    commit(repo, modified, staged)
    if action == "regenerate":
        merge_growth(repo)
    head = git(repo, "rev-parse", "HEAD").stdout
    for path in (modified, staged, untracked):
        write(repo, path, '{"updated": true}\n')
    git(repo, "add", "--", staged)
    status = git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    staged_diff = git(repo, "diff", "--cached").stdout
    result, _ = invoke(fixture, tmp_path, action)
    assert result["valid"]
    assert git(repo, "status", "--porcelain", "--untracked-files=all").stdout == status
    assert git(repo, "diff", "--cached").stdout == staged_diff
    assert all((repo / path).read_text() == '{"updated": true}\n'
               for path in (modified, staged, untracked))
    if action == "regenerate":
        assert result["commit_sha"]
        assert baseline(repo)["files"]["lib/schema.ts"] == 3
        assert baseline(repo)["growAllowances"] == {"lib/schema.ts": BEAD}
        assert git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").stdout.strip() == BASELINE
        assert git(repo, "rev-parse", "HEAD^").stdout == head
    else:
        assert git(repo, "rev-parse", "HEAD").stdout == head


def test_preexisting_main_drift_is_held_before_merge(fixture, tmp_path, monkeypatch):
    repo, _ = fixture
    write(repo, "lib/other.ts", "// drift\n// drift\n")
    commit(repo, "lib/other.ts")
    old = (repo / BASELINE).read_bytes()
    monkeypatch.setenv("UPDATE_BASELINE", "1")
    monkeypatch.setenv("FILE_LENGTH_GROW_OK", BEAD)
    result, _ = invoke(fixture, tmp_path, "check")
    assert result["hold"] == "ratchet-check-red"
    assert (repo / BASELINE).read_bytes() == old


@pytest.mark.parametrize("new", [False, True])
def test_unrelated_growth_cannot_be_attributed_even_without_base_check(fixture, tmp_path, new):
    repo, _ = fixture
    path = "lib/unrelated.ts" if new else "lib/other.ts"
    write(repo, path, "// drift\n" * (351 if new else 2))
    commit(repo, path)
    merge = merge_growth(repo)
    old = (repo / BASELINE).read_bytes()
    result, evidence = invoke(fixture, tmp_path)
    assert result["hold"] == "ratchet-unrelated-growth"
    assert json.loads((evidence / "baseline-summary.json").read_text())["out_of_scope"] == [path]
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == merge
    assert (repo / BASELINE).read_bytes() == old
    assert git(repo, "status", "--porcelain").stdout == ""


@pytest.mark.parametrize("bead", [None, "", "20260913T194409Z-60603", "AestheticcNext-WRONG"])
def test_no_resolvable_bead_holds_without_mutation(fixture, tmp_path, bead):
    repo, _ = fixture
    merge = merge_growth(repo)
    result, evidence = invoke(fixture, tmp_path, bead=bead)
    assert result["hold"] == "ratchet-missing-bead"
    assert not list(evidence.glob("*-regenerate-command.txt"))
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == merge


def test_no_growth_no_baseline_commit(fixture, tmp_path):
    repo, _ = fixture
    merge_growth(repo, text="")
    result, _ = invoke(fixture, tmp_path)
    assert result["valid"] and result["commit_sha"] is None


def test_new_oversized_tracked_source_is_attributed(fixture, tmp_path):
    repo, _ = fixture
    git(repo, "checkout", "-qb", BRANCH)
    write(repo, "lib/new.ts", "// new\n" * 351)
    commit(repo, "lib/new.ts")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-m", "merge", BRANCH)
    result, _ = invoke(fixture, tmp_path)
    assert result["valid"]
    assert baseline(repo)["growAllowances"]["lib/new.ts"] == BEAD


def test_clean_incoming_baseline_cannot_hide_unrelated_growth(fixture, tmp_path):
    repo, _ = fixture
    git(repo, "checkout", "-qb", BRANCH)
    write(repo, "lib/schema.ts", "// source\n" * 3)
    incoming = baseline(repo)
    incoming["files"]["lib/schema.ts"] = 3
    incoming["files"]["lib/other.ts"] = 2
    incoming["growAllowances"] = {"lib/schema.ts": "AestheticcNext-old", "lib/other.ts": BEAD}
    write(repo, BASELINE, json.dumps(incoming, indent=2) + "\n")
    commit(repo, "lib/schema.ts", BASELINE)
    git(repo, "checkout", "-q", "main")
    write(repo, "lib/other.ts", "// drift\n" * 2)
    commit(repo, "lib/other.ts")
    git(repo, "merge", "--no-ff", "-m", "clean but wrong baseline", BRANCH)
    result, _ = invoke(fixture, tmp_path)
    assert result["hold"] == "ratchet-unrelated-growth"


def conflicted_baselines(repo, authored=False):
    git(repo, "checkout", "-qb", BRANCH)
    write(repo, "lib/schema.ts", "// first\n// second\n// candidate\n")
    incoming = baseline(repo)
    incoming["files"]["lib/schema.ts"] = 3
    incoming["growAllowances"]["lib/schema.ts"] = BEAD
    write(repo, BASELINE, json.dumps(incoming, indent=2) + "\n")
    if authored:
        write(repo, "lib/other.ts", "// branch conflicting edit\n")
    commit(repo, "lib/schema.ts", "lib/other.ts", BASELINE)
    git(repo, "checkout", "-q", "main")
    prior = baseline(repo)
    # An older allowance on the same entry must survive until real regen.
    prior["growAllowances"]["lib/schema.ts"] = "AestheticcNext-prior"
    write(repo, BASELINE, json.dumps(prior, indent=2) + "\n")
    if authored:
        write(repo, "lib/other.ts", "// main conflicting edit\n")
    commit(repo, BASELINE, "lib/other.ts")
    assert git(repo, "merge", "--no-ff", "-m", "merge", BRANCH, check=False).returncode != 0
    return prior


def test_baseline_conflict_keeps_integration_then_regenerates(fixture, tmp_path):
    repo, _ = fixture
    prior = conflicted_baselines(repo)
    result, _ = invoke(fixture, tmp_path, "resolve-conflict")
    assert result["valid"]
    assert baseline(repo) == prior
    git(repo, "commit", "--no-edit")
    result, _ = invoke(fixture, tmp_path)
    assert result["valid"]
    assert baseline(repo)["files"]["lib/schema.ts"] == 3
    assert baseline(repo)["growAllowances"]["lib/schema.ts"] == BEAD


def test_authored_conflict_is_never_resolved(fixture, tmp_path):
    repo, _ = fixture
    conflicted_baselines(repo, authored=True)
    before = (repo / BASELINE).read_bytes()
    result, _ = invoke(fixture, tmp_path, "resolve-conflict")
    assert result["hold"] == "ratchet-authored-conflict"
    assert (repo / BASELINE).read_bytes() == before
    assert "lib/other.ts" in git(repo, "diff", "--name-only", "--diff-filter=U").stdout


def test_sequential_migration_candidates_keep_separate_allowances(fixture, tmp_path):
    repo, _ = fixture
    merge_growth(repo)
    first, _ = invoke(fixture, tmp_path)
    assert first["valid"]
    merge_growth(repo, "lib/other.ts", branch="goal/aestheticcnext-second")
    second, _ = invoke(fixture, tmp_path, bead="AestheticcNext-second")
    assert second["valid"]
    assert baseline(repo)["growAllowances"] == {
        "lib/schema.ts": BEAD, "lib/other.ts": "AestheticcNext-second"}
    final, _ = invoke(fixture, tmp_path, "check")
    assert final["valid"]


@pytest.mark.parametrize("when", ["regen", "check"])
def test_failed_runner_restores_baseline_without_commit(fixture, tmp_path, when):
    repo, runner = fixture
    merge = merge_growth(repo)
    before = (repo / BASELINE).read_bytes()
    runner.write_text(runner.read_text() + (
        '\nif os.environ.get("UPDATE_BASELINE") == "1": raise SystemExit(9)\n'
        if when == "regen" else '\nif "UPDATE_BASELINE" not in os.environ: raise SystemExit(9)\n'))
    result, evidence = invoke(fixture, tmp_path)
    assert result["hold"] == ("ratchet-command-failed" if when == "regen" else "ratchet-check-red")
    assert any(p.read_text() == "9\n" for p in evidence.glob("*-exit-code.txt"))
    assert (repo / BASELINE).read_bytes() == before
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == merge


@pytest.mark.parametrize("dirty", ["staged", "untracked", "modified"])
def test_dirty_or_untracked_source_holds(fixture, tmp_path, dirty):
    repo, _ = fixture
    merge_growth(repo)
    path = "lib/new.ts" if dirty == "untracked" else "lib/other.ts"
    write(repo, path, "// pending\n")
    if dirty == "staged":
        git(repo, "add", "--", path)
    result, _ = invoke(fixture, tmp_path)
    assert result["hold"] == "ratchet-dirty-tree"


@pytest.mark.parametrize("dirty", ["staged", "untracked", "modified"])
def test_runner_churn_outside_beads_holds_without_commit(fixture, tmp_path, dirty):
    repo, runner = fixture
    path = ".beads-backup/issues.jsonl"
    if dirty != "untracked":
        write(repo, path, "{}\n")
        commit(repo, path)
    merge = merge_growth(repo)
    before = (repo / BASELINE).read_bytes()
    runner.write_text(runner.read_text() + f'''
if os.environ.get("UPDATE_BASELINE") == "1":
    pending = pathlib.Path({path!r})
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text('{{"updated": true}}\\n')
    if {dirty!r} == "staged": subprocess.check_call(["git", "add", "--", str(pending)])
''')
    result, _ = invoke(fixture, tmp_path)
    assert result["hold"] == "ratchet-dirty-tree"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == merge
    assert (repo / BASELINE).read_bytes() == before


def test_wrong_merge_or_base_sha_holds(fixture, tmp_path):
    repo, _ = fixture
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    merge_growth(repo)
    result, _ = invoke(fixture, tmp_path, merge=base)
    assert result["hold"] == "ratchet-invalid-input"
    result, _ = invoke(fixture, tmp_path, "check", expected=base)
    assert result["hold"] == "ratchet-invalid-input"


def test_wrong_generator_allowance_is_rejected(fixture, tmp_path):
    repo, runner = fixture
    merge_growth(repo)
    runner.write_text(runner.read_text() + '''
if os.environ.get("UPDATE_BASELINE") == "1":
    b["growAllowances"]["lib/schema.ts"] = "AestheticcNext-wrong"
    p.write_text(json.dumps(b) + "\\n")
''')
    result, _ = invoke(fixture, tmp_path)
    assert result["hold"] == "ratchet-invalid-attribution"


def test_beads_style_hook_cannot_add_unrelated_files_to_generated_commit(fixture, tmp_path):
    repo, _ = fixture
    merge_growth(repo)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    precommit = hooks / 'pre-commit'
    precommit.write_text('#!/bin/sh\necho unrelated > issues.jsonl\ngit add -- issues.jsonl\n')
    precommit.chmod(0o755)
    git(repo, 'config', 'core.hooksPath', str(hooks))
    result, _ = invoke(fixture, tmp_path)
    assert result['valid']
    assert git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').stdout.strip() == BASELINE
    assert not (repo / 'issues.jsonl').exists()
    assert git(repo, 'config', 'core.hooksPath').stdout.strip() == str(hooks)
