from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance.accounts import list_accounts
from backend.finance.entries import (
    EntryCommand,
    cash_for,
    create_entry,
    get_entry,
    result_for,
    reverse_entry,
    settle_entry,
)


@pytest.fixture
def expense_account(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "entries.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    return next(row for row in list_accounts(1) if row["system_key"] == "electricity")


def expense(account_id, amount=10_000):
    return create_entry(
        EntryCommand(
            company_id=1,
            account_id=account_id,
            amount_cents=amount,
            competence="2026-08",
            due_date=date(2026, 9, 5),
            source="manual",
            external_id=None,
            description="Energia elétrica",
        )
    )


def test_august_expense_paid_in_september_hits_correct_views(expense_account):
    entry = expense(expense_account["id"])
    settle_entry(entry["id"], 10_000, paid_at=date(2026, 9, 5))

    assert result_for(1, "2026-08")["expenses"] == 10_000
    assert cash_for(1, "2026-09")["outflows"] == 10_000


def test_partial_payment_keeps_open_balance(expense_account):
    entry = expense(expense_account["id"])
    settle_entry(entry["id"], 4_000, paid_at=date(2026, 9, 5))

    current = get_entry(entry["id"])
    assert current["status"] == "partially_paid"
    assert current["paid_cents"] == 4_000
    assert current["open_cents"] == 6_000


def test_reversal_preserves_history_and_offsets_cash(expense_account):
    entry = expense(expense_account["id"])
    settle_entry(entry["id"], 10_000, paid_at=date(2026, 9, 5))
    reverse_entry(entry["id"], reversed_at=date(2026, 9, 8), reason="Pagamento devolvido")

    assert get_entry(entry["id"])["status"] == "reversed"
    assert result_for(1, "2026-08")["expenses"] == 0
    assert cash_for(1, "2026-09")["outflows"] == 0
    with db.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM financial_events WHERE entry_id=?",
            (entry["id"],),
        ).fetchone()["total"] == 3

