from __future__ import annotations
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from . import settings

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def connection(path=None):
    db = sqlite3.connect(str(path or settings.DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

def initialize():
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS datasets(
          company INTEGER NOT NULL, resource TEXT NOT NULL, period TEXT NOT NULL,
          payload TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
          PRIMARY KEY(company,resource,period));
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, company INTEGER NOT NULL,
          mode TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', error TEXT,
          completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(company) WHERE state IN ('queued','running');
        CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS config(company INTEGER PRIMARY KEY, fixed_cost_cents INTEGER NOT NULL DEFAULT 1691346);
        ''')
        db.execute('INSERT OR IGNORE INTO schema_versions VALUES(1,?)', (now(),))
    os.chmod(settings.DB_PATH, 0o600)

def save_companies(rows):
    with connection() as db:
        for r in rows:
            db.execute('INSERT INTO companies VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',
                       (int(r['EmpresaId']), r.get('DescricaoReduzida') or r['RazaoSocial']))

def companies():
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM companies ORDER BY name')]

def put_dataset(company, resource, period, payload, db=None):
    sql = '''INSERT INTO datasets(company,resource,period,payload,updated_at) VALUES(?,?,?,?,?)
    ON CONFLICT(company,resource,period) DO UPDATE SET payload=excluded.payload,
    updated_at=excluded.updated_at,version=datasets.version+1'''
    args = (company, resource, period, json.dumps(payload, ensure_ascii=False, allow_nan=False), now())
    if db is not None:
        db.execute(sql, args)
    else:
        with connection() as own:
            own.execute(sql, args)

def dataset(company, resource, period='current', db=None):
    def read(conn):
        row = conn.execute('SELECT * FROM datasets WHERE company=? AND resource=? AND period=?',
                          (company, resource, period)).fetchone()
        if not row:
            return None
        return {**dict(row), 'payload': json.loads(row['payload'])}
    if db is not None:
        return read(db)
    with connection() as own:
        return read(own)

def periods(company):
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT period,updated_at,version FROM datasets WHERE company=? AND resource=? ORDER BY period DESC', (company, 'sales'))]

def create_job(company, mode):
    with connection() as db:
        try:
            cur = db.execute('INSERT INTO jobs(company,mode,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                             (company,mode,'queued',now(),now()))
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = db.execute("SELECT id FROM jobs WHERE company=? AND state IN ('queued','running')", (company,)).fetchone()
            return row['id']

def update_job(job, **fields):
    allowed = {'state','detail','error','completed','total'}
    assert set(fields) <= allowed
    fields['updated_at'] = now()
    with connection() as db:
        db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),job))

def jobs(company):
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE company=? ORDER BY id DESC LIMIT 15',(company,))]
