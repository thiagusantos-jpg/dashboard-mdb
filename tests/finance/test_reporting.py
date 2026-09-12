from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance.accounts import list_accounts, set_parameter
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.reporting import management_result


COMPANY = 1


@pytest.fixture
def report_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "reporting.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    db.put_dataset(
        COMPANY,
        "sales",
        "2026-09",
        {
            "raw_count": 1,
            "receipts": [
                {
                    "id": 1,
                    "reference_id": 0,
                    "aliases": [1],
                    "date": "2026-09-02",
                    "status": "V",
                    "species": "CF",
                    "revenue": 100_000,
                    "items": [
                        {
                            "id": 1,
                            "product_id": 10,
                            "quantity": "1",
                            "revenue": 100_000,
                            "cost": 60_000,
                            "unit_cost": "600",
                            "status": "V",
                        }
                    ],
                }
            ],
            "analysis": [],
        },
        documents=1,
    )
    return {row["system_key"]: row for row in list_accounts(COMPANY)}


def add(account, amount, description):
    create_entry(
        EntryCommand(
            company_id=COMPANY,
            account_id=account["id"],
            amount_cents=amount,
            competence="2026-09",
            due_date=date(2026, 9, 20),
            source="manual",
            external_id=None,
            description=description,
        )
    )


def test_inventory_payment_does_not_duplicate_mobne_cogs(report_db):
    add(report_db["cogs"], 30_000, "Compra de mercadoria")
    add(report_db["electricity"], 10_000, "Energia")
    add(report_db["owner_compensation"], 50_000, "Pró-labore")
    add(report_db["profit_distribution"], 80_000, "Distribuição")

    result = management_result(COMPANY, "2026-09")

    assert result["revenue_cents"] == 100_000
    assert result["cogs_cents"] == 60_000
    assert result["owner_compensation_cents"] == 50_000
    assert result["profit_distribution_cents"] == 80_000
    assert result["operating_expenses_cents"] == 10_000
    assert result["managerial_result_cents"] == -20_000


def test_report_shows_budget_actual_variance_and_source(report_db):
    add(report_db["electricity"], 10_000, "Energia")
    set_parameter(
        COMPANY,
        "budget:electricity",
        12_000,
        "2026-09-01",
    )

    result = management_result(COMPANY, "2026-09")
    electricity = next(
        line for line in result["accounts"] if line["system_key"] == "electricity"
    )

    assert electricity["budget_cents"] == 12_000
    assert electricity["actual_cents"] == 10_000
    assert electricity["variance_cents"] == -2_000
    assert electricity["source"] == "manual"

