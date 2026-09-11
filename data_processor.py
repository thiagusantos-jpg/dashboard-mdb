"""
Módulo de Processamento de Dados para Upload Excel
Valida e transforma dados brutos para estrutura esperada
"""

import pandas as pd
import streamlit as st
from datetime import datetime
from typing import Dict, Tuple, List
import os

class DataProcessor:
    """Processa e valida dados de upload Excel"""

    # Colunas esperadas para cada tipo de dado
    EXPECTED_COLUMNS = {
        'vendas': ['Data', 'Categoria', 'Produto', 'Quantidade', 'Valor_Unitario', 'Vlr_Venda', 'Custo', 'Vlr_Lucro', 'Qtde_Documentos'],
        'produtos': ['Produto', 'Categoria', 'Custo_Medio', 'Preco', 'Estoque'],
        'simples': ['Data', 'Categoria', 'Produto', 'Faturamento']  # Formato simplificado
    }

    @staticmethod
    def validate_data(df: pd.DataFrame, data_type: str = 'vendas') -> Tuple[bool, str]:
        """
        Valida se os dados têm as colunas esperadas

        Args:
            df: DataFrame a validar
            data_type: Tipo de dado ('vendas', 'produtos', 'simples')

        Returns:
            Tupla (sucesso, mensagem)
        """
        if df.empty:
            return False, "❌ Arquivo vazio!"

        expected = DataProcessor.EXPECTED_COLUMNS.get(data_type, [])
        if not expected:
            return False, f"❌ Tipo de dado '{data_type}' não reconhecido"

        # Normalizar nomes de colunas (remover espaços, converter para maiúsculas)
        df.columns = df.columns.str.strip().str.upper()
        expected_upper = [col.upper() for col in expected]

        missing = [col for col in expected_upper if col not in df.columns]
        if missing:
            return False, f"❌ Colunas faltando: {', '.join(missing)}"

        return True, "✅ Dados validados com sucesso"

    @staticmethod
    def process_vendas(df: pd.DataFrame) -> pd.DataFrame:
        """
        Processa dados de vendas

        Args:
            df: DataFrame com dados brutos de vendas

        Returns:
            DataFrame processado
        """
        df = df.copy()
        df.columns = df.columns.str.strip().str.upper()

        # Converter Data para datetime
        try:
            df['DATA'] = pd.to_datetime(df['DATA'])
        except:
            st.warning("⚠️ Coluna 'Data' não pode ser convertida para data")

        # Preencher valores nulos
        df['QUANTIDADE'] = pd.to_numeric(df.get('QUANTIDADE', 0), errors='coerce').fillna(0)
        df['VALOR_UNITARIO'] = pd.to_numeric(df.get('VALOR_UNITARIO', 0), errors='coerce').fillna(0)
        df['VLR_VENDA'] = pd.to_numeric(df['VLR_VENDA'], errors='coerce').fillna(0)
        df['CUSTO'] = pd.to_numeric(df['CUSTO'], errors='coerce').fillna(0)
        df['VLR_LUCRO'] = df['VLR_VENDA'] - df['CUSTO']
        df['QTDE_DOCUMENTOS'] = pd.to_numeric(df.get('QTDE_DOCUMENTOS', 1), errors='coerce').fillna(1)

        # Calcular markup e margem
        df['MARKDOWN_PCT'] = (df['VLR_LUCRO'] / df['VLR_VENDA'] * 100).fillna(0)

        return df

    @staticmethod
    def process_produtos(df: pd.DataFrame) -> pd.DataFrame:
        """
        Processa dados de produtos

        Args:
            df: DataFrame com dados de produtos

        Returns:
            DataFrame processado
        """
        df = df.copy()
        df.columns = df.columns.str.strip().str.upper()

        # Converter colunas numéricas
        df['CUSTO_MEDIO'] = pd.to_numeric(df.get('CUSTO_MEDIO', 0), errors='coerce').fillna(0)
        df['PRECO'] = pd.to_numeric(df.get('PRECO', 0), errors='coerce').fillna(0)
        df['ESTOQUE'] = pd.to_numeric(df.get('ESTOQUE', 0), errors='coerce').fillna(0)

        # Calcular margem
        df['MARGEM'] = ((df['PRECO'] - df['CUSTO_MEDIO']) / df['PRECO'] * 100).fillna(0)

        return df

    @staticmethod
    def aggregate_to_monthly(df: pd.DataFrame, mes: int, ano: int) -> Dict[str, pd.DataFrame]:
        """
        Agrega dados diários em formato mensal para Base_PowerBI.xlsx

        Args:
            df: DataFrame com dados processados
            mes: Mês (1-12)
            ano: Ano

        Returns:
            Dicionário com DataFrames para cada aba
        """
        result = {}

        # Vendas Mensais
        vendas_mensais = df.groupby('CATEGORIA').agg({
            'VLR_VENDA': 'sum',
            'VLR_LUCRO': 'sum',
            'QTDE_DOCUMENTOS': 'sum',
            'MARKDOWN_PCT': 'mean'
        }).reset_index()
        vendas_mensais.columns = ['Categoria', 'Vlr_Venda', 'Vlr_Lucro', 'Qtde_Documentos', 'Markdown_Pct']
        result['fato_vendas_mensais'] = vendas_mensais

        # Vendas Diárias
        vendas_diarias = df[['DATA', 'CATEGORIA', 'VLR_VENDA', 'VLR_LUCRO', 'QTDE_DOCUMENTOS']].copy()
        vendas_diarias.columns = ['Data', 'Categoria', 'Vlr_Venda', 'Vlr_Lucro', 'Qtde_Documentos']
        result['fato_vendas_diarias'] = vendas_diarias

        return result

    @staticmethod
    def save_processed_data(data_dict: Dict[str, pd.DataFrame], filename: str = None) -> Tuple[bool, str]:
        """
        Salva dados processados em Excel

        Args:
            data_dict: Dicionário com DataFrames
            filename: Nome do arquivo (default: data_<timestamp>.xlsx)

        Returns:
            Tupla (sucesso, caminho do arquivo)
        """
        try:
            if filename is None:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"data_upload_{timestamp}.xlsx"

            filepath = os.path.join("/home/user/dubairro/dados_importados", filename)

            # Criar diretório se não existir
            os.makedirs(os.path.dirname(filepath), exist_ok=True)

            with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
                for sheet_name, df in data_dict.items():
                    df.to_excel(writer, sheet_name=sheet_name, index=False)

            return True, filepath
        except Exception as e:
            return False, f"Erro ao salvar dados: {str(e)}"

    @staticmethod
    def detect_format(df: pd.DataFrame) -> str:
        """
        Detecta o formato dos dados (simples, vendas, produtos)

        Args:
            df: DataFrame a analisar

        Returns:
            String indicando o formato detectado
        """
        cols_upper = [col.upper().strip() for col in df.columns]

        # Verificar formato de vendas (mais completo)
        if all(col in cols_upper for col in ['VLR_VENDA', 'CUSTO', 'QTDE_DOCUMENTOS']):
            return 'vendas'

        # Verificar formato de produtos
        if all(col in cols_upper for col in ['CUSTO_MEDIO', 'PRECO', 'ESTOQUE']):
            return 'produtos'

        # Verificar formato simples
        if all(col in cols_upper for col in ['DATA', 'CATEGORIA', 'PRODUTO', 'FATURAMENTO']):
            return 'simples'

        return 'desconhecido'
