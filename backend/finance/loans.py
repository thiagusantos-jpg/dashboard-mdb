from __future__ import annotations

import secrets
from datetime import date
from typing import Optional

from .. import database as db
from . import accounts
from .entries import EntryCommand, create_entry, settle_entry


def _new_id() -> int:
    return secrets.randbits(63) or 1


class InstallmentVersionConflict(ValueError):
    """Raised by pay_installment_on_connection when the optimistic-concurrency
    version check fails. See entries.EntryVersionConflict for the rationale;
    subclasses ValueError so a bare `except ValueError` still catches it."""


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
    if not installments:
        raise ValueError("Informe ao menos uma parcela.")
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


def record_disbursement(loan_id: int, amount_cents: int, disbursed_at: date) -> dict:
    if amount_cents <= 0:
        raise ValueError("O valor desembolsado deve ser maior que zero.")
    disbursement_id = _new_id()
    timestamp = db.now()
    with db.connection() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise ValueError("Empréstimo não encontrado.")
        conn.execute(
            """
            INSERT INTO loan_disbursements(id,loan_id,amount_cents,disbursed_at,created_at)
            VALUES(?,?,?,?,?)
            """,
            (disbursement_id, loan_id, amount_cents, disbursed_at.isoformat(), timestamp),
        )
        row = conn.execute(
            "SELECT * FROM loan_disbursements WHERE id=?", (disbursement_id,)
        ).fetchone()
    return dict(row)


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


def renegotiate(loan_id: int, installments: list, *, reason: str) -> dict:
    """Close the active schedule and open a new one. Paid installments stay
    attached to the closed schedule — renegotiation only replaces what's still owed."""
    timestamp = db.now()
    new_schedule_id = _new_id()
    with db.connection() as conn:
        loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
        if not loan:
            raise ValueError("Empréstimo não encontrado.")
        conn.execute(
            "UPDATE loan_schedules SET status='closed',reason=? WHERE id=?",
            (reason.strip(), loan["active_schedule_id"]),
        )
        _insert_schedule(conn, new_schedule_id, loan_id, installments, timestamp)
        conn.execute(
            "UPDATE loans SET active_schedule_id=?,updated_at=?,version=version+1 WHERE id=?",
            (new_schedule_id, timestamp, loan_id),
        )
        row = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    return dict(row)


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
    paid_principal = sum(i["paid_principal_cents"] or 0 for i in installments if i["status"] == "paid")
    open_principal = sum(i["principal_cents"] for i in installments if i["status"] == "open")
    return {
        "loan": dict(loan),
        "installments": installments,
        "principal_cents": open_principal,
        "paid_principal_cents": paid_principal,
    }
