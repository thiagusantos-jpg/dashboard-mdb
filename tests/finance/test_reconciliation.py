from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, ledger, reconciliation
from backend.finance.entries import EntryCommand, create_entry


COMPANY = 1


@pytest.fixture
def reconciliation_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "reconciliation.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def make_entry(amount, due_date, description, nature_key="sales"):
    account = accounts.account_by_key(COMPANY, nature_key)
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=amount,
        competence=due_date.isoformat()[:7], due_date=due_date, source="manual",
        external_id=None, description=description,
    ))


def make_cash_event(amount, occurred_at, description="Crédito"):
    bank = ledger.create_account(COMPANY, "Stone", "payment")
    return ledger.post_cash_event(COMPANY, bank["id"], amount, occurred_at, description)


def test_unambiguous_single_match_is_auto_confirmed(reconciliation_db):
    entry = make_entry(1_000_00, date(2026, 9, 10), "Venda cartão")
    event = make_cash_event(1_000_00, date(2026, 9, 12))

    group = reconciliation.suggest(COMPANY, event["id"])

    assert group["status"] == "auto_matched"
    assert group["difference_cents"] == 0
    assert len(group["links"]) == 2


def test_one_credit_can_settle_multiple_entries(reconciliation_db):
    sale1 = make_entry(600_00, date(2026, 9, 10), "Venda 1")
    sale2 = make_entry(400_00, date(2026, 9, 10), "Venda 2")
    fee = make_entry(25_00, date(2026, 9, 10), "Taxa adquirente", nature_key="acquiring_fees")
    event = make_cash_event(975_00, date(2026, 9, 12))

    group = reconciliation.suggest(COMPANY, event["id"])

    assert group["status"] == "auto_matched"
    assert group["difference_cents"] == 0
    assert len(group["links"]) == 4


def test_ambiguous_candidates_stay_pending_for_human_review(reconciliation_db):
    make_entry(500_00, date(2026, 9, 10), "Venda A")
    make_entry(500_00, date(2026, 9, 11), "Venda B")
    event = make_cash_event(500_00, date(2026, 9, 12))

    group = reconciliation.suggest(COMPANY, event["id"])

    assert group["status"] == "suggested"
    assert len(group["candidates"]) >= 2


def test_confirm_resolves_a_suggested_group(reconciliation_db):
    entry_a = make_entry(500_00, date(2026, 9, 10), "Venda A")
    make_entry(500_00, date(2026, 9, 11), "Venda B")
    event = make_cash_event(500_00, date(2026, 9, 12))
    group = reconciliation.suggest(COMPANY, event["id"])

    confirmed = reconciliation.confirm(group["id"], [entry_a["id"]])

    assert confirmed["status"] == "manual_matched"
    assert confirmed["difference_cents"] == 0


def test_no_candidates_leaves_group_unmatched(reconciliation_db):
    event = make_cash_event(300_00, date(2026, 9, 12))

    group = reconciliation.suggest(COMPANY, event["id"])

    assert group["status"] == "unmatched"
    assert len(group["links"]) == 1


def test_an_item_cannot_be_linked_into_two_groups(reconciliation_db):
    entry = make_entry(1_000_00, date(2026, 9, 10), "Venda cartão")
    event = make_cash_event(1_000_00, date(2026, 9, 12))
    reconciliation.suggest(COMPANY, event["id"])

    other_event = make_cash_event(1_000_00, date(2026, 9, 13))
    group2 = reconciliation.suggest(COMPANY, other_event["id"])
    # the entry is already linked to the first group, so it can't be an
    # unambiguous candidate for the second one
    assert group2["status"] == "unmatched"


def test_undo_frees_items_for_a_fresh_suggestion(reconciliation_db):
    entry = make_entry(1_000_00, date(2026, 9, 10), "Venda cartão")
    event = make_cash_event(1_000_00, date(2026, 9, 12))
    group = reconciliation.suggest(COMPANY, event["id"])

    reconciliation.undo(group["id"], reason="Conciliação errada")

    new_group = reconciliation.suggest(COMPANY, event["id"])
    assert new_group["id"] != group["id"]
    assert new_group["status"] == "auto_matched"
