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

    # The brief's own literal 3rd assertion — read the PERSISTED
    # bank_cash_links row back from the database (not just the CSV parser's
    # own echo of external_id) and confirm it points at the payment's real
    # cash_event_id.
    with db.connection() as conn:
        link_row = conn.execute(
            "SELECT * FROM bank_cash_links WHERE company=? AND cash_account_id=? AND external_id=?",
            (COMPANY, cash_account["id"], external_transaction_id),
        ).fetchone()
    assert link_row is not None
    linked_external_id = link_row["external_id"]
    assert linked_external_id == external_transaction_id
    assert str(link_row["cash_event_id"]) == str(cash_event_id)


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

    # Committing the identical decision twice must not create a SECOND
    # bank_cash_links row for the same cash_event_id — "linked == 1" both
    # times would still pass even if a second row were wrongly inserted, so
    # this counts the actual persisted rows directly.
    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM bank_cash_links WHERE cash_event_id=?",
            (cash_event_id,),
        ).fetchone()["c"]
    assert count == 1


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


def test_repeated_external_id_within_one_file_creates_only_one_cash_event(cash_account):
    """A file with the SAME external_id twice (e.g. a duplicated OFX FITID
    line) must only ever create ONE cash_events row for it — the duplicate
    check inside commit_bank_import must see this SAME transaction's own
    earlier write to external_records, not a stale read from a separate
    connection blind to the outer transaction's in-flight inserts."""
    content = (
        "Data,Descricao,Valor,FITID\n"
        "2026-09-12,Pagamento fornecedor,-40.00,BANK-DUP\n"
        "2026-09-12,Pagamento fornecedor,-40.00,BANK-DUP\n"
    ).encode("utf-8")

    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], "extrato.csv", content)
    assert preview["count"] == 2

    commit = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], "extrato.csv", content,
        decisions={"BANK-DUP": "new"}, preview_hash=preview["preview_hash"],
    )
    assert commit["imported"] == 1
    assert commit["duplicates"] == 1

    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM cash_events WHERE company=? AND cash_account_id=? AND amount_cents=?",
            (COMPANY, cash_account["id"], -4_000),
        ).fetchone()["c"]
    assert count == 1
    assert ledger.account_balance(cash_account["id"]) == -4_000


def test_bank_cash_links_cash_event_id_is_unique_at_the_database_level(cash_account):
    """The "linked at most once" invariant must be enforced by the database
    itself (migration 019's UNIQUE(cash_event_id)), not only by an
    application-level check-then-insert — the latter alone races under
    concurrent commits at Postgres READ COMMITTED. A direct second insert
    for the same cash_event_id, bypassing commit_bank_import entirely, must
    be rejected by the schema."""
    entry = expense_entry(100_000)
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=cash_account["id"],
    )
    cash_event_id = payment["cash_event_id"]

    import secrets
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO bank_cash_links(
                id,company,cash_account_id,provider,external_id,cash_event_id,created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (secrets.randbits(63), COMPANY, cash_account["id"], "bank_file", "BANK-A", cash_event_id, db.now()),
        )

    with pytest.raises(Exception):
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO bank_cash_links(
                    id,company,cash_account_id,provider,external_id,cash_event_id,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (secrets.randbits(63), COMPANY, cash_account["id"], "bank_file", "BANK-B", cash_event_id, db.now()),
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


def _preview_and_commit(cash_account, name, statement):
    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], name, statement)
    result = bank_files.commit_bank_import(
        COMPANY, cash_account["id"], name, statement,
        decisions={item["external_id"]: item["decision"] for item in preview["items"]},
        preview_hash=preview["preview_hash"],
    )
    return preview, result


def test_reviewing_the_same_statement_again_marks_lines_as_already_imported(cash_account):
    statement = _csv_statement(
        external_id="BANK-0100", iso_date="2026-09-12", amount_cents=-15_000,
        description="Pix fornecedor",
    )
    _preview_and_commit(cash_account, "extrato.csv", statement)

    # Before the fix the second review offered the movement the first import
    # created as "já lançado", and confirming that suggestion was refused.
    preview, result = _preview_and_commit(cash_account, "extrato.csv", statement)
    item = preview["items"][0]
    assert item["already_imported"] is True
    assert item["candidate_cash_event_ids"] == []
    assert item["decision"] == "new"
    assert preview["already_imported"] == 1
    assert result == {"total": 1, "imported": 0, "duplicates": 1, "linked": 0}
    assert ledger.account_balance(cash_account["id"]) == -15_000


def test_overlapping_statements_never_link_a_new_line_to_another_imported_line(cash_account):
    # Two different R$ 10 Pix on the same day, one in each file (monthly
    # statements overlap). The second must not be offered as the first.
    first = _csv_statement(external_id="PIX-A", iso_date="2026-09-12", amount_cents=1_000, description="Pix A")
    second = (
        "Data,Descricao,Valor,FITID\n"
        "2026-09-12,Pix A,10.00,PIX-A\n"
        "2026-09-12,Pix B,10.00,PIX-B\n"
    ).encode("utf-8")
    _preview_and_commit(cash_account, "setembro-1.csv", first)

    preview, result = _preview_and_commit(cash_account, "setembro-2.csv", second)
    by_id = {item["external_id"]: item for item in preview["items"]}
    assert by_id["PIX-A"]["already_imported"] is True
    assert by_id["PIX-B"]["already_imported"] is False
    assert by_id["PIX-B"]["candidate_cash_event_ids"] == []
    assert result["imported"] == 1 and result["duplicates"] == 1
    assert ledger.account_balance(cash_account["id"]) == 2_000


def test_a_payment_recorded_in_the_panel_is_still_offered_after_other_imports(cash_account):
    other = _csv_statement(external_id="X-1", iso_date="2026-09-12", amount_cents=-40_000, description="Outro")
    _preview_and_commit(cash_account, "a.csv", other)
    entry = expense_entry(40_000)
    payment = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-overlap", cash_account_id=cash_account["id"],
    )
    statement = _csv_statement(external_id="X-2", iso_date="2026-09-12", amount_cents=-40_000, description="Aluguel")

    preview = bank_files.preview_bank_import(COMPANY, cash_account["id"], "b.csv", statement)
    assert preview["items"][0]["candidate_cash_event_ids"] == [str(payment["cash_event_id"])]
