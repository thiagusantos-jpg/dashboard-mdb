"""Lembrete de vencimento na Central de Ações (item 2 de Contas a pagar): uma ação por
conta que vence em até 2 dias ou já venceu, criada uma vez e resolvida quando a conta é paga."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import actions, api, database as db, security
from backend.finance import accounts
from backend.finance.entries import EntryCommand, create_entry, settle_entry


TODAY = date(2026, 9, 15)


@pytest.fixture
def finance_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "due-reminders.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "due-reminders-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"})
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def _bill(description, due, cents=100_000, key="rent"):
    account = accounts.account_by_key(1, key)
    return create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=cents, competence=due[:7],
        due_date=date.fromisoformat(due), source="manual", external_id=None, description=description,
    ))


def test_creates_one_action_per_bill_due_soon(finance_db):
    overdue = _bill("Requinte Imobiliária", "2026-09-10", 800_000)
    _bill("Enel", "2026-09-15", 50_000, key="electricity")
    _bill("Sabesp", "2026-09-17", 20_000, key="water")
    _bill("Contador", "2026-09-25", 90_000, key="accounting")

    result = actions.ensure_due_reminders(1, TODAY)

    created = {action["alert_key"]: action for action in result["created"]}
    assert set(created) == {f"vencimento:entry:{overdue['id']}"} | {
        key for key in created if key.startswith("vencimento:entry:")
    }
    assert len(created) == 3, "a conta que vence em 10 dias ainda não vira ação"
    late = created[f"vencimento:entry:{overdue['id']}"]
    assert late["priority"] == "high"
    assert late["due_date"] == "2026-09-10"
    assert late["title"] == "Pagar Requinte Imobiliária — venceu em 10/09"
    soon = next(a for a in created.values() if a["title"].startswith("Pagar Sabesp"))
    assert soon["priority"] == "medium"
    assert soon["title"] == "Pagar Sabesp — vence em 17/09"


def test_does_not_repeat_the_same_bill(finance_db):
    _bill("Enel", "2026-09-15", 50_000, key="electricity")
    first = actions.ensure_due_reminders(1, TODAY)
    assert len(first["created"]) == 1

    assert actions.ensure_due_reminders(1, TODAY)["created"] == []
    actions.transition_action(first["created"][0]["id"], "dismissed")
    assert actions.ensure_due_reminders(1, TODAY)["created"] == []
    assert len(actions.list_actions(1)) == 1


def test_resolves_itself_when_the_bill_is_paid(finance_db):
    bill = _bill("Enel", "2026-09-15", 50_000, key="electricity")
    actions.ensure_due_reminders(1, TODAY)
    settle_entry(bill["id"], 50_000, paid_at=date(2026, 9, 15))

    result = actions.ensure_due_reminders(1, TODAY)

    assert [a["status"] for a in result["resolved"]] == ["resolved"]
    assert result["resolved"][0]["status_note"] == "Conta paga."
    assert result["created"] == []


def test_limits_how_many_actions_one_run_creates(finance_db):
    for day in range(1, 13):
        _bill(f"Fornecedor {day:02d}", f"2026-09-{day:02d}", 10_000)

    created = actions.ensure_due_reminders(1, TODAY)["created"]

    assert len(created) == 8, "um lote por vez, para não inundar a Central"
    assert [a["due_date"] for a in created] == [f"2026-09-{day:02d}" for day in range(1, 9)]
    assert len(actions.ensure_due_reminders(1, TODAY)["created"]) == 4, "o resto vem na próxima vez"


def test_endpoint_answers(client):
    _bill("Enel", "2026-09-15", 50_000, key="electricity")
    response = client.post("/api/companies/1/actions/due-reminders")
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"created", "resolved"}
