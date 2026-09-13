# C — Experiência, conta pessoal e administração — Implementation Plan

> **Status (13/09/2026):** executado, mesclado (PR #11) e publicado. C1 924d27f · C2 dcb95c2 · C3 fe67749 · C4 08fb084 · C5 1ccff84 · C6 e577e23 (migração renumerada para 024) · C7 b7e207f; ponto de equilíbrio pela Central de custos em 6ba1f0a (migração 025). Pendências registradas em docs/validation/2026-09-12-melhorias-aceite.md.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Entregar dois módulos claros, formulários completos para uma loja, manutenção da própria conta e resultado com contexto de apuração.

**Architecture:** Reutilizar os serviços existentes e os contratos B, com formulários e coordenação de página compartilhados. Separar arquivos pequenos de conta pessoal, navegação e formulários sem reescrever a aplicação em framework.

**Tech Stack:** HTML/CSS/JavaScript, FastAPI, pytest, node:test e inspeção em navegador.

**Spec:** [Plano mestre](2026-09-12-melhorias-consolidadas.md), [A](2026-09-12-melhorias-a-confiabilidade.md), [B](2026-09-12-melhorias-b-financeiro.md).

## Global Constraints

Respeitar todas as restrições do mestre. Aplicar ui-ux-pro-max na execução de interface. Manter amarelo da marca, Lucide, layout responsivo e CSP. Erros legíveis, labels associados, títulos h1/h2, foco visível e feedback anunciado. Nenhuma afirmação de conformidade de acessibilidade sem validação. Não exigir seletores de loja.

## C1 — Minha conta e troca de senha

**Files:** Create `backend/routes/profile.py`, `web/assets/profile.js`, `tests/test_profile_api.py`, `tests/test_profile_frontend.cjs`; Modify `backend/identity.py`, `backend/security.py`, `backend/api.py`, `web/index.html`, `web/assets/app.js`.

**Interfaces:** `GET /api/me -> {id,name,email,version}`; `PATCH /api/me {name,email,current_password?,expected_version}`; `POST /api/me/password {current_password,new_password,expected_version}`. Credenciais mudam apenas o usuário autenticado. Resposta de troca fornece CSRF novo e cookie renovado; nunca retorna hash/salt.

- [x] Reutilizar fixture de cliente de `tests/test_users_api.py`, criando dois usuários sintéticos. Testar atualização própria, e-mail duplicado, senha errada, sessão revogada e ausência de autoelevação. Exemplo após PATCH autenticado:

```python
assert response.status_code == 200
assert response.json()['name'] == 'Nome atualizado'
assert 'password_hash' not in response.json()
assert identity.verify_credentials(email, old_password) is None
assert identity.verify_credentials(email, new_password) is not None
```

- [x] Rodar `.venv/bin/pytest tests/test_profile_api.py -q` e `node --test tests/test_profile_frontend.cjs` para RED.
- [x] Implementar edição de nome 1–120 e e-mail normalizado/único; senha atual obrigatória apenas quando e-mail muda ou senha é trocada. Nova senha mantém regra atual de formulário 8–200, com confirmação no cliente e gerenciadores permitidos. Validar versão e unicidade na transação; verificação de senha errada retorna 422 de campo, não 401 que encerraria login. Reutilizar scrypt existente. Não aceitar user_id/role/is_admin da requisição. Aplicar CSRF e rate limit.
- [x] Acrescentar helper de sessão capaz de inserir token novo na mesma transação que revoga sessões anteriores. Nome apenas mantém sessão; mudança de e-mail ou senha revoga anteriores e gera nova. Gravar cookie como o login atual e atualizar APP.csrf. Não chamar `reset_password` como fluxo de alteração autenticada.
- [x] Perfil do rodapé abre Minha conta e Alterar senha; confirmação visual de sucesso, regras visíveis, mostrar/ocultar com aria-pressed. Limpar senhas ao fechar; não persistir no armazenamento. Testar usuário viewer também consegue alterar sua conta, sem ganhar acesso administrativo.
- [x] Rodar profile, identity, security e testes frontend; commit `feat: manage own profile and password`.

## C2 — Formulário compartilhado de despesas e pagamentos

**Files:** Create `web/assets/finance-forms.js`, `tests/test_finance_forms_frontend.cjs`; Modify `web/assets/finance.js`, `web/assets/loans.js`, `web/assets/cashflow.js`, `web/assets/reconciliation.js`, `web/assets/app.js`, `web/assets/style.css`, `web/index.html`.

**Interfaces:** `openExpenseForm(entry=null)`, `openPaymentForm(obligation)`, `openLoanForm(loan=null)`. `api` normaliza erros de B para `error.message` e `error.fields`. Formulários integram `APP.pageState` de A4; APIs são B1–B7. Nenhuma função grava antes do submit.

- [x] Adicionar testes node:test com DOM mínimo no padrão do repositório para: preenchimento → erro 422 → valores preservados; duplo clique → um POST; partial payment preenche open_cents; cancelar drawer devolve foco. Comparar efeito no fetch stub, não só presença de texto no código:

```javascript
assert.equal(postCalls.length, 1);
assert.equal(JSON.parse(postCalls[0].body).amount_cents, 60000);
assert.equal(descriptionInput.value, 'Aluguel corrigido');
assert.equal(submitButton.disabled, false);
```

- [x] Rodar `node --test tests/test_finance_forms_frontend.cjs` para RED.
- [x] Lista primeiro; botão Nova despesa e Editar visível. Drawer com h2/aria-labelledby, Escape, foco contido e fundo inativo; página inteira no mobile. Campos principais descrição/categoria/valor/competência/vencimento; fornecedor e observações em detalhes. Moeda pt-BR convertida sem float monetário acumulado; IDs strings. Novo/Editar usam mesmo formulário; ações Salvar/Salvar e adicionar outra/Cancelar.
- [x] Pagamento mostra total, pago, saldo, data e escolha gerar caixa/vincular movimento existente. Empréstimo mostra composição principal/juros. Cancelar e estornar mostram item/valor/efeito e motivo obrigatório. Histórico exibe antes/depois; bloquear ações pelo allowed_actions, mantendo explicação textual. Para cadastros, gerar chave estável por intenção de submit; reenvio após erro de rede usa a mesma chave.
- [x] Criar abas Única/Recorrente/Parcelada da despesa com prévia B6; empréstimo com prévia do cronograma, editar finalidade/credor e renegociar futuro. Guardar filtros e posição da lista ao salvar; atualizar linha/totais sem reconstruir formulário de outra operação.
- [x] Padronizar .data-table também em financeiro, usar Lucide em títulos e mensagens. Estado vazio oferece ação pertinente, erro oferece repetir consulta. Inputs maiores no mobile, datas rotuladas, select de conciliação com busca e detalhes antes de confirmar.
- [x] Rodar `node --test tests/*.cjs`; verificar criação/edição/pagamento/cancelamento por teclado e em 375px. Commit `feat: deliver editable financial forms and payment workflows`.

## C3 — Dois módulos e navegação compatível

**Files:** Create `web/assets/navigation.js`, `tests/test_navigation_frontend.cjs`; Modify `web/index.html`, `web/assets/app.js`, `web/assets/settings.js`, `web/assets/style.css`, `web/assets/insights.js`.

**Interfaces:** `moduleForPage(page)->'analises'|'financeiro'|null`; `canonicalRoute(hash)->string`; estado `APP.module`, `APP.moduleRoutes`. Configurações/Minha conta preservam último módulo. Persistência em sessionStorage apenas de rotas/filtros não sensíveis, nunca credenciais.

- [x] Testes de links diretos, voltar/avançar e seleção:

```javascript
assert.equal(moduleForPage('precos'), 'analises');
assert.equal(moduleForPage('contas-pagar'), 'financeiro');
assert.equal(canonicalRoute('#/sync/2026-09'), '#/configuracoes/integracoes');
assert.equal(canonicalRoute('#/emprestimos/2026-09'), '#/contas-pagar?tipo=emprestimo');
```

- [x] Rodar `node --test tests/test_navigation_frontend.cjs` para RED.
- [x] Implementar seletor textual Análises/Financeiro e menu contextual, sem submenus extras. Análises mantém sete destinos; Financeiro Resultado gerencial/Custos e despesas/Contas a pagar/Fluxo de caixa/Conciliação. Renomear telas conforme mestre. Usar links reais e aria-current; módulo ativo com estado anunciado. Links antigos de páginas analíticas mantêm compatibilidade.
- [x] Criar filtros específicos: período de vendas em Análises, competência independente de Mobne em despesas/resultado, vencimentos em obrigações, horizonte 30/60/90 dias em caixa, intervalo/conta na conciliação. Configurações não mostra calendário global. Troca de módulo restaura seu contexto sem sobrescrever o outro.
- [x] Reaproveitar render de sync dentro de settingsShell('integracoes',body), redirecionar links antigos, atualizar lógica do polling e estados vazios. Status compacto fora de Configurações leva à integração; passos avançados recolhíveis. Corrigir contador de jobs e mensagem final na origem/backend se necessário; nunca limitar visualmente 9/6 para ocultar erro de contagem. Acrescentar regressão em `tests/test_sync.py` para progresso <= total e final coerente.
- [x] No navegador: testar desktop, drawer mobile, rota antiga, período futuro financeiro e retorno entre módulos. Rodar node suites e sync; commit `feat: separate analytics and finance navigation`.

## C4 — Resultado gerencial validado e retirada do simulador

**Files:** Modify `backend/finance/reporting.py`, `backend/routes/financial_reports.py`, `web/assets/app.js`, `web/assets/finance.js`, `web/assets/insights.js`, `web/index.html`; Test `tests/finance/test_reporting.py`, `tests/test_permissions.py`, `tests/test_finance_frontend.cjs`.

**Interfaces:** management-result acrescenta `data_status:{sales_available,expenses_reviewed,reason}` e admite null nos campos financeiros dependentes de vendas ausentes. Cartão no Resumo é secundário e consulta o mesmo endpoint com finance.sensitive.read quando números completos puderem expor dados restritos. Nunca apresentar resultado parcial filtrado como total da empresa.

- [x] Testar ausência de vendas → receita/resultado null; categoria arquivada permanece; mesma competência produz o mesmo cartão nos dois módulos; acesso restrito não vaza totais sensíveis. Exemplo:

```python
result = management_result(1, '2026-09')
assert result['data_status']['sales_available'] is False
assert result['managerial_result_cents'] is None
```

- [x] Rodar reporting e testes frontend para RED. Ajustar o contrato do endpoint e os renderizadores que antes assumiam inteiro. Se vendas existem com zero confirmado, manter zero legítimo.
- [x] Remover input/listener/PUT de custo fixo do cliente e cartões de simulação/equilíbrio; preservar campo legado no banco até migração futura sem dependentes. Remover cálculos de custo fixo de projeções e velocímetro. Projeções de vendas mostram receita estimada, hipóteses e cobertura histórica; resultado futuro após despesas fica fora deste cálculo comercial.
- [x] Resumo abre com faturamento/margem bruta/ticket/cupons e prioridades comerciais; cartão de Resultado gerencial, se autorizado, leva ao financeiro e mostra Em apuração. Não adicionar outra fonte de lucro líquido ou frase de negócio seguro.
- [x] Rodar reporting/models/API/permissions e frontend. Commit `feat: replace legacy simulations with verified management results`.

## C5 — Cadastros administrativos e auxiliares para uma loja

**Files:** Create `backend/migrations/016_catalog_maintenance.sql`, `tests/test_catalog_maintenance_api.py`; Modify `backend/migrations.py`, `backend/routes/finance_accounts.py`, `backend/routes/cashflow.py`, `backend/routes/settings.py`, `backend/routes/users.py`, `backend/organization.py`, `web/assets/settings.js`, `web/assets/cashflow.js`, `web/assets/finance-forms.js`.

**Interfaces:** PATCH/arquivo para categorias, favorecidos e cash-accounts com expected_version; DELETE de exceção de calendário com expected_version. Edição de dados da empresa permanece no PUT existente. Perfil de usuário usa endpoint existente; remoção deve indicar “Desativar usuário” enquanto backend desativa globalmente.

- [x] Testar arquivar categoria/conta preserva histórico e saldo; edição concorrente falha 409; fornecedor de outra empresa rejeitado; exceção de calendário editável/removível sem afetar outra data. Fixture e asserções:

```python
assert balance_after_archive == balance_before_archive
assert historical_expenses_after_archive == historical_expenses_before_archive
assert stale_update.status_code == 409
```

- [x] Rodar `.venv/bin/pytest tests/test_catalog_maintenance_api.py -q` para RED. Migration 016 acrescenta version default 1 a counterparties, caso ainda ausente após inspeção; demais entidades já têm versão. Registrar no runner. Não apagar categoria/favorecido/conta usados.
- [x] Interface Dados da empresa sem tabela Nova loja; manter edição de nome/endereço/contato, máscara e validação de CNPJ/telefone, UF em select. Cadastro rápido de fornecedor retorna ao formulário original sem perder campos. Categoria chamada Categoria da despesa e banco chamado Conta de pagamento.
- [x] Usuários: editar perfil e desativar, sem autoelevação; impedir desativação/destituição do último administrador utilizável e do próprio acesso por esse fluxo. Minha conta fica em C1. Calendário: data brasileira, editar/remover com versão; ocultar coluna técnica de versão. Contas de caixa: editar nome/arquivar, saldo inicial com data, Entrada/Saída explícitas e interface de transferência usando serviço existente.
- [x] Rodar APIs novas + organization/users/accounts/ledger e testes settings/frontend. Commit `feat: maintain supporting records for single-store operation`.

## C6 — Revisão mensal e contexto de apuração

**Files:** Create `backend/migrations/017_period_reviews.sql`, `backend/finance/period_reviews.py`, `backend/routes/period_reviews.py`, `tests/finance/test_period_reviews.py`; Modify `backend/migrations.py`, `backend/api.py`, `backend/finance/reporting.py`, `web/assets/finance.js`.

**Interfaces:** `GET /period-reviews/{period}` e `POST /period-reviews/{period}` sob prefixo financeiro; body `{action:'review'|'reopen',expected_revision,reason}`. Resposta `{status:'in_progress'|'reviewed',revision,reviewed_at,reviewed_by,checks}`. É revisão gerencial, não fechamento contábil rígido.

- [x] Testar revisar → alterar despesa → status em apuração; sync de vendas com nova versão também invalida selo; revisão repetida com hash antigo dá 409.

```python
assert first_review['status'] == 'reviewed'
assert after_expense_change['status'] == 'in_progress'
assert after_sales_refresh['status'] == 'in_progress'
```

- [x] Rodar `.venv/bin/pytest tests/finance/test_period_reviews.py -q` para RED. Migration 017 cria period_reviews(company,period,revision,status,reviewed_by,reviewed_at,reason) com PK(company,period). Registrar no runner.
- [x] Calcular revision por hash canônico de dados relevantes do período: versão de vendas/catálogos usados, entries e eventos, juros, vínculos e parâmetros do resultado. Hash muda mesmo quando valor total permanece igual mas classificação muda. Comparar hash atual no GET, sem depender de todo chamador lembrar de invalidar cache. POST valida expected_revision e permissão settings.manage + finance.sensitive.read.
- [x] Checklist mostra dados de vendas disponíveis, recorrências previstas por confirmar, pagamentos sem vínculo de caixa e divergências conhecidas. Exigir reconhecimento explícito de completude pelo usuário; checklist vazio não certifica despesas omitidas. Reabertura com motivo; mudanças tornam estado em apuração automaticamente, sem bloquear operação silenciosamente.
- [x] Rodar period_reviews/reporting/sync e validar duas sessões revisando versões diferentes. Commit `feat: show monthly review status with data revision tracking`.

## C7 — Validação de experiência, desempenho e entrega

**Files:** Create `docs/validation/2026-09-12-melhorias-aceite.md`; Modify `web/assets/style.css`, `web/assets/echarts-charts.js`, `web/assets/app.js`, `web/assets/insights.js`, `backend/api.py` apenas quando necessário pelos resultados medidos.

- [x] Executar jornada integrada do mestre e preencher relatório com resultado por passo, ambiente, data e limitações. Testar 375/768/1440px, teclado, zoom 200%, movimento reduzido, títulos, labels, foco do drawer e retorno do foco. Contraste medido >=4,5:1 em texto normal; alvos web >=24px com espaçamento suficiente, preferindo 44px em controles móveis.
- [x] Comparar baseline A5 com mesmos volumes. Se tabelas de estoque dominarem renderização, paginar primeiro em 50 linhas e preservar filtro/ordenação; se dashboard dominar servidor, agregar histórico por mês/versão e invalidar por catálogos/parametrização. Acrescentar teste funcional de equivalência dos dados antes de otimizar; não introduzir Redis ou framework como pré-requisito.
- [x] Verificar tamanho do bundle e carregamento de ECharts somente nas telas que precisam, caso a medição justifique. Manter skeleton com recuperação; nenhuma consulta deve ficar indefinidamente em carregamento. No teste de listas, verificar o comportamento: `assert.equal(renderedRows.length, 50)` e totais/filtros equivalentes ao conjunto completo.
- [x] Rodar `.venv/bin/pytest tests -q` e `node --test tests/*.cjs`; completar PostgreSQL de B. Revisar diff, esquema, permissões, migração e compatibilidade de links. Corrigir regressões antes de marcar aceite.
- [x] Commit `test: validate end-to-end dashboard improvements`; preparar release com changelog e rollback compatível. Publicação segue autorização de execução/publicação vigente; o pedido atual é apenas o plano.

**Gate C:** duas áreas claras, uma loja, sem simulador legado, próprios dados editáveis, fluxos completos e métricas registradas. O aceite não depende das funcionalidades explicitamente adiadas no mestre.
