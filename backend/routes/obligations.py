from __future__ import annotations

from datetime import datetime as _datetime
from zoneinfo import ZoneInfo

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance import obligations, payment_matches, payments


router = APIRouter(prefix="/api/companies/{company}/finance", tags=["finance"])

_VALID_KINDS = ("entry", "loan_installment")


class PaymentCreate(BaseModel):
    amount_cents: int = Field(gt=0)
    paid_at: date
    expected_version: int
    cash_account_id: Optional[int] = None
    existing_cash_event_id: Optional[int] = None
    principal_cents: Optional[int] = None
    interest_cents: Optional[int] = None
    late_fee_cents: Optional[int] = Field(default=None, ge=1)


class PaymentReversalCreate(BaseModel):
    reason: str
    reversed_at: date
    expected_version: int


def _error_detail(code: str, message: str, fields=None) -> dict:
    return {"code": code, "message": message, "fields": fields}


def _has_permission(auth: security.AuthContext, permission: str, company: int) -> bool:
    try:
        permissions.ensure_permission(auth, permission, company)
        return True
    except HTTPException:
        return False


def _strip_write_actions(item: dict) -> dict:
    item["allowed_actions"] = [
        action for action in item["allowed_actions"] if action not in obligations.WRITE_ACTIONS
    ]
    return item


def _require_entry_payment_access(
    company: int, entry_id: int, auth: security.AuthContext
) -> None:
    """Mirrors backend/routes/financial_entries.py's
    `_require_sensitive_if_needed`: paying an entry booked against a
    sensitive account (e.g. salaries) still requires finance.sensitive.read,
    same as the legacy settlement route enforced — this route must not
    become a way to bypass that gate. loan_installment has no sensitive-
    account concept (obligations.py: "loans carry no account/sensitive
    linkage"), so this only applies to kind='entry'."""
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT a.sensitive FROM financial_entries e
            JOIN finance_accounts a ON a.id=e.account_id
            WHERE e.id=? AND e.company=?
            """,
            (entry_id, company),
        ).fetchone()
    if row and row["sensitive"]:
        permissions.ensure_permission(auth, "finance.sensitive.read", company)


@router.get("/obligations")
def list_obligations(
    company: int,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    due_from: Optional[date] = None,
    due_to: Optional[date] = None,
    q: str = "",
    cursor: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    if kind is not None and kind not in _VALID_KINDS:
        raise HTTPException(
            422, _error_detail("invalid_fields", "Tipo de obrigação inválido.", ["kind"])
        )

    include_sensitive = _has_permission(auth, "finance.sensitive.read", company)
    can_write = _has_permission(auth, "finance.write", company)

    try:
        result = obligations.list_obligations(
            company,
            kind=kind,
            status=status,
            due_from=due_from,
            due_to=due_to,
            q=q,
            cursor=cursor,
            limit=limit,
            include_sensitive=include_sensitive,
        )
    except ValueError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", str(exc), ["cursor"])
        ) from exc

    if not can_write:
        result["items"] = [_strip_write_actions(item) for item in result["items"]]
    return result


@router.get("/obligations/summary")
def obligations_summary(
    company: int,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    return obligations.obligation_summary(
        company,
        today=_datetime.now(ZoneInfo("America/Sao_Paulo")).date(),
        include_sensitive=_has_permission(auth, "finance.sensitive.read", company),
    )


@router.get("/obligations/payment-suggestions")
def obligation_payment_suggestions(
    company: int,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    """Contas abertas que já têm um débito correspondente no caixa. Só sugere: a baixa
    em si passa pela gaveta de pagamento e por payments.record_payment, como sempre.

    Declarada antes de /obligations/{kind}/{obligation_id} de propósito — o caminho
    literal precisa vir antes do parametrizado, senão "payment-suggestions" seria lido
    como um `kind`."""
    return payment_matches.suggestions(
        company,
        today=_datetime.now(ZoneInfo("America/Sao_Paulo")).date(),
        include_sensitive=_has_permission(auth, "finance.sensitive.read", company),
    )


@router.get("/obligations/{kind}/{obligation_id}")
def get_obligation(
    company: int,
    kind: str,
    obligation_id: str,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.read")),
):
    if kind not in _VALID_KINDS:
        raise HTTPException(404, _error_detail("not_found", "Obrigação não encontrada."))
    try:
        numeric_id = int(obligation_id)
    except (TypeError, ValueError):
        raise HTTPException(
            422, _error_detail("invalid_fields", "Identificador inválido.", ["id"])
        )

    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone()
    if not exists:
        raise HTTPException(404, _error_detail("not_found", "Empresa não encontrada."))

    include_sensitive = _has_permission(auth, "finance.sensitive.read", company)
    can_write = _has_permission(auth, "finance.write", company)

    item = obligations.get_obligation(
        company, kind, numeric_id, include_sensitive=include_sensitive
    )
    if item is None:
        raise HTTPException(404, _error_detail("not_found", "Obrigação não encontrada."))

    if not can_write:
        _strip_write_actions(item)
    item["payments"] = _obligation_payments(company, kind, numeric_id)
    return item


def _obligation_payments(company: int, kind: str, obligation_id: int) -> list:
    """Every payment recorded against one obligation, reversed ones included,
    so the detail view can offer "Estornar" on a specific payment. Only
    display fields: idempotency keys and stored responses stay server-side."""
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT id,amount_cents,principal_cents,interest_cents,paid_at,
                   cash_event_id,owns_cash_event,reversed_at,reversal_reason,created_at
            FROM obligation_payments
            WHERE company=? AND obligation_kind=? AND obligation_id=?
            ORDER BY paid_at,created_at,id
            """,
            (company, kind, obligation_id),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "amount_cents": row["amount_cents"],
            "principal_cents": row["principal_cents"],
            "interest_cents": row["interest_cents"],
            "paid_at": row["paid_at"],
            "cash_event_id": None if row["cash_event_id"] is None else str(row["cash_event_id"]),
            "generated_cash_event": bool(row["owns_cash_event"]),
            "reversed_at": row["reversed_at"],
            "reversal_reason": row["reversal_reason"],
        }
        for row in rows
    ]


@router.post("/obligations/{kind}/{obligation_id}/payments")
def add_payment(
    company: int,
    kind: str,
    obligation_id: str,
    body: PaymentCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    if kind not in _VALID_KINDS:
        raise HTTPException(404, _error_detail("not_found", "Obrigação não encontrada."))
    try:
        numeric_id = int(obligation_id)
    except (TypeError, ValueError):
        raise HTTPException(
            422, _error_detail("invalid_fields", "Identificador inválido.", ["id"])
        )

    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone()
    if not exists:
        raise HTTPException(404, _error_detail("not_found", "Empresa não encontrada."))

    if kind == "entry":
        _require_entry_payment_access(company, numeric_id, auth)

    try:
        result = payments.record_payment(
            company,
            kind,
            numeric_id,
            amount_cents=body.amount_cents,
            paid_at=body.paid_at,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
            cash_account_id=body.cash_account_id,
            existing_cash_event_id=body.existing_cash_event_id,
            principal_cents=body.principal_cents,
            interest_cents=body.interest_cents,
            late_fee_cents=body.late_fee_cents,
            actor_id=auth.user_id,
        )
    except payments.PaymentNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", exc.message)) from exc
    except payments.PaymentConflictError as exc:
        raise HTTPException(409, _error_detail("conflict", exc.message, exc.fields)) from exc
    except payments.PaymentValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
    return result


@router.post("/payments/{payment_id}/reverse")
def reverse_obligation_payment(
    company: int,
    payment_id: str,
    body: PaymentReversalCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    try:
        numeric_id = int(payment_id)
    except (TypeError, ValueError):
        raise HTTPException(
            422, _error_detail("invalid_fields", "Identificador inválido.", ["id"])
        )

    with db.connection() as conn:
        exists = conn.execute("SELECT 1 FROM companies WHERE id=?", (company,)).fetchone()
    if not exists:
        raise HTTPException(404, _error_detail("not_found", "Empresa não encontrada."))

    # Mirror the same sensitive-account gate applied to creating a payment
    # (_require_entry_payment_access) — undoing a payment against a
    # sensitive-account entry must not be reachable without
    # finance.sensitive.read either.
    with db.connection() as conn:
        payment_row = conn.execute(
            "SELECT obligation_kind,obligation_id FROM obligation_payments WHERE id=? AND company=?",
            (numeric_id, company),
        ).fetchone()
    if payment_row and payment_row["obligation_kind"] == "entry":
        _require_entry_payment_access(company, payment_row["obligation_id"], auth)

    try:
        result = payments.reverse_payment(
            company,
            numeric_id,
            reason=body.reason,
            reversed_at=body.reversed_at,
            expected_version=body.expected_version,
            idempotency_key=idempotency_key,
            actor_id=auth.user_id,
        )
    except payments.PaymentNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", exc.message)) from exc
    except payments.PaymentConflictError as exc:
        raise HTTPException(409, _error_detail("conflict", exc.message, exc.fields)) from exc
    except payments.PaymentValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", exc.message, exc.fields)
        ) from exc
    return result
