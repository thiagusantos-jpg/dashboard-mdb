"""Unit tests for the pure business logic in backend/models.py.

These cover the reconciliation/dedup bugs found while running the first real
Mobne sync (see PLANO_IMPLEMENTACAO_MOBNE.md, "Testes obrigatórios") and the
core contract checks for receipts, aliasing, and aggregation.
"""
from __future__ import annotations
import copy
import pytest
from backend import models


def make_raw(**overrides):
    raw = {
        'CupomFiscalId': 1001,
        'CupomFiscalRefId': None,
        'EmpresaId': 218,
        'DtaMovimento': '2026-01-15T10:00:00',
        'VlrLiquido': 100.00,
        'Status': 'V',
        'Especie': 'CF',
        'CupomFiscalItem': [
            {'CupomItemId': 1, 'ProdutoId': 501, 'Status': 'V',
             'Quantidade': 2, 'VlrLiquido': 100.00, 'CustoVendaMedioLiquido': 30.00},
        ],
    }
    raw.update(overrides)
    return raw


def make_receipt(**overrides):
    return models.receipt(make_raw(**overrides), 218, '2026-01-01', '2026-01-31')


# --- number / cents / civil_date -------------------------------------------------

def test_number_rejects_missing_value():
    with pytest.raises(models.DataError):
        models.number(None)


def test_cents_rounds_half_up():
    assert models.cents('10.005') == 1001


def test_civil_date_converts_utc_to_brasilia_crossing_a_day_boundary():
    # 2026-02-01T02:30:00Z is 2026-01-31 23:30 in America/Sao_Paulo (UTC-3).
    assert models.civil_date('2026-02-01T02:30:00Z') == '2026-01-31'


# --- receipt() ---------------------------------------------------------------------

def test_receipt_rejects_company_mismatch():
    with pytest.raises(models.DataError):
        make_receipt(EmpresaId=999)


def test_receipt_rejects_total_that_diverges_from_items():
    with pytest.raises(models.DataError):
        make_receipt(VlrLiquido=999.00)


def test_receipt_rejects_duplicate_item_id():
    with pytest.raises(models.DataError):
        make_receipt(CupomFiscalItem=[
            {'CupomItemId': 1, 'ProdutoId': 501, 'Status': 'V', 'Quantidade': 1,
             'VlrLiquido': 50.00, 'CustoVendaMedioLiquido': 10.00},
            {'CupomItemId': 1, 'ProdutoId': 502, 'Status': 'V', 'Quantidade': 1,
             'VlrLiquido': 50.00, 'CustoVendaMedioLiquido': 10.00},
        ])


def test_receipt_preserves_missing_cost_as_none_not_zero():
    raw = make_raw(CupomFiscalItem=[
        {'CupomItemId': 1, 'ProdutoId': 501, 'Status': 'V', 'Quantidade': 1,
         'VlrLiquido': 100.00},  # no CustoVendaMedioLiquido at all
    ])
    r = models.receipt(raw, 218, '2026-01-01', '2026-01-31')
    assert r['items'][0]['cost'] is None


# --- canonical_receipts() -----------------------------------------------------------

def test_canonical_receipts_dedupes_identical_page_overlap():
    """Regression test: a page boundary handing back the same record twice must
    not abort the sync (this was the bug behind 'Cupom repetido entre páginas')."""
    r1 = make_receipt()
    r2 = make_receipt()  # same raw input -> equal but distinct dict, like two pages
    result = models.canonical_receipts([r1, r2])
    assert len(result) == 1


def test_canonical_receipts_rejects_genuinely_conflicting_duplicate():
    r1 = make_receipt()
    r2 = make_receipt(VlrLiquido=200.00, CupomFiscalItem=[
        {'CupomItemId': 1, 'ProdutoId': 501, 'Status': 'V', 'Quantidade': 2,
         'VlrLiquido': 200.00, 'CustoVendaMedioLiquido': 30.00},
    ])
    with pytest.raises(models.DataError):
        models.canonical_receipts([r1, r2])


def test_canonical_receipts_collapses_fiscal_and_reference_pair_into_one_sale():
    """A pedido faturado em nota + cupom must count as a single sale."""
    cf = make_receipt(CupomFiscalId=2001, Especie='CF', CupomFiscalRefId=3001)
    nf = make_receipt(CupomFiscalId=3001, Especie='NF')
    result = models.canonical_receipts([cf, nf])
    assert len(result) == 1
    assert set(result[0]['aliases']) == {2001, 3001}
    assert result[0]['species'] == 'CF'  # fiscal representation preferred


def test_canonical_receipts_marks_group_cancelled_if_either_side_is():
    cf = make_receipt(CupomFiscalId=2001, Especie='CF', CupomFiscalRefId=3001, Status='V')
    nf = make_receipt(CupomFiscalId=3001, Especie='NF', Status='C')
    result = models.canonical_receipts([cf, nf])
    assert result[0]['status'] == 'C'


# --- reconcile() ---------------------------------------------------------------------

def test_reconcile_publishes_small_gap_but_discloses_it():
    """Regression test for the 1-in-3351 Mobne gap found on the real 2026-01 sync:
    a small difference is published, never silently hidden or silently zeroed."""
    receipt = {'id': 70045717, 'aliases': [70045717], 'status': 'V', 'revenue': 1364}
    result = models.reconcile([receipt], [])
    assert result['exact_match'] is False
    assert result['missing_documents'] == 1
    assert result['missing_document_ids'] == [70045717]
    assert result['difference'] == 1364
    assert result['matched'] is True  # within the disclosed tolerance


def test_reconcile_blocks_when_gap_exceeds_tolerance():
    receipt = {'id': 1, 'aliases': [1], 'status': 'V', 'revenue': 100000}  # R$1000, nothing matches
    result = models.reconcile([receipt], [])
    assert result['matched'] is False
    assert result['exact_match'] is False


def test_reconcile_exact_match_when_everything_lines_up():
    receipt = {'id': 1, 'aliases': [1], 'status': 'V', 'revenue': 100}
    analysis = [{'document_id': 1, 'item_id': 1, 'document_status': 'V',
                 'item_status': 'V', 'direction': 'S', 'revenue': 100}]
    result = models.reconcile([receipt], analysis)
    assert result['exact_match'] is True
    assert result['matched'] is True
    assert result['difference'] == 0


# --- summarize() ---------------------------------------------------------------------

def test_summarize_counts_one_receipt_once_across_categories():
    receipts = [{
        'id': 1, 'status': 'V', 'date': '2026-01-10', 'revenue': 300,
        'items': [
            {'id': 1, 'product_id': 1, 'status': 'V', 'revenue': 200, 'cost': 100, 'quantity': '1'},
            {'id': 2, 'product_id': 2, 'status': 'V', 'revenue': 100, 'cost': 50, 'quantity': '1'},
        ],
    }]
    products = {1: {'name': 'A', 'category': 'Bebidas'}, 2: {'name': 'B', 'category': 'Mercearia'}}
    result = models.summarize(receipts, products)
    assert result['totals']['receipts'] == 1
    assert result['totals']['ticket'] == 300


def test_summarize_excludes_cancelled_from_revenue_but_counts_it():
    receipts = [
        {'id': 1, 'status': 'V', 'date': '2026-01-10', 'revenue': 100,
         'items': [{'id': 1, 'product_id': 1, 'status': 'V', 'revenue': 100, 'cost': 50, 'quantity': '1'}]},
        {'id': 2, 'status': 'C', 'date': '2026-01-11', 'revenue': 50, 'items': []},
    ]
    result = models.summarize(receipts)
    assert result['totals']['revenue'] == 100
    assert result['totals']['cancelled'] == 1


def test_summarize_reports_zero_cost_ratio_without_hiding_profit():
    """Regression: Jul-Aug/2025 had 100% zero-cost items (Mobne had no purchase
    history yet for a newly onboarded store), inflating margin to 100%. An explicit
    zero stays a known cost (design choice, see models.receipt) but the ratio must
    be surfaced so the frontend can warn when it is this high."""
    receipts = [{'id': 1, 'status': 'V', 'date': '2026-01-10', 'revenue': 200,
                 'items': [
                     {'id': 1, 'product_id': 1, 'status': 'V', 'revenue': 100, 'cost': 0, 'quantity': '1'},
                     {'id': 2, 'product_id': 2, 'status': 'V', 'revenue': 100, 'cost': 50, 'quantity': '1'},
                 ]}]
    result = models.summarize(receipts)
    assert result['totals']['zero_cost_items'] == 1
    assert result['totals']['zero_cost_ratio'] == 0.5
    assert result['totals']['profit'] == 150  # 200 revenue - (0 + 50) known cost


def test_summarize_zero_cost_ratio_is_zero_when_there_are_no_items():
    result = models.summarize([])
    assert result['totals']['zero_cost_ratio'] == 0.0


def test_summarize_profit_is_none_when_cost_is_missing_not_estimated():
    receipts = [{'id': 1, 'status': 'V', 'date': '2026-01-10', 'revenue': 100,
                 'items': [{'id': 1, 'product_id': 1, 'status': 'V', 'revenue': 100,
                            'cost': None, 'quantity': '1'}]}]
    result = models.summarize(receipts)
    assert result['totals']['profit'] is None
    assert result['totals']['unknown'] == 1
