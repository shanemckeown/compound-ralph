#!/usr/bin/env python3
"""builder-remote-qa.py: a builder's or reviewer's HEAVY test run, on Cloud Build (LUCY-ll8dh).

    builder-remote-qa.py [--sha HEAD] [--max-inflight N] [--admit-wait 600] [--poll 30] [--keep-scratch]

Run from an AestheticcNext worktree root. Fast targeted tests stay local (targeted-tests.py);
this is the heavy set: full node + dom + mobile jest, the full pg lane, lint, tsc and the
production build, i.e. the two lanes of the land gate's cloudbuild-gate.yaml, run by the
existing runner ~/.claude/scripts/cloudbuild-gate-remote.py (unchanged).

1. Resolve --sha to an exact commit. The scratch is `git archive <sha>` into a fresh temp dir,
   never the worktree, so every verdict names the tree it tested. A dirty worktree is not
   tested: a note says the run covers the committed sha only. If the sha's tree has a
   .gitattributes naming export-ignore / export-subst / filter=lfs, a .gitmodules or a
   submodule, an archive would not be that tree: no remote run (`FALLBACK local: archive would
   differ from sha (<reason>)`), and the local lane's scratch is an exact checkout-index copy.
2. Admission (Cloud Build quota cloudbuild.googleapis.com/ongoing_builds, quota id
   OngoingBuildsPerProject, project aestheticc, region global; shared with prod builds and
   landing gates). The project limit is read once per run:
   `gcloud beta quotas preferences describe ongoing-builds-20 --project=aestheticc
   --format=value(quotaConfig.grantedValue)`; a failed, empty or non-integer read means 10.
   The cap for builder runs is limit - BUILDER_QA_RESERVE, so 10 -> 6 and 20 -> 16. The reserve
   (for prod + landing) is never below MIN_RESERVE = 4: a lower BUILDER_QA_RESERVE is clamped,
   and BUILDER_QA_MAX_INFLIGHT / --max-inflight can only LOWER the cap (a higher one is clamped
   to limit - 4, with a note).
   A run adds 2 builds. Admission is atomic laptop-wide: under an flock on RESERVATIONS it
   lists the ongoing build ids, reconciles every pending reservation (an admitted builder whose
   builds may not be visible yet) and admits only if ongoing + pending + 2 <= cap. Each build
   counts once: listed as ongoing, or still pending, never both; a reservation's printed id
   that is terminal frees its slot. A reservation is held until its ids show as ongoing or
   terminal, or its owner pid exits (no age expiry: a slow upload keeps its slot). Otherwise
   poll every --poll seconds up to --admit-wait, then fall back local. A failed list, or a
   corrupt ledger (`FALLBACK local: reservation ledger unreadable (<reason>)`; the file is
   moved aside with a timestamp), is infra: fall back local, never submit blind.
   Builders on other machines are not covered by the lock; this one laptop runs the fleet.
3. Remote verdict: runner exit 0 WITH its `RESULT unit=SUCCESS pg=SUCCESS exit 0` line ->
   `REMOTE GREEN @<sha>`, exit 0. Exit 1 WITH a `FAILED LANE` line -> `REMOTE RED @<sha>` and
   that line, exit 1. Anything else (2 refused, 137 infra, 127, a 1 with no FAILED LANE line,
   a 0 with no RESULT line, uv or gcloud missing, any exception) is infra: never green, never
   a code red. It prints `FALLBACK local: <reason>` and runs the local lane on the same
   scratch (node_modules linked from the worktree). The local lane covers every step of both
   remote lanes (LOCAL_STEPS names the cloudbuild-gate.yaml step ids each one stands for; a
   test pins them): tsc-heavy, lint, node + dom jest, mobile jest, the full pg lane under
   pg-lane-lock.py, then `npm run build`, stopping at the first failure. Its exit is the
   verdict: `LOCAL GREEN @<sha>` (0), `LOCAL RED @<sha>` (that step's exit), or `LOCAL INFRA
   @<sha>` (no node_modules, lock unavailable, command missing: still non-zero).
   SIGTERM/SIGINT pass to the runner's process group (it cancels its builds) and exit 130 with
   no fallback. If the runner has not exited after KILL_GRACE (longer than its own signal
   join bound plus its cancels), its group is SIGKILLed and every build id it printed gets a
   best-effort `gcloud builds cancel`.
4. BUILDER_QA_REMOTE=0 forces local, =1 remote; unset or unrecognised = REMOTE_DEFAULT
   (tier_gates.gate_remote()'s pattern).
5. Evidence: sha, mode, build ids, wall and queue-wait seconds on stdout, and one JSON line
   appended to ~/.claude/state/builder-remote-qa.jsonl (BUILDER_QA_LOG overrides).

Never exits 0 unless a green was observed. Never deploys, never touches traffic.
Every gcloud call goes through one runner (CLOUDBUILD_GATE_GCLOUD names another binary);
tests inject fakes into main().
"""
import argparse
import fcntl
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
RUNNER = SCRIPTS / "cloudbuild-gate-remote.py"
GATED = [sys.executable, str(SCRIPTS / "gated-test-run.py")]
PG_LOCK = [sys.executable, str(SCRIPTS / "pg-lane-lock.py"), "--"]
TIER_GATES = SCRIPTS.parent / "skills" / "land-batch" / "bin" / "tier_gates.py"
PROJECT, REGION = "aestheticc", "global"
QUOTA_PREFERENCE = "ongoing-builds-20"
FALLBACK_LIMIT = 10  # the project's granted ongoing-builds limit when it cannot be read
MIN_RESERVE = 4  # builds always kept free for prod builds and landing gates; never lower
BUILDS_PER_RUN = 2  # the runner submits two lanes (unit + pg)
GCLOUD_TIMEOUT = 90
RESERVATIONS = Path.home() / ".claude" / "state" / "builder-remote-qa.reservations"
# The runner's SIGTERM path: SIGNAL_JOIN_BOUND (20 s) for hung submits, then a cancel per lane
# (each a quick gcloud call, normally seconds). Wait well past both before SIGKILL.
KILL_GRACE = 180
# A local step exit that means "could not run", not "the code is red".
LOCAL_INFRA_CODES = {3, 126, 127}  # pg-lane-lock unavailable, cannot execute, not found
# BUILDER_QA_REMOTE=1 / =0 picks remote / local; unset uses this.
REMOTE_DEFAULT = True
LOG = Path.home() / ".claude" / "state" / "builder-remote-qa.jsonl"
GREEN_LINE = re.compile(r"^RESULT unit=SUCCESS pg=SUCCESS exit 0\s*$", re.M)
FAILED_LANE = re.compile(r"^FAILED LANE \S+: .*$", re.M)
LANE_BUILD = re.compile(r"^LANE (\S+) build (\S+)", re.M)
EXIT_GREEN, EXIT_RED, EXIT_LOCAL_UNAVAILABLE, EXIT_INTERRUPTED = 0, 1, 3, 130
# The gate's next-build placeholders (cloudbuild-gate.yaml): no secrets, the same module graph.
BUILD_ENV = {"NODE_OPTIONS": "--max-old-space-size=6144", "NEXT_TELEMETRY_DISABLED": "1",
             "NEXT_DISABLE_WEBPACK_CACHE": "1", "NEXTAUTH_URL": "https://aestheti.cc",
             "NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY": "pk_test_land_gate_placeholder",
             "NEXT_PUBLIC_RECAPTCHA_SITE_KEY": "land-gate-placeholder",
             "NEXT_PUBLIC_POSTHOG_KEY": "phc_land_gate_placeholder",
             "NEXT_PUBLIC_SENTRY_DSN": "", "NEXT_PUBLIC_SENTRY_ENVIRONMENT": "land-gate"}


class Interrupted(BaseException):
    """Raised by the SIGTERM/SIGINT handler. BaseException, so no `except Exception` eats it."""


# ---------------------------------------------------------------- pure plan functions

def qa_remote(env=None):
    value = (os.environ if env is None else env).get("BUILDER_QA_REMOTE", "").strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return REMOTE_DEFAULT


def parse_limit(result):
    """(limit, note) from the quota read's (code, stdout, stderr); FALLBACK_LIMIT unless clean."""
    code, out, err = result
    value = (out or "").strip().strip("'\"")
    if code != 0:
        return FALLBACK_LIMIT, f"quota read failed (exit {code}: {(err or '').strip()[-200:]}); limit {FALLBACK_LIMIT}"
    if not value.isdigit() or int(value) <= 0:
        return FALLBACK_LIMIT, f"quota read returned {value!r}; limit {FALLBACK_LIMIT}"
    return int(value), f"granted limit {int(value)}"


def builder_cap(limit, env=None, override=None):
    """(cap, note): the ongoing builds a builder run may bring the project to.

    The reserve never drops below MIN_RESERVE and an override can only lower the cap.
    """
    env = os.environ if env is None else env
    notes = []
    reserve = MIN_RESERVE
    raw_reserve = env.get("BUILDER_QA_RESERVE", "").strip()
    if raw_reserve.isdigit():
        reserve = int(raw_reserve)
        if reserve < MIN_RESERVE:
            notes.append(f"BUILDER_QA_RESERVE={reserve} clamped to {MIN_RESERVE}")
            reserve = MIN_RESERVE
    ceiling = max(0, limit - reserve)
    cap, how = ceiling, f"limit {limit} - reserve {reserve}"
    raw = override if override is not None else env.get("BUILDER_QA_MAX_INFLIGHT", "").strip()
    if str(raw).strip():
        try:
            wanted = int(raw)
        except ValueError:
            wanted = None
        if wanted is not None and 0 <= wanted <= ceiling:
            cap, how = wanted, f"override (ceiling {ceiling})"
        elif wanted is not None and wanted > ceiling:
            notes.append(f"override {wanted} clamped to limit {limit} - reserve {reserve} = {ceiling}")
    note = f"cap {cap} = {how}" + (f" ({'; '.join(notes)})" if notes else "")
    return cap, note


def remote_verdict(code, output):
    """('green'|'red'|'infra', reason). Green and red each need the runner's own evidence line."""
    if code == 0 and GREEN_LINE.search(output or ""):
        return "green", "both lanes SUCCESS"
    if code == 1:
        failed = FAILED_LANE.findall(output or "")
        if failed:
            return "red", failed[-1]
        return "infra", "runner exit 1 with no FAILED LANE line (not a test result)"
    if code == 0:
        return "infra", "runner exit 0 with no RESULT line (not an observed green)"
    return "infra", f"runner exit {code}"


def build_ids(output):
    return {lane: build for lane, build in LANE_BUILD.findall(output or "")}


# Every cloudbuild-gate.yaml step id the local lane stands in for. npm-ci is the linked
# node_modules (the worktree's install), the one remote step with no local twin.
LOCAL_EQUIVALENT_OF_NPM_CI = "npm-ci"
NO_PG = ("RUN_PG_TESTS", "PG_LANE_LOCK_TOKEN")
# (label, gate step ids covered, command, env overrides, env removals); cheapest first.
LOCAL_STEPS = (
    ("tsc-heavy", ("lint-typecheck",), [*GATED, "tsc-heavy", "--noEmit"], {}, NO_PG),
    ("lint", ("lint-typecheck",), ["npm", "run", "lint"], {}, NO_PG),
    ("node + dom jest", ("unit-tests",), [*GATED, "jest"], {}, NO_PG),
    ("mobile jest", ("mobile-tests",), ["bash", "scripts/ci/run-mobile-tests.sh"], {}, NO_PG),
    ("full pg lane", ("pg-tests",), [*PG_LOCK, *GATED, "jest", "--selectProjects", "pg-tests"],
     {"RUN_PG_TESTS": "true", "TZ": "UTC"}, ("SKIP_PG_TESTS", "PG_LANE_LOCK_TOKEN")),
    ("npm run build", ("next-build",), ["npm", "run", "build"], BUILD_ENV, ("PG_LANE_LOCK_TOKEN",)),
)


def local_steps():
    """The local fallback lane, in order: (label, command, env overrides, env removals)."""
    return [(label, list(cmd), dict(over), rem) for label, _, cmd, over, rem in LOCAL_STEPS]


def covered_gate_steps():
    return {LOCAL_EQUIVALENT_OF_NPM_CI} | {sid for _, ids, *_ in LOCAL_STEPS for sid in ids}


def step_env(base, overrides, removals):
    env = {k: v for k, v in base.items() if k not in removals}
    env.update(overrides)
    return env


# ---------------------------------------------------------------- side effects

def run_gcloud(args):
    """The only place gcloud runs. Returns (returncode, stdout, stderr); never raises."""
    binary = os.environ.get("CLOUDBUILD_GATE_GCLOUD") or "gcloud"
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True, timeout=GCLOUD_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "", f"gcloud {' '.join(args[:3])} timed out after {GCLOUD_TIMEOUT}s"
    except OSError as exc:
        return 127, "", f"could not execute {binary}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def read_limit(gcloud):
    try:
        result = gcloud(["beta", "quotas", "preferences", "describe", QUOTA_PREFERENCE,
                         f"--project={PROJECT}", "--format=value(quotaConfig.grantedValue)", "--quiet"])
    except Exception as exc:  # noqa: BLE001 (unreadable = the fallback limit; Interrupted is not an Exception)
        result = (127, "", str(exc))
    return parse_limit(result)


def ongoing_ids(gcloud):
    """The set of ongoing build ids, or None when the list cannot be read (infra)."""
    try:
        code, out, _ = gcloud(["builds", "list", "--ongoing", f"--project={PROJECT}",
                               f"--region={REGION}", "--format=value(id)", "--quiet"])
    except Exception:  # noqa: BLE001 (could not count = infra; Interrupted is not an Exception)
        return None
    if code != 0:
        return None
    return {line.strip() for line in (out or "").splitlines() if line.strip()}


def build_status(gcloud, build_id):
    """A build's status (e.g. WORKING, SUCCESS), or None when it cannot be read."""
    try:
        code, out, _ = gcloud(["builds", "describe", build_id, f"--project={PROJECT}",
                               f"--region={REGION}", "--format=value(status)", "--quiet"])
    except Exception:  # noqa: BLE001 (unknown = still pending, the safe side)
        return None
    if code != 0:
        return None
    return (out or "").strip() or None


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


class LedgerCorrupt(Exception):
    """The reservation ledger cannot be trusted; admission fails closed."""


def entry_problem(entry):
    """Why a ledger entry is malformed, or None."""
    if not isinstance(entry, dict):
        return f"entry {entry!r} is not an object"
    pid = entry.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return f"entry pid {pid!r} is not a positive integer"
    if not isinstance(entry.get("token"), str) or not entry["token"]:
        return "entry token missing"
    at = entry.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool):
        return f"entry time {at!r} is not a number"
    ids = entry.get("build_ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, str) and i for i in ids):
        return f"entry build_ids {ids!r} is not a list of ids"
    return None


RUNNING_STATUSES = {"QUEUED", "PENDING", "WORKING"}


def reconcile(entries, listed, status_of):
    """(entries to keep, pending build count). Pure apart from status_of(build_id).

    An entry with no build ids yet is 2 pending builds. A printed id that is in the ongoing
    list counts there, never also as pending. One that is terminal (a status outside
    RUNNING_STATUSES) frees its slot. One that is neither (not visible yet, or unreadable) is
    still pending. A lane whose id has not printed yet is pending too. An entry whose builds
    are all listed or terminal is done and dropped.
    """
    keep, pending = [], 0
    for entry in entries:
        ids = entry.get("build_ids", [])
        not_printed = max(0, BUILDS_PER_RUN - len(ids))
        still = []
        for build_id in ids:
            if build_id in listed:
                continue
            status = status_of(build_id)
            if status is None or status in RUNNING_STATUSES:
                still.append(build_id)
        if still or not_printed:
            keep.append(entry)
            pending += len(still) + not_printed
    return keep, pending


class Reservations:
    """Laptop-wide pending-submit ledger: a JSON list in `path`, changed only under its flock.

    An entry is held until its build ids are visible as ongoing or terminal (reconcile), or its
    owner pid exits (release on exit, or dead-pid cleanup). There is no age-based expiry, so a
    slow upload keeps its slot. A corrupt ledger fails closed (LedgerCorrupt) and is renamed
    aside with a timestamp so the next run starts clean. A reused pid keeps a stale entry alive
    (the safe side: it over-reserves, never under-reserves).
    """

    def __init__(self, path=None, now=time.time, alive=pid_alive):
        self.path = Path(path or RESERVATIONS)
        self.now, self.alive = now, alive
        self.token = None

    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path.with_name(self.path.name + ".lock"), "a+")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    def _load(self):
        """Entries whose owner is alive; raises LedgerCorrupt (file moved aside) when untrusted."""
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return []
        except OSError as exc:
            self._quarantine(f"unreadable: {exc}")
        try:
            entries = json.loads(text)
        except ValueError as exc:
            self._quarantine(f"not JSON: {exc}")
        if not isinstance(entries, list):
            self._quarantine("not a list")
        for entry in entries:
            problem = entry_problem(entry)
            if problem:
                self._quarantine(problem)
        return [e for e in entries if self.alive(e["pid"])]  # a dead owner never submits

    def _quarantine(self, reason):
        stamp = time.strftime("%Y%m%dT%H%M%S")
        aside = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{os.getpid()}")
        try:
            os.replace(self.path, aside)
            where = f"moved aside to {aside}"
        except OSError as exc:
            where = f"left in place ({exc})"
        raise LedgerCorrupt(f"{reason}; {where}")

    def _save(self, entries):
        tmp = self.path.with_name(self.path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(entries))
        os.replace(tmp, self.path)

    def try_reserve(self, list_ids_fn, cap, status_fn=lambda build_id: None):
        """Under the lock: (state, ongoing, pending, reason).

        state is admitted / full / list-failed / ledger-corrupt; only admitted records us.
        """
        handle = self._locked()
        try:
            try:
                entries = self._load()
            except LedgerCorrupt as exc:
                return "ledger-corrupt", None, None, str(exc)
            listed = list_ids_fn()
            if listed is None:
                return "list-failed", None, None, "gcloud builds list failed"
            statuses = {}

            def status_of(build_id):
                if build_id not in statuses:
                    statuses[build_id] = status_fn(build_id)
                return statuses[build_id]
            entries, pending = reconcile(entries, set(listed), status_of)
            count = len(listed)
            admitted = count + pending + BUILDS_PER_RUN <= cap
            if admitted:
                self.token = f"{os.getpid()}-{time.monotonic_ns()}-{id(self)}"
                entries.append({"pid": os.getpid(), "at": self.now(), "token": self.token,
                                "build_ids": []})
            self._save(entries)
            return ("admitted" if admitted else "full"), count, pending, None
        finally:
            handle.close()  # drops the flock

    def record_ids(self, build_ids):
        """Our builds are submitted: keep the entry, now with ids, until the list shows them."""
        ids = sorted(set(build_ids))
        self._update(lambda entry: {**entry, "build_ids": ids})

    def release(self):
        """The owner is exiting (its builds are terminal or cancelled): drop its entry."""
        self._update(lambda entry: None)
        self.token = None

    def _update(self, change):
        if self.token is None:
            return
        handle = self._locked()
        try:
            try:
                entries = self._load()
            except LedgerCorrupt:
                return  # moved aside; the next admission starts clean
            out = []
            for entry in entries:
                if entry["token"] == self.token:
                    entry = change(entry)
                if entry is not None:
                    out.append(entry)
            self._save(out)
        finally:
            handle.close()


def admit(gcloud, cap, wait, poll, sleep, clock, say, reservations):
    """(admitted, waited seconds, reason). Reconciles, counts and reserves atomically per poll."""
    started = clock()
    announced = False
    while True:
        state, count, pending, why = reservations.try_reserve(
            lambda: ongoing_ids(gcloud), cap, lambda build_id: build_status(gcloud, build_id))
        waited = clock() - started
        if state == "ledger-corrupt":
            return False, waited, f"reservation ledger unreadable ({why})"
        if state == "list-failed":
            return False, waited, "gcloud builds list failed (cannot count ongoing builds; not submitting blind)"
        sums = f"{count} ongoing + {pending} pending + {BUILDS_PER_RUN}"
        if state == "admitted":
            return True, waited, f"{sums} <= cap {cap}"
        if waited >= wait:
            return False, waited, f"admission: {sums} > cap {cap} after {waited:.0f}s wait"
        if not announced:
            say(f"QUEUE: {sums} > cap {cap}; polling every {poll:.0f}s for up to {wait:.0f}s")
            announced = True
        sleep(poll)


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


ARCHIVE_ATTRS = ("export-ignore", "export-subst", "filter=lfs")


def archive_problems(entries, read_blob):
    """Why `git archive <sha>` would not reproduce the sha's tree; [] when it does.

    entries: (mode, type, object, path) rows of `git ls-tree -r --full-tree <sha>`;
    read_blob(object) -> text. export-ignore drops files, export-subst rewrites bytes, an LFS
    filter leaves pointer files, and a submodule is not archived at all.
    """
    problems = []
    for _mode, kind, obj, path in entries:
        name = path.rsplit("/", 1)[-1]
        if kind == "commit":
            problems.append(f"submodule {path}")
        elif name == ".gitmodules":
            problems.append(f"{path} exists")
        elif name == ".gitattributes":
            text = read_blob(obj)
            problems += [f"{path} sets {attr}" for attr in ARCHIVE_ATTRS if attr in text]
    return problems


def tree_entries(sha, worktree):
    rows = []
    for line in git("ls-tree", "-r", "--full-tree", sha, cwd=worktree).splitlines():
        meta, _, path = line.partition("\t")
        mode, kind, obj = meta.split()
        rows.append((mode, kind, obj, path))
    return rows


def sha_archive_problems(sha, worktree):
    return archive_problems(tree_entries(sha, worktree),
                            lambda obj: git("cat-file", "-p", obj, cwd=worktree))


def make_scratch(sha, worktree, exact=False):
    """A fresh dir holding the tree of `sha`. Default: git archive | tar -x (what the remote run
    uploads). exact=True (archive_problems found a difference): read-tree into a throwaway index
    and checkout-index, so export-* attributes cannot drop or rewrite files for the local lane."""
    scratch = Path(tempfile.mkdtemp(prefix=f"builder-qa-{sha[:12]}-"))
    if exact:
        index = scratch.parent / f"{scratch.name}.index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        try:
            for args in (["read-tree", sha], ["checkout-index", "-a", "-f", f"--prefix={scratch}/"]):
                subprocess.run(["git", *args], cwd=worktree, env=env, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(scratch, ignore_errors=True)
            raise RuntimeError(f"exact checkout of {sha[:12]} failed: {exc.stderr!r}") from exc
        finally:
            index.unlink(missing_ok=True)
        return scratch
    archive = subprocess.Popen(["git", "archive", "--format=tar", sha], cwd=worktree,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    untar = subprocess.run(["tar", "-x", "-C", str(scratch)], stdin=archive.stdout,
                           capture_output=True)
    archive.stdout.close()
    archive_err = archive.stderr.read().decode(errors="replace")
    archive.stderr.close()
    if archive.wait() != 0 or untar.returncode != 0:
        shutil.rmtree(scratch, ignore_errors=True)
        raise RuntimeError(f"git archive {sha[:12]} failed: {archive_err.strip()[-300:]} "
                           f"{untar.stderr.decode(errors='replace').strip()[-300:]}")
    return scratch


def link_node_modules(scratch, worktree):
    """tier_gates.link_node_modules (the land scratch's): scratch/node_modules -> the worktree's."""
    if not (Path(worktree) / "node_modules").is_dir():  # follows a symlinked node_modules
        return None
    spec = importlib.util.spec_from_file_location("tier_gates", TIER_GATES)
    tier_gates = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tier_gates)
    return tier_gates.link_node_modules(scratch, worktree)



class Child:
    """The one running subprocess (its own process group) and the build ids seen so far."""

    def __init__(self):
        self.proc = None
        self.grace = KILL_GRACE
        self.build_ids = {}


def stream(command, cwd, env, say, child, grace=KILL_GRACE):
    """Run command in its own process group, echoing output line by line. Returns (code, output)."""
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
    child.proc, child.grace = proc, grace  # stays set on an exception, so main() can stop it
    lines = []
    for line in proc.stdout:
        lines.append(line)
        say(line.rstrip("\n"))
    code = proc.wait()
    child.proc = None
    return code, "".join(lines)


def _signal_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except OSError:
            pass


def stop_child(child, gcloud, say):
    """SIGTERM the child's group (the runner cancels its own builds); after child.grace, SIGKILL
    the group and best-effort `gcloud builds cancel` every build id it printed."""
    proc = child.proc
    if proc is None or proc.poll() is not None:
        return
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=child.grace)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    say(f"runner still running after {child.grace:.0f}s; group killed, cancelling "
        f"{len(child.build_ids)} build(s) directly")
    for build_id in child.build_ids.values():
        try:
            code, _, err = gcloud(["builds", "cancel", build_id, f"--project={PROJECT}",
                                   f"--region={REGION}", "--quiet"])
            say(f"cancel {build_id}: exit {code} {(err or '').strip()[-200:]}")
        except Exception as exc:  # noqa: BLE001 (best effort)
            say(f"cancel {build_id} failed: {exc}")


def run_remote(scratch, say, child):
    command = ["uv", "run", "--script", str(RUNNER), "--scratch", str(scratch)]
    return stream(command, str(scratch), dict(os.environ), say, child, grace=KILL_GRACE)


def run_local(scratch, worktree, say, child):
    """(code, label of the failed step or None)."""
    if not link_node_modules(scratch, worktree):
        say(f"LOCAL UNAVAILABLE: no node_modules in {worktree}")
        return EXIT_LOCAL_UNAVAILABLE, "node_modules"
    for label, command, overrides, removals in local_steps():
        say(f"==> local {label}: {' '.join(command)}")
        code, _ = stream(command, str(scratch), step_env(os.environ, overrides, removals), say, child,
                         grace=30)
        say(f"<== local {label}: exit {code}")
        if code != 0:
            return code, label
    return EXIT_GREEN, None


def append_log(record, path=None):
    path = Path(path or os.environ.get("BUILDER_QA_LOG") or LOG)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # evidence only; never changes the verdict


def _interrupt(signum, _frame):
    raise Interrupted(signal.Signals(signum).name)


# ---------------------------------------------------------------- main

def main(argv=None, gcloud=run_gcloud, remote=run_remote, local=run_local, scratch_maker=make_scratch,
         sleep=time.sleep, clock=time.monotonic, out=None, env=None, cwd=None, reservations=None,
         archive_check=None):
    out = out or sys.stdout
    env = os.environ if env is None else env

    def say(text):
        print(text, file=out, flush=True)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--max-inflight", default=None, help="lower the cap (else BUILDER_QA_MAX_INFLIGHT; "
                                                          "never above project limit - 4)")
    ap.add_argument("--admit-wait", type=float, default=600)
    ap.add_argument("--poll", type=float, default=30)
    ap.add_argument("--keep-scratch", action="store_true")
    args = ap.parse_args(argv)

    worktree = Path(cwd or os.getcwd())
    started = clock()
    child = Child()
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "worktree": str(worktree), "mode": None,
              "verdict": None, "exit": None, "build_ids": child.build_ids, "queue_wait_s": 0,
              "reason": None}
    reservations = reservations or Reservations(env.get("BUILDER_QA_RESERVATIONS"))
    holder = {}
    sigs = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.signal(sig, _interrupt) for sig in sigs}
    try:
        try:
            code = _run(args, worktree, env, gcloud, remote, local, scratch_maker, sleep, clock, say,
                        record, child, holder, reservations, archive_check or sha_archive_problems)
        except Interrupted as exc:
            for sig in sigs:
                signal.signal(sig, signal.SIG_IGN)
            stop_child(child, gcloud, say)
            say(f"INTERRUPTED by {exc}: no verdict @{record.get('sha', '?')}")
            record.update(verdict="interrupted", reason=str(exc))
            code = EXIT_INTERRUPTED
        except Exception as exc:  # noqa: BLE001 (a crash here is never a green)
            for sig in sigs:
                signal.signal(sig, signal.SIG_IGN)
            stop_child(child, gcloud, say)
            say(f"ERROR: {type(exc).__name__}: {exc}: no verdict @{record.get('sha', '?')}")
            record.update(verdict="error", reason=f"{type(exc).__name__}: {exc}")
            code = EXIT_LOCAL_UNAVAILABLE
        # Finalise with signals ignored, so evidence and cleanup always complete.
        for sig in sigs:
            signal.signal(sig, signal.SIG_IGN)
        try:
            reservations.release()
        except Exception as exc:  # noqa: BLE001 (a stale entry ages out; never changes the verdict)
            say(f"reservation release failed: {exc}")
        scratch = holder.get("scratch")
        if scratch is not None and not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
        elif scratch is not None:
            say(f"scratch kept: {scratch}")
        if code == 0 and record.get("verdict") not in ("remote-green", "local-green"):
            code = EXIT_LOCAL_UNAVAILABLE  # belt and braces: 0 only with an observed green
        record["exit"] = code
        record["wall_s"] = round(clock() - started, 1)
        say(f"EVIDENCE sha={record.get('sha')} mode={record['mode']} verdict={record['verdict']} "
            f"builds={','.join(f'{k}:{v}' for k, v in record['build_ids'].items()) or '-'} "
            f"wall={record['wall_s']}s queue_wait={record['queue_wait_s']}s")
        append_log(record, env.get("BUILDER_QA_LOG"))
        return code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _run(args, worktree, env, gcloud, remote, local, scratch_maker, sleep, clock, say, record, child,
         holder, reservations, archive_check):
    sha = git("rev-parse", "--verify", f"{args.sha}^{{commit}}", cwd=worktree).strip()
    record["sha"] = sha
    say(f"builder-remote-qa: sha {sha}")
    try:
        dirty = git("status", "--porcelain", cwd=worktree).strip()
    except subprocess.CalledProcessError:
        dirty = "unknown"
    if dirty:
        say(f"NOTE: the worktree has uncommitted changes; this run tests the committed sha {sha[:12]} only")
    record["dirty"] = bool(dirty)
    differs = archive_check(sha, worktree)  # finding 6: the upload must be the sha's exact tree
    scratch = holder["scratch"] = scratch_maker(sha, worktree, exact=bool(differs))

    def fall_back(reason):
        reservations.release()
        say(f"FALLBACK local: {reason}")
        record["reason"] = reason
        return _local(scratch, worktree, sha, local, say, record, child, mode="fallback")

    if not qa_remote(env):
        record["reason"] = "BUILDER_QA_REMOTE=0"
        say("mode local (BUILDER_QA_REMOTE=0)")
        return _local(scratch, worktree, sha, local, say, record, child, mode="local")
    if differs:
        return fall_back(f"archive would differ from sha ({'; '.join(differs)})")

    limit, limit_note = read_limit(gcloud)  # once per run, never cached across runs
    cap, cap_note = builder_cap(limit, env, args.max_inflight)
    record.update(limit=limit, cap=cap)
    say(f"admission: {limit_note}; {cap_note}")
    admitted, waited, why = admit(gcloud, cap, args.admit_wait, args.poll, sleep, clock, say, reservations)
    record["queue_wait_s"] = round(waited, 1)
    if not admitted:
        return fall_back(why)
    say(f"admitted: {why} (queue wait {waited:.0f}s)")

    def remote_say(line):
        say(line)
        match = LANE_BUILD.match(line)
        if match:
            child.build_ids[match.group(1)] = match.group(2)
            if len(child.build_ids) >= BUILDS_PER_RUN:
                # Held, now with its ids, until the list shows them (reconcile); not released.
                reservations.record_ids(list(child.build_ids.values()))

    record["mode"] = "remote"
    try:
        code, output = remote(scratch, remote_say, child)
    except Exception as exc:  # noqa: BLE001 (uv missing, exec errors: infra; Interrupted propagates)
        return fall_back(f"runner could not run: {type(exc).__name__}: {exc}")
    record["build_ids"].update(build_ids(output))
    verdict, reason = remote_verdict(code, output)
    if verdict == "green":
        record.update(verdict="remote-green", reason=reason)
        say(f"REMOTE GREEN @{sha}")
        return EXIT_GREEN
    if verdict == "red":
        record.update(verdict="remote-red", reason=reason)
        say(f"REMOTE RED @{sha}")
        say(reason)
        return EXIT_RED
    return fall_back(reason)


def _local(scratch, worktree, sha, local, say, record, child, mode):
    record["mode"] = mode
    code, failed = local(scratch, worktree, say, child)
    if code == 0 and failed is None:
        record["verdict"] = "local-green"
        say(f"LOCAL GREEN @{sha}")
        return EXIT_GREEN
    code = code or EXIT_RED  # a failed step that somehow exited 0 is still not green
    record["failed_step"] = failed
    if code in LOCAL_INFRA_CODES or failed == "node_modules":
        record["verdict"] = "local-infra"
        say(f"LOCAL INFRA @{sha}: {failed} exit {code} (could not run; not a test result)")
    else:
        record["verdict"] = "local-red"
        say(f"LOCAL RED @{sha}: {failed} exit {code}")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
