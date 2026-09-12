from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, ledger, loans
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.forecast import forecast


COMPANY = 1
AS_OF = date(2026, 9, 12)


@pytest.fixture
def forecast_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "forecast.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def test_forecast_reports_lowest_cash_date(forecast_db):
    account = ledger.create_account(COMPANY, "Banco", "bank")
    ledger.post_cash_event(COMPANY, account["id"], 10_000_00, date(2026, 8, 25), "Saldo inicial")

    rent = accounts.account_by_key(COMPANY, "rent")
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=rent["id"], amount_cents=4_000_00,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel de setembro",
    ))

    loan = loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=3_000_00, net_disbursement_cents=2_900_00,
        installments=[{"number": 1, "due_date": "2026-09-10", "principal_cents": 3_000_00, "interest_cents": 0}],
        start_date="2026-09-01",
    )

    result = forecast(COMPANY, date(2026, 9, 1), date(2026, 9, 30), "base", as_of=AS_OF)

    assert result["lowest"]["date"] == "2026-09-20"
    assert result["lowest"]["balance_cents"] == 10_000_00 - 3_000_00 - 4_000_00
    assert result["alerts"] == []
    # The installment's due date (09-10) is before "today" (09-12, AS_OF) — an
    # overdue-but-unpaid obligation is carried forward onto today, not dropped.
    today_bucket = next(d for d in result["days"] if d["date"] == "2026-09-12")
    assert today_bucket["items"][0]["source"] == "loan_installments"
    assert today_bucket["items"][0]["confidence"] == "forecast"


def test_forecast_flags_negative_cash(forecast_db):
    account = ledger.create_account(COMPANY, "Banco", "bank")
    ledger.post_cash_event(COMPANY, account["id"], 1_000_00, date(2026, 8, 25), "Saldo inicial")
    rent = accounts.account_by_key(COMPANY, "rent")
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=rent["id"], amount_cents=4_000_00,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel de setembro",
    ))

    result = forecast(COMPANY, date(2026, 9, 1), date(2026, 9, 30), "base", as_of=AS_OF)

    assert result["alerts"][0]["type"] == "negative_cash"
    assert result["alerts"][0]["date"] == "2026-09-20"


def test_realized_days_use_ledger_not_forecast_sources(forecast_db):
    account = ledger.create_account(COMPANY, "Banco", "bank")
    ledger.post_cash_event(COMPANY, account["id"], 500_00, date(2026, 9, 5), "Venda do dia")

    result = forecast(COMPANY, date(2026, 9, 1), date(2026, 9, 12), "base", as_of=AS_OF)

    realized_day = next(d for d in result["days"] if d["date"] == "2026-09-05")
    assert realized_day["items"][0]["confidence"] == "realized"
    assert realized_day["items"][0]["source"] == "ledger"


def test_invalid_scenario_is_rejected(forecast_db):
    with pytest.raises(ValueError):
        forecast(COMPANY, date(2026, 9, 1), date(2026, 9, 30), "unicorn", as_of=AS_OF)
