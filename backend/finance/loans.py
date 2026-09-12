from __future__ import annotations

import secrets
from datetime import date
from typing import Optional

from .. import database as db
from . import accounts
from .entries import EntryCommand, create_entry, settle_entry


def _new_id() -> int:
    return secrets.randbits(63) or 1


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


def pay_installment(
    installment_id: int,
    *,
    principal_cents: int,
    interest_cents: int,
    paid_at: date,
    created_by: Optional[int] = None,
) -> dict:
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
