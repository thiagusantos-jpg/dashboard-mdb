from __future__ import annotations

import secrets
from datetime import date

from .. import database as db, models
from .catalog import _base_catalog


def _new_id() -> int:
    return secrets.randbits(63) or 1


def snapshot_catalogs(company: int, observed_at: date, *, store: int = 0) -> int:
    """Persists one stock/price/cost row per product for this date — Task 16's
    fix for depending on only the immediately previous dataset. Safe to call
    once per sync: the unique key makes a same-day re-run a no-op update."""
    with db.connection() as conn:
        product_list, stock_map, price_map = _base_catalog(company, conn)
        timestamp = db.now()
        count = 0
        for product in product_list:
            pid = product["id"]
            stock_row = stock_map.get(pid)
            price_row = price_map.get(pid)
            cost_cents = (
                models.cents(stock_row["unit_cost"])
                if stock_row and stock_row["unit_cost"] is not None else None
            )
            conn.execute(
                """
                INSERT INTO operation_snapshots(
                    id,company,store,product_id,observed_at,stock_quantity,price_cents,cost_cents,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(company,store,product_id,observed_at) DO UPDATE SET
                    stock_quantity=excluded.stock_quantity,price_cents=excluded.price_cents,
                    cost_cents=excluded.cost_cents
                """,
                (
                    _new_id(), company, store, pid, observed_at.isoformat(),
                    stock_row["quantity"] if stock_row else None,
                    price_row["price"] if price_row else None,
                    cost_cents, timestamp,
                ),
            )
            count += 1
    return count


def product_history(company: int, product_id, *, store: int = 0, limit: int = 90) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM operation_snapshots
            WHERE company=? AND store=? AND product_id=?
            ORDER BY observed_at DESC LIMIT ?
            """,
            (company, store, product_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
