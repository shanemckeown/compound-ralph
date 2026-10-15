"""Check 13 of fleet-dispatch.py: the CLAUDE.md WIP cap. Refuse when >= 10 origin goal/* or
long-goal/* branches are unlanded (tip not on origin/main by unlanded-sweep.py's own test) and
their bead isn't closed with LANDED @<sha> / SUPERSEDED by <branch> (LUCY-6v5gv). Overridable only with --force plus a written reason (dated client promises).
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests"""
import importlib.util
import os
import types
import unittest

try:
    import pytest
except ImportError:
    raise unittest.SkipTest("needs pytest: uvx pytest ~/.claude/scripts/tests")

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fleet-dispatch.py")
spec = importlib.util.spec_from_file_location("fleet_dispatch_wip", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)


def wip(n):
    return [{"branch": f"goal/aestheticcnext-b{i}", "tip": f"{i:09d}", "bead": f"AestheticcNext-b{i}",
             "status": "pushed"} for i in range(n)]


def test_under_cap_passes_and_reports_count():
    problems, warning, detail = fd.check_wip(lambda: wip(9))
    assert problems == [] and warning is None
    assert detail == "9 unlanded goal/long-goal branches (cap 10)"


def test_at_cap_refuses_and_lists_branches():
    problems, _, detail = fd.check_wip(lambda: wip(10))
    text = "\n".join(problems)
    assert "WIP cap: 10 unlanded branches" in text and "--force-reason" in text
    assert "goal/aestheticcnext-b9" in text and "10 unlanded" in detail


def test_unreadable_count_warns_not_refuses():
    def boom():
        raise RuntimeError("bd down")
    problems, warning, detail = fd.check_wip(boom)
    assert problems == [] and detail is None and "bd down" in warning


def test_force_past_wip_needs_a_reason():
    failures = {fd.WIP_CHECK: ["x"]}
    assert "check 13" in fd.force_refusal(failures, True, None)
    assert "check 13" in fd.force_refusal(failures, True, "  ")
    assert fd.force_refusal(failures, True, "Pratish promise, live 12 Oct") is None
    assert fd.force_refusal(failures, False, None) is None  # plain refusal path handles it


def test_wip_reason_does_not_unlock_a_resource_failure():
    failures = {fd.WIP_CHECK: ["x"], "7 worktree count": ["x"]}
    assert "can't be forced" in fd.force_refusal(failures, True, "Pratish promise")


# ---- probe_wip_branches: which branches count -------------------------------------------
@pytest.fixture
def fake_repo(monkeypatch):
    refs = {  # branch -> (tip, committer time)
        "goal/aestheticcnext-aaaaa": ("a" * 40, 100),        # pushed, unlanded: counts
        "goal/7xzhz": ("b" * 40, 100),                       # no prefix: still maps to its bead
        "goal/aestheticcnext-ck.17.1": ("c" * 40, 100),      # nested child id
        "goal/aestheticcnext-ddddd": ("d" * 40, 100),        # ancestor of origin/main: landed
        "goal/aestheticcnext-eeeee": ("e" * 40, 100),        # landed_elsewhere says landed
        "goal/aestheticcnext-fffff": ("f" * 40, 100),        # bead closed/open: not WIP
        "goal/aestheticcnext-ggggg": ("1" * 40, 100),        # older sibling
        "goal/aestheticcnext-ggggg-r2": ("2" * 40, 200),     # newer sibling carries the bead
    }
    beads = {k: (f"AestheticcNext-{k}", "pushed", {"id": f"AestheticcNext-{k}"})
             for k in ("aaaaa", "7xzhz", "ddddd", "eeeee", "ggggg")}
    beads["ck.17.1"] = ("AestheticcNext-ck.17.1", "in_progress", {"id": "AestheticcNext-ck.17.1"})

    def sh(cmd, cwd=None, timeout=60, bead=None):
        if "for-each-ref" in cmd:
            return 0, "\n".join(f"origin/{b}\t{t}\t{ct}" for b, (t, ct) in refs.items()), ""
        if "merge-base --is-ancestor" in cmd:
            return (0 if "d" * 40 in cmd else 1), "", ""
        return 0, "", ""

    us = types.SimpleNamespace(
        land_commits=lambda: [],
        landed_elsewhere=lambda branch, tip, ct, key, land, subj: "x" if key == "eeeee" else None,
        sha_landed_in_text=lambda text, key: None,
        bead_text=lambda r: "")
    monkeypatch.setattr(fd, "sh", sh)
    monkeypatch.setattr(fd, "wip_bead_statuses", lambda: beads)
    monkeypatch.setattr(fd, "_load_unlanded_sweep", lambda: us)


def test_probe_counts_only_pushed_not_landed(fake_repo):
    got = {w["branch"]: w for w in fd.probe_wip_branches(fetch=False)}
    assert set(got) == {"goal/aestheticcnext-aaaaa", "goal/7xzhz", "goal/aestheticcnext-ck.17.1",
                        "goal/aestheticcnext-ggggg-r2"}
    assert got["goal/7xzhz"]["bead"] == "AestheticcNext-7xzhz"
    assert got["goal/aestheticcnext-ck.17.1"]["status"] == "in_progress"


def test_unlanded_sweep_exposes_the_shared_helpers():
    us = fd._load_unlanded_sweep()
    for name in ("landed_elsewhere", "land_commits", "sha_landed_in_text", "bead_text"):
        assert callable(getattr(us, name))


def test_run_gate_prints_wip_count(monkeypatch, capsys):
    for name in ("check_bead_is_dispatchable", "check_no_inflight_work", "check_dependencies",
                 "check_epic_has_children", "check_client_triage_tree", "check_premise_tree"):
        monkeypatch.setattr(fd, name, lambda b, **k: [])
    monkeypatch.setattr(fd, "check_resources", lambda: ({}, {}, {}))
    monkeypatch.setattr(fd, "check_credit_pace", lambda b, r=None: ([], None, None))
    real_check_wip = fd.check_wip
    monkeypatch.setattr(fd, "check_wip", lambda: real_check_wip(lambda: wip(12)))
    failed, failures = fd.run_gate("AestheticcNext-epic1", is_epic=True)
    out = capsys.readouterr().out
    assert failed and fd.WIP_CHECK in failures
    assert "WIP: 12 unlanded goal/long-goal branches (cap 10)" in out
    assert "✗ 13 WIP cap — 12 unlanded" in out

