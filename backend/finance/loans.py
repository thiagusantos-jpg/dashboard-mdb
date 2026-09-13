from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date
from typing import Optional

from .. import database as db
from . import accounts
from .entries import (
    EntryCommand,
    create_entry,
    create_entry_on_connection,
    settle_entry,
    settle_entry_on_connection,
)
from .entry_management import _insert_audit
from .ledger import post_cash_event


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _is_unique_violation(exc: Exception) -> bool:
    # Duplicated from backend/finance/payments.py::_is_unique_violation
    # (importing it here would create a cycle: payments.py already imports
    # from this module). Same rationale as that copy: recover from a race on
    # the idempotency UNIQUE constraint by replaying the winner's row instead
    # of surfacing a raw IntegrityError.
    name = type(exc).__name__
    if name in ("IntegrityError", "UniqueViolation"):
        return True
    return getattr(exc, "sqlstate", None) == "23505"


# Final review C4: loan_disbursements now carries a partial UNIQUE index on
# cash_event_id (migration 022). See payments.py::_is_cash_event_unique_violation
# for the full rationale — the message of a violation names the column
# (SQLite) or the index (PostgreSQL), and both spellings contain "cash_event",
# which is what separates it from the idempotency-key UNIQUE that shares the
# same INSERT.
_CASH_EVENT_ALLOCATED_MESSAGE = "Movimento de caixa já está vinculado a outro desembolso."


def _is_cash_event_unique_violation(exc: Exception) -> bool:
    if not _is_unique_violation(exc):
        return False
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    if constraint:
        return "cash_event" in constraint
    return "cash_event" in str(exc)


class InstallmentVersionConflict(ValueError):
    """Raised by pay_installment_on_connection when the optimistic-concurrency
    version check fails. See entries.EntryVersionConflict for the rationale;
    subclasses ValueError so a bare `except ValueError` still catches it."""


class LoanError(Exception):
    """Base error for the NEW loan-contract operations added by task B7
    (PATCH lender/purpose, cancel, disbursement, and the stronger
    create/renegotiate validation below). Pre-existing functions in this
    module (create_loan's lender/principal checks, the legacy disbursement
    helper, pay_installment_*) deliberately keep raising bare ValueError —
    retrofitting them to this shape is out of this task's scope (see task
    brief: "retrofitting all of those is explicitly OUT OF SCOPE"). Every
    error condition this task INTRODUCES uses this hierarchy instead, so
    backend/routes/loans.py can map it to the plan's {code,message,fields}
    contract."""

    def __init__(self, message: str, *, fields: Optional[list] = None):
        super().__init__(message)
        self.message = message
        self.fields = fields


class LoanNotFoundError(LoanError):
    """-> HTTP 404."""


class LoanConflictError(LoanError):
    """-> HTTP 409 (version conflict, wrong loan status, movement already
    recorded, idempotency key reused with different data, ...)."""


class LoanValidationError(LoanError):
    """-> HTTP 422 (bad input shape, principal-sum mismatch, ...)."""


def _validate_schedule_items(installments: list) -> int:
    """Server-side revalidation of a proposed installment schedule — types,
    quantities and dates — shared by create_loan and renegotiate so a fresh
    contract and a renegotiated one get identical scrutiny regardless of
    caller (the HTTP layer's Pydantic models already coerce types for real
    requests, but this module's functions are also called directly, e.g. by
    this task's own tests, with no such gate). Returns the sum of
    principal_cents so callers can compare it against whatever total the
    caller expects (the loan's principal_cents for create_loan, the
    outstanding principal for renegotiate) — "validar tipos, quantidades,
    datas e soma ... no servidor" per the task brief.

    Variable interest per installment (juros variáveis) is valid by
    construction: interest_cents is read per item and never compared against
    any running total, so a schedule where each installment carries a
    different interest amount is accepted without special-casing.
    """
    if not installments:
        raise LoanValidationError("Informe ao menos uma parcela.", fields=["installments"])
    principal_sum = 0
    seen_numbers = set()
    for item in installments:
        number = item.get("number")
        principal = item.get("principal_cents")
        interest = item.get("interest_cents", 0)
        due_date = item.get("due_date")
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise LoanValidationError("Número de parcela inválido.", fields=["installments"])
        if number in seen_numbers:
            raise LoanValidationError("Números de parcela duplicados.", fields=["installments"])
        seen_numbers.add(number)
        if not isinstance(principal, int) or isinstance(principal, bool) or principal < 0:
            raise LoanValidationError("Principal da parcela inválido.", fields=["installments"])
        if not isinstance(interest, int) or isinstance(interest, bool) or interest < 0:
            raise LoanValidationError("Juros da parcela inválido.", fields=["installments"])
        try:
            date.fromisoformat(str(due_date))
        except (TypeError, ValueError) as exc:
            raise LoanValidationError(
                "Data de vencimento inválida.", fields=["installments"]
            ) from exc
        principal_sum += principal
    return principal_sum


def create_loan(
    company: int,
    lender: str,
    purpose: str,
    principal_cents: int,
    net_disbursement_cents: int,
    installments: list,
    *,
    cet_bps: Optional[int] = None,
    rate_bps: Optional[int] = None,
    grace_days: int = 0,
    start_date: str,
    store: Optional[int] = None,
) -> dict:
    clean_lender = lender.strip()
    if not clean_lender:
        raise ValueError("Informe o credor.")
    if principal_cents <= 0:
        raise ValueError("O principal deve ser maior que zero.")
    principal_sum = _validate_schedule_items(installments)
    if principal_sum != principal_cents:
        raise LoanValidationError(
            "O principal do contrato deve ser igual à soma do principal das parcelas.",
            fields=["principal_cents", "installments"],
        )
    loan_id = _new_id()
    schedule_id = _new_id()
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO loans(
                id,company,store,lender,purpose,principal_cents,net_disbursement_cents,
                cet_bps,rate_bps,grace_days,start_date,status,active_schedule_id,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                loan_id, company, store, clean_lender, purpose.strip(),
                principal_cents, net_disbursement_cents, cet_bps, rate_bps,
                grace_days, start_date, "active", schedule_id, timestamp, timestamp,
            ),
        )
        _insert_schedule(conn, schedule_id, loan_id, installments, timestamp)
        row = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    return dict(row)


def _insert_schedule(conn, schedule_id: int, loan_id: int, installments: list, timestamp: str) -> None:
    conn.execute(
        "INSERT INTO loan_schedules(id,loan_id,status,reason,created_at) VALUES(?,?,?,?,?)",
        (schedule_id, loan_id, "active", "", timestamp),
    )
    for item in installments:
        principal = item["principal_cents"]
        interest = item.get("interest_cents", 0)
        conn.execute(
            """
            INSERT INTO loan_installments(
                id,schedule_id,loan_id,number,due_date,principal_cents,interest_cents,
                total_cents,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _new_id(), schedule_id, loan_id, item["number"], item["due_date"],
                principal, interest, principal + interest, "open", timestamp, timestamp,
            ),
        )


def _disbursement_request_hash(
    *,
    loan_id: int,
    amount_cents: int,
    disbursed_at: date,
    cash_account_id: Optional[int],
    existing_cash_event_id: Optional[int],
) -> str:
    # Mirrors payments.py::_request_hash's rationale exactly (same fields
    # that determine whether a retry is a genuine replay vs. a conflicting
    # reuse of the same key) — deliberately excludes nothing version-related
    # since record_disbursement has no expected_version parameter at all.
    payload = {
        "loan_id": loan_id,
        "amount_cents": amount_cents,
        "disbursed_at": disbursed_at.isoformat(),
        "cash_account_id": cash_account_id,
        "existing_cash_event_id": existing_cash_event_id,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def record_disbursement(
    company: int,
    loan_id: int,
    amount_cents: int,
    disbursed_at: date,
    *,
    idempotency_key: str,
    cash_account_id: Optional[int] = None,
    existing_cash_event_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> dict:
    """Record that a loan's principal was actually disbursed — a genuine gap
    filled from scratch (see task brief: the pre-B7 version only inserted a
    bookkeeping row into `loan_disbursements`, with no cash movement, no
    financial_entries trace, and no idempotency mechanism at all).

    Follows the same two established patterns this codebase already uses
    elsewhere, applied to an inflow instead of an outflow:
    - payments.py::record_payment's "generate a new cash movement OR link an
      already-imported one" XOR shape (`cash_account_id` vs.
      `existing_cash_event_id`);
    - payments.py::record_payment's idempotency-key + UNIQUE(company,
      idempotency_key) replay/conflict mechanics (migration 021, mirroring
      017_obligation_payments.sql).

    "Não tratar empréstimo recebido como venda": the cash inflow is booked as
    a financial_entries row against the `loan_proceeds` system account
    (nature=financing_inflow, previously unused anywhere in this codebase),
    not any revenue-nature account — obligations.py's payable query already
    excludes financing_inflow entries, and this makes the inflow show up in
    reporting.py::management_result as financing, never as revenue.
    """
    if amount_cents <= 0:
        raise ValueError("O valor desembolsado deve ser maior que zero.")
    if not idempotency_key or not str(idempotency_key).strip():
        raise LoanValidationError(
            "Informe a chave de idempotência.", fields=["idempotency_key"]
        )
    idempotency_key = str(idempotency_key).strip()
    if cash_account_id is not None and existing_cash_event_id is not None:
        raise LoanValidationError(
            "Informe apenas uma origem de caixa (conta ou movimento existente).",
            fields=["cash_account_id", "existing_cash_event_id"],
        )
    if cash_account_id is None and existing_cash_event_id is None:
        raise LoanValidationError(
            "Informe a conta de caixa ou um movimento existente para o desembolso.",
            fields=["cash_account_id", "existing_cash_event_id"],
        )

    # accounts.account_by_key() opens its own connection (and may seed
    # default accounts) — must be resolved BEFORE this function opens its own
    # `with db.connection()` below, same rationale as
    # payments.py::record_payment's interest_account_id resolution.
    proceeds_account = accounts.account_by_key(company, "loan_proceeds")

    request_hash = _disbursement_request_hash(
        loan_id=loan_id,
        amount_cents=amount_cents,
        disbursed_at=disbursed_at,
        cash_account_id=cash_account_id,
        existing_cash_event_id=existing_cash_event_id,
    )

    try:
        with db.connection() as conn:
            # 1. Idempotency check — a genuine replay returns verbatim.
            existing = conn.execute(
                "SELECT * FROM loan_disbursements WHERE company=? AND idempotency_key=?",
                (company, idempotency_key),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise LoanConflictError(
                        "Chave de idempotência já usada com dados diferentes.",
                        fields=["idempotency_key"],
                    )
                return json.loads(existing["response_json"])

            loan = conn.execute(
                "SELECT * FROM loans WHERE id=? AND company=?", (loan_id, company)
            ).fetchone()
            if not loan:
                raise LoanNotFoundError("Empréstimo não encontrado.")
            if loan["status"] != "active":
                raise LoanConflictError("Empréstimo não está ativo.")

            # 2. Cash-link validation (existing_cash_event_id path only reads
            # here; the new-event path is created below).
            if existing_cash_event_id is not None:
                event = conn.execute(
                    "SELECT * FROM cash_events WHERE id=? AND company=?",
                    (existing_cash_event_id, company),
                ).fetchone()
                if not event:
                    raise LoanValidationError(
                        "Movimento de caixa não encontrado.",
                        fields=["existing_cash_event_id"],
                    )
                if event["amount_cents"] != amount_cents:
                    raise LoanValidationError(
                        "Movimento de caixa não corresponde ao valor desembolsado.",
                        fields=["existing_cash_event_id"],
                    )
                if conn.execute(
                    "SELECT 1 FROM cash_events WHERE reversed_event_id=?",
                    (existing_cash_event_id,),
                ).fetchone():
                    raise LoanValidationError(
                        "Movimento de caixa já foi estornado.",
                        fields=["existing_cash_event_id"],
                    )
                if conn.execute(
                    "SELECT 1 FROM loan_disbursements WHERE cash_event_id=?",
                    (existing_cash_event_id,),
                ).fetchone():
                    # Fast pre-check only — the race-proof guarantee is the
                    # UNIQUE index caught at the INSERT below (C4).
                    raise LoanValidationError(
                        _CASH_EVENT_ALLOCATED_MESSAGE,
                        fields=["existing_cash_event_id"],
                    )

            disbursement_id = _new_id()
            timestamp = db.now()
            description = f"Desembolso — {loan['lender']}"

            # 3. Create or link the cash movement.
            if cash_account_id is not None:
                cash_event = post_cash_event(
                    company,
                    cash_account_id,
                    amount_cents,
                    disbursed_at,
                    description,
                    created_by=actor_id,
                    conn=conn,
                )
                cash_event_id = cash_event["id"]
                owns_cash_event = 1
            else:
                cash_event_id = existing_cash_event_id
                owns_cash_event = 0

            # 4. Book the inflow against loan_proceeds (financing_inflow) and
            # settle it immediately — the money already arrived on
            # disbursed_at, there is no "open" period for this entry.
            entry = create_entry_on_connection(
                conn,
                EntryCommand(
                    company_id=company,
                    account_id=proceeds_account["id"],
                    amount_cents=amount_cents,
                    competence=disbursed_at.isoformat()[:7],
                    due_date=disbursed_at,
                    source="loan",
                    external_id=f"loan-disbursement:{disbursement_id}",
                    description=description,
                    created_by=actor_id,
                ),
            )
            settle_entry_on_connection(
                conn, entry["id"], amount_cents, paid_at=disbursed_at, created_by=actor_id
            )

            response = {
                "disbursement_id": str(disbursement_id),
                "loan_id": str(loan_id),
                "amount_cents": amount_cents,
                "disbursed_at": disbursed_at.isoformat(),
                "entry_id": str(entry["id"]),
                "cash_event_id": str(cash_event_id) if cash_event_id is not None else None,
            }
            try:
                conn.execute(
                    """
                    INSERT INTO loan_disbursements(
                        id,loan_id,company,amount_cents,disbursed_at,cash_event_id,
                        owns_cash_event,idempotency_key,request_hash,response_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        disbursement_id, loan_id, company, amount_cents,
                        disbursed_at.isoformat(), cash_event_id, owns_cash_event,
                        idempotency_key, request_hash,
                        json.dumps(response, ensure_ascii=False), timestamp,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - see C4 note above
                if not _is_cash_event_unique_violation(exc):
                    # Anything else (notably the idempotency-key UNIQUE) is
                    # left to the outer handler's replay recovery.
                    raise
                # Another transaction claimed this movement between our
                # pre-check and this INSERT. Raising aborts the whole
                # `with db.connection()` block, rolling back the cash event
                # and the proceeds entry created above — the loser leaves no
                # effects.
                raise LoanValidationError(
                    _CASH_EVENT_ALLOCATED_MESSAGE,
                    fields=["existing_cash_event_id"],
                ) from exc
            return response
    except LoanError:
        raise
    except Exception as exc:  # noqa: BLE001 - only recover a true idempotency race
        if not _is_unique_violation(exc):
            raise
        with db.connection() as conn2:
            row = conn2.execute(
                "SELECT * FROM loan_disbursements WHERE company=? AND idempotency_key=?",
                (company, idempotency_key),
            ).fetchone()
        if row is None:
            raise
        if row["request_hash"] != request_hash:
            raise LoanConflictError(
                "Chave de idempotência já usada com dados diferentes.",
                fields=["idempotency_key"],
            )
        return json.loads(row["response_json"])


def pay_installment_legacy_unsafe(
    installment_id: int,
    *,
    principal_cents: int,
    interest_cents: int,
    paid_at: date,
    created_by: Optional[int] = None,
) -> dict:
    """LEGACY / UNSAFE — do not call this for new code.

    This is the pre-B3 "pay an installment" implementation. It is no longer
    reachable from any HTTP route: both routes that used to call into it
    (`POST /entries/{id}/settlements` and `POST /loans/installments/{id}/payments`)
    were rewritten in task B3 to reject with 409, so live traffic never hits this
    function anymore. The real, safe payment path is
    `record_payment()` in `backend/finance/payments.py`, which delegates to
    `pay_installment_on_connection()` below.

    Why it still exists: it is still directly invoked by
    `tests/finance/test_loans.py`, `tests/finance/test_forecast.py`,
    `tests/test_obligations_api.py`, `tests/finance/test_obligations.py`, and
    `tests/finance/test_payments.py` as a fixture-setup shortcut (to create an
    already-paid installment without going through the full `record_payment`
    flow), and by `backfill_legacy_payments()`'s own historical-data assumptions.
    It is kept, renamed and documented here rather than deleted, so its
    divergence from the safe path is visible to the next reader instead of silent.

    KNOWN BUG — do not use for anything that matters: unlike
    `pay_installment_on_connection`, this function does **not** validate that
    `principal_cents`/`interest_cents` stay within the installment's remaining
    per-component balance. It unconditionally marks the installment `status='paid'`
    after a single call, even if the amounts paid don't actually cover the
    installment's remaining principal/interest. `pay_installment_on_connection`
    fixes this (partial payments, per-component balance checks, version-gated
    concurrency) and should be used for any real payment recording.
    """
    if principal_cents < 0 or interest_cents < 0:
        raise ValueError("Os valores pagos não podem ser negativos.")
    with db.connection() as conn:
        installment = conn.execute(
            "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
        ).fetchone()
        if not installment:
            raise ValueError("Parcela não encontrada.")
        if installment["status"] == "paid":
            raise ValueError("Parcela já paga.")
        loan = conn.execute(
            "SELECT * FROM loans WHERE id=?", (installment["loan_id"],)
        ).fetchone()

    entry_id = None
    if interest_cents:
        interest_account = accounts.account_by_key(loan["company"], "loan_interest")
        entry = create_entry(
            EntryCommand(
                company_id=loan["company"],
                account_id=interest_account["id"],
                amount_cents=interest_cents,
                competence=paid_at.isoformat()[:7],
                due_date=paid_at,
                source="loan",
                external_id=f"loan-installment:{installment_id}",
                description=f"Juros — {loan['lender']} parcela {installment['number']}",
                created_by=created_by,
            )
        )
        settle_entry(entry["id"], interest_cents, paid_at=paid_at, created_by=created_by)
        entry_id = entry["id"]

    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            UPDATE loan_installments
            SET status='paid',paid_principal_cents=?,paid_interest_cents=?,paid_at=?,
                entry_id=?,updated_at=?
            WHERE id=?
            """,
            (principal_cents, interest_cents, paid_at.isoformat(), entry_id, timestamp, installment_id),
        )
        row = conn.execute(
            "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
        ).fetchone()
    return dict(row)


def pay_installment_on_connection(
    conn,
    installment_id: int,
    *,
    principal_cents: int,
    interest_cents: int,
    paid_at: date,
    created_by: Optional[int] = None,
    expected_version: Optional[int] = None,
    interest_entry_id: Optional[int] = None,
) -> dict:
    """Apply a (possibly partial) installment payment on the caller's
    connection/transaction — no commit here, and no interest-entry creation
    (that's the caller's job, sharing the same connection; see
    backend/finance/payments.py::record_payment, which is this function's
    only caller). Unlike the legacy `pay_installment_legacy_unsafe()` above, this:

    - accepts a partial payment (principal_cents/interest_cents need not
      cover the whole installment) and only flips status to 'paid' once both
      components reach zero balance, per the task brief's "aceitar parcial
      sem marcar quitada até zerar saldo";
    - validates principal_cents/interest_cents each stay within their own
      remaining component balance (the legacy function performs no such
      check — see test_loans.py::test_paying_an_installment_twice_fails,
      which relies on the legacy function accepting an over-payment on the
      first call, so that laxer behavior is deliberately preserved there and
      NOT shared with this stricter helper);
    - supports the same `expected_version` optimistic-concurrency gate as
      entries.settle_entry_on_connection (see that function's docstring for
      why the conditional UPDATE itself is the real concurrency guarantee,
      not just the earlier read).

    `interest_entry_id`, when given, is persisted onto the installment's
    `entry_id` column (via COALESCE, so it's only ever set once — the first
    payment that carries interest — and never overwritten by a later
    interest-less partial payment).
    """
    if principal_cents < 0 or interest_cents < 0:
        raise ValueError("Os valores pagos não podem ser negativos.")
    installment = conn.execute(
        "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
    ).fetchone()
    if not installment:
        raise ValueError("Parcela não encontrada.")
    if installment["status"] == "paid":
        raise ValueError("Parcela já paga.")
    paid_principal = int(installment["paid_principal_cents"] or 0)
    paid_interest = int(installment["paid_interest_cents"] or 0)
    open_principal = installment["principal_cents"] - paid_principal
    open_interest = installment["interest_cents"] - paid_interest
    if principal_cents > open_principal or interest_cents > open_interest:
        raise ValueError("O pagamento excede o saldo em aberto da parcela.")

    new_paid_principal = paid_principal + principal_cents
    new_paid_interest = paid_interest + interest_cents
    fully_paid = (
        new_paid_principal == installment["principal_cents"]
        and new_paid_interest == installment["interest_cents"]
    )
    status = "paid" if fully_paid else "partially_paid"
    timestamp = db.now()

    set_sql = (
        "status=?,paid_principal_cents=?,paid_interest_cents=?,paid_at=?,"
        "entry_id=COALESCE(?,entry_id),version=version+1,updated_at=?"
    )
    params = [
        status, new_paid_principal, new_paid_interest, paid_at.isoformat(),
        interest_entry_id, timestamp,
    ]
    if expected_version is None:
        conn.execute(
            f"UPDATE loan_installments SET {set_sql} WHERE id=?",
            (*params, installment_id),
        )
    else:
        changed = conn.execute(
            f"UPDATE loan_installments SET {set_sql} WHERE id=? AND version=?",
            (*params, installment_id, expected_version),
        )
        if changed.rowcount != 1:
            raise InstallmentVersionConflict("Versão desatualizada; recarregue a parcela.")

    row = conn.execute(
        "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
    ).fetchone()
    return dict(row)


def reverse_installment_payment_on_connection(
    conn,
    installment_id: int,
    *,
    principal_cents: int,
    interest_cents: int,
    expected_version: Optional[int] = None,
) -> dict:
    """Undo ONE specific payment's contribution to an installment's running
    `paid_principal_cents`/`paid_interest_cents` totals — decrements by
    exactly this payment's amounts, never resets to zero, so an earlier or
    later SEPARATE payment on the same installment (pay_installment_on_connection
    supports partial payments, so multiple obligation_payments rows can
    target one installment) is left untouched. This is the inverse of
    pay_installment_on_connection; its only caller is
    backend/finance/payments.py::reverse_payment.

    Any compensation for a shared interest financial_entries row (created by
    payments.py::_ensure_interest_settlement) is the caller's responsibility
    via entries.reverse_settlement_on_connection — this function only ever
    touches the loan_installments row itself.

    Same optimistic-concurrency contract as pay_installment_on_connection:
    when `expected_version` is given, the UPDATE is conditioned on it and a
    rowcount of 0 raises `InstallmentVersionConflict`.
    """
    if principal_cents < 0 or interest_cents < 0:
        raise ValueError("Os valores estornados não podem ser negativos.")
    installment = conn.execute(
        "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
    ).fetchone()
    if not installment:
        raise ValueError("Parcela não encontrada.")
    paid_principal = int(installment["paid_principal_cents"] or 0)
    paid_interest = int(installment["paid_interest_cents"] or 0)
    new_paid_principal = max(0, paid_principal - principal_cents)
    new_paid_interest = max(0, paid_interest - interest_cents)
    fully_paid = (
        new_paid_principal == installment["principal_cents"]
        and new_paid_interest == installment["interest_cents"]
    )
    if fully_paid:
        status = "paid"
    elif new_paid_principal > 0 or new_paid_interest > 0:
        status = "partially_paid"
    else:
        status = "open"
    timestamp = db.now()

    set_sql = (
        "status=?,paid_principal_cents=?,paid_interest_cents=?,version=version+1,updated_at=?"
    )
    params = [status, new_paid_principal, new_paid_interest, timestamp]
    if expected_version is None:
        conn.execute(
            f"UPDATE loan_installments SET {set_sql} WHERE id=?",
            (*params, installment_id),
        )
    else:
        changed = conn.execute(
            f"UPDATE loan_installments SET {set_sql} WHERE id=? AND version=?",
            (*params, installment_id, expected_version),
        )
        if changed.rowcount != 1:
            raise InstallmentVersionConflict("Versão desatualizada; recarregue a parcela.")

    row = conn.execute(
        "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
    ).fetchone()
    return dict(row)


def _principal_outstanding_on_connection(conn, loan) -> int:
    """What's still owed on `loan`'s principal, summed across EVERY
    installment ever created for it — not just the active schedule's. Paid
    installments on a PREVIOUSLY closed schedule (one or more renegotiations
    ago) still contribute their paid_principal_cents here, since that column
    is never reset/moved when a schedule closes (renegotiate only flips the
    schedule's own status and swaps the loan's active_schedule_id — see
    _insert_schedule/renegotiate below). This makes the calculation correct
    regardless of how many times the loan has already been renegotiated
    (task brief's self-review: "loan renegotiated more than once — does
    outstanding principal calculation still work?").

    Deliberately `loan_id`-scoped (not schedule-scoped): this is
    "principal_outstanding" per the task brief's own assertion
    (`sum(...) == principal_outstanding`), i.e. the loan's original
    principal_cents minus everything already amortized — NOT the closed
    schedule's own remaining balance, which is a different (and, once a
    schedule has been renegotiated more than once, generally smaller/wrong)
    number.
    """
    paid = conn.execute(
        "SELECT COALESCE(SUM(paid_principal_cents),0) AS paid FROM loan_installments WHERE loan_id=?",
        (loan["id"],),
    ).fetchone()["paid"]
    return loan["principal_cents"] - int(paid or 0)


def renegotiate(
    loan_id: int, installments: list, *, reason: str, actor_id: Optional[int] = None
) -> dict:
    """Close the active schedule and open a new one. Paid installments stay
    attached to the closed schedule — renegotiation only replaces what's
    still owed.

    Task B7 additions: the new schedule's principal must sum to exactly the
    loan's outstanding principal (not its original total principal — see
    `_principal_outstanding_on_connection`), and every installment in it is
    revalidated the same way `create_loan` validates a fresh schedule
    (`_validate_schedule_items`: types, quantities, dates, sum)."""
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise LoanValidationError("Informe o motivo da renegociação.", fields=["reason"])
    if len(clean_reason) > 500:
        raise LoanValidationError(
            "Motivo deve ter no máximo 500 caracteres.", fields=["reason"]
        )
    principal_sum = _validate_schedule_items(installments)

    timestamp = db.now()
    new_schedule_id = _new_id()
    with db.connection() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise LoanNotFoundError("Empréstimo não encontrado.")
        if loan["status"] != "active":
            raise LoanConflictError("Empréstimo não está ativo.")

        outstanding = _principal_outstanding_on_connection(conn, loan)
        if principal_sum != outstanding:
            raise LoanValidationError(
                "A soma do principal das novas parcelas deve ser igual ao saldo devedor.",
                fields=["installments"],
            )

        conn.execute(
            "UPDATE loan_schedules SET status='closed',reason=? WHERE id=?",
            (clean_reason, loan["active_schedule_id"]),
        )
        _insert_schedule(conn, new_schedule_id, loan_id, installments, timestamp)
        conn.execute(
            "UPDATE loans SET active_schedule_id=?,updated_at=?,version=version+1 WHERE id=?",
            (new_schedule_id, timestamp, loan_id),
        )
        after = dict(conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone())
        _insert_audit(
            conn,
            company=loan["company"],
            entity_type="loan",
            entity_id=loan_id,
            action="renegotiate",
            before=dict(loan),
            after=after,
            reason=clean_reason,
            actor_id=actor_id,
            timestamp=timestamp,
        )
        row = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    return dict(row)


_LOAN_EDITABLE_FIELDS = {"lender", "purpose"}


def update_loan_details(
    company: int,
    loan_id: int,
    patch: dict,
    *,
    expected_version: int,
    actor_id: Optional[int] = None,
) -> dict:
    """PATCH /loans/{id}: edit `lender`/`purpose` only, optimistic-locked by
    `expected_version` (same `UPDATE ... WHERE id=? AND version=?` pattern as
    loan_installments/financial_entries elsewhere in this codebase). Any
    value-bearing field (principal_cents, installments, dates, ...) is
    rejected here — changing what's actually owed must go through
    `renegotiate`, never this descriptive-edit endpoint."""
    if not patch:
        raise LoanValidationError("Nenhuma alteração informada.")
    unknown = sorted(set(patch) - _LOAN_EDITABLE_FIELDS)
    if unknown:
        raise LoanValidationError(
            "Campo não pode ser alterado por esta operação; utilize a renegociação "
            "para alterar valores do contrato.",
            fields=unknown,
        )

    timestamp = db.now()
    with db.connection() as conn:
        loan = conn.execute(
            "SELECT * FROM loans WHERE id=? AND company=?", (loan_id, company)
        ).fetchone()
        if not loan:
            raise LoanNotFoundError("Empréstimo não encontrado.")
        if loan["status"] != "active":
            raise LoanConflictError("Empréstimo não está ativo.")

        updates: dict = {}
        if "lender" in patch:
            clean_lender = (patch["lender"] or "").strip()
            if not clean_lender:
                raise LoanValidationError("Informe o credor.", fields=["lender"])
            updates["lender"] = clean_lender
        if "purpose" in patch:
            updates["purpose"] = (patch["purpose"] or "").strip()

        set_sql = ",".join(f"{column}=?" for column in updates)
        params: list = list(updates.values())
        params.append(timestamp)
        params.extend([loan_id, company, expected_version])
        changed = conn.execute(
            f"UPDATE loans SET {set_sql},updated_at=?,version=version+1 "
            "WHERE id=? AND company=? AND version=?",
            params,
        )
        if changed.rowcount != 1:
            raise LoanConflictError("Versão desatualizada; recarregue o contrato.")

        after = dict(conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone())
        _insert_audit(
            conn,
            company=company,
            entity_type="loan",
            entity_id=loan_id,
            action="update",
            before=dict(loan),
            after=after,
            reason="",
            actor_id=actor_id,
            timestamp=timestamp,
        )
    return loan_position(loan_id)


def cancel_loan(
    company: int, loan_id: int, *, reason: str, actor_id: Optional[int] = None
) -> dict:
    """Cancel a loan contract that has NO recorded movement whatsoever (no
    payment against any installment, no disbursement) — "não apagar contrato
    com pagamentos; cancelar contrato sem movimentação exige motivo e
    atualização da agenda". There is no delete route for loans (a loan with
    payments can never be removed); this is the only terminal transition, and
    it is refused outright once any movement exists.

    "Atualização da agenda": the active schedule is marked status='cancelled'
    (a new terminal value alongside 'active'/'closed'). Both
    obligations.py::_loan_installment_rows and
    forecast.py::_open_installments_by_due_date_on_connection already gate
    their installment query on `s.status='active'` (see this task's
    investigation of obligations.py:
    no code change was needed there for this reason — a non-'active' schedule
    is already invisible to both the payable list and the forecast, exactly
    like a renegotiated-away closed schedule already is). Individual
    installment rows are left untouched (still 'open') — their own schedule
    no longer being 'active' is what removes them, mirroring the exact
    mechanism renegotiate() already relies on for a closed schedule.
    """
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise LoanValidationError("Informe o motivo do cancelamento.", fields=["reason"])
    if len(clean_reason) > 500:
        raise LoanValidationError(
            "Motivo deve ter no máximo 500 caracteres.", fields=["reason"]
        )

    timestamp = db.now()
    with db.connection() as conn:
        loan = conn.execute(
            "SELECT * FROM loans WHERE id=? AND company=?", (loan_id, company)
        ).fetchone()
        if not loan:
            raise LoanNotFoundError("Empréstimo não encontrado.")
        if loan["status"] == "cancelled":
            raise LoanConflictError("Empréstimo já está cancelado.")

        has_payment = conn.execute(
            """
            SELECT 1 FROM loan_installments
            WHERE loan_id=? AND (
                COALESCE(paid_principal_cents,0)>0 OR COALESCE(paid_interest_cents,0)>0
                OR status!='open'
            )
            LIMIT 1
            """,
            (loan_id,),
        ).fetchone()
        has_disbursement = conn.execute(
            "SELECT 1 FROM loan_disbursements WHERE loan_id=? LIMIT 1", (loan_id,)
        ).fetchone()
        if has_payment or has_disbursement:
            raise LoanConflictError(
                "Empréstimo com pagamento ou desembolso registrado não pode ser cancelado."
            )

        conn.execute(
            "UPDATE loans SET status='cancelled',updated_at=?,version=version+1 WHERE id=?",
            (timestamp, loan_id),
        )
        conn.execute(
            "UPDATE loan_schedules SET status='cancelled',reason=? WHERE id=?",
            (clean_reason, loan["active_schedule_id"]),
        )
        after = dict(conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone())
        _insert_audit(
            conn,
            company=company,
            entity_type="loan",
            entity_id=loan_id,
            action="cancel",
            before=dict(loan),
            after=after,
            reason=clean_reason,
            actor_id=actor_id,
            timestamp=timestamp,
        )
    return loan_position(loan_id)


def loan_position(loan_id: int) -> dict:
    with db.connection() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise ValueError("Empréstimo não encontrado.")
        installments = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM loan_installments
                WHERE loan_id=? AND (schedule_id=? OR status='paid')
                ORDER BY due_date,number
                """,
                (loan_id, loan["active_schedule_id"]),
            )
        ]
    # Final-review C3: both sums used to filter on a status label
    # (`=='paid'` / `=='open'`), which predates task B3's
    # `status='partially_paid'`. A partially-paid installment matched
    # NEITHER, so its money disappeared from both sides of the position at
    # once — a loan with a real partial payment reported
    # `principal_cents: 0, paid_principal_cents: 0`, and web/assets/loans.js
    # rendered "Principal em aberto: R$ 0,00" for a loan that still owed
    # money. Compute from the AMOUNTS instead of the labels.
    #
    # `paid_principal` sums every installment returned above (the active
    # schedule's rows plus any already-paid row from a superseded schedule —
    # see the query's `OR status='paid'`), matching
    # _principal_outstanding_on_connection's own loan-wide view of amortized
    # principal.
    #
    # `open_principal` is restricted to the ACTIVE schedule and to rows that
    # still owe something by definition ('open'/'partially_paid'): a
    # superseded schedule's leftover row was already replaced by the new
    # schedule's installments, so counting it would double-count the same
    # debt, and a 'paid' row owes nothing regardless of what its
    # paid_principal_cents column says (pay_installment_legacy_unsafe can
    # mark a row 'paid' while having recorded less than its full principal —
    # see that function's documented KNOWN BUG).
    active_schedule_id = loan["active_schedule_id"]
    paid_principal = sum(int(i["paid_principal_cents"] or 0) for i in installments)
    open_principal = sum(
        max(0, int(i["principal_cents"]) - int(i["paid_principal_cents"] or 0))
        for i in installments
        if i["schedule_id"] == active_schedule_id
        and i["status"] in ("open", "partially_paid")
    )
    return {
        "loan": dict(loan),
        "installments": installments,
        "principal_cents": open_principal,
        "paid_principal_cents": paid_principal,
    }
