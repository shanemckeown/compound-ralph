"""cloudbuild-gate-remote.py (LUCY-vxk2v): the land gate's two Cloud Build lanes, gcloud stubbed.

No test here submits a real build: every gcloud call goes to FakeGcloud.
Run: uvx --python 3.13 --with pyyaml --with pytest pytest ~/.claude/scripts/tests/test_cloudbuild_gate_remote.py -q
"""
import importlib.util
import io
import json
import os
import signal
import stat
import subprocess
import sys
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
                 submit_out=None):
        self.sa_exists = sa_exists
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
            return 0, json.dumps({"id": args[2], "status": status, "steps": self.steps.get(lane, [])}), ""
        if verb == "log":
            return 0, self.logs.get(lane, f"{lane} log body\n"), ""
        return 0, "", ""  # cancel

    def verbs(self, verb):
        return [c for c in self.calls if c[1] == verb]


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
    first = out.splitlines()[:2]
    assert first == ["LANE unit build build-unit https://console/build-unit",
                     "LANE pg build build-pg https://console/build-pg"]
    assert "===== LANE unit build build-unit status SUCCESS =====" in out and "pg log body" in out
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
    assert proc.stdout.splitlines()[0] == "LANE unit build b-unit u"
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


def test_sigint_during_the_second_submit_cancels_only_the_first_lane(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})

    def interrupted(args):
        if args[1] == "submit" and "--substitutions=_LANE=pg" in args:
            os.kill(os.getpid(), signal.SIGINT)
        return fake(args)
    before = signal.getsignal(signal.SIGINT)
    clock, out = Clock(), io.StringIO()
    code = gr.main(["--scratch", str(scratch)], runner=interrupted, sleep=clock.sleep, clock=clock, out=out)
    assert code == 137 and "INTERRUPTED by SIGINT" in out.getvalue()
    assert [c[2] for c in fake.verbs("cancel")] == ["build-unit"]
    assert signal.getsignal(signal.SIGINT) == before
