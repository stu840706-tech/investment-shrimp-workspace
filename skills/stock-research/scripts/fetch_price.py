#!/usr/bin/env python3
"""fetch_price.py - 抓取股價歷史與技術面數據"""
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
    with urllib.request.urlopen(f"{BASE_URL}?{params}", timeout=30) as r:
        return json.loads(r.read().decode())

def calc_ma(prices, n):
    if len(prices) < n:
        return None
    return round(sum(prices[-n:]) / n, 2)

def fetch_price(stock_id):
    start = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    data = finmind_get("TaiwanStockPrice", stock_id, start)
    rows = data.get("data", [])[-60:]

    if not rows:
        return {}

    closes = [r.get("close", 0) for r in rows]
    volumes = [r.get("Trading_Volume", 0) for r in rows]

    latest = rows[-1]
    current_price = latest.get("close", 0)

    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)

    high_60 = max(r.get("max", 0) for r in rows)
    low_60 = min(r.get("min", 0) for r in rows)

    avg_vol_20 = round(sum(volumes[-20:]) / min(20, len(volumes)))

    daily = []
    for r in rows[-14:]:
        daily.append({
            "date": r.get("date", "")[:10],
            "open": r.get("open"),
            "high": r.get("max"),
            "low": r.get("min"),
            "close": r.get("close"),
            "volume": r.get("Trading_Volume"),
        })

    return {
        "current_price": current_price,
        "date": latest.get("date", "")[:10],
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma60": ma60,
        "high_60d": high_60,
        "low_60d": low_60,
        "avg_volume_20d": avg_vol_20,
        "daily": daily,
        "price_vs_ma20": round((current_price - ma20) / ma20 * 100, 2) if ma20 else None,
        "price_vs_high60": round((current_price - high_60) / high_60 * 100, 2) if high_60 else None,
    }

def fetch_shareholding(stock_id):
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    data = finmind_get("TaiwanStockShareholding", stock_id, start)
    rows = data.get("data", [])
    if not rows:
        return {}
    latest = rows[-1]
    return {
        "date": latest.get("date", "")[:10],
        "foreign_pct": latest.get("ForeignInvestmentSharesRatio", 0),
        "foreign_shares": latest.get("ForeignInvestmentShares", 0),
    }

def main(stock_id):
    print(f"[fetch_price] 抓取 {stock_id} 股價資料...")
    price = fetch_price(stock_id)
    shareholding = fetch_shareholding(stock_id)

    result = {
        "stock_id": stock_id,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "price": price,
        "shareholding": shareholding,
    }
    out_path = Path(__file__).parent.parent.parent.parent / "state" / f"research_{stock_id}_price.json"
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[fetch_price] 完成")
    print(f" 現價: {price.get('current_price')} ({price.get('date')})")
    print(f" MA5:{price.get('ma5')} MA10:{price.get('ma10')} MA20:{price.get('ma20')} MA60:{price.get('ma60')}")
    print(f" 60日高:{price.get('high_60d')} 低:{price.get('low_60d')}")
    print(f" 外資持股比率: {shareholding.get('foreign_pct')}%")
    return result

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    main(stock_id)