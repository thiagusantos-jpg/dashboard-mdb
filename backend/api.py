from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from . import database as db, models, security, settings
from .sync import Worker

@asynccontextmanager
async def lifespan(app):
    db.initialize()
    security.access_password()
    worker=None
    if os.getenv('MDB_DISABLE_WORKER')!='1':
        # The launcher owns an exclusive process lock; restart incomplete work safely.
        with db.connection() as conn:
            conn.execute("UPDATE jobs SET state='failed',error='Execução interrompida pelo reinício; sincronize novamente.',updated_at=? WHERE state='running'",(db.now(),))
        worker=Worker(); worker.start()
    yield
    if worker: worker.stop.set()

app=FastAPI(title='Mercado duBairro',version='3.0.0',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','testserver'])

@app.middleware('http')
async def secure_headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'
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
def sync(company:int,body:SyncRequest):
    authorized_company(company)
    return {'job_id':db.create_job(company,body.mode)}

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
        products=db.dataset(company,'products',db=conn)
        catalog={r['id']:r for r in products['payload']} if products else {}
        data=models.summarize(sales['payload']['receipts'],catalog,sales['payload']['analysis'])
        prior_period=f'{int(period[:4])-1:04}'+period[4:]
        prior=db.dataset(company,'sales',prior_period,conn)
        comparison=None
        if prior:
            # Match elapsed days when the selected period is not a closed month.
            last_day=int(sales['payload']['end'][-2:])
            docs=[r for r in prior['payload']['receipts'] if int(r['date'][-2:])<=last_day]
            previous=models.summarize(docs)['totals']
            comparison={'period':prior_period,'totals':previous,
                'revenue_change':round((data['totals']['revenue']/previous['revenue']-1)*100,2) if previous['revenue'] else None}
        fixed=conn.execute('SELECT fixed_cost_cents FROM config WHERE company=?',(company,)).fetchone()
        fixed=fixed['fixed_cost_cents'] if fixed else 1691346
        timeline=[]
        for r in conn.execute("SELECT period,payload FROM datasets WHERE company=? AND resource='sales' ORDER BY period",(company,)):
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
    return {**data,'period':period,'start':sales['payload']['start'],'end':sales['payload']['end'],
        'updated_at':sales['updated_at'],'version':sales['version'],'comparison':comparison,
        'reconciliation':sales['payload']['reconciliation'],'raw_count':sales['payload']['raw_count'],
        'inventory':inventory,'stock_updated_at':stock['updated_at'] if stock else None,
        'prices_updated_at':prices['updated_at'] if prices else None,'timeline':timeline,
        'fixed_cost_cents':fixed,'simulated_net':data['totals']['profit']-fixed if data['totals']['profit'] is not None else None,
        'partial_month':sales['payload']['end']<__import__('backend.sync',fromlist=['month_end']).month_end(period).isoformat(),
        'as_of':today,'scope':'Vendas PDV válidas; referências fiscais e não fiscais deduplicadas.'}

WEB=settings.ROOT/'web'
@app.get('/')
def index():
    return FileResponse(WEB/'index.html')

app.mount('/assets',StaticFiles(directory=WEB/'assets',check_dir=False),name='assets')
