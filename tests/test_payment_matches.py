"""Sugerir a baixa de uma conta a partir de um débito que já está no caixa (item 8).

A sugestão nunca quita nada sozinha, e cala quando há ambiguidade: dois gastos legítimos
de mesmo valor e data nunca devem ser fundidos por palpite.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts, ledger, payment_matches
from backend.finance.entries import EntryCommand, create_entry


TODAY = date(2026, 9, 20)


@pytest.fixture
def finance_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "payment-matches.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "payment-matches-api.sqlite3")
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


def _bill(description, due, cents, key="rent"):
    account = accounts.account_by_key(1, key)
    return create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=cents, competence=due[:7],
        due_date=date.fromisoformat(due), source="manual", external_id=None, description=description,
    ))


def _debit(cash_account_id, cents, occurred, description="Pagamento de boleto"):
    return ledger.post_cash_event(1, cash_account_id, -cents, date.fromisoformat(occurred), description)


def _bank():
    return ledger.create_account(1, "Banco", "bank")["id"]


def _suggestions():
    return payment_matches.suggestions(1, today=TODAY, include_sensitive=True)


def test_unused_debit_matching_an_open_bill_is_suggested(finance_db):
    bank = _bank()
    bill = _bill("Aluguel", "2026-09-18", 800_00)
    event = _debit(bank, 800_00, "2026-09-19")

    items = _suggestions()

    assert len(items) == 1
    assert items[0]["obligation"]["id"] == str(bill["id"])
    assert items[0]["obligation"]["open_cents"] == 800_00
    assert items[0]["cash_event"]["id"] == str(event["id"])
    assert items[0]["cash_event"]["amount_cents"] == -800_00
    assert items[0]["cash_event"]["occurred_at"] == "2026-09-19"


def test_a_debit_of_a_different_amount_is_not_suggested(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-18", 800_00)
    _debit(bank, 799_00, "2026-09-19")

    assert _suggestions() == []


def test_debit_already_used_by_a_payment_is_not_suggested(client):
    bank = _bank()
    paid = _bill("Aluguel", "2026-09-18", 800_00)
    event = _debit(bank, 800_00, "2026-09-19")
    response = client.post(
        f"/api/companies/1/finance/obligations/entry/{paid['id']}/payments",
        json={
            "amount_cents": 800_00, "paid_at": "2026-09-19", "expected_version": 1,
            "existing_cash_event_id": event["id"],
        },
        headers={"Idempotency-Key": "chave-1"},
    )
    assert response.status_code in (200, 201), response.text

    # Uma segunda conta idêntica: o débito já foi consumido, então não serve de novo.
    _bill("Água", "2026-09-18", 800_00, key="water")

    assert _suggestions() == []


def test_reversed_debit_is_not_suggested(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-18", 800_00)
    event = _debit(bank, 800_00, "2026-09-19")
    ledger.reverse_event(event["id"], reason="Lançado na conta errada")

    assert _suggestions() == []


def test_two_bills_with_the_same_amount_suggest_nothing(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-18", 800_00)
    _bill("Água", "2026-09-18", 800_00, key="water")
    _debit(bank, 800_00, "2026-09-19")

    assert _suggestions() == []


def test_two_debits_for_the_same_bill_suggest_nothing(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-18", 800_00)
    _debit(bank, 800_00, "2026-09-19", description="Boleto")
    _debit(bank, 800_00, "2026-09-17", description="Outro boleto")

    assert _suggestions() == []


def test_the_window_around_the_due_date_has_an_edge(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-14", 800_00)
    _debit(bank, 800_00, "2026-09-19")  # exatamente 5 dias: ainda é a mesma conta
    assert len(_suggestions()) == 1


def test_a_debit_far_from_the_due_date_is_not_suggested(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-13", 800_00)
    _debit(bank, 800_00, "2026-09-19")  # 6 dias: longe demais para afirmar que é a mesma

    assert _suggestions() == []


def test_a_bill_already_paid_is_not_suggested(finance_db):
    bank = _bank()
    _bill("Aluguel", "2026-09-18", 800_00)
    _debit(bank, 800_00, "2026-09-19")
    # Uma conta sem saldo em aberto sai da lista: nada a dar baixa.
    with db.connection() as conn:
        conn.execute("UPDATE financial_entries SET status='paid' WHERE company=1")

    assert _suggestions() == []


def test_endpoint_returns_the_suggestions(client):
    today = date.today()
    bank = _bank()
    bill = _bill("Internet", (today - timedelta(days=1)).isoformat(), 120_00, key="internet")
    event = _debit(bank, 120_00, today.isoformat())

    response = client.get("/api/companies/1/finance/obligations/payment-suggestions")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["obligation"]["id"] == str(bill["id"])
    assert body[0]["cash_event"]["id"] == str(event["id"])
