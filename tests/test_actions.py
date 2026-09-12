from __future__ import annotations

import pytest

from backend import actions, database as db


COMPANY = 1


@pytest.fixture
def actions_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "actions.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def test_create_from_alert_is_idempotent_while_open(actions_db):
    a = actions.create_from_alert(COMPANY, "stock:77", "v1", "Ruptura de estoque: produto 77")
    b = actions.create_from_alert(COMPANY, "stock:77", "v1", "Ruptura de estoque: produto 77")

    assert a["id"] == b["id"]
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM actions WHERE company=?", (COMPANY,)).fetchone()["c"]
    assert count == 1


def test_new_condition_can_reopen_resolved_problem(actions_db):
    old = actions.create_from_alert(COMPANY, "stock:77", "v1", "Ruptura de estoque: produto 77")
    actions.transition_action(old["id"], "resolved")

    new = actions.create_from_alert(COMPANY, "stock:77", "v2", "Ruptura de estoque: produto 77")

    assert new["id"] != old["id"]
    assert new["alert_version"] == "v2"


def test_transition_records_history_and_resolved_at(actions_db):
    action = actions.create_from_alert(COMPANY, "price:5", "v1", "Preço abaixo do custo")

    actions.transition_action(action["id"], "in_progress", note="Assumido pelo gerente")
    resolved = actions.transition_action(action["id"], "resolved", note="Preço corrigido")

    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None
    events = actions.list_events(action["id"])
    assert [e["to_status"] for e in events] == ["open", "in_progress", "resolved"]


def test_invalid_transition_is_rejected(actions_db):
    action = actions.create_from_alert(COMPANY, "price:5", "v1", "Preço abaixo do custo")
    actions.transition_action(action["id"], "resolved")

    with pytest.raises(ValueError):
        actions.transition_action(action["id"], "in_progress")
