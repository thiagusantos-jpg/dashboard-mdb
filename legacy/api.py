"""
API FastAPI para Upload de Dados e Integração Mobne
Compatível com Vercel Deployment
"""

from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import os
import logging
from pathlib import Path

# Configure logging
logger = logging.getLogger(__name__)

from data_processor import DataProcessor

try:
    from mobne_api import (
        MobneAPIClient,
        sync_mobne_to_json,
        MOBNE_EMPRESAS,
        MOBNE_EMPRESA_ID,
        MOBNE_API_KEY,
        _cache_clear,
    )
    MOBNE_AVAILABLE = True
except ImportError:
    MobneAPIClient = None
    MOBNE_AVAILABLE = False

try:
    from analises import (
        carregar_configuracoes,
        salvar_configuracoes,
        obter_status_sincronizacao,
        sincronizacao_completa,
        analises_periodo,
        obter_analise_vendas,
    )
except ImportError:
    carregar_configuracoes = None
    salvar_configuracoes = None
    obter_status_sincronizacao = None
    sincronizacao_completa = None
    analises_periodo = None
    obter_analise_vendas = None

# ============================================================
# APP
# ============================================================

app = FastAPI(title="DuBairro API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# SAÚDE
# ============================================================

@app.get("/api/health")
async def health_check():
    return JSONResponse({
        "status": "ok",
        "service": "dubairro-api",
        "version": "1.1.0",
        "mobne_available": MOBNE_AVAILABLE,
    })


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload_excel(file: UploadFile = File(...)):
    """
    Endpoint para upload de arquivo Excel/CSV.

    Formatos esperados:
    - vendas: Data, Categoria, Produto, Quantidade, Valor_Unitario, Vlr_Venda, Custo, Vlr_Lucro, Qtde_Documentos
    - produtos: Produto, Categoria, Custo_Medio, Preco, Estoque
    - simples: Data, Categoria, Produto, Faturamento
    """
    try:
        if not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
            return JSONResponse(
                {"error": "Apenas arquivos Excel (.xlsx, .xls) ou CSV são aceitos"},
                status_code=400,
            )

        try:
            if file.filename.lower().endswith(".csv"):
                df = pd.read_csv(file.file)
            else:
                df = pd.read_excel(file.file)
        except Exception as e:
            return JSONResponse({"error": f"Erro ao ler arquivo: {str(e)}"}, status_code=400)

        if df.empty:
            return JSONResponse({"error": "Arquivo vazio"}, status_code=400)

        processor = DataProcessor()
        format_type = processor.detect_format(df)

        if format_type == "desconhecido":
            return JSONResponse(
                {"error": "Formato de arquivo não reconhecido. Verifique as colunas."},
                status_code=400,
            )

        is_valid, validation_msg = processor.validate_data(df, format_type)
        if not is_valid:
            return JSONResponse({"error": validation_msg}, status_code=400)

        if format_type == "vendas":
            processed_df = processor.process_vendas(df)
            mes = int(processed_df["DATA"].dt.month.iloc[0])
            ano = int(processed_df["DATA"].dt.year.iloc[0])
        elif format_type == "produtos":
            processed_df = processor.process_produtos(df)
            mes = 1
            ano = 2026
        else:
            processed_df = df.copy()
            mes = 1
            ano = 2026

        data_dict = processor.aggregate_to_monthly(processed_df, mes, ano)
        success, filepath = processor.save_processed_data(data_dict)

        if not success:
            return JSONResponse({"error": filepath}, status_code=500)

        return JSONResponse({
            "status": "success",
            "message": f"Arquivo processado com sucesso: {format_type}",
            "format": format_type,
            "rows_processed": len(processed_df),
            "filepath": filepath,
            "mes": mes,
            "ano": ano,
        })

    except Exception as e:
        return JSONResponse({"error": f"Erro ao processar arquivo: {str(e)}"}, status_code=500)


# ============================================================
# MOBNE — STATUS
# ============================================================

@app.get("/api/mobne/status")
async def mobne_status():
    """Verifica status e configuração da integração Mobne"""
    api_key = os.getenv("MOBNE_API_KEY", "")
    empresas_info = {}

    if MOBNE_AVAILABLE:
        for eid, info in MOBNE_EMPRESAS.items():
            empresas_info[eid] = {
                "cnpj": info["cnpj"],
                "nome": info.get("nome", ""),
            }

    return JSONResponse({
        "status": "configured" if api_key else "not_configured",
        "has_api_key": bool(api_key),
        "mobne_available": MOBNE_AVAILABLE,
        "empresa_padrao": MOBNE_EMPRESA_ID,
        "empresas": empresas_info,
    })


# ============================================================
# MOBNE — SYNC
# ============================================================

@app.post("/api/mobne/sync")
async def sync_mobne(
    action: str = Query("all", description="produtos | clientes | vendas | all"),
    empresa_id: str = Query(None, description="ID da empresa (padrão: empresa principal)"),
    force: bool = Query(False, description="Ignorar cache e forçar nova busca"),
):
    """
    Sincroniza dados com API Mobne e salva em public/data/*.json

    - **all**: sincroniza tudo (produtos + clientes + vendas)
    - **produtos**: apenas produtos
    - **clientes**: apenas clientes
    - **vendas**: apenas vendas (últimos 30 dias)
    """
    if not MOBNE_AVAILABLE:
        return JSONResponse(
            {"error": "MobneAPIClient não disponível. Verifique as dependências."},
            status_code=503,
        )

    api_key = os.getenv("MOBNE_API_KEY", "")
    if not api_key:
        return JSONResponse(
            {"error": "MOBNE_API_KEY não configurado nas variáveis de ambiente"},
            status_code=400,
        )

    eid = str(empresa_id or MOBNE_EMPRESA_ID)

    try:
        if action == "all":
            results = sync_mobne_to_json(empresa_id=eid, force=force)
            total = sum(r.get("count", 0) for r in results.values() if r.get("ok"))
            return JSONResponse({
                "status": "success",
                "empresa_id": eid,
                "action": "all",
                "results": results,
                "total_items": total,
            })

        client = MobneAPIClient(empresa_id=eid)

        if action == "produtos":
            ok, data = client.fetch_produtos()
            label = "Produtos"
        elif action == "clientes":
            ok, data = client.fetch_clientes()
            label = "Clientes"
        elif action == "vendas":
            ok, data = client.fetch_vendas()
            label = "Vendas"
        else:
            return JSONResponse(
                {"error": f"Action '{action}' inválida. Use: all, produtos, clientes, vendas"},
                status_code=400,
            )

        if not ok:
            return JSONResponse(
                {"error": f"Falha ao buscar {label} do Mobne"},
                status_code=502,
            )

        return JSONResponse({
            "status": "success",
            "empresa_id": eid,
            "action": action,
            "label": label,
            "count": len(data),
        })

    except Exception as e:
        return JSONResponse(
            {"error": f"Erro ao sincronizar com Mobne: {str(e)}"},
            status_code=500,
        )


@app.delete("/api/mobne/cache")
async def clear_mobne_cache(empresa_id: str = Query(None)):
    """Limpa o cache em memória do cliente Mobne"""
    if not MOBNE_AVAILABLE:
        return JSONResponse({"error": "Mobne não disponível"}, status_code=503)
    prefix = str(empresa_id) if empresa_id else ""
    _cache_clear(prefix)
    return JSONResponse({"status": "ok", "message": f"Cache limpo (prefixo='{prefix}')"})


@app.get("/api/mobne/empresas")
async def listar_empresas():
    """Lista as empresas configuradas no sistema"""
    if not MOBNE_AVAILABLE:
        return JSONResponse({"error": "Mobne não disponível"}, status_code=503)
    return JSONResponse({
        "empresa_padrao": MOBNE_EMPRESA_ID,
        "empresas": {
            eid: {"cnpj": info["cnpj"], "nome": info.get("nome", "")}
            for eid, info in MOBNE_EMPRESAS.items()
        },
    })


# ============================================================
# MOBNE — VENDAS MENSAIS (Para Dashboard)
# ============================================================

@app.get("/api/mobne/vendas-mensais")
async def mobne_vendas_mensais(
    mes: int = Query(None, ge=1, le=12, description="Mês (1-12)"),
    ano: int = Query(None, ge=2000, le=2100, description="Ano"),
    empresa_id: str = Query(None, description="ID da empresa (padrão: empresa principal)"),
    use_mock: bool = Query(False, description="Usar dados de teste (debug)"),
):
    """
    Retorna dados de vendas mensais formatados para o dashboard

    Query Parameters:
    - mes: 1-12 (obrigatório)
    - ano: 2000-2100 (obrigatório)
    - empresa_id: ID da empresa (opcional, padrão: 218)
    - use_mock: true para usar dados de teste (debug)

    Response:
    {
        "vendas_mensais": [
            {"Data": "2026-02-01", "Categoria": "Alimentos", "Produto": "Arroz", ...},
            ...
        ]
    }
    """
    if not MOBNE_AVAILABLE:
        return JSONResponse({"error": "Mobne não disponível"}, status_code=503)

    api_key = os.getenv("MOBNE_API_KEY", "")
    if not api_key:
        return JSONResponse(
            {"error": "MOBNE_API_KEY não configurado"},
            status_code=400,
        )

    if mes is None or ano is None:
        return JSONResponse(
            {"error": "Parâmetros obrigatórios: mes (1-12) e ano"},
            status_code=400,
        )

    try:
        from datetime import datetime, timedelta

        eid = str(empresa_id or MOBNE_EMPRESA_ID)

        # Buscar vendas do mês
        data_inicio = datetime(ano, mes, 1)
        if mes == 12:
            data_fim = datetime(ano + 1, 1, 1) - timedelta(days=1)
        else:
            data_fim = datetime(ano, mes + 1, 1) - timedelta(days=1)

        client = MobneAPIClient(empresa_id=eid)
        ok, vendas_raw = client.fetch_vendas(data_inicio, data_fim)

        if not ok:
            return JSONResponse(
                {"error": f"Falha ao buscar vendas do Mobne para {mes:02d}/{ano}"},
                status_code=502,
            )

        # Formatar dados para o formato esperado pelo dashboard
        vendas_formatadas = []

        # Se não há dados reais e use_mock=true, usar dados de teste
        if not vendas_raw and use_mock:
            logger.info(f"Usando dados de teste para {mes:02d}/{ano}")
            # Gerar dados de teste realistas
            categorias = ["Alimentos", "Bebidas", "Higiene", "Limpeza", "Congelados"]
            produtos = {
                "Alimentos": ["Arroz 5kg", "Feijão 1kg", "Macarrão", "Pão", "Leite"],
                "Bebidas": ["Suco 1L", "Refrigerante", "Água 1.5L", "Cerveja", "Vinho"],
                "Higiene": ["Xampu", "Sabonete", "Papel Higiênico", "Desodorante"],
                "Limpeza": ["Detergente", "Desinfetante", "Vassoura", "Pano"],
                "Congelados": ["Frango", "Carne", "Peixe", "Alinha Fria"],
            }

            # Criar vendas de teste (10 transações ao longo do mês)
            num_vendas = 10
            for i in range(1, num_vendas + 1):
                dia = (i * 3) % 28 + 1
                data_venda = datetime(ano, mes, dia).strftime("%Y-%m-%d")

                # 2-4 itens por venda
                num_itens = (i % 3) + 2
                for j in range(num_itens):
                    categoria = categorias[j % len(categorias)]
                    produto = produtos[categoria][(i + j) % len(produtos[categoria])]

                    quantidade = (j + 1) * 2
                    preco_unitario = 10 + (j * 5)
                    valor_venda = quantidade * preco_unitario
                    custo = valor_venda * 0.6
                    lucro = valor_venda - custo

                    vendas_formatadas.append({
                        "Data": data_venda,
                        "Categoria": categoria,
                        "Produto": produto,
                        "Quantidade": quantidade,
                        "Valor_Unitario": round(preco_unitario, 2),
                        "Vlr_Venda": round(valor_venda, 2),
                        "Custo": round(custo, 2),
                        "Vlr_Lucro": round(lucro, 2),
                    })
        else:
            # Usar dados reais do Mobne
            for venda in vendas_raw:
                # Extrai informações da venda do Mobne
                data_venda = venda.get("DataEmissao", venda.get("Data", ""))

                # Processa itens da venda
                itens = venda.get("Itens", venda.get("Items", []))
                valor_total = venda.get("ValorTotal", venda.get("ValorFinal", 0))

                if itens:
                    # Se tem itens, cria um registro por item
                    for item in itens:
                        vendas_formatadas.append({
                            "Data": data_venda,
                            "Categoria": item.get("Categoria", "Sem Categoria"),
                            "Produto": item.get("Descricao", item.get("NomeProduto", "Produto")),
                            "Quantidade": item.get("Quantidade", 1),
                            "Valor_Unitario": item.get("VrUnitario", 0),
                            "Vlr_Venda": item.get("VrTotal", 0),
                            "Custo": item.get("CustoMedio", 0),
                            "Vlr_Lucro": item.get("VrTotal", 0) - item.get("CustoMedio", 0),
                        })
                else:
                    # Se não tem itens, cria um registro único
                    vendas_formatadas.append({
                        "Data": data_venda,
                        "Categoria": "Geral",
                        "Produto": f"Venda #{venda.get('Numero', 'N/A')}",
                        "Quantidade": 1,
                        "Valor_Unitario": valor_total,
                        "Vlr_Venda": valor_total,
                        "Custo": 0,
                        "Vlr_Lucro": valor_total,
                    })

        return JSONResponse({
            "status": "success",
            "mes": mes,
            "ano": ano,
            "empresa_id": eid,
            "total_vendas": len(vendas_raw),
            "total_itens": len(vendas_formatadas),
            "using_mock_data": not vendas_raw and use_mock,
            "vendas_mensais": vendas_formatadas,
        })

    except Exception as e:
        return JSONResponse(
            {"error": f"Erro ao buscar vendas do Mobne: {str(e)}"},
            status_code=500,
        )


@app.get("/api/mobne/yoy")
async def mobne_yoy(
    ano_referencia: int = Query(2026, ge=2000, le=2100, description="Ano de referência"),
    empresa_id: str = Query(None, description="ID da empresa (padrão: empresa principal)"),
):
    """
    Retorna dados de comparação ano a ano (YoY) para cada mês

    Query Parameters:
    - ano_referencia: Ano para comparação (padrão: 2026)
    - empresa_id: ID da empresa (opcional)

    Response:
    {
        "yoy": [
            {
                "Mes": "Janeiro",
                "Mes_Num": 1,
                "Receita_2025": 50000.00,
                "Receita_2026": 73244.04,
                "Variacao_Percentual": 46.49,
                ...
            },
            ...
        ]
    }
    """
    # Tentar carregar dados estáticos como fallback
    static_yoy_path = Path(__file__).parent / "public" / "data" / "yoy.json"

    if not MOBNE_AVAILABLE:
        # Fallback para dados estáticos
        if static_yoy_path.exists():
            import json
            with open(static_yoy_path, 'r') as f:
                yoy_data = json.load(f)
            return JSONResponse({
                "status": "success",
                "ano_referencia": ano_referencia,
                "enterprise_id": empresa_id or MOBNE_EMPRESA_ID,
                "source": "static",
                "yoy": yoy_data,
            })
        return JSONResponse({"error": "Mobne não disponível e dados estáticos não encontrados"}, status_code=503)

    api_key = os.getenv("MOBNE_API_KEY", "")
    if not api_key:
        # Fallback para dados estáticos se API Key não configurado
        if static_yoy_path.exists():
            import json
            with open(static_yoy_path, 'r') as f:
                yoy_data = json.load(f)
            return JSONResponse({
                "status": "success",
                "ano_referencia": ano_referencia,
                "empresa_id": empresa_id or MOBNE_EMPRESA_ID,
                "source": "static",
                "yoy": yoy_data,
            })
        return JSONResponse(
            {"error": "MOBNE_API_KEY não configurado e dados estáticos não encontrados"},
            status_code=400,
        )

    try:
        from datetime import datetime, timedelta

        eid = str(empresa_id or MOBNE_EMPRESA_ID)
        client = MobneAPIClient(empresa_id=eid)

        meses = [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
        ]

        yoy_data = []

        for mes_num in range(1, 13):
            # Buscar dados do ano anterior
            data_inicio_2025 = datetime(ano_referencia - 1, mes_num, 1)
            if mes_num == 12:
                data_fim_2025 = datetime(ano_referencia, 1, 1) - timedelta(days=1)
            else:
                data_fim_2025 = datetime(ano_referencia - 1, mes_num + 1, 1) - timedelta(days=1)

            ok_2025, vendas_2025 = client.fetch_vendas(data_inicio_2025, data_fim_2025)
            receita_2025 = sum(v.get("ValorTotal", v.get("ValorFinal", 0)) for v in vendas_2025) if ok_2025 else 0

            # Buscar dados do ano atual
            data_inicio_atual = datetime(ano_referencia, mes_num, 1)
            if mes_num == 12:
                data_fim_atual = datetime(ano_referencia + 1, 1, 1) - timedelta(days=1)
            else:
                data_fim_atual = datetime(ano_referencia, mes_num + 1, 1) - timedelta(days=1)

            ok_atual, vendas_atual = client.fetch_vendas(data_inicio_atual, data_fim_atual)
            receita_atual = sum(v.get("ValorTotal", v.get("ValorFinal", 0)) for v in vendas_atual) if ok_atual else 0

            # Calcular variação
            if receita_2025 > 0:
                variacao = ((receita_atual - receita_2025) / receita_2025) * 100
            else:
                variacao = 100 if receita_atual > 0 else 0

            yoy_data.append({
                "Mes": meses[mes_num - 1],
                "Mes_Num": mes_num,
                f"Receita_{ano_referencia - 1}": round(receita_2025, 2),
                f"Receita_{ano_referencia}": round(receita_atual, 2),
                "Variacao_Percentual": round(variacao, 2),
            })

        return JSONResponse({
            "status": "success",
            "ano_referencia": ano_referencia,
            "empresa_id": eid,
            "source": "mobne",
            "yoy": yoy_data,
        })

    except Exception as e:
        # Fallback para dados estáticos quando API falha
        if static_yoy_path.exists():
            import json
            with open(static_yoy_path, 'r') as f:
                yoy_data = json.load(f)
            return JSONResponse({
                "status": "success",
                "ano_referencia": ano_referencia,
                "empresa_id": empresa_id or MOBNE_EMPRESA_ID,
                "source": "static",
                "error": f"Mobne falhou: {str(e)}. Usando dados estáticos.",
                "yoy": yoy_data,
            })

        return JSONResponse(
            {"error": f"Erro ao buscar dados YoY do Mobne: {str(e)}"},
            status_code=500,
        )


# ============================================================
# DASHBOARD — STATUS DE SINCRONIZAÇÃO
# ============================================================

@app.get("/api/dashboard/status/sync")
async def dashboard_status_sync():
    """
    Retorna o status completo da sincronização de todos os dados

    Response:
    {
        "sincronizacao_completa": true/false,
        "todos_dados_sincronizados": true/false,
        "detalhes": {
            "produtos": {"ultima_sync": "...", "total": 123},
            "clientes": {...},
            "vendas": {...}
        }
    }
    """
    if not obter_status_sincronizacao:
        return JSONResponse({"error": "Módulo de análises não disponível"}, status_code=503)

    status = obter_status_sincronizacao()
    completo = sincronizacao_completa()

    return JSONResponse({
        "sincronizacao_completa": completo,
        "todos_dados_sincronizados": completo,
        "detalhes": {
            "produtos": status.get("produtos"),
            "clientes": status.get("clientes"),
            "vendas": status.get("vendas"),
        },
        "timestamp": status.get("timestamp"),
    })


# ============================================================
# DASHBOARD — ANÁLISES POR PERÍODO
# ============================================================

@app.get("/api/dashboard/analises/periodo")
async def dashboard_analises_periodo(
    mes: int = Query(None, ge=1, le=12, description="Mês (1-12)"),
    ano: int = Query(None, ge=2000, le=2100, description="Ano"),
):
    """
    Retorna análises completas para um período específico

    Query Parameters:
    - mes: 1-12 (obrigatório)
    - ano: 2000-2100 (obrigatório)

    Response inclui:
    - Métricas: total vendas, valor, ticket médio
    - Comparativo: vs. mês anterior (variação %)
    - Gráficos: vendas por dia, faturamento por dia
    - Financeiro: lucro bruto, lucro líquido, ponto de equilíbrio
    """
    if not analises_periodo:
        return JSONResponse({"error": "Módulo de análises não disponível"}, status_code=503)

    if mes is None or ano is None:
        return JSONResponse(
            {"error": "Parâmetros obrigatórios: mes (1-12) e ano"},
            status_code=400,
        )

    result = analises_periodo(mes, ano)

    if "error" in result:
        return JSONResponse(result, status_code=400)

    return JSONResponse(result)


# ============================================================
# ANÁLISE DE VENDAS
# ============================================================

@app.get("/api/analise-vendas")
async def analise_vendas(
    categoria: str = Query(None, description="Filtrar por categoria"),
    curva: str = Query(None, description="Filtrar por curva (A ou B/C)"),
):
    """
    Retorna dados de Análise de Vendas com Produto, Categoria, Curva ABC, Valor Liquido

    Query Parameters:
    - categoria: Filtrar por categoria (opcional)
    - curva: Filtrar por curva A ou B/C (opcional)

    Response:
    {
        "status": "success",
        "total_registros": 150,
        "total_valor_liquido": 45230.50,
        "dados": [
            {
                "Produto": "BANANA PRATA (KG)",
                "Categoria": "Frutas",
                "Curva_ABC": "A",
                "Valor_Liquido": 953.62
            },
            ...
        ]
    }
    """
    if not obter_analise_vendas:
        return JSONResponse({"error": "Módulo de análises não disponível"}, status_code=503)

    result = obter_analise_vendas(filtro_categoria=categoria, filtro_curva=curva)

    if result.get("status") == "error":
        return JSONResponse(result, status_code=500)

    return JSONResponse(result)


# ============================================================
# DASHBOARD — CONFIGURAÇÕES
# ============================================================

@app.get("/api/dashboard/configuracoes")
async def get_configuracoes():
    """
    Retorna as configurações atuais do dashboard

    Response:
    {
        "custo_fixo_mensal": 16913.46,
        "margem_meta": 15.0,
        "faturamento_meta": 100000.0,
        "custos_detalhes": {...},
        "data_atualizacao": "..."
    }
    """
    if not carregar_configuracoes:
        return JSONResponse({"error": "Módulo de análises não disponível"}, status_code=503)

    config = carregar_configuracoes()
    return JSONResponse(config)


@app.post("/api/dashboard/configuracoes")
async def post_configuracoes(body: dict):
    """
    Atualiza as configurações do dashboard

    Body esperado:
    {
        "custo_fixo_mensal": 16913.46,
        "margem_meta": 15.0,
        "faturamento_meta": 100000.0,
        "custos_detalhes": {
            "aluguel": 0.0,
            "salarios": 0.0,
            "utilidades": 0.0,
            "manutencao": 0.0
        }
    }
    """
    if not salvar_configuracoes:
        return JSONResponse({"error": "Módulo de análises não disponível"}, status_code=503)

    if not body:
        return JSONResponse({"error": "Body vazio"}, status_code=400)

    # Validação básica
    if "custo_fixo_mensal" in body:
        if not isinstance(body["custo_fixo_mensal"], (int, float)):
            return JSONResponse({"error": "custo_fixo_mensal deve ser um número"}, status_code=400)

    if "margem_meta" in body:
        margem = body["margem_meta"]
        if not isinstance(margem, (int, float)) or not (0 <= margem <= 100):
            return JSONResponse(
                {"error": "margem_meta deve estar entre 0 e 100"},
                status_code=400,
            )

    # Salvar
    config = carregar_configuracoes()
    config.update(body)

    if salvar_configuracoes(config):
        return JSONResponse({
            "status": "success",
            "message": "Configurações atualizadas",
            "config": config,
        })
    else:
        return JSONResponse(
            {"error": "Erro ao salvar configurações"},
            status_code=500,
        )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():
    return JSONResponse({
        "name": "DuBairro API",
        "version": "1.3.0",
        "status": "ok",
        "endpoints": {
            "health": "GET /api/health",
            "upload": "POST /api/upload",
            "analise_vendas": "GET /api/analise-vendas?categoria=Frutas&curva=A",
            "mobne": {
                "status": "GET /api/mobne/status",
                "sync": "POST /api/mobne/sync?action=all|produtos|clientes|vendas",
                "cache": "DELETE /api/mobne/cache",
                "empresas": "GET /api/mobne/empresas",
            },
            "dashboard": {
                "status_sync": "GET /api/dashboard/status/sync",
                "analises_periodo": "GET /api/dashboard/analises/periodo?mes=1&ano=2026",
                "configuracoes_get": "GET /api/dashboard/configuracoes",
                "configuracoes_post": "POST /api/dashboard/configuracoes",
            },
        },
    })
