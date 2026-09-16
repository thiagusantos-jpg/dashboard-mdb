from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance import reconciliation
from .obligations import _error_detail


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class ConfirmBody(BaseModel):
    entry_ids: list[int] = Field(default_factory=list)
    payment_ids: list[int] = Field(default_factory=list)
    accept_partial: bool = False


class UndoBody(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class SuggestBody(BaseModel):
    cash_event_id: int
    tolerance_cents: int = Field(default=0, ge=0)
    window_days: int = Field(default=5, ge=1, le=60)


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


@router.get(
    "/reconciliation",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def list_reconciliation_groups(company: int, status: Optional[str] = None):
    _require_company(company)
    return reconciliation.list_groups(company, status=status)


@router.get(
    "/reconciliation/sources",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def list_data_sources(company: int):
    _require_company(company)
    return reconciliation.data_sources(company)


@router.get(
    "/reconciliation/stone-daily",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_stone_daily_check(
    company: int,
    start: Optional[date] = None,
    end: Optional[date] = None,
    cash_account_id: Optional[int] = None,
    tolerance_cents: int = reconciliation.STONE_DEFAULT_TOLERANCE_CENTS,
):
    """Stone's expected deposit per day vs. the credit that reached the bank.
    Defaults to the last 30 days plus the coming week."""
    _require_company(company)
    today = date.today()
    start = start or today - timedelta(days=30)
    end = end or today + timedelta(days=7)
    if end < start:
        raise HTTPException(422, "A data final deve ser depois da inicial.")
    if (end - start).days > 400:
        raise HTTPException(422, "Escolha um período de até 400 dias.")
    if tolerance_cents < 0:
        raise HTTPException(422, "A tolerância não pode ser negativa.")
    return reconciliation.stone_daily_check(
        company, start, end, today=today,
        cash_account_id=cash_account_id, tolerance_cents=tolerance_cents,
    )


@router.post(
    "/reconciliation/suggest",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def suggest_group(company: int, body: SuggestBody):
    _require_company(company)
    try:
        return reconciliation.suggest(
            company, body.cash_event_id,
            tolerance_cents=body.tolerance_cents, window_days=body.window_days,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/reconciliation/{group_id}/confirm",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def confirm_group(
    company: int,
    group_id: int,
    body: ConfirmBody,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    with db.connection() as conn:
        row = conn.execute(
            "SELECT company FROM reconciliation_groups WHERE id=?", (group_id,)
        ).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Grupo de conciliação não encontrado.")
    try:
        return reconciliation.confirm(
            group_id, body.entry_ids, payment_ids=body.payment_ids,
            accept_partial=body.accept_partial, created_by=auth.user_id,
        )
    except ValueError as exc:
        message = str(exc)
        if message == "Pagamento não encontrado.":
            # A payment_id outside this company's/scope's reach is a missing
            # resource, not an invalid-fields error — same contract as
            # obligations.py's own 404 for "Obrigação não encontrada.".
            raise HTTPException(
                404, _error_detail("not_found", message, ["payment_ids"])
            ) from exc
        raise HTTPException(422, _error_detail("invalid_fields", message, [])) from exc


@router.post(
    "/reconciliation/{group_id}/undo",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def undo_group(company: int, group_id: int, body: UndoBody):
    with db.connection() as conn:
        row = conn.execute(
            "SELECT company FROM reconciliation_groups WHERE id=?", (group_id,)
        ).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Grupo de conciliação não encontrado.")
    try:
        return reconciliation.undo(group_id, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
