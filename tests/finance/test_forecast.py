from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from backend import database as db
from backend.finance import accounts, ledger, loans
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.forecast import SCENARIOS, forecast


COMPANY = 1
AS_OF = date(2026, 9, 12)


def _count_queries(fn):
    """Count every SQL statement executed on any SQLite connection opened
    while `fn` runs — the exact methodology docs/validation/2026-09-12-baseline.md
    used (a `sqlite3.connect` wrapper that attaches `set_trace_callback` to
    every new connection). `sqlite3.Connection.execute` itself can't be
    monkeypatched (it's a read-only slot on a built-in type), so this hooks
    connection creation instead. Includes the `PRAGMA foreign_keys=ON` every
    backend.database.connection() open issues, so it slightly overcounts
    "business queries", but it is an honest, direct proxy for "how many
    round-trips to the database this call makes" — which is exactly what
    task B7's optimization targets."""
    real_connect = sqlite3.connect
    counter = {"n": 0}

    def counting_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(lambda _sql: counter.__setitem__("n", counter["n"] + 1))
        return conn

    sqlite3.connect = counting_connect
    try:
        result = fn()
    finally:
        sqlite3.connect = real_connect
    return result, counter["n"]


# --- Frozen pre-B7 reference implementation, for the equivalence test below.
#
# This is a verbatim copy of forecast.py's day-by-day implementation as it
# existed BEFORE task B7's optimization (one-plus query per day). It exists
# solely so test_forecast_optimization_is_equivalent_to_reference below can
# assert the optimized forecast() produces IDENTICAL output to the original
# on a realistic scenario — proving the query-count optimization did not
# silently change any result. Do not "fix" divergences here to match the
# optimized version; if they ever disagree, the optimized version has a bug.


def _ref_date_range(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _ref_balance_before(company, day):
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS balance FROM cash_events WHERE company=? AND occurred_at<?",
            (company, day.isoformat()),
        ).fetchone()
    return int(row["balance"])


def _ref_realized_events_for_day(company, day):
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cash_events WHERE company=? AND occurred_at=?",
            (company, day.isoformat()),
        ).fetchall()
    return [
        {"amount_cents": row["amount_cents"], "description": row["description"],
         "source": "ledger", "confidence": "realized"}
        for row in rows
    ]


def _ref_open_entries_due(company, day, *, due_on_or_before=False):
    comparator = "<=" if due_on_or_before else "="
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT e.id,e.amount_cents,e.description,a.nature,
                   COALESCE(SUM(
                       CASE WHEN ev.event_type='settled' THEN ev.amount_cents
                            WHEN ev.event_type='reversed' THEN -ev.amount_cents ELSE 0 END
                   ),0) AS paid_cents
            FROM financial_entries e
            JOIN finance_accounts a ON a.id=e.account_id
            LEFT JOIN financial_events ev ON ev.entry_id=e.id
            WHERE e.company=? AND e.due_date{comparator}?
              AND e.status IN ('open','overdue','partially_paid')
            GROUP BY e.id
            """,
            (company, day.isoformat()),
        ).fetchall()
    items = []
    for row in rows:
        remaining = max(0, row["amount_cents"] - row["paid_cents"])
        if remaining <= 0:
            continue
        signed = remaining if row["nature"] in ("revenue", "financing_inflow") else -remaining
        items.append({"amount_cents": signed, "description": row["description"],
                      "source": "financial_entries", "confidence": "forecast"})
    return items


def _ref_open_installments_due(company, day, *, due_on_or_before=False):
    comparator = "<=" if due_on_or_before else "="
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT i.total_cents,i.number,l.lender FROM loan_installments i
            JOIN loans l ON l.id=i.loan_id
            JOIN loan_schedules s ON s.id=i.schedule_id
            WHERE l.company=? AND i.due_date{comparator}?
              AND i.schedule_id=l.active_schedule_id
              AND s.status='active' AND i.status='open'
            """,
            (company, day.isoformat()),
        ).fetchall()
    return [
        {"amount_cents": -row["total_cents"], "description": f"Parcela {row['number']} — {row['lender']}",
         "source": "loan_installments", "confidence": "forecast"}
        for row in rows
    ]


def _ref_average_daily_realized(company, as_of, window_days=30):
    window_start = as_of - timedelta(days=window_days)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS total FROM cash_events WHERE company=? AND occurred_at>=? AND occurred_at<?",
            (company, window_start.isoformat(), as_of.isoformat()),
        ).fetchone()
    return int(row["total"]) / window_days


def _ref_scenario_layer(company, day, scenario, as_of):
    multiplier = SCENARIOS[scenario]
    if multiplier == 1.0:
        return []
    baseline = _ref_average_daily_realized(company, as_of)
    if baseline == 0:
        return []
    adjustment = round(baseline * (multiplier - 1.0))
    if adjustment == 0:
        return []
    return [{"amount_cents": adjustment, "description": f"Ajuste de cenário ({scenario})",
             "source": "scenario", "confidence": "simulated"}]


def _reference_forecast(company, start, end, scenario="base", *, as_of=None):
    today = as_of or date.today()
    balance = _ref_balance_before(company, start)
    days = []
    for day in _ref_date_range(start, end):
        if day < today:
            items = _ref_realized_events_for_day(company, day)
        elif day == today:
            items = _ref_realized_events_for_day(company, day)
            items += _ref_open_entries_due(company, day, due_on_or_before=True)
            items += _ref_open_installments_due(company, day, due_on_or_before=True)
        else:
            items = _ref_open_entries_due(company, day) + _ref_open_installments_due(company, day)
            items += _ref_scenario_layer(company, day, scenario, today)
        balance += sum(item["amount_cents"] for item in items)
        days.append({"date": day.isoformat(), "items": items, "balance_cents": balance})

    lowest = min(days, key=lambda d: d["balance_cents"]) if days else None
    alerts = []
    if lowest is not None and lowest["balance_cents"] < 0:
        alerts.append({"type": "negative_cash", "date": lowest["date"], "balance_cents": lowest["balance_cents"]})
    return {
        "company": company, "start": start.isoformat(), "end": end.isoformat(), "scenario": scenario,
        "starting_balance_cents": _ref_balance_before(company, start),
        "days": days,
        "lowest": {"date": lowest["date"], "balance_cents": lowest["balance_cents"]} if lowest else None,
        "alerts": alerts,
    }


def _normalize(result):
    """Item ORDER within a day can legitimately differ between the grouped
    (optimized) queries and the original per-row queries — sort them so the
    comparison is about content, not incidental SQL row order."""
    normalized = dict(result)
    normalized["days"] = [
        {**day, "items": sorted(day["items"], key=lambda i: (i["source"], i["description"], i["amount_cents"]))}
        for day in result["days"]
    ]
    return normalized


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
    # Partially pay the principal (task B7: renegotiate's new schedule must
    # sum to the OUTSTANDING principal — 100_000-40_000=60_000 — not an
    # arbitrary amount; pay_installment_legacy_unsafe still marks the
    # installment 'paid' unconditionally regardless of whether the amount
    # actually covers it, which is exactly the "KNOWN BUG" this legacy
    # fixture helper deliberately preserves for tests like this one).
    loans.pay_installment_legacy_unsafe(
        first["id"], principal_cents=40_000, interest_cents=10_000,
        paid_at=date(2026, 9, 15),
    )
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-09-25", "principal_cents": 60_000, "interest_cents": 5_000}],
        reason="Nova parcela",
    )

    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'loan_installments']
    assert amounts == [-65000]

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
    loans.pay_installment_legacy_unsafe(
        first["id"], principal_cents=50_000, interest_cents=5_000,
        paid_at=date(2026, 9, 10),
    )
    # `second` (due 2026-09-20) is deliberately left unpaid/open on the old
    # schedule.

    # Task B7: renegotiate's new schedule must sum to the loan's outstanding
    # principal (100_000 total - 50_000 paid on `first` = 50_000) — the
    # orphaned `second` installment's own 50_000 is exactly what's being
    # replaced here, which is why this number changed from the pre-B7
    # version of this test (60_000, an arbitrary amount unrelated to what
    # was actually still owed).
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-09-25", "principal_cents": 50_000, "interest_cents": 6_000}],
        reason="Renegociação com saldo em aberto",
    )

    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'loan_installments']
    # Only the new active schedule's installment (-56000) should appear — the
    # stale open installment on the closed schedule (-55000) must not leak in.
    assert -55000 not in amounts
    assert amounts == [-56000]

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


# --- Task B7: query-count optimization ---------------------------------


def _seed_realistic_scenario(forecast_db):
    account = ledger.create_account(COMPANY, "Banco", "bank")
    ledger.post_cash_event(COMPANY, account["id"], 10_000_00, date(2026, 8, 20), "Saldo inicial")
    ledger.post_cash_event(COMPANY, account["id"], 500_00, date(2026, 9, 5), "Venda do dia")
    ledger.post_cash_event(COMPANY, account["id"], 300_00, date(2026, 9, 10), "Venda do dia")

    rent = accounts.account_by_key(COMPANY, "rent")
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=rent["id"], amount_cents=4_000_00,
        competence="2026-09", due_date=date(2026, 9, 5), source="manual",
        external_id=None, description="Aluguel vencido"))
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=rent["id"], amount_cents=2_000_00,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel de setembro"))
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=rent["id"], amount_cents=1_000_00,
        competence="2026-10", due_date=date(2026, 10, 25), source="manual",
        external_id=None, description="Aluguel de outubro"))

    loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=3_000_00, net_disbursement_cents=2_900_00,
        installments=[
            {"number": 1, "due_date": "2026-09-08", "principal_cents": 1_500_00, "interest_cents": 6_00},
            {"number": 2, "due_date": "2026-09-28", "principal_cents": 1_500_00, "interest_cents": 6_00},
        ],
        start_date="2026-09-01",
    )


def test_forecast_query_count_is_constant_regardless_of_window(forecast_db):
    _seed_realistic_scenario(forecast_db)

    _, count_30 = _count_queries(
        lambda: forecast(COMPANY, date(2026, 9, 1), date(2026, 9, 30), as_of=AS_OF)
    )
    _, count_360 = _count_queries(
        lambda: forecast(COMPANY, date(2026, 9, 1), date(2027, 8, 27), as_of=AS_OF)
    )

    # Before this task: ~3 statements per day (100 for a 30-day window, 1090
    # for 360 days — see docs/validation/2026-09-12-baseline.md). After: a
    # small FIXED number of statements regardless of window length.
    assert count_30 == count_360
    assert count_30 < 20


def test_forecast_query_count_stays_constant_for_non_base_scenario(forecast_db):
    # Self-review: the scenario layer used to call _average_daily_realized
    # (one query) PER FUTURE DAY whenever scenario != 'base' — its own,
    # separate source of O(days) queries, easy to miss when only exercising
    # scenario='base'. Confirm it was fixed too.
    _seed_realistic_scenario(forecast_db)

    _, count_30 = _count_queries(
        lambda: forecast(COMPANY, date(2026, 9, 1), date(2026, 9, 30), "pessimistic", as_of=AS_OF)
    )
    _, count_360 = _count_queries(
        lambda: forecast(COMPANY, date(2026, 9, 1), date(2027, 8, 27), "pessimistic", as_of=AS_OF)
    )

    assert count_30 == count_360
    assert count_30 < 25


def test_forecast_optimization_is_equivalent_to_reference(forecast_db):
    _seed_realistic_scenario(forecast_db)

    optimized = forecast(COMPANY, date(2026, 9, 1), date(2026, 10, 31), "base", as_of=AS_OF)
    reference = _reference_forecast(COMPANY, date(2026, 9, 1), date(2026, 10, 31), "base", as_of=AS_OF)

    assert _normalize(optimized) == _normalize(reference)


def test_forecast_optimization_is_equivalent_to_reference_non_base_scenario(forecast_db):
    _seed_realistic_scenario(forecast_db)

    optimized = forecast(COMPANY, date(2026, 9, 1), date(2026, 10, 31), "optimistic", as_of=AS_OF)
    reference = _reference_forecast(COMPANY, date(2026, 9, 1), date(2026, 10, 31), "optimistic", as_of=AS_OF)

    assert _normalize(optimized) == _normalize(reference)
