from __future__ import annotations

import base64
import json
from datetime import date
from typing import Optional

from .. import database as db


# Obligation kinds this module understands. `key` on every item is always
# `<kind>:<id>` (never a bare numeric id) so the frontend can tell which
# underlying table/kind an item came from without a separate lookup.
_KINDS = ("entry", "loan_installment")

# Statuses that represent a genuinely outstanding (still-owed) obligation.
# Entries: mirrors backend/finance/forecast.py::_open_entries_due — 'overdue'
# is included defensively even though nothing in this codebase currently
# persists that literal status value to the `financial_entries` row (see
# backend/finance/entries.py::get_entry, which computes it only for the
# response, never writes it back — "preservar estado original no banco").
_OPEN_ENTRY_STATUSES = ("open", "overdue", "partially_paid")

# Fields an item's `allowed_actions` can carry that only make sense for a
# caller with write access. The route layer (backend/routes/obligations.py)
# strips these out for a read-only caller; this module always computes the
# "if the caller could write" version, since `list_obligations`'s signature
# (fixed by the task brief) has no permission parameter of its own — auth
# awareness lives in the route, not here. Either way, `allowed_actions` is
# informational only: the server revalidates on every mutating POST.
WRITE_ACTIONS = {"pay", "edit_description"}


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _clamp_limit(limit) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200
    return limit


def _encode_cursor(due_date: str, kind: str, item_id: int) -> str:
    payload = json.dumps([due_date, kind, str(item_id)])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        due_date, kind, id_str = json.loads(raw)
        if not isinstance(due_date, str) or not isinstance(kind, str) or not isinstance(id_str, str):
            raise ValueError
        date.fromisoformat(due_date)
        if kind not in _KINDS:
            raise ValueError
        item_id = int(id_str)
    except Exception as exc:  # noqa: BLE001 - any decode failure is "invalid cursor"
        raise ValueError("Cursor inválido.") from exc
    return (due_date, kind, item_id)


def _sort_key(item: dict):
    return (item["due_date"], item["kind"], int(item["id"]))


def _entry_allowed_actions(status: str, source: str, open_cents: int) -> list:
    actions = ["details"]
    # Matches backend/finance/entry_management.py's editability rules well
    # enough for an informational hint: 'open'/'partially_paid' + manual
    # source is editable there (description is always allowed in both
    # states). Reconciliation-link and full-field-vs-partial-field nuance is
    # intentionally not re-checked here — the POST endpoint (B1, already
    # shipped) re-validates all of that independently.
    if source == "manual" and status in ("open", "partially_paid"):
        actions.append("edit_description")
    if open_cents > 0:
        actions.append("pay")
    return actions


def _installment_allowed_actions(open_cents: int) -> list:
    actions = ["details"]
    if open_cents > 0:
        actions.append("pay")
    return actions


def _entry_rows(company: int) -> list:
    placeholders = ",".join("?" for _ in _OPEN_ENTRY_STATUSES)
    with db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT e.id,e.version,e.description,e.due_date,e.competence,
                   e.amount_cents,e.status,e.source,
                   e.installment_number,e.installment_count,a.sensitive,
                   COALESCE(SUM(
                       CASE WHEN ev.event_type='settled' THEN ev.amount_cents
                            WHEN ev.event_type='reversed' THEN -ev.amount_cents ELSE 0 END
                   ),0) AS paid_cents
            FROM financial_entries e
            JOIN finance_accounts a ON a.id=e.account_id
            LEFT JOIN financial_events ev ON ev.entry_id=e.id
            WHERE e.company=? AND e.status IN ({placeholders})
            GROUP BY e.id
            """,
            (company, *_OPEN_ENTRY_STATUSES),
        ).fetchall()
    items = []
    for row in rows:
        paid_cents = int(row["paid_cents"])
        open_cents = max(0, row["amount_cents"] - paid_cents)
        items.append({
            "key": f"entry:{row['id']}",
            "kind": "entry",
            "id": str(row["id"]),
            "version": int(row["version"]),
            "description": row["description"],
            "due_date": row["due_date"],
            "competence": row["competence"],
            "total_cents": row["amount_cents"],
            "paid_cents": paid_cents,
            "open_cents": open_cents,
            "status": row["status"],
            "source": row["source"],
            "loan_id": None,
            "number": row["installment_number"],
            "count": row["installment_count"],
            "allowed_actions": _entry_allowed_actions(row["status"], row["source"], open_cents),
            "_sensitive": bool(row["sensitive"]),
        })
    return items


def _loan_installment_rows(company: int) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT i.id,i.number,i.due_date,i.total_cents,i.status,i.loan_id,l.lender,
                   (SELECT COUNT(*) FROM loan_installments WHERE schedule_id=i.schedule_id) AS count
            FROM loan_installments i
            JOIN loans l ON l.id=i.loan_id
            JOIN loan_schedules s ON s.id=i.schedule_id
            WHERE l.company=? AND i.schedule_id=l.active_schedule_id
              AND s.status='active' AND i.status='open'
            """,
            (company,),
        ).fetchall()
    items = []
    for row in rows:
        # Never a partially-paid installment in this state: pay_installment()
        # only ever transitions an installment straight to 'paid' — there is
        # no intermediate persisted status — so an 'open' row here always has
        # paid_cents=0. `total_cents` already bakes principal+interest into
        # ONE number for the whole installment; the interest a paid
        # installment later generates surfaces as its own (already-'paid',
        # so list-excluded) financial_entries row, never a second obligation
        # for the same installment.
        items.append({
            "key": f"loan_installment:{row['id']}",
            "kind": "loan_installment",
            "id": str(row["id"]),
            "version": 1,  # placeholder: loan_installments has no `version`
            # column yet — added by task B3's migration, which defaults new
            # rows to 1. Synced here so B2 ships the field the shared
            # contract requires without owning that schema change.
            "description": f"Parcela {row['number']}/{row['count']} — {row['lender']}",
            "due_date": row["due_date"],
            "competence": None,
            "total_cents": row["total_cents"],
            "paid_cents": 0,
            "open_cents": row["total_cents"],
            "status": row["status"],
            "source": "loan",
            "loan_id": str(row["loan_id"]),
            "number": row["number"],
            "count": row["count"],
            "allowed_actions": _installment_allowed_actions(row["total_cents"]),
            "_sensitive": False,  # loans carry no account/sensitive linkage
        })
    return items


def list_obligations(
    company: int,
    *,
    kind: Optional[str] = None,
    status: Optional[str] = None,
    due_from=None,
    due_to=None,
    q: str = "",
    cursor: Optional[str] = None,
    limit: int = 50,
    include_sensitive: bool = False,
) -> dict:
    limit = _clamp_limit(limit)
    after = _decode_cursor(cursor) if cursor else None  # raises ValueError

    items = []
    if kind in (None, "entry"):
        items.extend(_entry_rows(company))
    if kind in (None, "loan_installment"):
        items.extend(_loan_installment_rows(company))

    if not include_sensitive:
        items = [item for item in items if not item["_sensitive"]]
    for item in items:
        item.pop("_sensitive", None)

    if status:
        items = [item for item in items if item["status"] == status]

    due_from_iso = _iso(due_from)
    due_to_iso = _iso(due_to)
    if due_from_iso:
        items = [item for item in items if item["due_date"] >= due_from_iso]
    if due_to_iso:
        items = [item for item in items if item["due_date"] <= due_to_iso]

    needle = (q or "").strip().lower()
    if needle:
        items = [item for item in items if needle in item["description"].lower()]

    items.sort(key=_sort_key)

    total = len(items)
    open_cents = sum(item["open_cents"] for item in items)

    if after is not None:
        items = [item for item in items if _sort_key(item) > after]

    page = items[:limit]
    has_more = len(items) > limit
    next_cursor = _encode_cursor(*_sort_key(page[-1])) if has_more and page else None

    return {
        "items": page,
        "total": total,
        "open_cents": open_cents,
        "next_cursor": next_cursor,
    }


def get_obligation(
    company: int,
    kind: str,
    item_id: int,
    *,
    include_sensitive: bool = False,
) -> Optional[dict]:
    """Fetch a single obligation by (kind,id), scoped to `company`.

    Unlike `list_obligations`, this is not restricted to the active
    schedule/open status — it deliberately preserves access to a paid
    installment's (or a settled entry's) detail for history/audit, per the
    task brief's "preservar acesso ao histórico pago no detalhe".
    """
    if kind == "entry":
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT e.id,e.version,e.description,e.due_date,e.competence,
                       e.amount_cents,e.status,e.source,
                       e.installment_number,e.installment_count,a.sensitive,
                       COALESCE(SUM(
                           CASE WHEN ev.event_type='settled' THEN ev.amount_cents
                                WHEN ev.event_type='reversed' THEN -ev.amount_cents ELSE 0 END
                       ),0) AS paid_cents
                FROM financial_entries e
                JOIN finance_accounts a ON a.id=e.account_id
                LEFT JOIN financial_events ev ON ev.entry_id=e.id
                WHERE e.id=? AND e.company=?
                GROUP BY e.id
                """,
                (item_id, company),
            ).fetchone()
        if not row:
            return None
        if row["sensitive"] and not include_sensitive:
            return None
        paid_cents = int(row["paid_cents"])
        open_cents = max(0, row["amount_cents"] - paid_cents)
        return {
            "key": f"entry:{row['id']}",
            "kind": "entry",
            "id": str(row["id"]),
            "version": int(row["version"]),
            "description": row["description"],
            "due_date": row["due_date"],
            "competence": row["competence"],
            "total_cents": row["amount_cents"],
            "paid_cents": paid_cents,
            "open_cents": open_cents,
            "status": row["status"],
            "source": row["source"],
            "loan_id": None,
            "number": row["installment_number"],
            "count": row["installment_count"],
            "allowed_actions": _entry_allowed_actions(row["status"], row["source"], open_cents),
        }

    if kind == "loan_installment":
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT i.id,i.number,i.due_date,i.total_cents,i.status,i.loan_id,l.lender,
                       i.paid_principal_cents,i.paid_interest_cents,
                       (SELECT COUNT(*) FROM loan_installments WHERE schedule_id=i.schedule_id) AS count
                FROM loan_installments i
                JOIN loans l ON l.id=i.loan_id
                WHERE i.id=? AND l.company=?
                """,
                (item_id, company),
            ).fetchone()
        if not row:
            return None
        if row["status"] == "paid":
            paid_cents = int(row["paid_principal_cents"] or 0) + int(row["paid_interest_cents"] or 0)
        else:
            paid_cents = 0
        open_cents = max(0, row["total_cents"] - paid_cents)
        return {
            "key": f"loan_installment:{row['id']}",
            "kind": "loan_installment",
            "id": str(row["id"]),
            "version": 1,  # see note in _loan_installment_rows: B3 placeholder.
            "description": f"Parcela {row['number']}/{row['count']} — {row['lender']}",
            "due_date": row["due_date"],
            "competence": None,
            "total_cents": row["total_cents"],
            "paid_cents": paid_cents,
            "open_cents": open_cents,
            "status": row["status"],
            "source": "loan",
            "loan_id": str(row["loan_id"]),
            "number": row["number"],
            "count": row["count"],
            "allowed_actions": _installment_allowed_actions(open_cents),
        }

    return None
