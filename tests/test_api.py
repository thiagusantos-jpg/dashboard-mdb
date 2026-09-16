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


def test_unsynced_stock_is_not_reported_as_zero_stock(isolated_db):
    sync_month('2026-01', [raw_receipt(1, '2026-01-05')], [raw_analysis(1, '2026-01-05')])
    with db.connection() as conn:
        conn.execute("DELETE FROM datasets WHERE company=? AND resource='stock'", (COMPANY,))
    d = api.dashboard(COMPANY, '2026-01')
    assert d['inventory'][0]['stock'] is None
    assert not any(a['type'] == 'estoque' for a in d['alerts'])
    assert any(a['type'] == 'integracao' for a in d['alerts'])


def _receipt_for(doc_id, day, product, revenue, cost):
    row = raw_receipt(doc_id, day, revenue=revenue, cost=cost)
    row['CupomFiscalItem'][0]['ProdutoId'] = product
    return row


def _analysis_for(doc_id, day, product, revenue, cost):
    row = raw_analysis(doc_id, day, revenue=revenue, cost=cost)
    row['Produto_ProdutoId'] = product
    row['Produto_Descricao'] = f'Produto {product}'
    return row


def test_product_map_groups_the_last_30_days_and_lists_what_changed_group(isolated_db):
    # January: products 501 and 502 sell every day of the 1st–20th with a 70% margin (both stars).
    jan_r, jan_a, doc = [], [], 0
    for day in range(1, 21):
        for product in (501, 502):
            doc += 1
            d = f'2026-01-{day:02d}'
            jan_r.append(_receipt_for(doc, d, product, 100.0, 30.0))
            jan_a.append(_analysis_for(doc, d, product, 100.0, 30.0))
    sync_month('2026-01', jan_r, jan_a)
    # February: 502 keeps selling daily; 501 sells once, at a 10% margin (low turnover).
    feb_r, feb_a = [], []
    for day in range(1, 21):
        doc += 1
        d = f'2026-02-{day:02d}'
        feb_r.append(_receipt_for(doc, d, 502, 100.0, 30.0))
        feb_a.append(_analysis_for(doc, d, 502, 100.0, 30.0))
    doc += 1
    feb_r.append(_receipt_for(doc, '2026-02-01', 501, 100.0, 90.0))
    feb_a.append(_analysis_for(doc, '2026-02-01', 501, 100.0, 90.0))
    sync_month('2026-02', feb_r, feb_a)

    m = api.dashboard(COMPANY, '2026-02')['product_map']

    assert (m['start'], m['end'], m['days']) == ('2026-01-30', '2026-02-28', 30)
    assert m['previous'] == {'start': '2025-12-31', 'end': '2026-01-29', 'available': True}
    groups = {p['id']: p['classification'] for p in m['products']}
    assert groups == {501: 'Baixo giro', 502: 'Estrela'}
    assert next(p for p in m['products'] if p['id'] == 501)['last_sold'] == '2026-02-01'
    assert [(c['id'], c['from'], c['to']) for c in m['changes']['lost_star']] == [(501, 'Estrela', 'Baixo giro')]
    assert [(c['id'], c['from'], c['to']) for c in m['changes']['became_low']] == [(501, 'Estrela', 'Baixo giro')]


def test_dashboard_alerts_carry_the_count_behind_each_message():
    inventory = [
        {'id': 1, 'name': 'A', 'abc': 'A', 'stock': 0, 'current_price': 500, 'current_cost': 300},
        {'id': 2, 'name': 'B', 'abc': 'A', 'stock': -2, 'current_price': 200, 'current_cost': 250},
        {'id': 3, 'name': 'C', 'abc': 'B', 'stock': 0, 'current_price': None, 'current_cost': 100},
    ]
    alerts = {a['type']: a for a in api.dashboard_alerts(inventory, True, 12)}
    assert alerts['estoque']['count'] == 2
    assert alerts['estoque']['message'].startswith('2 produto(s) da curva A')
    assert alerts['preco']['count'] == 1
    assert alerts['custo']['count'] == 12
    assert 'integracao' not in alerts

    unsynced = api.dashboard_alerts([], False, 0)
    assert [(a['type'], a['count']) for a in unsynced] == [('integracao', None)]


def _sales_payload(period, start, end, days, revenue=100.0):
    """A stored sales month whose window is `start`..`end` but whose receipts stop at
    the last day in `days` — exactly what Mobne returns when a day has not been
    exported yet (Set/2026: asked to 15/09, answered to 13/09)."""
    receipts = [{'id': int(d.replace('-', '')), 'reference_id': 0, 'date': d, 'status': 'V',
                 'species': 'CF', 'revenue': int(revenue * 100), 'aliases': [int(d.replace('-', ''))],
                 'items': [{'id': 1, 'product_id': 501, 'quantity': '1', 'revenue': int(revenue * 100),
                            'cost': 3000, 'unit_cost': '30', 'status': 'V'}]} for d in days]
    return {'receipts': receipts, 'analysis': [], 'raw_count': len(receipts), 'start': start, 'end': end,
            'reconciliation': {'receipt_revenue': sum(r['revenue'] for r in receipts), 'analysis_revenue': 0,
                               'difference': 0, 'missing_documents': 0, 'additional_documents': 0,
                               'additional_revenue': 0, 'exact_match': True, 'matched': True}}


def test_dashboard_reports_the_last_day_mobne_actually_covered(isolated_db):
    """The window asked of Mobne is not the window Mobne answered. Reporting the
    requested end as coverage made Set/2026 read '1 a 15/09' over 13 days of data
    (R$ 4.515,82 missing) and compared those 13 days against 15 days of August."""
    db.initialize()
    db.save_companies([{'EmpresaId': COMPANY, 'RazaoSocial': 'Loja Teste'}])
    db.put_dataset(COMPANY, 'sales', '2026-01',
                   _sales_payload('2026-01', '2026-01-01', '2026-01-31',
                                  [f'2026-01-{d:02d}' for d in range(1, 16)]), documents=15)
    db.put_dataset(COMPANY, 'sales', '2026-02',
                   _sales_payload('2026-02', '2026-02-01', '2026-02-15',
                                  [f'2026-02-{d:02d}' for d in range(1, 14)]), documents=13)

    d = api.dashboard(COMPANY, '2026-02')

    assert d['end'] == '2026-02-13'          # coverage, not the requested 15/02
    assert d['requested_end'] == '2026-02-15'
    # 13 days against 13 days: comparing them with January's 15 days invented a -13,3% fall.
    assert d['comparison_mom']['totals']['receipts'] == 13
    assert d['comparison_mom']['revenue_change'] == 0.0


def test_dashboard_flags_sales_data_that_stopped_before_yesterday(isolated_db):
    """Mobne silently behind is the failure the partner sees as 'o faturamento não bate'."""
    db.initialize()
    db.save_companies([{'EmpresaId': COMPANY, 'RazaoSocial': 'Loja Teste'}])
    db.put_dataset(COMPANY, 'sales', '2026-02',
                   _sales_payload('2026-02', '2026-02-01', '2026-02-15',
                                  [f'2026-02-{d:02d}' for d in range(1, 14)]), documents=13)

    d = api.dashboard(COMPANY, '2026-02')

    assert d['data_gap_days'] >= 2
    assert [a['type'] for a in d['alerts'] if a['type'] == 'cobertura'] == ['cobertura']
