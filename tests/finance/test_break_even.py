"""Break-even point from the cost center (replaces the legacy manual fixed
cost): each expense category is fixed or variable, and
break-even = fixed costs ÷ contribution margin, where
contribution = revenue − COGS − variable costs of the competence."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import api, database as db, security
from backend.finance import accounts
from backend.finance.accounts import list_accounts
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.reporting import management_result


COMPANY = 1


@pytest.fixture
def be_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "break-even.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 1,
        "receipts": [{"id": 1, "reference_id": 0, "aliases": [1], "date": "2026-09-02", "status": "V", "species": "CF",
                      "revenue": 100_000, "items": [{"id": 1, "product_id": 10, "quantity": "1", "revenue": 100_000,
                                                     "cost": 60_000, "unit_cost": "600", "status": "V"}]}],
        "analysis": [],
    }, documents=1)
    return {row["system_key"]: row for row in list_accounts(COMPANY)}


def add(account, amount, competence="2026-09"):
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=amount, competence=competence,
        due_date=date(int(competence[:4]), int(competence[5:]), 20), source="manual", external_id=None,
        description=account["name"],
    ))


def test_default_classification_of_categories(be_db):
    assert be_db["rent"]["cost_behavior"] == "fixed"
    assert be_db["salaries"]["cost_behavior"] == "fixed"
    assert be_db["owner_compensation"]["cost_behavior"] == "fixed"
    assert be_db["taxes"]["cost_behavior"] == "variable"
    assert be_db["commissions"]["cost_behavior"] == "variable"
    assert be_db["acquiring_fees"]["cost_behavior"] == "variable"
    assert be_db["packaging"]["cost_behavior"] == "variable"
    assert be_db["cogs"]["cost_behavior"] is None
    assert be_db["profit_distribution"]["cost_behavior"] is None


def test_break_even_uses_fixed_costs_and_contribution_margin(be_db):
    add(be_db["rent"], 20_000)
    add(be_db["commissions"], 10_000)

    be = management_result(COMPANY, "2026-09")["break_even"]

    assert be["fixed_costs_cents"] == 20_000
    assert be["variable_costs_cents"] == 10_000
    assert be["contribution_margin_cents"] == 30_000
    assert be["contribution_margin_pct"] == 30.0
    assert be["break_even_cents"] == 66_667
    assert be["gap_pct"] == 50.0
    assert be["reason"] == ""


def test_reclassifying_a_category_changes_the_break_even(be_db):
    add(be_db["rent"], 20_000)
    add(be_db["commissions"], 10_000)
    commissions = be_db["commissions"]

    updated = accounts.update_account(COMPANY, commissions["id"], expected_version=commissions["version"], cost_behavior="fixed")
    be = management_result(COMPANY, "2026-09")["break_even"]

    assert updated["cost_behavior"] == "fixed"
    assert be["fixed_costs_cents"] == 30_000
    assert be["variable_costs_cents"] == 0
    assert be["break_even_cents"] == 75_000  # 30.000 ÷ 40%


def test_without_fixed_costs_the_break_even_is_unavailable_with_a_reason(be_db):
    add(be_db["commissions"], 10_000)

    be = management_result(COMPANY, "2026-09")["break_even"]

    assert be["break_even_cents"] is None
    assert "custos fixos" in be["reason"]


def test_without_sales_the_costs_are_known_but_the_break_even_is_not(be_db):
    add(be_db["rent"], 20_000, competence="2026-10")

    be = management_result(COMPANY, "2026-10")["break_even"]

    assert be["fixed_costs_cents"] == 20_000
    assert be["break_even_cents"] is None
    assert be["contribution_margin_pct"] is None
    assert "Vendas" in be["reason"]


def test_negative_contribution_margin_has_no_break_even(be_db):
    add(be_db["rent"], 20_000)
    add(be_db["commissions"], 50_000)

    be = management_result(COMPANY, "2026-09")["break_even"]

    assert be["break_even_cents"] is None
    assert "não cobre" in be["reason"]


def test_restricted_profiles_do_not_get_a_partial_break_even(be_db):
    add(be_db["rent"], 20_000)
    add(be_db["owner_compensation"], 30_000)  # sensitive account

    be = management_result(COMPANY, "2026-09", include_sensitive=False)["break_even"]

    assert be["fixed_costs_cents"] is None
    assert be["break_even_cents"] is None
    assert "restrit" in be["reason"]


def test_invalid_cost_behavior_is_rejected(be_db):
    rent = be_db["rent"]
    with pytest.raises(ValueError):
        accounts.update_account(COMPANY, rent["id"], expected_version=rent["version"], cost_behavior="semi")


def test_api_changes_the_cost_behavior_of_a_category(be_db, monkeypatch):
    monkeypatch.setattr(security, "access_password", lambda: "bootstrap-password")
    monkeypatch.setenv("MDB_DISABLE_WORKER", "1")
    security._attempts.clear()
    rent = be_db["rent"]
    with TestClient(api.app) as client:
        login = client.post("/api/login", json={"email": "admin@loja.test", "password": "bootstrap-password"})
        client.headers["x-csrf-token"] = login.json()["csrf"]
        response = client.patch(f"/api/companies/1/finance/accounts/{rent['id']}",
                                json={"expected_version": rent["version"], "cost_behavior": "variable"})
        invalid = client.patch(f"/api/companies/1/finance/accounts/{rent['id']}",
                               json={"expected_version": response.json()["version"], "cost_behavior": "semi"})

    assert response.status_code == 200, response.text
    assert response.json()["cost_behavior"] == "variable"
    assert invalid.status_code == 422
