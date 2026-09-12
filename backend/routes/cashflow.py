from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance import ledger


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class CashAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    kind: str = Field(pattern="^(bank|payment|cash)$")
    store: Optional[int] = None


class CashEventCreate(BaseModel):
    cash_account_id: int
    amount_cents: int
    occurred_at: date
    description: str = Field(min_length=1, max_length=240)


class Transfer(BaseModel):
    from_account_id: int
    to_account_id: int
    amount_cents: int = Field(gt=0)
    occurred_at: date
    description: str = Field(default="Transferência entre contas", max_length=240)


class Reversal(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def _require_company(company: int) -> None:
    with db.connection() as conn:
        if not conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone():
            raise HTTPException(404, "Empresa não encontrada.")


@router.get(
    "/cash-accounts",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def list_cash_accounts(company: int, include_archived: bool = False):
    _require_company(company)
    accounts = ledger.list_accounts(company, include_archived)
    for account in accounts:
        account["balance_cents"] = ledger.account_balance(account["id"])
    return accounts


@router.post(
    "/cash-accounts",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def create_cash_account(company: int, body: CashAccountCreate):
    _require_company(company)
    try:
        return ledger.create_account(company, body.name, body.kind, store=body.store)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/cash-events",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def list_cash_events(company: int, cash_account_id: Optional[int] = None):
    _require_company(company)
    return ledger.list_events(company, cash_account_id=cash_account_id)


@router.post(
    "/cash-events",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def create_cash_event(
    company: int,
    body: CashEventCreate,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    _require_company(company)
    try:
        return ledger.post_cash_event(
            company, body.cash_account_id, body.amount_cents, body.occurred_at,
            body.description, created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/cash-transfers",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def create_transfer(
    company: int,
    body: Transfer,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    _require_company(company)
    try:
        return ledger.transfer(
            company, body.from_account_id, body.to_account_id, body.amount_cents,
            body.occurred_at, description=body.description, created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/cash-balance",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def consolidated_balance(company: int):
    _require_company(company)
    return {"company": company, "balance_cents": ledger.consolidated_balance(company)}


@router.post(
    "/cash-events/{event_id}/reverse",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def reverse_cash_event(
    company: int,
    event_id: int,
    body: Reversal,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    with db.connection() as conn:
        row = conn.execute(
            "SELECT company FROM cash_events WHERE id=?", (event_id,)
        ).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Lançamento não encontrado.")
    try:
        return ledger.reverse_event(event_id, reason=body.reason, created_by=auth.user_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
