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
