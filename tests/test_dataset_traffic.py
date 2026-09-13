"""Guards against the "download a multi-MB blob to compute a small number" pattern.

`datasets.payload` holds denormalized JSON per company/resource/period — a few MB
per row in production (62 MB of sales history for a single store). Reading it is
nearly free against local SQLite and expensive against a network-attached
Postgres, where every byte is billed egress: the /status poll alone (every 60s
per open tab) was transferring 4.42 MB to produce four item counts, ~45 GB/month.

These tests assert the cheap path stays cheap by inspecting the SQL actually
executed, so a future refactor that reintroduces a payload read on a hot path
fails here instead of silently on the invoice.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend import api, database as db


COMPANY = 218


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, 'DB_PATH', tmp_path / 'test.sqlite3')
    db.initialize()
    with db.connection() as conn:
        conn.execute('INSERT INTO companies VALUES(?,?)', (COMPANY, 'Loja Teste'))
    return tmp_path


def executed_sql(fn):
    """Capture every statement run on any SQLite connection opened while fn() runs.

    Hooks connection creation rather than execute() — sqlite3.Connection.execute
    is a read-only slot on a built-in type and cannot be monkeypatched. Same
    methodology as tests/finance/test_forecast.py's query counter.
    """
    real_connect = sqlite3.connect
    statements: list[str] = []

    def tracing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    sqlite3.connect = tracing_connect
    try:
        result = fn()
    finally:
        sqlite3.connect = real_connect
    return result, statements


def payload_reads(statements):
    """Statements that pull `datasets.payload` over the wire.

    `SELECT *` counts: it carries the payload column even though the word never
    appears in the SQL text, which is exactly how the original /status read 4.42 MB
    while looking innocent.
    """
    reads = []
    for raw in statements:
        sql = ' '.join(raw.lower().split())
        if 'from datasets' not in sql or not sql.startswith('select'):
            continue
        projection = sql.split('from datasets')[0]
        if '*' in projection or 'payload' in projection:
            reads.append(raw)
    return reads


def catalog(n):
    return [{'id': i, 'description': f'Produto {i}', 'cursor': i} for i in range(1, n + 1)]


# --- /status ---------------------------------------------------------------


def test_status_does_not_transfer_catalog_payloads(isolated_db):
    """The 45 GB/month regression: /status is polled every 60s by every open tab
    and only ever needed a count and a timestamp per catalog."""
    db.put_dataset(COMPANY, 'products', 'current', catalog(3))
    db.put_dataset(COMPANY, 'stock', 'current', catalog(2))

    _, sql = executed_sql(lambda: api.status(COMPANY))

    assert payload_reads(sql) == []


def test_status_still_reports_catalog_counts_and_timestamps(isolated_db):
    """Behaviour preserved: the cheap path must report exactly what the
    payload-reading one did."""
    db.put_dataset(COMPANY, 'products', 'current', catalog(3))
    db.put_dataset(COMPANY, 'stock', 'current', catalog(2))

    result = api.status(COMPANY)

    assert result['catalogs']['products']['count'] == 3
    assert result['catalogs']['stock']['count'] == 2
    assert result['catalogs']['products']['updated_at'] is not None
    assert result['catalogs']['categories'] is None  # never synced: absent, not zero


def test_catalog_counts_are_backfilled_at_startup_for_legacy_rows(isolated_db):
    """Rows written before `documents` was stored for catalogs carry 0. They must
    report their real count, not 0 — a dashboard claiming "0 produtos" reads as a
    broken sync. Models the real ordering: the legacy rows already exist when the
    new code starts up."""
    db.put_dataset(COMPANY, 'products', 'current', catalog(3))
    with db.connection() as conn:  # a row as the previous version left it
        conn.execute("UPDATE datasets SET documents=0 WHERE resource='products'")

    db.initialize()  # the app starting again (every serverless cold start does this)

    assert api.status(COMPANY)['catalogs']['products']['count'] == 3


def test_an_empty_catalog_reports_zero_rather_than_being_treated_as_uncounted(isolated_db):
    db.put_dataset(COMPANY, 'products', 'current', [])

    assert api.status(COMPANY)['catalogs']['products']['count'] == 0
