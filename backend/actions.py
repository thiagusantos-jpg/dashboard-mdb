from __future__ import annotations

import secrets
from typing import Optional

from . import database as db


_ACTIVE_STATUSES = ("open", "in_progress")
_TERMINAL_STATUSES = ("resolved", "dismissed")
_ALLOWED_TRANSITIONS = {
    "open": {"in_progress", "resolved", "dismissed"},
    "in_progress": {"resolved", "dismissed", "open"},
}


def _new_id() -> int:
    return secrets.randbits(63) or 1


def create_from_alert(
    company: int,
    alert_key: str,
    alert_version: str,
    title: str,
    *,
    priority: str = "medium",
    assignee: Optional[int] = None,
    due_date: Optional[str] = None,
    evidence: str = "",
    created_by: Optional[int] = None,
) -> dict:
    """Same alert, same version, already active → same action (no duplicate
    work items every time the alert re-fires). A new version of a condition
    that was already resolved is a genuinely new problem and gets a new id —
    closing an action never blocks the same alert from being raised again."""
    with db.connection() as conn:
        existing = conn.execute(
            """
            SELECT * FROM actions
            WHERE company=? AND alert_key=? AND alert_version=? AND status IN ('open','in_progress')
            """,
            (company, alert_key, alert_version),
        ).fetchone()
        if existing:
            return dict(existing)

        action_id = _new_id()
        timestamp = db.now()
        conn.execute(
            """
            INSERT INTO actions(
                id,company,alert_key,alert_version,title,status,priority,assignee,due_date,
                evidence,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                action_id, company, alert_key, alert_version, title.strip(), "open", priority,
                assignee, due_date, evidence, timestamp, timestamp,
            ),
        )
        conn.execute(
            "INSERT INTO action_events(id,action_id,event_type,to_status,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (_new_id(), action_id, "created", "open", created_by, timestamp),
        )
        return dict(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())


def transition_action(action_id: int, to_status: str, *, note: str = "", created_by: Optional[int] = None) -> dict:
    timestamp = db.now()
    with db.connection() as conn:
        action = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action:
            raise ValueError("Ação não encontrada.")
        from_status = action["status"]
        allowed = _ALLOWED_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise ValueError(f"Transição inválida: {from_status} → {to_status}.")
        resolved_at = timestamp if to_status in _TERMINAL_STATUSES else action["resolved_at"]
        conn.execute(
            "UPDATE actions SET status=?,resolved_at=?,updated_at=? WHERE id=?",
            (to_status, resolved_at, timestamp, action_id),
        )
        conn.execute(
            """
            INSERT INTO action_events(id,action_id,event_type,from_status,to_status,note,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (_new_id(), action_id, "status_changed", from_status, to_status, note.strip(), created_by, timestamp),
        )
        return dict(conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone())


def list_events(action_id: int) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM action_events WHERE action_id=? ORDER BY created_at,id", (action_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def list_actions(company: int, *, status: Optional[str] = None) -> list:
    where = " AND status=?" if status else ""
    params = (company, status) if status else (company,)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM actions WHERE company=?" + where + " ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(row) for row in rows]
