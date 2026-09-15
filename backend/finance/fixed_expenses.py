"""Guided setup of the store's fixed monthly expenses (Resultado gerencial).

Each filled row becomes a monthly recurrence whose occurrence for the chosen month
is confirmed right away, so the result reflects it; later months arrive as
forecasts for the partners to confirm (Revisão do mês counts them)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from .. import database as db
from . import accounts
from .expense_schedules import confirm_entry
from .recurrence import create_recurrence, generate_occurrences

TEMPLATE = (
    ("rent", "Aluguel"),
    ("salaries", "Salários"),
    ("payroll_taxes", "Encargos trabalhistas (INSS, FGTS)"),
    ("electricity", "Energia elétrica"),
    ("water", "Água"),
    ("internet", "Internet"),
    ("telephone", "Telefone"),
    ("accounting", "Contabilidade"),
    ("mobne", "Sistema Mobne"),
    ("payment_terminal_rent", "Aluguel da maquininha"),
)
_LABELS = dict(TEMPLATE)


def _configured(conn, company: int, account_id: int, period: Optional[str] = None) -> bool:
    sql = "SELECT 1 FROM financial_recurrences WHERE company=? AND account_id=? AND active=1"
    params = [company, account_id]
    if period:
        sql += " AND start_competence<=? AND (end_competence IS NULL OR end_competence>=?)"
        params += [period, period]
    return conn.execute(sql, params).fetchone() is not None


def setup_template(company: int) -> list:
    rows = []
    for key, label in TEMPLATE:
        account = accounts.account_by_key(company, key)
        with db.connection() as conn:
            configured = _configured(conn, company, account["id"])
        rows.append({
            "system_key": key, "label": label, "account_id": account["id"],
            "sensitive": bool(account["sensitive"]), "configured": configured,
        })
    return rows


def setup_fixed_expenses(company: int, period: str, items: list, *, created_by: Optional[int] = None) -> dict:
    try:
        date.fromisoformat(period + "-01")
    except (TypeError, ValueError):
        raise ValueError("Competência inválida.") from None
    if not items:
        raise ValueError("Preencha o valor de pelo menos uma despesa.")
    clean = []
    for item in items:
        key = item.get("system_key")
        if key not in _LABELS:
            raise ValueError("Despesa fixa desconhecida.")
        amount, day = item.get("amount_cents"), item.get("due_day")
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError(f"Valor inválido para {_LABELS[key]}.")
        if not isinstance(day, int) or isinstance(day, bool) or not 1 <= day <= 31:
            raise ValueError(f"Dia de vencimento inválido para {_LABELS[key]}.")
        clean.append((key, amount, day))

    created, skipped = [], []
    for key, amount, day in clean:
        account = accounts.account_by_key(company, key)
        with db.connection() as conn:
            already = _configured(conn, company, account["id"], period)
        if already:
            skipped.append(key)
            continue
        recurrence = create_recurrence(
            company=company, account_id=account["id"], description=_LABELS[key], amount_cents=amount,
            start_competence=period, due_day=day, created_by=created_by,
        )
        occurrence = next(
            entry for entry in generate_occurrences(recurrence["id"], through_competence=period)
            if entry["competence"] == period
        )
        confirmed = confirm_entry(company, occurrence["id"], expected_version=occurrence["version"], actor_id=created_by)
        created.append({"system_key": key, "recurrence_id": recurrence["id"], "entry_id": confirmed["id"]})
    return {"created": created, "skipped": skipped}
