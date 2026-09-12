from __future__ import annotations

import calendar
import secrets
from datetime import date
from typing import Optional

from .. import database as db
from .entries import EntryCommand, create_entry
from .entry_management import (
    EntryConflictError,
    EntryNotFoundError,
    EntryValidationError,
    _insert_audit,
)

VALID_COMPETENCE_MODES = {"single", "distributed"}


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _validate_competence(value: str) -> str:
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError("Competência inválida.")
    date.fromisoformat(value + "-01")
    return value


def _add_months(year: int, month: int, offset: int) -> tuple:
    total = (year * 12 + (month - 1)) + offset
    return total // 12, total % 12 + 1


def preview_expense_schedule(
    total_cents: int,
    count: int,
    first_due: date,
    competence_mode: str,
    competence: Optional[str] = None,
) -> list:
    """Compute the (amount_cents, due_date, competence) breakdown for an
    installment expense, without writing anything.

    Rounding: `total_cents` is split into `count` equal shares with integer
    floor division; whatever remainder is left over (because the total isn't
    evenly divisible) is added onto the FIRST installment, never the last —
    every later installment carries an identical, unrounded share.

    Due dates: the day-of-month of `first_due` is the "anchor day" and is
    reused for every installment, clamped to the last day of a shorter month
    (e.g. an anchor of 31 becomes 28/29 in February).

    Competence: in 'single' mode every row shares the same, caller-supplied
    `competence`. In 'distributed' mode each row's competence is derived from
    its own due month instead — the caller must treat the distributed rows'
    competence as computed, not chosen, and confirm it explicitly before
    creating real entries from it (see create_expense_schedule).
    """
    if competence_mode not in VALID_COMPETENCE_MODES:
        raise ValueError("Modo de competência inválido.")
    if not isinstance(count, int) or isinstance(count, bool) or not (1 <= count <= 120):
        raise ValueError("A quantidade de parcelas deve estar entre 1 e 120.")
    if not isinstance(total_cents, int) or isinstance(total_cents, bool) or total_cents <= 0:
        raise ValueError("O valor total deve ser maior que zero.")
    if not isinstance(first_due, date):
        raise ValueError("Data da primeira parcela inválida.")
    if competence_mode == "single":
        if not competence:
            raise ValueError("Informe a competência para o modo single.")
        _validate_competence(competence)

    anchor_day = first_due.day
    base = total_cents // count
    remainder = total_cents - base * count

    rows = []
    for index in range(count):
        year, month = _add_months(first_due.year, first_due.month, index)
        last_day = calendar.monthrange(year, month)[1]
        due_date = date(year, month, min(anchor_day, last_day))
        amount = base + remainder if index == 0 else base
        row_competence = (
            competence if competence_mode == "single" else f"{year:04d}-{month:02d}"
        )
        rows.append(
            {
                "installment_number": index + 1,
                "installment_count": count,
                "amount_cents": amount,
                "due_date": due_date.isoformat(),
                "competence": row_competence,
            }
        )
    return rows


def create_expense_schedule(
    *,
    company: int,
    account_id: int,
    description: str,
    total_cents: int,
    count: int,
    first_due: date,
    competence_mode: str,
    competence: Optional[str] = None,
    store: Optional[int] = None,
    counterparty_id: Optional[int] = None,
    confirmed: bool = False,
    created_by: Optional[int] = None,
) -> dict:
    """Create an installment expense schedule and every one of its
    `financial_entries` rows atomically, in a single transaction.

    Every generated entry is created with `forecast=True` (status
    'forecast'), same as recurrence occurrences — nothing is treated as a
    real/open obligation until explicitly confirmed via
    `confirm_entry`/POST /entries/{id}/confirm.

    'distributed' mode auto-derives each installment's competence from its
    own due month (see preview_expense_schedule) rather than reusing a
    caller-chosen value, so it requires `confirmed=True` — the caller must
    have already reviewed the preview and knowingly accepted those computed
    competences.
    """
    description = (description or "").strip()
    if not description:
        raise ValueError("Informe a descrição.")
    if competence_mode == "distributed" and not confirmed:
        raise ValueError(
            "Confirme a prévia antes de gerar parcelas com competência distribuída."
        )

    rows = preview_expense_schedule(
        total_cents, count, first_due, competence_mode, competence
    )

    schedule_id = _new_id()
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO expense_schedules(
                id,company,account_id,store,counterparty_id,description,
                total_cents,installment_count,competence_mode,first_due,
                created_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                schedule_id,
                company,
                account_id,
                store or 0,
                counterparty_id,
                description,
                total_cents,
                count,
                competence_mode,
                first_due.isoformat(),
                created_by,
                timestamp,
                timestamp,
            ),
        )

        entries = []
        for row in rows:
            entry = create_entry(
                EntryCommand(
                    company_id=company,
                    store_id=store,
                    account_id=account_id,
                    counterparty_id=counterparty_id,
                    amount_cents=row["amount_cents"],
                    competence=row["competence"],
                    due_date=date.fromisoformat(row["due_date"]),
                    source="expense_schedule",
                    external_id=f"{schedule_id}:{row['installment_number']}",
                    description=description,
                    installment_number=row["installment_number"],
                    installment_count=count,
                    created_by=created_by,
                    forecast=True,
                ),
                conn=conn,
            )
            conn.execute(
                "UPDATE financial_entries SET expense_schedule_id=? WHERE id=?",
                (schedule_id, entry["id"]),
            )
            entry["expense_schedule_id"] = schedule_id
            entries.append(entry)

        header = dict(
            conn.execute(
                "SELECT * FROM expense_schedules WHERE id=?", (schedule_id,)
            ).fetchone()
        )

    return {"schedule": header, "entries": entries}


def confirm_entry(
    company: int,
    entry_id: int,
    *,
    expected_version: int,
    amount_cents: Optional[int] = None,
    competence: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> dict:
    """Confirm a 'forecast' entry (created by a recurrence occurrence or an
    expense-schedule installment) into a real, 'open' obligation.

    This is the explicit human/API step the plan requires before a forecast
    entry counts as a realized commitment ("não realizado automático"). The
    caller may optionally confirm/adjust the entry's `amount_cents` and/or
    `competence` at this point — most relevant for a 'distributed'-mode
    installment, whose competence was computed rather than chosen — before
    the status flips to 'open'. Only entries currently in 'forecast' status
    are eligible; anything else is a conflict, not a validation error.
    """
    timestamp = db.now()
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM financial_entries WHERE id=? AND company=?",
            (entry_id, company),
        ).fetchone()
        if not row:
            raise EntryNotFoundError("Lançamento não encontrado.")
        entry = dict(row)

        if entry["status"] != "forecast":
            raise EntryConflictError(
                "Apenas lançamentos previstos (forecast) podem ser confirmados."
            )

        updates: dict = {"status": "open"}

        if amount_cents is not None:
            try:
                amount_cents = int(amount_cents)
            except (TypeError, ValueError) as exc:
                raise EntryValidationError(
                    "O valor deve ser maior que zero.", fields=["amount_cents"]
                ) from exc
            if isinstance(amount_cents, bool) or amount_cents <= 0:
                raise EntryValidationError(
                    "O valor deve ser maior que zero.", fields=["amount_cents"]
                )
            updates["amount_cents"] = amount_cents

        if competence is not None:
            try:
                _validate_competence(competence)
            except ValueError as exc:
                raise EntryValidationError(
                    "Competência inválida.", fields=["competence"]
                ) from exc
            updates["competence"] = competence

        set_sql = ",".join(f"{column}=?" for column in updates)
        params = list(updates.values()) + [timestamp, entry_id, company, expected_version]
        changed = conn.execute(
            f"UPDATE financial_entries SET {set_sql},version=version+1,updated_at=? "
            "WHERE id=? AND company=? AND version=?",
            params,
        )
        if changed.rowcount != 1:
            raise EntryConflictError("Versão desatualizada; recarregue o lançamento.")

        after = dict(
            conn.execute(
                "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
            ).fetchone()
        )

        _insert_audit(
            conn,
            company=company,
            entity_type="financial_entry",
            entity_id=entry_id,
            action="confirm",
            before=entry,
            after=after,
            reason="",
            actor_id=actor_id,
            timestamp=timestamp,
        )

    return after
