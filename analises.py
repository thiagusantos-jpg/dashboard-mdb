"""
Módulo de Análises e Processamento de Dados
Para os endpoints do Dashboard
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "public" / "data"
CONFIG_FILE = DATA_DIR / "configuracoes.json"


# ============================================================
# CONFIGURAÇÕES
# ============================================================

def carregar_configuracoes() -> dict:
    """Carrega configurações salvas"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Erro ao carregar configurações: {e}")

    # Padrão
    return {
        "custo_fixo_mensal": 16913.46,
        "margem_meta": 15.0,
        "faturamento_meta": 100000.0,
        "custos_detalhes": {
            "aluguel": 0.0,
            "salarios": 0.0,
            "utilidades": 0.0,
            "manutencao": 0.0,
        },
        "data_atualizacao": datetime.now().isoformat(),
    }


def salvar_configuracoes(config: dict) -> bool:
    """Salva configurações em arquivo JSON"""
    try:
        config["data_atualizacao"] = datetime.now().isoformat()
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar configurações: {e}")
        return False


# ============================================================
# STATUS DE SINCRONIZAÇÃO
# ============================================================

def carregar_status_arquivo(caminho: Path) -> Optional[dict]:
    """Carrega informações de sincronização de um arquivo JSON"""
    if not caminho.exists():
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "ultima_sync": data.get("ultima_sync", "Nunca"),
            "total": data.get("total", 0),
        }
    except Exception as e:
        logger.error(f"Erro ao carregar status: {e}")
        return None


def obter_status_sincronizacao() -> dict:
    """Retorna status de todas as sincronizações"""
    return {
        "produtos": carregar_status_arquivo(DATA_DIR / "produtos.json"),
        "clientes": carregar_status_arquivo(DATA_DIR / "clientes.json"),
        "vendas": carregar_status_arquivo(DATA_DIR / "vendas_mobne.json"),
        "timestamp": datetime.now().isoformat(),
    }


def sincronizacao_completa() -> bool:
    """Verifica se TODAS as sincronizações foram feitas com sucesso"""
    status = obter_status_sincronizacao()
    return (
        status.get("produtos") is not None and status["produtos"].get("total", 0) > 0
        and status.get("clientes") is not None and status["clientes"].get("total", 0) > 0
        and status.get("vendas") is not None and status["vendas"].get("total", 0) > 0
    )


# ============================================================
# ANÁLISES POR PERÍODO
# ============================================================

def carregar_vendas() -> List[dict]:
    """Carrega dados de vendas do JSON"""
    try:
        caminho = DATA_DIR / "vendas_mobne.json"
        if caminho.exists():
            with open(caminho) as f:
                data = json.load(f)
            return data.get("vendas", [])
    except Exception as e:
        logger.error(f"Erro ao carregar vendas: {e}")
    return []


def carregar_produtos() -> List[dict]:
    """Carrega dados de produtos do JSON"""
    try:
        caminho = DATA_DIR / "produtos.json"
        if caminho.exists():
            with open(caminho) as f:
                data = json.load(f)
            return data.get("produtos", [])
    except Exception as e:
        logger.error(f"Erro ao carregar produtos: {e}")
    return []


def filtrar_vendas_por_periodo(vendas: List[dict], mes: int, ano: int) -> List[dict]:
    """Filtra vendas por mês e ano"""
    filtradas = []

    for venda in vendas:
        data_str = venda.get("DtaEmissao", "")
        if data_str:
            try:
                data = datetime.fromisoformat(data_str.replace("Z", "+00:00"))
                if data.month == mes and data.year == ano:
                    filtradas.append(venda)
            except Exception as e:
                logger.debug(f"Erro ao parsear data: {e}")

    return filtradas


def calcular_metricas(vendas: List[dict]) -> dict:
    """Calcula métricas para um conjunto de vendas"""
    if not vendas:
        return {
            "total_vendas": 0,
            "valor_total": 0.0,
            "ticket_medio": 0.0,
            "items": 0,
        }

    total_vendas = len(vendas)
    valor_total = sum(v.get("Valor", 0) for v in vendas if isinstance(v.get("Valor"), (int, float)))
    items = sum(len(v.get("PedidoItens", [])) for v in vendas)
    ticket_medio = valor_total / total_vendas if total_vendas > 0 else 0

    return {
        "total_vendas": total_vendas,
        "valor_total": valor_total,
        "ticket_medio": ticket_medio,
        "items": items,
    }


def obter_vendas_por_dia(vendas: List[dict]) -> Dict[str, int]:
    """Agrupa vendas por dia"""
    vendas_por_dia = {}

    for venda in vendas:
        data_str = venda.get("DtaEmissao", "")
        if data_str:
            try:
                data = datetime.fromisoformat(data_str.replace("Z", "+00:00"))
                dia = data.strftime("%Y-%m-%d")
                vendas_por_dia[dia] = vendas_por_dia.get(dia, 0) + 1
            except Exception as e:
                logger.debug(f"Erro ao agrupar por dia: {e}")

    return vendas_por_dia


def obter_faturamento_por_dia(vendas: List[dict]) -> Dict[str, float]:
    """Agrupa faturamento por dia"""
    faturamento_por_dia = {}

    for venda in vendas:
        data_str = venda.get("DtaEmissao", "")
        valor = venda.get("Valor", 0) or 0
        if data_str:
            try:
                data = datetime.fromisoformat(data_str.replace("Z", "+00:00"))
                dia = data.strftime("%Y-%m-%d")
                faturamento_por_dia[dia] = faturamento_por_dia.get(dia, 0) + valor
            except Exception as e:
                logger.debug(f"Erro ao agrupar faturamento: {e}")

    return faturamento_por_dia


def analises_periodo(mes: int, ano: int) -> dict:
    """Retorna análises completas para um período específico"""
    # Validar entrada
    if not (1 <= mes <= 12):
        return {"error": "Mês inválido (1-12)"}
    if ano < 2000 or ano > 2100:
        return {"error": "Ano inválido"}

    # Carregar dados
    vendas_todas = carregar_vendas()
    produtos_todos = carregar_produtos()

    # Filtrar por período
    vendas_filtradas = filtrar_vendas_por_periodo(vendas_todas, mes, ano)

    # Calcular métricas
    metricas = calcular_metricas(vendas_filtradas)

    # Vendas por dia
    vendas_por_dia = obter_vendas_por_dia(vendas_filtradas)
    faturamento_por_dia = obter_faturamento_por_dia(vendas_filtradas)

    # Período anterior para comparativo
    if mes == 1:
        mes_anterior = 12
        ano_anterior = ano - 1
    else:
        mes_anterior = mes - 1
        ano_anterior = ano

    vendas_anterior = filtrar_vendas_por_periodo(vendas_todas, mes_anterior, ano_anterior)
    metricas_anterior = calcular_metricas(vendas_anterior)

    # Variação
    var_vendas = 0
    var_fatura = 0

    if metricas_anterior["total_vendas"] > 0:
        var_vendas = ((metricas["total_vendas"] - metricas_anterior["total_vendas"])
                      / metricas_anterior["total_vendas"] * 100)

    if metricas_anterior["valor_total"] > 0:
        var_fatura = ((metricas["valor_total"] - metricas_anterior["valor_total"])
                      / metricas_anterior["valor_total"] * 100)

    # Carregar configurações para análises adicionais
    config = carregar_configuracoes()
    custo_fixo = config.get("custo_fixo_mensal", 16913.46)
    margem_meta = config.get("margem_meta", 15.0) / 100

    # Calcular lucro líquido
    lucro_bruto = metricas["valor_total"] * margem_meta
    lucro_liquido = lucro_bruto - custo_fixo

    # Ponto de equilíbrio
    if margem_meta > 0:
        ponto_equilibrio = custo_fixo / margem_meta
    else:
        ponto_equilibrio = 0

    return {
        "periodo": {"mes": mes, "ano": ano},
        "metricas": metricas,
        "comparativo": {
            "mes_anterior": mes_anterior,
            "ano_anterior": ano_anterior,
            "metricas_anterior": metricas_anterior,
            "variacao_vendas_pct": round(var_vendas, 2),
            "variacao_fatura_pct": round(var_fatura, 2),
        },
        "vendas_por_dia": vendas_por_dia,
        "faturamento_por_dia": faturamento_por_dia,
        "financeiro": {
            "lucro_bruto": round(lucro_bruto, 2),
            "lucro_liquido": round(lucro_liquido, 2),
            "ponto_equilibrio": round(ponto_equilibrio, 2),
            "custo_fixo": custo_fixo,
        },
        "total_produtos": len(produtos_todos),
        "timestamp": datetime.now().isoformat(),
    }


# ============================================================
# ANÁLISE DE VENDAS (Para relatório de Análise de Vendas)
# ============================================================

def obter_analise_vendas(
    filtro_categoria: Optional[str] = None,
    filtro_curva: Optional[str] = None,
) -> dict:
    """
    Retorna dados de Análise de Vendas com Produto, Categoria, Curva ABC, Valor Liquido

    Args:
        filtro_categoria: Filtrar por categoria (opcional)
        filtro_curva: Filtrar por curva (A ou B/C) (opcional)

    Returns:
        dict com lista de produtos e totalizações
    """
    try:
        # Carregar dados de produtos
        caminho_produtos = DATA_DIR / "produtos.json"
        caminho_categoria_map = DATA_DIR / "categoria_mapping.json"

        produtos_dados = []
        categoria_map = {}

        # Carregar produtos
        if caminho_produtos.exists():
            with open(caminho_produtos) as f:
                produtos_dados = json.load(f)

        # Carregar mapeamento de categoria
        if caminho_categoria_map.exists():
            try:
                with open(caminho_categoria_map) as f:
                    categoria_map = json.load(f)
            except Exception as e:
                logger.debug(f"Erro ao carregar categoria_mapping.json: {e}")

        # Processar dados
        analise_dados = []
        total_valor_liquido = 0.0
        categorias_unicas = set()

        for produto in produtos_dados:
            nome_produto = produto.get("Produto", "")
            curva = produto.get("Curva", "B/C")
            valor_liquido = produto.get("Lucro_Total", 0)
            categoria = categoria_map.get(nome_produto, "Outros")

            # Aplicar filtros
            if filtro_categoria and categoria != filtro_categoria:
                continue
            if filtro_curva and curva != filtro_curva:
                continue

            analise_dados.append({
                "Produto": nome_produto,
                "Categoria": categoria,
                "Curva_ABC": curva,
                "Valor_Liquido": round(valor_liquido, 2),
            })

            total_valor_liquido += valor_liquido
            categorias_unicas.add(categoria)

        # Ordenar por valor líquido (decrescente)
        analise_dados.sort(key=lambda x: -x["Valor_Liquido"])

        return {
            "status": "success",
            "total_registros": len(analise_dados),
            "total_valor_liquido": round(total_valor_liquido, 2),
            "categorias_disponiveis": sorted(list(categorias_unicas)),
            "filtros_aplicados": {
                "categoria": filtro_categoria,
                "curva": filtro_curva,
            },
            "dados": analise_dados,
            "timestamp": datetime.now().isoformat(),
        }

    except Exception as e:
        logger.error(f"Erro ao obter análise de vendas: {e}")
        return {
            "status": "error",
            "error": str(e),
            "dados": [],
        }
