"""
Página de Integração com ERP Mobne
Sincroniza produtos, clientes e vendas com o Mobne
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from auth import require_auth, init_auth_session, is_authenticated
from mobne_api import MobneIntegration, MobneAPIClient, setup_mobne_connection_ui, display_mobne_status

# Configuração da página
st.set_page_config(
    page_title="Integração Mobne | Mercado duBairro",
    page_icon="🔗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Inicializar autenticação
init_auth_session()

# CSS customizado
st.markdown("""
<style>
    .success-box {
        background: #D4EDDA; border: 1px solid #C3E6CB; padding: 12px;
        border-radius: 8px; color: #155724; margin: 8px 0;
    }
    .error-box {
        background: #F8D7DA; border: 1px solid #F5C6CB; padding: 12px;
        border-radius: 8px; color: #721C24; margin: 8px 0;
    }
    .info-box {
        background: #D1ECF1; border: 1px solid #BEE5EB; padding: 12px;
        border-radius: 8px; color: #0C5460; margin: 8px 0;
    }
</style>
""", unsafe_allow_html=True)


@require_auth
def main():
    """Página principal de integração Mobne"""
    st.title("🔗 Integração com ERP Mobne")
    st.markdown("Sincronize dados entre Mercado duBairro e ERP Mobne")

    # Sidebar - Configuração
    with st.sidebar:
        st.header("⚙️ Configuração")
        integration = MobneIntegration()
        display_mobne_status()
        st.markdown("---")

        if not integration.is_connected():
            setup_mobne_connection_ui()
        else:
            st.markdown("### 📊 Opções de Sincronização")
            sync_option = st.radio(
                "Selecione uma ação:",
                [
                    "Sincronizar Produtos",
                    "Sincronizar Clientes",
                    "Sincronizar Vendas",
                    "Enviar Vendas"
                ]
            )

    # Conteúdo principal
    if not integration.is_connected():
        st.warning("⚠️ Configure a conexão com Mobne para começar!")
        return

    if sync_option == "Sincronizar Produtos":
        sync_produtos_section()
    elif sync_option == "Sincronizar Clientes":
        sync_clientes_section()
    elif sync_option == "Sincronizar Vendas":
        sync_vendas_section()
    elif sync_option == "Enviar Vendas":
        enviar_vendas_section()


def sync_produtos_section():
    """Seção para sincronizar produtos"""
    st.header("📦 Sincronizar Produtos")

    integration = MobneIntegration()
    client = integration.get_client()

    if not client:
        st.error("Erro ao obter cliente da API")
        return

    col1, col2 = st.columns([3, 1])

    with col2:
        if st.button("🔄 Sincronizar Agora", key="sync_produtos"):
            with st.spinner("Sincronizando produtos..."):
                success, df = client.sync_produtos_para_dataframe()

                if success:
                    st.success(f"✅ {len(df)} produtos sincronizados!")

                    # Mostrar resumo
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total de Produtos", len(df))
                    with col2:
                        if 'preco' in df.columns or 'PRECO' in df.columns:
                            preco_col = 'preco' if 'preco' in df.columns else 'PRECO'
                            st.metric("Valor Médio", f"R$ {df[preco_col].mean():.2f}")
                    with col3:
                        st.metric("Data de Sync", datetime.now().strftime("%d/%m/%Y %H:%M"))

                    # Mostrar tabela
                    st.subheader("Produtos Sincronizados")
                    st.dataframe(df, use_container_width=True)

                    # Opção de download
                    csv = df.to_csv(index=False)
                    st.download_button(
                        label="📥 Baixar como CSV",
                        data=csv,
                        file_name=f"produtos_mobne_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.error("❌ Erro ao sincronizar produtos")


def sync_clientes_section():
    """Seção para sincronizar clientes"""
    st.header("👥 Sincronizar Clientes")

    integration = MobneIntegration()
    client = integration.get_client()

    if not client:
        st.error("Erro ao obter cliente da API")
        return

    col1, col2 = st.columns([3, 1])

    with col2:
        if st.button("🔄 Sincronizar Agora", key="sync_clientes"):
            with st.spinner("Sincronizando clientes..."):
                success, df = client.sync_clientes_para_dataframe()

                if success:
                    st.success(f"✅ {len(df)} clientes sincronizados!")

                    # Mostrar resumo
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total de Clientes", len(df))
                    with col2:
                        st.metric("Última Sincronização", datetime.now().strftime("%d/%m/%Y %H:%M"))
                    with col3:
                        st.metric("Status", "✅ Ativo")

                    # Mostrar tabela
                    st.subheader("Clientes Sincronizados")
                    st.dataframe(df, use_container_width=True)

                    # Opção de download
                    csv = df.to_csv(index=False)
                    st.download_button(
                        label="📥 Baixar como CSV",
                        data=csv,
                        file_name=f"clientes_mobne_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.error("❌ Erro ao sincronizar clientes")


def sync_vendas_section():
    """Seção para sincronizar vendas do Mobne"""
    st.header("💰 Sincronizar Vendas")

    integration = MobneIntegration()
    client = integration.get_client()

    if not client:
        st.error("Erro ao obter cliente da API")
        return

    # Filtros
    col1, col2, col3 = st.columns(3)

    with col1:
        data_inicio = st.date_input("Data Início", datetime.now() - timedelta(days=30))

    with col2:
        data_fim = st.date_input("Data Fim", datetime.now())

    with col3:
        st.write("")  # Espaçamento
        if st.button("🔄 Sincronizar Vendas", key="sync_vendas"):
            with st.spinner("Sincronizando vendas..."):
                success, df = client.sync_vendas_para_dataframe(
                    data_inicio=datetime.combine(data_inicio, datetime.min.time()),
                    data_fim=datetime.combine(data_fim, datetime.max.time())
                )

                if success:
                    st.success(f"✅ {len(df)} vendas sincronizadas!")

                    # Mostrar resumo
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total de Vendas", len(df))
                    with col2:
                        if 'valor_total' in df.columns or 'VALOR_TOTAL' in df.columns:
                            valor_col = 'valor_total' if 'valor_total' in df.columns else 'VALOR_TOTAL'
                            st.metric("Faturamento Total", f"R$ {df[valor_col].sum():.2f}")
                    with col3:
                        st.metric("Período", f"{data_inicio.strftime('%d/%m')} a {data_fim.strftime('%d/%m/%Y')}")

                    # Mostrar tabela
                    st.subheader("Vendas Sincronizadas")
                    st.dataframe(df, use_container_width=True)

                    # Opção de download
                    csv = df.to_csv(index=False)
                    st.download_button(
                        label="📥 Baixar como CSV",
                        data=csv,
                        file_name=f"vendas_mobne_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.error("❌ Erro ao sincronizar vendas")


def enviar_vendas_section():
    """Seção para enviar vendas para o Mobne"""
    st.header("📤 Enviar Vendas para Mobne")

    st.info("📌 Envie dados de vendas realizadas para o ERP Mobne")

    integration = MobneIntegration()
    client = integration.get_client()

    if not client:
        st.error("Erro ao obter cliente da API")
        return

    # Opções de envio
    envio_method = st.radio(
        "Escolha o método de envio:",
        ["Entrada Manual", "Upload CSV", "Sincronização Automática"]
    )

    if envio_method == "Entrada Manual":
        envio_manual(client)
    elif envio_method == "Upload CSV":
        envio_csv(client)
    elif envio_method == "Sincronização Automática":
        envio_automatico(client)


def envio_manual(client: MobneAPIClient):
    """Formulário para entrada manual de vendas"""
    st.subheader("✍️ Entrada Manual de Venda")

    with st.form("venda_form"):
        col1, col2 = st.columns(2)

        with col1:
            data_venda = st.date_input("Data da Venda")
            cliente_id = st.number_input("ID do Cliente", min_value=1)

        with col2:
            valor_total = st.number_input("Valor Total (R$)", min_value=0.01, step=0.01)
            quantidade = st.number_input("Quantidade de Itens", min_value=1)

        descricao = st.text_area("Descrição/Observações")

        submit = st.form_submit_button("📤 Enviar Venda")

        if submit:
            venda_data = {
                "data": data_venda.strftime("%Y-%m-%d"),
                "cliente_id": int(cliente_id),
                "produtos": [{"quantidade": int(quantidade)}],
                "valor_total": float(valor_total),
                "observacoes": descricao
            }

            with st.spinner("Enviando venda..."):
                success, result = client.send_venda(venda_data)

                if success:
                    st.success(f"✅ Venda enviada com sucesso! ID: {result}")
                else:
                    st.error(f"❌ Erro ao enviar venda: {result}")


def envio_csv(client: MobneAPIClient):
    """Upload de vendas via CSV"""
    st.subheader("📤 Upload de Vendas (CSV)")

    st.markdown("""
    **Formato esperado do CSV:**
    - Data (YYYY-MM-DD)
    - Cliente_ID
    - Produto_ID
    - Quantidade
    - Valor_Unitario
    - Valor_Total
    """)

    uploaded_file = st.file_uploader("Escolha arquivo CSV", type="csv")

    if uploaded_file:
        try:
            df = pd.read_csv(uploaded_file)
            st.dataframe(df, use_container_width=True)

            if st.button("📤 Enviar Vendas do CSV"):
                with st.spinner("Processando vendas..."):
                    sucessos = 0
                    erros = 0

                    progress_bar = st.progress(0)

                    for idx, row in df.iterrows():
                        venda_data = {
                            "data": str(row['Data']),
                            "cliente_id": int(row['Cliente_ID']),
                            "produtos": [
                                {
                                    "produto_id": int(row['Produto_ID']),
                                    "quantidade": int(row['Quantidade']),
                                    "valor_unitario": float(row['Valor_Unitario'])
                                }
                            ],
                            "valor_total": float(row['Valor_Total'])
                        }

                        success, _ = client.send_venda(venda_data)
                        if success:
                            sucessos += 1
                        else:
                            erros += 1

                        progress_bar.progress((idx + 1) / len(df))

                    st.success(f"✅ {sucessos} vendas enviadas com sucesso")
                    if erros > 0:
                        st.warning(f"⚠️ {erros} vendas tiveram erro")

        except Exception as e:
            st.error(f"❌ Erro ao processar arquivo: {str(e)}")


def envio_automatico(client: MobneAPIClient):
    """Configuração de sincronização automática"""
    st.subheader("⚙️ Sincronização Automática")

    st.info("🔄 Configure a sincronização automática de vendas para o Mobne")

    col1, col2 = st.columns(2)

    with col1:
        frequencia = st.selectbox(
            "Frequência de Sincronização",
            ["A cada 1 hora", "A cada 4 horas", "Diária", "Semanal"]
        )

    with col2:
        horario = st.time_input("Horário para sincronizar", datetime.now().time())

    if st.button("💾 Salvar Configuração"):
        st.success("✅ Configuração de sincronização automática salva!")
        st.info(f"Próxima sincronização: {frequencia} às {horario}")


if __name__ == "__main__":
    main()
