from __future__ import annotations

import pytest

from backend import database as db
from backend.finance.accounts import (
    AccountNature,
    archive_account,
    create_account,
    default_account,
    list_accounts,
    resolve_parameter,
    set_parameter,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "accounts.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def test_owner_compensation_and_profit_distribution_have_distinct_natures():
    assert default_account("owner_compensation").nature == "operating_expense"
    assert default_account("profit_distribution").nature == "profit_distribution"


def test_default_chart_covers_store_operating_costs(isolated_db):
    keys = {account["system_key"] for account in list_accounts(1)}

    assert {
        "electricity",
        "water",
        "rent",
        "salaries",
        "taxes",
        "acquiring_fees",
        "owner_compensation",
        "profit_distribution",
        "loan_interest",
        "loan_principal",
    } <= keys


def test_custom_account_can_be_archived(isolated_db):
    account = create_account(
        1, "Consultoria especial", AccountNature.OPERATING_EXPENSE
    )
    archive_account(1, account["id"], expected_version=1)

    assert all(row["id"] != account["id"] for row in list_accounts(1))
    archived = list_accounts(1, include_archived=True)
    assert next(row for row in archived if row["id"] == account["id"])["archived"]


def test_effective_parameters_prefer_product_over_category_store_and_global(isolated_db):
    set_parameter(1, "target_margin", 20, "2026-01-01")
    set_parameter(1, "target_margin", 25, "2026-01-01", store=10)
    set_parameter(1, "target_margin", 30, "2026-01-01", category=20)
    set_parameter(1, "target_margin", 35, "2026-01-01", product=30)

    assert resolve_parameter(
        1,
        "target_margin",
        "2026-09-12",
        store=10,
        category=20,
        product=30,
    ) == 35
