#!/usr/bin/env python3
"""
scan_revenue — 每日掃描子模組
可獨立執行：python3 workflows/scan_revenue.py
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

def scan_monthly_revenue():
    """
    月營收異常偵測邏輯：
    - 條件1（雙增）：YoY > 10% AND MoM > 10%
    - 條件2a（上市以來新高）：當月 >= 歷史最高
    - 條件2b（近2年新高）：當月 >= 近24個月滾動最高
    - 條件4（連續成長）：連續3+月 MoM 正成長且遞增
    """
    print("[Step 1] 月營收掃描...")
    anomalies = []

    # TWSE 月營收
    print(" [TWSE] 抓取上市公司月營收...")
    try:
        twse_revenue = get_twse_openapi("/opendata/t187ap05_L")
        print(f" → TWSE {len(twse_revenue)} 筆記錄")
    except Exception as e:
        print(f" → TWSE 失敗: {e}")
        twse_revenue = []

    # Tpex 月營收
    print(" [Tpex] 抓取上櫃公司月營收...")
    try:
        tpex_revenue = get_tpex_openapi("/mopsfin_t187ap05_O")
        print(f" → Tpex {len(tpex_revenue)} 筆記錄")
    except Exception as e:
        print(f" → Tpex 失敗: {e}")
        tpex_revenue = []

    # 載入歷史
    history_file = STATE_DIR / "revenue_history.json"
    history = load_json(history_file)

    all_stocks = {}

    # 處理 TWSE
    for row in twse_revenue:
        try:
            code = str(row.get('公司代號', '')).strip()
            name = row.get('公司名稱', '').strip()
            industry = row.get('產業別', '').strip()
            rev_str = row.get('營業收入-當月營收', '0')
            prev_rev_str = row.get('營業收入-上月營收', '0')
            last_year_str = row.get('營業收入-去年當月營收', '0')
            yoy_pct_api = to_float(row.get('營業收入-去年同月增減(%)', 0))
            mom_pct_api = to_float(row.get('營業收入-上月比較增減(%)', 0))

            rev = to_float(rev_str)
            prev_rev = to_float(prev_rev_str)
            last_year_rev = to_float(last_year_str) if last_year_str else 0

            # Date: 資料年月 in ROC format (5-char: YYYMM)
            date_val = str(row.get('資料年月', '')).strip()
            if len(date_val) == 5:
                roc_y = int(date_val[:3])
                yyyy = roc_y + 1911
                ym = f"{yyyy}{date_val[3:]}"
            elif len(date_val) == 6:
                ym = str(int(date_val[:3]) + 1911) + date_val[3:]
            else:
                continue

            if not code or rev <= 0:
                continue

            if code not in all_stocks:
                all_stocks[code] = {'name': name, 'industry': industry, 'source': 'TWSE', 'months': {}}
            all_stocks[code]['months'][ym] = rev
            all_stocks[code]['industry'] = industry or all_stocks[code].get('industry', '')
            # Store YoY/MoM from API if available
            if yoy_pct_api != 0 or mom_pct_api != 0:
                if 'yoy_pct_api' not in all_stocks[code]:
                    all_stocks[code]['yoy_pct_api'] = {}
                    all_stocks[code]['mom_pct_api'] = {}
                all_stocks[code]['yoy_pct_api'][ym] = yoy_pct_api
                all_stocks[code]['mom_pct_api'][ym] = mom_pct_api

        except Exception as e:
            continue

    # 處理 Tpex
    for row in tpex_revenue:
        try:
            code = str(row.get('公司代號', '')).strip()
            name = row.get('公司名稱', '').strip()
            industry = row.get('產業別', '').strip()
            rev_str = row.get('營業收入-當月營收', '0')
            prev_rev_str = row.get('營業收入-上月營收', '0')
            last_year_str = row.get('營業收入-去年當月營收', '0')
            yoy_pct_api = to_float(row.get('營業收入-去年同月增減(%)', 0))
            mom_pct_api = to_float(row.get('營業收入-上月比較增減(%)', 0))

            rev = to_float(rev_str)
            prev_rev = to_float(prev_rev_str)
            last_year_rev = to_float(last_year_str) if last_year_str else 0

            date_val = str(row.get('資料年月', '')).strip()
            if len(date_val) == 5:
                roc_y = int(date_val[:3])
                yyyy = roc_y + 1911
                ym = f"{yyyy}{date_val[3:]}"
            elif len(date_val) == 6:
                ym = str(int(date_val[:3]) + 1911) + date_val[3:]
            else:
                continue

            if not code or rev <= 0:
                continue

            if code not in all_stocks:
                all_stocks[code] = {'name': name, 'industry': industry, 'source': 'Tpex', 'months': {}}
            all_stocks[code]['months'][ym] = rev
            all_stocks[code]['industry'] = industry or all_stocks[code].get('industry', '')
            if yoy_pct_api != 0 or mom_pct_api != 0:
                if 'yoy_pct_api' not in all_stocks[code]:
                    all_stocks[code]['yoy_pct_api'] = {}
                    all_stocks[code]['mom_pct_api'] = {}
                all_stocks[code]['yoy_pct_api'][ym] = yoy_pct_api
                all_stocks[code]['mom_pct_api'][ym] = mom_pct_api

        except Exception as e:
            continue

    # 更新歷史並判斷異常
    stats = {'checked': 0, 'cond1': 0, 'cond2_all': 0, 'cond2_2y': 0, 'cond4': 0, 'triggered': 0}

    for code, info in all_stocks.items():
        # 合併：歷史資料 + 當月最新資料
        hist_months = history.get(code, {}).get('months', {})
        cur_months = info['months']  # 當月API資料
        all_months = dict(hist_months)  # 先拷貝歷史
        all_months.update(cur_months)  # 再疊加當月（覆蓋歷史）

        if not all_months:
            continue

        # 更新歷史（TWSE t187ap05_L 已是千元單位，直接儲存）
        if code not in history:
            history[code] = {'name': info['name'], 'industry': info['industry'], 'months': {}}
        for ym, rev in cur_months.items():
            history[code]['months'][ym] = rev

        sorted_months = sorted(all_months.keys())
        if len(sorted_months) < 2:
            continue

        cur_month = sorted_months[-1]
        cur_rev = all_months[cur_month]

        # YoY: 找去年同期
        yoy_ym = None
        prev_year = int(cur_month[:4]) - 1
        for m in sorted_months:
            if m.startswith(str(prev_year)) and m[4:] == cur_month[4:]:
                yoy_ym = m
                break

        # MoM: 前一個月
        mom_ym = None
        for i, m in enumerate(sorted_months):
            if m == cur_month and i > 0:
                mom_ym = sorted_months[i - 1]
                break

        # 優先使用 API 預算好的 YoY/MoM，否則自己計算
        yoy_pct_api_map = info.get('yoy_pct_api', {})
        mom_pct_api_map = info.get('mom_pct_api', {})

        yoy_pct = yoy_pct_api_map.get(cur_month)
        mom_pct = mom_pct_api_map.get(cur_month)

        if yoy_pct is None:
            if yoy_ym and yoy_ym in all_months and all_months[yoy_ym] > 0:
                yoy_pct = (cur_rev - all_months[yoy_ym]) / all_months[yoy_ym] * 100
        if mom_pct is None:
            if mom_ym and mom_ym in all_months and all_months[mom_ym] > 0:
                mom_pct = (cur_rev - all_months[mom_ym]) / all_months[mom_ym] * 100

        # ==== 條件1：雙增 ====
        cond1 = yoy_pct is not None and mom_pct is not None and yoy_pct > 10 and mom_pct > 10

        # ==== 條件2a：上市以來新高 ====
        cond2_all = False
        if all_months.get(yoy_ym) or yoy_ym is None:
            hist_max = max(all_months.values())
            cond2_all = cur_rev >= hist_max

        # ==== 條件2b：近2年新高 ====
        cond2_2y = False
        cutoff_ym = str(int(cur_month[:4]) - 2) + cur_month[4:]
        recent_months = {m: v for m, v in all_months.items() if m >= cutoff_ym}
        if recent_months:
            rolling_max = max(recent_months.values())
            cond2_2y = cur_rev >= rolling_max

        # ==== 條件4：連續3月遞增 ====
        cond4 = False
        if len(sorted_months) >= 3:
            last3 = sorted_months[-3:]
            vals = [all_months[m] for m in last3]
            if vals[0] < vals[1] < vals[2]:  # 連續遞增
                # 檢查這3個月的 MoM 是否皆為正
                m1_mom = safe_div(vals[1] - vals[0], vals[0]) * 100
                m2_mom = safe_div(vals[2] - vals[1], vals[1]) * 100
                cond4 = m1_mom > 0 and m2_mom > 0

        # 更新統計
        stats['checked'] += 1
        if cond1:
            stats['cond1'] += 1
        if cond2_all:
            stats['cond2_all'] += 1
        if cond2_2y:
            stats['cond2_2y'] += 1
        if cond4:
            stats['cond4'] += 1

        # 觸發：任一條件
        cond_neg = yoy_pct is not None and yoy_pct < -10
        any_trigger = cond1 or cond2_all or cond2_2y or cond4 or cond_neg
        if any_trigger:
            stats['triggered'] += 1
            flag_parts = []
            if cond1:
                flag_parts.append('雙增')
            if cond_neg:
                flag_parts.append('營收下滑')
            if cond2_all:
                flag_parts.append('上市新高')
            if cond2_2y:
                flag_parts.append('近2年高')
            if cond4:
                flag_parts.append('連3月遞增')

            anomalies.append({
                'code': code,
                'name': info['name'],
                'industry': info['industry'],
                'source': info['source'],
                'revenue': cur_rev,
                'yoy_pct': round(yoy_pct, 1) if yoy_pct is not None else None,
                'mom_pct': round(mom_pct, 1) if mom_pct is not None else None,
                'detail': f"{' + '.join(flag_parts)} | YoY:{yoy_pct:.0f}% MoM:{mom_pct:.0f}%" if yoy_pct is not None else '',
                'flags': flag_parts,
            })

    # 儲存歷史
    save_json(history_file, history)
    print(f" → 檢查 {stats['checked']} 間公司")
    print(f" → 條件1(雙增): {stats['cond1']} | 條件2a(上市新高): {stats['cond2_all']} | 條件2b(近2年高): {stats['cond2_2y']} | 條件4(連3月遞增): {stats['cond4']}")
    print(f" → 符合觸發(C1_OR_C2_OR_C4): {stats['triggered']} 筆")

    return anomalies

# ==================== Step 2: 重大訊息掃描 ====================
