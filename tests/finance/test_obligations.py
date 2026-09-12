from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend import database as db
from backend.finance import accounts, ledger, loans
from backend.finance.entries import EntryCommand, create_entry, settle_entry
from backend.finance.obligations import get_obligation, list_obligations
from backend.finance.payments import record_payment


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
    loans.pay_installment_legacy_unsafe(
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


# --- Nature filtering (Fix round 1 / Finding 1) -----------------------------
# Obligations are exclusively "contas a pagar". A manual entry booked against
# a revenue- or financing_inflow-nature account (e.g. an unpaid confirmed
# sale posted to the `sales` account) must never surface as a payable — this
# mirrors entries.py::result_for (filters TO expense natures) and
# entries.py::cash_for (filters OUT revenue/financing_inflow natures).


def _revenue_entry():
    account = accounts.account_by_key(COMPANY, "sales")
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=75_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Venda a receber",
    ))


def test_revenue_nature_entry_excluded_from_list(obligations_db):
    _revenue_entry()
    _partial_entry()

    result = list_obligations(1, include_sensitive=True)
    assert result["total"] == 1
    assert all(item["kind"] != "entry" or "Venda" not in item["description"] for item in result["items"])


def test_financing_inflow_nature_entry_excluded_from_list(obligations_db):
    account = accounts.account_by_key(COMPANY, "loan_proceeds")
    create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=50_000,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Recebimento de empréstimo",
    ))
    result = list_obligations(1, include_sensitive=True)
    assert result["total"] == 0


def test_revenue_nature_entry_excluded_from_get_obligation(obligations_db):
    entry = _revenue_entry()
    assert get_obligation(1, "entry", entry["id"], include_sensitive=True) is None


# --- Computed overdue status (Fix round 1 / Finding 2) ----------------------
# "Status de atraso é calculado por data civil e saldo; preservar estado
# original no banco" — overdue is a read-time derived value, never persisted.


def _past_due_entry():
    account = accounts.account_by_key(COMPANY, "rent")
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=30_000,
        competence="2026-01", source="manual", external_id=None,
        due_date=date.today() - timedelta(days=1), description="Aluguel atrasado",
    ))


def test_past_due_open_entry_reports_overdue_in_list(obligations_db):
    entry = _past_due_entry()

    result = list_obligations(1, include_sensitive=True)
    item = next(i for i in result["items"] if i["id"] == str(entry["id"]))
    assert item["status"] == "overdue"


def test_status_filter_overdue_matches_computed_status_not_raw_db_value(obligations_db):
    _past_due_entry()

    overdue_result = list_obligations(1, status="overdue", include_sensitive=True)
    assert len(overdue_result["items"]) == 1

    open_result = list_obligations(1, status="open", include_sensitive=True)
    assert len(open_result["items"]) == 0


def test_past_due_entry_reports_overdue_in_get_obligation(obligations_db):
    entry = _past_due_entry()
    item = get_obligation(1, "entry", entry["id"], include_sensitive=True)
    assert item["status"] == "overdue"


def test_past_due_loan_installment_reports_overdue(obligations_db):
    loan = loans.create_loan(
        COMPANY, lender="Banco Atrasado", purpose="Capital de giro",
        principal_cents=100_000, net_disbursement_cents=98_000,
        installments=[
            {
                "number": 1,
                "due_date": (date.today() - timedelta(days=3)).isoformat(),
                "principal_cents": 100_000, "interest_cents": 2_000,
            },
        ],
        start_date=(date.today() - timedelta(days=30)).isoformat(),
    )
    position = loans.loan_position(loan["id"])
    installment_id = position["installments"][0]["id"]

    result = list_obligations(1, include_sensitive=True)
    item = next(i for i in result["items"] if i["id"] == str(installment_id))
    assert item["status"] == "overdue"

    detail = get_obligation(1, "loan_installment", installment_id, include_sensitive=True)
    assert detail["status"] == "overdue"

    overdue_result = list_obligations(1, status="overdue", include_sensitive=True)
    assert any(i["id"] == str(installment_id) for i in overdue_result["items"])
    open_result = list_obligations(1, status="open", include_sensitive=True)
    assert all(i["id"] != str(installment_id) for i in open_result["items"])


def test_future_due_entry_does_not_report_overdue(obligations_db):
    # `_partial_entry()`'s due_date (2026-09-20) is not yet past "today" —
    # consistent with every other fixed-date fixture in this file (e.g.
    # `test_get_obligation_entry_detail` already asserts a bare
    # "partially_paid" for it with no overdue branch).
    entry = _partial_entry()
    result = list_obligations(1, include_sensitive=True)
    item = next(i for i in result["items"] if i["id"] == str(entry["id"]))
    assert item["status"] == "partially_paid"


# --- Partially-paid loan installments (B3 follow-up fix) --------------------
# Task B3 added real partial-payment support for loan installments
# (backend/finance/payments.py::record_payment), but obligations.py was left
# untouched per that task's scope — leaving a partially-paid installment
# (real, nonzero open_cents) silently dropped from list_obligations (still
# filtered to status='open') and its paid_cents miscomputed in both
# list_obligations and get_obligation. This section proves that gap is
# closed: a partially-paid installment stays visible with the correct
# open_cents/paid_cents/status in both the list and detail views, and that a
# fully-paid installment's `version` field reflects the real stored column
# (bumped by the payment), not the old hardcoded placeholder of 1.


def _bank_account():
    return ledger.create_account(COMPANY, "Banco X", "bank")


def _future_two_installment_loan():
    # Due dates deliberately in the future relative to "today" (mirrors
    # tests/finance/test_payments.py::two_installment_loan) so the derived
    # 'overdue' status (obligations._compute_status) never kicks in here —
    # unlike _two_installment_loan() above, whose first installment
    # (due_date="2026-09-10") is in the past as of this suite's "today" and
    # would otherwise make a partially-paid balance report 'overdue' instead
    # of 'partially_paid', which is not what these tests are about.
    return loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=200_000, net_disbursement_cents=195_000,
        installments=[
            {"number": 1, "due_date": "2026-10-10", "principal_cents": 100_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-11-10", "principal_cents": 100_000, "interest_cents": 5_000},
        ],
        start_date="2026-09-01",
    )


def test_partially_paid_installment_appears_in_list_with_correct_balances(obligations_db):
    bank = _bank_account()
    loan = _future_two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    assert installment["id"] is not None

    result = record_payment(
        COMPANY, "loan_installment", installment["id"], amount_cents=50_000,
        paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="partial-1",
        cash_account_id=bank["id"], principal_cents=50_000, interest_cents=0,
    )
    assert result["obligation"]["status"] == "partially_paid"

    listed = list_obligations(COMPANY, include_sensitive=True)
    item = next(i for i in listed["items"] if i["id"] == str(installment["id"]))
    # Must NOT have silently dropped off the payable list.
    assert item["status"] == "partially_paid"
    assert item["paid_cents"] == 50_000
    assert item["open_cents"] == 105_000 - 50_000
    assert item["total_cents"] == 105_000

    # The still-open balance must be reflected in the aggregate total too.
    other_installment_open = 105_000  # the untouched second installment
    assert listed["open_cents"] == (105_000 - 50_000) + other_installment_open


def test_get_obligation_on_partially_paid_installment_returns_correct_values(obligations_db):
    bank = _bank_account()
    loan = _future_two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]

    record_payment(
        COMPANY, "loan_installment", installment["id"], amount_cents=50_000,
        paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="partial-2",
        cash_account_id=bank["id"], principal_cents=50_000, interest_cents=0,
    )

    detail = get_obligation(COMPANY, "loan_installment", installment["id"], include_sensitive=True)
    assert detail is not None
    assert detail["status"] == "partially_paid"
    assert detail["paid_cents"] == 50_000
    assert detail["open_cents"] == 105_000 - 50_000
    assert detail["allowed_actions"] == ["details", "pay"]


def test_fully_paid_installment_version_reflects_real_stored_version(obligations_db):
    bank = _bank_account()
    loan = _future_two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    assert installment["id"] is not None

    first = record_payment(
        COMPANY, "loan_installment", installment["id"], amount_cents=50_000,
        paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="version-1",
        cash_account_id=bank["id"], principal_cents=50_000, interest_cents=0,
    )
    second = record_payment(
        COMPANY, "loan_installment", installment["id"], amount_cents=55_000,
        paid_at=date(2026, 9, 11), expected_version=first["obligation"]["version"],
        idempotency_key="version-2", cash_account_id=bank["id"],
        principal_cents=50_000, interest_cents=5_000,
    )
    assert second["obligation"]["status"] == "paid"
    # Two payments each bump loan_installments.version by 1 (starting at 1),
    # so the real stored value is 3 — not the old hardcoded placeholder of 1.
    assert second["obligation"]["version"] == 3

    detail = get_obligation(COMPANY, "loan_installment", installment["id"], include_sensitive=True)
    assert detail["version"] == 3
    assert detail["version"] != 1
