# Central de Ações — design aprovado

Data: 14/09/2026
Simulação navegável: https://claude.ai/code/artifact/758ae999-61a2-46bd-a09c-684c54a65869
Plano de implementação: `docs/superpowers/plans/2026-09-14-central-de-acoes.md`

## Problema

A Central de Ações atual (`web/assets/actions.js`) é uma tabela com:

- o texto inteiro do alerta como título (nomes de produtos em maiúsculas, cortados);
- prioridade em inglês ("medium") e datas com segundos;
- ações abertas e resolvidas misturadas, sem contadores nem filtros;
- três botões empilhados por linha, e "Descartar" sem confirmação;
- nenhuma forma de ver o que foi feito, embora o backend já grave uma anotação por mudança de situação e o histórico da ação.

## Decisões (itens aprovados pelo sócio)

Nesta tela:

1. **Contadores no topo:** A fazer, Em andamento, Atrasadas e Concluídas nos últimos 30 dias. Clicar filtra a lista.
2. **Filtros:** abas Pendentes (padrão), Concluídas, Descartadas e Todas, mais um filtro por origem. O filtro fica na URL (`#/acoes?situacao=…&origem=…`).
3. **Cartões legíveis:**
   - título curto;
   - até 3 produtos em etiquetas, com "+N";
   - prioridade Alta/Média/Baixa com cor e palavra;
   - idade relativa ("há 2 dias").
   - Ações criadas a partir de alertas herdam a gravidade do alerta como prioridade.
4. **Um botão principal por cartão:** Assumir, e depois Concluir. O menu "Mais" traz Concluir ou Voltar para "A fazer", Responsável e prazo, Histórico e Descartar (com confirmação e motivo).
5. **Situação de hoje:**
   - alertas: quantos existem no mês sincronizado mais recente, comparado com a contagem na criação;
   - "O alerta não aparece mais no painel: pode concluir" quando o alerta sumiu;
   - ação de preço de um produto: preço, custo e margem atuais.
6. **Concluir:** pede "O que foi feito?", uma anotação opcional. O histórico mostra quem fez o quê e quando.
7. **Atalho para a origem:**
   - Produtos e estoque já filtrado;
   - Mapa de produtos;
   - página do produto;
   - Integrações.
8. **Alerta repetido:** o cartão mostra "Já tratado antes: concluída em DD/MM" e dá acesso ao histórico da ação anterior.

Outras melhorias:

9. **Responsável e prazo:**
   - quem assume vira responsável;
   - dá para trocar o responsável, o prazo e a prioridade;
   - prazo vencido aparece como "atrasada".
10. **Contador de ações pendentes no menu lateral.**
11. **Nova ação sem alerta (manual).**
12. **Resultado medido depois de concluir:**
    - ação de preço: margem e faturamento do produto nos 30 dias antes da criação contra os 30 dias depois da conclusão (parcial enquanto os 30 dias não passaram);
    - ações de contagem: contagem de hoje contra a da criação.
13. **Sem seletor de mês na Central:** a situação de hoje sempre lê o mês sincronizado mais recente.
14. **Aviso no Resumo executivo:** uma linha abaixo das boas-vindas quando há ação atrasada ou parada há mais de 7 dias.

## Fica para depois (registrado a pedido do sócio)

15. **Resumo semanal das ações por e-mail ou WhatsApp.** Toda segunda de manhã: o que está pendente, o que atrasou e o que foi concluído na semana.
    - O sócio **gostou da ideia** e pediu para implementar em outro momento.
    - Combina com a ideia 7 do Resumo executivo (resumo semanal do faturamento), que também ficou para depois.
    - Precisa de um serviço de envio (e-mail transacional ou API do WhatsApp) e de um agendamento (cron da Vercel).
