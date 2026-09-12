from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security


FIXTURES = Path(__file__).parent / "integrations" / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "receivables-api.sqlite3")
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


def test_import_receivables_then_query_expected_settlements(client):
    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()

    imported = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/receivables-import",
        files={"file": ("stone-conciliation-sample.xml", content, "application/xml")},
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()["imported"] == 3

    settlements = client.get(
        "/api/companies/1/finance/receivables/expected-settlements",
        params={"start": "2026-09-01", "end": "2026-12-31"},
    )
    assert settlements.status_code == 200
    assert len(settlements.json()) == 2

    report = client.get(
        "/api/companies/1/finance/receivables/effective-fee-report",
        params={"start": "2026-09-01", "end": "2026-12-31"},
    )
    assert report.status_code == 200
    assert report.json()["gross_cents"] == 1_000_00 + 300_00 + 300_00
