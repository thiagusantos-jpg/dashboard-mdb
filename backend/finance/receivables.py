from __future__ import annotations

import secrets
from datetime import date
from typing import Optional

from .. import database as db
from ..integrations import records
from ..integrations.stone_receivables import parse_conciliation_xml
from . import accounts
from .entries import EntryCommand, create_entry


def _new_id() -> int:
    return secrets.randbits(63) or 1


def sync_receivables(company: int, cash_account_id: int, xml_content: bytes) -> dict:
    receivables = parse_conciliation_xml(xml_content)
    imported = 0
    duplicates = 0
    for receivable in receivables:
        external_id = f"{receivable.transaction_key}:{receivable.installment_number}"
        already_seen = _record_exists(company, cash_account_id, external_id)
        payload = {
            "gross_cents": receivable.gross_cents,
            "fee_cents": receivable.fee_cents,
            "net_cents": receivable.net_cents,
            "settlement_date": receivable.settlement_date,
            "brand_id": receivable.brand_id,
        }
        records.upsert_external_record(
            company, "stone_receivable", cash_account_id, external_id, "1", payload,
        )
        if already_seen:
            duplicates += 1
            continue

        fee_entry_id = None
        if receivable.fee_cents > 0:
            fee_account = accounts.account_by_key(company, "acquiring_fees")
            settlement = receivable.settlement_date or date.today().isoformat()
            entry = create_entry(EntryCommand(
                company_id=company, account_id=fee_account["id"], amount_cents=receivable.fee_cents,
                competence=settlement[:7], due_date=date.fromisoformat(settlement), source="stone_receivable",
                external_id=external_id,
                description=f"Taxa de adquirente — {receivable.transaction_key} parcela {receivable.installment_number}",
            ))
            fee_entry_id = entry["id"]

        timestamp = db.now()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO stone_receivables(
                    id,company,cash_account_id,transaction_key,installment_number,brand_id,
                    gross_cents,fee_cents,net_cents,settlement_date,fee_entry_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _new_id(), company, cash_account_id, receivable.transaction_key,
                    receivable.installment_number, receivable.brand_id, receivable.gross_cents,
                    receivable.fee_cents, receivable.net_cents, receivable.settlement_date,
                    fee_entry_id, timestamp,
                ),
            )
        imported += 1
    return {"total": len(receivables), "imported": imported, "duplicates": duplicates}


def _record_exists(company: int, cash_account_id, external_id: str) -> bool:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM external_records
            WHERE company=? AND source='stone_receivable' AND account_id=? AND external_id=? AND version='1'
            """,
            (company, str(cash_account_id), external_id),
        ).fetchone()
    return row is not None


def expected_settlements(company: int, start: date, end: date) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT settlement_date,COUNT(*) AS count,SUM(net_cents) AS net_cents,
                   SUM(gross_cents) AS gross_cents,SUM(fee_cents) AS fee_cents
            FROM stone_receivables
            WHERE company=? AND settlement_date BETWEEN ? AND ?
            GROUP BY settlement_date ORDER BY settlement_date
            """,
            (company, start.isoformat(), end.isoformat()),
        ).fetchall()
    return [dict(row) for row in rows]


def effective_fee_report(company: int, start: date, end: date) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(gross_cents),0) AS gross_cents,COALESCE(SUM(fee_cents),0) AS fee_cents,
                   COALESCE(SUM(net_cents),0) AS net_cents
            FROM stone_receivables
            WHERE company=? AND settlement_date BETWEEN ? AND ?
            """,
            (company, start.isoformat(), end.isoformat()),
        ).fetchone()
    gross = int(row["gross_cents"])
    fee = int(row["fee_cents"])
    net = int(row["net_cents"])
    effective_rate = round(fee / gross * 100, 4) if gross else None
    contracted_rate = accounts.resolve_parameter(company, "contracted_mdr_rate", end.isoformat())
    return {
        "gross_cents": gross,
        "fee_cents": fee,
        "net_cents": net,
        "effective_rate_pct": effective_rate,
        "contracted_rate_pct": contracted_rate,
        "variance_pct": (
            round(effective_rate - contracted_rate, 4)
            if effective_rate is not None and contracted_rate is not None
            else None
        ),
    }
