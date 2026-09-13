"""Maintenance of supporting records for a single store (task C5): categories,
counterparties, cash accounts, calendar exceptions and users are edited or
archived with version checks, and history always survives."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, identity, permissions, security
from backend.finance.reporting import management_result


BASE = "/api/companies/1/finance"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "catalog-maintenance.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
        conn.execute("INSERT INTO companies(id,name) VALUES(2,'Outra empresa')")
    with TestClient(api.app) as test_client:
        login = test_client.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"})
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def _category(client, name="Manutenção"):
    response = client.post(f"{BASE}/accounts", json={"name": name, "nature": "operating_expense"})
    assert response.status_code == 201, response.text
    return response.json()


def _expense(client, account_id, amount=100_000, counterparty_id=None):
    body = {
        "account_id": str(account_id), "amount_cents": amount, "competence": "2026-09",
        "due_date": "2026-09-20", "description": "Reforma",
    }
    if counterparty_id is not None:
        body["counterparty_id"] = str(counterparty_id)
    return client.post(f"{BASE}/entries", json=body)


def test_archiving_a_category_keeps_its_history(client):
    category = _category(client)
    assert _expense(client, category["id"]).status_code == 201
    before = management_result(1, "2026-09")["operating_expenses_cents"]

    archived = client.patch(f"{BASE}/accounts/{category['id']}", json={"expected_version": category["version"], "archived": True})

    assert archived.status_code == 200, archived.text
    assert archived.json()["archived"] is True
    historical = client.get(f"{BASE}/entries?competence=2026-09").json()
    assert any(str(e["account_id"]) == str(category["id"]) for e in historical)
    assert management_result(1, "2026-09")["operating_expenses_cents"] == before == 100_000
    active_ids = {str(a["id"]) for a in client.get(f"{BASE}/accounts").json()}
    all_ids = {str(a["id"]) for a in client.get(f"{BASE}/accounts?include_archived=true").json()}
    assert str(category["id"]) not in active_ids
    assert str(category["id"]) in all_ids


def test_renaming_a_category_with_a_stale_version_conflicts(client):
    category = _category(client)

    first = client.patch(f"{BASE}/accounts/{category['id']}", json={"expected_version": category["version"], "name": "Manutenção predial"})
    stale = client.patch(f"{BASE}/accounts/{category['id']}", json={"expected_version": category["version"], "name": "Outra"})

    assert first.status_code == 200, first.text
    assert first.json()["name"] == "Manutenção predial"
    assert stale.status_code == 409


def test_counterparty_is_editable_only_within_its_company(client):
    own = client.post(f"{BASE}/counterparties", json={"name": "Fornecedor A", "kind": "supplier"}).json()
    foreign = client.post("/api/companies/2/finance/counterparties", json={"name": "Fornecedor B", "kind": "supplier"}).json()

    renamed = client.patch(f"{BASE}/counterparties/{own['id']}", json={"expected_version": own["version"], "name": "Fornecedor A Ltda"})
    cross = client.patch(f"{BASE}/counterparties/{foreign['id']}", json={"expected_version": foreign["version"], "name": "Invasão"})
    stale = client.patch(f"{BASE}/counterparties/{own['id']}", json={"expected_version": own["version"], "name": "De novo"})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Fornecedor A Ltda"
    assert cross.status_code == 404
    assert stale.status_code == 409


def test_an_expense_cannot_use_another_company_counterparty(client):
    category = _category(client)
    foreign = client.post("/api/companies/2/finance/counterparties", json={"name": "Fornecedor B", "kind": "supplier"}).json()

    response = _expense(client, category["id"], counterparty_id=foreign["id"])

    assert response.status_code == 422


def test_archiving_a_cash_account_keeps_balance_and_movements(client):
    account = client.post(f"{BASE}/cash-accounts", json={"name": "Banco X", "kind": "bank"}).json()
    moved = client.post(f"{BASE}/cash-events", json={
        "cash_account_id": str(account["id"]), "amount_cents": 25_000, "occurred_at": "2026-09-01", "description": "Saldo inicial",
    })
    assert moved.status_code in (200, 201), moved.text
    balance_before = next(a for a in client.get(f"{BASE}/cash-accounts").json() if str(a["id"]) == str(account["id"]))["balance_cents"]

    renamed = client.patch(f"{BASE}/cash-accounts/{account['id']}", json={"expected_version": account["version"], "name": "Banco X — PJ"})
    archived = client.patch(f"{BASE}/cash-accounts/{account['id']}", json={"expected_version": renamed.json()["version"], "archived": True})
    stale = client.patch(f"{BASE}/cash-accounts/{account['id']}", json={"expected_version": account["version"], "name": "Velho"})

    assert renamed.status_code == 200, renamed.text
    assert archived.status_code == 200, archived.text
    assert stale.status_code == 409
    listed = client.get(f"{BASE}/cash-accounts?include_archived=true").json()
    balance_after = next(a for a in listed if str(a["id"]) == str(account["id"]))["balance_cents"]
    assert balance_after == balance_before == 25_000
    assert all(str(a["id"]) != str(account["id"]) for a in client.get(f"{BASE}/cash-accounts").json())


def test_calendar_exception_is_editable_and_removable_without_touching_other_dates(client):
    christmas = client.put("/api/companies/1/settings/calendar", json={"date": "2026-12-25", "status": "closed", "description": "Natal"}).json()
    new_year = client.put("/api/companies/1/settings/calendar", json={"date": "2027-01-01", "status": "closed", "description": "Ano novo"}).json()

    edited = client.put("/api/companies/1/settings/calendar", json={
        "date": "2026-12-25", "status": "closed", "description": "Natal — fechado", "expected_version": christmas["version"],
    })
    stale_delete = client.delete(f"/api/companies/1/settings/calendar/2026-12-25?expected_version={christmas['version']}")
    removed = client.delete(f"/api/companies/1/settings/calendar/2026-12-25?expected_version={edited.json()['version']}")

    assert edited.status_code == 200, edited.text
    assert stale_delete.status_code == 409
    assert removed.status_code == 204, removed.text
    dates = [entry["date"] for entry in client.get("/api/companies/1/settings/calendar").json()]
    assert dates == ["2027-01-01"]
    assert new_year["version"] == 1


def test_user_profile_edit_respects_version_and_blocks_self_role_change(client):
    created = client.post("/api/companies/1/users", json={
        "email": "gerente@loja.test", "name": "Gerente", "password": "senha-segura", "role": "manager",
    }).json()
    admin_id = client.get("/api/me").json()["id"]

    renamed = client.patch(f"/api/companies/1/users/{created['id']}", json={"expected_version": created["version"], "name": "Gerente da loja"})
    stale = client.patch(f"/api/companies/1/users/{created['id']}", json={"expected_version": created["version"], "name": "Outro"})
    self_role = client.put(f"/api/companies/1/users/{admin_id}/role", json={"role": "viewer"})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Gerente da loja"
    assert stale.status_code == 409
    assert self_role.status_code == 409


def test_last_usable_administrator_cannot_be_removed(client):
    only_admin = identity.create_user("dono@loja.test", "senha-segura-1", "Dono")
    permissions.grant_role(only_admin["id"], 1, "administrator")
    with db.connection() as conn:
        conn.execute("UPDATE users SET is_admin=0")  # no global administrator left besides the scoped one

    assert permissions.usable_administrators(1, excluding_user_id=only_admin["id"]) == 0
    with pytest.raises(ValueError):
        permissions.ensure_administrator_remains(1, only_admin["id"], new_role="manager")
    permissions.ensure_administrator_remains(1, only_admin["id"], new_role="administrator")


def test_default_system_categories_can_be_renamed_but_not_archived(client):
    electricity = next(a for a in client.get(f"{BASE}/accounts").json() if a["system_key"] == "electricity")

    archived = client.patch(f"{BASE}/accounts/{electricity['id']}", json={"expected_version": electricity["version"], "archived": True})
    renamed = client.patch(f"{BASE}/accounts/{electricity['id']}", json={"expected_version": electricity["version"], "name": "Energia elétrica"})

    assert archived.status_code == 422
    assert renamed.status_code == 200, renamed.text
