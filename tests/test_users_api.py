from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "users-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
        conn.execute("INSERT INTO companies(id,name) VALUES(2,'Loja 2')")
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def test_admin_creates_scoped_user_without_exposing_password_hash(client):
    response = client.post(
        "/api/companies/1/users",
        json={
            "email": "gerente@loja.test",
            "name": "Gerente",
            "password": "senha-segura",
            "role": "manager",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["email"] == "gerente@loja.test"
    assert response.json()["role"] == "manager"
    assert "password_hash" not in response.json()


def test_invalid_role_does_not_leave_an_orphan_user(client):
    response = client.post(
        "/api/companies/1/users",
        json={
            "email": "sem-perfil@loja.test",
            "name": "Sem perfil",
            "password": "senha-segura",
            "role": "unknown",
        },
    )

    assert response.status_code == 422
    with db.connection() as conn:
        assert conn.execute(
            "SELECT 1 FROM users WHERE email='sem-perfil@loja.test'"
        ).fetchone() is None


def test_company_scope_blocks_cross_company_access(client):
    created = client.post(
        "/api/companies/1/users",
        json={
            "email": "gerente@loja.test",
            "name": "Gerente",
            "password": "senha-segura",
            "role": "manager",
        },
    )
    assert created.status_code == 201
    assert client.post("/api/logout").status_code == 200
    client.cookies.clear()
    login = client.post(
        "/api/login",
        json={"email": "gerente@loja.test", "password": "senha-segura"},
    )
    assert login.status_code == 200
    client.headers["x-csrf-token"] = login.json()["csrf"]

    assert client.get("/api/companies/1/status").status_code == 200
    assert client.get("/api/companies/2/status").status_code == 403
    assert client.post(
        "/api/companies/1/users",
        json={
            "email": "outro@loja.test",
            "name": "Outro",
            "password": "senha-segura",
            "role": "viewer",
        },
    ).status_code == 403
