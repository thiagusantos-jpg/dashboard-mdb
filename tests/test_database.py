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
