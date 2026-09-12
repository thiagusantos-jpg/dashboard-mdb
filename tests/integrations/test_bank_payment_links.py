from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, ledger
from backend.finance.entries import EntryCommand, create_entry
from backend.finance.payments import record_payment
from backend.integrations import bank_files


COMPANY = 1


@pytest.fixture
def bank_payment_links_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "bank_payment_links.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def cash_account(bank_payment_links_db):
    return ledger.create_account(COMPANY, "Banco X", "bank")


def expense_entry(amount_cents=100_000):
    account = accounts.account_by_key(COMPANY, "rent")
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=amount_cents,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))


def _csv_statement(*, external_id: str, iso_date: str, amount_cents: int, description: str) -> bytes:
    amount = f"{amount_cents / 100:.2f}"
    body = (
        "Data,Descricao,Valor,FITID\n"
        f"{iso_date},{description},{amount},{external_id}\n"
    )
    return body.encode("utf-8")


def test_import_linking_existing_payment_avoids_duplicate_cash_and_stays_idempotent(
    cash_account,
):
    entry = expense_entry(100_000)
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=cash_account["id"],
    )
    cash_event_id = payment["cash_event_id"]

    before_import_balance = ledger.account_balance(cash_account["id"])
    assert before_import_balance == -40_000

    statement = _csv_statement(
        external_id="BANK-0001", iso_date="2026-09-12", amount_cents=-40_000,
        description="Pagamento fornecedor",
    )

    preview = bank_files.preview_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
    )
    assert preview["count"] == 1
    item = preview["items"][0]
    external_transaction_id = item["external_id"]
    assert item["candidate_cash_event_ids"] == [str(cash_event_id)]
    assert item["decision"] == f"link:{cash_event_id}"

    commit = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
        decisions={external_transaction_id: f"link:{cash_event_id}"},
        preview_hash=preview["preview_hash"],
    )
    assert commit["linked"] == 1
    assert commit["imported"] == 0

    after_link_balance = ledger.account_balance(cash_account["id"])
    assert after_link_balance == before_import_balance

    # Re-importing the same statement a second time must not create a third
    # duplicate cash movement, and must not error either — a fresh preview
    # now shows no candidate (it is already linked), so the default "new"
    # decision is chosen, and the pre-existing external_records dedup makes
    # it a no-op.
    second_preview = bank_files.preview_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
    )
    second_item = second_preview["items"][0]
    assert second_item["candidate_cash_event_ids"] == []
    assert second_item["decision"] == "new"

    second_commit = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
        decisions={second_item["external_id"]: "new"},
        preview_hash=second_preview["preview_hash"],
    )
    assert second_commit["imported"] == 0
    assert second_commit["duplicates"] == 1

    after_second_import_balance = ledger.account_balance(cash_account["id"])
    assert after_second_import_balance == before_import_balance
    assert external_transaction_id == "BANK-0001"


def test_recommitting_the_exact_same_link_decision_returns_existing_link(cash_account):
    entry = expense_entry(100_000)
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=cash_account["id"],
    )
    cash_event_id = payment["cash_event_id"]
    statement = _csv_statement(
        external_id="BANK-0001", iso_date="2026-09-12", amount_cents=-40_000,
        description="Pagamento fornecedor",
    )
    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], "extrato.csv", statement)
    decisions = {"BANK-0001": f"link:{cash_event_id}"}

    first = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
        decisions=decisions, preview_hash=preview["preview_hash"],
    )
    second = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
        decisions=decisions, preview_hash=preview["preview_hash"],
    )

    assert first["linked"] == 1
    assert second["linked"] == 1
    assert ledger.account_balance(cash_account["id"]) == -40_000


def test_two_legitimate_equal_payments_are_not_auto_merged(cash_account):
    """Two DIFFERENT real payments with identical amount/date must never be
    silently linked to each other just because they match on value/date —
    matching only ever produces a suggestion, never an automatic decision,
    when the suggestion is ambiguous."""
    entry_a = expense_entry(100_000)
    entry_b = expense_entry(100_000)
    payment_a = record_payment(
        COMPANY, "entry", entry_a["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-a", cash_account_id=cash_account["id"],
    )
    payment_b = record_payment(
        COMPANY, "entry", entry_b["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-b", cash_account_id=cash_account["id"],
    )
    before_balance = ledger.account_balance(cash_account["id"])
    assert before_balance == -80_000

    statement = _csv_statement(
        external_id="BANK-0001", iso_date="2026-09-12", amount_cents=-40_000,
        description="Pagamento fornecedor",
    )
    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], "extrato.csv", statement)
    item = preview["items"][0]

    assert sorted(item["candidate_cash_event_ids"]) == sorted(
        [str(payment_a["cash_event_id"]), str(payment_b["cash_event_id"])]
    )
    # Ambiguous match -> never auto-decided as a link.
    assert item["decision"] == "new"

    # Committing with the default "new" decision creates a THIRD, independent
    # cash movement rather than silently fusing into either candidate.
    commit = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", statement,
        decisions={item["external_id"]: "new"},
        preview_hash=preview["preview_hash"],
    )
    assert commit["imported"] == 1
    assert ledger.account_balance(cash_account["id"]) == before_balance - 40_000


def test_stale_preview_hash_is_rejected_with_conflict(cash_account):
    entry = expense_entry(100_000)
    record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=cash_account["id"],
    )
    statement = _csv_statement(
        external_id="BANK-0001", iso_date="2026-09-12", amount_cents=-40_000,
        description="Pagamento fornecedor",
    )
    other_statement = _csv_statement(
        external_id="BANK-9999", iso_date="2026-09-12", amount_cents=-1_00,
        description="Outro",
    )
    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], "extrato.csv", statement)

    with pytest.raises(bank_files.BankImportConflict):
        bank_files.commit_bank_import(
            COMPANY, cash_account["id"], "extrato.csv", other_statement,
            decisions={}, preview_hash=preview["preview_hash"],
        )


def test_link_decision_revalidates_candidate_scope_and_amount(cash_account):
    other_account = ledger.create_account(COMPANY, "Banco Y", "bank")
    entry = expense_entry(100_000)
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=other_account["id"],
    )
    statement = _csv_statement(
        external_id="BANK-0001", iso_date="2026-09-12", amount_cents=-40_000,
        description="Pagamento fornecedor",
    )
    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], "extrato.csv", statement)
    # The candidate belongs to a different cash account, so it must never
    # appear as a suggestion for this account's import.
    assert preview["items"][0]["candidate_cash_event_ids"] == []

    with pytest.raises(bank_files.BankImportConflict):
        bank_files.commit_bank_import(
            COMPANY, cash_account["id"], "extrato.csv", statement,
            decisions={"BANK-0001": f"link:{payment['cash_event_id']}"},
            preview_hash=preview["preview_hash"],
        )
