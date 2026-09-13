# Evolução do Dashboard para Plataforma de Gestão — Implementation Plan

> **Status (13/09/2026):** executado — R1–R6 integrados ao main (merges 516298d, b6fb269, ba8a8e5, 21aad4d, 6c24f30). As melhorias seguintes foram replanejadas em 2026-09-12-melhorias-consolidadas.md.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Transformar o dashboard Mercado duBairro em uma plataforma de gestão com usuários individuais, despesas, empréstimos, fluxo de caixa, conciliação Stone/Open Finance e ações operacionais rastreáveis.

**Architecture:** Manter FastAPI, PostgreSQL/SQLite e o frontend atual em HTML/CSS/JavaScript. Separar os novos domínios em serviços e routers pequenos. Mobne continua como fonte das vendas; financeiro, Open Finance e recebíveis Stone são fontes complementares ligadas por conciliação idempotente.

**Tech Stack:** Python 3.9+, FastAPI, Pydantic, SQLite/PostgreSQL, httpx, JavaScript sem framework, ECharts, pytest e Node.js.

**Spec:** [PLANO_EVOLUCAO_DASHBOARD.md](../../../PLANO_EVOLUCAO_DASHBOARD.md)

## Global Constraints

- Valores monetários persistidos são centavos inteiros.
- Datas civis usam `America/Sao_Paulo`; instantes de auditoria usam UTC.
- Toda mutação valida autenticação, CSRF, empresa, loja, permissão, fechamento e versão concorrente.
- Dados externos guardam origem, identificador, versão, instante observado e hash de idempotência.
- Empréstimo recebido não é receita; principal pago não é despesa; juros e encargos são despesas financeiras.
- Pró-labore é despesa; distribuição de lucros é destinação do resultado.
- Pagamento de mercadoria não duplica o CMV reconhecido nas vendas Mobne.
- Open Finance começa somente para leitura.
- Lote externo incompleto nunca substitui a última base válida.
- Cada tarefa termina com testes verdes e um commit isolado.
- Toda tarefa que cria um arquivo `backend/migrations/NNN_*.sql` também adiciona esse número ao registro ordenado `MIGRATIONS` em `backend/migrations.py` e inclui o arquivo no commit.

## Release map

| Release | Tasks | Entrega |
|---|---:|---|
| R1 Administração | 1–4 | Migrações, usuários, perfis, empresa, lojas e Configurações |
| R2 Resultado gerencial | 5–7 | Plano de contas, despesas, pagamentos, orçamento e resultado |
| R3 Dívida e caixa | 8–10 | Empréstimos, razão de caixa e projeção |
| R4 Conciliação | 11–13 | Idempotência, conciliação e importação OFX/CSV |
| R5 Stone | 14–15 | Open Finance e recebíveis/taxas |
| R6 Operação | 16–19 | Catálogo, reposição, produtos, metas, ações e promoções |
| R7 Fechamento | 20 | Relatório, auditoria, backup e piloto |

---

## R1 — Administração

### Task 1: Migrações versionadas

**Files:**
- Create: `backend/migrations.py`
- Create: `backend/migrations/001_identity.sql`
- Modify: `backend/database.py`
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `migrate(conn) -> int`, `current_version(conn) -> int`.

- [x] **Step 1: Write failing idempotency tests**

```python
def test_migrate_twice_is_safe(isolated_db):
    db.initialize()
    db.initialize()
    with db.connection() as conn:
        assert migrations.current_version(conn) >= 1
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_migrations.py -q`  
Expected: FAIL because `backend.migrations` does not exist.

- [x] **Step 3: Implement migration runner**

Read numbered SQL files in order, apply each inside a transaction and record it in `schema_versions`. Refuse to open a database whose version is newer than the code.

```python
def migrate(conn) -> int:
    """Apply every unapplied migration exactly once and return the current version."""
```

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_database.py tests/test_migrations.py -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/database.py backend/migrations.py backend/migrations tests/test_migrations.py
git commit -m "feat: add versioned database migrations"
```

### Task 2: Usuários individuais e sessões

**Files:**
- Create: `backend/identity.py`
- Create: `backend/migrations/002_users.sql`
- Modify: `backend/security.py`
- Modify: `backend/api.py`
- Test: `tests/test_identity.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Produces: `create_user()`, `verify_credentials()`, `disable_user()`.
- Changes: sessões passam a conter `user_id`.

- [x] **Step 1: Write failing tests**

```python
def test_disabled_user_loses_existing_session(client):
    cookie = create_and_login_user(client, "gerente@loja.test")
    disable_user_by_email("gerente@loja.test")
    assert client.get("/api/session", cookies=cookie).status_code == 401
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_identity.py tests/test_security.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement identity**

Hash passwords with `hashlib.scrypt`, salt aleatório de 16 bytes, `n=16384`, `r=8`, `p=1`. Use a senha local atual apenas para criar o primeiro Administrador; depois disso, login exige e-mail e senha individuais.

```python
@dataclass(frozen=True)
class AuthContext:
    user_id: int
    email: str
    session_hash: str
```

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_identity.py tests/test_security.py tests/test_api.py -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/identity.py backend/security.py backend/api.py backend/migrations/002_users.sql tests/test_identity.py tests/test_security.py
git commit -m "feat: add individual user accounts"
```

### Task 3: RBAC por empresa e loja

**Files:**
- Create: `backend/permissions.py`
- Create: `backend/routes/users.py`
- Create: `backend/migrations/003_permissions.sql`
- Modify: `backend/api.py`
- Test: `tests/test_permissions.py`
- Test: `tests/test_users_api.py`

**Interfaces:**
- Produces: `require_permission(permission, company_param="company")`.
- Produces: APIs de usuários, perfis e vínculos por empresa/loja.

- [x] **Step 1: Write failing role-matrix tests**

```python
@pytest.mark.parametrize("role,permission,allowed", [
    ("administrator", "users.manage", True),
    ("partner", "finance.sensitive.read", True),
    ("manager", "finance.sensitive.read", False),
    ("viewer", "inventory.write", False),
])
def test_default_roles(role, permission, allowed):
    assert role_allows(role, permission) is allowed
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_permissions.py tests/test_users_api.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement roles and server-side enforcement**

Seed Administrador, Sócio, Gerente e Consulta. Criar permissões separadas para usuários, integrações, estoque, financeiro e dados sensíveis. Substituir verificações genéricas de empresa por dependências de permissão.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_permissions.py tests/test_users_api.py tests/test_api.py -q`  
Expected: PASS, inclusive acesso cruzado retornando `403`.

- [x] **Step 5: Commit**

```bash
git add backend/permissions.py backend/routes/users.py backend/api.py backend/migrations/003_permissions.sql tests/test_permissions.py tests/test_users_api.py
git commit -m "feat: enforce company and store permissions"
```

### Task 4: Empresa, lojas, calendário e Configurações

**Files:**
- Create: `backend/organization.py`
- Create: `backend/routes/settings.py`
- Create: `backend/migrations/004_organization.sql`
- Create: `web/assets/settings.js`
- Modify: `web/index.html`
- Modify: `web/assets/app.js`
- Modify: `web/assets/style.css`
- Test: `tests/test_organization.py`
- Test: `tests/test_settings_frontend.cjs`

**Interfaces:**
- Produces: perfis de empresa, lojas, calendário e `operating_days(start, end)`.
- Produces: `#/configuracoes/empresa`, `usuarios`, `calendario`, `metas`, `alertas`, `integracoes`.

- [x] **Step 1: Write failing calendar and permission tests**

```python
def test_operating_days_excludes_sunday_and_exception():
    assert operating_days("2026-09-01", "2026-09-08", closed_weekdays={6},
                          exceptions={"2026-09-07": "closed"}) == 6
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_organization.py -q && node --test tests/test_settings_frontend.cjs`  
Expected: FAIL.

- [x] **Step 3: Implement organization service and accessible UI**

Mobne-owned IDs remain read-only. Local metadata includes razão social, nome fantasia, CNPJ, endereço, contatos, logo, lojas, horários, feriados and exceptions. Every save uses record `version`.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_organization.py tests/test_api.py -q && node --test tests/test_settings_frontend.cjs`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/organization.py backend/routes/settings.py backend/migrations/004_organization.sql backend/api.py web/index.html web/assets/app.js web/assets/settings.js web/assets/style.css tests/test_organization.py tests/test_settings_frontend.cjs
git commit -m "feat: add organization settings"
```

---

## R2 — Resultado gerencial

### Task 5: Plano de contas e parâmetros

**Files:**
- Create: `backend/finance/accounts.py`
- Create: `backend/routes/finance_accounts.py`
- Create: `backend/migrations/005_finance_accounts.sql`
- Test: `tests/finance/test_accounts.py`

**Interfaces:**
- Produces: `AccountNature`, `create_account()`, `archive_account()`, `list_accounts()`.

- [x] **Step 1: Write failing taxonomy tests**

```python
def test_owner_compensation_and_profit_distribution_have_distinct_natures():
    assert default_account("owner_compensation").nature == "operating_expense"
    assert default_account("profit_distribution").nature == "profit_distribution"
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_accounts.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement accounts and counterparties**

Seed all categories in the functional spec. System accounts cannot change nature after use. Add effective-dated goals, budgets, margins, alert rules and replenishment parameters.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance/test_accounts.py tests/test_migrations.py -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/finance/accounts.py backend/routes/finance_accounts.py backend/migrations/005_finance_accounts.sql tests/finance/test_accounts.py
git commit -m "feat: add chart of accounts"
```

### Task 6: Despesas, recorrências e pagamentos

**Files:**
- Create: `backend/finance/entries.py`
- Create: `backend/finance/recurrence.py`
- Create: `backend/routes/financial_entries.py`
- Create: `backend/migrations/006_financial_entries.sql`
- Test: `tests/finance/test_entries.py`
- Test: `tests/finance/test_recurrence.py`

**Interfaces:**
- Produces: `create_entry()`, `settle_entry()`, `reverse_entry()`, `generate_occurrences()`.

- [x] **Step 1: Write failing competence/cash tests**

```python
def test_august_expense_paid_in_september_hits_correct_views():
    entry = expense(100_00, competence="2026-08", due="2026-09-05")
    settle(entry.id, 100_00, paid_at="2026-09-05")
    assert result_for("2026-08").expenses == 100_00
    assert cash_for("2026-09").outflows == 100_00
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_entries.py tests/finance/test_recurrence.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement append-only financial events**

Statuses: `forecast`, `open`, `partially_paid`, `paid`, `overdue`, `cancelled`, `reversed`. Repeated recurrence jobs use unique `(recurrence_id, competence)`.

```python
@dataclass(frozen=True)
class EntryCommand:
    company_id: int
    account_id: int
    amount_cents: int
    competence: str
    due_date: date
    source: str
    external_id: str | None
```

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance/test_entries.py tests/finance/test_recurrence.py -q`  
Expected: PASS, including partial payments and reversals.

- [x] **Step 5: Commit**

```bash
git add backend/finance/entries.py backend/finance/recurrence.py backend/routes/financial_entries.py backend/migrations/006_financial_entries.sql tests/finance
git commit -m "feat: add expenses and payment events"
```

### Task 7: Resultado, orçamento e telas financeiras

**Files:**
- Create: `backend/finance/reporting.py`
- Create: `backend/routes/financial_reports.py`
- Create: `web/assets/finance.js`
- Modify: `web/index.html`
- Modify: `web/assets/app.js`
- Modify: `web/assets/style.css`
- Test: `tests/finance/test_reporting.py`
- Test: `tests/test_finance_frontend.cjs`

**Interfaces:**
- Produces: `management_result(company, period, store=None)`.
- Produces: Financeiro, Custos e Despesas and Contas a Pagar pages.

- [x] **Step 1: Write failing reporting tests**

```python
def test_inventory_payment_does_not_duplicate_mobne_cogs(period):
    result = management_result(COMPANY, "2026-09")
    assert result["cogs_cents"] == mobne_cogs_cents
    assert result["owner_compensation_cents"] == 5_000_00
    assert result["profit_distribution_cents"] == 8_000_00
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_reporting.py -q && node --test tests/test_finance_frontend.cjs`  
Expected: FAIL.

- [x] **Step 3: Implement statement and UI**

Return revenue, CMV, gross profit, operating expenses, pró-labore, operating result, financial expenses, managerial result and distributions as separate lines. Show budget, actual, variance and source.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance tests/test_models.py tests/test_api.py -q && node --test tests/test_finance_frontend.cjs`  
Expected: PASS without changing reconciled sales totals.

- [x] **Step 5: Commit**

```bash
git add backend/finance/reporting.py backend/routes/financial_reports.py web/index.html web/assets/app.js web/assets/finance.js web/assets/style.css tests/finance/test_reporting.py tests/test_finance_frontend.cjs
git commit -m "feat: add management result and finance UI"
```

---

## R3 — Dívida e caixa

### Task 8: Empréstimos e financiamentos

**Files:**
- Create: `backend/finance/loans.py`
- Create: `backend/routes/loans.py`
- Create: `backend/migrations/007_loans.sql`
- Create: `web/assets/loans.js`
- Test: `tests/finance/test_loans.py`
- Test: `tests/test_loans_frontend.cjs`

**Interfaces:**
- Produces: `create_loan()`, `record_disbursement()`, `pay_installment()`, `loan_position()`.

- [x] **Step 1: Write failing accounting tests**

```python
def test_installment_splits_principal_and_interest():
    pay_installment(principal=2_400_00, interest=600_00, total=3_000_00)
    assert loan_position(LOAN).principal_cents == ORIGINAL - 2_400_00
    assert result_for("2026-09").financial_expenses == 600_00
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_loans.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement contracts and versioned schedules**

Store lender, purpose, principal, net disbursement, CET, rate, grace, dates and schedule. Renegotiation closes the prior schedule and preserves paid installments.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance/test_loans.py -q && node --test tests/test_loans_frontend.cjs`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/finance/loans.py backend/routes/loans.py backend/migrations/007_loans.sql web/assets/loans.js web/index.html web/assets/app.js tests/finance/test_loans.py tests/test_loans_frontend.cjs
git commit -m "feat: add loan schedules and debt position"
```

### Task 9: Contas financeiras e razão de caixa

**Files:**
- Create: `backend/finance/ledger.py`
- Create: `backend/routes/cashflow.py`
- Create: `backend/migrations/008_cash_ledger.sql`
- Test: `tests/finance/test_ledger.py`

**Interfaces:**
- Produces: `post_cash_event()`, `transfer()`, `account_balance()`, `consolidated_balance()`.

- [x] **Step 1: Write failing transfer test**

```python
def test_internal_transfer_does_not_change_consolidated_cash():
    before = consolidated_balance(COMPANY)
    transfer(STONE, OTHER_BANK, 5_000_00, date(2026, 9, 12))
    assert consolidated_balance(COMPANY) == before
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_ledger.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement balanced append-only legs**

Support bank account, payment account and physical cash. Posted events are immutable; corrections create reversal events linked to the original.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance/test_ledger.py tests/finance/test_entries.py tests/finance/test_loans.py -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/finance/ledger.py backend/routes/cashflow.py backend/migrations/008_cash_ledger.sql tests/finance/test_ledger.py
git commit -m "feat: add cash ledger and internal transfers"
```

### Task 10: Fluxo realizado e projetado

**Files:**
- Create: `backend/finance/forecast.py`
- Create: `web/assets/cashflow.js`
- Modify: `backend/routes/cashflow.py`
- Modify: `web/index.html`
- Modify: `web/assets/app.js`
- Test: `tests/finance/test_forecast.py`
- Test: `tests/test_cashflow_frontend.cjs`

**Interfaces:**
- Produces: `forecast(company, start, end, scenario)`.

- [x] **Step 1: Write failing projection test**

```python
def test_forecast_reports_lowest_cash_date():
    result = forecast(COMPANY, date(2026, 9, 1), date(2026, 11, 30), "base")
    assert result["lowest"]["date"] == "2026-10-05"
    assert result["alerts"][0]["type"] == "negative_cash"
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_forecast.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement forecast sources**

Use posted ledger events for realized days, open expenses and loan schedules for known outflows, confirmed receivables for inflows and sales projections only in scenario layers. Return confidence and source per item.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance -q && node --test tests/test_cashflow_frontend.cjs`  
Expected: PASS with distinct realized, forecast and simulated styles.

- [x] **Step 5: Commit**

```bash
git add backend/finance/forecast.py backend/routes/cashflow.py web/assets/cashflow.js web/index.html web/assets/app.js tests/finance/test_forecast.py tests/test_cashflow_frontend.cjs
git commit -m "feat: add realized and projected cash flow"
```

---

## R4 — Conciliação

### Task 11: Registros externos idempotentes

**Files:**
- Create: `backend/integrations/records.py`
- Create: `backend/migrations/009_external_records.sql`
- Test: `tests/integrations/test_external_records.py`

**Interfaces:**
- Produces: `upsert_external_record(source, account_id, external_id, version, payload)`.

- [x] **Step 1: Write failing replay/conflict tests**

```python
def test_replay_is_noop():
    a = upsert_external_record("ofx", 1, "fitid-123", "1", {"amount": -100})
    b = upsert_external_record("ofx", 1, "fitid-123", "1", {"amount": -100})
    assert a.id == b.id
    assert external_record_count() == 1
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/integrations/test_external_records.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement canonical hashes and quarantine**

Hash sorted UTF-8 JSON with SHA-256. Unique key: company, source, external account, external ID and version. Same key with different payload enters a data-quality quarantine.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/integrations/test_external_records.py tests/test_sync.py -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/integrations/records.py backend/migrations/009_external_records.sql tests/integrations/test_external_records.py
git commit -m "feat: add idempotent external records"
```

### Task 12: Motor de conciliação muitos-para-muitos

**Files:**
- Create: `backend/finance/reconciliation.py`
- Create: `backend/routes/reconciliation.py`
- Create: `backend/migrations/010_reconciliation.sql`
- Create: `web/assets/reconciliation.js`
- Test: `tests/finance/test_reconciliation.py`
- Test: `tests/test_reconciliation_frontend.cjs`

**Interfaces:**
- Produces: `suggest()`, `confirm()`, `undo()`.
- States: `unmatched`, `suggested`, `auto_matched`, `manual_matched`, `partial`, `divergent`, `ignored`.

- [x] **Step 1: Write failing grouped-settlement tests**

```python
def test_one_credit_can_settle_multiple_sales():
    group = reconcile(bank_credit=975_00, sales=[600_00, 400_00], fees=[25_00])
    assert group.difference_cents == 0
    assert len(group.links) == 4
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_reconciliation.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement deterministic matching**

Try explicit external link, known relationship, approved rule and then amount/date/description candidates. Auto-confirm only one unambiguous candidate within configured tolerance.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/finance/test_reconciliation.py -q && node --test tests/test_reconciliation_frontend.cjs`  
Expected: PASS; ambiguous records remain suggestions.

- [x] **Step 5: Commit**

```bash
git add backend/finance/reconciliation.py backend/routes/reconciliation.py backend/migrations/010_reconciliation.sql web/assets/reconciliation.js web/index.html web/assets/app.js tests/finance/test_reconciliation.py tests/test_reconciliation_frontend.cjs
git commit -m "feat: add many-to-many reconciliation"
```

### Task 13: Contingência OFX/CSV

**Files:**
- Create: `backend/integrations/bank_files.py`
- Create: `backend/routes/bank_imports.py`
- Create: `tests/integrations/fixtures/stone-sample.ofx`
- Create: `tests/integrations/fixtures/stone-sample.csv`
- Test: `tests/integrations/test_bank_files.py`

**Interfaces:**
- Produces: `parse_bank_file(content, filename) -> list[ExternalTransaction]`.

- [x] **Step 1: Write failing repeat-import tests**

```python
def test_importing_same_statement_twice_creates_three_records(fixture_bytes):
    import_bank_file(COMPANY, STONE_ACCOUNT, "stone-sample.ofx", fixture_bytes)
    import_bank_file(COMPANY, STONE_ACCOUNT, "stone-sample.ofx", fixture_bytes)
    assert external_transaction_count() == 3
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/integrations/test_bank_files.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement bounded preview/import**

Limit uploads to 10 MiB and 50,000 rows. Require account, date, amount, direction and deterministic identity. Preview counts, date range and duplicate count before commit.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/integrations -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/integrations/bank_files.py backend/routes/bank_imports.py tests/integrations
git commit -m "feat: add idempotent OFX and CSV import"
```

---

## R5 — Stone

### Task 14: Conta Stone via Open Finance

**Precondition:** registrar em `docs/integrations/open-finance-provider-evaluation.md` suporte confirmado a Conta Stone PJ, CNPJ, saldos, transações, histórico, webhooks, consentimento, sandbox, preço, exportação e encerramento.

**Files:**
- Create: `docs/integrations/open-finance-provider-evaluation.md`
- Create: `backend/integrations/open_finance.py`
- Create: `backend/integrations/open_finance_service.py`
- Create: `backend/routes/open_finance.py`
- Create: `backend/migrations/011_open_finance.sql`
- Modify: `web/assets/settings.js`
- Test: `tests/integrations/test_open_finance.py`
- Test: `tests/test_open_finance_frontend.cjs`

**Interfaces:**
- Produces: `create_consent()`, `list_accounts()`, `list_balances()`, `list_transactions()`, `revoke_consent()`.

- [x] **Step 1: Write failing provider-contract tests**

```python
class OpenFinanceProvider(Protocol):
    def create_consent(self, company_id: int, return_url: str) -> ConsentLink: ...
    def list_transactions(self, account_id: str, start: date, end: date) -> list[ExternalTransaction]: ...
    def revoke_consent(self, consent_id: str) -> None: ...
```

- [x] **Step 2: Write failing lifecycle test**

```python
def test_revocation_stops_sync_and_preserves_history(service):
    connection = service.connect_and_sync()
    count = external_transaction_count()
    service.revoke(connection.id)
    assert service.sync(connection.id).state == "revoked"
    assert external_transaction_count() == count
```

- [x] **Step 3: Verify RED**

Run: `.venv/bin/pytest tests/integrations/test_open_finance.py -q`  
Expected: FAIL.

- [x] **Step 4: Implement read-only consent and sync**

Keep vendor SDK behind the provider protocol. Encrypt tokens with a deployment key. Validate OAuth state and signed webhooks. Preserve last valid balances on provider failure.

- [x] **Step 5: Verify GREEN**

Run: `.venv/bin/pytest tests/integrations tests/test_security.py tests/test_permissions.py -q && node --test tests/test_open_finance_frontend.cjs`  
Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add docs/integrations/open-finance-provider-evaluation.md backend/integrations backend/routes/open_finance.py backend/migrations/011_open_finance.sql web/assets/settings.js tests/integrations tests/test_open_finance_frontend.cjs
git commit -m "feat: connect Stone account through Open Finance"
```

### Task 15: Recebíveis, taxas e antecipações Stone

**Precondition:** acesso confirmado à API de conciliação Stone ou de parceira conciliadora.

**Files:**
- Create: `backend/integrations/stone_receivables.py`
- Create: `backend/finance/receivables.py`
- Create: `backend/routes/receivables.py`
- Create: `backend/migrations/012_receivables.sql`
- Create: `web/assets/receivables.js`
- Test: `tests/integrations/test_stone_receivables.py`
- Test: `tests/finance/test_receivables.py`

**Interfaces:**
- Produces: `sync_receivables()`, `expected_settlements()`, `effective_fee_report()`.

- [x] **Step 1: Write failing economic-chain test**

```python
def test_sale_fee_receivable_and_credit_form_one_chain():
    group = reconcile_chain(mobne_sale(1_000_00),
                            stone_receivable(gross=1_000_00, fee=25_00, net=975_00),
                            bank_credit(975_00))
    assert group.revenue_count == 1
    assert group.fee_expense_cents == 25_00
    assert group.bank_inflow_count == 1
```

- [x] **Step 2: Add failing cases**

Cover one sale with several settlements, one credit for several sales, chargeback, cancellation and anticipation that consumes future receivables.

- [x] **Step 3: Verify RED**

Run: `.venv/bin/pytest tests/integrations/test_stone_receivables.py tests/finance/test_receivables.py -q`  
Expected: FAIL.

- [x] **Step 4: Implement real adapter and Recebíveis UI**

Map authorization, transaction, installment, adjustment and settlement. Compare contracted and charged fee by effective date. Keep anticipation cost separate from normal acquiring fee.

- [x] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/integrations tests/finance/test_receivables.py tests/finance/test_reconciliation.py -q`  
Expected: PASS.

```bash
git add backend/integrations/stone_receivables.py backend/finance/receivables.py backend/routes/receivables.py backend/migrations/012_receivables.sql web/assets/receivables.js web/index.html web/assets/app.js tests/integrations/test_stone_receivables.py tests/finance/test_receivables.py
git commit -m "feat: reconcile Stone receivables and fees"
```

---

## R6 — Operação

### Task 16: Catálogo completo e snapshots

**Files:**
- Create: `backend/operations/catalog.py`
- Create: `backend/operations/snapshots.py`
- Create: `backend/migrations/013_operation_snapshots.sql`
- Modify: `backend/sync.py`
- Modify: `backend/api.py`
- Test: `tests/operations/test_catalog.py`
- Test: `tests/operations/test_snapshots.py`

**Interfaces:**
- Produces: `inventory_catalog(company, period)`, `snapshot_catalogs(company, observed_at)`.

- [x] **Step 1: Write failing unsold-product test**

```python
def test_product_with_stock_and_no_sales_is_visible():
    seed_product(id=77, stock=12, sales=[])
    row = by_id(inventory_catalog(COMPANY, "2026-09"), 77)
    assert row["revenue"] == 0
    assert row["stock"] == 12
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/operations/test_catalog.py tests/operations/test_snapshots.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement catalog-first inventory**

Join sales metrics onto every active product. Persist daily stock, price and cost snapshots per store/product/date; stop relying on only the immediately previous dataset.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/operations tests/test_sync.py tests/test_api.py -q`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/operations backend/migrations/013_operation_snapshots.sql backend/sync.py backend/api.py tests/operations
git commit -m "feat: add complete catalog and operation snapshots"
```

### Task 17: Reposição preventiva e página do produto

**Files:**
- Create: `backend/operations/replenishment.py`
- Create: `backend/routes/replenishment.py`
- Create: `backend/routes/products.py`
- Create: `backend/migrations/014_replenishment.sql`
- Create: `web/assets/product-detail.js`
- Modify: `web/assets/app.js`
- Test: `tests/operations/test_replenishment.py`
- Test: `tests/test_product_detail_frontend.cjs`

**Interfaces:**
- Produces: `recommend(company, store, as_of)` and product detail endpoint.

- [x] **Step 1: Write failing replenishment formula test**

```python
def test_reorder_uses_available_stock_lead_time_and_pack_size():
    result = recommend_one(daily_demand=3, stock=5, reserved=2,
                           lead_days=4, safety_days=2, pack_size=6)
    assert result.available == 3
    assert result.reorder_point == 18
    assert result.suggested_quantity == 18
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/operations/test_replenishment.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement transparent recommendations**

Use demand over open days, available stock, lead time, safety days and package multiple. Return all inputs and data timestamps. Product page shows sales, quantity, cost, margin, price, stock, actions and promotions.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/operations/test_replenishment.py -q && node --test tests/test_product_detail_frontend.cjs`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/operations/replenishment.py backend/routes/replenishment.py backend/routes/products.py backend/migrations/014_replenishment.sql web/assets/product-detail.js web/assets/app.js tests/operations/test_replenishment.py tests/test_product_detail_frontend.cjs
git commit -m "feat: add replenishment and product detail"
```

### Task 18: Metas, comparações e simulador de preço

**Files:**
- Create: `backend/operations/goals.py`
- Create: `backend/operations/pricing.py`
- Create: `backend/routes/goals.py`
- Create: `backend/migrations/015_goals.sql`
- Modify: `web/assets/insights.js`
- Test: `tests/operations/test_goals.py`
- Test: `tests/operations/test_pricing.py`
- Test: `tests/test_goals_frontend.cjs`

**Interfaces:**
- Produces: `goal_progress()`, `compare_intervals()`, `simulate_price()`.

- [x] **Step 1: Write failing goal/price tests**

```python
def test_daily_target_uses_remaining_open_days():
    assert goal_progress(100_000_00, 70_000_00, 10).required_per_day == 3_000_00

def test_price_simulation_does_not_mutate_mobne_price():
    before = current_price(PRODUCT)
    simulate_price(PRODUCT, 12_00, expected_quantity=100)
    assert current_price(PRODUCT) == before
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/operations/test_goals.py tests/operations/test_pricing.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement versioned goals and comparisons**

Support week, fortnight, quarter and custom interval using equivalent operating days. Replace hard-coded margin and break-even targets in `insights.js`. Simulations never write to Mobne.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/operations/test_goals.py tests/operations/test_pricing.py -q && node --test tests/test_goals_frontend.cjs`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/operations/goals.py backend/operations/pricing.py backend/routes/goals.py backend/migrations/015_goals.sql web/assets/insights.js tests/operations/test_goals.py tests/operations/test_pricing.py tests/test_goals_frontend.cjs
git commit -m "feat: add goals comparisons and pricing simulation"
```

### Task 19: Central de Ações, promoções e cesta

**Files:**
- Create: `backend/actions.py`
- Create: `backend/operations/promotions.py`
- Create: `backend/operations/baskets.py`
- Create: `backend/routes/actions.py`
- Create: `backend/migrations/016_actions.sql`
- Create: `web/assets/actions.js`
- Test: `tests/test_actions.py`
- Test: `tests/operations/test_promotions.py`
- Test: `tests/operations/test_baskets.py`
- Test: `tests/test_actions_frontend.cjs`

**Interfaces:**
- Produces: `create_from_alert()`, `transition_action()`, `promotion_result()`, `basket_pairs()`.

- [x] **Step 1: Write failing action and basket tests**

```python
def test_new_condition_can_reopen_resolved_problem():
    old = create_from_alert("stock:77", "v1")
    resolve(old.id)
    assert create_from_alert("stock:77", "v2").id != old.id

def test_receipt_aliases_count_as_one_basket():
    assert basket_pairs(canonical_receipt_with_aliases).receipt_count == 1
```

- [x] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_actions.py tests/operations/test_promotions.py tests/operations/test_baskets.py -q`  
Expected: FAIL.

- [x] **Step 3: Implement action events and observational analysis**

Store assignee, priority, due date, evidence and immutable events. Compare promotion windows as “variação observada”; do not claim causation. Basket analysis consumes canonical receipts from `backend/models.py`.

- [x] **Step 4: Verify GREEN**

Run: `.venv/bin/pytest tests/test_actions.py tests/operations -q && node --test tests/test_actions_frontend.cjs`  
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add backend/actions.py backend/operations backend/routes/actions.py backend/migrations/016_actions.sql web/assets/actions.js web/index.html web/assets/app.js tests/test_actions.py tests/operations tests/test_actions_frontend.cjs
git commit -m "feat: add action center and promotion insights"
```

---

## R7 — Fechamento

### Task 20: Fechamento, relatório, auditoria e recuperação

**Files:**
- Create: `backend/finance/closing.py`
- Create: `backend/reports.py`
- Create: `backend/audit.py`
- Create: `backend/metrics.py`
- Create: `backend/routes/closing.py`
- Create: `backend/migrations/017_closing.sql`
- Create: `web/assets/closing.js`
- Create: `scripts/backup_database.sh`
- Create: `scripts/restore_database.sh`
- Create: `docs/runbooks/financial-operations.md`
- Create: `docs/runbooks/integration-recovery.md`
- Modify: `SECURITY.md`
- Test: `tests/finance/test_closing.py`
- Test: `tests/test_reports.py`
- Test: `tests/test_audit.py`
- Test: `tests/test_backup_restore.py`
- Test: `tests/test_closing_frontend.cjs`

**Interfaces:**
- Produces: `closing_checklist()`, `close_period()`, `reopen_period()`, `partner_report()`.

- [x] **Step 1: Write failing closure tests**

```python
def test_material_divergence_blocks_closing():
    seed_divergence(10_000_00, status="unresolved")
    with pytest.raises(ClosureBlocked):
        close_period(COMPANY, "2026-09", PARTNER)

def test_manager_cannot_reopen_period():
    with pytest.raises(PermissionDenied):
        reopen_period(COMPANY, "2026-09", MANAGER, "correção")
```

- [x] **Step 2: Write failing audit and backup tests**

Verify that tokens, passwords and raw bank payloads are redacted. Back up a temporary database containing a company, expense and reconciliation; restore it to a second database and compare row counts and totals.

- [x] **Step 3: Verify RED**

Run: `.venv/bin/pytest tests/finance/test_closing.py tests/test_reports.py tests/test_audit.py tests/test_backup_restore.py -q`  
Expected: FAIL.

- [x] **Step 4: Implement immutable closing snapshots**

Snapshot source versions, rule versions, totals and exceptions. Closed periods reject ordinary mutations. Reopening records actor, reason and creates a new closing version.

- [x] **Step 5: Implement partner report and operational controls**

Report result, cash, payables, receivables, debt, data quality, actions and notes from the snapshot. Add internal health for sync age, consent, pending reconciliation, overdue items, failed jobs and last backup.

- [x] **Step 6: Run full verification**

Run: `.venv/bin/pytest -q`  
Expected: all Python tests PASS.

Run: `node --test tests/*.cjs`  
Expected: all frontend tests PASS.

- [x] **Step 7: Run pilot checklist**

Use one company and one closed month. Reconcile management result with the accounting reference, bank balance with zero unexplained difference, one loan schedule and Stone fees. Revoke/reconnect consent and restore a backup outside production.

- [x] **Step 8: Commit**

```bash
git add backend/finance/closing.py backend/reports.py backend/audit.py backend/metrics.py backend/routes/closing.py backend/migrations/017_closing.sql web/assets/closing.js scripts docs/runbooks SECURITY.md tests/finance/test_closing.py tests/test_reports.py tests/test_audit.py tests/test_backup_restore.py
git commit -m "feat: add financial closing and operational recovery"
```

---

## Release gates

### R1 gate

- [x] Existing Mobne sync and dashboard tests pass.
- [x] Four profiles enforce permissions in the API.
- [x] First administrator bootstrap disables shared-password login.

### R2 gate

- [x] Expense competence and cash dates reconcile.
- [x] Pró-labore and profit distribution remain separate.
- [x] Purchase payments do not duplicate CMV.

### R3 gate

- [x] Loan principal, interest and fees reconcile independently.
- [x] Internal transfers do not change consolidated cash.
- [x] Projection identifies the lowest cash date in 30/60/90 days.

### R4 gate

- [x] Reimports and webhook replays create zero duplicates.
- [x] One-to-many and many-to-one reconciliation pass.
- [x] Ambiguities remain pending human review.

### R5 gate

- [x] Provider failure preserves last valid balances.
- [x] Consent revocation stops synchronization.
- [x] Mobne sale, Stone fee, receivable and bank credit form one chain.

### R6/R7 gate

- [x] Unsold catalog products appear in operational decisions.
- [x] Replenishment exposes every formula input.
- [x] Closed reports reproduce identical totals and source versions.
- [x] Backup restore succeeds outside production.

## Execution rules

1. Execute tasks in order inside each release.
2. Start Tasks 14 and 15 only after their external preconditions are confirmed.
3. When provider access is unavailable, finish the provider-neutral contract and OFX/CSV contingency; do not invent an API.
4. Never squash applied database migrations into a different history.
5. Run each release gate before enabling the next release.
6. Pilot every financial release with sanitized or copied data before the live company.
7. Review specification compliance before code quality after each task.

## Final acceptance

- [x] Access is isolated by user, company, store and permission.
- [x] Result separates CMV, operating expenses, pró-labore, financial expenses and profit distribution.
- [x] Cash separates realized, forecast and simulated values.
- [x] Debt separates principal, interest, fees, installments and balance.
- [x] Bank transactions, sales and receivables do not duplicate economic events.
- [x] Inventory includes unsold products and preventive replenishment.
- [x] Goals use operating days and versioned parameters.
- [x] Alerts become traceable actions with observed outcomes.
- [x] Monthly closing is reproducible and auditable.
- [x] Python, Node, backup and restoration checks pass in the pilot.
