"""
Shared utilities for all workflows.
Handles secrets loading and common helpers.
"""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

_WORKSPACE_ROOT = Path(__file__).parent.parent
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from config import (
    FINMIND_TOKEN, NOTION_KEY, NOTION_PARENT_DB,
    NOTION_LEGACY_DB, NOTION_NEWS_DB,
    TELEGRAM_TOKEN, TELEGRAM_DM, TELEGRAM_GROUP,
    MINIMAX_API_KEY, BRAVE_API_KEY, SECRETS,
)

TW_TZ = timezone(timedelta(hours=8))

def now_tw():
    return datetime.now(TW_TZ)

def today_tw_str(fmt="%Y%m%d"):
    return now_tw().strftime(fmt)


# ── Long content sender ────────────────────────────────────────
# 自動判斷長度，長內容（>2000字）直接發 Telegram 附件，短內容回傳文字
# 回傳格式：(success: bool, mode: str, message: str)
#   mode='file': 發送成功/失敗都回傳路徑字串
#   mode='text': 短內容直接回傳

OUT_DIR = _WORKSPACE_ROOT / "state" / "direct_output"
SECRETS = _WORKSPACE_ROOT / "config" / "secrets.json"


def _send_telegram_doc(filepath, filename):
    import urllib.request
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


def echo_to_telegram(content, title='output'):
    """
    將內容發送到 Telegram（長內容自動附件，短內容直接文字）。
    回傳值直接作為 OpenClaw 回覆內容。
    """
    if len(content) > 2000:
        ts = now_tw().strftime('%H%M%S')
        safe = title.replace('/', '_').replace(' ', '_')
        filename = today_tw_str() + '_' + ts + '_' + safe + '.txt'
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        filepath = OUT_DIR / filename
        filepath.write_text(content, encoding='utf-8')
        ok = _send_telegram_doc(str(filepath), filename)
        return '[附件已發送] ' + str(filepath)
    else:
        return content
