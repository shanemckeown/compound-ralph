"""unlanded-sweep.py landed_open: a PUSHED bead whose work is on origin/main is closed LANDED @<sha>,
and nothing still in flight is (LUCY-g1u0e).
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests"""
import importlib.util
import os
import unittest

try:
    import pytest  # noqa: F401
except ImportError:
    raise unittest.SkipTest("needs pytest: uvx pytest ~/.claude/scripts/tests")

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "unlanded-sweep.py")
spec = importlib.util.spec_from_file_location("unlanded_sweep_landed", SCRIPT)
us = importlib.util.module_from_spec(spec)
spec.loader.exec_module(us)

LAND_T = 1_790_000_000  # 2026-09-21
LAND = ("b75fe3a631" + "0" * 30, LAND_T,
        "land: goal/aestheticcnext-2dp1n @ 9ec601253385a2e7da3b57971bb6734bed711a8b (batch 20260921T1 via /land-batch)")


def bead(status="pushed", comments=()):
    return {"id": "AestheticcNext-2dp1n", "status": status, "comments": list(comments)}


def ref(branch="goal/aestheticcnext-2dp1n", ct=LAND_T - 600, on_main=True, names=True, tip="9ec6012533"):
    return {"branch": branch, "tip": tip, "ct": ct, "on_main": on_main, "names_bead": names}


def test_exact_key_forms():
    assert us.exact_key("goal/aestheticcnext-2dp1n") == "2dp1n"
    assert us.exact_key("goal/aestheticcnext-2dp1n-r2") == "2dp1n"
    assert us.exact_key("worktree-goal-aestheticcnext-rt4gc") == "rt4gc"
    assert us.exact_key("auto/AestheticcNext-flkg9") == "flkg9"
    assert us.exact_key("goal/aestheticcnext-njnfq.23") == "njnfq.23"
    assert us.exact_key("long-goal/aestheticcnext-1b6a3") == "1b6a3"


def test_exact_key_rejects_long_goal_child_suffix():
    # a child's land must never close the epic it is named after
    assert us.exact_key("long-goal/aestheticcnext-1b6a3-32") is None
    assert us.exact_key("goal/lucy-g1u0e") is None


def test_landed_index_keeps_newest_land_per_key():
    old = ("a" * 40, LAND_T - 99, "land: goal/aestheticcnext-2dp1n @ 1111111 (batch x)")
    child = ("c" * 40, LAND_T + 5, "land: long-goal/aestheticcnext-2dp1n-3 @ 2222222 (batch y)")
    other = ("d" * 40, LAND_T + 9, "fix: mention aestheticcnext-2dp1n in passing")
    idx = us.landed_index([LAND, old, child, other])  # git log order is newest first; order must not matter
    assert idx["2dp1n"][0] == LAND[0]
    idx = us.landed_index([old, LAND, child, other])
    assert idx["2dp1n"][0] == LAND[0]


def test_pushed_with_land_commit_closes():
    r = us.decide_landed(bead(), LAND, [ref()])
    assert r["action"] == "close" and r["sha"] == LAND[0]


def test_non_pushed_is_reported_not_closed():
    for st in ("open", "in_progress", "blocked", "deferred"):
        assert us.decide_landed(bead(st), LAND, [])["action"] == "report"


def test_redispatch_after_land_skips():
    c = {"created_at": "2026-09-22T09:00:00Z", "text": "started /goal 2026-09-22T09:00:00+01:00"}
    assert us.decide_landed(bead(comments=[c]), LAND, [])["action"] == "skip"
    earlier = {"created_at": "2026-09-01T09:00:00Z", "text": "started /goal 2026-09-01"}
    assert us.decide_landed(bead(comments=[earlier]), LAND, [])["action"] == "close"
    chatter = {"created_at": "2026-09-22T09:00:00Z", "text": "Landed as part of zm5as.10's re-land"}
    assert us.decide_landed(bead(comments=[chatter]), LAND, [])["action"] == "close"


def unlanded(branch, ct, descends=False, equivalent=False):
    return dict(ref(branch, ct=ct, on_main=False), descends=descends, equivalent=equivalent)


def test_newer_unlanded_branch_skips_older_superseded_sibling_does_not():
    newer = unlanded("goal/aestheticcnext-2dp1n-r2", LAND_T + 60)
    assert us.decide_landed(bead(), LAND, [ref(), newer])["action"] == "skip"
    stale = unlanded("goal/aestheticcnext-2dp1n-r1", LAND_T - 60)
    assert us.decide_landed(bead(), LAND, [stale])["action"] == "close"


def test_commits_pushed_on_top_of_the_landed_tip_skip_even_if_older_than_the_land():
    # builder pushed more work while the train tested the pinned tip: committed before the land commit
    past = unlanded("goal/aestheticcnext-2dp1n", LAND_T - 30, descends=True)
    assert us.decide_landed(bead(), LAND, [past])["action"] == "skip"
    same = unlanded("goal/aestheticcnext-2dp1n", LAND_T + 60, equivalent=True)  # rebased copy of landed work
    assert us.decide_landed(bead(), LAND, [same])["action"] == "close"


def test_bead_lookup_failure_never_closes(monkeypatch):
    rows = '[{"id": "AestheticcNext-2dp1n", "status": "pushed", "title": "t"}]'
    monkeypatch.setattr(us, "sh", lambda args, *a, **k: (0, rows))
    monkeypatch.setattr(us, "git", lambda *a, **k: (0, "") if a[0] != "for-each-ref" else (0, ""))
    monkeypatch.setattr(us, "bead", lambda key, env: None)
    out = us.landed_open({}, [LAND])
    assert [(o["bead"], o["action"]) for o in out] == [("AestheticcNext-2dp1n", "skip")]
    monkeypatch.setattr(us, "bead", lambda key, env: bead())
    assert us.landed_open({}, [LAND])[0]["action"] == "close"


def test_tip_on_main_without_land_commit():
    r = us.decide_landed(bead(), None, [ref(ct=LAND_T)])
    assert r["action"] == "close" and r["sha"] == "9ec6012533"
    # a tip on main that never names the bead (branch cut from main, nothing pushed) is no evidence
    assert us.decide_landed(bead(), None, [ref(names=False)]) is None
    assert us.decide_landed(bead(), None, [ref(on_main=False)]) is None


def test_close_landed_reverifies_ancestry_and_uses_landed_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(us, "git", lambda *a, **k: (0 if a[-2] == "good" else 1, ""))
    monkeypatch.setattr(us, "sh", lambda args, *a, **k: (calls.append(args), (0, ""))[1])
    items = [{"bead": "AestheticcNext-a", "sha": "good", "evidence": "land: x", "action": "close", "why": ""},
             {"bead": "AestheticcNext-b", "sha": "gone", "evidence": "land: y", "action": "close", "why": ""},
             {"bead": "AestheticcNext-c", "sha": "good", "evidence": "land: z", "action": "report", "why": ""}]
    us.close_landed(items, {})
    assert [c[2] for c in calls] == ["AestheticcNext-a"]
    assert calls[0][:2] == ["bd", "close"] and us.re.search(r"^LANDED @good\b", calls[0][4])
    assert items[0]["closed"] is True and items[1]["closed"] is False and "closed" not in items[2]
