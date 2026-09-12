"""Tests for backend/database.py's schema-v2 migration: adding the 'documents'
column to an existing datasets table and backfilling it from each sales
payload's raw_count, without losing any existing rows.
"""
from __future__ import annotations
import json
import sqlite3
import pytest
from backend import database as db


@pytest.fixture
def isolated_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, 'DB_PATH', tmp_path / 'test.sqlite3')
    return tmp_path / 'test.sqlite3'


def test_documents_column_is_backfilled_from_pre_migration_rows(isolated_db_path):
    # Simulate a database created before the 'documents' column existed.
    conn = sqlite3.connect(str(isolated_db_path))
    conn.execute('''CREATE TABLE datasets(
        company INTEGER NOT NULL, resource TEXT NOT NULL, period TEXT NOT NULL,
        payload TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(company,resource,period))''')
    conn.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?)',
        (218, 'sales', '2025-01', json.dumps({'raw_count': 0, 'receipts': []}), '2025-01-01T00:00:00+00:00', 1))
    conn.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?)',
        (218, 'sales', '2025-08', json.dumps({'raw_count': 42, 'receipts': []}), '2025-08-01T00:00:00+00:00', 1))
    conn.commit()
    conn.close()

    db.initialize()

    periods = {p['period']: p['documents'] for p in db.periods(218)}
    assert periods == {'2025-01': 0, '2025-08': 42}


def test_put_dataset_persists_and_updates_documents_count(isolated_db_path):
    db.initialize()
    db.put_dataset(218, 'sales', '2026-01', {'receipts': [1]}, documents=5)
    assert db.periods(218)[0]['documents'] == 5

    db.put_dataset(218, 'sales', '2026-01', {'receipts': [1, 2]}, documents=8)
    row = db.periods(218)[0]
    assert row['documents'] == 8
    assert row['version'] == 2  # conflict path still bumps version as before


# --- Task B7 fix round 1: connection(snapshot=True) -------------------------
#
# forecast() needs every one of its several SELECTs to read from ONE
# consistent point-in-time view, immune to a write another connection commits
# while it's still running ("consistência de um snapshot por cálculo"). These
# tests exercise the primitive directly: that snapshot=True actually opens an
# explicit read transaction (not just "a connection", which on SQLite's
# default isolation_level="" never implicitly begins one before a bare
# SELECT), that a genuinely separate connection's commit mid-transaction is
# invisible to it, and that it never commits (always rolls back) on exit,
# consistent with being read-only by contract. See
# tests/finance/test_forecast.py for the equivalent test against forecast()
# itself, with a real interleaved write from a separate connection.


def test_snapshot_connection_opens_explicit_transaction(isolated_db_path):
    db.initialize()
    with db.connection(snapshot=True) as conn:
        # sqlite3's own bookkeeping: in_transaction is only True once a
        # transaction has actually been opened (explicitly, here, since a
        # bare SELECT alone never opens one under isolation_level="").
        assert conn.in_transaction is True


def test_snapshot_connection_ignores_concurrent_commit(isolated_db_path):
    db.initialize()
    with db.connection() as setup:
        setup.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")

    with db.connection(snapshot=True) as snap:
        before = snap.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]

        # A genuinely separate connection, opened and committed WHILE `snap`
        # above is still open mid-transaction.
        with db.connection() as writer:
            writer.execute("INSERT INTO companies(id,name) VALUES(2,'Loja 2')")

        during = snap.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]
        assert during == before == 1, (
            "a commit from a separate connection must not be visible inside "
            "an already-open snapshot transaction"
        )

    # A fresh connection (after the snapshot block above has exited/rolled
    # back) sees the write — proving it really happened and the earlier
    # exclusion was snapshot isolation, not a failed write.
    with db.connection() as fresh:
        after = fresh.execute("SELECT COUNT(*) AS c FROM companies").fetchone()["c"]
    assert after == 2


def test_snapshot_connection_never_commits(isolated_db_path):
    # Read-only by contract: even though nothing here writes, confirm the
    # exit path is rollback, not commit, by checking a write attempted inside
    # a snapshot block (misuse, but illustrative) does not survive it.
    db.initialize()
    with db.connection(snapshot=True) as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(99,'Não deveria persistir')")

    with db.connection() as fresh:
        row = fresh.execute("SELECT COUNT(*) AS c FROM companies WHERE id=99").fetchone()
    assert row["c"] == 0
