#!/usr/bin/env python3
"""
scan_news — 每日掃描子模組
可獨立執行：python3 workflows/scan_news.py
也可被 daily-scan.py orchestrator 呼叫
"""

from _scan_utils import (
    rate_limit_wait, get_twse_openapi, get_tpex_openapi,
    get_twse_3insti, to_float, safe_div,
    load_json, save_json, parse_twse_date, parse_roc_date,
    STATE_DIR, BROWSER_HEADERS
)
from _common import FINMIND_TOKEN, TELEGRAM_TOKEN, TELEGRAM_DM
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
import time, json

def scan_material_news():
    """
    重大訊息關鍵字過濾
    """
    print("[Step 2] 重大訊息掃描...")
    anomalies = []

    # 關鍵字設定
    POSITIVE_HIGH = ['擴廠', '資本支出上修', '購置機器', '重大合約', '策略合作', '新產線', '量產', '導入客戶', '取得訂單', '出貨', '扩产', '资本支出']
    POSITIVE_MID = ['現金股利', '配息', '庫藏股買回', '買回庫藏股', '股息', '除息', '除權']
    WARNING_HIGH = ['解任', '辭職', '辭任', '轉讓持股', '申報轉讓', '停工', '火災', '客戶終止', '違約', '重編財報', '更換會計師', '掏空', '假帳', '破產', '清算', '下市', '下櫃']

    def check_keywords(text):
        text_lower = text.lower()
        flags = []
        level = None
        for kw in POSITIVE_HIGH:
            if kw in text:
                level = 'high'
                flags.append(f'[正{kw}]')
        for kw in POSITIVE_MID:
            if kw in text:
                if level is None:
                    level = 'mid'
                flags.append(f'[正{kw}]')
        for kw in WARNING_HIGH:
            if kw in text:
                level = 'warning'
                flags.append(f'[警{kw}]')
        return level, flags

    # TWSE
    print(" [TWSE] 抓取上市公司重大訊息...")
    try:
        twse_news = get_twse_openapi("/opendata/t187ap04_L")
        print(f" → TWSE {len(twse_news)} 筆記錄")
    except Exception as e:
        print(f" → TWSE 失敗: {e}")
        twse_news = []

    # Tpex
    print(" [Tpex] 抓取上櫃公司重大訊息...")
    try:
        tpex_news = get_tpex_openapi("/mopsfin_t187ap04_O")
        print(f" → Tpex {len(tpex_news)} 筆記錄")
    except Exception as e:
        print(f" → Tpex 失敗: {e}")
        tpex_news = []

    for row in twse_news:
        try:
            code = str(row.get('公司代號', '')).strip()
            name = row.get('公司名稱', '').strip()
            title = row.get('主旨 ', row.get('主旨', '')).strip()
            content = row.get('說明', title)
            date_str = row.get('發言日期', '').strip()

            level, flags = check_keywords(title + content)
            if level:
                anomalies.append({
                    'code': code,
                    'name': name,
                    'title': title[:80],
                    'level': level,
                    'flags': flags,
                    'date': date_str,
                    'source': 'TWSE',
                    'detail': ' '.join(flags),
                })
        except:
            continue

    for row in tpex_news:
        try:
            code = str(row.get('SecuritiesCompanyCode', row.get('公司代號', ''))).strip()
            name = row.get('CompanyName', row.get('公司名稱', '')).strip()
            title = row.get('主旨', '').strip()
            content = row.get('說明', title)
            date_str = row.get('發言日期', '').strip()

            level, flags = check_keywords(title + content)
            if level:
                anomalies.append({
                    'code': code,
                    'name': name,
                    'title': title[:80],
                    'level': level,
                    'flags': flags,
                    'date': date_str,
                    'source': 'Tpex',
                    'detail': ' '.join(flags),
                })
        except:
            continue

    print(f" → 發現 {len(anomalies)} 筆重大訊息")
    return anomalies

# ==================== Step 3: 三大法人 + 內部人追蹤 ====================
