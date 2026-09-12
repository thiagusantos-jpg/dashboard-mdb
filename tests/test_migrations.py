from __future__ import annotations

import sqlite3

import pytest

from backend import database as db
from backend import migrations


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    path = tmp_path / "migrations.sqlite3"
    monkeypatch.setattr(db.settings, "DB_PATH", path)
    return path


def test_migrate_twice_is_safe(isolated_db):
    db.initialize()
    db.initialize()

    with db.connection() as conn:
        latest = migrations.MIGRATIONS[-1][0]
        assert migrations.current_version(conn) == latest
        assert [row["version"] for row in conn.execute(
            "SELECT version FROM schema_versions ORDER BY version"
        )] == list(range(1, latest + 1))


def test_refuses_database_newer_than_application(isolated_db):
    db.initialize()
    with sqlite3.connect(isolated_db) as conn:
        conn.execute(
            "INSERT INTO schema_versions(version, applied_at) VALUES(999, 'future')"
        )

    with pytest.raises(migrations.MigrationError, match="newer than this application"):
        db.initialize()


def test_applies_registered_sql_migration_once(isolated_db, tmp_path, monkeypatch):
    migration_dir = tmp_path / "sql"
    migration_dir.mkdir()
    (migration_dir / "003_probe.sql").write_text(
        """
        CREATE TABLE migration_probe(value INTEGER NOT NULL);
        INSERT INTO migration_probe(value) VALUES(42);
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(migrations, "MIGRATION_DIRECTORY", migration_dir)
    monkeypatch.setattr(migrations, "MIGRATIONS", ((3, "003_probe.sql"),))

    db.initialize()
    db.initialize()

    with db.connection() as conn:
        assert migrations.current_version(conn) == 3
        assert conn.execute("SELECT value FROM migration_probe").fetchone()["value"] == 42
