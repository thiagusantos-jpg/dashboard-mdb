"""Read-only Mobne client. No credentials or upstream bodies in exceptions/logs."""
from __future__ import annotations
import time
import httpx
from . import settings

class MobneError(Exception):
    pass

class MobneDeadline(Exception):
    """The caller's time budget (client.deadline) ran out. A pause for the caller to
    resume later, not a Mobne failure, so it deliberately is not a MobneError."""
    pass

PATHS = {
    'companies': '/api/v1/Empresa/consulta-cadastro-empresa',
    'categories': '/api/v1/Produto/consulta-cadastro-categoria',
    'products': '/api/v1/Produto/consulta-cadastro-produto',
    'stock': '/api/v1/Produto/consulta-estoque-produto',
    'prices': '/api/v1/Produto/consulta-preco',
    'receipts': '/api/v1/Cupom/consulta',
    'analysis': '/api/v1/AnaliseVenda/AnaliseVendas',
}

TIMEOUT = httpx.Timeout(90, connect=15)

class MobneClient:
    def __init__(self, key=None, transport=None, sleeper=time.sleep, clock=time.monotonic):
        key = key or settings.API_KEY
        if not key:
            raise MobneError('Credencial Mobne não configurada no servidor.')
        self.http = httpx.Client(base_url=settings.BASE_URL,
            headers={'Authorization': 'ApiKey ' + key, 'Accept': 'application/json'},
            timeout=TIMEOUT, follow_redirects=False, transport=transport)
        self.sleep = sleeper
        self.clock = clock
        # A clock() reading after which no request starts and none waits (None: no limit).
        # Set by a serverless sync so a slow Mobne cannot outlive the platform's time limit.
        self.deadline = None

    def close(self):
        self.http.close()

    def _time_left(self):
        if self.deadline is None:
            return None
        left = self.deadline - self.clock()
        if left <= 0:
            raise MobneDeadline()
        return left

    def _wait(self, seconds):
        left = self._time_left()
        self.sleep(seconds if left is None else min(seconds, left))

    def request(self, resource, params):
        for attempt in range(4):
            response = None
            left = self._time_left()
            timeout = TIMEOUT if left is None else httpx.Timeout(min(90, left), connect=min(15, left))
            try:
                # Status first, body second: Mobne (Kestrel) answers a rejected key with a
                # 401 whose chunked body breaks mid-read, which used to surface as a retried
                # "Falha de comunicação" instead of the credential problem it is.
                response = self.http.send(self.http.build_request('GET', PATHS[resource], params=params, timeout=timeout), stream=True)
                if response.status_code in (401, 403):
                    raise MobneError('Credencial Mobne recusada (HTTP %d): verifique MOBNE_API_KEY no servidor.' % response.status_code)
                response.read()
            except httpx.TransportError:
                self._time_left()  # a wait cut short by our own budget is a pause, not a network failure
                if attempt == 3:
                    raise MobneError('Falha de comunicação com o Mobne. A base anterior foi preservada.') from None
                self._wait(2 ** attempt)
                continue
            finally:
                if response is not None:
                    response.close()
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt == 3:
                    raise MobneError(f'Mobne temporariamente indisponível (HTTP {response.status_code}).')
                try:
                    delay = float(response.headers.get('Retry-After', 2 ** attempt))
                except ValueError:
                    delay = 2 ** attempt
                self._wait(min(60, max(1, delay)))
                continue
            if response.status_code != 200:
                raise MobneError(f'Consulta {resource} recusada pelo Mobne (HTTP {response.status_code}).')
            try:
                data = response.json()
            except ValueError:
                raise MobneError('Mobne retornou conteúdo inválido.') from None
            if not isinstance(data, dict) or data.get('Success') is not True:
                raise MobneError(f'Mobne não confirmou sucesso em {resource}.')
            return data.get('Data')
        raise MobneError('Consulta não concluída.')

    def fetch_all(self, resource, params=None, progress=None):
        params = dict(params or {})
        size = 100 if resource == 'receipts' else 1000
        page, expected, pages = 1, None, None
        records = []
        previous_signatures = set()
        while True:
            data = self.request(resource, dict(params, PageSize=size, PageNumber=page))
            if not isinstance(data, dict) or not isinstance(data.get('Items'), list):
                raise MobneError(f'Formato inesperado em {resource}.')
            paging, rows = data.get('Paging'), data['Items']
            if not isinstance(paging, dict):
                raise MobneError(f'Paginação ausente em {resource}.')
            try:
                current_total = int(paging['TotalItems'])
                current_pages = int(paging['TotalPages'])
                current_page = int(paging['PageNumber'])
            except (KeyError, TypeError, ValueError):
                raise MobneError(f'Paginação inválida em {resource}.') from None
            if current_page != page or current_total < 0 or current_pages < 0:
                raise MobneError('Página incorreta retornada pelo Mobne.')
            if expected is None:
                expected, pages = current_total, current_pages
            if current_total != expected or current_pages != pages:
                raise MobneError('A base Mobne mudou durante a consulta; repita a sincronização.')
            signature = repr(rows)
            if rows and signature in previous_signatures:
                raise MobneError('Mobne repetiu uma página; carga interrompida para evitar duplicação.')
            previous_signatures.add(signature)
            records.extend(rows)
            if progress:
                progress(page, max(pages, 1), len(records), expected)
            if page >= pages:
                if len(records) != expected:
                    raise MobneError(f'Carga incompleta em {resource}: contagem divergente.')
                return records
            if not rows:
                raise MobneError(f'Página vazia antes do fim em {resource}.')
            page += 1
            self.sleep(0.15)

    def analysis(self, company, start, end):
        data = self.request('analysis', {'Filter.EmpresaId': company,
            'Filter.DataInicial': start + 'T00:00:00', 'Filter.DataFinal': end + 'T23:59:59'})
        if not isinstance(data, dict) or data.get('IsCompletedSuccessfully') is not True or not isinstance(data.get('Result'), list):
            raise MobneError('Análise Mobne não retornou um resultado completo.')
        return data['Result']
