from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance import loans
from ..finance.payments import CASH_LINK_REQUIRED_MESSAGE


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


class InstallmentPlan(BaseModel):
    number: int = Field(ge=1)
    due_date: str
    principal_cents: int = Field(ge=0)
    interest_cents: int = Field(ge=0)


class LoanCreate(BaseModel):
    lender: str = Field(min_length=1, max_length=180)
    purpose: str = Field(default="", max_length=240)
    principal_cents: int = Field(gt=0)
    net_disbursement_cents: int = Field(gt=0)
    installments: list[InstallmentPlan]
    cet_bps: Optional[int] = None
    rate_bps: Optional[int] = None
    grace_days: int = Field(default=0, ge=0)
    start_date: str
    store: Optional[int] = None


class Disbursement(BaseModel):
    amount_cents: int = Field(gt=0)
    disbursed_at: date


class InstallmentPayment(BaseModel):
    principal_cents: int = Field(ge=0)
    interest_cents: int = Field(ge=0)
    paid_at: date


class Renegotiation(BaseModel):
    installments: list[InstallmentPlan]
    reason: str = Field(min_length=1, max_length=500)


def _require_company(company: int) -> None:
    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone()
    if not exists:
        raise HTTPException(404, "Empresa não encontrada.")


def _require_loan(company: int, loan_id: int) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM loans WHERE id=? AND company=?", (loan_id, company)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Empréstimo não encontrado.")
    return dict(row)


@router.get(
    "/loans",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def list_loans(company: int):
    _require_company(company)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM loans WHERE company=? ORDER BY start_date DESC", (company,)
        ).fetchall()
    return [loans.loan_position(row["id"]) for row in rows]


@router.post(
    "/loans",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def create_loan(company: int, body: LoanCreate):
    _require_company(company)
    try:
        loan = loans.create_loan(
            company,
            lender=body.lender,
            purpose=body.purpose,
            principal_cents=body.principal_cents,
            net_disbursement_cents=body.net_disbursement_cents,
            installments=[item.model_dump() for item in body.installments],
            cet_bps=body.cet_bps,
            rate_bps=body.rate_bps,
            grace_days=body.grace_days,
            start_date=body.start_date,
            store=body.store,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return loans.loan_position(loan["id"])


@router.get(
    "/loans/{loan_id}",
    dependencies=[Depends(permissions.require_permission("finance.read"))],
)
def get_loan(company: int, loan_id: int):
    _require_loan(company, loan_id)
    return loans.loan_position(loan_id)


@router.post(
    "/loans/{loan_id}/disbursements",
    status_code=201,
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def add_disbursement(company: int, loan_id: int, body: Disbursement):
    _require_loan(company, loan_id)
    try:
        return loans.record_disbursement(loan_id, body.amount_cents, body.disbursed_at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/loans/installments/{installment_id}/payments",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def pay_installment(
    company: int,
    installment_id: int,
    body: InstallmentPayment,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT i.loan_id,l.company FROM loan_installments i
            JOIN loans l ON l.id=i.loan_id
            WHERE i.id=?
            """,
            (installment_id,),
        ).fetchone()
    if not row or row["company"] != company:
        raise HTTPException(404, "Parcela não encontrada.")
    # This legacy route's payload never carries a cash-account link
    # (`InstallmentPayment` above has no cash_account_id/
    # existing_cash_event_id field) — task B3 requires every payment to be
    # linked to a real cash movement, same rule enforced by POST
    # /obligations/loan_installment/{id}/payments (backend/finance/payments.py
    # ::record_payment). Rather than silently marking the installment paid
    # with no cash trace, reject and point the caller at the new flow.
    raise HTTPException(409, CASH_LINK_REQUIRED_MESSAGE)


@router.post(
    "/loans/{loan_id}/renegotiate",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def renegotiate(company: int, loan_id: int, body: Renegotiation):
    _require_loan(company, loan_id)
    try:
        loans.renegotiate(
            loan_id,
            installments=[item.model_dump() for item in body.installments],
            reason=body.reason,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return loans.loan_position(loan_id)
