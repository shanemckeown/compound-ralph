"""cloudbuild-gate-remote.py (LUCY-vxk2v): the land gate's two Cloud Build lanes, gcloud stubbed.

No test here submits a real build: every gcloud call goes to FakeGcloud.
Run: uvx --python 3.13 --with pyyaml --with pytest pytest ~/.claude/scripts/tests/test_cloudbuild_gate_remote.py -q
"""
import importlib.util
import io
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "cloudbuild-gate-remote.py"
spec = importlib.util.spec_from_file_location("cloudbuild_gate_remote", SCRIPT)
gr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gr)
REAL_GATE_YAML = (Path(__file__).resolve().parent / "fixtures" / "cloudbuild-gate.e312f4c6b2.yaml").read_text()
SA = "projects/aestheticc/serviceAccounts/cloudbuild-gate@aestheticc.iam.gserviceaccount.com"

PG_FAIL_LOG = """\
Step #4 - "pg-tests": PASS pg-tests tests/pg-integration/ok.test.ts (21 s)
Step #4 - "pg-tests": FAIL pg-tests tests/pg-integration/comms-claim.test.ts (24 s)
Step #4 - "pg-tests":   ● claim › sends once
Step #4 - "pg-tests": FAIL tests/pg-integration/other.test.ts
Step #4 - "pg-tests": FAIL pg-tests tests/pg-integration/comms-claim.test.ts
"""


class FakeGcloud:
    """Scripted gcloud: lane -> list of describe statuses (last one repeats), plus logs."""

    def __init__(self, statuses, logs=None, steps=None, submit_fail_lane=None, sa_exists=True,
                 submit_out=None, filtered=None):
        self.sa_exists = sa_exists
        self.filtered = filtered or {}  # lane -> `gcloud logging read` textPayload output
        self.submit_out = submit_out
        self.statuses = {lane: list(s) for lane, s in statuses.items()}
        self.logs = logs or {}
        self.steps = steps or {}
        self.submit_fail_lane = submit_fail_lane
        self.calls = []
        self.lane_of = {}

    def __call__(self, args):
        self.calls.append(list(args))
        if args[0] == "iam":
            if self.sa_exists:
                return 0, json.dumps({"email": args[3]}), ""
            return 1, "", f"ERROR: NOT_FOUND: Unknown service account {args[3]}"
        if args[0] == "logging":
            build_id = re.search(r'build_id="([^"]+)"', args[2]).group(1)
            result = self.filtered.get(self.lane_of[build_id], "")
            return result if isinstance(result, tuple) else (0, result, "")
        verb = args[1]
        if verb == "submit":
            lane = next(a for a in args if a.startswith("--substitutions=")).split("=")[-1]
            if lane == self.submit_fail_lane:
                return 1, "", "ERROR: upload failed"
            if self.submit_out is not None:
                return 0, self.submit_out, ""
            build_id = f"build-{lane}"
            self.lane_of[build_id] = lane
            return 0, json.dumps({"id": build_id, "logUrl": f"https://console/{build_id}"}), ""
        lane = self.lane_of[args[2]]
        if verb == "describe":
            queue = self.statuses[lane]
            status = queue.pop(0) if len(queue) > 1 else queue[0]
            return 0, json.dumps({"id": args[2], "status": status, "steps": self.steps.get(lane, []),
                                  "createTime": "2026-10-20T02:17:10.5Z"}), ""
        if verb == "log":
            return 0, self.logs.get(lane, f"{lane} log body\n"), ""
        return 0, "", ""  # cancel

    def verbs(self, verb):
        return [c for c in self.calls if c[1] == verb]

    def reads(self, lane=None):
        """The `gcloud logging read` calls (for one lane's build, if given)."""
        return [c for c in self.calls if c[:2] == ["logging", "read"]
                and (lane is None or f'build_id="build-{lane}"' in c[2])]


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def scratch(tmp_path):
    (tmp_path / "cloudbuild-gate.yaml").write_text(REAL_GATE_YAML)
    return tmp_path


def run(scratch, fake, *extra):
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch), *extra], runner=fake, sleep=clock.sleep, clock=clock, out=out)
    return code, out.getvalue()


def test_both_lanes_success_exits_0_with_ids_and_urls_first(scratch):
    fake = FakeGcloud({"unit": ["QUEUED", "WORKING", "SUCCESS"], "pg": ["WORKING", "SUCCESS"]})
    code, out = run(scratch, fake)
    assert code == 0
    first = sorted(out.splitlines()[:2])  # the lanes submit concurrently, so either may print first
    assert first == ["LANE pg build build-pg https://console/build-pg",
                     "LANE unit build build-unit https://console/build-unit"]
    assert "===== LANE unit build build-unit status SUCCESS =====" in out
    assert "pg log body" not in out  # a green lane prints its summary, not its log
    assert out.rstrip().splitlines()[-1] == "RESULT unit=SUCCESS pg=SUCCESS exit 0"
    submits = fake.verbs("submit")
    assert [c[3] for c in submits] == [f"--config={scratch / 'cloudbuild-gate.yaml'}"] * 2
    assert all("--async" in c and "--format=json" in c and "--region=global" in c and
               "--project=aestheticc" in c and c[2] == str(scratch) for c in submits)
    # Review P2-1: both lanes run as the test-only gate account, never the default (deploying) one.
    assert all(f"--service-account={SA}" in c for c in submits)
    assert not fake.verbs("cancel")


def test_both_lanes_are_submitted_before_any_poll(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    run(scratch, fake)
    assert fake.calls[0][:3] == ["iam", "service-accounts", "describe"]
    assert [c[1] for c in fake.calls[1:4]] == ["submit", "submit", "describe"]


def test_one_lane_failure_exits_1_and_names_the_suites_last(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["WORKING", "FAILURE"]}, logs={"pg": PG_FAIL_LOG})
    code, out = run(scratch, fake)
    assert code == 1
    assert out.rstrip().splitlines()[-1] == (
        "FAILED LANE pg: tests/pg-integration/comms-claim.test.ts tests/pg-integration/other.test.ts")
    assert "ok.test.ts" not in out.rstrip().splitlines()[-1]
    assert not fake.verbs("cancel")  # never retries or cancels a finished red


def test_failure_without_jest_lines_names_the_failing_step(scratch):
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, logs={"unit": "tsc: error TS2322\n"},
                      steps={"unit": [{"id": "npm-ci", "status": "SUCCESS"},
                                      {"id": "lint-typecheck", "status": "FAILURE"}]})
    code, out = run(scratch, fake)
    assert code == 1 and out.rstrip().splitlines()[-1] == "FAILED LANE unit: lint-typecheck"


@pytest.mark.parametrize("infra", ["TIMEOUT", "INTERNAL_ERROR", "CANCELLED", "EXPIRED"])
def test_infra_terminal_status_exits_137(scratch, infra):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["WORKING", infra]})
    code, out = run(scratch, fake)
    assert code == 137 and f"INFRA LANE pg: {infra}" in out
    assert not fake.verbs("cancel")


def test_a_real_red_wins_over_the_other_lanes_infra_state(scratch):
    fake = FakeGcloud({"unit": ["TIMEOUT"], "pg": ["FAILURE"]}, logs={"pg": PG_FAIL_LOG})
    assert run(scratch, fake)[0] == 1


def test_ceiling_cancels_both_builds_and_exits_137(scratch):
    fake = FakeGcloud({"unit": ["WORKING"], "pg": ["SUCCESS"]})
    code, out = run(scratch, fake, "--poll", "20", "--ceiling", "100")
    assert code == 137 and "CEILING 100s passed" in out
    assert sorted(c[2] for c in fake.verbs("cancel")) == ["build-pg", "build-unit"]
    assert not fake.verbs("log")


def test_unreadable_describe_keeps_polling_until_the_ceiling(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    broken = lambda args: (1, "", "ERROR") if args[1] == "describe" else fake(args)  # noqa: E731
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch), "--ceiling", "60"], runner=broken, sleep=clock.sleep,
                   clock=clock, out=out)
    assert code == 137 and len(fake.verbs("cancel")) == 2


def test_second_submit_failure_cancels_the_first_and_exits_137(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]}, submit_fail_lane="pg")
    code, out = run(scratch, fake)
    assert code == 137 and "SUBMIT FAILED" in out
    assert [c[2] for c in fake.verbs("cancel")] == ["build-unit"] and not fake.verbs("describe")


def test_the_deploy_config_is_refused_by_name(tmp_path):
    (tmp_path / "cloudbuild.yaml").write_text(REAL_GATE_YAML)
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(tmp_path, fake, "--config", "cloudbuild.yaml")
    assert code == 2 and "deploy build" in out and fake.calls == []


def test_missing_config_is_refused(tmp_path):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(tmp_path, fake)
    assert code == 2 and "unreadable" in out and fake.calls == []


def test_cli_uses_the_gcloud_named_by_env(scratch, tmp_path):
    # The default runner is the only exec seam: point it at a stub binary and run the real CLI.
    fake_bin = tmp_path / "fake-gcloud"
    fake_bin.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$(dirname "$0")/calls.txt"\n'
        'case "$2" in\n'
        '  submit) lane=$(echo "$@" | sed -n "s/.*_LANE=\\([a-z]*\\).*/\\1/p");'
        ' echo "{\\"id\\": \\"b-$lane\\", \\"logUrl\\": \\"u\\"}" ;;\n'
        '  describe) echo \'{"status": "SUCCESS"}\' ;;\n'
        '  log) echo "lane log" ;;\n'
        "esac\n")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "CLOUDBUILD_GATE_GCLOUD": str(fake_bin)}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--scratch", str(scratch), "--poll", "0"],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sorted(proc.stdout.splitlines()[:2]) == ["LANE pg build b-pg u", "LANE unit build b-unit u"]
    calls = (tmp_path / "calls.txt").read_text()
    assert "builds submit" in calls and "run deploy" not in calls and "update-traffic" not in calls



# ---- Review round 1, P1-1: the allow-list. One named regression per bypass the reviewer listed;
# each config below returned [] (would be submitted) from the old text deny-list.

def refused(tmp_path, text, reason):
    (tmp_path / "cloudbuild-gate.yaml").write_text(text)
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(tmp_path, fake)
    assert code == 2, out
    assert reason in out, out
    assert fake.calls == []  # refused before any gcloud call, the SA check included
    return out


def test_bypass_flow_style_yaml_gcloud_builder(tmp_path):
    refused(tmp_path, "steps: [{name: 'gcr.io/cloud-builders/gcloud', args: [run, services, replace, s.yaml]}]\n",
            "is not a node:<tag> image")


def test_bypass_flow_style_quoted_json_keys(tmp_path):
    text = 'steps:\n  - {"name": "gcr.io/cloud-builders/gcloud", "args": ["run", "services", "replace", "s.yaml"]}\n'
    refused(tmp_path, text, "is not a node:<tag> image")


CLI_STEP = """steps:
  - name: gcr.io/google.com/cloudsdktool/google-cloud-cli
    entrypoint: bash
    args: ['-c', '{cmd}']
"""


@pytest.mark.parametrize("cmd", [
    "gcloud -q run services replace svc.yaml",
    "gcloud --project=aestheticc run services replace svc.yaml",
])
def test_bypass_gcloud_flag_before_run(tmp_path, cmd):
    out = refused(tmp_path, CLI_STEP.format(cmd=cmd), "mentions 'gcloud'")
    assert "is not a node:<tag> image" in out


def test_bypass_gcloud_beta_run(tmp_path):
    refused(tmp_path, CLI_STEP.format(cmd="gcloud beta run services replace svc.yaml"), "mentions 'gcloud'")


def test_bypass_gcloud_double_space_run(tmp_path):
    refused(tmp_path, CLI_STEP.format(cmd="gcloud  run services replace svc.yaml"), "mentions 'gcloud'")


def test_bypass_substitution_supplies_the_verb(tmp_path):
    text = ("substitutions: {_V: run}\nsteps:\n  - name: node:20\n    entrypoint: bash\n"
            "    args: ['-c', 'gcloud $_V services replace svc.yaml']\n")
    refused(tmp_path, text, "mentions 'gcloud'")


def test_bypass_anchor_supplies_the_entrypoint(tmp_path):
    text = "substitutions: {_E: &e gcloud}\nsteps:\n  - name: node:20\n    entrypoint: *e\n    args: [run, services, replace, s.yaml]\n"
    out = refused(tmp_path, text, "entrypoint 'gcloud' is not one of")  # safe_load resolved the anchor
    assert "mentions 'gcloud'" in out


def test_bypass_anchor_supplies_the_builder(tmp_path):
    text = ("substitutions: {_B: &b 'gcr.io/kaniko-project/executor:latest'}\n"
            "steps:\n  - name: *b\n    args: ['--context=.']\n")
    refused(tmp_path, text, "'gcr.io/kaniko-project/executor:latest' is not a node:<tag> image")


def test_bypass_split_quotes(tmp_path):
    # `g""cloud` and `update-tr""affic` hide every word from a text scan; the image allow-list holds.
    text = CLI_STEP.format(cmd='g""cloud r""un services update-tr""affic svc --to-lat""est=true')
    out = refused(tmp_path, text, "is not a node:<tag> image")
    assert "mentions 'gcloud'" not in out


def test_bypass_curl_to_run_googleapis_with_metadata_token(tmp_path):
    text = ("steps:\n  - name: node:20\n    entrypoint: bash\n    args:\n      - -c\n      - |\n"
            "        T=$$(curl -s -H Metadata-Flavor:Google http://metadata.google.internal/computeMetadata/v1/"
            "instance/service-accounts/default/token)\n"
            "        curl -X PATCH -H \"Authorization: Bearer $$T\" https://run.googleapis.com/v2/projects/x/services/y\n")
    out = refused(tmp_path, text, "mentions 'run.googleapis'")
    assert "mentions 'metadata'" in out


def test_bypass_kaniko_destination_push(tmp_path):
    text = ("steps:\n  - name: gcr.io/kaniko-project/executor:latest\n"
            "    args: ['--destination=europe-west2-docker.pkg.dev/aestheticc/x/app:1', '--context=.']\n")
    out = refused(tmp_path, text, "is not a node:<tag> image")
    assert "mentions 'docker'" in out


def test_bypass_quoted_images_key(tmp_path):
    text = REAL_GATE_YAML + '"images": [\'gcr.io/aestheticc/app\']\n'
    refused(tmp_path, text, "top-level key 'images' is not allowed")


def test_bypass_top_level_service_account(tmp_path):
    text = REAL_GATE_YAML + "serviceAccount: 'projects/aestheticc/serviceAccounts/deployer@x.iam.gserviceaccount.com'\n"
    out = refused(tmp_path, text, "a serviceAccount key at config.serviceAccount")
    assert "top-level key 'serviceAccount' is not allowed" in out


def test_bypass_service_account_under_options(tmp_path):
    text = REAL_GATE_YAML.replace("options:\n", "options:\n  serviceAccount: deployer@x.iam.gserviceaccount.com\n", 1)
    refused(tmp_path, text, "a serviceAccount key at config.options.serviceAccount")


# ---- the allow-list's own edges

def test_the_real_gate_config_passes_the_allow_list(tmp_path):
    path = tmp_path / "cloudbuild-gate.yaml"
    path.write_text(REAL_GATE_YAML)
    assert gr.config_problems(path) == []


@pytest.mark.parametrize("text, reason", [
    ("steps: [unclosed\n", "does not parse as YAML"),
    ("- just\n- a list\n", "not a mapping"),
    ("timeout: 60s\n", "no steps: list"),
    ("availableSecrets: {}\nsteps:\n  - name: node:20\n", "top-level key 'availableSecrets'"),
    ("steps:\n  - name: node:20\n    entrypoint: python3\n", "entrypoint 'python3' is not one of"),
    ("steps:\n  - name: node\n", "'node' is not a node:<tag> image"),
    ("steps:\n  - name: ubuntu:22.04\n", "is not a node:<tag> image"),
    ("# a comment naming KUBECTL still refuses\nsteps:\n  - name: node:20\n", "mentions 'kubectl'"),
])
def test_allow_list_edges(tmp_path, text, reason):
    refused(tmp_path, text, reason)


def test_missing_pyyaml_refuses_with_exit_2_never_the_old_deny_list(scratch, monkeypatch):
    monkeypatch.setitem(sys.modules, "yaml", None)  # `import yaml` now raises ImportError
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(scratch, fake)
    assert code == 2 and "PyYAML is not installed" in out and fake.calls == []


# ---- Review P2-1: the gate service account

def test_missing_gate_service_account_refuses_with_exit_2_before_any_submit(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]}, sa_exists=False)
    code, out = run(scratch, fake)
    assert code == 2 and "gate service account cloudbuild-gate@aestheticc.iam.gserviceaccount.com not found" in out
    assert fake.calls == [["iam", "service-accounts", "describe",
                           "cloudbuild-gate@aestheticc.iam.gserviceaccount.com",
                           "--project=aestheticc", "--format=json"]]


def test_service_account_email_is_passed_as_its_resource_name(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, _ = run(scratch, fake, "--service-account", "other-gate@aestheticc.iam.gserviceaccount.com")
    assert code == 0 and fake.calls[0][3] == "other-gate@aestheticc.iam.gserviceaccount.com"
    want = "--service-account=projects/aestheticc/serviceAccounts/other-gate@aestheticc.iam.gserviceaccount.com"
    assert [want in c for c in fake.verbs("submit")] == [True, True]


# ---- Review P3-1: gcloud exec errors are infra (137), never a red (1)

def test_run_gcloud_turns_a_missing_binary_into_an_exec_error(monkeypatch):
    monkeypatch.setenv("CLOUDBUILD_GATE_GCLOUD", "/nonexistent/gcloud")
    code, out, err = gr.run_gcloud(["version"])
    assert code == gr.EXEC_ERROR and out == "" and "/nonexistent/gcloud" in err


def test_cli_with_a_missing_gcloud_exits_137(scratch):
    env = {**os.environ, "CLOUDBUILD_GATE_GCLOUD": "/nonexistent/gcloud"}
    proc = subprocess.run([sys.executable, str(SCRIPT), "--scratch", str(scratch), "--poll", "0"],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == 137, proc.stdout + proc.stderr
    assert "GCLOUD UNAVAILABLE" in proc.stdout and "Traceback" not in proc.stderr


def test_a_runner_raising_oserror_mid_submit_exits_137_and_cancels_the_started_lane(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})

    def flaky(args):
        if args[1] == "submit" and "--substitutions=_LANE=pg" in args:
            raise PermissionError("gcloud not executable")
        return fake(args)
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=flaky, sleep=clock.sleep, clock=clock, out=out)
    assert code == 137 and "SUBMIT FAILED" in out.getvalue()
    assert [c[2] for c in fake.verbs("cancel")] == ["build-unit"]


def test_non_json_submit_exits_137(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]}, submit_out="Created [x]. not json")
    code, out = run(scratch, fake)
    assert code == 137 and "printed no JSON" in out and not fake.verbs("describe")


# ---- Review P3-2: SIGTERM / SIGINT cancel what was submitted

def test_sigterm_while_polling_cancels_both_lanes_and_exits_137(scratch):
    fake = FakeGcloud({"unit": ["WORKING"], "pg": ["WORKING"]})
    before = signal.getsignal(signal.SIGTERM)
    out = io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=fake,
                   sleep=lambda _s: os.kill(os.getpid(), signal.SIGTERM), clock=lambda: 0.0, out=out)
    assert code == 137 and "INTERRUPTED by SIGTERM" in out.getvalue()
    assert sorted(c[2] for c in fake.verbs("cancel")) == ["build-pg", "build-unit"]
    assert not fake.verbs("log")
    assert signal.getsignal(signal.SIGTERM) == before  # handler restored


def test_sigint_during_concurrent_submit_cancels_whatever_was_submitted(scratch):
    # The signal lands while the pg upload is still in flight (unit has already returned). The
    # build pg's submit returns AFTER the stop must be cancelled too, by its own thread.
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    unit_done = threading.Event()

    def interrupted(args):
        if args[1] == "submit" and "--substitutions=_LANE=pg" in args:
            assert unit_done.wait(5)
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(0.3)  # the main thread's handler runs and snapshots before this submit returns
        result = fake(args)
        if args[1] == "submit" and "--substitutions=_LANE=unit" in args:
            unit_done.set()
        return result
    before = signal.getsignal(signal.SIGINT)
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=interrupted, sleep=clock.sleep, clock=clock, out=out)
    assert code == 137 and "INTERRUPTED by SIGINT" in out.getvalue()
    assert sorted(c[2] for c in fake.verbs("cancel")) == ["build-pg", "build-unit"]
    assert len(fake.verbs("cancel")) == 2  # each exactly once
    assert "LANE pg build build-pg submitted after the stop; cancelled" in out.getvalue()
    assert not fake.verbs("describe")
    assert signal.getsignal(signal.SIGINT) == before


def test_sigterm_before_any_submit_returns_cancels_both_late_builds(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    barrier = threading.Barrier(2, timeout=5)

    def both_in_flight(args):
        if args[1] == "submit":
            if barrier.wait() == 0:
                os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.3)
        return fake(args)
    out = io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=both_in_flight, sleep=lambda _s: None,
                   clock=lambda: 0.0, out=out)
    assert code == 137 and "cancelling 0 submitted lane build(s)" in out.getvalue()
    assert sorted(c[2] for c in fake.verbs("cancel")) == ["build-pg", "build-unit"]


# ---- LUCY-zp23t item 1: duplicate keys and merge keys (review round 2, P3-R2-1)

def test_duplicate_top_level_steps_is_refused(tmp_path):
    text = "steps:\n  - name: gcr.io/kaniko-project/executor:latest\nsteps:\n  - name: node:20\n"
    refused(tmp_path, text, "duplicate key 'steps' at line 3 (first at line 1)")


def test_duplicate_name_inside_one_step_is_refused(tmp_path):
    text = "steps:\n  - name: google/cloud-sdk\n    name: node:20\n    entrypoint: bash\n"
    refused(tmp_path, text, "duplicate key 'name' at line 3")


def test_duplicate_key_inside_options_is_refused(tmp_path):
    text = REAL_GATE_YAML.replace("options:\n", "options:\n  logging: NONE\n", 1)
    refused(tmp_path, text, "duplicate key 'logging'")


def test_duplicate_key_inside_substitutions_is_refused(tmp_path):
    text = REAL_GATE_YAML.replace("substitutions:\n", "substitutions:\n  _LANE: 'pg'\n", 1)
    refused(tmp_path, text, "duplicate key '_LANE'")


def test_merge_key_is_refused_outright(tmp_path):
    # `<<` then an override of `name` is the duplicate-key split by another name; refuse any merge.
    text = ("substitutions: {_X: x}\nbase: &base {name: 'gcr.io/x/y'}\n"
            "steps:\n  - <<: *base\n    name: node:20\n")
    refused(tmp_path, text, "merge key '<<' at line 4 is not allowed")


def test_merge_key_without_override_is_refused_too(tmp_path):
    text = "steps:\n  - <<: {name: node:20}\n    entrypoint: bash\n"
    refused(tmp_path, text, "merge key '<<'")


def test_anchors_and_aliases_still_resolve(tmp_path):
    path = tmp_path / "cloudbuild-gate.yaml"
    path.write_text("substitutions: {_I: &img 'node:20'}\nsteps:\n  - name: *img\n  - name: *img\n")
    assert gr.config_problems(path) == []


# ---- LUCY-zp23t item 2: the options allow-list (review round 2, P4-R2-1)

@pytest.mark.parametrize("line, key", [
    ("  pool: {name: projects/aestheticc/locations/europe-west2/workerPools/p}\n", "pool"),
    ("  workerPool: projects/aestheticc/locations/europe-west2/workerPools/p\n", "workerPool"),
    ("  env: ['NODE_OPTIONS=--require=/x.js']\n", "env"),
    ("  secretEnv: ['TOKEN']\n", "secretEnv"),
    ("  volumes: [{name: v, path: /v}]\n", "volumes"),
    ("  requestedVerifyOption: VERIFIED\n", "requestedVerifyOption"),
])
def test_options_key_outside_the_allow_list_is_refused(tmp_path, line, key):
    refused(tmp_path, REAL_GATE_YAML.replace("options:\n", "options:\n" + line, 1),
            f"options key {key!r} is not allowed (only diskSizeGb, logging, machineType)")


def test_options_disk_size_is_allowed(tmp_path):
    path = tmp_path / "cloudbuild-gate.yaml"
    path.write_text(REAL_GATE_YAML.replace("options:\n", "options:\n  diskSizeGb: 200\n", 1))
    assert gr.config_problems(path) == []


def test_options_that_is_not_a_mapping_is_refused(tmp_path):
    refused(tmp_path, "options: [pool]\nsteps:\n  - name: node:20\n", "options is not a mapping")


# ---- LUCY-zp23t item 3: runner plumbing

def test_both_submits_start_before_either_finishes(scratch):
    # Sequential submits would deadlock on the barrier (BrokenBarrierError after 5 s).
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    barrier = threading.Barrier(2, timeout=5)

    def concurrent(args):
        if args[1] == "submit":
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                return 1, "", "submits ran one after the other"
        return fake(args)
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=concurrent, sleep=clock.sleep, clock=clock, out=out)
    assert code == 0, out.getvalue()
    assert len(fake.verbs("submit")) == 2


def test_lane_log_fetches_never_overlap(scratch):
    # LUCY-tdbtv: parallel reads tripped the Cloud Logging read quota (429); fetch one at a time.
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    lock, overlaps = threading.Lock(), []
    in_flight = [0]

    def watched(args):
        if args[1] in ("log", "read"):
            with lock:
                in_flight[0] += 1
                if in_flight[0] > 1:
                    overlaps.append(args[2])
            time.sleep(0.2)
            with lock:
                in_flight[0] -= 1
        return fake(args)
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=watched, sleep=clock.sleep, clock=clock, out=out)
    assert code == 0 and overlaps == []
    assert len(fake.reads()) == 2 and not fake.verbs("log")  # LUCY-csdds: green lanes never fetch the log


UNIT_OK_LOG = """\
Step #2 - "unit-tests": PASS tests/a.test.ts
Step #2 - "unit-tests": noisy line one
Step #2 - "unit-tests": Test Suites: 412 passed, 412 total
Step #2 - "unit-tests": Tests:       5120 passed, 5120 total
Step #2 - "unit-tests": Snapshots:   3 passed, 3 total
Step #2 - "unit-tests": Time:        301.2 s
"""


def test_success_lane_prints_a_summary_and_a_failure_lane_its_full_log(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["FAILURE"]}, logs={"pg": PG_FAIL_LOG},
                      filtered={"unit": UNIT_OK_LOG})
    code, out = run(scratch, fake)
    assert code == 1
    assert "===== LANE unit build build-unit status SUCCESS =====\nlog: https://console/build-unit\n" in out
    assert "Test Suites: 412 passed" in out and "Tests:       5120 passed" in out and "Time:        301.2 s" in out
    assert "noisy line one" not in out and "Snapshots:" not in out and "PASS tests/a.test.ts" not in out
    assert PG_FAIL_LOG.rstrip() in out  # the red lane's whole log
    assert out.rstrip().splitlines()[-1].startswith("FAILED LANE pg: ")


def test_infra_lane_prints_its_full_log(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["TIMEOUT"]}, logs={"pg": "step a\nstep b timed out\n"})
    code, out = run(scratch, fake)
    assert code == 137 and "step a\nstep b timed out" in out


def test_failed_lane_lines_stay_last_when_both_lanes_fail(scratch):
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["FAILURE"]},
                      logs={"unit": "FAIL tests/u.test.ts\n", "pg": PG_FAIL_LOG})
    code, out = run(scratch, fake)
    assert code == 1
    assert out.rstrip().splitlines()[-2:] == [
        "FAILED LANE unit: tests/u.test.ts",
        "FAILED LANE pg: tests/pg-integration/comms-claim.test.ts tests/pg-integration/other.test.ts"]


def test_build_lines_reach_a_redirected_evidence_file_before_the_run_ends(scratch, tmp_path):
    # stdout goes to a file (block-buffered by default). The stub reads that file on its first
    # describe: both LANE lines must already be there, not held back until exit.
    evidence = tmp_path / "evidence.log"
    fake_bin = tmp_path / "fake-gcloud"
    fake_bin.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        '  submit) lane=$(echo "$@" | sed -n "s/.*_LANE=\\([a-z]*\\).*/\\1/p");'
        ' echo "{\\"id\\": \\"b-$lane\\", \\"logUrl\\": \\"u\\"}" ;;\n'
        '  describe) [ -f "$(dirname "$0")/seen.txt" ] || cp "$EVIDENCE" "$(dirname "$0")/seen.txt";'
        ' echo \'{"status": "SUCCESS"}\' ;;\n'
        '  log) echo "Tests: 1 passed" ;;\n'
        "esac\n")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "CLOUDBUILD_GATE_GCLOUD": str(fake_bin), "EVIDENCE": str(evidence)}
    env.pop("PYTHONUNBUFFERED", None)
    with evidence.open("w") as sink:
        proc = subprocess.run([sys.executable, str(SCRIPT), "--scratch", str(scratch), "--poll", "0"],
                              env=env, stdout=sink, stderr=subprocess.PIPE, text=True)
    assert proc.returncode == 0, proc.stderr
    seen = (tmp_path / "seen.txt").read_text().splitlines()
    assert sorted(seen) == ["LANE pg build b-pg u", "LANE unit build b-unit u"]


def test_main_flushes_every_line_it_says(scratch):
    class Sink(io.StringIO):
        flushes = 0

        def flush(self):
            Sink.flushes += 1
            super().flush()
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    clock, out = Clock(), Sink()
    gr.main(["--scratch", str(scratch)], runner=fake, sleep=clock.sleep, clock=clock, out=out)
    assert Sink.flushes >= len(out.getvalue().splitlines()) - 2  # summaries join lines in one say


# ---- LUCY-zp23t review round 1

def test_signal_between_thread_append_and_start_exits_137_never_1(scratch, monkeypatch):
    # P2-1: the pg thread is in state.threads but not started when the signal lands. Joining it
    # raised RuntimeError, which escaped main() as exit 1 (a red) instead of 137 (infra).
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    real_start = threading.Thread.start

    def start(self):
        if self.name == "submit-pg":
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.1)  # the handler runs in the main thread, before the real start
        return real_start(self)
    monkeypatch.setattr(threading.Thread, "start", start)
    out = io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=fake, sleep=lambda _s: None, clock=lambda: 0.0, out=out)
    assert code == 137 and "INTERRUPTED by SIGTERM" in out.getvalue()
    assert [c[2] for c in fake.verbs("cancel")] == ["build-unit"]
    assert not any("_LANE=pg" in a for c in fake.verbs("submit") for a in c)


def test_sigterm_with_a_hung_submit_exits_137_within_the_bound(scratch, monkeypatch):
    # P3-1: the pg upload never returns. SIGTERM must still end the run, within the join bound.
    monkeypatch.setattr(gr, "SIGNAL_JOIN_BOUND", 0.5, raising=False)
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    release = threading.Event()

    def hung(args):
        if args[1] == "submit" and "--substitutions=_LANE=pg" in args:
            release.wait(6)  # "never" for this test; released in finally so no thread outlives it
            return 1, "", "hung"
        return fake(args)
    timer = threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM))
    out = io.StringIO()
    started = time.monotonic()
    try:
        timer.start()
        code = gr.main(["--scratch", str(scratch)], runner=hung, sleep=lambda _s: None,
                       clock=lambda: 0.0, out=out)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        timer.cancel()
    assert code == 137 and "INTERRUPTED by SIGTERM" in out.getvalue()
    assert elapsed < 4, elapsed
    assert "submit(s) still running" in out.getvalue()
    assert [c[2] for c in fake.verbs("cancel")] == ["build-unit"]


def test_a_hung_gcloud_call_times_out_as_infra(scratch, tmp_path, monkeypatch):
    # P3-1: the real runner bounds every gcloud call; a submit past the bound is infra (137).
    fake_bin = tmp_path / "slow-gcloud"
    fake_bin.write_text('#!/bin/sh\ncase "$2" in submit) exec sleep 30 ;; esac\necho "{}"\n')
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CLOUDBUILD_GATE_GCLOUD", str(fake_bin))
    monkeypatch.setattr(gr, "GCLOUD_TIMEOUT", 0.5, raising=False)
    started = time.monotonic()
    code, out = run(scratch, gr.run_gcloud)
    assert code == 137 and "SUBMIT FAILED" in out and "timed out after 0.5s" in out
    assert time.monotonic() - started < 10


def test_gcloud_timeout_is_well_over_a_slow_upload():
    assert gr.GCLOUD_TIMEOUT >= 300


def _cancel_signals_once(fake):
    """A runner whose first cancel delivers SIGTERM, i.e. the signal lands inside a cancel loop."""
    fired = []

    def runner(args):
        result = fake(args)
        if args[1] == "cancel" and not fired:
            fired.append(1)
            os.kill(os.getpid(), signal.SIGTERM)
        return result
    return runner


def test_signal_during_the_ceiling_cancel_loop_cancels_each_build_once(scratch):
    # P4-1: the ceiling loop and then the signal path both cancelled build-unit.
    fake = FakeGcloud({"unit": ["WORKING"], "pg": ["WORKING"]})
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch), "--ceiling", "10"], runner=_cancel_signals_once(fake),
                   sleep=clock.sleep, clock=clock, out=out)
    assert code == 137
    assert sorted(c[2] for c in fake.verbs("cancel")) == ["build-pg", "build-unit"]


def test_signal_during_the_submit_error_cleanup_cancels_the_build_once(scratch):
    # P4-1: the submit-error loop and then the signal path both cancelled build-unit.
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]}, submit_fail_lane="pg")
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=_cancel_signals_once(fake),
                   sleep=clock.sleep, clock=clock, out=out)
    assert code == 137
    assert [c[2] for c in fake.verbs("cancel")] == ["build-unit"]


def test_docstring_names_the_strict_loader_not_safe_load():
    assert "yaml.safe_load" not in gr.__doc__ and "strict SafeLoader" in gr.__doc__


def test_a_timed_out_service_account_check_is_infra_not_a_refusal(scratch):
    # Review round 2 P3: a describe that timed out could not check the SA; that is infra (137).
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})

    def slow_iam(args):
        if args[0] == "iam":
            fake.calls.append(list(args))
            return 124, "", "gcloud iam service-accounts timed out after 600s"
        return fake(args)
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=slow_iam, sleep=clock.sleep, clock=clock, out=out)
    assert code == 137, out.getvalue()
    assert "GCLOUD UNAVAILABLE" in out.getvalue() and "not found" not in out.getvalue()
    assert not fake.verbs("submit")


# ---- LUCY-tdbtv: 429 log fetches, incomplete evidence (build 9c17d03e)

UNIT_FAIL_STEPS = [{"id": "npm-ci", "status": "SUCCESS"}, {"id": "unit-tests", "status": "FAILURE"}]
UNIT_FAIL_FULL = """\
Step #2 - "unit-tests": PASS tests/a.test.ts
Step #2 - "unit-tests": FAIL tests/api/ai-entitlement-webhooks.test.ts
Step #2 - "unit-tests": Test Suites: 1 failed, 411 passed, 412 total
Step #2 - "unit-tests": Tests:       1 failed, 5119 passed, 5120 total
"""
UNIT_TRUNCATED = """\
Step #2 - "unit-tests": PASS tests/a.test.ts
Step #2 - "unit-tests": PASS tests/b.test.ts
"""
QUOTA_ERR = ("ERROR: (gcloud.builds.log) HttpError accessing <logging.googleapis.com/v2/entries:list>: "
             "response: <{'status': '429'}>, content <Quota exceeded for quota metric 'Read requests'>")


def scripted_logs(fake, lane, results):
    """A runner whose `builds log` for `lane` returns `results` in turn (the last repeats)."""
    queue = list(results)

    def runner(args):
        if args[1] == "log" and fake.lane_of.get(args[2]) == lane:
            fake.calls.append(list(args))
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return fake(args)
    return runner


def run_with(scratch, runner, *extra):
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch), *extra], runner=runner, sleep=clock.sleep, clock=clock, out=out)
    return code, out.getvalue(), clock


def test_a_429_on_the_first_fetch_is_retried_and_the_full_log_names_the_suite(scratch):
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS})
    runner = scripted_logs(fake, "unit", [(1, UNIT_TRUNCATED, QUOTA_ERR), (0, UNIT_FAIL_FULL, "")])
    code, out, clock = run_with(scratch, runner)
    assert code == 1
    assert out.rstrip().splitlines()[-1] == "FAILED LANE unit: tests/api/ai-entitlement-webhooks.test.ts"
    assert "LOG INCOMPLETE" not in out and "Test Suites: 1 failed" in out
    assert len([c for c in fake.verbs("log") if c[2] == "build-unit"]) == 2
    assert clock.now == gr.FETCH_DELAYS[0]


def test_a_429_with_exit_0_is_still_retried(scratch):
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS})
    runner = scripted_logs(fake, "unit", [(0, UNIT_FAIL_FULL, QUOTA_ERR), (0, UNIT_FAIL_FULL, "")])
    code, out, _ = run_with(scratch, runner)
    assert code == 1 and len([c for c in fake.verbs("log") if c[2] == "build-unit"]) == 2


def test_a_log_missing_the_jest_summary_is_retried_then_flagged_incomplete(scratch):
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS})
    runner = scripted_logs(fake, "unit", [(0, UNIT_TRUNCATED, "")])
    code, out, _ = run_with(scratch, runner)
    assert code == 1  # the build status is authoritative
    tries = len(gr.FETCH_DELAYS) + 1
    assert len([c for c in fake.verbs("log") if c[2] == "build-unit"]) == tries
    assert ("LOG INCOMPLETE for unit: 2 lines fetched, no jest summary for unit-tests; "
            "see https://console/build-unit") in out
    assert out.rstrip().splitlines()[-1] == "FAILED LANE unit: unit-tests"


def test_the_filtered_read_names_the_suites_without_a_full_fetch(scratch):
    truncated = UNIT_TRUNCATED + 'Step #2 - "unit-tests": FAIL tests/early.test.ts\n'
    filtered = ('Step #2 - "unit-tests": FAIL tests/early.test.ts\n'
                'Step #2 - "unit-tests": FAIL tests/api/ai-entitlement-webhooks.test.ts\n')
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS},
                      logs={"unit": truncated}, filtered={"unit": filtered})
    code, out = run(scratch, fake)
    assert code == 1
    assert out.rstrip().splitlines()[-1] == (
        "FAILED LANE unit: tests/early.test.ts tests/api/ai-entitlement-webhooks.test.ts")
    reads = fake.reads("unit")
    assert len(reads) == 1
    assert ('resource.type="build"' in reads[0][2] and 'resource.labels.build_id="build-unit"' in reads[0][2]
            and f'textPayload=~"{gr.FAILURE_LINES}"' in reads[0][2])
    assert not fake.verbs("log")  # LUCY-csdds: the filtered read named the suites, so no full fetch


def test_backoff_gives_up_after_n_tries_without_hanging(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["TIMEOUT"]})
    runner = scripted_logs(fake, "pg", [(1, "partial\n", QUOTA_ERR)])
    started = time.monotonic()
    code, out, clock = run_with(scratch, runner)
    assert time.monotonic() - started < 5
    assert code == 137
    tries = len(gr.FETCH_DELAYS) + 1
    assert tries >= 5
    assert len([c for c in fake.verbs("log") if c[2] == "build-pg"]) == tries
    assert clock.now == sum(gr.FETCH_DELAYS)
    assert gr.FETCH_DELAYS[0] <= 15 and gr.FETCH_DELAYS[-1] >= 100
    assert list(gr.FETCH_DELAYS) == sorted(gr.FETCH_DELAYS)
    assert f"LOG INCOMPLETE for pg: 1 lines fetched, fetch failed after {tries} tries" in out


def test_fetch_with_retry_uses_the_patchable_sleep(monkeypatch):
    monkeypatch.setattr(gr, "FETCH_DELAYS", (1, 2))
    slept = []
    result, problem = gr.fetch_with_retry(lambda _a: (1, "", "boom"), ["builds", "log", "x"], slept.append)
    assert slept == [1, 2] and problem.startswith("exit 1")


# LUCY-csdds: the full log is fetched only when the filtered Cloud Logging read cannot name the red
# suites. Two runs on 20 Oct spent ~16 min re-downloading 5 MB logs under 429s.
def test_a_success_lane_makes_no_builds_log_call_and_prints_the_jest_summary(scratch):
    pg_ok = UNIT_OK_LOG.replace('#2 - "unit-tests"', '#3 - "pg-tests"').replace("412", "57")
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]}, filtered={"unit": UNIT_OK_LOG, "pg": pg_ok})
    code, out = run(scratch, fake)
    assert code == 0 and not fake.verbs("log")
    assert ("===== LANE unit build build-unit status SUCCESS =====\nlog: https://console/build-unit\n"
            'Step #2 - "unit-tests": Test Suites: 412 passed, 412 total\n') in out
    assert "Tests:       5120 passed" in out and "Time:        301.2 s" in out
    assert 'Step #3 - "pg-tests": Test Suites: 57 passed, 57 total' in out
    assert "noisy line one" not in out and "Snapshots:" not in out
    reads = fake.reads()
    assert len(reads) == 2  # ONE read per lane
    for lane, read in zip(gr.LANES, reads):
        assert f'resource.labels.build_id="build-{lane}"' in read[2]
        assert f'textPayload=~"{gr.SUMMARY_LINES}"' in read[2]
        assert 'timestamp>="2026-10-20T02:17:10.5Z"' in read[2]
        assert "--order=asc" in read
    assert out.rstrip().splitlines()[-1] == "RESULT unit=SUCCESS pg=SUCCESS exit 0"


def test_a_success_lane_whose_summary_read_fails_says_unavailable_and_keeps_exit_0(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]},
                      filtered={"unit": (1, "", QUOTA_ERR), "pg": UNIT_OK_LOG})
    code, out, clock = run_with(scratch, fake)
    assert code == 0 and not fake.verbs("log")
    tries = len(gr.FETCH_DELAYS) + 1
    assert len(fake.reads("unit")) == tries and clock.now == sum(gr.FETCH_DELAYS)
    assert "===== LANE unit build build-unit status SUCCESS =====\nlog: https://console/build-unit\nsummary unavailable (exit 1" in out
    assert "Test Suites: 412 passed" in out  # the pg lane's summary still prints


def test_a_failure_lane_names_the_suite_from_the_filtered_read_without_a_full_fetch(scratch):
    filtered = ('Step #2 - "unit-tests":   ● Console\n'
                'Step #2 - "unit-tests": FAIL tests/api/ai-entitlement-webhooks.test.ts (12 s)\n'
                'Step #2 - "unit-tests":   ● webhook › grants the entitlement\n'
                'Step #2 - "unit-tests":     Expected: 200\n'
                'Step #2 - "unit-tests":     Received: 500\n'
                'Step #2 - "unit-tests": Test Suites: 1 failed, 411 passed, 412 total\n'
                'Step #2 - "unit-tests": Tests:       1 failed, 5119 passed, 5120 total\n'
                'ERROR: build step 2 "node:20" failed: step exited with non-zero status: 1\n')
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS},
                      logs={"unit": UNIT_TRUNCATED}, filtered={"unit": filtered, "pg": UNIT_OK_LOG})
    code, out = run(scratch, fake)
    assert code == 1 and not fake.verbs("log")
    assert len(fake.reads("unit")) == 1
    assert "Expected: 200" in out and "Received: 500" in out and "ERROR: build step 2" in out
    assert "● Console" not in out  # jest's console-block headers are noise, not evidence
    assert "● webhook › grants the entitlement" in out
    assert "LOG INCOMPLETE" not in out
    assert out.rstrip().splitlines()[-1] == "FAILED LANE unit: tests/api/ai-entitlement-webhooks.test.ts"


def test_a_failure_lane_whose_filtered_read_has_no_fail_line_falls_back_to_the_full_fetch(scratch):
    filtered = 'ERROR: build step 1 "node:20" failed: step exited with non-zero status: 2\n'
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, logs={"unit": "tsc: error TS2322\n"},
                      steps={"unit": [{"id": "npm-ci", "status": "SUCCESS"},
                                      {"id": "lint-typecheck", "status": "FAILURE"}]},
                      filtered={"unit": filtered})
    code, out = run(scratch, fake)
    assert code == 1
    assert len(fake.reads("unit")) == 1
    assert [c[2] for c in fake.verbs("log")] == ["build-unit"]  # the full fetch, unit lane only
    assert "tsc: error TS2322" in out
    assert out.rstrip().splitlines()[-1] == "FAILED LANE unit: lint-typecheck"


def test_a_failed_filtered_read_falls_back_to_the_full_fetch_and_keeps_log_incomplete(scratch):
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS},
                      logs={"unit": UNIT_TRUNCATED}, filtered={"unit": (1, "", QUOTA_ERR)})
    code, out = run(scratch, fake)
    assert code == 1
    tries = len(gr.FETCH_DELAYS) + 1
    assert len(fake.reads("unit")) == tries
    assert len([c for c in fake.verbs("log") if c[2] == "build-unit"]) == tries
    assert "LOG INCOMPLETE for unit: 2 lines fetched, no jest summary for unit-tests" in out
    assert out.rstrip().splitlines()[-1] == "FAILED LANE unit: unit-tests"


def test_a_jest_timeout_failure_evidence_includes_the_thrown_exceeded_timeout_line(scratch):
    # Live run on unit build 442cc56d: the evidence named the test but not why it failed.
    assert "thrown: " in gr.FAILURE_LINES and "Exceeded timeout" in gr.FAILURE_LINES
    filtered = ('Step #2 - "unit-tests": FAIL tests/components/room-picker.test.tsx (31 s)\n'
                'Step #2 - "unit-tests":   ● a suggested room is not submitted as a choice › keeps it a hint\n'
                'Step #2 - "unit-tests":     thrown: "Exceeded timeout of 5000 ms for a test.\n'
                'Step #2 - "unit-tests": Test Suites: 1 failed, 411 passed, 412 total\n')
    fake = FakeGcloud({"unit": ["FAILURE"], "pg": ["SUCCESS"]}, steps={"unit": UNIT_FAIL_STEPS},
                      filtered={"unit": filtered, "pg": UNIT_OK_LOG})
    code, out = run(scratch, fake)
    assert code == 1 and not fake.verbs("log")
    assert 'thrown: "Exceeded timeout of 5000 ms for a test.' in out
    assert out.rstrip().splitlines()[-1] == "FAILED LANE unit: tests/components/room-picker.test.tsx"
