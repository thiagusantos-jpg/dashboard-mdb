# Contas a pagar — Bloco 3 (itens 7 e 8)

> Último dos três blocos das 8 melhorias aprovadas em 15/09/2026. Bloco 1 saiu em `9d9698a`,
> bloco 2 em `6178f32` (e a correção das gavetas em `ae2fc71`).

**Objetivo:** mostrar se o dinheiro dos próximos 30 dias dá conta das contas, somando o que a
Stone tem para depositar (item 7), e sugerir a baixa de uma conta quando já existe uma saída de
caixa que combina com ela (item 8).

## Restrições

As mesmas dos blocos anteriores: CSP sem inline, escopo global compartilhado (agora com o teste
`tests/test_asset_globals_frontend.cjs` guardando), ids grandes como texto, `Idempotency-Key` em
todo pagamento, testes de backend e frontend antes do commit, commit só com aprovação do sócio, e
`?v=` atualizado em `web/index.html`.

## O que já existe (levantado antes de planejar)

- `backend/finance/forecast.py::forecast(company, start, end, scenario)` já projeta saldo dia a dia
  a partir dos movimentos realizados, dos lançamentos abertos e das parcelas de empréstimo. Já tem
  rota (`GET /finance/forecast`) e é consumida pelo Fluxo de caixa.
- `backend/finance/receivables.py::expected_settlements(company, start, end)` devolve, por data de
  liquidação, `count`/`gross_cents`/`fee_cents`/`net_cents` da tabela `stone_receivables`.
- `payments.record_payment(..., existing_cash_event_id=...)` já quita uma conta contra um movimento
  de caixa que já existe, com guardas fortes: o evento tem de existir, não pode estar estornado e
  não pode já estar em outro pagamento ativo (índice UNIQUE em `obligation_payments.cash_event_id`).
  A gaveta de pagamento já expõe isso em "Vincular movimento já registrado".
- `finance/reconciliation.py` casa um movimento com lançamentos, mas **só cria vínculos e muda o
  status do grupo** — não quita a obrigação. Por isso o item 8 não é só expor o que existe.

## Decisão que evita contar a taxa duas vezes (item 7)

`sync_receivables` cria um lançamento **só para a taxa** (conta `acquiring_fees`), nunca para o
líquido. Esse lançamento nasce `status='open'`, `source='stone_receivable'`, e nada o quita — ou
seja, `_open_entries_by_due_date_on_connection` já o conta como **saída** na data da liquidação.

Como `líquido = bruto − taxa`, somar o líquido como entrada descontaria a taxa duas vezes. Então a
projeção soma o **bruto**, e a taxa continua sendo a saída que já existe hoje:

```
+ bruto (novo)  −  taxa (lançamento que já existia)  =  líquido que cai na conta
```

Só entram liquidações com `settlement_date > hoje`. As passadas já viraram movimento de caixa de
verdade pela importação do extrato; somá-las contaria o mesmo dinheiro duas vezes.

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `backend/finance/forecast.py` | somar os recebíveis da Stone como entrada datada |
| `backend/finance/payment_matches.py` (novo) | achar saída de caixa não usada que combina com conta aberta |
| `backend/routes/obligations.py` | rota das sugestões de baixa |
| `web/assets/finance.js` | painel de 30 dias e painel de sugestões em Contas a pagar |
| `web/assets/finance-forms.js` | abrir a gaveta de pagamento já vinculada ao movimento sugerido |
| `web/assets/style.css`, `web/index.html` | estilos e versões |
| `tests/test_cash_forecast_stone.py` (novo) | item 7 |
| `tests/test_payment_matches.py` (novo) | item 8 |
| `tests/test_payables_forecast_frontend.cjs` (novo) | itens 7 e 8 no front |

---

### Task 1 — A Stone entra na projeção (item 7)

**Decisões**

- `_expected_settlements_by_day_on_connection(conn, company, start, end)`, no mesmo estilo das
  outras três consultas agrupadas: **uma** consulta, não uma por dia.
- Cada dia futuro ganha um item `{amount_cents: +gross, description: "Recebíveis Stone (N vendas)",
  source: "stone_receivable", confidence: "forecast"}`.
- Nada muda para quem já usa `forecast()`: o Fluxo de caixa simplesmente passa a projetar melhor.

**Testes (`tests/test_cash_forecast_stone.py`)**

1. `test_future_settlement_enters_the_forecast_as_gross`: recebível de 1.000,00 bruto com 20,00 de
   taxa liquidando em 3 dias → o dia projetado sobe 1.000,00 e o lançamento da taxa tira 20,00,
   fechando em 980,00 de efeito líquido.
2. `test_settlement_already_in_the_past_is_not_counted_again`: liquidação de ontem não entra.
3. `test_forecast_without_receivables_is_unchanged`: sem Stone, a projeção é idêntica à de hoje.

---

### Task 2 — Contas a pagar mostra os 30 dias (item 7, front)

**Decisões**

- A página chama a rota que já existe (`/finance/forecast?start=hoje&end=hoje+30`), em paralelo com
  o que já carrega, e com `.catch(() => null)`: se falhar, a página continua inteira.
- Um painel com o que entra, o que sai, o saldo no fim dos 30 dias e o **pior dia**, com a data.
- Quando o pior dia fica negativo, o painel diz em que dia o dinheiro acaba — que é a pergunta real
  do sócio, e não um número no fim do mês.

---

### Task 3 — "Esta conta parece que já saiu da conta" (item 8)

**Decisões**

- Uma saída de caixa é **candidata** quando: `amount_cents < 0`, não estornada, não é transferência,
  e não está em nenhum `obligation_payments` ativo (o mesmo critério que o índice UNIQUE já impõe na
  hora de pagar).
- Casa com uma conta aberta quando o valor bate **exatamente** com o saldo em aberto e a data do
  movimento está a até 5 dias do vencimento.
- Só sugere par **1 para 1**: se o mesmo movimento serve a duas contas, ou a mesma conta tem dois
  movimentos possíveis, nada é sugerido. Ambiguidade vira silêncio, não palpite.
- **Nada é quitado sozinho.** A sugestão abre a gaveta de pagamento já com "Vincular movimento já
  registrado" escolhido; quem confirma é o sócio. Toda a validação de `record_payment` continua no
  caminho.

**Testes (`tests/test_payment_matches.py`)**

1. `test_unused_debit_matching_an_open_bill_is_suggested`.
2. `test_debit_already_used_by_a_payment_is_not_suggested`.
3. `test_reversed_debit_is_not_suggested`.
4. `test_two_bills_with_the_same_amount_suggest_nothing` (ambiguidade).
5. `test_debit_far_from_the_due_date_is_not_suggested`.
6. `test_endpoint_returns_the_suggestions`.

---

### Task 4 — Verificação e commit

- [x] `?v=` para `20260915-b3`; 536 testes de backend e 177 de frontend passando.
- [x] Navegador: título "Próximos 30 dias" (o horizonte pedido, não as linhas devolvidas), "Sai"
      como valor positivo, alerta nomeando o dia em que o caixa fica negativo; "Dar baixa" abrindo
      a gaveta em modo vincular com o movimento já escolhido; com a projeção e as sugestões
      falhando, a página continua inteira e só os painéis somem; tema escuro trocando de verdade
      pela regra `data-theme`; 375px sem rolagem horizontal.
- [ ] `git status`, aprovação do sócio e commit:
      `feat: project the next 30 days with Stone settlements and suggest paying a bill from a bank debit`.
