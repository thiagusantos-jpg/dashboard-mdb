from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, ledger, reconciliation
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.payments import record_payment


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


# --- New item_type='payment' capability (task B5) ---------------------------
#
# Lets a reconciliation group link to ONE SPECIFIC obligation_payments row
# (one partial slice of an obligation) instead of the whole underlying
# financial_entry — so confirming one partial payment's own bank echo never
# forces the ENTIRE obligation to be treated as consumed by that single
# reconciliation. Purely additive: every existing entry_ids-only test above
# is unaffected, and old groups (which only ever hold 'financial_entry' /
# 'cash_event' links) keep reading back exactly as before.


def _rent_entry(amount_cents, due_date):
    account = accounts.account_by_key(COMPANY, "rent")
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=amount_cents,
        competence=due_date.isoformat()[:7], due_date=due_date, source="manual",
        external_id=None, description="Aluguel",
    ))


def test_confirm_links_one_partial_payment_without_consuming_the_whole_entry(reconciliation_db):
    entry = _rent_entry(100_000, date(2026, 9, 20))
    bank = ledger.create_account(COMPANY, "Banco X", "bank")
    first_payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 10),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    second_payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=60_000, paid_at=date(2026, 9, 15),
        expected_version=2, idempotency_key="pay-2", cash_account_id=bank["id"],
    )

    # An unrelated, unexplained debit that happens to match ONLY the first
    # payment's own slice (not the entry's full 100_000 total) — suggest()
    # finds no whole-entry candidate at this amount, so it starts unmatched;
    # confirm() then targets that ONE payment specifically.
    stray_debit = ledger.post_cash_event(COMPANY, bank["id"], -40_000, date(2026, 9, 10), "Débito a explicar")
    group = reconciliation.suggest(COMPANY, stray_debit["id"])
    assert group["status"] == "unmatched"

    payment_id = int(first_payment["payment_id"])
    confirmed = reconciliation.confirm(group["id"], [], payment_ids=[payment_id])

    assert confirmed["status"] == "manual_matched"
    assert confirmed["difference_cents"] == 0
    payment_links = [link for link in confirmed["links"] if link["item_type"] == "payment"]
    assert len(payment_links) == 1
    assert payment_links[0]["item_id"] == payment_id

    # The SECOND payment (and the entry itself) remain completely free for
    # their own, separate reconciliation — the first confirm() must never
    # have consumed the whole obligation.
    another_stray_debit = ledger.post_cash_event(
        COMPANY, bank["id"], -60_000, date(2026, 9, 15), "Outro débito a explicar"
    )
    other_group = reconciliation.suggest(COMPANY, another_stray_debit["id"])
    second_payment_id = int(second_payment["payment_id"])
    other_confirmed = reconciliation.confirm(other_group["id"], [], payment_ids=[second_payment_id])
    assert other_confirmed["status"] == "manual_matched"
    assert other_confirmed["difference_cents"] == 0


def test_a_payment_cannot_be_linked_into_two_reconciliation_groups(reconciliation_db):
    # Entry total (100_000) deliberately differs from the payment (40_000) —
    # left partially_paid, not fully paid — so the reopened/partial entry's
    # own whole-amount never accidentally auto-matches either stray debit
    # below, keeping this test focused on the payment-level double-link guard.
    entry = _rent_entry(100_000, date(2026, 9, 20))
    bank = ledger.create_account(COMPANY, "Banco X", "bank")
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 10),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    payment_id = int(payment["payment_id"])
    stray_debit_1 = ledger.post_cash_event(COMPANY, bank["id"], -40_000, date(2026, 9, 10), "Débito 1")
    stray_debit_2 = ledger.post_cash_event(COMPANY, bank["id"], -40_000, date(2026, 9, 11), "Débito 2")
    group1 = reconciliation.suggest(COMPANY, stray_debit_1["id"])
    group2 = reconciliation.suggest(COMPANY, stray_debit_2["id"])

    reconciliation.confirm(group1["id"], [], payment_ids=[payment_id])

    with pytest.raises(ValueError):
        reconciliation.confirm(group2["id"], [], payment_ids=[payment_id])


def test_a_reversed_payment_cannot_be_reconciled(reconciliation_db):
    from backend.finance.payments import reverse_payment

    # Entry total (100_000) deliberately differs from the payment being
    # reversed (40_000) so that, once reversed, the reopened entry's own
    # whole-amount (-100_000) does NOT accidentally auto-match the -40_000
    # stray debit below — keeping this test focused on the reversed-payment
    # guard itself rather than an incidental whole-entry match.
    entry = _rent_entry(100_000, date(2026, 9, 20))
    bank = ledger.create_account(COMPANY, "Banco X", "bank")
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 10),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    payment_id = int(payment["payment_id"])
    reverse_payment(
        COMPANY, payment_id, reason="Pagamento incorreto", reversed_at=date(2026, 9, 11),
        expected_version=2, idempotency_key="undo-1",
    )

    stray_debit = ledger.post_cash_event(COMPANY, bank["id"], -40_000, date(2026, 9, 10), "Débito")
    group = reconciliation.suggest(COMPANY, stray_debit["id"])

    with pytest.raises(ValueError):
        reconciliation.confirm(group["id"], [], payment_ids=[payment_id])
