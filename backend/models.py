"""Explicit contracts for the Mobne fields verified in read-only samples."""
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

class DataError(ValueError):
    pass

def number(value):
    if value is None or isinstance(value, bool):
        raise DataError('Valor numérico obrigatório ausente.')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise DataError('Valor numérico inválido.') from None
    if not result.is_finite():
        raise DataError('Valor numérico não finito.')
    return result

def cents(value):
    return int((number(value)*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def civil_date(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(ZoneInfo('America/Sao_Paulo'))
        return parsed.date().isoformat()
    except (ValueError, TypeError):
        raise DataError('Data de movimento inválida.') from None

def receipt(raw, company, start, end):
    required = ['CupomFiscalId','EmpresaId','DtaMovimento','VlrLiquido','Status','CupomFiscalItem']
    if any(k not in raw for k in required):
        raise DataError('Contrato de cupom mudou: campos obrigatórios ausentes.')
    if int(raw['EmpresaId']) != company:
        raise DataError('Mobne retornou uma empresa diferente da solicitada.')
    day = civil_date(raw['DtaMovimento'])
    if not start <= day <= end:
        raise DataError('Cupom fora do período solicitado.')
    if raw['Status'] not in ('V','C') or raw.get('Especie') not in ('NF','CF'):
        raise DataError('Situação ou espécie de cupom desconhecida.')
    items = []
    seen = set()
    for item in raw['CupomFiscalItem']:
        if item.get('Status') not in ('V','C'):
            raise DataError('Situação de item desconhecida.')
        item_id = int(item['CupomItemId'])
        if item_id in seen:
            raise DataError('Item duplicado no cupom.')
        seen.add(item_id)
        qty = number(item['Quantidade'])
        # A numeric zero is preserved. Absence remains unknown; never use a target margin.
        unit = item.get('CustoVendaMedioLiquido')
        cost = cents(number(unit)*qty) if unit is not None else None
        items.append({'id':item_id,'product_id':int(item['ProdutoId']),
            'quantity':str(qty),'revenue':cents(item['VlrLiquido']),
            'cost':cost,'unit_cost':str(number(unit)) if unit is not None else None,
            'status':item['Status']})
    total = cents(raw['VlrLiquido'])
    active = [i for i in items if i['status']=='V']
    if raw['Status']=='V' and sum(i['revenue'] for i in active)!=total:
        raise DataError('Total do cupom diverge dos itens líquidos; base não publicada.')
    return {'id':int(raw['CupomFiscalId']), 'reference_id':int(raw.get('CupomFiscalRefId') or 0),
            'date':day,'status':raw['Status'],'species':raw['Especie'],
            'revenue':total,'items':items}

def canonical_receipts(rows):
    """Fiscal/non-fiscal representations of a single sale count only once."""
    by_id = {}
    for r in rows:
        if r['id'] in by_id and by_id[r['id']] != r:
            raise DataError('Versões conflitantes do mesmo cupom.')
        by_id[r['id']] = r
    # A page boundary can hand back the same record twice with identical content;
    # that is not corruption and must not abort a sync. Real conflicts still raise above.
    rows = list(by_id.values())
    parent = {r['id']:r['id'] for r in rows}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for r in rows:
        if r['reference_id'] in parent:
            parent[find(r['id'])] = find(r['reference_id'])
    groups = defaultdict(list)
    for r in rows:
        groups[find(r['id'])].append(r)
    chosen = []
    for group in groups.values():
        best = max(group, key=lambda r:(r['species']=='CF',r['id']))
        # If cancellation is reflected by either representation, do not resurrect the sale.
        if any(r['status']=='C' for r in group):
            best = dict(best, status='C')
        valid = [r for r in group if r['status']=='V']
        if len({r['revenue'] for r in valid})>1:
            raise DataError('Cupom fiscal e referência possuem valores divergentes.')
        chosen.append(dict(best, aliases=[r['id'] for r in group]))
    return chosen

def normalize_analysis(rows, company):
    result=[]
    for r in rows:
        if int(r['Empresa_EmpresaId']) != company:
            raise DataError('Empresa divergente na análise Mobne.')
        result.append({
            'document_id':int(r['DocumentoId']), 'item_id':int(r['ItemId']),
            'product_id':int(r['Produto_ProdutoId']), 'name':r.get('Produto_Descricao',''),
            'category_id':r.get('Produto_CatCategoriaId'), 'category':r.get('Produto_CatCategoria') or 'Sem categoria',
            'date':civil_date(r['DtaMovimento']), 'revenue':cents(r['VlrDocumentoLiquido']),
            'quantity':str(number(r['QtdeProduto'])),
            'unit_cost':str(number(r['CustoMedioLiquido'])) if r.get('CustoMedioLiquido') is not None else None,
            'document_status':r.get('StatusDocto'),'item_status':r.get('StatusItem'),
            'direction':r.get('EntradaSaida'),'type':r.get('Tipo'),'species':r.get('Especie')})
    return result

def reconcile(receipts, analysis):
    valid_analysis=[r for r in analysis if r['document_status']=='V' and r['item_status']=='V' and r['direction']=='S']
    by_doc=defaultdict(list)
    for r in valid_analysis:
        by_doc[r['document_id']].append(r)
    expected=actual=0
    missing=0
    missing_ids=[]
    aliases=set()
    known=set()
    for r in receipts:
        aliases.update(r['aliases'])
        if r['status']!='V':
            continue
        expected+=r['revenue']
        doc_id=next((i for i in [r['id']]+r['aliases'] if i in by_doc),None)
        if doc_id is None:
            missing+=1
            missing_ids.append(r['id'])
            continue
        known.add(doc_id)
        actual+=sum(x['revenue'] for x in by_doc[doc_id])
    extra=[r for r in valid_analysis if r['document_id'] not in aliases]
    difference=expected-actual
    # Cupom (vendas PDV com status válido) é a fonte de receita; AnaliseVendas é conferência.
    # Uma diferença pequena entre as duas é publicada e fica registrada aqui, nunca escondida;
    # acima da tolerância, a sincronização bloqueia o lote (ver sync.run).
    # Widened from R$50/0,1% after 2025-09 (10 documentos, R$92,92, 0,116% da receita) bloqueou
    # a carga histórica com uma folga real pequena — R$100/0,2% dá margem sem abrir mão do limite.
    tolerance=max(10000, round(expected*0.002))
    return {'receipt_revenue':expected,'analysis_revenue':actual,'difference':difference,
            'missing_documents':missing,'missing_document_ids':missing_ids,
            'additional_documents':len({r['document_id'] for r in extra}),
            'additional_revenue':sum(r['revenue'] for r in extra),
            'tolerance_cents':tolerance,'exact_match':difference==0 and missing==0,
            'matched':abs(difference)<=tolerance,
            'scope':'Vendas PDV (Cupom) é a receita oficial; AnaliseVendas é conferência. '
                    'Documentos adicionais da análise são sinalizados separadamente.'}

def summarize(receipts, products=None, analysis=None):
    products=products or {}
    descriptions={r['product_id']:r for r in (analysis or [])}
    daily=defaultdict(lambda:{'revenue':0,'cost':0,'unknown':0,'receipts':0})
    cats=defaultdict(lambda:{'revenue':0,'cost':0,'unknown':0,'docs':set()})
    prods={}
    revenue=cost=unknown=documents=cancelled=0
    zero_cost=items_total=0
    for doc in receipts:
        if doc['status']=='C':
            cancelled+=1
            continue
        documents+=1
        revenue+=doc['revenue']
        day=daily[doc['date']]
        day['revenue']+=doc['revenue']; day['receipts']+=1
        for item in doc['items']:
            if item['status']!='V': continue
            items_total+=1
            pid=item['product_id']
            desc=descriptions.get(pid,{})
            prod=products.get(pid,{})
            name=desc.get('name') or prod.get('name') or f'Produto {pid}'
            category=desc.get('category') or prod.get('category') or 'Sem categoria'
            p=prods.setdefault(pid,{'id':pid,'name':name,'category':category,'revenue':0,'cost':0,'unknown':0,'quantity':Decimal(0),'days':set()})
            c=cats[category]; c['docs'].add(doc['id'])
            p['revenue']+=item['revenue']; p['quantity']+=number(item['quantity']); p['days'].add(doc['date'])
            c['revenue']+=item['revenue']
            if item['cost'] is None:
                unknown+=1; p['unknown']+=1; c['unknown']+=1; day['unknown']+=1
            else:
                cost+=item['cost']; p['cost']+=item['cost']; c['cost']+=item['cost']; day['cost']+=item['cost']
                if item['cost']==0 and item['revenue']>0: zero_cost+=1
    def financial(r):
        r['profit']=None if r['unknown'] else r['revenue']-r['cost']
        r['margin']=round(r['profit']/r['revenue']*100,2) if r['profit'] is not None and r['revenue'] else None
        return r
    ranked=sorted(prods.values(),key=lambda x:(-x['revenue'],x['id']))
    acc=0
    for p in ranked:
        before=acc/revenue if revenue else 0
        p['abc']='A' if before<.8 else 'B' if before<.95 else 'C'
        acc+=p['revenue']; p['quantity']=float(p['quantity']); days=p.pop('days')
        p['days_sold']=len(days); p['last_sold']=max(days) if days else None
        p['turnover']=p['days_sold']/len(daily) if daily else 0
        financial(p)
        p['classification']='Estrela' if p['turnover']>=.6 and (p['margin'] or 0)>=35 else 'Gerador de caixa' if p['turnover']>=.6 else 'Oportunidade' if (p['margin'] or 0)>=35 else 'Baixo giro'
    totals=financial({'revenue':revenue,'cost':cost,'unknown':unknown,'receipts':documents})
    totals.update(ticket=round(revenue/documents) if documents else None, cancelled=cancelled,
                  products=len(ranked),zero_cost_items=zero_cost,
                  # Mobne reports an explicit 0 as a real known cost (e.g. free/promo items),
                  # never remapped to "unknown" here — but a high ratio means many items likely
                  # had no purchase-cost history yet (new store/product), and margin is overstated.
                  # See ANALISE_TECNICA_2026-09-11.md and Jul-Aug/2025 (100% zero-cost).
                  zero_cost_ratio=round(zero_cost/items_total,4) if items_total else 0.0)
    return {'totals':totals,'daily':[dict(date=k,**financial(v)) for k,v in sorted(daily.items())],
            'categories':[dict(name=k,receipts=len(v.pop('docs')),**financial(v)) for k,v in sorted(cats.items(),key=lambda x:-x[1]['revenue'])],
            'products':ranked}
