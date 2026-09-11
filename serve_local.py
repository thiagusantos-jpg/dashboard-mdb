"""Run one local instance. The file lock protects scheduler restart recovery."""
import fcntl
from backend import settings
import uvicorn

if __name__=='__main__':
    lock=open(settings.STATE/'server.lock','w')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('O dashboard já está em execução.')
    print('Dashboard: http://127.0.0.1:8765')
    print('Senha local: .runtime/acesso-local.txt')
    uvicorn.run('backend.api:app',host='127.0.0.1',port=8765,access_log=False)
