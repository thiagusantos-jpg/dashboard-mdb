from __future__ import annotations

import json
import secrets
from datetime import date
from typing import Iterable, Optional

from .. import database as db
from .entries import _paid_cents, get_entry
from . import validators as _shared_validators


class EntryManagementError(Exception):
    """Base error for entry edit/cancel/history operations."""

    def __init__(self, message: str, *, fields: Optional[list] = None):
        super().__init__(message)
        self.message = message
        self.fields = fields


class EntryNotFoundError(EntryManagementError):
    """Raised when the entry does not exist within the given company scope."""


class EntryConflictError(EntryManagementError):
    """Raised for optimistic-locking or entry-state conflicts (maps to HTTP 409)."""


class EntryValidationError(EntryManagementError):
    """Raised for invalid or non-editable field values (maps to HTTP 422)."""


_OPEN_EDITABLE_FIELDS = {
    "account_id",
    "counterparty_id",
    "description",
    "amount_cents",
    "competence",
    "due_date",
    "notes",
}
_PARTIAL_EDITABLE_FIELDS = {"description", "due_date", "notes"}

_BLOCKED_STATUSES = {"cancelled", "reversed", "paid"}


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _allowed_fields(status: str) -> set:
    if status == "open":
        return set(_OPEN_EDITABLE_FIELDS)
    if status == "partially_paid":
        return set(_PARTIAL_EDITABLE_FIELDS)
    return set()


def _is_reconciled(conn, entry_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM reconciliation_links WHERE item_type='financial_entry' AND item_id=?",
        (entry_id,),
    ).fetchone()
    return row is not None


# The field validators below are thin bindings onto backend/finance/validators.py
# (shared with recurrence.py, which edits the same field set under the same
# rules): each just supplies EntryValidationError as the exception class to
# raise, so every other call site in this module keeps calling
# `_validate_description(value)` etc. unchanged.


def _validate_description(value) -> str:
    return _shared_validators.validate_description(value, error_cls=EntryValidationError)


def _validate_notes(value) -> str:
    return _shared_validators.validate_notes(value, error_cls=EntryValidationError)


def _validate_amount(value) -> int:
    return _shared_validators.validate_amount(value, error_cls=EntryValidationError)


def _validate_competence(value) -> str:
    return _shared_validators.validate_competence(value, error_cls=EntryValidationError)


def _validate_due_date(value) -> str:
    return _shared_validators.validate_due_date(value, error_cls=EntryValidationError)


def _validate_account(conn, company: int, account_id) -> int:
    return _shared_validators.validate_account(
        conn, company, account_id, error_cls=EntryValidationError
    )


def _validate_counterparty(conn, company: int, counterparty_id) -> int:
    return _shared_validators.validate_counterparty(
        conn, company, counterparty_id, error_cls=EntryValidationError
    )


def _insert_audit(
    conn,
    *,
    company: int,
    entity_type: str,
    entity_id: int,
    action: str,
    before: dict,
    after: dict,
    reason: str,
    actor_id: Optional[int],
    timestamp: str,
) -> None:
    conn.execute(
        """
        INSERT INTO finance_audit(
            id,company,entity_type,entity_id,action,before_json,after_json,
            reason,actor_id,created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            _new_id(),
            company,
            entity_type,
            entity_id,
            action,
            json.dumps(before, ensure_ascii=False, default=str),
            json.dumps(after, ensure_ascii=False, default=str),
            reason,
            actor_id,
            timestamp,
        ),
    )


def _load_entry_for_write(conn, company: int, entry_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM financial_entries WHERE id=? AND company=?",
        (entry_id, company),
    ).fetchone()
    if not row:
        raise EntryNotFoundError("Lançamento não encontrado.")
    return dict(row)


def update_entry(
    company: int,
    entry_id: int,
    patch: dict,
    *,
    expected_version: int,
    actor_id: Optional[int] = None,
) -> dict:
    if not patch:
        raise EntryValidationError("Nenhuma alteração informada.")

    timestamp = db.now()
    with db.connection() as conn:
        entry = _load_entry_for_write(conn, company, entry_id)

        if entry["status"] in _BLOCKED_STATUSES:
            raise EntryConflictError(
                "Lançamento não pode ser alterado neste status."
            )
        if entry["source"] != "manual":
            raise EntryConflictError(
                "Lançamentos importados não podem ser editados manualmente."
            )
        if _is_reconciled(conn, entry_id):
            raise EntryConflictError(
                "Lançamento conciliado não pode ser alterado."
            )

        allowed = _allowed_fields(entry["status"])
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise EntryValidationError(
                "Campo não pode ser alterado neste status: " + ", ".join(unknown),
                fields=unknown,
            )

        updates: dict = {}

        if "account_id" in patch:
            account_id = patch["account_id"]
            if account_id is None:
                raise EntryValidationError(
                    "Categoria é obrigatória.", fields=["account_id"]
                )
            if int(account_id) != entry["account_id"]:
                account_id = _validate_account(conn, company, account_id)
            else:
                account_id = entry["account_id"]
            updates["account_id"] = account_id

        if "counterparty_id" in patch:
            counterparty_id = patch["counterparty_id"]
            if counterparty_id is None:
                updates["counterparty_id"] = None
            elif entry["counterparty_id"] is not None and int(counterparty_id) == entry["counterparty_id"]:
                updates["counterparty_id"] = entry["counterparty_id"]
            else:
                updates["counterparty_id"] = _validate_counterparty(
                    conn, company, counterparty_id
                )

        if "description" in patch:
            updates["description"] = _validate_description(patch["description"])

        if "amount_cents" in patch:
            updates["amount_cents"] = _validate_amount(patch["amount_cents"])

        if "competence" in patch:
            updates["competence"] = _validate_competence(patch["competence"])

        if "due_date" in patch:
            updates["due_date"] = _validate_due_date(patch["due_date"])

        if "notes" in patch:
            updates["notes"] = _validate_notes(patch["notes"])

        if not updates:
            raise EntryValidationError("Nenhuma alteração informada.")

        set_sql = ",".join(f"{column}=?" for column in updates)
        params: list = list(updates.values())
        params.append(timestamp)
        params.extend([entry_id, company, expected_version])
        changed = conn.execute(
            f"UPDATE financial_entries SET {set_sql},version=version+1,updated_at=? "
            "WHERE id=? AND company=? AND version=?",
            params,
        )
        if changed.rowcount != 1:
            raise EntryConflictError("Versão desatualizada; recarregue o lançamento.")

        after_row = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
        after = dict(after_row)

        _insert_audit(
            conn,
            company=company,
            entity_type="financial_entry",
            entity_id=entry_id,
            action="update",
            before=entry,
            after=after,
            reason="",
            actor_id=actor_id,
            timestamp=timestamp,
        )

    return get_entry(entry_id)


def cancel_entry(
    company: int,
    entry_id: int,
    *,
    expected_version: int,
    reason: str,
    actor_id: Optional[int] = None,
) -> dict:
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise EntryValidationError("Informe o motivo do cancelamento.", fields=["reason"])
    if len(clean_reason) > 500:
        raise EntryValidationError(
            "Motivo deve ter no máximo 500 caracteres.", fields=["reason"]
        )

    timestamp = db.now()
    with db.connection() as conn:
        entry = _load_entry_for_write(conn, company, entry_id)

        if entry["status"] in {"cancelled", "reversed"}:
            raise EntryConflictError("Lançamento já está cancelado ou revertido.")

        paid = _paid_cents(conn, entry_id)
        if paid != 0:
            raise EntryConflictError(
                "Lançamento com pagamento registrado não pode ser cancelado."
            )

        if _is_reconciled(conn, entry_id):
            raise EntryConflictError(
                "Lançamento conciliado não pode ser cancelado."
            )

        changed = conn.execute(
            """
            UPDATE financial_entries SET status='cancelled',version=version+1,updated_at=?
            WHERE id=? AND company=? AND version=?
            """,
            (timestamp, entry_id, company, expected_version),
        )
        if changed.rowcount != 1:
            raise EntryConflictError("Versão desatualizada; recarregue o lançamento.")

        after_row = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
        after = dict(after_row)

        _insert_audit(
            conn,
            company=company,
            entity_type="financial_entry",
            entity_id=entry_id,
            action="cancel",
            before=entry,
            after=after,
            reason=clean_reason,
            actor_id=actor_id,
            timestamp=timestamp,
        )

    return get_entry(entry_id)


def entry_history(company: int, entry_id: int) -> list:
    with db.connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM financial_entries WHERE id=? AND company=?",
            (entry_id, company),
        ).fetchone()
        if not exists:
            raise EntryNotFoundError("Lançamento não encontrado.")

        audit_rows = conn.execute(
            """
            SELECT * FROM finance_audit
            WHERE company=? AND entity_type='financial_entry' AND entity_id=?
            ORDER BY created_at,id
            """,
            (company, entry_id),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM financial_events WHERE entry_id=? ORDER BY occurred_at,id",
            (entry_id,),
        ).fetchall()

    history: list = []
    for row in audit_rows:
        history.append(
            {
                "id": row["id"],
                "kind": "audit",
                "action": row["action"],
                "before": json.loads(row["before_json"]),
                "after": json.loads(row["after_json"]),
                "amount_cents": None,
                "reason": row["reason"],
                "actor_id": row["actor_id"],
                "created_at": row["created_at"],
            }
        )
    for row in event_rows:
        history.append(
            {
                "id": row["id"],
                "kind": "event",
                "action": row["event_type"],
                "before": None,
                "after": None,
                "amount_cents": row["amount_cents"],
                "reason": row["reason"],
                "actor_id": row["created_by"],
                "created_at": row["created_at"],
            }
        )

    history.sort(key=lambda item: (item["created_at"], item["kind"], item["id"]))
    return history
