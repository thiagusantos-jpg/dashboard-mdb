from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from .. import database as db, permissions
from ..operations.catalog import by_id, inventory_catalog
from ..operations.replenishment import recommend
from ..operations.snapshots import product_history


router = APIRouter(prefix="/api/companies/{company}/products", tags=["operations"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


@router.get(
    "/{product_id}",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_product_detail(company: int, product_id: int, period: str):
    _require_company(company)
    catalog = inventory_catalog(company, period)
    product = by_id(catalog, product_id)
    if not product:
        raise HTTPException(404, "Produto não encontrado no catálogo desta empresa.")
    as_of = date.fromisoformat(period + "-01")
    recommendations = [r for r in recommend(company, None, as_of) if r["product_id"] == product_id]
    return {
        "product": product,
        "replenishment": recommendations[0] if recommendations else None,
        "history": product_history(company, product_id),
    }
