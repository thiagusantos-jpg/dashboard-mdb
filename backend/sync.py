from __future__ import annotations
import argparse
import calendar
import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from . import database as db, models, settings
from .mobne import MobneClient, MobneError
from .operations.snapshots import snapshot_catalogs

def month_end(period):
    year,month=map(int,period.split('-'))
    return date(year,month,calendar.monthrange(year,month)[1])

def period_summary(payload):
    """The dashboard timeline's whole per-period need: an end date and four totals.

    Cached on the row (datasets.summary) so the timeline never reads the receipts
    again — recomputing these from every historical month cost 62 MB per page load.
    Computed exactly as the timeline used to, products/analysis left out on purpose.
    """
    return {'end':payload['end'],'totals':models.summarize(payload['receipts'])['totals']}

def _backfill_summaries(company):
    """Cache the timeline aggregate for periods synced before it was stored.

    A daily 'recent' run only rewrites recent months, so without this the store's
    older periods would keep paying the full-payload cost forever. Reads each
    missing period once, here — inside a job that already has a progress channel
    and a generous time budget — instead of on a user's page load.
    """
    for period in db.periods_missing_summary(company):
        row=db.dataset(company,'sales',period)
        if not row or not isinstance(row['payload'],dict) or 'receipts' not in row['payload']:
            continue
        db.set_period_summary(company,period,period_summary(row['payload']))

def month_range(start,end):
    y,m=map(int,start.split('-'))
    result=[]
    while f'{y:04}-{m:02}'<=end:
        result.append(f'{y:04}-{m:02}')
        m+=1
        if m==13: y+=1; m=1
    return result

def catalog_payload(resource,rows,categories=None):
    if resource=='categories':
        return [{'id':int(r['CategoriaId']),'name':r['Categoria']} for r in rows]
    if resource=='products':
        return [{'id':int(r['ProdutoId']),'name':r['Descricao'],'category_id':r.get('CategoriaId'),
                 'category':(categories or {}).get(r.get('CategoriaId'),'Sem categoria'),'status':r.get('Status')} for r in rows]
    if resource=='stock':
        return [{'id':int(r['ProdutoId']),'quantity':float(models.number(r['QtdeEstoque'])),
            'reserved':float(models.number(r.get('QtdeReservaPedidoVenda',0))),
            'unit_cost':str(models.number(r['CustoMedioLiquido'])) if r.get('CustoMedioLiquido') is not None else None,
            'last_cost':str(models.number(r['CustoMedioUltimaEntradaLiquido'])) if r.get('CustoMedioUltimaEntradaLiquido') is not None else None,
            'cursor':r.get('NroBaseExportacao',0)} for r in rows]
    if resource=='prices':
        return [{'id':int(r['ProdutoId']),'package':str(models.number(r['QtdEmbalagem'])),
            'price':models.cents(r['PrecoUnitario']),'updated_at':r.get('DtaAlteracao'),'cursor':r.get('NroBaseExportacao',0)} for r in rows]
    raise ValueError(resource)

def run(company,mode='recent',period=None,job_id=None,client=None):
    db.initialize()
    job_id=job_id or db.create_job(company,mode)
    client=client or MobneClient()
    today=datetime.now(ZoneInfo('America/Sao_Paulo')).date()
    try:
        db.update_job(job_id,state='running',detail='Validando empresa')
        companies=client.fetch_all('companies')
        if company not in {int(r['EmpresaId']) for r in companies}:
            raise MobneError('Empresa não autorizada pela credencial Mobne.')
        db.save_companies(companies)
        if mode=='history':
            periods=month_range('2025-01',today.strftime('%Y-%m'))
            # Completed immutable past windows are checkpoints. Failed windows are fetched again in full.
            completed={r['period'] for r in db.periods(company)}
            periods=[p for p in periods if p not in completed or p==today.strftime('%Y-%m')]
        elif mode=='month':
            periods=[period]
        else:
            first=today.replace(day=1)
            periods=[(first-timedelta(days=1)).strftime('%Y-%m'),today.strftime('%Y-%m')]
            if mode=='reconcile': periods=month_range('2025-01',today.strftime('%Y-%m'))
        db.update_job(job_id,total=len(periods)+4,completed=0)
        # Sales first: a catalog outage must not erase the last good sales dataset.
        # Each period is independent: a bad reconciliation or contract surprise in one
        # month must not block the clean months around it (see ANALISE_TECNICA_2026-09-11.md).
        # Only a MobneError (network/auth/contract-wide) aborts the whole run below, since
        # it will most likely repeat for every remaining period too.
        failed_periods=[]
        for index,p in enumerate(periods):
            end=min(month_end(p),today).isoformat(); start=p+'-01'
            try:
                if start>end: raise models.DataError('Período futuro não permitido.')
                def progress(page,pages,count,total):
                    db.update_job(job_id,detail=f'Cupons {p}: página {page}/{pages} · {count}/{total}')
                raw=client.fetch_all('receipts',{'Filter.EmpresaId':company,
                    'Filter.DataMovimentoInicial':start+'T00:00:00',
                    'Filter.DataMovimentoFinal':end+'T23:59:59','Filter.Especie':'T'},progress)
                canonical=models.canonical_receipts([models.receipt(r,company,start,end) for r in raw])
                db.update_job(job_id,detail=f'Reconciliando {p} com a análise Mobne')
                analysis=models.normalize_analysis(client.analysis(company,start,end),company)
                check=models.reconcile(canonical,analysis)
                if not check['matched']:
                    raise models.DataError(f'Reconciliação {p}: diferença de {check["difference"]/100:.2f}; {check["missing_documents"]} documentos sem correspondência. Base anterior preservada.')
                with db.connection() as conn:
                    db.put_dataset(company,'sales',p,{'receipts':canonical,'analysis':analysis,
                        'reconciliation':check,'raw_count':len(raw),'start':start,'end':end},conn,documents=len(raw),
                        summary=period_summary({'receipts':canonical,'end':end}))
                db.update_job(job_id,completed=index+1,detail=f'{p} reconciliado e salvo')
            except (models.DataError,KeyError,ValueError,TypeError) as e:
                message=str(e) if isinstance(e,models.DataError) else f'Contrato Mobne incompatível em {p}; consulte a cobertura da sincronização.'
                failed_periods.append((p,message))
                db.update_job(job_id,completed=index+1,detail=f'{p}: falhou — base anterior preservada')
        # Full daily refresh, with per-entity atomic replacement. Current observations never rewrite historical costs.
        categories=db.dataset(company,'categories')
        cats={r['id']:r['name'] for r in categories['payload']} if categories else {}
        # Timestamps only: deciding "is this catalog younger than a day?" used to load
        # each payload in full (~4.4 MB per run) to read one field off it.
        freshness=db.catalog_meta(company,['categories','products','stock','prices'])
        for i,resource in enumerate(['categories','products','stock','prices']):
            cached=freshness.get(resource)
            age=(datetime.fromisoformat(db.now())-datetime.fromisoformat(cached['updated_at'])).total_seconds() if cached else float('inf')
            if age<86400:
                db.update_job(job_id,completed=len(periods)+i+1,detail=f'{resource}: base atual preservada')
                continue
            def progress(page,pages,count,total):
                db.update_job(job_id,detail=f'{resource}: página {page}/{pages} · {count}/{total}')
            rows=client.fetch_all(resource,{} if resource=='categories' else {'Filter.EmpresaId':company},progress)
            payload=catalog_payload(resource,rows,cats)
            with db.connection() as conn:
                if resource in ('stock','prices'):
                    # Server-side copy: the superseded payload never leaves the database.
                    db.copy_dataset(company,resource,resource+'_previous',conn)
                db.put_dataset(company,resource,'current',payload,conn)
            if resource=='categories': cats={r['id']:r['name'] for r in payload}
            db.update_job(job_id,completed=len(periods)+i+1)
        # One row per product per day — the point-in-time history the previous
        # design lost by only ever keeping the latest stock/price dataset.
        snapshot_catalogs(company,today)
        if failed_periods:
            ok=len(periods)-len(failed_periods)
            db.update_job(job_id,state='completed_with_errors',
                detail=f'{ok}/{len(periods)} períodos sincronizados; {len(failed_periods)} com falha (execute novamente para tentar de novo)',
                error='; '.join(f'{p}: {msg}' for p,msg in failed_periods))
        else:
            db.update_job(job_id,state='completed',detail='Sincronização concluída',error=None)
    except (MobneError,models.DataError,KeyError,ValueError,TypeError) as e:
        message=str(e) if isinstance(e,(MobneError,models.DataError)) else 'Contrato Mobne incompatível; consulte a cobertura da sincronização.'
        db.update_job(job_id,state='failed',error=message,detail='Execução interrompida; lotes completos preservados')
    except Exception:
        db.update_job(job_id,state='failed',error='Falha interna de sincronização. Os lotes completos foram preservados.')
        raise
    finally:
        client.close()
        # Deliberately outside the try above, so a Mobne outage doesn't also freeze
        # work that never touches Mobne: this only reads rows already in our database
        # and fills one derived column on them. Guarded, because an exception raised
        # from a finally block would replace the job's own outcome — including the
        # real error a user needs to see. Best-effort by design: whatever it misses,
        # the next run picks up, and the dashboard recomputes from the payload
        # meanwhile (see api.py::dashboard's timeline fallback).
        try:
            _backfill_summaries(company)
        except Exception:
            pass
    return job_id

class Worker:
    def __init__(self):
        self.stop=threading.Event()
        self.thread=threading.Thread(target=self.loop,daemon=True,name='mobne-sync')
    def start(self):
        self.thread.start()
    def loop(self):
        # One local process, plus database uniqueness to prevent conflicting manual jobs.
        next_run=0
        import time
        while not self.stop.is_set():
            try:
                with db.connection() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    job=conn.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
                    if job: conn.execute("UPDATE jobs SET state='running',updated_at=? WHERE id=?",(db.now(),job['id']))
                if job:
                    run(job['company'],job['mode'],job_id=job['id'])
                elif time.time()>=next_run:
                    for c in db.companies():
                        latest=db.jobs(c['id'])
                        if not latest or (datetime.fromisoformat(db.now())-datetime.fromisoformat(latest[0]['updated_at'])).total_seconds()>=settings.SYNC_SECONDS:
                            db.create_job(c['id'],'recent')
                    next_run=time.time()+60
            except Exception:
                # Never log upstream responses or secrets; job status contains the actionable failure.
                pass
            self.stop.wait(2)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--company',type=int,default=218)
    parser.add_argument('--mode',choices=['month','recent','history','reconcile'],default='recent')
    parser.add_argument('--period',default='2026-01')
    args=parser.parse_args()
    job=run(args.company,args.mode,args.period)
    result=next(r for r in db.jobs(args.company) if r['id']==job)
    print({k:result[k] for k in ['id','state','detail','error']})
    raise SystemExit(0 if result['state']=='completed' else 1)
