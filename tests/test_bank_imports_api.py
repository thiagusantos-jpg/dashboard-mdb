from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


FIXTURES = Path(__file__).parent / "integrations" / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "bank-imports-api.sqlite3")
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    with TestClient(api.app) as test_client:
        login = test_client.post(
            "/api/login",
            json={"email": "admin@loja.test", "password": "bootstrap-password"},
        )
        assert login.status_code == 200, login.text
        test_client.headers["x-csrf-token"] = login.json()["csrf"]
        yield test_client


def test_preview_then_commit_ofx_import(client):
    content = (FIXTURES / "stone-sample.ofx").read_bytes()

    preview = client.post(
        "/api/companies/1/finance/bank-imports/preview",
        files={"file": ("stone-sample.ofx", content, "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["count"] == 3

    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()

    commit = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("stone-sample.ofx", content, "application/octet-stream")},
    )
    assert commit.status_code == 201, commit.text
    assert commit.json()["imported"] == 3

    balance = client.get("/api/companies/1/finance/cash-balance").json()
    assert balance["balance_cents"] == 600_00 + 400_00 - 25_00
