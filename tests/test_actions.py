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


def _user(user_id, name, company=COMPANY, admin=0):
    ts = db.now()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO users(id,email,name,password_hash,password_salt,is_admin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, f"u{user_id}@loja.test", name, "x", "x", admin, ts, ts),
        )
        if company is not None:
            conn.execute(
                "INSERT INTO user_scopes(user_id,company,store,role,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (user_id, company, 0, "partner", ts, ts),  # store 0 = whole company, as grant_role() writes
            )


def test_baseline_count_is_kept(actions_db):
    action = actions.create_from_alert(COMPANY, "estoque", "v1", "170 produto(s) sem estoque", baseline_count=170)
    assert action["baseline_count"] == 170
    assert actions.list_actions(COMPANY)[0]["baseline_count"] == 170


def test_taking_an_action_makes_the_partner_its_assignee(actions_db):
    _user(10, "Ana Sócia")
    action = actions.create_from_alert(COMPANY, "estoque", "v1", "Ruptura")

    taken = actions.transition_action(action["id"], "in_progress", created_by=10)

    assert taken["assignee"] == 10
    assert taken["assignee_name"] == "Ana Sócia"


def test_taking_keeps_an_existing_assignee(actions_db):
    _user(10, "Ana")
    _user(11, "Bia")
    action = actions.create_from_alert(COMPANY, "estoque", "v1", "Ruptura", assignee=11)

    taken = actions.transition_action(action["id"], "in_progress", created_by=10)

    assert taken["assignee"] == 11


def test_update_sets_assignee_due_date_and_priority_and_logs_it(actions_db):
    _user(10, "Ana Sócia")
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")

    updated = actions.update_action(
        action["id"], {"assignee": 10, "due_date": "2026-09-20", "priority": "high"}, created_by=10
    )

    assert (updated["assignee"], updated["due_date"], updated["priority"]) == (10, "2026-09-20", "high")
    last = actions.list_events(action["id"])[-1]
    assert last["event_type"] == "updated"
    assert last["note"] == "Responsável: Ana Sócia; Prazo: 20/09/2026; Prioridade: alta"
    assert last["created_by_name"] == "Ana Sócia"


def test_update_can_clear_the_due_date(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos", due_date="2026-09-20")
    cleared = actions.update_action(action["id"], {"due_date": None})
    assert cleared["due_date"] is None
    assert actions.list_events(action["id"])[-1]["note"] == "Prazo removido"


def test_update_without_changes_logs_nothing(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")
    actions.update_action(action["id"], {"priority": "medium"})
    assert len(actions.list_events(action["id"])) == 1


def test_closed_action_cannot_be_edited(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")
    actions.transition_action(action["id"], "resolved")
    with pytest.raises(ValueError):
        actions.update_action(action["id"], {"priority": "high"})


def test_status_note_is_the_note_that_closed_the_action(actions_db):
    action = actions.create_from_alert(COMPANY, "preco", "v1", "44 produtos")
    actions.transition_action(action["id"], "in_progress", note="Vou ver com o fornecedor")
    actions.transition_action(action["id"], "resolved", note="Preço corrigido")
    assert actions.list_actions(COMPANY)[0]["status_note"] == "Preço corrigido"


def test_assignees_are_the_company_partners_and_global_admins(actions_db):
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(2,'Loja 2')")
    _user(10, "Ana")
    _user(11, "Bia sem acesso", company=None)
    _user(12, "Admin geral", company=None, admin=1)
    _user(13, "Caio da outra loja", company=2)

    names = {u["name"] for u in actions.assignees(COMPANY)}

    assert {"Ana", "Admin geral"} <= names
    assert "Bia sem acesso" not in names
    assert "Caio da outra loja" not in names
    assert actions.is_assignable(COMPANY, 10)
    assert not actions.is_assignable(COMPANY, 13)
