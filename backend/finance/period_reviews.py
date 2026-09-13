"""Monthly managerial review of a competence month (task C6).

A review is a sign-off that someone looked at the month's expenses, tied to a
revision: a SHA-256 of everything the managerial result reads for that month
(sales dataset version, entries with their classification, payment and
settlement events, reconciliation links and effective budget parameters). The
stored status is only honoured while the current revision still matches, so a
new expense, a reclassification with the same total, a reversed payment or a
new sales sync all put the month back "em apuração" automatically.

It is not an accounting close: nothing is locked, and an all-green checklist
does not certify that no expense is missing — reviewing always requires the
user's explicit acknowledgement.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Optional

from .. import database as db


_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ReviewConflict(RuntimeError):
    """The month's data changed since the caller computed its revision (HTTP 409)."""


class ReviewValidation(ValueError):
    def __init__(self, message: str, fields=None):
        super().__init__(message)
        self.fields = list(fields or [])


def _rows(conn, sql: str, params) -> list:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _period_data(conn, company: int, period: str) -> dict:
    if not _PERIOD.match(period or ""):
        raise ReviewValidation("Competência inválida.", fields=["period"])
    sales = conn.execute(
        "SELECT version,updated_at,documents FROM datasets WHERE company=? AND resource='sales' AND period=?",
        (company, period),
    ).fetchone()
    entries = _rows(
        conn,
        """
        SELECT e.id,e.account_id,e.counterparty_id,e.amount_cents,e.status,e.source,e.store,e.version,
               a.nature,a.system_key,a.sensitive,a.archived
        FROM financial_entries e
        JOIN finance_accounts a ON a.id=e.account_id
        WHERE e.company=? AND e.competence=?
        ORDER BY e.id
        """,
        (company, period),
    )
    entry_ids = [row["id"] for row in entries]
    events, payments, links = [], [], []
    if entry_ids:
        marks = ",".join("?" for _ in entry_ids)
        events = _rows(
            conn,
            f"SELECT id,entry_id,event_type,amount_cents,occurred_at FROM financial_events WHERE entry_id IN ({marks}) ORDER BY id",
            entry_ids,
        )
        payments = _rows(
            conn,
            f"""
            SELECT id,obligation_id,amount_cents,principal_cents,interest_cents,cash_event_id,
                   financial_event_id,reversed_at
            FROM obligation_payments
            WHERE company=? AND obligation_kind='entry' AND obligation_id IN ({marks})
            ORDER BY id
            """,
            (company, *entry_ids),
        )
        links = _rows(
            conn,
            f"""
            SELECT l.item_type,l.item_id,l.group_id,g.status
            FROM reconciliation_links l JOIN reconciliation_groups g ON g.id=l.group_id
            WHERE l.item_type='financial_entry' AND l.item_id IN ({marks})
            ORDER BY l.item_id,l.group_id
            """,
            entry_ids,
        )
    payment_ids = [row["id"] for row in payments]
    if payment_ids:
        marks = ",".join("?" for _ in payment_ids)
        links += _rows(
            conn,
            f"""
            SELECT l.item_type,l.item_id,l.group_id,g.status
            FROM reconciliation_links l JOIN reconciliation_groups g ON g.id=l.group_id
            WHERE l.item_type='payment' AND l.item_id IN ({marks})
            ORDER BY l.item_id,l.group_id
            """,
            payment_ids,
        )
    parameters = _rows(
        conn,
        """
        SELECT key,value_json,store,category,product,effective_from,effective_to,version
        FROM management_parameters
        WHERE company=? AND effective_from<=? AND (effective_to IS NULL OR effective_to>=?)
        ORDER BY key,store,category,product,effective_from
        """,
        (company, period + "-31", period + "-01"),
    )
    return {
        "sales": dict(sales) if sales else None,
        "entries": entries,
        "events": events,
        "payments": payments,
        "links": links,
        "parameters": parameters,
    }


def _revision(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def period_revision(company: int, period: str) -> str:
    with db.connection() as conn:
        return _revision(_period_data(conn, company, period))


def _checks(data: dict) -> list:
    forecasts = sum(1 for entry in data["entries"] if entry["status"] == "forecast")
    payment_event_ids = {p["financial_event_id"] for p in data["payments"] if p["financial_event_id"] is not None}
    legacy_settlements = sum(
        1 for event in data["events"] if event["event_type"] == "settled" and event["id"] not in payment_event_ids
    )
    unlinked_payments = sum(1 for p in data["payments"] if p["cash_event_id"] is None and p["reversed_at"] is None)
    without_cash = legacy_settlements + unlinked_payments
    divergences = len({link["group_id"] for link in data["links"] if link["status"] in ("divergent", "partial")})
    return [
        {
            "key": "sales_available",
            "label": "Vendas do mês sincronizadas do Mobne",
            "ok": data["sales"] is not None,
            "count": None,
            "detail": "" if data["sales"] is not None else "Sem vendas sincronizadas: receita e resultado ficam indisponíveis.",
        },
        {
            "key": "forecasts_pending",
            "label": "Recorrências previstas por confirmar",
            "ok": forecasts == 0,
            "count": forecasts,
            "detail": "Valores previstos não entram no realizado até serem confirmados em Custos e despesas." if forecasts else "",
        },
        {
            "key": "payments_without_cash_link",
            "label": "Pagamentos sem movimento de caixa vinculado",
            "ok": without_cash == 0,
            "count": without_cash,
            "detail": "Pagamentos registrados antes do vínculo com o caixa; confira no Fluxo de caixa." if without_cash else "",
        },
        {
            "key": "divergences",
            "label": "Conciliações com divergência",
            "ok": divergences == 0,
            "count": divergences,
            "detail": "Há grupos de conciliação parciais ou divergentes ligados a este mês." if divergences else "",
        },
    ]


def get_review(company: int, period: str) -> dict:
    with db.connection() as conn:
        data = _period_data(conn, company, period)
        row = conn.execute(
            "SELECT * FROM period_reviews WHERE company=? AND period=?", (company, period)
        ).fetchone()
    revision = _revision(data)
    row = dict(row) if row else None
    reviewed = bool(row and row["status"] == "reviewed" and row["revision"] == revision)
    return {
        "period": period,
        "status": "reviewed" if reviewed else "in_progress",
        "revision": revision,
        "reviewed_at": row["reviewed_at"] if reviewed else None,
        "reviewed_by": str(row["reviewed_by"]) if reviewed and row["reviewed_by"] is not None else None,
        "reason": row["reason"] if row else "",
        "changed_since_review": bool(row and row["status"] == "reviewed" and row["revision"] != revision),
        "checks": _checks(data),
    }


def record_review(
    company: int,
    period: str,
    *,
    action: str,
    expected_revision: str,
    reason: str,
    acknowledged: bool,
    actor_id: Optional[int],
) -> dict:
    if action not in ("review", "reopen"):
        raise ReviewValidation("Ação inválida.", fields=["action"])
    clean_reason = (reason or "").strip()
    if action == "review" and not acknowledged:
        raise ReviewValidation(
            "Confirme que conferiu as despesas e lançamentos do mês antes de marcar como revisado.",
            fields=["acknowledged"],
        )
    if action == "reopen" and not clean_reason:
        raise ReviewValidation("Informe o motivo para reabrir a apuração.", fields=["reason"])
    with db.connection() as conn:
        current = _revision(_period_data(conn, company, period))
        if current != expected_revision:
            raise ReviewConflict(
                "Os dados do mês mudaram desde que você abriu a revisão. Recarregue e confira de novo."
            )
        timestamp = db.now()
        reviewing = action == "review"
        conn.execute(
            """
            INSERT INTO period_reviews(company,period,revision,status,reviewed_by,reviewed_at,reason,updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(company,period) DO UPDATE SET
                revision=excluded.revision, status=excluded.status, reviewed_by=excluded.reviewed_by,
                reviewed_at=excluded.reviewed_at, reason=excluded.reason, updated_at=excluded.updated_at
            """,
            (
                company, period, current, "reviewed" if reviewing else "in_progress",
                actor_id if reviewing else None, timestamp if reviewing else None,
                clean_reason, timestamp,
            ),
        )
    return get_review(company, period)
