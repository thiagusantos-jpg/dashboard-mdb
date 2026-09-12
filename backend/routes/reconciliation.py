from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance import reconciliation


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class ConfirmBody(BaseModel):
    entry_ids: list[int] = Field(default_factory=list)
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
            group_id, body.entry_ids, accept_partial=body.accept_partial, created_by=auth.user_id
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


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
