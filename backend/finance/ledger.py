from __future__ import annotations

import secrets
from datetime import date
from typing import Optional

from .. import database as db


VALID_KINDS = {"bank", "payment", "cash"}


def _new_id() -> int:
    return secrets.randbits(63) or 1


def create_account(company: int, name: str, kind: str, *, store: Optional[int] = None) -> dict:
    if kind not in VALID_KINDS:
        raise ValueError("Tipo de conta inválido.")
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Informe o nome da conta.")
    account_id = _new_id()
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO cash_accounts(id,company,store,name,kind,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (account_id, company, store, clean_name, kind, timestamp, timestamp),
        )
        row = conn.execute("SELECT * FROM cash_accounts WHERE id=?", (account_id,)).fetchone()
    return dict(row)


def list_accounts(company: int, include_archived: bool = False) -> list:
    where = "" if include_archived else " AND archived=0"
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM cash_accounts WHERE company=?" + where + " ORDER BY name",
                (company,),
            )
        ]


def _require_account(conn, company: int, account_id: int) -> None:
    if not conn.execute(
        "SELECT 1 FROM cash_accounts WHERE id=? AND company=?", (account_id, company)
    ).fetchone():
        raise ValueError("Conta financeira não encontrada.")


def post_cash_event(
    company: int,
    cash_account_id: int,
    amount_cents: int,
    occurred_at: date,
    description: str,
    *,
    entry_id: Optional[int] = None,
    created_by: Optional[int] = None,
    conn=None,
) -> dict:
    """Create a cash movement. When `conn` is given, writes happen on the
    caller's connection/transaction with no internal commit (for callers —
    backend/finance/payments.py — that need this to be part of one larger
    atomic operation); otherwise behaves exactly as before, opening and
    committing its own connection."""
    if amount_cents == 0:
        raise ValueError("O valor não pode ser zero.")
    clean_description = description.strip()
    if not clean_description:
        raise ValueError("Informe a descrição.")
    event_id = _new_id()
    timestamp = db.now()

    def _write(c) -> dict:
        _require_account(c, company, cash_account_id)
        c.execute(
            """
            INSERT INTO cash_events(
                id,company,cash_account_id,amount_cents,occurred_at,description,
                kind,entry_id,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                event_id, company, cash_account_id, amount_cents, occurred_at.isoformat(),
                clean_description, "entry", entry_id, created_by, timestamp,
            ),
        )
        row = c.execute("SELECT * FROM cash_events WHERE id=?", (event_id,)).fetchone()
        return dict(row)

    if conn is not None:
        return _write(conn)
    with db.connection() as own_conn:
        return _write(own_conn)


def transfer(
    company: int,
    from_account_id: int,
    to_account_id: int,
    amount_cents: int,
    occurred_at: date,
    *,
    description: str = "Transferência entre contas",
    created_by: Optional[int] = None,
) -> dict:
    if amount_cents <= 0:
        raise ValueError("O valor da transferência deve ser maior que zero.")
    if from_account_id == to_account_id:
        raise ValueError("As contas de origem e destino devem ser diferentes.")
    group_id = _new_id()
    timestamp = db.now()
    clean_description = description.strip()
    with db.connection() as conn:
        _require_account(conn, company, from_account_id)
        _require_account(conn, company, to_account_id)
        for account_id, signed_amount in ((from_account_id, -amount_cents), (to_account_id, amount_cents)):
            conn.execute(
                """
                INSERT INTO cash_events(
                    id,company,cash_account_id,amount_cents,occurred_at,description,
                    kind,transfer_group,created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _new_id(), company, account_id, signed_amount, occurred_at.isoformat(),
                    clean_description, "transfer", group_id, created_by, timestamp,
                ),
            )
        rows = conn.execute(
            "SELECT * FROM cash_events WHERE transfer_group=?", (group_id,)
        ).fetchall()
    return {"transfer_group": group_id, "events": [dict(row) for row in rows]}


def reverse_event(event_id: int, *, reason: str, created_by: Optional[int] = None) -> dict:
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("Informe o motivo do estorno.")
    reversal_id = _new_id()
    timestamp = db.now()
    with db.connection() as conn:
        event = conn.execute("SELECT * FROM cash_events WHERE id=?", (event_id,)).fetchone()
        if not event:
            raise ValueError("Lançamento não encontrado.")
        if conn.execute(
            "SELECT 1 FROM cash_events WHERE reversed_event_id=?", (event_id,)
        ).fetchone():
            raise ValueError("Este lançamento já foi revertido.")
        conn.execute(
            """
            INSERT INTO cash_events(
                id,company,cash_account_id,amount_cents,occurred_at,description,
                kind,reversed_event_id,created_by,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                reversal_id, event["company"], event["cash_account_id"], -event["amount_cents"],
                timestamp[:10], f"Estorno: {clean_reason}", "reversal", event_id, created_by, timestamp,
            ),
        )
        row = conn.execute("SELECT * FROM cash_events WHERE id=?", (reversal_id,)).fetchone()
    return dict(row)


def account_balance(cash_account_id: int) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS balance FROM cash_events WHERE cash_account_id=?",
            (cash_account_id,),
        ).fetchone()
    return int(row["balance"])


def consolidated_balance(company: int) -> int:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) AS balance FROM cash_events WHERE company=?",
            (company,),
        ).fetchone()
    return int(row["balance"])


def list_events(company: int, *, cash_account_id: Optional[int] = None, limit: int = 200) -> list:
    where = " AND cash_account_id=?" if cash_account_id is not None else ""
    params = (company, cash_account_id, limit) if cash_account_id is not None else (company, limit)
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM cash_events WHERE company=?" + where
                + " ORDER BY occurred_at DESC,id DESC LIMIT ?",
                params,
            )
        ]
