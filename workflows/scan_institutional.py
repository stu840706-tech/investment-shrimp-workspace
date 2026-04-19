#!/usr/bin/env python3
"""
scan_institutional — 每日掃描子模組
可獨立執行：python3 workflows/scan_institutional.py
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

def scan_3insti_chip():
    """
    三大法人日成交統計 + 5日連續買超追蹤 + 內部人持股異動
    """
    print("[Step 3] 三大法人籌碼掃描...")
    anomalies = []

    # 取得前一個交易日
    today = datetime.now()
    date_str = today.strftime('%Y%m%d')
    # TWSE 格式
    twse_date = today.strftime('%Y%m%d')

    # TWSE 三大法人
    print(f" [TWSE] 抓取上市公司三大法人 ({twse_date})...")
    try:
        twse_3i = get_twse_3insti(twse_date)
        print(f" → TWSE {len(twse_3i)} 筆記錄")
    except Exception as e:
        print(f" → TWSE 失敗: {e}")
        twse_3i = []

    # Tpex 三大法人
    print(f" [Tpex] 抓取上櫃公司三大法人 ({date_str})...")
    try:
        tpex_3i_raw = get_tpex_openapi("/tpex_3insti_daily_trading")
        # Tpex 回傳格式：{data: [...], fields: [...]}
        tpex_3i = []
        if isinstance(tpex_3i_raw, dict):
            fields = tpex_3i_raw.get('fields', [])
            for row in tpex_3i_raw.get('data', []):
                rec = dict(zip(fields, [c.strip() if isinstance(c, str) else c for c in row]))
                tpex_3i.append(rec)
        print(f" → Tpex {len(tpex_3i)} 筆記錄")
    except Exception as e:
        print(f" → Tpex 失敗: {e}")
        tpex_3i = []

    # 處理 TWSE 資料
    # fields: "股票代號","股票名稱","發行量加權股價報酬指數","外資買進股數","外資賣出股數","外資買賣超股數","投信買進股數","投信賣出股數","投信買賣超股數","自營商買進股數","自營商賣出股數","自營商買賣超股數"
    for row in twse_3i:
        try:
            code = str(row.get('股票代號', '')).strip()
            name = row.get('股票名稱', '').strip()
            if not code:
                continue

            # 嘗試解析數字（可能帶逗點或為空）
            def parse_num(s):
                try:
                    return to_float(s)
                except:
                    return 0.0

            fore_buy = parse_num(row.get('外資買進股數', 0))
            fore_sell = parse_num(row.get('外援賣出股數', 0))
            fore_net = parse_num(row.get('外援買賣超股數', 0))
            sec_buy = parse_num(row.get('投信買進股數', 0))
            sec_sell = parse_num(row.get('投信賣出股數', 0))
            sec_net = parse_num(row.get('投信買賣超股數', 0))

            # 單日門檻：外援>500萬股 OR 投信>100萬股
            fore_threshold = 5_000_000  # 500萬股
            sec_threshold = 1_000_000  # 100萬股

            reason = []
            if fore_net > fore_threshold:
                reason.append(f'外援買超{fore_net/10_000:.0f}張')
            if sec_net > sec_threshold:
                reason.append(f'投信買超{sec_net/10_000:.0f}張')

            if reason:
                anomalies.append({
                    'code': code,
                    'name': name,
                    'type': '法人買超',
                    'detail': ' '.join(reason),
                    'source': 'TWSE',
                })
        except Exception as e:
            continue

    # 處理 Tpex 資料
    # Tpex fields: 可能包含 股票代號/名稱, 外資, 投信, 自營商
    for row in tpex_3i:
        try:
            code = str(row.get('股票代號', row.get('SecuritiesCompanyCode', ''))).strip()
            name = str(row.get('股票名稱', row.get('CompanyName', ''))).strip()
            if not code or code == 'nan':
                continue

            fore_net = to_float(row.get('ForeignInvestorsNetBuy', 0))
            sec_net = to_float(row.get('SecuritiesInvestmentTrustNetBuy', 0))
            dealer_net = to_float(row.get('DealerNetBuy', 0))

            reason = []
            if fore_net > 5_000_000:
                reason.append(f'外援買超{fore_net/10_000:.0f}張')
            if sec_net > 1_000_000:
                reason.append(f'投信買超{sec_net/10_000:.0f}張')

            if reason:
                anomalies.append({
                    'code': code,
                    'name': name,
                    'type': '法人買超',
                    'detail': ' '.join(reason),
                    'source': 'Tpex',
                })
        except:
            continue

    # 5日連續買超追蹤
    chip_history_file = STATE_DIR / "3insti_history.json"
    chip_history = load_json(chip_history_file)

    # 更新歷史
    today_ymd = today.strftime('%Y%m%d')
    for item in anomalies:
        code = item['code']
        if code not in chip_history:
            chip_history[code] = {'name': item['name'], 'days': []}
        chip_history[code]['days'].append({'date': today_ymd, 'net': item.get('_raw_net', 0)})
        chip_history[code]['days'] = chip_history[code]['days'][-10:]  # 保留最近10天

    # 檢查5日連續買超
    for code, info in chip_history.items():
        days = info.get('days', [])
        if len(days) < 5:
            continue
        last5 = days[-5:]
        # 檢查是否全是買超（net > 0）
        if all(d.get('net', 0) > 0 for d in last5):
            total_net = sum(d.get('net', 0) for d in last5)
            # 只有不在當日 anomalies 的才加（避免重複）
            existing = [a for a in anomalies if a['code'] == code]
            has_already = any('連5日' in a.get('detail', '') for a in existing)
            if not has_already and total_net > 10_000_000:  # 5日合計買超>1000張
                anomalies.append({
                    'code': code,
                    'name': info['name'],
                    'type': '法人連續買超',
                    'detail': f'連5日買超，合計{total_net/10_000:.0f}張',
                    'source': 'TWSE',
                })

    save_json(chip_history_file, chip_history)

    # --- 內部人追蹤（董監事持股變動）---
    print(" [TWSE] 抓取董監事持股資料...")
    insider_file = STATE_DIR / "insider_history.json"
    insider_history = load_json(insider_file)

    try:
        twse_insider = get_twse_openapi("/opendata/t187ap11_L")
        tpex_insider = get_tpex_openapi("/mopsfin_t187ap11_O")
        all_insider = twse_insider + tpex_insider

        company_insider = defaultdict(list)
        for row in all_insider:
            code = str(row.get('公司代號', row.get('SecuritiesCompanyCode', ''))).strip()
            if code:
                company_insider[code].append(row)

        for code, rows in company_insider.items():
            try:
                if not rows:
                    continue
                name = rows[0].get('公司名稱', rows[0].get('CompanyName', code))

                total_change = 0
                count = 0
                for row in rows:
                    try:
                        sel = to_float(row.get('選任時持股 ', row.get('選任時持股', 0)))
                        curr = to_float(row.get('目前持股', 0))
                        if sel > 0:
                            pct = (curr - sel) / sel * 100
                            total_change += pct
                            count += 1
                    except:
                        continue

                avg_change = total_change / count if count > 0 else 0

                if code not in insider_history:
                    insider_history[code] = {'name': name, 'records': []}
                insider_history[code]['name'] = name
                insider_history[code]['records'].append({'change_pct': avg_change})
                insider_history[code]['records'] = insider_history[code]['records'][-5:]

                latest_change = insider_history[code]['records'][-1]['change_pct'] if insider_history[code]['records'] else 0

                # 警示：持股減少>5%
                if latest_change < -5:
                    anomalies.append({
                        'code': code,
                        'name': name,
                        'type': '內部人警示',
                        'detail': f'董監事持股減少{abs(latest_change):.1f}%',
                        'source': 'TWSE' if '公司代號' in rows[0] else 'Tpex',
                    })
            except:
                continue

        save_json(insider_file, insider_history)
        print(f" → 董監事資料更新: {len(company_insider)} 間公司")
    except Exception as e:
        print(f" → 董監事資料取得失敗: {e}")

    print(f" → 發現 {len(anomalies)} 筆法人交易")
    return anomalies

# ==================== Step 4: 季財報掃描（僅財報季執行） ====================
