from __future__ import annotations

import itertools
import json
import secrets
from datetime import date, timedelta
from typing import Optional

from .. import database as db


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _linked_item_ids(conn, item_type: str) -> set:
    return {
        row["item_id"]
        for row in conn.execute(
            "SELECT item_id FROM reconciliation_links WHERE item_type=?", (item_type,)
        )
    }


_INFLOW_NATURES = {"revenue", "financing_inflow"}


def _candidate_entries(conn, company: int, around: date, window_days: int) -> list:
    start = (around - timedelta(days=window_days)).isoformat()
    end = (around + timedelta(days=window_days)).isoformat()
    linked = _linked_item_ids(conn, "financial_entry")
    rows = conn.execute(
        """
        SELECT e.*,a.nature FROM financial_entries e
        JOIN finance_accounts a ON a.id=e.account_id
        WHERE e.company=? AND e.due_date BETWEEN ? AND ? AND e.status NOT IN ('cancelled','reversed')
        """,
        (company, start, end),
    ).fetchall()
    candidates = []
    for row in rows:
        if row["id"] in linked:
            continue
        item = dict(row)
        # Cash events are signed (a credit is positive); an entry contributes with
        # the same sign as the cash it represents — revenue/financing in, anything
        # else (expenses, fees, taxes) out — so a fee correctly offsets a credit.
        item["signed_cents"] = item["amount_cents"] if item["nature"] in _INFLOW_NATURES else -item["amount_cents"]
        candidates.append(item)
    return candidates


def _find_matching_combos(target_cents: int, candidates: list, tolerance_cents: int, max_items: int = 3) -> list:
    for size in range(1, min(max_items, len(candidates)) + 1):
        matches = [
            combo
            for combo in itertools.combinations(candidates, size)
            if abs(sum(item["signed_cents"] for item in combo) - target_cents) <= tolerance_cents
        ]
        if matches:
            return matches
    return []


def _row_to_group(conn, group_id: int) -> dict:
    group = dict(conn.execute("SELECT * FROM reconciliation_groups WHERE id=?", (group_id,)).fetchone())
    links = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM reconciliation_links WHERE group_id=? ORDER BY id", (group_id,)
        )
    ]
    group["links"] = links
    group["candidates"] = json.loads(group["candidates_json"]) if group["candidates_json"] else []
    del group["candidates_json"]
    return group


def get_group(group_id: int) -> dict:
    with db.connection() as conn:
        group = conn.execute("SELECT id FROM reconciliation_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise ValueError("Grupo de conciliação não encontrado.")
        return _row_to_group(conn, group_id)


def suggest(company: int, cash_event_id: int, *, tolerance_cents: int = 0, window_days: int = 5) -> dict:
    with db.connection() as conn:
        cash_event = conn.execute(
            "SELECT * FROM cash_events WHERE id=? AND company=?", (cash_event_id, company)
        ).fetchone()
        if not cash_event:
            raise ValueError("Lançamento de caixa não encontrado.")
        if cash_event_id in _linked_item_ids(conn, "cash_event"):
            raise ValueError("Este lançamento já está em uma conciliação.")
        cash_event = dict(cash_event)

    target = cash_event["amount_cents"]
    around = date.fromisoformat(cash_event["occurred_at"])
    with db.connection() as conn:
        candidates = _candidate_entries(conn, company, around, window_days)
    matches = _find_matching_combos(target, candidates, tolerance_cents)

    group_id = _new_id()
    timestamp = db.now()
    if len(matches) == 1:
        status, chosen, candidates_json = "auto_matched", matches[0], None
    elif len(matches) > 1:
        status, chosen = "suggested", ()
        candidates_json = json.dumps([
            {"entry_ids": [item["id"] for item in combo], "total_cents": sum(item["signed_cents"] for item in combo)}
            for combo in matches
        ])
    else:
        status, chosen, candidates_json = "unmatched", (), None

    difference = target - sum(item["signed_cents"] for item in chosen)
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO reconciliation_groups(
                id,company,status,difference_cents,tolerance_cents,method,candidates_json,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                group_id, company, status, difference, tolerance_cents,
                "auto" if status == "auto_matched" else None, candidates_json, timestamp, timestamp,
            ),
        )
        conn.execute(
            "INSERT INTO reconciliation_links(id,group_id,item_type,item_id,amount_cents,created_at) VALUES(?,?,?,?,?,?)",
            (_new_id(), group_id, "cash_event", cash_event_id, cash_event["amount_cents"], timestamp),
        )
        for entry in chosen:
            conn.execute(
                "INSERT INTO reconciliation_links(id,group_id,item_type,item_id,amount_cents,created_at) VALUES(?,?,?,?,?,?)",
                (_new_id(), group_id, "financial_entry", entry["id"], entry["amount_cents"], timestamp),
            )
        return _row_to_group(conn, group_id)


def confirm(
    group_id: int,
    entry_ids: list,
    *,
    payment_ids: Optional[list] = None,
    accept_partial: bool = False,
    created_by: Optional[int] = None,
) -> dict:
    """Manually resolve a `suggested`/`unmatched` group against whole
    `financial_entries` (`entry_ids`, the original, unchanged behavior) and/or
    specific `obligation_payments` rows (`payment_ids`, new).

    `payment_ids` lets a bank credit/debit be matched against ONE SPECIFIC
    partial-payment slice of an obligation instead of the obligation's
    entire remaining amount — e.g. an entry paid via two separate 40 000 and
    60 000 obligation_payments rows (backend/finance/payments.py::
    record_payment) can have each payment reconciled independently, rather
    than a single reconciliation forcing the whole 100 000 entry to be
    treated as one unit ("não consumir a obrigação inteira no primeiro
    vínculo"). A payment is reversed-out via
    backend/finance/payments.py::reverse_payment, never through this
    module, so a reversed payment (`reversed_at IS NOT NULL`) can never be
    reconciled here. This is purely additive: item_type='financial_entry'
    and 'cash_event' rows written by every existing caller are completely
    unaffected, and old groups (which only ever contain those two item
    types) read back exactly as before via _row_to_group.
    """
    payment_ids = payment_ids or []
    timestamp = db.now()
    with db.connection() as conn:
        group = conn.execute("SELECT * FROM reconciliation_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise ValueError("Grupo de conciliação não encontrado.")
        if group["status"] in ("manual_matched", "auto_matched", "ignored"):
            raise ValueError("Este grupo já foi resolvido.")
        anchor = conn.execute(
            "SELECT * FROM reconciliation_links WHERE group_id=? AND item_type='cash_event'", (group_id,)
        ).fetchone()
        linked = _linked_item_ids(conn, "financial_entry")
        total = 0
        for entry_id in entry_ids:
            if entry_id in linked:
                raise ValueError("Um dos lançamentos já está em outra conciliação.")
            entry = conn.execute(
                """
                SELECT e.*,a.nature FROM financial_entries e
                JOIN finance_accounts a ON a.id=e.account_id
                WHERE e.id=?
                """,
                (entry_id,),
            ).fetchone()
            if not entry:
                raise ValueError("Lançamento não encontrado.")
            conn.execute(
                "INSERT INTO reconciliation_links(id,group_id,item_type,item_id,amount_cents,created_at) VALUES(?,?,?,?,?,?)",
                (_new_id(), group_id, "financial_entry", entry_id, entry["amount_cents"], timestamp),
            )
            total += entry["amount_cents"] if entry["nature"] in _INFLOW_NATURES else -entry["amount_cents"]

        linked_payments = _linked_item_ids(conn, "payment")
        for payment_id in payment_ids:
            if payment_id in linked_payments:
                raise ValueError("Um dos pagamentos já está em outra conciliação.")
            payment = conn.execute(
                "SELECT * FROM obligation_payments WHERE id=? AND company=?",
                (payment_id, group["company"]),
            ).fetchone()
            if not payment:
                raise ValueError("Pagamento não encontrado.")
            if payment["reversed_at"] is not None:
                raise ValueError("Este pagamento foi estornado e não pode ser conciliado.")
            conn.execute(
                "INSERT INTO reconciliation_links(id,group_id,item_type,item_id,amount_cents,created_at) VALUES(?,?,?,?,?,?)",
                (_new_id(), group_id, "payment", payment_id, payment["amount_cents"], timestamp),
            )
            # obligation_payments only ever records money PAID OUT (an
            # expense/obligation settlement), never a receivable — always an
            # outflow, unlike a financial_entry whose sign depends on its
            # account's nature.
            total += -payment["amount_cents"]

        difference = anchor["amount_cents"] - total
        if difference == 0:
            status = "manual_matched"
        elif accept_partial:
            status = "partial"
        else:
            status = "divergent"
        conn.execute(
            """
            UPDATE reconciliation_groups
            SET status=?,difference_cents=?,candidates_json=NULL,method='manual',
                confirmed_by=?,confirmed_at=?,updated_at=?
            WHERE id=?
            """,
            (status, difference, created_by, timestamp, timestamp, group_id),
        )
        return _row_to_group(conn, group_id)


def undo(group_id: int, *, reason: str) -> dict:
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValueError("Informe o motivo.")
    timestamp = db.now()
    with db.connection() as conn:
        group = conn.execute("SELECT * FROM reconciliation_groups WHERE id=?", (group_id,)).fetchone()
        if not group:
            raise ValueError("Grupo de conciliação não encontrado.")
        conn.execute("DELETE FROM reconciliation_links WHERE group_id=?", (group_id,))
        conn.execute(
            "UPDATE reconciliation_groups SET status='ignored',notes=?,updated_at=? WHERE id=?",
            (clean_reason, timestamp, group_id),
        )
        return _row_to_group(conn, group_id)


def list_groups(company: int, *, status: Optional[str] = None) -> list:
    where = " AND status=?" if status else ""
    params = (company, status) if status else (company,)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id FROM reconciliation_groups WHERE company=?" + where + " ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [_row_to_group(conn, row["id"]) for row in rows]
