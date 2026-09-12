from __future__ import annotations

import calendar
import secrets
from datetime import date
from typing import Optional

from .. import database as db
from .entries import EntryCommand, create_entry
from .entry_management import _insert_audit
from . import validators as _shared_validators


class RecurrenceError(Exception):
    """Base error for recurrence edit operations (mirrors
    entry_management.EntryManagementError's message/fields shape so the
    route layer can map these onto the same {code,message,fields} contract)."""

    def __init__(self, message: str, *, fields: Optional[list] = None):
        super().__init__(message)
        self.message = message
        self.fields = fields


class RecurrenceNotFoundError(RecurrenceError):
    """Raised when the recurrence (or, for scope='single', the targeted
    occurrence) does not exist within the given company scope."""


class RecurrenceConflictError(RecurrenceError):
    """Raised for optimistic-locking or state conflicts (maps to HTTP 409)."""


class RecurrenceValidationError(RecurrenceError):
    """Raised for invalid field values or scope misuse (maps to HTTP 422)."""


_RECURRENCE_EDITABLE_FIELDS = {
    "account_id",
    "counterparty_id",
    "description",
    "amount_cents",
    "due_day",
    "end_competence",
    "active",
}
_OCCURRENCE_EDITABLE_FIELDS = {
    "account_id",
    "counterparty_id",
    "description",
    "amount_cents",
    "due_date",
    "notes",
}


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


# --- Task B6: PATCH /recurrences/{id} (edit scope + optimistic locking) ----


# _validate_description/_validate_amount/_validate_due_date/_validate_notes/
# _validate_account/_validate_counterparty below (plus _validate_competence_format,
# the parameterized-field counterpart of entry_management._validate_competence)
# are thin bindings onto backend/finance/validators.py — shared with
# entry_management.py, which edits the same field set under the same rules.
# Each just supplies RecurrenceValidationError as the exception class to
# raise, so every other call site in this module keeps calling
# `_validate_description(value)` etc. unchanged.


def _validate_competence_format(value: str, field: str) -> str:
    return _shared_validators.validate_competence(
        value, error_cls=RecurrenceValidationError, field=field
    )


def _validate_description(value) -> str:
    return _shared_validators.validate_description(
        value, error_cls=RecurrenceValidationError
    )


def _validate_amount(value) -> int:
    return _shared_validators.validate_amount(value, error_cls=RecurrenceValidationError)


def _validate_due_day(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in range(1, 32):
        raise RecurrenceValidationError(
            "Dia de vencimento inválido.", fields=["due_day"]
        )
    return value


def _validate_end_competence(value) -> Optional[str]:
    if value is None:
        return None
    return _validate_competence_format(value, "end_competence")


def _validate_due_date(value) -> str:
    return _shared_validators.validate_due_date(value, error_cls=RecurrenceValidationError)


def _validate_notes(value) -> str:
    return _shared_validators.validate_notes(value, error_cls=RecurrenceValidationError)


def _validate_account(conn, company: int, account_id) -> int:
    return _shared_validators.validate_account(
        conn, company, account_id, error_cls=RecurrenceValidationError
    )


def _validate_counterparty(conn, company: int, counterparty_id) -> int:
    return _shared_validators.validate_counterparty(
        conn, company, counterparty_id, error_cls=RecurrenceValidationError
    )


def _load_recurrence_for_write(conn, company: int, recurrence_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM financial_recurrences WHERE id=? AND company=?",
        (recurrence_id, company),
    ).fetchone()
    if not row:
        raise RecurrenceNotFoundError("Recorrência não encontrada.")
    return dict(row)


def _cascade_future_occurrences(
    conn,
    *,
    recurrence_id: int,
    effective_competence: str,
    updates: dict,
    timestamp: str,
) -> None:
    """Push a scope='future' header change onto already-generated occurrences
    that are still 'forecast' (never touching an occurrence that has been
    confirmed/opened, partially paid, paid, cancelled or reversed) with
    competence >= effective_competence. `end_competence`/`active` changes
    never reach here — they only shape what generate_occurrences() will (or
    won't) create next, not entries that already exist."""
    cascading = {
        key: value
        for key, value in updates.items()
        if key in {"account_id", "counterparty_id", "description", "amount_cents"}
    }
    if not cascading and "due_day" not in updates:
        return

    rows = conn.execute(
        """
        SELECT * FROM financial_entries
        WHERE recurrence_id=? AND status='forecast' AND competence>=?
        """,
        (recurrence_id, effective_competence),
    ).fetchall()

    for row in rows:
        row = dict(row)
        entry_updates = dict(cascading)
        if "due_day" in updates:
            year, month = map(int, row["competence"].split("-"))
            last_day = calendar.monthrange(year, month)[1]
            entry_updates["due_date"] = date(
                year, month, min(updates["due_day"], last_day)
            ).isoformat()
        if not entry_updates:
            continue
        set_sql = ",".join(f"{column}=?" for column in entry_updates)
        conn.execute(
            f"UPDATE financial_entries SET {set_sql},version=version+1,updated_at=? "
            "WHERE id=?",
            list(entry_updates.values()) + [timestamp, row["id"]],
        )


def update_recurrence(
    company: int,
    recurrence_id: int,
    changes: dict,
    *,
    expected_version: int,
    scope: str,
    effective_competence: Optional[str] = None,
    occurrence_competence: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> dict:
    """Edit a recurrence with explicit edit-scope semantics.

    scope='single': edits exactly one already-generated occurrence (picked
    by `occurrence_competence`) without touching the recurrence definition
    or any other occurrence. Only a still-'forecast' occurrence is eligible.
    `expected_version` is checked against THAT OCCURRENCE's own version
    (same optimistic-locking convention as PATCH /entries/{id}).

    scope='future': edits the recurrence's own definition (fields in
    _RECURRENCE_EDITABLE_FIELDS, including `active`, which suspends the
    series and stops future generate_occurrences() calls from creating new
    entries) and cascades the applicable field changes onto every occurrence
    that is still 'forecast' with competence >= `effective_competence`.
    `expected_version` is checked against the RECURRENCE's own version.
    Past, confirmed/open, partially paid, paid, cancelled or reversed
    occurrences are never touched by the cascade.
    """
    if scope not in ("single", "future"):
        raise RecurrenceValidationError("Escopo inválido.", fields=["scope"])
    if not changes:
        raise RecurrenceValidationError("Nenhuma alteração informada.")

    timestamp = db.now()
    with db.connection() as conn:
        recurrence = _load_recurrence_for_write(conn, company, recurrence_id)

        if scope == "single":
            if not occurrence_competence:
                raise RecurrenceValidationError(
                    "Informe a competência da ocorrência.", fields=["competence"]
                )
            unknown = sorted(set(changes) - _OCCURRENCE_EDITABLE_FIELDS)
            if unknown:
                raise RecurrenceValidationError(
                    "Campo não pode ser alterado neste escopo: " + ", ".join(unknown),
                    fields=unknown,
                )

            entry_row = conn.execute(
                "SELECT * FROM financial_entries WHERE recurrence_id=? AND competence=?",
                (recurrence_id, occurrence_competence),
            ).fetchone()
            if not entry_row:
                raise RecurrenceNotFoundError(
                    "Ocorrência não encontrada para esta competência."
                )
            entry = dict(entry_row)
            if entry["status"] != "forecast":
                raise RecurrenceConflictError(
                    "Apenas ocorrências previstas (forecast) podem ser editadas."
                )

            updates: dict = {}
            if "account_id" in changes:
                updates["account_id"] = _validate_account(
                    conn, company, changes["account_id"]
                )
            if "counterparty_id" in changes:
                counterparty_id = changes["counterparty_id"]
                updates["counterparty_id"] = (
                    None
                    if counterparty_id is None
                    else _validate_counterparty(conn, company, counterparty_id)
                )
            if "description" in changes:
                updates["description"] = _validate_description(changes["description"])
            if "amount_cents" in changes:
                updates["amount_cents"] = _validate_amount(changes["amount_cents"])
            if "due_date" in changes:
                updates["due_date"] = _validate_due_date(changes["due_date"])
            if "notes" in changes:
                updates["notes"] = _validate_notes(changes["notes"])
            if not updates:
                raise RecurrenceValidationError("Nenhuma alteração informada.")

            set_sql = ",".join(f"{column}=?" for column in updates)
            params = list(updates.values()) + [
                timestamp,
                entry["id"],
                expected_version,
            ]
            changed = conn.execute(
                f"UPDATE financial_entries SET {set_sql},version=version+1,updated_at=? "
                "WHERE id=? AND version=?",
                params,
            )
            if changed.rowcount != 1:
                raise RecurrenceConflictError(
                    "Versão desatualizada; recarregue a ocorrência."
                )

            after = dict(
                conn.execute(
                    "SELECT * FROM financial_entries WHERE id=?", (entry["id"],)
                ).fetchone()
            )
            _insert_audit(
                conn,
                company=company,
                entity_type="financial_entry",
                entity_id=entry["id"],
                action="update",
                before=entry,
                after=after,
                reason="",
                actor_id=actor_id,
                timestamp=timestamp,
            )
            return {"recurrence": dict(recurrence), "occurrence": after}

        # scope == "future"
        if not effective_competence:
            raise RecurrenceValidationError(
                "Informe a competência efetiva.", fields=["effective_competence"]
            )
        _validate_competence_format(effective_competence, "effective_competence")

        unknown = sorted(set(changes) - _RECURRENCE_EDITABLE_FIELDS)
        if unknown:
            raise RecurrenceValidationError(
                "Campo não pode ser alterado neste escopo: " + ", ".join(unknown),
                fields=unknown,
            )

        updates = {}
        if "account_id" in changes:
            updates["account_id"] = _validate_account(conn, company, changes["account_id"])
        if "counterparty_id" in changes:
            counterparty_id = changes["counterparty_id"]
            updates["counterparty_id"] = (
                None
                if counterparty_id is None
                else _validate_counterparty(conn, company, counterparty_id)
            )
        if "description" in changes:
            updates["description"] = _validate_description(changes["description"])
        if "amount_cents" in changes:
            updates["amount_cents"] = _validate_amount(changes["amount_cents"])
        if "due_day" in changes:
            updates["due_day"] = _validate_due_day(changes["due_day"])
        if "end_competence" in changes:
            updates["end_competence"] = _validate_end_competence(changes["end_competence"])
        if "active" in changes:
            updates["active"] = 1 if changes["active"] else 0
        if not updates:
            raise RecurrenceValidationError("Nenhuma alteração informada.")

        set_sql = ",".join(f"{column}=?" for column in updates)
        params = list(updates.values()) + [
            timestamp,
            recurrence_id,
            company,
            expected_version,
        ]
        changed = conn.execute(
            f"UPDATE financial_recurrences SET {set_sql},version=version+1,updated_at=? "
            "WHERE id=? AND company=? AND version=?",
            params,
        )
        if changed.rowcount != 1:
            raise RecurrenceConflictError(
                "Versão desatualizada; recarregue a recorrência."
            )

        _cascade_future_occurrences(
            conn,
            recurrence_id=recurrence_id,
            effective_competence=effective_competence,
            updates=updates,
            timestamp=timestamp,
        )

        after = dict(
            conn.execute(
                "SELECT * FROM financial_recurrences WHERE id=?", (recurrence_id,)
            ).fetchone()
        )
        _insert_audit(
            conn,
            company=company,
            entity_type="financial_recurrence",
            entity_id=recurrence_id,
            action="update",
            before=recurrence,
            after=after,
            reason="",
            actor_id=actor_id,
            timestamp=timestamp,
        )
        return {"recurrence": after, "occurrence": None}
