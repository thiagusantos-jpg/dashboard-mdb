from __future__ import annotations
import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from . import database as db, identity, models, permissions, security, settings
from . import sync
from .operations.catalog import inventory_catalog
from .routes.finance_accounts import router as finance_accounts_router
from .routes.financial_entries import router as financial_entries_router
from .routes.bank_imports import router as bank_imports_router
from .routes.cashflow import router as cashflow_router
from .routes.financial_reports import router as financial_reports_router
from .routes.loans import router as loans_router
from .routes.open_finance import router as open_finance_router
from .routes.products import router as products_router
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
app.include_router(settings_router)
app.include_router(finance_accounts_router)
app.include_router(financial_entries_router)
app.include_router(financial_reports_router)
app.include_router(loans_router)
app.include_router(cashflow_router)
app.include_router(reconciliation_router)
app.include_router(bank_imports_router)
app.include_router(open_finance_router)
app.include_router(receivables_router)
app.include_router(replenishment_router)
app.include_router(products_router)
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

class Login(BaseModel):
    email: Optional[str]=Field(default=None,max_length=254)
    password: str=Field(min_length=1,max_length=200)

@app.post('/api/login')
def login(body: Login,request:Request,response:Response):
    raw=security.login(request,body.password,body.email)
    response.set_cookie(security.COOKIE,raw,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=43200)
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
    return {'csrf':security.csrf(raw),'companies':permissions.companies_for(auth),'user':auth.email}

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
    catalogs={}
    for r in ['products','categories','stock','prices']:
        d=db.dataset(company,r)
        catalogs[r]={'count':len(d['payload']),'updated_at':d['updated_at']} if d else None
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
    job_id=db.create_job(company,body.mode)
    if settings.IS_SERVERLESS:
        # No background Worker is running here to pick the job off the queue.
        sync.run(company,body.mode,job_id=job_id)
    return {'job_id':job_id}

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
    secret=os.environ.get('CRON_SECRET')
    if not secret or request.headers.get('authorization')!=f'Bearer {secret}':
        raise HTTPException(401,'Não autorizado.')
    results=[]
    for c in db.companies():
        job_id=db.create_job(c['id'],'recent')
        sync.run(c['id'],'recent',job_id=job_id)
        results.append(c['id'])
    return {'synced':results}

class Config(BaseModel):
    fixed_cost_cents:int=Field(ge=0,le=1000000000)

@app.put('/api/companies/{company}/config',dependencies=[Depends(permissions.require_permission('settings.manage'))])
def config(company:int,body:Config):
    authorized_company(company)
    with db.connection() as conn:
        conn.execute('INSERT INTO config VALUES(?,?) ON CONFLICT(company) DO UPDATE SET fixed_cost_cents=excluded.fixed_cost_cents',(company,body.fixed_cost_cents))
    return {'ok':True}

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
        def comparison_for(other_period):
            other=db.dataset(company,'sales',other_period,conn)
            if not other or not other['payload'].get('raw_count'):
                return None  # Mobne has no data for this period (e.g. before onboarding); not a same-store comparison
            # Match elapsed days when the selected period is not a closed month.
            last_day=int(sales['payload']['end'][-2:])
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
        for r in conn.execute("SELECT period,payload FROM datasets WHERE company=? AND resource='sales' AND documents>0 ORDER BY period",(company,)):
            p=__import__('json').loads(r['payload'])
            s=models.summarize(p['receipts'])['totals']
            # Seasonality/projection pages must not read an in-progress month as a closed one.
            timeline.append({'period':r['period'],'end':p['end'],
                'partial':p['end']<__import__('backend.sync',fromlist=['month_end']).month_end(r['period']).isoformat(),**s})
        stock=db.dataset(company,'stock',db=conn)
        prices=db.dataset(company,'prices',db=conn)
        # Every active product, sold or not — models.summarize() alone only returns
        # products that appear in a receipt, hiding unsold stock from every decision
        # this list feeds (low-stock alerts, replenishment, the Estoque page).
        inventory=inventory_catalog(company,period,conn)
    today=datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    margin=data['totals']['margin']
    # None when the period has any unknown-cost item, same as simulated_net below — a break-even
    # point built on a partially-unknown margin would be worse than none at all.
    break_even_cents=round(fixed/(margin/100)) if margin else None
    alerts=[]
    low_stock=[p for p in inventory if p['abc']=='A' and p['stock'] is not None and p['stock']<=0]
    if stock is None:
        alerts.append({'severity':'medium','type':'integracao',
            'message':'Estoque ainda não sincronizado. Consulte a integração Mobne para concluir a carga.'})
    if low_stock:
        names=', '.join(p['name'] for p in low_stock[:5])+('…' if len(low_stock)>5 else '')
        alerts.append({'severity':'high','type':'estoque',
            'message':f'{len(low_stock)} produto(s) da curva A com estoque zerado ou negativo no Mobne: {names}.'})
    underpriced=[p for p in inventory if p['current_price'] is not None and p['current_cost'] is not None
                 and p['current_price']<p['current_cost']]
    if underpriced:
        names=', '.join(p['name'] for p in underpriced[:5])+('…' if len(underpriced)>5 else '')
        alerts.append({'severity':'high','type':'preco',
            'message':f'{len(underpriced)} produto(s) vendendo abaixo do custo atual do Mobne: {names}.'})
    if data['totals']['unknown']>0:
        alerts.append({'severity':'medium','type':'custo',
            'message':f'{data["totals"]["unknown"]} item(ns) vendido(s) sem custo conhecido — lucro do período não pôde ser calculado para eles.'})
    return {**data,'period':period,'start':sales['payload']['start'],'end':sales['payload']['end'],
        'updated_at':sales['updated_at'],'version':sales['version'],'comparison':comparison,'comparison_mom':comparison_mom,
        'reconciliation':sales['payload']['reconciliation'],'raw_count':sales['payload']['raw_count'],
        'inventory':inventory,'stock_updated_at':stock['updated_at'] if stock else None,
        'prices_updated_at':prices['updated_at'] if prices else None,'timeline':timeline,
        'fixed_cost_cents':fixed,'simulated_net':data['totals']['profit']-fixed if data['totals']['profit'] is not None else None,
        'break_even_cents':break_even_cents,'break_even_gap_pct':pct_change(data['totals']['revenue'],break_even_cents),
        'alerts':alerts,
        'partial_month':sales['payload']['end']<__import__('backend.sync',fromlist=['month_end']).month_end(period).isoformat(),
        'as_of':today,'scope':'Vendas PDV válidas; referências fiscais e não fiscais deduplicadas.'}

WEB=settings.ROOT/'web'
@app.get('/')
def index():
    return FileResponse(WEB/'index.html')

app.mount('/assets',StaticFiles(directory=WEB/'assets',check_dir=False),name='assets')
