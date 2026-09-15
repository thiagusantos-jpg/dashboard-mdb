"""Contas a pagar por urgência: vencidas, hoje, 7 e 30 dias; despesas previstas para
confirmar; e se o saldo de caixa cobre o que vence na semana."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts, ledger, obligations
from backend.finance.entries import EntryCommand, create_entry, settle_entry


TODAY = date(2026, 9, 15)


@pytest.fixture
def finance_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "obligations-summary.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "obligations-summary-api.sqlite3")
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


def _entry(description, due, cents, key="rent", forecast=False):
    account = accounts.account_by_key(1, key)
    return create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=cents, competence=due[:7],
        due_date=date.fromisoformat(due), source="manual", external_id=None,
        description=description, forecast=forecast,
    ))


def test_summary_splits_what_is_owed_by_urgency(finance_db):
    _entry("Aluguel", "2026-09-10", 800_000)
    water = _entry("Água", "2026-09-12", 20_000, key="water")
    settle_entry(water["id"], 5_000, paid_at=date(2026, 9, 12))
    _entry("Energia", "2026-09-15", 50_000, key="electricity")
    _entry("Internet", "2026-09-22", 12_000, key="internet")
    _entry("Contador", "2026-10-15", 90_000, key="accounting")
    _entry("IPTU", "2026-12-01", 30_000, key="iptu")

    summary = obligations.obligation_summary(1, today=TODAY, include_sensitive=True)
    buckets = summary["buckets"]
    assert summary["today"] == "2026-09-15"
    assert (buckets["overdue"]["count"], buckets["overdue"]["cents"]) == (2, 815_000)
    assert (buckets["today"]["count"], buckets["today"]["cents"]) == (1, 50_000)
    assert (buckets["week"]["count"], buckets["week"]["cents"]) == (1, 12_000)
    assert (buckets["month"]["count"], buckets["month"]["cents"]) == (1, 90_000)
    assert (buckets["later"]["count"], buckets["later"]["cents"]) == (1, 30_000)
    assert (summary["count"], summary["open_cents"]) == (6, 997_000)


def test_summary_lists_forecasts_to_confirm_in_the_next_30_days(finance_db):
    _entry("Aluguel previsto", "2026-09-10", 800_000, forecast=True)
    _entry("Salários previstos", "2026-10-05", 500_000, key="salaries", forecast=True)
    _entry("Aluguel de dezembro", "2026-12-10", 800_000, forecast=True)

    restricted = obligations.obligation_summary(1, today=TODAY, include_sensitive=False)
    assert [f["description"] for f in restricted["forecasts"]] == ["Aluguel previsto"]
    assert restricted["forecast_cents"] == 800_000
    assert {"id", "version", "due_date", "competence", "amount_cents"} <= set(restricted["forecasts"][0])
    assert restricted["count"] == 0, "a forecast is not a payable until it is confirmed"

    full = obligations.obligation_summary(1, today=TODAY, include_sensitive=True)
    assert [f["description"] for f in full["forecasts"]] == ["Aluguel previsto", "Salários previstos"]


def test_summary_says_whether_cash_covers_what_is_due_in_7_days(finance_db):
    _entry("Aluguel", "2026-09-10", 800_000)
    _entry("Internet", "2026-09-22", 12_000, key="internet")
    _entry("Contador", "2026-10-15", 90_000, key="accounting")
    without_accounts = obligations.obligation_summary(1, today=TODAY, include_sensitive=True)
    assert without_accounts["cash_balance_cents"] is None
    assert without_accounts["coverage"] is None

    account = ledger.create_account(1, "Conta corrente", "bank")
    ledger.post_cash_event(1, account["id"], 300_000, date(2026, 9, 1), "Saldo inicial")
    short = obligations.obligation_summary(1, today=TODAY, include_sensitive=True)
    assert short["cash_balance_cents"] == 300_000
    assert short["coverage"] == {"due_cents": 812_000, "shortfall_cents": 512_000}

    ledger.post_cash_event(1, account["id"], 600_000, date(2026, 9, 14), "Depósito")
    covered = obligations.obligation_summary(1, today=TODAY, include_sensitive=True)
    assert covered["coverage"] == {"due_cents": 812_000, "shortfall_cents": 0}


def test_summary_endpoint(client):
    _entry("Aluguel", "2026-09-10", 800_000)
    response = client.get("/api/companies/1/finance/obligations/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert set(body["buckets"]) == {"overdue", "today", "week", "month", "later"}
