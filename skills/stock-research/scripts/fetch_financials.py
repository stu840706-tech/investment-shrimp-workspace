#!/usr/bin/env python3
"""fetch_financials.py - 抓取個股財務數字"""
import sys, json, time
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

def fetch_revenue(stock_id):
    start = (datetime.now() - timedelta(days=450)).strftime("%Y-%m-%d")
    data = finmind_get("TaiwanStockMonthRevenue", stock_id, start)
    rows = data.get("data", [])[-14:]

    rev_map = {}
    for r in rows:
        ym = r.get("date", "")[:7]
        rev_map[ym] = r.get("revenue", 0)

    result = []
    for i, r in enumerate(rows):
        rev = r.get("revenue", 0)
        prev_rev = rows[i-1].get("revenue", 0) if i > 0 else 0
        mom = round((rev - prev_rev) / prev_rev * 100, 2) if prev_rev else None

        ym = r.get("date", "")[:7]
        try:
            y, m = int(ym[:4]), int(ym[5:7])
            yoy_ym = f"{y-1}-{m:02d}"
            yoy_rev = rev_map.get(yoy_ym)
            yoy_pct = round((rev - yoy_rev) / yoy_rev * 100, 2) if yoy_rev else None
        except Exception:
            yoy_pct = None

        result.append({
            "date": ym,
            "revenue": rev,
            "revenue_mom": mom,
            "revenue_yoy": yoy_pct,
            "revenue_raw": r
        })
    return result

def fetch_quarterly(stock_id):
    """季報三率近8季

    FinMind TaiwanStockFinancialStatements 邏輯：
    - Revenue Q4 = 全年YTD → 需差分（Q4YTD - Q3單季累計）
    - GrossProfit / OperatingIncome / IncomeAfterTaxes / EPS → 各季單季值，直接取最後一筆
    """
    start = (datetime.now() - timedelta(days=900)).strftime("%Y-%m-%d")
    data = finmind_get("TaiwanStockFinancialStatements", stock_id, start)
    rows = data.get("data", [])

    # 每季每type取最後一筆（覆蓋式，最新修正值優先）
    quarters = {}
    for r in sorted(rows, key=lambda x: x.get("date", "")):
        date = r.get("date", "")
        if len(date) < 7:
            continue
        m = int(date[5:7])
        q_num = (m - 1) // 3 + 1
        key = f"{date[:4]}Q{q_num}"
        if key not in quarters:
            quarters[key] = {"q_num": q_num, "year": date[:4]}
        typ = r.get("type", "")
        val = r.get("value", 0) or 0
        # Revenue Q4 是YTD，其餘直接覆蓋取最新
        if typ == "Revenue":
            quarters[key]["revenue_raw"] = val
        elif typ == "GrossProfit":
            quarters[key]["gross_profit"] = val
        elif typ == "OperatingIncome":
            quarters[key]["operating_income"] = val
        elif typ == "IncomeAfterTaxes":
            quarters[key]["net_income"] = val
        elif typ == "EPS":
            quarters[key]["eps"] = val

    result = []
    for q in sorted(quarters.keys()):
        d = quarters[q]
        q_num = d.get("q_num", 0)
        year = d.get("year", "")

        # Revenue：Q4 需差分，其餘直接取
        if q_num == 4:
            q3 = quarters.get(f"{year}Q3", {})
            rev = (d.get("revenue_raw", 0) or 0) - (q3.get("revenue_raw", 0) or 0)
        else:
            rev = d.get("revenue_raw", 0) or 0

        gp = d.get("gross_profit", 0) or 0
        oi = d.get("operating_income", 0) or 0
        ni = d.get("net_income", 0) or 0

        result.append({
            "quarter": q,
            "revenue": rev,
            "gross_margin": round(gp/rev*100, 2) if rev else None,
            "operating_margin": round(oi/rev*100, 2) if rev else None,
            "net_margin": round(ni/rev*100, 2) if rev else None,
            "eps": d.get("eps"),
            "gross_profit": gp,
            "operating_income": oi,
            "net_income": ni,
        })

    return result[-8:]

def main(stock_id):
    print(f"[fetch_financials] 抓取 {stock_id} 財務數字...")
    result = {
        "stock_id": stock_id,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "monthly_revenue": fetch_revenue(stock_id),
        "quarterly": fetch_quarterly(stock_id)
    }
    out_path = Path(__file__).parent.parent.parent.parent / "state" / f"research_{stock_id}_financials.json"
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[fetch_financials] 完成，輸出至 {out_path}")
    return result

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    main(stock_id)