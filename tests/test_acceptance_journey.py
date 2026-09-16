"""Integrated acceptance journey from the master plan (task C7), end to end
through the HTTP API with synthetic data:

R$ 1.000 expense → edit description → pay R$ 400 linked to the imported
statement line → R$ 600 open → settle the rest → correct (reverse) the second
payment → R$ 600 open again → pay it linked to the other statement line;
archive a category without changing history; renegotiate a loan so only the
new schedule is payable; change the password.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance.reporting import management_result


BASE = "/api/companies/1/finance"

STATEMENT = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<BANKMSGSRSV1>
<STMTTRNRS>
<STMTRS>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260912100000
<TRNAMT>-400.00
<FITID>JOURNEY-400
<MEMO>Aluguel parcial
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260915100000
<TRNAMT>-600.00
<FITID>JOURNEY-600
<MEMO>Aluguel restante
</STMTTRN>
</BANKTRANLIST>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "acceptance.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"})
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def obligation(client, entry_id):
    return ok(client.get(f"{BASE}/obligations/entry/{entry_id}"))


def pay(client, entry_id, amount, key, **cash):
    current = obligation(client, entry_id)
    return ok(client.post(
        f"{BASE}/obligations/entry/{entry_id}/payments",
        json={"amount_cents": amount, "paid_at": "2026-09-15", "expected_version": current["version"], **cash},
        headers={"Idempotency-Key": key},
    ))


def test_expense_payment_correction_and_statement_journey(client):
    rent = next(a for a in ok(client.get(f"{BASE}/accounts")) if a["system_key"] == "rent")
    entry = ok(client.post(f"{BASE}/entries", json={
        "account_id": str(rent["id"]), "amount_cents": 100_000, "competence": "2026-09",
        "due_date": "2026-09-20", "description": "Aluguel",
    }), 201)

    edited = ok(client.patch(f"{BASE}/entries/{entry['id']}", json={"expected_version": entry["version"], "description": "Aluguel de setembro"}))
    assert edited["description"] == "Aluguel de setembro"

    bank = ok(client.post(f"{BASE}/cash-accounts", json={"name": "Banco", "kind": "bank"}), 201)
    preview = ok(client.post(f"{BASE}/cash-accounts/{bank['id']}/bank-imports/preview",
                             files={"file": ("extrato.ofx", STATEMENT, "application/octet-stream")}))
    ok(client.post(f"{BASE}/cash-accounts/{bank['id']}/bank-imports",
                   files={"file": ("extrato.ofx", STATEMENT, "application/octet-stream")},
                   data={"preview_hash": preview["preview_hash"],
                         "decisions": json.dumps({item["external_id"]: "new" for item in preview["items"]})}), 201)
    statement = {event["amount_cents"]: event for event in ok(client.get(f"{BASE}/cash-events"))}
    line_400, line_600 = statement[-40_000], statement[-60_000]

    pay(client, entry["id"], 40_000, "journey-400", existing_cash_event_id=str(line_400["id"]))
    assert obligation(client, entry["id"])["open_cents"] == 60_000

    second = pay(client, entry["id"], 60_000, "journey-600", cash_account_id=str(bank["id"]))
    assert second["obligation"]["status"] == "paid"
    assert second["obligation"]["open_cents"] == 0

    current = obligation(client, entry["id"])
    ok(client.post(f"{BASE}/payments/{second['payment_id']}/reverse",
                   json={"reason": "Pago pela conta errada", "reversed_at": "2026-09-15", "expected_version": current["version"]},
                   headers={"Idempotency-Key": "journey-undo-600"}))
    reopened = obligation(client, entry["id"])
    assert reopened["open_cents"] == 60_000
    assert reopened["paid_cents"] == 40_000

    final = pay(client, entry["id"], 60_000, "journey-600-statement", existing_cash_event_id=str(line_600["id"]))
    assert final["obligation"]["status"] == "paid"
    payments = obligation(client, entry["id"])["payments"]
    live = [p for p in payments if p["reversed_at"] is None]
    assert sorted(p["cash_event_id"] for p in live) == sorted([str(line_400["id"]), str(line_600["id"])])
    assert all(not p["generated_cash_event"] for p in live)  # statement lines reused, no duplicated cash movement

    # A second import of the same statement creates nothing new.
    again = ok(client.post(f"{BASE}/cash-accounts/{bank['id']}/bank-imports/preview",
                           files={"file": ("extrato.ofx", STATEMENT, "application/octet-stream")}))
    assert again["already_imported"] == len(again["items"])
    assert all(not item["candidate_cash_event_ids"] for item in again["items"])
    before = ok(client.get(f"{BASE}/cash-events"))
    result = ok(client.post(f"{BASE}/cash-accounts/{bank['id']}/bank-imports",
                            files={"file": ("extrato.ofx", STATEMENT, "application/octet-stream")},
                            data={"preview_hash": again["preview_hash"],
                                  "decisions": json.dumps({item["external_id"]: item["decision"] for item in again["items"]})}), 201)
    assert result["imported"] == 0 and result["duplicates"] == len(again["items"])
    assert len(ok(client.get(f"{BASE}/cash-events"))) == len(before)


def test_archiving_a_category_keeps_history(client):
    category = ok(client.post(f"{BASE}/accounts", json={"name": "Reformas", "nature": "operating_expense"}), 201)
    ok(client.post(f"{BASE}/entries", json={
        "account_id": str(category["id"]), "amount_cents": 50_000, "competence": "2026-09",
        "due_date": "2026-09-20", "description": "Pintura",
    }), 201)
    before = management_result(1, "2026-09")["operating_expenses_cents"]

    ok(client.patch(f"{BASE}/accounts/{category['id']}", json={"expected_version": category["version"], "archived": True}))

    assert management_result(1, "2026-09")["operating_expenses_cents"] == before == 50_000


def test_renegotiating_a_loan_leaves_only_the_new_schedule_payable(client):
    loan = ok(client.post(f"{BASE}/loans", json={
        "lender": "Banco", "purpose": "Capital de giro", "principal_cents": 200_000, "net_disbursement_cents": 195_000,
        "start_date": "2026-09-01",
        "installments": [
            {"number": 1, "due_date": "2026-10-10", "principal_cents": 100_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-11-10", "principal_cents": 100_000, "interest_cents": 5_000},
        ],
    }), 201)
    ok(client.post(f"{BASE}/loans/{loan['loan']['id']}/renegotiate", json={
        "reason": "Novo prazo",
        "installments": [
            {"number": 1, "due_date": "2026-12-10", "principal_cents": 50_000, "interest_cents": 2_000},
            {"number": 2, "due_date": "2027-01-10", "principal_cents": 50_000, "interest_cents": 2_000},
            {"number": 3, "due_date": "2027-02-10", "principal_cents": 50_000, "interest_cents": 2_000},
            {"number": 4, "due_date": "2027-03-10", "principal_cents": 50_000, "interest_cents": 2_000},
        ],
    }))

    payable = ok(client.get(f"{BASE}/obligations?kind=loan_installment"))["items"]

    assert sorted(item["due_date"] for item in payable) == ["2026-12-10", "2027-01-10", "2027-02-10", "2027-03-10"]
    assert sum(item["open_cents"] for item in payable) == 208_000


def test_password_change_lets_the_new_password_in_and_keeps_the_old_one_out(client):
    me = ok(client.get("/api/me"))
    ok(client.post("/api/me/password", json={
        "current_password": "bootstrap-password", "new_password": "nova-senha-forte", "expected_version": me["version"],
    }))

    fresh = TestClient(api.app)
    assert fresh.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"}).status_code == 401
    assert fresh.post("/api/login", json={"email": "admin@loja.test", "password": "nova-senha-forte"}).status_code == 200
