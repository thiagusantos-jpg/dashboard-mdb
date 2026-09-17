from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from .. import database as db
from ..finance import ledger
from . import records


MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000

# Provider tag used both as the external_records `source` (existing
# convention) and as the `bank_cash_links.provider` value for this import
# path — mirrors the provider-tagging vocabulary used elsewhere in
# backend/integrations (e.g. open_finance.py/stone_receivables.py's
# provider-neutral contracts), kept as a plain string since bank_file
# imports have no per-integration provider name of their own.
PROVIDER_BANK_FILE = "bank_file"

# How many days around an imported transaction's date to look for a
# candidate cash_event — mirrors the window_days precedent in
# backend/finance/reconciliation.py::suggest, but kept tight since a
# bank-line-to-cash-event match represents the SAME real-world money
# movement (not an independent entry being reconciled against a credit),
# so dates should already be very close.
CANDIDATE_WINDOW_DAYS = 3

# "Reserva Stone" is a daily-liquidity investment inside the Conta Stone.
# Moving money there is neither income nor spending, so those statement lines
# become transfers to a "Reserva Stone" cash account (created on first use).
RESERVE_ACCOUNT_NAME = "Reserva Stone"
RESERVE_DECISION = "transfer:reserve"
_RESERVE_LINE = re.compile(r"reserva\s+stone", re.IGNORECASE)


# "LOJA - Mensalidade | Cobrança": the terminal fee, when Stone charges it
# apart from a deposit. The expense itself comes from the receivables report
# (backend/finance/receivables.py), so this line is cash only.
_STONE_CHARGE_LINE = re.compile(r"mensalidade\s*\|\s*cobran", re.IGNORECASE)


def is_stone_charge(description: str) -> bool:
    return bool(_STONE_CHARGE_LINE.search(description or ""))


def is_reserve_move(description: str) -> bool:
    return bool(_RESERVE_LINE.search(description or ""))


def _reserve_account_id(conn, company: int) -> int:
    row = conn.execute(
        "SELECT id FROM cash_accounts WHERE company=? AND name=? AND archived=0 ORDER BY created_at LIMIT 1",
        (company, RESERVE_ACCOUNT_NAME),
    ).fetchone()
    if row:
        return row["id"]
    return ledger.create_account(company, RESERVE_ACCOUNT_NAME, "bank", conn=conn)["id"]


class BankFileError(ValueError):
    pass


class BankImportConflict(ValueError):
    """Raised when the caller's preview_hash no longer matches the file being
    committed, or when a `link:<cash_event_id>` decision's candidate no
    longer validates (scope, account or amount) at commit time — e.g.
    because a concurrent import or payment consumed it since the preview
    was generated. Maps to HTTP 409."""


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _is_unique_violation(exc: BaseException) -> bool:
    """True for a UNIQUE-constraint violation on either backend this
    codebase targets. psycopg is only importable when `db.PG` is set (see
    backend/database.py's own `if PG: import psycopg` guard) — this
    project's SQLite-only dev/test environment never has the package
    installed at all, so it must only be imported lazily, on the Postgres
    path."""
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    if db.PG:
        import psycopg
        return isinstance(exc, psycopg.errors.IntegrityError)
    return False


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


def _external_record_exists(
    company: int, account_id, external_id: str, version: str, *, conn=None,
) -> bool:
    """When `conn` is given, the read happens on the CALLER's own connection
    (same established pattern as records.upsert_external_record's own
    `conn=` parameter) — required inside commit_bank_import's single
    transaction, where a SEPARATE connection would never see this same
    transaction's own uncommitted writes from earlier lines in the same
    file (e.g. a repeated external_id within one file: without sharing the
    connection, the second occurrence would wrongly read `already_seen =
    False` and take the "new" branch too, creating a second cash_events row
    for what is really one already-recorded bank line)."""
    def _query(c):
        row = c.execute(
            """
            SELECT 1 FROM external_records
            WHERE company=? AND source='bank_file' AND account_id=? AND external_id=? AND version=?
            """,
            (company, str(account_id), external_id, version),
        ).fetchone()
        return row is not None

    if conn is not None:
        return _query(conn)
    with db.connection() as own_conn:
        return _query(own_conn)


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


# --- Preview -> decide -> commit flow, teaching the import to recognize an
# already-recorded payment's cash_event instead of always creating a new one
# (the "caixa duplicado" bug: a payment made via
# backend/finance/payments.py::record_payment(..., cash_account_id=...)
# already creates its own cash_events row for the real-world outflow; later
# importing the bank statement covering that same movement must offer to
# LINK to that existing row rather than blindly creating a second one). ----


def _linked_cash_event_ids(conn) -> set:
    return {
        row["cash_event_id"]
        for row in conn.execute("SELECT cash_event_id FROM bank_cash_links")
    }


def _reversed_cash_event_ids(conn) -> set:
    return {
        row["reversed_event_id"]
        for row in conn.execute(
            "SELECT reversed_event_id FROM cash_events WHERE reversed_event_id IS NOT NULL"
        )
    }


def _candidate_cash_events(
    conn, company: int, cash_account_id: int, transactions: list,
    *, linked: set, reversed_ids: set,
):
    """Returns a lookup `(amount_cents, around: date) -> [cash_event_id]` of
    cash_events that MIGHT represent the same real-world movement as an
    imported bank line: same company/account, exact amount match, and an
    occurred_at within CANDIDATE_WINDOW_DAYS of the transaction's date.
    Never a confirmation — only ever surfaced as a suggestion in the preview
    response; the actual link only happens on the caller's explicit
    commit-time `link:<cash_event_id>` decision for that line.

    Excludes cash_events already claimed by another bank_cash_links row
    (any provider/external_id — a real movement is linked at most once) and
    ones that have themselves been reversed (ledger.reverse_event leaves the
    original row in place but it no longer represents live, matchable
    money). Restricted to kind='entry' rows (transfers and reversal rows are
    never import-linkable) — this is the same `kind` value
    ledger.post_cash_event always writes, whether the event was created by a
    payment or by a prior "new"-decision bank import.

    The account's events for the file's whole date span are read in ONE
    query and matched in memory: a 3-month Stone statement has ~3,000 lines,
    and one query per line took minutes against the hosted database.
    """
    by_amount: dict = {}
    if transactions:
        window = timedelta(days=CANDIDATE_WINDOW_DAYS)
        start = (min(date.fromisoformat(t.date) for t in transactions) - window).isoformat()
        end = (max(date.fromisoformat(t.date) for t in transactions) + window).isoformat()
        rows = conn.execute(
            """
            SELECT id, amount_cents, occurred_at FROM cash_events
            WHERE company=? AND cash_account_id=? AND kind='entry'
                  AND occurred_at BETWEEN ? AND ?
            ORDER BY occurred_at,id
            """,
            (company, cash_account_id, start, end),
        ).fetchall()
        for row in rows:
            if row["id"] in linked or row["id"] in reversed_ids:
                continue
            by_amount.setdefault(int(row["amount_cents"]), []).append(
                (str(row["occurred_at"]), row["id"])
            )

    def lookup(amount_cents: int, around: date) -> list:
        start = (around - timedelta(days=CANDIDATE_WINDOW_DAYS)).isoformat()
        end = (around + timedelta(days=CANDIDATE_WINDOW_DAYS)).isoformat()
        return [
            event_id for occurred, event_id in by_amount.get(int(amount_cents), ())
            if start <= occurred <= end
        ]

    return lookup


def _imported_external_ids(conn, company: int, cash_account_id: int) -> set:
    return {
        row["external_id"]
        for row in conn.execute(
            "SELECT external_id FROM external_records WHERE company=? AND source=? AND account_id=?",
            (company, PROVIDER_BANK_FILE, str(cash_account_id)),
        )
    }


def _preview_items(conn, company: int, cash_account_id: int, transactions: list) -> list:
    linked = _linked_cash_event_ids(conn)
    reversed_ids = _reversed_cash_event_ids(conn)
    imported = _imported_external_ids(conn, company, cash_account_id)
    candidates_for = _candidate_cash_events(
        conn, company, cash_account_id, transactions, linked=linked, reversed_ids=reversed_ids,
    )
    items = []
    for tx in transactions:
        if tx.external_id in imported:
            # A line from an earlier import (same file again, or overlapping
            # statements): committing it is a no-op, so offer nothing to link.
            items.append({
                "external_id": tx.external_id,
                "date": tx.date,
                "amount_cents": tx.amount_cents,
                "description": tx.description,
                "candidate_cash_event_ids": [],
                "decision": "new",
                "already_imported": True,
                "internal_transfer": False,
                "stone_charge": is_stone_charge(tx.description),
            })
            continue
        if is_reserve_move(tx.description):
            items.append({
                "external_id": tx.external_id,
                "date": tx.date,
                "amount_cents": tx.amount_cents,
                "description": tx.description,
                "candidate_cash_event_ids": [],
                "decision": RESERVE_DECISION,
                "already_imported": False,
                "internal_transfer": True,
                "stone_charge": False,
            })
            continue
        candidates = candidates_for(tx.amount_cents, date.fromisoformat(tx.date))
        # A suggestion is only ever pre-selected when it is UNAMBIGUOUS (a
        # single matching candidate) — two legitimate, independently-real
        # outflows that happen to share amount/date must never be silently
        # fused, so an ambiguous match defaults to "new" just like no match
        # at all. The caller may still override any line explicitly.
        decision = f"link:{candidates[0]}" if len(candidates) == 1 else "new"
        items.append({
            "external_id": tx.external_id,
            "date": tx.date,
            "amount_cents": tx.amount_cents,
            "description": tx.description,
            "candidate_cash_event_ids": [str(c) for c in candidates],
            "decision": decision,
            "already_imported": False,
            "internal_transfer": False,
            "stone_charge": is_stone_charge(tx.description),
        })
    return items


def _preview_hash(transactions: list) -> str:
    # Deliberately hashes only the FILE's own transaction content (external
    # id/date/amount/description) — not the candidate suggestions, which are
    # allowed to legitimately change between preview and commit (e.g. a
    # concurrent import claims a candidate). That kind of staleness is
    # caught separately, per decision line, inside commit_bank_import's
    # transaction (scope/account/amount re-validation, and the
    # already-linked-elsewhere check) — see BankImportConflict. This hash
    # instead guards against committing decisions against a DIFFERENT file
    # than the one the caller actually previewed.
    canonical = [
        {
            "external_id": tx.external_id,
            "date": tx.date,
            "amount_cents": tx.amount_cents,
            "description": tx.description,
        }
        for tx in transactions
    ]
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def preview_bank_import(company: int, cash_account_id: int, filename: str, content: bytes) -> dict:
    """Parse a bank file and, for each line, suggest whether it looks like a
    brand-new movement ("new") or the same real-world outflow as an
    already-recorded cash_event ("link:<id>") — a SUGGESTION only, never an
    automatic decision. The caller reviews/overrides `decision` per line and
    passes the whole set back to commit_bank_import along with
    `preview_hash`."""
    transactions = parse_bank_file(content, filename)
    with db.connection() as conn:
        items = _preview_items(conn, company, cash_account_id, transactions)
    dates = [t.date for t in transactions]
    return {
        "preview_hash": _preview_hash(transactions),
        "count": len(transactions),
        "start": min(dates) if dates else None,
        "end": max(dates) if dates else None,
        "total_cents": sum(t.amount_cents for t in transactions),
        "already_imported": sum(1 for item in items if item["already_imported"]),
        "internal_transfers": sum(1 for item in items if item["internal_transfer"]),
        "stone_charges": sum(1 for item in items if item["stone_charge"] and not item["already_imported"]),
        "items": items,
    }


def _parse_decision(raw: str, external_id: str) -> Optional[int]:
    """Returns None for "new", or the candidate cash_event_id for
    "link:<id>". Raises BankFileError (-> 422) for anything else."""
    if raw == "new":
        return None
    if raw.startswith("link:"):
        raw_id = raw[len("link:"):]
        try:
            return int(raw_id)
        except ValueError:
            pass
    raise BankFileError(f"Decisão inválida para {external_id!r}: {raw!r}")


def commit_bank_import(
    company: int,
    cash_account_id: int,
    filename: str,
    content: bytes,
    *,
    decisions: dict,
    preview_hash: str,
) -> dict:
    """Apply the caller's per-line decisions from a prior preview_bank_import
    call. `decisions` maps external_id -> "new" | "link:<cash_event_id>"; a
    line missing from the map defaults to "new" (the safe, conservative
    choice — a link only ever happens on an explicit decision, never
    inferred). `preview_hash` must match the CURRENT content of the file
    being committed (see _preview_hash) or the whole commit is rejected with
    BankImportConflict (-> 409) before touching anything.

    - decision "new": identical to the historical behavior — creates a
      fresh cash_events row, still gated by the existing
      external_records-based duplicate-import check (unchanged).
    - decision "link:<id>": does NOT create any cash_events row. Re-
      validates, inside this same transaction, that the candidate still (a)
      belongs to this company/cash_account_id (scope), (b) matches the
      transaction's amount_cents exactly (value), and (c) is not already
      claimed by another bank_cash_links row — any of those failing raises
      BankImportConflict. If a bank_cash_links row for this exact
      (company,cash_account_id,provider,external_id) ALREADY exists (a
      genuine re-submission/retry of a previously-successful link, e.g. the
      same statement imported again), the existing link is returned as-is
      instead of erroring or creating a duplicate.
    """
    transactions = parse_bank_file(content, filename)
    if _preview_hash(transactions) != preview_hash:
        raise BankImportConflict(
            "A prévia da importação mudou; gere uma nova prévia antes de confirmar."
        )

    imported = 0
    duplicates = 0
    linked = 0
    transferred = 0
    version = "1"
    timestamp = db.now()
    # A 3-month statement has ~3,000 lines: the "new" and Reserva Stone lines
    # are written in batches (one round trip per table instead of ~8 per
    # line), or the import outlives the function's time limit. Everything
    # still runs in this one transaction.
    record_rows: list = []
    event_rows: list = []
    link_rows: list = []

    def flush(conn) -> None:
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
        if event_rows:
            conn.executemany(
                """
                INSERT INTO cash_events(
                    id,company,cash_account_id,amount_cents,occurred_at,description,
                    kind,entry_id,transfer_group,created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                event_rows,
            )
        if link_rows:
            conn.executemany(
                """
                INSERT INTO bank_cash_links(
                    id,company,cash_account_id,provider,external_id,cash_event_id,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                link_rows,
            )
        record_rows.clear()
        event_rows.clear()
        link_rows.clear()

    def add_event(account_id: int, amount_cents: int, occurred_at: str, description: str,
                  kind: str, transfer_group=None) -> int:
        event_id = _new_id()
        event_rows.append((
            event_id, company, account_id, amount_cents, occurred_at, description,
            kind, None, transfer_group, None, timestamp,
        ))
        return event_id

    with db.connection() as conn:
        seen_hashes = {
            row["external_id"]: row["payload_hash"]
            for row in conn.execute(
                """
                SELECT external_id, payload_hash FROM external_records
                WHERE company=? AND source=? AND account_id=? AND version=?
                """,
                (company, PROVIDER_BANK_FILE, str(cash_account_id), version),
            )
        }
        linked_lines = {
            row["external_id"]
            for row in conn.execute(
                """
                SELECT external_id FROM bank_cash_links
                WHERE company=? AND cash_account_id=? AND provider=?
                """,
                (company, cash_account_id, PROVIDER_BANK_FILE),
            )
        }
        account_checked = False
        reserve_id = None
        for tx in transactions:
            payload = {"date": tx.date, "amount_cents": tx.amount_cents, "description": tx.description}
            payload_hash = records._canonical_hash(payload)
            known_hash = seen_hashes.get(tx.external_id)
            already_seen = known_hash is not None
            if already_seen and known_hash != payload_hash:
                # Same key, different content: the regular upsert raises
                # ExternalRecordConflict, exactly as before.
                flush(conn)
                records.upsert_external_record(
                    company, PROVIDER_BANK_FILE, cash_account_id, tx.external_id, version, payload,
                    conn=conn,
                )
            if not already_seen:
                record_rows.append((
                    _new_id(), company, PROVIDER_BANK_FILE, str(cash_account_id), tx.external_id,
                    version, json.dumps(payload, ensure_ascii=False, sort_keys=True), payload_hash,
                    timestamp, timestamp,
                ))
                seen_hashes[tx.external_id] = payload_hash
            raw_decision = decisions.get(tx.external_id, "new")
            if raw_decision in (RESERVE_DECISION, "new") and not account_checked:
                ledger._require_account(conn, company, cash_account_id)
                account_checked = True
            if raw_decision == RESERVE_DECISION:
                if not is_reserve_move(tx.description):
                    raise BankFileError(f"{tx.external_id!r} não é uma movimentação da Reserva Stone.")
                if already_seen:
                    duplicates += 1
                    continue
                if reserve_id is None:
                    reserve_id = _reserve_account_id(conn, company)
                    if reserve_id == cash_account_id:
                        raise BankFileError("O extrato da própria Reserva Stone não tem transferências para ela mesma.")
                if tx.amount_cents == 0:
                    raise ValueError("O valor da transferência deve ser maior que zero.")
                outgoing = tx.amount_cents < 0
                amount = abs(tx.amount_cents)
                group_id = _new_id()
                description = (tx.description or RESERVE_ACCOUNT_NAME).strip()
                source_id = cash_account_id if outgoing else reserve_id
                target_id = reserve_id if outgoing else cash_account_id
                source_leg = add_event(source_id, -amount, tx.date, description, "transfer", group_id)
                target_leg = add_event(target_id, amount, tx.date, description, "transfer", group_id)
                own_leg = source_leg if outgoing else target_leg
                link_rows.append((
                    _new_id(), company, cash_account_id, PROVIDER_BANK_FILE, tx.external_id,
                    own_leg, timestamp,
                ))
                linked_lines.add(tx.external_id)
                transferred += 1
                continue
            candidate_cash_event_id = _parse_decision(raw_decision, tx.external_id)

            if candidate_cash_event_id is None:
                if already_seen:
                    duplicates += 1
                    continue
                if tx.amount_cents == 0:
                    raise ValueError("O valor não pode ser zero.")
                description = (tx.description or f"Importado ({tx.external_id})").strip()
                if not description:
                    raise ValueError("Informe a descrição.")
                event_id = add_event(cash_account_id, tx.amount_cents, tx.date, description, "entry")
                # Claim the movement for this bank line, so a later statement
                # never offers it as "already recorded" for a different line.
                link_rows.append((
                    _new_id(), company, cash_account_id, PROVIDER_BANK_FILE, tx.external_id,
                    event_id, timestamp,
                ))
                linked_lines.add(tx.external_id)
                imported += 1
                continue

            if tx.external_id in linked_lines:
                # "Links duplicados retornam registro existente" — a retry of
                # the exact same successful link, never a second link row.
                linked += 1
                continue

            if already_seen:
                # This external transaction was already imported before
                # (recorded as its own external_records row) without ever
                # being linked — retroactively linking it now would leave
                # that earlier import's own effect (or lack thereof)
                # inconsistent with this decision, so it is rejected rather
                # than guessed at.
                raise BankImportConflict(
                    f"Esta transação já foi importada anteriormente sem vínculo: {tx.external_id!r}."
                )

            # Links are few and each needs fresh checks: write what is pending first.
            flush(conn)
            candidate = conn.execute(
                "SELECT * FROM cash_events WHERE id=? AND company=? AND cash_account_id=?",
                (candidate_cash_event_id, company, cash_account_id),
            ).fetchone()
            if not candidate:
                raise BankImportConflict(
                    f"Candidato de conciliação inválido para {tx.external_id!r}."
                )
            if int(candidate["amount_cents"]) != int(tx.amount_cents):
                raise BankImportConflict(
                    f"Valor do candidato não corresponde à transação {tx.external_id!r}."
                )
            if conn.execute(
                "SELECT 1 FROM bank_cash_links WHERE cash_event_id=?",
                (candidate_cash_event_id,),
            ).fetchone():
                raise BankImportConflict(
                    f"Candidato já vinculado a outra transação importada: {tx.external_id!r}."
                )

            # The SELECT above is a fast pre-check, not the actual guarantee —
            # under Postgres READ COMMITTED, two concurrent commits could
            # both pass it before either INSERTs. The real, race-proof
            # guarantee is bank_cash_links's own UNIQUE(cash_event_id)
            # constraint (migration 019): a violation here means someone
            # else claimed this candidate in the interim, mapped to the same
            # BankImportConflict the pre-check above already raises for the
            # non-racy case.
            try:
                conn.execute(
                    """
                    INSERT INTO bank_cash_links(
                        id,company,cash_account_id,provider,external_id,cash_event_id,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        _new_id(), company, cash_account_id, PROVIDER_BANK_FILE, tx.external_id,
                        candidate_cash_event_id, timestamp,
                    ),
                )
            except Exception as exc:
                if not _is_unique_violation(exc):
                    raise
                raise BankImportConflict(
                    f"Candidato já vinculado a outra transação importada: {tx.external_id!r}."
                ) from exc
            linked_lines.add(tx.external_id)
            linked += 1
        flush(conn)

    return {
        "total": len(transactions),
        "imported": imported,
        "duplicates": duplicates,
        "linked": linked,
        "transferred": transferred,
    }
