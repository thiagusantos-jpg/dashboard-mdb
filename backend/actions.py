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
_EDITABLE = ("assignee", "due_date", "priority")
_PRIORITY_WORDS = {"high": "alta", "medium": "média", "low": "baixa"}

# Every action handed to the UI carries its assignee's name and the note written
# when it reached its current status ("O que foi feito" / motivo do descarte).
_SELECT_ACTION = """
    SELECT a.*, u.name AS assignee_name,
        (SELECT e.note FROM action_events e
         WHERE e.action_id=a.id AND e.to_status=a.status
         ORDER BY e.created_at DESC, e.id DESC LIMIT 1) AS status_note
    FROM actions a LEFT JOIN users u ON u.id=a.assignee
"""


def _new_id() -> int:
    return secrets.randbits(63) or 1


def _get(conn, action_id: int) -> Optional[dict]:
    row = conn.execute(_SELECT_ACTION + " WHERE a.id=?", (action_id,)).fetchone()
    return dict(row) if row else None


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
    baseline_count: Optional[int] = None,
    created_by: Optional[int] = None,
) -> dict:
    """Same alert, same version, already active → same action (no duplicate
    work items every time the alert re-fires). A new version of a condition
    that was already resolved is a genuinely new problem and gets a new id —
    closing an action never blocks the same alert from being raised again."""
    with db.connection() as conn:
        existing = conn.execute(
            """
            SELECT id FROM actions
            WHERE company=? AND alert_key=? AND alert_version=? AND status IN ('open','in_progress')
            """,
            (company, alert_key, alert_version),
        ).fetchone()
        if existing:
            return _get(conn, existing["id"])

        action_id = _new_id()
        timestamp = db.now()
        conn.execute(
            """
            INSERT INTO actions(
                id,company,alert_key,alert_version,title,status,priority,assignee,due_date,
                evidence,baseline_count,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                action_id, company, alert_key, alert_version, title.strip(), "open", priority,
                assignee, due_date, evidence, baseline_count, timestamp, timestamp,
            ),
        )
        conn.execute(
            "INSERT INTO action_events(id,action_id,event_type,to_status,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (_new_id(), action_id, "created", "open", created_by, timestamp),
        )
        return _get(conn, action_id)


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
        # "Assumir" is how a partner takes an action: without an assignee, it becomes theirs.
        assignee = action["assignee"]
        if to_status == "in_progress" and assignee is None and created_by is not None:
            assignee = created_by
        conn.execute(
            "UPDATE actions SET status=?,resolved_at=?,assignee=?,updated_at=? WHERE id=?",
            (to_status, resolved_at, assignee, timestamp, action_id),
        )
        conn.execute(
            """
            INSERT INTO action_events(id,action_id,event_type,from_status,to_status,note,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (_new_id(), action_id, "status_changed", from_status, to_status, note.strip(), created_by, timestamp),
        )
        return _get(conn, action_id)


def _describe(changed: dict, conn) -> str:
    parts = []
    if "assignee" in changed:
        if changed["assignee"] is None:
            parts.append("Responsável removido")
        else:
            row = conn.execute("SELECT name FROM users WHERE id=?", (changed["assignee"],)).fetchone()
            parts.append(f"Responsável: {row['name'] if row else 'usuário removido'}")
    if "due_date" in changed:
        due = changed["due_date"]
        parts.append(f"Prazo: {due[8:10]}/{due[5:7]}/{due[:4]}" if due else "Prazo removido")
    if "priority" in changed:
        parts.append(f"Prioridade: {_PRIORITY_WORDS[changed['priority']]}")
    return "; ".join(parts)


def update_action(action_id: int, changes: dict, *, created_by: Optional[int] = None) -> dict:
    """Assignee, due date and priority of an open action. Each real change is one
    'updated' event whose note says what changed, in the partner's words."""
    fields = {key: value for key, value in changes.items() if key in _EDITABLE}
    timestamp = db.now()
    with db.connection() as conn:
        action = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action:
            raise ValueError("Ação não encontrada.")
        if action["status"] not in _ACTIVE_STATUSES:
            raise ValueError("Ação encerrada não pode ser alterada.")
        changed = {key: value for key, value in fields.items() if action[key] != value}
        if not changed:
            return _get(conn, action_id)
        assignments = ",".join(f"{key}=?" for key in changed)
        conn.execute(
            f"UPDATE actions SET {assignments},updated_at=? WHERE id=?",
            (*changed.values(), timestamp, action_id),
        )
        conn.execute(
            "INSERT INTO action_events(id,action_id,event_type,note,created_by,created_at) VALUES(?,?,?,?,?,?)",
            (_new_id(), action_id, "updated", _describe(changed, conn), created_by, timestamp),
        )
        return _get(conn, action_id)


def assignees(company: int) -> list:
    """People who can own an action of this company: its active users and global admins."""
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id,u.name FROM users u
            WHERE u.active=1 AND (
                u.is_admin=1 OR EXISTS(SELECT 1 FROM user_scopes s WHERE s.user_id=u.id AND s.company=?)
            )
            ORDER BY u.name,u.id
            """,
            (company,),
        ).fetchall()
    return [dict(row) for row in rows]


def is_assignable(company: int, user_id: int) -> bool:
    return any(person["id"] == user_id for person in assignees(company))


def list_events(action_id: int) -> list:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT e.*, u.name AS created_by_name FROM action_events e
            LEFT JOIN users u ON u.id=e.created_by
            WHERE e.action_id=? ORDER BY e.created_at,e.id
            """,
            (action_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_actions(company: int, *, status: Optional[str] = None) -> list:
    where = " AND a.status=?" if status else ""
    params = (company, status) if status else (company,)
    with db.connection() as conn:
        rows = conn.execute(
            _SELECT_ACTION + " WHERE a.company=?" + where + " ORDER BY a.created_at DESC", params
        ).fetchall()
    return [dict(row) for row in rows]


_MONTH_LABELS = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")
CLOSING_REMINDER_DAY = 5


def ensure_closing_reminder(company: int, today, *, created_by: Optional[int] = None) -> dict:
    """Last month's closing action in the Central de Ações. Created once, from day 5 on,
    and never again after it is concluded or dismissed; resolved by itself as soon as
    the month is marked as reviewed. Runs whenever someone opens the dashboard, so no
    scheduled job is needed."""
    from .finance import period_reviews

    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    period = f"{year:04d}-{month:02d}"
    if today.day < CLOSING_REMINDER_DAY:
        return {"period": period, "status": "too_early", "action": None}
    key = f"fechamento:{period}"
    reviewed = period_reviews.get_review(company, period)["status"] == "reviewed"
    with db.connection() as conn:
        existing = conn.execute(
            "SELECT id,status FROM actions WHERE company=? AND alert_key=? ORDER BY created_at DESC LIMIT 1",
            (company, key),
        ).fetchone()
        current = _get(conn, existing["id"]) if existing else None
    if current:
        if reviewed and current["status"] in _ACTIVE_STATUSES:
            resolved = transition_action(current["id"], "resolved", note="Mês marcado como revisado.", created_by=created_by)
            return {"period": period, "status": "resolved", "action": resolved}
        return {"period": period, "status": "exists", "action": current}
    if reviewed:
        return {"period": period, "status": "reviewed", "action": None}
    label = f"{_MONTH_LABELS[month - 1]}/{year}"
    action = create_from_alert(
        company, key, period,
        f"Fechar {label}: confirmar as despesas previstas, enviar o XML da Stone e marcar o mês como revisado",
        priority="high", due_date=f"{today.year:04d}-{today.month:02d}-10", created_by=created_by,
    )
    return {"period": period, "status": "created", "action": action}


DUE_REMINDER_DAYS = 2
DUE_REMINDER_LIMIT = 8


def _due_reminder_title(item: dict, today) -> str:
    due = str(item["due_date"])[:10]
    when = f"{due[8:10]}/{due[5:7]}"
    verb = "venceu em" if due < today.isoformat() else "vence em"
    return f"Pagar {item['description']} — {verb} {when}"[:240]


def ensure_due_reminders(company: int, today, *, created_by: Optional[int] = None, limit: int = DUE_REMINDER_LIMIT) -> dict:
    """One action per bill already overdue or falling due within two days, so the
    partner is warned without opening Contas a pagar. Created once per bill (a
    dismissed one never comes back) and resolved by itself once the bill is paid.
    Capped per run so a long overdue list does not flood the Central de Ações."""
    from datetime import timedelta

    from .finance import obligations

    horizon = (today + timedelta(days=DUE_REMINDER_DAYS)).isoformat()
    items = [
        item for item in obligations.list_obligations(company, limit=200, include_sensitive=True)["items"]
        if item["open_cents"] > 0
    ]
    open_keys = {f"vencimento:{item['kind']}:{item['id']}" for item in items}

    with db.connection() as conn:
        # O padrão do LIKE vai como parâmetro, nunca no texto do SQL: database.py::_PGConn
        # troca '?' por '%s' antes de entregar a query ao psycopg, e um '%' literal que
        # sobrasse ali seria lido como placeholder ("only '%s', '%b', '%t' are allowed").
        # O SQLite aceitava dos dois jeitos, então só o Postgres quebrava — em produção.
        rows = conn.execute(
            "SELECT id,alert_key,status FROM actions WHERE company=? AND alert_key LIKE ?",
            (company, "vencimento:%"),
        ).fetchall()
    known = {row["alert_key"] for row in rows}
    stale = [row for row in rows if row["status"] in _ACTIVE_STATUSES and row["alert_key"] not in open_keys]

    resolved = [
        transition_action(row["id"], "resolved", note="Conta paga.", created_by=created_by)
        for row in stale
    ]

    created = []
    for item in items:
        if len(created) >= limit:
            break
        key = f"vencimento:{item['kind']}:{item['id']}"
        if key in known or str(item["due_date"])[:10] > horizon:
            continue
        created.append(create_from_alert(
            company, key, str(item["due_date"])[:10], _due_reminder_title(item, today),
            priority="high" if str(item["due_date"])[:10] < today.isoformat() else "medium",
            due_date=str(item["due_date"])[:10], created_by=created_by,
        ))
    return {"created": created, "resolved": resolved}
