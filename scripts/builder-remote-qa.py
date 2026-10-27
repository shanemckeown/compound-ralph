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
   Admission is a slot semaphore: N = (limit - BUILDER_QA_RESERVE) // 2 runs (each run is 2
   builds), so 10 -> 3 and 20 -> 8. The reserve (for prod + landing) is never below
   MIN_RESERVE = 4: a lower BUILDER_QA_RESERVE is clamped, and BUILDER_QA_MAX_INFLIGHT /
   --max-inflight (in builds) can only LOWER N (a higher one is clamped, with a note).
   A run takes a free slot by flock(LOCK_EX | LOCK_NB) on SLOT_DIR/slot-<i> (i < N), polling
   every --poll seconds up to --admit-wait, then falls back local. The locked fd stays open for
   the whole run and is passed to the runner (pass_fds), so the kernel holds the slot until the
   last process with the fd exits: SIGKILL, pid reuse and a runner that outlives this wrapper
   are all covered with no bookkeeping. (uv passes the fd on to the runner; the runner's own
   short gcloud calls do not inherit it, so the slot lives exactly as long as the runner.) The
   holder note in the file is for humans only. No ledger is kept (only the .limit value below).
   One capacity value for everyone: under a short gate lock (SLOT_DIR/.admit) a caller reads
   SLOT_DIR/.limit (the granted limit the previous caller read), uses min(its own read, .limit)
   for its slot count and its deny-only guard, then writes its own read to .limit, so a lowered
   quota binds every caller at once. Holders of ANY slot-* file at or above that count (from a
   higher limit) count as occupied, so at most N runs are ever held. SLOT_DIR is always
   <account home from the password database>/.claude/state/builder-qa-slots: no env var or
   HOME value can select another one (tests pass main(slots_dir=...)).
   The slot outlives every run whose runner was launched but gave no green/red verdict (exit
   130, infra, a lost response, ids never printed, one id only). It is held until a FIXED
   bound, measured on our own clock: wall time just before the runner was launched + the gate
   config's timeout (2100 s) + a 20 min queue allowance, raised to startTime + timeout + 10 min
   whenever `gcloud builds describe` gives a later startTime (Cloud Build times a build from
   its execution start). The only early release: both lanes' build ids are known and describe
   shows each one TERMINAL (polled every 30 s). Describe errors and unknown statuses keep
   holding. A fallback local lane runs while the slot is still held; the rest of the bound is
   held at exit. A second SIGTERM/SIGINT during the exit wait ends the process (default
   action) and the kernel frees the slot: accepted.
   Deny-only guard, while holding the slot: one `gcloud builds list --ongoing`; if ongoing + 2 >
   the granted limit, or the list fails, the slot is dropped and the run falls back local. That
   read can only deny, never admit beyond the slots, so a lagging list is harmless.
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
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
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
EXPECTED_LANES = ("unit", "pg")  # the runner submits exactly these two builds
QUEUE_ALLOWANCE = 1200  # s a submitted build may sit queued before it starts executing
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


def builder_slots(limit, env=None, override=None):
    """(slots, note): N = (limit - reserve) // 2 concurrent builder runs (2 builds each).

    The reserve never drops below MIN_RESERVE; an override (BUILDER_QA_MAX_INFLIGHT or
    --max-inflight, in builds) can only lower N.
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
    ceiling = max(0, limit - reserve) // BUILDS_PER_RUN
    slots, how = ceiling, f"(limit {limit} - reserve {reserve}) // {BUILDS_PER_RUN}"
    raw = override if override is not None else env.get("BUILDER_QA_MAX_INFLIGHT", "").strip()
    if str(raw).strip():
        try:
            wanted = int(raw) // BUILDS_PER_RUN
        except ValueError:
            wanted = None
        if wanted is not None and 0 <= wanted <= ceiling:
            slots, how = wanted, f"override {raw} builds (ceiling {ceiling})"
        elif wanted is not None and wanted > ceiling:
            notes.append(f"override {raw} builds clamped to {ceiling} slots")
    note = f"{slots} slots = {how}" + (f" ({'; '.join(notes)})" if notes else "")
    return slots, note


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


class Slot:
    """One held builder slot: an open fd with an exclusive flock on slot-<index>.

    The kernel drops the lock only when every process holding a copy of the fd has exited or
    closed it. The fd is inheritable and passed to the runner (pass_fds), so a SIGKILLed
    wrapper whose runner is still uploading keeps the slot until the runner dies too.
    """

    def __init__(self, fd, index, path, limit=None, slots=None):
        self.fd, self.index, self.path = fd, index, path
        self.limit, self.slots = limit, slots  # the effective capacity it was admitted under

    def release(self):
        if self.fd is not None:
            try:
                os.close(self.fd)  # this process's copy; children holding it keep the lock
            except OSError:
                pass
            self.fd = None


def slot_dir():
    """The one slot directory for this account: from the password database, never HOME or any
    env var, so every launcher on this machine shares one semaphore. (Tests pass their own
    directory to main(slots_dir=...); no runtime setting can select another one.)"""
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".claude" / "state" / "builder-qa-slots"


def slot_index(path):
    """slot-<i> -> i; any other slot-* name -> None (still probed, never admitted to)."""
    suffix = path.name[len("slot-"):]
    return int(suffix) if suffix.isdigit() else None


def read_shared_limit(directory):
    """The granted limit the last admitted caller read (slots/.limit), or None."""
    try:
        text = (Path(directory) / ".limit").read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def write_shared_limit(directory, limit):
    try:
        (Path(directory) / ".limit").write_text(f"{int(limit)}\n")
    except OSError:
        pass  # the next caller then trusts only its own read: still capped by it


def slot_held(path):
    """True if another open file description holds slot `path` (a probe lock is dropped at once)."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # cannot probe: count it as held
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True  # EWOULDBLOCK: someone holds it
    finally:
        os.close(fd)  # closing drops any probe lock we did take
    return False


def try_acquire(directory, slots, note="", limit=None, slots_for=None):
    """A Slot below the effective slot count whose flock is free, or None.

    Everything happens under a short directory gate lock (.admit), so there is one capacity
    value for everyone. With `limit` (this caller's granted-limit read) and `slots_for`
    (limit -> slots): the effective limit is min(limit, slots/.limit), the effective slot count
    min(slots, slots_for(that)), and slots/.limit is then set to this caller's read, so a
    lowered quota binds every caller at once. Holders of ANY slot-* file at or above the
    effective count (left from a higher limit) count as occupied, so at most that many runs
    are ever held. Files are never deleted. The note (pid, sha, time) is for humans only.
    The returned Slot carries the effective limit for the deny-only guard.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    gate = os.open(directory / ".admit", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(gate, fcntl.LOCK_EX)
        effective = limit
        if limit is not None:
            shared = read_shared_limit(directory)
            if shared is not None and shared < limit:
                effective = shared
                if slots_for is not None:
                    slots = min(slots, slots_for(shared))
            write_shared_limit(directory, limit)
        if slots <= 0:
            return None
        held_high = sum(slot_held(path) for path in directory.glob("slot-*")
                        if slot_index(path) is None or slot_index(path) >= slots)
        free, held_low = [], 0
        for index in range(slots):
            if slot_held(directory / f"slot-{index}"):
                held_low += 1
            else:
                free.append(index)
        if held_low + held_high >= slots:
            return None
        for index in free:
            path = directory / f"slot-{index}"
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            os.set_inheritable(fd, True)
            try:
                os.ftruncate(fd, 0)
                os.pwrite(fd, note.encode(), 0)
            except OSError:
                pass  # the note is cosmetic
            return Slot(fd, index, path, effective, slots)
        return None
    finally:
        os.close(gate)


TERMINAL_STATUSES = {"SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"}
DEFAULT_GATE_TIMEOUT = 2100
TERMINAL_MARGIN = 600  # Cloud Build has killed a build by createTime + timeout + this
TERMINAL_POLL = 30


def gate_timeout(scratch):
    """The gate config's top-level build timeout in seconds (2100 if it cannot be read)."""
    try:
        text = (Path(scratch) / "cloudbuild-gate.yaml").read_text()
    except OSError:
        return DEFAULT_GATE_TIMEOUT
    match = re.search(r"^timeout:\s*['\"]?(\d+)s?['\"]?\s*$", text, re.M)
    return int(match.group(1)) if match else DEFAULT_GATE_TIMEOUT


def _epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _seconds(value):
    match = re.fullmatch(r"(\d+)(?:\.\d+)?s?", str(value or ""))
    return int(match.group(1)) if match else None


def describe_build(gcloud, build_id):
    """(status or None, startTime epoch or None, build timeout s or None). Errors read as unknown."""
    try:
        code, out, _ = gcloud(["builds", "describe", build_id, f"--project={PROJECT}",
                               f"--region={REGION}", "--format=json", "--quiet"])
        if code != 0:
            return None, None, None
        build = json.loads(out)
    except Exception:  # noqa: BLE001 (unknown = keep holding)
        return None, None, None
    if not isinstance(build, dict):
        return None, None, None
    return build.get("status"), _epoch(build.get("startTime")), _seconds(build.get("timeout"))


def hold_until_terminal(gcloud, build_ids, timeout, submit_start, sleep, wall, say):
    """Block (still holding the slot) until the hold may end. Returns True on a confirmed early
    release, False at the bound.

    The bound is FIXED from our own clock: submit_start (wall time just before the runner was
    launched) + the gate timeout + QUEUE_ALLOWANCE. Cloud Build measures its timeout from
    startTime, so whenever describe gives a startTime, the bound is raised to the latest
    startTime + timeout + TERMINAL_MARGIN: a build whose execution started late is never
    released while it can still run. The only early release: BOTH expected lanes have a known
    build id and describe shows each in TERMINAL_STATUSES. Missing ids (a lost response, an
    interrupt before the ids printed, one lane only), describe errors and unknown statuses all
    hold to the bound.
    """
    ids = {lane: build_ids.get(lane) for lane in EXPECTED_LANES}
    known = {lane: b for lane, b in ids.items() if b}
    bound = submit_start + timeout + QUEUE_ALLOWANCE
    terminal = set()
    announced = False
    while True:
        for lane, build_id in known.items():
            if lane in terminal:
                continue
            status, started, build_timeout = describe_build(gcloud, build_id)
            if started is not None:
                bound = max(bound, started + max(timeout, build_timeout or 0) + TERMINAL_MARGIN)
            if status in TERMINAL_STATUSES:
                terminal.add(lane)
        if len(known) == len(EXPECTED_LANES) and terminal == set(EXPECTED_LANES):
            return True
        if wall() >= bound:
            say(f"HOLD: hard bound reached with lanes {sorted(set(EXPECTED_LANES) - terminal)} not "
                f"confirmed terminal; releasing the slot")
            return False
        if not announced:
            missing = [lane for lane in EXPECTED_LANES if lane not in known]
            say(f"HOLD: keeping the builder slot (known builds: "
                f"{', '.join(known.values()) or 'none'}; unknown lanes: {', '.join(missing) or 'none'}) "
                f"until both are confirmed terminal or {max(0, bound - wall()):.0f}s pass")
            announced = True
        sleep(TERMINAL_POLL)

def admit(gcloud, slots, limit, wait, poll, sleep, clock, say, directory, note, slots_for=None):
    """(slot or None, waited seconds, reason).

    1. Take a free slot (poll every `poll` s up to `wait` s). The slots are the admission.
    2. Deny-only guard, while holding it: one `gcloud builds list --ongoing`. If ongoing + 2 >
       the granted limit, or the list fails, drop the slot and fall back. This read can only
       DENY a run the slots already admitted, never admit one beyond them, so a list that lags
       behind recent submits is harmless here (it may let a run through that the slots allow;
       it can never add a run the slots refused).
    """
    started = clock()
    announced = False
    slot = None
    while slot is None:
        slot = try_acquire(directory, slots, note, limit, slots_for) if slots > 0 else None
        waited = clock() - started
        if slot is not None:
            break
        if slots <= 0:
            return None, waited, "no builder slots (limit - reserve < 2 builds)"
        if waited >= wait:
            return None, waited, f"admission: all {slots} builder slots busy after {waited:.0f}s wait"
        if not announced:
            say(f"QUEUE: all {slots} builder slots busy; polling every {poll:.0f}s for up to {wait:.0f}s")
            announced = True
        sleep(poll)
    try:
        listed = ongoing_ids(gcloud)
    except BaseException:  # an interrupt mid-guard: drop the slot now, not at process exit
        slot.release()
        raise
    if listed is None:
        slot.release()
        return None, waited, "gcloud builds list failed (deny-only guard cannot read; not submitting blind)"
    if slot.limit is not None:
        limit = min(limit, slot.limit)  # the shared (lowest current) limit, read under .admit
    if len(listed) + BUILDS_PER_RUN > limit:
        slot.release()
        return None, waited, (f"deny-only guard: {len(listed)} ongoing + {BUILDS_PER_RUN} > granted "
                              f"limit {limit}")
    return slot, waited, f"slot {slot.index} of {slots}; {len(listed)} ongoing + {BUILDS_PER_RUN} <= {limit}"


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
        self.pass_fds = ()  # the held slot's fd, for the runner only


def stream(command, cwd, env, say, child, grace=KILL_GRACE, pass_fds=()):
    """Run command in its own process group, echoing output line by line. Returns (code, output)."""
    proc = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True,
                            pass_fds=tuple(pass_fds))
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
    # The slot fd goes to the runner, so its lock lives as long as the runner does.
    return stream(command, str(scratch), dict(os.environ), say, child, grace=KILL_GRACE,
                  pass_fds=child.pass_fds)


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


def unresolved_submit(holder):
    """True once the runner was launched and gave no green/red verdict: builds may be running."""
    return holder.get("submit_start") is not None and not holder.get("terminal_ok")


def release_slot(holder, child, gcloud, sleep, wall, say):
    """Close the held slot. After any launch of the runner that ended without its own green/red
    verdict (exit 130, infra, a lost response, ids never printed, one id only), first hold it
    (hold_until_terminal) until both lanes are confirmed terminal or the fixed bound passes."""
    slot = holder.get("slot")
    if slot is None or slot.fd is None:
        return
    if unresolved_submit(holder):
        timeout = gate_timeout(holder.get("scratch") or ".")
        holder["terminal_ok"] = hold_until_terminal(gcloud, child.build_ids, timeout,
                                                    holder["submit_start"], sleep, wall, say)
    slot.release()
    child.pass_fds = ()


# ---------------------------------------------------------------- main

def main(argv=None, gcloud=run_gcloud, remote=run_remote, local=run_local, scratch_maker=make_scratch,
         sleep=time.sleep, clock=time.monotonic, out=None, env=None, cwd=None, archive_check=None,
         wall=time.time, slots_dir=None):
    out = out or sys.stdout
    env = os.environ if env is None else env

    def say(text):
        print(text, file=out, flush=True)

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha", default="HEAD")
    ap.add_argument("--max-inflight", default=None, help="builds; can only lower the slot count (else "
                                                          "BUILDER_QA_MAX_INFLIGHT; never above limit - 4)")
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
    slots_dir = Path(slots_dir) if slots_dir is not None else slot_dir()  # tests inject theirs
    holder = {}
    sigs = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.signal(sig, _interrupt) for sig in sigs}
    try:
        try:
            code = _run(args, worktree, env, gcloud, remote, local, scratch_maker, sleep, clock, say,
                        record, child, holder, slots_dir, archive_check or sha_archive_problems, wall)
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
        # Hold the slot until any submitted build is confirmed finished. During this wait a
        # second SIGTERM/SIGINT ends the process (default action), which is accepted: the
        # kernel then drops the slot at exit, the one case where it can free early.
        for sig in sigs:
            signal.signal(sig, signal.SIG_DFL)
        release_slot(holder, child, gcloud, sleep, wall, say)
        # Finalise with signals ignored, so evidence and cleanup always complete.
        for sig in sigs:
            signal.signal(sig, signal.SIG_IGN)
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
         holder, slots_dir, archive_check, wall):
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
        # Nothing submitted: drop the slot before the local lane. Submitted without a verdict:
        # keep holding it through the local lane; main() holds the rest of the bound at exit.
        if not unresolved_submit(holder):
            release_slot(holder, child, gcloud, sleep, wall, say)
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
    slots, slots_note = builder_slots(limit, env, args.max_inflight)
    record.update(limit=limit, slots=slots)
    say(f"admission: {limit_note}; {slots_note}")
    note = f"pid {os.getpid()} sha {sha} since {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n"
    slot, waited, why = admit(gcloud, slots, limit, args.admit_wait, args.poll, sleep, clock, say,
                              slots_dir, note, lambda lim: builder_slots(lim, env, args.max_inflight)[0])
    record["queue_wait_s"] = round(waited, 1)
    if slot is None:
        return fall_back(why)
    holder["slot"] = slot
    record["slot"] = slot.index
    child.pass_fds = (slot.fd,)
    say(f"admitted: {why} (queue wait {waited:.0f}s)")

    def remote_say(line):
        say(line)
        match = LANE_BUILD.match(line)
        if match:
            child.build_ids[match.group(1)] = match.group(2)

    record["mode"] = "remote"
    holder["submit_start"] = wall()  # before launch: from here builds may exist we cannot see
    try:
        code, output = remote(scratch, remote_say, child)
    except Exception as exc:  # noqa: BLE001 (uv missing, exec errors: infra; Interrupted propagates)
        if child.proc is None and not child.build_ids:
            holder.pop("submit_start", None)  # the runner never started: nothing was submitted
        return fall_back(f"runner could not run: {type(exc).__name__}: {exc}")
    record["build_ids"].update(build_ids(output))
    verdict, reason = remote_verdict(code, output)
    if verdict in ("green", "red"):
        holder["terminal_ok"] = True  # the runner only exits 0/1 once both lanes are terminal
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
