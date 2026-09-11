from __future__ import annotations
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from . import settings

PG = bool(settings.DATABASE_URL)
if PG:
    import psycopg
    from psycopg.rows import dict_row

def now():
    return datetime.now(timezone.utc).isoformat()

# --------------------------------------------------------------------------
# Postgres adapter: makes a psycopg connection look enough like a sqlite3
# Connection (bare .execute(sql, params) returning a fetchable/iterable
# cursor, dict-like rows, .commit()/.rollback()/.close()) that every other
# function below — and every direct conn.execute(...) call in api.py/sync.py
# — runs unchanged against either backend. The only genuine SQL differences
# (schema DDL, and RETURNING id instead of sqlite3's cursor.lastrowid) are
# isolated to initialize() and create_job() below.
# --------------------------------------------------------------------------
class _PGCursor:
    def __init__(self, cur):
        self._cur = cur
    def fetchone(self):
        return self._cur.fetchone()
    def fetchall(self):
        return self._cur.fetchall()
    def __iter__(self):
        return iter(self._cur)

class _PGConn:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        # Every call site here writes SQLite-style '?' placeholders; psycopg wants '%s'.
        # None of this app's SQL text otherwise contains a literal '?' character.
        cur.execute(sql.replace('?', '%s'), params)
        return _PGCursor(cur)
    def commit(self):
        self._conn.commit()
    def rollback(self):
        self._conn.rollback()
    def close(self):
        self._conn.close()

@contextmanager
def connection(path=None):
    if PG:
        conn = psycopg.connect(settings.DATABASE_URL, row_factory=dict_row)
        db = _PGConn(conn)
    else:
        conn = sqlite3.connect(str(path or settings.DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        db = conn
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()

def _initialize_sqlite(db):
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
    # v2: 'documents' lets periods()/dashboard() tell a period Mobne genuinely has
    # no sales for (e.g. before onboarding) from one merely not yet synced, without
    # parsing every multi-MB sales payload on the hot status-poll path.
    columns = {r['name'] for r in db.execute('PRAGMA table_info(datasets)')}
    if 'documents' not in columns:
        db.execute('ALTER TABLE datasets ADD COLUMN documents INTEGER NOT NULL DEFAULT 0')
        for row in db.execute("SELECT company,resource,period,payload FROM datasets WHERE resource='sales'"):
            count = json.loads(row['payload']).get('raw_count', 0)
            db.execute('UPDATE datasets SET documents=? WHERE company=? AND resource=? AND period=?',
                       (count, row['company'], row['resource'], row['period']))
        db.execute('INSERT OR IGNORE INTO schema_versions VALUES(2,?)', (now(),))

def _initialize_postgres(db):
    # Same shape as the SQLite schema above, in Postgres DDL — a fresh database,
    # so no migration path is needed here the way SQLite's ALTER TABLE one is.
    db.execute('''
    CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)
    ''')
    db.execute('''
    CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, name TEXT NOT NULL)
    ''')
    db.execute('''
    CREATE TABLE IF NOT EXISTS datasets(
      company INTEGER NOT NULL, resource TEXT NOT NULL, period TEXT NOT NULL,
      payload TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
      documents INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(company,resource,period))
    ''')
    db.execute('''
    CREATE TABLE IF NOT EXISTS jobs(
      id SERIAL PRIMARY KEY, company INTEGER NOT NULL,
      mode TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', error TEXT,
      completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0)
    ''')
    db.execute('''
    CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(company) WHERE state IN ('queued','running')
    ''')
    db.execute('''
    CREATE TABLE IF NOT EXISTS sessions(hash TEXT PRIMARY KEY, expires DOUBLE PRECISION NOT NULL)
    ''')
    db.execute('''
    CREATE TABLE IF NOT EXISTS config(company INTEGER PRIMARY KEY, fixed_cost_cents INTEGER NOT NULL DEFAULT 1691346)
    ''')
    db.execute('INSERT INTO schema_versions VALUES(1,?) ON CONFLICT(version) DO NOTHING', (now(),))

def initialize():
    with connection() as db:
        if PG:
            _initialize_postgres(db)
        else:
            _initialize_sqlite(db)
    if not PG:
        os.chmod(settings.DB_PATH, 0o600)

def save_companies(rows):
    with connection() as db:
        for r in rows:
            db.execute('INSERT INTO companies VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',
                       (int(r['EmpresaId']), r.get('DescricaoReduzida') or r['RazaoSocial']))

def companies():
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM companies ORDER BY name')]

def put_dataset(company, resource, period, payload, db=None, documents=0):
    sql = '''INSERT INTO datasets(company,resource,period,payload,updated_at,documents) VALUES(?,?,?,?,?,?)
    ON CONFLICT(company,resource,period) DO UPDATE SET payload=excluded.payload,
    updated_at=excluded.updated_at,version=datasets.version+1,documents=excluded.documents'''
    args = (company, resource, period, json.dumps(payload, ensure_ascii=False, allow_nan=False), now(), documents)
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
        return [dict(r) for r in db.execute('SELECT period,updated_at,version,documents FROM datasets WHERE company=? AND resource=? ORDER BY period DESC', (company, 'sales'))]

def create_job(company, mode):
    with connection() as db:
        if PG:
            try:
                cur = db.execute('INSERT INTO jobs(company,mode,state,created_at,updated_at) VALUES(?,?,?,?,?) RETURNING id',
                                 (company,mode,'queued',now(),now()))
                return cur.fetchone()['id']
            except Exception:
                db.rollback()
                row = db.execute("SELECT id FROM jobs WHERE company=? AND state IN ('queued','running')", (company,)).fetchone()
                return row['id']
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
