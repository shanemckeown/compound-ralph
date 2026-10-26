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

    def __init__(self, ongoing=(0,), limit=(0, "10\n", ""), list_code=0, statuses=None):
        self.ongoing, self.limit, self.list_code = list(ongoing), limit, list_code
        self.statuses = statuses or {}
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[:3] == ["beta", "quotas", "preferences"]:
            return self.limit
        if args[:2] == ["builds", "list"]:
            if self.list_code:
                return self.list_code, "", "ERROR: (gcloud.builds.list) reauth needed"
            n = self.ongoing.pop(0) if len(self.ongoing) > 1 else self.ongoing[0]
            ids = n if isinstance(n, (set, list, tuple)) else [f"b{i}" for i in range(n)]
            return 0, "".join(f"{i}\n" for i in ids), ""
        if args[:2] == ["builds", "describe"]:
            return 0, self.statuses.get(args[2], "WORKING") + "\n", ""
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

    def scratch_maker(sha, worktree, exact=False):
        if exact:
            calls["exact"] = True
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
    env = {"BUILDER_QA_LOG": str(log), "BUILDER_QA_SLOT_DIR": str(tmp_path / "slots"),
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
    slot = bq.try_acquire(tmp_path / "slots", 3)  # nothing left held
    assert slot is not None and slot.index == 0
    slot.release()


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


# ---------------------------------------------------------------- finding 6: archive != sha

def commit_file(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    subprocess.run(["git", "add", "-f", path], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", f"add {path}"], cwd=repo, check=True)


def add_submodule_gitlink(repo):
    subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"160000,{head(repo)},vendor/sub"],
                   cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "gitlink"], cwd=repo, check=True)


TRIGGERS = [
    pytest.param(lambda r: commit_file(r, ".gitattributes", "tests/red.test.ts export-ignore\n"),
                 ".gitattributes sets export-ignore", id="export-ignore"),
    pytest.param(lambda r: commit_file(r, ".gitattributes", "VERSION export-subst\n"),
                 ".gitattributes sets export-subst", id="export-subst"),
    pytest.param(lambda r: commit_file(r, "assets/.gitattributes", "*.bin filter=lfs diff=lfs\n"),
                 "assets/.gitattributes sets filter=lfs", id="lfs-nested"),
    pytest.param(lambda r: commit_file(r, ".gitmodules", '[submodule "x"]\n\tpath = x\n'),
                 ".gitmodules exists", id="gitmodules"),
    pytest.param(add_submodule_gitlink, "submodule vendor/sub", id="submodule-gitlink"),
]


@pytest.mark.parametrize("make,reason", TRIGGERS)
def test_archive_trigger_skips_remote_and_falls_back_on_an_exact_scratch(repo, tmp_path, make, reason):
    make(repo)
    gcloud = Gcloud()
    code, out, calls, gcloud, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT),
                                               local=(0, None))
    assert calls["remote"] == 0 and calls["local"] == 1 and calls.get("exact") is True
    assert "FALLBACK local: archive would differ from sha (" in out and reason in out
    assert gcloud.calls == []  # never even admitted
    assert records[-1]["mode"] == "fallback" and code == 0 and "REMOTE" not in out


def test_clean_tree_has_no_archive_problems(repo):
    commit_file(repo, ".gitattributes", "*.sh text eol=lf\n")
    assert bq.sha_archive_problems(head(repo), repo) == []


def test_exact_scratch_keeps_what_archive_drops(repo):
    commit_file(repo, "tests/red.test.ts", "red\n")
    commit_file(repo, ".gitattributes", "tests/red.test.ts export-ignore\nb.txt export-subst\n")
    commit_file(repo, "b.txt", "$Format:%H$\n")
    import shutil
    archived = bq.make_scratch(head(repo), repo)
    exact = bq.make_scratch(head(repo), repo, exact=True)
    try:
        assert not (archived / "tests" / "red.test.ts").exists()  # what finding 6 reproduced
        assert (exact / "tests" / "red.test.ts").read_text() == "red\n"
        assert (exact / "b.txt").read_text() == "$Format:%H$\n"
        assert (exact / "a.txt").read_text() == "committed\n"
    finally:
        shutil.rmtree(archived)
        shutil.rmtree(exact)


# ---------------------------------------------------------------- slot semaphore (r3 redesign)

@pytest.mark.parametrize("limit,slots", [(10, 3), (20, 8), (11, 3), (6, 1), (5, 0), (3, 0)])
def test_slots_are_limit_minus_4_over_2(limit, slots):
    assert bq.builder_slots(limit, {})[0] == slots


@pytest.mark.parametrize("env,override,slots", [
    ({"BUILDER_QA_RESERVE": "0"}, None, 3),        # reserve clamped up to 4
    ({"BUILDER_QA_RESERVE": "2"}, None, 3),
    ({"BUILDER_QA_RESERVE": "6"}, None, 2),        # a bigger reserve is honoured
    ({"BUILDER_QA_MAX_INFLIGHT": "10"}, None, 3),  # an override above the ceiling is clamped
    ({}, "99", 3),
    ({"BUILDER_QA_MAX_INFLIGHT": "4"}, None, 2),   # lowering works (4 builds = 2 runs)
    ({}, "2", 1),
    ({}, "0", 0),
])
def test_override_only_lowers_and_reserve_never_below_4(env, override, slots):
    got, note = bq.builder_slots(10, env, override)
    assert got == slots
    if env.get("BUILDER_QA_RESERVE") in ("0", "2") or override == "99" or env.get("BUILDER_QA_MAX_INFLIGHT") == "10":
        assert "clamped" in note


def test_granted_20_gives_8_slots_end_to_end(repo, tmp_path):
    gcloud = Gcloud(limit=(0, "20\n", ""))
    code, out, calls, _, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT), local=(1, "x"))
    assert records[-1]["slots"] == 8 and calls["remote"] == 1 and code == 0


def test_quota_read_failure_means_limit_10_and_3_slots(repo, tmp_path):
    gcloud = Gcloud(limit=(1, "", "ERROR: quota api"))
    _, _, calls, _, records, _ = run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT), local=(1, "x"))
    assert records[-1]["limit"] == 10 and records[-1]["slots"] == 3 and calls["remote"] == 1


def test_quota_read_once_per_run(repo, tmp_path):
    gcloud = Gcloud()
    run(repo, tmp_path, gcloud=gcloud, remote=(0, GREEN_OUT), local=(0, None))
    assert sum(1 for c in gcloud.calls if c[0] == "beta") == 1


SLOT_CHILD = r"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("bq", sys.argv[1])
bq = importlib.util.module_from_spec(spec); spec.loader.exec_module(bq)
slot = bq.try_acquire(sys.argv[2], int(sys.argv[3]), "test")
print("ADMITTED" if slot else "REFUSED", flush=True)
sys.stdin.read()  # hold the slot until the test closes stdin
"""


def holders(directory, n, slots):
    return [subprocess.Popen([sys.executable, "-c", SLOT_CHILD, str(SCRIPT), str(directory), str(slots)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for _ in range(n)]


def test_three_slots_seven_processes_exactly_three_admitted(tmp_path):
    procs = holders(tmp_path / "slots", 7, 3)
    try:
        answers = [p.stdout.readline().strip() for p in procs]
    finally:
        for p in procs:
            p.stdin.close()
            p.wait(timeout=30)
    assert answers.count("ADMITTED") == 3 and answers.count("REFUSED") == 4


def test_three_slots_seven_threads_exactly_three_admitted(tmp_path):
    barrier = threading.Barrier(7)
    got, lock = [], threading.Lock()

    def worker():
        barrier.wait()
        slot = bq.try_acquire(tmp_path / "slots", 3, "t")
        with lock:
            got.append(slot)
    threads = [threading.Thread(target=worker) for _ in range(7)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    held = [s for s in got if s]
    try:
        assert len(held) == 3 and sorted(s.index for s in held) == [0, 1, 2]
    finally:
        for s in held:
            s.release()


def test_slot_frees_after_its_holder_dies(tmp_path):
    procs = holders(tmp_path / "slots", 1, 1)
    assert procs[0].stdout.readline().strip() == "ADMITTED"
    assert bq.try_acquire(tmp_path / "slots", 1) is None
    procs[0].stdin.close()
    procs[0].wait(timeout=30)
    slot = bq.try_acquire(tmp_path / "slots", 1)
    assert slot is not None
    slot.release()


KILLED_PARENT = r"""
import importlib.util, subprocess, sys, time
spec = importlib.util.spec_from_file_location("bq", sys.argv[1])
bq = importlib.util.module_from_spec(spec); spec.loader.exec_module(bq)
slot = bq.try_acquire(sys.argv[2], 1, "parent")
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], pass_fds=(slot.fd,),
                         start_new_session=True)
print(child.pid, flush=True)
time.sleep(120)
"""


def test_sigkilled_holder_with_live_child_keeps_the_slot_until_the_child_dies(tmp_path):
    parent = subprocess.Popen([sys.executable, "-c", KILLED_PARENT, str(SCRIPT), str(tmp_path / "slots")],
                              stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline().strip())
    try:
        parent.kill()  # SIGKILL: no cleanup runs in the wrapper
        parent.wait(timeout=30)
        assert bq.try_acquire(tmp_path / "slots", 1) is None  # the runner still holds it
    finally:
        os.kill(child_pid, signal.SIGKILL)
    for _ in range(100):  # the child is reaped by init; wait for the kernel to drop the lock
        slot = bq.try_acquire(tmp_path / "slots", 1)
        if slot:
            break
        time.sleep(0.05)
    assert slot is not None
    slot.release()


def test_slots_at_or_above_n_are_ignored_not_deleted(tmp_path):
    directory = tmp_path / "slots"
    directory.mkdir()
    (directory / "slot-5").write_text("old holder")
    slot = bq.try_acquire(directory, 2)
    assert slot.index == 0 and (directory / "slot-5").read_text() == "old holder"
    slot.release()


def test_holder_note_is_written_but_never_read(tmp_path):
    slot = bq.try_acquire(tmp_path / "slots", 1, "pid 1 sha abc\n")
    try:
        assert (tmp_path / "slots" / "slot-0").read_text() == "pid 1 sha abc\n"
    finally:
        slot.release()
    (tmp_path / "slots" / "slot-0").write_text("garbage that must not matter")
    slot = bq.try_acquire(tmp_path / "slots", 1)
    assert slot is not None
    slot.release()


def hold_all(directory, n):
    return [bq.try_acquire(directory, n) for _ in range(n)]


def test_wait_timeout_falls_back_local_with_zero_submits(repo, tmp_path):
    held = hold_all(tmp_path / "slots", 3)
    try:
        code, out, calls, gcloud, records, clock = run(repo, tmp_path, remote=(0, GREEN_OUT), local=(0, None),
                                                       argv=["--admit-wait", "120", "--poll", "30"])
    finally:
        for s in held:
            s.release()
    assert calls == {"remote": 0, "local": 1}
    assert clock.sleeps == [30, 30, 30, 30]
    assert "QUEUE: all 3 builder slots busy" in out
    assert "FALLBACK local: admission: all 3 builder slots busy after 120s wait" in out
    assert not any(c[:2] == ["builds", "list"] for c in gcloud.calls)  # never reached the guard
    assert records[-1]["queue_wait_s"] == 120 and code == 0


def test_slot_frees_during_wait_then_submits(repo, tmp_path):
    held = hold_all(tmp_path / "slots", 3)
    clock = Clock()
    original = clock.sleep

    def sleep(seconds):
        original(seconds)
        if len(clock.sleeps) == 2:
            held[1].release()
    clock.sleep = sleep
    try:
        code, _, calls, _, records, _ = run(repo, tmp_path, remote=(0, GREEN_OUT), local=(1, "x"), clock=clock)
    finally:
        for s in held:
            s.release()
    assert calls["remote"] == 1 and records[-1]["slot"] == 1 and records[-1]["queue_wait_s"] == 60


@pytest.mark.parametrize("gcloud,reason", [
    (lambda: Gcloud(ongoing=(9,)), "deny-only guard: 9 ongoing + 2 > granted limit 10"),
    (lambda: Gcloud(list_code=1), "gcloud builds list failed (deny-only guard cannot read"),
])
def test_deny_only_guard_full_or_failing_falls_back_with_zero_submits(repo, tmp_path, gcloud, reason):
    code, out, calls, _, records, _ = run(repo, tmp_path, gcloud=gcloud(), remote=(0, GREEN_OUT),
                                          local=(1, "lint"))
    assert calls == {"remote": 0, "local": 1}
    assert f"FALLBACK local: {reason}" in out and code == 1
    slot = bq.try_acquire(tmp_path / "slots", 1)  # the slot was dropped before the local lane
    assert slot is not None
    slot.release()


def test_guard_at_exactly_the_limit_admits(repo, tmp_path):
    _, _, calls, _, _, _ = run(repo, tmp_path, gcloud=Gcloud(ongoing=(8,)), remote=(0, GREEN_OUT), local=(1, "x"))
    assert calls["remote"] == 1  # 8 + 2 = 10 <= 10


def test_slot_fd_is_passed_to_the_runner_and_released_after(repo, tmp_path):
    seen = {}

    def remote(scratch, say, child):
        seen["fds"] = child.pass_fds
        seen["busy"] = bq.try_acquire(tmp_path / "slots", 1) is None  # slot-0 is ours now
        return 0, GREEN_OUT
    code, _, _, _, _, _ = run(repo, tmp_path, remote=remote, local=(1, "x"))
    assert code == 0 and len(seen["fds"]) == 1 and seen["busy"]
    slot = bq.try_acquire(tmp_path / "slots", 1)
    assert slot is not None
    slot.release()


def test_no_ledger_file_is_written_anywhere(repo, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    run(repo, tmp_path, remote=(0, GREEN_OUT), local=(0, None))
    run(repo, tmp_path, remote=(137, ""), local=(1, "x"))
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    written = {p for p in written if not p.startswith("wt/")}
    assert written <= {"log.jsonl", "slots/slot-0"}, written
    assert not hasattr(bq, "Reservations") and not hasattr(bq, "RESERVATIONS")
