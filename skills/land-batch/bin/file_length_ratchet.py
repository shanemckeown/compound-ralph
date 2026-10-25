#!/usr/bin/env python3
"""Regenerate a LAND candidate's baseline with verified attribution; never push.

Commands run at --repo's root. --command-json injects the single-test command
for portable fixture tests; production uses the existing gated Jest wrapper.
Every subprocess has command/raw-output/exit-code evidence in a fresh directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys

BASELINE = "tests/meta/file-length-baseline.json"
# Beads exports are generated churn, never part of the baseline-only commit.
CLEAN_PATHSPEC = ["--", ".", ":(top,exclude).beads/**",
                  ":(top,exclude)issues.jsonl", ":(top,exclude)export-state.json"]
BEAD = re.compile(r"(?:AestheticcNext|LUCY)-[a-z0-9.]+")
DEFAULT_COMMAND = [
    # The repo-root scripts/ beside this skill: under run-gate, the export's copy (LUCY-p122j r7).
    "python3", str(Path(__file__).resolve().parents[3] / "scripts/gated-test-run.py"), "jest",
    "--selectProjects", "node-tests", "--runTestsByPath",
    "tests/meta/file-length-ratchet.test.ts", "--runInBand",
]


class Hold(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class Gate:
    def __init__(self, repo, evidence, command, timeout=600):
        self.repo = Path(repo).resolve()
        requested = Path(evidence).resolve()
        self.evidence = requested
        attempt = 1
        while True:
            try:
                self.evidence.mkdir(parents=True, exist_ok=False)
                break
            except FileExistsError:
                attempt += 1
                self.evidence = requested.with_name(f"{requested.name}-{attempt}")
        self.command = command
        self.timeout = timeout
        self.sequence = 0
        self.result = {"valid": False, "repo": str(self.repo),
                       "evidence_dir": str(self.evidence)}

    def write_json(self, name, value):
        (self.evidence / name).write_text(json.dumps(value, indent=2) + "\n")

    def run(self, label, command, *, grow_bead=None, category="ratchet-command-failed"):
        self.sequence += 1
        prefix = self.evidence / f"{self.sequence:02d}-{label}"
        env = os.environ.copy()
        # A caller's UPDATE_BASELINE must never turn a check into a repair.
        env.pop("UPDATE_BASELINE", None)
        env.pop("FILE_LENGTH_GROW_OK", None)
        if grow_bead:
            env.update(UPDATE_BASELINE="1", FILE_LENGTH_GROW_OK=grow_bead)
        rendered = "env -u UPDATE_BASELINE -u FILE_LENGTH_GROW_OK "
        if grow_bead:
            rendered += f"UPDATE_BASELINE=1 FILE_LENGTH_GROW_OK={shlex.quote(grow_bead)} "
        Path(f"{prefix}-command.txt").write_text(
            f"cd {shlex.quote(str(self.repo))}\n{rendered}{shlex.join(command)}\n",
            errors="surrogateescape")
        try:
            proc = subprocess.Popen(command, cwd=self.repo, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    start_new_session=True)
            try:
                output, errors = proc.communicate(timeout=self.timeout)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                output, errors = proc.communicate()
                errors += f"\nTimed out after {self.timeout}s; killed process group\n".encode()
                code = 124
        except OSError as exc:
            output, errors, code = b"", str(exc).encode(errors="surrogateescape"), 127
        Path(f"{prefix}-raw-output.txt").write_bytes(output + errors)
        Path(f"{prefix}-exit-code.txt").write_text(f"{code}\n")
        if code in (124, 127, 134, 137, -signal.SIGABRT, -signal.SIGKILL) or any(
                marker in output + errors for marker in (
                    b"Test suite failed to run", b"Cannot find module")):
            raise Hold("ratchet-infra", f"{label} infrastructure failure (exit {code}); "
                       f"fix scratch dependencies/runner and retry; see {prefix}-raw-output.txt")
        if code:
            raise Hold(category, f"{label} exited {code}; see {prefix}-raw-output.txt")
        return output.decode("utf-8", errors="surrogateescape")

    def git(self, label, *args):
        return self.run(label, ["git", *args])

    def require_clean(self):
        status = self.git("status", "status", "--porcelain", "--untracked-files=all", *CLEAN_PATHSPEC)
        if status:
            raise Hold("ratchet-dirty-tree", "Scratch must be clean, with all new source tracked: " + status)

    def identity(self, expected=None):
        root = self.git("root", "rev-parse", "--show-toplevel").strip()
        if Path(root).resolve() != self.repo:
            raise Hold("ratchet-invalid-input", "--repo must be the scratch worktree root")
        head = self.git("head", "rev-parse", "HEAD").strip()
        self.result["head_before"] = head
        if expected and self.git("expected-head", "rev-parse", expected).strip() != head:
            raise Hold("ratchet-invalid-input", "HEAD differs from the pinned expected SHA")
        return head

    def check(self, expected=None):
        self.identity(expected)
        self.require_clean()
        self.require_jest()
        self.run("check", self.command, category="ratchet-check-red")
        self.require_clean()

    def require_jest(self):
        jest = self.repo / "node_modules/.bin/jest"
        if not jest.is_file() or not os.access(jest, os.X_OK):
            raise Hold("ratchet-infra", f"Missing or non-executable {jest}; fix the scratch's "
                       "node_modules symlink to the repo's installed dependencies "
                       "and ensure node_modules/.bin/jest is executable, then retry")

    def resolve_conflict(self):
        self.identity()
        merge_head = self.git("merge-head-path", "rev-parse", "--git-path", "MERGE_HEAD").strip()
        if not (self.repo / merge_head).is_file():
            raise Hold("ratchet-no-merge-in-progress", "The merge refused to START (usually "
                       "staged/dirty Beads state); clean that state and retry. "
                       "Do not treat this as a conflict or call merge --abort")
        conflicts = self.git("unmerged", "diff", "--name-only", "--diff-filter=U", "-z")
        paths = set(filter(None, conflicts.split("\0")))
        if paths != {BASELINE}:
            raise Hold("ratchet-authored-conflict", "Expected only baseline conflict; found: " + repr(sorted(paths)))
        self.git("merge-head", "rev-parse", "--verify", "MERGE_HEAD")
        self.git("integration-baseline", "show", f"HEAD:{BASELINE}")
        self.git("resolve-baseline", "restore", "--source=HEAD", "--staged", "--worktree", "--", BASELINE)
        self.result["resolved"] = BASELINE
        self.result["next"] = "Finish the merge, sensitive-file check, then regenerate; do not push yet"

    @staticmethod
    def baseline(text):
        try:
            value = json.loads(text)
            assert value["version"] == 1
            assert isinstance(value["files"], dict) and value["files"]
            assert isinstance(value["growAllowances"], dict)
            assert all(isinstance(k, str) and type(v) is int and v >= 0
                       for k, v in value["files"].items())
            assert all(k in value["files"] and isinstance(v, str) and BEAD.fullmatch(v)
                       for k, v in value["growAllowances"].items())
            return value
        except (ValueError, KeyError, AssertionError, TypeError) as exc:
            raise Hold("ratchet-malformed-baseline", "Invalid version-1 baseline") from exc

    def verify_delta(self, before, after, changed, bead, tree):
        old, new = before["files"], after["files"]
        dropped = old.keys() - new.keys()
        dropped_present = dropped & tree
        raised = {p for p, count in new.items() if count > old.get(p, 350)}
        allowances = after["growAllowances"]
        reattributed = {p for p, owner in allowances.items()
                       if owner != before["growAllowances"].get(p)}
        outside = (raised | reattributed) - changed
        bad_owners = {p for p in new if allowances.get(p) != (
            bead if p in raised else before["growAllowances"].get(p))}
        summary = {
            "bead_id": bead, "candidate_changed_files": sorted(changed),
            "raised": [{"path": p, "before": old.get(p), "after": new[p],
                        "bead_id": allowances.get(p)} for p in sorted(raised)],
            "allowance_changes": sorted(reattributed), "out_of_scope": sorted(outside),
            "invalid_allowance_owners": sorted(bad_owners),
            "dropped": sorted(dropped), "dropped_present": sorted(dropped_present),
            "entries": [{"path": p, "before": old.get(p), "after": new.get(p)}
                        for p in sorted(old.keys() | new.keys()) if old.get(p) != new.get(p)],
        }
        self.write_json("baseline-summary.json", summary)
        if dropped_present:
            raise Hold("ratchet-entries-dropped", "Baseline entries dropped for files still in "
                       "the merged tree: " + ", ".join(sorted(dropped_present)))
        if outside:
            raise Hold("ratchet-unrelated-growth", "Growth/attribution outside candidate: " + ", ".join(sorted(outside)))
        if bad_owners:
            raise Hold("ratchet-invalid-attribution", "Unexpected allowance ownership: " + ", ".join(sorted(bad_owners)))

    def regenerate(self, merge, branch, bead):
        if not bead or not BEAD.fullmatch(bead):
            raise Hold("ratchet-missing-bead", "Use the resolved candidate bead_id, never RUN_ID")
        if not merge or not branch:
            raise Hold("ratchet-invalid-input", "--merge and --branch are required")
        head = self.identity()
        self.require_clean()
        merge = self.git("merge-sha", "rev-parse", "--verify", merge).strip()
        parents = self.git("parents", "rev-list", "--parents", "-n", "1", head).split()
        message = f"chore(ratchet): regenerate file-length baseline for {branch} ({bead})"
        if head != merge:
            if len(parents) == 2 and parents[1] == merge:
                paths = self.git("retry-files", "diff", "--name-only", "--no-renames",
                                 "-z", merge, head, "--").split("\0")
                subject = self.git("retry-subject", "log", "-1", "--format=%s").strip()
                if set(filter(None, paths)) == {BASELINE} and subject == message:
                    self.result.update(merge_sha=merge, branch=branch, bead_id=bead,
                                       commit_sha=head, commit_message=message)
                    return
            raise Hold("ratchet-invalid-input", "HEAD differs from the pinned merge SHA "
                       "and is not its matching baseline-only ratchet commit")
        if len(parents) != 3:
            raise Hold("ratchet-invalid-input", "HEAD must be the just-created two-parent --no-ff merge")
        self.require_jest()
        changed = set(filter(None, self.git(
            "candidate-files", "diff", "--name-only", "--no-renames", "-z", parents[1], head, "--").split("\0")))
        generator_changes = {p for p in changed if p in (
            "tests/meta/file-length-ratchet.test.ts", "jest.config.js")
            or Path(p).name.startswith("jest.setup")}
        if generator_changes:
            raise Hold("ratchet-generator-changed", "Candidate changes ratchet/Jest execution: "
                       + ", ".join(sorted(generator_changes)))
        tree = set(filter(None, self.git(
            "merged-tree", "ls-tree", "-r", "--name-only", "-z", head).split("\0")))
        before_text = self.git("parent-baseline", "show", f"{parents[1]}:{BASELINE}")
        before = self.baseline(before_text)
        path = self.repo / BASELINE
        original = path.read_bytes() if path.exists() else None
        self.result.update(merge_sha=head, branch=branch, bead_id=bead, base_sha=parents[1])
        try:
            # Even a clean merge may have imported obsolete counts/allowances.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(before_text, encoding="utf-8", errors="surrogateescape")
            self.run("regenerate", self.command, grow_bead=bead)
            after = self.baseline(path.read_text(encoding="utf-8", errors="surrogateescape"))
            (self.evidence / "baseline-before.json").write_text(
                before_text, encoding="utf-8", errors="surrogateescape")
            (self.evidence / "baseline-after.json").write_bytes(path.read_bytes())
            diff = self.git("baseline-diff", "diff", parents[1], "--", BASELINE)
            (self.evidence / "baseline.diff").write_text(diff, errors="surrogateescape")
            self.verify_delta(before, after, changed, bead, tree)
            generated = path.read_bytes()
            self.run("check", self.command, category="ratchet-check-red")
            if generated != path.read_bytes():
                raise Hold("ratchet-check-mutated", "Check mode modified the baseline")
            dirty = set(filter(None, self.git(
                "dirty-files", "diff", "--name-only", "HEAD", "-z", *CLEAN_PATHSPEC).split("\0")))
            untracked = self.git("untracked", "ls-files", "--others", "--exclude-standard", "-z", *CLEAN_PATHSPEC)
            if dirty - {BASELINE} or untracked:
                raise Hold("ratchet-dirty-tree", "Ratchet command changed files outside the baseline")
            if self.git("baseline-status", "diff", "HEAD", "--", BASELINE):
                # Beads hooks stage/export unrelated state even for --only commits.
                # Scope this exception to the already-verified generated artifact.
                self.git("commit", "-c", "core.hooksPath=/dev/null", "commit",
                         "--only", "-m", message, "--", BASELINE)
                self.result["commit_sha"] = self.git("committed-head", "rev-parse", "HEAD").strip()
                self.result["commit_message"] = message
            else:
                self.result["commit_sha"] = None
            self.require_clean()
        except Exception:
            # Restore only our generated working file; caller owns scratch rollback.
            # After a successful commit do not undo that commit's working copy.
            if not self.result.get("commit_sha"):
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "resolve-conflict", "regenerate"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--evidence-dir", required=True, help="Fresh directory per invocation")
    parser.add_argument("--expected-head")
    parser.add_argument("--merge")
    parser.add_argument("--branch")
    parser.add_argument("--bead-id")
    parser.add_argument("--command-json", help="Test command argv JSON; defaults to gated single-file Jest")
    parser.add_argument("--timeout", type=float, default=600, help="Subprocess timeout in seconds")
    args = parser.parse_args()
    if not 0 < args.timeout < float("inf"):
        parser.error("--timeout must be positive and finite")
    gate = None
    try:
        command = json.loads(args.command_json) if args.command_json else DEFAULT_COMMAND
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise Hold("ratchet-invalid-input", "--command-json must be a nonempty string array")
        gate = Gate(args.repo, args.evidence_dir, command, args.timeout)
        if args.action == "check":
            gate.check(args.expected_head)
        elif args.action == "resolve-conflict":
            gate.resolve_conflict()
        else:
            gate.regenerate(args.merge, args.branch, args.bead_id)
        gate.result["valid"] = True
        result = gate.result
    except (Hold, OSError, ValueError) as exc:
        result = gate.result if gate else {"valid": False}
        result.update(hold=getattr(exc, "category", "ratchet-command-failed"), error=str(exc))
    if gate:
        gate.write_json("result.json", result)
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
