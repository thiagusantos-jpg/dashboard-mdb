from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Tuple


BASELINE_VERSION = 2
MIGRATION_DIRECTORY = Path(__file__).with_name("migrations")

# The first two schema versions predate the SQL migration runner and remain
# installed by backend.database so existing databases keep their history.
# New migrations start at version 3 and must be appended in order.
MIGRATIONS: Tuple[Tuple[int, str], ...] = ()


class MigrationError(RuntimeError):
    """Raised when the recorded schema cannot be migrated safely."""


def current_version(conn) -> int:
    row = conn.execute("SELECT MAX(version) AS version FROM schema_versions").fetchone()
    return int(row["version"] or 0)


def _validated_migrations() -> Iterable[Tuple[int, Path]]:
    versions = [version for version, _ in MIGRATIONS]
    expected = list(range(BASELINE_VERSION + 1, BASELINE_VERSION + 1 + len(versions)))
    if versions != expected:
        raise MigrationError(
            f"migration registry must contain consecutive versions after {BASELINE_VERSION}"
        )

    for version, filename in MIGRATIONS:
        path = MIGRATION_DIRECTORY / filename
        if not path.is_file():
            raise MigrationError(f"migration file is missing: {path}")
        yield version, path


def _statements(sql: str) -> Iterable[str]:
    for fragment in sql.split(";"):
        statement = fragment.strip()
        if statement:
            yield statement


def migrate(conn) -> int:
    """Apply every unapplied migration exactly once and return the version."""
    registered = tuple(_validated_migrations())
    latest = registered[-1][0] if registered else BASELINE_VERSION
    installed = current_version(conn)
    if installed > latest:
        raise MigrationError(
            f"database schema {installed} is newer than this application ({latest})"
        )

    for version, path in registered:
        if version <= installed:
            continue
        for statement in _statements(path.read_text(encoding="utf-8")):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_versions(version, applied_at) VALUES(?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        installed = version
    return installed
