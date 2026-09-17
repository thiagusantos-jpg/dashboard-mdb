from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts, ledger
from backend.finance.entries import EntryCommand, create_entry


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

    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()

    preview = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports/preview",
        files={"file": ("stone-sample.ofx", content, "application/octet-stream")},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["count"] == 3
    assert {item["decision"] for item in preview_body["items"]} == {"new"}

    commit = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("stone-sample.ofx", content, "application/octet-stream")},
        data={
            "preview_hash": preview_body["preview_hash"],
            "decisions": json.dumps({item["external_id"]: item["decision"] for item in preview_body["items"]}),
        },
    )
    assert commit.status_code == 201, commit.text
    assert commit.json()["imported"] == 3

    balance = client.get("/api/companies/1/finance/cash-balance").json()
    assert balance["balance_cents"] == 600_00 + 400_00 - 25_00


def test_importing_a_payments_bank_line_offers_a_link_suggestion(client):
    account = accounts.account_by_key(1, "rent")
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    cash_account = ledger.create_account(1, "Banco X", "bank")

    payment = client.post(
        f"/api/companies/1/finance/obligations/entry/{entry['id']}/payments",
        headers={"Idempotency-Key": "pay-1"},
        json={
            "amount_cents": 40_000, "paid_at": "2026-09-12", "expected_version": 1,
            "cash_account_id": cash_account["id"],
        },
    )
    assert payment.status_code == 200, payment.text
    cash_event_id = payment.json()["cash_event_id"]

    content = (
        "Data,Descricao,Valor,FITID\n2026-09-12,Pagamento fornecedor,-400.00,BANK-0001\n"
    ).encode("utf-8")
    preview = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports/preview",
        files={"file": ("extrato.csv", content, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    item = preview.json()["items"][0]
    assert item["candidate_cash_event_ids"] == [str(cash_event_id)]
    assert item["decision"] == f"link:{cash_event_id}"

    commit = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("extrato.csv", content, "text/csv")},
        data={
            "preview_hash": preview.json()["preview_hash"],
            "decisions": json.dumps({item["external_id"]: item["decision"]}),
        },
    )
    assert commit.status_code == 201, commit.text
    assert commit.json()["linked"] == 1
    assert commit.json()["imported"] == 0

    balance = client.get("/api/companies/1/finance/cash-balance").json()
    assert balance["balance_cents"] == -40_000


def test_commit_errors_use_the_code_message_fields_contract(client):
    """Task B5 fix round 1, Finding 5: every new 422/409 raised by this
    route must carry {code,message,fields}, not a bare string."""
    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    content = (
        "Data,Descricao,Valor,FITID\n2026-09-12,Pagamento fornecedor,-40.00,BANK-0001\n"
    ).encode("utf-8")
    preview = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports/preview",
        files={"file": ("extrato.csv", content, "text/csv")},
    ).json()

    # Malformed `decisions` JSON -> 422 {code,message,fields}.
    bad_decisions = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("extrato.csv", content, "text/csv")},
        data={"preview_hash": preview["preview_hash"], "decisions": "not-json"},
    )
    assert bad_decisions.status_code == 422, bad_decisions.text
    body = bad_decisions.json()["detail"]
    assert body["code"] == "invalid_fields"
    assert body["fields"] == ["decisions"]

    # Stale preview_hash -> 409 {code,message,fields}.
    stale = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports",
        files={"file": ("extrato.csv", content, "text/csv")},
        data={"preview_hash": "not-the-real-hash", "decisions": "{}"},
    )
    assert stale.status_code == 409, stale.text
    stale_body = stale.json()["detail"]
    assert stale_body["code"] == "conflict"
    assert isinstance(stale_body["message"], str) and stale_body["message"]


def _statement(lines: int) -> bytes:
    rows = []
    for n in range(lines):
        memo = "Dinheiro Guardado - Reserva Stone" if n % 10 == 0 else f"Venda {n}"
        rows.append(
            f"<STMTTRN><DTPOSTED>202606{n % 28 + 1:02d}120000<TRNAMT>-{n + 1}.00"
            f"<FITID>BIG-{n}<MEMO>{memo}</STMTTRN>"
        )
    return ("<OFX><BANKTRANLIST>" + "".join(rows) + "</BANKTRANLIST></OFX>").encode()


def test_a_long_statement_costs_the_same_few_queries_as_a_short_one(client, monkeypatch):
    """A 3-month Stone statement has ~3,000 lines; one query per line
    outlived the hosting time limit against the hosted database."""
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

    from contextlib import contextmanager

    @contextmanager
    def counting_connection(*args, **kwargs):
        with real_connection(*args, **kwargs) as conn:
            yield Counting(conn)

    def run(lines: int) -> tuple:
        cash_account = client.post(
            "/api/companies/1/finance/cash-accounts", json={"name": f"Stone {lines}", "kind": "payment"}
        ).json()
        base = f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/bank-imports"
        content = _statement(lines)
        monkeypatch.setattr(db, "connection", counting_connection)
        calls["n"] = 0
        preview = client.post(f"{base}/preview", files={"file": ("big.ofx", content)})
        preview_calls = calls["n"]
        calls["n"] = 0
        body = preview.json()
        commit = client.post(base, files={"file": ("big.ofx", content)}, data={
            "preview_hash": body["preview_hash"],
            "decisions": json.dumps({i["external_id"]: i["decision"] for i in body["items"]}),
        })
        monkeypatch.setattr(db, "connection", real_connection)
        assert commit.status_code == 201, commit.text
        return preview_calls, calls["n"], commit.json()

    run(10)  # creates the Reserva Stone account, a one-off write
    short_preview, short_commit, _ = run(20)
    long_preview, long_commit, result = run(500)
    assert result["imported"] + result["transferred"] + result["duplicates"] == 500
    assert result["transferred"] == 50
    assert long_preview == short_preview
    assert long_commit == short_commit
