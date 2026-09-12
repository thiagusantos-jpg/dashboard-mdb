from __future__ import annotations

import pytest

from backend import database as db
from backend import identity


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "identity.sqlite3")
    db.initialize()


def test_create_and_verify_user_with_scrypt(isolated_db):
    user = identity.create_user(
        email="GERENTE@LOJA.TEST",
        password="uma-senha-forte",
        name="Gerente da loja",
    )

    authenticated = identity.verify_credentials("gerente@loja.test", "uma-senha-forte")
    assert authenticated["id"] == user["id"]
    assert authenticated["email"] == "gerente@loja.test"
    assert "uma-senha-forte" not in authenticated["password_hash"]
    assert identity.verify_credentials("gerente@loja.test", "senha-incorreta") is None


def test_disabled_user_cannot_authenticate(isolated_db):
    user = identity.create_user("caixa@loja.test", "senha-segura", "Operador")
    identity.disable_user(user["id"])

    assert identity.verify_credentials("caixa@loja.test", "senha-segura") is None

