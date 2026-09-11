"""Integration tests for backend/sync.py against a temp sqlite db and a fake
Mobne client (no network, no credentials).

Regression coverage for the 2026-09-11 incident (ANALISE_TECNICA_2026-09-11.md):
a reconciliation failure in one month silently blocked every later month in the
same run, and required several manual re-runs to recover.
"""
from __future__ import annotations
import json
import sqlite3
import pytest
from backend import database as db, sync
from backend.mobne import MobneError

COMPANY = 218


def raw_receipt(id, day, revenue=100.0, cost=30.0):
    return {'CupomFiscalId': id, 'CupomFiscalRefId': None, 'EmpresaId': COMPANY,
            'DtaMovimento': f'{day}T10:00:00', 'VlrLiquido': revenue, 'Status': 'V', 'Especie': 'CF',
            'CupomFiscalItem': [{'CupomItemId': 1, 'ProdutoId': 501, 'Status': 'V',
                'Quantidade': 1, 'VlrLiquido': revenue, 'CustoVendaMedioLiquido': cost}]}


def raw_analysis(doc_id, day, revenue=100.0, cost=30.0):
    return {'DocumentoId': doc_id, 'ItemId': 1, 'Produto_ProdutoId': 501, 'Produto_Descricao': 'Produto teste',
            'Produto_CatCategoriaId': None, 'Produto_CatCategoria': None, 'DtaMovimento': f'{day}T10:00:00',
            'VlrDocumentoLiquido': revenue, 'QtdeProduto': 1, 'CustoMedioLiquido': cost,
            'StatusDocto': 'V', 'StatusItem': 'V', 'EntradaSaida': 'S', 'Tipo': 'V', 'Especie': 'CF',
            'Empresa_EmpresaId': COMPANY}


class FakeClient:
    """Duck-types MobneClient: fetch_all(resource, params, progress), analysis(company, start, end), close()."""

    def __init__(self, receipts_by_period, analysis_by_period, fail_receipts_for=None):
        self.receipts_by_period = receipts_by_period
        self.analysis_by_period = analysis_by_period
        self.fail_receipts_for = fail_receipts_for or set()

    def fetch_all(self, resource, params=None, progress=None):
        if resource == 'companies':
            return [{'EmpresaId': COMPANY, 'DescricaoReduzida': 'Loja Teste', 'RazaoSocial': 'Loja Teste LTDA'}]
        if resource == 'receipts':
            period = params['Filter.DataMovimentoInicial'][:7]
            if period in self.fail_receipts_for:
                raise MobneError('Mobne temporariamente indisponível (HTTP 502).')
            return self.receipts_by_period.get(period, [])
        return []  # categories/products/stock/prices: empty catalogs, not under test here

    def analysis(self, company, start, end):
        return self.analysis_by_period.get(start[:7], [])

    def close(self):
        pass


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Every sync test runs against its own throwaway file; never the real dashboard.sqlite3."""
    monkeypatch.setattr(db.settings, 'DB_PATH', tmp_path / 'test.sqlite3')
    return tmp_path


def test_a_bad_month_does_not_block_a_clean_month_in_the_same_run(isolated_db):
    """Regression: 2025-09's reconciliation gap (R$92,92) previously aborted the whole
    'history'/'recent' run, leaving every later month unsynced until manually retried."""
    # A gap must exceed the tolerance floor (max(R$100, 0,2%), see models.reconcile) to block a month.
    receipts = {'2026-08': [raw_receipt(1, '2026-08-05', revenue=1000.0)], '2026-09': [raw_receipt(2, '2026-09-05')]}
    analysis = {'2026-08': [], '2026-09': [raw_analysis(2, '2026-09-05')]}  # Aug: no matching analysis at all
    client = FakeClient(receipts, analysis)

    job_id = sync.run(COMPANY, mode='recent', client=client)

    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'completed_with_errors'
    assert '2026-08' in job['error']

    assert db.dataset(COMPANY, 'sales', '2026-08') is None  # bad month: previous base preserved, not overwritten
    good = db.dataset(COMPANY, 'sales', '2026-09')
    assert good is not None
    assert good['payload']['reconciliation']['exact_match'] is True

    # Sales come before catalogs in run(); a period failure must not stop the catalog refresh either.
    assert db.dataset(COMPANY, 'categories') is not None


def test_a_network_failure_still_aborts_the_whole_run(isolated_db):
    """Unlike a data-quality gap, a transport/infra failure will likely repeat for every
    remaining period too, so the run should fail fast rather than hammer Mobne per-month."""
    receipts = {'2026-08': [raw_receipt(1, '2026-08-05')], '2026-09': [raw_receipt(2, '2026-09-05')]}
    analysis = {'2026-08': [raw_analysis(1, '2026-08-05')], '2026-09': [raw_analysis(2, '2026-09-05')]}
    client = FakeClient(receipts, analysis, fail_receipts_for={'2026-08'})

    job_id = sync.run(COMPANY, mode='recent', client=client)

    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'failed'
    assert db.dataset(COMPANY, 'sales', '2026-09') is None  # never reached
    assert db.dataset(COMPANY, 'categories') is None  # never reached


def test_saved_period_records_its_document_count(isolated_db):
    receipts = {'2026-08': [raw_receipt(1, '2026-08-05')], '2026-09': [raw_receipt(2, '2026-09-05')]}
    analysis = {'2026-08': [raw_analysis(1, '2026-08-05')], '2026-09': [raw_analysis(2, '2026-09-05')]}
    client = FakeClient(receipts, analysis)

    sync.run(COMPANY, mode='recent', client=client)

    periods = {p['period']: p['documents'] for p in db.periods(COMPANY)}
    assert periods['2026-08'] == 1
    assert periods['2026-09'] == 1


def test_period_with_zero_raw_receipts_is_recorded_as_documents_zero(isolated_db):
    """Mobne genuinely has no sales before onboarding; that period is saved (so 'history'
    mode does not refetch it forever) but flagged so the API/frontend can tell it apart
    from a period that simply has not been synced yet."""
    receipts = {'2026-08': [], '2026-09': [raw_receipt(2, '2026-09-05')]}
    analysis = {'2026-08': [], '2026-09': [raw_analysis(2, '2026-09-05')]}
    client = FakeClient(receipts, analysis)

    sync.run(COMPANY, mode='recent', client=client)

    periods = {p['period']: p['documents'] for p in db.periods(COMPANY)}
    assert periods['2026-08'] == 0
    assert periods['2026-09'] == 1
