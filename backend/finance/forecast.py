from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from .. import database as db


SCENARIOS = {"base": 1.0, "pessimistic": 0.85, "optimistic": 1.15}


def _date_range(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _balance_before_on_connection(conn, company: int, day: date) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS balance FROM cash_events WHERE company=? AND occurred_at<?",
        (company, day.isoformat()),
    ).fetchone()
    return int(row["balance"])


# --- Task B7 / A5 baseline optimization -------------------------------------
#
# The pre-B7 implementation called one-plus query PER DAY of the forecast
# window (_realized_events_for_day / _open_entries_due / _open_installments_due,
# each opening its own connection) — confirmed O(days) in
# docs/validation/2026-09-12-baseline.md: 100/1,090/2,170 SQL statements and
# 22.8ms/245ms/495ms median latency for 30/360/720-day windows. The three
# functions below replace that day-by-day loop with exactly three grouped
# queries — one for every realized cash movement in the relevant part of the
# window, one for every still-open entry due on/before the window's end
# (grouped by due_date in Python), one for every still-open loan installment
# the same way — turning O(days) queries into O(1), independent of window
# size. `forecast()` runs all three (plus, when scenario!='base', a fourth
# for the scenario baseline) inside a SINGLE `db.connection()`/transaction —
# "consistência de um snapshot por cálculo": nothing else can write between
# them and produce a forecast whose days are assembled from different points
# in time. No caching is introduced anywhere here, per the brief's explicit
# "não implementar cache sem chave de revisão que inclua pagamentos/catálogos"
# — this only removes redundant per-day querying.


def _realized_events_by_day_on_connection(conn, company: int, start: date, end: date) -> dict:
    """Every realized cash movement with `start<=occurred_at<=end`, grouped by
    occurred_at. Replaces the old `_realized_events_for_day` call made once
    per day in [start, min(end,today)]."""
    rows = conn.execute(
        """
        SELECT occurred_at,amount_cents,description FROM cash_events
        WHERE company=? AND occurred_at>=? AND occurred_at<=?
        """,
        (company, start.isoformat(), end.isoformat()),
    ).fetchall()
    by_day: dict = defaultdict(list)
    for row in rows:
        by_day[row["occurred_at"]].append({
            "amount_cents": row["amount_cents"],
            "description": row["description"],
            "source": "ledger",
            "confidence": "realized",
        })
    return by_day


def _open_entries_by_due_date_on_connection(conn, company: int, end: date) -> dict:
    """Every still-open financial_entries row due on or before `end`,
    grouped by due_date. Replaces the old `_open_entries_due` call made once
    per day (with `due_on_or_before=True` for "today" and `=` for every
    future day) — callers reproduce that exact split in Python: bucket rows
    with due_date<=today onto "today", everything else onto its own
    due_date."""
    rows = conn.execute(
        """
        SELECT e.id,e.due_date,e.amount_cents,e.description,a.nature,
               COALESCE(SUM(
                   CASE WHEN ev.event_type='settled' THEN ev.amount_cents
                        WHEN ev.event_type='reversed' THEN -ev.amount_cents ELSE 0 END
               ),0) AS paid_cents
        FROM financial_entries e
        JOIN finance_accounts a ON a.id=e.account_id
        LEFT JOIN financial_events ev ON ev.entry_id=e.id
        WHERE e.company=? AND e.due_date<=?
          AND e.status IN ('open','overdue','partially_paid')
        GROUP BY e.id
        """,
        (company, end.isoformat()),
    ).fetchall()
    by_due_date: dict = defaultdict(list)
    for row in rows:
        # Net out payments already recorded against the entry (settlements
        # minus any reversals) so a partially-paid entry still shows its
        # remaining, unpaid balance instead of disappearing entirely.
        remaining = max(0, row["amount_cents"] - row["paid_cents"])
        if remaining <= 0:
            continue
        # Revenue-nature entries (e.g. confirmed receivables) are inflows;
        # every other open entry (expenses, taxes, distributions) is money
        # going out.
        signed = remaining if row["nature"] in ("revenue", "financing_inflow") else -remaining
        by_due_date[row["due_date"]].append({
            "amount_cents": signed,
            "description": row["description"],
            "source": "financial_entries",
            "confidence": "forecast",
        })
    return by_due_date


def _open_installments_by_due_date_on_connection(conn, company: int, end: date) -> dict:
    """Every open installment on the loan's ACTIVE schedule due on or before
    `end`, grouped by due_date. Replaces the old `_open_installments_due`
    call made once per day, same split rationale as entries above. A
    renegotiated-away closed schedule (s.status != 'active') or a
    cancelled-loan schedule (task B7's cancel_loan: same status column, a
    non-'active' value) is excluded exactly as before — this query's WHERE
    clause is unchanged from the old per-day version other than the due_date
    comparator."""
    rows = conn.execute(
        """
        SELECT i.due_date,i.total_cents,i.number,l.lender FROM loan_installments i
        JOIN loans l ON l.id=i.loan_id
        JOIN loan_schedules s ON s.id=i.schedule_id
        WHERE l.company=? AND i.due_date<=?
          AND i.schedule_id=l.active_schedule_id
          AND s.status='active' AND i.status='open'
        """,
        (company, end.isoformat()),
    ).fetchall()
    by_due_date: dict = defaultdict(list)
    for row in rows:
        by_due_date[row["due_date"]].append({
            "amount_cents": -row["total_cents"],
            "description": f"Parcela {row['number']} — {row['lender']}",
            "source": "loan_installments",
            "confidence": "forecast",
        })
    return by_due_date


def _average_daily_realized_on_connection(conn, company: int, as_of: date, window_days: int = 30) -> float:
    window_start = as_of - timedelta(days=window_days)
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS total FROM cash_events WHERE company=? AND occurred_at>=? AND occurred_at<?",
        (company, window_start.isoformat(), as_of.isoformat()),
    ).fetchone()
    return int(row["total"]) / window_days


def _scenario_adjustment_cents(scenario: str, baseline: float) -> int:
    """The scenario layer's adjustment amount does not depend on which day
    it's applied to (see the old `_scenario_layer`, whose per-day dict never
    varied by `day` either) — computed ONCE per forecast() call instead of
    once per future day, which used to mean a second source of O(days)
    queries (via `_average_daily_realized`) whenever scenario != 'base'."""
    multiplier = SCENARIOS[scenario]
    if multiplier == 1.0 or baseline == 0:
        return 0
    return round(baseline * (multiplier - 1.0))


def forecast(company: int, start: date, end: date, scenario: str = "base", *, as_of: Optional[date] = None) -> dict:
    if scenario not in SCENARIOS:
        raise ValueError("Cenário inválido.")
    if end < start:
        raise ValueError("O período final deve ser posterior ao inicial.")
    today = as_of or date.today()

    with db.connection() as conn:
        balance = _balance_before_on_connection(conn, company, start)
        # Reused for the response's starting_balance_cents below instead of
        # querying it again with the same arguments (A5 baseline doc flagged
        # this exact redundant second call).
        starting_balance_cents = balance

        realized_effective_end = min(end, today)
        realized_by_day = (
            _realized_events_by_day_on_connection(conn, company, start, realized_effective_end)
            if start <= realized_effective_end
            else {}
        )
        open_entries_by_due = _open_entries_by_due_date_on_connection(conn, company, end)
        open_installments_by_due = _open_installments_by_due_date_on_connection(conn, company, end)

        scenario_adjustment = 0
        if scenario != "base":
            baseline_avg = _average_daily_realized_on_connection(conn, company, today)
            scenario_adjustment = _scenario_adjustment_cents(scenario, baseline_avg)

    today_iso = today.isoformat()

    days = []
    for day in _date_range(start, end):
        day_iso = day.isoformat()
        if day < today:
            items = list(realized_by_day.get(day_iso, []))
        elif day == today:
            # Anything still open with a due date up to and including today
            # is a real, unpaid obligation the moment the forecast is
            # generated — carry it forward onto "today" instead of silently
            # dropping it once its original due date has passed the
            # realized/forecast boundary (matches the old due_on_or_before=True
            # behavior exactly: ANY due_date<=today, not just due_date==today).
            items = list(realized_by_day.get(day_iso, []))
            for due_date, rows in open_entries_by_due.items():
                if due_date <= today_iso:
                    items.extend(rows)
            for due_date, rows in open_installments_by_due.items():
                if due_date <= today_iso:
                    items.extend(rows)
        else:
            items = list(open_entries_by_due.get(day_iso, []))
            items += list(open_installments_by_due.get(day_iso, []))
            if scenario_adjustment:
                items.append({
                    "amount_cents": scenario_adjustment,
                    "description": f"Ajuste de cenário ({scenario})",
                    "source": "scenario",
                    "confidence": "simulated",
                })
        balance += sum(item["amount_cents"] for item in items)
        days.append({"date": day_iso, "items": items, "balance_cents": balance})

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
        "starting_balance_cents": starting_balance_cents,
        "days": days,
        "lowest": {"date": lowest["date"], "balance_cents": lowest["balance_cents"]} if lowest else None,
        "alerts": alerts,
    }
