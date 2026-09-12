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
