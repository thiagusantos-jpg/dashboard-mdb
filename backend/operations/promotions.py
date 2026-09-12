from __future__ import annotations

from datetime import date

from .. import models
from .goals import _receipts_in_range


def _product_stats(company: int, product_id, start: date, end: date) -> dict:
    receipts = _receipts_in_range(company, start, end)
    revenue = 0
    quantity = 0.0
    for receipt in receipts:
        for item in receipt["items"]:
            if item["product_id"] == product_id and item.get("status") == "V":
                revenue += item["revenue"]
                quantity += float(models.number(item["quantity"]))
    days = (end - start).days + 1
    return {
        "start": start.isoformat(), "end": end.isoformat(), "days": days,
        "revenue_cents": revenue, "quantity": quantity,
        "revenue_per_day_cents": round(revenue / days) if days else None,
    }


def promotion_result(
    company: int, product_id, promo_start: date, promo_end: date, baseline_start: date, baseline_end: date,
) -> dict:
    """Compares a promotion window against a baseline window — reported as an
    observed variation, never as proof the promotion caused the change (other
    things move revenue at the same time: season, competitors, stock-outs)."""
    promo = _product_stats(company, product_id, promo_start, promo_end)
    baseline = _product_stats(company, product_id, baseline_start, baseline_end)
    variation_pct = (
        round((promo["revenue_per_day_cents"] / baseline["revenue_per_day_cents"] - 1) * 100, 2)
        if promo["revenue_per_day_cents"] and baseline["revenue_per_day_cents"]
        else None
    )
    return {
        "product_id": product_id,
        "promotion": promo,
        "baseline": baseline,
        "observed_variation_pct": variation_pct,
        "note": "Variação observada; não representa uma relação causal comprovada.",
    }
