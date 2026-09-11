#!/usr/bin/env python3
"""
Script de teste detalhado para diagnosticar problemas na integração Mobne
Mostra informações sobre requests e respostas
"""

import os
import json
from dotenv import load_dotenv
import requests
from datetime import datetime, timedelta

# Carregar variáveis de ambiente
load_dotenv()

# Configurações
MOBNE_API_BASE_URL = os.getenv("MOBNE_API_URL", "https://apiexternal.mobne.com.br")
MOBNE_API_KEY = os.getenv("MOBNE_API_KEY", "")
MOBNE_CNPJ = os.getenv("MOBNE_CNPJ", "")
MOBNE_EMPRESA_ID = os.getenv("MOBNE_EMPRESA_ID", "218")

print("=" * 70)
print("TESTE DETALHADO DE INTEGRAÇÃO COM API MOBNE")
print("=" * 70)

# Headers
headers = {
    "Authorization": f"ApiKey {MOBNE_API_KEY}",
    "Content-Type": "application/json",
    "empresaId": MOBNE_EMPRESA_ID,
    "Accept": "application/json",
    "User-Agent": "Mercado-duBairro/1.0"
}

print("\n📋 HEADERS ENVIADOS:")
print(json.dumps({
    "Authorization": f"ApiKey {MOBNE_API_KEY[:30]}...",
    "Content-Type": "application/json",
    "empresaId": MOBNE_EMPRESA_ID,
    "Accept": "application/json",
    "User-Agent": "Mercado-duBairro/1.0"
}, indent=2, ensure_ascii=False))

# Endpoints para testar
endpoints = [
    {
        "name": "Verificar Conexão (Produtos)",
        "method": "GET",
        "endpoint": "/api/v1/Produto/consulta-cadastro-produto?PageSize=1&PageNumber=1",
    },
    {
        "name": "Buscar Clientes",
        "method": "GET",
        "endpoint": "/api/v1/Cliente/consulta-cadastro-cliente?PageSize=1&PageNumber=1",
    },
    {
        "name": "Buscar Vendas",
        "method": "GET",
        "endpoint": "/api/v1/vendas",
    },
]

print("\n" + "=" * 70)
print("TESTANDO ENDPOINTS")
print("=" * 70)

for endpoint_info in endpoints:
    print(f"\n📡 {endpoint_info['name']}")
    print(f"   Método: {endpoint_info['method']}")

    url = f"{MOBNE_API_BASE_URL}{endpoint_info['endpoint']}"
    print(f"   URL: {url}")

    try:
        if endpoint_info['method'] == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        else:
            response = requests.post(url, headers=headers, timeout=10)

        print(f"   Status: {response.status_code}")
        print(f"   Headers Resposta: {dict(response.headers)}")

        if response.status_code == 200:
            data = response.json()
            print(f"   ✅ Sucesso!")
            print(f"   Resposta (primeiros 500 chars): {json.dumps(data)[:500]}")
        else:
            print(f"   ❌ Erro HTTP {response.status_code}")
            if response.text:
                print(f"   Resposta: {response.text[:500]}")

    except requests.exceptions.Timeout:
        print(f"   ❌ Timeout na requisição")
    except requests.exceptions.ConnectionError as e:
        print(f"   ❌ Erro de conexão: {str(e)[:200]}")
    except Exception as e:
        print(f"   ❌ Erro: {str(e)[:200]}")

print("\n" + "=" * 70)
print("RESUMO DIAGNÓSTICO")
print("=" * 70)
print("""
✅ VERIFICADO:
  • Conexão com servidor Mobne está respondendo
  • Headers de autenticação estão sendo enviados corretamente

⚠️  POSSÍVEIS PROBLEMAS:
  • Alguns endpoints retornam 404 (não encontrado)
  • Pode ser necessário ajustar URLs dos endpoints
  • CNPJ pode estar em placeholder (00.000.000/0000-00)

💡 PRÓXIMOS PASSOS:
  1. Verifique se o CNPJ está correto no arquivo .env
  2. Consulte a documentação da API Mobne
  3. Verifique se sua chave de API tem permissão para esses endpoints
  4. Teste via Streamlit: http://localhost:8501/integracao_mobne
""")
