from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "actions-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def test_create_list_and_transition_action(client):
    created = client.post(
        "/api/companies/1/actions",
        json={"alert_key": "estoque", "alert_version": "produto 77 sem estoque", "title": "Ruptura de estoque"},
    )
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]

    listed = client.get("/api/companies/1/actions")
    assert len(listed.json()) == 1

    transitioned = client.post(
        f"/api/companies/1/actions/{action_id}/transition",
        json={"to_status": "in_progress"},
    )
    assert transitioned.status_code == 200, transitioned.text
    assert transitioned.json()["status"] == "in_progress"

    events = client.get(f"/api/companies/1/actions/{action_id}/events")
    assert len(events.json()) == 2


def test_basket_pairs_endpoint(client):
    db.put_dataset(1, "sales", "2026-09", {
        "raw_count": 1,
        "receipts": [{
            "id": 1, "reference_id": 0, "aliases": [1], "date": "2026-09-05", "status": "V", "species": "CF",
            "revenue": 100_00,
            "items": [
                {"id": 1, "product_id": 10, "quantity": "1", "revenue": 50_00, "cost": 20_00, "unit_cost": "20", "status": "V"},
                {"id": 2, "product_id": 20, "quantity": "1", "revenue": 50_00, "cost": 20_00, "unit_cost": "20", "status": "V"},
            ],
        }],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })

    response = client.get("/api/companies/1/baskets", params={"period": "2026-09"})
    assert response.status_code == 200, response.text
    assert response.json()["receipt_count"] == 1
