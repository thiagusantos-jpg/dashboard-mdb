from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import ledger, obligations, reserve
from backend.finance.reporting import management_result


COMPANY = 1


@pytest.fixture
def accounts_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "reserve-balance.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    stone = ledger.create_account(COMPANY, "Conta Stone", "payment")
    res = ledger.create_account(COMPANY, "Reserva Stone", "bank")
    # Imported period: 1.000 in, 1.900 out (more redeemed than saved).
    ledger.transfer(COMPANY, stone["id"], res["id"], 1_000_00, date(2026, 6, 3))
    ledger.transfer(COMPANY, res["id"], stone["id"], 1_900_00, date(2026, 8, 20))
    return stone, res


def test_first_update_suggests_and_books_an_opening_balance_before_the_statements(accounts_pair):
    _, res = accounts_pair
    status = reserve.balance_status(COMPANY, res["id"], date(2026, 9, 1))
    assert status["panel_balance_cents"] == -900_00
    assert status["suggest_opening"] is True

    result = reserve.update_balance(COMPANY, res["id"], 3_100_00, date(2026, 9, 1), opening=True)
    assert result["booked"] == "opening"
    assert result["difference_cents"] == 4_000_00
    assert result["date"] == "2026-06-02"
    assert ledger.account_balance(res["id"]) == 3_100_00
    # An opening balance is not income.
    assert management_result(COMPANY, "2026-09")["financial_income_cents"] == 0
    assert reserve.balance_status(COMPANY, res["id"], date(2026, 9, 1))["suggest_opening"] is False


def test_later_gains_are_investment_income_in_the_management_result(accounts_pair):
    _, res = accounts_pair
    reserve.update_balance(COMPANY, res["id"], 3_100_00, date(2026, 9, 1), opening=True)

    result = reserve.update_balance(COMPANY, res["id"], 3_127_45, date(2026, 9, 16), opening=False)
    assert result["booked"] == "income"
    assert result["difference_cents"] == 27_45
    assert ledger.account_balance(res["id"]) == 3_127_45

    report = management_result(COMPANY, "2026-09")
    assert report["financial_income_cents"] == 27_45
    line = next(l for l in report["accounts"] if l["system_key"] == "investment_income")
    assert line["actual_cents"] == 27_45
    # Never shows up as a bill to pay.
    assert not any("Reserva" in str(row.get("description", "")) for row in obligations._entry_rows(COMPANY))


def test_a_drop_is_booked_as_an_investment_cost(accounts_pair):
    _, res = accounts_pair
    reserve.update_balance(COMPANY, res["id"], 3_100_00, date(2026, 9, 1), opening=True)
    result = reserve.update_balance(COMPANY, res["id"], 3_095_00, date(2026, 9, 16), opening=False)
    assert result["booked"] == "expense"
    assert management_result(COMPANY, "2026-09")["financial_expenses_cents"] == 5_00
    assert ledger.account_balance(res["id"]) == 3_095_00


def test_same_balance_books_nothing(accounts_pair):
    _, res = accounts_pair
    result = reserve.update_balance(COMPANY, res["id"], 0, date(2026, 6, 2), opening=False)
    assert result["booked"] == "none"


def test_guards(accounts_pair):
    _, res = accounts_pair
    reserve.update_balance(COMPANY, res["id"], 3_100_00, date(2026, 9, 10), opening=True)
    with pytest.raises(ValueError, match="saldo inicial"):
        reserve.update_balance(COMPANY, res["id"], 3_200_00, date(2026, 9, 12), opening=True)
    with pytest.raises(ValueError, match="Já existe atualização"):
        reserve.update_balance(COMPANY, res["id"], 3_200_00, date(2026, 9, 1), opening=False)
    with pytest.raises(ValueError, match="negativo"):
        reserve.update_balance(COMPANY, res["id"], -1, date(2026, 9, 12), opening=False)
    with pytest.raises(ValueError, match="data passada"):
        reserve.update_balance(COMPANY, res["id"], 1, date(2999, 1, 1), opening=False)
