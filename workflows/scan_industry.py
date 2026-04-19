#!/usr/bin/env python3
"""
scan_industry — 每日掃描子模組
可獨立執行：python3 workflows/scan_industry.py
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

def scan_industry_strength():
    """
    產業相對強弱分析：
    - 市佔率掠奪：個股營收YoY - 產業平均YoY > 10%
    - 逆勢抗跌：產業平均YoY < -10% AND 個股YoY > 0%
    """
    print("[Step 5] 產業相對強弱分析...")
    # 抓月營收建立產業分類
    print(" [TWSE] 抓取月營收建立產業分類...")
    try:
        twse_rev = get_twse_openapi("/opendata/t187ap05_L")
        print(f" → TWSE {len(twse_rev)} 筆記錄")
    except Exception as e:
        print(f" → TWSE 失敗: {e}")
        twse_rev = []

    print(" [Tpex] 抓取上櫃月營收...")
    try:
        tpex_rev = get_tpex_openapi("/mopsfin_t187ap05_O")
        print(f" → Tpex {len(tpex_rev)} 筆記錄")
    except Exception as e:
        print(f" → Tpex 失敗: {e}")
        tpex_rev = []

    all_rev = twse_rev + tpex_rev

    # 建立 stock_industry：股票代碼 → {name, industry, rev_cur, cur_month}
    stock_industry = {}
    api_cur_months = set()
    for row in all_rev:
        try:
            code = str(row.get('公司代號', '')).strip()
            name = row.get('公司名稱', '').strip()
            industry = row.get('產業別', '').strip()
            rev = to_float(row.get('營業收入-當月營收', '0'))
            date_val = str(row.get('資料年月', '')).strip()
            if not code or rev <= 0:
                continue
            # 動態解析當月月份（民國年 YYYMM → 西元 YYYYMM）
            ym_api = None
            if len(date_val) == 5:
                ym_api = f"{int(date_val[:3]) + 1911}{date_val[3:]}"
            elif len(date_val) == 6:
                ym_api = f"{int(date_val[:3]) + 1911}{date_val[3:]}"
            if ym_api:
                api_cur_months.add(ym_api)
                stock_industry[code] = {'name': name, 'industry': industry, 'rev_cur': rev, 'cur_month': ym_api}
        except:
            continue

    # 動態決定當月月份（取 API 中最新月份）
    api_cur_month = sorted(api_cur_months)[-1] if api_cur_months else datetime.now().strftime('%Y%m')

    # 讀取歷史 + 合併當月
    history_file = STATE_DIR / "revenue_history.json"
    history = load_json(history_file)

    for code, info in stock_industry.items():
        hist_months = history.get(code, {}).get('months', {})
        cur_rev = info.get('rev_cur', 0)
        cur_month = info.get('cur_month') or api_cur_month  # 動態取得，不再 hardcode
        all_months = dict(hist_months)
        if cur_rev > 0:
            all_months[cur_month] = cur_rev
        info['all_months'] = all_months
        info['cur_month'] = cur_month

    anomalies = []
    stats = {'checked': 0, 'cond1': 0, 'cond2': 0, 'triggered': 0}

    for code, info in stock_industry.items():
        all_months = info.get('all_months', {})
        if len(all_months) < 2:
            continue

        sorted_months = sorted(all_months.keys())
        cur_month = sorted_months[-1]
        cur_rev = all_months[cur_month]

        prev_year = str(int(cur_month[:4]) - 1) + cur_month[4:]
        prev_rev = all_months.get(prev_year)
        if not prev_rev or prev_rev <= 0:
            continue

        yoy_pct = (cur_rev - prev_rev) / prev_rev * 100
        yoy_pct = max(-99, min(500, yoy_pct))
        industry = info['industry']
        stats['checked'] += 1

        # 產業平均 YoY
        industry_yoys = []
        for c2, i2 in stock_industry.items():
            if i2['industry'] != industry or c2 == code:
                continue
            m2 = i2.get('all_months', {})
            if len(m2) < 2:
                continue
            sm2 = sorted(m2.keys())
            cur2 = sm2[-1]
            prev2 = str(int(cur2[:4]) - 1) + cur2[4:]
            if prev2 in m2 and m2[prev2] > 0:
                iy = (m2[cur2] - m2[prev2]) / m2[prev2] * 100
                iy = max(-99, min(500, iy))
                industry_yoys.append(iy)

        if not industry_yoys:
            continue

        avg_industry_yoy = sum(industry_yoys) / len(industry_yoys)
        cond1 = yoy_pct - avg_industry_yoy > 10
        cond2 = avg_industry_yoy < -10 and yoy_pct > 0

        if cond1:
            stats['cond1'] += 1
        if cond2:
            stats['cond2'] += 1

        if cond1 or cond2:
            stats['triggered'] += 1
            anomalies.append({
                'code': code,
                'name': info['name'],
                'industry': industry,
                'source': 'TWSE',
                'yoy_pct': round(yoy_pct, 1),
                'industry_yoy': round(avg_industry_yoy, 1),
                'detail': f"{'市佔率掠奪' if cond1 else '逆勢抗跌'} | 個股YoY:{yoy_pct:.0f}% 產業YoY:{avg_industry_yoy:.0f}%",
            })

    print(f" → 檢查 {stats['checked']} 間公司 / {len(set(info['industry'] for info in stock_industry.values()))} 個產業")
    print(f" → 市佔率掠奪: {stats['cond1']} | 逆勢抗跌: {stats['cond2']}")
    print(f" → 符合觸發: {stats['triggered']} 筆")

    return anomalies

# ==================== 輸出模組 ====================
