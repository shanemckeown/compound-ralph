#!/usr/bin/env python3
"""Detect, reconcile, and prove Drizzle migration batches for /land-batch.

The module is standard-library-only.  Discovery imports the hash scanner; the
LAND integration gate invokes the CLI after selected branches are merged into
its private scratch worktree.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from land_batch_safety import MIGRATION_SQL_RE


JOURNAL_PATH = Path("drizzle/meta/_journal.json")
SNAPSHOT_SUFFIX = "_snapshot.json"
MISSING = object()


class MigrationBatchError(RuntimeError):
    """A fail-closed migration batch error."""


def _run(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    check: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        input=input_bytes,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=check,
    )


def _git(repo: str | Path, args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=timeout,
        check=False,
    )


def _git_text(repo: str | Path, args: list[str], *, timeout: int = 120) -> str:
    result = _git(repo, args, timeout=timeout)
    if result.returncode != 0:
        raise MigrationBatchError(
            f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return result.stdout.decode("utf-8", errors="replace")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationBatchError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationBatchError(f"expected JSON object in {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(value)
    os.replace(temporary, path)


def migration_number(path: str) -> str | None:
    match = MIGRATION_SQL_RE.fullmatch(path)
    return match.group(0).split("/", 1)[1][:4] if match else None


def added_migration_claims(
    repo: str | Path,
    base_ref: str,
    candidates: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read candidate-added migration SQL at each pinned tip and hash bytes."""
    claims: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for candidate in candidates:
        branch = candidate.get("branch")
        tip_sha = candidate.get("tip_sha")
        source_ref = candidate.get("source_ref")
        if not branch or not tip_sha:
            continue
        diff = _git(
            repo,
            [
                "diff",
                "--diff-filter=A",
                "--name-only",
                "-z",
                f"{base_ref}...{tip_sha}",
                "--",
                "drizzle/*.sql",
            ],
        )
        if diff.returncode != 0:
            errors.append(
                {
                    "branch": str(branch),
                    "error": diff.stderr.decode("utf-8", errors="replace").strip()
                    or "git diff failed",
                }
            )
            continue
        for raw_path in diff.stdout.split(b"\0"):
            if not raw_path:
                continue
            path = raw_path.decode("utf-8", errors="replace")
            number = migration_number(path)
            if number is None:
                continue
            content = _git(repo, ["show", f"{tip_sha}:{path}"])
            if content.returncode != 0:
                errors.append(
                    {
                        "branch": str(branch),
                        "path": path,
                        "error": content.stderr.decode("utf-8", errors="replace").strip()
                        or "git show failed",
                    }
                )
                continue
            claims.append(
                {
                    "number": number,
                    "branch": branch,
                    "source_ref": source_ref,
                    "tip_sha": tip_sha,
                    "path": path,
                    "sha256": hashlib.sha256(content.stdout).hexdigest(),
                    "size": len(content.stdout),
                }
            )
    claims.sort(key=lambda item: (item["number"], item["sha256"], item["branch"], item["path"]))
    return claims, errors


def base_migration_claims(
    repo: str | Path,
    base_ref: str,
    numbers: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Hash base migrations for numbers newly claimed by a candidate.

    A one-branch batch can still be unsafe when it adds a second filename for a
    number that already exists on the landing base.  Include only relevant base
    paths so discovery catches that case without bloating every report with the
    complete migration history.
    """
    wanted = set(numbers)
    if not wanted:
        return [], []
    listing = _git(repo, ["ls-tree", "-r", "-z", "--name-only", base_ref, "--", "drizzle"])
    if listing.returncode != 0:
        return [], [
            {
                "branch": f"BASE:{base_ref}",
                "error": listing.stderr.decode("utf-8", errors="replace").strip()
                or "git ls-tree failed",
            }
        ]

    claims: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for raw_path in listing.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="replace")
        number = migration_number(path)
        if number not in wanted:
            continue
        content = _git(repo, ["show", f"{base_ref}:{path}"])
        if content.returncode != 0:
            errors.append(
                {
                    "branch": f"BASE:{base_ref}",
                    "path": path,
                    "error": content.stderr.decode("utf-8", errors="replace").strip()
                    or "git show failed",
                }
            )
            continue
        claims.append(
            {
                "number": number,
                "branch": f"BASE:{base_ref}",
                "source_ref": base_ref,
                "tip_sha": base_ref,
                "path": path,
                "sha256": hashlib.sha256(content.stdout).hexdigest(),
                "size": len(content.stdout),
                "base_claim": True,
            }
        )
    return claims, errors


def classify_migration_claims(claims: Iterable[dict[str, Any]]) -> dict[str, Any]:
    claim_list = [dict(claim) for claim in claims]
    by_number: dict[str, list[dict[str, Any]]] = {}
    for claim in claim_list:
        by_number.setdefault(str(claim["number"]), []).append(claim)

    collisions = []
    inheritance = []
    for number, grouped_claims in sorted(by_number.items()):
        variants_by_hash: dict[str, list[dict[str, Any]]] = {}
        for claim in grouped_claims:
            variants_by_hash.setdefault(claim["sha256"], []).append(claim)
        variants = []
        for content_hash, variant_claims in sorted(variants_by_hash.items()):
            variants.append(
                {
                    "sha256": content_hash,
                    "branches": sorted({claim["branch"] for claim in variant_claims}),
                    "paths": sorted({claim["path"] for claim in variant_claims}),
                    "claims": variant_claims,
                }
            )
        branches = sorted({claim["branch"] for claim in grouped_claims})
        if len(variants) > 1:
            branch_pairs = set()
            for index, left in enumerate(variants):
                for right in variants[index + 1 :]:
                    for branch_a in left["branches"]:
                        for branch_b in right["branches"]:
                            branch_pairs.add(tuple(sorted((branch_a, branch_b))))
            collisions.append(
                {
                    "kind": "migration-number-collision",
                    "number": number,
                    "branches": branches,
                    "branch_pairs": [list(pair) for pair in sorted(branch_pairs)],
                    "variants": variants,
                }
            )
        for variant in variants:
            if len(variant["branches"]) > 1:
                inheritance.append(
                    {
                        "kind": "migration-inheritance",
                        "number": number,
                        "sha256": variant["sha256"],
                        "branches": variant["branches"],
                        "paths": variant["paths"],
                    }
                )
    return {
        "claims": claim_list,
        "collisions": collisions,
        "inheritance": inheritance,
        "collision_count": len(collisions),
        "inheritance_count": len(inheritance),
    }


def scan_candidate_migrations(
    repo: str | Path,
    base_ref: str,
    candidates: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    claims, errors = added_migration_claims(repo, base_ref, candidates)
    base_claims, base_errors = base_migration_claims(
        repo,
        base_ref,
        (claim["number"] for claim in claims),
    )
    claims.extend(base_claims)
    claims.sort(key=lambda item: (item["number"], item["sha256"], item["branch"], item["path"]))
    errors.extend(base_errors)
    result = classify_migration_claims(claims)
    result["errors"] = errors
    result["valid"] = not errors
    return result


def _entry_prefix(idx: int) -> str:
    return str(idx).zfill(4)


def _replace_prefix(tag: str, idx: int) -> str:
    if not re.match(r"^\d{4}_", tag):
        raise MigrationBatchError(f'migration tag does not start with four digits: "{tag}"')
    return re.sub(r"^\d{4}_", f"{_entry_prefix(idx)}_", tag, count=1)


def _journal_at_ref(repo: str | Path, base_ref: str) -> dict[str, Any]:
    raw = _git_text(repo, ["show", f"{base_ref}:{JOURNAL_PATH.as_posix()}"])
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MigrationBatchError(f"base journal at {base_ref} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
        raise MigrationBatchError(f"base journal at {base_ref} has no entries array")
    return value


def _json_at_ref(repo: str | Path, ref: str, path: str) -> dict[str, Any]:
    raw = _git_text(repo, ["show", f"{ref}:{path}"])
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MigrationBatchError(f"invalid JSON at {ref}:{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationBatchError(f"expected JSON object at {ref}:{path}")
    return value


def _source_migration_records(
    repo: Path,
    base_ref: str,
    candidates: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read migration SQL, entries, and snapshots from pinned candidate tips.

    Generated journal and numeric snapshot paths necessarily conflict when
    parallel branches all generate the same next index.  Reconstruction from
    pinned refs lets LAND defer only those generated conflicts while preserving
    each branch's exact authored SQL and schema snapshot.
    """
    claims, errors = added_migration_claims(repo, base_ref, candidates)
    if errors:
        raise MigrationBatchError("candidate migration source scan failed: " + json.dumps(errors))
    records = []
    journals: dict[str, dict[str, Any]] = {}
    for claim in claims:
        tip = str(claim["tip_sha"])
        if tip not in journals:
            journals[tip] = _json_at_ref(repo, tip, JOURNAL_PATH.as_posix())
        journal = journals[tip]
        tag = Path(claim["path"]).stem
        matching_entries = [entry for entry in journal.get("entries", []) if entry.get("tag") == tag]
        if len(matching_entries) != 1:
            raise MigrationBatchError(
                f"{claim['branch']}:{claim['path']} has {len(matching_entries)} matching journal entries"
            )
        entry = copy.deepcopy(matching_entries[0])
        if not isinstance(entry.get("when"), (int, float)) or not isinstance(entry.get("idx"), int):
            raise MigrationBatchError(f"candidate journal entry {tag} has invalid idx/when")
        snapshot = None
        snapshot_source = None
        for candidate_path in (
            f"drizzle/meta/{tag}{SNAPSHOT_SUFFIX}",
            f"drizzle/meta/{claim['number']}{SNAPSHOT_SUFFIX}",
        ):
            shown = _git(repo, ["show", f"{tip}:{candidate_path}"])
            if shown.returncode != 0:
                continue
            try:
                parsed = json.loads(shown.stdout)
            except json.JSONDecodeError as exc:
                raise MigrationBatchError(
                    f"invalid candidate snapshot {tip}:{candidate_path}: {exc}"
                ) from exc
            if not isinstance(parsed, dict):
                raise MigrationBatchError(f"expected object in {tip}:{candidate_path}")
            snapshot = parsed
            snapshot_source = candidate_path
            break
        if snapshot is None:
            raise MigrationBatchError(
                f"{claim['branch']}:{claim['path']} has no tag-named or numeric snapshot at its pinned tip"
            )
        sql = _git(repo, ["show", f"{tip}:{claim['path']}"])
        if sql.returncode != 0:
            raise MigrationBatchError(f"cannot reread pinned migration {tip}:{claim['path']}")
        if hashlib.sha256(sql.stdout).hexdigest() != claim["sha256"]:
            raise MigrationBatchError(f"pinned migration hash changed while scanning {claim['path']}")
        records.append(
            {
                "claim": claim,
                "entry": entry,
                "snapshot": snapshot,
                "snapshot_source": snapshot_source,
                "sql": sql.stdout,
            }
        )

    by_variant: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        claim = record["claim"]
        by_variant.setdefault((claim["number"], claim["sha256"]), []).append(record)

    unique_records = []
    inherited_claims = 0
    for _, variants in sorted(by_variant.items()):
        variants.sort(
            key=lambda record: (
                record["entry"].get("when", 0),
                record["entry"].get("tag", ""),
                record["claim"]["branch"],
            )
        )
        canonical = variants[0]
        canonical_body = _snapshot_body(canonical["snapshot"])
        for inherited in variants[1:]:
            if _snapshot_body(inherited["snapshot"]) != canonical_body:
                raise MigrationBatchError(
                    "byte-identical inherited migration SQL has divergent snapshot bodies: "
                    f"{canonical['claim']['branch']} and {inherited['claim']['branch']}"
                )
        unique_records.append(canonical)
        inherited_claims += len(variants) - 1

    tags: dict[str, str] = {}
    paths: dict[str, str] = {}
    for record in unique_records:
        tag = str(record["entry"].get("tag"))
        content_hash = record["claim"]["sha256"]
        if tag in tags and tags[tag] != content_hash:
            raise MigrationBatchError(f"authored migration tag {tag} has different contents; do not auto-resolve")
        path = record["claim"]["path"]
        if path in paths and paths[path] != content_hash:
            raise MigrationBatchError(f"authored migration path {path} has different contents; do not auto-resolve")
        tags[tag] = content_hash
        paths[path] = content_hash
    return unique_records, {
        "candidate_claim_count": len(records),
        "unique_migration_count": len(unique_records),
        "inherited_claim_count": inherited_claims,
        "source_paths": sorted(record["claim"]["path"] for record in records),
    }


def materialize_candidate_migrations(
    repo: str | Path,
    base_ref: str,
    candidates: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Reconstruct generated integration metadata from pinned candidate refs."""
    root = Path(repo).resolve()
    drizzle_dir = root / "drizzle"
    records, report = _source_migration_records(root, base_ref, candidates)
    all_claim_paths = set(report.pop("source_paths"))
    base_journal = _journal_at_ref(root, base_ref)

    base_listing = _git(root, ["ls-tree", "-r", "-z", "--name-only", base_ref, "--", "drizzle/meta"])
    if base_listing.returncode != 0:
        raise MigrationBatchError(base_listing.stderr.decode("utf-8", errors="replace").strip())
    base_snapshot_paths = {
        raw.decode("utf-8", errors="replace")
        for raw in base_listing.stdout.split(b"\0")
        if raw and raw.decode("utf-8", errors="replace").endswith(SNAPSHOT_SUFFIX)
    }
    base_snapshot_names = {Path(path).name for path in base_snapshot_paths}
    for snapshot_path in _snapshot_files(drizzle_dir):
        if snapshot_path.name not in base_snapshot_names:
            snapshot_path.unlink()
    for relative_path in sorted(base_snapshot_paths):
        source = _git(root, ["show", f"{base_ref}:{relative_path}"])
        if source.returncode != 0:
            raise MigrationBatchError(f"cannot restore base snapshot {base_ref}:{relative_path}")
        _write_bytes(root / relative_path, source.stdout)

    chosen_paths = {record["claim"]["path"] for record in records}
    for path in all_claim_paths - chosen_paths:
        candidate_path = root / path
        if candidate_path.exists():
            candidate_path.unlink()

    added_entries = []
    for record in records:
        entry = copy.deepcopy(record["entry"])
        tag = entry.get("tag")
        if not isinstance(tag, str) or Path(record["claim"]["path"]).stem != tag:
            raise MigrationBatchError(
                f"migration path/tag mismatch: {record['claim']['path']} vs {tag!r}"
            )
        _write_bytes(root / record["claim"]["path"], record["sql"])
        _write_json(drizzle_dir / "meta" / f"{tag}{SNAPSHOT_SUFFIX}", record["snapshot"])
        added_entries.append(entry)
    _write_json(
        root / JOURNAL_PATH,
        {**base_journal, "entries": copy.deepcopy(base_journal["entries"]) + added_entries},
    )
    return report


def _snapshot_files(drizzle_dir: Path) -> list[Path]:
    return sorted((drizzle_dir / "meta").glob(f"*{SNAPSHOT_SUFFIX}"))


def _snapshot_for_entry(drizzle_dir: Path, entry: dict[str, Any]) -> Path:
    tag_path = drizzle_dir / "meta" / f"{entry['tag']}{SNAPSHOT_SUFFIX}"
    numeric_path = drizzle_dir / "meta" / f"{_entry_prefix(int(entry['idx']))}{SNAPSHOT_SUFFIX}"
    if tag_path.exists():
        return tag_path
    if numeric_path.exists():
        return numeric_path
    raise MigrationBatchError(
        f"journal entry {entry.get('tag')} has no tag-named or numeric snapshot"
    )


def _snapshot_index(drizzle_dir: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    indexed = {}
    for snapshot_path in _snapshot_files(drizzle_dir):
        snapshot = _read_json(snapshot_path)
        snapshot_id = snapshot.get("id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise MigrationBatchError(f"snapshot has no id: {snapshot_path}")
        if snapshot_id in indexed and indexed[snapshot_id][1] != snapshot:
            raise MigrationBatchError(f"divergent snapshots share id {snapshot_id}")
        indexed[snapshot_id] = (snapshot_path, snapshot)
    return indexed


def _merge_snapshot_value(base: Any, current: Any, branch: Any, pointer: str = "$") -> Any:
    """Apply one branch snapshot delta onto the cumulative snapshot.

    This is a strict three-way merge.  Independent keys compose; incompatible
    edits to the same value fail loudly instead of silently dropping schema.
    """
    if branch == base:
        return copy.deepcopy(current)
    if current == base:
        return copy.deepcopy(branch)
    if current == branch:
        return copy.deepcopy(current)
    if all(isinstance(value, dict) for value in (base, current, branch)):
        merged = {}
        for key in sorted(set(base) | set(current) | set(branch)):
            base_value = base.get(key, MISSING)
            current_value = current.get(key, MISSING)
            branch_value = branch.get(key, MISSING)
            child_pointer = f"{pointer}.{key}"
            if branch_value is MISSING:
                if base_value is MISSING:
                    if current_value is not MISSING:
                        merged[key] = copy.deepcopy(current_value)
                    continue
                if current_value is MISSING or current_value == base_value:
                    continue
                raise MigrationBatchError(
                    f"snapshot conflict at {child_pointer}: branch deletes a concurrently changed value"
                )
            if base_value is MISSING:
                if current_value is MISSING or current_value == branch_value:
                    merged[key] = copy.deepcopy(branch_value)
                    continue
                raise MigrationBatchError(
                    f"snapshot conflict at {child_pointer}: two migrations add different values"
                )
            if current_value is MISSING:
                if branch_value == base_value:
                    continue
                raise MigrationBatchError(
                    f"snapshot conflict at {child_pointer}: branch changes a concurrently deleted value"
                )
            merged[key] = _merge_snapshot_value(
                base_value,
                current_value,
                branch_value,
                child_pointer,
            )
        return merged
    raise MigrationBatchError(
        f"snapshot conflict at {pointer}: both cumulative and branch snapshots changed the same value"
    )


def _snapshot_body(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if key not in ("id", "prevId")}


def rebuild_cumulative_snapshots(
    drizzle_dir: Path,
    base_entries: list[dict[str, Any]],
    added_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return cumulative snapshots in deterministic ``(when, tag)`` order."""
    if not base_entries:
        raise MigrationBatchError("cannot reconcile without a base journal tail")
    snapshot_index = _snapshot_index(drizzle_dir)
    base_tail = _read_json(_snapshot_for_entry(drizzle_dir, base_entries[-1]))
    current = copy.deepcopy(base_tail)
    rebuilt = []
    seen_snapshot_paths: set[Path] = set()

    for entry in added_entries:
        snapshot_path = _snapshot_for_entry(drizzle_dir, entry)
        if snapshot_path in seen_snapshot_paths:
            raise MigrationBatchError(
                f"two added journal entries resolve to {snapshot_path.name}; each migration needs its own snapshot"
            )
        seen_snapshot_paths.add(snapshot_path)
        branch_snapshot = _read_json(snapshot_path)
        parent_id = branch_snapshot.get("prevId")
        parent_record = snapshot_index.get(parent_id)
        if parent_record is None:
            raise MigrationBatchError(
                f"snapshot {snapshot_path.name} points to unknown prevId {parent_id!r}"
            )
        parent_snapshot = parent_record[1]
        merged_body = _merge_snapshot_value(
            _snapshot_body(parent_snapshot),
            _snapshot_body(current),
            _snapshot_body(branch_snapshot),
        )
        next_snapshot = {
            "id": branch_snapshot["id"],
            "prevId": current.get("id"),
            **merged_body,
        }
        rebuilt.append(
            {
                "entry": copy.deepcopy(entry),
                "original_snapshot_path": snapshot_path,
                "snapshot": next_snapshot,
            }
        )
        current = next_snapshot
    return rebuilt


def _validate_journal(
    drizzle_dir: Path,
    journal: dict[str, Any],
    *,
    enforce_tag_prefix_from: int | None = None,
) -> list[str]:
    errors = []
    previous_when = -1
    seen_tags = set()
    for position, entry in enumerate(journal.get("entries", [])):
        tag = entry.get("tag")
        if entry.get("idx") != position:
            errors.append(f"entry {position} has idx {entry.get('idx')}")
        if not isinstance(entry.get("when"), (int, float)) or entry["when"] <= previous_when:
            errors.append(f"entry {position} has non-increasing when {entry.get('when')}")
        else:
            previous_when = entry["when"]
        if not isinstance(tag, str) or tag in seen_tags:
            errors.append(f"entry {position} has duplicate/invalid tag {tag!r}")
        else:
            seen_tags.add(tag)
            if (
                enforce_tag_prefix_from is not None
                and position >= enforce_tag_prefix_from
                and not tag.startswith(f"{position:04d}_")
            ):
                errors.append(
                    f"entry {position} tag prefix does not match idx: {tag}"
                )
            if not (drizzle_dir / f"{tag}.sql").is_file():
                errors.append(f"entry {position} has no SQL file for {tag}")
    return errors


def _rename_via_staging(rename_pairs: list[tuple[Path, Path]]) -> None:
    staged = []
    for position, (source, destination) in enumerate(rename_pairs):
        if source == destination:
            continue
        if not source.exists():
            raise MigrationBatchError(f"cannot rename missing file {source}")
        temporary = source.with_name(f".land-batch-{position:04d}-{source.name}")
        if temporary.exists():
            raise MigrationBatchError(f"temporary reconciliation path already exists: {temporary}")
        source.rename(temporary)
        staged.append((temporary, destination))
    for temporary, destination in staged:
        if destination.exists():
            raise MigrationBatchError(f"refusing to overwrite migration artifact {destination}")
        temporary.rename(destination)


def _internal_reconcile(
    repo: Path,
    base_journal: dict[str, Any],
    current_journal: dict[str, Any],
    added_entries: list[dict[str, Any]],
    rebuilt: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Testable implementation of the documented ``(when, tag)`` recipe."""
    drizzle_dir = repo / "drizzle"
    next_entries = [copy.deepcopy(entry) for entry in base_journal["entries"]]
    previous_when = next_entries[-1]["when"] if next_entries else -1
    changes = []
    rename_pairs = []
    for original_entry, rebuilt_record in zip(added_entries, rebuilt):
        next_idx = len(next_entries)
        next_tag = _replace_prefix(original_entry["tag"], next_idx)
        next_when = max(original_entry["when"], previous_when + 1)
        next_entry = {**copy.deepcopy(original_entry), "idx": next_idx, "tag": next_tag, "when": next_when}
        old_sql = drizzle_dir / f"{original_entry['tag']}.sql"
        new_sql = drizzle_dir / f"{next_tag}.sql"
        old_snapshot = rebuilt_record["original_snapshot_path"]
        new_snapshot = drizzle_dir / "meta" / f"{next_tag}{SNAPSHOT_SUFFIX}"
        rename_pairs.extend(((old_sql, new_sql), (old_snapshot, new_snapshot)))
        next_entries.append(next_entry)
        previous_when = next_when
        changes.append(
            {
                "from_tag": original_entry["tag"],
                "to_tag": next_tag,
                "from_idx": original_entry["idx"],
                "to_idx": next_idx,
                "snapshot": new_snapshot.name,
            }
        )
    _rename_via_staging(rename_pairs)
    next_journal = {**current_journal, "entries": next_entries}
    _write_json(repo / JOURNAL_PATH, next_journal)
    return next_journal, changes, "internal-test-engine"


def _documented_reconcile(
    repo: Path,
    base_ref: str,
    added_entries: list[dict[str, Any]],
    rebuilt: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """Invoke AestheticcNext's documented reconciliation implementation.

    All added artifacts first move to their deterministic final-free staging
    tags.  That prevents the target helper's overwrite guard from tripping when
    an old 0241 must move out of the way of a colliding 0240.
    """
    runner = repo / "scripts/reconcile-drizzle-migrations.mjs"
    readme = repo / "drizzle/README.md"
    if not runner.is_file() or not readme.is_file():
        raise MigrationBatchError("documented Drizzle reconciliation recipe is unavailable")
    readme_text = readme.read_text(encoding="utf-8")
    if "db:reconcile-migrations" not in readme_text:
        raise MigrationBatchError("drizzle/README.md no longer documents reconciliation")

    drizzle_dir = repo / "drizzle"
    journal_path = repo / JOURNAL_PATH
    original_journal_text = journal_path.read_text(encoding="utf-8")
    staged_entries = []
    stage_renames = []
    restoration_pairs = []
    for position, (entry, rebuilt_record) in enumerate(zip(added_entries, rebuilt)):
        suffix = re.sub(r"^\d{4}_", "", entry["tag"])
        stage_tag = f"9999_{suffix}"
        if any(candidate["tag"] == stage_tag for candidate in staged_entries):
            stage_tag = f"9999_{position:04d}_{suffix}"
        staged_entry = {**copy.deepcopy(entry), "tag": stage_tag}
        staged_entries.append(staged_entry)
        old_sql = drizzle_dir / f"{entry['tag']}.sql"
        stage_sql = drizzle_dir / f"{stage_tag}.sql"
        old_snapshot = rebuilt_record["original_snapshot_path"]
        stage_snapshot = drizzle_dir / "meta" / f"{stage_tag}{SNAPSHOT_SUFFIX}"
        stage_renames.extend(((old_sql, stage_sql), (old_snapshot, stage_snapshot)))
        restoration_pairs.extend(((stage_sql, old_sql), (stage_snapshot, old_snapshot)))

    current_journal = json.loads(original_journal_text)
    base_tags = {entry["tag"] for entry in _journal_at_ref(repo, base_ref)["entries"]}
    base_entries_current = [entry for entry in current_journal["entries"] if entry["tag"] in base_tags]
    _rename_via_staging(stage_renames)
    _write_json(journal_path, {**current_journal, "entries": base_entries_current + staged_entries})
    try:
        result = _run(
            [
                "node",
                str(runner.relative_to(repo)),
                "--base-ref",
                base_ref,
                "--drizzle-dir",
                "drizzle",
            ],
            cwd=repo,
            timeout=180,
        )
        if result.returncode != 0:
            raise MigrationBatchError(
                "documented reconciliation failed: "
                + result.stderr.decode("utf-8", errors="replace").strip()
            )
    except Exception:
        journal_path.write_text(original_journal_text, encoding="utf-8")
        for staged_path, original_path in restoration_pairs:
            if staged_path.exists() and not original_path.exists():
                staged_path.rename(original_path)
        raise

    next_journal = _read_json(journal_path)
    next_added = next_journal["entries"][-len(added_entries) :] if added_entries else []
    changes = []
    for original_entry, next_entry in zip(added_entries, next_added):
        changes.append(
            {
                "from_tag": original_entry["tag"],
                "to_tag": next_entry["tag"],
                "from_idx": original_entry["idx"],
                "to_idx": next_entry["idx"],
                "snapshot": f"{next_entry['tag']}{SNAPSHOT_SUFFIX}",
            }
        )
    invocation = "node scripts/reconcile-drizzle-migrations.mjs --base-ref " + base_ref
    return next_journal, changes, invocation


def reconcile_migration_tree(
    repo: str | Path,
    base_ref: str,
    *,
    use_documented_tool: bool = True,
    candidates: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = Path(repo).resolve()
    drizzle_dir = root / "drizzle"
    source_preparation = None
    if candidates is not None:
        source_preparation = materialize_candidate_migrations(root, base_ref, candidates)
    current_journal = _read_json(root / JOURNAL_PATH)
    base_journal = _journal_at_ref(root, base_ref)
    base_tags = {entry["tag"] for entry in base_journal["entries"]}
    current_tags = {entry["tag"] for entry in current_journal.get("entries", [])}
    missing_base = sorted(base_tags - current_tags)
    if missing_base:
        raise MigrationBatchError(f"integration journal is missing base entries: {', '.join(missing_base)}")
    added_entries = sorted(
        (copy.deepcopy(entry) for entry in current_journal["entries"] if entry["tag"] not in base_tags),
        key=lambda entry: (entry["when"], entry["tag"]),
    )
    rebuilt = rebuild_cumulative_snapshots(drizzle_dir, base_journal["entries"], added_entries)
    if use_documented_tool:
        next_journal, changes, invocation = _documented_reconcile(root, base_ref, added_entries, rebuilt)
    else:
        next_journal, changes, invocation = _internal_reconcile(
            root, base_journal, current_journal, added_entries, rebuilt
        )

    next_added = next_journal["entries"][-len(rebuilt) :] if rebuilt else []
    for rebuilt_record, next_entry in zip(rebuilt, next_added):
        snapshot_path = drizzle_dir / "meta" / f"{next_entry['tag']}{SNAPSHOT_SUFFIX}"
        _write_json(snapshot_path, rebuilt_record["snapshot"])

    errors = _validate_journal(
        drizzle_dir,
        next_journal,
        enforce_tag_prefix_from=len(base_journal["entries"]),
    )
    if errors:
        raise MigrationBatchError("reconciled journal is invalid: " + "; ".join(errors))
    new_indices = [entry["idx"] for entry in next_added]
    expected_indices = list(range(len(base_journal["entries"]), len(next_journal["entries"])))
    if new_indices != expected_indices:
        raise MigrationBatchError(f"reconciled journal has gaps: {new_indices} != {expected_indices}")
    result = {
        "valid": True,
        "recipe": invocation,
        "base_entry_count": len(base_journal["entries"]),
        "added_entry_count": len(added_entries),
        "journal_entry_count": len(next_journal["entries"]),
        "new_indices": new_indices,
        "new_tags": [entry["tag"] for entry in next_added],
        "changes": changes,
        "snapshots_rebuilt": len(rebuilt),
    }
    if source_preparation is not None:
        result["source_preparation"] = source_preparation
    return result


def _schema_objects(snapshot: dict[str, Any]) -> dict[str, dict[Any, Any]]:
    objects: dict[str, dict[Any, Any]] = {
        "tables": {},
        "columns": {},
        "constraints": {},
        "indexes": {},
    }
    for table_value in snapshot.get("tables", {}).values():
        schema = table_value.get("schema") or "public"
        table = table_value.get("name")
        if not table:
            continue
        table_key = (schema, table)
        objects["tables"][table_key] = table_value
        for column_value in table_value.get("columns", {}).values():
            column = column_value.get("name")
            if column:
                objects["columns"][(schema, table, column)] = column_value
        for group in ("foreignKeys", "uniqueConstraints", "checkConstraints", "compositePrimaryKeys"):
            for constraint_value in table_value.get(group, {}).values():
                name = constraint_value.get("name")
                if name:
                    objects["constraints"][(schema, table, name)] = constraint_value
        for index_value in table_value.get("indexes", {}).values():
            name = index_value.get("name")
            if name:
                objects["indexes"][(schema, table, name)] = index_value
    return objects


def _expected_delta(base_snapshot: dict[str, Any], final_snapshot: dict[str, Any]) -> dict[str, dict[Any, Any]]:
    base_objects = _schema_objects(base_snapshot)
    final_objects = _schema_objects(final_snapshot)
    return {
        kind: {
            key: value
            for key, value in final_objects[kind].items()
            if base_objects[kind].get(key, MISSING) != value
        }
        for kind in final_objects
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def _temporary_postgres():
    required = {
        name: shutil.which(name)
        for name in ("initdb", "postgres", "psql", "createdb", "pg_config")
    }
    missing = sorted(name for name, executable in required.items() if executable is None)
    if missing:
        raise MigrationBatchError(f"scratch Postgres proof requires: {', '.join(missing)}")
    with tempfile.TemporaryDirectory(prefix="land-batch-postgres-") as temporary:
        temporary_path = Path(temporary)
        data_dir = temporary_path / "data"
        # Codex's macOS sandbox blocks System V shared memory.  initdb launches
        # its sibling ``postgres`` binary directly, before postgresql.conf
        # exists, so provide a private sibling wrapper that selects PostgreSQL's
        # supported mmap/posix implementations for the bootstrap child too.
        bootstrap_bin = temporary_path / "bin"
        bootstrap_bin.mkdir()
        bootstrap_initdb = bootstrap_bin / "initdb"
        shutil.copy2(required["initdb"], bootstrap_initdb)
        bootstrap_postgres = bootstrap_bin / "postgres"
        bootstrap_postgres.write_text(
            "#!/bin/sh\n"
            'case "${1:-}" in -V|--version) '
            f'exec "{required["postgres"]}" "$@";; esac\n'
            'case "${1:-}" in --boot) shift; '
            f'exec "{required["postgres"]}" --boot -c shared_memory_type=mmap '
            '-c dynamic_shared_memory_type=posix "$@";; esac\n'
            f'exec "{required["postgres"]}" "$@"\n',
            encoding="utf-8",
        )
        bootstrap_postgres.chmod(0o700)
        sharedir_result = _run([required["pg_config"], "--sharedir"], timeout=10)
        if sharedir_result.returncode != 0:
            raise MigrationBatchError(
                sharedir_result.stderr.decode("utf-8", errors="replace").strip()
            )
        sharedir = sharedir_result.stdout.decode("utf-8", errors="replace").strip()
        init = _run(
            [
                str(bootstrap_initdb),
                "-L",
                sharedir,
                "-D",
                str(data_dir),
                "-A",
                "trust",
                "--no-locale",
                "-E",
                "UTF8",
            ],
            timeout=60,
        )
        if init.returncode != 0:
            raise MigrationBatchError(init.stderr.decode("utf-8", errors="replace").strip())
        port = _free_port()
        log_path = temporary_path / "postgres.log"
        with log_path.open("wb") as log_handle:
            server = subprocess.Popen(
                [
                    required["postgres"],
                    "-D",
                    str(data_dir),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(port),
                    "-c",
                    "shared_memory_type=mmap",
                    "-c",
                    "dynamic_shared_memory_type=posix",
                ],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                ready = _run(
                    [required["psql"], "-X", "-At", "-h", "127.0.0.1", "-p", str(port), "-d", "postgres", "-c", "select 1"],
                    timeout=5,
                )
                if ready.returncode == 0 and ready.stdout.strip() == b"1":
                    break
                if server.poll() is not None:
                    raise MigrationBatchError(f"scratch Postgres exited early; see {log_path}")
                time.sleep(0.1)
            else:
                raise MigrationBatchError(f"scratch Postgres did not become ready; see {log_path}")
            yield {
                "host": "127.0.0.1",
                "port": port,
                "psql": required["psql"],
                "createdb": required["createdb"],
                "log": log_path,
            }
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def _create_database(server: dict[str, Any], name: str) -> None:
    result = _run(
        [server["createdb"], "-h", server["host"], "-p", str(server["port"]), name],
        timeout=20,
    )
    if result.returncode != 0:
        raise MigrationBatchError(result.stderr.decode("utf-8", errors="replace").strip())


def _psql(server: dict[str, Any], database: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return _run(
        [server["psql"], "-X", "-h", server["host"], "-p", str(server["port"]), "-d", database, *args],
        timeout=timeout,
    )


def _apply_chain(server: dict[str, Any], database: str, drizzle_dir: Path) -> dict[str, Any]:
    journal = _read_json(drizzle_dir / "meta/_journal.json")
    failures = []
    applied = []
    for entry in journal.get("entries", []):
        sql_path = drizzle_dir / f"{entry['tag']}.sql"
        if not sql_path.is_file():
            failures.append({"tag": entry["tag"], "error": "SQL FILE MISSING"})
            continue
        result = _psql(
            server,
            database,
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-1",
            "-f",
            str(sql_path),
            timeout=120,
        )
        if result.returncode == 0:
            applied.append(entry["tag"])
        else:
            failures.append(
                {
                    "tag": entry["tag"],
                    "error": result.stderr.decode("utf-8", errors="replace").strip()[-1000:],
                }
            )
    return {"applied": applied, "failures": failures, "entry_count": len(journal.get("entries", []))}


def _database_objects(server: dict[str, Any], database: str) -> dict[str, Any]:
    queries = {
        "tables": "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2",
        "columns": "SELECT table_schema, table_name, column_name, is_nullable FROM information_schema.columns WHERE table_schema NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,ordinal_position",
        "constraints": "SELECT n.nspname, c.relname, con.conname FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3",
        "indexes": "SELECT schemaname, tablename, indexname FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2,3",
    }
    result: dict[str, Any] = {}
    for kind, query in queries.items():
        command = _psql(server, database, "-At", "-F", "\t", "-c", query)
        if command.returncode != 0:
            raise MigrationBatchError(command.stderr.decode("utf-8", errors="replace").strip())
        rows = [tuple(line.split("\t")) for line in command.stdout.decode().splitlines() if line]
        result[kind] = rows
    return result


def _verify_expected_objects(
    expected: dict[str, dict[Any, Any]],
    actual: dict[str, Any],
) -> list[str]:
    errors = []
    actual_tables = {tuple(row) for row in actual["tables"]}
    actual_columns = {tuple(row[:3]): row[3] for row in actual["columns"]}
    actual_constraints = {tuple(row) for row in actual["constraints"]}
    actual_indexes = {tuple(row) for row in actual["indexes"]}
    for key in expected["tables"]:
        if key not in actual_tables:
            errors.append(f"missing table {'.'.join(key)}")
    for key, definition in expected["columns"].items():
        if key not in actual_columns:
            errors.append(f"missing column {'.'.join(key)}")
            continue
        expected_nullable = "NO" if definition.get("notNull") else "YES"
        if actual_columns[key] != expected_nullable:
            errors.append(
                f"column {'.'.join(key)} nullable={actual_columns[key]}, expected {expected_nullable}"
            )
    for key in expected["constraints"]:
        if key not in actual_constraints:
            errors.append(f"missing constraint {'.'.join(key)}")
    for key in expected["indexes"]:
        if key not in actual_indexes:
            errors.append(f"missing index {'.'.join(key)}")
    return errors


def _archive_drizzle(repo: Path, base_ref: str, destination: Path) -> Path:
    archive_path = destination / "base-drizzle.tar"
    archive = _git(repo, ["archive", "--format=tar", "-o", str(archive_path), base_ref, "drizzle"])
    if archive.returncode != 0:
        raise MigrationBatchError(archive.stderr.decode("utf-8", errors="replace").strip())
    extract_dir = destination / "base"
    extract_dir.mkdir()
    extracted = _run(["tar", "-xf", str(archive_path), "-C", str(extract_dir)], timeout=60)
    if extracted.returncode != 0:
        raise MigrationBatchError(extracted.stderr.decode("utf-8", errors="replace").strip())
    return extract_dir / "drizzle"


def _cached_pglite_tarball() -> Path:
    configured = os.environ.get("LAND_BATCH_PGLITE_TARBALL")
    if configured and Path(configured).is_file():
        return Path(configured)
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        raise MigrationBatchError("native Postgres unavailable and npm/node are missing for PGlite fallback")
    listed = _run([npm, "cache", "ls", "@electric-sql/pglite"], timeout=30)
    keys = [
        line.strip()
        for line in listed.stdout.decode("utf-8", errors="replace").splitlines()
        if "pglite-" in line and line.rstrip().endswith(".tgz")
    ]
    if not keys:
        raise MigrationBatchError("native Postgres unavailable and PGlite is not cached")
    keys.sort(reverse=True)
    npm_root = _run([npm, "root", "-g"], timeout=10)
    cache_root = _run([npm, "config", "get", "cache"], timeout=10)
    if npm_root.returncode != 0 or cache_root.returncode != 0:
        raise MigrationBatchError("could not locate npm cache for PGlite fallback")
    cacache_module = (
        Path(npm_root.stdout.decode().strip()) / "npm/node_modules/cacache"
    )
    if not cacache_module.exists():
        raise MigrationBatchError("npm's cacache module is unavailable for PGlite fallback")
    lookup_script = (
        "const c=require(process.argv[1]);"
        "(async()=>{const i=await c.get.info(process.argv[2],process.argv[3]);"
        "if(i&&i.path)process.stdout.write(i.path)})().catch(()=>process.exit(1));"
    )
    for key in keys:
        lookup = _run(
            [
                node,
                "-e",
                lookup_script,
                str(cacache_module),
                cache_root.stdout.decode().strip() + "/_cacache",
                key,
            ],
            timeout=30,
        )
        candidate = Path(lookup.stdout.decode().strip())
        if lookup.returncode == 0 and candidate.is_file():
            return candidate
    raise MigrationBatchError("cached PGlite tarball could not be resolved")


def _prove_with_pglite(base_dir: Path, integration_dir: Path, temporary: Path) -> dict[str, Any]:
    tarball = _cached_pglite_tarball()
    package_dir = temporary / "pglite"
    package_dir.mkdir()
    extracted = _run(["tar", "-xzf", str(tarball), "-C", str(package_dir)], timeout=60)
    if extracted.returncode != 0:
        raise MigrationBatchError(extracted.stderr.decode("utf-8", errors="replace").strip())
    entry = package_dir / "package/dist/index.cjs"
    if not entry.is_file():
        raise MigrationBatchError("cached PGlite package has no dist/index.cjs")
    request_path = temporary / "pglite-request.json"
    response_path = temporary / "pglite-response.json"
    _write_json(
        request_path,
        {"base_dir": str(base_dir), "integration_dir": str(integration_dir)},
    )
    environment = os.environ.copy()
    environment["PGLITE_ENTRY"] = str(entry)
    runner = Path(__file__).with_name("pglite-proof.cjs")
    proof = _run(
        [shutil.which("node") or "node", str(runner), str(request_path), str(response_path)],
        env=environment,
        timeout=300,
    )
    if proof.returncode != 0:
        raise MigrationBatchError(
            "PGlite scratch proof failed: "
            + proof.stderr.decode("utf-8", errors="replace").strip()
        )
    result = _read_json(response_path)
    result["backend"] = "pglite-postgresql-wasm"
    return result


def _tail_snapshot(drizzle_dir: Path) -> dict[str, Any]:
    journal = _read_json(drizzle_dir / "meta/_journal.json")
    entries = journal.get("entries", [])
    if not entries:
        raise MigrationBatchError(f"journal is empty: {drizzle_dir}")
    return _read_json(_snapshot_for_entry(drizzle_dir, entries[-1]))


def prove_migration_chain(repo: str | Path, base_ref: str) -> dict[str, Any]:
    """Replay base and integration chains in distinct databases created empty."""
    root = Path(repo).resolve()
    integration_dir = root / "drizzle"
    journal = _read_json(integration_dir / "meta/_journal.json")
    base_entry_count = len(_journal_at_ref(root, base_ref)["entries"])
    journal_errors = _validate_journal(
        integration_dir,
        journal,
        enforce_tag_prefix_from=base_entry_count,
    )
    if journal_errors:
        raise MigrationBatchError("proof refused invalid journal: " + "; ".join(journal_errors))
    with tempfile.TemporaryDirectory(prefix="land-batch-chain-") as temporary:
        temporary_path = Path(temporary)
        base_dir = _archive_drizzle(root, base_ref, temporary_path)
        expected = _expected_delta(_tail_snapshot(base_dir), _tail_snapshot(integration_dir))
        try:
            with _temporary_postgres() as server:
                _create_database(server, "land_batch_base")
                _create_database(server, "land_batch_integration")
                base_result = _apply_chain(server, "land_batch_base", base_dir)
                integration_result = _apply_chain(server, "land_batch_integration", integration_dir)
                actual = _database_objects(server, "land_batch_integration")
                backend = "native-postgresql"
        except MigrationBatchError as native_error:
            fallback = _prove_with_pglite(base_dir, integration_dir, temporary_path)
            base_result = fallback["base"]
            integration_result = fallback["integration"]
            actual = fallback["actual"]
            backend = fallback["backend"]
            native_backend_error = str(native_error)
        object_errors = _verify_expected_objects(expected, actual)

    base_failures = {failure["tag"] for failure in base_result["failures"]}
    new_failures = [
        failure
        for failure in integration_result["failures"]
        if failure["tag"] not in base_failures
    ]
    if new_failures or object_errors:
        detail = []
        if new_failures:
            detail.append("new migration failures: " + ", ".join(item["tag"] for item in new_failures))
        detail.extend(object_errors)
        raise MigrationBatchError("scratch-from-empty proof failed: " + "; ".join(detail))
    result = {
        "valid": True,
        "scratch_database_from_empty": True,
        "backend": backend,
        "base": {
            "entries": base_result["entry_count"],
            "applied": len(base_result["applied"]),
            "failures": base_result["failures"],
        },
        "integration": {
            "entries": integration_result["entry_count"],
            "applied": len(integration_result["applied"]),
            "failures": integration_result["failures"],
        },
        "new_failure_count": 0,
        "expected_objects": {kind: len(values) for kind, values in expected.items()},
        "object_errors": [],
    }
    if backend != "native-postgresql":
        result["native_backend_error"] = native_backend_error
    return result


def _load_candidates(path: str) -> list[dict[str, Any]]:
    raw = os.read(0, 10_000_000).decode("utf-8") if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    candidates = value.get("candidates") if isinstance(value, dict) else value
    if not isinstance(candidates, list):
        raise MigrationBatchError("candidate input must be a list or an object with candidates")
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan")
    scan.add_argument("--repo", required=True)
    scan.add_argument("--base-ref", default="origin/main")
    scan.add_argument("--candidates", required=True, help="JSON file or - for stdin")
    reconcile = subparsers.add_parser("reconcile")
    reconcile.add_argument("--repo", required=True)
    reconcile.add_argument("--base-ref", default="origin/main")
    reconcile.add_argument(
        "--candidates",
        help="discovery JSON used to reconstruct generated metadata from pinned candidate tips",
    )
    reconcile.add_argument("--internal-test-engine", action="store_true", help=argparse.SUPPRESS)
    prove = subparsers.add_parser("prove")
    prove.add_argument("--repo", required=True)
    prove.add_argument("--base-ref", default="origin/main")
    prove.add_argument("--evidence")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            result = scan_candidate_migrations(args.repo, args.base_ref, _load_candidates(args.candidates))
        elif args.command == "reconcile":
            candidates = _load_candidates(args.candidates) if args.candidates else None
            result = reconcile_migration_tree(
                args.repo,
                args.base_ref,
                use_documented_tool=not args.internal_test_engine,
                candidates=candidates,
            )
        elif args.command == "prove":
            result = prove_migration_chain(args.repo, args.base_ref)
            if args.evidence:
                _write_json(Path(args.evidence), result)
        else:
            raise AssertionError(args.command)
    except (MigrationBatchError, OSError, ValueError, json.JSONDecodeError) as exc:
        failure = {"valid": False, "error": str(exc)}
        if getattr(args, "command", None) == "prove" and getattr(args, "evidence", None):
            _write_json(Path(args.evidence), failure)
        print(json.dumps(failure, separators=(",", ":")))
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
