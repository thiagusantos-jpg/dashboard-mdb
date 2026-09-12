from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Optional

from .. import database as db
from ..finance import ledger
from . import records
from .open_finance import OpenFinanceProvider


def _new_id() -> int:
    return secrets.randbits(63) or 1


class OpenFinanceService:
    """Owns the connection lifecycle (pending → active → revoked/error) and
    feeds synced balances/transactions through the same idempotent-record and
    cash-ledger infrastructure Task 11/9 already built — Open Finance doesn't
    get its own posting logic, it's just another source."""

    def __init__(self, provider: OpenFinanceProvider):
        self.provider = provider

    def start_consent(self, company: int, cash_account_id: int, return_url: str) -> dict:
        connection_id = _new_id()
        timestamp = db.now()
        consent = self.provider.create_consent(company, return_url)
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO open_finance_connections(
                    id,company,cash_account_id,provider,status,consent_jti,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (connection_id, company, cash_account_id, "stone", "pending", consent.jti, timestamp, timestamp),
            )
        return {"connection_id": connection_id, "consent_url": consent.url, "expires_at": consent.expires_at}

    def complete_consent(self, jti: str, external_account_id: str) -> dict:
        timestamp = db.now()
        with db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM open_finance_connections WHERE consent_jti=?", (jti,)
            ).fetchone()
            if not row:
                raise ValueError("Consentimento não encontrado.")
            conn.execute(
                """
                UPDATE open_finance_connections
                SET status='active',external_account_id=?,updated_at=?
                WHERE id=?
                """,
                (external_account_id, timestamp, row["id"]),
            )
            return dict(conn.execute(
                "SELECT * FROM open_finance_connections WHERE id=?", (row["id"],)
            ).fetchone())

    def get_connection(self, connection_id: int) -> dict:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM open_finance_connections WHERE id=?", (connection_id,)
            ).fetchone()
        if not row:
            raise ValueError("Conexão não encontrada.")
        return dict(row)

    def sync(self, connection_id: int, *, lookback_days: int = 30) -> dict:
        connection = self.get_connection(connection_id)
        if connection["status"] == "revoked":
            return connection
        timestamp = db.now()
        try:
            balance = self.provider.get_balance(connection["external_account_id"])
            end = date.today()
            start = end - timedelta(days=lookback_days)
            transactions = self.provider.list_transactions(connection["external_account_id"], start, end)
            for tx in transactions:
                payload = {"date": tx.date, "amount_cents": tx.amount_cents, "description": tx.description}
                already_seen = self._external_record_exists(connection["company"], connection["cash_account_id"], tx.external_id)
                records.upsert_external_record(
                    connection["company"], "stone_open_finance", connection["cash_account_id"],
                    tx.external_id, "1", payload,
                )
                if not already_seen:
                    ledger.post_cash_event(
                        connection["company"], connection["cash_account_id"], tx.amount_cents,
                        date.fromisoformat(tx.date), tx.description or f"Stone ({tx.external_id})",
                    )
            with db.connection() as conn:
                conn.execute(
                    """
                    UPDATE open_finance_connections
                    SET last_synced_at=?,last_balance_cents=?,last_error=NULL,updated_at=?
                    WHERE id=?
                    """,
                    (timestamp, balance.balance_cents, timestamp, connection_id),
                )
        except Exception as exc:  # noqa: BLE001 — any provider failure must not wipe last-known state
            with db.connection() as conn:
                conn.execute(
                    "UPDATE open_finance_connections SET last_error=?,updated_at=? WHERE id=?",
                    (str(exc), timestamp, connection_id),
                )
            connection = self.get_connection(connection_id)
            connection["status"] = "error"
            return connection
        return self.get_connection(connection_id)

    def revoke(self, connection_id: int) -> dict:
        connection = self.get_connection(connection_id)
        self.provider.revoke_consent(connection.get("consent_jti") or "")
        timestamp = db.now()
        with db.connection() as conn:
            conn.execute(
                "UPDATE open_finance_connections SET status='revoked',updated_at=? WHERE id=?",
                (timestamp, connection_id),
            )
        return self.get_connection(connection_id)

    @staticmethod
    def _external_record_exists(company: int, account_id, external_id: str) -> bool:
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM external_records
                WHERE company=? AND source='stone_open_finance' AND account_id=? AND external_id=? AND version='1'
                """,
                (company, str(account_id), external_id),
            ).fetchone()
        return row is not None
