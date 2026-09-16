# Contas a pagar — Bloco 2 (itens 4, 5 e 6)

> Segundo de três blocos das 8 melhorias aprovadas em 15/09/2026. Bloco 1 (lembrete de
> vencimento, calendário e pagamento em lote) saiu no commit `9d9698a`.

**Objetivo:** registrar quanto o atraso custou (item 4), saber como cada conta é paga (item 5) e
repetir menos digitação a cada mês (item 6).

**Bloco 3, depois deste:** item 7 (previsão de 30 dias somando os recebíveis da Stone) e item 8
(sugerir a baixa da conta a partir do débito do extrato importado).

## Restrições

As mesmas do bloco 1: CSP sem inline, escopo global compartilhado, ids grandes como texto,
`Idempotency-Key` em todo pagamento, testes de backend e frontend antes do commit, commit só com
aprovação do sócio, e `?v=` atualizado em `web/index.html`.

## Arquivos

| Arquivo | Responsabilidade |
|---|---|
| `backend/migrations/028_payment_extras.sql` (novo) | `obligation_payments.late_fee_cents`/`late_fee_entry_id`; `financial_entries.payment_method`/`payment_code` |
| `backend/migrations.py` | registrar a migração 28 |
| `backend/finance/payments.py` | juros e multa no pagamento de uma despesa |
| `backend/finance/entries.py`, `backend/finance/entry_management.py` | gravar e editar forma de pagamento e código |
| `backend/finance/counterparties.py` (novo) | última despesa de um credor (sugestão) |
| `backend/routes/obligations.py`, `backend/routes/financial_entries.py`, `backend/routes/finance_accounts.py` | campos novos e rota de sugestão |
| `web/assets/finance-forms.js` | campo de juros e multa no pagamento; forma de pagamento no formulário de despesa; sugestão ao escolher o credor |
| `web/assets/finance.js` | marca "débito automático" na lista |
| `web/assets/style.css`, `web/index.html` | estilos e versões |
| `tests/test_payment_extras.py` (novo) | item 4 |
| `tests/test_payment_method.py` (novo) | itens 5 e 6 (backend) |
| `tests/test_payment_forms_frontend.cjs` (novo) | itens 4, 5 e 6 (frontend) |

---

### Task 1 — Juros e multa ao pagar uma conta vencida (item 4)

**Decisões**

- Um campo só, o acréscimo total pago além do saldo (`late_fee_cents`), porque o sócio vê no boleto
  o valor final, não a divisão entre juros e multa.
- Só para despesa (`kind="entry"`). Parcela de empréstimo já tem principal e juros; pedir acréscimo
  ali seria ambíguo, então a API recusa com 422.
- O acréscimo vira um lançamento **já quitado** na conta "Juros e multas" (`fines`, 5.03), com
  competência e vencimento no dia do pagamento, na mesma transação do pagamento.
- A saída de caixa é uma só, de `amount_cents + late_fee_cents`.
- A "impressão digital" do pedido inclui o acréscimo: repetir a mesma chave com outro acréscimo é
  conflito, não repetição.
- Estorno do pagamento também estorna o lançamento do acréscimo.

**Interfaces**

- `payments.record_payment(..., late_fee_cents: Optional[int] = None)`.
- Resposta ganha `"late_fee_cents"` e `"late_fee_entry_id"`.
- `PaymentCreate.late_fee_cents: Optional[int] = Field(default=None, ge=1)`.

**Testes (`tests/test_payment_extras.py`)**

1. `test_late_fee_becomes_a_settled_expense_and_one_cash_outflow`: paga 100,00 de saldo com 12,34 de
   acréscimo → saída única de 112,34; lançamento em "Juros e multas" criado e já pago; obrigação quitada.
2. `test_late_fee_is_refused_for_a_loan_installment`: 422.
3. `test_same_key_with_a_different_late_fee_is_a_conflict`: mesma chave, acréscimo diferente → 409.
4. `test_replay_with_the_same_late_fee_returns_the_same_payment`: mesma chave e mesmo acréscimo → mesmo `payment_id`.
5. `test_reversing_the_payment_also_reverses_the_late_fee`: depois do estorno, o lançamento do
   acréscimo fica estornado e o caixa volta ao valor anterior.
6. `test_endpoint_accepts_late_fee`: 200 pela API, com `late_fee_cents` na resposta.

---

### Task 2 — Como cada conta é paga (item 5)

**Decisões**

- `payment_method` em cada despesa: `boleto`, `pix`, `debito_automatico`, `transferencia` ou vazio.
- `payment_code` opcional (linha digitável do boleto ou Pix copia e cola), até 200 caracteres,
  guardado como texto, sem validar dígito verificador — o sócio localiza o boleto pelo DDA da Stone;
  o campo serve para conferir e copiar.
- Editável enquanto a despesa está aberta ou parcialmente paga (entra nos dois conjuntos).
- Em Contas a pagar, conta com `debito_automatico` ganha a marca "Débito automático", porque ela sai
  da conta sozinha e não deve ser paga de novo pelo lote; fica fora da seleção em lote.
- A recorrência repassa os dois campos para cada mês gerado.

**Testes (`tests/test_payment_method.py`)**

1. `test_entry_keeps_payment_method_and_code`: criação e leitura.
2. `test_payment_method_is_validated`: valor fora da lista → 422.
3. `test_open_entry_can_change_how_it_is_paid`: PATCH muda os dois campos; versão sobe.
4. `test_recurrence_passes_how_it_is_paid_to_every_month`: ocorrência gerada herda os campos.

---

### Task 3 — Credor recorrente (item 6)

**Decisões**

- `GET /finance/counterparties/{id}/last-expense` devolve a última despesa daquele credor
  (categoria, valor, forma de pagamento e competência), ou 204 quando não houver.
- No formulário de despesa, escolher o credor preenche categoria, valor e forma de pagamento **só
  quando os campos estiverem vazios**, e mostra "Repetido do último lançamento (Ago/2026)" com a
  opção de limpar. Nunca sobrescreve o que o sócio digitou.

**Testes**

- Backend (`tests/test_payment_method.py`): `test_last_expense_of_a_counterparty` (último por
  competência e vencimento; 204 sem histórico; despesa cancelada não conta).
- Frontend (`tests/test_payment_forms_frontend.cjs`): `applySuggestion(values, suggestion)` só
  preenche campo vazio e devolve a lista do que preencheu.

---

### Task 4 — Frontend dos três itens

- Pagamento: campo "Juros e multa (R$)" aparece quando a conta está vencida; o resumo mostra
  "Total a pagar" somando o acréscimo; `buildPaymentRequest` envia `late_fee_cents`.
- Despesa: campos "Como é paga" e "Linha digitável ou Pix copia e cola"; `entryCandidate` e
  `ENTRY_FIELD_ORDER` passam a carregá-los.
- Lista: marca "Débito automático" e exclusão do lote (`selectableObligations`).
- Sugestão do credor no formulário de despesa.

**Testes (`tests/test_payment_forms_frontend.cjs`)**

1. `buildPaymentRequest` com acréscimo válido e com acréscimo inválido.
2. `paymentTotal(obligation, values)` soma saldo + acréscimo.
3. `entryCandidate` carrega `payment_method` e `payment_code`.
4. `selectableObligations` tira as contas em débito automático.
5. Fonte: `finance-forms.js` contém `late_fee_cents` e `data-suggestion-clear`.

---

### Task 5 — Verificação e commit

- [ ] `?v=` atualizado; `.venv/bin/python -m pytest -q tests`; `node --test tests/*.cjs`.
- [ ] Navegador: pagar conta vencida com acréscimo; cadastrar despesa com boleto e código; escolher
      credor conhecido e ver a sugestão; conta em débito automático fora do lote; tema escuro; 400px.
- [ ] `git status`, aprovação do sócio e commit:
      `feat: record late fees, how each bill is paid and repeat the last expense of a supplier`.
