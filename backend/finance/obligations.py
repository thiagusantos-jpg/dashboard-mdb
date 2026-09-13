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

# Account natures that represent money coming IN (revenue/financing), never a
# payable. Obligations returned by this module are exclusively "contas a
# pagar" — mirrors the precedent already set elsewhere in this codebase:
# entries.py::result_for filters TO expense natures, entries.py::cash_for
# filters OUT these same two natures. Without this filter, a manual entry
# booked against a revenue-nature account (e.g. the `sales` system account)
# would surface here as a "payable", which it is not.
_NON_PAYABLE_NATURES = ("revenue", "financing_inflow")

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


def _compute_status(status: str, due_date: str, open_cents: int) -> str:
    """Read-time derived overdue status — never persisted.

    Mirrors backend/finance/entries.py::get_entry's idiom (`if status=='open'
    and due_date<today: status='overdue'`), extended to `partially_paid` per
    the shared contract's "Status de atraso é calculado por data civil e
    saldo" (calendar date AND balance): a still-open-ish item whose
    outstanding balance is positive and whose due date has passed "today" is
    reported as overdue. A final status (paid/cancelled/reversed) or a
    zeroed-out balance is never overridden.
    """
    if status in ("open", "partially_paid") and open_cents > 0 and due_date < date.today().isoformat():
        return "overdue"
    return status


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


def _installment_allowed_actions(open_cents: int, *, schedule_active: bool) -> list:
    """`schedule_active` is "this installment belongs to the CURRENT schedule
    of an ACTIVE loan" — i.e. `loans.status='active'` AND
    `i.schedule_id=loans.active_schedule_id` AND `loan_schedules.status='active'`.

    Final-review C1: an installment left behind on a renegotiated-away
    (status='closed') or cancelled schedule keeps `status='open'` forever —
    renegotiate()/cancel_loan() never touch the installment rows — so
    `open_cents>0` alone would advertise "pay" for a debt that was already
    replaced by the new schedule's installments (paying it would move real
    cash against principal that is still outstanding elsewhere: the same debt
    payable twice). `record_payment` refuses such a payment with a 409; this
    keeps the advertised action honest instead of offering a button the
    server will reject. Callers with no schedule context at all must say so
    explicitly rather than defaulting.
    """
    actions = ["details"]
    if open_cents > 0 and schedule_active:
        actions.append("pay")
    return actions


def _entry_rows(company: int) -> list:
    placeholders = ",".join("?" for _ in _OPEN_ENTRY_STATUSES)
    nature_placeholders = ",".join("?" for _ in _NON_PAYABLE_NATURES)
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
              AND a.nature NOT IN ({nature_placeholders})
            -- Final-review I1: every non-aggregated selected column is listed,
            -- the joined a.sensitive included. `GROUP BY e.id` alone is valid
            -- on SQLite only; PostgreSQL's functional-dependency relaxation
            -- does not extend to a joined table's columns, so this query would
            -- 500 there. Same convention as backend/finance/reporting.py.
            GROUP BY e.id,e.version,e.description,e.due_date,e.competence,
                     e.amount_cents,e.status,e.source,
                     e.installment_number,e.installment_count,a.sensitive
            """,
            (company, *_OPEN_ENTRY_STATUSES, *_NON_PAYABLE_NATURES),
        ).fetchall()
    items = []
    for row in rows:
        paid_cents = int(row["paid_cents"])
        open_cents = max(0, row["amount_cents"] - paid_cents)
        status = _compute_status(row["status"], row["due_date"], open_cents)
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
            "status": status,
            "source": row["source"],
            "loan_id": None,
            "number": row["installment_number"],
            "count": row["installment_count"],
            # Editability is driven by the *persisted* status (matches
            # entry_management.py::_allowed_fields, which reads the raw DB
            # row and has never heard of "overdue") — not the derived display
            # status computed just above.
            "allowed_actions": _entry_allowed_actions(row["status"], row["source"], open_cents),
            "_sensitive": bool(row["sensitive"]),
        })
    return items


def _loan_installment_rows(company: int) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT i.id,i.number,i.due_date,i.total_cents,i.status,i.loan_id,i.version,
                   i.paid_principal_cents,i.paid_interest_cents,l.lender,
                   (SELECT COUNT(*) FROM loan_installments WHERE schedule_id=i.schedule_id) AS count
            FROM loan_installments i
            JOIN loans l ON l.id=i.loan_id
            JOIN loan_schedules s ON s.id=i.schedule_id
            WHERE l.company=? AND i.schedule_id=l.active_schedule_id
              AND s.status='active' AND l.status='active'
              AND i.status IN ('open','partially_paid')
            """,
            (company,),
        ).fetchall()
    items = []
    for row in rows:
        # Task B3 added real partial-payment support for installments: a row
        # here can now sit at status='partially_paid' with a genuine nonzero
        # paid_cents (paid_principal_cents/paid_interest_cents), so the sum
        # below is unconditional — mirrors
        # payments.py::_loan_installment_obligation_item's computation
        # exactly, which is the source of truth for this logic.
        paid_cents = int(row["paid_principal_cents"] or 0) + int(row["paid_interest_cents"] or 0)
        open_cents = max(0, row["total_cents"] - paid_cents)
        status = _compute_status(row["status"], row["due_date"], open_cents)
        items.append({
            "key": f"loan_installment:{row['id']}",
            "kind": "loan_installment",
            "id": str(row["id"]),
            "version": int(row["version"]),
            "description": f"Parcela {row['number']}/{row['count']} — {row['lender']}",
            "due_date": row["due_date"],
            "competence": None,
            "total_cents": row["total_cents"],
            "paid_cents": paid_cents,
            "open_cents": open_cents,
            "status": status,
            "source": "loan",
            "loan_id": str(row["loan_id"]),
            "number": row["number"],
            "count": row["count"],
            # This query already restricts itself to the active schedule of an
            # active loan (WHERE clause below), so every row here is payable
            # by construction.
            "allowed_actions": _installment_allowed_actions(open_cents, schedule_active=True),
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
        nature_placeholders = ",".join("?" for _ in _NON_PAYABLE_NATURES)
        with db.connection() as conn:
            row = conn.execute(
                f"""
                SELECT e.id,e.version,e.description,e.due_date,e.competence,
                       e.amount_cents,e.status,e.source,
                       e.installment_number,e.installment_count,a.sensitive
                       ,COALESCE(SUM(
                           CASE WHEN ev.event_type='settled' THEN ev.amount_cents
                                WHEN ev.event_type='reversed' THEN -ev.amount_cents ELSE 0 END
                       ),0) AS paid_cents
                FROM financial_entries e
                JOIN finance_accounts a ON a.id=e.account_id
                LEFT JOIN financial_events ev ON ev.entry_id=e.id
                WHERE e.id=? AND e.company=?
                  AND a.nature NOT IN ({nature_placeholders})
                -- Final-review I1: see _entry_rows above — every
                -- non-aggregated selected column must be listed for
                -- PostgreSQL, joined columns included.
                GROUP BY e.id,e.version,e.description,e.due_date,e.competence,
                         e.amount_cents,e.status,e.source,
                         e.installment_number,e.installment_count,a.sensitive
                """,
                (item_id, company, *_NON_PAYABLE_NATURES),
            ).fetchone()
        if not row:
            return None
        if row["sensitive"] and not include_sensitive:
            return None
        paid_cents = int(row["paid_cents"])
        open_cents = max(0, row["amount_cents"] - paid_cents)
        status = _compute_status(row["status"], row["due_date"], open_cents)
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
            "status": status,
            "source": row["source"],
            "loan_id": None,
            "number": row["installment_number"],
            "count": row["installment_count"],
            # See _entry_rows: editability tracks the persisted status, not
            # the derived "overdue" display status.
            "allowed_actions": _entry_allowed_actions(row["status"], row["source"], open_cents),
        }

    if kind == "loan_installment":
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT i.id,i.number,i.due_date,i.total_cents,i.status,i.loan_id,i.version,l.lender,
                       i.paid_principal_cents,i.paid_interest_cents,i.schedule_id,
                       l.status AS loan_status,l.active_schedule_id,
                       s.status AS schedule_status,
                       (SELECT COUNT(*) FROM loan_installments WHERE schedule_id=i.schedule_id) AS count
                FROM loan_installments i
                JOIN loans l ON l.id=i.loan_id
                LEFT JOIN loan_schedules s ON s.id=i.schedule_id
                WHERE i.id=? AND l.company=?
                """,
                (item_id, company),
            ).fetchone()
        if not row:
            return None
        # Real partial-payment support (task B3): paid_principal_cents/
        # paid_interest_cents hold genuine partial amounts whenever
        # status='partially_paid', not just when status='paid' — sum them
        # unconditionally, matching
        # payments.py::_loan_installment_obligation_item.
        paid_cents = int(row["paid_principal_cents"] or 0) + int(row["paid_interest_cents"] or 0)
        open_cents = max(0, row["total_cents"] - paid_cents)
        status = _compute_status(row["status"], row["due_date"], open_cents)
        # This branch deliberately returns installments on closed/cancelled
        # schedules too (history access) — so, unlike the list query above,
        # "payable" has to be computed here rather than assumed. See
        # _installment_allowed_actions / final-review C1.
        schedule_active = (
            row["loan_status"] == "active"
            and row["schedule_id"] == row["active_schedule_id"]
            and row["schedule_status"] == "active"
        )
        return {
            "key": f"loan_installment:{row['id']}",
            "kind": "loan_installment",
            "id": str(row["id"]),
            "version": int(row["version"]),
            "description": f"Parcela {row['number']}/{row['count']} — {row['lender']}",
            "due_date": row["due_date"],
            "competence": None,
            "total_cents": row["total_cents"],
            "paid_cents": paid_cents,
            "open_cents": open_cents,
            "status": status,
            "source": "loan",
            "loan_id": str(row["loan_id"]),
            "number": row["number"],
            "count": row["count"],
            "allowed_actions": _installment_allowed_actions(
                open_cents, schedule_active=schedule_active
            ),
        }

    return None
