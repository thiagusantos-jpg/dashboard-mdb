"""
Módulo de Integração com ERP Mobne
Sincroniza dados entre Mercado duBairro e ERP Mobne via API REST
"""

import requests
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging
import os
import json
import time
from functools import wraps
from pathlib import Path

# Configuração de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURAÇÕES DA API MOBNE
# ============================================================

MOBNE_API_BASE_URL = os.getenv("MOBNE_API_URL", "https://apiexternal.mobne.com.br")
MOBNE_API_TIMEOUT = int(os.getenv("MOBNE_API_TIMEOUT", "30"))
MOBNE_API_KEY = os.getenv("MOBNE_API_KEY", "")

# Multi-empresa: dict com empresaId -> {cnpj, nome}
MOBNE_EMPRESAS = {
    "218": {
        "cnpj": os.getenv("MOBNE_EMPRESA1_CNPJ", "52.104.353/0001-35"),
        "nome": "MERCADO DUBAIRRO LTDA – ME (Matriz)",
    }
}

# Adiciona empresa 2 se configurada
_empresa2_id = os.getenv("MOBNE_EMPRESA2_ID", "")
if _empresa2_id:
    MOBNE_EMPRESAS[_empresa2_id] = {
        "cnpj": os.getenv("MOBNE_EMPRESA2_CNPJ", "52.104.353/0002-16"),
        "nome": "MERCADO DUBAIRRO LTDA (Filial)",
    }

# Empresa padrão
MOBNE_EMPRESA_ID = os.getenv("MOBNE_EMPRESA_ID", "218")
MOBNE_CNPJ = os.getenv("MOBNE_CNPJ", "52.104.353/0001-35")

# Cache em memória
_cache: Dict[str, Dict] = {}
CACHE_TTL = 300  # 5 minutos


def _cache_get(key: str):
    """Retorna valor do cache se ainda válido"""
    if key in _cache:
        entry = _cache[key]
        if time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
        del _cache[key]
    return None


def _cache_set(key: str, data):
    """Salva valor no cache com timestamp"""
    _cache[key] = {"data": data, "ts": time.time()}


def _cache_clear(prefix: str = ""):
    """Limpa cache (por prefixo opcional)"""
    keys = [k for k in _cache if k.startswith(prefix)]
    for k in keys:
        del _cache[k]


# ============================================================
# CLIENTE DA API
# ============================================================

class MobneAPIClient:
    """Cliente para comunicação com API Mobne"""

    def __init__(
        self,
        api_key: str = None,
        cnpj: str = None,
        base_url: str = None,
        empresa_id: str = None,
    ):
        self.api_key = api_key or MOBNE_API_KEY
        self.cnpj = cnpj or MOBNE_CNPJ
        self.empresa_id = str(empresa_id or MOBNE_EMPRESA_ID)
        self.base_url = base_url or MOBNE_API_BASE_URL
        self.session = requests.Session()
        self._setup_headers()
        self.last_sync = None

    def _setup_headers(self) -> None:
        """Configura headers de autenticação conforme especificação Mobne"""
        self.session.headers.update({
            "Authorization": f"ApiKey {self.api_key}",
            "Content-Type": "application/json",
            "empresaId": self.empresa_id,
            "Accept": "application/json",
            "User-Agent": "Mercado-duBairro/1.0",
        })

    def _make_request(self, method: str, endpoint: str, use_cache: bool = True, **kwargs) -> Tuple[bool, Dict]:
        """
        Realiza requisição HTTP com cache e tratamento de erro

        Args:
            method: GET, POST, PUT, DELETE
            endpoint: Endpoint da API (sem base URL)
            use_cache: Se True, tenta retornar resultado em cache para GETs
        """
        url = f"{self.base_url}{endpoint}"
        kwargs.setdefault("timeout", MOBNE_API_TIMEOUT)

        # Cache apenas para GET
        cache_key = f"{self.empresa_id}:{method}:{endpoint}"
        if method == "GET" and use_cache:
            cached = _cache_get(cache_key)
            if cached is not None:
                logger.info(f"[CACHE HIT] {endpoint}")
                return True, cached

        try:
            response = self.session.request(method, url, **kwargs)
            response.raise_for_status()
            data = response.json()

            if method == "GET" and use_cache:
                _cache_set(cache_key, data)

            return True, data

        except requests.exceptions.ConnectionError:
            msg = f"Erro de conexão com Mobne API: {url}"
            logger.error(msg)
            return False, {"error": msg}
        except requests.exceptions.Timeout:
            msg = "Timeout na requisição para Mobne API"
            logger.error(msg)
            return False, {"error": msg}
        except requests.exceptions.HTTPError as e:
            msg = f"Erro HTTP {response.status_code}: {response.text}"
            logger.error(msg)
            return False, {"error": msg}
        except Exception as e:
            msg = f"Erro ao comunicar com Mobne API: {str(e)}"
            logger.error(msg)
            return False, {"error": msg}

    def verify_connection(self) -> Tuple[bool, str]:
        """Verifica se a conexão com a API está funcionando"""
        success, response = self._make_request(
            "GET",
            "/api/v1/Produto/consulta-cadastro-produto?PageSize=1&PageNumber=1",
            use_cache=False,
        )
        if success:
            total = response.get("Data", {}).get("Paging", {}).get("TotalItems", 0)
            return True, f"Conectado ao Mobne — {total} produtos disponíveis"
        return False, f"Erro ao conectar: {response.get('error', 'Erro desconhecido')}"

    def fetch_produtos(self, page_size: int = 100, page_number: int = 1) -> Tuple[bool, List[Dict]]:
        """Busca lista de produtos do Mobne"""
        success, response = self._make_request(
            "GET",
            f"/api/v1/Produto/consulta-cadastro-produto?PageSize={page_size}&PageNumber={page_number}",
        )
        if success:
            products = response.get("Data", {}).get("Items", response.get("data", []))
            logger.info(f"Buscados {len(products)} produtos do Mobne (empresa {self.empresa_id})")
            return True, products
        return False, []

    def fetch_produtos_todos(self) -> Tuple[bool, List[Dict]]:
        """Busca TODOS os produtos paginando automaticamente"""
        all_products = []
        page = 1
        while True:
            success, response = self._make_request(
                "GET",
                f"/api/v1/Produto/consulta-cadastro-produto?PageSize=100&PageNumber={page}",
                use_cache=False,
            )
            if not success:
                break
            paging = response.get("Data", {}).get("Paging", {})
            items = response.get("Data", {}).get("Items", [])
            all_products.extend(items)
            if page >= paging.get("TotalPages", 1):
                break
            page += 1
        logger.info(f"Total: {len(all_products)} produtos buscados (empresa {self.empresa_id})")
        return len(all_products) > 0, all_products

    def fetch_clientes(self, page_size: int = 100, page_number: int = 1) -> Tuple[bool, List[Dict]]:
        """Busca lista de clientes do Mobne"""
        success, response = self._make_request(
            "GET",
            f"/api/v1/Pessoa/consulta-cadastro-pessoa?Filter.Tipo=C&PageSize={page_size}&PageNumber={page_number}",
        )
        if success:
            clients = response.get("Data", {}).get("Items", [])
            logger.info(f"Buscados {len(clients)} clientes do Mobne (empresa {self.empresa_id})")
            return True, clients
        return False, []

    def fetch_vendas(
        self,
        data_inicio: datetime = None,
        data_fim: datetime = None,
        limit: int = 100,
        offset: int = 0,
        situacao: str = "F",
        operacao_id: int = None,
    ) -> Tuple[bool, List[Dict]]:
        """
        Busca vendas (pedidos) do Mobne dentro de um período

        Args:
            data_inicio: Data de início da busca
            data_fim: Data de fim da busca
            limit: Quantidade máxima de registros
            offset: Deslocamento para paginação
            situacao: F=Faturado, C=Cancelado, A=Aberto
            operacao_id: ID da operação (para filtrar cupom/nota fiscal)
        """
        if data_fim is None:
            data_fim = datetime.now()
        if data_inicio is None:
            data_inicio = data_fim - timedelta(days=30)

        data_inicio_str = data_inicio.strftime("%Y-%m-%dT00:00:00.000")
        data_fim_str = data_fim.strftime("%Y-%m-%dT23:59:59.999")

        endpoint = (
            f"/api/v1/PedidoVenda/consulta-pedido-venda"
            f"?Filter.DataEmissaoDe={data_inicio_str}"
            f"&Filter.DataEmissaoAte={data_fim_str}"
            f"&Filter.Situacao={situacao}"
        )

        # Adicionar filtro de operação se fornecido
        if operacao_id is not None:
            endpoint += f"&Filter.OperacaoId={operacao_id}"

        endpoint += f"&PageSize={limit}&PageNumber={offset + 1}"

        success, response = self._make_request("GET", endpoint)
        if success:
            vendas = response.get("Data", {}).get("Items", [])
            logger.info(f"Buscadas {len(vendas)} vendas do Mobne (empresa {self.empresa_id})")
            return True, vendas
        return False, []

    def fetch_analise_vendas(
        self,
        data_inicio: datetime = None,
        data_fim: datetime = None,
    ) -> Tuple[bool, List[Dict]]:
        """Busca análise de vendas por dia/operador/empresa"""
        if data_fim is None:
            data_fim = datetime.now()
        if data_inicio is None:
            data_inicio = data_fim - timedelta(days=30)

        data_inicio_str = data_inicio.strftime("%Y-%m-%dT00:00:00.000")
        data_fim_str = data_fim.strftime("%Y-%m-%dT23:59:59.999")

        endpoint = (
            f"/api/v1/AnaliseVenda/AnaliseVendasPorDiaOperadorEmpresa"
            f"?DataInicio={data_inicio_str}"
            f"&DataFim={data_fim_str}"
        )
        success, response = self._make_request("GET", endpoint)
        if success:
            items = response.get("Data", response if isinstance(response, list) else [])
            return True, items
        return False, []

    def send_venda(self, venda_data: Dict) -> Tuple[bool, str]:
        """Envia dados de venda para o Mobne"""
        required_fields = ["data", "cliente_id", "produtos", "valor_total"]
        missing = [f for f in required_fields if f not in venda_data]
        if missing:
            msg = f"Campos obrigatórios faltando: {', '.join(missing)}"
            logger.error(msg)
            return False, msg

        success, response = self._make_request(
            "POST", "/api/v1/vendas", use_cache=False, json=venda_data
        )
        if success:
            venda_id = response.get("id")
            logger.info(f"Venda {venda_id} enviada para Mobne com sucesso")
            return True, venda_id
        return False, response.get("error", "Erro desconhecido")

    # ---- DataFrames helpers ----

    def sync_produtos_para_dataframe(self) -> Tuple[bool, pd.DataFrame]:
        """Sincroniza produtos do Mobne e retorna como DataFrame"""
        success, products = self.fetch_produtos()
        if not success:
            return False, pd.DataFrame()
        try:
            df = pd.DataFrame(products)
            df["DATA_SYNC"] = datetime.now()
            df["EMPRESA_ID"] = self.empresa_id
            self.last_sync = datetime.now()
            return True, df
        except Exception as e:
            logger.error(f"Erro ao processar produtos: {str(e)}")
            return False, pd.DataFrame()

    def sync_clientes_para_dataframe(self) -> Tuple[bool, pd.DataFrame]:
        """Sincroniza clientes do Mobne e retorna como DataFrame"""
        success, clients = self.fetch_clientes()
        if not success:
            return False, pd.DataFrame()
        try:
            df = pd.DataFrame(clients)
            df["DATA_SYNC"] = datetime.now()
            df["EMPRESA_ID"] = self.empresa_id
            self.last_sync = datetime.now()
            return True, df
        except Exception as e:
            logger.error(f"Erro ao processar clientes: {str(e)}")
            return False, pd.DataFrame()

    def sync_vendas_para_dataframe(
        self, data_inicio: datetime = None, data_fim: datetime = None
    ) -> Tuple[bool, pd.DataFrame]:
        """Sincroniza vendas do Mobne e retorna como DataFrame"""
        success, vendas = self.fetch_vendas(data_inicio, data_fim)
        if not success:
            return False, pd.DataFrame()
        try:
            df = pd.DataFrame(vendas)
            df["DATA_SYNC"] = datetime.now()
            df["EMPRESA_ID"] = self.empresa_id
            self.last_sync = datetime.now()
            return True, df
        except Exception as e:
            logger.error(f"Erro ao processar vendas: {str(e)}")
            return False, pd.DataFrame()


# ============================================================
# SYNC PARA public/data (frontend estático)
# ============================================================

DATA_DIR = Path(__file__).parent / "public" / "data"


def sync_mobne_to_json(empresa_id: str = None, force: bool = False) -> Dict:
    """
    Sincroniza dados do Mobne e salva em public/data/*.json

    Returns:
        dict com resultado de cada entidade sincronizada
    """
    empresa_id = str(empresa_id or MOBNE_EMPRESA_ID)
    client = MobneAPIClient(empresa_id=empresa_id)

    if force:
        _cache_clear(empresa_id)

    results = {}
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Produtos ----
    try:
        success, products = client.fetch_produtos(page_size=100)
        if success and products:
            _salvar_json(DATA_DIR / "produtos.json", {
                "empresa_id": empresa_id,
                "ultima_sync": datetime.now().isoformat(),
                "total": len(products),
                "produtos": products,
            })
            results["produtos"] = {"ok": True, "count": len(products)}
        else:
            results["produtos"] = {"ok": False, "error": "Sem dados"}
    except Exception as e:
        results["produtos"] = {"ok": False, "error": str(e)}

    # ---- Vendas (últimos 30 dias) ----
    try:
        data_fim = datetime.now()
        data_inicio = data_fim - timedelta(days=30)
        success, vendas = client.fetch_vendas(data_inicio, data_fim)
        if success:
            _salvar_json(DATA_DIR / "vendas_mobne.json", {
                "empresa_id": empresa_id,
                "ultima_sync": datetime.now().isoformat(),
                "periodo": {
                    "inicio": data_inicio.strftime("%Y-%m-%d"),
                    "fim": data_fim.strftime("%Y-%m-%d"),
                },
                "total": len(vendas),
                "vendas": vendas,
            })
            results["vendas"] = {"ok": True, "count": len(vendas)}
        else:
            results["vendas"] = {"ok": False, "error": "Sem dados"}
    except Exception as e:
        results["vendas"] = {"ok": False, "error": str(e)}

    # ---- Clientes ----
    try:
        success, clientes = client.fetch_clientes(page_size=100)
        if success and clientes:
            _salvar_json(DATA_DIR / "clientes.json", {
                "empresa_id": empresa_id,
                "ultima_sync": datetime.now().isoformat(),
                "total": len(clientes),
                "clientes": clientes,
            })
            results["clientes"] = {"ok": True, "count": len(clientes)}
        else:
            results["clientes"] = {"ok": False, "error": "Sem dados"}
    except Exception as e:
        results["clientes"] = {"ok": False, "error": str(e)}

    logger.info(f"sync_mobne_to_json empresa={empresa_id}: {results}")
    return results


def _salvar_json(path: Path, data: dict):
    """Salva dict como JSON formatado"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ============================================================
# INTEGRAÇÃO STREAMLIT
# ============================================================

class MobneIntegration:
    """Gerenciador de integração com Mobne para uso em Streamlit"""

    def __init__(self):
        self.client = None
        self._initialize_session_state()

    def _initialize_session_state(self):
        defaults = {
            "mobne_client": None,
            "mobne_connected": False,
            "mobne_api_key": "",
            "mobne_cnpj": "",
            "mobne_empresa_id": MOBNE_EMPRESA_ID,
        }
        for k, v in defaults.items():
            if k not in st.session_state:
                st.session_state[k] = v

    def connect(self, api_key: str, cnpj: str, empresa_id: str = None) -> Tuple[bool, str]:
        try:
            self.client = MobneAPIClient(
                api_key=api_key, cnpj=cnpj, empresa_id=empresa_id or MOBNE_EMPRESA_ID
            )
            success, message = self.client.verify_connection()
            if success:
                st.session_state.mobne_client = self.client
                st.session_state.mobne_connected = True
                st.session_state.mobne_api_key = api_key
                st.session_state.mobne_cnpj = cnpj
                st.session_state.mobne_empresa_id = self.client.empresa_id
                return True, message
            return False, message
        except Exception as e:
            return False, f"Erro ao conectar: {str(e)}"

    def disconnect(self):
        st.session_state.mobne_client = None
        st.session_state.mobne_connected = False
        st.session_state.mobne_api_key = ""
        st.session_state.mobne_cnpj = ""

    def is_connected(self) -> bool:
        return st.session_state.get("mobne_connected", False)

    def get_client(self) -> Optional[MobneAPIClient]:
        return st.session_state.get("mobne_client")

    @staticmethod
    def require_mobne_connection(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            integration = MobneIntegration()
            if not integration.is_connected():
                st.error("Não conectado ao Mobne. Configure a integração primeiro!")
                return None
            return func(*args, **kwargs)
        return wrapper


# ============================================================
# UI HELPERS (Streamlit)
# ============================================================

def setup_mobne_connection_ui():
    """Cria interface para configurar conexão com Mobne"""
    st.markdown("### Configurar Conexão Mobne")

    # Selecionar empresa
    empresa_opcoes = {
        f"Empresa {eid} — {info['cnpj']}": eid
        for eid, info in MOBNE_EMPRESAS.items()
    }
    empresa_label = st.selectbox("Empresa", list(empresa_opcoes.keys()))
    empresa_id_sel = empresa_opcoes[empresa_label]

    with st.form("mobne_connection_form"):
        api_key = st.text_input("Chave de API Mobne", value=MOBNE_API_KEY, type="password")
        cnpj = st.text_input(
            "CNPJ da Empresa",
            value=MOBNE_EMPRESAS.get(empresa_id_sel, {}).get("cnpj", ""),
        )
        submit = st.form_submit_button("Conectar ao Mobne")

        if submit:
            if not api_key or not cnpj:
                st.error("Preencha todos os campos!")
            else:
                with st.spinner("Conectando ao Mobne..."):
                    integration = MobneIntegration()
                    success, message = integration.connect(api_key, cnpj, empresa_id_sel)
                    if success:
                        st.success(message)
                        st.session_state.mobne_configured = True
                    else:
                        st.error(message)


def display_mobne_status():
    """Exibe status da conexão Mobne"""
    integration = MobneIntegration()
    if integration.is_connected():
        eid = st.session_state.get("mobne_empresa_id", MOBNE_EMPRESA_ID)
        empresa_info = MOBNE_EMPRESAS.get(str(eid), {})
        st.success(f"Conectado ao Mobne — Empresa {eid} ({empresa_info.get('cnpj', '')})")
        if st.button("Desconectar do Mobne"):
            integration.disconnect()
            st.rerun()
    else:
        st.info("Não conectado ao Mobne")
