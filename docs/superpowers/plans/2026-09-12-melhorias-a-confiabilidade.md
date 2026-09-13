# A — Confiabilidade dos cálculos e da sessão — Implementation Plan

> **Status (13/09/2026):** executado e integrado ao main (merge a803233); baseline em docs/validation/2026-09-12-baseline.md.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Corrigir as três inconsistências reproduzidas e impedir que atualizações ou respostas atrasadas apaguem formulários ou troquem o conteúdo da rota.

**Architecture:** Corrigir seleções/agregações existentes e acrescentar um coordenador pequeno de carregamento no frontend. Manter contratos públicos compatíveis.

**Tech Stack:** Stack existente; pytest com SQLite temporário e node:test com relógio/fetch controlados.

**Spec:** [Plano mestre](2026-09-12-melhorias-consolidadas.md) e [análise](../../../ANALISE_UX_2026-09-12.md).

## Global Constraints

Aplicam-se integralmente as restrições e regras do plano mestre. Cada tarefa: teste RED do defeito, correção mínima, teste GREEN e commit apenas dos arquivos da tarefa. Reutilizar fixtures locais existentes; não criar banco em `.runtime`.

## A1 — Saldo parcialmente pago na previsão

**Files:** Modify `backend/finance/forecast.py`; Test `tests/finance/test_forecast.py`.

**Interfaces:** `forecast(company,start,end,scenario='base',*,as_of=None)->dict` permanece. `_open_entries_due` passa a usar soma líquida de pagamentos por lançamento e devolver apenas saldo positivo com sinal da natureza.

- [x] Acrescentar teste à fixture `forecast_db` existente:

```python
def test_partial_balance_stays_in_forecast(forecast_db):
    from backend.finance.entries import settle_entry
    account = accounts.account_by_key(1, 'rent')
    entry = create_entry(EntryCommand(
        company_id=1, account_id=account['id'], amount_cents=100000,
        competence='2026-09', due_date=date(2026, 9, 20), source='manual',
        external_id=None, description='Aluguel'))
    settle_entry(entry['id'], 40000, paid_at=date(2026, 9, 12))
    result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
    amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
               if i['source'] == 'financial_entries']
    assert amounts == [-60000]
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_forecast.py -q`; confirmar falha por saldo ausente.
- [x] Incluir `partially_paid` e reutilizar semântica de `_paid_cents` em `entries.py` por agregação, sem N+1 por lançamento. Aplicar `remaining=max(0,total-paid)` antes de atribuir sinal. Tratar vencidos no dia inicial compatível com a janela; evitar perder vencidos quando start for posterior a hoje.

```python
remaining = max(0, row['amount_cents'] - row['paid_cents'])
signed = remaining if row['nature'] in ('revenue', 'financing_inflow') else -remaining
```

- [x] Cobrir quitado sem previsão, parcial vencido, pagamento estornado e janela futura; rodar `.venv/bin/pytest tests/finance/test_forecast.py tests/finance/test_entries.py -q`.
- [x] Commit: `fix: preserve outstanding balances in cash forecast`.

## A2 — Categorias arquivadas no histórico

**Files:** Modify `backend/finance/reporting.py`; Test `tests/finance/test_reporting.py` e `tests/finance/test_accounts.py`.

**Interfaces:** `management_result(company,period,store=None)->dict` conserva chaves. Categorias arquivadas entram na leitura histórica, não na lista de categorias disponíveis para novos lançamentos.

- [x] Na fixture isolada de reporting, criar categoria personalizada com `accounts.create_account(1,'Manutenção',accounts.AccountNature.OPERATING_EXPENSE)` e lançamento de 100000 centavos em setembro; obter `version` retornada pela criação. Adicionar estas asserções antes/depois de `accounts.archive_account`:

```python
before = management_result(1, '2026-09')['operating_expenses_cents']
accounts.archive_account(1, account['id'], expected_version=account['version'])
after = management_result(1, '2026-09')['operating_expenses_cents']
assert before == after == 100000
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_reporting.py -q`; confirmar desaparecimento indevido.
- [x] Trocar iteração do relatório por `list_accounts(company, include_archived=True)`; manter filtro de categoria ativa nas escritas, permitindo preservar categoria antiga em edição apenas descritiva.
- [x] Testar categoria sem movimento, categorias padrão e resultado de competência anterior; rodar `.venv/bin/pytest tests/finance/test_reporting.py tests/finance/test_accounts.py -q`.
- [x] Commit: `fix: retain archived categories in historical results`.

## A3 — Cronograma vigente de empréstimos

**Files:** Modify `backend/finance/forecast.py`; Test `tests/finance/test_forecast.py`, `tests/finance/test_loans.py`.

**Interfaces:** `_open_installments_due` retorna somente parcelas abertas do cronograma ativo do contrato. Histórico pago continua consultável por `loan_position`.

- [x] Criar contrato sintético com principal 100000 e juros 10000, vencimento 20/09, e renegociar para principal 100000 e juros 5000, vencimento 25/09. Verificar:

```python
result = forecast(1, AS_OF, date(2026, 9, 30), as_of=AS_OF)
amounts = [i['amount_cents'] for d in result['days'] for i in d['items']
           if i['source'] == 'loan_installments']
assert amounts == [-105000]
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_forecast.py -q`; confirmar que antes há duas parcelas.
- [x] Restringir a consulta:

```sql
JOIN loan_schedules s ON s.id=i.schedule_id
WHERE l.company=? AND i.schedule_id=l.active_schedule_id
  AND s.status='active' AND i.status='open'
```

- [x] Manter comparação por vencimento existente e testar renegociação com parcela já paga. Rodar `.venv/bin/pytest tests/finance/test_forecast.py tests/finance/test_loans.py -q`.
- [x] Commit: `fix: forecast only active loan schedules`.

## A4 — Carregamento e atualização sem perda de contexto

**Files:** Create `web/assets/page-state.js`, `tests/test_page_state_frontend.cjs`; Modify `web/index.html`, `web/assets/app.js`, `web/assets/settings.js`, `web/assets/finance.js`, `web/assets/loans.js`, `web/assets/cashflow.js`, `web/assets/reconciliation.js`.

**Interfaces:** global `createPageState()->{begin(key),isCurrent(token),markDirty(value),canRefresh(),reset()}`. `begin` invalida token anterior; `reset` invalida todos. Guardar no APP `pageState`, `statusTimer`, `pollTimer`; ambos os timers encerrados no logout e antes de novo login. `api` aceita `signal`; erros AbortError não viram mensagem de sessão expirada.

- [x] Testar coordenação sem DOM via VM de Node, seguindo o padrão dos testes existentes:

```javascript
const state = createPageState();
const old = state.begin('resumo/2026-09');
const current = state.begin('configuracoes/empresa');
assert.equal(state.isCurrent(old), false);
assert.equal(state.isCurrent(current), true);
state.markDirty(true);
assert.equal(state.canRefresh(), false);
state.reset();
assert.equal(state.isCurrent(current), false);
```

- [x] Rodar `node --test tests/test_page_state_frontend.cjs` e confirmar RED antes da criação da implementação.
- [x] Implementar contador monotônico e flag dirty. Capturar token/empresa/período no início de cada render; antes de escrever conteúdo ou erro, verificar token. Não usar APP mutável depois de await para identificar a resposta. Instalar escuta de input/change no formulário para marcar dirty e limpar somente após salvamento ou descarte explícito.
- [x] Separar polling de status da renderização. Quando dados mudarem com formulário dirty, mostrar “Novos dados disponíveis” e atualizar sem substituir os campos. Ao navegar com alterações, oferecer Continuar editando/Descartar, mantendo a rota original se cancelar. Não salvar senha em storage nem rascunho persistente de campos sensíveis.
- [x] Adicionar teste com duas Promises resolvidas em ordem inversa e fake timers: nenhuma resposta antiga sobrescreve a rota; formulário permanece após 60 segundos; login/logout repetido não multiplica timers; status indisponível preserva conteúdo anterior. Timeout configurável de API em 30 segundos com botão Tentar novamente nas consultas, sem repetir POST automaticamente.
- [x] Rodar `node --test tests/*.cjs`; confirmar no navegador troca rápida entre resumo/configurações/despesas e retorno pelo histórico.
- [x] Commit: `fix: preserve forms and discard obsolete page requests`.

## A5 — Regressão e diagnóstico de escala

**Files:** Create `docs/validation/2026-09-12-baseline.md`; Modify testes de A1–A4 somente se necessário para cobrir a jornada completa.

- [x] Rodar `.venv/bin/pytest tests -q` e `node --test tests/*.cjs`; registrar comandos, ambiente e resultados reais, sem declarar PostgreSQL validado por testes SQLite.
- [x] Em banco descartável, medir `dashboard`, `management_result`, `forecast` em 1/12/24 meses, após aquecimento e em 20 consultas sequenciais por cenário. Registrar mediana, p95, tamanho de resposta e número de consultas; não copiar dados privados para artefatos.
- [x] Registrar gargalos medidos para C7. Não substituir esta tarefa por mudança preventiva de framework ou sistema de build.
- [x] Commit dos resultados de validação: `docs: record reliability validation baseline`.

**Gate A:** três regressões verdes; campos e rota preservados; nenhuma mutação no banco real. O gate não certifica migrações e operações novas de B.
