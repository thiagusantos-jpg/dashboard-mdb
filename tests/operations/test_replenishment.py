from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts
from backend.operations.replenishment import recommend, recommend_one


COMPANY = 1


@pytest.fixture
def replenishment_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "replenishment.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def test_reorder_uses_available_stock_lead_time_and_pack_size():
    result = recommend_one(daily_demand=3, stock=5, reserved=2, lead_days=4, safety_days=2, pack_size=6)

    assert result["available"] == 3
    assert result["reorder_point"] == 18
    assert result["suggested_quantity"] == 18


def test_sufficient_stock_needs_no_reorder():
    result = recommend_one(daily_demand=2, stock=100, reserved=0, lead_days=3, safety_days=2, pack_size=1)

    assert result["suggested_quantity"] == 0


def test_recommend_lists_products_needing_reorder(replenishment_db):
    db.put_dataset(COMPANY, "products", "current", [
        {"id": 1, "name": "Produto 1", "category": "Mercearia", "status": "A"},
    ])
    db.put_dataset(COMPANY, "stock", "current", [
        {"id": 1, "quantity": 5, "reserved": 2, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1},
    ])
    db.put_dataset(COMPANY, "prices", "current", [
        {"id": 1, "package": "1.0", "price": 1000, "updated_at": "2026-09-01", "cursor": 1},
    ])
    accounts.set_parameter(COMPANY, "replenishment_lead_days", 4, "2026-01-01", product=1)
    accounts.set_parameter(COMPANY, "replenishment_safety_days", 2, "2026-01-01", product=1)
    accounts.set_parameter(COMPANY, "replenishment_pack_size", 6, "2026-01-01", product=1)
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 1,
        "receipts": [{
            "id": 1, "reference_id": 0, "aliases": [1], "date": "2026-09-01", "status": "V", "species": "CF",
            "revenue": 300_00,
            "items": [{"id": 1, "product_id": 1, "quantity": "3", "revenue": 300_00, "cost": 150_00,
                       "unit_cost": "50", "status": "V"}],
        }],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })

    recommendations = recommend(COMPANY, None, date(2026, 9, 1))

    assert len(recommendations) == 1
    row = recommendations[0]
    assert row["product_id"] == 1
    assert row["daily_demand"] == pytest.approx(3.0)
    assert row["suggested_quantity"] == 18


def test_products_with_enough_stock_are_not_recommended(replenishment_db):
    db.put_dataset(COMPANY, "products", "current", [
        {"id": 2, "name": "Produto 2", "category": "Mercearia", "status": "A"},
    ])
    db.put_dataset(COMPANY, "stock", "current", [
        {"id": 2, "quantity": 1000, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1},
    ])
    db.put_dataset(COMPANY, "prices", "current", [])

    recommendations = recommend(COMPANY, None, date(2026, 9, 1))

    assert recommendations == []
