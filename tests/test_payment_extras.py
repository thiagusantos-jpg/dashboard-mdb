"""Juros e multa ao pagar uma conta vencida (item 4 de Contas a pagar).

O acréscimo pago além do saldo vira um lançamento já quitado em "Juros e multas",
com uma única saída de caixa somando conta + acréscimo. Estornar o pagamento desfaz
o acréscimo junto: ele nunca sobra como uma nova conta a pagar."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts, entries as entries_module, ledger, loans
from backend.finance.entries import EntryCommand, create_entry


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "payment-extras.sqlite3")
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


def _overdue_bill(amount_cents=100_000):
    account = accounts.account_by_key(1, "rent")
    return create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=amount_cents, competence="2026-09",
        due_date=date(2026, 9, 10), source="manual", external_id=None, description="Aluguel",
    ))


def _pay(client, entry_id, key, **extra):
    body = {"amount_cents": 100_000, "paid_at": "2026-09-20", "expected_version": 1}
    body.update(extra)
    return client.post(
        f"/api/companies/1/finance/obligations/entry/{entry_id}/payments",
        json=body, headers={"Idempotency-Key": key},
    )


def _cash_events():
    with db.connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT amount_cents,description FROM cash_events WHERE company=1 ORDER BY id")]


def test_late_fee_becomes_a_settled_expense_and_one_cash_outflow(client):
    bill = _overdue_bill()
    bank = ledger.create_account(1, "Banco X", "bank")

    response = _pay(client, bill["id"], "fee-1", cash_account_id=bank["id"], late_fee_cents=1_234)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["late_fee_cents"] == 1_234
    assert body["obligation"]["open_cents"] == 0
    assert [event["amount_cents"] for event in _cash_events()] == [-101_234], "uma saída só, conta + acréscimo"

    fee = entries_module.get_entry(int(body["late_fee_entry_id"]))
    assert fee["account_id"] == accounts.account_by_key(1, "fines")["id"]
    assert (fee["amount_cents"], fee["open_cents"], fee["status"]) == (1_234, 0, "paid")
    assert fee["competence"] == "2026-09"

    listed = client.get("/api/companies/1/finance/obligations").json()["items"]
    assert listed == [], "nem a conta paga nem o acréscimo ficam como contas a pagar"


def test_late_fee_is_refused_for_a_loan_installment(client):
    loan = loans.create_loan(
        1, lender="Banco Local", purpose="Capital de giro", principal_cents=200_000,
        net_disbursement_cents=195_000, start_date="2026-09-01",
        installments=[{"number": 1, "due_date": "2026-09-10", "principal_cents": 200_000, "interest_cents": 5_000}],
    )
    installment_id = loans.loan_position(loan["id"])["installments"][0]["id"]
    bank = ledger.create_account(1, "Banco X", "bank")

    response = client.post(
        f"/api/companies/1/finance/obligations/loan_installment/{installment_id}/payments",
        json={"amount_cents": 205_000, "paid_at": "2026-09-20", "expected_version": 1,
              "cash_account_id": bank["id"], "principal_cents": 200_000, "interest_cents": 5_000,
              "late_fee_cents": 1_000},
        headers={"Idempotency-Key": "fee-loan"},
    )

    assert response.status_code == 422, response.text


def test_late_fee_must_be_positive(client):
    bill = _overdue_bill()
    bank = ledger.create_account(1, "Banco X", "bank")
    assert _pay(client, bill["id"], "fee-zero", cash_account_id=bank["id"], late_fee_cents=0).status_code == 422


def test_same_key_with_a_different_late_fee_is_a_conflict(client):
    bill = _overdue_bill()
    bank = ledger.create_account(1, "Banco X", "bank")
    assert _pay(client, bill["id"], "fee-2", cash_account_id=bank["id"], late_fee_cents=1_000).status_code == 200

    conflict = _pay(client, bill["id"], "fee-2", cash_account_id=bank["id"], late_fee_cents=2_000)

    assert conflict.status_code == 409, conflict.text


def test_replay_with_the_same_late_fee_returns_the_same_payment(client):
    bill = _overdue_bill()
    bank = ledger.create_account(1, "Banco X", "bank")
    first = _pay(client, bill["id"], "fee-3", cash_account_id=bank["id"], late_fee_cents=1_000)
    replay = _pay(client, bill["id"], "fee-3", cash_account_id=bank["id"], late_fee_cents=1_000)

    assert replay.status_code == 200, replay.text
    assert replay.json()["payment_id"] == first.json()["payment_id"]
    assert len(_cash_events()) == 1, "a repetição não move dinheiro de novo"


def test_reversing_the_payment_also_undoes_the_late_fee(client):
    bill = _overdue_bill()
    bank = ledger.create_account(1, "Banco X", "bank")
    paid = _pay(client, bill["id"], "fee-4", cash_account_id=bank["id"], late_fee_cents=1_234).json()

    reversal = client.post(
        f"/api/companies/1/finance/payments/{paid['payment_id']}/reverse",
        json={"reason": "Pago em duplicidade", "reversed_at": "2026-09-21", "expected_version": 2},
        headers={"Idempotency-Key": "fee-4-rev"},
    )

    assert reversal.status_code == 200, reversal.text
    assert sum(event["amount_cents"] for event in _cash_events()) == 0, "o caixa volta ao que era"
    fee = entries_module.get_entry(int(paid["late_fee_entry_id"]))
    assert fee["status"] in {"reversed", "cancelled"}, "o acréscimo é desfeito, não vira conta a pagar"
    keys = [item["key"] for item in client.get("/api/companies/1/finance/obligations").json()["items"]]
    assert f"entry:{paid['late_fee_entry_id']}" not in keys
