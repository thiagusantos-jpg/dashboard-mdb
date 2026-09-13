"""Guards against the "download a multi-MB blob to compute a small number" pattern.

`datasets.payload` holds denormalized JSON per company/resource/period — a few MB
per row in production (62 MB of sales history for a single store). Reading it is
nearly free against local SQLite and expensive against a network-attached
Postgres, where every byte is billed egress: the /status poll alone (every 60s
per open tab) was transferring 4.42 MB to produce four item counts, ~45 GB/month.

These tests assert the cheap path stays cheap by inspecting the SQL actually
executed, so a future refactor that reintroduces a payload read on a hot path
fails here instead of silently on the invoice.
"""
from __future__ import annotations

import sqlite3

import pytest

from backend import api, database as db, models, sync


COMPANY = 218


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, 'DB_PATH', tmp_path / 'test.sqlite3')
    db.initialize()
    with db.connection() as conn:
        conn.execute('INSERT INTO companies VALUES(?,?)', (COMPANY, 'Loja Teste'))
    return tmp_path


def executed_sql(fn):
    """Capture every statement run on any SQLite connection opened while fn() runs.

    Hooks connection creation rather than execute() — sqlite3.Connection.execute
    is a read-only slot on a built-in type and cannot be monkeypatched. Same
    methodology as tests/finance/test_forecast.py's query counter.
    """
    real_connect = sqlite3.connect
    statements: list[str] = []

    def tracing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    sqlite3.connect = tracing_connect
    try:
        result = fn()
    finally:
        sqlite3.connect = real_connect
    return result, statements


def payload_reads(statements):
    """Statements that pull `datasets.payload` over the wire.

    `SELECT *` counts: it carries the payload column even though the word never
    appears in the SQL text, which is exactly how the original /status read 4.42 MB
    while looking innocent.
    """
    reads = []
    for raw in statements:
        sql = ' '.join(raw.lower().split())
        if 'from datasets' not in sql or not sql.startswith('select'):
            continue
        projection = sql.split('from datasets')[0]
        if '*' in projection or 'payload' in projection:
            reads.append(raw)
    return reads


def catalog(n):
    return [{'id': i, 'description': f'Produto {i}', 'cursor': i} for i in range(1, n + 1)]


# --- /status ---------------------------------------------------------------


def test_status_does_not_transfer_catalog_payloads(isolated_db):
    """The 45 GB/month regression: /status is polled every 60s by every open tab
    and only ever needed a count and a timestamp per catalog."""
    db.put_dataset(COMPANY, 'products', 'current', catalog(3))
    db.put_dataset(COMPANY, 'stock', 'current', catalog(2))

    _, sql = executed_sql(lambda: api.status(COMPANY))

    assert payload_reads(sql) == []


def test_status_still_reports_catalog_counts_and_timestamps(isolated_db):
    """Behaviour preserved: the cheap path must report exactly what the
    payload-reading one did."""
    db.put_dataset(COMPANY, 'products', 'current', catalog(3))
    db.put_dataset(COMPANY, 'stock', 'current', catalog(2))

    result = api.status(COMPANY)

    assert result['catalogs']['products']['count'] == 3
    assert result['catalogs']['stock']['count'] == 2
    assert result['catalogs']['products']['updated_at'] is not None
    assert result['catalogs']['categories'] is None  # never synced: absent, not zero


def test_catalog_counts_are_backfilled_at_startup_for_legacy_rows(isolated_db):
    """Rows written before `documents` was stored for catalogs carry 0. They must
    report their real count, not 0 — a dashboard claiming "0 produtos" reads as a
    broken sync. Models the real ordering: the legacy rows already exist when the
    new code starts up."""
    db.put_dataset(COMPANY, 'products', 'current', catalog(3))
    with db.connection() as conn:  # a row as the previous version left it
        conn.execute("UPDATE datasets SET documents=0 WHERE resource='products'")

    db.initialize()  # the app starting again (every serverless cold start does this)

    assert api.status(COMPANY)['catalogs']['products']['count'] == 3


def test_an_empty_catalog_reports_zero_rather_than_being_treated_as_uncounted(isolated_db):
    db.put_dataset(COMPANY, 'products', 'current', [])

    assert api.status(COMPANY)['catalogs']['products']['count'] == 0


# --- /dashboard timeline ---------------------------------------------------


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
        return self.receipts if resource == 'receipts' else []

    def analysis(self, company, start, end):
        return self.analysis_rows

    def close(self):
        pass


def sync_month(period, revenue=100.0):
    day = f'{period}-05'
    job_id = sync.run(COMPANY, mode='month', period=period,
                      client=FakeClient([raw_receipt(1, day, revenue=revenue)],
                                        [raw_analysis(1, day, revenue=revenue)]))
    job = next(r for r in db.jobs(COMPANY) if r['id'] == job_id)
    assert job['state'] == 'completed', job['error']


def summarize_calls(monkeypatch, fn):
    """How many period payloads a call parses and re-aggregates.

    The timeline's cost is one models.summarize() over a full receipts list per
    historical period — counting statements would miss it entirely, since all of
    them arrive through a single query returning N multi-MB rows.
    """
    calls = {'n': 0}
    real = models.summarize

    def counting(*args, **kwargs):
        calls['n'] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(models, 'summarize', counting)
    try:
        fn()
    finally:
        monkeypatch.setattr(models, 'summarize', real)
    return calls['n']


def test_dashboard_cost_does_not_grow_with_the_stores_history(isolated_db, monkeypatch):
    """The 62 MB-per-page-load regression: the timeline re-read and re-aggregated
    every historical period's full payload to produce ~200 bytes of totals per
    month, so opening the dashboard got more expensive with every month the store
    had ever operated. The selected period and its two comparisons are a legitimate
    constant cost; the history behind them must not be."""
    for period in ('2026-01', '2026-02', '2026-03'):
        sync_month(period)
    short_history = summarize_calls(monkeypatch, lambda: api.dashboard(COMPANY, '2026-03'))

    for period in ('2025-08', '2025-09', '2025-10', '2025-11', '2025-12'):
        sync_month(period)
    long_history = summarize_calls(monkeypatch, lambda: api.dashboard(COMPANY, '2026-03'))

    assert long_history == short_history


def test_dashboard_timeline_query_does_not_carry_payloads(isolated_db):
    """The transfer side of the same regression: whatever the timeline reads per
    period must not include the payload column."""
    for period in ('2026-01', '2026-02', '2026-03'):
        sync_month(period)

    _, statements = executed_sql(lambda: api.dashboard(COMPANY, '2026-03'))

    timeline_scans = [s for s in statements if "resource='sales'" in s and 'ORDER BY period' in s]
    assert timeline_scans, 'expected the timeline to scan the sales periods'
    assert payload_reads(timeline_scans) == []


def test_dashboard_timeline_matches_the_payload_derived_computation(isolated_db):
    """Equivalence guard: the stored summary must reproduce exactly what parsing the
    raw payload produced, or this trades correctness for bandwidth."""
    sync_month('2026-01', revenue=150.0)
    sync_month('2026-02', revenue=250.0)

    timeline = {row['period']: row for row in api.dashboard(COMPANY, '2026-02')['timeline']}

    for period, expected_revenue in (('2026-01', 15000), ('2026-02', 25000)):
        payload = db.dataset(COMPANY, 'sales', period)['payload']
        expected = models.summarize(payload['receipts'])['totals']
        assert timeline[period]['revenue'] == expected_revenue
        for field, value in expected.items():
            assert timeline[period][field] == value, f'{period}.{field}'
        assert timeline[period]['end'] == payload['end']


def test_dashboard_timeline_is_correct_for_periods_with_no_stored_summary(isolated_db):
    """Rows written before the summary existed must still render, reading their
    payload as before, until the next sync backfills them."""
    sync_month('2026-01', revenue=150.0)
    with db.connection() as conn:
        conn.execute("UPDATE datasets SET summary=NULL WHERE resource='sales'")

    timeline = api.dashboard(COMPANY, '2026-01')['timeline']

    assert [row['revenue'] for row in timeline] == [15000]


def test_sync_backfills_summaries_for_periods_that_lack_them(isolated_db):
    """A daily 'recent' sync only rewrites recent months, so historical periods would
    keep paying the full-payload cost forever without an explicit backfill."""
    sync_month('2026-01')
    sync_month('2026-02')
    with db.connection() as conn:
        conn.execute("UPDATE datasets SET summary=NULL WHERE resource='sales' AND period='2026-01'")

    sync_month('2026-02')  # any sync run heals what is missing

    with db.connection() as conn:
        missing = conn.execute(
            "SELECT COUNT(*) AS n FROM datasets WHERE resource='sales' AND summary IS NULL").fetchone()
    assert missing['n'] == 0


# --- the sync's own payload round-trips (A4) -------------------------------


class CatalogClient(FakeClient):
    """Also returns catalog rows, so the catalog branch of sync.run() is exercised."""

    def fetch_all(self, resource, params=None, progress=None):
        if resource == 'companies':
            return [{'EmpresaId': COMPANY, 'DescricaoReduzida': 'Loja Teste', 'RazaoSocial': 'Loja Teste LTDA'}]
        if resource == 'receipts':
            return self.receipts
        if resource == 'categories':
            return [{'CategoriaId': 1, 'Categoria': 'Mercearia'}]
        if resource == 'products':
            return [{'ProdutoId': 501, 'Descricao': 'Produto teste', 'CategoriaId': 1,
                     'Status': 'A', 'NroBaseExportacao': 0}]
        if resource == 'stock':
            return [{'ProdutoId': 501, 'QtdeEstoque': 10, 'QtdeReservaPedidoVenda': 0,
                     'CustoMedioLiquido': 5, 'CustoMedioUltimaEntradaLiquido': 5,
                     'NroBaseExportacao': 0}]
        if resource == 'prices':
            return [{'ProdutoId': 501, 'QtdEmbalagem': 1, 'PrecoUnitario': 10,
                     'DtaAlteracao': None, 'NroBaseExportacao': 0}]
        return []


def sync_with_catalogs(period, revenue=100.0):
    day = f'{period}-05'
    return sync.run(COMPANY, mode='month', period=period,
                    client=CatalogClient([raw_receipt(1, day, revenue=revenue)],
                                         [raw_analysis(1, day, revenue=revenue)]))


def payload_reads_per_resource(statements, resources=('products', 'stock', 'prices')):
    """How many times each catalog's payload crosses the wire during a sync.

    One read each is legitimate and expected: snapshot_catalogs() writes a
    stock/price/cost row per product per day and genuinely needs the contents.
    Anything beyond that is a round-trip for data the caller already had or never
    needed — what these tests exist to prevent.
    """
    return {r: sum(1 for s in payload_reads(statements) if f"resource='{r}'" in s)
            for r in resources}


def test_sync_does_not_read_catalog_payloads_just_to_check_their_age(isolated_db):
    """sync.run() skips a catalog refreshed under 24h ago — but decided that by
    loading the whole payload to look at one timestamp, the same waste /status had.
    With the refresh skipped entirely, only snapshot_catalogs should read them."""
    sync_with_catalogs('2026-01')

    _, statements = executed_sql(lambda: sync_with_catalogs('2026-02'))  # catalogs still fresh

    for resource, reads in payload_reads_per_resource(statements).items():
        assert reads <= 1, f"{resource} payload read {reads}x; only snapshot_catalogs needs it"


def test_rotating_a_catalog_to_previous_does_not_round_trip_its_payload(isolated_db):
    """stock/prices keep a '_previous' copy. Reading the payload out and writing it
    straight back moves it across the wire twice for a copy the database can do itself."""
    sync_with_catalogs('2026-01')
    with db.connection() as conn:  # age the catalogs so the next run refreshes them
        conn.execute("UPDATE datasets SET updated_at='2020-01-01T00:00:00+00:00' "
                     "WHERE period='current'")

    _, statements = executed_sql(lambda: sync_with_catalogs('2026-02'))

    for resource, reads in payload_reads_per_resource(statements, ('stock', 'prices')).items():
        assert reads <= 1, f"{resource} payload read {reads}x; the rotation should stay server-side"


def test_the_previous_copy_still_holds_the_superseded_catalog(isolated_db):
    """Behaviour preserved: whatever cheaper mechanism does the copy, '_previous' must
    still carry the values the refresh replaced."""
    sync_with_catalogs('2026-01')
    before = db.dataset(COMPANY, 'stock')['payload']
    with db.connection() as conn:
        conn.execute("UPDATE datasets SET updated_at='2020-01-01T00:00:00+00:00' WHERE period='current'")

    sync_with_catalogs('2026-02')

    assert db.dataset(COMPANY, 'stock_previous')['payload'] == before
