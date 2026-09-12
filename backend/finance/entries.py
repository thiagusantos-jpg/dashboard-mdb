from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .. import database as db


VALID_STATUSES = {
    "forecast",
    "open",
    "partially_paid",
    "paid",
    "overdue",
    "cancelled",
    "reversed",
}


@dataclass(frozen=True)
class EntryCommand:
    company_id: int
    account_id: int
    amount_cents: int
    competence: str
    due_date: date
    source: str
    external_id: Optional[str]
    description: str
    store_id: Optional[int] = None
    counterparty_id: Optional[int] = None
    recurrence_id: Optional[int] = None
    installment_number: Optional[int] = None
    installment_count: Optional[int] = None
    notes: str = ""
    created_by: Optional[int] = None
    forecast: bool = False


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _validate_competence(competence: str) -> None:
    if len(competence) != 7:
        raise ValueError("Competência inválida.")
    date.fromisoformat(competence + "-01")


def create_entry(command: EntryCommand) -> dict:
    _validate_competence(command.competence)
    if command.amount_cents <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    description = command.description.strip()
    if not description:
        raise ValueError("Informe a descrição.")
    entry_id = _new_id()
    timestamp = db.now()
    status = "forecast" if command.forecast else "open"
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO financial_entries(
                id,company,store,account_id,counterparty_id,description,
                amount_cents,competence,due_date,status,source,external_id,
                recurrence_id,installment_number,installment_count,notes,
                created_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                entry_id,
                command.company_id,
                command.store_id or 0,
                command.account_id,
                command.counterparty_id,
                description,
                command.amount_cents,
                command.competence,
                command.due_date.isoformat(),
                status,
                command.source,
                command.external_id,
                command.recurrence_id,
                command.installment_number,
                command.installment_count,
                command.notes.strip(),
                command.created_by,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO financial_events(
                id,entry_id,event_type,occurred_at,created_by,created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (_new_id(), entry_id, "created", timestamp, command.created_by, timestamp),
        )
        row = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
    return dict(row)


def _paid_cents(conn, entry_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(
            CASE WHEN event_type='settled' THEN amount_cents
                 WHEN event_type='reversed' THEN -amount_cents ELSE 0 END
        ),0) AS paid
        FROM financial_events WHERE entry_id=?
        """,
        (entry_id,),
    ).fetchone()
    return int(row["paid"])


def get_entry(entry_id: int) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if not row:
            raise ValueError("Lançamento não encontrado.")
        result = dict(row)
        paid = _paid_cents(conn, entry_id)
    result["paid_cents"] = paid
    result["open_cents"] = max(0, result["amount_cents"] - paid)
    if result["status"] == "open" and result["due_date"] < date.today().isoformat():
        result["status"] = "overdue"
    return result


def settle_entry(
    entry_id: int,
    amount_cents: int,
    *,
    paid_at: date,
    created_by: Optional[int] = None,
) -> dict:
    if amount_cents <= 0:
        raise ValueError("O pagamento deve ser maior que zero.")
    with db.connection() as conn:
        entry = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if not entry:
            raise ValueError("Lançamento não encontrado.")
        if entry["status"] in {"cancelled", "reversed"}:
            raise ValueError("Este lançamento não pode ser pago.")
        paid = _paid_cents(conn, entry_id)
        if paid + amount_cents > entry["amount_cents"]:
            raise ValueError("O pagamento excede o saldo em aberto.")
        timestamp = db.now()
        conn.execute(
            """
            INSERT INTO financial_events(
                id,entry_id,event_type,amount_cents,occurred_at,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                _new_id(),
                entry_id,
                "settled",
                amount_cents,
                paid_at.isoformat(),
                created_by,
                timestamp,
            ),
        )
        status = "paid" if paid + amount_cents == entry["amount_cents"] else "partially_paid"
        conn.execute(
            """
            UPDATE financial_entries SET status=?,version=version+1,updated_at=?
            WHERE id=?
            """,
            (status, timestamp, entry_id),
        )
    return get_entry(entry_id)


def reverse_entry(
    entry_id: int,
    *,
    reversed_at: date,
    reason: str,
    created_by: Optional[int] = None,
) -> dict:
    with db.connection() as conn:
        entry = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if not entry or entry["status"] in {"cancelled", "reversed"}:
            raise ValueError("Lançamento não encontrado ou já revertido.")
        paid = _paid_cents(conn, entry_id)
        timestamp = db.now()
        conn.execute(
            """
            INSERT INTO financial_events(
                id,entry_id,event_type,amount_cents,occurred_at,reason,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                _new_id(),
                entry_id,
                "reversed",
                paid,
                reversed_at.isoformat(),
                reason.strip(),
                created_by,
                timestamp,
            ),
        )
        conn.execute(
            """
            UPDATE financial_entries SET status='reversed',version=version+1,updated_at=?
            WHERE id=?
            """,
            (timestamp, entry_id),
        )
    return get_entry(entry_id)


def result_for(company: int, competence: str) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(e.amount_cents),0) AS expenses
            FROM financial_entries e
            JOIN finance_accounts a ON a.id=e.account_id
            WHERE e.company=? AND e.competence=?
              AND e.status NOT IN ('cancelled','reversed')
              AND a.nature IN ('operating_expense','financial_expense','tax_expense')
            """,
            (company, competence),
        ).fetchone()
    return {"expenses": int(row["expenses"])}


def cash_for(company: int, period: str) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(
                CASE WHEN v.event_type='settled' THEN v.amount_cents
                     WHEN v.event_type='reversed' THEN -v.amount_cents ELSE 0 END
            ),0) AS outflows
            FROM financial_events v
            JOIN financial_entries e ON e.id=v.entry_id
            JOIN finance_accounts a ON a.id=e.account_id
            WHERE e.company=? AND SUBSTR(v.occurred_at,1,7)=?
              AND a.nature NOT IN ('revenue','financing_inflow')
            """,
            (company, period),
        ).fetchone()
    return {"outflows": int(row["outflows"])}
