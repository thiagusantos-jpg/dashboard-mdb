from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.operations.promotions import promotion_result


COMPANY = 1


@pytest.fixture
def promotions_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "promotions.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def receipt(id_, day, product_id, revenue_cents, quantity="1"):
    return {
        "id": id_, "reference_id": 0, "aliases": [id_], "date": day, "status": "V", "species": "CF",
        "revenue": revenue_cents,
        "items": [{"id": id_, "product_id": product_id, "quantity": quantity, "revenue": revenue_cents,
                   "cost": revenue_cents // 2, "unit_cost": "1", "status": "V"}],
    }


def test_promotion_result_reports_observed_variation_without_causal_claim(promotions_db):
    db.put_dataset(COMPANY, "sales", "2026-08", {
        "raw_count": 1, "receipts": [receipt(1, "2026-08-15", 77, 100_00)],
        "analysis": [], "start": "2026-08-01", "end": "2026-08-31",
    })
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 1, "receipts": [receipt(2, "2026-09-15", 77, 200_00)],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })

    result = promotion_result(
        COMPANY, 77, date(2026, 9, 15), date(2026, 9, 15), date(2026, 8, 15), date(2026, 8, 15)
    )

    assert result["promotion"]["revenue_cents"] == 200_00
    assert result["baseline"]["revenue_cents"] == 100_00
    assert result["observed_variation_pct"] == 100.0
    assert "causal" in result["note"]
