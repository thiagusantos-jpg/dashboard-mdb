from __future__ import annotations
import hashlib
import hmac
import secrets
import os
import time
import threading
from dataclasses import dataclass
from typing import Optional

from . import settings, database as db, identity
from fastapi import HTTPException, Request

COOKIE='mdb_session'
_lock=threading.Lock()
_attempts={}


@dataclass(frozen=True)
class AuthContext:
    user_id: int
    email: str
    session_hash: str

def access_password():
    if settings.IS_SERVERLESS:
        # No local filesystem to persist an auto-generated password to, and no
        # per-instance state anyway (each invocation may be a fresh container) —
        # so the deploy must supply this explicitly rather than us inventing one.
        pw=os.environ.get('MDB_ACCESS_PASSWORD')
        if not pw:
            raise HTTPException(500,'MDB_ACCESS_PASSWORD não configurada nesta implantação.')
        return pw
    path=settings.STATE/'acesso-local.txt'
    if not path.exists():
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(18)+'\n')
        except FileExistsError:
            pass
    return path.read_text().strip()

def fingerprint(raw):
    return hashlib.sha256(raw.encode()).hexdigest()

def csrf(raw):
    return hmac.new(access_password().encode(),raw.encode(),hashlib.sha256).hexdigest()

def same_origin(request):
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'Origem não autorizada.')

def authenticate(request: Request):
    raw=request.cookies.get(COOKIE,'')
    if not raw:
        raise HTTPException(401,'Faça login para acessar o dashboard.')
    session_hash=fingerprint(raw)
    with db.connection() as conn:
        row=conn.execute(
            '''SELECT s.expires,s.user_id,u.email
               FROM sessions s
               JOIN users u ON u.id=s.user_id AND u.active=1
               WHERE s.hash=?''',
            (session_hash,),
        ).fetchone()
    if row is None or row['expires']<time.time():
        raise HTTPException(401,'Sua sessão expirou. Entre novamente.')
    if request.method not in ('GET','HEAD'):
        same_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token',''),csrf(raw)):
            raise HTTPException(403,'Atualize a página e tente novamente.')
    return AuthContext(
        user_id=row['user_id'],
        email=row['email'],
        session_hash=session_hash,
    )

def rate_limit(request, key='login'):
    ip=request.client.host if request.client else 'local'
    bucket=f'{key}:{ip}'
    with _lock:
        recent=[t for t in _attempts.get(bucket,[]) if t>time.time()-60]
        if len(recent)>=8: raise HTTPException(429,'Muitas tentativas. Aguarde um minuto.')
        _attempts[bucket]=recent+[time.time()]

def login(request, password, email: Optional[str] = None):
    same_origin(request)
    rate_limit(request)
    if not identity.users_exist():
        if not hmac.compare_digest(password,access_password()):
            raise HTTPException(401,'Senha incorreta.')
        try:
            identity.create_user(
                email or 'admin@mercadodubairro.local',
                password,
                'Administrador local',
                is_admin=True,
            )
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from exc
    elif not email:
        raise HTTPException(401,'Informe seu e-mail e senha.')

    user=identity.verify_credentials(email or 'admin@mercadodubairro.local',password)
    if user is None:
        raise HTTPException(401,'E-mail ou senha incorretos.')
    raw=secrets.token_urlsafe(32)
    with db.connection() as conn:
        conn.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        conn.execute(
            'INSERT INTO sessions(hash,expires,user_id) VALUES(?,?,?)',
            (fingerprint(raw),time.time()+43200,user['id']),
        )
    return raw
