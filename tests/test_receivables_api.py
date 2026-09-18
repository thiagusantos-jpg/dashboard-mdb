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


def test_contracted_rate_feeds_the_fee_variance(client):
    saved = client.put(
        "/api/companies/1/finance/receivables/contracted-rate",
        json={"rate_pct": 1.5, "effective_from": "2026-01-01"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"rate_pct": 1.5, "effective_from": "2026-01-01"}

    report = client.get(
        "/api/companies/1/finance/receivables/effective-fee-report",
        params={"start": "2026-09-01", "end": "2026-12-31"},
    ).json()
    assert report["contracted_rate_pct"] == 1.5


def test_contracted_rate_rejects_out_of_range_values(client):
    response = client.put(
        "/api/companies/1/finance/receivables/contracted-rate", json={"rate_pct": 45},
    )
    assert response.status_code == 422


def test_reconciliation_sources_report_what_each_account_has_imported(client):
    stone = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    bank = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Banco", "kind": "bank"}
    ).json()
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()
    client.post(
        f"/api/companies/1/finance/cash-accounts/{stone['id']}/receivables-import",
        files={"file": ("stone-conciliation-sample.xml", content, "application/xml")},
    )

    sources = client.get("/api/companies/1/finance/reconciliation/sources")
    assert sources.status_code == 200, sources.text
    by_name = {item["name"]: item for item in sources.json()}
    assert by_name["Stone"]["stone"]["count"] == 3
    assert by_name["Stone"]["stone"]["last_import_at"]
    assert by_name["Stone"]["bank"]["count"] == 0
    assert by_name["Banco"]["stone"] == {"count": 0, "last_import_at": None, "last_settlement_date": None}
    assert str(by_name["Banco"]["cash_account_id"]) == str(bank["id"])


def test_stone_daily_check_compares_the_xml_with_bank_credits(client):
    stone = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()
    client.post(
        f"/api/companies/1/finance/cash-accounts/{stone['id']}/receivables-import",
        files={"file": ("stone-conciliation-sample.xml", content, "application/xml")},
    )
    expected = client.get(
        "/api/companies/1/finance/receivables/expected-settlements",
        params={"start": "2026-01-01", "end": "2026-12-31"},
    ).json()
    first = expected[0]
    client.post(
        "/api/companies/1/finance/cash-events",
        json={"cash_account_id": stone["id"], "amount_cents": first["net_cents"],
              "occurred_at": first["settlement_date"], "description": "Repasse Stone"},
    )

    response = client.get(
        "/api/companies/1/finance/reconciliation/stone-daily",
        params={"start": "2026-01-01", "end": "2026-12-31", "cash_account_id": stone["id"]},
    )
    assert response.status_code == 200, response.text
    days = {d["settlement_date"]: d for d in response.json()["days"]}
    assert days[first["settlement_date"]]["status"] == "ok"
    assert len(days) == len(expected)


def test_stone_daily_check_validates_the_period(client):
    backwards = client.get(
        "/api/companies/1/finance/reconciliation/stone-daily",
        params={"start": "2026-09-10", "end": "2026-09-01"},
    )
    assert backwards.status_code == 422
    default = client.get("/api/companies/1/finance/reconciliation/stone-daily")
    assert default.status_code == 200
    assert default.json()["days"] == []


def test_the_reserve_account_is_not_listed_as_a_source_to_import(client):
    client.post("/api/companies/1/finance/cash-accounts", json={"name": "Conta Stone", "kind": "payment"})
    client.post("/api/companies/1/finance/cash-accounts", json={"name": "Reserva Stone", "kind": "bank"})
    names = [item["name"] for item in client.get("/api/companies/1/finance/reconciliation/sources").json()]
    assert names == ["Conta Stone"]


def _report(sales: int, prefix: int) -> bytes:
    header = (FIXTURES / "stone-recebiveis-sample.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    rows = [
        f"00.000.000/0001-00;100000001;Venda;09/09/2026 12:00:00;10/09/2026;10/09/2026;Visa;Debito;"
        f"{prefix}{n:013d};1;1;10,000000;9,900000;-0,100000;0,000000;0,000000;Pago;10/09/2026 00:00:00;10,000000;0,000000"
        for n in range(sales)
    ]
    return ("﻿" + "\n".join([header] + rows)).encode("utf-8")


def test_a_long_sales_report_costs_the_same_few_queries_as_a_short_one(client, monkeypatch):
    """A quarter of Stone sales is ~7,000 rows; four queries per row outlived
    the hosting time limit against the hosted database."""
    from contextlib import contextmanager

    calls = {"n": 0}
    real_connection = db.connection

    class Counting:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def execute(self, *args):
            calls["n"] += 1
            return self._conn.execute(*args)

        def executemany(self, *args):
            calls["n"] += 1
            return self._conn.executemany(*args)

    @contextmanager
    def counting_connection(*args, **kwargs):
        with real_connection(*args, **kwargs) as conn:
            yield Counting(conn)

    def run(sales: int) -> tuple:
        account = client.post(
            "/api/companies/1/finance/cash-accounts", json={"name": f"Stone {sales}", "kind": "payment"}
        ).json()
        monkeypatch.setattr(db, "connection", counting_connection)
        calls["n"] = 0
        response = client.post(
            f"/api/companies/1/finance/cash-accounts/{account['id']}/receivables-import",
            files={"file": ("recebiveis.csv", _report(sales, prefix=sales), "text/csv")},
        )
        monkeypatch.setattr(db, "connection", real_connection)
        assert response.status_code == 201, response.text
        return calls["n"], response.json()

    run(3)  # creates the fee accounts, a one-off write
    short_calls, _ = run(5)
    long_calls, result = run(400)
    assert result["imported"] == 400
    assert long_calls == short_calls
