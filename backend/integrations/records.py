from __future__ import annotations

import hashlib
import json
import secrets

from .. import database as db


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _canonical_hash(payload) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExternalRecordConflict(ValueError):
    """Raised when the same (company, source, account, external_id, version) key
    is upserted with a different payload — a data-quality issue, not a normal
    replay. The conflicting attempt is quarantined, never silently applied."""

    def __init__(self, message: str, quarantine_id: int):
        super().__init__(message)
        self.quarantine_id = quarantine_id


def upsert_external_record(
    company: int,
    source: str,
    account_id,
    external_id,
    version,
    payload,
) -> dict:
    account_id = str(account_id)
    external_id = str(external_id)
    version = str(version)
    payload_hash = _canonical_hash(payload)
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    timestamp = db.now()
    with db.connection() as conn:
        existing = conn.execute(
            """
            SELECT * FROM external_records
            WHERE company=? AND source=? AND account_id=? AND external_id=? AND version=?
            """,
            (company, source, account_id, external_id, version),
        ).fetchone()
        if existing:
            existing = dict(existing)

    if existing:
        if existing["payload_hash"] == payload_hash:
            return existing
        # The conflicting attempt must survive even though upsert fails, so it's
        # committed in its own transaction before raising — a `with` block that
        # raises rolls its own writes back (see backend/database.py connection()).
        quarantine_id = _new_id()
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO external_record_quarantine(
                    id,company,source,account_id,external_id,version,existing_hash,
                    conflicting_payload_json,conflicting_hash,detected_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    quarantine_id, company, source, account_id, external_id, version,
                    existing["payload_hash"], payload_json, payload_hash, timestamp,
                ),
            )
        raise ExternalRecordConflict(
            "Registro externo já existe com um conteúdo diferente para a mesma chave.",
            quarantine_id,
        )

    record_id = _new_id()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO external_records(
                id,company,source,account_id,external_id,version,payload_json,payload_hash,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record_id, company, source, account_id, external_id, version,
                payload_json, payload_hash, timestamp, timestamp,
            ),
        )
        row = dict(conn.execute("SELECT * FROM external_records WHERE id=?", (record_id,)).fetchone())
    return row


def external_record_count(company: int = None) -> int:
    with db.connection() as conn:
        if company is None:
            row = conn.execute("SELECT COUNT(*) AS c FROM external_records").fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM external_records WHERE company=?", (company,)
            ).fetchone()
    return int(row["c"])


def list_quarantine(company: int) -> list:
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM external_record_quarantine
                WHERE company=? AND resolved=0 ORDER BY detected_at DESC
                """,
                (company,),
            )
        ]
