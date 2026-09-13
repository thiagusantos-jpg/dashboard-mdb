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


# --- the summary backfill must not be hostage to Mobne -----------------------
#
# _backfill_summaries() only reads rows already in this database and fills one
# derived column on them; it never calls Mobne. Leaving it in the success path
# meant a vendor outage (Mobne returned 401s for days) also froze the dashboard's
# timeline at the expensive full-payload fallback, for no reason.


def test_backfill_still_runs_when_the_mobne_sync_fails(isolated_db):
    """A vendor outage must not block work that only touches our own database."""
    receipts = {'2026-09': [raw_receipt(1, '2026-09-05')]}
    analysis = {'2026-09': [raw_analysis(1, '2026-09-05')]}
    sync.run(COMPANY, mode='month', period='2026-09', client=FakeClient(receipts, analysis))
    with db.connection() as conn:  # a period stored before summaries were cached
        conn.execute("UPDATE datasets SET summary=NULL WHERE resource='sales'")

    job_id = sync.run(COMPANY, mode='recent',
                      client=FakeClient(receipts, analysis, fail_receipts_for={'2026-08', '2026-09'}))

    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'failed'
    assert 'Mobne' in job['error']  # the real cause is still what gets reported
    assert db.periods_missing_summary(COMPANY) == []  # ...and the backfill ran anyway


def test_a_failing_backfill_never_changes_what_the_job_reports(isolated_db, monkeypatch):
    """The backfill is best-effort bookkeeping. If it breaks, the job's own outcome —
    and, on failure, the real error message a user needs — must survive untouched."""
    receipts = {'2026-08': [raw_receipt(1, '2026-08-05')], '2026-09': [raw_receipt(2, '2026-09-05')]}
    analysis = {'2026-08': [raw_analysis(1, '2026-08-05')], '2026-09': [raw_analysis(2, '2026-09-05')]}

    def exploding(_company):
        raise RuntimeError('backfill quebrou')

    monkeypatch.setattr(sync, '_backfill_summaries', exploding)
    job_id = sync.run(COMPANY, mode='recent', client=FakeClient(receipts, analysis))

    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'completed'
    assert job['error'] is None
    assert db.dataset(COMPANY, 'sales', '2026-09') is not None  # synced data intact


def test_a_failing_backfill_does_not_mask_a_mobne_error(isolated_db, monkeypatch):
    monkeypatch.setattr(sync, '_backfill_summaries', lambda _c: (_ for _ in ()).throw(RuntimeError('boom')))
    client = FakeClient({}, {}, fail_receipts_for={'2026-08', '2026-09'})

    job_id = sync.run(COMPANY, mode='recent', client=client)

    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'failed'
    assert 'Mobne' in job['error']
    assert 'boom' not in (job['error'] or '')
