from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.operations.goals import (
    compare_intervals,
    current_goal_progress,
    goal_progress,
    set_revenue_goal,
)


COMPANY = 1


@pytest.fixture
def goals_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "goals.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def receipt(id_, day, revenue_reais):
    return {
        "id": id_, "reference_id": 0, "aliases": [id_], "date": day, "status": "V", "species": "CF",
        "revenue": revenue_reais * 100,
        "items": [{"id": id_, "product_id": 1, "quantity": "1", "revenue": revenue_reais * 100,
                   "cost": revenue_reais * 50, "unit_cost": str(revenue_reais * 0.5), "status": "V"}],
    }


def test_daily_target_uses_remaining_open_days():
    result = goal_progress(100_000_00, 70_000_00, 10)
    assert result["required_per_day"] == 3_000_00


def test_fully_reached_goal_has_no_remaining_amount():
    result = goal_progress(50_000_00, 60_000_00, 5)
    assert result["remaining_cents"] == 0
    assert result["required_per_day"] == 0


def test_current_goal_progress_reads_achieved_revenue_from_sales(goals_db):
    set_revenue_goal(COMPANY, 100_000_00, "2026-01-01")
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 1, "receipts": [receipt(1, "2026-09-05", 700)],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-30",
    })

    progress = current_goal_progress(COMPANY, date(2026, 9, 10))

    assert progress["target_cents"] == 100_000_00
    assert progress["achieved_cents"] == 700_00
    assert progress["remaining_cents"] == 99_300_00


def test_no_goal_configured_returns_none(goals_db):
    assert current_goal_progress(COMPANY, date(2026, 9, 10)) is None


def test_compare_intervals_normalizes_by_operating_days(goals_db):
    db.put_dataset(COMPANY, "sales", "2026-09", {
        "raw_count": 1,
        "receipts": [receipt(1, "2026-09-01", 100), receipt(2, "2026-09-02", 100)],
        "analysis": [], "start": "2026-09-01", "end": "2026-09-07",
    })
    db.put_dataset(COMPANY, "sales", "2026-08", {
        "raw_count": 1, "receipts": [receipt(3, "2026-08-01", 100)],
        "analysis": [], "start": "2026-08-01", "end": "2026-08-07",
    })

    result = compare_intervals(
        COMPANY, date(2026, 9, 1), date(2026, 9, 2), date(2026, 8, 1), date(2026, 8, 1)
    )

    assert result["interval_1"]["revenue_cents"] == 200_00
    assert result["interval_1"]["operating_days"] == 2
    assert result["interval_2"]["revenue_cents"] == 100_00
    assert result["interval_2"]["operating_days"] == 1
    assert result["revenue_per_day_change_pct"] == 0.0
