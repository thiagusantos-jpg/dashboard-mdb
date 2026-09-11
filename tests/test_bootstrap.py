"""First sync must work before the local company registry is populated."""
import time
import pytest
from fastapi.testclient import TestClient
from backend import api, database as db, security, settings, sync


class FakeMobne:
    def fetch_all(self, resource, params=None, progress=None):
        return [{'EmpresaId': 218, 'DescricaoReduzida': 'Loja teste'}] if resource == 'companies' else []

    def analysis(self, company, start, end):
        return []

    def close(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'PG', False)
    monkeypatch.setattr(settings, 'DB_PATH', tmp_path / 'bootstrap.sqlite3')
    monkeypatch.setattr(security, 'access_password', lambda: 'test-only-password')
    monkeypatch.setattr(sync, 'MobneClient', FakeMobne)
    db.initialize()
    with TestClient(api.app) as client:
        r = client.post('/api/login', json={'password': 'test-only-password'})
        assert r.status_code == 200
        client.headers['x-csrf-token'] = r.json()['csrf']
        yield client


@pytest.mark.parametrize('serverless', [False, True])
def test_bootstrap_completes_with_empty_registry(client, monkeypatch, serverless):
    monkeypatch.setattr(settings, 'IS_SERVERLESS', serverless)
    assert client.get('/api/session').json()['companies'] == []
    response = client.post('/api/companies/218/sync', json={'mode': 'recent'})
    assert response.status_code == 202
    job_id = response.json()['job_id']
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = client.get(f'/api/sync-jobs/{job_id}').json()
        if job['state'] == 'completed':
            break
        time.sleep(.05)
    assert job['state'] == 'completed', job
    assert len(client.get('/api/companies/218/status').json()['periods']) == 2


def test_rejected_company_reports_failure_before_registration(client, monkeypatch):
    monkeypatch.setattr(settings, 'IS_SERVERLESS', True)
    response = client.post('/api/companies/999/sync', json={'mode': 'recent'})
    job = client.get('/api/sync-jobs/' + str(response.json()['job_id'])).json()
    assert job['state'] == 'failed'
    assert 'não autorizada' in job['error']
    assert client.get('/api/session').json()['companies'] == []


def test_job_status_requires_authentication(client):
    assert client.get('/api/sync-jobs/99999').status_code == 404
    client.cookies.clear()
    assert client.get('/api/sync-jobs/99999').status_code == 401
    assert client.post('/api/companies/218/sync', json={'mode': 'recent'}).status_code == 401
