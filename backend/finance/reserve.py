"""Atualizar saldo da Reserva Stone.

The Reserva Stone yields 100–110% of CDI inside Stone, and that yield never
shows up in the Conta Stone statement: the panel only sees the transfers in
and out. The partner reads the real balance in the Stone app and the panel
books the gap:

- the first time, the gap is money that was already there before the
  imported statements, so it becomes an opening balance (no effect on the
  management result);
- afterwards a positive gap is investment income (`investment_income`) and a
  negative one an investment cost such as IR/IOF (`investment_taxes`), both
  already settled into the reserve account.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Optional

from .. import database as db
from . import accounts, ledger
from .entries import EntryCommand, create_entry

SOURCE = "reserve_balance"
OPENING_DESCRIPTION = "Saldo inicial da Reserva Stone"


def _account(conn, company: int, cash_account_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM cash_accounts WHERE id=? AND company=?", (cash_account_id, company)
    ).fetchone()
    if not row:
        raise ValueError("Conta de caixa não encontrada.")
    return dict(row)


def _panel_balance(conn, cash_account_id: int, as_of: date) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) AS b FROM cash_events WHERE cash_account_id=? AND occurred_at<=?",
        (cash_account_id, as_of.isoformat()),
    ).fetchone()
    return int(row["b"])


def _history(conn, company: int, cash_account_id: int) -> dict:
    first = conn.execute(
        "SELECT MIN(occurred_at) AS d FROM cash_events WHERE cash_account_id=?", (cash_account_id,)
    ).fetchone()["d"]
    last = conn.execute(
        "SELECT MAX(due_date) AS d FROM financial_entries WHERE company=? AND source=? AND external_id LIKE ?",
        (company, SOURCE, f"{cash_account_id}:%"),
    ).fetchone()["d"]
    # The opening event is dated before the statements; the day the partner
    # checked the Stone app is kept in its description.
    opening = conn.execute(
        "SELECT description FROM cash_events WHERE cash_account_id=? AND description LIKE ? ORDER BY created_at LIMIT 1",
        (cash_account_id, OPENING_DESCRIPTION + "%"),
    ).fetchone()
    checked = opening["description"][-11:-1] if opening else None
    return {
        "first_event_date": first,
        "last_update_date": max(d for d in (last, checked) if d) if (last or checked) else None,
        "has_opening": opening is not None,
    }


def balance_status(company: int, cash_account_id: int, as_of: date) -> dict:
    with db.connection() as conn:
        account = _account(conn, company, cash_account_id)
        history = _history(conn, company, cash_account_id)
        return {
            "cash_account_id": account["id"],
            "name": account["name"],
            "as_of": as_of.isoformat(),
            "panel_balance_cents": _panel_balance(conn, cash_account_id, as_of),
            **history,
            # Nothing was ever reconciled: the first gap is an opening balance.
            "suggest_opening": not history["has_opening"] and history["last_update_date"] is None,
        }


def update_balance(
    company: int,
    cash_account_id: int,
    real_balance_cents: int,
    as_of: date,
    *,
    opening: bool,
    created_by: Optional[int] = None,
) -> dict:
    if real_balance_cents < 0:
        raise ValueError("O saldo da Reserva não pode ser negativo.")
    if as_of > date.today():
        raise ValueError("Use a data de hoje ou uma data passada.")
    # Resolved before the transaction: account_by_key may seed on its own connection.
    categories = {key: accounts.account_by_key(company, key) for key in ("investment_income", "investment_taxes")}
    with db.connection() as conn:
        _account(conn, company, cash_account_id)
        history = _history(conn, company, cash_account_id)
        if opening and history["has_opening"]:
            raise ValueError("O saldo inicial desta conta já foi lançado; a diferença agora é rendimento.")
        if history["last_update_date"] and as_of.isoformat() < history["last_update_date"]:
            raise ValueError(
                f"Já existe atualização em {history['last_update_date']}: informe um saldo desta data em diante."
            )
        panel = _panel_balance(conn, cash_account_id, as_of)
        difference = real_balance_cents - panel
        base = {"panel_balance_cents": panel, "real_balance_cents": real_balance_cents, "difference_cents": difference}
        if difference == 0:
            return {**base, "booked": "none"}

        if opening:
            # Dated before everything the panel knows, so it never looks like
            # money that came in during the imported period.
            first = history["first_event_date"]
            day = date.fromisoformat(first) - timedelta(days=1) if first else as_of
            event = ledger.post_cash_event(
                company, cash_account_id, difference, day,
                f"{OPENING_DESCRIPTION} (conferido em {as_of.isoformat()})",
                created_by=created_by, conn=conn,
            )
            return {**base, "booked": "opening", "cash_event_id": event["id"], "date": day.isoformat()}

        key = "investment_income" if difference > 0 else "investment_taxes"
        account = categories[key]
        label = "Rendimento da Reserva Stone" if difference > 0 else "Ajuste negativo da Reserva Stone (IR/IOF)"
        entry = create_entry(EntryCommand(
            company_id=company, account_id=account["id"], amount_cents=abs(difference),
            competence=as_of.isoformat()[:7], due_date=as_of, source=SOURCE,
            external_id=f"{cash_account_id}:{as_of.isoformat()}:{secrets.token_hex(4)}",
            description=label,
            notes=f"Saldo no app da Stone {real_balance_cents / 100:.2f}; no painel {panel / 100:.2f}.",
            created_by=created_by,
        ), conn=conn)
        # Already settled into the reserve: never an open bill.
        conn.execute(
            "UPDATE financial_entries SET status='paid',version=version+1,updated_at=? WHERE id=?",
            (db.now(), entry["id"]),
        )
        event = ledger.post_cash_event(
            company, cash_account_id, difference, as_of, label,
            entry_id=entry["id"], created_by=created_by, conn=conn,
        )
        return {
            **base,
            "booked": "income" if difference > 0 else "expense",
            "entry_id": entry["id"], "cash_event_id": event["id"], "date": as_of.isoformat(),
        }
