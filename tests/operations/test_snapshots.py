from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.operations.snapshots import product_history, snapshot_catalogs


COMPANY = 1


@pytest.fixture
def snapshots_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "snapshots.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    db.put_dataset(COMPANY, "products", "current", [
        {"id": 1, "name": "Produto 1", "category_id": 1, "category": "Mercearia", "status": "A"},
    ])
    db.put_dataset(COMPANY, "stock", "current", [
        {"id": 1, "quantity": 10, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1},
    ])
    db.put_dataset(COMPANY, "prices", "current", [
        {"id": 1, "package": "1.0", "price": 1000, "updated_at": "2026-09-01", "cursor": 1},
    ])


def test_snapshot_persists_one_row_per_product(snapshots_db):
    count = snapshot_catalogs(COMPANY, date(2026, 9, 10))

    assert count == 1
    history = product_history(COMPANY, 1)
    assert len(history) == 1
    assert history[0]["stock_quantity"] == 10
    assert history[0]["price_cents"] == 1000


def test_same_day_snapshot_updates_instead_of_duplicating(snapshots_db):
    snapshot_catalogs(COMPANY, date(2026, 9, 10))
    db.put_dataset(COMPANY, "stock", "current", [
        {"id": 1, "quantity": 3, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 2},
    ])

    snapshot_catalogs(COMPANY, date(2026, 9, 10))

    history = product_history(COMPANY, 1)
    assert len(history) == 1
    assert history[0]["stock_quantity"] == 3


def test_different_days_accumulate_history(snapshots_db):
    snapshot_catalogs(COMPANY, date(2026, 9, 10))
    snapshot_catalogs(COMPANY, date(2026, 9, 11))

    history = product_history(COMPANY, 1)
    assert len(history) == 2


def test_a_large_catalog_goes_out_in_a_few_statements_not_one_per_product(snapshots_db, monkeypatch):
    """2026-09-15: 11.412 single-row INSERTs over the network to Postgres took the
    whole 300 s Vercel allows, so the sync that had saved everything still failed."""
    from contextlib import contextmanager

    total = 1200
    db.put_dataset(COMPANY, "products", "current", [
        {"id": i, "name": f"Produto {i}", "category_id": 1, "category": "Mercearia", "status": "A"}
        for i in range(1, total + 1)
    ] + [{"id": 7, "name": "Produto 7 (repetido)", "category_id": 1, "category": "Mercearia", "status": "A"}])
    db.put_dataset(COMPANY, "stock", "current", [
        {"id": i, "quantity": i, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1}
        for i in range(1, total + 1)
    ])
    statements = []
    real_connection = db.connection

    @contextmanager
    def counting_connection(*args, **kwargs):
        with real_connection(*args, **kwargs) as conn:
            class Counting:
                def execute(self, sql, params=()):
                    statements.append(sql)
                    return conn.execute(sql, params)

                def __getattr__(self, name):
                    return getattr(conn, name)
            yield Counting()

    monkeypatch.setattr(db, "connection", counting_connection)
    count = snapshot_catalogs(COMPANY, date(2026, 9, 15))
    monkeypatch.setattr(db, "connection", real_connection)

    assert count == total  # the repeated product is one row, not an error
    assert len([s for s in statements if "INSERT INTO operation_snapshots" in s]) == 3
    assert product_history(COMPANY, total)[0]["stock_quantity"] == total
    assert len(product_history(COMPANY, 7)) == 1
