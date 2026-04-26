#!/usr/bin/env python3
"""fetch_chips.py - 抓取個股籌碼面數字"""
import sys, json
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import FINMIND_TOKEN

BASE_URL = "https://api.finmindtrade.com/api/v4/data"

def finmind_get(dataset, stock_id, start_date):
    import urllib.request, urllib.parse
    params = urllib.parse.urlencode({
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "token": FINMIND_TOKEN
    })
    url = f"{BASE_URL}?{params}"
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())

def fetch_institutional(stock_id):
    """三大法人近30個交易日買賣超"""
    start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    data = finmind_get("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start)
    rows = data.get("data", [])

    daily = {}
    for r in rows:
        date = r.get("date", "")[:10]
        name = r.get("name", "")
        buy = r.get("buy", 0) or 0
        sell = r.get("sell", 0) or 0
        net = buy - sell
        if date not in daily:
            daily[date] = {"date": date, "foreign": 0, "trust": 0, "dealer": 0, "total": 0}
        # FinMind uses English name keys
        if name == "Foreign_Investor":
            daily[date]["foreign"] += net
        elif name == "Investment_Trust":
            daily[date]["trust"] += net
        elif name in ("Dealer_self", "Dealer_Hedging", "Foreign_Dealer_Self"):
            daily[date]["dealer"] += net
        daily[date]["total"] = (
            daily[date]["foreign"] + daily[date]["trust"] + daily[date]["dealer"]
        )

    result = sorted(daily.values(), key=lambda x: x["date"])[-30:]

    summary = {
        "foreign_30d": sum(r["foreign"] for r in result),
        "trust_30d": sum(r["trust"] for r in result),
        "dealer_30d": sum(r["dealer"] for r in result),
        "total_30d": sum(r["total"] for r in result),
    }
    return {"daily": result, "summary": summary}

def fetch_margin(stock_id):
    """融資融券餘額近30個交易日"""
    start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    data = finmind_get("TaiwanStockMarginPurchaseShortSale", stock_id, start)
    rows = data.get("data", [])[-30:]

    result = []
    for r in rows:
        result.append({
            "date": r.get("date", "")[:10],
            "margin_balance": r.get("MarginPurchaseYesterdayBalance", 0),
            "margin_change": r.get("MarginPurchaseTodayBalance", 0) - r.get("MarginPurchaseYesterdayBalance", 0),
            "short_balance": r.get("ShortSaleYesterdayBalance", 0),
            "short_change": r.get("ShortSaleTodayBalance", 0) - r.get("ShortSaleYesterdayBalance", 0),
            "usage_rate": r.get("MarginPurchaseUseRate", 0),
        })

    summary = {}
    if len(result) >= 2:
        latest = result[-1]
        oldest = result[0]
        summary = {
            "margin_latest": latest["margin_balance"],
            "margin_change_30d": latest["margin_balance"] - oldest["margin_balance"],
            "short_latest": latest["short_balance"],
        }
    return {"daily": result, "summary": summary}

def main(stock_id):
    print(f"[fetch_chips] 抓取 {stock_id} 籌碼數字...")
    result = {
        "stock_id": stock_id,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "institutional": fetch_institutional(stock_id),
        "margin": fetch_margin(stock_id),
    }
    out_path = Path(__file__).parent.parent.parent.parent / "state" / f"research_{stock_id}_chips.json"
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[fetch_chips] 完成，輸出至 {out_path}")

    inst = result["institutional"]["summary"]
    margin = result["margin"]["summary"]
    print(f"三大法人30日合計: 外資{inst['foreign_30d']:+,} 投信{inst['trust_30d']:+,} 自營{inst['dealer_30d']:+,}")
    print(f"融資餘額最新: {margin.get('margin_latest',0):,} 張，30日變化: {margin.get('margin_change_30d',0):+,}")

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    main(stock_id)