from __future__ import annotations

import json
from datetime import date

import pytest

from backend import database as db
from backend.finance import reconciliation
from backend.finance.accounts import archive_account, create_account, list_accounts
from backend.finance.accounts import AccountNature
from backend.finance.entries import EntryCommand, create_entry, get_entry, settle_entry
from backend.finance.entry_management import (
    EntryConflictError,
    EntryNotFoundError,
    EntryValidationError,
    cancel_entry,
    entry_history,
    update_entry,
)


@pytest.fixture
def expense_account(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "entry_management.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
        conn.execute("INSERT INTO companies(id,name) VALUES(2,'Loja 2')")
    return next(row for row in list_accounts(1) if row["system_key"] == "electricity")


def expense(account_id, amount=10_000, company=1, counterparty_id=None):
    return create_entry(
        EntryCommand(
            company_id=company,
            account_id=account_id,
            amount_cents=amount,
            competence="2026-08",
            due_date=date(2026, 9, 5),
            source="manual",
            external_id=None,
            description="Energia elétrica",
            counterparty_id=counterparty_id,
        )
    )


def test_open_entry_is_editable(expense_account):
    entry = expense(expense_account["id"])

    changed = update_entry(
        1,
        entry["id"],
        {"amount_cents": 120_000},
        expected_version=entry["version"],
        actor_id=None,
    )

    assert changed["amount_cents"] == 120_000
    assert changed["version"] == entry["version"] + 1


def test_stale_version_returns_conflict(expense_account):
    entry = expense(expense_account["id"])
    update_entry(
        1,
        entry["id"],
        {"description": "Energia elétrica - loja centro"},
        expected_version=entry["version"],
        actor_id=None,
    )

    with pytest.raises(EntryConflictError):
        update_entry(
            1,
            entry["id"],
            {"amount_cents": 5_000},
            expected_version=entry["version"],
            actor_id=None,
        )


def test_cancel_without_payment_removes_from_totals(expense_account):
    entry = expense(expense_account["id"])

    cancelled = cancel_entry(
        1,
        entry["id"],
        expected_version=entry["version"],
        reason="Lançamento duplicado",
        actor_id=None,
    )

    assert cancelled["status"] == "cancelled"
    from backend.finance.entries import result_for

    assert result_for(1, "2026-08")["expenses"] == 0


def test_payment_blocks_cancellation(expense_account):
    entry = expense(expense_account["id"])
    settle_entry(entry["id"], 4_000, paid_at=date(2026, 9, 5))
    current = get_entry(entry["id"])

    with pytest.raises(EntryConflictError):
        cancel_entry(
            1,
            entry["id"],
            expected_version=current["version"],
            reason="Tentativa de cancelamento",
            actor_id=None,
        )


def _link_to_reconciliation(entry_id, amount_cents):
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO reconciliation_groups(id,company,status,created_at,updated_at)
            VALUES(900,1,'manual_matched',?,?)
            ON CONFLICT(id) DO NOTHING
            """,
            (db.now(), db.now()),
        )
        conn.execute(
            """
            INSERT INTO reconciliation_links(id,group_id,item_type,item_id,amount_cents,created_at)
            VALUES(?,900,'financial_entry',?,?,?)
            """,
            (entry_id, entry_id, amount_cents, db.now()),
        )


def test_reconciliation_blocks_cancellation(expense_account, monkeypatch):
    entry = expense(expense_account["id"])
    _link_to_reconciliation(entry["id"], entry["amount_cents"])

    with pytest.raises(EntryConflictError):
        cancel_entry(
            1,
            entry["id"],
            expected_version=entry["version"],
            reason="Tentativa de cancelamento",
            actor_id=None,
        )


def test_reconciliation_blocks_update(expense_account):
    entry = expense(expense_account["id"])
    _link_to_reconciliation(entry["id"], entry["amount_cents"])

    with pytest.raises(EntryConflictError):
        update_entry(
            1,
            entry["id"],
            {"description": "Nova descrição"},
            expected_version=entry["version"],
            actor_id=None,
        )


def test_other_company_scope_does_not_alter_record(expense_account):
    entry = expense(expense_account["id"], company=1)

    with pytest.raises(EntryNotFoundError):
        update_entry(
            2,
            entry["id"],
            {"description": "Alterado por engano"},
            expected_version=entry["version"],
            actor_id=None,
        )

    unchanged = get_entry(entry["id"])
    assert unchanged["description"] == "Energia elétrica"
    assert unchanged["version"] == entry["version"]


def test_other_company_scope_does_not_alter_record_on_cancel(expense_account):
    entry = expense(expense_account["id"], company=1)

    with pytest.raises(EntryNotFoundError):
        cancel_entry(
            2,
            entry["id"],
            expected_version=entry["version"],
            reason="Tentativa de outra empresa",
            actor_id=None,
        )

    unchanged = get_entry(entry["id"])
    # get_entry() derives "overdue" from an open entry's due_date vs today for
    # display purposes; the underlying persisted status is still "open".
    assert unchanged["status"] in {"open", "overdue"}
    assert unchanged["version"] == entry["version"]


def test_partial_payment_restricts_whitelist_to_administrative_fields(expense_account):
    entry = expense(expense_account["id"], amount=10_000)
    settle_entry(entry["id"], 4_000, paid_at=date(2026, 9, 5))
    current = get_entry(entry["id"])
    assert current["status"] == "partially_paid"

    updated = update_entry(
        1,
        entry["id"],
        {"notes": "Combinado novo vencimento"},
        expected_version=current["version"],
        actor_id=None,
    )
    assert updated["notes"] == "Combinado novo vencimento"

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"amount_cents": 999},
            expected_version=updated["version"],
            actor_id=None,
        )


def test_paid_entry_cannot_be_edited(expense_account):
    entry = expense(expense_account["id"], amount=10_000)
    settle_entry(entry["id"], 10_000, paid_at=date(2026, 9, 5))
    current = get_entry(entry["id"])
    assert current["status"] == "paid"

    with pytest.raises(EntryConflictError):
        update_entry(
            1,
            entry["id"],
            {"notes": "Não deveria funcionar"},
            expected_version=current["version"],
            actor_id=None,
        )


def test_imported_entry_cannot_be_edited(expense_account):
    entry = create_entry(
        EntryCommand(
            company_id=1,
            account_id=expense_account["id"],
            amount_cents=5_000,
            competence="2026-08",
            due_date=date(2026, 9, 5),
            source="ofx",
            external_id="ofx-1",
            description="Importado do banco",
        )
    )

    with pytest.raises(EntryConflictError):
        update_entry(
            1,
            entry["id"],
            {"notes": "Não deveria funcionar"},
            expected_version=entry["version"],
            actor_id=None,
        )


def test_new_account_must_belong_to_company_and_be_active(expense_account):
    entry = expense(expense_account["id"])

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"account_id": 999_999_999},
            expected_version=entry["version"],
            actor_id=None,
        )

    other_account = create_account(1, "Categoria temporária", AccountNature.OPERATING_EXPENSE)
    archive_account(1, other_account["id"], expected_version=other_account["version"])

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"account_id": other_account["id"]},
            expected_version=entry["version"],
            actor_id=None,
        )


def test_historical_account_can_remain_even_if_archived_when_not_changing(expense_account):
    other_account = create_account(1, "Categoria histórica", AccountNature.OPERATING_EXPENSE)
    entry = expense(other_account["id"])
    archive_account(1, other_account["id"], expected_version=other_account["version"])

    updated = update_entry(
        1,
        entry["id"],
        {"description": "Ainda editável"},
        expected_version=entry["version"],
        actor_id=None,
    )
    assert updated["description"] == "Ainda editável"
    assert updated["account_id"] == other_account["id"]


def test_new_counterparty_must_belong_to_company_and_be_active(expense_account):
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO counterparties(id,company,name,kind,created_at,updated_at)
            VALUES(500,1,'Fornecedor X','supplier',?,?)
            """,
            (db.now(), db.now()),
        )
        conn.execute(
            """
            INSERT INTO counterparties(id,company,name,kind,archived,created_at,updated_at)
            VALUES(501,2,'Fornecedor Outra Empresa','supplier',0,?,?)
            """,
            (db.now(), db.now()),
        )

    entry = expense(expense_account["id"])

    updated = update_entry(
        1,
        entry["id"],
        {"counterparty_id": 500},
        expected_version=entry["version"],
        actor_id=None,
    )
    assert updated["counterparty_id"] == 500

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"counterparty_id": 501},
            expected_version=updated["version"],
            actor_id=None,
        )


def test_invalid_field_values_are_rejected(expense_account):
    entry = expense(expense_account["id"])

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"description": ""},
            expected_version=entry["version"],
            actor_id=None,
        )

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"amount_cents": 0},
            expected_version=entry["version"],
            actor_id=None,
        )

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"competence": "not-a-date"},
            expected_version=entry["version"],
            actor_id=None,
        )

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"due_date": "not-a-date"},
            expected_version=entry["version"],
            actor_id=None,
        )

    with pytest.raises(EntryValidationError):
        update_entry(
            1,
            entry["id"],
            {"notes": "x" * 2001},
            expected_version=entry["version"],
            actor_id=None,
        )


def test_update_records_audit_entry(expense_account):
    entry = expense(expense_account["id"])

    update_entry(
        1,
        entry["id"],
        {"amount_cents": 15_000},
        expected_version=entry["version"],
        actor_id=42,
    )

    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM finance_audit
            WHERE entity_type='financial_entry' AND entity_id=?
            """,
            (entry["id"],),
        ).fetchone()
    assert row is not None
    assert row["action"] == "update"
    assert row["actor_id"] == 42
    before = json.loads(row["before_json"])
    after = json.loads(row["after_json"])
    assert before["amount_cents"] == 10_000
    assert after["amount_cents"] == 15_000


def test_cancel_records_audit_entry_with_reason(expense_account):
    entry = expense(expense_account["id"])

    cancel_entry(
        1,
        entry["id"],
        expected_version=entry["version"],
        reason="Duplicado",
        actor_id=7,
    )

    with db.connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM finance_audit
            WHERE entity_type='financial_entry' AND entity_id=?
            """,
            (entry["id"],),
        ).fetchone()
    assert row["action"] == "cancel"
    assert row["reason"] == "Duplicado"
    assert row["actor_id"] == 7


def test_audit_failure_rolls_back_update(expense_account, monkeypatch):
    entry = expense(expense_account["id"])

    from backend.finance import entry_management

    def boom(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(entry_management, "_insert_audit", boom)

    with pytest.raises(RuntimeError):
        entry_management.update_entry(
            1,
            entry["id"],
            {"amount_cents": 999},
            expected_version=entry["version"],
            actor_id=None,
        )

    unchanged = get_entry(entry["id"])
    assert unchanged["amount_cents"] == 10_000
    assert unchanged["version"] == entry["version"]

    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS total FROM finance_audit WHERE entity_id=?",
            (entry["id"],),
        ).fetchone()["total"]
    assert count == 0


def test_audit_failure_rolls_back_cancel(expense_account, monkeypatch):
    entry = expense(expense_account["id"])

    from backend.finance import entry_management

    def boom(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(entry_management, "_insert_audit", boom)

    with pytest.raises(RuntimeError):
        entry_management.cancel_entry(
            1,
            entry["id"],
            expected_version=entry["version"],
            reason="Duplicado",
            actor_id=None,
        )

    unchanged = get_entry(entry["id"])
    # get_entry() derives "overdue" from an open entry's due_date vs today for
    # display purposes; the underlying persisted status is still "open".
    assert unchanged["status"] in {"open", "overdue"}
    assert unchanged["version"] == entry["version"]


def test_entry_history_includes_creation_and_legacy_events_without_fabricating_actors(
    expense_account,
):
    entry = expense(expense_account["id"])
    settle_entry(entry["id"], 4_000, paid_at=date(2026, 9, 5), created_by=None)
    current = get_entry(entry["id"])
    update_entry(
        1,
        entry["id"],
        {"notes": "Nova nota"},
        expected_version=current["version"],
        actor_id=99,
    )

    history = entry_history(1, entry["id"])

    kinds = [(item["kind"], item["action"]) for item in history]
    assert ("event", "created") in kinds
    assert ("event", "settled") in kinds
    assert ("audit", "update") in kinds

    created_item = next(item for item in history if item["action"] == "created")
    assert created_item["actor_id"] is None

    update_item = next(item for item in history if item["action"] == "update")
    assert update_item["actor_id"] == 99

    ordered_timestamps = [item["created_at"] for item in history]
    assert ordered_timestamps == sorted(ordered_timestamps)


def test_entry_history_requires_scope(expense_account):
    entry = expense(expense_account["id"], company=1)

    with pytest.raises(EntryNotFoundError):
        entry_history(2, entry["id"])
