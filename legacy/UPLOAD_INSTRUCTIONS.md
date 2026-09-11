# 📥 Guia de Importação de Dados - Mercado duBairro

## Visão Geral

A funcionalidade de importação permite que administradores carreguem dados em Excel para análise no dashboard.

## 🔐 Autenticação

Apenas usuários autenticados como administrador podem acessar a página de importação.

**Credenciais Padrão:**
- Usuário: `admin` | Senha: `dubairro2026`
- Usuário: `gestor` | Senha: `gestor123`

> ⚠️ **IMPORTANTE:** Altere as senhas em produção! Use variáveis de ambiente.

## 📋 Formatos Suportados

### 1. Formato de Vendas (Mais Completo)
Use este formato se você tem dados detalhados de vendas por transação.

**Colunas Obrigatórias:**
```
Data, Categoria, Produto, Quantidade, Valor_Unitario, Vlr_Venda, Custo, Qtde_Documentos
```

**Exemplo:**
```
Data          | Categoria    | Produto      | Quantidade | Valor_Unitario | Vlr_Venda | Custo  | Qtde_Documentos
2026-01-15   | Alimentos    | Arroz 5kg    | 10         | 25.00          | 250.00    | 150.00 | 1
2026-01-15   | Bebidas      | Suco 1L      | 5          | 15.00          | 75.00     | 35.00  | 1
```

**Processamento:**
- Calcula automaticamente: `Vlr_Lucro = Vlr_Venda - Custo`
- Calcula: `Markdown_Pct = (Vlr_Lucro / Vlr_Venda) * 100`
- Agrega dados diários em formato mensal
- Gera abas: `fato_vendas_mensais` e `fato_vendas_diarias`

### 2. Formato de Produtos
Use para atualizar dados de catálogo de produtos.

**Colunas Obrigatórias:**
```
Produto, Categoria, Custo_Medio, Preco, Estoque
```

**Exemplo:**
```
Produto    | Categoria    | Custo_Medio | Preco | Estoque
Arroz 5kg  | Alimentos    | 15.00       | 25.00 | 50
Feijão 1kg | Alimentos    | 8.00        | 12.00 | 75
```

**Processamento:**
- Calcula: `Margem = ((Preco - Custo_Medio) / Preco) * 100`
- Gera aba: `dim_produtos`

### 3. Formato Simples
Use para dados agregados ou importação rápida.

**Colunas Obrigatórias:**
```
Data, Categoria, Produto, Faturamento
```

**Exemplo:**
```
Data       | Categoria    | Produto    | Faturamento
2026-01-15 | Alimentos    | Diversos   | 1000.00
2026-01-16 | Bebidas      | Diversos   | 500.00
```

## 🚀 Como Usar

### Passo 1: Fazer Login
1. Abra o dashboard
2. Vá para a página **"📥 Importação de Dados"** (após autenticar)
3. Digite suas credenciais de admin

### Passo 2: Preparar o Arquivo
1. Organize seus dados em Excel com as colunas corretas
2. Salve como `.xlsx` (Excel format)
3. Verifique se não há linhas vazias no final

### Passo 3: Upload
1. Clique em "Selecione um arquivo Excel"
2. Escolha seu arquivo
3. Revise o preview dos dados
4. Clique em "✅ Processar e Salvar Dados"

### Passo 4: Confirmação
O sistema:
- ✅ Detecta o formato automaticamente
- ✅ Valida as colunas
- ✅ Processa os dados
- ✅ Salva em `/dados_importados/`

## 📊 Dados Processados

Os dados são salvos em Excel com múltiplas abas:

### Para Vendas:
- **fato_vendas_mensais**: Agregado mensal por categoria
- **fato_vendas_diarias**: Detalhado por dia

### Para Produtos:
- **dim_produtos**: Catálogo de produtos processado

### Para Simples:
- **dados_importados**: Dados conforme enviados

## 🔍 Validação

O sistema valida:
- ✅ Presença de todas as colunas obrigatórias
- ✅ Tipo de dados (numéricos, datas)
- ✅ Ausência de linhas vazias
- ✅ Limite de arquivo (5MB)

## ⚙️ Estrutura de Arquivos

```
dubairro/
├── app.py                    # Aplicação principal
├── auth.py                   # Autenticação e controle de acesso
├── data_processor.py         # Processamento de dados
├── dados_importados/         # Diretório com uploads processados
│   ├── data_upload_20260220_145300.xlsx
│   ├── data_upload_20260220_150000.xlsx
│   └── ...
└── UPLOAD_INSTRUCTIONS.md    # Este arquivo
```

## 🔒 Segurança

1. **Autenticação**: Apenas admins podem fazer upload
2. **Validação**: Todos os dados são validados
3. **Isolamento**: Dados importados ficam separados
4. **Rastreamento**: Timestamp em cada arquivo importado

## 🐛 Troubleshooting

### Erro: "Colunas faltando"
- Verifique se os nomes das colunas estão exatos
- Capitulação não importa, mas espaços em branco sim
- Use `Data`, `Categoria`, `Produto`, etc.

### Erro: "Arquivo vazio"
- Certifique-se de que tem dados após o header
- Remova linhas em branco do final

### Erro: "Tipo de dados não reconhecido"
- O sistema não conseguiu detectar o formato
- Adicione mais colunas ou use um formato padrão

## 📞 Suporte

Para questões técnicas, verifique:
1. Se as colunas estão corretas
2. Se o arquivo é `.xlsx` válido
3. Se há dados nulos ou incompletos

## 📝 Notas Futuras

Melhorias planejadas:
- [ ] Integração com banco de dados
- [ ] Suporte para `.csv`
- [ ] Histórico de importações
- [ ] Merge com dados existentes
- [ ] Agendamento automático
- [ ] API REST para upload programático

---

**Versão:** 1.0
**Data:** Fevereiro 2026
**Mercado duBairro © 2026**
