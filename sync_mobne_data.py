#!/usr/bin/env python3
"""
Sincroniza dados do ERP Mobne para arquivos JSON estáticos.

Gera:
  - public/data/vendas_mensais.json  (vendas agrupadas por mês/categoria)
  - public/data/yoy.json             (comparativo ano a ano)

Uso:
  python sync_mobne_data.py                    # sincroniza tudo (Jan/2025 até mês atual)
  python sync_mobne_data.py --mes 2 --ano 2026 # sincroniza apenas Fev/2026
  python sync_mobne_data.py --ano 2025         # sincroniza todos os meses de 2025

Requer: MOBNE_API_KEY como variável de ambiente (ou em .env)
"""

import json
import os
import sys
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# Carregar variáveis de ambiente do .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Se dotenv não está instalado, carregar manualmente
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Importa o cliente Mobne do projeto
from mobne_api import MobneAPIClient, MOBNE_API_KEY, MOBNE_EMPRESA_ID

DATA_DIR = Path(__file__).parent / "public" / "data"
MESES_NOMES = [
    "", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
]


def fetch_vendas_mes_completo(client: MobneAPIClient, mes: int, ano: int, operacao_id: int = None) -> list:
    """
    Busca TODAS as vendas de um mês com paginação automática.
    Retorna lista de pedidos (cada um pode ter múltiplos itens).

    Args:
        operacao_id: Se fornecido, filtra apenas por essa operação (ex: cupom fiscal, nota fiscal)
    """
    data_inicio = datetime(ano, mes, 1)
    if mes == 12:
        data_fim = datetime(ano + 1, 1, 1) - timedelta(days=1)
    else:
        data_fim = datetime(ano, mes + 1, 1) - timedelta(days=1)

    all_vendas = []
    page = 1
    max_pages = 100  # segurança

    while page <= max_pages:
        success, vendas = client.fetch_vendas(
            data_inicio=data_inicio,
            data_fim=data_fim,
            limit=100,
            offset=page - 1,
            operacao_id=operacao_id,
        )

        if not success or not vendas:
            break

        all_vendas.extend(vendas)
        logger.info(f"  Página {page}: {len(vendas)} pedidos (total: {len(all_vendas)})")

        if len(vendas) < 100:
            break
        page += 1

    return all_vendas


def agregar_vendas_por_categoria(vendas_raw: list, mes: int, ano: int) -> list:
    """
    Agrega vendas brutas do Mobne em formato vendas_mensais.json.

    Input:  lista de pedidos Mobne (com PedidoItens dentro)
    Output: lista de dicts agrupados por Categoria, com Periodo, Vlr_Venda, etc.
    """
    categorias = defaultdict(lambda: {
        "Qtde_Venda": 0,
        "Qtde_Documentos": set(),  # IDs de pedidos únicos
        "Vlr_Venda": 0.0,
        "Vlr_Lucro": 0.0,
        "Custo_Total": 0.0,
    })

    for pedido in vendas_raw:
        pedido_id = pedido.get("PedidoVendaId", pedido.get("Id", pedido.get("Numero", id(pedido))))
        itens = pedido.get("PedidoItens", pedido.get("Itens", pedido.get("Items", [])))
        valor_total_pedido = pedido.get("VlrTotal", pedido.get("ValorTotal", pedido.get("ValorFinal", 0))) or 0

        # Se não temos VlrTotal direto, calcular da soma dos itens
        if not valor_total_pedido and itens:
            valor_total_pedido = sum(i.get("VlrLiquidoItem", i.get("VrTotal", 0)) or 0 for i in itens)

        if itens:
            for item in itens:
                # Extrair categoria - tentar vários nomes de campo
                cat = (
                    item.get("Categoria") or
                    item.get("DescricaoGrupo") or
                    item.get("NomeProduto", "") or
                    "Sem Categoria"
                ) or "Sem Categoria"

                qtd = item.get("QtdPedida") or item.get("Quantidade", 1) or 1
                vr_total = item.get("VlrLiquidoItem") or item.get("VrTotal", item.get("ValorTotal", 0)) or 0
                custo = item.get("CustoMedio", item.get("VrCusto", 0)) or 0

                # Calcular lucro: se temos custo, usa ele; senão assume 45% de margem
                if custo:
                    lucro = vr_total - (custo * qtd)
                else:
                    lucro = vr_total * 0.45

                categorias[cat]["Qtde_Venda"] += qtd
                categorias[cat]["Qtde_Documentos"].add(pedido_id)
                categorias[cat]["Vlr_Venda"] += vr_total
                categorias[cat]["Vlr_Lucro"] += lucro
                categorias[cat]["Custo_Total"] += custo * qtd
        else:
            # Pedido sem itens detalhados
            categorias["Geral"]["Qtde_Venda"] += 1
            categorias["Geral"]["Qtde_Documentos"].add(pedido_id)
            categorias["Geral"]["Vlr_Venda"] += valor_total_pedido
            categorias["Geral"]["Vlr_Lucro"] += valor_total_pedido * 0.45
            categorias["Geral"]["Custo_Total"] += valor_total_pedido * 0.55

    periodo = f"{mes:02d}/{ano}"
    resultado = []

    for cat, dados in sorted(categorias.items()):
        vlr_venda = dados["Vlr_Venda"]
        vlr_lucro = dados["Vlr_Lucro"]
        custo_total = dados["Custo_Total"]
        qtde_docs = len(dados["Qtde_Documentos"])
        ticket_medio = vlr_venda / qtde_docs if qtde_docs > 0 else 0
        markdown_pct = (vlr_lucro / vlr_venda * 100) if vlr_venda > 0 else 0
        markup_pct = (vlr_lucro / custo_total * 100) if custo_total > 0 else 0

        resultado.append({
            "Periodo": periodo,
            "Mes": mes,
            "Ano": ano,
            "Categoria": cat,
            "Qtde_Venda": round(dados["Qtde_Venda"], 3),
            "Qtde_Documentos": qtde_docs,
            "Ticket_Medio": round(ticket_medio, 2),
            "Vlr_Venda": round(vlr_venda, 2),
            "Markdown_Pct": round(markdown_pct, 2),
            "Markup_Pct": round(markup_pct, 2),
            "Vlr_Lucro": round(vlr_lucro, 2),
            "Custo_Medio_Liq": round(custo_total, 2),
        })

    return resultado


def gerar_yoy(vendas_mensais: list, ano_ref: int = 2026) -> list:
    """
    Gera dados de comparação Year-over-Year a partir de vendas_mensais.

    Compara ano_ref vs ano_ref-1.
    """
    # Agrupar totais por mês/ano
    totais = defaultdict(lambda: {"receita": 0, "lucro": 0, "cupons": 0, "skus": 0})

    for row in vendas_mensais:
        key = (row["Mes"], row["Ano"])
        totais[key]["receita"] += row["Vlr_Venda"]
        totais[key]["lucro"] += row["Vlr_Lucro"]
        totais[key]["cupons"] += row["Qtde_Documentos"]
        totais[key]["skus"] += 1  # cada categoria conta como 1

    yoy = []
    for mes_num in range(1, 13):
        ant = totais.get((mes_num, ano_ref - 1), {"receita": 0, "lucro": 0, "cupons": 0, "skus": 0})
        atu = totais.get((mes_num, ano_ref), {"receita": 0, "lucro": 0, "cupons": 0, "skus": 0})

        var_receita = 0
        var_lucro = 0
        if ant["receita"] > 0:
            var_receita = ((atu["receita"] - ant["receita"]) / ant["receita"]) * 100
        if ant["lucro"] > 0:
            var_lucro = ((atu["lucro"] - ant["lucro"]) / ant["lucro"]) * 100

        margem_ant = (ant["lucro"] / ant["receita"] * 100) if ant["receita"] > 0 else 0
        margem_atu = (atu["lucro"] / atu["receita"] * 100) if atu["receita"] > 0 else 0

        yoy.append({
            "Mes": MESES_NOMES[mes_num],
            "Mes_Num": mes_num,
            "Receita_2025": round(ant["receita"], 2),
            "Lucro_2025": round(ant["lucro"], 2),
            "Margem_2025": round(margem_ant, 2),
            "Cupons_2025": ant["cupons"],
            "SKUs_2025": ant["skus"],
            "Receita_2026": round(atu["receita"], 2),
            "Lucro_2026": round(atu["lucro"], 2),
            "Margem_2026": round(margem_atu, 2),
            "Cupons_2026": atu["cupons"],
            "Var_Receita_Pct": round(var_receita, 2),
            "Var_Lucro_Pct": round(var_lucro, 2),
        })

    return yoy


def salvar_json(path: Path, data):
    """Salva dados como JSON formatado."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"Salvo: {path} ({len(data)} registros)")


def sync(mes_filtro: int = None, ano_filtro: int = None, operacao_id: int = None):
    """
    Executa a sincronização completa.

    Se mes_filtro e ano_filtro forem fornecidos, sincroniza apenas aquele mês.
    Caso contrário, sincroniza Jan/2025 até o mês atual.

    Args:
        operacao_id: Se fornecido, filtra apenas por essa operação (ex: cupom fiscal, nota fiscal)
    """
    if not MOBNE_API_KEY:
        logger.error("MOBNE_API_KEY não configurado! Configure a variável de ambiente.")
        logger.error("  export MOBNE_API_KEY='sua-chave-aqui'")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    client = MobneAPIClient()
    logger.info(f"Conectando ao Mobne (empresa {client.empresa_id})...")

    # Verificar conexão
    ok, msg = client.verify_connection()
    if not ok:
        logger.error(f"Falha na conexão: {msg}")
        sys.exit(1)
    logger.info(f"Conexão OK: {msg}")

    # Determinar quais meses sincronizar
    agora = datetime.now()
    periodos = []

    if mes_filtro and ano_filtro:
        periodos.append((mes_filtro, ano_filtro))
    elif ano_filtro:
        # Todos os meses do ano especificado
        max_mes = agora.month if ano_filtro == agora.year else 12
        for m in range(1, max_mes + 1):
            periodos.append((m, ano_filtro))
    else:
        # Jan/2025 até mês atual
        for ano in [2025, 2026]:
            max_mes = agora.month if ano == agora.year else 12
            for m in range(1, max_mes + 1):
                periodos.append((m, ano))

    # Carregar dados existentes para merge
    vendas_path = DATA_DIR / "vendas_mensais.json"
    if vendas_path.exists():
        with open(vendas_path, "r", encoding="utf-8") as f:
            vendas_existentes = json.load(f)
    else:
        vendas_existentes = []

    # Indexar dados existentes por periodo
    vendas_por_periodo = {}
    for row in vendas_existentes:
        key = f"{row['Mes']:02d}/{row['Ano']}"
        if key not in vendas_por_periodo:
            vendas_por_periodo[key] = []
        vendas_por_periodo[key].append(row)

    # Sincronizar cada mês
    total_vendas = 0
    for mes, ano in periodos:
        logger.info(f"\n{'='*50}")
        logger.info(f"Sincronizando {MESES_NOMES[mes]}/{ano}...")
        if operacao_id:
            logger.info(f"  (Filtrando por OperacaoId: {operacao_id})")

        vendas_raw = fetch_vendas_mes_completo(client, mes, ano, operacao_id=operacao_id)
        logger.info(f"  → {len(vendas_raw)} pedidos encontrados")

        if vendas_raw:
            agregado = agregar_vendas_por_categoria(vendas_raw, mes, ano)
            vendas_por_periodo[f"{mes:02d}/{ano}"] = agregado
            total_vendas += len(vendas_raw)

            receita = sum(r["Vlr_Venda"] for r in agregado)
            lucro = sum(r["Vlr_Lucro"] for r in agregado)
            logger.info(f"  → Receita: R$ {receita:,.2f} | Lucro: R$ {lucro:,.2f}")
            logger.info(f"  → {len(agregado)} categorias")
        else:
            logger.warning(f"  → Nenhum pedido encontrado para {MESES_NOMES[mes]}/{ano}")

    # Consolidar todos os dados
    todas_vendas = []
    for periodo_rows in vendas_por_periodo.values():
        todas_vendas.extend(periodo_rows)

    # Ordenar por Ano, Mes, Categoria
    todas_vendas.sort(key=lambda r: (r["Ano"], r["Mes"], r["Categoria"]))

    # Salvar vendas_mensais.json
    salvar_json(vendas_path, todas_vendas)

    # Gerar e salvar yoy.json
    yoy = gerar_yoy(todas_vendas, ano_ref=2026)
    salvar_json(DATA_DIR / "yoy.json", yoy)

    # Resumo
    logger.info(f"\n{'='*50}")
    logger.info(f"SINCRONIZAÇÃO COMPLETA!")
    logger.info(f"  Períodos sincronizados: {len(periodos)}")
    logger.info(f"  Total de pedidos processados: {total_vendas}")
    logger.info(f"  Categorias no vendas_mensais.json: {len(todas_vendas)}")
    logger.info(f"  Arquivo: {vendas_path}")
    logger.info(f"  Arquivo: {DATA_DIR / 'yoy.json'}")

    # Mostrar resumo YoY
    logger.info(f"\n--- Resumo YoY ---")
    for row in yoy:
        r25 = row["Receita_2025"]
        r26 = row["Receita_2026"]
        var = row["Var_Receita_Pct"]
        status = "✅" if r26 > 0 else "⬜"
        logger.info(
            f"  {status} {row['Mes']:>10}: "
            f"2025 R$ {r25:>10,.2f} | 2026 R$ {r26:>10,.2f} | Var: {var:>+7.1f}%"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Sincroniza dados do Mobne para JSONs do dashboard"
    )
    parser.add_argument("--mes", type=int, help="Mês específico (1-12)")
    parser.add_argument("--ano", type=int, help="Ano específico (ex: 2025, 2026)")
    parser.add_argument(
        "--operacao",
        type=int,
        help="ID da operação (ex: 1=Cupom Fiscal, 2=Nota Fiscal PDV)"
    )
    args = parser.parse_args()

    sync(mes_filtro=args.mes, ano_filtro=args.ano, operacao_id=args.operacao)


if __name__ == "__main__":
    main()
