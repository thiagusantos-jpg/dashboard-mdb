# Plano de implementação — dashboard integrado ao Mobne

Atualizado em 11/09/2026 após aprovação da análise técnica e solicitação de operação sem importação Excel.

## Objetivo e escopo

O dashboard deve obter os dados operacionais diretamente do ERP Mobne, em consultas de leitura executadas pelo backend. A carga histórica, as atualizações e os recálculos não podem depender de arquivos Excel, de execução manual de scripts ou do navegador de um usuário específico.

Este plano substitui as etapas centradas em upload do planejamento anterior. Os arquivos existentes ficam preservados como referência de reconciliação; a importação Excel não é requisito do novo fluxo. A interface atual será reaproveitada conforme a análise aprovada, com reestruturação do núcleo de dados.

**Estado atual:** documentação pública e especificação OpenAPI consultadas. A credencial fornecida não foi inserida em arquivos nem usada em chamadas autenticadas nesta atualização de plano. Permissões, empresas acessíveis, formato dos retornos e cobertura histórica ainda precisam ser validados na primeira etapa de implementação. Não há integração nova implementada ou agendamento ativado nesta etapa.

## Arquitetura proposta

`Mobne → serviço de sincronização → validação e armazenamento persistente → indicadores → API autenticada → dashboard`

- Manter Python/FastAPI como backend e separar completamente módulos de interface Streamlit.
- Usar banco relacional persistente, com transações, migrações e isolamento por empresa. Escolha e provisionamento da hospedagem serão fechados ao iniciar a implementação, considerando o ambiente de produção disponível.
- Rodar a sincronização em job independente das consultas do dashboard. O frontend consulta o banco através da API e não chama o ERP diretamente.
- Manter dados brutos necessários à rastreabilidade em área privada, com retenção definida; modelos normalizados; agregados diários/mensais versionados; histórico das execuções e seus erros.
- Utilizar apenas os campos necessários. Cadastro completo de clientes não é pré-requisito para contar cupons; identificador de cliente só será necessário para métricas de clientes únicos.

## Mapeamento dos dados necessários

As rotas e restrições abaixo foram conferidas na [especificação oficial Mobne](https://apiexternal.mobne.com.br/swagger/v1/swagger.json), disponibilizada na [documentação](https://apiexternal.mobne.com.br/). O mapeamento dos campos das respostas ainda depende de amostras autenticadas: vários retornos 200 não têm esquema detalhado na documentação.

| Necessidade | Consulta GET documentada | Aplicação no projeto |
|---|---|---|
| Empresas | `/api/v1/Empresa/consulta-cadastro-empresa` | Descobrir empresas acessíveis e confirmar o ID interno; não presumir que o ID 218 do código seja a empresa correta. |
| Vendas de caixa | `/api/v1/Cupom/consulta` | Fonte candidata principal para vendas PDV, documentos, itens e cancelamentos. Validar conteúdo real do retorno. |
| Reconciliação analítica | `/api/v1/AnaliseVenda/AnaliseVendas` e `/api/v1/AnaliseVenda/AnaliseVendasDocumentos` | Conferir totais e identificar se oferecem receita/custo/lucro adequados aos indicadores. |
| Total diário | `/api/v1/AnaliseVenda/AnaliseVendasPorDiaOperadorEmpresa` | Conferência por empresa e dia. |
| Produtos e categorias | `/api/v1/Produto/consulta-cadastro-produto` e `/api/v1/Produto/consulta-cadastro-categoria` | Dimensão de produtos, agrupamentos e curva ABC calculada por período. |
| Preços | `/api/v1/Produto/consulta-preco` | Comparação de preços e histórico das mudanças observadas. |
| Estoque e custos | `/api/v1/Produto/consulta-estoque-produto` e `/api/v1/Produto/consulta-movimentacao-estoque` | Estoque atual, evolução e dados de custo; validar o significado e a competência de cada campo. |
| Notas e pedidos | `/api/v1/Nota/consulta` e `/api/v1/PedidoVenda/consulta-pedido-venda` | Complementar canais não cobertos por cupons, somente após mapear os vínculos entre documentos para não duplicar vendas. |
| Despesas | `/api/v1/Financeiro/consulta-titulo` e `/api/v1/Financeiro/consulta-conta-gerencial` | Avaliar a cobertura de despesas operacionais para o resultado líquido, com classificação e regime definidos. |

**Mudança relevante em relação ao código atual:** pedidos de venda não serão presumidos como todas as vendas do mercado. A documentação identifica a consulta de cupons como venda PDV. A diferença atual de faturamento pode ter relação com cobertura de canais, mas isso é hipótese a validar, não diagnóstico confirmado.

## Restrições documentadas que entram no desenho

- Autenticação por header `Authorization: ApiKey <segredo>`. Configurar o token como `MOBNE_API_KEY` apenas no servidor, fora do repositório, assets, URLs e logs. Não reproduzir seu valor em exemplos ou relatórios.
- Usar o filtro `Filter.EmpresaId` onde documentado. Não confiar somente no header `empresaId` empregado no cliente antigo. Distinguir `EmpresaId` interno de `NroEmpresa`.
- Cupons: `PageSize` máximo 100; filtros obrigatórios de empresa e datas de movimento; intervalo documentado de até 60 dias. Tratar status válido/cancelado e espécie fiscal/não fiscal conforme a cobertura do mercado.
- Análises de vendas e por documento: intervalos máximos de 31 dias. Dividir a carga histórica em janelas menores ou iguais ao limite e paginar cada janela.
- Análise diária: exige `Filter.EmpresaId` e `Filter.Data`. O helper atual envia `DataInicio` e `DataFim`, parâmetros que não correspondem a esse contrato, e deve ser corrigido na implementação.
- Produtos, preços, estoque e empresas possuem filtros de ponteiro de exportação em consultas específicas. Preços também oferecem filtro de alteração por data. Utilizar esses mecanismos somente após validar os campos de resposta e a semântica inclusiva do ponteiro.
- Não foi identificado nesta leitura um limite global confiável de requisições por segundo. Começar com concorrência conservadora, respeitar `429`/`Retry-After`, limitar retentativas e medir antes de ampliar a frequência.

## Etapas de entrega

### 1. Validar acesso e fechar o contrato real dos dados

1. Configurar o segredo no ambiente do backend sem expô-lo ao cliente ou aos logs.
2. Realizar consultas pequenas, somente leitura, para identificar empresas e testar acesso aos recursos necessários.
3. Buscar um dia representativo de vendas e um período histórico; inspecionar tipos, paginação, IDs, itens, descontos, custos, status e vínculo entre documentos.
4. Confrontar cupons, análises e relatórios do ERP com os mesmos filtros. Determinar se cupons são suficientes ou se notas de outros canais precisam ser incorporadas.
5. Registrar matriz de cobertura por indicador: disponível, derivável, sem permissão ou não fornecido pela API. Guardar fixtures sanitizadas para testes.

**Aceite:** empresa confirmada, endpoints necessários acessíveis, contratos de resposta mapeados e origem de cada indicador definida. Uma resposta HTTP 200 isolada não significa integração concluída.

### 2. Construir armazenamento e carga histórica

- Modelar empresa, produto, categoria, documento, item, preço/custo/estoque observado, execução de sincronização e agregados.
- Chaves únicas por empresa e identificador de origem; manter tipo do documento. Vincular pedido, nota e cupom quando representarem a mesma operação.
- Usar valores monetários decimais, datas de movimento e fuso explicitamente definidos. Preservar quantidade e custo unitário/total sem conversões implícitas.
- Carga inicial proposta: janeiro de 2025 até a data atual, para cobrir o comparativo existente; verificar antes a disponibilidade e o volume. Exibir qualquer lacuna histórica encontrada.
- Percorrer todas as páginas e persistir checkpoints. Erro em uma página impede promover aquele lote como completo.
- Publicar agregados de uma versão consistente após validação; manter a última versão válida durante a carga.

**Aceite:** um mês completo reconciliado sem Excel; repetição da carga não duplica documentos; interrupção e retomada preservam consistência; dados persistem após reinício/deploy.

### 3. Automatizar atualização incremental

- Frequência inicial proposta: uma execução por hora, configurável após medir volume e limites. Esse valor é uma decisão de projeto, não uma capacidade já comprovada do ERP.
- Usar ponteiros de alteração onde suportados; reconsultar uma janela recente para vendas e cancelamentos que não disponham de cursor de modificação. Definir a janela com base no comportamento observado e executar reconciliação periódica de períodos anteriores.
- Atualizar documentos cancelados/corrigidos, em vez de apenas acrescentar registros novos. Não misturar ausência legítima de vendas, falha de autenticação e falha de comunicação.
- Evitar execuções simultâneas conflitantes por empresa; usar retentativas limitadas, espera progressiva e timeouts.
- Disponibilizar botão autenticado “Sincronizar agora”, status por entidade, cobertura de datas, última execução bem-sucedida e mensagem de falha acionável.
- O agendador fará parte da infraestrutura do projeto; a atualização deste plano não cria uma automação no Codex.

**Aceite:** uma venda nova e seu eventual cancelamento aparecem corretamente no dashboard; indisponibilidade do ERP preserva os últimos números válidos, com aviso de desatualização.

### 4. Recalcular indicadores e conectar as telas

- Receita líquida, descontos/devoluções, documentos distintos, ticket médio, vendas por produto/categoria/dia e comparação anual obtidos de uma fonte reconciliada.
- Lucro bruto e margem usam custo da operação/período quando disponível. Custo atual de cadastro não substitui automaticamente custo histórico.
- Curva ABC, giro e alertas de ruptura calculados para o período correto. Estoque atual não deve ser apresentado como estoque histórico; iniciar snapshots quando a API não fornecer histórico recuperável.
- Para resultado líquido, confirmar despesas efetivas e sua classificação. Se o ERP não registrar ou não expuser os custos fixos necessários, oferecer configuração explícita dentro do dashboard, sem Excel, e rotular o resultado como simulação até existir cobertura adequada. Meta não vira custo nem lucro realizado.
- Substituir consumo de JSON público/localStorage pela API autenticada. Armazenamento local fica restrito a preferências e cache identificado por versão.
- Exibir período, origem, atualização e cobertura dos dados. Indicadores sem insumo suficiente ficam indisponíveis, sem usar margem fictícia ou zeros que aparentem resultado real.

**Aceite:** resumo mensal, diário e por produto reconciliados; todos os filtros coerentes; dois usuários autorizados visualizam a mesma versão dos dados; todas as telas essenciais funcionam sem upload de arquivos.

### 5. Operação e publicação

- Separar ambientes e segredos; autenticação e autorização por empresa; armazenamento privado; migrações e backup.
- Testes automatizados de contratos, cálculos, paginação, deduplicação, cancelamentos, retomada e permissões; smoke test de instalação limpa e deployment.
- Medir duração e taxa de erro da sincronização, atraso dos dados e tempo de resposta das consultas do painel.
- A migração será gradual: validar o fluxo novo antes de desativar o antigo, com possibilidade de voltar à última versão estável dos agregados.

## Testes obrigatórios

1. Mais de 100 cupons: todas as páginas entram no total.
2. Falha na segunda página: lote não é publicado como completo.
3. Um cupom com várias categorias: conta uma compra, sem multiplicar o ticket.
4. Reexecução e sobreposição de janelas: nenhum documento ou item duplicado.
5. Cupom cancelado e venda devolvida: tratamento compatível com o relatório de referência do ERP.
6. Pedido faturado em nota/cupom: receita contabilizada uma única vez.
7. Datas em viradas de mês e ano: período correto, inclusive no fuso de Brasília.
8. Custos ausentes: lucro não é estimado silenciosamente.
9. Mudança de preço/custo: atualização atual não altera resultado histórico indevidamente.
10. Falta de permissão, timeout e `429`: erro visível, retentativa controlada e preservação da base válida.
11. Acesso entre empresas: isolamento garantido.
12. Dashboard completo após reinício em outro navegador, sem Excel e sem depender de `localStorage`.

## Primeira entrega concreta

**Um mês completo de vendas PDV vindo diretamente do Mobne, persistido, reconciliado e apresentado no resumo executivo**, com paginação completa, contagem correta de cupons, segurança e status da sincronização. Depois, ampliar histórico e integrar produtos, preços, estoque e os demais relatórios conforme a matriz de cobertura.

O token disponível viabiliza iniciar a validação autenticada na implementação; a existência da documentação não garante, sozinha, que a credencial tenha acesso a todos os recursos ou a custos históricos. Eventuais lacunas devem ser registradas por indicador e tratadas diretamente na integração/configuração, sem transformar Excel em etapa obrigatória.
