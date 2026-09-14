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
import subprocess
import sys

BASELINE = "tests/meta/file-length-baseline.json"
# Beads exports are generated churn, never part of the baseline-only commit.
CLEAN_PATHSPEC = ["--", ".", ":(top,exclude).beads/**"]
BEAD = re.compile(r"(?:AestheticcNext|LUCY)-[a-z0-9.]+")
DEFAULT_COMMAND = [
    "python3", str(Path.home() / ".claude/scripts/gated-test-run.py"), "jest",
    "--selectProjects", "node-tests", "--runTestsByPath",
    "tests/meta/file-length-ratchet.test.ts",
]


class Hold(RuntimeError):
    def __init__(self, category, message):
        super().__init__(message)
        self.category = category


class Gate:
    def __init__(self, repo, evidence, command):
        self.repo = Path(repo).resolve()
        self.evidence = Path(evidence).resolve()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.command = command
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
            f"cd {shlex.quote(str(self.repo))}\n{rendered}{shlex.join(command)}\n")
        try:
            proc = subprocess.run(command, cwd=self.repo, env=env, capture_output=True,
                                  timeout=600)
            output, errors, code = proc.stdout, proc.stderr, proc.returncode
        except (OSError, subprocess.TimeoutExpired) as exc:
            output, errors, code = b"", str(exc).encode(), 124 if isinstance(
                exc, subprocess.TimeoutExpired) else 127
        Path(f"{prefix}-raw-output.txt").write_bytes(output + errors)
        Path(f"{prefix}-exit-code.txt").write_text(f"{code}\n")
        if code:
            raise Hold(category, f"{label} exited {code}; see {prefix}-raw-output.txt")
        return output.decode("utf-8")

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
        self.run("check", self.command, category="ratchet-check-red")
        self.require_clean()

    def resolve_conflict(self):
        self.identity()
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

    def verify_delta(self, before, after, changed, bead):
        old, new = before["files"], after["files"]
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
            "entries": [{"path": p, "before": old.get(p), "after": new.get(p)}
                        for p in sorted(old.keys() | new.keys()) if old.get(p) != new.get(p)],
        }
        self.write_json("baseline-summary.json", summary)
        if outside:
            raise Hold("ratchet-unrelated-growth", "Growth/attribution outside candidate: " + ", ".join(sorted(outside)))
        if bad_owners:
            raise Hold("ratchet-invalid-attribution", "Unexpected allowance ownership: " + ", ".join(sorted(bad_owners)))

    def regenerate(self, merge, branch, bead):
        if not bead or not BEAD.fullmatch(bead):
            raise Hold("ratchet-missing-bead", "Use the resolved candidate bead_id, never RUN_ID")
        if not merge or not branch:
            raise Hold("ratchet-invalid-input", "--merge and --branch are required")
        head = self.identity(merge)
        self.require_clean()
        parents = self.git("parents", "rev-list", "--parents", "-n", "1", head).split()
        if len(parents) != 3:
            raise Hold("ratchet-invalid-input", "HEAD must be the just-created two-parent --no-ff merge")
        changed = set(filter(None, self.git(
            "candidate-files", "diff", "--name-only", "--no-renames", "-z", parents[1], head, "--").split("\0")))
        before_text = self.git("parent-baseline", "show", f"{parents[1]}:{BASELINE}")
        before = self.baseline(before_text)
        path = self.repo / BASELINE
        original = path.read_bytes() if path.exists() else None
        self.result.update(merge_sha=head, branch=branch, bead_id=bead, base_sha=parents[1])
        try:
            # Even a clean merge may have imported obsolete counts/allowances.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(before_text)
            self.run("regenerate", self.command, grow_bead=bead)
            after = self.baseline(path.read_text())
            (self.evidence / "baseline-before.json").write_text(before_text)
            (self.evidence / "baseline-after.json").write_bytes(path.read_bytes())
            diff = self.git("baseline-diff", "diff", parents[1], "--", BASELINE)
            (self.evidence / "baseline.diff").write_text(diff)
            self.verify_delta(before, after, changed, bead)
            generated = path.read_bytes()
            self.run("check", self.command, category="ratchet-check-red")
            if generated != path.read_bytes():
                raise Hold("ratchet-check-mutated", "Check mode modified the baseline")
            dirty = set(filter(None, self.git(
                "dirty-files", "diff", "--name-only", "HEAD", "-z", *CLEAN_PATHSPEC).split("\0")))
            untracked = self.git("untracked", "ls-files", "--others", "--exclude-standard", "-z", *CLEAN_PATHSPEC)
            if dirty - {BASELINE} or untracked:
                raise Hold("ratchet-dirty-tree", "Ratchet command changed files outside the baseline")
            message = f"chore(ratchet): regenerate file-length baseline for {branch} ({bead})"
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
    args = parser.parse_args()
    gate = None
    try:
        command = json.loads(args.command_json) if args.command_json else DEFAULT_COMMAND
        if not isinstance(command, list) or not command or not all(isinstance(x, str) for x in command):
            raise Hold("ratchet-invalid-input", "--command-json must be a nonempty string array")
        gate = Gate(args.repo, args.evidence_dir, command)
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
