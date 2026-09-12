from __future__ import annotations

from typing import Optional

from .catalog import by_id, inventory_catalog


def simulate_price(
    company: int,
    period: str,
    product_id,
    new_price_cents: int,
    expected_quantity: float,
    *,
    cost_cents: Optional[int] = None,
) -> dict:
    """Purely computed from the current catalog snapshot — never writes
    anything back to Mobne or the local database, so a simulation can never
    accidentally change what's actually charged at the register."""
    catalog = inventory_catalog(company, period)
    product = by_id(catalog, product_id)
    if product is None:
        raise ValueError("Produto não encontrado no catálogo desta empresa.")
    effective_cost = cost_cents if cost_cents is not None else (product["current_cost"] or 0)
    simulated_revenue = round(new_price_cents * expected_quantity)
    simulated_cost = round(effective_cost * expected_quantity)
    simulated_profit = simulated_revenue - simulated_cost
    simulated_margin = round(simulated_profit / simulated_revenue * 100, 2) if simulated_revenue else None
    return {
        "product_id": product["id"],
        "name": product["name"],
        "current_price_cents": product["current_price"],
        "current_cost_cents": product["current_cost"],
        "simulated_price_cents": new_price_cents,
        "expected_quantity": expected_quantity,
        "simulated_revenue_cents": simulated_revenue,
        "simulated_cost_cents": simulated_cost,
        "simulated_profit_cents": simulated_profit,
        "simulated_margin_pct": simulated_margin,
    }
