from __future__ import annotations

import pytest

from backend import database as db
from backend.operations.pricing import simulate_price


COMPANY = 1


@pytest.fixture
def pricing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "pricing.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    db.put_dataset(COMPANY, "products", "current", [
        {"id": 1, "name": "Produto 1", "category": "Mercearia", "status": "A"},
    ])
    db.put_dataset(COMPANY, "stock", "current", [
        {"id": 1, "quantity": 10, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1},
    ])
    db.put_dataset(COMPANY, "prices", "current", [
        {"id": 1, "package": "1.0", "price": 1000, "updated_at": "2026-09-01", "cursor": 1},
    ])


def current_price(company, product_id):
    return db.dataset(company, "prices")["payload"][0]["price"]


def test_price_simulation_does_not_mutate_mobne_price(pricing_db):
    before = current_price(COMPANY, 1)

    simulate_price(COMPANY, "2026-09", 1, 12_00, expected_quantity=100)

    assert current_price(COMPANY, 1) == before


def test_simulation_computes_profit_and_margin(pricing_db):
    result = simulate_price(COMPANY, "2026-09", 1, 12_00, expected_quantity=100)

    assert result["simulated_revenue_cents"] == 1_200_00
    assert result["simulated_cost_cents"] == 500_00
    assert result["simulated_profit_cents"] == 700_00
    assert result["simulated_margin_pct"] == pytest.approx(58.33, rel=1e-3)


def test_unknown_product_raises(pricing_db):
    with pytest.raises(ValueError):
        simulate_price(COMPANY, "2026-09", 999, 12_00, expected_quantity=100)
