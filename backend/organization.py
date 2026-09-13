from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from typing import Mapping, Optional, Set

from . import database as db


class ConcurrentUpdateError(RuntimeError):
    pass


PROFILE_FIELDS = {
    "legal_name",
    "trade_name",
    "cnpj",
    "address",
    "contacts",
    "logo_url",
}


def operating_days(
    start: str,
    end: str,
    *,
    closed_weekdays: Optional[Set[int]] = None,
    exceptions: Optional[Mapping[str, str]] = None,
) -> int:
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if last < first:
        raise ValueError("A data final deve ser igual ou posterior à inicial.")
    closed = closed_weekdays or set()
    overrides = exceptions or {}
    total = 0
    current = first
    while current <= last:
        status = overrides.get(current.isoformat())
        if status == "open" or (status != "closed" and current.weekday() not in closed):
            total += 1
        current += timedelta(days=1)
    return total


def operating_days_for_company(company: int, start: str, end: str, *, store: Optional[int] = None) -> int:
    """operating_days() with this company's own registered calendar exceptions
    (Task 4) — the only caller-supplied input is the date range."""
    overrides = {
        row["date"]: row["status"]
        for row in calendar_exceptions(company, store)
        if start <= row["date"] <= end
    }
    return operating_days(start, end, exceptions=overrides)


def _profile(row) -> Optional[dict]:
    if not row:
        return None
    result = dict(row)
    result["address"] = json.loads(result.pop("address_json"))
    result["contacts"] = json.loads(result.pop("contacts_json"))
    return result


def company_profile(company: int) -> dict:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM organization_profiles WHERE company=?", (company,)
        ).fetchone()
        if row:
            return _profile(row)
        source = conn.execute(
            "SELECT name FROM companies WHERE id=?", (company,)
        ).fetchone()
    if not source:
        raise ValueError("Empresa não encontrada.")
    return {
        "company": company,
        "legal_name": "",
        "trade_name": source["name"],
        "cnpj": "",
        "address": {},
        "contacts": {},
        "logo_url": "",
        "version": None,
    }


def save_company_profile(
    company: int,
    changes: Mapping[str, object],
    *,
    expected_version: Optional[int],
) -> dict:
    unknown = set(changes) - PROFILE_FIELDS
    if unknown:
        raise ValueError("Campos cadastrais inválidos.")
    with db.connection() as conn:
        current = conn.execute(
            "SELECT * FROM organization_profiles WHERE company=?", (company,)
        ).fetchone()
        if current is None:
            if expected_version is not None:
                raise ConcurrentUpdateError("O cadastro foi alterado por outro usuário.")
            company_row = conn.execute(
                "SELECT name FROM companies WHERE id=?", (company,)
            ).fetchone()
            if not company_row:
                raise ValueError("Empresa não encontrada.")
            values = {
                "legal_name": "",
                "trade_name": company_row["name"],
                "cnpj": "",
                "address": {},
                "contacts": {},
                "logo_url": "",
                **changes,
            }
            timestamp = db.now()
            conn.execute(
                """
                INSERT INTO organization_profiles(
                    company,legal_name,trade_name,cnpj,address_json,contacts_json,
                    logo_url,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    company,
                    values["legal_name"],
                    values["trade_name"],
                    values["cnpj"],
                    json.dumps(values["address"], ensure_ascii=False),
                    json.dumps(values["contacts"], ensure_ascii=False),
                    values["logo_url"],
                    timestamp,
                    timestamp,
                ),
            )
        else:
            current_dict = _profile(current)
            if expected_version != current_dict["version"]:
                raise ConcurrentUpdateError("O cadastro foi alterado por outro usuário.")
            values = {**current_dict, **changes}
            changed = conn.execute(
                """
                UPDATE organization_profiles
                SET legal_name=?,trade_name=?,cnpj=?,address_json=?,contacts_json=?,
                    logo_url=?,version=version+1,updated_at=?
                WHERE company=? AND version=?
                """,
                (
                    values["legal_name"],
                    values["trade_name"],
                    values["cnpj"],
                    json.dumps(values["address"], ensure_ascii=False),
                    json.dumps(values["contacts"], ensure_ascii=False),
                    values["logo_url"],
                    db.now(),
                    company,
                    expected_version,
                ),
            )
            if changed.rowcount != 1:
                raise ConcurrentUpdateError("O cadastro foi alterado por outro usuário.")
        saved = conn.execute(
            "SELECT * FROM organization_profiles WHERE company=?", (company,)
        ).fetchone()
    return _profile(saved)


def list_stores(company: int) -> list:
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM stores WHERE company=? ORDER BY name", (company,)
            )
        ]


def create_store(
    company: int,
    name: str,
    *,
    mobne_id: Optional[int] = None,
    timezone: str = "America/Sao_Paulo",
) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("Informe o nome da loja.")
    store_id = secrets.randbits(63) or 1
    timestamp = db.now()
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO stores(id,company,mobne_id,name,timezone,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (store_id, company, mobne_id, clean_name, timezone, timestamp, timestamp),
        )
        row = conn.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone()
    return dict(row)


def save_store(
    company: int,
    store_id: int,
    changes: Mapping[str, object],
    *,
    expected_version: int,
) -> dict:
    allowed = {"name", "timezone", "active"}
    if set(changes) - allowed:
        raise ValueError("Campos de loja inválidos.")
    with db.connection() as conn:
        current = conn.execute(
            "SELECT * FROM stores WHERE id=? AND company=?", (store_id, company)
        ).fetchone()
        if not current:
            raise ValueError("Loja não encontrada.")
        values = {**dict(current), **changes}
        changed = conn.execute(
            """
            UPDATE stores SET name=?,timezone=?,active=?,version=version+1,updated_at=?
            WHERE id=? AND company=? AND version=?
            """,
            (
                str(values["name"]).strip(),
                values["timezone"],
                int(bool(values["active"])),
                db.now(),
                store_id,
                company,
                expected_version,
            ),
        )
        if changed.rowcount != 1:
            raise ConcurrentUpdateError("A loja foi alterada por outro usuário.")
        row = conn.execute("SELECT * FROM stores WHERE id=?", (store_id,)).fetchone()
    return dict(row)


def save_business_hours(
    company: int,
    store: int,
    weekday: int,
    *,
    opens_at: Optional[str],
    closes_at: Optional[str],
    closed: bool,
    expected_version: Optional[int],
) -> dict:
    if weekday not in range(7):
        raise ValueError("Dia da semana inválido.")
    with db.connection() as conn:
        belongs = conn.execute(
            "SELECT 1 FROM stores WHERE id=? AND company=?", (store, company)
        ).fetchone()
        if not belongs:
            raise ValueError("Loja não encontrada.")
        current = conn.execute(
            "SELECT * FROM business_hours WHERE store=? AND weekday=?",
            (store, weekday),
        ).fetchone()
        if current is None:
            if expected_version is not None:
                raise ConcurrentUpdateError("O horário foi alterado por outro usuário.")
            conn.execute(
                """
                INSERT INTO business_hours(
                    store,weekday,opens_at,closes_at,closed,updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (store, weekday, opens_at, closes_at, int(closed), db.now()),
            )
        else:
            changed = conn.execute(
                """
                UPDATE business_hours SET opens_at=?,closes_at=?,closed=?,
                    version=version+1,updated_at=?
                WHERE store=? AND weekday=? AND version=?
                """,
                (
                    opens_at,
                    closes_at,
                    int(closed),
                    db.now(),
                    store,
                    weekday,
                    expected_version,
                ),
            )
            if changed.rowcount != 1:
                raise ConcurrentUpdateError("O horário foi alterado por outro usuário.")
        row = conn.execute(
            "SELECT * FROM business_hours WHERE store=? AND weekday=?",
            (store, weekday),
        ).fetchone()
    return dict(row)


def save_calendar_exception(
    company: int,
    day: str,
    status: str,
    *,
    description: str = "",
    store: Optional[int] = None,
    expected_version: Optional[int] = None,
) -> dict:
    date.fromisoformat(day)
    if status not in {"open", "closed"}:
        raise ValueError("O status deve ser open ou closed.")
    with db.connection() as conn:
        current = conn.execute(
            "SELECT version FROM calendar_exceptions WHERE company=? AND store=? AND date=?",
            (company, store or 0, day),
        ).fetchone()
        if current is None:
            if expected_version is not None:
                raise ConcurrentUpdateError("A data foi alterada por outro usuário.")
            conn.execute(
                """
                INSERT INTO calendar_exceptions(
                    company,store,date,status,description,updated_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (company, store or 0, day, status, description.strip(), db.now()),
            )
        else:
            changed = conn.execute(
                """
                UPDATE calendar_exceptions SET status=?,description=?,
                    version=version+1,updated_at=?
                WHERE company=? AND store=? AND date=? AND version=?
                """,
                (
                    status,
                    description.strip(),
                    db.now(),
                    company,
                    store or 0,
                    day,
                    expected_version,
                ),
            )
            if changed.rowcount != 1:
                raise ConcurrentUpdateError("A data foi alterada por outro usuário.")
        row = conn.execute(
            "SELECT * FROM calendar_exceptions WHERE company=? AND store=? AND date=?",
            (company, store or 0, day),
        ).fetchone()
    return dict(row)


def calendar_exceptions(company: int, store: Optional[int] = None) -> list:
    with db.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM calendar_exceptions
                WHERE company=? AND store IN (0,?)
                ORDER BY date
                """,
                (company, store or 0),
            )
        ]


def delete_calendar_exception(
    company: int,
    day: str,
    *,
    expected_version: int,
    store: Optional[int] = None,
) -> None:
    """Removes one exception; the date then follows the regular business hours.
    Other dates are untouched, and a stale version never deletes."""
    date.fromisoformat(day)
    with db.connection() as conn:
        current = conn.execute(
            "SELECT version FROM calendar_exceptions WHERE company=? AND store=? AND date=?",
            (company, store or 0, day),
        ).fetchone()
        if current is None:
            raise LookupError("Data não encontrada no calendário.")
        changed = conn.execute(
            "DELETE FROM calendar_exceptions WHERE company=? AND store=? AND date=? AND version=?",
            (company, store or 0, day, expected_version),
        )
        if changed.rowcount != 1:
            raise ConcurrentUpdateError("A data foi alterada por outro usuário.")
