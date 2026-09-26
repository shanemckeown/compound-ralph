"""LUCY-w6fpi: full pg lanes serialise laptop-wide; builder gates never run one.

Run: python3 -m unittest ~/.claude/tests/test_pg_lane.py  (from ~/.claude/tests)
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
LOCK = SCRIPTS / "pg-lane-lock.py"
GOAL_MD = Path(__file__).resolve().parent.parent / "commands" / "goal.md"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each fake lane records its start/end, sleeping in between.
LANE = ("import sys, time; open(sys.argv[1], 'a').write(f'start {time.time()}\\n'); "
        "time.sleep(1.0); open(sys.argv[1], 'a').write(f'end {time.time()}\\n')")


class PgLaneLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "PG_LANE_LOCK_DIR": self.tmp.name}
        self.env.pop("PG_LANE_LOCK_HELD", None)

    def tearDown(self):
        self.tmp.cleanup()

    def lane(self, record, env=None):
        return subprocess.Popen(
            [sys.executable, str(LOCK), "--", sys.executable, "-c", LANE, record],
            env=env or self.env, stderr=subprocess.PIPE, text=True)

    def test_two_concurrent_full_lanes_serialise_and_the_second_logs(self):
        a_rec, b_rec = (os.path.join(self.tmp.name, n) for n in ("a", "b"))
        first = self.lane(a_rec)
        time.sleep(0.3)  # first holds the lock
        second = self.lane(b_rec)
        _, err_a = first.communicate(timeout=20)
        _, err_b = second.communicate(timeout=20)
        self.assertEqual((first.returncode, second.returncode), (0, 0))

        def span(path):
            marks = dict(line.split() for line in Path(path).read_text().splitlines())
            return float(marks["start"]), float(marks["end"])

        a, b = span(a_rec), span(b_rec)
        self.assertLessEqual(a[1], b[0], f"lanes overlapped: {a} vs {b}")
        self.assertIn("waiting: full pg lane already running", err_b)
        self.assertIn("acquired (no wait)", err_a)
        log = Path(self.tmp.name, "pg-lane.log").read_text()
        self.assertIn("waiting", log)

    def test_nested_wrapper_does_not_deadlock_on_itself(self):
        rec = os.path.join(self.tmp.name, "n")
        inner = [sys.executable, str(LOCK), "--", sys.executable, "-c", LANE, rec]
        run = subprocess.run([sys.executable, str(LOCK), "--", *inner],
                             env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_lock_unavailable_fails_open_loudly(self):
        rec = os.path.join(self.tmp.name, "f")
        env = {**self.env, "PG_LANE_LOCK_DIR": "/dev/null/not-a-dir"}
        run = subprocess.run([sys.executable, str(LOCK), "--", sys.executable, "-c", LANE, rec],
                             env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0)
        self.assertIn("UNSERIALISED", run.stderr)


class GatedFullPgDetectionTest(unittest.TestCase):
    gated = load("gated_test_run", "gated-test-run.py")

    def test_full_lane_detection(self):
        full = self.gated.is_full_pg_lane
        self.assertTrue(full(["--selectProjects", "pg-tests"]))
        self.assertTrue(full(["--selectProjects=pg-tests", "--maxWorkers", "2"]))
        self.assertFalse(full(["--selectProjects", "pg-tests", "--findRelatedTests", "lib/a.ts"]))
        # jest reads a path straight after --selectProjects as a project name (round 3).
        self.assertTrue(full(["--selectProjects", "pg-tests", "tests/pg-integration/x.test.ts"]))
        self.assertFalse(full(["tests/pg-integration/x.test.ts", "--selectProjects", "pg-tests"]))
        self.assertTrue(full(["--selectProjects=pg-tests", "tests/pg-integration/x.test.ts"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "--env", "./pg-env.js"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "--testEnvironment",
                              "./tests/setup/pg-test-environment.js"]))
        self.assertFalse(full(["--selectProjects", "node-tests"]))
        self.assertFalse(full([]))
        # Option values are not path filters (Astra round-1 P2).
        self.assertTrue(full(["--config", "./jest.config.js", "--selectProjects", "pg-tests"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "--outputFile", "/tmp/r.json"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "node-tests"]))
        # Round-2 P2: any other option value keeps it full (errs towards locking).
        self.assertTrue(full(["--selectProjects", "pg-tests", "--coverageProvider", "v8"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "--reporters", "default",
                              "./ci-reporter.js"]))
        self.assertFalse(full(["--selectProjects", "pg-tests", "--runInBand",
                               "tests/pg-integration/quickbooks-sync.test.ts"]))

    def test_lock_os_errors_fail_open(self):
        lock = load("pg_lane_lock", "pg-lane-lock.py")
        with tempfile.TemporaryDirectory() as tmp:
            lock.LOCK_DIR = Path(tmp)
            lock.LOCK, lock.LOG = Path(tmp, "pg-lane.lock"), Path(tmp, "pg-lane.log")
            real_flock, real_write = lock.fcntl.flock, lock.os.write

            def enolck(*_):
                raise OSError(77, "No locks available")
            try:
                lock.fcntl.flock = enolck
                self.assertIsNone(lock.acquire())  # warns, caller still runs the lane
                lock.fcntl.flock = real_flock
                lock.os.write = enolck  # holder metadata write fails (ENOSPC-like)
                self.assertIsNotNone(lock.acquire())  # lock kept despite metadata error
            finally:
                lock.fcntl.flock, lock.os.write = real_flock, real_write
            self.assertIn("UNSERIALISED", Path(tmp, "pg-lane.log").read_text())

    def test_gated_wraps_only_the_full_lane_in_the_lock(self):
        parser = self.gated.argparse.ArgumentParser()

        def command(*tail):
            args = type("A", (), {"command": ["jest", *tail], "reserve_mib": None,
                                  "heap_mib": None})()
            return self.gated.command_for(args, parser)[0]

        self.assertIn(str(LOCK), command("--selectProjects", "pg-tests"))
        self.assertNotIn(str(LOCK), command("--selectProjects", "pg-tests",
                                            "--findRelatedTests", "lib/a.ts"))
        self.assertNotIn(str(LOCK), command("--findRelatedTests", "lib/a.ts"))


class BuilderGateNeverRunsFullLaneTest(unittest.TestCase):
    targeted = load("targeted_tests", "targeted-tests.py")

    def assert_targeted(self, steps):
        for _, command, _ in steps:
            self.assertIn("--findRelatedTests", command)
            self.assertFalse(self.gated_full(command[command.index("jest") + 1:]))

    def gated_full(self, tail):
        return GatedFullPgDetectionTest.gated.is_full_pg_lane(tail)

    def test_targeted_plan_only_runs_related_suites(self):
        steps = self.targeted.plan(["lib/a.ts"], ["/x/tests/pg-integration/a.test.ts"], 143)
        self.assertEqual([label for label, _, _ in steps], ["related jest", "related pg-tests"])
        self.assert_targeted(steps)
        self.assertEqual(steps[1][2]["RUN_PG_TESTS"], "true")
        self.assertNotIn(str(LOCK), steps[1][1])

    def test_near_full_related_pg_set_takes_the_lock(self):
        suites = [f"/x/{n}.test.ts" for n in range(130)]
        steps = self.targeted.plan(["lib/schema.ts"], suites, 143)
        self.assertIn(str(LOCK), steps[1][1])
        steps = self.targeted.plan(["lib/a.ts"], suites[:28], 143)  # a lib service: no lock
        self.assertNotIn(str(LOCK), steps[1][1])

    def test_deleted_helper_selects_the_tests_that_import_it(self):
        with tempfile.TemporaryDirectory() as repo:
            def sh(*cmd):
                subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
            os.makedirs(f"{repo}/tests/pg-integration")
            Path(repo, "tests/pg-integration/_readiness.ts").write_text("export const r = 1;\n")
            Path(repo, "tests/pg-integration/ready.test.ts").write_text(
                "import { r } from './_readiness';\n")
            Path(repo, "tests/pg-integration/other.test.ts").write_text("it('x', () => {});\n")
            sh("git", "init", "-q", "-b", "main")
            sh("git", "add", ".")
            sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
            os.remove(f"{repo}/tests/pg-integration/_readiness.ts")
            cwd = os.getcwd()
            try:
                os.chdir(repo)
                files = self.targeted.changed_files("HEAD")
            finally:
                os.chdir(cwd)
        self.assertEqual(files, ["tests/pg-integration/ready.test.ts"])

    def test_goal_qa_command_is_targeted_and_never_the_full_lane(self):
        text = GOAL_MD.read_text()
        qa = next(line for line in text.splitlines() if line.startswith("QA_COMMAND="))
        self.assertIn("targeted-tests.py", qa)
        for banned in ("test:pg", "pg-tests", "test:cloudbuild", "gated-test-run.py jest"):
            self.assertNotIn(banned, qa)


if __name__ == "__main__":
    unittest.main()
