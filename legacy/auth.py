"""
Módulo de Autenticação para Mercado duBairro
Controla acesso a funcionalidades administrativas
"""

import streamlit as st
from datetime import datetime, timedelta

# Credenciais dos administradores (em produção, usar banco de dados)
ADMIN_CREDENTIALS = {
    "admin": "dubairro2026",  # TODO: Substituir por variáveis de ambiente
    "gestor": "gestor123"      # TODO: Substituir por variáveis de ambiente
}

def init_auth_session():
    """Inicializa variáveis de sessão de autenticação"""
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'username' not in st.session_state:
        st.session_state.username = None
    if 'login_time' not in st.session_state:
        st.session_state.login_time = None

def login(username: str, password: str) -> bool:
    """
    Valida credenciais do admin

    Args:
        username: Nome de usuário
        password: Senha

    Returns:
        bool: True se credenciais são válidas
    """
    if username in ADMIN_CREDENTIALS and ADMIN_CREDENTIALS[username] == password:
        st.session_state.authenticated = True
        st.session_state.username = username
        st.session_state.login_time = datetime.now()
        return True
    return False

def logout():
    """Faz logout do usuário"""
    st.session_state.authenticated = False
    st.session_state.username = None
    st.session_state.login_time = None

def is_authenticated() -> bool:
    """Verifica se o usuário está autenticado"""
    return st.session_state.get('authenticated', False)

def require_auth(page_func):
    """
    Decorator para proteger uma página com autenticação

    Args:
        page_func: Função da página a proteger

    Returns:
        Função wrapper que verifica autenticação
    """
    def wrapper(*args, **kwargs):
        init_auth_session()

        if not is_authenticated():
            st.error("🔒 Acesso restrito a administradores!")
            st.markdown("---")

            with st.form("login_form"):
                st.markdown("### 🔐 Login Administrativo")
                username = st.text_input("Usuário", placeholder="admin")
                password = st.text_input("Senha", type="password", placeholder="••••••••")
                submit = st.form_submit_button("Entrar")

                if submit:
                    if login(username, password):
                        st.success("✅ Login realizado com sucesso!")
                        st.rerun()
                    else:
                        st.error("❌ Usuário ou senha incorretos!")
            return

        # Mostrar informações do usuário logado
        with st.sidebar:
            st.markdown(f"**👤 Usuário:** {st.session_state.username}")
            if st.button("🚪 Logout"):
                logout()
                st.rerun()

        # Executar a função protegida
        return page_func(*args, **kwargs)

    return wrapper

def get_current_user() -> str:
    """Retorna o usuário atualmente logado"""
    return st.session_state.get('username', 'Anônimo')
