from __future__ import annotations

from datetime import date

import pytest

from backend import actions, database as db
from backend.operations.action_results import price_action_result


COMPANY = 1


@pytest.fixture
def results_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "results.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def _receipt(doc, day, product, revenue, cost):
    return {"id": doc, "reference_id": 0, "aliases": [doc], "date": day, "status": "V", "species": "CF",
            "revenue": revenue,
            "items": [{"id": 1, "product_id": product, "quantity": "1", "revenue": revenue, "cost": cost,
                       "unit_cost": str(cost / 100), "status": "V"}]}


def _month(period, receipts):
    db.put_dataset(COMPANY, "sales", period, {
        "raw_count": len(receipts), "receipts": receipts, "analysis": [],
        "start": f"{period}-01", "end": f"{period}-28",
    })


def _price_action(created_at, resolved_at=None, key="preco:77"):
    action = actions.create_from_alert(COMPANY, key, "259", "Reajustar preço")
    with db.connection() as conn:
        if resolved_at:
            conn.execute("UPDATE actions SET status='resolved',created_at=?,resolved_at=? WHERE id=?",
                         (created_at, resolved_at, action["id"]))
        else:
            conn.execute("UPDATE actions SET created_at=? WHERE id=?", (created_at, action["id"]))
        return dict(conn.execute("SELECT * FROM actions WHERE id=?", (action["id"],)).fetchone())


def _seed():
    # Before (07-17..08-15): two sales at a 20% margin. After (08-21..09-19): two at 33,33%.
    _month("2026-07", [_receipt(1, "2026-07-20", 77, 1000, 800)])
    _month("2026-08", [_receipt(2, "2026-08-10", 77, 1000, 800), _receipt(3, "2026-08-25", 77, 1200, 800),
                       _receipt(4, "2026-08-26", 99, 5000, 1000)])
    _month("2026-09", [_receipt(5, "2026-09-05", 77, 1200, 800)])


def test_measured_after_thirty_days(results_db):
    _seed()
    action = _price_action("2026-08-16T15:00:00+00:00", "2026-08-20T15:00:00+00:00")
    with db.connection() as conn:
        result = price_action_result(COMPANY, action, date(2026, 10, 1), conn)
    assert result["kind"] == "measured"
    assert result["days"] == 30
    assert (result["before"]["start"], result["before"]["end"]) == ("2026-07-17", "2026-08-15")
    assert (result["after"]["start"], result["after"]["end"]) == ("2026-08-21", "2026-09-19")
    assert (result["before"]["revenue"], result["before"]["margin"]) == (2000, 20.0)
    assert (result["after"]["revenue"], result["after"]["margin"]) == (2400, 33.33)


def test_partial_while_the_thirty_days_have_not_passed(results_db):
    _seed()
    action = _price_action("2026-08-16T15:00:00+00:00", "2026-08-20T15:00:00+00:00")
    with db.connection() as conn:
        partial = price_action_result(COMPANY, action, date(2026, 8, 26), conn)
        same_day = price_action_result(COMPANY, action, date(2026, 8, 21), conn)
    assert (partial["kind"], partial["days"], partial["after"]["revenue"]) == ("measuring", 5, 1200)
    assert partial["ready_on"] == "2026-09-20"
    assert (same_day["kind"], same_day["days"], same_day["after"]) == ("measuring", 0, None)


def test_other_actions_have_no_price_result(results_db):
    open_price = _price_action("2026-08-16T15:00:00+00:00")
    stock = _price_action("2026-08-16T15:00:00+00:00", "2026-08-20T15:00:00+00:00", key="estoque")
    with db.connection() as conn:
        assert price_action_result(COMPANY, open_price, date(2026, 10, 1), conn) == {"kind": "pending"}
        assert price_action_result(COMPANY, stock, date(2026, 10, 1), conn) == {"kind": "none"}
