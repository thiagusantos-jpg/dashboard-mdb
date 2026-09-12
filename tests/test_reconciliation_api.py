from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts
from backend.finance.entries import EntryCommand, create_entry


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "reconciliation-api.sqlite3")
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


def test_suggest_and_confirm_via_api(client):
    account = accounts.account_by_key(1, "sales")
    create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=1_000_00,
        competence="2026-09", due_date=date(2026, 9, 10), source="manual",
        external_id=None, description="Venda cartão",
    ))
    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    event = client.post(
        "/api/companies/1/finance/cash-events",
        json={"cash_account_id": cash_account["id"], "amount_cents": 1_000_00,
              "occurred_at": "2026-09-12", "description": "Crédito"},
    ).json()

    response = client.post(
        "/api/companies/1/finance/reconciliation/suggest",
        json={"cash_event_id": event["id"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "auto_matched"

    groups = client.get("/api/companies/1/finance/reconciliation").json()
    assert len(groups) == 1


def test_confirm_with_unknown_payment_id_returns_404_error_contract(client):
    """Task B5 fix round 1, Finding 5: a payment_id outside scope must be a
    404 {code,message,fields} error (a missing resource), not a bare-string
    422 — the plan's Global Constraints require every new 422/409/403/404 to
    use this shape."""
    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    event = client.post(
        "/api/companies/1/finance/cash-events",
        json={"cash_account_id": cash_account["id"], "amount_cents": 12_345,
              "occurred_at": "2026-09-12", "description": "Débito sem par"},
    ).json()
    group = client.post(
        "/api/companies/1/finance/reconciliation/suggest",
        json={"cash_event_id": event["id"]},
    ).json()
    assert group["status"] == "unmatched"

    response = client.post(
        f"/api/companies/1/finance/reconciliation/{group['id']}/confirm",
        json={"payment_ids": [999999999]},
    )
    assert response.status_code == 404, response.text
    body = response.json()["detail"]
    assert body["code"] == "not_found"
    assert isinstance(body["message"], str) and body["message"]
    assert body["fields"] == ["payment_ids"]
