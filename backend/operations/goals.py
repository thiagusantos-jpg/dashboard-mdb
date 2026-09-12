from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from .. import database as db, models, organization
from ..finance import accounts
from ..sync import month_range


def goal_progress(target_cents: int, achieved_cents: int, remaining_days: int) -> dict:
    """No hidden math: the required daily pace is just what's left divided by
    the days left to make it — visible so a manager can judge if it's realistic."""
    remaining_cents = max(target_cents - achieved_cents, 0)
    required_per_day = round(remaining_cents / remaining_days) if remaining_days > 0 else None
    return {
        "target_cents": target_cents,
        "achieved_cents": achieved_cents,
        "remaining_cents": remaining_cents,
        "remaining_days": remaining_days,
        "required_per_day": required_per_day,
        "progress_pct": round(achieved_cents / target_cents * 100, 2) if target_cents else None,
    }


def set_revenue_goal(company: int, target_cents: int, effective_from: str, *, store: Optional[int] = None) -> dict:
    return accounts.set_parameter(company, "goal:revenue", target_cents, effective_from, store=store)


def revenue_goal(company: int, on_date: str, *, store: Optional[int] = None) -> Optional[int]:
    return accounts.resolve_parameter(company, "goal:revenue", on_date, store=store)


def current_goal_progress(company: int, as_of: date, *, store: Optional[int] = None) -> Optional[dict]:
    target = revenue_goal(company, as_of.isoformat(), store=store)
    if target is None:
        return None
    month_start = as_of.replace(day=1)
    receipts = _receipts_in_range(company, month_start, as_of)
    achieved = models.summarize(receipts)["totals"]["revenue"]
    month_end_day = _month_end(as_of)
    tomorrow = as_of + timedelta(days=1)
    remaining_days = (
        organization.operating_days_for_company(company, tomorrow.isoformat(), month_end_day.isoformat(), store=store)
        if tomorrow <= month_end_day else 0
    )
    return goal_progress(target, achieved, remaining_days)


def _month_end(any_day: date) -> date:
    if any_day.month == 12:
        return date(any_day.year, 12, 31)
    next_month = date(any_day.year, any_day.month + 1, 1)
    return next_month - timedelta(days=1)


def _receipts_in_range(company: int, start: date, end: date) -> list:
    receipts = []
    for period in month_range(f"{start.year:04}-{start.month:02}", f"{end.year:04}-{end.month:02}"):
        dataset = db.dataset(company, "sales", period)
        if not dataset or not dataset["payload"].get("raw_count"):
            continue
        for receipt in dataset["payload"]["receipts"]:
            receipt_date = date.fromisoformat(receipt["date"])
            if start <= receipt_date <= end:
                receipts.append(receipt)
    return receipts


def compare_intervals(company: int, start1: date, end1: date, start2: date, end2: date) -> dict:
    """Compares two arbitrary date ranges (week, fortnight, quarter, custom) by
    revenue-per-operating-day — a 6-day week and a 4-day week aren't directly
    comparable any other way."""

    def interval_stats(start: date, end: date) -> dict:
        totals = models.summarize(_receipts_in_range(company, start, end))["totals"]
        days = organization.operating_days_for_company(company, start.isoformat(), end.isoformat())
        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "operating_days": days,
            "revenue_cents": totals["revenue"],
            "profit_cents": totals["profit"],
            "revenue_per_day_cents": round(totals["revenue"] / days) if days else None,
        }

    a = interval_stats(start1, end1)
    b = interval_stats(start2, end2)
    change_pct = (
        round((a["revenue_per_day_cents"] / b["revenue_per_day_cents"] - 1) * 100, 2)
        if a["revenue_per_day_cents"] and b["revenue_per_day_cents"]
        else None
    )
    return {"interval_1": a, "interval_2": b, "revenue_per_day_change_pct": change_pct}
