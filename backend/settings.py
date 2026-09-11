from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT / '.env.local', override=True)

# Vercel sets VERCEL=1 on every deployment (build and runtime) — see
# https://vercel.com/docs/environment-variables/system-environment-variables.
# On Vercel the filesystem is read-only at runtime and each invocation may be a
# fresh instance, so there is no local state directory and no local password
# file: DATABASE_URL (a hosted Postgres) and MDB_ACCESS_PASSWORD are required
# instead. Locally, neither is set and behavior is unchanged from before.
IS_SERVERLESS = bool(os.getenv('VERCEL'))
DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('POSTGRES_URL')

if not IS_SERVERLESS:
    STATE = Path(os.getenv('MDB_STATE_DIR', str(ROOT / '.runtime')))
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    DB_PATH = STATE / 'dashboard.sqlite3'
else:
    STATE = None
    DB_PATH = None

API_KEY = os.getenv('MOBNE_API_KEY', '')
BASE_URL = 'https://apiexternal.mobne.com.br'
SYNC_SECONDS = max(300, int(os.getenv('MDB_SYNC_SECONDS', '3600')))
