# B — Ciclo completo das obrigações financeiras — Implementation Plan

> **Status (13/09/2026):** executado no main (41ca55c … d724563, correções 91d86e3, 9c056c1).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Permitir corrigir lançamentos e pagamentos, integrar parcelas à agenda e manter resultado, dívida e caixa consistentes.

**Architecture:** Manter financial_entries para despesas e loans/loan_installments para contratos. Criar consulta unificada de obrigações e serviço transacional de pagamentos, sem copiar principal de empréstimo como despesa. Adaptar APIs antigas para as mesmas regras.

**Tech Stack:** FastAPI, SQLite/PostgreSQL, pytest e frontend existente.

**Spec:** [Plano mestre](2026-09-12-melhorias-consolidadas.md). Depende de A1–A4.

## Global Constraints

Aplicam-se as regras do mestre. Novos IDs públicos são strings. Guardar valores em centavos e não recalcular pagamentos antigos. Para cada tarefa: teste comportamental RED, implementação mínima, GREEN e commit. As assinaturas abaixo são contratos novos a implementar, não funções já disponíveis.

## Contratos compartilhados

Todas as rotas abaixo têm prefixo `/api/companies/{company}/finance`. Nova consulta:

```json
{
  "items": [{
    "key": "entry:123", "kind": "entry", "id": "123", "version": 1,
    "description": "Aluguel", "due_date": "2026-09-20", "competence": "2026-09",
    "total_cents": 100000, "paid_cents": 40000, "open_cents": 60000,
    "status": "partially_paid", "source": "manual", "loan_id": null,
    "number": null, "count": null,
    "allowed_actions": ["details", "edit_description", "pay"]
  }],
  "total": 1, "open_cents": 60000, "next_cursor": null
}
```

`key` é `entry:<id>` ou `loan_installment:<id>` e nunca um ID numérico sem origem. Os totais abrangem o filtro completo, não só a página. Status de atraso é calculado por data civil e saldo; preservar estado original no banco. Paginar por `(due_date,kind,id)`, limit padrão 50, máximo 200, cursor validado no servidor. `allowed_actions` depende de permissão, origem, vínculos, pagamentos e versão; o servidor revalida no POST.

Erros novos: 422 para campos inválidos; 409 para versão/estado/conflito de idempotência; 403 para permissão; 404 para recurso fora do escopo. Formato de detail: `{code,message,fields}`; atualizar `api()` em C2 para manter compatibilidade com detail string.

## B1 — Histórico e edição de lançamentos

**Files:** Create `backend/migrations/012_finance_audit.sql`, `backend/finance/entry_management.py`, `tests/finance/test_entry_management.py`; Modify `backend/migrations.py`, `backend/routes/financial_entries.py`, `backend/finance/entries.py`, `tests/test_migrations.py`.

**Interfaces:** `update_entry(company,entry_id,patch,*,expected_version,actor_id)->dict`, `cancel_entry(company,entry_id,*,expected_version,reason,actor_id)->dict`; `PATCH /entries/{id}`, `POST /entries/{id}/cancel`, `GET /entries/{id}/history`.

- [x] Escrever testes usando SQLite temporário no padrão de `tests/finance/test_entries.py`: lançamento aberto editável; segunda edição com version antiga retorna conflito; cancelamento sem pagamento remove dos totais aplicáveis; pagamento ou conciliação impede cancelamento; escopo de outra empresa não altera registro. Exemplo de asserção:

```python
changed = update_entry(1, entry['id'], {'amount_cents': 120000},
                       expected_version=entry['version'], actor_id=None)
assert changed['amount_cents'] == 120000
assert changed['version'] == entry['version'] + 1
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_entry_management.py -q` e confirmar RED.
- [x] Migration 012 cria `finance_audit(id BIGINT PRIMARY KEY, company INTEGER NOT NULL, entity_type TEXT NOT NULL, entity_id BIGINT NOT NULL, action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL, actor_id BIGINT, created_at TEXT NOT NULL)` e índice `(company,entity_type,entity_id,created_at)`. Registrar 012 no runner.
- [x] Implementar whitelist: aberto manual permite account_id/counterparty_id/description/amount_cents/competence/due_date/notes; parcial permite description/due_date/notes. Categoria nova e favorecido devem pertencer à empresa e estar ativos; categoria histórica pode permanecer se não estiver sendo trocada. Datas e competência ISO válidas, descrição 1–240, valor >0; notas até 2000. Cancelado/revertido/pago/importado/conciliado bloqueia alterações financeiras. Guardar antes/depois na mesma transação do UPDATE com condição `version=?`.
- [x] Histórico inclui criação e eventos legados para consulta, sem fabricar autores ausentes. Novas alterações auditadas. Permissões sensitive verificadas para categorias antiga e nova. Testar falha de auditoria revertendo UPDATE.
- [x] Rodar `.venv/bin/pytest tests/finance/test_entry_management.py tests/test_migrations.py tests/test_permissions.py -q`; commit `feat: edit and cancel financial entries with audit history`.

## B2 — Consulta unificada e dados de saldo

**Files:** Create `backend/finance/obligations.py`, `backend/routes/obligations.py`, `tests/finance/test_obligations.py`, `tests/test_obligations_api.py`; Modify `backend/api.py`.

**Interfaces:** `list_obligations(company,*,kind=None,status=None,due_from=None,due_to=None,q='',cursor=None,limit=50,include_sensitive=False)->dict`; `GET /obligations`, `GET /obligations/{kind}/{id}`. Contrato JSON acima. `include_sensitive` é derivado de autorização, nunca aceito livremente da requisição.

- [x] Testar entrada parcial + contrato com duas parcelas: lista contém três obrigações, juros gerados pelo contrato não aparecem como nova obrigação independente; soma dos saldos correta; cronograma antigo excluído; totais iguais entre páginas.

```python
result = list_obligations(1, limit=1, include_sensitive=True)
assert len(result['items']) == 1
assert result['total'] == 3
assert result['next_cursor'] is not None
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_obligations.py tests/test_obligations_api.py -q` para RED.
- [x] Implementar UNION com identidades tipadas; saldo de entries vem dos eventos líquidos; saldo de parcelas vem dos pagamentos persistidos. Nesta tarefa, antes de B3, adaptar campos legados paid_principal/paid_interest. Incluir apenas schedule vigente e preservar acesso ao histórico pago no detalhe. GET aplica sensitive tanto a linhas quanto a totais. Registrar router em api.py.
- [x] Testar busca, filtros combináveis, cursor inválido, viewer sem escrita e IDs além de `2**53`; rodar suites novas e `tests/test_permissions.py`. Commit `feat: expose unified payable obligations`.

## B3 — Pagamento atômico e idempotente

**Files:** Create `backend/migrations/013_obligation_payments.sql`, `backend/finance/payments.py`, `tests/finance/test_payments.py`; Modify `backend/migrations.py`, `backend/finance/entries.py`, `backend/finance/loans.py`, `backend/finance/ledger.py`, `backend/routes/obligations.py`, rotas antigas de pagamento em `financial_entries.py` e `loans.py`.

**Interfaces:** `record_payment(company,kind,obligation_id,*,amount_cents,paid_at,expected_version,idempotency_key,cash_account_id=None,existing_cash_event_id=None,principal_cents=None,interest_cents=None,actor_id=None)->dict`. `POST /obligations/{kind}/{id}/payments`. Resposta `{payment_id,obligation,cash_event_id}`; chave de idempotência pelo header `Idempotency-Key`.

- [x] Criar teste de reenvio e rollback com falha injetada na escrita do caixa; usar fixture isolada criando conta de caixa e despesa. Asserção principal:

```python
first = record_payment(1, 'entry', entry['id'], amount_cents=40000,
    paid_at=date(2026,9,12), expected_version=1, idempotency_key='test-1',
    cash_account_id=bank['id'])
second = record_payment(1, 'entry', entry['id'], amount_cents=40000,
    paid_at=date(2026,9,12), expected_version=1, idempotency_key='test-1',
    cash_account_id=bank['id'])
assert first['payment_id'] == second['payment_id']
assert first['obligation']['open_cents'] == 60000
assert ledger.account_balance(bank['id']) == -40000
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_payments.py -q` e confirmar RED.
- [x] Migration 013 cria `obligation_payments`: id, company, obligation_kind, obligation_id, amount_cents, principal_cents, interest_cents, paid_at, cash_event_id, owns_cash_event (INTEGER), financial_event_id, interest_entry_id, idempotency_key, request_hash, response_json, reversed_at, created_by, created_at. UNIQUE(company,idempotency_key), FK de caixa/evento quando presente, índice de obrigação. Todos os IDs BIGINT; valores monetários BIGINT; datas/textos TEXT. Campos condicionais nulos; kind validado pelo serviço. Acrescentar version à loan_installments com default 1. Registrar migration.
- [x] Na mesma conexão: verificar registro de idempotência, versão e saldo → inserir pagamento → atualizar eventos/dívida → gerar ou vincular caixa → auditoria → gravar resposta. Mesmo key/body retorna resposta inicial antes da checagem de versão; key/body diferente dá 409. Concorrência usa UPDATE condicionado à versão e unicidade; transação perdedora não deixa efeitos. Refatorar helpers para aceitar conexão do chamador, sem commit interno.
- [x] Exigir exatamente um entre cash_account_id e existing_cash_event_id. Movimento existente deve ser da mesma empresa/conta, saída de mesmo valor, não estornado e não alocado; registrar vínculo sem criar saída. Parcela exige principal+juros=total, ambos não negativos e dentro dos saldos componentes; aceitar parcial sem marcar quitada até zerar saldo. Despesa não recebe composição de empréstimo. Evitar principal como despesa; juros registrados uma vez com competência explicitada e padrão do vencimento da parcela.
- [x] Backfill de pagamentos antigos: criar referências a eventos/parcelas existentes, sem duplicar eventos ou saídas; deixar caixa nulo quando não comprovado e expor “Vínculo de caixa pendente”. Chaves estáveis `legacy:event:<id>`/`legacy:installment:<id>`. Rodar duas vezes em cópia sintética e obter mesmos IDs/totais. Encaminhar APIs antigas à mesma lógica; payload antigo sem vínculo retorna 409 “Atualize a página para registrar a conta de pagamento”.
- [x] Rodar testes novos, entries/loans/ledger/forecast e APIs respectivas. Em PostgreSQL descartável, testar dois pagamentos concorrentes com chaves distintas excedendo saldo: apenas um confirma. Commit `feat: record linked obligation payments atomically`.

## B4 — Corrigir pagamento sem apagar obrigação

**Files:** Modify `backend/finance/payments.py`, `backend/finance/entries.py`, `backend/finance/loans.py`, `backend/finance/ledger.py`, `backend/routes/obligations.py`; Create `tests/finance/test_payment_reversal.py`.

**Interfaces:** `reverse_payment(company,payment_id,*,reason,reversed_at,expected_version,idempotency_key,actor_id=None)->dict`; `POST /payments/{id}/reverse`.

- [x] Testar pagar 40000 de 100000 e estornar: saldo volta a 100000, obrigação não fica reversed, caixa gerado volta a zero. Reenvio de estorno não cria segundo ajuste.

```python
result = reverse_payment(1, payment_id, reason='Valor incorreto',
    reversed_at=date(2026,9,12), expected_version=2, idempotency_key='undo-1')
assert result['obligation']['open_cents'] == 100000
assert result['obligation']['status'] == 'open'
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_payment_reversal.py -q` para RED.
- [x] Estornar só o financial_event correspondente e sua composição; recomputar saldo/status. Caixa criado pelo pagamento recebe evento inverso; caixa externo apenas perde alocação, sem apagar a transação bancária. Bloquear pagamento conciliado até desfazer conciliação explicitamente. Não reutilizar `reverse_entry`, que reverte o lançamento inteiro. Motivo obrigatório 3–500 caracteres; data civil explícita.
- [x] Testar parcela parcial, categoria arquivada, legado sem caixa, dupla reversão e falha intermediária. Rodar testes novos e `tests/finance/test_reconciliation.py`. Commit `feat: reverse individual payments with preserved history`.

## B5 — Importação e conciliação sem caixa duplicado

**Files:** Create `backend/migrations/014_bank_payment_links.sql`, `tests/integrations/test_bank_payment_links.py`; Modify `backend/migrations.py`, `backend/integrations/bank_files.py`, `backend/integrations/records.py`, `backend/finance/reconciliation.py`, `backend/routes/bank_imports.py`, `backend/routes/reconciliation.py`.

**Interfaces:** preview de importação passa a retornar linhas com `external_id,date,amount_cents,description,candidate_cash_event_ids,decision`. Commit aceita decisões `new` ou `link:<cash_event_id>` por linha e hash da prévia. Comparação por valor/data é sugestão, não confirmação automática. Novo item_type `payment` na conciliação, preservando tipos legados.

- [x] Criar teste: pagamento gerou saída de 40000; importar extrato da mesma saída com decisão link mantém saldo -40000; importar novamente mantém saldo. Duas saídas legítimas iguais não podem ser fundidas automaticamente.

```python
assert after_link_balance == before_import_balance
assert after_second_import_balance == before_import_balance
assert linked_external_id == external_transaction.external_id
```

- [x] Rodar `.venv/bin/pytest tests/integrations/test_bank_payment_links.py -q` para RED.
- [x] Migration 014 cria relação `bank_cash_links(id,company,cash_account_id,provider,external_id,cash_event_id,created_at)` com UNIQUE(company,cash_account_id,provider,external_id). Preservar registros externos existentes. Importação confirma hash, conta, escopo e valor do candidato dentro da transação; mudança de prévia retorna 409; links duplicados retornam registro existente.
- [x] Adequar conciliação para alocar pagamentos parciais por payment_id, não consumir a obrigação inteira no primeiro vínculo. Excluir previsão quando já liquidada; fluxo de caixa conta cash_event apenas uma vez. Manter compatibilidade de leitura dos grupos antigos; não migrar grupos ambíguos por inferência.
- [x] Rodar `.venv/bin/pytest tests/integrations tests/test_bank_imports_api.py tests/finance/test_reconciliation.py tests/test_reconciliation_api.py -q`; commit `feat: reconcile imported movements with existing payments`.

## B6 — Parcelas e recorrências de despesas

**Files:** Create `backend/migrations/015_expense_schedules.sql`, `backend/finance/expense_schedules.py`, `tests/finance/test_expense_schedules.py`; Modify `backend/migrations.py`, `backend/finance/recurrence.py`, `backend/routes/financial_entries.py`.

**Interfaces:** `preview_expense_schedule(total_cents,count,first_due,competence_mode,competence)->list`; `POST /expense-schedules/preview` e `/expense-schedules`; `PATCH /recurrences/{id}` com expected_version/scope/effective_competence; `POST /entries/{id}/confirm` transforma forecast em open após confirmação de valor/competência.

- [x] Testar soma exata, mês curto e escopo de edição:

```python
rows = preview_expense_schedule(10000, 3, date(2026,1,31), 'single', '2026-01')
assert [r['amount_cents'] for r in rows] == [3334,3333,3333]
assert [r['due_date'] for r in rows] == ['2026-01-31','2026-02-28','2026-03-31']
assert {r['competence'] for r in rows} == {'2026-01'}
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_expense_schedules.py -q` para RED.
- [x] Migration 015 cria cabeçalho expense_schedules (id/company/count/total_cents/competence_mode/version/created_at) e expense_schedule_id nullable nos entries. Gerar entradas atomicamente, distribuir resto na primeira parcela e usar dia âncora original; count 1–120, total positivo. Modo distributed usa competência do mês de cada parcela e exige confirmação na prévia; single preserva competência informada.
- [x] Recorrências novas geram forecast, não realizado automático. Editar uma ocorrência sem alterar a série; editar futuras só atualiza ocorrências forecast após effective_competence; suspender série impede novas gerações. Não modificar entradas confirmadas ou pagas. Datas finais encerram geração; geração repetida conserva UNIQUE(recurrence_id,competence).
- [x] Rodar tests de schedules, recurrence, reporting e forecast; commit `feat: preview installment expenses and confirm recurring costs`.

## B7 — Contratos, renegociação e consultas eficientes

**Files:** Modify `backend/finance/loans.py`, `backend/routes/loans.py`, `backend/finance/obligations.py`, `backend/finance/forecast.py`; Test `tests/finance/test_loans.py`, `tests/test_loans_api.py`, `tests/finance/test_obligations.py`.

- [x] Testar contrato cujo principal difere da soma das parcelas: rejeitar 422; cronograma com juros variáveis válido; renegociação preserva pagos e troca só saldo futuro. Editar credor/finalidade por `PATCH /loans/{id}` com expected_version; mudança de valores futuros usa renegociação, não edição descritiva.

```python
assert sum(i['principal_cents'] for i in new_schedule) == principal_outstanding
assert paid_installment_before == paid_installment_after
assert len(active_schedules) == 1
```

- [x] Conferir RED; validar tipos, quantidades, datas, soma e versões no servidor. Registro de desembolso também oferece gerar caixa ou vincular crédito existente e segue idempotência de B3; não tratar empréstimo recebido como venda. Não apagar contrato com pagamentos; cancelar contrato sem movimentação exige motivo e atualização da agenda.
- [x] Trocar consultas diárias repetidas da previsão por carregamento de eventos/obrigações do intervalo e agrupamento por data quando baseline A5 confirmar custo relevante. Consistência de um snapshot por cálculo; não implementar cache sem chave de revisão que inclua pagamentos/catálogos.
- [x] Executar suites de loans, obligations, forecast, payments; comparar totais antes/depois; commit `feat: maintain loan contracts and consistent obligation forecasts`.

**Gate B:** jornada mestre passa em SQLite e PostgreSQL descartável; reenvios não duplicam, falhas não deixam efeitos parciais, IDs legados preservados. A interface nova depende desses contratos, não de botões que chamam endpoints antigos incompletos.
