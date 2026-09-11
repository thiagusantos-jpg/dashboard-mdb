# Análise técnica — Mercado duBairro

Data: 11/09/2026. Resultado: **análise concluída com problemas relevantes encontrados**. Nenhum código da aplicação ou dado de negócio foi alterado.

## Parecer

Recomendo **reescrever gradualmente o núcleo de importação, sincronização, persistência e cálculo dos indicadores**, preservando a interface e os componentes de processamento que passarem por testes de reconciliação. A base atual serve como protótipo e especificação visual, mas os indicadores não têm consistência suficiente para orientar decisões sem conferência com o ERP.

Não há evidência de que seja necessário trocar Python/FastAPI, adotar microserviços ou reescrever toda a interface em React. A prioridade é estabelecer uma fonte confiável de dados e uma única implementação das regras de negócio.

## Escopo e limites

- Leitura da estrutura, dos módulos Python, dos fluxos do JavaScript principal, das configurações de publicação, dos documentos Markdown e dos JSONs consumidos pelo dashboard; comparação das cópias existentes.
- A pasta não contém um repositório Git utilizável. Não foi possível avaliar histórico de alterações, branches, CI remoto ou o que está efetivamente publicado.
- Validação sintática dos arquivos Python e dos dois JavaScripts principais.
- Reproduções isoladas com Node.js e pandas, usando dados sintéticos. No teste do processador Python, apenas a dependência de interface Streamlit foi substituída por um objeto mínimo; as transformações pandas foram executadas de verdade. Funções puras do sincronizador foram extraídas por AST para evitar carregar credenciais ou acessar o ERP.
- Não foi executada a aplicação completa: faltam dependências de servidor no ambiente disponível, e há uma dependência ausente no próprio manifesto do projeto. Não foram realizados testes de carga, navegação visual, publicação ou consultas ao Mobne.
- Planilhas e documentos binários não foram integralmente auditados. Os números abaixo vêm dos JSONs locais, não de uma reconciliação independente com o ERP. Não foi verificada a validade de credenciais nem a existência de proteção externa na hospedagem.

## Arquitetura encontrada

O frontend publicado é indicado por `vercel.json`, com `outputDirectory: public`. Ele usa HTML, CSS, JavaScript, Plotly e SheetJS. Carrega seis arquivos JSON na inicialização e processa importações também no navegador, guardando resultados em `localStorage`.

O backend é FastAPI: `app.py` importa `api.py`. Há módulos de processamento pandas e integração Mobne, misturados com código de uma interface Streamlit anterior. A persistência usa arquivos Excel/JSON. Não encontrei banco implementado, migrações, autenticação da API ou agendador efetivamente configurado. APScheduler aparece como dependência, mas não há job registrado no código examinado.

Existem versões divergentes na raiz, em `public/` e em `projeto_dubairro-claude-deploy-vercel-k9YvI/`, além do ZIP. O PRD descreve React, banco e autenticação que não correspondem à implementação atual; deve ser tratado como plano, não como documentação do sistema entregue.

## Achados prioritários

P1 = corrigir antes de confiar nos números ou habilitar uso compartilhado. P2 = corrigir em seguida. Confiança indica a força da evidência local, não uma medição do impacto em produção.

### 1. [P1] Fontes atuais produzem números incompatíveis — confiança 10/10

Em janeiro de 2026, [public/data/vendas_mensais.json](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/data/vendas_mensais.json:18) contém `"Vlr_Venda": 217.61`, em uma única categoria. Já [public/data/vendas_diarias.json](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/data/vendas_diarias.json:1) soma **R$ 72.689,38** para janeiro, valor também encontrado na receita dos 1.252 produtos. A cópia [data/vendas_mensais.json](/Users/thiagosantos/Documents/DASHBOARD_MDB/data/vendas_mensais.json:1) soma **R$ 73.244,04** para janeiro, em 24 categorias.

O frontend reúne essas fontes sem verificar consistência: `files.forEach((f, i) => DATA[f] = results[i]);`, em [public/js/app.js:303](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:303). Assim, resumo, gráficos diários e ranking podem representar bases diferentes. Não é possível concluir qual total é o correto sem reconciliação.

**Correção:** identificar a origem de cada conjunto, reconciliar por empresa/período/documento e publicar todos os agregados como uma versão única. Bloquear publicação quando os totais divergem além da tolerância explicada por descontos, cancelamentos ou cobertura dos dados.

### 2. [P1] Dependência ausente impede a API de importar — confiança 10/10

[data_processor.py:7](/Users/thiagosantos/Documents/DASHBOARD_MDB/data_processor.py:7) e [mobne_api.py:8](/Users/thiagosantos/Documents/DASHBOARD_MDB/mobne_api.py:8) executam `import streamlit as st`. [requirements.txt](/Users/thiagosantos/Documents/DASHBOARD_MDB/requirements.txt:1) não declara Streamlit. `api.py` importa `DataProcessor` sem proteção, antes da criação da aplicação.

**Reprodução:** importar `data_processor` em ambiente com pandas disponível e sem Streamlit termina em `ModuleNotFoundError: No module named 'streamlit'`.

**Correção:** separar a interface Streamlit dos módulos usados pelo servidor e eliminar essa dependência do caminho de inicialização FastAPI. Se a interface antiga continuar em uso, manter suas dependências separadas. Adicionar uma verificação de importação da aplicação em ambiente limpo.

### 3. [P1] API administrativa sem controle de acesso — confiança 9/10

Rotas como `@app.post("/api/upload")`, `@app.post("/api/mobne/sync")` e `@app.post("/api/dashboard/configuracoes")`, em [api.py:82](/Users/thiagosantos/Documents/DASHBOARD_MDB/api.py:82), não têm dependência de autenticação; também não há middleware de autenticação na aplicação. `auth.py` controla somente a interface Streamlit e contém senhas fixas em texto claro.

O sincronizador ainda grava clientes em `DATA_DIR / "clientes.json"`, dentro de `public/data`, em [mobne_api.py:419](/Users/thiagosantos/Documents/DASHBOARD_MDB/mobne_api.py:419). Esse arquivo não está presente na cópia atual, mas passaria a integrar a área estática se gerado e publicado por esse caminho.

**Impacto:** caso o deployment esteja acessível sem proteção externa, terceiros podem chamar funções administrativas e ler dados publicados como estáticos. CORS não é autenticação. A exposição real na internet não foi testada.

**Correção:** autenticar no servidor, verificar permissão por operação e empresa, remover senhas fixas e servir dados privados por rotas protegidas. Manter somente assets públicos em `public/`. Substituir credenciais fixas se tiverem sido utilizadas.

### 4. [P1] Persistência não funciona como projetada na Vercel — confiança 9/10

[data_processor.py:157](/Users/thiagosantos/Documents/DASHBOARD_MDB/data_processor.py:157) usa `os.path.join("/home/user/dubairro/dados_importados", filename)`. [analises.py:52](/Users/thiagosantos/Documents/DASHBOARD_MDB/analises.py:52) grava configurações no diretório da aplicação, e [mobne_api.py:437](/Users/thiagosantos/Documents/DASHBOARD_MDB/mobne_api.py:437) abre os JSONs estáticos em modo `"w"`.

A documentação da Vercel descreve o filesystem das funções como somente leitura, com espaço temporário em `/tmp`. Mesmo usar `/tmp` não torna arquivos persistentes nem atualiza assets já publicados no CDN. [Referência oficial](https://vercel.com/docs/functions/runtimes).

**Correção:** usar armazenamento persistente para uploads, dados, configurações e status; deixar arquivos temporários apenas para processamento. Em execução local, parametrizar os caminhos. As atualizações precisam ser atômicas e isoladas por empresa: atualmente todas as empresas escrevem nos mesmos nomes de arquivo.

### 5. [P1] Sincronização sobrescreve produtos com formato incompatível — confiança 10/10

[mobne_api.py:381](/Users/thiagosantos/Documents/DASHBOARD_MDB/mobne_api.py:381) grava `produtos.json` como objeto com `empresa_id`, `total` e `produtos`. O frontend espera uma lista de indicadores e executa `produtos.filter(...)` em [public/js/app.js:340](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:340). Os registros brutos também não têm necessariamente os campos analíticos esperados pelo painel.

**Reprodução:** fornecer ao resumo o objeto gerado pelo sincronizador resulta em `TypeError: produtos.filter is not a function`. Na direção inversa, `analises.carregar_produtos()` retorna zero produtos para a lista atual de 1.252 registros, pois tenta `data.get("produtos", [])`.

**Correção:** separar dados brutos de dados analíticos e definir contratos explícitos para ambos. Gerar os indicadores antes de disponibilizar o conjunto consumido pelo painel. Validar o formato antes de substituir a versão anterior.

### 6. [P1] Lucro estimado é apresentado como lucro realizado — confiança 10/10

[sync_mobne_data.py:136](/Users/thiagosantos/Documents/DASHBOARD_MDB/sync_mobne_data.py:136) usa `lucro = vr_total * 0.45` quando não encontra custo. A importação simplificada faz o mesmo em [public/js/app.js:1249](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:1249). A análise da API usa outra regra: `lucro_bruto = metricas["valor_total"] * margem_meta`, em [analises.py:256](/Users/thiagosantos/Documents/DASHBOARD_MDB/analises.py:256).

**Reprodução:** vendas sintéticas de R$ 300 sem custo produzem R$ 135 de lucro. Os próprios dados mensais atuais apresentam margem de 45% e custo zero.

**Correção:** calcular resultado a partir dos custos efetivos, com regra explícita de custo unitário versus total, descontos e devoluções. Quando faltar custo, apresentar lucro como indisponível. Estimativas devem ficar identificadas e separadas dos números realizados. A classificação de lucro líquido também deve refletir quais despesas estão efetivamente incluídas.

### 7. [P1] Importação altera meses, datas e valores — confiança 10/10

[public/js/app.js:1242](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:1242) define o período pela primeira data válida e agrega todas as linhas somente por categoria. A linha 1248 usa `parseFloat(...)`; a linha 1219 converte datas seriais Excel para um instante UTC e depois usa getters de data local.

**Reproduções:**

- Janeiro R$ 100 + fevereiro R$ 200 vira janeiro R$ 300.
- Texto brasileiro `1.234,56` vira o número `1.234`, em vez de `1234.56`.
- Serial Excel `46054`, correspondente a 01/02/2026, vira 31/01/2026 às 21h no fuso de Brasília e é agregado em janeiro.

O agregador Python também agrupa apenas por categoria e não inclui `mes`/`ano` na saída, apesar de receber esses argumentos.

**Correção:** agrupar por empresa, ano, mês e categoria; interpretar datas Excel como datas civis; validar calendários e números brasileiros explicitamente. Rejeitar linhas inválidas com número da linha e motivo. Exigir confirmação explícita do modo de substituição de períodos, pois a importação atual remove todo o período anterior antes de adicionar o arquivo novo.

### 8. [P1] Upload pode anunciar sucesso sem salvar no servidor — confiança 10/10

[public/js/app.js:1377](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:1377) executa `fetch('/api/upload', { method: 'POST', body: formData }).catch(() => {});` depois da mensagem de sucesso. Não verifica status HTTP; erros 400/500 não rejeitam `fetch`. Falhas ao salvar em `localStorage` também ficam apenas no console.

No servidor, todos os formatos passam por `processor.aggregate_to_monthly(...)`, em [api.py:137](/Users/thiagosantos/Documents/DASHBOARD_MDB/api.py:137). Arquivos `simples` e `produtos` não possuem as colunas de vendas exigidas nesse agregador.

**Reprodução:** os dois formatos passam pela validação de colunas e depois geram `KeyError` para `VLR_VENDA`, `VLR_LUCRO`, `QTDE_DOCUMENTOS` e `MARKDOWN_PCT`.

Mesmo o fluxo de vendas que salva Excel não atualiza os JSONs consumidos pelo dashboard. O navegador persiste somente dados mensais e YoY; os dados diários e de produtos continuam anteriores. Ao recarregar, `DATA.vendas_mensais = vendas_mensais` em [public/js/app.js:279](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:279) substitui a base recém-baixada por uma cópia local sem verificar versão ou validade.

**Correção:** fazer o servidor validar, persistir e devolver o conjunto/versionamento confirmado; só então anunciar sucesso. Implementar processamento específico por formato. Usar armazenamento local como cache identificado por versão, não como fonte de verdade. Recalcular todos os indicadores afetados pela importação.

### 9. [P1] Paginação pode truncar dados e publicar resultados parciais — confiança 10/10

[mobne_api.py:217](/Users/thiagosantos/Documents/DASHBOARD_MDB/mobne_api.py:217) define `limit: int = 100` e `fetch_vendas` faz uma única chamada. Os endpoints mensal/YoY e a sincronização JSON usam esse método sem percorrer páginas. Produtos e clientes da sincronização também ficam na primeira página.

O script `sync_mobne_data.py` pagina, mas usa `if not success or not vendas: break`, na [linha 81](/Users/thiagosantos/Documents/DASHBOARD_MDB/sync_mobne_data.py:81). O chamador aceita qualquer lista não vazia e substitui o período anterior. Há ainda um teto de 100 páginas sem indicação de incompletude.

**Reprodução:** uma primeira página de 100 pedidos e erro na segunda retornam 100 pedidos como resultado normal.

**Correção:** unificar a paginação, comparar contagem com os metadados disponíveis, distinguir vazio de falha e impedir a publicação de uma execução incompleta. Usar retentativas limitadas para falhas transitórias e deduplicação por identificador estável. Preservar a última versão válida até a nova sincronização terminar.

### 10. [P1] Contratos de vendas variam entre módulos — confiança 8/10

[api.py:403](/Users/thiagosantos/Documents/DASHBOARD_MDB/api.py:403) procura `DataEmissao`, `Itens`/`Items` e `ValorTotal`/`ValorFinal`. [analises.py:135](/Users/thiagosantos/Documents/DASHBOARD_MDB/analises.py:135) filtra por `DtaEmissao`, e suas métricas usam `Valor`. [sync_mobne_data.py:111](/Users/thiagosantos/Documents/DASHBOARD_MDB/sync_mobne_data.py:111) prioriza `PedidoItens`, `VlrTotal` e `VlrLiquidoItem`.

Um pedido no formato documentado pelo próprio sincronizador perde itens e valor no endpoint mensal. Além disso, sua resposta é por item e não traz `Mes`, `Ano` ou `Periodo`, exigidos pelo filtro do frontend. O frontend atual não chama esse endpoint, por isso esta incompatibilidade afeta a API e uma futura ligação direta, não explica sozinha os dados estáticos atuais.

**Correção:** validar amostras reais sanitizadas e implementar um único adaptador Mobne → modelo interno. Rejeitar formatos desconhecidos em vez de converter silenciosamente campos ausentes em zero. A documentação atual do contrato Mobne não foi validada nesta auditoria.

### 11. [P2] Cupons são duplicados entre categorias e SKUs contam categorias — confiança 10/10

[sync_mobne_data.py:194](/Users/thiagosantos/Documents/DASHBOARD_MDB/sync_mobne_data.py:194) faz `totais[key]["cupons"] += row["Qtde_Documentos"]`. O mesmo pedido pode estar em várias categorias. A linha seguinte faz `totais[key]["skus"] += 1`, contando categorias como produtos.

**Reprodução:** um único pedido com duas categorias gera dois cupons. Isso reduz artificialmente o ticket médio. Na UI, número de cupons também é chamado de clientes, embora uma pessoa possa comprar mais de uma vez.

**Correção:** calcular cupons a partir de IDs distintos de pedidos e SKUs a partir de IDs distintos de produtos no conjunto original. Manter contagens por categoria apenas para essa dimensão; distinguir compras de clientes únicos.

### 12. [P2] Seleção de período não governa todos os indicadores — confiança 10/10

[public/js/app.js:333](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:333) compara com `yoyMes[yoyMes.length - 1]`, independentemente do mês selecionado. A linha 343 usa `periodoBadge(mesNome, 2026)`. Em [public/js/app.js:635](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:635), o diagnóstico recebe `vd = DATA.vendas_diarias` sem filtro de período. Produtos e erosão também não têm seleção temporal equivalente.

**Reprodução:** selecionar dezembro de 2025 apresenta `Analisando: Dezembro/2026`. Gráficos diários continuam usando a base de janeiro.

**Correção:** centralizar o filtro de período; escolher a linha YoY do mês e ano corretos; incluir período nos dados de produtos e erosão ou informar claramente a competência desses indicadores. Gerar anos disponíveis pelos dados, evitando referências fixas a 2025/2026.

### 13. [P1] Texto de planilha é interpretado como HTML — confiança 9/10

[public/js/app.js:1199](/Users/thiagosantos/Documents/DASHBOARD_MDB/public/js/app.js:1199) concatena `html += `<td>${val || '-'}</td>`;` e a prévia é inserida via `innerHTML` na linha 1123. Cabeçalhos e nomes em outras tabelas também são interpolados sem escape.

**Reprodução:** uma célula contendo uma tag HTML benigna foi preservada como marcação na saída da função, em vez de texto. Não foi executado um ataque no navegador. O caminho permite XSS ao abrir uma planilha maliciosa; em uso compartilhado, a persistência de campos não sanitizados aumenta o alcance.

**Correção:** usar `textContent` para dados e construir a tabela com elementos DOM; se houver HTML proposital, sanitizar somente esse caso com uma solução mantida. Adicionar teste que exija representação textual de tags em células e cabeçalhos.

### 14. [P2] I/O bloqueante dentro de rotas assíncronas e YoY caro — confiança 9/10

[api.py:530](/Users/thiagosantos/Documents/DASHBOARD_MDB/api.py:530) percorre 12 meses e chama `client.fetch_vendas` duas vezes por mês: **24 consultas sequenciais**, desconsiderando cache. O cliente usa `self.session.request(...)` em [mobne_api.py:130](/Users/thiagosantos/Documents/DASHBOARD_MDB/mobne_api.py:130), com timeout padrão de 30 segundos. Uploads executam pandas diretamente em rotas `async def`.

**Impacto:** essas operações podem ocupar o event loop e atrasar outras requisições no mesmo processo. Não foi medida latência de produção. Como ilustração, 24 chamadas de um segundo somariam aproximadamente 24 segundos só de rede; isso não é um benchmark. A orientação sobre rotas síncronas e threadpool está na [documentação oficial FastAPI](https://fastapi.tiangolo.com/async/).

**Correção:** tirar a sincronização do caminho das consultas e persistir agregados mensais. Executar jobs com progresso, timeout e limites de concorrência. Para chamadas síncronas curtas, usar a integração apropriada com threadpool; processamento pesado merece worker separado. Consultar o ERP apenas para atualizar dados, não para recalcular dois anos a cada abertura de relatório.

## Manutenção e gargalos secundários

- **Cópias divergentes:** `js/app.js` tem 1.248 linhas e `public/js/app.js`, 1.458. HTML, CSS e JSON também diferem. Escolher uma raiz ativa e arquivar o restante fora do pacote de deploy, mantendo backup. Corrigir uma cópia não corrige as outras.
- **Testes não protegem o negócio:** os arquivos `test_mobne_*` são diagnósticos de rede com mensagens de sucesso, sem assertions de reconciliação. Um deles faz chamadas já na importação e imprime parte da chave. Não foram executados. Separar diagnósticos manuais de testes automatizados e remover logging de segredos.
- **Falhas parecem ausência de vendas:** `loadData()` converte HTTP não OK e falhas de rede em listas vazias. Exibir separadamente: erro, ausência real de dados e dados desatualizados. Não gerar conclusões financeiras a partir de uma carga incompleta.
- **Carga inicial:** os seis JSONs carregados no início somam aproximadamente 579 KiB sem compressão, incluindo 1.252 produtos e 730 dias de calendário. Plotly e SheetJS são carregados por scripts bloqueantes no `<head>`. Carregar SheetJS somente na importação e dados conforme a página é uma melhoria mensurável; os filtros sobre 1.252 produtos não demonstram, por si, necessidade de uma arquitetura mais complexa.
- **Importações sem limites explícitos:** definir tamanho, quantidade de linhas/abas, timeout e formatos realmente suportados. `.xls` é aceito pela API, mas seu leitor habitual não está declarado nas dependências. Processamento integral no navegador pode congelar a tela com arquivos grandes; medir antes de decidir por worker no frontend.
- **Portabilidade e publicação:** o processador principal usa `/mnt/user-data/uploads` e `/mnt/user-data/outputs`. A configuração `.vercelignore` exclui `*.png`, incluindo o logo referenciado pelo HTML. Parametrizar diretórios, revisar exclusões e testar o deployment real. Não foi concluído que as rotas Vercel estejam publicadas corretamente ou incorretamente apenas pela configuração local.
- **Reprodutibilidade:** dependências Python usam limites inferiores sem versões resolvidas. Manter arquivo de lock/constraints, versão de runtime e verificações de instalação limpa. Dependabot configurado não comprova que alertas ou CI estejam funcionando remotamente.
- **Médias de margem:** há médias aritméticas de percentuais em `data_processor.py:128` e `processar_dados_mercado.py:365`. Para apresentar margem financeira consolidada, usar lucro total dividido por receita total; se a intenção for média por dia, rotular essa métrica explicitamente.

## Plano recomendado e critérios de aceite

**Atualização aprovada em 11/09/2026:** a integração direta com o ERP Mobne passa a ser a fonte principal, sem dependência de importação Excel. O [plano de implementação Mobne](/Users/thiagosantos/Documents/DASHBOARD_MDB/PLANO_IMPLEMENTACAO_MOBNE.md) substitui as etapas de upload abaixo como fluxo operacional e detalha endpoints documentados, carga histórica, atualização incremental e critérios de aceite. Os achados da auditoria permanecem como registro do estado original.

1. **Estabilizar e reconciliar.** Preservar os arquivos atuais, definir qual versão será mantida, corrigir inicialização e confrontar pelo menos um mês completo com o ERP. Nenhum dashboard deve combinar agregados de versões distintas.
2. **Reescrever o núcleo de dados.** Modelo único de empresa, produto, documento, item, data, receita, custo e origem; importação validada; adaptador Mobne; paginação completa; deduplicação; atualização atômica; armazenamento persistente. Criar testes a partir dos casos reproduzidos nesta análise.
3. **Unificar os indicadores.** Regras calculadas uma única vez, com rastreabilidade até os registros de origem. Custos ausentes não viram lucro realizado. Cupons distintos, períodos e números brasileiros precisam passar pelos testes.
4. **Conectar a interface à fonte persistente.** Confirmar gravação antes de anunciar sucesso, invalidar cache por versão, aplicar filtros a todos os componentes e mostrar origem/atualização/cobertura. Preservar as telas úteis, separando aos poucos estado, acesso a dados, cálculos e renderização.
5. **Preparar uso compartilhado e operação.** Autenticação, autorização por empresa, proteção de dados privados, sincronização em job, logs sem segredos, testes automatizados e verificação de deployment. Medir latências e memória antes de otimizações adicionais.

Critérios mínimos: importar janeiro e fevereiro sem misturar meses; converter `1.234,56` e datas Excel corretamente; contar um pedido com duas categorias como um cupom; falha na página 2 não substituir a base completa; importação repetida não duplicar documentos; mudanças não autorizadas serem recusadas; os totais mensal, diário e por produto fecharem com a mesma origem e critérios; uma importação confirmada aparecer em outro navegador autorizado.

## Decisão sobre reescrita

**Viável e recomendada para o núcleo de dados.** Hoje as regras de negócio estão repetidas em JavaScript, pandas, scripts de exportação e adaptadores Mobne, com resultados diferentes. Corrigir apenas a interface deixaria esses conflitos ativos.

**Reaproveitar:** organização das páginas, conceitos dos indicadores, estilos/gráficos que atendem ao usuário, parsers de relatórios validados e conhecimento acumulado dos campos Mobne. **Reescrever:** contratos de dados, persistência, importação, sincronização e cálculo centralizado. **Reavaliar depois:** migração da interface para outro framework, somente se sua evolução justificar.

Estimar prazo fechado agora seria prematuro: falta confirmar o contrato real do Mobne, o ambiente efetivamente publicado e as regras de custo/documentos. A primeira entrega deve ser um fluxo completo de um mês reconciliado, funcionando com dados persistentes e testes; a migração dos demais relatórios vem após essa validação.
