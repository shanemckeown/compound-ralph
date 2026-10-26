"""builder-remote-qa.py (LUCY-ll8dh): builder heavy runs on Cloud Build, gcloud and runner faked.

No test here submits a real build or runs a real local lane: gcloud, the runner, the local lane
and the scratch are all injected. The question every test asks: can a red or untested tree read
as green (exit 0)?
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests/test_builder_remote_qa.py -q
"""
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "builder-remote-qa.py"
spec = importlib.util.spec_from_file_location("builder_remote_qa", SCRIPT)
bq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bq)

GREEN_OUT = ("LANE unit build u-123 https://log/u\nLANE pg build p-456 https://log/p\n"
             "RESULT unit=SUCCESS pg=SUCCESS exit 0\n")
RED_OUT = ("LANE unit build u-1 https://log/u\nLANE pg build p-2 https://log/p\n"
           "RESULT unit=FAILURE pg=SUCCESS exit 1\n"
           "FAILED LANE unit: tests/unit/zz-red-ll8dh.test.ts\n")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / "a.txt").write_text("committed\n")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=root, check=True)
    return root


class Gcloud:
    """Scripted gcloud: a quota read and a sequence of `builds list --ongoing` counts."""

    def __init__(self, ongoing=(0,), limit=(0, "10\n", ""), list_code=0):
        self.ongoing, self.limit, self.list_code = list(ongoing), limit, list_code
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:3] == ["beta", "quotas", "preferences"]:
            return self.limit
        if args[:2] == ["builds", "list"]:
            if self.list_code:
                return self.list_code, "", "ERROR: (gcloud.builds.list) reauth needed"
            n = self.ongoing.pop(0) if len(self.ongoing) > 1 else self.ongoing[0]
            return 0, "".join(f"b{i}\n" for i in range(n)), ""
        raise AssertionError(f"unexpected gcloud call {args}")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run(repo, tmp_path, *, gcloud=None, remote=None, local=None, env=None, argv=(), clock=None):
    gcloud = gcloud or Gcloud()
    clock = clock or Clock()
    calls = {"remote": 0, "local": 0}
    scratch = tmp_path / "scratch"

    def scratch_maker(sha, worktree):
        scratch.mkdir(exist_ok=True)
        return scratch

    def fake_remote(s, say, child):
        calls["remote"] += 1
        if callable(remote) and not isinstance(remote, tuple):
            return remote(s, say, child)
        return remote

    def fake_local(s, worktree, say, child):
        calls["local"] += 1
        if callable(local):
            return local(s, worktree, say, child)
        return local

    log = tmp_path / "log.jsonl"
    env = {"BUILDER_QA_LOG": str(log), "BUILDER_QA_RESERVATIONS": str(tmp_path / "res.json"),
           **(env or {})}
    out = io.StringIO()
    code = bq.main(list(argv), gcloud=gcloud, remote=fake_remote, local=fake_local,
                   scratch_maker=scratch_maker, sleep=clock.sleep, clock=clock.clock, out=out,
                   env=env, cwd=repo)
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return code, out.getvalue(), calls, gcloud, records, clock


def head(repo):
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_remote_green_is_zero_and_names_the_sha(repo, tmp_path):
    code, out, calls, _, records, _ = run(repo, tmp_path, remote=(0, GREEN_OUT), local=(0, None))
    assert code == 0
    assert f"REMOTE GREEN @{head(repo)}" in out
    assert calls == {"remote": 1, "local": 0}
    assert records[-1]["verdict"] == "remote-green" and records[-1]["sha"] == head(repo)
    assert records[-1]["build_ids"] == {"unit": "u-123", "pg": "p-456"}
    for key in ("wall_s", "queue_wait_s", "mode", "exit"):
        assert key in records[-1]


def test_remote_red_is_one_names_the_suite_and_never_falls_back(repo, tmp_path):
    code, out, calls, _, records, _ = run(repo, tmp_path, remote=(1, RED_OUT), local=(0, None))
    assert code == 1
    assert f"REMOTE RED @{head(repo)}" in out
    assert "FAILED LANE unit: tests/unit/zz-red-ll8dh.test.ts" in out.split("REMOTE RED")[1]
    assert calls["local"] == 0
    assert records[-1]["verdict"] == "remote-red"


def raises(exc):
    def remote(*_):
        raise exc
    return remote


INFRA = [
    pytest.param((2, "REFUSED x\n"), id="refused-2"),
    pytest.param((137, "RESULT unit=TIMEOUT pg=SUCCESS exit 137\n"), id="infra-137"),
    pytest.param((127, ""), id="uv-missing-127"),
    pytest.param((1, "error: Failed to fetch pyyaml\n"), id="exit1-no-failed-lane"),
    pytest.param((0, "LANE unit build u\n"), id="exit0-no-result-line"),
    pytest.param((-15, ""), id="killed-by-signal"),
    pytest.param((3, ""), id="other-exit"),
    pytest.param(raises(FileNotFoundError("uv")), id="uv-not-on-path"),
    pytest.param(raises(RuntimeError("boom")), id="exception"),
]


@pytest.mark.parametrize("remote", INFRA)
@pytest.mark.parametrize("local_code", [0, 1])
def test_every_infra_outcome_falls_back_local_and_local_is_the_verdict(repo, tmp_path, remote, local_code):
    local = (local_code, None if local_code == 0 else "full pg lane")
    code, out, calls, _, records, _ = run(repo, tmp_path, remote=remote, local=local)
    assert calls["local"] == 1
    assert "FALLBACK local: " in out
    assert "REMOTE GREEN" not in out and "REMOTE RED" not in out
    assert code == local_code
    assert records[-1]["mode"] == "fallback"
    assert records[-1]["verdict"] == ("local-green" if local_code == 0 else "local-red")


def test_admission_over_cap_waits_then_falls_back_local(repo, tmp_path):
    gcloud = Gcloud(ongoing=(5,))  # limit 10 -> cap 6; 5 + 2 > 6
    code, out, calls, gcloud, records, clock = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT),
                                                   local=(0, None), argv=["--admit-wait", "120", "--poll", "30"])
    assert calls == {"remote": 0, "local": 1}
    assert clock.sleeps == [30, 30, 30, 30]
    assert "QUEUE: 5 ongoing + 0 pending + 2 > cap 6" in out
    assert "FALLBACK local: admission: 5 ongoing + 0 pending + 2 > cap 6" in out
    assert records[-1]["queue_wait_s"] == 120
    assert code == 0 and "LOCAL GREEN" in out


def test_admission_frees_up_then_submits(repo, tmp_path):
    gcloud = Gcloud(ongoing=(6, 5, 4))
    code, out, calls, _, records, clock = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT),
                                              local=(0, None))
    assert calls == {"remote": 1, "local": 0}
    assert clock.sleeps == [30, 30]
    assert records[-1]["queue_wait_s"] == 60
    assert code == 0


def test_builds_list_failure_falls_back_without_submitting(repo, tmp_path):
    code, out, calls, _, _, _ = run(repo, tmp_path, gcloud=Gcloud(list_code=1), remote=(0, GREEN_OUT),
                                    local=(1, "node + dom jest"))
    assert calls == {"remote": 0, "local": 1}
    assert "FALLBACK local: gcloud builds list failed" in out
    assert code == 1


def test_builder_qa_remote_0_never_calls_gcloud(repo, tmp_path):
    gcloud = Gcloud()
    code, out, calls, gcloud, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT),
                                               local=(0, None), env={"BUILDER_QA_REMOTE": "0"})
    assert gcloud.calls == []
    assert calls == {"remote": 0, "local": 1}
    assert records[-1]["mode"] == "local" and code == 0 and "LOCAL GREEN" in out


@pytest.mark.parametrize("value,expected", [("", True), ("1", True), ("on", True), ("0", False),
                                            ("off", False), ("FALSE", False), ("maybe", True)])
def test_qa_remote_env_pattern(value, expected):
    assert bq.qa_remote({"BUILDER_QA_REMOTE": value}) is expected


@pytest.mark.parametrize("result,limit", [
    ((1, "", "ERROR: not found"), 10),
    ((127, "", "no gcloud"), 10),
    ((0, "", ""), 10),
    ((0, "ten\n", ""), 10),
    ((0, "0\n", ""), 10),
    ((0, "20\n", ""), 20),
    ((0, "'20'\n", ""), 20),
])
def test_parse_limit(result, limit):
    assert bq.parse_limit(result)[0] == limit


def test_cap_today_is_6_and_20_gives_16():
    assert bq.builder_cap(10, {})[0] == 6
    assert bq.builder_cap(20, {})[0] == 16
    assert bq.builder_cap(20, {"BUILDER_QA_RESERVE": "6"})[0] == 14


def test_cap_override_wins():
    assert bq.builder_cap(20, {"BUILDER_QA_MAX_INFLIGHT": "4"})[0] == 4
    assert bq.builder_cap(20, {"BUILDER_QA_MAX_INFLIGHT": "4"}, override="8")[0] == 8


def test_quota_read_failure_means_limit_10_cap_6_end_to_end(repo, tmp_path):
    gcloud = Gcloud(ongoing=(5,), limit=(1, "", "ERROR: quota api"))
    code, out, calls, _, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT),
                                          local=(1, "full pg lane"), argv=["--admit-wait", "0"])
    assert records[-1]["limit"] == 10 and records[-1]["cap"] == 6
    assert calls["remote"] == 0 and code == 1


def test_granted_20_admits_where_10_would_not(repo, tmp_path):
    gcloud = Gcloud(ongoing=(8,), limit=(0, "20\n", ""))
    code, _, calls, _, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT),
                                        local=(1, "x"), argv=["--admit-wait", "0"])
    assert records[-1]["cap"] == 16 and calls["remote"] == 1 and code == 0


def test_quota_read_once_per_run(repo, tmp_path):
    gcloud = Gcloud(ongoing=(6, 6, 0))
    run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT), local=(0, None))
    assert sum(1 for c in gcloud.calls if c[0] == "beta") == 1


def test_env_override_beats_granted_limit_end_to_end(repo, tmp_path):
    gcloud = Gcloud(ongoing=(3,), limit=(0, "20\n", ""))
    _, _, calls, _, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT), local=(1, "x"),
                                     env={"BUILDER_QA_MAX_INFLIGHT": "4"}, argv=["--admit-wait", "0"])
    assert records[-1]["cap"] == 4 and calls["remote"] == 0


@pytest.mark.parametrize("local", [(0, "npm run build"), (0, "node_modules")])
def test_local_zero_with_a_failed_step_is_never_green(repo, tmp_path, local):
    code, out, _, _, _, _ = run(repo, tmp_path, remote=(137, ""), local=local)
    assert code != 0 and "LOCAL GREEN" not in out


def test_local_crash_is_never_green(repo, tmp_path):
    code, out, _, _, records, _ = run(repo, tmp_path, remote=(2, ""), local=raises(OSError("npm missing")))
    assert code != 0 and records[-1]["verdict"] == "error"


def test_bad_sha_is_never_green(repo, tmp_path):
    code, out, calls, _, _, _ = run(repo, tmp_path, remote=(0, GREEN_OUT), local=(0, None),
                                    argv=["--sha", "deadbeef"])
    assert code != 0 and calls == {"remote": 0, "local": 0}


def test_dirty_worktree_note_and_committed_sha_only(repo, tmp_path):
    (repo / "a.txt").write_text("dirty\n")
    code, out, _, _, records, _ = run(repo, tmp_path, remote=(0, GREEN_OUT), local=(0, None))
    assert "tests the committed sha" in out and records[-1]["dirty"] is True and code == 0


def test_scratch_is_the_committed_tree_not_the_worktree(repo):
    (repo / "a.txt").write_text("dirty\n")
    (repo / "untracked.txt").write_text("x\n")
    scratch = bq.make_scratch(head(repo), repo)
    try:
        assert (scratch / "a.txt").read_text() == "committed\n"
        assert not (scratch / "untracked.txt").exists()
    finally:
        import shutil
        shutil.rmtree(scratch)


def test_scratch_removed_after_run(repo, tmp_path):
    run(repo, tmp_path, remote=(0, GREEN_OUT), local=(0, None))
    assert not (tmp_path / "scratch").exists()


def test_remote_verdict_needs_its_evidence_line():
    assert bq.remote_verdict(0, GREEN_OUT)[0] == "green"
    assert bq.remote_verdict(0, "")[0] == "infra"
    assert bq.remote_verdict(1, RED_OUT)[0] == "red"
    assert bq.remote_verdict(1, "RESULT unit=FAILURE pg=SUCCESS exit 1\n")[0] == "infra"
    assert bq.remote_verdict(137, GREEN_OUT)[0] == "infra"


def test_local_steps_pg_lane_is_locked_and_node_lane_drops_pg_env():
    steps = {label: (cmd, over, rem) for label, cmd, over, rem in bq.local_steps()}
    node_cmd, node_over, node_rem = steps["node + dom jest"]
    assert "RUN_PG_TESTS" in node_rem
    pg_cmd, pg_over, pg_rem = steps["full pg lane"]
    assert pg_cmd[1].endswith("pg-lane-lock.py") and pg_cmd[2] == "--"
    assert pg_over["RUN_PG_TESTS"] == "true" and "SKIP_PG_TESTS" in pg_rem
    assert steps["npm run build"][0] == ["npm", "run", "build"]
    env = bq.step_env({"RUN_PG_TESTS": "true", "KEEP": "1"}, node_over, node_rem)
    assert env == {"KEEP": "1"}


def test_run_local_stops_at_first_red(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(bq, "link_node_modules", lambda s, w: tmp_path)
    monkeypatch.setattr(bq, "stream", lambda cmd, cwd, env, say, child, grace=0: (ran.append(cmd) or (1, "")))
    code, failed = bq.run_local(tmp_path, tmp_path, lambda _: None, bq.Child())
    assert (code, failed) == (1, "tsc-heavy") and len(ran) == 1


def test_run_local_without_node_modules_is_unavailable(tmp_path):
    code, failed = bq.run_local(tmp_path, tmp_path, lambda _: None, bq.Child())
    assert code == bq.EXIT_LOCAL_UNAVAILABLE and failed == "node_modules"


# ---------------------------------------------------------------- round 1 (Astra xhigh) fixes

FIXTURE_GATE = Path(__file__).resolve().parent / "fixtures" / "cloudbuild-gate.9be1ffa6f8.yaml"
LIVE_GATE = Path.home() / "Documents" / "GitReBase" / "AestheticcNext" / "cloudbuild-gate.yaml"


def gate_step_ids(path):
    return set(re.findall(r'^\s*id:\s*"([^"]+)"', path.read_text(), re.M))


def test_local_lane_covers_every_gate_step_of_the_fixture():
    ids = gate_step_ids(FIXTURE_GATE)
    assert ids == {"npm-ci", "unit-tests", "mobile-tests", "pg-tests", "lint-typecheck", "next-build"}
    assert bq.covered_gate_steps() == ids


@pytest.mark.skipif(not LIVE_GATE.exists(), reason="no AestheticcNext checkout on this machine")
def test_local_lane_covers_every_gate_step_of_the_live_config():
    """Fails when cloudbuild-gate.yaml gains a step the local fallback does not stand in for."""
    assert gate_step_ids(LIVE_GATE) <= bq.covered_gate_steps()


def test_local_lane_runs_tsc_lint_mobile_too():
    labels = [s[0] for s in bq.local_steps()]
    assert labels == ["tsc-heavy", "lint", "node + dom jest", "mobile jest", "full pg lane", "npm run build"]
    commands = {s[0]: s[1] for s in bq.local_steps()}
    assert commands["tsc-heavy"][-2:] == ["tsc-heavy", "--noEmit"]
    assert commands["lint"] == ["npm", "run", "lint"]
    assert commands["mobile jest"] == ["bash", "scripts/ci/run-mobile-tests.sh"]


@pytest.mark.parametrize("env,override,cap", [
    ({"BUILDER_QA_RESERVE": "0"}, None, 6),        # reserve clamped up to 4
    ({"BUILDER_QA_RESERVE": "2"}, None, 6),
    ({"BUILDER_QA_MAX_INFLIGHT": "10"}, None, 6),  # override above limit - 4 clamped
    ({}, "10", 6),
    ({}, "99", 6),
    ({"BUILDER_QA_MAX_INFLIGHT": "2"}, None, 2),   # lowering still works
    ({}, "0", 0),
])
def test_reserve_never_below_4_and_overrides_only_lower(env, override, cap):
    got, note = bq.builder_cap(10, env, override)
    assert got == cap
    if cap == 6:
        assert "clamped" in note


def test_quota_20_cap_16_and_override_cannot_exceed_it():
    assert bq.builder_cap(20, {}, "18")[0] == 16


def concurrent_admissions(tmp_path, n, base_ongoing, cap=6):
    path = tmp_path / "res.json"
    barrier = threading.Barrier(n)
    results = []
    lock = threading.Lock()

    def worker():
        res = bq.Reservations(path)
        barrier.wait()
        admitted, _, _ = res.try_reserve(lambda: base_ongoing, cap)
        with lock:
            results.append(admitted)
    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, path


@pytest.mark.parametrize("base,expected", [(0, 3), (2, 2), (4, 1), (5, 0)])
def test_concurrent_admissions_never_overshoot_the_cap(tmp_path, base, expected):
    """Seven builders at once, none of their builds visible yet: the reservations alone hold them."""
    results, path = concurrent_admissions(tmp_path, 7, base)
    assert results.count(True) == expected
    assert base + 2 * results.count(True) <= 6
    assert len(json.loads(path.read_text())) == expected


CHILD = r"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bq", sys.argv[1])
bq = importlib.util.module_from_spec(spec); spec.loader.exec_module(bq)
admitted, _, _ = bq.Reservations(sys.argv[2]).try_reserve(lambda: 0, 6)
print("ADMITTED" if admitted else "REFUSED", flush=True)
sys.stdin.read()  # stay alive (a live pid) until the test closes stdin
"""


def test_concurrent_admissions_across_processes(tmp_path):
    path = tmp_path / "res.json"
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(SCRIPT), str(path)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) for _ in range(6)]
    try:
        answers = [p.stdout.readline().strip() for p in procs]
    finally:
        for p in procs:
            p.stdin.close()
            p.wait(timeout=30)
    assert answers.count("ADMITTED") == 3 and answers.count("REFUSED") == 3


def test_dead_and_old_reservations_are_dropped(tmp_path):
    path = tmp_path / "res.json"
    path.write_text(json.dumps([{"pid": 99999999, "at": time.time(), "token": "dead"},
                                {"pid": os.getpid(), "at": time.time() - 99999, "token": "old"},
                                {"pid": os.getpid(), "at": time.time(), "token": "live"}]))
    admitted, _, pending = bq.Reservations(path).try_reserve(lambda: 0, 6)
    assert (admitted, pending) == (True, 2)
    tokens = [e["token"] for e in json.loads(path.read_text())]
    assert "dead" not in tokens and "old" not in tokens and len(tokens) == 2


def test_failed_count_reserves_nothing(tmp_path):
    path = tmp_path / "res.json"
    assert bq.Reservations(path).try_reserve(lambda: None, 6) == (None, None, None)
    assert json.loads(path.read_text()) == []


def test_reservation_released_once_both_build_ids_print_and_on_exit(repo, tmp_path):
    seen = {}

    def remote(scratch, say, child):
        say("LANE unit build u-1 https://x")
        seen["after_one"] = json.loads((tmp_path / "res.json").read_text())
        say("LANE pg build p-2 https://x")
        seen["after_two"] = json.loads((tmp_path / "res.json").read_text())
        return 0, "RESULT unit=SUCCESS pg=SUCCESS exit 0\n"
    code, _, _, _, records, _ = run(repo, tmp_path, remote=remote, local=(1, "x"))
    assert len(seen["after_one"]) == 1 and seen["after_two"] == []
    assert code == 0 and records[-1]["build_ids"] == {"unit": "u-1", "pg": "p-2"}
    assert json.loads((tmp_path / "res.json").read_text()) == []


def test_reservation_released_when_remote_infra_falls_back(repo, tmp_path):
    run(repo, tmp_path, remote=(137, ""), local=(1, "x"))
    assert json.loads((tmp_path / "res.json").read_text()) == []


class SignalAt(Gcloud):
    """Delivers a real SIGTERM to this process inside the named gcloud call."""

    def __init__(self, at, **kw):
        super().__init__(**kw)
        self.at = at

    def __call__(self, args):
        if args[0] == self.at:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)  # the handler fires here
        return super().__call__(args)


@pytest.mark.parametrize("at", ["beta", "builds"])
def test_signal_during_quota_read_or_admission_poll_exits_130(repo, tmp_path, at):
    code, out, calls, _, records, _ = run(repo, tmp_path, gcloud=SignalAt(at), remote=(0, GREEN_OUT),
                                          local=(0, None))
    assert code == 130
    assert calls == {"remote": 0, "local": 0}
    assert "INTERRUPTED" in out and "FALLBACK" not in out
    assert records[-1]["verdict"] == "interrupted"
    res = tmp_path / "res.json"
    assert not res.exists() or json.loads(res.read_text()) == []  # nothing left reserved


def test_interrupted_is_not_an_exception():
    assert not issubclass(bq.Interrupted, Exception)


def test_interrupt_after_build_ids_keeps_mode_and_ids_in_evidence(repo, tmp_path):
    def remote(scratch, say, child):
        say("LANE unit build u-9 https://x")
        say("LANE pg build p-9 https://x")
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(1)
        return 0, GREEN_OUT
    code, _, calls, _, records, _ = run(repo, tmp_path, remote=remote, local=(0, None))
    assert code == 130 and calls["local"] == 0
    assert records[-1]["mode"] == "remote"
    assert records[-1]["build_ids"] == {"unit": "u-9", "pg": "p-9"}


@pytest.mark.parametrize("local,label", [((3, "full pg lane"), "LOCAL INFRA"), ((127, "lint"), "LOCAL INFRA"),
                                         ((3, "node_modules"), "LOCAL INFRA"), ((1, "lint"), "LOCAL RED")])
def test_local_infra_is_labelled_infra_and_nonzero(repo, tmp_path, local, label):
    code, out, _, _, records, _ = run(repo, tmp_path, remote=(137, ""), local=local)
    assert code != 0 and f"{label} @" in out
    assert records[-1]["verdict"] == ("local-infra" if label == "LOCAL INFRA" else "local-red")


def test_stop_child_kills_the_group_and_cancels_printed_builds():
    proc = subprocess.Popen([sys.executable, "-c",
                             "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                             "print('up', flush=True); time.sleep(60)"],
                            stdout=subprocess.PIPE, text=True, start_new_session=True)
    proc.stdout.readline()
    child = bq.Child()
    child.proc, child.grace = proc, 0.5
    child.build_ids.update({"unit": "u-1", "pg": "p-2"})
    calls = []

    def gcloud(args):
        calls.append(args)
        return 0, "", ""
    bq.stop_child(child, gcloud, lambda _: None)
    assert proc.poll() is not None
    assert [c[:3] for c in calls] == [["builds", "cancel", "u-1"], ["builds", "cancel", "p-2"]]


def test_stop_child_waits_for_a_runner_that_exits_on_sigterm():
    proc = subprocess.Popen([sys.executable, "-c", "import time; print('up', flush=True); time.sleep(60)"],
                            stdout=subprocess.PIPE, text=True, start_new_session=True)
    proc.stdout.readline()
    child = bq.Child()
    child.proc = proc
    calls = []
    bq.stop_child(child, lambda a: calls.append(a) or (0, "", ""), lambda _: None)
    assert proc.poll() is not None and calls == []


def test_kill_grace_exceeds_the_runners_signal_bound():
    spec2 = importlib.util.spec_from_file_location("gr", SCRIPT.parent / "cloudbuild-gate-remote.py")
    gr = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(gr)
    assert bq.KILL_GRACE > gr.SIGNAL_JOIN_BOUND + 60
