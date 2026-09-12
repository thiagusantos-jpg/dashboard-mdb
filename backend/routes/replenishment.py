from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import database as db, permissions
from ..operations.replenishment import recommend


router = APIRouter(prefix="/api/companies/{company}", tags=["operations"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


@router.get(
    "/replenishment",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_replenishment(company: int, store: Optional[int] = None, as_of: Optional[date] = None):
    _require_company(company)
    return recommend(company, store, as_of or date.today())
