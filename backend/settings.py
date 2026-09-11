from pathlib import Path
import os
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
load_dotenv(ROOT / '.env.local', override=True)
STATE = Path(os.getenv('MDB_STATE_DIR', str(ROOT / '.runtime')))
STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(STATE, 0o700)
DB_PATH = STATE / 'dashboard.sqlite3'
API_KEY = os.getenv('MOBNE_API_KEY', '')
BASE_URL = 'https://apiexternal.mobne.com.br'
SYNC_SECONDS = max(300, int(os.getenv('MDB_SYNC_SECONDS', '3600')))
