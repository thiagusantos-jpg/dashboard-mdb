from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, loans
from backend.finance.entries import EntryCommand, create_entry, settle_entry
from backend.finance.obligations import get_obligation, list_obligations


COMPANY = 1


@pytest.fixture
def obligations_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "obligations.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def _partial_entry():
    account = accounts.account_by_key(COMPANY, "rent")
    entry = create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=100_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    settle_entry(entry["id"], 40_000, paid_at=date(2026, 9, 12))
    return entry


def _two_installment_loan():
    return loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=200_000, net_disbursement_cents=195_000,
        installments=[
            {"number": 1, "due_date": "2026-09-10", "principal_cents": 100_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-10-10", "principal_cents": 100_000, "interest_cents": 5_000},
        ],
        start_date="2026-09-01",
    )


# --- Brief's exact scenario -------------------------------------------------


def test_partial_entry_and_two_installment_loan_yields_three_obligations(obligations_db):
    _partial_entry()
    _two_installment_loan()

    result = list_obligations(1, limit=1, include_sensitive=True)
    assert len(result["items"]) == 1
    assert result["total"] == 3
    assert result["next_cursor"] is not None


def test_interest_baked_into_installment_does_not_appear_as_separate_obligation(obligations_db):
    # A 2-installment loan (interest included in total_cents per installment)
    # must contribute exactly 2 obligations, not 4 (principal+interest split).
    _two_installment_loan()
    result = list_obligations(1, include_sensitive=True)
    installment_items = [i for i in result["items"] if i["kind"] == "loan_installment"]
    assert len(installment_items) == 2
    assert installment_items[0]["total_cents"] == 105_000  # 100_000 principal + 5_000 interest, one row


def test_sum_of_open_balances_is_correct(obligations_db):
    _partial_entry()  # 100_000 - 40_000 paid = 60_000 open
    _two_installment_loan()  # 105_000 + 105_000 = 210_000 open
    result = list_obligations(1, include_sensitive=True)
    assert result["open_cents"] == 60_000 + 105_000 + 105_000


def test_old_schedule_excluded_after_renegotiation(obligations_db):
    loan = _two_installment_loan()
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-11-01", "principal_cents": 210_000, "interest_cents": 0}],
        reason="Prazo estendido",
    )
    result = list_obligations(1, include_sensitive=True)
    installment_items = [i for i in result["items"] if i["kind"] == "loan_installment"]
    # Only the new schedule's single installment should remain.
    assert len(installment_items) == 1
    assert installment_items[0]["total_cents"] == 210_000


def test_totals_are_identical_across_pages(obligations_db):
    _partial_entry()
    _two_installment_loan()

    page1 = list_obligations(1, limit=1, include_sensitive=True)
    page2 = list_obligations(1, limit=1, cursor=page1["next_cursor"], include_sensitive=True)

    assert page1["total"] == page2["total"] == 3
    assert page1["open_cents"] == page2["open_cents"]
    assert page1["items"][0]["key"] != page2["items"][0]["key"]


def test_pagination_reaches_last_page_with_no_next_cursor(obligations_db):
    _partial_entry()
    _two_installment_loan()

    seen = []
    cursor = None
    for _ in range(10):
        page = list_obligations(1, limit=1, cursor=cursor, include_sensitive=True)
        seen.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 3
    assert len({item["key"] for item in seen}) == 3


# --- Filters -----------------------------------------------------------------


def test_search_filters_by_description(obligations_db):
    _partial_entry()
    _two_installment_loan()

    result = list_obligations(1, q="aluguel", include_sensitive=True)
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "entry"

    result = list_obligations(1, q="banco local", include_sensitive=True)
    assert len(result["items"]) == 2
    assert all(i["kind"] == "loan_installment" for i in result["items"])


def test_combinable_filters_kind_status_and_due_range(obligations_db):
    _partial_entry()
    _two_installment_loan()

    result = list_obligations(
        1,
        kind="loan_installment",
        status="open",
        due_from=date(2026, 10, 1),
        due_to=date(2026, 10, 31),
        include_sensitive=True,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["due_date"] == "2026-10-10"


def test_kind_filter_selects_only_entries(obligations_db):
    _partial_entry()
    _two_installment_loan()

    result = list_obligations(1, kind="entry", include_sensitive=True)
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "entry"


# --- Cursor validation ---------------------------------------------------


def test_invalid_cursor_raises_value_error(obligations_db):
    _partial_entry()
    with pytest.raises(ValueError):
        list_obligations(1, cursor="not-a-valid-cursor!!", include_sensitive=True)


def test_tampered_cursor_raises_value_error(obligations_db):
    _partial_entry()
    with pytest.raises(ValueError):
        list_obligations(1, cursor="AAAA====", include_sensitive=True)


# --- Sensitive gating ------------------------------------------------------


def test_sensitive_entry_excluded_from_items_and_totals_by_default(obligations_db):
    sensitive_account = accounts.account_by_key(COMPANY, "salaries")
    assert sensitive_account["sensitive"] is True
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    _partial_entry()

    without_sensitive = list_obligations(1, include_sensitive=False)
    assert len(without_sensitive["items"]) == 1
    assert without_sensitive["total"] == 1
    assert without_sensitive["open_cents"] == 60_000

    with_sensitive = list_obligations(1, include_sensitive=True)
    assert with_sensitive["total"] == 2
    assert with_sensitive["open_cents"] == 60_000 + 50_000


# --- Detail (get_obligation) ------------------------------------------------


def test_get_obligation_entry_detail(obligations_db):
    entry = _partial_entry()
    item = get_obligation(1, "entry", entry["id"], include_sensitive=True)
    assert item["key"] == f"entry:{entry['id']}"
    assert item["open_cents"] == 60_000
    assert item["status"] == "partially_paid"


def test_get_obligation_preserves_access_to_paid_installment_history(obligations_db):
    loan = _two_installment_loan()
    position = loans.loan_position(loan["id"])
    first = position["installments"][0]
    loans.pay_installment(
        first["id"], principal_cents=100_000, interest_cents=5_000, paid_at=date(2026, 9, 10),
    )

    # Paid installment must not appear in the list anymore...
    result = list_obligations(1, include_sensitive=True)
    assert all(item["id"] != str(first["id"]) for item in result["items"])

    # ...but its detail must still be reachable for history/audit purposes.
    item = get_obligation(1, "loan_installment", first["id"], include_sensitive=True)
    assert item is not None
    assert item["status"] == "paid"
    assert item["paid_cents"] == 105_000
    assert item["open_cents"] == 0


def test_get_obligation_returns_none_for_unknown_id(obligations_db):
    assert get_obligation(1, "entry", 999999, include_sensitive=True) is None


def test_get_obligation_hides_sensitive_item_without_permission(obligations_db):
    sensitive_account = accounts.account_by_key(COMPANY, "salaries")
    entry = create_entry(EntryCommand(
        company_id=COMPANY, account_id=sensitive_account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 15), source="manual",
        external_id=None, description="Folha de pagamento",
    ))
    assert get_obligation(1, "entry", entry["id"], include_sensitive=False) is None
    assert get_obligation(1, "entry", entry["id"], include_sensitive=True) is not None


# --- IDs stay strings, even past JS's 2**53 safe-integer limit -------------


def test_ids_are_strings_even_beyond_js_safe_integer(obligations_db, monkeypatch):
    from backend.finance import entries as entries_module

    big_id = 9_007_199_254_740_993  # 2**53 + 1
    monkeypatch.setattr(entries_module, "_new_id", lambda: big_id)
    account = accounts.account_by_key(COMPANY, "rent")
    entry = create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=10_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))
    assert entry["id"] == big_id

    result = list_obligations(1, include_sensitive=True)
    item = next(i for i in result["items"] if i["key"] == f"entry:{big_id}")
    assert isinstance(item["id"], str)
    assert item["id"] == str(big_id)
