"""Conta Stone statements move money to and from the "Reserva Stone". That is
not income nor spending: importing it must be a transfer to a reserve account,
so the consolidated cash balance stays right."""
from __future__ import annotations

import pytest

from backend import database as db
from backend.finance import ledger
from backend.integrations import bank_files


COMPANY = 1

STATEMENT = (
    "Data,Descricao,Valor,FITID\n"
    "2026-08-05,LOJA LTDA - Reserva Stone,-1000.00,R-OUT\n"
    "2026-08-06,LOJA LTDA - Reserva Stone,250.00,R-IN\n"
    "2026-08-06,FULANO - Pix | Maquininha,40.00,PIX-1\n"
).encode("utf-8")


@pytest.fixture
def stone_account(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "reserve.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    return ledger.create_account(COMPANY, "Conta Stone", "payment")


def preview_and_commit(account, decisions=None):
    preview = bank_files.preview_bank_import(COMPANY, account["id"], "extrato.csv", STATEMENT)
    chosen = {item["external_id"]: item["decision"] for item in preview["items"]}
    chosen.update(decisions or {})
    result = bank_files.commit_bank_import(
        COMPANY, account["id"], "extrato.csv", STATEMENT,
        decisions=chosen, preview_hash=preview["preview_hash"],
    )
    return preview, result


def reserve_account():
    return next(a for a in ledger.list_accounts(COMPANY) if a["name"] == bank_files.RESERVE_ACCOUNT_NAME)


def events(account_id):
    with db.connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cash_events WHERE cash_account_id=? ORDER BY occurred_at,amount_cents", (account_id,),
        )]


def test_the_review_proposes_reserve_lines_as_internal_transfers(stone_account):
    preview = bank_files.preview_bank_import(COMPANY, stone_account["id"], "extrato.csv", STATEMENT)
    by_id = {item["external_id"]: item for item in preview["items"]}
    assert by_id["R-OUT"]["decision"] == "transfer:reserve"
    assert by_id["R-OUT"]["internal_transfer"] is True
    assert by_id["R-OUT"]["candidate_cash_event_ids"] == []
    assert by_id["PIX-1"]["decision"] == "new"
    assert by_id["PIX-1"]["internal_transfer"] is False
    assert preview["internal_transfers"] == 2


def test_reserve_lines_become_mirrored_transfers_and_keep_the_total_cash(stone_account):
    _, result = preview_and_commit(stone_account)
    assert result == {"total": 3, "imported": 1, "duplicates": 0, "linked": 0, "transferred": 2}

    reserve = reserve_account()
    assert ledger.account_balance(stone_account["id"]) == -1000_00 + 250_00 + 40_00
    assert ledger.account_balance(reserve["id"]) == 1000_00 - 250_00
    # Money moved, none was spent: the company's cash is just the Pix.
    assert ledger.account_balance(stone_account["id"]) + ledger.account_balance(reserve["id"]) == 40_00

    stone_side = [e for e in events(stone_account["id"]) if e["kind"] == "transfer"]
    assert [e["amount_cents"] for e in stone_side] == [-1000_00, 250_00]
    assert all(e["transfer_group"] for e in stone_side)
    assert stone_side[0]["description"] == "LOJA LTDA - Reserva Stone"


def test_importing_again_creates_no_second_transfer_nor_second_reserve_account(stone_account):
    preview_and_commit(stone_account)
    preview, result = preview_and_commit(stone_account)
    assert preview["already_imported"] == 3
    assert result["transferred"] == 0 and result["duplicates"] == 3
    assert [a["name"] for a in ledger.list_accounts(COMPANY)].count(bank_files.RESERVE_ACCOUNT_NAME) == 1
    assert ledger.account_balance(reserve_account()["id"]) == 750_00


def test_the_partner_can_keep_a_reserve_line_as_a_plain_movement(stone_account):
    _, result = preview_and_commit(stone_account, {"R-IN": "new"})
    assert result["transferred"] == 1 and result["imported"] == 2
    assert ledger.account_balance(reserve_account()["id"]) == 1000_00


def test_an_existing_reserve_account_is_reused(stone_account):
    existing = ledger.create_account(COMPANY, "Reserva Stone", "bank")
    preview_and_commit(stone_account)
    assert reserve_account()["id"] == existing["id"]
    assert ledger.account_balance(existing["id"]) == 750_00


def test_transfer_decisions_are_refused_on_lines_that_are_not_reserve_moves(stone_account):
    with pytest.raises(bank_files.BankFileError):
        preview_and_commit(stone_account, {"PIX-1": "transfer:reserve"})


def test_the_reserve_name_matches_the_one_reconciliation_hides():
    from backend.finance import reconciliation
    assert reconciliation.RESERVE_ACCOUNT_NAME == bank_files.RESERVE_ACCOUNT_NAME
