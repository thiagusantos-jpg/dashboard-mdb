# Contas a pagar — Bloco 1 (itens 2, 3 e 1)

> **Para quem executa:** este é o primeiro de três blocos das 8 melhorias de Contas a pagar,
> aprovadas em 15/09/2026. Um commit por bloco, com aprovação do sócio antes de cada commit.

**Objetivo:** avisar o sócio do vencimento sem ele abrir a página (item 2), mostrar o mês em
calendário para ver os dias pesados (item 3) e permitir pagar várias contas de uma vez (item 1).

**Blocos seguintes (não são deste plano):**
- **Bloco 2 — pagamento mais inteligente:** item 4 (juros e multa no pagamento), item 5 (forma de
  pagamento: boleto, Pix, débito automático ou transferência, com código opcional), item 6 (credor
  recorrente sugerindo categoria e valor).
- **Bloco 3 — caixa e extrato:** item 7 (previsão de 30 dias somando os recebíveis da Stone),
  item 8 (sugerir a baixa da conta a partir do débito do extrato importado).

## Decisões do sócio (15/09/2026)

- Pagam por boleto localizado no DDA do app da Stone, por Pix e por débito automático/transferência.
- Ainda não importam extrato bancário, mas vão passar a importar.
- Entrega em 3 blocos, um commit cada.

## Restrições que valem para todo o plano

- **CSP:** sem `<script>` inline, sem atributo `style=`, sem handler inline. Larguras por CSSOM.
- **Escopo global compartilhado:** `finance.js` carrega depois de `finance-forms.js` e antes de
  `app.js`; funções de `app.js` (`api`, `esc`, `money`, `routeHash`, `beginPage`) só podem ser usadas
  dentro de funções. `newIdempotencyKey()` e `buildPaymentRequest()` vêm de `finance-forms.js`.
- **Ids grandes:** a API devolve ids acima de 2^53 como texto. Comparar sempre com `String(a) === String(b)`.
- **Pagamento:** a rota exige o cabeçalho `Idempotency-Key`; cada conta paga em lote leva a sua chave.
- **Testes:** `.venv/bin/python -m pytest -q tests` e `node --test tests/*.cjs`, os dois antes do commit.
- **Commit:** só com aprovação explícita do sócio; mensagem terminando com
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Cache:** atualizar `?v=` de `finance.js`, `actions.js` e `style.css` em `web/index.html`.

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `backend/actions.py` | `ensure_due_reminders()`: ações de vencimento, criadas uma vez e resolvidas quando a conta é paga |
| `backend/routes/actions.py` | `POST /actions/due-reminders` |
| `web/assets/actions.js` | origem "Conta a pagar", atalho para a página e chamada no login |
| `web/assets/finance.js` | calendário do mês, seleção de contas e pagamento em lote |
| `web/assets/style.css` | calendário, seleção e barra de ação |
| `web/index.html` | versões dos assets |
| `tests/test_due_reminders.py` (novo) | backend do item 2 |
| `tests/test_payables_calendar_frontend.cjs` (novo) | itens 3 e 1 (regras puras + fonte da página) |
| `tests/test_finance_setup_frontend.cjs` | acrescenta a origem "vencimento" na Central |

---

### Task 1 — Lembrete de vencimento na Central de Ações (item 2)

**Interfaces**

- `actions.ensure_due_reminders(company, today, *, created_by=None, limit=8) -> dict`
  - Considera obrigações em aberto (`obligations._entry_rows` + `_loan_installment_rows`, sem
    filtro de sensíveis) com `open_cents > 0` e vencimento até `today + 2 dias`.
  - Uma ação por obrigação: `alert_key = f"vencimento:{kind}:{id}"`, `alert_version = due_date`,
    `due_date` da ação = vencimento da conta, prioridade `high` se vencida, senão `medium`.
  - Título: `Pagar {descrição} — venceu em DD/MM` (vencida) ou `Pagar {descrição} — vence em DD/MM`.
  - Cria no máximo `limit` por execução, das mais antigas para as mais novas.
  - Fecha sozinha: ação ativa cuja obrigação não está mais em aberto vira `resolved` com a nota
    `Conta paga.`
  - Retorna `{"created": [ação...], "resolved": [ação...]}`.
- `POST /api/companies/{company}/actions/due-reminders` (permissão `finance.read`), devolve o mesmo dict.

**Testes (`tests/test_due_reminders.py`)**

1. `test_creates_one_action_per_bill_due_soon`: contas vencendo hoje, em 2 dias e em 10 dias →
   ações só para as duas primeiras; `alert_key`, prioridade e prazo conferidos.
2. `test_does_not_repeat_the_same_bill`: segunda execução não cria nada; ação descartada não volta.
3. `test_resolves_itself_when_the_bill_is_paid`: após `settle_entry` cobrir o saldo, a ação vira
   `resolved` com a nota "Conta paga.".
4. `test_limits_how_many_actions_one_run_creates`: 12 contas vencidas → 8 ações, as mais antigas.
5. `test_endpoint_answers`: `POST` devolve 200 com as chaves `created` e `resolved`.

**Passos**

- [ ] Escrever `tests/test_due_reminders.py` e rodar (`FAIL`: função não existe).
- [ ] Implementar `ensure_due_reminders` em `backend/actions.py` e a rota.
- [ ] Rodar `.venv/bin/python -m pytest -q tests/test_due_reminders.py` (`PASS`).

---

### Task 2 — A Central mostra e liga essas ações (item 2, frontend)

**Interfaces**

- `ACTION_ORIGINS.vencimento = {label: 'Conta a pagar', icon: 'calendar'}`.
- `actionSource` reconhece `vencimento:{kind}:{id}` e devolve `{href: routeHash('contas-pagar'), label: 'Abrir Contas a pagar'}`.
- `ensureClosingReminder()` passa a chamar também `POST /actions/due-reminders` antes de atualizar o
  contador do menu (mantém o nome, que `app.js` já chama no login).

**Testes (`tests/test_finance_setup_frontend.cjs`, teste existente dos lembretes)**

- `actionOrigin('vencimento:entry:9')` → `'vencimento'`.
- `actionSource({alert_key: 'vencimento:entry:9'}, '2026-09')` → `#/contas-pagar`.
- `web/assets/actions.js` contém `actions/due-reminders`.

**Passos**

- [ ] Acrescentar as asserções ao teste e rodar (`FAIL`).
- [ ] Implementar em `web/assets/actions.js`.
- [ ] `node --test tests/test_finance_setup_frontend.cjs` (`PASS`).

---

### Task 3 — Calendário do mês em Contas a pagar (item 3)

**Interfaces (puras, testadas em vm)**

- `calendarWeeks(monthISO, today)` → semanas de domingo a sábado cobrindo o mês;
  cada célula `{iso, day, inMonth, isToday}`.
- `dayTotals(items)` → `{[iso]: {cents, count}}` somando `open_cents` por vencimento.
- `monthShift(monthISO, delta)` → `'YYYY-MM'`.

**Comportamento**

- Alternador "Lista | Calendário" na barra de filtros; a escolha fica em `APP.financeFilters`.
- No calendário, o mês é próprio da página (setas ‹ ›), independente do seletor global, que não
  existe nesta página.
- Busca as contas do mês inteiro (`due_from`/`due_to` do mês, `limit=200`) sem mexer nos filtros da lista.
- Cada dia mostra o total e a quantidade; dias vencidos em vermelho; hoje destacado.
- Clicar no dia aplica o filtro daquele dia e volta para a lista.

**Testes (`tests/test_payables_calendar_frontend.cjs`)**

1. `calendarWeeks('2026-09', '2026-09-15')`: 5 semanas, primeira célula 30/08, 15/09 marcado como hoje.
2. `dayTotals`: soma por dia, ignorando contas de outros dias.
3. `monthShift('2026-01', -1)` → `'2025-12'`; `monthShift('2026-12', 1)` → `'2027-01'`.
4. Fonte da página: contém `data-payables-view`, `calendarWeeks(` e `data-calendar-day`.

**Passos**

- [ ] Escrever o teste e rodar (`FAIL`).
- [ ] Implementar helpers, a visão de calendário e o CSS.
- [ ] `node --test tests/test_payables_calendar_frontend.cjs` (`PASS`).

---

### Task 4 — Pagar várias de uma vez (item 1)

**Interfaces**

- `selectableObligations(items)` → só `kind === 'entry'` com ação `pay` (parcela de empréstimo pede
  principal e juros, então fica fora do lote e mostra o motivo).
- `selectionSummary(items, selectedKeys)` → `{count, cents}`.
- `buildBatchPayments(items, selectedKeys, values)` → `{requests: [{key, description, path, body}], errors}`,
  reaproveitando `buildPaymentRequest` com o saldo em aberto de cada conta.

**Comportamento**

- Caixa de seleção por linha e no cabeçalho do grupo; barra fixa "N contas · R$ X · Pagar selecionadas".
- A gaveta pede data do pagamento e conta de caixa, e lista o que será pago.
- Envia uma requisição por conta, cada uma com sua `Idempotency-Key`, mostrando progresso.
- Erro em uma conta não impede as outras: ao fim, relata quantas foram pagas e quais falharam.

**Testes (mesmo arquivo da Task 3)**

1. `selectableObligations` tira parcelas de empréstimo.
2. `selectionSummary` soma só as selecionadas.
3. `buildBatchPayments` gera uma requisição por conta, com `expected_version` e `cash_account_id`;
   sem conta de caixa, devolve erro.
4. Fonte da página: contém `data-payables-select`, `buildBatchPayments(` e `Idempotency-Key`.

**Passos**

- [ ] Escrever os testes e rodar (`FAIL`).
- [ ] Implementar seleção, barra de ação, gaveta e envio em série.
- [ ] `node --test tests/*.cjs` (`PASS`).

---

### Task 5 — Verificação e commit do bloco 1

- [ ] Atualizar `?v=` de `finance.js`, `actions.js` e `style.css` em `web/index.html`.
- [ ] `.venv/bin/python -m pytest -q tests` e `node --test tests/*.cjs`.
- [ ] Navegador (API simulada, como nos blocos anteriores): calendário com dias somados, clique no
      dia filtrando, seleção de 2 contas e pagamento em lote, tema claro e escuro, largura de 400px
      sem rolagem lateral, console sem erros.
- [ ] Mostrar `git status`, pedir aprovação e só então commitar:
      `feat: remind due bills, show the month as a calendar and pay several at once`.
