from __future__ import annotations

import calendar
import secrets
from datetime import date
from typing import Optional

from .. import database as db
from .entries import EntryCommand, create_entry


def _next_month(period: str) -> str:
    year, month = map(int, period.split("-"))
    month += 1
    if month == 13:
        year, month = year + 1, 1
    return f"{year:04d}-{month:02d}"


def create_recurrence(
    *,
    company: int,
    account_id: int,
    description: str,
    amount_cents: int,
    start_competence: str,
    due_day: int,
    end_competence: Optional[str] = None,
    store: Optional[int] = None,
    counterparty_id: Optional[int] = None,
    created_by: Optional[int] = None,
) -> dict:
    date.fromisoformat(start_competence + "-01")
    if end_competence:
        date.fromisoformat(end_competence + "-01")
    if due_day not in range(1, 32) or amount_cents <= 0:
        raise ValueError("Recorrência inválida.")
    recurrence_id = secrets.randbits(63) or 1
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO financial_recurrences(
                id,company,store,account_id,counterparty_id,description,
                amount_cents,start_competence,end_competence,due_day,
                created_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                recurrence_id,
                company,
                store or 0,
                account_id,
                counterparty_id,
                description.strip(),
                amount_cents,
                start_competence,
                end_competence,
                due_day,
                created_by,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM financial_recurrences WHERE id=?", (recurrence_id,)
        ).fetchone()
    return dict(row)


def generate_occurrences(recurrence_id: int, *, through_competence: str) -> list:
    date.fromisoformat(through_competence + "-01")
    with db.connection() as conn:
        recurrence = conn.execute(
            "SELECT * FROM financial_recurrences WHERE id=? AND active=1",
            (recurrence_id,),
        ).fetchone()
    if not recurrence:
        raise ValueError("Recorrência não encontrada.")
    last = min(
        through_competence,
        recurrence["end_competence"] or through_competence,
    )
    generated = []
    competence = recurrence["start_competence"]
    while competence <= last:
        with db.connection() as conn:
            exists = conn.execute(
                """
                SELECT 1 FROM financial_entries
                WHERE recurrence_id=? AND competence=?
                """,
                (recurrence_id, competence),
            ).fetchone()
        if not exists:
            year, month = map(int, competence.split("-"))
            due_day = min(
                recurrence["due_day"], calendar.monthrange(year, month)[1]
            )
            generated.append(
                create_entry(
                    EntryCommand(
                        company_id=recurrence["company"],
                        store_id=recurrence["store"] or None,
                        account_id=recurrence["account_id"],
                        counterparty_id=recurrence["counterparty_id"],
                        amount_cents=recurrence["amount_cents"],
                        competence=competence,
                        due_date=date(year, month, due_day),
                        source="recurrence",
                        external_id=f"{recurrence_id}:{competence}",
                        recurrence_id=recurrence_id,
                        description=recurrence["description"],
                        created_by=recurrence["created_by"],
                        forecast=True,
                    )
                )
            )
        competence = _next_month(competence)
    return generated
