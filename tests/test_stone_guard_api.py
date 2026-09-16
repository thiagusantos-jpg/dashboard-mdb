from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts


FIXTURES = Path(__file__).parent / "integrations" / "fixtures"
BASE = "/api/companies/1/finance"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "stone-guard.sqlite3")
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


def import_report(client):
    stone = client.post(f"{BASE}/cash-accounts", json={"name": "Conta Stone", "kind": "payment"}).json()
    content = (FIXTURES / "stone-recebiveis-sample.csv").read_bytes()
    response = client.post(
        f"{BASE}/cash-accounts/{stone['id']}/receivables-import",
        files={"file": ("recebiveis.csv", content, "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def fee_entry(account_key, competence="2026-09", **extra):
    body = {
        "account_id": str(accounts.account_by_key(1, account_key)["id"]),
        "amount_cents": 100_00, "competence": competence, "due_date": f"{competence}-15",
        "description": "Taxa maquininha",
    }
    body.update(extra)
    return body


def test_without_a_stone_report_manual_fees_are_free(client):
    assert client.get(f"{BASE}/stone-coverage").json()["months"] == []
    assert client.post(f"{BASE}/entries", json=fee_entry("acquiring_fees")).status_code == 201


def test_a_manual_fee_in_a_covered_month_needs_confirmation(client):
    import_report(client)
    coverage = client.get(f"{BASE}/stone-coverage").json()
    assert coverage["months"] == ["2026-09"]
    assert sorted(coverage["accounts"].values()) == ["acquiring_fees", "payment_terminal_rent", "receivables_advance"]

    for key in ("acquiring_fees", "receivables_advance", "payment_terminal_rent"):
        refused = client.post(f"{BASE}/entries", json=fee_entry(key))
        assert refused.status_code == 422, key
        detail = refused.json()["detail"]
        assert detail["code"] == "stone_duplicate"
        assert detail["fields"] == ["confirm_not_stone_duplicate"]
        assert "set/2026" in detail["message"] and "contar em dobro" in detail["message"]

    confirmed = client.post(f"{BASE}/entries", json=fee_entry("acquiring_fees", confirm_not_stone_duplicate=True))
    assert confirmed.status_code == 201
    # Other months and other categories are untouched.
    assert client.post(f"{BASE}/entries", json=fee_entry("acquiring_fees", competence="2026-10")).status_code == 201
    assert client.post(f"{BASE}/entries", json=fee_entry("rent")).status_code == 201


def test_recurring_and_installment_fees_are_guarded_too(client):
    import_report(client)
    account_id = str(accounts.account_by_key(1, "payment_terminal_rent")["id"])
    recurring = {"account_id": account_id, "amount_cents": 90_00, "description": "Maquininha",
                 "start_competence": "2026-12", "due_day": 5}
    assert client.post(f"{BASE}/recurrences", json=recurring).json()["detail"]["code"] == "stone_duplicate"
    assert client.post(f"{BASE}/recurrences", json={**recurring, "confirm_not_stone_duplicate": True}).status_code == 201

    schedule = {"account_id": account_id, "description": "Maquininha nova", "total_cents": 300_00, "count": 3,
                "first_due": "2026-08-10", "competence_mode": "distributed", "confirmed": True}
    assert client.post(f"{BASE}/expense-schedules", json=schedule).json()["detail"]["code"] == "stone_duplicate"
    outside = {**schedule, "first_due": "2026-11-10"}
    assert client.post(f"{BASE}/expense-schedules", json=outside).status_code == 201


def test_the_fixed_expense_template_marks_the_terminal_fee_as_automatic(client):
    template = {row["system_key"]: row for row in client.get(f"{BASE}/fixed-expenses").json()}
    assert template["payment_terminal_rent"]["stone_automatic"] is False
    import_report(client)
    template = {row["system_key"]: row for row in client.get(f"{BASE}/fixed-expenses").json()}
    assert template["payment_terminal_rent"]["stone_automatic"] is True
    refused = client.post(f"{BASE}/fixed-expenses", json={
        "period": "2026-09", "items": [{"system_key": "payment_terminal_rent", "amount_cents": 90_00, "due_day": 5}],
    })
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "stone_duplicate"


def test_the_import_reports_what_was_booked_automatically(client):
    result = import_report(client)
    assert result["monthly_fees"] == 1 and result["monthly_fee_cents"] == 90_00
    assert result["fee_entries"] == 3
