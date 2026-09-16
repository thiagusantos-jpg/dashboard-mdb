from __future__ import annotations
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from . import database as db, identity, models, permissions, security, settings
from . import sync
from .finance import accounts
from .operations.catalog import inventory_catalog
from .operations.goals import current_goal_progress
from .operations.sales_window import receipts_between as _receipts_between
from .routes.finance_accounts import router as finance_accounts_router
from .routes.financial_entries import router as financial_entries_router
from .routes.actions import router as actions_router
from .routes.bank_imports import router as bank_imports_router
from .routes.cashflow import router as cashflow_router
from .routes.financial_reports import router as financial_reports_router
from .routes.period_reviews import router as period_reviews_router
from .routes.goals import router as goals_router
from .routes.loans import router as loans_router
from .routes.obligations import router as obligations_router
from .routes.open_finance import router as open_finance_router
from .routes.products import router as products_router
from .routes.profile import router as profile_router
from .routes.receivables import router as receivables_router
from .routes.replenishment import router as replenishment_router
from .routes.reconciliation import router as reconciliation_router
from .routes.settings import router as settings_router
from .routes.users import router as users_router
from .sync import Worker

# Account, entry, user and store ids are secrets.randbits(63) — deliberately random
# and unguessable, but past JavaScript's 2^53 safe-integer limit. A plain int in JSON
# silently rounds in the browser, so every id round-tripped through a form breaks
# ("Conta financeira não encontrada"). Stringify oversized ints app-wide instead of
# hunting down each id field; FastAPI/Pydantic already accept a numeric string back
# as an int.
_JS_MAX_SAFE_INT = 2**53 - 1

def _stringify_big_ints(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > _JS_MAX_SAFE_INT else value
    if isinstance(value, dict):
        return {k: _stringify_big_ints(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_big_ints(v) for v in value]
    return value

class BigIntSafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(_stringify_big_ints(content), ensure_ascii=False).encode('utf-8')

@asynccontextmanager
async def lifespan(app):
    db.initialize()
    security.access_password()
    worker=None
    # Serverless: no long-lived process to own a background thread, and each
    # invocation may be a fresh container — sync runs via /api/cron/sync instead.
    if not settings.IS_SERVERLESS and os.getenv('MDB_DISABLE_WORKER')!='1':
        # The launcher owns an exclusive process lock; restart incomplete work safely.
        with db.connection() as conn:
            conn.execute("UPDATE jobs SET state='failed',error='Execução interrompida pelo reinício; sincronize novamente.',updated_at=? WHERE state='running'",(db.now(),))
        worker=Worker(); worker.start()
    yield
    if worker: worker.stop.set()

app=FastAPI(title='Mercado duBairro',version='3.0.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None,default_response_class=BigIntSafeJSONResponse)
app.include_router(users_router)
app.include_router(profile_router)
app.include_router(settings_router)
app.include_router(finance_accounts_router)
app.include_router(financial_entries_router)
app.include_router(financial_reports_router)
app.include_router(period_reviews_router)
app.include_router(loans_router)
app.include_router(obligations_router)
app.include_router(cashflow_router)
app.include_router(reconciliation_router)
app.include_router(bank_imports_router)
app.include_router(open_finance_router)
app.include_router(receivables_router)
app.include_router(replenishment_router)
app.include_router(products_router)
app.include_router(goals_router)
app.include_router(actions_router)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver','*.vercel.app'])
# /dashboard is ~420 KB of JSON per month; it was going over the wire uncompressed.
app.add_middleware(GZipMiddleware,minimum_size=1024)

@app.middleware('http')
async def secure_headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
    else:
        # Page and assets: always revalidate (a cheap 304 when unchanged). Without this, browsers
        # heuristically cached style.css and kept serving the old layout after an update.
        response.headers['Cache-Control']='no-cache'
    return response

# FastAPI's own request-body validation (a malformed field type, a missing
# required field, a failed Field(...) constraint) rejects the request before
# any route body runs, and its default handler replies with
# {"detail": [{"loc":..., "msg":..., "type":...}, ...]} — not the shared
# contract's {"detail": {code, message, fields}} shape used everywhere a
# route raises its own HTTPException (see shared-contracts.md: "Formato de
# detail: {code,message,fields}"). Reshape it here once, app-wide, instead of
# hand-validating every field FastAPI already validates for us. Verified
# before adding this that no existing test asserts on the raw default shape.
@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError):
    fields = sorted({
        str(error["loc"][-1]) for error in exc.errors() if error.get("loc")
    })
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "invalid_fields",
                "message": "Dados inválidos na requisição.",
                "fields": fields,
            }
        },
    )

class Login(BaseModel):
    email: Optional[str]=Field(default=None,max_length=254)
    password: str=Field(min_length=1,max_length=200)

@app.post('/api/login')
def login(body: Login,request:Request,response:Response):
    raw=security.login(request,body.password,body.email)
    security.set_session_cookie(response,request,raw)
    return {'csrf':security.csrf(raw)}

class PasswordReset(BaseModel):
    email: str=Field(min_length=3,max_length=254)
    master_password: str=Field(min_length=1,max_length=200)
    new_password: str=Field(min_length=8,max_length=200)

@app.post('/api/reset-password')
def reset_password(body: PasswordReset,request:Request):
    # For the sole administrator locked out of their own dashboard, with no other
    # admin to ask: whoever holds the same master password already trusted to
    # bootstrap the first account (backend/security.py: local file or
    # MDB_ACCESS_PASSWORD) can reset any user's password. No email/SMTP dependency.
    security.same_origin(request)
    security.rate_limit(request,key='reset-password')
    if not hmac.compare_digest(body.master_password,security.access_password()):
        raise HTTPException(401,'Senha mestra incorreta.')
    user=identity.reset_password(body.email,body.new_password)
    if user is None:
        raise HTTPException(404,'Usuário não encontrado.')
    return {'ok':True}

@app.get('/api/session')
def session(request:Request,auth=Depends(security.authenticate)):
    raw=request.cookies.get(security.COOKIE,'')
    # The name greets the partner on the Resumo; the e-mail stays the account identifier.
    with db.connection() as conn:
        row=conn.execute('SELECT name FROM users WHERE id=?',(auth.user_id,)).fetchone()
    return {'csrf':security.csrf(raw),'companies':permissions.companies_for(auth),'user':auth.email,
        'name':(row['name'] if row else '') or ''}

@app.post('/api/logout')
def logout(response:Response,auth=Depends(security.authenticate)):
    with db.connection() as conn: conn.execute('DELETE FROM sessions WHERE hash=?',(auth.session_hash,))
    response.delete_cookie(security.COOKIE)
    return {'ok':True}

@app.get('/api/health')
def health():
    return {'status':'ok','version':'3.0.0'}

def authorized_company(company):
    if company not in {r['id'] for r in db.companies()}:
        raise HTTPException(403,'Empresa não autorizada.')

def validate_period(period):
    try:
        parsed=date.fromisoformat(period+'-01')
        if len(period)!=7 or parsed>date.today(): raise ValueError()
    except ValueError:
        raise HTTPException(422,'Período inválido.')

def shift_period(period,delta_months):
    y,m=map(int,period.split('-'))
    idx=y*12+(m-1)+delta_months
    return f'{idx//12:04}-{idx%12+1:02}'

def pct_change(new,old):
    if new is None or old is None or old==0: return None
    return round((new/old-1)*100,2)

@app.get('/api/companies/{company}/status',dependencies=[Depends(permissions.require_permission('dashboard.read'))])
def status(company:int):
    authorized_company(company)
    periods=db.periods(company)
    # Count and timestamp only: this endpoint is polled every 60s by every open tab,
    # and reading each catalog's payload just to len() it moved megabytes per poll.
    names=['products','categories','stock','prices']
    meta=db.catalog_meta(company,names)
    catalogs={r:meta.get(r) for r in names}
    return {'jobs':db.jobs(company),'periods':periods,'catalogs':catalogs,
            'interval_minutes':settings.SYNC_SECONDS//60,'source':'Mobne · vendas PDV'}

class SyncRequest(BaseModel):
    mode:str=Field(pattern='^(recent|history|reconcile)$')

@app.post('/api/companies/{company}/sync',dependencies=[Depends(permissions.require_permission('integrations.manage'))],status_code=202)
def trigger_sync(company:int,body:SyncRequest):
    # No authorized_company() pre-check here on purpose: the very first sync for a company
    # runs against an empty companies table (nothing to check membership against yet) — sync.run()
    # already re-validates the id against the live Mobne company list before touching anything,
    # so this endpoint can safely bootstrap a brand-new database the same way the old local-only
    # CLI entrypoint (`python -m backend.sync --company ...`) always did.
    if settings.IS_SERVERLESS:
        # No background Worker is running here to pick the job off the queue: this call
        # runs it, in steps that fit the platform's time limit. 'paused' asks the page
        # to post again to continue the same job.
        return sync.run_serverless(company,body.mode)
    job_id,created=db.claim_job(company,body.mode)
    return {'job_id':job_id,'already_running':not created,'paused':False}

@app.get('/api/sync-jobs/{job_id}')
def sync_job(job_id:int,auth=Depends(security.authenticate)):
    # The administrator must be able to follow bootstrap before companies exist.
    with db.connection() as conn:
        job=conn.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if job is None:
        raise HTTPException(404,'Sincronização não encontrada.')
    permissions.ensure_permission(auth,'integrations.manage',job['company'])
    return dict(job)

@app.get('/api/cron/sync')
def cron_sync(request:Request):
    # Vercel Cron issues a GET request on schedule, with Authorization: Bearer <CRON_SECRET>.
    # See https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs.
    # vercel.json schedules it in UTC: '0 9 * * *' is 06:00 in São Paulo. It ran at
    # 03:00 BRT until 2026-09-16, when Set/2026 was published two days short because
    # Mobne had not exported 14/09 yet at that hour (see dashboard()'s covered end).
    # Hobby allows one cron a day, so this hour is the only shot the day gets.
    secret=os.environ.get('CRON_SECRET')
    if not secret or request.headers.get('authorization')!=f'Bearer {secret}':
        raise HTTPException(401,'Não autorizado.')
    started=time.monotonic()
    ids=[c['id'] for c in db.companies()]
    # A call chained below starts from the company it paused on; earlier ones are done.
    resume_from=request.query_params.get('from','')
    if resume_from.isdigit() and int(resume_from) in ids:
        ids=ids[ids.index(int(resume_from)):]
    results=[]
    for company in ids:
        outcome=sync.run_serverless(company,'recent',started=started)
        if not outcome['already_running']:
            results.append(company)
        if outcome['paused']:
            # Hobby runs this cron once a day, so nobody else would resume the job:
            # this call starts the next one. It stops when the job finishes or fails.
            host=request.headers.get('x-forwarded-host') or request.headers.get('host')
            _call_again(f'https://{host}/api/cron/sync?from={company}',secret)
            return {'synced':results,'continuing':True}
    return {'synced':results,'continuing':False}

def _call_again(url,secret):
    """Start another invocation without waiting for its answer. Only the request has
    to reach Vercel: a function is not cancelled when its caller disconnects unless
    supportsCancellation is set, and vercel.json does not set it."""
    import httpx
    try:
        httpx.get(url,headers={'authorization':f'Bearer {secret}'},timeout=httpx.Timeout(10,read=2))
    except httpx.HTTPError:
        pass

class Config(BaseModel):
    fixed_cost_cents:int=Field(ge=0,le=1000000000)

@app.put('/api/companies/{company}/config',dependencies=[Depends(permissions.require_permission('settings.manage'))])
def config(company:int,body:Config):
    authorized_company(company)
    with db.connection() as conn:
        conn.execute('INSERT INTO config VALUES(?,?) ON CONFLICT(company) DO UPDATE SET fixed_cost_cents=excluded.fixed_cost_cents',(company,body.fixed_cost_cents))
    return {'ok':True}

PRODUCT_MAP_DAYS=30

def product_map_payload(company,end,catalog,conn):
    """Product groups over the last 30 days up to `end`, not the calendar month: early in a
    month the 60% giro cut meant 8 sales days out of 13, so groups swung week to week. The
    30 days before that window give each product's previous group ("what changed")."""
    end_day=date.fromisoformat(end)
    start=(end_day-timedelta(days=PRODUCT_MAP_DAYS-1)).isoformat()
    prev_end=(end_day-timedelta(days=PRODUCT_MAP_DAYS)).isoformat()
    prev_start=(end_day-timedelta(days=2*PRODUCT_MAP_DAYS-1)).isoformat()
    receipts,analysis=_receipts_between(company,start,end,conn)
    current=models.summarize(receipts,catalog,analysis)
    prev_receipts,prev_analysis=_receipts_between(company,prev_start,prev_end,conn)
    previous={p['id']:p for p in models.summarize(prev_receipts,catalog,prev_analysis)['products']} if prev_receipts else {}
    # A product sold at cost R$ 0 has a fake 100% margin; its group means nothing on either side.
    reliable=lambda p:not (p['cost']==0 and p['revenue']>0)
    def change(p,before,to):
        return {'id':before['id'],'name':(p or before)['name'],'category':(p or before)['category'],
            'from':before['classification'],'to':to,'revenue':p['revenue'] if p else 0,'previous_revenue':before['revenue']}
    lost_star,became_low=[],[]
    now={p['id']:p for p in current['products']}
    for pid,before in previous.items():
        p=now.get(pid)
        if not reliable(before) or (p and not reliable(p)):
            continue
        if before['classification']=='Estrela' and (not p or p['classification']!='Estrela'):
            lost_star.append(change(p,before,p['classification'] if p else 'Sem vendas'))
        if p and p['classification']=='Baixo giro' and before['classification']!='Baixo giro':
            became_low.append(change(p,before,'Baixo giro'))
    order=lambda rows:sorted(rows,key=lambda r:(-r['previous_revenue'],r['id']))
    return {'start':start,'end':end,'days':PRODUCT_MAP_DAYS,'sales_days':len(current['daily']),
        'products':current['products'],
        'previous':{'start':prev_start,'end':prev_end,'available':bool(prev_receipts)},
        'changes':{'lost_star':order(lost_star),'became_low':order(became_low)}}

def dashboard_alerts(inventory,stock_synced,unknown_items,coverage=None):
    """Resumo attention points. `count` is the number behind each message, so an action
    created from it can later say how the problem moved (Central de Ações).

    `coverage` is (last day with sales, day asked of Mobne, missing days) — see
    dashboard(): a period whose receipts stop before the window we asked for is the
    difference the partner reads as "o faturamento não bate com o Mobne"."""
    alerts=[]
    if coverage and coverage[2]>=2:
        covered,requested,missing=coverage
        alerts.append({'severity':'high','type':'cobertura','count':missing,
            'message':f'O Mobne não devolveu vendas de {missing} dia(s) deste período: a última venda carregada é de '
                      f'{covered[8:]}/{covered[5:7]} e a sincronização pediu até {requested[8:]}/{requested[5:7]}. '
                      'O faturamento abaixo cobre só até essa data e fica menor que o Painel Gerencial do Mobne; '
                      'sincronize novamente quando o Mobne publicar os dias que faltam.'})
    low_stock=[p for p in inventory if p['abc']=='A' and p['stock'] is not None and p['stock']<=0]
    if not stock_synced:
        alerts.append({'severity':'medium','type':'integracao','count':None,
            'message':'Estoque ainda não sincronizado. Consulte a integração Mobne para concluir a carga.'})
    if low_stock:
        names=', '.join(p['name'] for p in low_stock[:5])+('…' if len(low_stock)>5 else '')
        alerts.append({'severity':'high','type':'estoque','count':len(low_stock),
            'message':f'{len(low_stock)} produto(s) da curva A com estoque zerado ou negativo no Mobne: {names}.'})
    underpriced=[p for p in inventory if p['current_price'] is not None and p['current_cost'] is not None
                 and p['current_price']<p['current_cost']]
    if underpriced:
        names=', '.join(p['name'] for p in underpriced[:5])+('…' if len(underpriced)>5 else '')
        alerts.append({'severity':'high','type':'preco','count':len(underpriced),
            'message':f'{len(underpriced)} produto(s) vendendo abaixo do custo atual do Mobne: {names}.'})
    if unknown_items>0:
        alerts.append({'severity':'medium','type':'custo','count':unknown_items,
            'message':f'{unknown_items} item(ns) vendido(s) sem custo conhecido — lucro do período não pôde ser calculado para eles.'})
    return alerts

@app.get('/api/companies/{company}/dashboard',dependencies=[Depends(permissions.require_permission('dashboard.read'))])
def dashboard(company:int,period:str):
    authorized_company(company); validate_period(period)
    with db.connection() as conn:
        # Read a consistent SQLite snapshot even while a worker publishes a new month.
        conn.execute('BEGIN')
        sales=db.dataset(company,'sales',period,conn)
        if not sales:
            raise HTTPException(404,'Este mês ainda não foi sincronizado. Consulte a integração Mobne.')
        if not sales['payload'].get('raw_count'):
            raise HTTPException(404,'O Mobne não tem vendas registradas neste período (ex.: antes da integração da loja).')
        products=db.dataset(company,'products',db=conn)
        catalog={r['id']:r for r in products['payload']} if products else {}
        data=models.summarize(sales['payload']['receipts'],catalog,sales['payload']['analysis'])
        # The window we asked Mobne for is not always the window Mobne answered: a day
        # is missing from /Cupom/consulta until Mobne exports it, and the sync stores the
        # requested end either way. Coverage is therefore the last day that actually has
        # sales — reporting the requested end instead made Set/2026 announce "1 a 15/09"
        # over 13 days of receipts and compare them against 15 days of Agosto.
        requested_end=sales['payload']['end']
        covered_end=data['daily'][-1]['date'] if data['daily'] else sales['payload']['start']
        missing_days=(date.fromisoformat(requested_end)-date.fromisoformat(covered_end)).days
        def comparison_for(other_period):
            other=db.dataset(company,'sales',other_period,conn)
            if not other or not other['payload'].get('raw_count'):
                return None  # Mobne has no data for this period (e.g. before onboarding); not a same-store comparison
            # Match elapsed days when the selected period is not a closed month.
            last_day=int(covered_end[-2:])
            docs=[r for r in other['payload']['receipts'] if int(r['date'][-2:])<=last_day]
            previous=models.summarize(docs)['totals']
            t=data['totals']
            return {'period':other_period,'totals':previous,
                'revenue_change':pct_change(t['revenue'],previous['revenue']),
                'profit_change':pct_change(t['profit'],previous['profit']),
                'ticket_change':pct_change(t['ticket'],previous['ticket']),
                'receipts_change':pct_change(t['receipts'],previous['receipts'])}
        comparison=comparison_for(f'{int(period[:4])-1:04}'+period[4:])  # vs. same month, prior year
        comparison_mom=comparison_for(shift_period(period,-1))          # vs. previous calendar month
        fixed=conn.execute('SELECT fixed_cost_cents FROM config WHERE company=?',(company,)).fetchone()
        fixed=fixed['fixed_cost_cents'] if fixed else 1691346
        timeline=[]
        # documents=0 means Mobne genuinely has no sales for the period (e.g. before
        # onboarding), not an unsynced gap; excluded so it never reads as a zero-revenue month.
        # Reads the cached per-period aggregate, never the payload: recomputing these
        # four totals from every historical month's receipts moved 62 MB per page load.
        for key,cached in db.period_summaries(company,conn):
            if cached is None:
                # Written before the summary was cached; pay the old cost for this row
                # alone until a sync backfills it (sync.py::_backfill_summaries).
                p=db.dataset(company,'sales',key,conn)['payload']
                cached={'end':p['end'],'totals':models.summarize(p['receipts'])['totals']}
            # Seasonality/projection pages must not read an in-progress month as a closed one.
            timeline.append({'period':key,'end':cached['end'],
                'partial':cached['end']<__import__('backend.sync',fromlist=['month_end']).month_end(key).isoformat(),
                **cached['totals']})
        stock=db.dataset(company,'stock',db=conn)
        prices=db.dataset(company,'prices',db=conn)
        # Every active product, sold or not — models.summarize() alone only returns
        # products that appear in a receipt, hiding unsold stock from every decision
        # this list feeds (low-stock alerts, replenishment, the Estoque page).
        inventory=inventory_catalog(company,period,conn)
        product_map=product_map_payload(company,sales['payload']['end'],catalog,conn)
    today=datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    margin=data['totals']['margin']
    # None when the period has any unknown-cost item, same as simulated_net below — a break-even
    # point built on a partially-unknown margin would be worse than none at all.
    break_even_cents=round(fixed/(margin/100)) if margin else None
    alerts=dashboard_alerts(inventory,stock is not None,data['totals']['unknown'],
        coverage=(covered_end,requested_end,missing_days))
    return {**data,'period':period,'start':sales['payload']['start'],'end':covered_end,
        'requested_end':requested_end,'data_gap_days':missing_days,
        'updated_at':sales['updated_at'],'version':sales['version'],'comparison':comparison,'comparison_mom':comparison_mom,
        'reconciliation':sales['payload']['reconciliation'],'raw_count':sales['payload']['raw_count'],
        'inventory':inventory,'product_map':product_map,'stock_updated_at':stock['updated_at'] if stock else None,
        'prices_updated_at':prices['updated_at'] if prices else None,'timeline':timeline,
        'fixed_cost_cents':fixed,'simulated_net':data['totals']['profit']-fixed if data['totals']['profit'] is not None else None,
        'break_even_cents':break_even_cents,'break_even_gap_pct':pct_change(data['totals']['revenue'],break_even_cents),
        'margin_goal_pct':accounts.resolve_parameter(company,'goal:margin',today),
        'revenue_goal':current_goal_progress(company,date.today()),
        'alerts':alerts,
        # Still the window's question — "is this month over?" — and not coverage: a closed
        # month whose last day happened to sell nothing must not read as in progress.
        'partial_month':requested_end<__import__('backend.sync',fromlist=['month_end']).month_end(period).isoformat(),
        'as_of':today,'scope':'Vendas PDV válidas; referências fiscais e não fiscais deduplicadas.'}

WEB=settings.ROOT/'web'
@app.get('/')
def index():
    return FileResponse(WEB/'index.html')

app.mount('/assets',StaticFiles(directory=WEB/'assets',check_dir=False),name='assets')
