from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "goals-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    db.put_dataset(1, "products", "current", [
        {"id": 1, "name": "Produto 1", "category": "Mercearia", "status": "A"},
    ])
    db.put_dataset(1, "prices", "current", [
        {"id": 1, "package": "1.0", "price": 1000, "updated_at": "2026-09-01", "cursor": 1},
    ])
    db.put_dataset(1, "stock", "current", [
        {"id": 1, "quantity": 10, "reserved": 0, "unit_cost": "5.00", "last_cost": "5.00", "cursor": 1},
    ])
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def test_set_and_read_goal_progress(client):
    put = client.put(
        "/api/companies/1/goals",
        json={"key": "revenue", "value": 100_000_00, "effective_from": "2026-01-01"},
    )
    assert put.status_code == 200, put.text

    progress = client.get("/api/companies/1/goals/progress", params={"as_of": "2026-09-10"})
    assert progress.status_code == 200
    assert progress.json()["target_cents"] == 100_000_00


def test_simulate_price_via_api(client):
    response = client.post(
        "/api/companies/1/pricing/simulate",
        json={"period": "2026-09", "product_id": 1, "new_price_cents": 1200, "expected_quantity": 100},
    )
    assert response.status_code == 200, response.text
    assert response.json()["simulated_revenue_cents"] == 1_200_00

    unchanged = client.get("/api/companies/1/finance/cash-accounts")  # sanity: server still healthy
    assert unchanged.status_code == 200
