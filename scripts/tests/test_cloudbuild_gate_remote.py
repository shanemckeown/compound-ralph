"""cloudbuild-gate-remote.py (LUCY-vxk2v): the land gate's two Cloud Build lanes, gcloud stubbed.

No test here submits a real build: every gcloud call goes to FakeGcloud.
Run: uvx --python 3.13 pytest ~/.claude/scripts/tests/test_cloudbuild_gate_remote.py -q
"""
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "cloudbuild-gate-remote.py"
spec = importlib.util.spec_from_file_location("cloudbuild_gate_remote", SCRIPT)
gr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gr)

GATE_YAML = """\
# cloudbuild-gate.yaml - test-only land gate. Deploys nothing.
timeout: 1800s
options:
  machineType: 'E2_HIGHCPU_32'
substitutions:
  _LANE: 'unit'
steps:
  - name: node:20
    id: npm-ci
    entrypoint: bash
    args: ['-c', 'set -euo pipefail; npm ci --legacy-peer-deps']
  # ---- lane: pg (nothing here pushes or deploys)
  - name: node:20
    id: pg-tests
    waitFor: [npm-ci]
    entrypoint: bash
    args: ['-c', '[ "$_LANE" = pg ] || exit 0; npx jest --selectProjects pg-tests --maxWorkers=14']
"""

PG_FAIL_LOG = """\
Step #4 - "pg-tests": PASS pg-tests tests/pg-integration/ok.test.ts (21 s)
Step #4 - "pg-tests": FAIL pg-tests tests/pg-integration/comms-claim.test.ts (24 s)
Step #4 - "pg-tests":   ● claim › sends once
Step #4 - "pg-tests": FAIL tests/pg-integration/other.test.ts
Step #4 - "pg-tests": FAIL pg-tests tests/pg-integration/comms-claim.test.ts
"""


class FakeGcloud:
    """Scripted gcloud: lane -> list of describe statuses (last one repeats), plus logs."""

    def __init__(self, statuses, logs=None, steps=None, submit_fail_lane=None):
        self.statuses = {lane: list(s) for lane, s in statuses.items()}
        self.logs = logs or {}
        self.steps = steps or {}
        self.submit_fail_lane = submit_fail_lane
        self.calls = []
        self.lane_of = {}

    def __call__(self, args):
        self.calls.append(list(args))
        verb = args[1]
        if verb == "submit":
            lane = next(a for a in args if a.startswith("--substitutions=")).split("=")[-1]
            if lane == self.submit_fail_lane:
                return 1, "", "ERROR: upload failed"
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
    (tmp_path / "cloudbuild-gate.yaml").write_text(GATE_YAML)
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
    assert not fake.verbs("cancel")


def test_both_lanes_are_submitted_before_any_poll(scratch):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    run(scratch, fake)
    assert [c[1] for c in fake.calls[:3]] == ["submit", "submit", "describe"]


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


@pytest.mark.parametrize("bad, reason", [
    ("images:\n  - 'europe-west2-docker.pkg.dev/aestheticc/x'\n", "top-level images"),
    ("artifacts:\n  objects:\n    location: gs://x\n", "top-level artifacts"),
    ("  - name: gcr.io/cloud-builders/docker\n    args: ['push', 'x']\n    entrypoint: bash\n"
     "    script: docker push europe-west2-docker.pkg.dev/x\n", "docker push"),
    ("  - name: gcr.io/google.com/cloudsdktool/cloud-sdk\n    args: ['gcloud', 'run', 'deploy', 'x']\n",
     "deploy"),
    ("  - name: x\n    args: ['bash', '-c', 'gcloud run services update-traffic x --to-latest']\n",
     "update-traffic"),
    # Astra round 1: the standard builder forms split the command into args.
    ("  - name: node:20\n    entrypoint: gcloud\n    args: ['run', 'services', 'replace', 'service.yaml']\n",
     "entrypoint is the gcloud builder"),
    ("  - name: 'gcr.io/cloud-builders/docker'\n    args: ['push', 'europe-west2-docker.pkg.dev/x']\n",
     "name is the docker builder"),
    ("  - name: node:20\n    args:\n      - push\n      - x\n", "'push' arg"),
    ("  - name: gcr.io/google.com/cloudsdktool/cloud-sdk:slim\n    args: ['bash', '-c', 'true']\n",
     "cloud-sdk builder"),
])
def test_deploying_config_is_refused_before_any_gcloud_call(tmp_path, bad, reason):
    (tmp_path / "cloudbuild-gate.yaml").write_text(GATE_YAML + bad)
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(tmp_path, fake)
    assert code == 2 and reason in out and fake.calls == []


def test_the_deploy_config_is_refused_by_name(tmp_path):
    (tmp_path / "cloudbuild.yaml").write_text(GATE_YAML)
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(tmp_path, fake, "--config", "cloudbuild.yaml")
    assert code == 2 and "deploy build" in out and fake.calls == []


def test_missing_config_is_refused(tmp_path):
    fake = FakeGcloud({"unit": ["SUCCESS"], "pg": ["SUCCESS"]})
    code, out = run(tmp_path, fake)
    assert code == 2 and "unreadable" in out and fake.calls == []


def test_comments_do_not_trip_the_guard_but_a_config_without_steps_does(scratch, tmp_path):
    # The header and in-steps comments say "Deploys nothing" / "deploys"; only step text counts.
    assert gr.config_problems(scratch / "cloudbuild-gate.yaml") == []
    no_steps = tmp_path / "empty.yaml"
    no_steps.write_text("timeout: 60s\n")
    assert gr.config_problems(no_steps) == ["no top-level steps: key"]


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
