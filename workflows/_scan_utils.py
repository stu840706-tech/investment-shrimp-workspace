#!/usr/bin/env python3
"""
Shared utilities for all scan modules.
"""

#!/usr/bin/env python3
"""
投資蝦每日市場掃描系統

- 月營收異常偵測
- 重大訊息掃描
- 三大法人籌碼追蹤
- 季財報異常（財報季執行：3/5/8/11月）
- 產業相對強弱分析
- Notion + Telegram 輸出
 """

from _common import NOTION_KEY, TELEGRAM_TOKEN, TELEGRAM_DM
from pathlib import Path
from datetime import datetime, timedelta
import time
import json
import requests
import math
from collections import defaultdict

from datetime import timezone, timedelta
TW_TZ = timezone(timedelta(hours=8))

def now_tw():
    """回傳 Taipei 時區的 datetime"""
    from datetime import datetime
    return datetime.now(TW_TZ)


# ==================== 全域設定 ====================

STATE_DIR = Path("/home/ubuntu/.openclaw/workspace/state")
STATE_DIR.mkdir(parents=True, exist_ok=True)

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.google.com',
}

# TWSE/Tpex rate limit: 3 calls / 5 sec

_last_call = 0.0
_call_lock = __import__('threading').Semaphore(1)

def rate_limit_wait():
    global _last_call
    with _call_lock:
        now = time.time()
        elapsed = now - _last_call
        if elapsed < 1.7:  # 3 calls per 5 sec → 1.67 sec between calls
            time.sleep(1.7 - elapsed)
        _last_call = time.time()

def get_twse_openapi(endpoint, params=None):
    """TWSE OpenAPI 抓取（帶速率限制）"""
    rate_limit_wait()
    url = f"https://openapi.twse.com.tw/v1/{endpoint.lstrip('/')}"
    resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_tpex_openapi(endpoint, params=None):
    """Tpex OpenAPI 抓取（帶速率限制）"""
    rate_limit_wait()
    url = f"https://www.tpex.org.tw/openapi/v1/{endpoint.lstrip('/')}"
    resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()

def get_twse_3insti(date_str):
    """
    TWSE 三大法人日成交統計（2024年起格式改了）
    date_str: YYYYMMDD
    """
    rate_limit_wait()
    url = "https://www.twse.com.tw/rwd/zh/fund/T86"
    params = {
        'date': date_str,
        'selectType': 'ALL',
        'response': 'json',
    }
    resp = requests.get(url, params=params, headers=BROWSER_HEADERS, timeout=30)
    data = resp.json()
    if isinstance(data, dict) and 'data' in data:
        fields = [f.strip() for f in data.get('fields', [])]
        records = []
        for row in data['data']:
            rec = dict(zip(fields, [c.strip() for c in row]))
            # 正規化：TWSE T86 欄位名稱相容舊名稱
            if '證券代號' in rec:
                rec.setdefault('股票代號', rec['證券代號'])
                rec.setdefault('股票名稱', rec.get('證券名稱', ''))
            # 動態找出「外陸資」買進/賣出/買賣超欄位（欄位名含括號後綴）
            fore_buy_key = next((k for k in rec if "外陸資買進" in k), None)
            fore_sell_key = next((k for k in rec if "外陸資賣出" in k), None)
            fore_net_key = next((k for k in rec if "外陸資買賣超" in k), None)
            if fore_buy_key:
                rec.setdefault('外援買進股數', rec[fore_buy_key])
            if fore_sell_key:
                rec.setdefault('外援賣出股數', rec[fore_sell_key])
            if fore_net_key:
                rec.setdefault('外援買賣超股數', rec[fore_net_key])
            records.append(rec)
        return records
        return records
    return []

def to_float(val, default=0.0):
    try:
        if val is None:
            return default
        v = str(val).replace(',', '').replace(' ', '').replace('–', '0').replace('NA', '0')
        return float(v) if v else default
    except:
        return default

def safe_div(a, b, default=0.0):
    try:
        return a / b if b and b != 0 else default
    except:
        return default

def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_twse_date(date_str):
    """TWSE 日期字串（YYYYMMDD 或 YYY/MM/DD）→ YYYYMMDD"""
    s = str(date_str).strip().replace('/', '').replace('-', '')
    if len(s) == 7:  # YYYMMDD format from some endpoints
        return s
    return s[:8]

def parse_roc_date(roc_str):
    """民國年 YYYMMDD → 西元 YYYYMMDD"""
    s = str(roc_str).strip().replace('/', '').replace('-', '')
    if len(s) == 7:
        s = '0' + s
    try:
        year = int(s[:3]) + 1911
        return f"{year}{s[3:]}"
    except:
        return datetime.now().strftime('%Y%m%d')

# ==================== Step 1: 月營收掃描 ====================
