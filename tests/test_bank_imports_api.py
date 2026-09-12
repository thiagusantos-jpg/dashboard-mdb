from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts, ledger
from backend.finance.entries import EntryCommand, create_entry


FIXTURES = Path(__file__).parent / "integrations" / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "bank-imports-api.sqlite3")
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


def test_preview_then_commit_ofx_import(client):
    content = (FIXTURES / "stone-sample.ofx").read_bytes()

    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()

    preview = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports/preview",
        files={"file": ("stone-sample.ofx", content, "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["count"] == 3
    assert {item["decision"] for item in preview_body["items"]} == {"new"}

    commit = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("stone-sample.ofx", content, "application/octet-stream")},
        data={
            "preview_hash": preview_body["preview_hash"],
            "decisions": json.dumps({item["external_id"]: item["decision"] for item in preview_body["items"]}),
        },
    )
    assert commit.status_code == 201, commit.text
    assert commit.json()["imported"] == 3

    balance = client.get("/api/companies/1/finance/cash-balance").json()
    assert balance["balance_cents"] == 600_00 + 400_00 - 25_00


def test_importing_a_payments_bank_line_offers_a_link_suggestion(client):
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    cash_account = ledger.create_account(1, "Banco X", "bank")

    payment = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        headers={"Idempotency-Key": "pay-1"},
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 1,
            "cash_account_id": cash_account["id"],
        },
    )
    assert payment.status_code == 200, payment.text
    cash_event_id = payment.json()["cash_event_id"]

    content = (
        "Data,Descricao,Valor,FITID\n2026-09-12,Pagamento fornecedor,-400.00,BANK-0001\n"
    ).encode("utf-8")
    preview = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports/preview",
        files={"file": ("extrato.csv", content, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    item = preview.json()["items"][0]
    assert item["candidate_cash_event_ids"] == [str(cash_event_id)]
    assert item["decision"] == f"link:{cash_event_id}"

    commit = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("extrato.csv", content, "text/csv")},
        data={
            "preview_hash": preview.json()["preview_hash"],
            "decisions": json.dumps({item["external_id"]: item["decision"]}),
        },
    )
    assert commit.status_code == 201, commit.text
    assert commit.json()["linked"] == 1
    assert commit.json()["imported"] == 0

    balance = client.get("/api/companies/1/finance/cash-balance").json()
    assert balance["balance_cents"] == -40_000
