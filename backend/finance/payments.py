from __future__ import annotations

import hashlib
import json
import secrets
from datetime import date
from typing import Optional

from .. import database as db
from . import accounts, obligations
from .entries import (
    EntryCommand,
    EntryVersionConflict,
    _paid_cents,
    create_entry_on_connection,
    reverse_settlement_on_connection,
    settle_entry_on_connection,
)
from .entry_management import _insert_audit
from .ledger import post_cash_event, reverse_event
from .loans import (
    InstallmentVersionConflict,
    pay_installment_on_connection,
    reverse_installment_payment_on_connection,
)


_KINDS = ("entry", "loan_installment")

# Message required verbatim by the task brief for a legacy payment call
# (POST /entries/{id}/settlements, POST /loans/installments/{id}/payments)
# that carries no cash-account link — those endpoints' request bodies never
# had cash_account_id/existing_cash_event_id fields, so every call reaching
# them structurally lacks a link; rather than silently recording an
# untracked payment, we reject and point the user at the new flow.
CASH_LINK_REQUIRED_MESSAGE = "Atualize a página para registrar a conta de pagamento."


class PaymentError(Exception):
    """Base error for backend/finance/payments.py::record_payment."""

    def __init__(self, message: str, *, fields: Optional[list] = None):
        super().__init__(message)
        self.message = message
        self.fields = fields


class PaymentValidationError(PaymentError):
    """Invalid/inconsistent input (-> HTTP 422)."""


class PaymentNotFoundError(PaymentError):
    """Obligation not found within the given company scope (-> HTTP 404)."""


class PaymentConflictError(PaymentError):
    """Version/state/idempotency conflict (-> HTTP 409)."""


class PaymentCashLinkRequiredError(PaymentConflictError):
    """Neither cash_account_id nor existing_cash_event_id was supplied. Maps
    to HTTP 409 with CASH_LINK_REQUIRED_MESSAGE — this is how legacy payment
    routes forwarding into record_payment surface their rejection."""

    def __init__(self):
        super().__init__(CASH_LINK_REQUIRED_MESSAGE)


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _is_unique_violation(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in ("IntegrityError", "UniqueViolation"):
        return True
    return getattr(exc, "sqlstate", None) == "23505"


def _request_hash(
    *,
    kind: str,
    obligation_id: int,
    amount_cents: int,
    paid_at: date,
    cash_account_id: Optional[int],
    existing_cash_event_id: Optional[int],
    principal_cents: Optional[int],
    interest_cents: Optional[int],
) -> str:
    # Deliberately excludes expected_version: the brief requires a genuine
    # idempotent replay (same key, same meaningful body) to return the
    # original response "antes da checagem de versão" (before the version
    # check) even if expected_version supplied on retry is now stale.
    payload = {
        "kind": kind,
        "obligation_id": obligation_id,
        "amount_cents": amount_cents,
        "paid_at": paid_at.isoformat(),
        "cash_account_id": cash_account_id,
        "existing_cash_event_id": existing_cash_event_id,
        "principal_cents": principal_cents,
        "interest_cents": interest_cents,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _load_idempotent_row(conn, company: int, idempotency_key: str):
    return conn.execute(
        "SELECT * FROM obligation_payments WHERE company=? AND idempotency_key=?",
        (company, idempotency_key),
    ).fetchone()


def _replay_or_conflict(row, request_hash: str) -> dict:
    if row["request_hash"] != request_hash:
        raise PaymentConflictError(
            "Chave de idempotência já usada com dados diferentes.",
            fields=["idempotency_key"],
        )
    return json.loads(row["response_json"])


def _reversal_request_hash(*, payment_id: int, reason: str, reversed_at: date) -> str:
    # Deliberately excludes expected_version, mirroring _request_hash's own
    # rationale above: a genuine idempotent replay must return the original
    # response even if a retry's expected_version is now stale.
    payload = {
        "payment_id": payment_id,
        "reason": reason,
        "reversed_at": reversed_at.isoformat(),
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --- Obligation snapshots, computed on the SAME connection/transaction -----
#
# obligations.get_obligation()/list_obligations() always open their own
# connection, which would either see stale (pre-transaction) data or block
# against this module's in-flight write transaction. So the response
# payload's `obligation` item is built here, against `conn`, instead.
# `obligations._compute_status` / `_entry_allowed_actions` /
# `_installment_allowed_actions` are reused (pure functions, no DB access) to
# keep field semantics identical to B2's read path without duplicating them.
#
# NOTE (flagged in the task report): obligations.py's own
# `get_obligation(..., 'loan_installment', ...)` computes paid_cents as
# `paid_principal+paid_interest if status=='paid' else 0` — correct before
# B3, but wrong now that installments can sit at status='partially_paid'
# with a nonzero paid_cents. This module does NOT reuse that function for
# exactly that reason (in addition to the connection-isolation problem
# above); backend/finance/obligations.py itself was left untouched per this
# task's explicit instructions, so that bug still needs a follow-up fix
# there (and its list-side counterpart, `_loan_installment_rows`'s
# `WHERE i.status='open'` filter, which will hide a partially-paid
# installment from `list_obligations` entirely).


def _entry_obligation_item(conn, company: int, entry_id: int) -> dict:
    row = conn.execute(
        """
        SELECT e.id,e.version,e.description,e.due_date,e.competence,
               e.amount_cents,e.status,e.source,
               e.installment_number,e.installment_count
        FROM financial_entries e
        WHERE e.id=? AND e.company=?
        """,
        (entry_id, company),
    ).fetchone()
    if not row:
        raise PaymentNotFoundError("Lançamento não encontrado.")
    paid_cents = _paid_cents(conn, entry_id)
    open_cents = max(0, row["amount_cents"] - paid_cents)
    status = obligations._compute_status(row["status"], row["due_date"], open_cents)
    return {
        "key": f"entry:{row['id']}",
        "kind": "entry",
        "id": str(row["id"]),
        "version": int(row["version"]),
        "description": row["description"],
        "due_date": row["due_date"],
        "competence": row["competence"],
        "total_cents": row["amount_cents"],
        "paid_cents": paid_cents,
        "open_cents": open_cents,
        "status": status,
        "source": row["source"],
        "loan_id": None,
        "number": row["installment_number"],
        "count": row["installment_count"],
        "allowed_actions": obligations._entry_allowed_actions(
            row["status"], row["source"], open_cents
        ),
    }


def _loan_installment_obligation_item(conn, company: int, installment_id: int) -> dict:
    row = conn.execute(
        """
        SELECT i.id,i.number,i.due_date,i.total_cents,i.status,i.loan_id,i.version,
               i.paid_principal_cents,i.paid_interest_cents,l.lender,
               (SELECT COUNT(*) FROM loan_installments WHERE schedule_id=i.schedule_id) AS count
        FROM loan_installments i
        JOIN loans l ON l.id=i.loan_id
        WHERE i.id=? AND l.company=?
        """,
        (installment_id, company),
    ).fetchone()
    if not row:
        raise PaymentNotFoundError("Parcela não encontrada.")
    # Unlike obligations.get_obligation's loan_installment branch, paid_cents
    # is always the real sum — see the module-level NOTE above.
    paid_cents = int(row["paid_principal_cents"] or 0) + int(row["paid_interest_cents"] or 0)
    open_cents = max(0, row["total_cents"] - paid_cents)
    status = obligations._compute_status(row["status"], row["due_date"], open_cents)
    return {
        "key": f"loan_installment:{row['id']}",
        "kind": "loan_installment",
        "id": str(row["id"]),
        "version": int(row["version"]),
        "description": f"Parcela {row['number']}/{row['count']} — {row['lender']}",
        "due_date": row["due_date"],
        "competence": None,
        "total_cents": row["total_cents"],
        "paid_cents": paid_cents,
        "open_cents": open_cents,
        "status": status,
        "source": "loan",
        "loan_id": str(row["loan_id"]),
        "number": row["number"],
        "count": row["count"],
        "allowed_actions": obligations._installment_allowed_actions(open_cents),
    }


def _obligation_item(conn, company: int, kind: str, obligation_id: int) -> dict:
    if kind == "entry":
        return _entry_obligation_item(conn, company, obligation_id)
    return _loan_installment_obligation_item(conn, company, obligation_id)


# --- Interest entry composition for loan_installment payments --------------


def _ensure_interest_settlement(
    conn,
    *,
    company: int,
    installment: dict,
    loan: dict,
    interest_cents: int,
    paid_at: date,
    actor_id: Optional[int],
    interest_account_id: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """Create the installment's interest financial_entry exactly once (the
    first payment that carries interest_cents>0), settling THIS payment's
    contribution toward it; subsequent partial payments reuse and further
    settle the same entry. Mirrors the "despesa não recebe composição de
    empréstimo... juros registrados uma vez" rule from the brief — the
    interest entry's amount_cents is always the installment's FULL interest
    total, paid down incrementally like any other entry.

    Competence/due_date default to the installment's own due date ("padrão
    do vencimento da parcela"), not paid_at, which differs from the legacy
    loans.pay_installment_legacy_unsafe()'s behavior (paid_at's month) —
    legacy callers are unaffected since they don't go through this path.

    Returns (interest_entry_id, financial_event_id) — either may be None
    when interest_cents==0 for this call and no interest entry exists yet.
    """
    if interest_cents <= 0:
        return installment["entry_id"], None

    entry_id = installment["entry_id"]
    if not entry_id:
        due = date.fromisoformat(installment["due_date"])
        entry = create_entry_on_connection(
            conn,
            EntryCommand(
                company_id=company,
                account_id=interest_account_id,
                amount_cents=installment["interest_cents"],
                competence=installment["due_date"][:7],
                due_date=due,
                source="loan",
                external_id=f"loan-installment:{installment['id']}",
                description=f"Juros — {loan['lender']} parcela {installment['number']}",
                created_by=actor_id,
            ),
        )
        entry_id = entry["id"]

    event_id = settle_entry_on_connection(
        conn, entry_id, interest_cents, paid_at=paid_at, created_by=actor_id
    )
    return entry_id, event_id


# --- The main entry point ---------------------------------------------------


def record_payment(
    company: int,
    kind: str,
    obligation_id: int,
    *,
    amount_cents: int,
    paid_at: date,
    expected_version: int,
    idempotency_key: str,
    cash_account_id: Optional[int] = None,
    existing_cash_event_id: Optional[int] = None,
    principal_cents: Optional[int] = None,
    interest_cents: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> dict:
    if kind not in _KINDS:
        raise PaymentValidationError("Tipo de obrigação inválido.", fields=["kind"])
    if amount_cents is None or amount_cents <= 0:
        raise PaymentValidationError(
            "O valor deve ser maior que zero.", fields=["amount_cents"]
        )
    if not idempotency_key or not str(idempotency_key).strip():
        raise PaymentValidationError(
            "Informe a chave de idempotência.", fields=["idempotency_key"]
        )
    idempotency_key = str(idempotency_key).strip()

    if cash_account_id is not None and existing_cash_event_id is not None:
        raise PaymentValidationError(
            "Informe apenas uma origem de caixa (conta ou movimento existente).",
            fields=["cash_account_id", "existing_cash_event_id"],
        )
    if cash_account_id is None and existing_cash_event_id is None:
        raise PaymentCashLinkRequiredError()

    if kind == "entry":
        if principal_cents is not None or interest_cents is not None:
            raise PaymentValidationError(
                "Lançamento de despesa não recebe composição de principal/juros.",
                fields=["principal_cents", "interest_cents"],
            )
    else:
        if principal_cents is None or interest_cents is None:
            raise PaymentValidationError(
                "Informe principal e juros da parcela.",
                fields=["principal_cents", "interest_cents"],
            )
        if principal_cents < 0 or interest_cents < 0:
            raise PaymentValidationError(
                "Os valores pagos não podem ser negativos.",
                fields=["principal_cents", "interest_cents"],
            )
        if principal_cents + interest_cents != amount_cents:
            raise PaymentValidationError(
                "Principal + juros deve ser igual ao valor pago.",
                fields=["principal_cents", "interest_cents", "amount_cents"],
            )

    # accounts.account_by_key() opens its own connection/transaction
    # (and may seed default accounts), so it must be resolved BEFORE this
    # function opens its own `with db.connection()` below — calling it from
    # inside that transaction would nest a second sqlite3 connection against
    # the same file mid-write, which is unsafe. Only needed when this call
    # actually carries an interest amount (mirrors
    # loans.pay_installment_legacy_unsafe's own `if interest_cents:` guard).
    interest_account_id = None
    if kind == "loan_installment" and interest_cents:
        interest_account_id = accounts.account_by_key(company, "loan_interest")["id"]

    request_hash = _request_hash(
        kind=kind,
        obligation_id=obligation_id,
        amount_cents=amount_cents,
        paid_at=paid_at,
        cash_account_id=cash_account_id,
        existing_cash_event_id=existing_cash_event_id,
        principal_cents=principal_cents,
        interest_cents=interest_cents,
    )

    try:
        with db.connection() as conn:
            # 1. Idempotency check — a genuine replay returns verbatim,
            # skipping every check below (including a stale version).
            existing = _load_idempotent_row(conn, company, idempotency_key)
            if existing:
                return _replay_or_conflict(existing, request_hash)

            # 2/3. Load obligation, validate state, version and balance.
            if kind == "entry":
                entry = conn.execute(
                    "SELECT * FROM financial_entries WHERE id=? AND company=?",
                    (obligation_id, company),
                ).fetchone()
                if not entry:
                    raise PaymentNotFoundError("Lançamento não encontrado.")
                if entry["status"] in {"cancelled", "reversed"}:
                    raise PaymentConflictError("Este lançamento não pode ser pago.")
                if int(entry["version"]) != int(expected_version):
                    raise PaymentConflictError(
                        "Versão desatualizada; recarregue o lançamento."
                    )
                paid = _paid_cents(conn, obligation_id)
                open_cents = max(0, entry["amount_cents"] - paid)
                if amount_cents > open_cents:
                    raise PaymentValidationError(
                        "O pagamento excede o saldo em aberto.", fields=["amount_cents"]
                    )
                description = entry["description"]
                loan = None
                installment = None
            else:
                installment = conn.execute(
                    """
                    SELECT i.*, l.company AS loan_company FROM loan_installments i
                    JOIN loans l ON l.id=i.loan_id
                    WHERE i.id=?
                    """,
                    (obligation_id,),
                ).fetchone()
                if not installment or installment["loan_company"] != company:
                    raise PaymentNotFoundError("Parcela não encontrada.")
                installment = dict(installment)
                if installment["status"] == "paid":
                    raise PaymentConflictError("Parcela já paga.")
                if int(installment["version"]) != int(expected_version):
                    raise PaymentConflictError(
                        "Versão desatualizada; recarregue a parcela."
                    )
                paid_principal = int(installment["paid_principal_cents"] or 0)
                paid_interest = int(installment["paid_interest_cents"] or 0)
                open_principal = installment["principal_cents"] - paid_principal
                open_interest = installment["interest_cents"] - paid_interest
                if principal_cents > open_principal or interest_cents > open_interest:
                    raise PaymentValidationError(
                        "O pagamento excede o saldo em aberto da parcela.",
                        fields=["principal_cents", "interest_cents"],
                    )
                open_cents = open_principal + open_interest
                if amount_cents > open_cents:
                    raise PaymentValidationError(
                        "O pagamento excede o saldo em aberto.", fields=["amount_cents"]
                    )
                loan = conn.execute(
                    "SELECT * FROM loans WHERE id=?", (installment["loan_id"],)
                ).fetchone()
                description = (
                    f"Parcela {installment['number']} — {loan['lender']}"
                )

            # 4. Cash-link validation (existing_cash_event_id path only reads
            # here; the new-event path is created in step 6).
            if existing_cash_event_id is not None:
                event = conn.execute(
                    "SELECT * FROM cash_events WHERE id=? AND company=?",
                    (existing_cash_event_id, company),
                ).fetchone()
                if not event:
                    raise PaymentValidationError(
                        "Movimento de caixa não encontrado.",
                        fields=["existing_cash_event_id"],
                    )
                if event["amount_cents"] != -amount_cents:
                    raise PaymentValidationError(
                        "Movimento de caixa não corresponde ao valor pago.",
                        fields=["existing_cash_event_id"],
                    )
                if conn.execute(
                    "SELECT 1 FROM cash_events WHERE reversed_event_id=?",
                    (existing_cash_event_id,),
                ).fetchone():
                    raise PaymentValidationError(
                        "Movimento de caixa já foi estornado.",
                        fields=["existing_cash_event_id"],
                    )
                if conn.execute(
                    """
                    SELECT 1 FROM obligation_payments
                    WHERE cash_event_id=? AND reversed_at IS NULL
                    """,
                    (existing_cash_event_id,),
                ).fetchone():
                    raise PaymentValidationError(
                        "Movimento de caixa já está vinculado a outro pagamento.",
                        fields=["existing_cash_event_id"],
                    )

            # 5. Update the underlying entry/installment — the
            # version-conditioned UPDATE inside these helpers is the real
            # concurrency gate (see entries.settle_entry_on_connection).
            financial_event_id = None
            interest_entry_id = None
            if kind == "entry":
                try:
                    financial_event_id = settle_entry_on_connection(
                        conn,
                        obligation_id,
                        amount_cents,
                        paid_at=paid_at,
                        created_by=actor_id,
                        expected_version=expected_version,
                    )
                except EntryVersionConflict as exc:
                    raise PaymentConflictError(str(exc)) from exc
                # settle_entry_on_connection returns only the event id, not a
                # row — fetch the post-payment raw row ourselves so the audit
                # trail can store a symmetric before/after pair (see step 7).
                after_raw = dict(
                    conn.execute(
                        "SELECT * FROM financial_entries WHERE id=?", (obligation_id,)
                    ).fetchone()
                )
            else:
                interest_entry_id, financial_event_id = _ensure_interest_settlement(
                    conn,
                    company=company,
                    installment=installment,
                    loan=dict(loan),
                    interest_cents=interest_cents,
                    paid_at=paid_at,
                    actor_id=actor_id,
                    interest_account_id=interest_account_id,
                )
                try:
                    # pay_installment_on_connection returns the post-payment
                    # raw loan_installments row — reused below (step 7) as the
                    # audit trail's `after` snapshot.
                    after_raw = pay_installment_on_connection(
                        conn,
                        obligation_id,
                        principal_cents=principal_cents,
                        interest_cents=interest_cents,
                        paid_at=paid_at,
                        created_by=actor_id,
                        expected_version=expected_version,
                        interest_entry_id=interest_entry_id,
                    )
                except InstallmentVersionConflict as exc:
                    raise PaymentConflictError(str(exc)) from exc

            # 6. Create or link the cash movement.
            if cash_account_id is not None:
                cash_event = post_cash_event(
                    company,
                    cash_account_id,
                    -amount_cents,
                    paid_at,
                    f"Pagamento — {description}",
                    entry_id=(obligation_id if kind == "entry" else None),
                    created_by=actor_id,
                    conn=conn,
                )
                cash_event_id = cash_event["id"]
                owns_cash_event = 1
            else:
                cash_event_id = existing_cash_event_id
                owns_cash_event = 0

            # 7. Audit trail — reuses B1's finance_audit mechanism. For an
            # entry payment this also makes the payment show up in the
            # existing GET /entries/{id}/history endpoint (entry_history()
            # filters entity_type='financial_entry').
            #
            # `before`/`after` are both raw table-row snapshots (financial_entries
            # or loan_installments), matching the symmetric shape
            # entry_management.update_entry()/cancel_entry() already use for
            # their own audit rows (see entry_management.py's `_insert_audit`
            # calls) — NOT the `_obligation_item`-shaped dict built below for
            # the HTTP response (`after_item`), which uses a different field
            # vocabulary (amount_cents vs total_cents/paid_cents/open_cents,
            # string ids, allowed_actions/key with no `before` counterpart).
            # Keeping the audit trail symmetric lets a UI render a field-level
            # diff the same way for every action, "pay" included.
            entity_type = "financial_entry" if kind == "entry" else "loan_installment"
            before_snapshot = dict(entry) if kind == "entry" else installment
            after_item = _obligation_item(conn, company, kind, obligation_id)
            timestamp = db.now()
            _insert_audit(
                conn,
                company=company,
                entity_type=entity_type,
                entity_id=obligation_id,
                action="pay",
                before=before_snapshot,
                after=after_raw,
                reason="",
                actor_id=actor_id,
                timestamp=timestamp,
            )

            # 8/9. Build the response and persist the payment row.
            payment_id = _new_id()
            response = {
                "payment_id": str(payment_id),
                "obligation": after_item,
                "cash_event_id": str(cash_event_id) if cash_event_id is not None else None,
            }
            conn.execute(
                """
                INSERT INTO obligation_payments(
                    id,company,obligation_kind,obligation_id,amount_cents,
                    principal_cents,interest_cents,paid_at,cash_event_id,
                    owns_cash_event,financial_event_id,interest_entry_id,
                    idempotency_key,request_hash,response_json,reversed_at,
                    created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payment_id, company, kind, obligation_id, amount_cents,
                    principal_cents, interest_cents, paid_at.isoformat(),
                    cash_event_id, owns_cash_event, financial_event_id,
                    interest_entry_id, idempotency_key, request_hash,
                    json.dumps(response, ensure_ascii=False), None,
                    actor_id, timestamp,
                ),
            )
            return response
    except PaymentError:
        raise
    except Exception as exc:  # noqa: BLE001 - only recover a true idempotency race
        if not _is_unique_violation(exc):
            raise
        with db.connection() as conn2:
            row = _load_idempotent_row(conn2, company, idempotency_key)
        if row is None:
            raise
        return _replay_or_conflict(row, request_hash)


# --- Undoing one specific payment, without cancelling the obligation -------


def _reversal_replay_or_conflict(row, request_hash: str) -> dict:
    if row["reversal_request_hash"] != request_hash:
        raise PaymentConflictError(
            "Chave de idempotência já usada com dados diferentes.",
            fields=["idempotency_key"],
        )
    return json.loads(row["reversal_response_json"])


def reverse_payment(
    company: int,
    payment_id: int,
    *,
    reason: str,
    reversed_at: date,
    expected_version: int,
    idempotency_key: str,
    actor_id: Optional[int] = None,
) -> dict:
    """Undo ONE specific `obligation_payments` row without cancelling the
    whole obligation: the entry/installment reopens to its pre-payment
    balance (only THIS payment's contribution is netted back out — other,
    separate payments against the same obligation are untouched), and:

    - a cash_event this payment OWNED (`owns_cash_event=1`) receives a
      compensating inverse cash_events row (ledger.reverse_event) — never
      deleted/mutated;
    - a cash_event this payment merely LINKED to an externally-imported bank
      transaction (`owns_cash_event=0`) is left completely untouched; it
      becomes available for a future allocation again purely because this
      payment's own `reversed_at` gets set (see record_payment's
      existing_cash_event_id "already allocated" check, which filters on
      `obligation_payments.reversed_at IS NULL`) — there is no separate
      "unlink" step.

    Deliberately does NOT reuse entries.reverse_entry(), which reverts an
    entry's ENTIRE remaining balance and permanently marks it 'reversed' —
    wrong for undoing one payment among possibly several partial ones. See
    entries.reverse_settlement_on_connection / loans.
    reverse_installment_payment_on_connection for the actual (inverse,
    partial) mechanics.

    A payment whose cash_event is already linked into a live reconciliation
    (backend/finance/reconciliation.py) is rejected (409) — the caller must
    call reconciliation.undo() explicitly first; this function never does
    that automatically.

    Idempotent: `idempotency_key` is a SEPARATE namespace from the
    payment's own creation key (stored in the same row's
    `reversal_idempotency_key` column, following the identical replay/
    conflict pattern as `record_payment`'s own idempotency check above).
    Reversing an already-reversed payment (`reversed_at IS NOT NULL`) with a
    DIFFERENT idempotency key is a distinct 409 ("dupla reversão"), not a
    replay.
    """
    clean_reason = (reason or "").strip()
    if not (3 <= len(clean_reason) <= 500):
        raise PaymentValidationError(
            "O motivo deve ter entre 3 e 500 caracteres.", fields=["reason"]
        )
    if not idempotency_key or not str(idempotency_key).strip():
        raise PaymentValidationError(
            "Informe a chave de idempotência.", fields=["idempotency_key"]
        )
    idempotency_key = str(idempotency_key).strip()

    request_hash = _reversal_request_hash(
        payment_id=payment_id, reason=clean_reason, reversed_at=reversed_at
    )

    try:
        with db.connection() as conn:
            # 1. Idempotency lookup — a genuine replay returns verbatim,
            # skipping every check below (including a stale version), same
            # semantics as record_payment's own check.
            existing = conn.execute(
                """
                SELECT * FROM obligation_payments
                WHERE company=? AND reversal_idempotency_key=?
                """,
                (company, idempotency_key),
            ).fetchone()
            if existing:
                return _reversal_replay_or_conflict(existing, request_hash)

            # 2. Load the payment row and validate its own state.
            payment = conn.execute(
                "SELECT * FROM obligation_payments WHERE id=? AND company=?",
                (payment_id, company),
            ).fetchone()
            if not payment:
                raise PaymentNotFoundError("Pagamento não encontrado.")
            if payment["reversed_at"] is not None:
                raise PaymentConflictError("Este pagamento já foi estornado.")

            # 3. Reconciliation lock — "bloquear pagamento conciliado até
            # desfazer conciliação explicitamente". A link row only exists
            # while the reconciliation group is live; reconciliation.undo()
            # deletes it, which is what frees this payment for reversal.
            if payment["cash_event_id"] is not None:
                linked = conn.execute(
                    """
                    SELECT 1 FROM reconciliation_links
                    WHERE item_type='cash_event' AND item_id=?
                    """,
                    (payment["cash_event_id"],),
                ).fetchone()
                if linked:
                    raise PaymentConflictError(
                        "Este pagamento está conciliado. Desfaça a conciliação "
                        "antes de estornar o pagamento.",
                        fields=["cash_event_id"],
                    )

            kind = payment["obligation_kind"]
            obligation_id = payment["obligation_id"]

            # 4. Undo the entry's/installment's OWN contribution — the
            # version-conditioned UPDATE inside these helpers is the real
            # concurrency gate (mirrors record_payment's step 5).
            if kind == "entry":
                entry = conn.execute(
                    "SELECT * FROM financial_entries WHERE id=? AND company=?",
                    (obligation_id, company),
                ).fetchone()
                if not entry:
                    raise PaymentNotFoundError("Lançamento não encontrado.")
                try:
                    reverse_settlement_on_connection(
                        conn,
                        obligation_id,
                        payment["amount_cents"],
                        reversed_at=reversed_at,
                        reason=clean_reason,
                        created_by=actor_id,
                        expected_version=expected_version,
                    )
                except EntryVersionConflict as exc:
                    raise PaymentConflictError(str(exc)) from exc
                except ValueError as exc:
                    raise PaymentConflictError(str(exc)) from exc
                after_raw = dict(
                    conn.execute(
                        "SELECT * FROM financial_entries WHERE id=?", (obligation_id,)
                    ).fetchone()
                )
                before_snapshot = dict(entry)
                entity_type = "financial_entry"
            else:
                installment = conn.execute(
                    """
                    SELECT i.*, l.company AS loan_company FROM loan_installments i
                    JOIN loans l ON l.id=i.loan_id
                    WHERE i.id=?
                    """,
                    (obligation_id,),
                ).fetchone()
                if not installment or installment["loan_company"] != company:
                    raise PaymentNotFoundError("Parcela não encontrada.")
                installment = dict(installment)

                # Undo any interest-entry settlement THIS payment
                # contributed. The interest entry is a SHARED
                # financial_entries row (payments.py::_ensure_interest_settlement
                # creates it once, then multiple partial installment
                # payments each settle their own slice of it) — a payment's
                # own `financial_event_id` is only set when THAT call
                # actually settled interest, so a payment that paid only
                # principal (financial_event_id NULL) leaves the shared
                # entry untouched here.
                if payment["financial_event_id"] is not None and payment["interest_entry_id"] is not None:
                    interest_entry = conn.execute(
                        "SELECT * FROM financial_entries WHERE id=?",
                        (payment["interest_entry_id"],),
                    ).fetchone()
                    if interest_entry and interest_entry["status"] not in {"cancelled", "reversed"}:
                        # No caller-supplied version for the shared interest
                        # entry: reverse_payment's single expected_version
                        # parameter guards the installment itself, mirroring
                        # how _ensure_interest_settlement's own
                        # settle_entry_on_connection call carries no
                        # expected_version either.
                        try:
                            reverse_settlement_on_connection(
                                conn,
                                payment["interest_entry_id"],
                                payment["interest_cents"] or 0,
                                reversed_at=reversed_at,
                                reason=clean_reason,
                                created_by=actor_id,
                            )
                        except ValueError as exc:
                            raise PaymentConflictError(str(exc)) from exc

                try:
                    after_raw = reverse_installment_payment_on_connection(
                        conn,
                        obligation_id,
                        principal_cents=payment["principal_cents"] or 0,
                        interest_cents=payment["interest_cents"] or 0,
                        expected_version=expected_version,
                    )
                except InstallmentVersionConflict as exc:
                    raise PaymentConflictError(str(exc)) from exc
                before_snapshot = installment
                entity_type = "loan_installment"

            # 5. Compensate the cash side. A cash_event this payment OWNED
            # gets a genuine inverse entry (never deleted/mutated); one it
            # merely LINKED (an imported bank transaction) is left alone —
            # clearing this row's reversed_at below is what frees it for a
            # future allocation.
            if payment["owns_cash_event"] and payment["cash_event_id"] is not None:
                reverse_event(
                    payment["cash_event_id"],
                    reason=clean_reason,
                    created_by=actor_id,
                    occurred_at=reversed_at,
                    conn=conn,
                )

            # 6. Audit trail — same symmetric raw-row before/after
            # convention as record_payment's "pay" action (see that
            # function's step 7 for the rationale).
            after_item = _obligation_item(conn, company, kind, obligation_id)
            timestamp = db.now()
            _insert_audit(
                conn,
                company=company,
                entity_type=entity_type,
                entity_id=obligation_id,
                action="reverse_payment",
                before=before_snapshot,
                after=after_raw,
                reason=clean_reason,
                actor_id=actor_id,
                timestamp=timestamp,
            )

            # 7. Build the response and persist the reversal onto the SAME
            # obligation_payments row (no new row — a payment is reversed
            # at most once, so a 1:1 extension of the payment row is
            # simpler than a separate table).
            response = {
                "payment_id": str(payment_id),
                "obligation": after_item,
                "cash_event_id": (
                    str(payment["cash_event_id"])
                    if payment["cash_event_id"] is not None
                    else None
                ),
                "reversed_at": reversed_at.isoformat(),
            }
            conn.execute(
                """
                UPDATE obligation_payments
                SET reversed_at=?, reversal_reason=?, reversed_by=?,
                    reversal_idempotency_key=?, reversal_request_hash=?,
                    reversal_response_json=?
                WHERE id=?
                """,
                (
                    reversed_at.isoformat(), clean_reason, actor_id,
                    idempotency_key, request_hash,
                    json.dumps(response, ensure_ascii=False), payment_id,
                ),
            )
            return response
    except PaymentError:
        raise
    except Exception as exc:  # noqa: BLE001 - only recover a true idempotency race
        if not _is_unique_violation(exc):
            raise
        with db.connection() as conn2:
            row = conn2.execute(
                """
                SELECT * FROM obligation_payments
                WHERE company=? AND reversal_idempotency_key=?
                """,
                (company, idempotency_key),
            ).fetchone()
        if row is None:
            raise
        return _reversal_replay_or_conflict(row, request_hash)


# --- Backfill of historical payments ----------------------------------------


def backfill_legacy_payments(company: Optional[int] = None) -> dict:
    """One-time backfill that creates `obligation_payments` rows referencing
    already-settled financial_events and already-paid loan_installments, so
    the new obligation-payments table reflects payment history that predates
    this task. Idempotent by construction: stable synthetic keys
    (`legacy:event:<id>` / `legacy:installment:<id>`) collide with the
    UNIQUE(company,idempotency_key) constraint on a second run, so already-
    backfilled rows are skipped rather than duplicated (existing totals are
    never recomputed — "Não recalcular pagamentos antigos").

    `cash_event_id`/`owns_cash_event` are left NULL/false — there is no
    reliable historical link to a real cash movement — and the obligation's
    `allowed_actions` already expose nothing about this; a "Vínculo de caixa
    pendente" (pending cash link) indicator is exposed by the presence of a
    NULL cash_event_id on this row, which the frontend/B4 can surface.

    Returns {"entries": n, "installments": n} counts of NEWLY inserted rows
    (rows already backfilled on a prior run are not recounted).
    """
    company_filter = " AND e.company=?" if company is not None else ""
    installment_company_filter = " AND l.company=?" if company is not None else ""
    params_entries = (company,) if company is not None else ()
    params_installments = (company,) if company is not None else ()

    inserted_entries = 0
    inserted_installments = 0
    timestamp = db.now()

    with db.connection() as conn:
        # Loan-interest entries (source='loan', created by
        # loans.pay_installment_legacy_unsafe or by this module's own
        # _ensure_interest_settlement) are excluded
        # here: their settlement is bundled into the loan_installment payment
        # row below (financial_event_id/interest_entry_id), exactly like a
        # live record_payment(kind='loan_installment') call does — one
        # obligation_payments row per installment payment, not two.
        settled_events = conn.execute(
            f"""
            SELECT v.id AS event_id, v.entry_id, v.amount_cents, v.occurred_at,
                   v.created_by, e.company
            FROM financial_events v
            JOIN financial_entries e ON e.id=v.entry_id
            WHERE v.event_type='settled' AND e.source!='loan'{company_filter}
            ORDER BY v.id
            """,
            params_entries,
        ).fetchall()
        for row in settled_events:
            key = f"legacy:event:{row['event_id']}"
            if conn.execute(
                "SELECT 1 FROM obligation_payments WHERE company=? AND idempotency_key=?",
                (row["company"], key),
            ).fetchone():
                continue
            payment_id = _new_id()
            response = {
                "payment_id": str(payment_id),
                "obligation": None,
                "cash_event_id": None,
            }
            conn.execute(
                """
                INSERT INTO obligation_payments(
                    id,company,obligation_kind,obligation_id,amount_cents,
                    principal_cents,interest_cents,paid_at,cash_event_id,
                    owns_cash_event,financial_event_id,interest_entry_id,
                    idempotency_key,request_hash,response_json,reversed_at,
                    created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payment_id, row["company"], "entry", row["entry_id"],
                    row["amount_cents"], None, None, row["occurred_at"],
                    None, 0, row["event_id"], None, key, "legacy",
                    json.dumps(response, ensure_ascii=False), None,
                    row["created_by"], timestamp,
                ),
            )
            inserted_entries += 1

        paid_installments = conn.execute(
            f"""
            SELECT i.id AS installment_id, i.paid_principal_cents, i.paid_interest_cents,
                   i.paid_at, i.entry_id, l.company
            FROM loan_installments i
            JOIN loans l ON l.id=i.loan_id
            WHERE i.status='paid'{installment_company_filter}
            ORDER BY i.id
            """,
            params_installments,
        ).fetchall()
        for row in paid_installments:
            key = f"legacy:installment:{row['installment_id']}"
            if conn.execute(
                "SELECT 1 FROM obligation_payments WHERE company=? AND idempotency_key=?",
                (row["company"], key),
            ).fetchone():
                continue
            principal = int(row["paid_principal_cents"] or 0)
            interest = int(row["paid_interest_cents"] or 0)
            payment_id = _new_id()
            response = {
                "payment_id": str(payment_id),
                "obligation": None,
                "cash_event_id": None,
            }
            conn.execute(
                """
                INSERT INTO obligation_payments(
                    id,company,obligation_kind,obligation_id,amount_cents,
                    principal_cents,interest_cents,paid_at,cash_event_id,
                    owns_cash_event,financial_event_id,interest_entry_id,
                    idempotency_key,request_hash,response_json,reversed_at,
                    created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payment_id, row["company"], "loan_installment",
                    row["installment_id"], principal + interest, principal,
                    interest, row["paid_at"] or timestamp, None, 0, None,
                    row["entry_id"], key, "legacy",
                    json.dumps(response, ensure_ascii=False), None,
                    None, timestamp,
                ),
            )
            inserted_installments += 1

    return {"entries": inserted_entries, "installments": inserted_installments}
