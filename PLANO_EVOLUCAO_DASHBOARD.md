# Plano de evolução funcional — Mercado duBairro

**Status:** planejado para execução futura  
**Data de elaboração:** 12/09/2026  
**Escopo:** configurações, usuários, gestão financeira, empréstimos, fluxo de caixa, integração bancária Stone/Open Finance e evolução da inteligência operacional.

## 1. Objetivo

Evoluir o dashboard de um painel de análise de vendas para uma plataforma de gestão da loja. Ao final do plano, os sócios deverão conseguir responder, em um único sistema:

1. Quanto a loja vendeu e lucrou no período?
2. Quais despesas consumiram o resultado?
3. Quanto dinheiro existe nas contas e quanto já está comprometido?
4. Quais recebimentos, contas e parcelas ainda estão pendentes?
5. Quais produtos exigem reposição, revisão de preço, promoção ou retirada do mix?
6. Quais ações foram atribuídas, executadas e tiveram resultado?

O sistema continuará usando o Mobne como fonte principal das vendas e dos dados operacionais. Dados bancários, despesas e contratos complementarão essa base sem duplicar receitas, custos ou pagamentos.

## 2. Estado atual verificado

O projeto já possui:

- autenticação por senha compartilhada e sessão;
- cadastro de empresas descobertas pela integração Mobne;
- sincronização de vendas, produtos, categorias, preços e estoque;
- armazenamento em SQLite local ou PostgreSQL em produção;
- reconciliação de vendas entre cupons e análise Mobne;
- resumo executivo, inteligência de preços, mapa de produtos, diagnóstico, sazonalidade, projeções, estoque e acompanhamento da sincronização;
- custo fixo único por empresa, usado somente para simulação;
- alertas de estoque zerado, preço abaixo do custo e produtos vendidos sem custo conhecido.

Limitações que este plano resolve:

- não existem usuários individuais, perfis ou permissões por empresa;
- informações cadastrais e parâmetros operacionais da loja não são administráveis;
- não há contas a pagar, despesas detalhadas, orçamento ou fechamento financeiro;
- custo fixo não possui composição nem vigência por mês;
- não há contratos de empréstimo, cronogramas ou saldo devedor;
- o fluxo de caixa não usa saldos e transações bancárias;
- estoque e preços são retratos atuais, sem série histórica completa;
- a lista de estoque parte dos produtos vendidos no período e pode omitir produtos parados;
- alertas indicam problemas, mas ainda não viram ações com responsável, prazo e resultado.

## 3. Princípios funcionais e contábeis

Estes princípios deverão orientar o modelo de dados, os cálculos e os textos da interface:

1. **Resultado e caixa são visões diferentes.** A competência informa quando a receita ou despesa pertence ao resultado; pagamento e recebimento informam quando o dinheiro movimentou a conta.
2. **Uma operação econômica deve ser reconhecida uma única vez.** O mesmo evento pode aparecer no Mobne, na agenda da Stone e no extrato bancário, mas deve possuir apenas uma receita ou despesa no resultado.
3. **Compra e custo da mercadoria vendida não são a mesma coisa.** O pagamento ao fornecedor afeta o caixa e o estoque. O custo da mercadoria vendida afeta o resultado conforme a venda. Não somar os dois como despesas do mesmo período.
4. **Parcela de empréstimo deve ser dividida.** Amortização reduz o saldo devedor e afeta o caixa; juros, tarifas e encargos são despesas financeiras.
5. **Pró-labore é despesa da empresa.** Deve ficar em remuneração dos sócios, com encargos quando aplicáveis. Distribuição de lucros deve ser registrada separadamente como destinação do resultado.
6. **Taxas de meios de pagamento devem ser conciliadas.** Taxa da venda, aluguel de equipamento e antecipação de recebíveis são componentes distintos.
7. **Dados importados mantêm origem e rastreabilidade.** Todo registro deverá indicar se veio do Mobne, Open Finance, conciliação Stone, lançamento manual ou regra automática.
8. **Valores estimados devem ser identificados.** Previsões, metas e simulações não podem aparecer como realizados.
9. **Meses fechados não mudam silenciosamente.** Reabertura e alterações posteriores exigem permissão e registro de auditoria.
10. **Integrações bancárias começam somente para consulta.** Iniciação de pagamentos ou movimentação de dinheiro ficará fora deste plano inicial.

## 4. Arquitetura funcional proposta

```text
Mobne ──────────────────────────────┐
                                    ├─> Eventos financeiros normalizados
Conta Stone / Open Finance ─────────┤          │
                                    │          ├─> Conciliação
Agenda de recebíveis Stone ─────────┤          │       │
                                    │          │       ├─> Fluxo de caixa
Lançamentos e contratos manuais ────┘          │       ├─> Resultado gerencial
                                               │       └─> Alertas e ações
Configurações, usuários e permissões ──────────┘
```

### Separação das fontes

| Fonte | Fonte de verdade para | Não deve ser usada isoladamente para |
|---|---|---|
| Mobne | Vendas, itens, produtos e custo da venda disponível | Confirmar que o dinheiro entrou no banco |
| Open Finance | Saldos e transações efetivamente disponibilizados pela conta conectada | Detalhar todas as vendas da maquininha Stone |
| Agenda/conciliação Stone | Recebíveis, liquidações, taxas e antecipações | Substituir o extrato bancário ou o registro da venda |
| Cadastro financeiro | Despesas, contratos, previsões e classificações gerenciais | Comprovar sozinho que uma conta foi paga |

## 5. Estrutura futura da navegação

### Gestão

- Resumo Executivo
- Produtos e Estoque
- Preços e Margens
- Metas e Projeções
- Central de Ações

### Financeiro

- Visão Geral
- Custos e Despesas
- Contas a Pagar
- Empréstimos
- Fluxo de Caixa
- Conciliação Bancária
- Recebíveis e Taxas
- Fechamento Mensal

### Administração

- Empresa e Lojas
- Usuários e Permissões
- Calendário de Operação
- Parâmetros e Alertas
- Integrações
- Histórico de Alterações

## 6. Plano de execução por fases

### Fase 0 — Definições financeiras e fundação técnica

**Objetivo:** definir contratos e regras antes de criar telas ou importar dados financeiros.

**Entregas:**

- glossário de receita, CMV, lucro bruto, despesa operacional, despesa financeira, pró-labore, distribuição de lucros, resultado gerencial e fluxo de caixa;
- plano de contas inicial com categorias, subcategorias e natureza de cada lançamento;
- definição de regime de competência e regime de caixa;
- modelo de centro de custo por loja e, futuramente, por departamento;
- estratégia de migrações versionadas para SQLite e PostgreSQL;
- identificadores imutáveis, trilha de auditoria e chaves de idempotência;
- política de fechamento, reabertura e exclusão lógica;
- revisão do contrato disponível no Financeiro do Mobne antes de decidir quais despesas podem ser importadas;
- fixtures sanitizadas para testes de vendas, despesas, empréstimos, transferências e recebíveis.

**Critérios de aceite:**

1. Cada exemplo de movimentação financeira possui tratamento documentado em competência e caixa.
2. Pagamento de fornecedor não duplica o CMV calculado a partir das vendas.
3. Parcela de empréstimo está separada em principal, juros e demais encargos.
4. Pró-labore e distribuição de lucros possuem categorias e efeitos distintos.
5. Todo registro importado possui origem, identificador externo e regra contra duplicidade.

**Estimativa:** 3 a 5 dias de produto, domínio e engenharia.

---

### Fase 1 — Configurações, empresas, usuários e permissões

**Objetivo:** substituir a senha compartilhada por acessos individuais e criar a base administrativa da plataforma.

**Entregas:**

- cadastro da empresa: razão social, nome fantasia, CNPJ, endereço, contatos e logo;
- cadastro de uma ou mais lojas/unidades e vínculo com a empresa correspondente no Mobne;
- criação, convite, edição, bloqueio e desativação de usuários;
- recuperação e troca de senha;
- perfis iniciais: Administrador, Sócio, Gerente e Consulta;
- permissões por módulo, operação e loja;
- permissão específica para visualizar custos, salários, pró-labore e resultado;
- preferências pessoais: loja, página inicial, filtros e notificações;
- calendário de funcionamento, horários, feriados e fechamentos excepcionais;
- histórico de login e de alterações administrativas;
- confirmação reforçada para operações sensíveis, como reabrir mês e alterar permissões.

**Regras iniciais dos perfis:**

| Perfil | Escopo sugerido |
|---|---|
| Administrador | Usuários, integrações, configurações e todos os dados |
| Sócio | Resultados, financeiro, metas, ações e lojas autorizadas |
| Gerente | Operação, estoque, preços e ações da loja; financeiro configurável |
| Consulta | Apenas visualização das telas e lojas autorizadas |

**Critérios de aceite:**

1. Não existem novas sessões criadas com uma senha compartilhada.
2. Um usuário sem permissão financeira recebe `403` também na API.
3. Um gerente de uma loja não visualiza dados de outra loja sem autorização.
4. Desativar um usuário invalida todas as sessões dele.
5. Alterações de permissões registram autor, data, valor anterior e novo valor.
6. O calendário de operação passa a alimentar médias diárias e metas.

**Estimativa:** 8 a 12 dias.

---

### Fase 2 — Cadastros e parâmetros de gestão

**Objetivo:** centralizar parâmetros hoje fixos no código ou ausentes.

**Entregas:**

- plano de contas editável, com categorias protegidas quando usadas por regras do sistema;
- fornecedores e beneficiários;
- contas bancárias, caixas físicos e formas de pagamento;
- parâmetros de curva ABC, margem-alvo e classificação por categoria;
- prazos de reposição, estoque de segurança e cobertura desejada;
- metas mensais de faturamento, lucro bruto, ticket e número de cupons;
- orçamento mensal por categoria de despesa;
- alertas configuráveis, responsáveis, frequência e horário silencioso;
- vigência por data para metas, margens e parâmetros operacionais;
- exceções por loja, categoria e produto.

**Critérios de aceite:**

1. Alterar um parâmetro futuro não recalcula meses fechados.
2. Regras específicas de produto prevalecem sobre categoria, loja e padrão global nessa ordem.
3. Metas e orçamentos possuem valor, competência, responsável e histórico.
4. A aplicação deixa de depender de metas fixas gravadas no JavaScript.

**Estimativa:** 5 a 8 dias.

---

### Fase 3 — Custos, despesas e contas a pagar

**Objetivo:** registrar o custo de operação completo e produzir resultado gerencial por competência.

**Categorias iniciais:**

- aluguel, condomínio, IPTU, seguros e segurança;
- energia, água, gás, internet, telefone e resíduos;
- salários, horas extras, benefícios, encargos, férias, 13º e rescisões;
- pró-labore e encargos, por sócio;
- limpeza, embalagens, materiais e uniformes;
- manutenção, refrigeração, dedetização e equipamentos;
- contabilidade, jurídico, licenças, sistemas e Mobne;
- publicidade, campanhas, entregas e comissões;
- tarifas bancárias, juros, multas e antecipações;
- impostos, com classificação revisada pela contabilidade;
- perdas por vencimento, avaria, quebra e inventário;
- aluguel de máquinas e outras taxas de meios de pagamento.

**Entregas:**

- lançamento com descrição, categoria, fornecedor, loja, competência, vencimento, pagamento e conta financeira;
- status: previsto, em aberto, parcialmente pago, pago, vencido, cancelado e estornado;
- despesas recorrentes, parceladas e rateadas entre lojas ou centros de custo;
- anexos de nota, boleto, contrato e comprovante;
- aprovações por valor e perfil;
- pagamentos parciais, estornos e ajustes sem apagar histórico;
- orçamento versus realizado por categoria;
- vencimentos dos próximos 7, 30, 60 e 90 dias;
- fechamento mensal com lista de pendências;
- substituição do custo fixo único pela composição das despesas do mês;
- importação opcional do Mobne somente após confirmar cobertura e sem duplicar lançamentos.

**Critérios de aceite:**

1. Uma despesa de agosto paga em setembro aparece no resultado de agosto e no caixa de setembro.
2. Uma despesa recorrente gera previsões futuras, identificadas como previstas até a confirmação.
3. Um pagamento parcial mantém saldo aberto correto.
4. Pró-labore reduz o resultado gerencial; distribuição de lucros não é classificada como despesa operacional.
5. Itens sensíveis de folha e pró-labore respeitam as permissões da Fase 1.
6. O resumo explica a diferença entre lucro bruto, resultado operacional e resultado gerencial.

**Estimativa:** 10 a 15 dias.

---

### Fase 4 — Empréstimos e financiamentos

**Objetivo:** acompanhar contratos, parcelas, custo do crédito e pressão da dívida sobre o caixa.

**Entregas:**

- cadastro de credor, finalidade, valor contratado, valor líquido recebido, datas, prazo, carência, sistema de amortização, taxa e CET;
- cronograma com principal, juros, tarifas, seguros, tributos e total da parcela;
- saldo devedor e pagamentos futuros;
- parcelas pagas, parciais, atrasadas, renegociadas e antecipadas;
- registro de amortização extraordinária;
- anexos do contrato e dos demonstrativos;
- alertas de vencimento e projeção do serviço da dívida;
- indicadores: dívida total, juros futuros, parcela mensal, dívida/faturamento e cobertura do serviço da dívida;
- separação visual entre resultado operacional, despesas financeiras e fluxo de financiamento.

**Critérios de aceite:**

1. Receber um empréstimo aumenta caixa e dívida, sem aumentar faturamento.
2. Pagar principal reduz caixa e saldo devedor, sem reduzir o resultado do período.
3. Juros e encargos afetam resultado e caixa nas datas corretas.
4. A soma do principal futuro coincide com o saldo devedor, ressalvados ajustes documentados.
5. Renegociação preserva o contrato e cronograma anteriores no histórico.

**Estimativa:** 6 a 9 dias.

---

### Fase 5 — Fluxo de caixa manual e projetado

**Objetivo:** consolidar as informações financeiras antes de conectar qualquer banco.

**Entregas:**

- contas financeiras: Conta Stone, outros bancos e caixa físico;
- saldos iniciais por conta e data;
- entradas e saídas realizadas;
- contas a pagar e parcelas futuras;
- recebimentos previstos de vendas e outras receitas;
- transferências entre contas próprias sem inflar entradas e saídas operacionais;
- depósitos e retiradas do caixa físico;
- projeção diária, semanal e mensal;
- cenários base, conservador e otimista;
- reserva mínima configurável;
- alertas de saldo projetado negativo;
- visão por conta, loja, categoria e período;
- exportação em CSV e relatório de fechamento.

**Critérios de aceite:**

1. O saldo final de cada conta corresponde ao saldo inicial mais entradas menos saídas.
2. Transferências internas não alteram o caixa consolidado.
3. Alterar uma previsão não modifica um lançamento realizado.
4. O usuário distingue visualmente previsto, vencido, realizado e conciliado.
5. O sistema projeta a menor posição de caixa dos próximos 30, 60 e 90 dias.

**Estimativa:** 8 a 12 dias.

---

### Fase 6 — Conta Stone via Open Finance

**Objetivo:** importar saldos e transações da Conta Stone com consentimento do cliente e conciliar o caixa realizado.

**Pré-condição obrigatória:** realizar uma prova de conceito com um provedor autorizado ou parceiro, confirmando suporte à Conta Stone PJ, CNPJ da empresa, saldos, transações, histórico disponível, webhooks, frequência, custo e ambiente de homologação. A documentação pública da Stone informa que os dados da maquininha não fazem parte atualmente do compartilhamento de dados da Conta Stone; por isso, recebíveis permanecem na Fase 7.

**Entregas:**

- escolha documentada entre integração direta como participante/parceiro e agregador Open Finance;
- fluxo de consentimento no ambiente da instituição, sem solicitar credenciais bancárias dentro do dashboard;
- armazenamento seguro de identificadores e tokens no backend;
- sincronização incremental e idempotente de contas, saldos e transações;
- página de integrações com estado, escopos, última atualização e ação de reconexão;
- cancelamento do vínculo pelo administrador;
- classificação sugerida das transações;
- regras automáticas confirmadas pelo usuário;
- conciliação de uma transação com despesa, recebimento, parcela ou transferência já cadastrada;
- fila de transações pendentes, possíveis duplicidades e divergências;
- fallback por importação OFX/CSV durante piloto ou indisponibilidade do provedor.

**Segurança:**

- acesso inicialmente somente para leitura;
- consentimento explícito e revogável;
- criptografia de segredos e identificadores sensíveis;
- nenhuma credencial bancária em logs, navegador ou banco em texto aberto;
- trilha de auditoria para conexão, renovação e revogação;
- política de retenção e exclusão compatível com LGPD;
- webhooks validados por assinatura, com proteção contra repetição.

**Critérios de aceite:**

1. Importar novamente o mesmo período não cria transações duplicadas.
2. O saldo importado é identificado como saldo bancário confirmado e mostra o horário da atualização.
3. Revogar consentimento interrompe novas sincronizações sem excluir o histórico já necessário à escrituração gerencial.
4. Um lançamento ambíguo permanece pendente; não é classificado silenciosamente.
5. Associar o débito de uma conta já cadastrada marca seu pagamento sem criar outra despesa.
6. Falha no provedor preserva a última base válida e informa que os dados estão desatualizados.

**Estimativa:** 10 a 18 dias após homologação comercial e técnica. O prazo externo do provedor não está incluído.

---

### Fase 7 — Recebíveis, taxas e conciliação Stone

**Objetivo:** ligar vendas Mobne, agenda da Stone e créditos bancários à mesma operação econômica.

**Pré-condição obrigatória:** obter acesso à solução de conciliação Stone diretamente ou por parceira conciliadora e validar os campos disponíveis.

**Entregas:**

- cadastro de adquirente, bandeira, modalidade, parcelamento, taxa contratada, tarifa fixa, prazo e vigência;
- importação da agenda de recebíveis, liquidações, cancelamentos, chargebacks e antecipações;
- comparação entre taxa contratada e taxa efetivamente descontada;
- vínculo de uma liquidação a várias vendas e de uma venda parcelada a várias liquidações;
- previsão de recebimentos por data;
- divergências de valor, atraso, ausência de liquidação e cobrança inesperada;
- custo de antecipação separado da taxa normal da venda;
- indicadores por adquirente e modalidade;
- fluxo completo de conferência: venda bruta, taxa, valor líquido previsto e crédito bancário confirmado.

**Exemplo obrigatório de reconciliação:**

```text
Venda Mobne:                 R$ 1.000,00
Taxa de adquirência:           R$ 25,00
Recebível líquido Stone:       R$ 975,00
Crédito confirmado no banco:   R$ 975,00
Receitas reconhecidas:                1
Despesas com taxa:                    1
Entradas bancárias:                   1
```

**Critérios de aceite:**

1. O exemplo acima gera uma receita, uma despesa de taxa e uma entrada bancária vinculadas.
2. Diferença acima da tolerância configurada impede conciliação automática.
3. Antecipação mostra valor antecipado, custo, recebíveis consumidos e impacto no caixa futuro.
4. O sistema não presume que todo crédito Stone corresponde a uma única venda.
5. Cancelamentos e chargebacks mantêm rastreabilidade até a venda original.

**Estimativa:** 10 a 18 dias após liberação de acesso à conciliação.

---

### Fase 8 — Inteligência operacional de produtos, preços e metas

**Objetivo:** transformar análises existentes em decisões operacionais específicas.

**Entregas:**

#### Catálogo e estoque

- construir a lista a partir do catálogo completo, incluindo produtos sem venda no período;
- produtos sem venda há 30, 60 e 90 dias;
- snapshots históricos de estoque, preços e custos;
- cobertura estimada em dias, estoque de segurança, ponto de reposição e quantidade sugerida;
- considerar estoque disponível, reservas, demanda recente, prazo de entrega e múltiplo de embalagem;
- alertas preventivos antes da ruptura;
- capital imobilizado e risco de vencimento quando os dados estiverem disponíveis.

#### Página individual do produto

- histórico de receita, quantidade, custo, margem, preço e estoque;
- posição atual e parâmetros de reposição;
- cupons relacionados com acesso controlado;
- alterações de preço e custo;
- tarefas e promoções relacionadas;
- comparação com categoria e produtos semelhantes.

#### Preços e margens

- margem-alvo por categoria ou produto;
- simulador de preço, volume e margem;
- lista de reajustes proposta, revisada antes de qualquer envio ao ERP;
- impacto estimado e acompanhamento posterior;
- distinção entre margem histórica da venda e custo/preço atuais.

#### Metas e comparação

- realizado versus meta mensal;
- valor necessário por dia aberto para atingir a meta;
- comparação por semana, quinzena, trimestre e intervalo personalizado;
- comparação por dias equivalentes do calendário de funcionamento;
- filtros coerentes por loja, categoria e produto.

**Critérios de aceite:**

1. Um produto com estoque e nenhuma venda no mês aparece como parado.
2. “Giro” atual passa a ser rotulado como frequência de venda; giro de estoque usa estoque médio quando houver histórico suficiente.
3. Reposição sugerida apresenta fórmula, parâmetros e data dos dados usados.
4. Simulações não alteram preço no Mobne.
5. Toda meta informa competência, calendário usado e progresso esperado versus realizado.

**Estimativa:** 15 a 24 dias, divididos em entregas menores.

---

### Fase 9 — Central de ações, promoções e inteligência de cesta

**Objetivo:** acompanhar decisões, responsáveis e resultados dentro do sistema.

**Entregas:**

- criar tarefa a partir de alertas de estoque, preço, custo, financeiro ou conciliação;
- responsável, prioridade, prazo, comentário, anexo e status;
- lembretes e histórico;
- prevenção de alertas e tarefas repetidas para o mesmo problema;
- indicadores de prazo e taxa de conclusão;
- registro de promoção com produtos, datas, preço, objetivo e orçamento;
- comparação antes, durante e depois da promoção;
- análise de produtos comprados juntos e itens por cupom;
- sugestões de kits, exposição conjunta e venda cruzada;
- acompanhamento do resultado após reajuste, compra ou promoção.

**Critérios de aceite:**

1. Um alerta convertido em tarefa mantém vínculo com a evidência original.
2. Resolver uma tarefa não oculta o problema se a condição continuar presente.
3. O sistema mostra resultado observado após uma ação, sem afirmar causalidade não comprovada.
4. A análise de cesta conta cada cupom uma vez e preserva a deduplicação atual.
5. Permissões impedem usuários não autorizados de criar ou atribuir ações financeiras.

**Estimativa:** 9 a 14 dias.

---

### Fase 10 — Relatórios, fechamento e operação contínua

**Objetivo:** consolidar a rotina mensal e preparar operação segura em produção.

**Entregas:**

- demonstrativo gerencial mensal com receita, CMV, lucro bruto, despesas, pró-labore, resultado operacional e despesas financeiras;
- fluxo de caixa realizado e projetado;
- posição de contas a pagar, recebíveis e dívida;
- relatório para reunião dos sócios em PDF e CSV;
- comentários do mês e explicações para eventos excepcionais;
- pacote de fechamento com pendências e conciliações;
- indicadores de qualidade e atualização de cada fonte;
- backup, restauração testada, retenção e monitoramento;
- notificações de falha de integração, consentimento e fechamento;
- documentação operacional e treinamento dos perfis;
- política de suporte e correção dos dados.

**Critérios de aceite:**

1. O relatório mensal pode ser reproduzido a partir da versão fechada do período.
2. Cada número possui origem e possibilidade de rastreamento até os lançamentos.
3. O fechamento informa todas as pendências antes da confirmação.
4. Reabrir o mês exige permissão e deixa registro completo.
5. Backup e restauração são executados em ambiente de teste com sucesso.

**Estimativa:** 7 a 11 dias.

## 7. Dependências e sequência recomendada

```text
Fase 0: regras e modelo
   ├──> Fase 1: usuários e permissões
   │       └──> Fase 2: configurações e parâmetros
   │               ├──> Fase 3: despesas
   │               │       ├──> Fase 4: empréstimos
   │               │       └──> Fase 5: fluxo de caixa
   │               │               ├──> Fase 6: Open Finance
   │               │               └──> Fase 7: recebíveis Stone
   │               └──> Fase 8: inteligência operacional
   └───────────────────────────────> Fase 9: central de ações

Fases 3 a 9 ──────────────────────> Fase 10: fechamento e relatórios
```

A Fase 5 deve funcionar com lançamentos manuais antes da integração bancária. Isso permite validar os conceitos financeiros sem depender de contratação, homologação ou disponibilidade de um provedor. A Fase 7 vem depois do extrato bancário porque uma agenda de recebíveis só fica totalmente útil quando o crédito pode ser confirmado. A inteligência operacional pode avançar em paralelo depois das fundações de usuários e parâmetros.

## 8. Modelo de dados inicial

Os nomes abaixo são uma proposta para orientar a implementação. A Fase 0 deve validar campos e cardinalidades antes da primeira migração.

| Domínio | Entidades principais |
|---|---|
| Identidade | `users`, `roles`, `permissions`, `user_company_roles`, `user_store_access`, `user_sessions` |
| Organização | `companies`, `stores`, `company_profiles`, `operating_calendar` |
| Configuração | `settings`, `setting_versions`, `goals`, `budgets`, `alert_rules` |
| Financeiro | `chart_of_accounts`, `counterparties`, `financial_entries`, `entry_allocations`, `payments`, `attachments` |
| Dívidas | `loan_contracts`, `loan_schedules`, `loan_payments`, `loan_events` |
| Caixa | `financial_accounts`, `cash_balances`, `bank_transactions`, `cash_forecasts` |
| Integrações | `connections`, `consents`, `sync_runs`, `external_records`, `webhook_events` |
| Conciliação | `reconciliation_groups`, `reconciliation_links`, `reconciliation_rules`, `receivables` |
| Operação | `inventory_snapshots`, `price_snapshots`, `replenishment_rules`, `promotions` |
| Execução | `actions`, `action_events`, `comments`, `notifications` |
| Governança | `audit_log`, `financial_closures`, `data_quality_issues` |

### Campos obrigatórios para lançamentos financeiros

- empresa e loja;
- natureza: receita, CMV, despesa operacional, despesa financeira, ativo, passivo, transferência ou distribuição;
- categoria e subcategoria;
- competência;
- vencimento;
- data de liquidação, quando realizada;
- valor previsto e valor realizado;
- contraparte;
- conta financeira;
- origem e identificador externo;
- status de conciliação;
- autor, aprovador e histórico de alterações.

Valores monetários devem continuar armazenados em centavos inteiros. Datas civis, instantes e fuso horário precisam ter tipos e usos definidos explicitamente.

## 9. APIs previstas

As rotas definitivas devem seguir os padrões de autenticação e empresa existentes.

| Área | Rotas mínimas |
|---|---|
| Usuários | `/api/users`, `/api/users/{id}`, `/api/roles`, `/api/permissions` |
| Empresa | `/api/companies/{id}/profile`, `/api/companies/{id}/stores`, `/api/companies/{id}/calendar` |
| Despesas | `/api/companies/{id}/financial-entries`, `/api/financial-entries/{id}/payments` |
| Orçamento | `/api/companies/{id}/budgets`, `/api/companies/{id}/goals` |
| Empréstimos | `/api/companies/{id}/loans`, `/api/loans/{id}/schedule`, `/api/loans/{id}/events` |
| Fluxo de caixa | `/api/companies/{id}/cash-flow`, `/api/companies/{id}/cash-forecast` |
| Bancos | `/api/companies/{id}/bank-connections`, `/api/bank-connections/{id}/sync` |
| Conciliação | `/api/companies/{id}/reconciliation`, `/api/reconciliation/{id}/confirm` |
| Recebíveis | `/api/companies/{id}/receivables`, `/api/companies/{id}/payment-fees` |
| Produtos | `/api/companies/{id}/products/{product_id}`, `/api/companies/{id}/replenishment` |
| Ações | `/api/companies/{id}/actions`, `/api/actions/{id}/events` |
| Fechamento | `/api/companies/{id}/closures`, `/api/closures/{period}/reopen` |

Toda mutação deve validar permissão no backend, empresa, CSRF, período fechado e versão concorrente do registro.

## 10. Estratégia de conciliação

### Ordem de tentativa

1. Correspondência explícita pelo identificador externo.
2. Vínculo já conhecido entre venda, parcela, recebível e liquidação.
3. Regra confirmada de contraparte e conta.
4. Candidatos por valor, data, descrição e sentido da transação.
5. Revisão manual quando houver mais de um candidato ou divergência.

### Estados

- não conciliado;
- sugestão disponível;
- conciliado automaticamente;
- conciliado manualmente;
- parcialmente conciliado;
- divergente;
- ignorado com justificativa.

O sistema deve aceitar relações muitos-para-muitos: uma liquidação pode reunir várias vendas, uma venda parcelada pode gerar vários recebimentos e um pagamento bancário pode quitar várias contas.

## 11. Estratégia de testes

| Camada | Cobertura mínima |
|---|---|
| Unidade | Competência versus caixa, parcelamento, amortização, juros, rateio, metas, saldos e classificação |
| Integração | Migrações SQLite/PostgreSQL, permissões, importações idempotentes, fechamento e reabertura |
| Contrato | Respostas Mobne, Open Finance e conciliação Stone com fixtures sanitizadas |
| Conciliação | Uma venda/uma liquidação, agrupamentos, parcelamento, divergência, chargeback e transferência interna |
| E2E | Usuário cria despesa, aprova, paga, concilia e visualiza resultado e caixa corretos |
| Segurança | Isolamento entre empresas, acesso a salários/pró-labore, CSRF, revogação e webhooks repetidos |
| Recuperação | Falha durante sincronização, repetição do job, restauração de backup e provedor indisponível |

### Casos obrigatórios

1. Conta de agosto paga em setembro.
2. Compra de mercadoria que não duplica CMV.
3. Pró-labore separado da distribuição de lucros.
4. Parcela com principal, juros, tarifa e pagamento parcial.
5. Empréstimo recebido sem aumentar receita.
6. Transferência Stone para outro banco sem aumentar entrada consolidada.
7. Venda Mobne de R$ 1.000, taxa de R$ 25 e liquidação de R$ 975.
8. Duas vendas agrupadas em um único crédito bancário.
9. Uma venda parcelada liquidada em datas diferentes.
10. Reimportação do mesmo extrato, webhook ou lote sem duplicidade.
11. Consentimento revogado e dados desatualizados claramente indicados.
12. Usuário sem permissão tentando acessar ou alterar pró-labore.
13. Produto com estoque, mas sem vendas, incluído na análise de parados.
14. Mês fechado rejeitando alteração comum e aceitando reabertura auditada.

## 12. Migração e compatibilidade

1. Preservar as tabelas e endpoints atuais durante a introdução dos novos módulos.
2. Criar migrações incrementais e reversíveis sempre que possível.
3. Converter o custo fixo existente em uma configuração legada somente após a composição de despesas estar pronta.
4. Durante a transição, identificar o “Resultado simulado legado” e o “Resultado gerencial” separadamente.
5. Não apagar datasets do Mobne nem sobrescrever snapshots históricos.
6. Ativar integrações externas por feature flag e empresa piloto.
7. Executar o primeiro fechamento financeiro em paralelo com a conferência manual.
8. Manter importação OFX/CSV como contingência operacional, sem transformá-la na fonte principal.

## 13. Rollback por tipo de mudança

- **Banco:** migração reversa ou restauração de backup verificado antes da implantação.
- **Integração:** desligar a feature flag e preservar os últimos dados válidos.
- **Conciliação:** desfazer os vínculos sem excluir os registros de origem.
- **Fechamento:** reabrir somente por operação auditada.
- **Cálculos:** versionar regras e manter a versão usada em cada fechamento.
- **Interface:** manter endpoints antigos durante uma versão de transição.

## 14. Métricas de sucesso

As metas exatas serão definidas após medir o processo atual. O produto deverá acompanhar:

- percentual das despesas conciliadas;
- percentual dos recebíveis conciliados;
- quantidade e valor de divergências;
- tempo gasto no fechamento mensal;
- dias com projeção de caixa negativa;
- contas e parcelas vencidas;
- taxa real de meios de pagamento por modalidade;
- economia identificada em taxas e despesas;
- rupturas de produtos Curva A;
- capital em produtos sem venda há 30/60/90 dias;
- tarefas concluídas no prazo;
- resultado observado após ações de preço, reposição e promoção;
- atualização e disponibilidade das integrações.

## 15. Escopo explicitamente excluído deste plano

- movimentar dinheiro ou iniciar pagamentos via Open Finance;
- executar reajustes diretamente no Mobne sem revisão humana;
- substituir o sistema contábil ou produzir demonstrações contábeis oficiais;
- calcular automaticamente obrigações fiscais e trabalhistas sem validação profissional;
- folha de pagamento completa;
- cobrança de clientes e emissão fiscal;
- concessão ou contratação automática de crédito;
- uso de dados pessoais de clientes para marketing sem base legal e escopo específico.

## 16. Marcos de entrega recomendados

| Marco | Fases | Resultado observável |
|---|---|---|
| M1 — Administração segura | 0 a 2 | Usuários individuais, permissões, empresa, lojas e parâmetros |
| M2 — Resultado gerencial | 3 e 4 | Despesas, pró-labore, empréstimos e resultado detalhado |
| M3 — Caixa controlado | 5 | Fluxo realizado e projetado funcionando manualmente |
| M4 — Caixa conectado | 6 | Saldo e extrato Stone sincronizados e conciliáveis |
| M5 — Recebíveis conferidos | 7 | Vendas, taxas, agenda e créditos bancários ligados |
| M6 — Operação orientada | 8 e 9 | Reposição, preço, metas, ações, promoções e cesta |
| M7 — Fechamento completo | 10 | Relatório dos sócios e fechamento reproduzível |

## 17. Definition of Done do programa

O plano estará concluído quando:

1. cada usuário acessar somente as empresas, lojas e dados autorizados;
2. despesas, pró-labore, empréstimos e meios de pagamento possuírem tratamento correto em resultado e caixa;
3. o fluxo de caixa fechar com as contas conectadas, dentro de uma diferença zero ou explicitamente justificada;
4. vendas Mobne, recebíveis Stone e créditos bancários não gerarem duplicidade;
5. projeções mostrarem premissas, competência, data de atualização e grau de confirmação;
6. estoque incluir itens parados e oferecer alertas preventivos de reposição;
7. alertas puderem virar ações acompanhadas até o resultado;
8. o fechamento mensal gerar um relatório rastreável para os sócios;
9. sincronizações repetidas forem idempotentes e falhas preservarem a última base válida;
10. testes automatizados, restauração de backup e documentação operacional estiverem validados em produção piloto.

## 18. Próximo passo quando a execução começar

Iniciar pela Fase 0 em uma especificação curta de domínio, usando amostras reais e sanitizadas de:

- uma despesa paga no mesmo mês;
- uma despesa paga em mês diferente da competência;
- uma compra de mercadorias;
- um pró-labore e uma distribuição de lucros;
- um contrato e uma parcela de empréstimo;
- uma venda Mobne liquidada pela Stone;
- uma transferência entre contas próprias.

Depois de validar esses sete casos com os sócios e a contabilidade, implementar o Marco M1. A prova de conceito Open Finance pode ser iniciada comercialmente em paralelo, sem bloquear as fases financeiras internas.
