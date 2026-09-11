"""
Entrypoint para Vercel
Expõe a aplicação FastAPI como app.py para que Vercel possa rodar
"""

from api import app

# Vercel vai procurar por 'app' e rodá-la
# Esta é a aplicação FastAPI que serve os endpoints
