"""Unit tests for backend/mobne.py against a mocked HTTP transport (no network,
no credentials). Covers pagination completeness, fail-closed behavior on a bad
page, and retry/backoff for 429/5xx per PLANO_IMPLEMENTACAO_MOBNE.md."""
from __future__ import annotations
import httpx
import pytest
from backend.mobne import MobneClient, MobneError


def transport_from(responses):
    responses = list(responses)

    def handler(request):
        resp = responses.pop(0)
        if isinstance(resp, int):
            return httpx.Response(resp, json={'Success': False})
        return httpx.Response(200, json=resp)
    return httpx.MockTransport(handler)


def page(items, total, pages, number):
    return {'Success': True, 'Data': {'Items': items,
            'Paging': {'TotalItems': total, 'TotalPages': pages, 'PageNumber': number}}}


def client_with(responses):
    return MobneClient(key='test-key', transport=transport_from(responses), sleeper=lambda s: None)


def test_fetch_all_walks_every_page():
    page1 = page([{'CupomFiscalId': i} for i in range(100)], 150, 2, 1)
    page2 = page([{'CupomFiscalId': i} for i in range(100, 150)], 150, 2, 2)
    client = client_with([page1, page2])
    try:
        records = client.fetch_all('receipts', {'Filter.EmpresaId': 218})
        assert len(records) == 150
    finally:
        client.close()


def test_fetch_all_fails_closed_when_a_later_page_errors():
    """A failure on page 2 must not publish the 100 rows from page 1 as complete."""
    page1 = page([{'CupomFiscalId': i} for i in range(100)], 150, 2, 1)
    client = client_with([page1, 500, 500, 500, 500])  # 4 retry attempts, all fail
    try:
        with pytest.raises(MobneError):
            client.fetch_all('receipts', {'Filter.EmpresaId': 218})
    finally:
        client.close()


def test_request_raises_immediately_on_401_without_leaking_body():
    client = client_with([401])
    try:
        with pytest.raises(MobneError) as excinfo:
            client.request('companies', {})
        assert 'test-key' not in str(excinfo.value)
    finally:
        client.close()


def test_rejected_key_with_a_broken_body_is_reported_as_a_credential_error():
    """Mobne (Kestrel) sends its 401 with a chunked body that breaks mid-read; that
    must say the key was refused, not retry and blame the network."""
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.RemoteProtocolError('peer closed connection without sending complete message body')
            yield b''

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, stream=BrokenBody())
    client = MobneClient(key='test-key', transport=httpx.MockTransport(handler), sleeper=lambda s: None)
    try:
        with pytest.raises(MobneError) as excinfo:
            client.request('companies', {})
        assert 'Credencial Mobne recusada' in str(excinfo.value)
        assert 'test-key' not in str(excinfo.value)
        assert len(calls) == 1  # no retries for a refused key
    finally:
        client.close()


def test_request_retries_after_429_then_succeeds():
    ok = page([], 0, 0, 1)
    client = client_with([429, ok])
    try:
        data = client.request('receipts', {'PageSize': 100, 'PageNumber': 1})
        assert data['Items'] == []
    finally:
        client.close()


def test_fetch_all_rejects_a_repeated_identical_page():
    p1 = page([{'CupomFiscalId': 1}], 2, 2, 1)
    # Same exact page content served again for page 2 -> must not silently duplicate.
    p2 = page([{'CupomFiscalId': 1}], 2, 2, 2)
    client = client_with([p1, p2])
    try:
        with pytest.raises(MobneError):
            client.fetch_all('receipts', {'Filter.EmpresaId': 218})
    finally:
        client.close()
