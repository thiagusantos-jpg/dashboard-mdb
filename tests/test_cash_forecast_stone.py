"""O dinheiro que a Stone ainda vai depositar entra na projeção de caixa.

A projeção soma o BRUTO de cada recebível, não o líquido: a importação do XML já cria
um lançamento aberto para a taxa, que a projeção conta como saída no mesmo dia. Como
líquido = bruto − taxa, somar o líquido descontaria a taxa duas vezes.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from backend import database as db
from backend.finance import accounts, forecast, ledger
from backend.finance.entries import EntryCommand, create_entry


TODAY = date(2026, 9, 15)


@pytest.fixture
def finance_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "forecast-stone.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    ledger.create_account(1, "Stone", "payment")


def _stone_account_id() -> int:
    with db.connection() as conn:
        return int(conn.execute("SELECT id FROM cash_accounts WHERE company=1").fetchone()["id"])


def _receivable(settlement: str, gross_cents: int, fee_cents: int, *, key: str):
    """Mirrors sync_receivables: the receivable row plus the OPEN fee entry it creates."""
    fee_entry_id = None
    if fee_cents:
        fee_account = accounts.account_by_key(1, "acquiring_fees")
        entry = create_entry(EntryCommand(
            company_id=1, account_id=fee_account["id"], amount_cents=fee_cents,
            competence=settlement[:7], due_date=date.fromisoformat(settlement),
            source="stone_receivable", external_id=key,
            description=f"Taxa de adquirente — {key}",
        ))
        fee_entry_id = entry["id"]
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO stone_receivables(
                id,company,cash_account_id,transaction_key,installment_number,brand_id,
                gross_cents,fee_cents,net_cents,settlement_date,fee_entry_id,created_at
            ) VALUES(?,1,?,?,1,'visa',?,?,?,?,?,?)
            """,
            (
                secrets.randbits(63) or 1, _stone_account_id(), key, gross_cents, fee_cents,
                gross_cents - fee_cents, settlement, fee_entry_id, db.now(),
            ),
        )


def _day(projection, day: date) -> dict:
    return next(d for d in projection["days"] if d["date"] == day.isoformat())


def _stone_items(projection) -> list:
    return [
        item
        for day in projection["days"]
        for item in day["items"]
        if item["source"] == "stone_receivables"
    ]


def test_future_settlement_enters_the_forecast_as_gross(finance_db):
    settlement = TODAY + timedelta(days=3)
    _receivable(settlement.isoformat(), 100_000, 2_000, key="tx-1")

    projection = forecast.forecast(1, TODAY, TODAY + timedelta(days=7), as_of=TODAY)

    day = _day(projection, settlement)
    amounts = sorted(item["amount_cents"] for item in day["items"])
    # A entrada bruta e a saída da taxa, que juntas dão o líquido de 980,00.
    assert amounts == [-2_000, 100_000]
    assert sum(amounts) == 98_000
    assert day["balance_cents"] == 98_000

    stone = _stone_items(projection)
    assert len(stone) == 1
    assert stone[0]["description"] == "Recebíveis Stone (1 venda)"
    assert stone[0]["confidence"] == "forecast"


def test_settlements_up_to_today_are_not_counted_again(finance_db):
    # Já caíram na conta pela importação do extrato: contá-las aqui dobraria o dinheiro.
    _receivable((TODAY - timedelta(days=3)).isoformat(), 50_000, 1_000, key="tx-old")
    _receivable(TODAY.isoformat(), 70_000, 1_500, key="tx-today")

    projection = forecast.forecast(1, TODAY, TODAY + timedelta(days=7), as_of=TODAY)

    assert _stone_items(projection) == []


def test_several_sales_on_the_same_day_become_one_line(finance_db):
    settlement = TODAY + timedelta(days=2)
    _receivable(settlement.isoformat(), 30_000, 600, key="tx-a")
    _receivable(settlement.isoformat(), 20_000, 400, key="tx-b")

    projection = forecast.forecast(1, TODAY, TODAY + timedelta(days=5), as_of=TODAY)

    stone = _stone_items(projection)
    assert len(stone) == 1
    assert stone[0]["amount_cents"] == 50_000
    assert stone[0]["description"] == "Recebíveis Stone (2 vendas)"


def test_forecast_without_receivables_has_no_stone_line(finance_db):
    projection = forecast.forecast(1, TODAY, TODAY + timedelta(days=7), as_of=TODAY)

    assert _stone_items(projection) == []
    assert len(projection["days"]) == 8
