# Melhorias consolidadas do Mercado duBairro — Implementation Plan

> **Status (13/09/2026):** planos A, B e C executados e em produção. Itens de migração/entrega abaixo que seguem abertos: backup restaurável verificado antes de migrar, integração em PostgreSQL descartável e piloto em duas sessões/celular — ver docs/validation/2026-09-12-melhorias-aceite.md.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use superpowers:subagent-driven-development only if the user chooses delegated execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar uma experiência simples para uma loja, com números financeiros consistentes, manutenção completa de lançamentos, conta pessoal e navegação separada entre Análises e Financeiro.

**Architecture:** Evoluir a aplicação existente, mantendo FastAPI, PostgreSQL/SQLite e HTML/CSS/JavaScript sem framework. Compartilhar dados e serviços entre módulos, preservar IDs e histórico e criar operações financeiras transacionais. Mudanças em esquema são aditivas; novas consultas convivem com endpoints antigos durante a migração.

**Tech Stack:** Python/FastAPI/Pydantic, SQLite/PostgreSQL, JavaScript, ECharts, pytest e node:test, usando as versões já instaladas no projeto.

**Spec:** [Análise e decisões consolidadas](../../../ANALISE_UX_2026-09-12.md), especialmente “Direção consolidada” e “Revisão crítica consolidada”.

## Global Constraints

- Uma única loja: não apresentar seleção ou cadastro de lojas; preservar identificadores internos existentes.
- Dois módulos, Análises e Financeiro, dentro da mesma aplicação e base de dados.
- Sincronização fica em Configurações → Integrações; Minha conta é acessível pelo perfil.
- Resultado simulado e custo fixo manual saem da interface. Resultado gerencial usa despesas por competência; projeções comerciais não representam dinheiro disponível.
- Empréstimos aparecem como obrigações em Contas a Pagar, mantendo contrato, principal e juros separados.
- Valores monetários persistidos são centavos inteiros. IDs BIGINT novos expostos ao JavaScript são strings decimais; não converter em Number.
- Datas civis usam America/Sao_Paulo; auditoria usa instantes UTC.
- Mutação exige autenticação, CSRF, empresa, permissão, validação de vínculos e versão concorrente. Minha conta resolve identidade pela sessão.
- Preservar pagamentos, importações, conciliações, categorias históricas e URLs anteriores.
- Nunca converter ausência de dados em zero confirmado. Não alterar permissões para fazer um cartão aparecer.
- Código sob CSP atual: sem scripts inline, handlers onclick ou dependências externas desnecessárias.
- Executar testes em banco temporário. PostgreSQL deve usar banco descartável explicitamente identificado, nunca DATABASE_URL de produção para testes.
- Cada tarefa fecha com testes pertinentes e commit isolado; não incluir os documentos preexistentes não rastreados inadvertidamente.

## Situação e precedência

Este plano parte dos módulos já implementados. Substitui, para as melhorias aqui descritas, a execução literal de `2026-09-12-evolucao-dashboard-gestao.md`, que ainda manda criar arquivos existentes. Não recriar usuários, tabelas ou módulos já presentes. Não modificar aquele documento durante a execução.

Nenhuma tarefa deste plano foi executada. A revisão anterior reproduziu três defeitos em SQLite temporário: saldo parcialmente pago ausente da previsão, categoria arquivada removida do resultado histórico e cronograma renegociado duplicado na previsão.

## Planos por subsistema

| Ordem | Plano | Dependências | Entrega verificável |
|---|---|---|---|
| 1 | [A — Confiabilidade](2026-09-12-melhorias-a-confiabilidade.md) | Nenhuma | Cálculos corretos, formulários preservados e respostas antigas descartadas |
| 2 | [B — Operação financeira](2026-09-12-melhorias-b-financeiro.md) | A | Editar/cancelar, pagar parcialmente, corrigir pagamento, integrar empréstimos e importações |
| 3 | [C — Experiência e administração](2026-09-12-melhorias-c-experiencia.md) | A; telas financeiras dependem de B | Minha conta, formulários, dois módulos, relatórios e configurações |

Minha conta (C1) pode ser entregue após A independentemente de B. Publicar mudanças de cálculo de A antes do restante somente após revisão e validação próprias. Não é necessário esperar a reformulação visual para corrigir os defeitos.

## Decisões de produto para evitar lacunas na execução

1. **Remover custo lançado:** ação Cancelar lançamento em registros manuais sem pagamento nem conciliação, exigindo motivo. Não há exclusão física de registros financeiros nesta entrega.
2. **Corrigir pagamento:** operação específica que estorna o pagamento selecionado, preserva obrigação e permite registrar o correto. Não reutilizar reversão do lançamento inteiro.
3. **Caixa:** novo pagamento exige escolher entre gerar movimento na conta de saída ou vincular movimento existente. Nunca gerar outro movimento ao vincular um existente. Pagamentos legados sem vínculo ficam sinalizados para revisão, sem backfill automático de saídas.
4. **Edição:** livre nos campos permitidos de lançamento manual em aberto; parcialmente pago permite descrição, observações e vencimento, preservando valor/classificação/competência até correção explícita. Importado ou conciliado apresenta origem e motivo do bloqueio.
5. **Parcelado:** competência não é deduzida silenciosamente de vencimento. Despesa parcelada informa competência única ou distribuída e apresenta prévia. Empréstimo usa cronograma do contrato.
6. **Recorrência:** gera valores previstos, confirmados pelo usuário antes de compor realizado. Alterações afetam uma ocorrência ou futuras não confirmadas; passadas e pagas ficam intactas.
7. **Resultado:** receita/CMV indisponíveis mantêm resultado indisponível. Estado inicial “Em apuração”; selo “Revisado” somente após tarefa de revisão mensal C6, com invalidação em mudanças posteriores.
8. **Ponto de equilíbrio:** remover indicador legado e velocímetro dependente de custo fixo. Não calcular substituto com total de despesas; só reintroduzir após classificação fixa/variável e validação específica, fora desta entrega.
9. **Próprio e-mail:** mudança autenticada com senha atual e unicidade. Informar que não há verificação por e-mail nesta versão; não criar dependência de SMTP. Troca de senha renova sessão atual e revoga as demais.

## Migrações, entrega e reversão

- [ ] Antes de executar, ler instruções locais, conferir `git status`, branch e worktrees. Isolar implementação conforme using-git-worktrees. Preservar alterações do usuário.
- [ ] Registrar baseline: `.venv/bin/pytest tests -q` e `node --test tests/*.cjs`; separar falhas existentes de regressões. Estes comandos ainda não foram rodados nesta tarefa de planejamento.
- [ ] Em B, migrations 012–015 e em C, 016–017 são números propostos sobre o registro atual encerrado em 011. Se o código avançar, renumerar todos os arquivos/referências antes do primeiro commit; não editar migrations aplicadas.
- [ ] Antes de migrar uma implantação, verificar backup restaurável; ensaiar em cópia isolada. Registrar contagem de lançamentos/eventos, total de pagamentos, saldos, IDs e vínculos antes/depois. Migração não recadastra obrigações.
- [ ] Rodar regressão SQLite e integração PostgreSQL descartável antes da liberação das tarefas transacionais. Concorrência deve ser testada com conexões separadas.
- [ ] Aplicar backend/esquema compatíveis antes dos novos assets. Atualizar versões de assets em `web/index.html`; evitar navegador antigo gravando por um caminho que ignora regras novas.
- [ ] Manter endpoints antigos como adaptadores das mesmas operações e regras; quando uma ação antiga não oferece dados suficientes, retornar erro legível pedindo atualização, sem efeito parcial.
- [ ] Reversão da aplicação deve usar build compatível com o esquema novo. O runner atual recusa banco com versão superior ao código; simplesmente voltar o commit não é rollback seguro. Nunca executar downgrade destrutivo para recuperar a UI.
- [ ] Depois do piloto, conferir rotina em desktop e celular, duas sessões simultâneas, importação repetida, datas perto da meia-noite e conflitos de edição. Não disparar pagamentos bancários reais: o produto registra movimentos.

## Jornada de aceite integrada

Em dados sintéticos: cadastrar R$ 1.000 → editar descrição → pagar R$ 400 → conferir R$ 600 pendentes → quitar → corrigir o segundo pagamento → conferir reabertura de R$ 600 → reconciliar com o extrato. Arquivar categoria não altera histórico. Renegociar contrato troca apenas parcelas futuras na previsão. Reabrir URL antiga de empréstimo mostra o contrato na área financeira. Trocar módulos mantém filtros e rascunho. Trocar senha permite novo login e invalida a senha anterior.

## Adiado conscientemente

Busca global, anexos/armazenamento, orçamento completo, pagamentos em lote, centros de custo, multiunidade, integrações bancárias novas, cenários avançados de juros e quitação antecipada com cálculo contratual automático. Todos permanecem no relatório como evolução, não como pré-requisitos escondidos. Esta entrega inclui recorrência básica, revisão mensal e manutenção de formulários; não inclui um motor contábil completo.

## Revisão do plano

- [x] Decisões mais recentes prevalecem: loja única, dois módulos, ausência do simulador legado.
- [x] Três defeitos reproduzidos têm testes de regressão especificados em A1–A3.
- [x] Pagamento/caixa/importação têm dependência explícita em B3–B5.
- [x] Conta pessoal, manutenção de formulários e revisão mensal têm tarefas em C.
- [x] Migração e rollback consideram o bloqueio de versões do runner atual.
- [x] Escopo adiado está separado da entrega principal.
