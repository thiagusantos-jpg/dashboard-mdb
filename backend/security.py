from __future__ import annotations
import hashlib
import hmac
import secrets
import os
import time
import threading
from . import settings, database as db
from fastapi import HTTPException, Request

COOKIE='mdb_session'
_lock=threading.Lock()
_attempts={}

def access_password():
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
    with db.connection() as conn:
        row=conn.execute('SELECT expires FROM sessions WHERE hash=?',(fingerprint(raw),)).fetchone()
    if row is None or row['expires']<time.time():
        raise HTTPException(401,'Sua sessão expirou. Entre novamente.')
    if request.method not in ('GET','HEAD'):
        same_origin(request)
        if not hmac.compare_digest(request.headers.get('x-csrf-token',''),csrf(raw)):
            raise HTTPException(403,'Atualize a página e tente novamente.')
    return raw

def login(request, password):
    same_origin(request)
    ip=request.client.host if request.client else 'local'
    with _lock:
        recent=[t for t in _attempts.get(ip,[]) if t>time.time()-60]
        if len(recent)>=8: raise HTTPException(429,'Muitas tentativas. Aguarde um minuto.')
        _attempts[ip]=recent+[time.time()]
    if not hmac.compare_digest(password,access_password()):
        raise HTTPException(401,'Senha incorreta.')
    raw=secrets.token_urlsafe(32)
    with db.connection() as conn:
        conn.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        conn.execute('INSERT INTO sessions VALUES(?,?)',(fingerprint(raw),time.time()+43200))
    return raw
