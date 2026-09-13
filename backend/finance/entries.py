from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .. import database as db
from . import obligations as _obligations


# The `financial_entries.source` marker owned by the loan machinery. Aliased
# from obligations.py (rather than re-spelled) so the "hide it from the
# payable-obligation surface" filter there, the "refuse a direct payment
# against it" guard in payments.py::record_payment and the "refuse a generic
# reversal" guard in reverse_entry() below can never drift apart. See that
# module's _LOAN_ENTRY_SOURCE for the full rationale.
_LOAN_ENTRY_SOURCE = _obligations._LOAN_ENTRY_SOURCE


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


class EntryVersionConflict(ValueError):
    """Raised by *_on_connection helpers when an optimistic-concurrency
    version check fails (rowcount 0 on a `WHERE version=?` UPDATE). Subclasses
    ValueError so existing bare `except ValueError` call sites keep working
    unchanged; callers that need to distinguish a stale-version conflict
    (-> HTTP 409) from other validation errors (-> 422) can catch this
    specifically (see backend/finance/payments.py)."""


def _validate_competence(competence: str) -> None:
    if len(competence) != 7:
        raise ValueError("Competência inválida.")
    date.fromisoformat(competence + "-01")


def create_entry_on_connection(conn, command: EntryCommand) -> dict:
    """Same as `create_entry`, but writes on the caller's connection/
    transaction and never commits — for callers (backend/finance/payments.py)
    that need entry creation to share a single atomic transaction with other
    writes (e.g. the loan-installment interest entry created as part of
    recording an obligation payment)."""
    _validate_competence(command.competence)
    if command.amount_cents <= 0:
        raise ValueError("O valor deve ser maior que zero.")
    description = command.description.strip()
    if not description:
        raise ValueError("Informe a descrição.")
    entry_id = _new_id()
    timestamp = db.now()
    status = "forecast" if command.forecast else "open"
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


def create_entry(command: EntryCommand, *, conn=None) -> dict:
    if conn is not None:
        return create_entry_on_connection(conn, command)
    with db.connection() as own_conn:
        return create_entry_on_connection(own_conn, command)


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


def _entry_snapshot(conn, entry_id: int) -> dict:
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


def get_entry(entry_id: int) -> dict:
    with db.connection() as conn:
        return _entry_snapshot(conn, entry_id)


def settle_entry_on_connection(
    conn,
    entry_id: int,
    amount_cents: int,
    *,
    paid_at: date,
    created_by: Optional[int] = None,
    expected_version: Optional[int] = None,
) -> int:
    """Insert a 'settled' financial_events row and advance the entry's paid
    state on the caller's connection/transaction (no commit here).

    When `expected_version` is given, the state-advancing UPDATE is
    conditioned on it (`WHERE ... AND version=?`) and a rowcount of 0 raises
    `EntryVersionConflict` — this doubles as the real concurrency gate for
    callers racing two payments against the same entry (see
    backend/finance/payments.py::record_payment), not just a courtesy check:
    under Postgres READ COMMITTED the conditional UPDATE re-evaluates its
    WHERE clause against the latest committed row once it acquires the row
    lock, so a loser whose read was stale gets rowcount 0 even though its
    initial SELECT looked fine.

    Returns the id of the inserted financial_events row (needed by callers
    that must persist it elsewhere, e.g. obligation_payments.financial_event_id).
    """
    if amount_cents <= 0:
        raise ValueError("O pagamento deve ser maior que zero.")
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
    event_id = _new_id()
    conn.execute(
        """
        INSERT INTO financial_events(
            id,entry_id,event_type,amount_cents,occurred_at,created_by,created_at
        ) VALUES(?,?,?,?,?,?,?)
        """,
        (
            event_id,
            entry_id,
            "settled",
            amount_cents,
            paid_at.isoformat(),
            created_by,
            timestamp,
        ),
    )
    status = "paid" if paid + amount_cents == entry["amount_cents"] else "partially_paid"
    if expected_version is None:
        conn.execute(
            """
            UPDATE financial_entries SET status=?,version=version+1,updated_at=?
            WHERE id=?
            """,
            (status, timestamp, entry_id),
        )
    else:
        changed = conn.execute(
            """
            UPDATE financial_entries SET status=?,version=version+1,updated_at=?
            WHERE id=? AND version=?
            """,
            (status, timestamp, entry_id, expected_version),
        )
        if changed.rowcount != 1:
            raise EntryVersionConflict("Versão desatualizada; recarregue o lançamento.")
    return event_id


def settle_entry(
    entry_id: int,
    amount_cents: int,
    *,
    paid_at: date,
    created_by: Optional[int] = None,
    conn=None,
) -> dict:
    if conn is not None:
        settle_entry_on_connection(
            conn, entry_id, amount_cents, paid_at=paid_at, created_by=created_by
        )
        return _entry_snapshot(conn, entry_id)
    with db.connection() as own_conn:
        settle_entry_on_connection(
            own_conn, entry_id, amount_cents, paid_at=paid_at, created_by=created_by
        )
    return get_entry(entry_id)


def reverse_settlement_on_connection(
    conn,
    entry_id: int,
    amount_cents: int,
    *,
    reversed_at: date,
    reason: str,
    created_by: Optional[int] = None,
    expected_version: Optional[int] = None,
) -> int:
    """Insert a compensating 'reversed' financial_events row for EXACTLY
    `amount_cents` — one specific payment's contribution, never the entry's
    entire remaining/paid balance — and recompute the entry's paid/open/
    status accordingly, on the caller's connection/transaction (no commit
    here). This is the inverse of settle_entry_on_connection, and is
    deliberately NOT `reverse_entry()` above: that function reverts the
    entry's whole current balance and permanently marks it 'reversed',
    which is correct for cancelling an entry outright but wrong for undoing
    one payment among possibly several against a partially-paid entry.

    Used by backend/finance/payments.py::reverse_payment for both (a) the
    'entry' kind's own settlement, and (b) partially un-settling a loan
    installment's shared interest entry (see loans.py's
    reverse_installment_payment_on_connection and payments.py's
    _ensure_interest_settlement, which created that shared entry).

    Same optimistic-concurrency contract as settle_entry_on_connection:
    when `expected_version` is given, the state-advancing UPDATE is
    conditioned on it and a rowcount of 0 raises `EntryVersionConflict`.

    Returns the id of the inserted financial_events 'reversed' row.
    """
    if amount_cents <= 0:
        raise ValueError("O valor do estorno deve ser maior que zero.")
    entry = conn.execute(
        "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
    ).fetchone()
    if not entry:
        raise ValueError("Lançamento não encontrado.")
    if entry["status"] in {"cancelled", "reversed"}:
        raise ValueError(
            "Este lançamento já foi cancelado ou revertido integralmente."
        )
    timestamp = db.now()
    event_id = _new_id()
    conn.execute(
        """
        INSERT INTO financial_events(
            id,entry_id,event_type,amount_cents,occurred_at,reason,created_by,created_at
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            event_id,
            entry_id,
            "reversed",
            amount_cents,
            reversed_at.isoformat(),
            reason.strip(),
            created_by,
            timestamp,
        ),
    )
    new_paid = _paid_cents(conn, entry_id)
    if new_paid <= 0:
        status = "open"
    elif new_paid >= entry["amount_cents"]:
        status = "paid"
    else:
        status = "partially_paid"
    if expected_version is None:
        conn.execute(
            """
            UPDATE financial_entries SET status=?,version=version+1,updated_at=?
            WHERE id=?
            """,
            (status, timestamp, entry_id),
        )
    else:
        changed = conn.execute(
            """
            UPDATE financial_entries SET status=?,version=version+1,updated_at=?
            WHERE id=? AND version=?
            """,
            (status, timestamp, entry_id, expected_version),
        )
        if changed.rowcount != 1:
            raise EntryVersionConflict("Versão desatualizada; recarregue o lançamento.")
    return event_id


def reverse_entry(
    entry_id: int,
    *,
    reversed_at: date,
    reason: str,
    created_by: Optional[int] = None,
) -> dict:
    """Revert an entry's WHOLE current balance and mark it 'reversed'
    permanently. Correct for cancelling a manual entry outright; wrong for
    undoing one payment among several (use
    reverse_settlement_on_connection / payments.py::reverse_payment).

    Refuses a `source='loan'` entry outright — see the guard below.
    """
    with db.connection() as conn:
        entry = conn.execute(
            "SELECT * FROM financial_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if not entry or entry["status"] in {"cancelled", "reversed"}:
            raise ValueError("Lançamento não encontrado ou já revertido.")
        # A loan-owned entry (the interest entry of a loan installment,
        # created by payments.py::_ensure_interest_settlement) is NEVER
        # reversible through this generic path, paid or not. Reversing it
        # here would (a) erase already-paid interest from the P&L
        # (reporting.py::management_result skips 'reversed' entries) even
        # though the cash really left the bank and the installment still
        # records paid_interest_cents, and (b) wedge the installment
        # permanently: every later interest-bearing payment would fail on the
        # reversed entry, and an entry reversal can never be undone
        # (cancel_entry/update_entry both refuse a 'reversed' row).
        # The only correct way to undo money against a loan installment is
        # reverse_payment(kind='loan_installment', ...), which reverses the
        # SPECIFIC payment's interest slice instead of destroying the entry.
        if entry["source"] == _LOAN_ENTRY_SOURCE:
            raise ValueError(
                "Este é o lançamento de juros de uma parcela de empréstimo e "
                "não pode ser revertido por aqui. Para desfazer um pagamento, "
                "estorne o pagamento correspondente na parcela do empréstimo."
            )
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
