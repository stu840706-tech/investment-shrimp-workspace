#!/usr/bin/env python3
"""fetch_broker_summary.py - 從 Notion broker_reports DB 讀取該股最近5份券商報告摘要"""
import sys, json, urllib.request
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import SECRETS, NOTION_KEY

NOTION_VERSION = "2022-06-28"
BROKER_REPORTS_DB = SECRETS["notion_broker_reports_db"]

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
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def fetch_broker_summary(stock_id):
    """讀取 broker_reports DB 該股最近5份報告"""
    payload = {
        "filter": {
            "property": "股票代碼",
            "title": {"equals": stock_id}
        },
        "sorts": [{"property": "報告日期", "direction": "descending"}],
        "page_size": 5
    }
    result = notion_post(
        f"https://api.notion.com/v1/databases/{BROKER_REPORTS_DB}/query",
        payload
    )
    rows = result.get("results", [])
    reports = []
    for row in rows:
        props = row.get("properties", {})
        def get_text(key):
            p = props.get(key, {})
            rt = p.get("rich_text", [])
            return rt[0]["plain_text"] if rt else ""
        def get_num(key):
            return props.get(key, {}).get("number")
        def get_select(key):
            s = props.get(key, {}).get("select")
            return s["name"] if s else ""
        def get_date(key):
            d = props.get(key, {}).get("date")
            return d["start"] if d else ""

        reports.append({
            "date": get_date("報告日期"),
            "broker": get_select("券商名稱"),
            "rating": get_select("評等"),
            "target_price": get_num("目標價"),
            "core_view": get_text("核心觀點"),
            "revenue_this_year": get_num("營收預測_今年"),
            "revenue_next_year": get_num("營收預測_明年"),
            "eps_this_year": get_num("EPS預測_今年"),
            "eps_next_year": get_num("EPS預測_明年"),
            "gross_margin_est": get_num("毛利率預測"),
            "pe_est": get_num("PE估值"),
        })
    return reports

def main(stock_id):
    print(f"[fetch_broker_summary] 讀取 {stock_id} 券商報告...")
    reports = fetch_broker_summary(stock_id)
    out_path = Path(__file__).parent.parent.parent.parent / "state" / f"research_{stock_id}_broker.json"
    result = {
        "stock_id": stock_id,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reports": reports,
        "count": len(reports)
    }
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[fetch_broker_summary] 找到 {len(reports)} 份報告")
    for r in reports:
        print(f"  {r['date']} {r['broker']} {r['rating']} TP:{r['target_price']} | {r['core_view'][:30]}")
    return result

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    main(stock_id)