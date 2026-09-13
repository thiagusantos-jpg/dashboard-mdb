from __future__ import annotations

import pytest

from backend.permissions import role_allows


@pytest.mark.parametrize(
    "role,permission,allowed",
    [
        ("administrator", "users.manage", True),
        ("partner", "finance.sensitive.read", True),
        ("manager", "finance.sensitive.read", False),
        ("manager", "inventory.write", True),
        ("viewer", "inventory.write", False),
        ("viewer", "dashboard.read", True),
    ],
)
def test_default_roles(role, permission, allowed):
    assert role_allows(role, permission) is allowed



def test_management_result_endpoint_never_leaks_sensitive_totals(tmp_path, monkeypatch):
    from datetime import date

    from fastapi.testclient import TestClient

    from backend import api, database as db, identity, permissions, security
    from backend.finance import accounts
    from backend.finance.entries import EntryCommand, create_entry

    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "permissions-report.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    secret = accounts.create_account(1, "Retirada extra", accounts.AccountNature.OPERATING_EXPENSE, sensitive=True)
    create_entry(EntryCommand(
        company_id=1, account_id=secret["id"], amount_cents=50_000, competence="2026-09",
        due_date=date(2026, 9, 20), source="manual", external_id=None, description="Retirada",
    ))

    def login(client, email, password):
        response = client.post("/api/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        client.headers["x-csrf-token"] = response.json()["csrf"]

    with TestClient(api.app) as admin:
        login(admin, "admin@loja.test", "bootstrap-password")
        viewer_user = identity.create_user("leitor@loja.test", "senha-segura-1", "Leitor")
        permissions.grant_role(viewer_user["id"], 1, "viewer")
        viewer = TestClient(api.app)
        login(viewer, "leitor@loja.test", "senha-segura-1")

        full = admin.get("/api/companies/1/finance/management-result?period=2026-09").json()
        limited = viewer.get("/api/companies/1/finance/management-result?period=2026-09")

    assert limited.status_code == 200, limited.text
    body = limited.json()
    assert full["operating_expenses_cents"] == 50_000
    assert body["operating_expenses_cents"] is None
    assert body["data_status"]["restricted"] is True
    assert all(str(line["account_id"]) != str(secret["id"]) for line in body["accounts"])
