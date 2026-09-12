from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import actions, database as db, permissions, security
from ..operations.baskets import basket_pairs
from ..operations.promotions import promotion_result


router = APIRouter(prefix="/api/companies/{company}", tags=["operations"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


class ActionCreate(BaseModel):
    alert_key: str = Field(min_length=1, max_length=200)
    alert_version: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=240)
    priority: str = Field(default="medium", pattern="^(low|medium|high)$")
    assignee: Optional[int] = None
    due_date: Optional[str] = None
    evidence: str = Field(default="", max_length=2000)


class ActionTransition(BaseModel):
    to_status: str = Field(pattern="^(open|in_progress|resolved|dismissed)$")
    note: str = Field(default="", max_length=2000)


@router.get(
    "/actions",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def list_company_actions(company: int, status: Optional[str] = None):
    _require_company(company)
    return actions.list_actions(company, status=status)


@router.post(
    "/actions",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def create_company_action(
    company: int, body: ActionCreate,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_company(company)
    return actions.create_from_alert(
        company, body.alert_key, body.alert_version, body.title,
        priority=body.priority, assignee=body.assignee, due_date=body.due_date,
        evidence=body.evidence, created_by=auth.user_id,
    )


@router.post(
    "/actions/{action_id}/transition",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def transition_company_action(
    company: int, action_id: int, body: ActionTransition,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    with db.connection() as conn:
        row = conn.execute("SELECT company FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Ação não encontrada.")
    try:
        return actions.transition_action(action_id, body.to_status, note=body.note, created_by=auth.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/actions/{action_id}/events",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_action_events(company: int, action_id: int):
    with db.connection() as conn:
        row = conn.execute("SELECT company FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Ação não encontrada.")
    return actions.list_events(action_id)


@router.get(
    "/promotions/result",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_promotion_result(
    company: int, product_id: int, promo_start: date, promo_end: date, baseline_start: date, baseline_end: date,
):
    _require_company(company)
    return promotion_result(company, product_id, promo_start, promo_end, baseline_start, baseline_end)


@router.get(
    "/baskets",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_basket_pairs(company: int, period: str):
    _require_company(company)
    dataset = db.dataset(company, "sales", period)
    receipts = dataset["payload"]["receipts"] if dataset else []
    return basket_pairs(receipts)
