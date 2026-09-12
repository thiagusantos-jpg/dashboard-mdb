from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "cashflow-api.sqlite3")
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


def create_account(client, name):
    response = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": name, "kind": "bank"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_transfer_between_accounts_keeps_consolidated_balance(client):
    stone = create_account(client, "Stone")
    other = create_account(client, "Banco Y")
    client.post(
        "/api/companies/1/finance/cash-events",
        json={"cash_account_id": stone["id"], "amount_cents": 5_000_00,
              "occurred_at": "2026-09-01", "description": "Recebimento"},
    )

    transfer = client.post(
        "/api/companies/1/finance/cash-transfers",
        json={"from_account_id": stone["id"], "to_account_id": other["id"],
              "amount_cents": 5_000_00, "occurred_at": "2026-09-12"},
    )
    assert transfer.status_code == 201, transfer.text

    balance = client.get("/api/companies/1/finance/cash-balance")
    assert balance.json()["balance_cents"] == 5_000_00

    accounts = client.get("/api/companies/1/finance/cash-accounts").json()
    by_name = {a["name"]: a["balance_cents"] for a in accounts}
    assert by_name["Stone"] == 0
    assert by_name["Banco Y"] == 5_000_00


def test_forecast_endpoint_returns_days_and_lowest(client):
    response = client.get(
        "/api/companies/1/finance/forecast",
        params={"start": "2026-09-01", "end": "2026-09-30", "scenario": "base"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["days"]) == 30
    assert "lowest" in body
