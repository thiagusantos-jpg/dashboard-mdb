from __future__ import annotations

from pathlib import Path

import pytest

from backend import database as db
from backend.finance import ledger
from backend.integrations.bank_files import (
    BankFileError,
    import_bank_file,
    parse_bank_file,
)
from backend.integrations.records import external_record_count


COMPANY = 1
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def bank_files_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "bank_files.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def stone_account(bank_files_db):
    return ledger.create_account(COMPANY, "Stone", "payment")


def test_parse_ofx_extracts_three_transactions():
    content = (FIXTURES / "stone-sample.ofx").read_bytes()
    transactions = parse_bank_file(content, "stone-sample.ofx")

    assert len(transactions) == 3
    assert transactions[0].amount_cents == 600_00
    assert transactions[2].amount_cents == -25_00
    assert transactions[0].external_id == "STONE-0001"


def test_parse_csv_matches_ofx_transactions():
    content = (FIXTURES / "stone-sample.csv").read_bytes()
    transactions = parse_bank_file(content, "stone-sample.csv")

    assert len(transactions) == 3
    assert transactions[1].amount_cents == 400_00
    assert transactions[1].date == "2026-09-10"


def test_unsupported_extension_is_rejected():
    with pytest.raises(BankFileError):
        parse_bank_file(b"whatever", "statement.pdf")


def test_importing_same_statement_twice_creates_three_records(bank_files_db, stone_account):
    content = (FIXTURES / "stone-sample.ofx").read_bytes()

    first = import_bank_file(COMPANY, stone_account["id"], "stone-sample.ofx", content)
    second = import_bank_file(COMPANY, stone_account["id"], "stone-sample.ofx", content)

    assert first["imported"] == 3
    assert second["imported"] == 0
    assert second["duplicates"] == 3
    assert external_record_count(COMPANY) == 3
    assert ledger.account_balance(stone_account["id"]) == 600_00 + 400_00 - 25_00


def test_ofx_and_csv_of_the_same_statement_produce_the_same_balance(bank_files_db):
    ofx_account = ledger.create_account(COMPANY, "Stone OFX", "payment")
    csv_account = ledger.create_account(COMPANY, "Stone CSV", "payment")

    import_bank_file(COMPANY, ofx_account["id"], "stone-sample.ofx", (FIXTURES / "stone-sample.ofx").read_bytes())
    import_bank_file(COMPANY, csv_account["id"], "stone-sample.csv", (FIXTURES / "stone-sample.csv").read_bytes())

    assert ledger.account_balance(ofx_account["id"]) == ledger.account_balance(csv_account["id"])
