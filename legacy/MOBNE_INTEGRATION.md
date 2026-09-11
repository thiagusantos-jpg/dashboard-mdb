# 🔗 Integração com ERP Mobne

Guia completo para integração do Mercado duBairro com ERP Mobne via API REST.

## 📋 Pré-requisitos

1. **Conta no Mobne** com acesso à API
2. **Credenciais da API**:
   - Chave de API (API Key)
   - CNPJ da empresa registrada no Mobne
3. **Python 3.8+** com bibliotecas requeridas:
   ```bash
   pip install requests pandas streamlit
   ```

## 🚀 Instalação

### 1. Obter Credenciais do Mobne

1. Acesse https://api.mobne.com.br/admin
2. Faça login com suas credenciais
3. Navegue até **API > Chaves de Acesso**
4. Gere uma nova chave de API ou copie a existente
5. Copie também o CNPJ da sua empresa

### 2. Configurar Variáveis de Ambiente

1. Copie o arquivo `.env.example` para `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edite o arquivo `.env` com suas credenciais:
   ```env
   MOBNE_API_URL=https://apiexternal.mobne.com.br
   MOBNE_API_KEY=sua_chave_api_aqui
   MOBNE_CNPJ=00.000.000/0000-00
   ```

### 3. Testar a Conexão

No Streamlit, vá para a página "Integração Mobne" e:
1. Preencha a chave de API e CNPJ
2. Clique em "Conectar ao Mobne"
3. Você verá uma mensagem de sucesso se estiver tudo OK

## 📦 Funcionalidades

### 1. Sincronizar Produtos

Importa produtos do ERP Mobne para o dashboard:

```python
from mobne_api import MobneAPIClient

client = MobneAPIClient(api_key="sua_chave", cnpj="seu_cnpj")
success, df = client.sync_produtos_para_dataframe()

if success:
    print(f"Sincronizados {len(df)} produtos")
    print(df.head())
```

**Campos sincronizados:**
- `id` - ID do produto
- `nome` - Nome do produto
- `sku` - Código do produto
- `preco` - Preço de venda
- `custo_medio` - Custo médio
- `estoque` - Quantidade em estoque
- `categoria` - Categoria do produto

### 2. Sincronizar Clientes

Importa cadastro de clientes do Mobne:

```python
success, df = client.sync_clientes_para_dataframe()

if success:
    print(f"Sincronizados {len(df)} clientes")
```

**Campos sincronizados:**
- `id` - ID do cliente
- `nome` - Nome do cliente
- `cnpj_cpf` - CNPJ/CPF
- `email` - Email
- `telefone` - Telefone
- `endereco` - Endereço
- `cidade` - Cidade
- `estado` - Estado

### 3. Sincronizar Vendas

Importa histórico de vendas do Mobne:

```python
from datetime import datetime, timedelta

data_inicio = datetime.now() - timedelta(days=30)
data_fim = datetime.now()

success, df = client.sync_vendas_para_dataframe(data_inicio, data_fim)

if success:
    print(f"Sincronizadas {len(df)} vendas")
    print(f"Faturamento total: R$ {df['valor_total'].sum():.2f}")
```

**Campos sincronizados:**
- `id` - ID da venda
- `data` - Data da venda
- `cliente_id` - ID do cliente
- `produtos` - Array de produtos
- `valor_total` - Valor total da venda
- `status` - Status da venda

### 4. Enviar Vendas

Envia dados de vendas para o Mobne:

```python
venda_data = {
    "data": "2026-02-20",
    "cliente_id": 123,
    "produtos": [
        {
            "produto_id": 456,
            "quantidade": 2,
            "valor_unitario": 50.00
        }
    ],
    "valor_total": 100.00,
    "observacoes": "Venda de teste"
}

success, venda_id = client.send_venda(venda_data)

if success:
    print(f"Venda {venda_id} enviada com sucesso!")
```

**Campos obrigatórios:**
- `data` - Data no formato YYYY-MM-DD
- `cliente_id` - ID do cliente (número inteiro)
- `produtos` - Array com dados dos produtos
- `valor_total` - Valor total da venda (decimal)

## 🔐 Segurança

### Proteções Implementadas

1. **Timeout de Requisições**: 30 segundos (evita travamentos)
2. **Tratamento de Erros**: Todas as requisições têm tratamento robusto
3. **Headers de Autenticação**: Bearer token + CNPJ em headers customizados
4. **Validação de Dados**: Verificação obrigatória de campos
5. **Logging**: Todos os eventos são registrados

### Boas Práticas

```python
# ✅ BOM: Usar variáveis de ambiente
import os
api_key = os.getenv("MOBNE_API_KEY")
cnpj = os.getenv("MOBNE_CNPJ")

# ❌ EVITAR: Hardcoded credentials
api_key = "chave_api_aqui"  # Nunca faça isso!
```

### Proteção em Produção

1. Use variáveis de ambiente (não commite `.env`)
2. Adicione `.env` ao `.gitignore` (já configurado)
3. Regenere a chave de API periodicamente
4. Implemente rate limiting no backend
5. Use HTTPS sempre

## 📊 Exemplos de Uso

### Exemplo 1: Dashboard de Sincronização

```python
import streamlit as st
from mobne_api import MobneIntegration

# Inicializar
integration = MobneIntegration()

# Conectar
success, msg = integration.connect(api_key="...", cnpj="...")

if success:
    st.success("Conectado ao Mobne!")

    # Sincronizar produtos
    client = integration.get_client()
    success, df_produtos = client.sync_produtos_para_dataframe()
    st.dataframe(df_produtos)
```

### Exemplo 2: Envio em Batch

```python
import pandas as pd
from mobne_api import MobneAPIClient

client = MobneAPIClient(api_key="...", cnpj="...")

# Ler dados do CSV
df = pd.read_csv("vendas.csv")

sucessos = 0
for _, row in df.iterrows():
    venda_data = {
        "data": row['data'],
        "cliente_id": int(row['cliente_id']),
        "produtos": [{"quantidade": int(row['quantidade'])}],
        "valor_total": float(row['valor_total'])
    }

    success, _ = client.send_venda(venda_data)
    if success:
        sucessos += 1

print(f"Enviadas {sucessos}/{len(df)} vendas")
```

### Exemplo 3: Sincronização Agendada

```python
from apscheduler.schedulers.background import BackgroundScheduler
from mobne_api import MobneAPIClient

def sync_diaria():
    client = MobneAPIClient(api_key="...", cnpj="...")
    success, df = client.sync_vendas_para_dataframe()
    print(f"Sincronizadas {len(df)} vendas")

scheduler = BackgroundScheduler()
scheduler.add_job(sync_diaria, 'cron', hour=23, minute=0)  # 23:00 todos os dias
scheduler.start()
```

## 🐛 Solução de Problemas

### Erro: "Erro de conexão com Mobne API"

**Causa**: Problema de conectividade ou URL incorreta

**Solução**:
1. Verifique se `MOBNE_API_URL` está correto
2. Teste a URL no navegador ou Postman
3. Verifique firewall/proxy
4. Verifique se a internet está conectada

### Erro: "401 Unauthorized"

**Causa**: Chave de API inválida ou expirada

**Solução**:
1. Regenere a chave em https://api.mobne.com.br/admin
2. Atualize `.env` com a nova chave
3. Reinicie o Streamlit

### Erro: "Timeout na requisição"

**Causa**: Servidor Mobne lento ou muitos dados

**Solução**:
1. Aumentar `MOBNE_API_TIMEOUT` em `.env`
2. Sincronizar dados em períodos menores
3. Usar paginação: `fetch_produtos(limit=100, offset=0)`

### Erro: "Campos obrigatórios faltando"

**Causa**: Dados incompletos ao enviar venda

**Solução**:
```python
# Verificar campos obrigatórios
required = ['data', 'cliente_id', 'produtos', 'valor_total']
missing = [f for f in required if f not in venda_data]

if missing:
    print(f"Faltando: {missing}")
```

## 📈 Performance e Otimizações

### Paginação

Para sincronizar grandes volumes de dados:

```python
all_products = []
limit = 100
offset = 0

while True:
    success, products = client.fetch_produtos(limit=limit, offset=offset)
    if not success or not products:
        break

    all_products.extend(products)
    offset += limit
```

### Cache de Dados

```python
import streamlit as st

@st.cache_data(ttl=3600)  # Cache por 1 hora
def get_products():
    client = MobneAPIClient(api_key="...", cnpj="...")
    success, df = client.sync_produtos_para_dataframe()
    return df

df = get_products()
```

### Processamento Assíncrono

```python
import asyncio
import aiohttp

async def sync_multiple():
    async with aiohttp.ClientSession() as session:
        # Sincronizar produtos, clientes e vendas em paralelo
        tasks = [
            client.fetch_produtos(),
            client.fetch_clientes(),
            client.fetch_vendas()
        ]
        results = await asyncio.gather(*tasks)
        return results
```

## 📞 Suporte

- **Documentação Mobne**: https://api.mobne.com.br/docs
- **Status da API**: https://status.mobne.com.br
- **Email de Suporte**: api-support@mobne.com.br

## 📝 Changelog

### v1.0 (2026-02-20)
- ✨ Integração inicial com Mobne API
- 📦 Sincronização de produtos
- 👥 Sincronização de clientes
- 💰 Sincronização de vendas
- 📤 Envio de vendas em batch
- 🔐 Tratamento robusto de erros
- 📊 Interface Streamlit completa
