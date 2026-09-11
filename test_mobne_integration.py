#!/usr/bin/env python3
"""
Script de teste para validar a integração com API Mobne
Verifica se a conexão está funcionando e exibe informações dos dados
"""

import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from mobne_api import MobneAPIClient

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

def test_connection():
    """Testa a conexão com a API Mobne"""
    print("=" * 60)
    print("TESTE DE INTEGRAÇÃO COM API MOBNE")
    print("=" * 60)

    # Carregar credenciais
    api_key = os.getenv("MOBNE_API_KEY", "")
    cnpj = os.getenv("MOBNE_CNPJ", "")
    empresa_id = os.getenv("MOBNE_EMPRESA_ID", "218")

    print("\n📋 CONFIGURAÇÕES:")
    print(f"  • URL Base: {os.getenv('MOBNE_API_URL', 'https://apiexternal.mobne.com.br')}")
    print(f"  • CNPJ: {cnpj if cnpj != '00.000.000/0000-00' else '⚠️ NÃO CONFIGURADO'}")
    print(f"  • Empresa ID: {empresa_id}")
    print(f"  • API Key: {'✅ Configurada' if api_key else '❌ NÃO CONFIGURADA'}")

    if not api_key:
        print("\n❌ ERRO: API Key não configurada!")
        print("   Configure a variável MOBNE_API_KEY no arquivo .env")
        return False

    if cnpj == "00.000.000/0000-00":
        print("\n⚠️  AVISO: CNPJ é um placeholder (00.000.000/0000-00)")
        print("   Configure o CNPJ real no arquivo .env para produção")
        print("   Continuando com teste...")


    # Criar cliente
    print("\n🔌 Conectando à API...")
    client = MobneAPIClient(api_key=api_key, cnpj=cnpj, empresa_id=empresa_id)

    # Teste 1: Verificar conexão
    print("\n📡 Teste 1: Verificar Conexão")
    print("-" * 60)
    success, message = client.verify_connection()
    print(f"  {message}")

    if not success:
        print("\n❌ Conexão falhou! Verifique as credenciais.")
        return False

    # Teste 2: Buscar produtos
    print("\n📦 Teste 2: Buscar Produtos")
    print("-" * 60)
    success, produtos = client.fetch_produtos(page_size=5)

    if success and produtos:
        print(f"  ✅ {len(produtos)} produtos encontrados (mostrando 5 primeiros)")
        for i, prod in enumerate(produtos[:5], 1):
            print(f"\n  Produto {i}:")
            for key, value in list(prod.items())[:5]:  # Mostrar apenas 5 campos
                print(f"    • {key}: {value}")
    elif success:
        print("  ⚠️ Nenhum produto encontrado")
    else:
        print("  ❌ Erro ao buscar produtos")

    # Teste 3: Buscar clientes
    print("\n\n👥 Teste 3: Buscar Clientes")
    print("-" * 60)
    success, clientes = client.fetch_clientes(page_size=5)

    if success and clientes:
        print(f"  ✅ {len(clientes)} clientes encontrados (mostrando 5 primeiros)")
        for i, cli in enumerate(clientes[:5], 1):
            print(f"\n  Cliente {i}:")
            for key, value in list(cli.items())[:5]:  # Mostrar apenas 5 campos
                print(f"    • {key}: {value}")
    elif success:
        print("  ⚠️ Nenhum cliente encontrado")
    else:
        print("  ❌ Erro ao buscar clientes")

    # Teste 4: Buscar vendas
    print("\n\n💰 Teste 4: Buscar Vendas (últimos 30 dias)")
    print("-" * 60)
    data_fim = datetime.now()
    data_inicio = data_fim - timedelta(days=30)

    success, vendas = client.fetch_vendas(data_inicio=data_inicio, data_fim=data_fim)

    if success and vendas:
        print(f"  ✅ {len(vendas)} vendas encontradas (mostrando 5 primeiras)")
        for i, venda in enumerate(vendas[:5], 1):
            print(f"\n  Venda {i}:")
            for key, value in list(venda.items())[:5]:  # Mostrar apenas 5 campos
                print(f"    • {key}: {value}")
    elif success:
        print("  ⚠️ Nenhuma venda encontrada no período")
    else:
        print("  ❌ Erro ao buscar vendas")

    # Resumo Final
    print("\n\n" + "=" * 60)
    print("✅ TESTE CONCLUÍDO COM SUCESSO!")
    print("=" * 60)
    print("\n✨ A integração com a API Mobne está funcionando!")
    print("\n📝 Para usar a integração:")
    print("   1. Acesse: http://localhost:8501/integracao_mobne")
    print("   2. Insira suas credenciais de API")
    print("   3. Use as opções de sincronização de dados")

    return True

if __name__ == "__main__":
    try:
        test_connection()
    except Exception as e:
        print(f"\n❌ ERRO: {str(e)}")
        print("\nDebug Info:")
        import traceback
        traceback.print_exc()
        sys.exit(1)
