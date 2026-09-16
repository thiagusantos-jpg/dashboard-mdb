from __future__ import annotations

from pathlib import Path

from backend.integrations.stone_receivables import parse_conciliation_xml


FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_transactions_and_installments():
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()

    receivables = parse_conciliation_xml(content)

    assert len(receivables) == 3
    first = receivables[0]
    assert first.transaction_key == "NSU-0001"
    assert first.gross_cents == 1_000_00
    assert first.fee_cents == 25_00
    assert first.net_cents == 975_00
    assert first.settlement_date == "2026-09-12"


def test_multiple_installments_of_one_transaction_are_each_a_receivable():
    content = (FIXTURES / "stone-conciliation-sample.xml").read_bytes()

    receivables = [r for r in parse_conciliation_xml(content) if r.transaction_key == "NSU-0002"]

    assert len(receivables) == 2
    assert receivables[0].installment_number == 1
    assert receivables[1].installment_number == 2
    assert receivables[1].settlement_date == "2026-10-12"


def test_parses_the_portal_receivables_csv():
    from backend.integrations.stone_receivables import looks_like_receivables_csv, parse_receivables_csv

    content = (FIXTURES / "stone-recebiveis-sample.csv").read_bytes()
    assert looks_like_receivables_csv(content)
    rows = parse_receivables_csv(content)
    assert len(rows) == 7
    first = rows[0]
    assert (first.transaction_key, first.installment_number, first.settlement_date) == ("90000000000001", 1, "2026-09-10")
    assert (first.gross_cents, first.net_cents, first.fee_cents) == (92_98, 90_26, 2_72)
    elo = rows[2]
    assert (elo.gross_cents, elo.net_cents, elo.fee_cents, elo.advance_fee_cents) == (1_200_00, 1_170_00, 30_00, 12_00)
    cancel = rows[3]
    assert cancel.category == "Cancelamento" and cancel.transaction_key == "90000000000004:cancelamento"
    assert cancel.fee_cents == -7
    fee, *balances = rows[4:]
    assert fee.fee_cents == 0 and fee.net_cents == -90_00
    assert len({r.transaction_key for r in rows}) == 7, "rows without Stone ID still get distinct keys"


def test_a_csv_that_is_not_the_stone_report_is_rejected():
    import pytest
    from backend.integrations.stone_receivables import ReceivablesFileError, parse_receivables_csv

    with pytest.raises(ReceivablesFileError, match="relatório de recebíveis"):
        parse_receivables_csv("Data;Valor\n01/09/2026;10,00\n".encode())
