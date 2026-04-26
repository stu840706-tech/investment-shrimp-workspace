#!/usr/bin/env python3
"""
fetch_universe.py - 抓取回測標的池
支援：0050成分股 / 台股全市場 / 追蹤清單
"""
import sys, json, urllib.request, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'workflows'))
from _common import FINMIND_TOKEN, SECRETS, NOTION_KEY

BASE_URL = "https://api.finmindtrade.com/api/v4/data"

def finmind_get(dataset, data_id="", start_date="2020-01-01"):
    params = {"dataset": dataset, "token": FINMIND_TOKEN}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    url = f"{BASE_URL}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())

def get_0050_components():
    """抓 0050 當前成分股"""
    data = finmind_get("TaiwanStockInfo")
    rows = data.get("data", [])
    # 篩選市值前50大（簡化版，實際應抓 ETF 持股）
    # 先用 TaiwanStockStockDividend 的 stock_id 清單代替
    data2 = finmind_get("Taiwan0050StocksInfo")
    if data2.get("data"):
        return [r["stock_id"] for r in data2["data"]]
    # fallback：用已知 0050 核心成分股
    return ["2330", "2317", "2454", "2382", "2308",
            "2881", "2882", "2886", "2891", "2303",
            "3711", "2412", "2002", "1301", "1303"]

def get_tracking_list():
    """從 Notion stock_tracking DB 抓追蹤清單"""
    db_id = SECRETS["notion_stock_tracking_db"]
    payload = json.dumps({
        "filter": {
            "or": [
                {"property": "狀態", "select": {"equals": "持有"}},
                {"property": "狀態", "select": {"equals": "未持有_看好"}},
                {"property": "狀態", "select": {"equals": "未持有_感興趣"}},
            ]
        },
        "page_size": 100
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        data=payload,
        headers={
            "Authorization": f"Bearer {NOTION_KEY}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    result = []
    for row in data.get("results", []):
        title = row.get("properties", {}).get("股票代碼", {}).get("title", [])
        if title:
            code = title[0].get("plain_text", "").strip()
            if code:
                result.append(code)
    return result

def get_universe(universe_type):
    """取得標的池"""
    if universe_type == "0050":
        stocks = get_0050_components()
        print(f"[fetch_universe] 0050 成分股：{len(stocks)} 檔")
    elif universe_type == "追蹤清單":
        stocks = get_tracking_list()
        print(f"[fetch_universe] 追蹤清單：{len(stocks)} 檔")
    else:
        # 台股全市場：先用 TaiwanStockInfo 篩選上市上櫃
        data = finmind_get("TaiwanStockInfo")
        stocks = [r["stock_id"] for r in data.get("data", [])
                  if r.get("type") in ("twse", "tpex")
                  and len(r.get("stock_id", "")) == 4]
        print(f"[fetch_universe] 台股全市場：{len(stocks)} 檔")
    return stocks

if __name__ == "__main__":
    universe = sys.argv[1] if len(sys.argv) > 1 else "0050"
    stocks = get_universe(universe)
    print(f"標的池：{stocks[:10]}...")
