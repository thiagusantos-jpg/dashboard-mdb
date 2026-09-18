from __future__ import annotations

import json
import secrets
from datetime import date
from typing import Optional

from .. import database as db
from ..integrations import records
from ..integrations.stone_receivables import (
    looks_like_receivables_csv,
    parse_conciliation_xml,
    parse_receivables_csv,
)
from . import accounts
from .entries import EntryCommand, create_entry


def _new_id() -> int:
    return secrets.randbits(63) or 1


def sync_receivables(company: int, cash_account_id: int, xml_content: bytes) -> dict:
    if looks_like_receivables_csv(xml_content):
        return sync_receivables_csv(company, cash_account_id, xml_content)
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


MONTHLY_FEE_CATEGORY = "Cobrança"


def sync_receivables_csv(company: int, cash_account_id: int, content: bytes) -> dict:
    """Portal CSV import, in one transaction (a quarter is ~7k rows; one
    connection per row would not fit a serverless request). Fees are booked
    once per settlement day — MDR in "Taxas de adquirentes", the anticipation
    discount in "Antecipação de recebíveis" — instead of one expense per sale.

    The report is also the only place the monthly terminal fee always shows up
    (the statement only has it when Stone charges it apart from a deposit), so
    each "Cobrança" row becomes one "Mensalidade Stone" expense, already paid:
    its cash effect is the report row itself (a smaller deposit) or the
    statement line, never a bill to pay."""
    receivables = parse_receivables_csv(content)
    fee_accounts = {
        "mdr": accounts.account_by_key(company, "acquiring_fees"),
        "advance": accounts.account_by_key(company, "receivables_advance"),
        "monthly": accounts.account_by_key(company, "payment_terminal_rent"),
    }
    imported = duplicates = 0
    fees_by_day: dict = {}
    monthly_fees = []
    timestamp = db.now()
    record_rows: list = []
    receivable_rows: list = []
    with db.connection() as conn:
        existing = {
            row["external_id"]
            for row in conn.execute(
                "SELECT external_id FROM external_records WHERE company=? AND source='stone_receivable' AND account_id=?",
                (company, str(cash_account_id)),
            )
        }
        known_keys = {
            (row["transaction_key"], row["installment_number"])
            for row in conn.execute(
                "SELECT transaction_key,installment_number FROM stone_receivables WHERE company=?", (company,),
            )
        }
        for receivable in receivables:
            external_id = f"{receivable.transaction_key}:{receivable.installment_number}"
            key = (receivable.transaction_key, receivable.installment_number)
            if external_id in existing or key in known_keys:
                duplicates += 1
                continue
            payload = {
                "gross_cents": receivable.gross_cents,
                "fee_cents": receivable.fee_cents,
                "net_cents": receivable.net_cents,
                "settlement_date": receivable.settlement_date,
                "brand_id": receivable.brand_id,
                "category": receivable.category,
            }
            # Unseen keys only (checked above), so this is the insert half of
            # records.upsert_external_record, batched below.
            record_rows.append((
                _new_id(), company, "stone_receivable", str(cash_account_id), external_id, "1",
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                records._canonical_hash(payload), timestamp, timestamp,
            ))
            receivable_rows.append((
                _new_id(), company, cash_account_id, receivable.transaction_key,
                receivable.installment_number, receivable.brand_id, receivable.gross_cents,
                receivable.fee_cents, receivable.net_cents, receivable.settlement_date,
                None, timestamp,
            ))
            existing.add(external_id)
            known_keys.add(key)
            if receivable.category == MONTHLY_FEE_CATEGORY and receivable.net_cents < 0:
                monthly_fees.append(receivable)
            day = fees_by_day.setdefault(receivable.settlement_date, {"mdr": 0, "advance": 0, "sales": 0})
            day["advance"] += receivable.advance_fee_cents
            day["mdr"] += receivable.fee_cents - receivable.advance_fee_cents
            day["sales"] += 1 if receivable.category == "Venda" else 0
            imported += 1
        # One round trip per table instead of four per row: against the hosted
        # database a quarter row by row outlived the function's time limit.
        if record_rows:
            conn.executemany(
                """
                INSERT INTO external_records(
                    id,company,source,account_id,external_id,version,payload_json,payload_hash,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                record_rows,
            )
        if receivable_rows:
            conn.executemany(
                """
                INSERT INTO stone_receivables(
                    id,company,cash_account_id,transaction_key,installment_number,brand_id,
                    gross_cents,fee_cents,net_cents,settlement_date,fee_entry_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                receivable_rows,
            )

        fee_entries = 0
        fee_cents = 0
        for fee in monthly_fees:
            settlement = fee.settlement_date
            entry = create_entry(EntryCommand(
                company_id=company, account_id=fee_accounts["monthly"]["id"], amount_cents=-fee.net_cents,
                competence=settlement[:7], due_date=date.fromisoformat(settlement),
                source="stone_receivable",
                external_id=f"{cash_account_id}:monthly:{fee.transaction_key}",
                description=f"Mensalidade Stone — {settlement[5:7]}/{settlement[:4]}",
            ), conn=conn)
            conn.execute(
                "UPDATE financial_entries SET status='paid',version=version+1,updated_at=? WHERE id=?",
                (db.now(), entry["id"]),
            )
        for settlement, day in sorted(fees_by_day.items()):
            for kind, label in (("mdr", "Taxas Stone"), ("advance", "Antecipação Stone")):
                amount = day[kind]
                if amount <= 0:
                    continue
                # A later export may bring more rows for a day already booked.
                taken = conn.execute(
                    "SELECT COUNT(*) AS n FROM financial_entries WHERE company=? AND source=? AND external_id LIKE ?",
                    (company, "stone_receivable", f"{cash_account_id}:{kind}:{settlement}:%"),
                ).fetchone()["n"]
                create_entry(EntryCommand(
                    company_id=company, account_id=fee_accounts[kind]["id"], amount_cents=amount,
                    competence=settlement[:7], due_date=date.fromisoformat(settlement),
                    source="stone_receivable",
                    external_id=f"{cash_account_id}:{kind}:{settlement}:{taken + 1}",
                    description=f"{label} de {settlement[8:10]}/{settlement[5:7]} ({day['sales']} venda(s))",
                ), conn=conn)
                fee_entries += 1
                fee_cents += amount
    return {
        "total": len(receivables), "imported": imported, "duplicates": duplicates,
        "fee_entries": fee_entries if imported else 0,
        "fee_cents": fee_cents if imported else 0,
        "monthly_fees": len(monthly_fees),
        "monthly_fee_cents": -sum(fee.net_cents for fee in monthly_fees),
    }


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
