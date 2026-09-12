from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "open-finance-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    for var in ("STONE_CLIENT_ID", "STONE_PRIVATE_KEY", "STONE_REDIRECT_URI"):
        monkeypatch.delenv(var, raising=False)
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


def test_consent_without_configured_credentials_returns_a_clear_error(client):
    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()

    response = client.post(
        "/api/companies/1/finance/open-finance/consent",
        json={"cash_account_id": cash_account["id"], "return_url": "https://app.local/return"},
    )

    assert response.status_code == 501
    assert "STONE_CLIENT_ID" in response.json()["detail"]


def test_list_connections_is_empty_by_default(client):
    response = client.get("/api/companies/1/finance/open-finance/connections")
    assert response.status_code == 200
    assert response.json() == []
