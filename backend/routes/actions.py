from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import actions, database as db, permissions, security
from ..operations import action_results
from ..operations.baskets import basket_pairs
from ..operations.promotions import promotion_result


router = APIRouter(prefix="/api/companies/{company}", tags=["operations"])


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


def _require_action(company: int, action_id: int) -> None:
    with db.connection() as conn:
        row = conn.execute("SELECT company FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Ação não encontrada.")


def _require_assignable(company: int, assignee: Optional[int]) -> None:
    if assignee is not None and not actions.is_assignable(company, assignee):
        raise HTTPException(422, "Responsável não tem acesso a esta empresa.")


class ActionCreate(BaseModel):
    alert_key: str = Field(min_length=1, max_length=200)
    alert_version: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=240)
    priority: str = Field(default="medium", pattern="^(low|medium|high)$")
    assignee: Optional[int] = None
    due_date: Optional[date] = None
    evidence: str = Field(default="", max_length=2000)
    baseline_count: Optional[int] = Field(default=None, ge=0)


class ActionTransition(BaseModel):
    to_status: str = Field(pattern="^(open|in_progress|resolved|dismissed)$")
    note: str = Field(default="", max_length=2000)


class ActionUpdate(BaseModel):
    # Only the keys actually sent change (model_fields_set); an explicit null clears.
    assignee: Optional[int] = None
    due_date: Optional[date] = None
    priority: Optional[str] = Field(default=None, pattern="^(low|medium|high)$")


@router.get(
    "/actions",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def list_company_actions(company: int, status: Optional[str] = None):
    _require_company(company)
    return actions.list_actions(company, status=status)


@router.get(
    "/actions/assignees",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def list_action_assignees(company: int):
    _require_company(company)
    return actions.assignees(company)


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
    _require_assignable(company, body.assignee)
    return actions.create_from_alert(
        company, body.alert_key, body.alert_version, body.title,
        priority=body.priority, assignee=body.assignee,
        due_date=body.due_date.isoformat() if body.due_date else None,
        evidence=body.evidence, baseline_count=body.baseline_count, created_by=auth.user_id,
    )


@router.post(
    "/actions/closing-reminder",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def ensure_company_closing_reminder(
    company: int,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_company(company)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    return actions.ensure_closing_reminder(company, today, created_by=auth.user_id)


@router.post(
    "/actions/due-reminders",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def ensure_company_due_reminders(
    company: int,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_company(company)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    return actions.ensure_due_reminders(company, today, created_by=auth.user_id)


@router.patch(
    "/actions/{action_id}",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def update_company_action(
    company: int, action_id: int, body: ActionUpdate,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_action(company, action_id)
    changes = {}
    for field in body.model_fields_set:
        value = getattr(body, field)
        changes[field] = value.isoformat() if isinstance(value, date) else value
    if "priority" in changes and changes["priority"] is None:
        raise HTTPException(422, "A prioridade não pode ficar vazia.")
    _require_assignable(company, changes.get("assignee"))
    try:
        return actions.update_action(action_id, changes, created_by=auth.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/actions/{action_id}/transition",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def transition_company_action(
    company: int, action_id: int, body: ActionTransition,
    auth: security.AuthContext = Depends(permissions.require_permission("dashboard.read")),
):
    _require_action(company, action_id)
    try:
        return actions.transition_action(action_id, body.to_status, note=body.note, created_by=auth.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/actions/{action_id}/events",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_action_events(company: int, action_id: int):
    _require_action(company, action_id)
    return actions.list_events(action_id)


@router.get(
    "/actions/{action_id}/result",
    dependencies=[Depends(permissions.require_permission("dashboard.read"))],
)
def get_action_result(company: int, action_id: int):
    _require_action(company, action_id)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    with db.connection() as conn:
        action = dict(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())
        return action_results.price_action_result(company, action, today, conn)


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
