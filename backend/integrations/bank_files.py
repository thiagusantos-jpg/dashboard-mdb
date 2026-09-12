from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

from .. import database as db
from ..finance import ledger
from . import records


MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000


class BankFileError(ValueError):
    pass


@dataclass(frozen=True)
class ExternalTransaction:
    date: str
    amount_cents: int
    description: str
    external_id: str


def parse_bank_file(content: bytes, filename: str) -> list:
    if len(content) > MAX_BYTES:
        raise BankFileError("Arquivo maior que 10 MiB.")
    lower = filename.lower()
    if lower.endswith(".ofx"):
        transactions = _parse_ofx(content)
    elif lower.endswith(".csv"):
        transactions = _parse_csv(content)
    else:
        raise BankFileError("Formato não suportado. Envie um arquivo .ofx ou .csv.")
    if len(transactions) > MAX_ROWS:
        raise BankFileError("Arquivo com mais de 50.000 linhas.")
    return transactions


def _to_cents(raw: str) -> int:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        raise BankFileError(f"Valor inválido: {raw!r}") from None
    return int((value * 100).to_integral_value())


def _ofx_field(block: str, tag: str) -> Optional[str]:
    match = re.search(rf"<{tag}>([^\r\n<]*)", block, re.IGNORECASE)
    return match.group(1).strip() if match else None


def _parse_ofx(content: bytes) -> list:
    text = content.decode("utf-8", errors="replace")
    blocks = re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.DOTALL | re.IGNORECASE)
    if not blocks:
        raise BankFileError("Nenhuma transação encontrada no OFX.")
    transactions = []
    for block in blocks:
        amount_raw = _ofx_field(block, "TRNAMT")
        posted = _ofx_field(block, "DTPOSTED")
        fitid = _ofx_field(block, "FITID")
        memo = _ofx_field(block, "MEMO") or _ofx_field(block, "NAME") or ""
        if amount_raw is None or posted is None or fitid is None:
            raise BankFileError("Transação OFX incompleta (faltam TRNAMT, DTPOSTED ou FITID).")
        iso_date = f"{posted[0:4]}-{posted[4:6]}-{posted[6:8]}"
        transactions.append(ExternalTransaction(
            date=iso_date, amount_cents=_to_cents(amount_raw), description=memo, external_id=fitid,
        ))
    return transactions


_CSV_DATE_KEYS = {"data", "date"}
_CSV_AMOUNT_KEYS = {"valor", "amount", "value"}
_CSV_DESC_KEYS = {"descricao", "descrição", "description", "memo", "historico", "histórico"}
_CSV_ID_KEYS = {"fitid", "id", "external_id"}


def _find_column(fieldnames, candidates) -> Optional[str]:
    for name in fieldnames:
        if name and name.strip().lower() in candidates:
            return name
    return None


def _parse_csv_date(raw: str) -> str:
    for sep in ("-", "/"):
        parts = raw.split(sep)
        if len(parts) == 3:
            y, m, d = parts if len(parts[0]) == 4 else parts[::-1]
            try:
                return date(int(y), int(m), int(d)).isoformat()
            except ValueError:
                continue
    raise BankFileError(f"Data inválida: {raw!r}")


def _parse_csv(content: bytes) -> list:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise BankFileError("CSV vazio ou sem cabeçalho.")
    date_col = _find_column(reader.fieldnames, _CSV_DATE_KEYS)
    amount_col = _find_column(reader.fieldnames, _CSV_AMOUNT_KEYS)
    desc_col = _find_column(reader.fieldnames, _CSV_DESC_KEYS)
    id_col = _find_column(reader.fieldnames, _CSV_ID_KEYS)
    if not date_col or not amount_col:
        raise BankFileError("O CSV precisa de colunas de data e valor.")
    transactions = []
    for index, row in enumerate(reader):
        raw_date = (row.get(date_col) or "").strip()
        raw_amount = (row.get(amount_col) or "").strip()
        if not raw_date or not raw_amount:
            continue
        iso_date = _parse_csv_date(raw_date)
        description = (row.get(desc_col) or "").strip() if desc_col else ""
        external_id = (row.get(id_col) or "").strip() if id_col else ""
        if not external_id:
            external_id = hashlib.sha256(
                f"{iso_date}|{raw_amount}|{description}|{index}".encode()
            ).hexdigest()[:16]
        transactions.append(ExternalTransaction(
            date=iso_date, amount_cents=_to_cents(raw_amount), description=description,
            external_id=external_id,
        ))
    return transactions


def _external_record_exists(company: int, account_id, external_id: str, version: str) -> bool:
    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM external_records
            WHERE company=? AND source='bank_file' AND account_id=? AND external_id=? AND version=?
            """,
            (company, str(account_id), external_id, version),
        ).fetchone()
    return row is not None


def import_bank_file(company: int, cash_account_id: int, filename: str, content: bytes) -> dict:
    transactions = parse_bank_file(content, filename)
    imported = 0
    duplicates = 0
    for tx in transactions:
        version = "1"
        already_seen = _external_record_exists(company, cash_account_id, tx.external_id, version)
        payload = {"date": tx.date, "amount_cents": tx.amount_cents, "description": tx.description}
        records.upsert_external_record(
            company, "bank_file", cash_account_id, tx.external_id, version, payload,
        )
        if already_seen:
            duplicates += 1
            continue
        ledger.post_cash_event(
            company, cash_account_id, tx.amount_cents, date.fromisoformat(tx.date),
            tx.description or f"Importado ({tx.external_id})",
        )
        imported += 1
    return {
        "total": len(transactions),
        "imported": imported,
        "duplicates": duplicates,
    }
