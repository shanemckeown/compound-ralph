#!/usr/bin/env python3
"""Shared sensitive-path policy for /land-batch.

Keep path configuration here so discovery and kickback classification cannot
silently drift apart.  ``drizzle/`` is intentionally special: generated SQL
migrations are sensitive, while README and generated metadata are handled by
the dedicated migration reconciliation gate.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


SENSITIVE_PREFIXES = (
    "lib/db/",
    "drizzle/",
    "lib/stripe/",
    "lib/auth/",
    "lib/payments/",
    "pages/api/auth/",
    "pages/api/admin/",
    "pages/api/webhooks/",
    "lib/email/templates/",
)

MIGRATION_SQL_RE = re.compile(r"^drizzle/\d{4}_[^/]+\.sql$")


def is_sensitive_path(path: str) -> bool:
    """Return whether a repository-relative path needs explicit opt-in."""
    if MIGRATION_SQL_RE.fullmatch(path):
        return True
    return any(
        path.startswith(prefix)
        for prefix in SENSITIVE_PREFIXES
        if prefix != "drizzle/"
    )


def sensitive_paths(paths_to_check: Iterable[str] | None) -> list[str]:
    return sorted(
        {
            path
            for path in (paths_to_check or [])
            if isinstance(path, str) and is_sensitive_path(path)
        }
    )


def validate_sensitive_prefixes(
    repo: str | Path,
    prefixes: Iterable[str] = SENSITIVE_PREFIXES,
) -> list[str]:
    """Return configured prefixes that do not map to a real target path.

    The old ``drizzle/migrations/`` value survived because configuration was
    only exercised against changed filenames.  This structural check makes a
    stale directory prefix fail before discovery can approve a candidate.
    """
    root = Path(repo)
    missing = []
    for prefix in prefixes:
        relative = prefix.rstrip("/")
        target = root / relative
        if not target.exists():
            missing.append(prefix)
            continue
        if prefix == "drizzle/" and not any(
            MIGRATION_SQL_RE.fullmatch(path.relative_to(root).as_posix())
            for path in target.glob("*.sql")
        ):
            missing.append(prefix)
    return missing
