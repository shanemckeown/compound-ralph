"""Tests for gate-watch.py + the unlanded-sweep bead-comment PASS fix (LUCY-3pfwv).
Run: python3 -m pytest ~/.claude/scripts/tests/test_gate_watch.py"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

SCRIPTS = os.path.expanduser("~/.claude/scripts")


def load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gw = load("gate_watch", "gate-watch.py")
sweep = load("unlanded_sweep", "unlanded-sweep.py")
safety = sweep._load_safety()

TIP = "b9e98d914dd31aed1f83af5a428bc8a81f6c7f28"
NOW = 1_800_000_000.0


def bead(*texts):
    return {"id": "AestheticcNext-x1", "comments": [{"text": t, "created_at": "2026-10-06T06:52:00Z"} for t in texts]}


# ---- sweep: bead-comment PASS ----
def test_safety_module_loads():
    assert safety is not None


def test_comment_pass_at_exact_tip_is_pass(monkeypatch):
    monkeypatch.setenv("LAND_REVIEWS_DIR", tempfile.mkdtemp())
    v, ev, at = sweep.comment_review(bead(f"MANAGER PASS @{TIP} (aestheticc-fc)"), TIP, safety)
    assert v == "PASS" and at == "2026-10-06T06:52:00Z"


def test_comment_pass_naming_different_tip_is_not_pass(monkeypatch):
    monkeypatch.setenv("LAND_REVIEWS_DIR", tempfile.mkdtemp())
    v, ev, at = sweep.comment_review(bead("MANAGER PASS @1111111aaaaaaa (aestheticc-fc)"), TIP, safety)
    assert v is None and at is None


def test_comment_fail_at_tip_is_fail(monkeypatch):
    monkeypatch.setenv("LAND_REVIEWS_DIR", tempfile.mkdtemp())
    v, _, _ = sweep.comment_review(bead(f"Opus FAIL @{TIP} one P2"), TIP, safety)
    assert v == "FAIL"


def test_comment_review_fails_open_without_safety_module():
    assert sweep.comment_review(bead(f"PASS @{TIP}"), TIP, safety=None)[0] in (None, "PASS")
    class Boom:
        def review_pass_for(self, *a, **k):
            raise RuntimeError("x")
    assert sweep.comment_review(bead(f"PASS @{TIP}"), TIP, safety=Boom()) == (None, "", None)
    assert sweep.comment_review(None, TIP, safety) == (None, "", None)


def test_opt_in_comment_detected():
    assert sweep.OPT_IN_RE.search("x\n  SENSITIVE-PATH OPT-IN: Shane, 6 Oct 'yes'\ny")
    assert not sweep.OPT_IN_RE.search("Privacy path -> sensitive opt-in may apply.")


# ---- gate-watch evaluate ----
def land_row(**kw):
    r = {"status": "LAND-READY", "branch": "goal/x-1", "tip": "abc1234", "bead": {"id": "B-1"},
         "age_days": 1, "pass_at": None, "sensitive": False, "opt_in_recorded": False}
    r.update(kw)
    return r


def test_land_ready_becomes_item_others_ignored():
    items = gw.land_items([land_row(), land_row(branch="n", status="NEEDS-REVIEW")], NOW)
    assert [i["id"] for i in items] == ["land:goal/x-1"]


def test_sensitive_without_opt_in_flagged_needs_opt_in():
    it = gw.land_items([land_row(sensitive=True)], NOW)[0]
    assert "needs Shane opt-in" in it["what"] and "Opt in" in it["question"]
    it2 = gw.land_items([land_row(sensitive=True, opt_in_recorded=True)], NOW)[0]
    assert "needs Shane opt-in" not in it2["what"]


def derived(now=NOW):
    return gw.land_items([land_row()], now)


def sens_derived(now=NOW):
    return gw.land_items([land_row(sensitive=True)], now)


def test_overdue_boundary():
    _, recs = gw.evaluate({}, sens_derived(), True, True, NOW)
    items, _ = gw.evaluate(recs, sens_derived(), True, True, NOW + 60 * 60)          # exactly 60 min: not overdue
    assert not items[0]["overdue"]
    items, _ = gw.evaluate(recs, sens_derived(), True, True, NOW + 60 * 60 + 1)      # 60 min + 1s
    assert items[0]["overdue"]


def test_ask_resets_clock():
    _, recs = gw.evaluate({}, sens_derived(), True, True, NOW)
    later = NOW + 2 * 3600
    items, recs = gw.evaluate(recs, sens_derived(), True, True, later)
    assert items[0]["overdue"]
    recs["land:goal/x-1"]["last_asked_at"] = gw.iso(later)
    items, _ = gw.evaluate(recs, sens_derived(), True, True, later + 30 * 60)
    assert not items[0]["overdue"]
    items, _ = gw.evaluate(recs, sens_derived(), True, True, later + 61 * 60)
    assert items[0]["overdue"]


def test_resolved_items_vanish_and_derived_resolves_when_gone():
    manual = {"m1": {"id": "m1", "what": "w", "question": "q", "first_seen_at": gw.iso(NOW),
                     "resolved_at": gw.iso(NOW)}}
    items, _ = gw.evaluate(manual, [], True, True, NOW + 9999)
    assert items == []
    _, recs = gw.evaluate({}, derived(), True, True, NOW)
    items, recs2 = gw.evaluate(recs, [], True, True, NOW + 10)      # landed: sweep ok and branch gone
    assert items == [] and recs2["land:goal/x-1"]["resolved_at"]


def test_failed_source_read_does_not_resolve():
    _, recs = gw.evaluate({}, derived(), True, True, NOW)
    items, recs2 = gw.evaluate(recs, [], False, True, NOW + 10)     # sweep failed: keep the item
    assert len(items) == 1 and not recs2["land:goal/x-1"].get("resolved_at")


def test_manual_item_overdue_from_first_seen():
    recs = {"m1": {"id": "m1", "what": "w", "question": "q?", "first_seen_at": gw.iso(NOW)}}
    items, _ = gw.evaluate(recs, [], True, True, NOW + 3601)
    assert items[0]["overdue"] and items[0]["kind"] == "manual"


def test_render_block_text_and_empty():
    _, recs = gw.evaluate({}, sens_derived(), True, True, NOW)
    items, _ = gw.evaluate(recs, sens_derived(), True, True, NOW + 7200)
    block = gw.render_block(items)
    assert block.startswith("RE-ASK SHANE NOW") and "PushNotification" in block and "AskUserQuestion" in block
    assert "gate-watch.py ask <id>" in block and "Ask: " in block
    items, _ = gw.evaluate(recs, sens_derived(), True, True, NOW + 5)
    assert gw.render_block(items) == ""


def test_shift_item():
    svc = {"status": {"latestCreatedRevisionName": "r2", "traffic": [{"revisionName": "r1", "percent": 100}]}}
    created = gw.iso(NOW - 31 * 60)
    assert gw.shift_item(svc, created, NOW)["id"] == "shift:r2"
    assert gw.shift_item(svc, gw.iso(NOW - 10 * 60), NOW) is None            # inside the grace
    svc["status"]["traffic"][0]["revisionName"] = "r2"
    assert gw.shift_item(svc, created, NOW) is None                          # already shifted
    assert gw.shift_item({}, created, NOW) is None                           # garbage: fail open


# ---- false-ask fix (8 Oct 2026): land is the lander's job, never a Shane ask ----
def test_passed_land_is_orchestrator_todo_never_reask_shane():
    _, recs = gw.evaluate({}, derived(), True, True, NOW)
    items, _ = gw.evaluate(recs, derived(), True, True, NOW + 7200)
    assert items[0]["audience"] == "orchestrator" and not items[0]["overdue"] and items[0]["todo_due"]
    block = gw.render_block(items)
    assert "RE-ASK SHANE" not in block and block.startswith("ORCHESTRATOR TO-DO")
    assert "Land it now (yes/hold)" not in block


def test_opted_in_sensitive_land_is_also_orchestrator():
    it = gw.land_items([land_row(sensitive=True, opt_in_recorded=True)], NOW)[0]
    assert it["audience"] == "orchestrator"


def test_sensitive_without_opt_in_stays_shane_ask():
    _, recs = gw.evaluate({}, sens_derived(), True, True, NOW)
    items, _ = gw.evaluate(recs, sens_derived(), True, True, NOW + 7200)
    assert items[0]["audience"] == "shane" and items[0]["overdue"]
    assert gw.render_block(items).startswith("RE-ASK SHANE NOW")


def test_legacy_land_row_without_audience_is_orchestrator():
    recs = {"land:goal/x-9": {"id": "land:goal/x-9", "kind": "land", "derived": True,
                              "what": "goal/x-9 (B) review PASS at abc, not landed",
                              "first_seen_at": gw.iso(NOW)}}
    items, _ = gw.evaluate(recs, [], False, False, NOW + 7200)
    assert items[0]["audience"] == "orchestrator" and not items[0]["overdue"]


def test_tip_on_main_auto_resolves_even_if_sweep_still_lists_it():
    _, recs = gw.evaluate({}, sens_derived(), True, True, NOW)
    items, recs2 = gw.evaluate(recs, sens_derived(), True, True, NOW + 7200, is_landed=lambda t: t == "abc1234")
    assert items == []
    r = recs2["land:goal/x-1"]
    assert r["resolved_at"] and "ancestor of origin/main" in r["resolved_reason"]
    items, _ = gw.evaluate(recs2, sens_derived(), True, True, NOW + 9000, is_landed=lambda t: True)
    assert items == []                                   # stale cache re-listing it does not reopen


def test_landed_check_failure_keeps_item():
    _, recs = gw.evaluate({}, sens_derived(), True, True, NOW)
    items, _ = gw.evaluate(recs, sens_derived(), True, True, NOW + 10, is_landed=lambda t: False)
    assert len(items) == 1


def test_manual_resolve_sticks_until_new_tip():
    _, recs = gw.evaluate({}, derived(), True, True, NOW)
    recs["land:goal/x-1"]["resolved_at"] = gw.iso(NOW + 1)
    items, recs = gw.evaluate(recs, derived(), True, True, NOW + 100)
    assert items == []                                   # same tip: stays resolved
    newtip = gw.land_items([land_row(tip="def5678")], NOW)
    items, _ = gw.evaluate(recs, newtip, True, True, NOW + 200)
    assert [i["id"] for i in items] == ["land:goal/x-1"]  # new push reopens


def shift_d(rev, now=NOW):
    return {"id": f"shift:{rev}", "kind": "shift", "audience": "shane", "rev": rev, "what": "w",
            "question": "q", "waiting_since": gw.iso(now)}


def test_shift_superseded_and_serving_auto_resolve():
    recs = {"shift:r1": {"id": "shift:r1", "kind": "shift", "first_seen_at": gw.iso(NOW)},      # manual-ish row
            "shift:r2": {"id": "shift:r2", "kind": "shift", "first_seen_at": gw.iso(NOW)}}
    items, recs2 = gw.evaluate(recs, [shift_d("r3")], True, True, NOW + 7200,
                               shift_state={"latest": "r3", "serving": "r1"})
    assert [i["id"] for i in items] == ["shift:r3"]
    assert "already serving" in recs2["shift:r1"]["resolved_reason"]
    assert "superseded by r3" in recs2["shift:r2"]["resolved_reason"]


def test_resolved_shift_stays_resolved():
    recs = {"shift:r3": {"id": "shift:r3", "kind": "shift", "derived": True, "first_seen_at": gw.iso(NOW),
                         "resolved_at": gw.iso(NOW + 1)}}
    items, _ = gw.evaluate(recs, [shift_d("r3")], True, True, NOW + 7200)
    assert items == []


def test_defer_silences_until_then(tmp_env):
    gw.cmd_mutate("add", "shift:r9", "w", "Shift?", now=NOW)
    assert gw.cmd_mutate("defer", "shift:r9", now=NOW, until="bad") == 2
    assert gw.cmd_mutate("defer", "shift:r9", now=NOW, until=gw.iso(NOW + 5 * 3600), reason="tonight") == 0
    gw.write_cache([], True, True, now=NOW)
    items = gw.compute(use_cache=True, now=NOW + 2 * 3600)
    assert items and not items[0]["overdue"]
    items = gw.compute(use_cache=True, now=NOW + 6 * 3600)
    assert items[0]["overdue"]


def test_resolve_records_reason(tmp_env):
    gw.cmd_mutate("add", "g2", "w", "q", now=NOW)
    gw.cmd_mutate("resolve", "g2", now=NOW + 1, reason="not Shane's")
    recs, _ = gw.read_ledger()
    assert recs["g2"]["resolved_reason"] == "not Shane's"


def test_tip_on_main_fails_closed_to_false(tmp_path):
    assert gw.tip_on_main("abc", repo=str(tmp_path)) is False   # not a repo
    assert gw.tip_on_main(None) is False


# ---- commands against a temp ledger ----
@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setattr(gw, "LEDGER", str(tmp_path / "L.jsonl"))
    monkeypatch.setattr(gw, "CACHE", str(tmp_path / "cache.json"))
    return tmp_path


def test_add_ask_resolve_cycle(tmp_env, capsys):
    assert gw.cmd_mutate("add", "g1", "thing", "Do it?", now=NOW) == 0
    recs, ok = gw.read_ledger()
    assert ok and recs["g1"]["what"] == "thing"
    gw.write_cache([], True, True, now=NOW)
    items = gw.compute(use_cache=True, now=NOW + 7200)
    assert items[0]["overdue"]
    gw.cmd_mutate("ask", "g1", now=NOW + 7200)
    items = gw.compute(use_cache=True, now=NOW + 7200 + 60)
    assert not items[0]["overdue"]
    gw.cmd_mutate("resolve", "g1", now=NOW + 7300)
    assert gw.compute(use_cache=True, now=NOW + 99999) == []


def test_corrupt_ledger_fails_open_and_is_not_overwritten(tmp_env):
    open(gw.LEDGER, "w").write("{not json\n")
    gw.write_cache([], True, True, now=NOW)
    assert gw.compute(use_cache=True, now=NOW) == []
    assert open(gw.LEDGER).read() == "{not json\n"
    assert gw.cmd_mutate("ask", "g", now=NOW) == 2


# ---- hook ----
def run_hook(env_extra, payload):
    env = dict(os.environ, **env_extra)
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "gate-watch.py"), "--hook"],
                          input=payload, capture_output=True, text=True, timeout=20, env=env)


def seeded(tmp_path, role_exit):
    ledger = tmp_path / "L.jsonl"
    ledger.write_text(json.dumps({"id": "m1", "what": "w", "question": "q?", "first_seen_at": "2020-01-01T00:00:00Z",
                                  "resolved_at": None}) + "\n")
    role = tmp_path / "role.py"
    role.write_text(f"import sys\nsys.exit({role_exit})\n")
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"computed_at": time.time(), "items": [], "sweep_ok": True, "shift_ok": True}))
    return {"GATE_LEDGER": str(ledger), "GATE_WATCH_CACHE": str(cache), "GATE_WATCH_ROLE": str(role)}


def test_hook_injects_in_orchestrator_session(tmp_path):
    r = run_hook(seeded(tmp_path, 0), json.dumps({"session_id": "abc"}))
    out = json.loads(r.stdout)
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "UserPromptSubmit" and h["additionalContext"].startswith("RE-ASK SHANE NOW")


def test_hook_silent_outside_orchestrator_session(tmp_path):
    r = run_hook(seeded(tmp_path, 1), json.dumps({"session_id": "abc"}))
    assert r.stdout == "" and r.returncode == 0


@pytest.mark.parametrize("payload", ["", "not json", "{}", json.dumps({"session_id": ""})])
def test_hook_silent_on_bad_payload(tmp_path, payload):
    r = run_hook(seeded(tmp_path, 0), payload)
    assert r.stdout == "" and r.returncode == 0


def test_hook_silent_when_role_script_missing(tmp_path):
    env = seeded(tmp_path, 0)
    env["GATE_WATCH_ROLE"] = str(tmp_path / "nope.py")
    r = run_hook(env, json.dumps({"session_id": "abc"}))
    assert r.stdout == "" and r.returncode == 0


def test_hook_silent_when_nothing_overdue(tmp_path):
    env = seeded(tmp_path, 0)
    open(env["GATE_LEDGER"], "w").write("")
    r = run_hook(env, json.dumps({"session_id": "abc"}))
    assert r.stdout == "" and r.returncode == 0
