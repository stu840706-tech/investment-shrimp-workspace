"""
Centralized secrets loader.
All workflows use: from config import SECRETS
"""
import json
from pathlib import Path

_CONFIG_DIR = Path(__file__).parent
_SECRETS_FILE = _CONFIG_DIR / "secrets.json"

if not _SECRETS_FILE.exists():
    raise FileNotFoundError(
        f"Missing secrets file: {_SECRETS_FILE}. "
        "Copy secrets.example.json to secrets.json and fill in your tokens."
    )

with open(_SECRETS_FILE, encoding='utf-8') as f:
    SECRETS = json.load(f)

# 為了方便，直接 export 常用的 key
FINMIND_TOKEN    = SECRETS["finmind_token"]
NOTION_KEY       = SECRETS["notion_key"]
NOTION_PARENT_DB = SECRETS["notion_parent_db_id"]
NOTION_LEGACY_DB = SECRETS["notion_legacy_db_id"]
NOTION_NEWS_DB   = SECRETS["notion_news_db"]
TELEGRAM_TOKEN   = SECRETS["telegram_bot_token"]
TELEGRAM_DM      = SECRETS["telegram_dm"]
TELEGRAM_GROUP   = SECRETS["telegram_group"]
MINIMAX_API_KEY  = SECRETS.get("minimax_api_key", "")
BRAVE_API_KEY    = SECRETS.get("brave_api_key", "")
