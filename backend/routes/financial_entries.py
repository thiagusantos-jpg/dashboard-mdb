from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import database as db, permissions, security
from ..finance.entries import EntryCommand, create_entry, reverse_entry
from ..finance.entry_management import (
    EntryConflictError,
    EntryNotFoundError,
    EntryValidationError,
    cancel_entry,
    entry_history,
    update_entry,
)
from ..finance.expense_schedules import confirm_entry, create_expense_schedule, preview_expense_schedule
from ..finance import fixed_expenses, stone_guard
from ..finance.payments import CASH_LINK_REQUIRED_MESSAGE
from ..finance.recurrence import (
    RecurrenceConflictError,
    RecurrenceNotFoundError,
    RecurrenceValidationError,
    create_recurrence,
    generate_occurrences,
    update_recurrence,
)


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
    payment_method: str = Field(default="", pattern=r"^(|boleto|pix|debito_automatico|transferencia)$")
    payment_code: str = Field(default="", max_length=200)
    forecast: bool = False
    confirm_not_stone_duplicate: bool = False


class Settlement(BaseModel):
    amount_cents: int = Field(gt=0)
    paid_at: date


class Reversal(BaseModel):
    reversed_at: date
    reason: str = Field(min_length=1, max_length=500)


class EntryUpdate(BaseModel):
    expected_version: int
    account_id: Optional[int] = None
    counterparty_id: Optional[int] = None
    description: Optional[str] = None
    amount_cents: Optional[int] = None
    competence: Optional[str] = None
    due_date: Optional[date] = None
    notes: Optional[str] = None
    payment_method: Optional[str] = Field(default=None, pattern=r"^(|boleto|pix|debito_automatico|transferencia)$")
    payment_code: Optional[str] = Field(default=None, max_length=200)


class EntryCancel(BaseModel):
    expected_version: int
    reason: str


class RecurrenceCreate(BaseModel):
    account_id: int
    amount_cents: int = Field(gt=0)
    description: str = Field(min_length=1, max_length=240)
    start_competence: str = Field(pattern=r"^\d{4}-\d{2}$")
    end_competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    due_day: int = Field(ge=1, le=31)
    store_id: Optional[int] = None
    counterparty_id: Optional[int] = None
    payment_method: str = Field(default="", pattern=r"^(|boleto|pix|debito_automatico|transferencia)$")
    payment_code: str = Field(default="", max_length=200)
    confirm_not_stone_duplicate: bool = False


class RecurrencePatch(BaseModel):
    expected_version: int
    scope: str = Field(pattern=r"^(single|future)$")
    # scope='single': identifies the one occurrence being edited.
    competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    # scope='future': cutoff competence for the cascade onto forecast occurrences.
    effective_competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    account_id: Optional[int] = None
    counterparty_id: Optional[int] = None
    description: Optional[str] = None
    amount_cents: Optional[int] = None
    due_day: Optional[int] = None
    due_date: Optional[date] = None
    end_competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    active: Optional[bool] = None
    notes: Optional[str] = None


class ExpenseSchedulePreviewRequest(BaseModel):
    total_cents: int = Field(gt=0)
    count: int = Field(ge=1, le=120)
    first_due: date
    competence_mode: str = Field(pattern=r"^(single|distributed)$")
    competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")


class ExpenseScheduleCreate(ExpenseSchedulePreviewRequest):
    account_id: int
    description: str = Field(min_length=1, max_length=240)
    store_id: Optional[int] = None
    counterparty_id: Optional[int] = None
    confirmed: bool = False
    confirm_not_stone_duplicate: bool = False


class EntryConfirm(BaseModel):
    expected_version: int
    amount_cents: Optional[int] = Field(default=None, gt=0)
    competence: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")


STONE_DUPLICATE_FIELD = "confirm_not_stone_duplicate"


def _guard_stone_duplicate(company: int, account_id: int, months, confirmed: bool) -> None:
    """Stone fees and the monthly fee come from the imported report; a manual
    one needs the partner to say it is a different charge."""
    if confirmed:
        return
    message = stone_guard.duplicate_message(company, account_id, months)
    if message:
        raise HTTPException(
            422, _error_detail("stone_duplicate", message, [STONE_DUPLICATE_FIELD])
        )


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


# --- New B1 routes (edit / cancel / history) -------------------------------
#
# NOTE: unlike the routes above (which raise HTTPException with a plain
# string `detail=`), the three routes below use the shared-contract error
# body `{code, message, fields}` per the B plan. This intentionally makes
# this file inconsistent until C2 updates the frontend's `api()` helper to
# handle both shapes (see master plan, C2 task notes).

_BIGINT_ID_FIELDS = (
    "id",
    "account_id",
    "counterparty_id",
    "recurrence_id",
    "created_by",
    "store",
    "expense_schedule_id",
)


def _stringify_ids(payload: dict) -> dict:
    result = dict(payload)
    for field in _BIGINT_ID_FIELDS:
        if field in result and result[field] is not None:
            result[field] = str(result[field])
    return result


def _stringify_history_item(item: dict) -> dict:
    result = dict(item)
    if result.get("id") is not None:
        result["id"] = str(result["id"])
    if result.get("actor_id") is not None:
        result["actor_id"] = str(result["actor_id"])
    # `before`/`after` are raw snapshots of the `financial_entries` row (see
    # entry_management._insert_audit): they carry the same BIGINT-ish columns
    # (id, account_id, counterparty_id, recurrence_id, created_by, store) as
    # the entry payload itself, so they need the same stringification or a
    # value > 2**53 silently loses precision in JS JSON.parse.
    if isinstance(result.get("before"), dict):
        result["before"] = _stringify_ids(result["before"])
    if isinstance(result.get("after"), dict):
        result["after"] = _stringify_ids(result["after"])
    return result


def _error_detail(code: str, message: str, fields=None) -> dict:
    return {"code": code, "message": message, "fields": fields}


def _entry_account_or_404(company: int, entry_id: int) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT account_id FROM financial_entries WHERE id=? AND company=?",
            (entry_id, company),
        ).fetchone()
    if not row:
        raise HTTPException(404, _error_detail("not_found", "Lançamento não encontrado."))
    return row["account_id"]


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
    _guard_stone_duplicate(company, body.account_id, [body.competence], body.confirm_not_stone_duplicate)
    if body.counterparty_id is not None:
        # Same rule as editing (B1): a counterparty from another company is rejected.
        from ..finance.validators import validate_counterparty

        class _FieldError(ValueError):
            def __init__(self, message, fields=None):
                super().__init__(message)
                self.fields = fields or []

        with db.connection() as conn:
            try:
                validate_counterparty(conn, company, body.counterparty_id, error_cls=_FieldError)
            except _FieldError as exc:
                raise HTTPException(422, _error_detail("invalid_fields", str(exc), exc.fields)) from exc
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
                payment_method=body.payment_method,
                payment_code=body.payment_code,
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
    # This legacy route's payload never carries a cash-account link
    # (`Settlement` above has no cash_account_id/existing_cash_event_id
    # field) — task B3 requires every payment to be linked to a real cash
    # movement (created or attached to an already-imported bank
    # transaction), same underlying rule as POST
    # /obligations/entry/{id}/payments (backend/finance/payments.py::
    # record_payment). Rather than silently settling the entry with no cash
    # trace, reject and point the caller at the new flow.
    _require_entry_access(company, entry_id, auth)
    raise HTTPException(409, _error_detail("conflict", CASH_LINK_REQUIRED_MESSAGE))


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
    _guard_stone_duplicate(company, body.account_id, None, body.confirm_not_stone_duplicate)
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
            payment_method=body.payment_method,
            payment_code=body.payment_code,
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


# --- Task B6: recurrence edit scope, installment previews, confirmation ---


@router.patch("/recurrences/{recurrence_id}")
def patch_recurrence(
    company: int,
    recurrence_id: int,
    body: RecurrencePatch,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    with db.connection() as conn:
        row = conn.execute(
            "SELECT account_id FROM financial_recurrences WHERE id=? AND company=?",
            (recurrence_id, company),
        ).fetchone()
    if not row:
        raise HTTPException(404, _error_detail("not_found", "Recorrência não encontrada."))
    _require_sensitive_if_needed(company, row["account_id"], auth)
    if body.account_id is not None and body.account_id != row["account_id"]:
        _require_sensitive_if_needed(company, body.account_id, auth)

    changes = body.dict(
        exclude_unset=True,
        exclude={"expected_version", "scope", "competence", "effective_competence"},
    )
    try:
        updated = update_recurrence(
            company,
            recurrence_id,
            changes,
            expected_version=body.expected_version,
            scope=body.scope,
            effective_competence=body.effective_competence,
            occurrence_competence=body.competence,
            actor_id=auth.user_id,
        )
    except RecurrenceNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", str(exc))) from exc
    except RecurrenceConflictError as exc:
        raise HTTPException(409, _error_detail("conflict", str(exc))) from exc
    except RecurrenceValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", str(exc), exc.fields)
        ) from exc
    return {
        "recurrence": _stringify_ids(updated["recurrence"]),
        "occurrence": _stringify_ids(updated["occurrence"])
        if updated["occurrence"] is not None
        else None,
    }


@router.post("/entries/{entry_id}/confirm")
def confirm(
    company: int,
    entry_id: int,
    body: EntryConfirm,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_entry_access(company, entry_id, auth)
    try:
        confirmed = confirm_entry(
            company,
            entry_id,
            expected_version=body.expected_version,
            amount_cents=body.amount_cents,
            competence=body.competence,
            actor_id=auth.user_id,
        )
    except EntryNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", str(exc))) from exc
    except EntryConflictError as exc:
        raise HTTPException(409, _error_detail("conflict", str(exc))) from exc
    except EntryValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", str(exc), exc.fields)
        ) from exc
    return _stringify_ids(confirmed)


@router.post("/expense-schedules/preview")
def preview_schedule(
    company: int,
    body: ExpenseSchedulePreviewRequest,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    try:
        rows = preview_expense_schedule(
            body.total_cents,
            body.count,
            body.first_due,
            body.competence_mode,
            body.competence,
        )
    except ValueError as exc:
        raise HTTPException(422, _error_detail("invalid_fields", str(exc))) from exc
    return {
        "items": rows,
        "total_cents": body.total_cents,
        "requires_confirmation": body.competence_mode == "distributed",
    }


@router.post("/expense-schedules", status_code=201)
def add_expense_schedule(
    company: int,
    body: ExpenseScheduleCreate,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_sensitive_if_needed(company, body.account_id, auth)
    try:
        months = [row["competence"] for row in preview_expense_schedule(
            body.total_cents, body.count, body.first_due, body.competence_mode, body.competence,
        )]
    except ValueError as exc:
        raise HTTPException(422, _error_detail("invalid_fields", str(exc))) from exc
    _guard_stone_duplicate(company, body.account_id, months, body.confirm_not_stone_duplicate)
    try:
        result = create_expense_schedule(
            company=company,
            account_id=body.account_id,
            description=body.description,
            total_cents=body.total_cents,
            count=body.count,
            first_due=body.first_due,
            competence_mode=body.competence_mode,
            competence=body.competence,
            store=body.store_id,
            counterparty_id=body.counterparty_id,
            confirmed=body.confirmed,
            created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, _error_detail("invalid_fields", str(exc))) from exc
    result["schedule"] = _stringify_ids(result["schedule"])
    result["entries"] = [_stringify_ids(entry) for entry in result["entries"]]
    return result


@router.patch("/entries/{entry_id}")
def patch_entry(
    company: int,
    entry_id: int,
    body: EntryUpdate,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    current_account_id = _entry_account_or_404(company, entry_id)
    _require_sensitive_if_needed(company, current_account_id, auth)
    if body.account_id is not None and body.account_id != current_account_id:
        _require_sensitive_if_needed(company, body.account_id, auth)

    patch = body.dict(exclude_unset=True, exclude={"expected_version"})
    try:
        updated = update_entry(
            company,
            entry_id,
            patch,
            expected_version=body.expected_version,
            actor_id=auth.user_id,
        )
    except EntryNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", str(exc))) from exc
    except EntryConflictError as exc:
        raise HTTPException(409, _error_detail("conflict", str(exc))) from exc
    except EntryValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", str(exc), exc.fields)
        ) from exc
    return _stringify_ids(updated)


@router.post("/entries/{entry_id}/cancel")
def cancel(
    company: int,
    entry_id: int,
    body: EntryCancel,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.write")
    ),
):
    _require_entry_access(company, entry_id, auth)
    try:
        cancelled = cancel_entry(
            company,
            entry_id,
            expected_version=body.expected_version,
            reason=body.reason,
            actor_id=auth.user_id,
        )
    except EntryNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", str(exc))) from exc
    except EntryConflictError as exc:
        raise HTTPException(409, _error_detail("conflict", str(exc))) from exc
    except EntryValidationError as exc:
        raise HTTPException(
            422, _error_detail("invalid_fields", str(exc), exc.fields)
        ) from exc
    return _stringify_ids(cancelled)


@router.get("/entries/{entry_id}/history")
def get_history(
    company: int,
    entry_id: int,
    auth: security.AuthContext = Depends(
        permissions.require_permission("finance.read")
    ),
):
    _require_entry_access(company, entry_id, auth)
    try:
        history = entry_history(company, entry_id)
    except EntryNotFoundError as exc:
        raise HTTPException(404, _error_detail("not_found", str(exc))) from exc
    return [_stringify_history_item(item) for item in history]


# --- Resultado gerencial: guided setup of the store's fixed monthly expenses ---


class FixedExpenseItem(BaseModel):
    system_key: str = Field(min_length=1, max_length=60)
    amount_cents: int = Field(gt=0)
    due_day: int = Field(ge=1, le=31)


class FixedExpenseSetup(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    items: list[FixedExpenseItem] = Field(min_length=1, max_length=20)


@router.get("/fixed-expenses")
def get_fixed_expense_template(
    company: int,
    auth=Depends(permissions.require_permission("finance.read")),
):
    return fixed_expenses.setup_template(company)


@router.get("/stone-coverage")
def get_stone_coverage(
    company: int,
    auth=Depends(permissions.require_permission("finance.read")),
):
    """Categories booked automatically from the Stone report and the months it covers."""
    return stone_guard.coverage(company)


@router.post("/fixed-expenses", status_code=201)
def post_fixed_expenses(
    company: int,
    body: FixedExpenseSetup,
    auth: security.AuthContext = Depends(permissions.require_permission("finance.write")),
):
    rows = {row["system_key"]: row for row in fixed_expenses.setup_template(company)}
    for item in body.items:
        row = rows.get(item.system_key)
        if row is None:
            raise HTTPException(422, _error_detail("invalid_fields", "Despesa fixa desconhecida."))
        # Salários e encargos são contas sensíveis.
        _require_sensitive_if_needed(company, row["account_id"], auth)
        if row.get("stone_automatic"):
            raise HTTPException(422, _error_detail(
                "stone_duplicate",
                "A mensalidade da Stone já entra automaticamente pelo relatório de recebíveis.",
                ["items"],
            ))
    try:
        return fixed_expenses.setup_fixed_expenses(
            company, body.period, [item.model_dump() for item in body.items], created_by=auth.user_id,
        )
    except ValueError as exc:
        raise HTTPException(422, _error_detail("invalid_fields", str(exc))) from exc
