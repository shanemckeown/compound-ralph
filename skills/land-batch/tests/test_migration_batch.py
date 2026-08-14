"""End-to-end migration collision reconciliation and scratch proof tests."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parent.parent
TARGET_REPO = Path("/Users/shane/Documents/GitReBase/AestheticcNext")
MIGRATION_BATCH_PATH = ROOT / "bin" / "migration_batch.py"
SAFETY_PATH = ROOT / "bin" / "land_batch_safety.py"

MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "land_batch_migrations",
    MIGRATION_BATCH_PATH,
)
migration_batch = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(migration_batch)

SAFETY_SPEC = importlib.util.spec_from_file_location(
    "land_batch_safety_test",
    SAFETY_PATH,
)
safety = importlib.util.module_from_spec(SAFETY_SPEC)
SAFETY_SPEC.loader.exec_module(safety)


def _run(args, cwd=None):
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _git(repo, *args):
    return _run(["git", "-C", str(repo), *args])


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _snapshot(snapshot_id, previous_id, tables):
    return {
        "id": snapshot_id,
        "prevId": previous_id,
        "version": "7",
        "dialect": "postgresql",
        "tables": tables,
        "enums": {},
        "schemas": {},
        "sequences": {},
        "roles": {},
        "policies": {},
        "views": {},
        "_meta": {"schemas": {}, "tables": {}, "columns": {}},
    }


def _table(name):
    constraint_name = f"{name}_id_positive"
    return {
        "name": name,
        "schema": "",
        "columns": {
            "id": {
                "name": "id",
                "type": "integer",
                "primaryKey": False,
                "notNull": True,
            }
        },
        "indexes": {},
        "foreignKeys": {},
        "compositePrimaryKeys": {},
        "uniqueConstraints": {},
        "policies": {},
        "checkConstraints": {
            constraint_name: {
                "name": constraint_name,
                "value": '"id" > 0',
            }
        },
        "isRLSEnabled": False,
    }


def _make_0240_integration_repo(tmp_path, table_names=("alpha", "beta", "gamma")):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    drizzle = repo / "drizzle"
    meta = drizzle / "meta"
    meta.mkdir(parents=True)
    base_entries = []
    for index in range(240):
        tag = f"{index:04d}_base"
        base_entries.append(
            {
                "idx": index,
                "version": "7",
                "when": 1_700_000_000_000 + index,
                "tag": tag,
                "breakpoints": True,
            }
        )
        (drizzle / f"{tag}.sql").write_text("SELECT 1;\n", encoding="utf-8")
    _write_json(meta / "_journal.json", {"version": "7", "dialect": "pg", "entries": base_entries})
    _write_json(meta / "0239_base_snapshot.json", _snapshot("base-0239", "base-0238", {}))
    _git(repo, "add", "drizzle")
    _git(repo, "commit", "-m", "base migration chain")

    added_entries = []
    for offset, table_name in enumerate(table_names):
        tag = f"0240_{table_name}"
        added_entries.append(
            {
                "idx": 240,
                "version": "7",
                "when": 1_800_000_000_000 + offset,
                "tag": tag,
                "breakpoints": True,
            }
        )
        (drizzle / f"{tag}.sql").write_text(
            f"CREATE TABLE {table_name} (id integer NOT NULL, "
            f"CONSTRAINT {table_name}_id_positive CHECK (id > 0));\n",
            encoding="utf-8",
        )
        _write_json(
            meta / f"{tag}_snapshot.json",
            _snapshot(f"{table_name}-snapshot", "base-0239", {f"public.{table_name}": _table(table_name)}),
        )
    _write_json(
        meta / "_journal.json",
        {"version": "7", "dialect": "pg", "entries": base_entries + added_entries},
    )
    return repo


def _make_sensitive_tree(repo):
    for prefix in safety.SENSITIVE_PREFIXES:
        (repo / prefix.rstrip("/")).mkdir(parents=True, exist_ok=True)
    (repo / "drizzle" / "0000_base.sql").write_text("SELECT 1;\n", encoding="utf-8")


def _parallel_0240_candidates(repo, table_names, identical=False):
    base_journal = json.loads((repo / "drizzle/meta/_journal.json").read_text(encoding="utf-8"))
    candidates = []
    for offset, requested_name in enumerate(table_names):
        table_name = "shared" if identical else requested_name
        branch = f"goal/{requested_name}"
        _git(repo, "switch", "-c", branch, "main")
        tag = f"0240_{table_name}"
        (repo / f"drizzle/{tag}.sql").write_text(
            f"CREATE TABLE {table_name} (id integer NOT NULL, "
            f"CONSTRAINT {table_name}_id_positive CHECK (id > 0));\n",
            encoding="utf-8",
        )
        entry = {
            "idx": 240,
            "version": "7",
            "when": 1_800_000_000_000 + (0 if identical else offset),
            "tag": tag,
            "breakpoints": True,
        }
        _write_json(
            repo / "drizzle/meta/_journal.json",
            {**base_journal, "entries": base_journal["entries"] + [entry]},
        )
        _write_json(
            repo / "drizzle/meta/0240_snapshot.json",
            _snapshot(
                f"{table_name}-snapshot",
                "base-0239",
                {f"public.{table_name}": _table(table_name)},
            ),
        )
        _git(repo, "add", "drizzle")
        _git(repo, "commit", "-m", f"add {requested_name} migration")
        tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
        candidates.append({"branch": branch, "source_ref": branch, "tip_sha": tip})
        _git(repo, "switch", "main")
    return candidates


def test_sensitive_prefix_validation_fails_for_absent_target_path(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_sensitive_tree(repo)

    assert safety.validate_sensitive_prefixes(repo) == []
    assert safety.validate_sensitive_prefixes(
        repo,
        (*safety.SENSITIVE_PREFIXES, "definitely/missing/"),
    ) == ["definitely/missing/"]


def test_every_configured_sensitive_prefix_exists_in_the_real_target_repo():
    assert safety.validate_sensitive_prefixes(TARGET_REPO) == []


def test_three_0240_migrations_reconcile_and_replay_from_empty(tmp_path):
    repo = _make_0240_integration_repo(tmp_path)

    reconciliation = migration_batch.reconcile_migration_tree(
        repo,
        "main",
        use_documented_tool=False,
    )

    assert reconciliation["new_indices"] == [240, 241, 242]
    assert reconciliation["new_tags"] == ["0240_alpha", "0241_beta", "0242_gamma"]
    assert reconciliation["snapshots_rebuilt"] == 3
    journal = json.loads((repo / "drizzle/meta/_journal.json").read_text(encoding="utf-8"))
    assert [entry["idx"] for entry in journal["entries"]] == list(range(243))
    tail_snapshot = json.loads(
        (repo / "drizzle/meta/0242_gamma_snapshot.json").read_text(encoding="utf-8")
    )
    assert set(tail_snapshot["tables"]) == {"public.alpha", "public.beta", "public.gamma"}

    proof = migration_batch.prove_migration_chain(repo, "main")

    assert proof["scratch_database_from_empty"] is True
    assert proof["new_failure_count"] == 0
    assert proof["integration"] == {"entries": 243, "applied": 243, "failures": []}
    assert proof["expected_objects"] == {
        "tables": 3,
        "columns": 3,
        "constraints": 3,
        "indexes": 0,
    }


def test_proof_rejects_contiguous_indices_with_duplicate_numeric_prefixes(tmp_path):
    repo = _make_0240_integration_repo(tmp_path)
    journal_path = repo / "drizzle/meta/_journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    for offset, entry in enumerate(journal["entries"][240:]):
        entry["idx"] = 240 + offset
    _write_json(journal_path, journal)

    with pytest.raises(migration_batch.MigrationBatchError, match="tag prefix does not match idx"):
        migration_batch.prove_migration_chain(repo, "main")


def test_three_parallel_branches_are_detected_reconstructed_and_proved(tmp_path):
    repo = _make_0240_integration_repo(tmp_path, table_names=())
    candidates = _parallel_0240_candidates(repo, ("alpha", "beta", "gamma"))

    scan = migration_batch.scan_candidate_migrations(repo, "main", candidates)
    assert scan["valid"] is True
    assert scan["collision_count"] == 1
    assert scan["collisions"][0]["number"] == "0240"
    assert len(scan["collisions"][0]["variants"]) == 3
    assert len(scan["collisions"][0]["branch_pairs"]) == 3

    reconciliation = migration_batch.reconcile_migration_tree(
        repo,
        "main",
        use_documented_tool=False,
        candidates=candidates,
    )
    assert reconciliation["source_preparation"] == {
        "candidate_claim_count": 3,
        "unique_migration_count": 3,
        "inherited_claim_count": 0,
    }
    assert reconciliation["new_tags"] == ["0240_alpha", "0241_beta", "0242_gamma"]
    assert reconciliation["new_indices"] == [240, 241, 242]

    proof = migration_batch.prove_migration_chain(repo, "main")
    assert proof["scratch_database_from_empty"] is True
    assert proof["integration"]["applied"] == 243
    assert proof["expected_objects"]["tables"] == 3


def test_parallel_identical_0240_is_inheritance_and_materializes_once(tmp_path):
    repo = _make_0240_integration_repo(tmp_path, table_names=())
    candidates = _parallel_0240_candidates(repo, ("shared-a", "shared-b"), identical=True)

    scan = migration_batch.scan_candidate_migrations(repo, "main", candidates)
    assert scan["collision_count"] == 0
    assert scan["inheritance_count"] == 1

    reconciliation = migration_batch.reconcile_migration_tree(
        repo,
        "main",
        use_documented_tool=False,
        candidates=candidates,
    )
    assert reconciliation["source_preparation"] == {
        "candidate_claim_count": 2,
        "unique_migration_count": 1,
        "inherited_claim_count": 1,
    }
    assert reconciliation["new_tags"] == ["0240_shared"]
    assert reconciliation["new_indices"] == [240]


def test_identical_sql_with_divergent_snapshots_fails_reconstruction(tmp_path):
    repo = _make_0240_integration_repo(tmp_path, table_names=())
    candidates = _parallel_0240_candidates(repo, ("shared-a", "shared-b"), identical=True)
    _git(repo, "switch", "goal/shared-b")
    snapshot_path = repo / "drizzle/meta/0240_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["tables"]["public.unexpected"] = _table("unexpected")
    _write_json(snapshot_path, snapshot)
    _git(repo, "add", str(snapshot_path.relative_to(repo)))
    _git(repo, "commit", "-m", "diverge generated snapshot")
    candidates[1]["tip_sha"] = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "switch", "main")

    scan = migration_batch.scan_candidate_migrations(repo, "main", candidates)
    assert scan["collision_count"] == 0
    assert scan["inheritance_count"] == 1
    with pytest.raises(migration_batch.MigrationBatchError, match="divergent snapshot bodies"):
        migration_batch.reconcile_migration_tree(
            repo,
            "main",
            use_documented_tool=False,
            candidates=candidates,
        )


def test_one_branch_with_two_different_claims_of_one_number_is_a_collision():
    report = migration_batch.classify_migration_claims(
        [
            {
                "number": "0240",
                "sha256": "a" * 64,
                "branch": "goal/double",
                "path": "drizzle/0240_a.sql",
            },
            {
                "number": "0240",
                "sha256": "b" * 64,
                "branch": "goal/double",
                "path": "drizzle/0240_b.sql",
            },
        ]
    )

    assert report["collision_count"] == 1
    assert report["collisions"][0]["branch_pairs"] == [["goal/double", "goal/double"]]


def test_inherited_pair_inside_a_mixed_number_never_blocks_each_other():
    shared_hash = "a" * 64
    report = migration_batch.classify_migration_claims(
        [
            {
                "number": "0240",
                "sha256": shared_hash,
                "branch": "goal/parent",
                "path": "drizzle/0240_shared.sql",
            },
            {
                "number": "0240",
                "sha256": shared_hash,
                "branch": "goal/stacked-child",
                "path": "drizzle/0240_shared.sql",
            },
            {
                "number": "0240",
                "sha256": "b" * 64,
                "branch": "goal/independent",
                "path": "drizzle/0240_other.sql",
            },
        ]
    )

    assert report["collision_count"] == 1
    assert report["collisions"][0]["branch_pairs"] == [
        ["goal/independent", "goal/parent"],
        ["goal/independent", "goal/stacked-child"],
    ]
    assert report["inheritance"] == [
        {
            "kind": "migration-inheritance",
            "number": "0240",
            "sha256": shared_hash,
            "branches": ["goal/parent", "goal/stacked-child"],
            "paths": ["drizzle/0240_shared.sql"],
        }
    ]


def test_default_reconciler_invokes_the_target_documented_recipe(tmp_path):
    repo = _make_0240_integration_repo(tmp_path)
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy2(
        TARGET_REPO / "scripts/reconcile-drizzle-migrations.mjs",
        scripts / "reconcile-drizzle-migrations.mjs",
    )
    shutil.copy2(
        TARGET_REPO / "scripts/migration-journal-utils.cjs",
        scripts / "migration-journal-utils.cjs",
    )
    shutil.copy2(TARGET_REPO / "drizzle/README.md", repo / "drizzle/README.md")

    reconciliation = migration_batch.reconcile_migration_tree(repo, "main")

    assert reconciliation["recipe"] == (
        "node scripts/reconcile-drizzle-migrations.mjs --base-ref main"
    )
    assert reconciliation["new_tags"] == ["0240_alpha", "0241_beta", "0242_gamma"]
    assert reconciliation["snapshots_rebuilt"] == 3


def test_inherited_identical_0240_remains_one_unrenumbered_migration(tmp_path):
    # Two stacked branches carrying the same file merge to one tree entry. The
    # hash scanner classifies the pair as inheritance; reconciliation must not
    # manufacture a second migration or move the surviving 0240.
    repo = _make_0240_integration_repo(tmp_path, table_names=("shared",))

    reconciliation = migration_batch.reconcile_migration_tree(
        repo,
        "main",
        use_documented_tool=False,
    )

    assert reconciliation["added_entry_count"] == 1
    assert reconciliation["new_indices"] == [240]
    assert reconciliation["new_tags"] == ["0240_shared"]


def test_single_branch_reusing_a_base_number_is_a_collision(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-b", "main"], cwd=repo)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "drizzle").mkdir()
    (repo / "drizzle/0239_existing.sql").write_text("SELECT 1;\n", encoding="utf-8")
    _git(repo, "add", "drizzle")
    _git(repo, "commit", "-m", "base migration")

    _git(repo, "switch", "-c", "goal/reuses-base-number")
    (repo / "drizzle/0239_different.sql").write_text("SELECT 2;\n", encoding="utf-8")
    _git(repo, "add", "drizzle")
    _git(repo, "commit", "-m", "reuse migration number")
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()

    report = migration_batch.scan_candidate_migrations(
        repo,
        "main",
        [
            {
                "branch": "goal/reuses-base-number",
                "source_ref": "refs/heads/goal/reuses-base-number",
                "tip_sha": tip,
            }
        ],
    )

    assert report["valid"] is True
    assert report["collision_count"] == 1
    assert report["collisions"][0]["number"] == "0239"
    assert report["collisions"][0]["branches"] == [
        "BASE:main",
        "goal/reuses-base-number",
    ]
