from __future__ import annotations
import json
import os
import socket
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from . import settings
from . import migrations

PG = bool(settings.DATABASE_URL)
if PG:
    import psycopg
    from psycopg.rows import dict_row


def _pg_hostaddr(dsn: str) -> str | None:
    """Resolve the DSN's host to an IPv4 address for libpq's `hostaddr`
    parameter, bypassing the OS resolver's default AAAA-first ordering.
    Vercel's serverless runtime has no outbound IPv6 route; Neon's pooler
    hostname (like many providers fronted by an AWS NLB) publishes both A
    and AAAA records, so a plain psycopg.connect(dsn) picks the IPv6
    address and fails with "Cannot assign requested address" on every
    invocation (verified live via Vercel's runtime error logs). Passing
    `hostaddr` alongside the DSN's own `host` still lets libpq use `host`
    for TLS SNI/certificate verification and SCRAM channel binding — those
    depend on the server's certificate, not on which IP address the TCP
    connection actually dials — so only the connection's destination
    changes, not its identity checks.
    Returns None (falls back to psycopg's own resolution) if the DSN names
    no single resolvable host, already pins a hostaddr itself, or has no
    IPv4 address at all.
    """
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    host = info.get("host")
    if not host or "," in host or info.get("hostaddr"):
        return None
    port = int(str(info.get("port") or 5432).split(",")[0])
    try:
        return socket.getaddrinfo(host, port, family=socket.AF_INET, proto=socket.IPPROTO_TCP)[0][4][0]
    except OSError:
        return None


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
    @property
    def rowcount(self):
        return self._cur.rowcount

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
def connection(path=None, snapshot=False):
    """`snapshot=True` opens a connection whose statements all read from one
    consistent point-in-time view of the database, immune to any write another
    connection commits while this `with` block is still open — used by
    `forecast.py::forecast()`, which issues several SELECTs that must be
    assembled from a single moment, not several independent reads. It is
    read-only by contract: the block is expected to run no writes, and it is
    always rolled back (never committed) on the way out, successful or not.

    - SQLite: the default `isolation_level=""` only opens an implicit
      transaction before a write statement, never before a bare SELECT — so
      without this, each SELECT in a `with connection():` block is its own
      independent read. Issuing an explicit `BEGIN DEFERRED` immediately
      after connecting (before any statement runs) starts a real transaction;
      in this codebase's WAL journal mode (`PRAGMA journal_mode=WAL`, set in
      `_initialize_sqlite`), a deferred transaction's snapshot is fixed at
      its first read and does not change even if another connection commits
      a write afterwards — verified directly against this project's sqlite3
      build (see `tests/finance/test_forecast.py`'s snapshot-isolation test).
    - PostgreSQL: a plain `psycopg.connect(...)` here otherwise runs under the
      server default `READ COMMITTED`, where every statement gets its own
      fresh snapshot. Setting `REPEATABLE READ` before the first statement
      gives the whole transaction one snapshot taken at its first query,
      same guarantee as the SQLite path above.
    """
    if PG:
        hostaddr = _pg_hostaddr(settings.DATABASE_URL)
        kwargs = {"hostaddr": hostaddr} if hostaddr else {}
        conn = psycopg.connect(settings.DATABASE_URL, row_factory=dict_row, **kwargs)
        if snapshot:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        db = _PGConn(conn)
    else:
        conn = sqlite3.connect(str(path or settings.DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        if snapshot:
            conn.execute('BEGIN DEFERRED')
        db = conn
    try:
        yield db
        db.rollback() if snapshot else db.commit()
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
    db.execute('INSERT INTO schema_versions VALUES(2,?) ON CONFLICT(version) DO NOTHING', (now(),))

def initialize():
    with connection() as db:
        if PG:
            _initialize_postgres(db)
        else:
            _initialize_sqlite(db)
        migrations.migrate(db)
        _backfill_catalog_counts(db)
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

def put_dataset(company, resource, period, payload, db=None, documents=None, summary=None):
    # `documents` is the row's item count, kept alongside the payload so readers
    # that only need "how many" (status(), periods()) never transfer the payload
    # itself — a few MB per row against a network-attached Postgres. Derived from
    # a list payload when the caller doesn't supply it; 'sales' passes its raw
    # document count explicitly, since its payload is a dict, not a list of items.
    if documents is None:
        documents = len(payload) if isinstance(payload, list) else 0
    # `summary` is the small per-period aggregate the dashboard's timeline needs,
    # cached here so it never re-reads the payload to recompute it (migration 023).
    sql = '''INSERT INTO datasets(company,resource,period,payload,updated_at,documents,summary) VALUES(?,?,?,?,?,?,?)
    ON CONFLICT(company,resource,period) DO UPDATE SET payload=excluded.payload,
    updated_at=excluded.updated_at,version=datasets.version+1,documents=excluded.documents,
    summary=excluded.summary'''
    args = (company, resource, period, json.dumps(payload, ensure_ascii=False, allow_nan=False), now(), documents,
            json.dumps(summary, ensure_ascii=False, allow_nan=False) if summary is not None else None)
    if db is not None:
        db.execute(sql, args)
    else:
        with connection() as own:
            own.execute(sql, args)

CATALOG_RESOURCES = ('categories', 'products', 'stock', 'prices', 'stock_previous', 'prices_previous')


def catalog_meta(company, resources):
    """Item count and last-update time per catalog, without transferring payloads.

    status() is polled every 60s by every open tab and needs nothing but these two
    fields; reading the payloads to call len() on them moved 4.42 MB per poll
    (~45 GB/month against a hosted Postgres) to produce four integers. One query
    for all resources, so this also replaces four round-trips with one.
    """
    placeholders = ','.join('?' * len(resources))
    with connection() as db:
        rows = db.execute(
            f"SELECT resource,documents,updated_at FROM datasets "
            f"WHERE company=? AND period='current' AND resource IN ({placeholders})",
            (company, *resources)).fetchall()
    return {r['resource']: {'count': r['documents'], 'updated_at': r['updated_at']} for r in rows}


def period_summaries(company, db=None):
    """Cached per-period aggregates for the dashboard timeline, oldest first.

    Carries no payload: the timeline needs an end date and four totals per month,
    and reading the receipts to recompute them cost 62 MB per page load against
    production data. A NULL summary means the row predates migration 023 — the
    caller falls back to its payload until a sync backfills it.
    """
    sql = ("SELECT period,summary FROM datasets "
           "WHERE company=? AND resource='sales' AND documents>0 ORDER BY period")

    def read(conn):
        return [(r['period'], json.loads(r['summary']) if r['summary'] else None)
                for r in conn.execute(sql, (company,))]

    if db is not None:
        return read(db)
    with connection() as own:
        return read(own)


def set_period_summary(company, period, summary, db=None):
    """Cache a period's timeline aggregate without rewriting its payload."""
    sql = "UPDATE datasets SET summary=? WHERE company=? AND resource='sales' AND period=?"
    args = (json.dumps(summary, ensure_ascii=False, allow_nan=False), company, period)
    if db is not None:
        db.execute(sql, args)
    else:
        with connection() as own:
            own.execute(sql, args)


def periods_missing_summary(company, db=None):
    """Periods whose timeline aggregate was never cached (see period_summaries)."""
    sql = ("SELECT period FROM datasets WHERE company=? AND resource='sales' "
           "AND summary IS NULL ORDER BY period")

    def read(conn):
        return [r['period'] for r in conn.execute(sql, (company,))]

    if db is not None:
        return read(db)
    with connection() as own:
        return read(own)


def _backfill_catalog_counts(db):
    """Fill `documents` for catalog rows written before it was stored for them.

    Without this the first start after the change reports every catalog as 0 items,
    which reads as a broken sync. Runs at every startup and is idempotent: once
    filled, it matches nothing. A genuinely empty catalog keeps matching (0 is both
    "empty" and "not counted" here) but costs only its own tiny row.
    """
    placeholders = ','.join('?' * len(CATALOG_RESOURCES))
    rows = db.execute(
        f"SELECT company,resource,period,payload FROM datasets "
        f"WHERE documents=0 AND resource IN ({placeholders})", CATALOG_RESOURCES).fetchall()
    for row in rows:
        payload = json.loads(row['payload'])
        if isinstance(payload, list) and payload:
            db.execute('UPDATE datasets SET documents=? WHERE company=? AND resource=? AND period=?',
                       (len(payload), row['company'], row['resource'], row['period']))


def copy_dataset(company, resource, target_resource, db=None):
    """Duplicate a catalog row under another resource name, server-side.

    The sync keeps a '_previous' copy of stock/prices before replacing them. Doing
    that by reading the payload out and writing the same bytes straight back moves
    a couple of MB across the wire twice for a copy the database can do by itself.
    A missing source row is a no-op, matching the caller's old `if previous` guard.
    """
    sql = '''INSERT INTO datasets(company,resource,period,payload,updated_at,documents,summary)
    SELECT company,?,period,payload,?,documents,summary FROM datasets
    WHERE company=? AND resource=? AND period='current'
    ON CONFLICT(company,resource,period) DO UPDATE SET payload=excluded.payload,
    updated_at=excluded.updated_at,version=datasets.version+1,documents=excluded.documents,
    summary=excluded.summary'''
    args = (target_resource, now(), company, resource)
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

# Vercel kills a function after maxDuration (300s, vercel.json) without running
# except/finally, so the job row stays 'running'. Every sync step writes
# updated_at, so an active job silent for longer than that is dead.
STALE_JOB_SECONDS = 360

def expire_stale_jobs(company):
    """Serverless only: fail active jobs whose run was killed, so the
    one_active_job index stops blocking 'Sincronizar agora' and the cron.
    Locally the launcher already fails 'running' rows on restart, and a live
    worker can legitimately wait out Mobne retries for longer."""
    if not settings.IS_SERVERLESS:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_JOB_SECONDS)).isoformat()
    with connection() as db:
        db.execute("UPDATE jobs SET state='failed',error=?,updated_at=? WHERE company=? AND state IN ('queued','running') AND updated_at<?",
                   ('Execução interrompida pelo limite de tempo do servidor; sincronize novamente.', now(), company, cutoff))

def claim_job(company, mode):
    """(job_id, created). A company has at most one queued/running job
    (one_active_job index): asking for another returns the active one with
    created=False. Whoever executes runs itself (serverless trigger, cron) must
    only do so when created is True — running an already-active job id again
    makes two runs write the same progress row, which is how a finished job
    ended up showing 9/6 with a stale "Cupons ... página" detail."""
    expire_stale_jobs(company)
    with connection() as db:
        if PG:
            try:
                cur = db.execute('INSERT INTO jobs(company,mode,state,created_at,updated_at) VALUES(?,?,?,?,?) RETURNING id',
                                 (company,mode,'queued',now(),now()))
                return cur.fetchone()['id'], True
            except Exception:
                db.rollback()
                row = db.execute("SELECT id FROM jobs WHERE company=? AND state IN ('queued','running')", (company,)).fetchone()
                return row['id'], False
        try:
            cur = db.execute('INSERT INTO jobs(company,mode,state,created_at,updated_at) VALUES(?,?,?,?,?)',
                             (company,mode,'queued',now(),now()))
            return cur.lastrowid, True
        except sqlite3.IntegrityError:
            row = db.execute("SELECT id FROM jobs WHERE company=? AND state IN ('queued','running')", (company,)).fetchone()
            return row['id'], False

def create_job(company, mode):
    return claim_job(company, mode)[0]

def update_job(job, **fields):
    allowed = {'state','detail','error','completed','total'}
    assert set(fields) <= allowed
    fields['updated_at'] = now()
    with connection() as db:
        db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?', (*fields.values(),job))

def jobs(company):
    # The status poll is what the page shows, so a killed run stops reading 'Em execução' there too.
    expire_stale_jobs(company)
    with connection() as db:
        return [dict(r) for r in db.execute('SELECT * FROM jobs WHERE company=? ORDER BY id DESC LIMIT 15',(company,))]
