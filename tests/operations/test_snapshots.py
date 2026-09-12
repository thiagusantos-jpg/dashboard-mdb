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
