from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "replenishment-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    db.put_dataset(1, "products", "current", [
        {"id": 1, "name": "Produto 1", "category": "Mercearia", "status": "A"},
    ])
    db.put_dataset(1, "stock", "current", [
        {"id": 1, "quantity": 0, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1},
    ])
    db.put_dataset(1, "prices", "current", [
        {"id": 1, "package": "1.0", "price": 1000, "updated_at": "2026-09-01", "cursor": 1},
    ])
    db.put_dataset(1, "sales", "2026-09", {
        "raw_count": 1,
        "receipts": [{
            "id": 1, "reference_id": 0, "aliases": [1], "date": "2026-09-01", "status": "V", "species": "CF",
            "revenue": 100_00,
            "items": [{"id": 1, "product_id": 1, "quantity": "2", "revenue": 100_00, "cost": 50_00,
                       "unit_cost": "25", "status": "V"}],
        }],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def test_replenishment_endpoint_lists_products_needing_reorder(client):
    response = client.get("/api/companies/1/replenishment", params={"as_of": "2026-09-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["product_id"] == 1


def test_product_detail_endpoint_returns_catalog_and_replenishment(client):
    response = client.get("/api/companies/1/products/1", params={"period": "2026-09"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["product"]["id"] == 1
    assert body["replenishment"] is not None
    assert body["history"] == []


def test_unknown_product_returns_404(client):
    response = client.get("/api/companies/1/products/999", params={"period": "2026-09"})
    assert response.status_code == 404
