from __future__ import annotations

from datetime import date
from typing import Type


"""Shared field validators for financial-entry-shaped edits.

`entry_management.py` (PATCH /entries/{id}) and `recurrence.py`
(PATCH /recurrences/{id}) both edit the same underlying field set
(description, amount, competence, due date, notes, account, counterparty)
and enforce identical rules and messages — they only differ in which
exception class each module raises (EntryValidationError vs.
RecurrenceValidationError). Both exception classes share the same
`(message, *, fields=None)` constructor shape, so each validator here takes
the caller's exception class as `error_cls` and raises that instead of
owning one exception hierarchy itself.

Callers should wrap these in thin same-named private functions that bind
their own `error_cls`, so call sites elsewhere in each module stay
unchanged.
"""


def validate_description(value, *, error_cls: Type[Exception]) -> str:
    text = (value or "").strip()
    if not (1 <= len(text) <= 240):
        raise error_cls(
            "Descrição deve ter entre 1 e 240 caracteres.", fields=["description"]
        )
    return text


def validate_notes(value, *, error_cls: Type[Exception]) -> str:
    text = (value or "").strip()
    if len(text) > 2000:
        raise error_cls("Notas devem ter no máximo 2000 caracteres.", fields=["notes"])
    return text


def validate_amount(value, *, error_cls: Type[Exception]) -> int:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        amount = None
    if amount is None or isinstance(value, bool) or amount <= 0:
        raise error_cls("O valor deve ser maior que zero.", fields=["amount_cents"])
    return amount


def validate_competence(
    value, *, error_cls: Type[Exception], field: str = "competence"
) -> str:
    if not isinstance(value, str) or len(value) != 7:
        raise error_cls("Competência inválida.", fields=[field])
    try:
        date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise error_cls("Competência inválida.", fields=[field]) from exc
    return value


def validate_due_date(value, *, error_cls: Type[Exception]) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise error_cls(
                "Data de vencimento inválida.", fields=["due_date"]
            ) from exc
    raise error_cls("Data de vencimento inválida.", fields=["due_date"])


def validate_account(conn, company: int, account_id, *, error_cls: Type[Exception]) -> int:
    try:
        account_id = int(account_id)
    except (TypeError, ValueError) as exc:
        raise error_cls("Categoria inválida.", fields=["account_id"]) from exc
    row = conn.execute(
        "SELECT archived FROM finance_accounts WHERE id=? AND company=?",
        (account_id, company),
    ).fetchone()
    if not row:
        raise error_cls(
            "Categoria não encontrada para esta empresa.", fields=["account_id"]
        )
    if row["archived"]:
        raise error_cls(
            "Categoria está arquivada e não pode ser selecionada.", fields=["account_id"]
        )
    return account_id


def validate_counterparty(
    conn, company: int, counterparty_id, *, error_cls: Type[Exception]
) -> int:
    try:
        counterparty_id = int(counterparty_id)
    except (TypeError, ValueError) as exc:
        raise error_cls(
            "Favorecido inválido.", fields=["counterparty_id"]
        ) from exc
    row = conn.execute(
        "SELECT archived FROM counterparties WHERE id=? AND company=?",
        (counterparty_id, company),
    ).fetchone()
    if not row:
        raise error_cls(
            "Favorecido não encontrado para esta empresa.", fields=["counterparty_id"]
        )
    if row["archived"]:
        raise error_cls(
            "Favorecido está arquivado e não pode ser selecionado.",
            fields=["counterparty_id"],
        )
    return counterparty_id
