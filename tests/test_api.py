"""Tests for backend/api.py's dashboard() business logic: month-over-month and
year-over-year comparisons, the break-even point, and inventory alerts — the
new Resumo Executivo KPIs (see ANALISE_TECNICA_2026-09-11.md).

Builds fixtures through sync.run() with a fake Mobne client (no network), then
calls dashboard() directly (bypassing the FastAPI/auth layer, same as calling
any other plain function) against a temp sqlite db.
"""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from backend import api, database as db, sync

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
    def __init__(self, receipts, analysis):
        self.receipts, self.analysis_rows = receipts, analysis

    def fetch_all(self, resource, params=None, progress=None):
        if resource == 'companies':
            return [{'EmpresaId': COMPANY, 'DescricaoReduzida': 'Loja Teste', 'RazaoSocial': 'Loja Teste LTDA'}]
        if resource == 'receipts':
            return self.receipts
        return []

    def analysis(self, company, start, end):
        return self.analysis_rows

    def close(self):
        pass


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, 'DB_PATH', tmp_path / 'test.sqlite3')
    return tmp_path


def sync_month(period, receipts, analysis):
    job_id = sync.run(COMPANY, mode='month', period=period, client=FakeClient(receipts, analysis))
    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'completed', job['error']


def test_mom_and_yoy_comparisons_pull_from_different_periods(isolated_db):
    sync_month('2025-01', [raw_receipt(1, '2025-01-05', revenue=200.0)], [raw_analysis(1, '2025-01-05', revenue=200.0)])
    sync_month('2025-12', [raw_receipt(2, '2025-12-05', revenue=150.0)], [raw_analysis(2, '2025-12-05', revenue=150.0)])
    sync_month('2026-01', [raw_receipt(3, '2026-01-05', revenue=300.0)], [raw_analysis(3, '2026-01-05', revenue=300.0)])

    d = api.dashboard(COMPANY, '2026-01')

    assert d['comparison']['period'] == '2025-01'
    assert d['comparison']['revenue_change'] == 50.0     # 300 vs 200
    assert d['comparison_mom']['period'] == '2025-12'
    assert d['comparison_mom']['revenue_change'] == 100.0  # 300 vs 150


def test_yoy_comparison_is_none_when_the_prior_year_has_no_mobne_data(isolated_db):
    sync_month('2025-01', [], [])  # Mobne genuinely has no sales this far back
    sync_month('2026-01', [raw_receipt(1, '2026-01-05')], [raw_analysis(1, '2026-01-05')])

    d = api.dashboard(COMPANY, '2026-01')
    assert d['comparison'] is None


def test_break_even_point_from_margin_and_registered_fixed_cost(isolated_db):
    sync_month('2026-01', [raw_receipt(1, '2026-01-05', revenue=1000.0, cost=500.0)],
               [raw_analysis(1, '2026-01-05', revenue=1000.0, cost=500.0)])
    with db.connection() as conn:
        conn.execute('INSERT INTO config VALUES(?,?)', (COMPANY, 50000))  # R$500,00 fixo

    d = api.dashboard(COMPANY, '2026-01')
    assert d['totals']['margin'] == 50.0
    assert d['break_even_cents'] == 100000  # R$500 / 50% margin = R$1000
    assert d['break_even_gap_pct'] == 0.0   # revenue lands exactly on break-even


def test_break_even_point_is_none_when_any_item_has_unknown_cost(isolated_db):
    """Same null-propagation rule as simulated_net: a partial margin must not produce
    a break-even figure that looks precise but is not."""
    sync_month('2026-01', [raw_receipt(1, '2026-01-05', revenue=100.0, cost=None)],
               [raw_analysis(1, '2026-01-05', revenue=100.0, cost=None)])
    d = api.dashboard(COMPANY, '2026-01')
    assert d['totals']['margin'] is None
    assert d['break_even_cents'] is None
    assert d['break_even_gap_pct'] is None


def test_alerts_flag_out_of_stock_curve_a_and_underpriced_products(isolated_db):
    sync_month('2026-01', [raw_receipt(1, '2026-01-05', revenue=1000.0, cost=500.0)],
               [raw_analysis(1, '2026-01-05', revenue=1000.0, cost=500.0)])
    db.put_dataset(COMPANY, 'stock', 'current',
        [{'id': 501, 'quantity': 0, 'reserved': 0, 'unit_cost': '10', 'last_cost': '10', 'cursor': 0}])
    db.put_dataset(COMPANY, 'prices', 'current',
        [{'id': 501, 'package': '1', 'price': 900, 'updated_at': None, 'cursor': 0}])  # R$9,00 < R$10,00 custo

    d = api.dashboard(COMPANY, '2026-01')
    types = {a['type'] for a in d['alerts']}
    assert 'estoque' in types
    assert 'preco' in types
    assert 'custo' not in types  # this fixture's item has a known cost


def test_alerts_flag_items_sold_with_unknown_cost(isolated_db):
    sync_month('2026-01', [raw_receipt(1, '2026-01-05', revenue=100.0, cost=None)],
               [raw_analysis(1, '2026-01-05', revenue=100.0, cost=None)])
    d = api.dashboard(COMPANY, '2026-01')
    assert any(a['type'] == 'custo' for a in d['alerts'])


def test_responses_are_gzip_compressed_when_the_client_accepts_it():
    """The /dashboard payload is ~420 KB of JSON; it used to go out uncompressed.
    Checked on a static asset so the test needs no session (no lifespan is started)."""
    client = TestClient(api.app)
    r = client.get('/assets/app.js', headers={'Accept-Encoding': 'gzip'})
    assert r.status_code == 200
    assert r.headers.get('content-encoding') == 'gzip'


def test_page_and_assets_are_always_revalidated_by_the_browser():
    """Regression: style.css was heuristically cached, so a UI update did not show up
    until a forced reload. no-cache keeps it cached but revalidated (304 when unchanged)."""
    client = TestClient(api.app)
    for path in ('/', '/assets/style.css', '/assets/app.js'):
        assert client.get(path).headers.get('cache-control') == 'no-cache', path
