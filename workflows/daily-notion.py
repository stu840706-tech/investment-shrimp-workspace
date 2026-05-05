#!/usr/bin/env python3
"""
daily-notion.py - 每日掃描結果寫入 Notion
每晚執行：讀取 scan_results_YYYYMMDD.json，寫入 Notion 子資料庫

資料來源：scan_results 的值（已正確計算），不重算 YoY/MoM/revenue
單位說明：scan_results.revenue 單位為千元；顯示時乘以 1000 轉為元

Notion 資料庫 Schema（11欄位）：

1. 股票名稱 (title)
2. 月營收 (rich_text) - "$值(年/月)" 格式，元
3. 月營收YoY (rich_text)
4. 月營收MoM (rich_text)
5. 營收利多 (multi_select)
6. 三率利多 (multi_select)
7. 籌碼面利多 (multi_select)
8. 股利條件 (multi_select)
9. 產業相對強弱 (multi_select)
10. 重大訊息 (rich_text)
11. 詳細內容(前面所有標籤的實際內容) (rich_text)
"""

import json, time, urllib.request, urllib.parse, re
from datetime import datetime, timedelta
from pathlib import Path
import argparse
from collections import defaultdict

# ==================== 設定 ====================

STATE_DIR = Path("/home/ubuntu/.openclaw/workspace/state")

from _common import now_tw,  FINMIND_TOKEN, NOTION_KEY, SECRETS
NOTION_VERSION = "2022-06-28"
PARENT_DB_ID = SECRETS["notion_parent_db_id"]  # 來自 https://www.notion.so/34e226f5a39880198606d2ce990675b0
SCAN_RESULTS_DB_ID = SECRETS["notion_scan_results_db"]  # B7: 固定 DB，不再動態建立

# ==================== Notion API ====================

def notion_post(url, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={
            'Authorization': f'Bearer {NOTION_KEY}',
            'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json'
        },
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))

def to_rich_text(text: str, limit: int = 1990) -> list:
    """
    Notion API rich_text 單一 block 上限 2000 字元。
    切分為多個 block 以避免 400 錯誤。
    """
    if not text:
        return []
    return [
        {"text": {"content": text[i:i+limit]}}
        for i in range(0, len(text), limit)
    ]

# ==================== 資料載入 ====================

def load_state():
    def load(name):
        p = STATE_DIR / name
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}
    return (
        load("revenue_history.json"),
        load("financial_history.json"),
        load("insider_history.json"),
        load("3insti_history.json"),
    )

def load_scan_results(date_str):
    file = STATE_DIR / f"scan_results_{date_str}.json"
    if not file.exists():
        yesterday = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
        file = STATE_DIR / f"scan_results_{yesterday.strftime('%Y%m%d')}.json"
    if file.exists():
        with open(file) as f:
            return json.load(f)
    return None

# ==================== 指標計算 ====================

def calc_revenue_tags(code, scan_results, revenue_history):
    """
    月營收標籤：直接從 scan_results 取 flags，
    不依賴 revenue_history 重算（避免單位不一致問題）。
    同時從 revenue_history 補充「近兩年新高」標籤（需要 24 個月資料）。
    回傳 (month_str, yoy_str, mom_str, tags, details)
    """
    # 從 scan_results 取當月資料
    rev_entry = next((
        e for e in scan_results.get('results', {}).get('月營收異常', [])
        if str(e.get('code', '')) == str(code)),
        None
    )
    month_str = yoy_str = mom_str = ""
    tags = []
    details = []
    # 從 revenue_history 取最新月份（用於格式化日期）
    hist = revenue_history.get(str(code), {})
    hist_months = hist.get('months', {})
    sorted_months = sorted(hist_months.keys(), reverse=True)
    latest_ym = sorted_months[0] if sorted_months else ""

    if rev_entry:
        revenue_qian = rev_entry.get('revenue', 0)  # 千元
        yoy_pct = rev_entry.get('yoy_pct')
        mom_pct = rev_entry.get('mom_pct')
        flags = rev_entry.get('flags', [])

        # 格式化月營收（千元 × 1000 = 元）
        if revenue_qian and revenue_qian > 0:
            revenue_yuan = revenue_qian * 1000
            ym = latest_ym or ""
            ym_display = f"{ym[:4]}/{ym[4:]}" if len(ym) >= 6 else ""
            month_str = f"${revenue_yuan:,.0f}({ym_display})"

        yoy_str = f"{yoy_pct:.1f}%" if yoy_pct is not None else ""
        mom_str = f"{mom_pct:.1f}%" if mom_pct is not None else ""

        # 從 flags 對應標籤
        flag_map = {
            '雙增': ('營收雙增(YoY/MoM>10%)',
                     f"YoY:{yoy_pct:.1f}% MoM:{mom_pct:.1f}%" if yoy_pct is not None else "雙增"),
            '上市新高': ('營收創歷史新高', '本月營收創上市以來新高'),
            '近2年高': ('營收創近兩年新高', '本月營收創近24個月新高'),
            '連3月遞增': ('營收連續成長月數 > 3', '連續3個月環比遞增'),
        }
        for flag in flags:
            if flag in flag_map:
                tag, detail = flag_map[flag]
                tags.append(tag)
                details.append(detail)

    return month_str, yoy_str, mom_str, tags, details

def calc_financial_tags(code, financial_history, industry):
    """季財報標籤：從 financial_history 計算三率、EPS，毛利跳升。"""
    fin_data = financial_history.get(str(code), {})
    quarters = fin_data.get('quarters', {})
    if not quarters:
        return [], []
    sorted_qs = sorted(quarters.keys(), reverse=True)
    tags = []
    details = []

    def get_q(qkey):
        if qkey not in quarters:
            return None
        d = quarters[qkey]
        rev  = d.get('Revenue', d.get('revenue', 0)) or 0
        gp   = d.get('GrossProfit', d.get('gross_profit', 0)) or 0
        op   = d.get('OperatingIncome', d.get('operating_profit', 0)) or 0
        np_  = d.get('IncomeAfterTaxes', d.get('net_profit', 0)) or 0
        eps  = d.get('EPS', d.get('eps', 0)) or 0
        non_op = d.get('TotalNonoperatingIncomeAndExpense', d.get('non_op', 0)) or 0
        def margin(val):
            return val / rev * 100 if rev != 0 else 0
        return {'rev': rev, 'gp': gp, 'op': op, 'np': np_, 'eps': eps,
                'non_op': non_op, 'gm': margin(gp), 'om': margin(op), 'nm': margin(np_)}

    latest_q = sorted_qs[0] if sorted_qs else None
    prev_q_key = sorted_qs[1] if len(sorted_qs) > 1 else None

    # 去年同期
    yoy_q_key = None
    if latest_q:
        parts = latest_q.split('_')
        if len(parts) == 2:
            yoy_q_key = f"{int(parts[0])-1}_{parts[1]}"

    cur  = get_q(latest_q)
    prev = get_q(prev_q_key)
    yoy  = get_q(yoy_q_key)

    if not cur:
        return [], []

    # 三率齊升
    if prev and yoy:
        if (cur['gm'] > prev['gm'] and cur['om'] > prev['om'] and cur['nm'] > prev['nm'] and
            cur['gm'] > yoy['gm'] and cur['om'] > yoy['om'] and cur['nm'] > yoy['nm']):
            tags.append("三率齊升")
            details.append(
                f"三率齊升: 毛利率{cur['gm']:.1f}%(QoQ+{cur['gm']-prev['gm']:.1f}%), "
                f"營益率{cur['om']:.1f}%(QoQ+{cur['om']-prev['om']:.1f}%), "
                f"淨利率{cur['nm']:.1f}%(QoQ+{cur['nm']-prev['nm']:.1f}%)"
            )

    # 毛利跳升
    if prev and prev['gm'] != 0:
        gm_qoq = cur['gm'] - prev['gm']
        is_tech = any(k in (industry or '') for k in ['半導體', '電子', '積體電路', '光電'])
        threshold = 3.0 if is_tech else 1.5
        if gm_qoq > threshold and cur['rev'] > prev['rev']:
            tag_name = "毛利跳升(電子股 QoQ 增加 > 3%)" if is_tech else "毛利跳升(傳產股 QoQ 增加 > 1.5%)"
            tags.append(tag_name)
            details.append(f"毛利跳升: QoQ+{gm_qoq:.1f}%, 當期毛利率{cur['gm']:.1f}%")

    # EPS 加速
    if cur['eps'] and cur['eps'] > 0 and len(sorted_qs) >= 5:
        prev4_eps = [get_q(q)['eps'] for q in sorted_qs[1:5] if get_q(q) and get_q(q)['eps'] > 0]
        if prev4_eps:
            avg_eps = sum(prev4_eps) / len(prev4_eps)
            if avg_eps > 0 and cur['eps'] > avg_eps * 1.2:
                yoy_eps_pct = ((cur['eps'] - yoy['eps']) / yoy['eps'] * 100) if yoy and yoy['eps'] > 0 else 0
                if yoy_eps_pct > 0:
                    tags.append("EPS > 過去 4 季平均 * 1.2")
                    details.append(f"EPS加速: {cur['eps']} > 過去4季均值{avg_eps:.2f}×1.2, YoY+{yoy_eps_pct:.0f}%")

    # 業外偏高警示（不加標籤，但加進詳細內容）
    if cur['np'] and abs(cur['np']) > 0:
        non_op_ratio = abs(cur['non_op']) / abs(cur['np'])
        if non_op_ratio > 0.3:
            details.append(f"⚠️業外偏高: {non_op_ratio:.0%} (業外/稅後淨利)，EPS含金量偏低")

    return tags, details

def calc_chip_tags(code, scan_results, insider_history, insti_history):
    """
    籌碼面標籤：從 scan_results['三大法人'] 取，涵蓋：
    - 法人買超（外資/投信/合計）
    - 法人連續買超
    - 內部人警示（董監申報轉讓）
    - 董監事公開市場買進
    - 融資高水位 / 融資暴增 / 融券暴增（FinMind）
    """
    tags = []
    details = []

    for entry in scan_results.get('results', {}).get('三大法人', []):
        if str(entry.get('code', '')) != str(code):
            continue
        etype = entry.get('type', '')
        detail_str = entry.get('detail', '')

        if etype == '內部人警示' or '申報轉讓' in detail_str or '持股減少' in detail_str:
            tags.append("董監事或大股東申報轉讓 > 持股 5%")
            details.append(f"⚠️{detail_str}")

        elif etype == '法人連續買超':
            tags.append("外資連續買超")
            details.append(f"連續買超: {detail_str}")

        elif etype == '法人買超' or '買超' in detail_str:
            if '外資' in detail_str:
                tags.append("外資買超")
            elif '投信' in detail_str:
                tags.append("投信買超")
            else:
                tags.append("三大法人合計買超")
            details.append(detail_str)

        elif '買進' in detail_str:
            tags.append("董監事公開市場買進")
            details.append(detail_str)

        elif etype == '融資高水位':
            tags.append("融資高水位")
            details.append(f"⚠️融資高水位: {detail_str}")

        elif etype == '融資暴增':
            tags.append("融資暴增")
            details.append(f"⚠️融資暴增: {detail_str}")

        elif etype == '融券暴增':
            tags.append("融券暴增")
            details.append(f"⚠️融券暴增: {detail_str}")

    # insider_history 補充（當三大法人無資料時的備援）
    if str(code) in insider_history and not tags:
        records = insider_history[str(code)].get('records', [])
        if records:
            latest = records[-1].get('change_pct', 0)
            if latest > 0:
                tags.append("董監持股增")
                details.append(f"董監事買進: +{latest:.2f}%")
            elif latest < -5:
                tags.append("董監事或大股東申報轉讓 > 持股 5%")
                details.append(f"⚠️董監事持股減少: {latest:.2f}%")

    return tags, details

def calc_industry_tags(code, scan_results):
    """
    產業相對強弱標籤：讀 detail 欄位（flags 欄位是空陣列，資訊在 detail）。
    """
    tags = []
    details = []
    for entry in scan_results.get('results', {}).get('產業強度', []):
        if str(entry.get('code', '')) != str(code):
            continue
        detail = entry.get('detail', '')
        if '市佔率掠奪' in detail:
            tags.append("個股營收 YOY - 產業平均 > 10%")
            details.append(detail)
        if '逆勢抗跌' in detail:
            tags.append("個股營收 YOY>0%/產業平均<-10%")
            details.append(detail)
    return tags, details

def collect_major_news(code, scan_results):
    """
    重大訊息：只從 scan_results['重大訊息'] 取，不混入月營收 detail。
    每則訊息完整保留 title。
    """
    news = []
    for entry in scan_results.get('results', {}).get('重大訊息', []):
        if str(entry.get('code', '')) != str(code):
            continue
        title = (entry.get('title') or '').strip()
        flags = entry.get('flags', [])
        level = entry.get('level', '')
        level_tag = '⚠️' if level == 'warning' else '📢'
        if title:
            news.append(f"{level_tag} {title}")
        elif flags:
            news.append(f"{level_tag} {'  '.join(flags)}")
    return news

# ==================== Notion 寫入 ====================

def create_page_and_database(date_str, scan_results):
    print(f"\n{'='*60}")
    print(f"建立 Notion 頁面: {date_str}")
    print(f"{'='*60}")

    # Step 1: 建立父頁面
    page_payload = {
        "parent": {"type": "page_id", "page_id": PARENT_DB_ID},
        "properties": {
            "title": {"title": [{"text": {"content": f"{date_str[:4]}年{date_str[4:6]}月{date_str[6:8]}日"}}]}
        }  }
    page = notion_post("https://api.notion.com/v1/pages", page_payload)
    page_id = page.get('id')
    print(f" [+] 頁面建立: {page_id}")

        # Step 2: B7 改用固定 scan_results DB（不再動態建立子 DB）
    db_id = SCAN_RESULTS_DB_ID
    print(f" [+] 使用固定資料庫: {db_id}")
    print(f" [+] 資料庫建立: {db_id}")

    # Step 3: 收集公司（合併所有分類，同一 code 不丟棄任何來源）
    # ★ Bug fix: 改用 defaultdict 合併，不再先到先得
    companies = defaultdict(lambda: {'name': '', 'entries': defaultdict(list)})
    results = scan_results.get('results', {})

    for category in ['月營收異常', '重大訊息', '三大法人', '產業強度']:
        for entry in results.get(category, []):
            code = str(entry.get('code', '')).strip()
            name = entry.get('name', '').strip()
            if not code:
                continue
            if name:
                companies[code]['name'] = name
            companies[code]['entries'][category].append(entry)

    print(f" [*] 準備填入 {len(companies)} 間公司")

    # 載入 state 資料
    revenue_hist, financial_hist, insider_hist, insti_hist = load_state()

    added = 0
    for code, info in companies.items():
        name = info['name']
        industry = revenue_hist.get(code, {}).get('industry', '')

        # ★ Bug fix: 各分類各自計算，不合併再拆
        month_str, yoy_str, mom_str, rev_tags, rev_details = calc_revenue_tags(
            code, scan_results, revenue_hist
        )
        fin_tags, fin_details = calc_financial_tags(code, financial_hist, industry)
        chip_tags, chip_details = calc_chip_tags(code, scan_results, insider_hist, insti_hist)
        ind_tags, ind_details = calc_industry_tags(code, scan_results)

        # 重大訊息（只取 scan_results 重大訊息分類，不混入月營收 detail）
        news_list = collect_major_news(code, scan_results)
        news_text = "\n".join(
            [f"{i+1}. {n}" for i, n in enumerate(news_list)]
        ) if news_list else ""

        # 詳細內容（所有分類合併，標示來源）
        all_details = []
        if rev_details:
            all_details.append("【月營收】")
            all_details.extend(rev_details)
        if fin_details:
            all_details.append("【季財報】")
            all_details.extend(fin_details)
        if chip_details:
            all_details.append("【籌碼面】")
            all_details.extend(chip_details)
        if ind_details:
            all_details.append("【產業強度】")
            all_details.extend(ind_details)

        # 章節標題不編號，內容項目連續編號
        detail_lines = []
        item_num = 0
        for d in all_details:
            if d.startswith('【'):
                detail_lines.append(d)
            else:
                item_num += 1
                detail_lines.append(f"{item_num}. {d}")
        detail_text = "\n".join(detail_lines) if detail_lines else ""

        # 建立 Notion properties
        props = {
            "股票名稱": {"title": [{"text": {"content": f"{name}/{code}"}}]},
        }

        if month_str:
            props["月營收"] = {"rich_text": to_rich_text(month_str)}
        if yoy_str:
            props["月營收YoY"] = {"rich_text": to_rich_text(yoy_str)}
        if mom_str:
            props["月營收MoM"] = {"rich_text": to_rich_text(mom_str)}

        # ★ Bug fix: 直接填各自標籤，不合併再拆
        props["營收利多"] = {"multi_select": [{"name": t} for t in rev_tags]}
        props["三率利多"] = {"multi_select": [{"name": t} for t in fin_tags]}
        props["籌碼面利多"] = {"multi_select": [{"name": t} for t in chip_tags]}
        props["股利條件"] = {"multi_select": []}  # 需要額外資料來源，目前留空
        props["產業相對強弱"] = {"multi_select": [{"name": t} for t in ind_tags]}

        if news_text:
            # ★ Bug fix: rich_text 超長切分，避免 400 錯誤
            props["重大訊息"] = {"rich_text": to_rich_text(news_text)}

        if detail_text:
            props["詳細內容"] = {"rich_text": to_rich_text(detail_text)}

        page_payload = {
            "parent": {"database_id": db_id},
            "properties": props
        }

        try:
            result = notion_post("https://api.notion.com/v1/pages", page_payload)
            if result.get('id'):
                added += 1
                if added % 20 == 0:
                    print(f" ... 已填入 {added} 間")
        except Exception as e:
            print(f" [!] {code} {name} 失敗: {e}")

        time.sleep(0.35)

    print(f"\n [✓] 完成! 共填入 {added} 間公司")
    return page_id, db_id

# ==================== 主程式 ====================

def main():
    parser = argparse.ArgumentParser(description="每日掃描結果寫入 Notion")
    parser.add_argument('--date', help='指定掃描結果日期 (YYYYMMDD，預設為昨天)')
    parser.add_argument('--test', action='store_true', help='測試模式：只處理 2 間公司')
    args = parser.parse_args()

    print("=" * 60)
    print(f"daily-notion.py 執行中 {now_tw().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    if args.date:
        date_str = args.date.replace('/', '').replace('-', '')
    else:
        date_str = next((f.stem.replace("scan_results_","") for f in sorted(Path("/home/ubuntu/.openclaw/workspace/state").glob("scan_results_*.json"), reverse=True)), (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))

    scan_results = load_scan_results(date_str)
    if not scan_results:
        print(f"錯誤: 無法找到 scan_results_{date_str}.json")
        return

    if args.test:
        print("*** 測試模式：每類別只留 2 間 ***")
        for cat in ['月營收異常', '重大訊息', '三大法人', '產業強度']:
            if cat in scan_results['results']:
                scan_results['results'][cat] = scan_results['results'][cat][:2]

    print(f"使用掃描結果: scan_results_{date_str}.json")
    results = scan_results.get('results', {})
    for cat in ['月營收異常', '重大訊息', '三大法人', '產業強度']:
        print(f"  {cat}: {len(results.get(cat, []))} 筆")

    page_id, db_id = create_page_and_database(date_str, scan_results)

    print(f"\n{'='*60}")
    print(f"完成!")
    print(f"頁面: https://notion.so/{page_id.replace('-', '')}")
    print(f"資料庫: https://notion.so/{db_id.replace('-', '')}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
