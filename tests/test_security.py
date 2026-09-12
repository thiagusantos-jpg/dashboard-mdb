from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, identity, security


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "security.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    db.initialize()
    with TestClient(api.app) as test_client:
        yield test_client


def bootstrap_admin(client):
    response = client.post(
        "/api/login",
        json={"email": "admin@loja.test", "password": "bootstrap-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf"]
    return response


def test_disabled_user_loses_existing_session(client):
    bootstrap_admin(client)
    user = identity.user_by_email("admin@loja.test")

    identity.disable_user(user["id"])

    assert client.get("/api/session").status_code == 401


def test_email_is_required_after_first_admin_is_created(client):
    bootstrap_admin(client)
    client.cookies.clear()

    assert client.post(
        "/api/login", json={"password": "bootstrap-password"}
    ).status_code == 401
    assert client.post(
        "/api/login",
        json={"email": "admin@loja.test", "password": "bootstrap-password"},
    ).status_code == 200
