from __future__ import annotations

import pytest

from backend import database as db
from backend.operations.catalog import by_id, inventory_catalog


COMPANY = 1


@pytest.fixture
def catalog_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "catalog.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def seed_product(product_id, *, stock=0, name=None, category="Mercearia"):
    existing_products = db.dataset(COMPANY, "products")
    existing_stock = db.dataset(COMPANY, "stock")
    existing_prices = db.dataset(COMPANY, "prices")
    products = (existing_products["payload"] if existing_products else [])
    products = [p for p in products if p["id"] != product_id] + [
        {"id": product_id, "name": name or f"Produto {product_id}", "category_id": 1, "category": category, "status": "A"}
    ]
    stock_rows = (existing_stock["payload"] if existing_stock else [])
    stock_rows = [s for s in stock_rows if s["id"] != product_id] + [
        {"id": product_id, "quantity": stock, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1}
    ]
    price_rows = (existing_prices["payload"] if existing_prices else [])
    price_rows = [p for p in price_rows if p["id"] != product_id] + [
        {"id": product_id, "package": "1.0", "price": 1000, "updated_at": "2026-09-01", "cursor": 1}
    ]
    db.put_dataset(COMPANY, "products", "current", products)
    db.put_dataset(COMPANY, "stock", "current", stock_rows)
    db.put_dataset(COMPANY, "prices", "current", price_rows)


def test_product_with_stock_and_no_sales_is_visible(catalog_db):
    seed_product(77, stock=12)
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 0, "receipts": [], "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })

    catalog = inventory_catalog(COMPANY, "2026-09")
    row = by_id(catalog, 77)

    assert row is not None
    assert row["revenue"] == 0
    assert row["stock"] == 12


def test_sold_product_carries_its_sales_metrics(catalog_db):
    seed_product(77, stock=5)
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 1,
        "receipts": [{
            "id": 1, "reference_id": 0, "aliases": [1], "date": "2026-09-05", "status": "V", "species": "CF",
            "revenue": 100_00,
            "items": [{"id": 1, "product_id": 77, "quantity": "1", "revenue": 100_00, "cost": 50_00,
                       "unit_cost": "50", "status": "V"}],
        }],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })

    catalog = inventory_catalog(COMPANY, "2026-09")
    row = by_id(catalog, 77)

    assert row["revenue"] == 100_00
    assert row["stock"] == 5


def test_no_sales_dataset_still_lists_the_full_catalog(catalog_db):
    seed_product(1, stock=3)
    seed_product(2, stock=0)

    catalog = inventory_catalog(COMPANY, "2026-09")

    assert {row["id"] for row in catalog} == {1, 2}
    assert all(row["revenue"] == 0 for row in catalog)
