from __future__ import annotations

import threading
from datetime import date

import pytest

from backend import database as db
from backend.finance import ledger, loans
from backend.finance.payments import record_payment
from backend.finance.reporting import management_result


COMPANY = 1


@pytest.fixture
def loan_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "loans.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")


def make_loan(principal=90_000_00, installments=3):
    plan = [
        {
            "number": n,
            "due_date": f"2026-{9 + n:02}-10",
            "principal_cents": principal // installments,
            "interest_cents": 6_00,
        }
        for n in range(1, installments + 1)
    ]
    return loans.create_loan(
        COMPANY,
        lender="Banco Local",
        purpose="Capital de giro",
        principal_cents=principal,
        net_disbursement_cents=principal - 1_500_00,
        installments=plan,
        start_date="2026-09-01",
    )


def test_create_loan_registers_schedule_and_installments(loan_db):
    loan = make_loan()

    position = loans.loan_position(loan["id"])

    assert position["principal_cents"] == 90_000_00
    assert len(position["installments"]) == 3
    assert position["installments"][0]["status"] == "open"


def test_installment_splits_principal_and_interest(loan_db):
    loan = make_loan(principal=90_000_00, installments=3)
    installment = loans.loan_position(loan["id"])["installments"][0]

    loans.pay_installment_legacy_unsafe(
        installment["id"],
        principal_cents=30_000_00,
        interest_cents=6_00,
        paid_at=date(2026, 10, 10),
    )

    position = loans.loan_position(loan["id"])
    assert position["principal_cents"] == 90_000_00 - 30_000_00

    result = management_result(COMPANY, "2026-10")
    assert result["financial_expenses_cents"] == 6_00


def test_paying_an_installment_twice_fails(loan_db):
    loan = make_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment_legacy_unsafe(
        installment["id"], principal_cents=33_333_00,
        interest_cents=6_00, paid_at=date(2026, 10, 10),
    )

    with pytest.raises(ValueError):
        loans.pay_installment_legacy_unsafe(
            installment["id"], principal_cents=1_00,
            interest_cents=0, paid_at=date(2026, 10, 11),
        )


def test_renegotiation_closes_schedule_and_keeps_paid_installments(loan_db):
    loan = make_loan(principal=90_000_00, installments=3)
    first = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment_legacy_unsafe(
        first["id"], principal_cents=30_000_00, interest_cents=6_00,
        paid_at=date(2026, 10, 10),
    )

    new_plan = [
        {"number": 1, "due_date": "2026-12-10", "principal_cents": 60_000_00, "interest_cents": 9_00},
    ]
    loans.renegotiate(loan["id"], installments=new_plan, reason="Prazo estendido")

    position = loans.loan_position(loan["id"])
    paid = [i for i in position["installments"] if i["status"] == "paid"]
    open_ = [i for i in position["installments"] if i["status"] == "open"]
    assert len(paid) == 1
    assert paid[0]["id"] == first["id"]
    assert len(open_) == 1
    assert open_[0]["principal_cents"] == 60_000_00
    assert position["principal_cents"] == 60_000_00


def test_record_disbursement(loan_db):
    loan = make_loan()
    account = ledger.create_account(COMPANY, "Banco", "bank")

    disbursement = loans.record_disbursement(
        COMPANY, loan["id"], 98_500_00, date(2026, 9, 1),
        idempotency_key="disb-1", cash_account_id=account["id"],
    )

    assert disbursement["loan_id"] == str(loan["id"])
    assert disbursement["amount_cents"] == 98_500_00
    assert disbursement["cash_event_id"] is not None
    assert ledger.account_balance(account["id"]) == 98_500_00


def test_record_disbursement_links_existing_cash_event(loan_db):
    # "gerar caixa OU vincular crédito existente" — the other path.
    loan = make_loan()
    account = ledger.create_account(COMPANY, "Banco", "bank")
    event = ledger.post_cash_event(
        COMPANY, account["id"], 98_500_00, date(2026, 9, 1), "Crédito recebido"
    )

    disbursement = loans.record_disbursement(
        COMPANY, loan["id"], 98_500_00, date(2026, 9, 1),
        idempotency_key="disb-link", existing_cash_event_id=event["id"],
    )

    assert disbursement["cash_event_id"] == str(event["id"])
    # No SECOND cash movement was created for the linked path — the account's
    # balance is exactly the one pre-existing event, not doubled.
    assert ledger.account_balance(account["id"]) == 98_500_00


def test_record_disbursement_is_idempotent_on_replay(loan_db):
    loan = make_loan()
    account = ledger.create_account(COMPANY, "Banco", "bank")

    first = loans.record_disbursement(
        COMPANY, loan["id"], 50_000_00, date(2026, 9, 1),
        idempotency_key="disb-replay", cash_account_id=account["id"],
    )
    second = loans.record_disbursement(
        COMPANY, loan["id"], 50_000_00, date(2026, 9, 1),
        idempotency_key="disb-replay", cash_account_id=account["id"],
    )

    assert first == second
    # A genuine replay must not create a second cash movement.
    assert ledger.account_balance(account["id"]) == 50_000_00


def test_record_disbursement_rejects_conflicting_reuse_of_idempotency_key(loan_db):
    loan = make_loan()
    account = ledger.create_account(COMPANY, "Banco", "bank")
    loans.record_disbursement(
        COMPANY, loan["id"], 50_000_00, date(2026, 9, 1),
        idempotency_key="disb-conflict", cash_account_id=account["id"],
    )

    with pytest.raises(loans.LoanConflictError):
        loans.record_disbursement(
            COMPANY, loan["id"], 60_000_00, date(2026, 9, 1),
            idempotency_key="disb-conflict", cash_account_id=account["id"],
        )


def test_concurrent_disbursements_cannot_share_one_cash_event(loan_db):
    """Final review C4, disbursement side: two requests with DIFFERENT
    idempotency keys naming the SAME incoming bank movement for two DIFFERENT
    loans, racing so that both clear the application-level "already linked?"
    pre-check before either inserts. Migration 022's UNIQUE index on
    loan_disbursements(cash_event_id) is the actual guarantee; the barrier
    below pins the interleaving to that window, since
    `create_entry_on_connection` is the first WRITE record_disbursement
    performs on the linked path, right after the pre-check."""
    loan_a = make_loan()
    loan_b = make_loan()
    account = ledger.create_account(COMPANY, "Banco", "bank")
    event = ledger.post_cash_event(
        COMPANY, account["id"], 50_000_00, date(2026, 9, 1), "Crédito recebido"
    )

    real_create = loans.create_entry_on_connection
    gate = threading.Barrier(2, timeout=10)

    def gated_create(*args, **kwargs):
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        return real_create(*args, **kwargs)

    results = {}
    errors = {}

    def attempt(name, loan_id, key):
        try:
            results[name] = loans.record_disbursement(
                COMPANY, loan_id, 50_000_00, date(2026, 9, 1),
                idempotency_key=key, existing_cash_event_id=event["id"],
            )
        except Exception as exc:  # noqa: BLE001
            errors[name] = exc

    loans.create_entry_on_connection = gated_create
    try:
        t1 = threading.Thread(target=attempt, args=("a", loan_a["id"], "race-a"))
        t2 = threading.Thread(target=attempt, args=("b", loan_b["id"], "race-b"))
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
    finally:
        loans.create_entry_on_connection = real_create

    assert len(results) == 1, f"expected exactly one winner, got results={results} errors={errors}"
    assert isinstance(errors[list(errors)[0]], loans.LoanValidationError)

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT loan_id FROM loan_disbursements WHERE cash_event_id=?", (event["id"],)
        ).fetchall()
    assert len(rows) == 1
    # The loser rolled back completely: no second proceeds entry, and the one
    # pre-existing credit is still the account's only movement.
    assert ledger.account_balance(account["id"]) == 50_000_00
    with db.connection() as conn:
        proceeds_entries = conn.execute(
            "SELECT COUNT(*) AS n FROM financial_entries WHERE source='loan'"
        ).fetchone()["n"]
    assert proceeds_entries == 1


def test_record_disbursement_requires_a_cash_link(loan_db):
    loan = make_loan()
    with pytest.raises(loans.LoanValidationError):
        loans.record_disbursement(
            COMPANY, loan["id"], 50_000_00, date(2026, 9, 1),
            idempotency_key="disb-no-link",
        )


def test_create_loan_rejects_principal_sum_mismatch(loan_db):
    with pytest.raises(loans.LoanValidationError):
        loans.create_loan(
            COMPANY,
            lender="Banco Local",
            purpose="Capital de giro",
            principal_cents=90_000_00,
            net_disbursement_cents=88_000_00,
            installments=[
                {"number": 1, "due_date": "2026-10-10", "principal_cents": 40_000_00, "interest_cents": 6_00},
            ],
            start_date="2026-09-01",
        )


def test_create_loan_accepts_variable_interest_schedule(loan_db):
    # "cronograma com juros variáveis válido" — each installment carries a
    # different interest_cents; only the PRINCIPAL sum is checked against
    # the contract's principal_cents.
    loan = loans.create_loan(
        COMPANY,
        lender="Banco Local",
        purpose="Capital de giro",
        principal_cents=90_000_00,
        net_disbursement_cents=88_000_00,
        installments=[
            {"number": 1, "due_date": "2026-10-10", "principal_cents": 30_000_00, "interest_cents": 9_00},
            {"number": 2, "due_date": "2026-11-10", "principal_cents": 30_000_00, "interest_cents": 6_00},
            {"number": 3, "due_date": "2026-12-10", "principal_cents": 30_000_00, "interest_cents": 3_00},
        ],
        start_date="2026-09-01",
    )
    position = loans.loan_position(loan["id"])
    interests = [i["interest_cents"] for i in position["installments"]]
    assert interests == [9_00, 6_00, 3_00]


def test_renegotiate_rejects_principal_sum_not_matching_outstanding(loan_db):
    loan = make_loan(principal=90_000_00, installments=3)
    first = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment_legacy_unsafe(
        first["id"], principal_cents=30_000_00, interest_cents=6_00,
        paid_at=date(2026, 10, 10),
    )
    # Outstanding is 90_000_00 - 30_000_00 = 60_000_00 — anything else must
    # be rejected, per the brief's own assertion
    # (sum(new_schedule principal) == principal_outstanding).
    with pytest.raises(loans.LoanValidationError):
        loans.renegotiate(
            loan["id"],
            installments=[{"number": 1, "due_date": "2026-12-10", "principal_cents": 61_000_00, "interest_cents": 9_00}],
            reason="Tentativa inválida",
        )


def test_renegotiate_requires_a_reason(loan_db):
    loan = make_loan()
    with pytest.raises(loans.LoanValidationError):
        loans.renegotiate(
            loan["id"],
            installments=[{"number": 1, "due_date": "2026-12-10", "principal_cents": 90_000_00, "interest_cents": 9_00}],
            reason="   ",
        )


def test_renegotiate_twice_still_computes_outstanding_correctly(loan_db):
    # Self-review: a loan renegotiated MORE THAN ONCE — paid installments are
    # now spread across two closed schedules plus the active one; outstanding
    # must still be loan.principal_cents minus every paid_principal_cents
    # ever recorded for the loan, regardless of which schedule they sit on.
    loan = make_loan(principal=90_000_00, installments=3)
    first = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment_legacy_unsafe(
        first["id"], principal_cents=30_000_00, interest_cents=6_00,
        paid_at=date(2026, 10, 10),
    )
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-12-10", "principal_cents": 60_000_00, "interest_cents": 9_00}],
        reason="Primeira renegociação",
    )
    second_schedule_installment = loans.loan_position(loan["id"])["installments"][-1]
    loans.pay_installment_legacy_unsafe(
        second_schedule_installment["id"], principal_cents=20_000_00, interest_cents=9_00,
        paid_at=date(2026, 12, 10),
    )
    # Outstanding is now 90_000_00 - 30_000_00 - 20_000_00 = 40_000_00.
    with pytest.raises(loans.LoanValidationError):
        loans.renegotiate(
            loan["id"],
            installments=[{"number": 1, "due_date": "2027-01-10", "principal_cents": 41_000_00, "interest_cents": 5_00}],
            reason="Segunda renegociação — valor errado",
        )
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2027-01-10", "principal_cents": 40_000_00, "interest_cents": 5_00}],
        reason="Segunda renegociação — valor correto",
    )
    position = loans.loan_position(loan["id"])
    open_ = [i for i in position["installments"] if i["status"] == "open"]
    assert len(open_) == 1
    assert open_[0]["principal_cents"] == 40_000_00


def test_patch_loan_updates_lender_and_purpose(loan_db):
    loan = make_loan()
    updated = loans.update_loan_details(
        COMPANY, loan["id"], {"lender": "Novo Banco", "purpose": "Novo motivo"},
        expected_version=loan["version"],
    )
    assert updated["loan"]["lender"] == "Novo Banco"
    assert updated["loan"]["purpose"] == "Novo motivo"


def test_patch_loan_rejects_stale_version(loan_db):
    loan = make_loan()
    loans.update_loan_details(
        COMPANY, loan["id"], {"lender": "Novo Banco"}, expected_version=loan["version"],
    )
    with pytest.raises(loans.LoanConflictError):
        loans.update_loan_details(
            COMPANY, loan["id"], {"lender": "Outro Banco"}, expected_version=loan["version"],
        )


def test_patch_loan_rejects_value_bearing_field(loan_db):
    loan = make_loan()
    with pytest.raises(loans.LoanValidationError):
        loans.update_loan_details(
            COMPANY, loan["id"], {"principal_cents": 100_00}, expected_version=loan["version"],
        )


def test_cancel_loan_without_movement_succeeds(loan_db):
    loan = make_loan()
    result = loans.cancel_loan(COMPANY, loan["id"], reason="Contrato não utilizado")
    assert result["loan"]["status"] == "cancelled"


def test_cancel_loan_requires_a_reason(loan_db):
    loan = make_loan()
    with pytest.raises(loans.LoanValidationError):
        loans.cancel_loan(COMPANY, loan["id"], reason="")


def test_cancel_loan_with_payment_is_rejected(loan_db):
    loan = make_loan()
    installment = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment_legacy_unsafe(
        installment["id"], principal_cents=30_000_00, interest_cents=6_00,
        paid_at=date(2026, 10, 10),
    )
    with pytest.raises(loans.LoanConflictError):
        loans.cancel_loan(COMPANY, loan["id"], reason="Tentativa inválida")


def test_cancel_loan_with_disbursement_and_no_payments_is_rejected(loan_db):
    # Self-review: a disbursement with ZERO payments is still "movement".
    loan = make_loan()
    account = ledger.create_account(COMPANY, "Banco", "bank")
    loans.record_disbursement(
        COMPANY, loan["id"], 90_000_00, date(2026, 9, 1),
        idempotency_key="disb-cancel-guard", cash_account_id=account["id"],
    )
    with pytest.raises(loans.LoanConflictError):
        loans.cancel_loan(COMPANY, loan["id"], reason="Tentativa inválida")


# --- Task B7 fix round 1: the brief's own literal `len(active_schedules)==1`
# assertion, checked directly against loan_schedules.status. Both existing
# adjacent tests (test_renegotiated_loan_shows_only_active_schedule_installment
# in test_forecast.py, and test_old_schedule_excluded_after_renegotiation in
# test_obligations.py) only prove installments filtered through
# `s.status='active'` become invisible — a second row left at
# `status='active'` after renegotiate/cancel would stay invisible to either
# query and both tests would still pass. These count `loan_schedules` rows
# directly to actually pin the invariant down.


def _active_schedule_count(loan_id):
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM loan_schedules WHERE loan_id=? AND status='active'",
            (loan_id,),
        ).fetchone()
    return row["c"]


def test_renegotiate_leaves_exactly_one_active_schedule(loan_db):
    loan = make_loan(principal=90_000_00, installments=3)
    assert _active_schedule_count(loan["id"]) == 1

    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-12-10", "principal_cents": 90_000_00, "interest_cents": 9_00}],
        reason="Prazo estendido",
    )

    # The brief's own literal assertion: exactly one active schedule after a
    # renegotiation — the old one must be flipped to 'closed', not merely
    # ignored by query filters, and not left duplicated as 'active'.
    assert _active_schedule_count(loan["id"]) == 1


def test_renegotiate_twice_leaves_exactly_one_active_schedule(loan_db):
    loan = make_loan(principal=90_000_00, installments=3)
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2026-12-10", "principal_cents": 90_000_00, "interest_cents": 9_00}],
        reason="Primeira renegociação",
    )
    loans.renegotiate(
        loan["id"],
        installments=[{"number": 1, "due_date": "2027-01-10", "principal_cents": 90_000_00, "interest_cents": 5_00}],
        reason="Segunda renegociação",
    )
    assert _active_schedule_count(loan["id"]) == 1


def test_cancel_loan_leaves_zero_active_schedules(loan_db):
    loan = make_loan()
    assert _active_schedule_count(loan["id"]) == 1

    loans.cancel_loan(COMPANY, loan["id"], reason="Contrato não utilizado")

    # cancel_loan flips the active schedule's status to 'cancelled' (see
    # backend/finance/loans.py::cancel_loan) — verify no schedule is left
    # 'active' after a successful cancellation.
    assert _active_schedule_count(loan["id"]) == 0


# --- Final review C3: loan_position with a partially-paid installment ------


def test_loan_position_reports_partially_paid_split(loan_db):
    """A real partial payment must show up on BOTH sides of the position.

    Before this fix both sums filtered on a status label that predates B3's
    `status='partially_paid'` (`paid_principal` counted only `=='paid'` rows,
    `open_principal` only `=='open'` rows), so a partially-paid installment
    matched neither and its money vanished from both: the position reported
    `principal_cents: 0, paid_principal_cents: 0` for a loan that plainly
    still owed money, and web/assets/loans.js rendered "Principal em aberto:
    R$ 0,00" from it.
    """
    bank = ledger.create_account(COMPANY, "Banco", "bank")
    loan = loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=100_000, net_disbursement_cents=95_000,
        installments=[{"number": 1, "due_date": "2026-10-10",
                       "principal_cents": 100_000, "interest_cents": 10_000}],
        start_date="2026-09-01",
    )
    installment = loans.loan_position(loan["id"])["installments"][0]

    record_payment(
        COMPANY, "loan_installment", installment["id"], amount_cents=44_000,
        paid_at=date(2026, 9, 12), expected_version=1, idempotency_key="partial-1",
        cash_account_id=bank["id"], principal_cents=40_000, interest_cents=4_000,
    )

    position = loans.loan_position(loan["id"])
    assert position["installments"][0]["status"] == "partially_paid"
    # 40_000 of the 100_000 principal is amortized; 60_000 is still owed.
    assert position["paid_principal_cents"] == 40_000
    assert position["principal_cents"] == 60_000
    # The two sides always add back up to the contract's principal.
    assert position["paid_principal_cents"] + position["principal_cents"] == 100_000


def test_loan_position_splits_stay_correct_after_full_payment(loan_db):
    # Companion to the test above: a fully-paid installment owes nothing and
    # contributes its whole principal to the paid side.
    bank = ledger.create_account(COMPANY, "Banco", "bank")
    loan = loans.create_loan(
        COMPANY, lender="Banco Local", purpose="Capital de giro",
        principal_cents=100_000, net_disbursement_cents=95_000,
        installments=[
            {"number": 1, "due_date": "2026-10-10", "principal_cents": 50_000, "interest_cents": 5_000},
            {"number": 2, "due_date": "2026-11-10", "principal_cents": 50_000, "interest_cents": 5_000},
        ],
        start_date="2026-09-01",
    )
    first = loans.loan_position(loan["id"])["installments"][0]
    record_payment(
        COMPANY, "loan_installment", first["id"], amount_cents=55_000,
        paid_at=date(2026, 9, 12), expected_version=1, idempotency_key="full-1",
        cash_account_id=bank["id"], principal_cents=50_000, interest_cents=5_000,
    )

    position = loans.loan_position(loan["id"])
    assert position["paid_principal_cents"] == 50_000
    assert position["principal_cents"] == 50_000
