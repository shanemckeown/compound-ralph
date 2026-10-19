"""tier_gates.py (LUCY-z3ykz.2): the LAND push-time gate set comes from the risk tier.
Run: uvx --python 3.13 pytest ~/.claude/skills/land-batch/tests/test_tier_gates.py -q"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "tier_gates.py"
spec = importlib.util.spec_from_file_location("tier_gates", SCRIPT)
tg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tg)

@pytest.fixture(autouse=True)
def _no_inherited_gate_switch(monkeypatch):
    # The lander's own LAND_GATE_REMOTE must not decide which cmd these cells see.
    monkeypatch.delenv("LAND_GATE_REMOTE", raising=False)


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


# LUCY-z3ykz.8: the push-time verdict. Independent review 13 Oct: `gates: []` with a matching
# head passed the SKILL loop, so the push went ahead with no gate run at all.

HEAD = "a" * 40


def _green(tmp_path, tier_plan):
    for gate in tier_plan.get("gates", []):
        (tmp_path / f"{gate['name']}-{HEAD}.exit").write_text("0\n")
    return tier_plan


def _plan_file(tmp_path, tier_plan):
    f = tmp_path / "tier.json"
    f.write_text(json.dumps(tier_plan))
    return f


def test_green_plans_pass_the_push_check(tmp_path):
    for paths in (LIGHT, FULL):
        tier_plan = _green(tmp_path, {**tg.plan(paths), "head": HEAD})
        assert tg.push_problems(tier_plan, HEAD, tmp_path) == []


def test_empty_gate_list_is_refused_even_with_a_matching_head(tmp_path):
    for tier in ("light", "full"):
        problems = tg.push_problems({"tier": tier, "gates": [], "head": HEAD}, HEAD, tmp_path)
        assert "gate list empty or missing" in problems
        assert any("tsc missing" in p for p in problems)


def test_full_tier_without_cloudbuild_is_refused(tmp_path):
    tier_plan = _green(tmp_path, {"tier": "full", "gates": [dict(tg.TSC)], "head": HEAD})
    assert tg.push_problems(tier_plan, HEAD, tmp_path) == ["required gate cloudbuild-full missing from the plan"]


def test_unknown_tier_needs_the_full_set(tmp_path):
    tier_plan = _green(tmp_path, {"tier": "medium", "gates": [dict(tg.TSC)], "head": HEAD})
    problems = tg.push_problems(tier_plan, HEAD, tmp_path)
    assert any("unknown tier" in p for p in problems) and any("cloudbuild-full" in p for p in problems)


def test_stale_head_and_missing_or_red_exit_files_are_refused(tmp_path):
    tier_plan = {**tg.plan(FULL), "head": HEAD}
    (tmp_path / f"tsc-{HEAD}.exit").write_text("2\n")
    problems = tg.push_problems(tier_plan, HEAD, tmp_path)
    assert problems == ["tsc:2", "cloudbuild-full:missing"]
    assert any("not the tree to push" in p for p in tg.push_problems(_green(tmp_path, tier_plan), "b" * 40, tmp_path))


def test_check_cli_exit_codes(tmp_path):
    def check(tier_plan):
        return subprocess.run([sys.executable, str(SCRIPT), "check", "--plan", str(_plan_file(tmp_path, tier_plan)),
                               "--head", HEAD, "--evidence-dir", str(tmp_path)], capture_output=True, text=True)
    empty = check({"tier": "light", "gates": [], "head": HEAD})
    assert empty.returncode == 1 and "gate list empty" in empty.stdout
    green = check(_green(tmp_path, {**tg.plan(LIGHT), "head": HEAD}))
    assert (green.returncode, green.stdout) == (0, "")
    (tmp_path / "tier.json").write_text("not json")
    unreadable = subprocess.run([sys.executable, str(SCRIPT), "check", "--plan", str(tmp_path / "tier.json"),
                                 "--head", HEAD, "--evidence-dir", str(tmp_path)], capture_output=True, text=True)
    assert unreadable.returncode == 1 and "unreadable" in unreadable.stdout


# LUCY-vxk2v: LAND_GATE_REMOTE swaps only the cloudbuild-full cmd; name and push check unchanged.

REMOTE = 'python3 "$HOME/.claude/scripts/cloudbuild-gate-remote.py" --scratch .'


def test_land_gate_remote_switches_only_the_cloudbuild_cmd():
    local = tg.plan(FULL, env={"LAND_GATE_REMOTE": "0"})
    remote = tg.plan(FULL, env={"LAND_GATE_REMOTE": "1"})
    assert names(local) == names(remote) == ["tsc", "cloudbuild-full"]
    assert local["gates"][0] == remote["gates"][0] == tg.TSC
    assert local["gates"][1]["cmd"] == tg.CLOUDBUILD["cmd"]
    assert local["gates"][1]["cmd"].endswith("env -u SKIP_PG_TESTS bash scripts/ci/simulate-cloudbuild-test.sh")
    assert remote["gates"][1]["cmd"] == REMOTE
    # Remote runs nothing on the laptop: no pg-lane lock, no local simulation.
    assert "pg-lane-lock" not in remote["gates"][1]["cmd"] and "simulate" not in remote["gates"][1]["cmd"]
    assert (local["cloudbuild_remote"], remote["cloudbuild_remote"]) == (False, True)


def test_land_gate_remote_unset_uses_the_one_default_constant(monkeypatch):
    assert tg.plan(FULL, env={})["gates"][1]["cmd"] == tg.CLOUDBUILD["cmd"]  # default off until acceptance
    monkeypatch.setattr(tg, "REMOTE_DEFAULT", True)
    assert tg.plan(FULL, env={})["gates"][1]["cmd"] == REMOTE
    assert tg.plan(FULL, env={"LAND_GATE_REMOTE": "0"})["gates"][1]["cmd"] == tg.CLOUDBUILD["cmd"]


def test_land_gate_remote_is_read_from_the_lander_environment(monkeypatch):
    monkeypatch.setenv("LAND_GATE_REMOTE", "1")
    assert tg.plan(FULL)["gates"][1]["cmd"] == REMOTE
    assert tg.plan(LIGHT)["gates"] == [tg.TSC]  # light tier never gains a Cloud Build gate


def test_remote_plan_passes_and_fails_the_push_check_like_the_local_one(tmp_path):
    tier_plan = _green(tmp_path, {**tg.plan(FULL, env={"LAND_GATE_REMOTE": "1"}), "head": HEAD})
    assert tg.push_problems(tier_plan, HEAD, tmp_path) == []
    (tmp_path / f"cloudbuild-full-{HEAD}.exit").write_text("137\n")
    assert tg.push_problems(tier_plan, HEAD, tmp_path) == ["cloudbuild-full:137"]
