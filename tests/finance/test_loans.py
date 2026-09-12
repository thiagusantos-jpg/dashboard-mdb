from __future__ import annotations

from datetime import date

import pytest

from backend import database as db
from backend.finance import loans
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

    loans.pay_installment(
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
    loans.pay_installment(
        installment["id"], principal_cents=33_333_00,
        interest_cents=6_00, paid_at=date(2026, 10, 10),
    )

    with pytest.raises(ValueError):
        loans.pay_installment(
            installment["id"], principal_cents=1_00,
            interest_cents=0, paid_at=date(2026, 10, 11),
        )


def test_renegotiation_closes_schedule_and_keeps_paid_installments(loan_db):
    loan = make_loan(principal=90_000_00, installments=3)
    first = loans.loan_position(loan["id"])["installments"][0]
    loans.pay_installment(
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

    disbursement = loans.record_disbursement(loan["id"], 98_500_00, date(2026, 9, 1))

    assert disbursement["loan_id"] == loan["id"]
    assert disbursement["amount_cents"] == 98_500_00
