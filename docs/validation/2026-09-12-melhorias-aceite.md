# Aceite das melhorias consolidadas — Plano C (C7)

Data da validação: 13/09/2026. Branch `plano-c-experiencia`, worktree
`.claude/worktrees/plano-c-experiencia`, a partir de `main` em `a574b72`.
Dados exclusivamente sintéticos; nenhum banco real foi lido ou alterado.

## 1. Ambiente

- macOS (Darwin 25.5.0), Apple M2, arm64.
- Python 3.9.6 do `.venv` do repositório; SQLite 3.51.0; Node v24.
- Suítes rodadas com `.venv/bin/python -m pytest tests -q` e `node --test tests/*.cjs`
  (o entry point `pytest` do venv não põe a raiz no `sys.path`, como já registrado em A5).

## 2. Regressão

| Suíte | Antes do Plano C (`main`) | Depois do Plano C |
|---|---|---|
| Python (pytest) | 394 passed | 439 passed, 0 failed |
| Node (`node --test`) | 39 passed | 84 passed, 0 failed |

Uma execução completa intermediária (após C4) teve 1 falha em
`tests/finance/test_payments.py::test_two_concurrent_payments_exceeding_balance_only_one_confirms`.
O teste passou 5 de 5 vezes isolado e nas execuções completas seguintes; é
sensível a tempo sob carga (threads + SQLite) e não depende de código do Plano C.
Fica registrado como teste instável, não corrigido.

## 3. Jornada integrada do plano mestre

Automatizada em `tests/test_acceptance_journey.py` (API HTTP completa, SQLite temporário):

| Passo | Resultado |
|---|---|
| Cadastrar despesa de R$ 1.000 | OK |
| Editar descrição | OK (versão verificada) |
| Importar extrato OFX com dois débitos (R$ 400 e R$ 600) | OK |
| Pagar R$ 400 vinculando a linha do extrato | OK — R$ 600 em aberto |
| Quitar R$ 600 gerando saída na conta | OK — status pago |
| Corrigir (estornar) o segundo pagamento | OK — R$ 600 reabertos, R$ 400 preservados |
| Pagar R$ 600 vinculando a outra linha do extrato | OK — nenhuma saída de caixa duplicada |
| Reimportar o mesmo extrato | OK — nada novo a criar |
| Arquivar categoria com lançamento | OK — resultado histórico inalterado |
| Renegociar empréstimo | OK — só o novo cronograma aparece como pagável |
| URL antiga de empréstimo | OK — `#/emprestimos` → `#/contas-pagar?tipo=emprestimo` (`tests/test_navigation_frontend.cjs`) |
| Trocar módulo mantendo contexto | OK — rota de cada módulo restaurada (`tests/test_navigation_frontend.cjs`) |
| Trocar senha | OK — nova senha entra, antiga é recusada, outras sessões encerradas |

Rascunho preservado ao trocar de módulo: coberto pela coordenação de página do
Plano A (confirmação antes de sair com formulário alterado), não por um teste
novo desta etapa.

## 4. Desempenho — mesmo método e volumes do baseline A5

Script descartável no scratchpad da sessão, idêntico à metodologia de
`2026-09-12-baseline.md`: SQLite novo por cenário, 2 aquecimentos, 20 execuções,
mediana/p95, tamanho da resposta e statements SQL via `set_trace_callback`.
Datasets de vendas gravados com o resumo por período, como `sync.run` faz.

### Antes da C7 (código após C6)

| Função (cenário) | Mediana (ms) | p95 (ms) | Resposta (bytes) | Statements SQL |
|---|---|---|---|---|
| `dashboard` (1 mês) | 3.09 | 3.25 | 20 694 | 21 |
| `management_result` (1 mês) | 33.34 | 33.74 | 2 664 | 176 |
| `forecast` (30 dias) | 0.65 | 0.71 | 3 066 | 7 |
| `dashboard` (12 meses) | 3.61 | 3.82 | 24 389 | 21 |
| `management_result` (12 meses) | 32.53 | 33.61 | 2 664 | 176 |
| `forecast` (360 dias) | 1.21 | 1.33 | 35 140 | 7 |
| `dashboard` (24 meses) | 4.18 | 4.36 | 28 348 | 21 |
| `management_result` (24 meses) | 32.93 | 42.11 | 2 666 | 176 |
| `forecast` (720 dias) | 2.03 | 2.52 | 69 866 | 7 |

### Depois da C7

| Função (cenário) | Mediana (ms) | p95 (ms) | Resposta (bytes) | Statements SQL |
|---|---|---|---|---|
| `dashboard` (1 mês) | 3.09 | 3.16 | 20 694 | 21 |
| `management_result` (1 mês) | 4.13 | 4.37 | 2 665 | 18 |
| `forecast` (30 dias) | 0.65 | 0.71 | 3 066 | 7 |
| `dashboard` (12 meses) | 3.64 | 3.93 | 24 389 | 21 |
| `management_result` (12 meses) | 4.18 | 4.95 | 2 666 | 18 |
| `forecast` (360 dias) | 1.46 | 1.70 | 35 140 | 7 |
| `dashboard` (24 meses) | 4.21 | 4.58 | 28 348 | 21 |
| `management_result` (24 meses) | 4.17 | 4.55 | 2 667 | 18 |
| `forecast` (720 dias) | 2.22 | 3.25 | 69 866 | 7 |

### Leitura

- **`forecast`** já não tinha o gargalo medido em A5 (100 → 2 170 consultas por
  janela): a otimização de consultas do Plano B (B7) o levou a 7 statements fixos
  em qualquer janela. Nada a fazer na C7.
- **`dashboard`** ficou constante em 21 statements e ~3–4 ms nos três volumes,
  graças ao resumo por período já cacheado em `main`. Nada a fazer.
- **`management_result`** era o gargalo restante: 176 statements e ~33 ms por
  chamada, constantes em qualquer volume. Duas causas, ambas corrigidas:
  1. um parâmetro de orçamento resolvido por conta, uma conexão para cada uma das
     53 contas → `resolve_parameters` resolve todas as chaves numa única consulta,
     com a mesma precedência (produto > categoria > loja > geral) e as mesmas regras
     de vigência;
  2. 53 upserts idempotentes das contas padrão em toda leitura → uma contagem
     decide se o seed é necessário; uma conta padrão ausente continua sendo recriada.

  Resultado: **176 → 18 statements e ~33 → ~4,2 ms (≈8×)**, sem mudança nas
  respostas. As 8 consultas acima do baseline A5 vêm do cálculo da revisão mensal (C6).
- Teste de equivalência escrito antes da otimização:
  `tests/finance/test_budget_resolution.py` (lote = chave a chave para loja
  nenhuma/5/7, incluindo vigências vencidas, futuras e escopo de loja; seed
  pulado ainda restaura conta padrão apagada).

## 5. Tamanho dos recursos do navegador

Medido sobre os arquivos servidos (bytes brutos / gzip):

| Recurso | Bruto | gzip |
|---|---|---|
| `vendor/echarts/echarts.min.js` | 1 121 883 | 368 202 |
| Demais JS + CSS + HTML | 377 512 | 109 901 |
| Total | 1 499 395 | 478 103 |

ECharts correspondia a ~77% do total comprimido e era carregado em todas as telas,
inclusive login, Configurações e todo o módulo Financeiro, que não desenham
gráficos. **Decisão:** carregar sob demanda. `ensureEcharts()` injeta o script
próprio (permitido pela CSP `script-src 'self'`) na primeira tela de Análises,
uma única vez; uma falha de download mostra "Tentar novamente" e não fica
memorizada. Coberto por `tests/test_echarts_loading_frontend.cjs`.

Tabela de Produtos e estoque (~700 linhas): o plano condiciona a paginação em 50
linhas a uma medição de renderização no navegador. Essa medição não foi possível
nesta sessão (ver §7), portanto **a paginação não foi implementada**.

## 6. Acessibilidade verificável sem navegador

### Contraste (WCAG, texto normal ≥ 4,5:1), calculado a partir dos tokens do CSS

| Par | Razão |
|---|---|
| Texto #2D2D2D / branco | 13,77:1 |
| Texto secundário #6B6B6B / branco | 5,33:1 |
| Erro #C0392B / branco | 5,44:1 |
| Sucesso #1E8449 / branco | 4,72:1 |
| Link #1F6FA8 / branco | 5,39:1 |
| Botão primário #2D2D2D / amarelo #FFC107 | 8,45:1 |
| Menu #CCCCCC / barra lateral #2D2D2D | 8,58:1 |
| Texto discreto da barra lateral #A0A0A0 / #2D2D2D | 5,27:1 |
| Selo de alerta #7A4F00 / #FDEBD0 | 6,10:1 |
| Selo de erro #943126 / #FADBD8 | 5,94:1 |
| Selo informativo #1B5E8C / #D6EAF8 | 5,61:1 |
| Selo neutro #6B6B6B / #EEEEEE | 4,59:1 |
| **Selo de sucesso #1E8449 / #D5F5E3** | **4,04:1 → corrigido para #17613A: 6,41:1** |

### Estrutura, verificada por testes e leitura de código

- Drawers e Minha conta usam `<dialog>` nativo com `h2` + `aria-labelledby`: foco
  contido, Escape fecha, fundo inerte; o foco volta ao controle que abriu
  (teste com stub em `tests/test_finance_forms_frontend.cjs`).
- Todos os campos novos têm `<label for>`; erros em `role="alert"`, sucesso em
  `role="status"`; campos com erro recebem `aria-invalid`.
- Seletor de módulo com `aria-current` e anúncio da troca; itens de menu com `aria-current="page"`.
- Controles de toque novos (botões de módulo, segmentados, fechar, mostrar senha)
  com altura mínima de 44 px no CSS.
- Nenhum `alert()` e nenhum `onclick=` restantes nas telas alteradas.

## 7. Limitações — o que NÃO foi validado

- **Navegador.** O painel de preview não conseguiu subir o servidor do worktree: o
  processo não tem permissão para ler `~/Documents` (erro ao abrir `.venv/pyvenv.cfg`).
  Por isso **não foram executados**:
  - larguras de 375, 768 e 1440 px;
  - navegação só por teclado;
  - zoom de 200%;
  - movimento reduzido;
  - foco real do drawer em navegador;
  - inspeção visual das telas novas.

  Os fluxos desta lista estão cobertos só por testes de API e de lógica.
  Precisam de uma sessão manual antes da publicação.
- **PostgreSQL.** Como em A5, toda a suíte roda em SQLite. O SQL novo usa os mesmos
  recursos já usados no projeto em Postgres: `ON CONFLICT`, `RETURNING`, `IN (...)`
  e `COUNT(*)`. Ainda assim, não há validação automatizada contra um Postgres real.
  O item "completar PostgreSQL de B" segue pendente.
- **Concorrência real** das revisões mensais e dos pagamentos: coberta por testes
  de conflito de versão/revisão com duas sessões HTTP no mesmo processo, não por
  carga simultânea.
- **Métricas de uso** e tarefas com usuários reais: não fazem parte desta validação.

## 8. Entrega e reversão

- Migração nova: **024_period_reviews** (plano propunha 017; renumerada porque o
  Plano B já usou 016–023). A 016 prevista para C5 **não foi criada**:
  `counterparties` já tinha coluna `version`.
- Endpoints novos: `/api/me`, `/api/me/password`, PATCH de categorias, favorecidos,
  contas de caixa e usuários, DELETE de exceção de calendário e `/period-reviews/{period}`.
  Endpoints antigos permanecem.
- `management-result` ganhou `data_status` e passou a devolver `null` quando falta
  dado de vendas ou quando o perfil não pode ver contas sensíveis. Clientes antigos
  que somavam esses campos como inteiros precisam do front novo.
- Rollback: o runner recusa banco com versão maior que o código. Voltar para `main`
  depois de aplicar a 024 exige um build compatível com essa versão. A tabela nova
  é aditiva e não é lida pelo código antigo, então ignorá-la em um build que
  reconheça a versão 24 é seguro. Nunca rodar downgrade destrutivo.
- Publicação/deploy: **não realizados** — dependem de autorização explícita.
