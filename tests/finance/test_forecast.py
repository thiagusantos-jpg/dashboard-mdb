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


def test_partial_balance_stays_in_forecast(forecast_db):
    from backend.finance.entries import settle_entry
    account = accounts.account_by_key(1, 'rent')
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account['id'], amount_cents=100000,
        competence='2026-09', due_date=date(2026, 9, 20), source='manual',
        external_id=None, description='Aluguel'))
    settle_entry(entry['id'], 40000, paid_at=date(2026, 9, 12))
    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'financial_entries']
    assert amounts == [-60000]


def test_fully_paid_entry_has_no_forecast_balance(forecast_db):
    from backend.finance.entries import settle_entry
    account = accounts.account_by_key(1, 'rent')
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account['id'], amount_cents=50000,
        competence='2026-09', due_date=date(2026, 9, 20), source='manual',
        external_id=None, description='Aluguel'))
    settle_entry(entry['id'], 50000, paid_at=date(2026, 9, 12))
    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'financial_entries']
    assert amounts == []


def test_partial_balance_carries_forward_when_overdue(forecast_db):
    from backend.finance.entries import settle_entry
    account = accounts.account_by_key(1, 'rent')
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account['id'], amount_cents=100000,
        competence='2026-09', due_date=date(2026, 9, 5), source='manual',
        external_id=None, description='Aluguel vencido'))
    settle_entry(entry['id'], 30000, paid_at=date(2026, 9, 5))
    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    today_bucket = next(d for d in result['days'] if d['date'] == AS_OF.isoformat())
    amounts = [i['amount_cents'] for i in today_bucket['items']
               if i['source'] == 'financial_entries']
    assert amounts == [-70000]


def test_reversed_payment_removes_entry_from_open_forecast(forecast_db):
    from backend.finance.entries import settle_entry, reverse_entry
    account = accounts.account_by_key(1, 'rent')
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account['id'], amount_cents=100000,
        competence='2026-09', due_date=date(2026, 9, 20), source='manual',
        external_id=None, description='Aluguel'))
    settle_entry(entry['id'], 40000, paid_at=date(2026, 9, 12))
    reverse_entry(entry['id'], reversed_at=date(2026, 9, 12), reason='Erro de lançamento')
    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'financial_entries']
    assert amounts == []


def test_renegotiated_loan_shows_only_active_schedule_installment(forecast_db):
    loan = loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=100_000, net_disbursement_cents=95_000,
        installments=[{"number": 1, "due_date": "2026-09-20", "principal_cents": 100_000, "interest_cents": 10_000}],
        start_date="2026-09-01",
    )
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-09-25", "principal_cents": 100_000, "interest_cents": 5_000}],
        reason="Prazo estendido",
    )

    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'loan_installments']
    assert amounts == [-105000]


def test_renegotiated_loan_keeps_paid_installment_query_working(forecast_db):
    loan = loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=100_000, net_disbursement_cents=95_000,
        installments=[{"number": 1, "due_date": "2026-09-20", "principal_cents": 100_000, "interest_cents": 10_000}],
        start_date="2026-09-01",
    )
    first = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment(
        first["id"], principal_cents=100_000, interest_cents=10_000,
        paid_at=date(2026, 9, 15),
    )
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-09-25", "principal_cents": 50_000, "interest_cents": 5_000}],
        reason="Nova parcela",
    )

    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'loan_installments']
    assert amounts == [-55000]

    position = loans.loan_position(loan["id"])
    paid = [i for i in position["installments"] if i["status"] == "paid"]
    open_ = [i for i in position["installments"] if i["status"] == "open"]
    assert len(paid) == 1
    assert len(open_) == 1


def test_renegotiated_loan_excludes_orphaned_unpaid_installment_from_closed_schedule(forecast_db):
    # Old schedule has TWO installments: one gets paid, the other is left
    # unpaid (still status='open') when the loan is renegotiated. `renegotiate`
    # only closes the schedule — it does not touch the status of installments
    # still attached to it — so this leftover installment stays 'open' forever
    # on a now-closed schedule. Before the fix, `_open_installments_due` only
    # filtered on `i.status='open'` (plus due date), so this orphaned
    # installment would leak into the forecast right alongside the new
    # schedule's installment. The fix restricts the query to
    # `i.schedule_id=l.active_schedule_id AND s.status='active'`, which must
    # exclude it.
    loan = loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=100_000, net_disbursement_cents=95_000,
        installments=[
            {"number": 1, "due_date": "2026-09-10", "principal_cents": 50_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-09-20", "principal_cents": 50_000, "interest_cents": 5_000},
        ],
        start_date="2026-09-01",
    )
    first, second = loans.loan_position(loan["id"])["installments"]
    loans.pay_installment(
        first["id"], principal_cents=50_000, interest_cents=5_000,
        paid_at=date(2026, 9, 10),
    )
    # `second` (due 2026-09-20) is deliberately left unpaid/open on the old
    # schedule.

    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-09-25", "principal_cents": 60_000, "interest_cents": 6_000}],
        reason="Renegociação com saldo em aberto",
    )

    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'loan_installments']
    # Only the new active schedule's installment (-66000) should appear — the
    # stale open installment on the closed schedule (-55000) must not leak in.
    assert -55000 not in amounts
    assert amounts == [-66000]

    # loan_position is unaffected by this fix (it doesn't filter by schedule
    # status the same way) and must still correctly report the paid
    # installment as paid.
    position = loans.loan_position(loan["id"])
    paid = [i for i in position["installments"] if i["status"] == "paid"]
    assert len(paid) == 1
    assert paid[0]["id"] == first["id"]
    assert paid[0]["paid_principal_cents"] == 50_000
    assert paid[0]["paid_interest_cents"] == 5_000


def test_future_window_shows_remaining_balance(forecast_db):
    from backend.finance.entries import settle_entry
    account = accounts.account_by_key(1, 'rent')
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account['id'], amount_cents=100000,
        competence='2026-10', due_date=date(2026, 10, 5), source='manual',
        external_id=None, description='Aluguel de outubro'))
    settle_entry(entry['id'], 25000, paid_at=date(2026, 9, 12))
    result = forecast(1, date(2026, 10, 1), date(2026, 10, 10), as_of=AS_OF)
    day = next(d for d in result['days'] if d['date'] == '2026-10-05')
    amounts = [i['amount_cents'] for i in day['items'] if i['source'] == 'financial_entries']
    assert amounts == [-75000]
