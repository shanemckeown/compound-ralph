"""LUCY-6v5gv (LUCY-8z3go harness bead A): check 5's Reuses: line, check 16 programme routing,
check 1 resume-not-sibling, and check 13 counting closed-bead branches.
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests/test_fleet_dispatch_programme.py -q"""
import importlib.util
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "fleet-dispatch.py"
spec = importlib.util.spec_from_file_location("fleet_dispatch_programme", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

NEW = "2026-10-16T09:00:00Z"
OLD = "2026-10-01T09:00:00Z"
BRIEF = ("Outcome: pressing Book confirms the slot.\nPremise: n/a — new capability, no prod state.\n"
         "Files: lib/booking/confirm.ts")


def brief(text="", created=NEW, comments=()):
    return {"id": "AestheticcNext-new1", "created_at": created, "description": BRIEF + "\n" + text,
            "acceptance_criteria": "npx jest tests/booking/confirm.test.ts",
            "comments": [{"text": t} for t in comments]}


EXISTS = {"lib/services/account-credit.ts", "~/.claude/scripts/fleet-dispatch.py"}


def exists(path):
    return path.split(":", 1)[0] in EXISTS


# ---- check 5: Reuses ---------------------------------------------------------------------
def test_new_bead_without_reuses_is_refused():
    problems = fd.check_bead_brief(brief(), exists)
    assert len(problems) == 1 and "'Reuses:' line" in problems[0]
    assert "Reuses: none — <why no existing path fits>" in problems[0]


@pytest.mark.parametrize("line", ["Reuses: lib/services/account-credit.ts:42 (add expiry column)",
                                  "**Reuses:** ~/.claude/scripts/fleet-dispatch.py:354",
                                  "Reuses: none — first send path for push notifications",
                                  "Reuses: n/a - harness only, no product code"])
def test_reuses_with_existing_path_or_reasoned_none_passes(line):
    assert fd.check_bead_brief(brief(line), exists) == []


def test_empty_reuses_line_does_not_borrow_the_next_line():
    for text in ("Reuses:\nFiles: ~/.claude/scripts/fleet-dispatch.py",
                 "Reuses: \n\nlib/services/account-credit.ts"):
        problems = fd.check_bead_brief(brief(text), exists)
        assert problems and "'Reuses:' line" in problems[0], text


def test_reuses_added_by_comment_counts_and_latest_wins():
    assert fd.check_bead_brief(brief(comments=["Reuses: lib/services/account-credit.ts"]), exists) == []
    stale = brief("Reuses: lib/services/account-credit.ts", comments=["Reuses: lib/gone/nope.ts"])
    assert "not found: lib/gone/nope.ts" in fd.check_bead_brief(stale, exists)[0]


@pytest.mark.parametrize("line,why", [("Reuses: none", "file path or 'none"),
                                      ("Reuses: the account credit table", "file path or 'none"),
                                      ("Reuses: lib/made/up.ts:10", "not found: lib/made/up.ts"),
                                      ("Reuses: TBD", "'Reuses:' line")])
def test_bare_none_prose_or_missing_path_refused(line, why):
    problems = fd.check_bead_brief(brief(line), exists)
    assert problems and why in problems[0], problems


def test_beads_filed_before_cutover_are_exempt_and_bad_dates_are_not():
    assert fd.check_bead_brief(brief(created=OLD), exists) == []
    assert "Reuses" in fd.check_bead_brief(brief(created="not-a-date"), exists)[0]


def test_real_path_resolver_reads_repo_vault_and_home():
    assert fd.reuse_path_exists("~/.claude/scripts/fleet-dispatch.py:1")
    assert fd.reuse_path_exists("Aestheticc/CLAUDE.md")
    assert not fd.reuse_path_exists("lib/definitely/not/here.ts")


# ---- check 16: programme routing ---------------------------------------------------------
ANITA = ["client:dr-anita-dhunna", "area:client-success"]
EPIC = {"id": "AestheticcNext-oxy7p", "title": "Anita voice programme"}


def rec(labels=ANITA, **kw):
    return dict({"id": "AestheticcNext-scr1", "labels": list(labels)}, **kw)


def branches(*rows):
    return lambda: [dict(branch=b, bead=bead, labels=labels) for b, bead, labels in rows]


def test_bead_sharing_an_open_epics_labels_is_routed_to_the_epic():
    problems, warning = fd.check_programme_route("AestheticcNext-scr1", rec(),
                                                 epics=lambda b, k: [EPIC], branches=branches())
    text = "\n".join(problems)
    assert warning is None
    assert "bd update AestheticcNext-scr1 --parent AestheticcNext-oxy7p" in text
    assert "fleet-dispatch.py AestheticcNext-oxy7p --epic" in text and "P0" in text


def test_bead_sharing_a_live_branchs_labels_is_routed_to_the_branch():
    live = branches(("goal/aestheticcnext-l2nko", "AestheticcNext-l2nko", ANITA + ["voice"]))
    problems, _ = fd.check_programme_route("AestheticcNext-scr1", rec(), epics=lambda b, k: [],
                                           branches=live)
    assert "commit on goal/aestheticcnext-l2nko" in problems[0]


def test_every_client_area_pair_is_checked():
    labels = ["client:aaa-other", "client:dr-anita-dhunna", "area:client-success"]
    asked = []

    def epics(b, key):
        asked.append(key)
        return [EPIC] if key == tuple(ANITA) else []
    problems, _ = fd.check_programme_route("AestheticcNext-scr1", rec(labels=labels), epics=epics,
                                           branches=branches())
    assert ("client:dr-anita-dhunna", "area:client-success") in asked and len(asked) == 2
    assert "fleet-dispatch.py AestheticcNext-oxy7p --epic" in problems[0]
    live = branches(("goal/aestheticcnext-l2nko", "AestheticcNext-l2nko", ANITA))
    problems, _ = fd.check_programme_route("AestheticcNext-scr1", rec(labels=labels),
                                           epics=lambda b, k: [], branches=live)
    assert "commit on goal/aestheticcnext-l2nko" in problems[0]


def test_no_sibling_passes():
    other = branches(("goal/aestheticcnext-x", "AestheticcNext-x", ["client:viso", "area:client-success"]),
                     ("goal/aestheticcnext-y", "AestheticcNext-y", ["client:dr-anita-dhunna", "area:finance"]))
    assert fd.check_programme_route("AestheticcNext-scr1", rec(), epics=lambda b, k: [],
                                    branches=other) == ([], None)


def test_own_branch_and_own_parent_are_not_siblings():
    own = branches(("goal/aestheticcnext-scr1", "AestheticcNext-scr1", ANITA))
    problems, _ = fd.check_programme_route("AestheticcNext-scr1", rec(parent="AestheticcNext-oxy7p"),
                                           epics=lambda b, k: [EPIC], branches=own)
    assert problems == []


def test_no_client_label_means_no_programme_and_no_lookup():
    def boom(*a):
        raise AssertionError("must not look anything up")
    assert fd.check_programme_route("x", rec(labels=["area:client-success"]), epics=boom,
                                    branches=boom) == ([], None)


def test_unreadable_sources_warn_not_refuse():
    def boom(*a):
        raise RuntimeError("bd down")
    problems, warning = fd.check_programme_route("x", rec(), epics=boom, branches=boom)
    assert problems == [] and "bd down" in warning and "not fully enforced" in warning


def test_epic_lookup_asks_bd_for_both_labels(monkeypatch):
    seen = {}

    def fake_sh(cmd, cwd=None, timeout=60, bead=None):
        seen["cmd"] = cmd
        return 0, '[{"id": "AestheticcNext-oxy7p"}]', ""
    monkeypatch.setattr(fd, "sh", fake_sh)
    assert fd.open_programme_epics("AestheticcNext-scr1", tuple(ANITA))[0]["id"] == "AestheticcNext-oxy7p"
    assert "--type epic" in seen["cmd"] and "closed" not in seen["cmd"]
    assert "--label 'client:dr-anita-dhunna' --label 'area:client-success'" in seen["cmd"]


def test_force_past_check_16_needs_p0_and_reason():
    failures = {fd.PROGRAMME_CHECK: ["x"]}
    assert "check 16" in fd.force_refusal(failures, True, "client down", priority=1)
    assert "check 16" in fd.force_refusal(failures, True, " ", priority=0)
    assert fd.force_refusal(failures, True, "prod voice down", priority=0) is None


def _gate(record, programme):
    stubs = {
        "check_bead_is_dispatchable": lambda b: [], "check_no_inflight_work": lambda b, **k: [],
        "check_dependencies": lambda b: [], "check_headless_eligible": lambda b, out=None: [],
        "check_bead_brief": lambda r: [], "check_client_triage_tree": lambda b, r=None: [],
        "check_premise_tree": lambda b, r=None: [], "load_bead_record": lambda b: (record, None),
        "check_resources": lambda snap=None: ({}, {}, {}),
        "check_credit_pace": lambda b, r=None: ([], None, None), "check_wip": lambda: ([], None, None),
        "check_programme_route": programme,
    }
    saved = {n: getattr(fd, n) for n in stubs}
    try:
        for n, f in stubs.items():
            setattr(fd, n, f)
        return fd.run_gate(record["id"], is_epic=False)
    finally:
        for n, f in saved.items():
            setattr(fd, n, f)


def test_run_gate_refuses_on_check_16_and_prints_warning_when_blind(capsys):
    failed, failures = _gate(rec(), lambda b, r: (["route it"], None))
    assert failed and fd.PROGRAMME_CHECK in failures
    failed, failures = _gate(rec(), lambda b, r: ([], "cannot scan"))
    assert not failed and "⚠ 16 programme routing — WARN (not refusing): cannot scan" in capsys.readouterr().out


# ---- check 1: resume, never sibling ------------------------------------------------------
def repo(worktrees="", branches_out="", landed=()):
    def sh(cmd, cwd=None, timeout=60, bead=None):
        if cmd.startswith("git worktree list"):
            return 0, worktrees, ""
        if cmd.startswith("git branch -a"):
            return 0, branches_out, ""
        if "merge-base --is-ancestor" in cmd:
            return (0 if any(l in cmd for l in landed) else 1), "", ""
        return 0, "", ""
    return sh


WT = "/r/.claude/worktrees/mbow6  abc123 [goal/aestheticcnext-mbow6]"
BR = ("  goal/aestheticcnext-mbow6\n  remotes/origin/goal/aestheticcnext-mbow6\n"
      "  remotes/origin/goal/aestheticcnext-mbow6x\n")


def test_unowned_branch_and_worktree_are_resumable(monkeypatch):
    monkeypatch.setattr(fd, "sh", repo(WT, BR))
    resume = []
    assert fd.check_no_inflight_work("AestheticcNext-mbow6", agents=[], resume=resume) == []
    assert resume[0] == "target goal/aestheticcnext-mbow6"
    assert "worktree /r/.claude/worktrees/mbow6" in resume
    assert "branch remotes/origin/goal/aestheticcnext-mbow6" in resume
    assert not any("mbow6x" in r for r in resume)


def test_live_session_on_the_bead_refuses(monkeypatch):
    monkeypatch.setattr(fd, "sh", repo(WT, BR))
    agents = [{"name": "AestheticcNext-mbow6", "state": "working", "cwd": "/r"}]
    problems = fd.check_no_inflight_work("AestheticcNext-mbow6", agents=agents)
    assert problems and "live session AestheticcNext-mbow6" in problems[0]


def test_target_is_the_newest_unlanded_sibling_under_its_own_name(monkeypatch):
    newest_first = ("  remotes/origin/goal/aestheticcnext-mbow6-r2\n  goal/aestheticcnext-mbow6\n"
                    "  remotes/origin/goal/aestheticcnext-mbow6\n")
    monkeypatch.setattr(fd, "sh", repo("", newest_first))
    resume = []
    assert fd.check_no_inflight_work("AestheticcNext-mbow6", agents=[], resume=resume) == []
    assert resume[0] == "target goal/aestheticcnext-mbow6-r2"


def test_target_prefers_origin_over_a_local_ref_tied_on_date(monkeypatch):
    # Live mbow6 shape: local goal/…-mbow6 points at -r2's tip, so it sorts first on a date tie.
    tied = ("  goal/aestheticcnext-mbow6\n  remotes/origin/goal/aestheticcnext-mbow6-r2\n"
            "  remotes/origin/goal/aestheticcnext-mbow6\n")
    monkeypatch.setattr(fd, "sh", repo("", tied))
    resume = []
    fd.check_no_inflight_work("AestheticcNext-mbow6", agents=[], resume=resume)
    assert resume[0] == "target goal/aestheticcnext-mbow6-r2"
    monkeypatch.setattr(fd, "sh", repo("", "  goal/aestheticcnext-mbow6\n"))  # local-only work
    resume = []
    fd.check_no_inflight_work("AestheticcNext-mbow6", agents=[], resume=resume)
    assert resume[0] == "target goal/aestheticcnext-mbow6"


def test_live_owner_cannot_be_forced():
    failures = {fd.INFLIGHT_CHECK: ["live session AestheticcNext-mbow6 is on this bead — branch x"]}
    for force, reason, prio in ((True, "P0 down", 0), (True, None, None), (False, None, None)):
        assert "live session" in fd.force_refusal(failures, force, reason, prio)
    unreadable = {fd.INFLIGHT_CHECK: ["branch exists (x) and `claude agents` is unreadable (t)"]}
    assert fd.force_refusal(unreadable, True, None) is None


def test_main_records_resume_target_on_the_bead(monkeypatch):
    calls = []
    monkeypatch.setattr(fd, "run_gate",
                        lambda b, e: (fd._GATE_STATE.update(resume_target="goal/aestheticcnext-mbow6-r2")
                                      or (False, {})))
    monkeypatch.setattr(fd, "sh", lambda cmd, **k: calls.append(cmd) or (0, "", ""))
    monkeypatch.setattr(fd, "dispatch", lambda *a: None)
    monkeypatch.setattr(fd, "write_manifest", lambda *a, **k: None)
    monkeypatch.setattr(fd.sys, "argv", ["fleet-dispatch.py", "AestheticcNext-mbow6", "--pre-claimed"])
    with pytest.raises(SystemExit):
        fd.main()
    assert any(c.startswith("bd comment AestheticcNext-mbow6 \"RESUME: goal/aestheticcnext-mbow6-r2")
               for c in calls), calls


@pytest.mark.parametrize("state", ["done", "idle"])
def test_finished_session_does_not_block_a_resume(monkeypatch, state):
    monkeypatch.setattr(fd, "sh", repo(WT, BR))
    agents = [{"name": "AestheticcNext-mbow6", "state": state}]
    assert fd.check_no_inflight_work("AestheticcNext-mbow6", agents=agents) == []


def test_unreadable_registry_fails_closed_only_when_work_exists(monkeypatch):
    def boom():
        raise RuntimeError("timeout")
    monkeypatch.setattr(fd, "probe_agents", boom)
    monkeypatch.setattr(fd, "sh", repo(WT, BR))
    assert "unreadable" in fd.check_no_inflight_work("AestheticcNext-mbow6")[0]
    monkeypatch.setattr(fd, "sh", repo())
    assert fd.check_no_inflight_work("AestheticcNext-mbow6") == []


def test_landed_branch_is_neither_work_nor_resume(monkeypatch):
    monkeypatch.setattr(fd, "sh", repo("", "  remotes/origin/goal/aestheticcnext-mbow6\n",
                                       landed=("aestheticcnext-mbow6",)))
    resume = []
    assert fd.check_no_inflight_work("AestheticcNext-mbow6", agents=[], resume=resume) == []
    assert resume == []


def test_epic_sees_a_live_child_session_but_single_bead_ignores_child_branches(monkeypatch):
    monkeypatch.setattr(fd, "sh", repo("", "  remotes/origin/goal/aestheticcnext-oxy7p.3\n"))
    child = [{"name": "AestheticcNext-oxy7p.3", "state": "working"}]
    assert fd.check_no_inflight_work("AestheticcNext-oxy7p", agents=child, is_epic=True)
    resume = []
    assert fd.check_no_inflight_work("AestheticcNext-oxy7p", agents=child, resume=resume) == []
    assert resume == []


def test_run_gate_prints_the_branch_to_resume(monkeypatch, capsys):
    monkeypatch.setattr(fd, "sh", repo("", "  remotes/origin/goal/aestheticcnext-mbow6\n"))
    monkeypatch.setattr(fd, "probe_agents", lambda: [])
    for name in ("check_bead_is_dispatchable", "check_dependencies", "check_epic_has_children",
                 "check_client_triage_tree", "check_premise_tree"):
        monkeypatch.setattr(fd, name, lambda b, **k: [])
    monkeypatch.setattr(fd, "check_resources", lambda: ({}, {}, {}))
    monkeypatch.setattr(fd, "check_credit_pace", lambda b, r=None: ([], None, None))
    monkeypatch.setattr(fd, "check_wip", lambda: ([], None, None))
    fd.run_gate("AestheticcNext-mbow6", is_epic=True)
    out = capsys.readouterr().out
    assert ("resume, don't sibling (no live session): target goal/aestheticcnext-mbow6; "
            "branch remotes/origin/goal/aestheticcnext-mbow6") in out
    assert fd._GATE_STATE["resume_target"] == "goal/aestheticcnext-mbow6"


# ---- check 13: closed beads count unless their landing is recorded -------------------------
@pytest.fixture
def pile(monkeypatch):
    refs = {"goal/aestheticcnext-open1": "a", "goal/aestheticcnext-closed1": "b",
            "goal/aestheticcnext-closed2": "c", "goal/aestheticcnext-closed3": "d",
            "long-goal/aestheticcnext-epic1": "e"}
    beads = {
        "open1": ("AestheticcNext-open1", "open", {"id": "AestheticcNext-open1", "labels": ANITA}),
        "closed1": ("AestheticcNext-closed1", "closed", {"id": "AestheticcNext-closed1",
                                                         "close_reason": "done, QA clean"}),
        "closed2": ("AestheticcNext-closed2", "closed", {"id": "AestheticcNext-closed2",
                                                         "close_reason": "LANDED @abc1234def"}),
        "closed3": ("AestheticcNext-closed3", "closed", {"id": "AestheticcNext-closed3"}),
        "epic1": ("AestheticcNext-epic1", "pushed", {"id": "AestheticcNext-epic1"}),
    }
    comments = {"AestheticcNext-closed3": [{"text": "SUPERSEDED by goal/aestheticcnext-open1"}]}

    def sh(cmd, cwd=None, timeout=60, bead=None):
        if "for-each-ref" in cmd:
            assert "refs/remotes/origin/goal" in cmd and "refs/remotes/origin/long-goal" in cmd
            return 0, "\n".join(f"origin/{b}\t{t * 40}\t100" for b, t in refs.items()), ""
        return 1, "", ""  # nothing is an ancestor of origin/main
    us = types.SimpleNamespace(land_commits=lambda: [], landed_elsewhere=lambda *a: None,
                               sha_landed_in_text=lambda text, key: None, bead_text=lambda r: "")
    monkeypatch.setattr(fd, "sh", sh)
    monkeypatch.setattr(fd, "wip_bead_statuses", lambda: beads)
    monkeypatch.setattr(fd, "_load_unlanded_sweep", lambda: us)
    monkeypatch.setattr(fd, "load_bead_record",
                        lambda b: ({"id": b, "comments": comments.get(b, [])}, None))


def test_closed_bead_branch_counts_unless_landing_recorded(pile):
    got = {w["branch"]: w for w in fd.probe_wip_branches(fetch=False)}
    assert set(got) == {"goal/aestheticcnext-open1", "goal/aestheticcnext-closed1",
                        "long-goal/aestheticcnext-epic1"}
    assert got["goal/aestheticcnext-open1"]["labels"] == ANITA


def test_bead_list_covers_every_status(monkeypatch):
    seen = []

    def sh(cmd, cwd=None, timeout=60, bead=None):
        seen.append(cmd)
        return 0, "[]", ""
    monkeypatch.setattr(fd, "sh", sh)
    fd.wip_bead_statuses()
    statuses = " ".join(seen)
    for s in ("open", "in_progress", "blocked", "pushed", "closed"):
        assert s in statuses


def test_checks_13_and_16_share_one_probe(monkeypatch):
    calls = []
    monkeypatch.setattr(fd, "probe_wip_branches", lambda fetch=True: calls.append(1) or [])
    fd._WIP_MEMO.clear()
    fd.unlanded_branches()
    fd.unlanded_branches()
    assert calls == [1]
    fd._WIP_MEMO.clear()
