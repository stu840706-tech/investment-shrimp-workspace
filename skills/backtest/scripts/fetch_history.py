#!/usr/bin/env python3
"""
fetch_history.py - 抓取歷史股價與財務資料供回測使用
"""
import sys, json, time, urllib.request, urllib.parse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import FINMIND_TOKEN

BASE_URL = "https://api.finmindtrade.com/api/v4/data"

def finmind_get(dataset, stock_id, start_date, end_date=""):
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "token": FINMIND_TOKEN
    }
    if end_date:
        params["end_date"] = end_date
    url = f"{BASE_URL}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())

def fetch_price_history(stock_id, start_date, end_date):
    """抓取日K資料"""
    data = finmind_get("TaiwanStockPrice", stock_id, start_date, end_date)
    rows = data.get("data", [])
    return {r["date"]: {
        "open": r.get("open", 0),
        "close": r.get("close", 0),
        "high": r.get("max", 0),
        "low": r.get("min", 0),
        "volume": r.get("Trading_Volume", 0),
    } for r in rows}

def fetch_revenue_history(stock_id, start_date, end_date):
    """抓取月營收資料"""
    data = finmind_get("TaiwanStockMonthRevenue", stock_id, start_date, end_date)
    rows = data.get("data", [])
    result = {}
    prev_rev = None
    prev_year_rev = {}
    for r in rows:
        date = r.get("date", "")[:7]
        rev = r.get("revenue", 0)
        mom = round((rev - prev_rev) / prev_rev * 100, 2) if prev_rev else None
        year = int(date[:4])
        month = int(date[5:7])
        yoy_key = f"{year-1}-{month:02d}"
        yoy_rev = prev_year_rev.get(yoy_key)
        yoy = round((rev - yoy_rev) / yoy_rev * 100, 2) if yoy_rev else None
        result[date] = {"revenue": rev, "mom": mom, "yoy": yoy}
        prev_rev = rev
        prev_year_rev[date] = rev
    return result

def fetch_quarterly_history(stock_id, start_date, end_date):
    """抓取季報毛利率資料"""
    data = finmind_get("TaiwanStockFinancialStatements", stock_id, start_date, end_date)
    rows = data.get("data", [])
    quarters = {}
    seen = {}
    for r in sorted(rows, key=lambda x: x.get("date", "")):
        date = r.get("date", "")
        if len(date) < 7:
            continue
        m = int(date[5:7])
        q_num = (m - 1) // 3 + 1
        key = f"{date[:4]}Q{q_num}"
        typ = r.get("type", "")
        dedup = f"{key}_{typ}"
        if dedup in seen:
            continue
        seen[dedup] = True
        if key not in quarters:
            quarters[key] = {"year": date[:4], "q_num": q_num, "date": date}
        val = r.get("value", 0) or 0
        if typ == "GrossProfit":
            quarters[key]["gross_profit"] = val
        elif typ == "Revenue":
            quarters[key]["revenue_raw"] = val
        elif typ == "OperatingIncome":
            quarters[key]["operating_income"] = val
        elif typ == "EPS":
            quarters[key]["eps"] = val

    # Q4 Revenue 差分
    result = {}
    for q, d in quarters.items():
        q_num = d.get("q_num", 0)
        year = d.get("year", "")
        if q_num == 4:
            q3 = quarters.get(f"{year}Q3", {})
            rev = (d.get("revenue_raw", 0) or 0) - (q3.get("revenue_raw", 0) or 0)
        else:
            rev = d.get("revenue_raw", 0) or 0
        gp = d.get("gross_profit", 0) or 0
        oi = d.get("operating_income", 0) or 0
        result[q] = {
            "date": d.get("date", ""),
            "revenue": rev,
            "gross_margin": round(gp/rev*100, 2) if rev else None,
            "operating_margin": round(oi/rev*100, 2) if rev else None,
            "eps": d.get("eps"),
        }
    return result

def fetch_all(stock_id, start_date, end_date):
    """抓取所有回測需要的資料"""
    print(f" [fetch_history] {stock_id}...")
    price = fetch_price_history(stock_id, start_date, end_date)
    time.sleep(0.3)
    revenue = fetch_revenue_history(stock_id, start_date, end_date)
    time.sleep(0.3)
    quarterly = fetch_quarterly_history(stock_id, start_date, end_date)
    return {
        "stock_id": stock_id,
        "price": price,
        "revenue": revenue,
        "quarterly": quarterly,
    }

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    start = sys.argv[2] if len(sys.argv) > 2 else "2023-01-01"
    end = sys.argv[3] if len(sys.argv) > 3 else "2024-12-31"
    result = fetch_all(stock_id, start, end)
    print(f"股價資料：{len(result['price'])} 天")
    print(f"月營收：{len(result['revenue'])} 個月")
    print(f"季報：{len(result['quarterly'])} 季")
