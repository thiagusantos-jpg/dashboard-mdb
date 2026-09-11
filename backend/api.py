from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field
from . import database as db, models, security, settings
from . import sync
from .sync import Worker

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

app=FastAPI(title='Mercado duBairro',version='3.0.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
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
    password: str=Field(min_length=1,max_length=200)

@app.post('/api/login')
def login(body: Login,request:Request,response:Response):
    raw=security.login(request,body.password)
    response.set_cookie(security.COOKIE,raw,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=43200)
    return {'csrf':security.csrf(raw)}

@app.get('/api/session')
def session(raw=Depends(security.authenticate)):
    return {'csrf':security.csrf(raw),'companies':db.companies(),'user':'Administrador local'}

@app.post('/api/logout')
def logout(response:Response,raw=Depends(security.authenticate)):
    with db.connection() as conn: conn.execute('DELETE FROM sessions WHERE hash=?',(security.fingerprint(raw),))
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

@app.get('/api/companies/{company}/status',dependencies=[Depends(security.authenticate)])
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

@app.post('/api/companies/{company}/sync',dependencies=[Depends(security.authenticate)],status_code=202)
def trigger_sync(company:int,body:SyncRequest):
    authorized_company(company)
    job_id=db.create_job(company,body.mode)
    if settings.IS_SERVERLESS:
        # No background Worker is running here to pick the job off the queue.
        sync.run(company,body.mode,job_id=job_id)
    return {'job_id':job_id}

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

@app.put('/api/companies/{company}/config',dependencies=[Depends(security.authenticate)])
def config(company:int,body:Config):
    authorized_company(company)
    with db.connection() as conn:
        conn.execute('INSERT INTO config VALUES(?,?) ON CONFLICT(company) DO UPDATE SET fixed_cost_cents=excluded.fixed_cost_cents',(company,body.fixed_cost_cents))
    return {'ok':True}

@app.get('/api/companies/{company}/dashboard',dependencies=[Depends(security.authenticate)])
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
        price_map={r['id']:r for r in (prices['payload'] if prices else []) if r['package']=='1.0' or r['package']=='1'}
        stock_map={r['id']:r for r in (stock['payload'] if stock else [])}
        inventory=[]
        for p in data['products']:
            s=stock_map.get(p['id']); pr=price_map.get(p['id'])
            inventory.append({**p,'stock':s['quantity'] if s else None,
                'current_price':pr['price'] if pr else None,
                'current_cost':models.cents(s['unit_cost']) if s and s['unit_cost'] is not None else None,
                'last_cost':models.cents(s['last_cost']) if s and s['last_cost'] is not None else None})
    today=datetime.now(ZoneInfo('America/Sao_Paulo')).date().isoformat()
    margin=data['totals']['margin']
    # None when the period has any unknown-cost item, same as simulated_net below — a break-even
    # point built on a partially-unknown margin would be worse than none at all.
    break_even_cents=round(fixed/(margin/100)) if margin else None
    alerts=[]
    low_stock=[p for p in inventory if p['abc']=='A' and (p['stock'] is None or p['stock']<=0)]
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
