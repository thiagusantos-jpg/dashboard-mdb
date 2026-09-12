from __future__ import annotations

import math
from datetime import date
from typing import Optional

from .. import organization
from ..finance import accounts
from .catalog import inventory_catalog


DEFAULT_LEAD_DAYS = 7
DEFAULT_SAFETY_DAYS = 3
DEFAULT_PACK_SIZE = 1


def recommend_one(
    daily_demand: float,
    stock: float,
    reserved: float,
    lead_days: int,
    safety_days: int,
    pack_size: int,
) -> dict:
    """Every input this returns is also an input the caller can see — no black
    box: reorder_point = demand over the days you'd be exposed (lead + safety);
    suggested_quantity rounds the shortfall up to a full package."""
    available = stock - reserved
    reorder_point = daily_demand * (lead_days + safety_days)
    if daily_demand <= 0 or available >= reorder_point:
        suggested = 0.0
    else:
        shortfall = reorder_point - available
        packs = math.ceil(shortfall / pack_size) if pack_size > 0 else shortfall
        suggested = packs * pack_size if pack_size > 0 else shortfall
    return {
        "available": available,
        "reorder_point": reorder_point,
        "suggested_quantity": suggested,
    }


def _operating_days_elapsed(company: int, as_of: date) -> int:
    start = as_of.replace(day=1)
    exceptions = {
        row["date"]: row["status"]
        for row in organization.calendar_exceptions(company)
        if start <= date.fromisoformat(row["date"]) <= as_of
    }
    return organization.operating_days(start.isoformat(), as_of.isoformat(), exceptions=exceptions)


def _resolve_int(company: int, key: str, product_id, as_of: str, default: int) -> int:
    value = accounts.resolve_parameter(company, key, as_of, product=product_id)
    return int(value) if value is not None else default


def recommend(company: int, store: Optional[int], as_of: date) -> list:
    period = f"{as_of.year:04}-{as_of.month:02}"
    catalog = inventory_catalog(company, period)
    open_days = max(_operating_days_elapsed(company, as_of), 1)
    as_of_iso = as_of.isoformat()

    recommendations = []
    for row in catalog:
        if row["stock"] is None:
            continue
        daily_demand = (row["quantity_sold"] or 0) / open_days
        lead_days = _resolve_int(company, "replenishment_lead_days", row["id"], as_of_iso, DEFAULT_LEAD_DAYS)
        safety_days = _resolve_int(company, "replenishment_safety_days", row["id"], as_of_iso, DEFAULT_SAFETY_DAYS)
        pack_size = _resolve_int(company, "replenishment_pack_size", row["id"], as_of_iso, DEFAULT_PACK_SIZE)
        result = recommend_one(daily_demand, row["stock"], row["reserved"] or 0, lead_days, safety_days, pack_size)
        if result["suggested_quantity"] > 0:
            recommendations.append({
                "product_id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "daily_demand": round(daily_demand, 4),
                "lead_days": lead_days,
                "safety_days": safety_days,
                "pack_size": pack_size,
                "data_as_of": as_of_iso,
                "open_days": open_days,
                **result,
            })
    recommendations.sort(key=lambda r: -r["suggested_quantity"])
    return recommendations
