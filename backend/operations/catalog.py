from __future__ import annotations

from typing import Optional

from .. import database as db, models


def _base_catalog(company: int, conn=None):
    products = db.dataset(company, "products", db=conn)
    stock = db.dataset(company, "stock", db=conn)
    prices = db.dataset(company, "prices", db=conn)
    product_list = products["payload"] if products else []
    stock_map = {r["id"]: r for r in (stock["payload"] if stock else [])}
    price_map = {
        r["id"]: r for r in (prices["payload"] if prices else [])
        if r["package"] in ("1.0", "1")
    }
    return product_list, stock_map, price_map


def inventory_catalog(company: int, period: str, conn=None) -> list:
    """Every active product, sold or not — models.summarize() alone only ever
    returns products that appear in a receipt, which is exactly why unsold
    stock silently disappeared from every operational view (R6 gate). Pass an
    existing connection to read inside a caller's transaction snapshot."""
    if conn is not None:
        product_list, stock_map, price_map = _base_catalog(company, conn)
        sales = db.dataset(company, "sales", period, conn)
    else:
        with db.connection() as owned:
            product_list, stock_map, price_map = _base_catalog(company, owned)
            sales = db.dataset(company, "sales", period, owned)

    catalog_by_id = {p["id"]: p for p in product_list}
    sold = {}
    if sales and sales["payload"].get("raw_count"):
        summary = models.summarize(
            sales["payload"]["receipts"], catalog_by_id, sales["payload"]["analysis"]
        )
        sold = {p["id"]: p for p in summary["products"]}

    # Union, not just the catalog: a product can sell before it ever appears in a
    # products sync (lag, a failed catalog fetch, a since-discontinued item still
    # in the historical sales) — dropping it here would just move the same bug
    # (a real product vanishing from every operational view) somewhere else.
    all_ids = list(catalog_by_id.keys()) + [pid for pid in sold if pid not in catalog_by_id]

    rows = []
    for pid in all_ids:
        product = catalog_by_id.get(pid, {})
        sale = sold.get(pid)
        stock_row = stock_map.get(pid)
        price_row = price_map.get(pid)
        rows.append({
            "id": pid,
            "name": product.get("name") or (sale["name"] if sale else f"Produto {pid}"),
            "category": product.get("category") or (sale["category"] if sale else "Sem categoria"),
            "status": product.get("status"),
            "revenue": sale["revenue"] if sale else 0,
            "cost": sale["cost"] if sale else 0,
            "profit": sale["profit"] if sale else 0,
            "unknown": sale["unknown"] if sale else 0,
            "quantity_sold": sale["quantity"] if sale else 0.0,
            "margin": sale["margin"] if sale else None,
            "abc": sale["abc"] if sale else None,
            "classification": sale["classification"] if sale else "Sem vendas",
            "stock": stock_row["quantity"] if stock_row else None,
            "reserved": stock_row["reserved"] if stock_row else None,
            "current_price": price_row["price"] if price_row else None,
            "current_cost": (
                models.cents(stock_row["unit_cost"])
                if stock_row and stock_row["unit_cost"] is not None else None
            ),
            "last_cost": (
                models.cents(stock_row["last_cost"])
                if stock_row and stock_row["last_cost"] is not None else None
            ),
        })
    return rows


def by_id(catalog: list, product_id) -> Optional[dict]:
    return next((row for row in catalog if row["id"] == product_id), None)
