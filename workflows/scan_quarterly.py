#!/usr/bin/env python3
"""
scan_quarterly — 每日掃描子模組
可獨立執行：python3 workflows/scan_quarterly.py
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

def check_financial_season():
    """檢查是否為財報季（3、5、8、11月）"""
    month = datetime.now().month
    return month in [3, 5, 8, 11]

def scan_quarterly_financials():
    """
    季財報異常偵測（僅財報季執行：3、5、8、11月）
    - 條件1a：三率齊升（QoQ>0 AND YoY>0）
    - 條件1b：三率齊升但營收衰退（轉型信號）
    - 條件2：毛利跳升（QoQ >3%科技/>1.5%傳產 + 營收上升）
    - 條件3：業外偏高警示（業外/稅後淨利 >30%）
    - 條件4：EPS 加速（EPS > avg*1.2 AND YoY>0）
    """
    if not check_financial_season():
        print("[Step 4] 非財報季，跳過")
        return []
    print("[Step 4] 季財報掃描（財報季）...")
    anomalies = []

    fin_history_file = STATE_DIR / "financial_history.json"
    fin_history = load_json(fin_history_file)

    # 抓 TWSE 季財報
    print(" [TWSE] 抓取季財報...")
    twse_fin = []
    for ep in ["/opendata/t187ap06_L_ci", "/opendata/t187ap06_L_ins", "/opendata/t187ap06_L_mim"]:
        try:
            data = get_twse_openapi(ep)
            twse_fin.extend(data)
        except Exception as e:
            print(f" → {ep} 失敗: {e}")

    # 抓 Tpex 季財報
    print(" [Tpex] 抓取季財報...")
    tpex_fin = []
    try:
        tpex_fin = get_tpex_openapi("/mopsfin_t187ap06_O_ci")
    except Exception as e:
        print(f" → Tpex 失敗: {e}")

    all_fin = twse_fin + tpex_fin
    print(f" → 共 {len(all_fin)} 筆記錄")

    # 解析季財報
    # TWSE fields: 公司代號, 公司名稱, 產業類別, 資料年月, 營業收入, 營業毛利, 營業利益, 稅前淨利, 本期淨利, 基本每股盈餘
    # Tpex fields: similar

    company_fin = {}
    for row in all_fin:
        try:
            code = str(row.get('公司代號', row.get('SecuritiesCompanyCode', ''))).strip()
            if not code:
                continue
            name = row.get('公司名稱', row.get('CompanyName', code))
            ym = str(row.get('資料年月', '')).strip()

            # 轉換為 YYYYQ 格式（財報季）
            if len(ym) == 5:
                roc_y = int(ym[:3])
                m = int(ym[3:])
            elif len(ym) == 6:
                roc_y = int(ym[:3])
                m = int(ym[3:])
            else:
                continue

            year = roc_y + 1911
            q = (m - 1) // 3 + 1
            yq = f"{year}Q{q}"

            revenue = to_float(row.get('營業收入', 0))
            gross_profit = to_float(row.get('營業毛利', 0))
            operating_profit = to_float(row.get('營業利益', 0))
            net_profit = to_float(row.get('本期淨利', row.get('稅後淨利', 0)))
            eps = to_float(row.get('基本每股盈餘', 0))

            if code not in company_fin:
                company_fin[code] = {'name': name, 'quarters': {}}
            company_fin[code]['quarters'][yq] = {
                'revenue': revenue,
                'gross_profit': gross_profit,
                'operating_profit': operating_profit,
                'net_profit': net_profit,
                'eps': eps,
            }
        except Exception as e:
            continue

    # 異常偵測
    stats = {'checked': 0, 'cond1': 0, 'cond2': 0, 'cond3': 0, 'cond4': 0, 'triggered': 0}

    for code, info in company_fin.items():
        quarters = info['quarters']
        if len(quarters) < 2:
            continue

        sorted_yqs = sorted(quarters.keys())
        cur_yq = sorted_yqs[-1]
        cur = quarters[cur_yq]

        revenue = cur['revenue']
        gross_profit = cur['gross_profit']
        operating_profit = cur['operating_profit']
        net_profit = cur['net_profit']
        eps = cur['eps']

        # 前一季
        prev_yq_idx = sorted_yqs.index(cur_yq) - 1
        prev_yq = sorted_yqs[prev_yq_idx] if prev_yq_idx >= 0 else None
        prev_q = quarters[prev_yq] if prev_yq else None

        # 去年同期（4季前）
        cur_year = int(cur_yq[:4])
        cur_q = int(cur_yq[5])
        yoy_y = cur_year - 1
        yoy_q_str = f"{yoy_y}Q{cur_q}"
        yoy_q = quarters.get(yoy_q_str)

        # ==== 條件1a：三率齊升 ====
        cond1a = False
        cond1a_rev_decline = False
        if prev_q and yoy_q:
            g_qoq = safe_div(gross_profit, prev_q['gross_profit']) - 1
            o_qoq = safe_div(operating_profit, prev_q['operating_profit']) - 1
            n_qoq = safe_div(net_profit, prev_q['net_profit']) - 1
            g_yoy = safe_div(gross_profit, yoy_q['gross_profit']) - 1
            o_yoy = safe_div(operating_profit, yoy_q['operating_profit']) - 1
            n_yoy = safe_div(net_profit, yoy_q['net_profit']) - 1
            rev_qoq = safe_div(revenue, prev_q['revenue']) - 1 if prev_q['revenue'] > 0 else 0

            if g_qoq > 0 and o_qoq > 0 and n_qoq > 0 and g_yoy > 0 and o_yoy > 0 and n_yoy > 0:
                cond1a = True
                stats['cond1'] += 1
                if rev_qoq < 0:
                    cond1a_rev_decline = True

        # ==== 條件2：毛利跳升 + 營收上升 ====
        cond2 = False
        if prev_q and prev_q['gross_profit'] > 0:
            gross_qoq_chg = (gross_profit - prev_q['gross_profit']) / prev_q['gross_profit'] * 100
            rev_qoq = safe_div(revenue, prev_q['revenue']) - 1 if prev_q['revenue'] > 0 else 0
            # 電子/科技（營收規模大 > 10億）/ 傳產簡單區分
            threshold = 3.0 if revenue > 10_000_000_000 else 1.5
            if gross_qoq_chg > threshold and rev_qoq > 0:
                cond2 = True
                stats['cond2'] += 1

        # ==== 條件3：業外偏高警示 ====
        cond3 = False
        non_op_ratio = 0.0
        if net_profit != 0:
            non_op = abs(net_profit - operating_profit)
            non_op_ratio = non_op / abs(net_profit)
            if non_op_ratio > 0.3:
                cond3 = True
                stats['cond3'] += 1

        # ==== 條件4：EPS 加速 ====
        cond4 = False
        if len(sorted_yqs) >= 5 and eps > 0:
            prev_4qs = [quarters[yq]['eps'] for yq in sorted_yqs[-5:-1] if quarters[yq]['eps'] > 0]
            if prev_4qs:
                avg_4q_eps = sum(prev_4qs) / len(prev_4qs)
                yoy_eps = safe_div(eps, yoy_q['eps']) - 1 if yoy_q and yoy_q['eps'] > 0 else 0
                if eps > avg_4q_eps * 1.2 and yoy_eps > 0:
                    cond4 = True
                    stats['cond4'] += 1

        stats['checked'] += 1

        # 觸發
        any_trigger = cond1a or cond1a_rev_decline or cond2 or cond3 or cond4
        if any_trigger:
            stats['triggered'] += 1
            flag_parts = []
            if cond1a:
                flag_parts.append('三率齊升')
            if cond1a_rev_decline:
                flag_parts.append('三率齊升(營收衰退-轉型信號)')
            if cond2:
                flag_parts.append(f'毛利跳升(+{gross_qoq_chg:.1f}%)')
            if cond3:
                flag_parts.append(f'業外偏高({non_op_ratio:.0%})')
            if cond4:
                flag_parts.append(f'EPS加速(+{(eps/(avg_4q_eps if avg_4q_eps else 1)-1)*100:.0f}%)')

            anomalies.append({
                'code': code,
                'name': info['name'],
                'quarter': cur_yq,
                'detail': ' | '.join(flag_parts),
                'flags': flag_parts,
                'source': 'TWSE',
            })

    # 更新歷史
    for code, info in company_fin.items():
        if code not in fin_history:
            fin_history[code] = {'name': info['name'], 'quarters': {}}
        for yq, qdata in info['quarters'].items():
            fin_history[code]['quarters'][yq] = qdata

    save_json(fin_history_file, fin_history)
    print(f" → 三率齊升: {stats['cond1']} | 毛利跳升: {stats['cond2']} | 業外偏高: {stats['cond3']} | EPS加速: {stats['cond4']}")
    print(f" → 符合觸發: {stats['triggered']} 筆")

    return anomalies

# ==================== Step 5: 產業相對強弱 ====================
