# Análise de experiência — Mercado duBairro

Data: 12/09/2026. Escopo: os 15 destinos do menu da aplicação atual em `web/`, seus fluxos JavaScript, estilos e endpoints associados. Inspeção visual da versão publicada: Sincronização, Resumo e carregamento de Financeiro. As demais telas foram avaliadas pelo código; não houve teste completo de transações, dispositivos móveis, carga ou perfis de acesso. Nenhuma alteração funcional foi realizada.

Aplicação: JavaScript sem framework, CSS próprio, ECharts e backend Python/FastAPI. Recomendações apoiadas na skill ui-ux-pro-max e na análise do produto. A busca da skill não encontrou correspondência específica para virtualização; essa recomendação deriva do quick-reference da skill e da implementação observada, não de um resultado da busca.

## Diagnóstico

O sistema oferece análises úteis, mas coloca relatórios, rotinas e manutenção no mesmo nível. O principal ganho virá de organizar a experiência pelas tarefas do gestor: entender o negócio, investigar vendas e produtos, operar o financeiro e administrar o sistema.

Há boas bases a preservar: URLs com período e histórico de navegação, indicação de mês parcial, estados de carregamento, alertas com links para produtos filtrados, busca com atraso de 150 ms, foco visível, tratamento de movimento reduzido, gráficos com infraestrutura de tabela acessível e cache do dashboard no cliente.

## Menu proposto

Proposta revisada: dois módulos principais, **Análises** e **Financeiro**, escolhidos por um seletor com texto no topo do menu lateral. Abaixo, mostrar somente as páginas do módulo ativo. Configurações e Minha conta ficam sempre disponíveis no rodapé. Lembrar a última página e os filtros de cada módulo; URLs diretas abrem automaticamente o módulo correto. Não criar níveis extras para os sete destinos analíticos.

| Grupo | Páginas |
|---|---|
| Análises | Resumo executivo; Preços e margens; Mapa de produtos; Desempenho de vendas; Sazonalidade e tendências; Projeções de vendas; Produtos e estoque |
| Financeiro | Resultado gerencial; Contas a pagar (inclui parcelas e contratos de empréstimos); Fluxo de caixa; Custos e despesas; Conciliação bancária |
| Configurações | Dados da empresa; Usuários e permissões; Calendário; Integrações e sincronização |

Metas e Alertas só devem parecer configurações utilizáveis quando houver funcionalidade. Hoje são páginas de promessa. Podem ficar explicitamente sinalizadas como indisponíveis, fora da navegação cotidiana.

Esta organização substitui a proposta inicial de cinco grupos. Considerar apenas uma loja: sem seleção de loja nos formulários ou filtros. Preservar atalhos contextuais entre módulos, sem duplicar telas e lançamentos.

### Sincronização dentro de Configurações

Recomendação: **Configurações → Integrações e sincronização → Mobne**. Já existe `configuracoes/integracoes`, mas sua implementação é apenas informativa. Reaproveitar ali o painel funcional de sincronização.

- Manter no topo um indicador compacto de atualização, com link para detalhes. Exibir fonte e horário pertinentes à tela; o horário das vendas Mobne não representa necessariamente o financeiro manual.
- Destacar somente “Atualizar dados”. Explicar o período abrangido. Histórico completo e reprocessamento ficam em “Opções avançadas”.
- Mostrar atualização em andamento, etapa, progresso coerente e resultado final. Na inspeção publicada havia uma execução concluída com contador `9/6` e descrição ainda referente a página de cupons: o progresso precisa de revisão.
- Avisar sobre falhas relevantes na Visão geral, sem exigir visitas frequentes a Configurações.
- Preservar links antigos `#/sync/...` por redirecionamento, inclusive links de estados vazios. O relógio de atualização deve reconhecer a nova rota e não reconstruir todo o painel.
- Separar o termo “reprocessar vendas Mobne” da conciliação bancária para não confundir duas operações diferentes.

## Problemas prioritários

### P1 — Conclusões positivas com premissas inadequadas

**Observado em produção:** custo fixo igual a zero, resultado simulado igual ao lucro bruto e narrativa “ponto de equilíbrio ... superado com folga de —”. Não é possível concluir se zero foi intencional ou ausência de configuração.

**Código:** `web/assets/app.js`, `resumoNarrative`, `renderResumo` e `updateCustoFixo`; `web/assets/insights.js`, `renderVisao`.

**Direção revisada após esclarecimento do usuário:** substituir o Resultado simulado do Resumo pelo Resultado gerencial, alimentado pelos lançamentos reais de custos e despesas e pela mesma fonte do Financeiro. Remover a premissa manual de custo fixo da navegação. Indicar quando a competência ainda está em apuração; ausência de lançamentos não comprova ausência de despesas. Nas projeções, partir dos custos cadastrados e distinguir hipóteses de valores realizados. Revisar os termos “Meta de lucro líquido”, “Margem real” e “o negócio está seguro”, que hoje extrapolam a base de cálculo. O ponto de equilíbrio depende de uma classificação adequada de custos fixos e variáveis, não da substituição cega pelo total das despesas.

### P1 — Atualização automática pode apagar trabalho em andamento

**Evidência no código:** `onAuthenticated` cria um intervalo de 60 segundos que chama `renderPage` quando não há `APP.dashboard`. Se a primeira página acessada após o login for Configurações ou uma página financeira, esse objeto pode continuar vazio. A nova renderização substitui os formulários. Uma sincronização concluída também invalida o objeto. O intervalo de 60 segundos não é armazenado para limpeza na saída.

**Melhoria:** separar atualização de status e atualização de conteúdo; preservar rascunhos; oferecer “Novos dados disponíveis” quando houver edição. Criar um único agendamento por sessão, com limpeza no logout e tratamento de falha de rede. Validar com formulário parcialmente preenchido por mais de um minuto.

### P1 — Respostas atrasadas podem exibir a tela errada

**Evidência no código:** `renderPage` espera o dashboard e só protege explicitamente a navegação para `sync`. Se o usuário for para Configurações ou Financeiro durante essa espera, o fallback pode renderizar o Resumo na rota nova. Os renderizadores financeiros e administrativos também escrevem em `#content` depois de requisições sem conferir se a rota ainda é a mesma.

**Melhoria:** associar cada carregamento à empresa, rota e período capturados no início; cancelar requisições obsoletas ou descartar respostas antigas. Testar troca rápida entre páginas e meses. Trata-se de risco identificado por análise estática, não de reprodução concluída no navegador.

### P1 — Registro de pagamento incompleto

**Evidência:** `web/assets/finance.js`, `renderContasPagar` e `onSettleEntry`, enviam o valor original e a data de hoje sem oferecer edição. Contas parcialmente pagas aparecem na lista, mas o saldo pendente não é usado; `backend/finance/entries.py` rejeita pagamento que exceda o total.

**Melhoria:** trocar “Pagar” por “Registrar pagamento”, coerente com a ação de registro. Abrir um painel com total, já pago, saldo, valor a registrar e data. Usar saldo pendente como padrão e permitir pagamento parcial. Após salvar, atualizar apenas a linha e os totais, com mensagem clara.

### P1 — Período global tem significados diferentes por tela

**Evidência:** o seletor é sempre montado com meses sincronizados do Mobne. Despesas e Resultado usam competência; Contas a Pagar lista todas as competências; Empréstimos e Conciliação não usam o mês; Fluxo de Caixa calcula sua janela a partir de hoje. Ainda assim, o topo mostra o mesmo mês e horário de vendas.

**Melhoria:** filtros contextuais: competência no resultado/despesas, vencimento em contas a pagar, intervalo e conta bancária na conciliação, horizonte de projeção no caixa. Permitir competências financeiras futuras independentemente de existir venda sincronizada. Ocultar o filtro temporal em configurações.

### P2 — Excesso de informação antes da decisão

**Observado:** quatro blocos de contexto/alerta antes de oito indicadores no Resumo. Ticket aparece tanto no cartão de Margem quanto em seu próprio cartão. Menu extenso faz nomes quebrarem e empurra o simulador para baixo da área inicialmente visível.

**Melhoria:** primeira linha com quatro indicadores essenciais e suas comparações; segundo bloco “Precisa de atenção”, com três prioridades e ação direta. Detalhes operacionais e simulações abaixo. Trocar M/M e A/A por rótulos compreensíveis ou explicação acessível. O detalhamento deve continuar disponível.

### P2 — Simulador altera configuração compartilhada

**Evidência:** `updateCustoFixo` grava `/config` automaticamente ao mudar o campo, invalidando o dashboard. A API armazena o custo por empresa, enquanto a interface o apresenta ao lado do período mensal.

**Melhoria revisada:** remover o simulador legado e usar a central de custos como fonte. Em Projeções e cenários, permitir ajustes temporários sobre essa base, sem alterar lançamentos reais. Respeitar a competência dos custos e explicar as premissas futuras.

### P2 — Formulários e tabelas inconsistentes

**Evidência:** cadastros de despesa, empréstimo e caixa não bloqueiam todos os novos envios durante a requisição. Algumas falhas usam `alert()`, outras texto inline; sucessos substituem o formulário inteiro. Tabelas financeiras usam `<table>` sem a classe `data-table`, e o estilo alternativo de células está restrito a `.settings-panel`, que não envolve essas páginas. Há mistura de emojis e Lucide.

**Melhoria:** componentes compartilhados de tabela, formulário e mensagem. Desabilitar submissão enquanto salva; manter valores em erros; mensagem de sucesso persistente e prevenção de duplicidade no servidor para operações relevantes. Formulários longos abrem por ação “Novo”, deixando consulta e pendências primeiro.

### P2 — Acessibilidade semântica incompleta

**Evidência:** títulos principais são frequentemente `div`, não `h1`; conciliação tem selects sem rótulo associado e motivo de desfazimento só com placeholder; datas de pagamento de parcelas não têm rótulo específico. O drawer móvel move o foco, mas não há contenção de foco/inativação do fundo no fluxo examinado.

**Melhoria:** hierarquia real de títulos; rótulos de controles contextualizados; mensagens ligadas aos campos; manter foco no drawer quando ele bloquear o conteúdo. Textos secundários de 11 px merecem revisão de legibilidade. Há estilos de foco e redução de movimento existentes: preservá-los. Contraste e mobile precisam de validação instrumental, não foram certificados nesta análise.

Referências: [W3C — rótulos](https://www.w3.org/WAI/tutorials/forms/labels/) e [W3C — mensagens de formulário](https://www.w3.org/WAI/tutorials/forms/notifications/).

### P2 — Gargalos de crescimento e espera

**Evidência:** `backend/api.py:dashboard` desserializa e resume todos os meses do histórico em cada chamada; o cliente recebe um payload conjunto para páginas diferentes. Estoque filtra, ordena e renderiza todas as linhas resultantes; já existe debounce de busca. `/finance/entries` sem competência retorna todos os lançamentos e o cliente filtra pendentes. Empréstimos renderiza contratos e parcelas completos. Todos os scripts, inclusive ECharts, são carregados na entrada.

**Melhoria:** agregados por empresa/mês/versão no servidor; separar dados pesados sob demanda; filtrar e paginar operações financeiras no backend; paginar tabelas e avaliar virtualização só quando o volume justificar. Cache precisa considerar versões de catálogos e configuração, além das vendas. Medir antes de escolher otimizações: latência p50/p95, tamanho de resposta, tempo até indicadores úteis e resposta da busca.

**Observação de produção:** Financeiro permaneceu em skeleton nas capturas feitas durante a inspeção. Isso não identifica a causa; investigar requisição/servidor separadamente. A função `api` não define timeout próprio. Oferecer mensagem de demora, repetição e recuperação sem exigir recarregar a aplicação.

## Melhorias por página

| Página atual | Proposta específica |
|---|---|
| Resumo Executivo | Visão geral com quatro KPIs principais, Resultado gerencial vindo do Financeiro, situação da apuração e pendências priorizadas |
| Inteligência de Preços | Preços e margens: lista de produtos a revisar por impacto, filtros e explicação de “erosão/markdown”; mostrar base temporal do preço e custo |
| Mapa de Produtos | Curva ABC e desempenho: conectar seleção do gráfico à lista filtrada; permitir abrir detalhes do produto |
| Diagnóstico de Faturamento | Desempenho de vendas: explicar quanto da variação vem de cupons e ticket; oferecer comparação mensal quando a anual não estiver disponível |
| Sazonalidade e Tendências | Sazonalidade: destacar cobertura do histórico, meses incompletos e período comparável; detalhes estatísticos recolhíveis |
| Visão Futurista | Projeções e cenários: premissas editáveis, simulação claramente identificada, base e limitações próximas do número |
| Produtos & Estoque | Consulta de produtos: filtros salvos, busca por código quando disponível, paginação, detalhes e data do estoque; explicitar que a lista atual é construída a partir de produtos vendidos no período |
| Financeiro | Resultado gerencial: demonstrativo em sequência receita → custos → despesas → resultado, com abertura dos lançamentos que compõem cada linha |
| Custos e Despesas | Lista e filtros primeiro; botão Nova despesa; distinguir competência/vencimento; oferecer edição/estorno conforme suporte e permissões |
| Contas a Pagar | Filtros Vencidas/Hoje/7 dias/Todas, totais pendentes e registro parcial com saldo e data |
| Empréstimos | Visão consolidada de dívida, parcelas próximas e contratos recolhíveis; prévia das parcelas antes de cadastrar |
| Fluxo de Caixa | Gráfico do saldo previsto, horizonte 30/60/90 dias e conta; Entrada/Saída explícita em vez de exigir valor negativo; cadastro de contas em ação secundária |
| Conciliação | Comparação lado a lado de extrato e lançamentos, com datas, descrições e valores; explicar sugestões antes de confirmar, em vez de mostrar somente Opção 1/2 e totais |
| Sincronização Mobne | Migrar para Integrações, simplificar atualização, corrigir progresso e manter diagnóstico avançado acessível |
| Configurações | Concluir Integrações; distinguir configurações prontas das planejadas; controles por permissão e recuperação de acesso orientada ao usuário |

Login/recuperação: a tela “Esqueci minha senha” pede senha mestra e descreve arquivo/variável de servidor. É um fluxo administrativo apresentado como recuperação comum. Oferecer instrução adequada ao usuário, como contatar o administrador, e avaliar recuperação por link caso haja infraestrutura. Manter recuperação administrativa claramente separada.

## Ordem sugerida e critérios de aceite

1. **Confiabilidade:** corrigir interpretação de simulações, preservar formulários, descartar respostas antigas e tratar saldo de pagamentos. Aceite: nenhum campo desaparece após atualização, rota e conteúdo permanecem consistentes, parcial usa saldo correto.
2. **Navegação:** cinco grupos, renomeações, Sincronização dentro de Integrações, simulador contextual e filtros pertinentes à tarefa. Aceite: links antigos funcionam e cada filtro altera somente os dados que promete.
3. **Operação:** padronizar tabelas/formulários, mensagens e estados vazios; filtros de pendências e detalhamento. Aceite: tarefas principais completas por teclado, feedback de envio e ausência de duplicidade por cliques repetidos.
4. **Escala:** medir e otimizar consultas, payloads e listas. Aceite: comparar latência e interação antes/depois com o mesmo volume e ambiente.

Validar a arquitetura com tarefas reais: localizar produtos com problema de margem, explicar uma queda de vendas, registrar pagamento parcial, identificar risco de caixa e investigar dados desatualizados. Medir tempo, erros e ajuda necessária. Não há métricas de uso ou entrevistas nesta análise; prioridades de frequência devem ser confirmadas com os usuários.

## Complemento — formulários e manutenção de cadastros

Solicitação do usuário: permitir corrigir e remover custos e contas a pagar e melhorar os demais formulários. Esta seção especifica recomendações; não implementa alterações.

### Um lançamento, duas formas de consultar

Custos e Despesas e Contas a Pagar já consultam os mesmos lançamentos financeiros. Manter essa unidade: editar uma despesa deve atualizar sua conta a pagar e o resultado gerencial, sem criar um cadastro paralelo. A primeira tela organiza competência e classificação; a segunda organiza vencimentos e saldos.

### Ações conforme o estado do lançamento

| Estado | Ações propostas | Regra |
|---|---|---|
| Rascunho, se implementado | Editar, duplicar, excluir | Não participa dos totais; exclusão recuperável |
| Em aberto, manual, sem vínculos | Editar, duplicar, cancelar | Alterações de valor, categoria, competência e vencimento registradas no histórico |
| Parcialmente pago | Ver pagamentos, registrar saldo, editar campos permitidos | Não apagar pagamentos nem reduzir total abaixo do já pago; correções financeiras por fluxo explícito |
| Pago | Ver detalhes, corrigir dados descritivos, corrigir pagamento | Correção de pagamento precisa de operação própria; o endpoint atual reverte o lançamento inteiro |
| Conciliado ou importado | Ver origem e vínculos, solicitar correção | Explicar dependências; alterações devem preservar conciliação e identidade da importação |
| Cancelado/estornado | Consultar histórico, duplicar | Fora da lista padrão de pendências; visível em filtro específico |

Para o usuário remover um custo lançado por engano, oferecer **Cancelar lançamento**, com descrição e valor na confirmação e explicação do efeito nos totais. Reservar “Excluir” para rascunhos ou registros sem histórico, com recuperação. A proposta mantém a capacidade de remover da operação sem perder rastreabilidade de pagamentos. Definir permissões e regras de competência encerrada antes de implementar.

**Lacuna técnica confirmada:** a interface de despesas não oferece edição/remoção e as rotas de lançamentos examinadas não têm atualização ou exclusão. Existe `/entries/{id}/reverse`, que marca todo o lançamento como revertido; isso não equivale a editar uma despesa nem a desfazer um pagamento específico. Novas ações exigem backend, validação de vínculos, histórico e atualização dos relatórios, além dos botões.

### Custos e despesas

Lista primeiro, botão **Nova despesa**, ação **Editar** visível em cada linha e menu secundário **Duplicar / Cancelar / Histórico**. Abrir criação e edição no mesmo painel lateral; em tela pequena, usar formulário de página inteira.

Campos iniciais: descrição, valor em reais, categoria de despesa, competência e vencimento. Mostrar a competência explicitamente no formulário, em vez de depender só do filtro global. “Conta” é ambíguo: distinguir **Categoria da despesa** de **Conta de pagamento**.

Campos adicionais recolhíveis: fornecedor/favorecido, loja, centro de custo quando houver suporte, observações e documento/comprovante quando houver armazenamento. Não tornar todos obrigatórios.

Permitir despesa única, recorrente ou parcelada. Exibir prévia de vencimentos, número de parcelas e valor total antes de salvar. Na recorrência, distinguir alterar só esta ocorrência ou as futuras, sem modificar pagamentos passados. Há criação/geração de recorrência no backend, mas a interface e a manutenção da série ainda precisam ser desenvolvidas.

Ações finais: **Salvar**, **Salvar e adicionar outra**, **Cancelar**. Moeda com formato brasileiro; erros junto aos campos; proteger contra envio duplicado e atualização automática; avisar ao sair com alterações. Mostrar conflitos de edição sem descartar o que o usuário digitou.

### Contas a pagar

Filtros: Vencidas, Hoje, Próximos 7 dias, A vencer, Pagas e Canceladas, com busca por descrição/favorecido. Exibir totais, valor original, já pago e saldo. Manter competência como filtro adicional, sem esconder dívidas anteriores por padrão.

Painel de pagamento: favorecido, saldo, valor pago editável, data e, quando integrado ao caixa, conta de origem. Juros, multa e desconto devem ter campos próprios e suporte no cálculo. Explicar se o registro também cria movimento de caixa; impedir duplicação quando um extrato importado chegar depois.

Após registrar, confirmar o valor e o saldo restante. Disponibilizar histórico de pagamentos e fluxo específico para corrigir o pagamento errado. Operações em lote podem vir depois, com prévia de itens e total.

### Outros formulários

| Formulário | Melhorias propostas |
|---|---|
| Categorias de custos/despesas | Criar, editar nome, arquivar e listar arquivadas; explicar categoria em uso; diferenciar categoria, centro de custo e conta bancária. O backend já arquiva contas financeiras, mas isso não significa suporte completo à edição |
| Empréstimos | Prévia editável das parcelas, totais de principal/juros, resumo do contrato e próximas parcelas; renegociação guiada e histórico. Existe endpoint de renegociação sem ação correspondente no formulário atual |
| Movimentação de caixa | Escolha Entrada/Saída com valor positivo; data e conta obrigatórias; histórico e correção de movimentos conforme vínculos; transferência entre contas como operação única, se implementada |
| Contas bancárias/caixa | Editar nome e arquivar sem perder movimentos; saldo inicial com data e origem; bloquear ações incompatíveis com vínculos e explicar o motivo |
| Conciliação | Mostrar os itens completos antes da confirmação; busca em vez de select extenso; divergência explícita; motivo do desfazimento com rótulo; manter contexto após confirmar |
| Empresa e lojas | Máscaras e validação de documento/telefone; seleção de estado; opção de editar e inativar lojas; preservar dados da empresa ainda não salvos ao cadastrar uma loja |
| Usuários | Editar perfil de acesso, revogar vínculo e explicar permissões; ações coerentes com limites do administrador. O backend já tem alteração de perfil e remoção de vínculo, ausentes na tabela atual |
| Calendário | Editar/remover exceção, datas em formato brasileiro, alertar sobre data já cadastrada e permitir seleção de loja quando suportada; remover coluna técnica de versão da tela principal |
| Fornecedores/favorecidos | Busca com cadastro rápido sem perder a despesa; evitar duplicidades; edição e arquivamento com histórico preservado |

### Critérios para validar a implementação futura

1. Cadastrar despesa e encontrá-la em Contas a Pagar sem duplicação.
2. Editar valor/competência e conferir reflexos no resultado e nos filtros.
3. Cancelar lançamento aberto e confirmar retirada dos totais aplicáveis, preservando histórico.
4. Registrar pagamento parcial, conferir saldo e quitar apenas o restante.
5. Corrigir um pagamento sem apagar o lançamento inteiro ou duplicar caixa.
6. Impedir alterações incoerentes em registros conciliados e explicar a correção possível.
7. Manter rascunho durante atualização automática, falha de rede e conflito entre usuários.
8. Validar criação/edição por teclado e em tela pequena, com rótulos e feedback acessíveis.

## Complemento — Minha conta e alteração de senha

Lacuna relatada pelo usuário e confirmada no código: não existe interface de edição do próprio nome/e-mail ou troca de senha autenticada. O rodapé mostra a identificação e Sair. As rotas atuais oferecem criação de usuários, alteração de perfil de acesso e desativação; a recuperação de senha usa senha mestra, inadequada como fluxo cotidiano de troca de senha.

Proposta: tornar nome/avatar no rodapé um menu com **Minha conta**, **Alterar senha** e **Sair**, disponível para todos os usuários autenticados, inclusive o administrador. Manter a gestão dos demais usuários em Configurações → Usuários e permissões.

- **Dados pessoais:** editar nome de exibição e e-mail de acesso, com unicidade do e-mail e confirmação de identidade para mudança do acesso. Se houver serviço de e-mail, verificar o novo endereço antes de efetivar a troca. Mostrar perfil/permissões como informação, sem permitir autoelevação de privilégios.
- **Alterar senha:** senha atual, nova senha e confirmação; opção mostrar/ocultar, regras visíveis, suporte a preenchimento por gerenciadores e erros junto aos campos. A troca autenticada não pede senha mestra.
- **Conclusão:** mensagem clara de sucesso e do tratamento das sessões; invalidar as outras sessões e renovar a atual após a alteração, conforme política implementada. Nunca apresentar troca malsucedida como concluída.
- **Backend:** operações do próprio usuário resolvem identidade pela sessão; verificar senha atual para troca de credencial, aplicar validação e registrar o evento sem armazenar senhas em logs. Não reutilizar a recuperação com senha mestra como atalho.
- **Validação:** nome atualizado no menu; e-mail duplicado tratado sem perda do formulário; senha atual incorreta não altera nada; confirmação divergente não envia; nova senha funciona no próximo login e a antiga deixa de funcionar; permissões permanecem iguais.

Prioridade alta, junto da manutenção de cadastros. Esta é uma proposta de implementação, não uma alteração de credenciais ou do sistema.

## Complemento — empréstimos integrados às contas a pagar

Direção proposta: retirar Empréstimos como destino independente do menu e integrar suas parcelas à rotina de **Financeiro → Contas a pagar**, mantendo a ficha de contrato acessível por filtro e pelo detalhe da parcela. Preservar a rota antiga como atalho compatível.

Ter parcelas e data final não define uma despesa como variável. Fixo/variável descreve relação com volume de atividade; única/recorrente/parcelada descreve a programação de pagamentos; empréstimo descreve a natureza da obrigação. Referência: [Sebrae — despesas fixas e variáveis](https://meuatendimento.sebrae.com.br/sites/PortalSebrae/ufs/pe/artigos/o-que-sao-despesas-fixas-e-variaveis%2Cf116c0e0119c7810VgnVCM1000001b00320aRCRD).

O plano de contas atual já distingue recebimento do empréstimo, amortização do principal e juros. Preservar essa distinção: principal reduz dívida, juros alimentam despesas financeiras conforme competência e tratamento aplicável, e o total pago sai do caixa. O dinheiro recebido do empréstimo não é faturamento. Ver [CPC 03 — fluxos de caixa](https://www.cpc.org.br/CPC/Documentos-emitidos/Pronunciamentos/Pronunciamento?Id=34).

### Cadastro progressivo

Uma ação **Novo compromisso** permite escolher Despesa ou Empréstimo/financiamento. Para despesas, escolher pagamento único, recorrente ou parcelado. Para empréstimos, abrir os campos de contrato: credor, valor contratado, valor líquido recebido, data do crédito, número de parcelas, primeiro vencimento e composição das parcelas. Não apresentar natureza e frequência como alternativas mutuamente exclusivas.

Exibir cronograma antes de salvar, com principal, juros, encargos quando suportados e total por vencimento. Permitir revisar cronograma contratado sem forçar todas as operações ao modelo atual de principal igual e juros iguais por parcela. Calcular data final a partir do cronograma e recalculá-la em renegociações.

### Rotina e acompanhamento

- Lista de contas a pagar mostra parcela 4/12, credor, vencimento, total, saldo e tipo Empréstimo. A ação abre o contrato e permite registrar pagamento pela mesma experiência das outras obrigações.
- Contrato mostra principal em aberto, total das parcelas futuras, juros previstos, parcelas restantes e previsão de término. Não confundir principal em aberto com soma de pagamentos futuros.
- Filtros: Todas, Despesas, Empréstimos e Parceladas; filtros podem se combinar, pois um empréstimo também é parcelado.
- Ações: editar dados permitidos, renegociar parcelas futuras, registrar quitação antecipada com prévia de desconto/encargos, consultar histórico. Não recalcular parcelas pagas silenciosamente.
- Resultado gerencial recebe apenas os componentes apropriados; fluxo de caixa recebe o desembolso total, sem duplicar os juros. Competência dos juros deve ser explícita: hoje o pagamento cria a despesa no mês do pagamento.

### Integração necessária

Atualmente `create_loan` cria contrato e cronograma próprios; `pay_installment` gera um lançamento financeiro para os juros na hora do pagamento. Contas a Pagar consulta lançamentos financeiros gerais, portanto não lista automaticamente o cronograma de principal e juros em aberto. Unificar a consulta e a experiência mantendo a origem de cada obrigação, com identificadores de vínculo e processamento único do pagamento. Não copiar uma parcela para despesas genéricas e depois contabilizar novamente os juros gerados pelo módulo atual.

Aceite: um contrato com 12 parcelas aparece uma vez como contrato e com 12 obrigações na agenda; uma parcela quitada desaparece das pendências, reduz a dívida pelo principal pago, registra os componentes aplicáveis uma única vez e afeta o caixa pelo total; renegociação mantém histórico e substitui somente obrigações futuras. Esta proposta altera a navegação recomendada, não a aplicação nesta etapa.

## Direção consolidada — propósito de Análises e Financeiro

**Análises:** compreender vendas, margem dos produtos, composição do mix, estoque e oportunidades. Priorizar leitura, comparação, investigação e decisões comerciais. **Financeiro:** acompanhar resultado após despesas, obrigações, pagamentos, dívida e caixa; priorizar execução e rastreabilidade. Configurações administra o sistema. É uma única aplicação com dados compartilhados, sem seleção de loja.

| Tela analítica | Pergunta central | Limite do escopo |
|---|---|---|
| Resumo executivo | Como está o desempenho comercial da loja? | Faturamento, margem bruta, cupons, ticket e prioridades; resultado gerencial pode aparecer como referência secundária com link e estado de apuração, vindo do Financeiro |
| Preços e margens | Quais produtos precisam de revisão de preço? | Custo/preço dos produtos, margem e oportunidades; não despesas administrativas |
| Mapa de produtos | Quais produtos sustentam vendas e lucro bruto? | Curva ABC, giro e contribuição por produto; conecta gráficos à lista filtrada |
| Desempenho de vendas | Por que o faturamento mudou? | Decomposição por cupons, ticket, categorias e dias; explica causas e comparações |
| Sazonalidade e tendências | Quais padrões se repetem no histórico? | Histórico, cobertura, meses comparáveis e comportamento recorrente |
| Projeções de vendas | Quanto podemos vender e como preparar compras? | Cenários comerciais futuros e premissas; saldo futuro e pagamentos ficam no Fluxo de caixa |
| Produtos e estoque | Quais itens precisam de atenção agora? | Consulta detalhada e filtros de ruptura/preço/custo; não prometer movimentação de estoque onde só há consulta |

O Resumo é a síntese e porta de entrada; Diagnóstico explica as causas; Sazonalidade descreve padrões históricos; Projeções testa o futuro. Mapa organiza contribuição e giro; Preços prioriza margem; Produtos e estoque permite investigar itens. Cada tela deve abrir com uma frase de propósito, poucos indicadores e uma próxima ação contextual.

No Financeiro, a entrada é **Resultado gerencial** com custos/despesas por competência e estado de apuração. Contas a pagar concentra obrigações, inclusive empréstimos; Custos e despesas mantém os lançamentos; Fluxo de caixa projeta disponibilidade; Conciliação confere registros e extratos. Evitar criar mais um resumo financeiro redundante.

### Conexões e filtros

- Análises usa período de vendas e comparação; custo e estoque atuais mantêm indicação própria da data. Financeiro usa competência, vencimento ou horizonte conforme a tarefa, com filtros independentes dos meses sincronizados.
- Não duplicar cálculo de resultado: eventual cartão no Resumo consulta a mesma fonte do Financeiro, respeita permissão e informa apuração pendente. Remover Resultado simulado legado; não converter custos operacionais em novo indicador comercial.
- Projeção de vendas pode alimentar cenário de caixa mediante premissas explícitas de recebimento, prazos e despesas. Faturamento projetado não deve virar automaticamente caixa recebido.
- Mostrar módulo e título no cabeçalho. Mesma identidade amarela, tipografia e componentes nos dois módulos; a distinção vem dos nomes, conteúdo e ações, não apenas de cores.
- No mobile, seletor textual de módulo dentro do menu, com estado ativo acessível e Configurações/Minha conta fáceis de localizar. Não adicionar outra barra de navegação concorrente.

Critérios de aceite da arquitetura: acesso direto abre módulo correto; trocar módulo preserva contexto anterior; não há filtros financeiros que aparentem filtrar vendas ou vice-versa; configurações e conta pessoal permanecem acessíveis; atalhos entre módulos levam à tela e ao contexto descritos. Testar se os usuários encontram revisão de preços em Análises e registro de pagamento em Financeiro sem ajuda.

## Revisão crítica consolidada — evidências e prioridades

Revisão usando using-superpowers e systematic-debugging. Foram executadas três reproduções com funções reais do backend e dados sintéticos em SQLite temporário, removido ao final. Nenhum banco real foi alterado. Resultados confirmam problemas na lógica compartilhada; não houve reprodução específica em PostgreSQL ou nova auditoria visual de produção.

### Problemas novos reproduzidos

| Prioridade | Cenário sintético | Esperado | Observado | Causa |
|---|---|---|---|---|
| P1 | Despesa de R$ 1.000 com R$ 400 pagos | R$ 600 pendentes na previsão | R$ 0 previstos | `forecast.py:_open_entries_due` seleciona apenas open/overdue, excluindo partially_paid; precisa usar saldo restante, não só adicionar o status |
| P1 | Arquivar categoria com despesa de R$ 1.000 | Histórico mantém R$ 1.000 | Despesas do relatório passam a R$ 0 | `reporting.py:management_result` constrói linhas percorrendo apenas contas ativas, embora a consulta de lançamentos encontre o histórico |
| P1 | Renegociar parcela antiga de R$ 1.100 para nova de R$ 1.050 | Previsão de R$ 1.050 | Previsão de R$ 2.150 | `forecast.py:_open_installments_due` inclui parcelas abertas de cronogramas encerrados; não restringe ao cronograma vigente |

Recomendações: projeção baseada no saldo pendente; relatórios que preservam categorias históricas arquivadas; seleção do cronograma vigente com histórico pago preservado. Transformar as reproduções em testes de regressão antes da implementação. Os defeitos ainda não foram corrigidos.

### Avaliação das decisões discutidas

- Manter Análises e Financeiro como módulos de navegação da mesma aplicação. Não criar aplicações/bancos separados: isso aumentaria divergências e sincronizações internas.
- Central de custos como fonte do resultado é a direção adequada, mas o relatório atual tem o defeito de categorias arquivadas e não representa completude de apuração. Corrigir e validar antes de propagar o número para o Resumo.
- Integrar empréstimos às contas a pagar melhora a rotina, preservando o contrato e a separação principal/juros. Não transformar parcelas em despesas genéricas duplicadas.
- Uma loja pede interface simples. Ocultar seleção de unidade e renomear Dados da empresa; não há necessidade de apagar a estrutura interna de identificação da loja nesta etapa.
- Minha conta é uma lacuna delimitada que pode ser entregue sem depender da reorganização completa do financeiro.
- O escopo acumulou muitas sugestões. Busca global, anexos, quitação antecipada avançada, operações em lote, orçamento e centros de custo ficam depois do fluxo básico completo.

### Regras ainda necessárias para implementação

1. **Estados e ações:** estabelecer editar/cancelar/estornar conforme vínculo e pagamento; diferenciar desfazer pagamento de reverter lançamento inteiro. Não permitir que exclusão/arquivamento altere silenciosamente o histórico.
2. **Pagamento e caixa:** definir se registrar pagamento gera caixa ou aguarda conciliação; ligar comprovante, parcela, lançamento e movimento de forma que reenvio/importação não duplique valores. Persistir cada operação completa de forma atômica, com proteção a reenvio e edição concorrente.
3. **Qualidade dos dados:** distinguir zero confirmado, ausência de fonte, valor previsto e competência em apuração. Não impor selo de resultado confiável apenas porque existe um número.
4. **Projeção:** separar cenário de vendas de calendário de recebimentos e pagamentos. Sem recebíveis/custos completos, nomear como projeção dos movimentos cadastrados e mostrar cobertura.
5. **Migração:** preservar IDs e vínculos existentes, links antigos, pagamentos e cronogramas encerrados. Validar totais antes/depois, sem recadastrar empréstimos como novas despesas.

### Ordem recomendada, substituindo a lista inicial de fases

1. Corrigir os três defeitos reproduzidos e os riscos já identificados de atualização de formulário, respostas obsoletas e pagamento parcial. Validar reconciliação entre pendências, resultado e caixa com cenários pequenos conhecidos.
2. Completar o ciclo de lançamento: criar, editar, cancelar, pagar parcialmente, quitar e corrigir pagamento, com histórico. Entregar Minha conta e remover seletores desnecessários de loja.
3. Reorganizar Análises/Financeiro, mover Sincronização, integrar parcelas à agenda e substituir Resultado simulado pela fonte gerencial validada. Preservar URLs e filtros contextuais.
4. Melhorar tabelas, acessibilidade, busca local, estados vazios e desempenho conforme medição. Criar fechamento mensal com revisão explícita e reabertura registrada quando o ciclo básico estiver consistente.
5. Acrescentar orçamento, anexos, busca global e automações avançadas conforme uso real.

Condição de sucesso: o usuário consegue cadastrar uma obrigação, corrigir um erro, pagar parte, verificar o restante no caixa, quitar e consultar o histórico sem valores divergentes. Métricas de desempenho ainda não foram coletadas; gargalos de consulta descritos anteriormente permanecem hipóteses de escala, não lentidão quantificada.
