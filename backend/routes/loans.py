from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance import loans
from ..finance.payments import CASH_LINK_REQUIRED_MESSAGE


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])


def _error_detail(code: str, message: str, fields=None) -> dict:
    return {"code": code, "message": message, "fields": fields}


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
    cash_account_id: Optional[int] = None
    existing_cash_event_id: Optional[int] = None


class InstallmentPayment(BaseModel):
    principal_cents: int = Field(ge=0)
    interest_cents: int = Field(ge=0)
    paid_at: date


class Renegotiation(BaseModel):
    installments: list[InstallmentPlan]
    reason: str = Field(min_length=1, max_length=500)


class LoanPatch(BaseModel):
    """Deliberately declares the SAME value-bearing fields as `LoanCreate`
    (principal_cents, installments, ...) alongside lender/purpose — not
    because PATCH accepts them, but so a client that mistakenly sends one
    gets a clean 422 from `loans.update_loan_details`'s own field check
    (below) instead of FastAPI silently dropping an unrecognized field.
    Mirrors backend/routes/financial_entries.py's EntryUpdate, which does the
    same thing for the same reason."""

    expected_version: int
    lender: Optional[str] = None
    purpose: Optional[str] = None
    principal_cents: Optional[int] = None
    net_disbursement_cents: Optional[int] = None
    cet_bps: Optional[int] = None
    rate_bps: Optional[int] = None
    grace_days: Optional[int] = None
    start_date: Optional[str] = None
    installments: Optional[list[InstallmentPlan]] = None


class LoanCancel(BaseModel):
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
    except loans.LoanValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
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
def add_disbursement(
    company: int,
    loan_id: int,
    body: Disbursement,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_loan(company, loan_id)
    try:
        return loans.record_disbursement(
            company,
            loan_id,
            body.amount_cents,
            body.disbursed_at,
            idempotency_key=idempotency_key,
            cash_account_id=body.cash_account_id,
            existing_cash_event_id=body.existing_cash_event_id,
            actor_id=auth.user_id,
        )
    except loans.LoanNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", exc.message)) from exc
    except loans.LoanConflictError as exc:
        raise HTTPException(
            409, _error_detail("conflict", exc.message, exc.fields)
        ) from exc
    except loans.LoanValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
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
def renegotiate(
    company: int,
    loan_id: int,
    body: Renegotiation,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_loan(company, loan_id)
    try:
        loans.renegotiate(
            loan_id,
            installments=[item.model_dump() for item in body.installments],
            reason=body.reason,
            actor_id=auth.user_id,
        )
    except loans.LoanNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", exc.message)) from exc
    except loans.LoanConflictError as exc:
        raise HTTPException(
            409, _error_detail("conflict", exc.message, exc.fields)
        ) from exc
    except loans.LoanValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return loans.loan_position(loan_id)


@router.patch(
    "/loans/{loan_id}",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def patch_loan(
    company: int,
    loan_id: int,
    body: LoanPatch,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_loan(company, loan_id)
    patch = body.dict(exclude_unset=True, exclude={"expected_version"})
    try:
        loans.update_loan_details(
            company,
            loan_id,
            patch,
            expected_version=body.expected_version,
            actor_id=auth.user_id,
        )
    except loans.LoanNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", exc.message)) from exc
    except loans.LoanConflictError as exc:
        raise HTTPException(
            409, _error_detail("conflict", exc.message, exc.fields)
        ) from exc
    except loans.LoanValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
    return loans.loan_position(loan_id)


@router.post(
    "/loans/{loan_id}/cancel",
    dependencies=[Depends(permissions.require_permission("finance.write"))],
)
def cancel_loan(
    company: int,
    loan_id: int,
    body: LoanCancel,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_loan(company, loan_id)
    try:
        loans.cancel_loan(company, loan_id, reason=body.reason, actor_id=auth.user_id)
    except loans.LoanNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", exc.message)) from exc
    except loans.LoanConflictError as exc:
        raise HTTPException(
            409, _error_detail("conflict", exc.message, exc.fields)
        ) from exc
    except loans.LoanValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
    return loans.loan_position(loan_id)
