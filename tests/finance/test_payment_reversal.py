from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import accounts, ledger, loans, reconciliation
from backend.finance.entries import EntryCommand, create_entry, get_entry, settle_entry
from backend.finance.payments import (
    PaymentConflictError,
    PaymentNotFoundError,
    PaymentValidationError,
    backfill_legacy_payments,
    record_payment,
    reverse_payment,
)


COMPANY = 1


@pytest.fixture
def payments_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "payment_reversal.sqlite3")
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
    return loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=principal, net_disbursement_cents=principal - 5_000,
        installments=[
            {"number": 1, "due_date": "2026-10-10", "principal_cents": principal // 2, "interest_cents": interest},
            {"number": 2, "due_date": "2026-11-10", "principal_cents": principal // 2, "interest_cents": interest},
        ],
        start_date="2026-09-01",
    )


def _payment_id_int(payment_id: str) -> int:
    return int(payment_id)


# --- Brief's exact scenario --------------------------------------------


def test_reverse_partial_payment_reopens_entry_and_zeroes_cash(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)

    paid = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    assert paid["obligation"]["version"] == 2

    result = reverse_payment(
        1, _payment_id_int(paid["payment_id"]), reason="Valor incorreto",
        reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
    )

    assert result["obligation"]["open_cents"] == 100_000
    assert result["obligation"]["status"] == "open"
    assert ledger.account_balance(bank["id"]) == 0

    reloaded = get_entry(entry["id"])
    assert reloaded["open_cents"] == 100_000
    assert reloaded["status"] == "open"

    # Reenvio do estorno (mesma chave) não cria segundo ajuste.
    replay = reverse_payment(
        1, _payment_id_int(paid["payment_id"]), reason="Valor incorreto",
        reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
    )
    assert replay == result
    assert ledger.account_balance(bank["id"]) == 0


# --- Partial loan installment payment reversal --------------------------


def test_reverse_partial_installment_payment_reopens_only_this_slice(payments_db):
    bank = bank_account()
    loan = two_installment_loan(principal=200_000, interest=5_000)
    installment = loans.loan_position(loan["id"])["installments"][0]

    first = record_payment(
        1, "loan_installment", installment["id"], amount_cents=50_000,
        paid_at=date(2026, 9, 10), expected_version=1, idempotency_key="k1",
        cash_account_id=bank["id"], principal_cents=50_000, interest_cents=0,
    )
    second = record_payment(
        1, "loan_installment", installment["id"], amount_cents=55_000,
        paid_at=date(2026, 9, 11), expected_version=first["obligation"]["version"],
        idempotency_key="k2", cash_account_id=bank["id"],
        principal_cents=50_000, interest_cents=5_000,
    )
    assert second["obligation"]["status"] == "paid"
    assert ledger.account_balance(bank["id"]) == -105_000

    # Reverse only the SECOND payment (the one carrying interest): the
    # installment must go back to partially_paid with exactly the first
    # payment's contribution still standing, and the shared interest entry
    # must be un-settled by exactly this payment's interest slice.
    result = reverse_payment(
        1, _payment_id_int(second["payment_id"]), reason="Valor pago errado",
        reversed_at=date(2026, 9, 11), expected_version=second["obligation"]["version"],
        idempotency_key="undo-2",
    )
    assert result["obligation"]["status"] == "partially_paid"
    assert result["obligation"]["open_cents"] == 55_000
    assert ledger.account_balance(bank["id"]) == -50_000

    with db.connection() as conn:
        row = conn.execute(
            "SELECT paid_principal_cents,paid_interest_cents FROM loan_installments WHERE id=?",
            (installment["id"],),
        ).fetchone()
    assert row["paid_principal_cents"] == 50_000
    assert row["paid_interest_cents"] == 0

    # The shared interest entry itself must be back to 'open' (its only
    # settlement was this reversed payment's 5_000).
    interest_entry_id = installment_row(installment["id"])["entry_id"]
    with db.connection() as conn:
        interest_status = conn.execute(
            "SELECT status FROM financial_entries WHERE id=?", (interest_entry_id,)
        ).fetchone()["status"]
    assert interest_status == "open"

    # Reversing the FIRST payment (principal-only) leaves the interest entry
    # untouched.
    reverse_payment(
        1, _payment_id_int(first["payment_id"]), reason="Também estava errado",
        reversed_at=date(2026, 9, 12), expected_version=result["obligation"]["version"],
        idempotency_key="undo-1",
    )
    with db.connection() as conn:
        row = conn.execute(
            "SELECT status,paid_principal_cents,paid_interest_cents FROM loan_installments WHERE id=?",
            (installment["id"],),
        ).fetchone()
    assert row["status"] == "open"
    assert row["paid_principal_cents"] == 0
    assert row["paid_interest_cents"] == 0
    assert ledger.account_balance(bank["id"]) == 0


def installment_row(installment_id):
    with db.connection() as conn:
        return dict(
            conn.execute(
                "SELECT * FROM loan_installments WHERE id=?", (installment_id,)
            ).fetchone()
        )


# --- Entry booked against a since-archived category is still reversible --


def test_reverse_payment_works_even_when_account_was_archived_afterward(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    paid = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )

    account = accounts.account_by_key(COMPANY, "rent")
    accounts.archive_account(COMPANY, account["id"], expected_version=account["version"])

    result = reverse_payment(
        1, _payment_id_int(paid["payment_id"]), reason="Categoria arquivada depois",
        reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
    )
    assert result["obligation"]["open_cents"] == 100_000
    assert result["obligation"]["status"] == "open"
    assert ledger.account_balance(bank["id"]) == 0


# --- Legacy payment with no cash link ------------------------------------


def test_reverse_legacy_payment_with_no_cash_link(payments_db):
    entry = expense_entry(100_000)
    settle_entry(entry["id"], 40_000, paid_at=date(2026, 9, 12))
    backfill_legacy_payments(COMPANY)

    with db.connection() as conn:
        legacy = conn.execute(
            "SELECT id FROM obligation_payments WHERE obligation_kind='entry' AND obligation_id=?",
            (entry["id"],),
        ).fetchone()
    reloaded = get_entry(entry["id"])

    result = reverse_payment(
        1, legacy["id"], reason="Corrigindo histórico legado",
        reversed_at=date(2026, 9, 12), expected_version=reloaded["version"],
        idempotency_key="undo-legacy",
    )
    assert result["cash_event_id"] is None
    assert result["obligation"]["open_cents"] == 100_000
    assert result["obligation"]["status"] == "open"


# --- Double reversal is rejected ------------------------------------------


def test_double_reversal_is_rejected(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    paid = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    reverse_payment(
        1, _payment_id_int(paid["payment_id"]), reason="Primeiro estorno",
        reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
    )
    with pytest.raises(PaymentConflictError):
        reverse_payment(
            1, _payment_id_int(paid["payment_id"]), reason="Segunda tentativa",
            reversed_at=date(2026, 9, 12), expected_version=1, idempotency_key="undo-2",
        )


# --- Intermediate failure rolls back everything ---------------------------


def test_injected_failure_rolls_back_everything_then_retry_succeeds(payments_db):
    import backend.finance.payments as payments_module

    bank = bank_account()
    entry = expense_entry(100_000)
    paid = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )

    def boom(*args, **kwargs):
        raise RuntimeError("simulated cash-reversal failure")

    original = payments_module.reverse_event
    payments_module.reverse_event = boom
    try:
        with pytest.raises(RuntimeError):
            reverse_payment(
                1, _payment_id_int(paid["payment_id"]), reason="Vai falhar no meio",
                reversed_at=date(2026, 9, 12), expected_version=2,
                idempotency_key="undo-retry-1",
            )
    finally:
        payments_module.reverse_event = original

    # Nothing must have been persisted: entry untouched, cash untouched, no
    # reversal recorded on the payment row.
    reloaded = get_entry(entry["id"])
    assert reloaded["version"] == 2
    assert reloaded["open_cents"] == 60_000
    assert reloaded["status"] == "partially_paid"
    assert ledger.account_balance(bank["id"]) == -40_000
    with db.connection() as conn:
        row = conn.execute(
            "SELECT reversed_at FROM obligation_payments WHERE id=?",
            (_payment_id_int(paid["payment_id"]),),
        ).fetchone()
    assert row["reversed_at"] is None

    # Retry with the SAME idempotency key now succeeds.
    result = reverse_payment(
        1, _payment_id_int(paid["payment_id"]), reason="Vai falhar no meio",
        reversed_at=date(2026, 9, 12), expected_version=2,
        idempotency_key="undo-retry-1",
    )
    assert result["obligation"]["open_cents"] == 100_000
    assert ledger.account_balance(bank["id"]) == 0


# --- Reconciliation lock ----------------------------------------------------


def test_reconciled_payment_cannot_be_reversed_until_undone(payments_db):
    bank = bank_account()
    # Due date close to paid_at so reconciliation.suggest's +-5 day window
    # around the cash event's occurred_at actually finds this entry as a
    # candidate.
    account = accounts.account_by_key(COMPANY, "rent")
    entry = create_entry(EntryCommand(
        company_id=COMPANY, account_id=account["id"], amount_cents=40_000,
        competence="2026-09", due_date=date(2026, 9, 12), source="manual",
        external_id=None, description="Aluguel",
    ))
    paid = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    cash_event_id = int(paid["cash_event_id"])
    group = reconciliation.suggest(COMPANY, cash_event_id)
    assert group["status"] == "auto_matched"

    with pytest.raises(PaymentConflictError):
        reverse_payment(
            1, _payment_id_int(paid["payment_id"]), reason="Tentando estornar conciliado",
            reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
        )

    reconciliation.undo(group["id"], reason="Conciliação errada")

    result = reverse_payment(
        1, _payment_id_int(paid["payment_id"]), reason="Agora pode",
        reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-2",
    )
    assert result["obligation"]["status"] == "open"


def test_payment_reconciled_via_payment_ids_path_cannot_be_reversed_until_undone(payments_db):
    """The reconciliation lock above only ever checked item_type='cash_event'
    anchored at the payment's OWN cash_event_id — the shape suggest()
    writes. A payment reconciled through the NEWER, caller-driven
    confirm(..., payment_ids=[...]) path (task B5) is anchored as
    item_type='payment' pointing at the payment's OWN id instead, against a
    DIFFERENT cash_event entirely — a shape the guard previously never
    checked, so reversal wrongly succeeded and left a stale
    reconciliation_links row pointing at a now-reversed payment."""
    bank = bank_account()
    entry = expense_entry(100_000)
    paid = record_payment(
        COMPANY, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 10),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    payment_id = _payment_id_int(paid["payment_id"])

    # A DIFFERENT, unrelated cash movement anchors the group — not the
    # payment's own cash_event_id — and this payment is linked into it as
    # one specific partial-payment slice.
    stray_debit = ledger.post_cash_event(
        COMPANY, bank["id"], -40_000, date(2026, 9, 11), "Débito a explicar"
    )
    group = reconciliation.suggest(COMPANY, stray_debit["id"])
    assert group["status"] == "unmatched"
    confirmed = reconciliation.confirm(group["id"], [], payment_ids=[payment_id])
    assert confirmed["status"] == "manual_matched"

    with pytest.raises(PaymentConflictError):
        reverse_payment(
            COMPANY, payment_id, reason="Tentando estornar conciliado via payment_ids",
            reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
        )

    reconciliation.undo(group["id"], reason="Conciliação errada")

    result = reverse_payment(
        COMPANY, payment_id, reason="Agora pode",
        reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-2",
    )
    assert result["obligation"]["status"] == "open"


# --- Validation --------------------------------------------------------


def test_reason_must_be_between_3_and_500_characters(payments_db):
    bank = bank_account()
    entry = expense_entry(100_000)
    paid = record_payment(
        1, "entry", entry["id"], amount_cents=40_000, paid_at=date(2026, 9, 12),
        expected_version=1, idempotency_key="pay-1", cash_account_id=bank["id"],
    )
    with pytest.raises(PaymentValidationError):
        reverse_payment(
            1, _payment_id_int(paid["payment_id"]), reason="oi",
            reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-1",
        )
    with pytest.raises(PaymentValidationError):
        reverse_payment(
            1, _payment_id_int(paid["payment_id"]), reason="x" * 501,
            reversed_at=date(2026, 9, 12), expected_version=2, idempotency_key="undo-2",
        )


def test_unknown_payment_raises_not_found(payments_db):
    with pytest.raises(PaymentNotFoundError):
        reverse_payment(
            1, 999_999, reason="Não existe",
            reversed_at=date(2026, 9, 12), expected_version=1, idempotency_key="undo-1",
        )
