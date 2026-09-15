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
from backend.mobne import MobneDeadline, MobneError

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


# --- Job progress stays coherent (task C3) ----------------------------------


def _clean_client():
    return FakeClient({'2026-08': [raw_receipt(1, '2026-08-10')], '2026-09': [raw_receipt(2, '2026-09-10')]},
                      {'2026-08': [raw_analysis(1, '2026-08-10')], '2026-09': [raw_analysis(2, '2026-09-10')]})


def test_finished_job_reports_progress_within_total_and_a_final_message(isolated_db, monkeypatch):
    monkeypatch.setattr(sync, 'datetime', _frozen_datetime('2026-09-15'))
    job_id = sync.run(COMPANY, 'recent', client=_clean_client())

    job = next(j for j in db.jobs(COMPANY) if j['id'] == job_id)
    assert job['state'] == 'completed'
    assert job['total'] > 0
    assert job['completed'] == job['total']
    assert job['detail'] == 'Sincronização concluída'


def test_starting_a_sync_while_one_is_active_reuses_it_without_running_twice(isolated_db, monkeypatch):
    db.initialize()
    active_id, created = db.claim_job(COMPANY, 'recent')
    assert created is True
    again_id, created_again = db.claim_job(COMPANY, 'history')
    assert again_id == active_id
    assert created_again is False


def _age_job(job_id, seconds):
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET state='running',updated_at=? WHERE id=?", (old, job_id))


def test_serverless_run_killed_by_the_time_limit_no_longer_blocks_a_new_sync(isolated_db, monkeypatch):
    from backend import settings
    monkeypatch.setattr(settings, 'IS_SERVERLESS', True)
    db.initialize()
    stuck_id, _ = db.claim_job(COMPANY, 'recent')
    _age_job(stuck_id, db.STALE_JOB_SECONDS + 60)

    assert db.jobs(COMPANY)[0]['state'] == 'failed'
    new_id, created = db.claim_job(COMPANY, 'recent')
    assert created is True
    assert new_id != stuck_id


def test_recent_or_local_active_jobs_are_not_expired(isolated_db, monkeypatch):
    from backend import settings
    db.initialize()
    active_id, _ = db.claim_job(COMPANY, 'recent')

    monkeypatch.setattr(settings, 'IS_SERVERLESS', True)
    _age_job(active_id, 60)
    assert db.claim_job(COMPANY, 'recent') == (active_id, False)

    monkeypatch.setattr(settings, 'IS_SERVERLESS', False)
    _age_job(active_id, db.STALE_JOB_SECONDS + 60)
    assert db.claim_job(COMPANY, 'recent') == (active_id, False)


def _serverless_app(monkeypatch):
    from backend import security, settings

    monkeypatch.setattr(db, 'PG', False)
    monkeypatch.setattr(security, 'access_password', lambda: 'bootstrap-password')
    monkeypatch.setenv('MDB_DISABLE_WORKER', '1')
    security._attempts.clear()
    db.initialize()
    with db.connection() as conn:
        conn.execute("INSERT OR IGNORE INTO companies(id,name) VALUES(?, 'Loja Teste')", (COMPANY,))
    monkeypatch.setattr(settings, 'IS_SERVERLESS', True)


def _post_sync(mode='recent'):
    from fastapi.testclient import TestClient
    from backend import api

    with TestClient(api.app) as client:
        login = client.post('/api/login', json={'email': 'admin@loja.test', 'password': 'bootstrap-password'})
        assert login.status_code == 200, login.text
        client.headers['x-csrf-token'] = login.json()['csrf']
        response = client.post(f'/api/companies/{COMPANY}/sync', json={'mode': mode})
    assert response.status_code == 202, response.text
    return response.json()


def test_serverless_trigger_does_not_start_a_second_run_on_an_active_job(isolated_db, monkeypatch):
    _serverless_app(monkeypatch)
    active_id, _ = db.claim_job(COMPANY, 'recent')
    runs = []
    monkeypatch.setattr(sync, 'run', lambda *args, **kwargs: runs.append((args, kwargs)))

    body = _post_sync()

    assert body['job_id'] == active_id
    assert body['already_running'] is True
    assert runs == []


# --- A serverless run fits Vercel's 300 s limit (2026-09-14 incident) --------
#
# Hobby kills a function at 300 s without running except/finally. One 'recent'
# run (37 + 12 receipt pages, two analyses, the catalogs) did not fit: the POST
# came back 504, the job stayed 'running' until expired, and every retry
# downloaded everything again from the first page.


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TimedClient(FakeClient):
    """Receipts for a period cost `cost` seconds of the fake clock; the period
    `cut` runs out of the caller's time budget the first `cuts` times it is fetched."""

    def __init__(self, clock, cost=None, cut=None, cuts=1):
        clean = _clean_client()
        super().__init__(clean.receipts_by_period, clean.analysis_by_period)
        self.clock, self.cost, self.cut, self.cuts = clock, cost or {}, cut, cuts
        self.fetched = []

    def fetch_all(self, resource, params=None, progress=None):
        if resource == 'receipts':
            period = params['Filter.DataMovimentoInicial'][:7]
            if period == self.cut and self.cuts:
                self.cuts -= 1
                raise MobneDeadline()
            self.fetched.append(period)
            self.clock.now += self.cost.get(period, 0)
        return super().fetch_all(resource, params, progress)


def _job(job_id):
    return next(j for j in db.jobs(COMPANY) if j['id'] == job_id)


def test_a_serverless_run_pauses_between_steps_and_the_next_call_resumes_it(isolated_db, monkeypatch):
    monkeypatch.setattr(sync, 'datetime', _frozen_datetime('2026-09-15'))
    clock = Clock()
    client = TimedClient(clock, cost={'2026-08': sync.STEP_START_SECONDS})

    job_id = sync.run(COMPANY, 'recent', client=client, started=0.0, clock=clock)

    job = _job(job_id)
    assert job['state'] == 'queued'  # paused, not killed: still the company's active job
    assert (job['completed'], job['total']) == (1, 6)
    assert job['error'] is None
    assert db.dataset(COMPANY, 'sales', '2026-08') is not None  # the finished step is kept
    assert db.dataset(COMPANY, 'sales', '2026-09') is None

    assert db.resume_job(job_id) is True
    assert db.resume_job(job_id) is False  # a second caller never runs it too
    clock.now = 1000.0
    sync.run(COMPANY, 'recent', job_id=job_id, client=client, started=1000.0, clock=clock)

    job = _job(job_id)
    assert job['state'] == 'completed'
    assert job['completed'] == job['total'] == 6
    assert client.fetched == ['2026-08', '2026-09']  # 2026-08 was not downloaded again


def test_a_step_cut_by_the_time_limit_is_redone_whole_on_the_next_call(isolated_db, monkeypatch):
    monkeypatch.setattr(sync, 'datetime', _frozen_datetime('2026-09-15'))
    clock = Clock()
    client = TimedClient(clock, cut='2026-09')

    job_id = sync.run(COMPANY, 'recent', client=client, started=0.0, clock=clock)

    job = _job(job_id)
    assert job['state'] == 'queued'
    assert job['completed'] == 1
    assert job['error'] is None
    assert db.dataset(COMPANY, 'sales', '2026-09') is None  # nothing half-saved

    assert db.resume_job(job_id)
    sync.run(COMPANY, 'recent', job_id=job_id, client=client, started=0.0, clock=clock)

    assert _job(job_id)['state'] == 'completed'
    assert db.dataset(COMPANY, 'sales', '2026-09') is not None


def test_a_step_that_never_fits_the_time_limit_fails_instead_of_resuming_forever(isolated_db, monkeypatch):
    monkeypatch.setattr(sync, 'datetime', _frozen_datetime('2026-09-15'))
    clock = Clock()
    client = TimedClient(clock, cut='2026-08', cuts=99)

    job_id = sync.run(COMPANY, 'recent', client=client, started=0.0, clock=clock)
    assert _job(job_id)['state'] == 'queued'  # one slow call may be Mobne having a bad minute
    assert db.resume_job(job_id)
    sync.run(COMPANY, 'recent', job_id=job_id, client=client, started=0.0, clock=clock)

    job = _job(job_id)
    assert job['state'] == 'failed'
    assert 'limite de tempo' in job['error']


def _saved_at(period, iso):
    with db.connection() as conn:
        conn.execute("UPDATE datasets SET updated_at=? WHERE company=? AND resource='sales' AND period=?", (iso, COMPANY, period))


def test_recent_does_not_download_again_a_previous_month_saved_after_it_settled(isolated_db, monkeypatch):
    """2026-09-14: every 'Recente' spent ~1.5 min downloading all 37 pages of August
    again, though August had been saved on 14/09. Late fixes after that are what
    'Reconciliar' is for."""
    monkeypatch.setattr(sync, 'datetime', _frozen_datetime('2026-09-15'))
    db.initialize()
    db.put_dataset(COMPANY, 'sales', '2026-08', {'receipts': [], 'end': '2026-08-31'}, documents=1)
    _saved_at('2026-08', '2026-09-14T13:16:39+00:00')
    client = TimedClient(Clock())

    job_id = sync.run(COMPANY, 'recent', client=client)

    job = _job(job_id)
    assert client.fetched == ['2026-09']
    assert job['state'] == 'completed'
    assert job['total'] == 1 + len(sync.CATALOGS)


def test_recent_still_refreshes_a_previous_month_saved_before_it_settled(isolated_db, monkeypatch):
    """Receipts for the last days of a month still arrive in the first days of the next one."""
    monkeypatch.setattr(sync, 'datetime', _frozen_datetime('2026-09-15'))
    db.initialize()
    db.put_dataset(COMPANY, 'sales', '2026-08', {'receipts': [], 'end': '2026-08-31'}, documents=1)
    _saved_at('2026-08', '2026-09-01T02:00:00+00:00')
    client = TimedClient(Clock())

    sync.run(COMPANY, 'recent', client=client)

    assert client.fetched == ['2026-08', '2026-09']


def test_a_paused_job_nobody_continues_is_released(isolated_db, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from backend import settings
    monkeypatch.setattr(settings, 'IS_SERVERLESS', True)
    db.initialize()
    paused_id, _ = db.claim_job(COMPANY, 'recent')
    old = (datetime.now(timezone.utc) - timedelta(seconds=db.STALE_JOB_SECONDS + 60)).isoformat()
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET state='queued',checkpoint='{}',updated_at=? WHERE id=?", (old, paused_id))

    job = db.jobs(COMPANY)[0]
    assert job['state'] == 'failed'
    assert 'pausada' in job['error']
    assert db.claim_job(COMPANY, 'recent')[1] is True


def test_serverless_trigger_continues_a_paused_job_and_says_when_more_is_left(isolated_db, monkeypatch):
    _serverless_app(monkeypatch)
    paused_id, _ = db.claim_job(COMPANY, 'recent')
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET checkpoint='{}' WHERE id=?", (paused_id,))
    runs = []

    def one_more_step(company, mode, job_id=None, **kwargs):
        runs.append(job_id)
        db.update_job(job_id, state='queued')  # still steps left after this call

    monkeypatch.setattr(sync, 'run', one_more_step)

    body = _post_sync()

    assert body == {'job_id': paused_id, 'already_running': False, 'paused': True}
    assert runs == [paused_id]


def test_the_daily_cron_calls_itself_again_while_its_sync_is_paused(isolated_db, monkeypatch):
    """Hobby allows one cron a day: without chaining, the unattended sync stops at the first pause."""
    from fastapi.testclient import TestClient
    from backend import api
    _serverless_app(monkeypatch)
    monkeypatch.setenv('CRON_SECRET', 'cron-secret')
    monkeypatch.setattr(sync, 'run', lambda company, mode, job_id=None, **kw: db.update_job(job_id, state='queued'))
    calls = []
    monkeypatch.setattr(api, '_call_again', lambda url, secret: calls.append((url, secret)))

    with TestClient(api.app) as client:
        response = client.get('/api/cron/sync', headers={'authorization': 'Bearer cron-secret', 'x-forwarded-host': 'painel.test'})

    assert response.status_code == 200, response.text
    assert response.json()['continuing'] is True
    assert calls == [(f'https://painel.test/api/cron/sync?from={COMPANY}', 'cron-secret')]


def _frozen_datetime(day):
    """sync.run reads the civil date from datetime.now(); pin it so 'recent'
    always means the month before `day` and the month of `day`."""
    from datetime import datetime as real_datetime

    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(day + 'T12:00:00').replace(tzinfo=tz)

    return Frozen
