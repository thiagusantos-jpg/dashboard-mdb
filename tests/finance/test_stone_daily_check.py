from __future__ import annotations

import secrets
from datetime import date

import pytest

from backend import database as db
from backend.finance import ledger, reconciliation


COMPANY = 1
TODAY = date(2026, 9, 16)


@pytest.fixture
def stone(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "PG", False)
    monkeypatch.setattr(db.settings, "DB_PATH", tmp_path / "stone-daily.sqlite3")
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT INTO companies(id,name) VALUES(1,'Loja 1')")
    return ledger.create_account(COMPANY, "Stone", "payment")


def expect(account, day: str, *net_cents: int):
    with db.connection() as conn:
        for index, net in enumerate(net_cents):
            conn.execute(
                """
                INSERT INTO stone_receivables(
                    id,company,cash_account_id,transaction_key,installment_number,brand_id,
                    gross_cents,fee_cents,net_cents,settlement_date,fee_entry_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (secrets.randbits(62) or 1, COMPANY, account["id"], f"{day}-{index}-{net}", 1, "1",
                 net + 2, 2, net, day, None, db.now()),
            )


def credit(account, day: str, cents: int, description="LOJA - Elo | Débito"):
    return ledger.post_cash_event(COMPANY, account["id"], cents, date.fromisoformat(day), description)


def check(**kwargs):
    return reconciliation.stone_daily_check(
        COMPANY, date(2026, 9, 1), date(2026, 9, 20), today=TODAY, **kwargs,
    )


def by_day(result):
    return {d["settlement_date"]: d for d in result["days"]}


def test_a_day_whose_sales_sum_arrived_is_ok_even_with_many_sales(stone):
    expect(stone, "2026-09-10", 100_00, 250_50, 49_50)
    credit(stone, "2026-09-10", 400_00)

    day = by_day(check())["2026-09-10"]
    assert day["status"] == "ok"
    assert day["sales"] == 3
    assert day["received_cents"] == 400_00
    assert day["difference_cents"] == 0


def test_a_weekend_settlement_paid_on_monday_still_matches(stone):
    expect(stone, "2026-09-12", 300_00)  # sábado
    credit(stone, "2026-09-14", 300_00)  # segunda

    assert by_day(check())["2026-09-12"]["status"] == "ok"


def test_small_rounding_differences_fit_the_tolerance(stone):
    expect(stone, "2026-09-10", 300_00)
    credit(stone, "2026-09-10", 299_40)

    assert by_day(check())["2026-09-10"]["status"] == "ok"
    assert by_day(check(tolerance_cents=0))["2026-09-10"]["status"] == "divergent"


def test_a_credit_with_the_wrong_amount_is_divergent_with_the_gap(stone):
    expect(stone, "2026-09-10", 2_980_10)
    credit(stone, "2026-09-10", 2_750_10)

    day = by_day(check())["2026-09-10"]
    assert day["status"] == "divergent"
    assert day["difference_cents"] == -230_00
    assert day["credit"]["description"] == "LOJA - Elo | Débito"


def test_nothing_arrived_is_missing_only_after_the_grace_days(stone):
    expect(stone, "2026-09-05", 100_00)   # 5+3 < 16: já deveria ter caído
    expect(stone, "2026-09-14", 100_00)   # ainda pode cair até 17/09
    expect(stone, "2026-09-18", 100_00)   # futuro

    days = by_day(check())
    assert days["2026-09-05"]["status"] == "missing"
    assert days["2026-09-05"]["difference_cents"] == -100_00
    assert days["2026-09-14"]["status"] == "upcoming"
    assert days["2026-09-18"]["status"] == "upcoming"


def test_one_credit_is_never_counted_for_two_days(stone):
    expect(stone, "2026-09-09", 500_00)
    expect(stone, "2026-09-10", 500_00)
    credit(stone, "2026-09-10", 500_00)

    days = by_day(check())
    statuses = sorted(d["status"] for d in days.values())
    assert statuses == ["missing", "ok"]


def test_an_exact_match_is_not_stolen_by_a_neighbour_day_that_differs(stone):
    expect(stone, "2026-09-09", 700_00)
    expect(stone, "2026-09-10", 500_00)
    credit(stone, "2026-09-10", 500_00)
    credit(stone, "2026-09-11", 650_00)

    days = by_day(check())
    assert days["2026-09-10"]["status"] == "ok"
    assert days["2026-09-09"]["status"] == "divergent"
    assert days["2026-09-09"]["difference_cents"] == -50_00


def test_a_conta_stone_day_sums_its_card_credits_and_ignores_pix_and_the_reserve(stone):
    expect(stone, "2026-08-05", 1_500_00, 529_26)
    credit(stone, "2026-08-05", 748_73, "LOJA LTDA - Antecipação | Crédito")
    credit(stone, "2026-08-05", 352_84, "LOJA LTDA - Antecipação | Crédito")
    credit(stone, "2026-08-05", 291_75, "LOJA LTDA - Visa Electron | Débito")
    credit(stone, "2026-08-05", 547_81, "LOJA LTDA - Maestro | Débito")
    credit(stone, "2026-08-05", 88_13, "LOJA LTDA - Elo | Débito")
    credit(stone, "2026-08-05", 48_71, "FULANO DE TAL - Pix | Maquininha")
    credit(stone, "2026-08-05", 5_000_00, "LOJA LTDA - Reserva Stone")
    credit(stone, "2026-08-05", 300_00, "CLIENTE - Transferência | Pix")

    result = reconciliation.stone_daily_check(
        COMPANY, date(2026, 8, 1), date(2026, 8, 10), today=TODAY,
    )
    day = by_day(result)["2026-08-05"]
    assert day["status"] == "ok"
    assert day["received_cents"] == 2_029_26
    assert day["credit"]["count"] == 5
    assert day["credit"]["description"] == "5 créditos de cartão (Antecipação | Crédito, Elo | Débito, Maestro | Débito…)"


def test_a_short_day_of_card_credits_is_divergent_not_matched_to_a_pix(stone):
    expect(stone, "2026-08-05", 100_00)
    credit(stone, "2026-08-05", 80_00, "LOJA - Elo | Débito")
    credit(stone, "2026-08-05", 100_00, "FULANO - Pix | Maquininha")

    day = by_day(reconciliation.stone_daily_check(COMPANY, date(2026, 8, 1), date(2026, 8, 10), today=TODAY))["2026-08-05"]
    assert day["status"] == "divergent"
    assert day["difference_cents"] == -20_00


def test_another_bank_with_a_single_stone_deposit_still_matches(stone):
    bank = ledger.create_account(COMPANY, "Itaú", "bank")
    expect(bank, "2026-09-10", 400_00)
    credit(bank, "2026-09-10", 400_00, "TED STONE PAGAMENTOS SA")
    credit(bank, "2026-09-10", 400_00, "PIX RECEBIDO JOAO")

    day = by_day(check(cash_account_id=bank["id"]))["2026-09-10"]
    assert day["status"] == "ok"
    assert day["credit"]["description"] == "TED STONE PAGAMENTOS SA"


def test_an_account_with_unlabelled_credits_falls_back_to_single_credits(stone):
    bank = ledger.create_account(COMPANY, "Banco", "bank")
    expect(bank, "2026-09-10", 400_00)
    credit(bank, "2026-09-10", 400_00, "CREDITO EM CONTA")
    credit(bank, "2026-09-10", 70_00, "CREDITO EM CONTA")

    assert by_day(check(cash_account_id=bank["id"]))["2026-09-10"]["status"] == "ok"


def test_reversed_credits_and_other_accounts_are_ignored(stone):
    other = ledger.create_account(COMPANY, "Itaú", "bank")
    expect(stone, "2026-09-10", 400_00)
    wrong = credit(stone, "2026-09-10", 400_00)
    ledger.reverse_event(wrong["id"], reason="lançado errado")
    credit(other, "2026-09-10", 400_00)

    assert by_day(check())["2026-09-10"]["status"] == "missing"
    assert check(cash_account_id=other["id"])["days"] == []


def test_summary_counts_only_days_already_due(stone):
    expect(stone, "2026-09-02", 100_00)
    expect(stone, "2026-09-03", 200_00)
    expect(stone, "2026-09-19", 900_00)
    credit(stone, "2026-09-02", 100_00)
    credit(stone, "2026-09-03", 150_00)

    summary = check()["summary"]
    assert summary["expected_cents"] == 300_00
    assert summary["received_cents"] == 250_00
    assert summary["difference_cents"] == -50_00
    assert (summary["due_days"], summary["ok_days"], summary["divergent_days"]) == (2, 1, 1)
    assert summary["upcoming_days"] == 1
    assert summary["upcoming_cents"] == 900_00
    assert summary["ok_pct"] == 50
