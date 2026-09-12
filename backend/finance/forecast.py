from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .. import database as db


SCENARIOS = {"base": 1.0, "pessimistic": 0.85, "optimistic": 1.15}


def _date_range(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _balance_before(company: int, day: date) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS balance FROM cash_events WHERE company=? AND occurred_at<?",
            (company, day.isoformat()),
        ).fetchone()
    return int(row["balance"])


def _realized_events_for_day(company: int, day: date) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM cash_events WHERE company=? AND occurred_at=?",
            (company, day.isoformat()),
        ).fetchall()
    return [
        {
            "amount_cents": row["amount_cents"],
            "description": row["description"],
            "source": "ledger",
            "confidence": "realized",
        }
        for row in rows
    ]


def _open_entries_due(company: int, day: date, *, due_on_or_before: bool = False) -> list:
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
        # Net out payments already recorded against the entry (settlements minus
        # any reversals) so a partially-paid entry still shows its remaining,
        # unpaid balance instead of disappearing from the forecast entirely.
        remaining = max(0, row["amount_cents"] - row["paid_cents"])
        if remaining <= 0:
            continue
        # Revenue-nature entries (e.g. confirmed receivables) are inflows; every
        # other open entry (expenses, taxes, distributions) is money going out.
        signed = remaining if row["nature"] in ("revenue", "financing_inflow") else -remaining
        items.append({
            "amount_cents": signed,
            "description": row["description"],
            "source": "financial_entries",
            "confidence": "forecast",
        })
    return items


def _open_installments_due(company: int, day: date, *, due_on_or_before: bool = False) -> list:
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
        {
            "amount_cents": -row["total_cents"],
            "description": f"Parcela {row['number']} — {row['lender']}",
            "source": "loan_installments",
            "confidence": "forecast",
        }
        for row in rows
    ]


def _average_daily_realized(company: int, as_of: date, window_days: int = 30) -> float:
    window_start = as_of - timedelta(days=window_days)
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS total FROM cash_events WHERE company=? AND occurred_at>=? AND occurred_at<?",
            (company, window_start.isoformat(), as_of.isoformat()),
        ).fetchone()
    return int(row["total"]) / window_days


def _scenario_layer(company: int, day: date, scenario: str, as_of: date) -> list:
    multiplier = SCENARIOS[scenario]
    if multiplier == 1.0:
        return []
    baseline = _average_daily_realized(company, as_of)
    if baseline == 0:
        return []
    adjustment = round(baseline * (multiplier - 1.0))
    if adjustment == 0:
        return []
    return [{
        "amount_cents": adjustment,
        "description": f"Ajuste de cenário ({scenario})",
        "source": "scenario",
        "confidence": "simulated",
    }]


def forecast(company: int, start: date, end: date, scenario: str = "base", *, as_of: Optional[date] = None) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError("Cenário inválido.")
    if end < start:
        raise ValueError("O período final deve ser posterior ao inicial.")
    today = as_of or date.today()
    balance = _balance_before(company, start)
    days = []
    for day in _date_range(start, end):
        if day < today:
            items = _realized_events_for_day(company, day)
        elif day == today:
            # Anything still open with a due date up to and including today is a
            # real, unpaid obligation the moment the forecast is generated — carry
            # it forward onto "today" instead of silently dropping it once its
            # original due date has passed the realized/forecast boundary.
            items = _realized_events_for_day(company, day)
            items += _open_entries_due(company, day, due_on_or_before=True)
            items += _open_installments_due(company, day, due_on_or_before=True)
        else:
            items = _open_entries_due(company, day) + _open_installments_due(company, day)
            items += _scenario_layer(company, day, scenario, today)
        balance += sum(item["amount_cents"] for item in items)
        days.append({"date": day.isoformat(), "items": items, "balance_cents": balance})

    lowest = min(days, key=lambda d: d["balance_cents"]) if days else None
    alerts = []
    if lowest is not None and lowest["balance_cents"] < 0:
        alerts.append({
            "type": "negative_cash",
            "date": lowest["date"],
            "balance_cents": lowest["balance_cents"],
        })
    return {
        "company": company,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "scenario": scenario,
        "starting_balance_cents": _balance_before(company, start),
        "days": days,
        "lowest": {"date": lowest["date"], "balance_cents": lowest["balance_cents"]} if lowest else None,
        "alerts": alerts,
    }
