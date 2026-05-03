"""
Shared utilities for all workflows.
Handles secrets loading and common helpers.
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 將父目錄加入 path，使 workflows/ 可以 import config
_WORKSPACE_ROOT = Path(__file__).parent.parent
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from config import (
    FINMIND_TOKEN, NOTION_KEY, NOTION_PARENT_DB,
    NOTION_LEGACY_DB, NOTION_NEWS_DB,
    TELEGRAM_TOKEN, TELEGRAM_DM, TELEGRAM_GROUP,
    MINIMAX_API_KEY, BRAVE_API_KEY, SECRETS,
)

# 台北時區
TW_TZ = timezone(timedelta(hours=8))

def now_tw():
    """回傳 Taipei 時區的 datetime"""
    return datetime.now(TW_TZ)

def today_tw_str(fmt="%Y%m%d"):
    """回傳 Taipei 時區的今日字串"""
    return now_tw().strftime(fmt)


# ── 長內容寫入工具 ──────────────────────────────────────────

OUT_DIR = _WORKSPACE_ROOT / "state" / "direct_output"


def echo_to_file(content: str, title: str = "output") -> str:
    """
    將長內容寫入 state/direct_output/，回傳路徑。
    超過 2000 字時自動使用此函式。
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = now_tw().strftime("%H%M%S")
    safe = title.replace("/", "_").replace(" ", "_")
    filename = f"{today_tw_str()}_{ts}_{safe}.txt"
    filepath = OUT_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return str(filepath)



# ── Long content sender ────────────────────────────────────────
OUT_DIR = _WORKSPACE_ROOT / 'state' / 'direct_output'

def _send_telegram_doc(filepath, filename):
    import urllib.request, json
    SECRETS = _WORKSPACE_ROOT / 'config' / 'secrets.json'
    secrets = json.loads(SECRETS.read_text(encoding='utf-8'))
    token = secrets['telegram_bot_token']
    dm = secrets['telegram_dm']
    url = 'https://api.telegram.org/bot' + token + '/sendDocument'
    boundary = '----FormBoundary7MA4YWxkTrZu0gW'
    with open(filepath, 'rb') as f:
        file_data = f.read()
    body = (
        '--' + boundary + '\r\n'
        'Content-Disposition: form-data; name="chat_id"\r\n\r\n' + dm + '\r\n'
        '--' + boundary + '\r\n'
        'Content-Disposition: form-data; name="document"; filename="' + filename + '"\r\n'
        'Content-Type: application/octet-stream\r\n\r\n'
    ).encode() + file_data + ('\r\n--' + boundary + '--\r\n').encode()
    req = urllib.request.Request(url, data=body,
        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            return resp.get('ok', False)
    except Exception as e:
        print('[Telegram send fail] ' + str(e))
        return False

def echo_to_file(content, title='output'):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = now_tw().strftime('%H%M%S')
    safe = title.replace('/', '_').replace(' ', '_')
    filename = today_tw_str() + '_' + ts + '_' + safe + '.txt'
    filepath = OUT_DIR / filename
    filepath.write_text(content, encoding='utf-8')
    if len(content) > 2000:
        _send_telegram_doc(str(filepath), filename)
    return filepath

def echo_to_telegram(content, title='output'):
    if len(content) > 2000:
        path = echo_to_file(content, title)
        print('[sent as attachment] ' + str(path))
    else:
        print(content)
