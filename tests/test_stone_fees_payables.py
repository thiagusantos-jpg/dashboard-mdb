"""Taxa de adquirente não é conta a pagar.

A taxa da Stone já foi descontada na liquidação: o que cai na conta é o líquido. Ela é
uma despesa de verdade — tem de aparecer no resultado e no break-even — mas nunca haverá
um pagamento a fazer, então não pode ficar na fila de Contas a pagar nem, pior, vencida.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import obligations, reporting


FIXTURES = Path(__file__).parent / "integrations" / "fixtures"
TODAY = date(2026, 9, 15)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "stone-fees.sqlite3")
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


def _import_receivables(client):
    """O fixture traz 3 recebíveis com taxa: 25,00 e 9,00 liquidando em 12/09/2026, e
    9,00 em 12/10/2026."""
    cash_account = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Stone", "kind": "payment"}
    ).json()
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()
    response = client.post(
        f"/api/companies/1/finance/cash-accounts/{cash_account['id']}/receivables-import",
        files={"file": ("stone-conciliation-sample.xml", content, "application/xml")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _fee_entries():
    with db.connection() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id,description,status,amount_cents,due_date FROM financial_entries"
            " WHERE company=1 AND source='stone_receivable' ORDER BY due_date,id"
        )]


def test_the_import_creates_one_fee_expense_per_receivable(client):
    _import_receivables(client)

    fees = _fee_entries()
    assert len(fees) == 3
    assert sum(fee["amount_cents"] for fee in fees) == 25_00 + 9_00 + 9_00


def test_imported_fees_never_show_up_as_bills_to_pay(client):
    _import_receivables(client)

    listing = obligations.list_obligations(1, include_sensitive=True, limit=50)
    fee_bills = [item for item in listing["items"] if "Taxa de adquirente" in item["description"]]

    assert fee_bills == [], "a taxa já saiu na liquidação: não há o que pagar"


def test_imported_fees_do_not_land_in_the_urgency_buckets(client):
    _import_receivables(client)

    summary = obligations.obligation_summary(1, today=TODAY, include_sensitive=True)

    # Duas taxas venceram em 12/09 e uma vence em 12/10: sem a correção, elas apareceriam
    # como vencidas e como contas do mês, inflando o que o sócio "precisa pagar".
    assert summary["buckets"]["overdue"]["count"] == 0
    assert summary["buckets"]["month"]["count"] == 0
    assert summary["count"] == 0
    assert summary["open_cents"] == 0


def test_the_fee_is_still_an_expense_in_the_result(client):
    """O contrapeso do teste acima: tirar a taxa da fila de pagamento não pode tirá-la do
    resultado. Ela é custo de vender no cartão e sustenta o break-even."""
    _import_receivables(client)

    result = reporting.management_result(1, "2026-09", include_sensitive=True)
    line = next(item for item in result["accounts"] if item["system_key"] == "acquiring_fees")

    # 25,00 (NSU-0001) + 9,00 (NSU-0002 parcela 1) liquidam em setembro.
    assert line["actual_cents"] == 25_00 + 9_00
    assert line["nature"] == "operating_expense"
    assert result["operating_expenses_cents"] >= 25_00 + 9_00

    # A parcela 2 liquida em outubro e pertence à competência de outubro.
    october = reporting.management_result(1, "2026-10", include_sensitive=True)
    assert next(i for i in october["accounts"] if i["system_key"] == "acquiring_fees")["actual_cents"] == 9_00


def test_the_fee_stays_open_because_it_is_a_real_future_outflow(client):
    """A correção é de visibilidade, não de estado: o lançamento continua aberto de
    propósito, porque a projeção de caixa o conta como saída no dia da liquidação — é o
    que faz `bruto − taxa = líquido` fechar na projeção dos próximos 30 dias."""
    _import_receivables(client)

    assert {fee["status"] for fee in _fee_entries()} == {"open"}


def test_a_fee_cannot_be_reached_or_paid_by_id(client):
    """Esconder da lista não bastaria: pela URL direta ainda dava para abrir a taxa e
    pagá-la, lançando uma saída de caixa de um dinheiro que a Stone já tinha descontado."""
    _import_receivables(client)
    fee = _fee_entries()[0]
    fee_id = fee["id"]

    assert obligations.get_obligation(1, "entry", fee_id, include_sensitive=True) is None

    # Requisição válida em tudo o mais (conta de caixa e valor exato da taxa), para que
    # a recusa prove a guarda e não um erro de preenchimento: com um valor qualquer o
    # 422 vinha de "excede o saldo" e escondia que o pagamento, correto, passava.
    bank = client.post(
        "/api/companies/1/finance/cash-accounts", json={"name": "Banco", "kind": "bank"}
    ).json()
    response = client.post(
        f"/api/companies/1/finance/obligations/entry/{fee_id}/payments",
        json={"amount_cents": fee["amount_cents"], "paid_at": "2026-09-12", "expected_version": 1,
              "cash_account_id": bank["id"]},
        headers={"Idempotency-Key": "taxa-1"},
    )
    assert response.status_code == 422, response.text
    assert "já foi descontada pela Stone" in response.json()["detail"]["message"]
