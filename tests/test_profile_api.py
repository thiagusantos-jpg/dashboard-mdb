from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, identity, security


ADMIN_EMAIL = "admin@loja.test"
ADMIN_PASSWORD = "bootstrap-password"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "profile-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: ADMIN_PASSWORD)
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        _login(test_client, ADMIN_EMAIL, ADMIN_PASSWORD)
        yield test_client


def _login(test_client, email, password):
    response = test_client.post("/api/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    test_client.headers["x-csrf-token"] = response.json()["csrf"]
    return response


def _second_session(email, password):
    """Another browser for the same account, to observe session revocation."""
    other = TestClient(api.app)
    _login(other, email, password)
    return other


def _me(test_client):
    response = test_client.get("/api/me")
    assert response.status_code == 200, response.text
    return response.json()


def test_get_me_returns_only_public_fields(client):
    me = _me(client)

    assert me["email"] == ADMIN_EMAIL
    assert set(me) == {"id", "name", "email", "version"}


def test_name_change_keeps_every_session(client):
    other = _second_session(ADMIN_EMAIL, ADMIN_PASSWORD)
    me = _me(client)

    response = client.patch(
        "/api/me",
        json={"name": "Nome atualizado", "email": ADMIN_EMAIL, "expected_version": me["version"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Nome atualizado"
    assert response.json()["version"] == me["version"] + 1
    assert "password_hash" not in response.json()
    assert "csrf" not in response.json()
    assert other.get("/api/me").status_code == 200


def test_email_change_requires_current_password(client):
    me = _me(client)

    missing = client.patch(
        "/api/me",
        json={"name": me["name"], "email": "novo@loja.test", "expected_version": me["version"]},
    )
    wrong = client.patch(
        "/api/me",
        json={
            "name": me["name"],
            "email": "novo@loja.test",
            "current_password": "senha-errada",
            "expected_version": me["version"],
        },
    )

    assert missing.status_code == 422
    assert missing.json()["detail"]["fields"] == ["current_password"]
    # A wrong password is a field error, never a 401 that would log the user out.
    assert wrong.status_code == 422
    assert wrong.json()["detail"]["fields"] == ["current_password"]
    assert _me(client)["email"] == ADMIN_EMAIL


def test_email_change_renews_current_session_and_revokes_others(client):
    other = _second_session(ADMIN_EMAIL, ADMIN_PASSWORD)
    me = _me(client)

    response = client.patch(
        "/api/me",
        json={
            "name": me["name"],
            "email": "  NOVO@Loja.test ",
            "current_password": ADMIN_PASSWORD,
            "expected_version": me["version"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["email"] == "novo@loja.test"
    assert response.json()["csrf"]
    client.headers["x-csrf-token"] = response.json()["csrf"]
    assert _me(client)["email"] == "novo@loja.test"
    assert other.get("/api/me").status_code == 401
    assert identity.verify_credentials("novo@loja.test", ADMIN_PASSWORD) is not None


def test_duplicate_email_is_rejected(client):
    created = client.post(
        "/api/companies/1/users",
        json={"email": "gerente@loja.test", "name": "Gerente", "password": "senha-segura", "role": "manager"},
    )
    assert created.status_code == 201, created.text
    me = _me(client)

    response = client.patch(
        "/api/me",
        json={
            "name": me["name"],
            "email": "gerente@loja.test",
            "current_password": ADMIN_PASSWORD,
            "expected_version": me["version"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["fields"] == ["email"]


def test_stale_version_is_rejected(client):
    me = _me(client)
    first = client.patch(
        "/api/me", json={"name": "Primeira", "email": ADMIN_EMAIL, "expected_version": me["version"]}
    )
    assert first.status_code == 200

    stale = client.patch(
        "/api/me", json={"name": "Segunda", "email": ADMIN_EMAIL, "expected_version": me["version"]}
    )

    assert stale.status_code == 409
    assert _me(client)["name"] == "Primeira"


def test_password_change_replaces_credentials_and_revokes_other_sessions(client):
    other = _second_session(ADMIN_EMAIL, ADMIN_PASSWORD)
    me = _me(client)

    response = client.post(
        "/api/me/password",
        json={
            "current_password": ADMIN_PASSWORD,
            "new_password": "nova-senha-forte",
            "expected_version": me["version"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["csrf"]
    client.headers["x-csrf-token"] = response.json()["csrf"]
    assert _me(client)["version"] == me["version"] + 1
    assert other.get("/api/me").status_code == 401
    assert identity.verify_credentials(ADMIN_EMAIL, ADMIN_PASSWORD) is None
    assert identity.verify_credentials(ADMIN_EMAIL, "nova-senha-forte") is not None
    # The renewed session can still write (fresh CSRF token matches the new cookie).
    assert client.patch(
        "/api/me", json={"name": "Depois da troca", "email": ADMIN_EMAIL, "expected_version": me["version"] + 1}
    ).status_code == 200


def test_password_change_with_wrong_current_password_is_a_field_error(client):
    me = _me(client)

    response = client.post(
        "/api/me/password",
        json={"current_password": "errada", "new_password": "nova-senha-forte", "expected_version": me["version"]},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["fields"] == ["current_password"]
    assert identity.verify_credentials(ADMIN_EMAIL, ADMIN_PASSWORD) is not None
    assert _me(client)["version"] == me["version"]


def test_short_new_password_is_rejected(client):
    me = _me(client)

    response = client.post(
        "/api/me/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "curta", "expected_version": me["version"]},
    )

    assert response.status_code == 422
    assert identity.verify_credentials(ADMIN_EMAIL, ADMIN_PASSWORD) is not None


def test_viewer_edits_own_account_without_gaining_privileges(client):
    created = client.post(
        "/api/companies/1/users",
        json={"email": "leitor@loja.test", "name": "Leitor", "password": "senha-segura", "role": "viewer"},
    )
    assert created.status_code == 201, created.text
    admin_id = _me(client)["id"]
    viewer = TestClient(api.app)
    _login(viewer, "leitor@loja.test", "senha-segura")
    me = _me(viewer)

    response = viewer.patch(
        "/api/me",
        json={
            "name": "Leitor renomeado",
            "email": "leitor@loja.test",
            "expected_version": me["version"],
            "is_admin": True,
            "role": "admin",
            "user_id": admin_id,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == me["id"]
    with db.connection() as conn:
        viewer_row = conn.execute("SELECT is_admin FROM users WHERE id=?", (me["id"],)).fetchone()
        admin_row = conn.execute("SELECT name FROM users WHERE id=?", (admin_id,)).fetchone()
    assert viewer_row["is_admin"] == 0
    assert admin_row["name"] != "Leitor renomeado"
    assert viewer.get("/api/companies/1/users").status_code == 403


def test_profile_mutation_requires_csrf(client):
    me = _me(client)
    client.headers.pop("x-csrf-token")

    response = client.patch(
        "/api/me", json={"name": "Sem CSRF", "email": ADMIN_EMAIL, "expected_version": me["version"]}
    )

    assert response.status_code == 403
