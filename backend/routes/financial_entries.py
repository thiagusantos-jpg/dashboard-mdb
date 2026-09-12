from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance.entries import EntryCommand, create_entry, reverse_entry, settle_entry
from ..finance.recurrence import create_recurrence, generate_occurrences


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class EntryCreate(BaseModel):
    account_id: int
    amount_cents: int = Field(gt=0)
    competence: str = Field(pattern=r"^\d{4}-\d{2}$")
    due_date: date
    description: str = Field(min_length=1, max_length=240)
    store_id: Optional[int] = None
    counterparty_id: Optional[int] = None
    notes: str = Field(default="", max_length=2000)
    forecast: bool = False


class Settlement(BaseModel):
    amount_cents: int = Field(gt=0)
    paid_at: date


class Reversal(BaseModel):
    reversed_at: date
    reason: str = Field(min_length=1, max_length=500)


class RecurrenceCreate(BaseModel):
    account_id: int
    amount_cents: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=240)
    start_competence: str = Field(pattern=r"^\d{4}-\d{2}$")
    end_competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    due_day: int = Field(ge=1, le=31)
    store_id: Optional[int] = None
    counterparty_id: Optional[int] = None


def _require_sensitive_if_needed(
    company: int, account_id: int, auth: security.AuthContext
) -> None:
    with db.connection() as conn:
        account = conn.execute(
            "SELECT sensitive FROM finance_accounts WHERE id=? AND company=?",
            (account_id, company),
        ).fetchone()
    if not account:
        raise HTTPException(422, "Conta financeira não encontrada.")
    if account["sensitive"]:
        permissions.ensure_permission(auth, "finance.sensitive.read", company)


def _require_entry_access(
    company: int, entry_id: int, auth: security.AuthContext
) -> None:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT e.account_id FROM financial_entries e
            WHERE e.id=? AND e.company=?
            """,
            (entry_id, company),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Lançamento não encontrado.")
    _require_sensitive_if_needed(company, row["account_id"], auth)


@router.get("/entries")
def list_entries(
    company: int,
    competence: Optional[str] = None,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.read")
    ),
):
    try:
        permissions.ensure_permission(auth, "finance.sensitive.read", company)
        sensitive_filter = ""
    except HTTPException:
        sensitive_filter = " AND a.sensitive=0"
    period_filter = " AND e.competence=?" if competence else ""
    params = (company, competence) if competence else (company,)
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT e.* FROM financial_entries e
                JOIN finance_accounts a ON a.id=e.account_id
                WHERE e.company=?
                """
                + period_filter
                + sensitive_filter
                + " ORDER BY e.due_date,e.id",
                params,
            )
        ]


@router.post("/entries", status_code=201)
def add_entry(
    company: int,
    body: EntryCreate,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_sensitive_if_needed(company, body.account_id, auth)
    try:
        return create_entry(
            EntryCommand(
                company_id=company,
                account_id=body.account_id,
                amount_cents=body.amount_cents,
                competence=body.competence,
                due_date=body.due_date,
                source="manual",
                external_id=None,
                description=body.description,
                store_id=body.store_id,
                counterparty_id=body.counterparty_id,
                notes=body.notes,
                created_by=auth.user_id,
                forecast=body.forecast,
            )
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/entries/{entry_id}/settlements")
def add_settlement(
    company: int,
    entry_id: int,
    body: Settlement,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_entry_access(company, entry_id, auth)
    try:
        return settle_entry(
            entry_id,
            body.amount_cents,
            paid_at=body.paid_at,
            created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/entries/{entry_id}/reverse")
def reverse(
    company: int,
    entry_id: int,
    body: Reversal,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_entry_access(company, entry_id, auth)
    try:
        return reverse_entry(
            entry_id,
            reversed_at=body.reversed_at,
            reason=body.reason,
            created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/recurrences", status_code=201)
def add_recurrence(
    company: int,
    body: RecurrenceCreate,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_sensitive_if_needed(company, body.account_id, auth)
    try:
        return create_recurrence(
            company=company,
            account_id=body.account_id,
            amount_cents=body.amount_cents,
            description=body.description,
            start_competence=body.start_competence,
            end_competence=body.end_competence,
            due_day=body.due_day,
            store=body.store_id,
            counterparty_id=body.counterparty_id,
            created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/recurrences/{recurrence_id}/generate")
def generate_recurrence(
    company: int,
    recurrence_id: int,
    through_competence: str,
    auth=Depends(permissions.require_permission("finance.write")),
):
    with db.connection() as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM financial_recurrences
            WHERE id=? AND company=?
            """,
            (recurrence_id, company),
        ).fetchone()
    if not exists:
        raise HTTPException(404, "Recorrência não encontrada.")
    try:
        return generate_occurrences(
            recurrence_id, through_competence=through_competence
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
