"""tier_gates.py (LUCY-z3ykz.2): the LAND push-time gate set comes from the risk tier.
Run: uvx --python 3.13 pytest ~/.claude/skills/land-batch/tests/test_tier_gates.py -q"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "tier_gates.py"
spec = importlib.util.spec_from_file_location("tier_gates", SCRIPT)
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

LIGHT = ["components/ui/button.tsx", "styles/globals.css", "CHANGELOG.md"]
FULL = ["components/ui/button.tsx", "lib/booking/slots.ts"]


def names(result):
    return [g["name"] for g in result["gates"]]


def test_light_diff_runs_tsc_once_and_skips_pg_lane_and_cloudbuild():
    result = tg.plan(LIGHT)
    assert result["tier"] == "light" and names(result) == ["tsc"]
    joined = json.dumps(result)
    assert "pg-tests" not in joined and "simulate-cloudbuild" not in joined


def test_full_diff_runs_tsc_then_the_whole_cloudbuild_simulation():
    result = tg.plan(FULL)
    assert result["tier"] == "full" and names(result) == ["tsc", "cloudbuild-full"]
    # The whole simulation (no --step) includes step #2, the pg lane inside the tarball.
    assert result["gates"][1]["cmd"].endswith("simulate-cloudbuild-test.sh")


def test_full_gates_are_sequential_and_pg_work_holds_the_lock():
    # 12 Oct: pg lane + test:cloudbuild run concurrently gave 9 false pg failures.
    result = tg.plan(FULL)
    assert result["sequential"] is True
    pg = [g for g in result["gates"] if "cloudbuild" in g["name"] or "pg" in g["cmd"]]
    assert len(pg) == 1 and pg[0]["cmd"].startswith('python3 "$HOME/.claude/scripts/pg-lane-lock.py" --')
    for gate in result["gates"]:
        assert "&" not in gate["cmd"].replace("&&", "")


def test_beads_export_adds_the_config_effect_suite():
    result = tg.plan(LIGHT + [".beads/issues.jsonl"])
    assert result["tier"] == "light" and names(result) == ["tsc", "beads-config-effect"]
    assert "tests/config-effect/coverage.config-effect.test.ts" in result["gates"][1]["cmd"]


def test_one_full_candidate_makes_the_batch_full():
    assert tg.plan(LIGHT + ["drizzle/0300_x.sql"])["tier"] == "full"


def test_empty_batch_is_full():
    assert tg.plan([])["tier"] == "full"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo_with_branch(tmp_path, path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x")
    _git(repo, "add", "README")
    _git(repo, "commit", "-qm", "base")
    base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    _git(repo, "add", path)
    _git(repo, "commit", "-qm", "change")
    return repo, base


def test_cli_plan_records_tier_reasons_and_head(tmp_path):
    repo, base = _repo_with_branch(tmp_path, "lib/x.ts")
    out = tmp_path / "tier.json"
    subprocess.run([sys.executable, str(SCRIPT), "plan", "--scratch", str(repo), "--base", base,
                    "--out", str(out)], check=True, capture_output=True)
    data = json.loads(out.read_text())
    assert data["tier"] == "full" and data["base"] == base and len(data["head"]) == 40
    assert data["reasons"] == ["lib/x.ts: not UI/style/copy (default full)"]


def test_fresh_scratch_has_node_modules_before_commit(tmp_path):
    trunk = tmp_path / "trunk"
    (trunk / "node_modules" / ".bin").mkdir(parents=True)
    repo, _ = _repo_with_branch(tmp_path, "components/a.tsx")
    scratch = tmp_path / "scratch"
    _git(repo, "worktree", "add", "-q", str(scratch), "HEAD")
    assert not (scratch / "node_modules").exists()
    subprocess.run([sys.executable, str(SCRIPT), "link-node-modules", "--scratch", str(scratch),
                    "--repo", str(trunk)], check=True, capture_output=True)
    assert (scratch / "node_modules" / ".bin").is_dir()
    # Idempotent: a second call (replay attempt) neither fails nor re-points the link.
    tg.link_node_modules(scratch, tmp_path / "elsewhere")
    assert (scratch / "node_modules").resolve() == (trunk / "node_modules").resolve()


def test_cloudbuild_gate_cannot_inherit_skip_pg_tests():
    # Astra round 2: SKIP_PG_TESTS=1 in the lander's shell would drop step #2 and still exit 0.
    cmd = tg.plan(FULL)["gates"][-1]["cmd"]
    assert "env -u SKIP_PG_TESTS bash scripts/ci/simulate-cloudbuild-test.sh" in cmd
