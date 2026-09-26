"""LUCY-w6fpi: full pg lanes serialise laptop-wide; builder gates never run one.

Run unsandboxed (the nested-lock test walks the parent chain with ps):
    cd ~/.claude/tests && python3 -m unittest test_pg_lane

Attempt 2: each "FIX n" test fails on attempt 1 (72c2386) and passes now.
"""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HOME = Path(__file__).resolve().parent.parent
SCRIPTS = HOME / "scripts"
LOCK = SCRIPTS / "pg-lane-lock.py"
GOAL_MD = HOME / "commands" / "goal.md"
LAND_SKILL = HOME / "skills" / "land-batch" / "SKILL.md"


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Each fake lane records its start/end, sleeping in between.
LANE = ("import sys, time; open(sys.argv[1], 'a').write(f'start {time.time()}\\n'); "
        "time.sleep(1.0); open(sys.argv[1], 'a').write(f'end {time.time()}\\n')")


def span(path):
    marks = dict(line.split() for line in Path(path).read_text().splitlines())
    return float(marks["start"]), float(marks["end"])


def wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


class PgLaneLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {**os.environ, "PG_LANE_LOCK_DIR": self.tmp.name}
        for key in ("PG_LANE_LOCK_HELD", "PG_LANE_LOCK_TOKEN"):
            self.env.pop(key, None)
        self.lock_file = Path(self.tmp.name, "pg-lane.lock")

    def tearDown(self):
        self.tmp.cleanup()

    def lane(self, record, env=None):
        return subprocess.Popen(
            [sys.executable, str(LOCK), "--", sys.executable, "-c", LANE, record],
            env=env or self.env, stderr=subprocess.PIPE, text=True)

    def holder(self):
        try:
            return json.loads(self.lock_file.read_text() or "{}")
        except (OSError, ValueError):
            return {}

    def assert_serialised(self, first, second, a_rec, b_rec):
        _, err_a = first.communicate(timeout=20)
        _, err_b = second.communicate(timeout=20)
        self.assertEqual((first.returncode, second.returncode), (0, 0), err_a + err_b)
        a, b = span(a_rec), span(b_rec)
        self.assertLessEqual(a[1], b[0], f"lanes overlapped: {a} vs {b}")
        self.assertIn("waiting: full pg lane already running", err_b)
        return err_a, err_b

    def test_two_concurrent_full_lanes_serialise_and_the_second_logs(self):
        a_rec, b_rec = (os.path.join(self.tmp.name, n) for n in ("a", "b"))
        first = self.lane(a_rec)
        self.assertTrue(wait_for(lambda: Path(a_rec).exists()))
        second = self.lane(b_rec)
        err_a, _ = self.assert_serialised(first, second, a_rec, b_rec)
        self.assertIn("acquired (no wait)", err_a)
        self.assertIn("waiting", Path(self.tmp.name, "pg-lane.log").read_text())

    def test_nested_wrapper_under_a_real_holder_does_not_deadlock(self):
        rec = os.path.join(self.tmp.name, "n")
        inner = [sys.executable, str(LOCK), "--", sys.executable, "-c", LANE, rec]
        run = subprocess.run([sys.executable, str(LOCK), "--", *inner],
                             env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("held by an ancestor", run.stderr)

    def test_child_exit_code_is_returned(self):
        run = subprocess.run([sys.executable, str(LOCK), "--", sys.executable, "-c",
                              "raise SystemExit(7)"], env=self.env, capture_output=True,
                             text=True, timeout=20)
        self.assertEqual(run.returncode, 7)

    # FIX 2: lock unavailable -> fail CLOSED (attempt 1 ran the lane unserialised).
    def test_fix2_lock_dir_unusable_refuses_and_does_not_run_the_lane(self):
        rec = os.path.join(self.tmp.name, "f")
        env = {**self.env, "PG_LANE_LOCK_DIR": "/dev/null/not-a-dir"}
        run = subprocess.run([sys.executable, str(LOCK), "--", sys.executable, "-c", LANE, rec],
                             env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertIn("REFUSED", run.stderr)
        self.assertFalse(Path(rec).exists(), "the lane ran without the lock")

    def test_fix2_enolck_refuses_and_sets_no_token(self):
        rec = os.path.join(self.tmp.name, "e")
        probe = (
            "import importlib.util, os, sys\n"
            f"spec = importlib.util.spec_from_file_location('l', {str(LOCK)!r})\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "def enolck(*_): raise OSError(77, 'No locks available')\n"
            "m.fcntl.flock = enolck\n"
            "code = m.main(['x', '--', sys.executable, '-c', \"open(sys.argv[1], 'w')\", "
            f"{rec!r}])\n"
            "assert 'PG_LANE_LOCK_TOKEN' not in os.environ and "
            "os.environ.get('PG_LANE_LOCK_HELD') != '1', 'lock-held marker set on refusal'\n"
            "sys.exit(code)\n")
        run = subprocess.run([sys.executable, "-c", probe], env=self.env,
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertFalse(Path(rec).exists(), "the lane ran after ENOLCK")

    # FIX 3: a surviving grandchild must not keep the lock after the holder is kill -9'd.
    def test_fix3_kill9_holder_releases_lock_even_if_a_grandchild_survives(self):
        pidfile = os.path.join(self.tmp.name, "grandchild.pid")
        survivor = (f"{sys.executable} -c 'import time; time.sleep(30)' & "
                    f"echo $! > {pidfile}; wait")
        holder = subprocess.Popen([sys.executable, str(LOCK), "--", "sh", "-c", survivor],
                                  env=self.env, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        try:
            self.assertTrue(wait_for(lambda: Path(pidfile).exists()
                                     and Path(pidfile).read_text().strip()))
            os.kill(holder.pid, signal.SIGKILL)
            holder.wait(timeout=5)
            rec = os.path.join(self.tmp.name, "after")
            started = time.time()
            nxt = subprocess.run([sys.executable, str(LOCK), "--", sys.executable, "-c",
                                  LANE, rec], env=self.env, capture_output=True, text=True,
                                 timeout=8)
            self.assertEqual(nxt.returncode, 0, nxt.stderr)
            self.assertLess(time.time() - started, 5, "lock still held by the orphan")
        finally:
            try:
                os.killpg(holder.pid, signal.SIGKILL)
            except OSError:
                pass

    # FIX 4: an environment flag never stands in for really holding the lock.
    def test_fix4_env_flags_do_not_bypass_a_real_holder(self):
        a_rec, b_rec = (os.path.join(self.tmp.name, n) for n in ("a", "b"))
        first = self.lane(a_rec)
        self.assertTrue(wait_for(lambda: Path(a_rec).exists() and self.holder().get("pid")))
        leaked = {**self.env, "PG_LANE_LOCK_HELD": "1",  # attempt 1's trusted flag
                  "PG_LANE_LOCK_TOKEN": self.holder().get("token", "x")}  # even the real one
        second = self.lane(b_rec, env=leaked)
        self.assert_serialised(first, second, a_rec, b_rec)


class GatedFullPgDetectionTest(unittest.TestCase):
    gated = load("gated_test_run", "gated-test-run.py")

    def full(self, tail, env=None):
        return self.gated.is_full_pg_lane(tail, env if env is not None else {})

    def test_full_lane_detection(self):
        full = self.full
        self.assertTrue(full(["--selectProjects", "pg-tests"]))
        self.assertTrue(full(["--selectProjects=pg-tests", "--maxWorkers", "2"]))
        self.assertFalse(full(["--selectProjects", "pg-tests", "--findRelatedTests", "lib/a.ts"]))
        self.assertFalse(full(["--selectProjects", "pg-tests", "--runTestsByPath", "/x/a.test.ts"]))
        # jest reads a path straight after --selectProjects as a project name.
        self.assertTrue(full(["--selectProjects", "pg-tests", "tests/pg-integration/x.test.ts"]))
        self.assertFalse(full(["tests/pg-integration/x.test.ts", "--selectProjects", "pg-tests"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "--env", "./pg-env.js"]))
        self.assertFalse(full(["--selectProjects", "node-tests"]))
        self.assertFalse(full([]))
        self.assertTrue(full(["--config", "./jest.config.js", "--selectProjects", "pg-tests"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "node-tests"]))
        self.assertTrue(full(["--selectProjects", "pg-tests", "--coverageProvider", "v8"]))

    # FIX 7 (sibling): RUN_PG_TESTS=true makes a bare jest the full lane plus node/dom.
    def test_fix7_bare_jest_with_run_pg_tests_is_a_full_lane(self):
        pg_env = {"RUN_PG_TESTS": "true"}
        self.assertTrue(self.full([], pg_env))
        self.assertTrue(self.full(["--maxWorkers=2"], pg_env))
        self.assertFalse(self.full(["--findRelatedTests", "lib/a.ts"], pg_env))
        self.assertFalse(self.full(["--selectProjects", "node-tests"], pg_env))

    def command(self, *tail):
        parser = self.gated.argparse.ArgumentParser()
        args = type("A", (), {"command": ["jest", *tail], "reserve_mib": None,
                              "heap_mib": None})()
        return self.gated.command_for(args, parser)[0]

    def test_gated_wraps_only_the_full_lane_in_the_lock(self):
        saved = os.environ.pop("RUN_PG_TESTS", None)
        try:
            self.assertIn(str(LOCK), self.command("--selectProjects", "pg-tests"))
            self.assertNotIn(str(LOCK), self.command("--selectProjects", "pg-tests",
                                                     "--findRelatedTests", "lib/a.ts"))
            self.assertNotIn(str(LOCK), self.command())
            os.environ["RUN_PG_TESTS"] = "true"
            self.assertIn(str(LOCK), self.command())
        finally:
            os.environ.pop("RUN_PG_TESTS", None)
            if saved is not None:
                os.environ["RUN_PG_TESTS"] = saved


class BuilderGateNeverRunsFullLaneTest(unittest.TestCase):
    targeted = load("targeted_tests", "targeted-tests.py")
    gated = GatedFullPgDetectionTest.gated
    SUITES = [f"/w/tests/pg-integration/s{n}.test.ts" for n in range(140)]

    def plan(self, files, broad=(), node_related=3, pg_related=None, pg_total=140,
             touched=()):
        return self.targeted.plan(list(files), list(broad), node_related, pg_related,
                                  pg_total, list(touched))

    def assert_never_full(self, steps):
        for _, command, env in steps:
            self.assertNotIn(str(LOCK), command)
            self.assertNotIn("--passWithNoTests", command)
            tail = command[command.index("jest") + 1:]
            self.assertFalse(self.gated.is_full_pg_lane(tail, env), command)

    def test_targeted_plan_only_runs_related_suites(self):
        steps, _ = self.plan(["lib/a.ts"], pg_related=self.SUITES[:3])
        self.assertEqual([s[0] for s in steps], ["related jest", "related pg-tests"])
        self.assertIn("--findRelatedTests", steps[0][1])
        self.assertEqual(steps[1][1][-3:], self.SUITES[:3])
        self.assertEqual(steps[1][2]["RUN_PG_TESTS"], "true")
        self.assert_never_full(steps)

    # FIX 1: never green on zero tests.
    def test_fix1_non_graph_paths_force_the_broad_node_set(self):
        for path in ("drizzle/0300_add_x.sql", "package.json", "jest.config.js",
                     "tsconfig.json", "scripts/ci/pg-lane.sh", ".gcloudignore",
                     "cloudbuild.yaml", "tests/setup/pg-env.ts"):
            files, broad = self.targeted.classify([path])
            self.assertTrue(broad, f"{path} should force the broad node/dom set")
            steps, notes = self.plan(files, broad)
            self.assertEqual(steps[0][1][-1], "jest", f"{path}: {steps[0][1]}")
            self.assertIn("BROAD", notes[0])
            self.assert_never_full(steps)

    def test_fix1_zero_related_or_no_files_runs_the_broad_set(self):
        for files, related in ((["lib/a.ts"], 0), ([], 3)):
            steps, _ = self.plan(files, node_related=related)
            self.assertEqual(steps[0][1][-1], "jest")
            self.assert_never_full(steps)

    def test_fix1_rename_and_delete_are_seen(self):
        with tempfile.TemporaryDirectory() as repo:
            def sh(*cmd):
                subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
            os.makedirs(f"{repo}/lib")
            os.makedirs(f"{repo}/tests")
            Path(repo, "lib/widget.ts").write_text("export const w = 1;\n")
            Path(repo, "lib/orphan.ts").write_text("export const o = 1;\n")
            Path(repo, "tests/widget.test.ts").write_text("import { w } from '../lib/widget';\n")
            sh("git", "init", "-q", "-b", "main")
            sh("git", "add", ".")
            sh("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
            sh("git", "mv", "lib/widget.ts", "lib/gadget.ts")
            sh("git", "rm", "-q", "lib/orphan.ts")
            cwd = os.getcwd()
            try:
                os.chdir(repo)
                paths = self.targeted.changed_paths("HEAD")
                files, broad = self.targeted.classify(paths)
            finally:
                os.chdir(cwd)
        self.assertIn("lib/widget.ts", paths)  # the rename's old side is not lost
        self.assertEqual(files, ["lib/gadget.ts", "tests/widget.test.ts"])
        self.assertEqual(len(broad), 1)
        self.assertIn("lib/orphan.ts", broad[0])  # nothing names it: broad, not zero

    # FIX 6: an over-cap related pg set never escalates to the full lane.
    def test_fix6_over_cap_pg_set_runs_only_edited_pg_files(self):
        touched = [self.SUITES[5], "/w/lib/schema.ts"]
        steps, notes = self.plan(["lib/schema.ts"], pg_related=self.SUITES[:130],
                                 touched=touched)
        self.assertEqual(steps[1][1][-1], self.SUITES[5])
        self.assertEqual(steps[1][1].count("--runTestsByPath"), 1)
        self.assertLess(len(steps[1][1]), 12)
        self.assertTrue(any("deferred" in n for n in notes))
        self.assert_never_full(steps)
        steps, notes = self.plan(["lib/schema.ts"], pg_related=self.SUITES[:130])
        self.assertEqual(len(steps), 1)  # nothing edited: pg deferred, never full
        self.assert_never_full(steps)

    # ATTEMPT 3 (6): edited pg files still over the cap wait for the lock.
    def test_a3_edited_pg_set_over_the_cap_takes_the_lock(self):
        edited = self.SUITES[:100]
        steps, notes = self.plan(["lib/schema.ts"], pg_related=self.SUITES[:130],
                                 touched=edited)
        command = steps[1][1]
        self.assertEqual(command[:3], [sys.executable, str(LOCK), "--"])
        self.assertEqual(command[-100:], edited)
        self.assertTrue(any("pg-lane lock" in n for n in notes))
        steps, _ = self.plan(["lib/schema.ts"], pg_related=self.SUITES[:130],
                             touched=edited[:3])
        self.assertNotIn(str(LOCK), steps[1][1])  # under the cap: no lock, no wait

    # FIX 7: the node/dom step never inherits RUN_PG_TESTS into an unlocked pg run.
    def test_fix7_node_step_strips_run_pg_tests(self):
        os.environ["RUN_PG_TESTS"] = "true"
        try:
            for files in (["lib/a.ts"], []):
                steps, _ = self.plan(files, pg_related=self.SUITES[:2])
                self.assertNotIn("RUN_PG_TESTS", steps[0][2])
                self.assert_never_full(steps)
                self.assertEqual(sum("pg-tests" in c for _, c, _ in steps), 1)
        finally:
            os.environ.pop("RUN_PG_TESTS", None)

    def test_goal_qa_command_is_targeted_and_never_the_full_lane(self):
        text = GOAL_MD.read_text()
        qa = next(line for line in text.splitlines() if line.startswith("QA_COMMAND="))
        self.assertIn("targeted-tests.py", qa)
        for banned in ("test:pg", "pg-tests", "test:cloudbuild", "gated-test-run.py jest"):
            self.assertNotIn(banned, qa)


class LandBatchRunsFullLaneBeforePushTest(unittest.TestCase):
    # FIX 5: a default LAND run passes the full pg lane before any push to main.
    def test_fix5_land_mode_runs_the_locked_full_lane_before_pushing_main(self):
        text = LAND_SKILL.read_text()
        land = text[text.index("## LAND mode"):text.index("## SHIP mode")]
        lane = land.index("pg-lane-lock.py\" -- \\\n    env TZ=UTC RUN_PG_TESTS=true npx jest "
                          "--selectProjects pg-tests")
        pushes = [i for i in range(len(land)) if land.startswith("push origin", i)]
        self.assertTrue(pushes)
        self.assertLess(lane, pushes[0], "LAND pushes main before the full pg lane")
        self.assertIn('push origin "$PG_TREE_SHA:main"', land)
        self.assertNotIn('push origin HEAD:main', land)
        migration = land[land.index("#### Mandatory migration-batch transaction"):]
        self.assertIn("then the full pg lane block", migration)

    # ATTEMPT 3 (5): the push block itself stops on a red, missing or stale lane.
    def test_a3_push_block_pushes_only_on_a_green_lane_for_this_tree(self):
        text = LAND_SKILL.read_text()
        end = text.index('push origin "$PG_TREE_SHA:main"')
        block = text[text.rindex("~~~bash\n", 0, end) + len("~~~bash\n"):
                     text.index("~~~", end)]
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp, "git")
            fake.write_text('#!/bin/sh\ncase "$*" in *rev-parse*) echo "$FAKE_HEAD";; '
                            '*push*) echo "$*" >> "$PUSHED";; esac\n')
            fake.chmod(0o755)
            pushed = Path(tmp, "pushed")

            def run(exit_text, head="abc", tree="abc"):
                evidence = Path(tmp, "full-pg")
                Path(f"{evidence}.exit").unlink(missing_ok=True)
                if exit_text is not None:
                    Path(f"{evidence}.exit").write_text(exit_text)
                pushed.unlink(missing_ok=True)
                env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}",
                       "FAKE_HEAD": head, "PUSHED": str(pushed), "SCRATCH": tmp,
                       "PG_EVIDENCE": str(evidence), "PG_TREE_SHA": tree}
                code = subprocess.run(["bash", "-c", block], env=env,
                                      capture_output=True).returncode
                return code, pushed.exists()

            self.assertEqual(run("0\n"), (0, True))
            for case in (dict(exit_text=None), dict(exit_text="1\n"),
                         dict(exit_text="3\n"), dict(exit_text=""),
                         dict(exit_text="0\n", head="moved"), dict(exit_text="0\n", tree="")):
                code, did_push = run(**case)
                self.assertFalse(did_push, f"pushed main on {case}")
                self.assertNotEqual(code, 0, case)


if __name__ == "__main__":
    unittest.main()
