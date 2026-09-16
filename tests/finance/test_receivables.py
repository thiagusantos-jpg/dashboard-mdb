from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backend import database as db
from backend.finance import accounts, ledger, receivables
from backend.finance.reporting import management_result


COMPANY = 1
FIXTURES = Path(__file__).parent.parent / "integrations" / "fixtures"


@pytest.fixture
def receivables_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "receivables.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


@pytest.fixture
def stone_account(receivables_db):
    return ledger.create_account(COMPANY, "Stone", "payment")


def test_sync_creates_a_fee_expense_per_installment(receivables_db, stone_account):
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()

    result = receivables.sync_receivables(COMPANY, stone_account["id"], content)

    assert result["imported"] == 3
    result_2026_09 = management_result(COMPANY, "2026-09")
    fee_line = next(
        (line for line in result_2026_09["accounts"] if line["system_key"] == "acquiring_fees"), None
    )
    assert fee_line is not None
    # NSU-0001 (25.00) + NSU-0002 installment 1 (9.00) settle in September;
    # installment 2 settles in October and must not leak into this competence.
    assert fee_line["actual_cents"] == 25_00 + 9_00


def test_reimporting_the_same_file_does_not_duplicate_fees(receivables_db, stone_account):
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()

    receivables.sync_receivables(COMPANY, stone_account["id"], content)
    second = receivables.sync_receivables(COMPANY, stone_account["id"], content)

    assert second["imported"] == 0
    assert second["duplicates"] == 3
    result_2026_09 = management_result(COMPANY, "2026-09")
    fee_line = next(line for line in result_2026_09["accounts"] if line["system_key"] == "acquiring_fees")
    assert fee_line["actual_cents"] == 25_00 + 9_00


def test_expected_settlements_group_net_amounts_by_date(receivables_db, stone_account):
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()
    receivables.sync_receivables(COMPANY, stone_account["id"], content)

    settlements = receivables.expected_settlements(COMPANY, date(2026, 9, 1), date(2026, 12, 31))

    by_date = {s["settlement_date"]: s["net_cents"] for s in settlements}
    assert by_date["2026-09-12"] == 975_00 + 291_00
    assert by_date["2026-10-12"] == 291_00


def test_effective_fee_report_compares_to_contracted_rate(receivables_db, stone_account):
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()
    receivables.sync_receivables(COMPANY, stone_account["id"], content)
    accounts.set_parameter(COMPANY, "contracted_mdr_rate", 2.0, "2026-01-01")

    report = receivables.effective_fee_report(COMPANY, date(2026, 9, 1), date(2026, 12, 31))

    assert report["gross_cents"] == 1_000_00 + 300_00 + 300_00
    assert report["fee_cents"] == 25_00 + 9_00 + 9_00
    assert report["effective_rate_pct"] == pytest.approx(2.6875, rel=1e-3)
    assert report["contracted_rate_pct"] == 2.0
    assert report["variance_pct"] == pytest.approx(0.6875, rel=1e-3)


def test_portal_csv_books_fees_once_per_day_and_never_as_bills(receivables_db, stone_account):
    from backend.finance import obligations

    content = (FIXTURES / "stone-recebiveis-sample.csv").read_bytes()
    result = receivables.sync_receivables(COMPANY, stone_account["id"], content)
    assert result == {"total": 7, "imported": 7, "duplicates": 0, "fee_entries": 3}

    with db.connection() as conn:
        entries = [dict(r) for r in conn.execute(
            "SELECT e.description,e.amount_cents,a.system_key FROM financial_entries e "
            "JOIN finance_accounts a ON a.id=e.account_id WHERE e.company=1 ORDER BY e.due_date,a.system_key"
        )]
    assert entries == [
        {"description": "Taxas Stone de 10/09 (2 venda(s))", "amount_cents": 2_72 + 50, "system_key": "acquiring_fees"},
        {"description": "Taxas Stone de 11/09 (1 venda(s))", "amount_cents": 18_00 - 7, "system_key": "acquiring_fees"},
        {"description": "Antecipação Stone de 11/09 (1 venda(s))", "amount_cents": 12_00, "system_key": "receivables_advance"},
    ]
    assert not any("Stone" in str(row.get("description")) for row in obligations._entry_rows(COMPANY))

    days = {d["settlement_date"]: d for d in receivables.expected_settlements(COMPANY, date(2026, 9, 1), date(2026, 9, 30))}
    assert days["2026-09-10"]["net_cents"] == 90_26 + 49_50
    assert days["2026-09-11"]["net_cents"] == 1_170_00 - 9_93 - 90_00


def test_reimporting_the_portal_csv_adds_nothing(receivables_db, stone_account):
    content = (FIXTURES / "stone-recebiveis-sample.csv").read_bytes()
    receivables.sync_receivables(COMPANY, stone_account["id"], content)
    again = receivables.sync_receivables(COMPANY, stone_account["id"], content)
    assert again == {"total": 7, "imported": 0, "duplicates": 7, "fee_entries": 0}
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM financial_entries").fetchone()["n"] == 3
        assert conn.execute("SELECT COUNT(*) AS n FROM stone_receivables").fetchone()["n"] == 7


def test_a_later_export_with_new_rows_for_a_booked_day_adds_a_second_fee_entry(receivables_db, stone_account):
    content = (FIXTURES / "stone-recebiveis-sample.csv").read_bytes()
    lines = content.decode("utf-8-sig").splitlines()
    first_part = "\n".join(lines[:2]).encode()
    receivables.sync_receivables(COMPANY, stone_account["id"], first_part)
    result = receivables.sync_receivables(COMPANY, stone_account["id"], content)
    assert result["imported"] == 6 and result["duplicates"] == 1
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT external_id,amount_cents FROM financial_entries WHERE external_id LIKE ? ORDER BY external_id",
            (f"{stone_account['id']}:mdr:2026-09-10:%",),
        ).fetchall()
    assert [(r["external_id"].rsplit(":", 1)[-1], r["amount_cents"]) for r in rows] == [("1", 2_72), ("2", 50)]
