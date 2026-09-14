from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions
from ..finance import accounts
from ..operations.goals import (
    compare_intervals,
    current_goal_progress,
    set_revenue_goal,
)
from ..operations.pricing import simulate_price


router = APIRouter(prefix="/api/companies/{company}", tags=["operations"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


class GoalSet(BaseModel):
    key: str = Field(pattern="^(revenue|margin)$")
    value: float
    effective_from: str


class PriceSimulation(BaseModel):
    period: str = Field(pattern=r"^\d{4}-\d{2}$")
    product_id: int
    new_price_cents: int = Field(gt=0)
    expected_quantity: float = Field(gt=0)
    cost_cents: Optional[int] = None


@router.get(
    "/goals/progress",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_goal_progress(company: int, as_of: Optional[date] = None):
    _require_company(company)
    return current_goal_progress(company, as_of or date.today())


@router.put(
    "/goals",
    dependencies=[Depends(permissions.require_permission("settings.manage"))],
)
def put_goal(company: int, body: GoalSet):
    _require_company(company)
    if body.key == "revenue":
        return set_revenue_goal(company, round(body.value), body.effective_from)
    return accounts.set_parameter(company, "goal:margin", body.value, body.effective_from)


GOAL_KEYS = {"revenue": "goal:revenue", "margin": "goal:margin"}


@router.get(
    "/goals",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def list_goals(company: int):
    """Every saved goal, so a wrong one can be found, corrected (same date) or removed."""
    _require_company(company)
    return {name: accounts.list_parameters(company, key) for name, key in GOAL_KEYS.items()}


@router.delete(
    "/goals/{key}/{effective_from}",
    dependencies=[Depends(permissions.require_permission("settings.manage"))],
)
def delete_goal(company: int, key: str, effective_from: str):
    _require_company(company)
    if key not in GOAL_KEYS:
        raise HTTPException(404, "Meta não encontrada.")
    try:
        date.fromisoformat(effective_from)
    except ValueError:
        raise HTTPException(422, "Data inválida: use AAAA-MM-DD.")
    if not accounts.delete_parameter(company, GOAL_KEYS[key], effective_from):
        raise HTTPException(404, "Meta não encontrada.")
    return {"deleted": True}


@router.get(
    "/goals/compare",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_compare_intervals(
    company: int, start1: date, end1: date, start2: date, end2: date,
):
    _require_company(company)
    try:
        return compare_intervals(company, start1, end1, start2, end2)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/pricing/simulate",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def post_simulate_price(company: int, body: PriceSimulation):
    _require_company(company)
    try:
        return simulate_price(
            company, body.period, body.product_id, body.new_price_cents,
            body.expected_quantity, cost_cents=body.cost_cents,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
