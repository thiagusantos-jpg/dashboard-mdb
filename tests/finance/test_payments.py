from __future__ import annotations

import threading
from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, ledger, loans
from backend.finance.entries import EntryCommand, create_entry, get_entry
from backend.finance.payments import (
    CASH_LINK_REQUIRED_MESSAGE,
    PaymentCashLinkRequiredError,
    PaymentConflictError,
    PaymentNotFoundError,
    PaymentValidationError,
    backfill_legacy_payments,
    record_payment,
)


COMPANY = 1


@pytest.fixture
def payments_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "payments.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def bank_account(name="Banco X"):
    return ledger.create_account(COMPANY, name, "bank")


def expense_entry(amount_cents=100_000):
    account = accounts.account_by_key(COMPANY, "rent")
    return create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=amount_cents,
        competence="2026-09", due_date=date(2026, 9, 20), source="manual",
        external_id=None, description="Aluguel",
    ))


def two_installment_loan(principal=200_000, interest=5_000):
    # Due dates are deliberately in the future relative to the "today" used
    # by obligations._compute_status (2026-09-12 in this suite's fixtures),
    # so a partially-paid installment's derived status stays 'partially_paid'
    # instead of being overridden to 'overdue' — keeping these tests focused
    # on the payment-balance behavior rather than overdue-date computation.
    return loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=principal, net_disbursement_cents=principal - 5_000,
        installments=[
            {"number": 1, "due_date": "2026-10-10", "principal_cents": principal // 2, "interest_cents": interest},
            {"number": 2, "due_date": "2026-11-10", "principal_cents": principal // 2, "interest_cents": interest},
        ],
        start_date="2026-09-01",
    )


# --- Brief's exact scenario: idempotent replay + cash link -----------------


def test_double_submission_is_idempotent_and_links_cash(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)

    first = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )
    second = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )

    assert first["payment_id"] == second["payment_id"]
    assert first["obligation"]["open_cents"] == 60_000
    assert ledger.account_balance(bank["id"]) == -40_000


def test_replay_only_happens_once_at_the_database_level(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)

    record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )
    record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )

    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM obligation_payments WHERE company=? AND idempotency_key=?",
            (1, "test-1"),
        ).fetchone()["n"]
    assert count == 1
    # Ledger only shows ONE outflow, not two.
    assert ledger.account_balance(bank["id"]) == -40_000


def test_same_key_different_body_conflicts(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)

    record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )
    with pytest.raises(PaymentConflictError):
        record_payment(
            1, "entry", entry["id"], amount_cents=50_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
        )


def test_replay_returns_original_response_even_with_stale_version(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)

    first = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )
    # A second, unrelated payment moves the entry's version forward.
    record_payment(
        1, "entry", entry["id"], amount_cents=20_000, paid_at=date(2026, 9, 13),
        expected_version=2, idempotency_key="test-2", cash_account_id=bank["id"],
    )
    # Replaying the first key with expected_version=1 (now stale) must still
    # return the ORIGINAL response, never re-checking the version.
    replay = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="test-1", cash_account_id=bank["id"],
    )
    assert replay["payment_id"] == first["payment_id"]


# --- RED-test scenario: injected cash-write failure --------------------


def test_injected_cash_failure_rolls_back_everything_then_retry_succeeds(payments_db):
    import backend.finance.payments as payments_module

    bank = bank_account()
    entry = expense_entry(100_000)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated cash-write failure")

    # Patched/restored manually (not via the pytest `monkeypatch` fixture):
    # that fixture is shared with `payments_db` above (same instance, since
    # fixtures are resolved once per test), so `monkeypatch.undo()` here
    # would also undo `payments_db`'s DB_PATH/PG patches and point the
    # "retry" call at a different, uninitialized database.
    original = payments_module.post_cash_event
    payments_module.post_cash_event = boom
    try:
        with pytest.raises(RuntimeError):
            record_payment(
                1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
                expected_version=1, idempotency_key="retry-1", cash_account_id=bank["id"],
            )
    finally:
        payments_module.post_cash_event = original

    # Nothing must have been persisted: entry untouched, no cash event, no
    # obligation_payments row for the failed attempt's idempotency key.
    reloaded = get_entry(entry["id"])
    assert reloaded["version"] == 1
    assert reloaded["open_cents"] == 100_000
    assert reloaded["status"] == "open"
    assert ledger.account_balance(bank["id"]) == 0
    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM obligation_payments WHERE idempotency_key=?",
            ("retry-1",),
        ).fetchone()["n"]
    assert count == 0

    # Resubmission (retry) with the SAME idempotency key now succeeds, since
    # the failed attempt left no trace to conflict with.
    result = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="retry-1", cash_account_id=bank["id"],
    )
    assert result["obligation"]["open_cents"] == 60_000
    assert ledger.account_balance(bank["id"]) == -40_000


# --- Validation rules --------------------------------------------------


def test_requires_exactly_one_cash_link(payments_db):
    entry = expense_entry(100_000)
    with pytest.raises(PaymentCashLinkRequiredError) as excinfo:
        record_payment(
            1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1",
        )
    assert excinfo.value.message == CASH_LINK_REQUIRED_MESSAGE

    bank = bank_account()
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k2",
            cash_account_id=bank["id"], existing_cash_event_id=1,
        )


def test_amount_exceeding_open_balance_is_rejected(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "entry", entry["id"], amount_cents=150_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1", cash_account_id=bank["id"],
        )


def test_stale_version_conflicts(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="k1", cash_account_id=bank["id"],
    )
    with pytest.raises(PaymentConflictError):
        record_payment(
            1, "entry", entry["id"], amount_cents=10_000, paid_at=date(2026, 9, 13),
            expected_version=1, idempotency_key="k2", cash_account_id=bank["id"],
        )


def test_unknown_obligation_raises_not_found(payments_db):
    bank = bank_account()
    with pytest.raises(PaymentNotFoundError):
        record_payment(
            1, "entry", 999_999, amount_cents=1_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1", cash_account_id=bank["id"],
        )


def test_entry_kind_rejects_principal_interest_split(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1", cash_account_id=bank["id"],
            principal_cents=20_000, interest_cents=20_000,
        )


# --- existing_cash_event_id path ----------------------------------------


def test_links_to_existing_unallocated_cash_event_without_creating_a_new_one(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    # Simulate an already-imported bank transaction: a cash_events row that
    # exists independently of any obligation payment.
    imported = ledger.post_cash_event(
        1, bank["id"], -40_000, date(2026, 9, 12), "Débito importado do extrato"
    )

    result = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="k1",
        existing_cash_event_id=imported["id"],
    )

    assert result["cash_event_id"] == str(imported["id"])
    # Only the ONE pre-existing outflow — record_payment must not create a
    # second cash movement.
    assert ledger.account_balance(bank["id"]) == -40_000
    with db.connection() as conn:
        row = conn.execute(
            "SELECT owns_cash_event,cash_event_id FROM obligation_payments WHERE idempotency_key=?",
            ("k1",),
        ).fetchone()
    assert row["owns_cash_event"] == 0
    assert row["cash_event_id"] == imported["id"]


def test_existing_cash_event_must_match_amount(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    imported = ledger.post_cash_event(1, bank["id"], -30_000, date(2026, 9, 12), "Débito")
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1",
            existing_cash_event_id=imported["id"],
        )


def test_existing_cash_event_cannot_be_reused_for_a_second_payment(payments_db):
    bank = bank_account()
    entry1 = expense_entry(100_000)
    entry2 = expense_entry(100_000)
    imported = ledger.post_cash_event(1, bank["id"], -40_000, date(2026, 9, 12), "Débito")

    record_payment(
        1, "entry", entry1["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="k1", existing_cash_event_id=imported["id"],
    )
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "entry", entry2["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k2", existing_cash_event_id=imported["id"],
        )


def test_existing_cash_event_cannot_be_reversed(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    imported = ledger.post_cash_event(1, bank["id"], -40_000, date(2026, 9, 12), "Débito")
    ledger.reverse_event(imported["id"], reason="Erro de importação")

    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1", existing_cash_event_id=imported["id"],
        )


# --- loan_installment payments (principal/interest split, partial) --------


def test_loan_installment_full_payment_creates_interest_entry_once(payments_db):
    bank = bank_account()
    loan = two_installment_loan(principal=200_000, interest=5_000)
    installment = loans.loan_position(loan["id"])["installments"][0]

    result = record_payment(
        1, "loan_installment", installment["id"], amount_cents=105_000,
        paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="k1",
        cash_account_id=bank["id"], principal_cents=100_000, interest_cents=5_000,
    )

    assert result["obligation"]["status"] == "paid"
    assert result["obligation"]["open_cents"] == 0
    assert ledger.account_balance(bank["id"]) == -105_000

    with db.connection() as conn:
        interest_entries = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_entries WHERE source='loan' AND external_id=?",
            (f"loan-installment:{installment['id']}",),
        ).fetchone()["n"]
    assert interest_entries == 1


def test_loan_installment_partial_payment_does_not_mark_paid(payments_db):
    bank = bank_account()
    loan = two_installment_loan(principal=200_000, interest=5_000)
    installment = loans.loan_position(loan["id"])["installments"][0]

    first = record_payment(
        1, "loan_installment", installment["id"], amount_cents=50_000,
        paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="k1",
        cash_account_id=bank["id"], principal_cents=50_000, interest_cents=0,
    )
    assert first["obligation"]["status"] == "partially_paid"
    assert first["obligation"]["open_cents"] == 55_000

    second = record_payment(
        1, "loan_installment", installment["id"], amount_cents=55_000,
        paid_at=date(2026, 9, 11), expected_version=first["obligation"]["version"],
        idempotency_key="k2", cash_account_id=bank["id"],
        principal_cents=50_000, interest_cents=5_000,
    )
    assert second["obligation"]["status"] == "paid"
    assert second["obligation"]["open_cents"] == 0
    assert ledger.account_balance(bank["id"]) == -105_000

    with db.connection() as conn:
        interest_entries = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_entries WHERE source='loan' AND external_id=?",
            (f"loan-installment:{installment['id']}",),
        ).fetchone()["n"]
    # Interest was only ever paid once (on the second call), so exactly one
    # interest entry — never one per partial-payment call.
    assert interest_entries == 1


def test_loan_installment_requires_principal_plus_interest_equal_total(payments_db):
    bank = bank_account()
    loan = two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "loan_installment", installment["id"], amount_cents=105_000,
            paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="k1",
            cash_account_id=bank["id"], principal_cents=100_000, interest_cents=4_000,
        )


def test_loan_installment_component_balances_cannot_be_exceeded(payments_db):
    bank = bank_account()
    loan = two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    with pytest.raises(PaymentValidationError):
        record_payment(
            1, "loan_installment", installment["id"], amount_cents=105_000,
            paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="k1",
            cash_account_id=bank["id"], principal_cents=101_000, interest_cents=4_000,
        )


# --- Legacy routes' behavior is exercised at the API layer, but the core
# rule (no cash link -> reject) is also directly testable here. ------------


def test_missing_cash_link_message_matches_brief_text(payments_db):
    entry = expense_entry(100_000)
    with pytest.raises(PaymentCashLinkRequiredError) as excinfo:
        record_payment(
            1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
            expected_version=1, idempotency_key="k1",
        )
    assert str(excinfo.value) == "Atualize a página para registrar a conta de pagamento."


# --- Backfill ---------------------------------------------------------


def test_backfill_is_idempotent_across_two_runs(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    from backend.finance.entries import settle_entry
    settle_entry(entry["id"], 40_000, paid_at=date(2026, 9, 12))

    loan = two_installment_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment_legacy_unsafe(
        installment["id"], principal_cents=100_000, interest_cents=5_000,
        paid_at=date(2026, 9, 10),
    )

    first_run = backfill_legacy_payments(1)
    assert first_run == {"entries": 1, "installments": 1}

    with db.connection() as conn:
        rows_after_first = conn.execute(
            "SELECT id,idempotency_key,amount_cents FROM obligation_payments ORDER BY idempotency_key"
        ).fetchall()
    ids_after_first = {row["idempotency_key"]: (row["id"], row["amount_cents"]) for row in rows_after_first}

    second_run = backfill_legacy_payments(1)
    assert second_run == {"entries": 0, "installments": 0}

    with db.connection() as conn:
        rows_after_second = conn.execute(
            "SELECT id,idempotency_key,amount_cents FROM obligation_payments ORDER BY idempotency_key"
        ).fetchall()
    ids_after_second = {row["idempotency_key"]: (row["id"], row["amount_cents"]) for row in rows_after_second}

    assert ids_after_first == ids_after_second
    keys = set(ids_after_first)
    assert any(k.startswith("legacy:event:") for k in keys)
    assert any(k.startswith("legacy:installment:") for k in keys)


def test_backfill_leaves_cash_event_null(payments_db):
    entry = expense_entry(100_000)
    from backend.finance.entries import settle_entry
    settle_entry(entry["id"], 40_000, paid_at=date(2026, 9, 12))

    backfill_legacy_payments(1)

    with db.connection() as conn:
        event_id = conn.execute(
            "SELECT id FROM financial_events WHERE entry_id=? AND event_type='settled'",
            (entry["id"],),
        ).fetchone()["id"]
        row = conn.execute(
            "SELECT cash_event_id,owns_cash_event FROM obligation_payments WHERE idempotency_key=?",
            (f"legacy:event:{event_id}",),
        ).fetchone()
    assert row["cash_event_id"] is None
    assert row["owns_cash_event"] == 0


# --- Concurrency proxy on SQLite (documented limitation: see task report) --


def test_two_concurrent_payments_exceeding_balance_only_one_confirms(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)

    results = {}
    errors = {}
    barrier = threading.Barrier(2)

    def attempt(name, key):
        try:
            barrier.wait(timeout=5)
        except threading.BrokenBarrierError:
            pass
        try:
            results[name] = record_payment(
                1, "entry", entry["id"], amount_cents=60_000, paid_at=date(2026, 9, 12),
                expected_version=1, idempotency_key=key, cash_account_id=bank["id"],
            )
        except Exception as exc:  # noqa: BLE001
            errors[name] = exc

    t1 = threading.Thread(target=attempt, args=("a", "concurrent-a"))
    t2 = threading.Thread(target=attempt, args=("b", "concurrent-b"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results) == 1, f"expected exactly one winner, got results={results} errors={errors}"
    assert len(errors) == 1
    assert isinstance(errors[list(errors)[0]], PaymentConflictError)

    reloaded = get_entry(entry["id"])
    assert reloaded["open_cents"] == 40_000
    assert ledger.account_balance(bank["id"]) == -60_000
