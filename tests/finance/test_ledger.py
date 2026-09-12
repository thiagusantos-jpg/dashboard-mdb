from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import ledger


COMPANY = 1


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "ledger.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def make_account(kind="bank", name="Banco X"):
    return ledger.create_account(COMPANY, name, kind)


def test_create_account_rejects_unknown_kind(ledger_db):
    with pytest.raises(ValueError):
        ledger.create_account(COMPANY, "Conta", "poupanca")


def test_post_cash_event_updates_account_balance(ledger_db):
    account = make_account()
    ledger.post_cash_event(COMPANY, account["id"], 10_000_00, date(2026, 9, 5), "Depósito inicial")

    assert ledger.account_balance(account["id"]) == 10_000_00
    assert ledger.consolidated_balance(COMPANY) == 10_000_00


def test_internal_transfer_does_not_change_consolidated_cash(ledger_db):
    stone = make_account(name="Stone")
    other_bank = make_account(name="Banco Y")
    ledger.post_cash_event(COMPANY, stone["id"], 5_000_00, date(2026, 9, 1), "Recebimento")
    before = ledger.consolidated_balance(COMPANY)

    ledger.transfer(COMPANY, stone["id"], other_bank["id"], 5_000_00, date(2026, 9, 12))

    assert ledger.consolidated_balance(COMPANY) == before
    assert ledger.account_balance(stone["id"]) == 0
    assert ledger.account_balance(other_bank["id"]) == 5_000_00


def test_transfer_rejects_same_account(ledger_db):
    account = make_account()
    with pytest.raises(ValueError):
        ledger.transfer(COMPANY, account["id"], account["id"], 100_00, date(2026, 9, 1))


def test_reversal_is_a_new_linked_event_not_a_mutation(ledger_db):
    account = make_account()
    event = ledger.post_cash_event(COMPANY, account["id"], 3_000_00, date(2026, 9, 1), "Venda")

    reversal = ledger.reverse_event(event["id"], reason="Lançamento duplicado")

    assert reversal["reversed_event_id"] == event["id"]
    assert reversal["amount_cents"] == -3_000_00
    assert ledger.account_balance(account["id"]) == 0
    # the original row is untouched — both events still exist
    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM cash_events WHERE cash_account_id=?", (account["id"],)
        ).fetchone()["c"]
    assert count == 2


def test_cannot_reverse_the_same_event_twice(ledger_db):
    account = make_account()
    event = ledger.post_cash_event(COMPANY, account["id"], 1_000_00, date(2026, 9, 1), "Venda")
    ledger.reverse_event(event["id"], reason="Erro")

    with pytest.raises(ValueError):
        ledger.reverse_event(event["id"], reason="Erro de novo")
