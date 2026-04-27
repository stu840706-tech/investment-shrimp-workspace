#!/usr/bin/env python3
"""
write_notion.py - 將回測結果寫入 Notion backtest_results DB
"""
import sys, json, urllib.request, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import SECRETS, NOTION_KEY

NOTION_VERSION = "2022-06-28"
BACKTEST_DB = SECRETS["notion_backtest_results_db"]

def notion_post(url, payload, method='POST'):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={
            'Authorization': f'Bearer {NOTION_KEY}',
            'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json'
        },
        method=method
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def to_rich_text(text, limit=1990):
    if not text:
        return []
    text = str(text)
    return [{"text": {"content": text[i:i+limit]}} for i in range(0, len(text), limit)]

def write_backtest_result(strategy_name, params, metrics, trades, universe_type, code_filename=""):
    """寫入回測結果到 Notion"""
    today = datetime.now().strftime("%Y-%m-%d")
    start_date = params.get("start_date", "")
    end_date = params.get("end_date", "")

    # 策略一句話說明
    strategy_desc = {
        "momentum": f"突破{params.get('lookback',20)}日高點買入，跌破{params.get('ma_exit',10)}日均線賣出",
        "revenue_growth": f"連續{params.get('consecutive',3)}月YoY>{params.get('yoy_threshold',10)}%買入，YoY轉負賣出",
        "margin_improvement": f"毛利QoQ>{params.get('qoq_threshold',1.5)}%買入，毛利下滑賣出",
    }.get(strategy_name, strategy_name)

    # 參數掃描結果（把 trades 摘要放這裡）
    top_trades = sorted(trades, key=lambda x: (x["price_sell"]-x["price_buy"])/x["price_buy"], reverse=True)[:5]
    params_text = f"參數：{json.dumps(params, ensure_ascii=False)}\n"
    params_text += f"最佳5筆交易：\n"
    for t in top_trades:
        pct = round((t["price_sell"]-t["price_buy"])/t["price_buy"]*100, 2)
        params_text += f" {t['stock_id']} {t['date_buy']}→{t['date_sell']} {pct:+.1f}%\n"

    payload = {
        "parent": {"database_id": BACKTEST_DB},
        "properties": {
            "策略名稱": {"title": to_rich_text(f"{strategy_name} {today}")},
            "回測日期": {"date": {"start": today}},
            "回測期間_起": {"date": {"start": start_date}} if start_date else {},
            "回測期間_迄": {"date": {"start": end_date}} if end_date else {},
            "標的類別": {"multi_select": [{"name": universe_type}]},
            "策略一句話": {"rich_text": to_rich_text(strategy_desc)},
            "程式碼檔名": {"rich_text": to_rich_text(code_filename or f"strategy_engine.py:{strategy_name}")},
            "總報酬率百分比": {"number": metrics.get("total_return_pct", 0)},
            "年化報酬百分比": {"number": metrics.get("annual_return_pct", 0)},
            "夏普比率": {"number": metrics.get("sharpe_ratio", 0)},
            "最大回撤百分比": {"number": metrics.get("max_drawdown_pct", 0)},
            "勝率百分比": {"number": metrics.get("win_rate_pct", 0)},
            "交易次數": {"number": metrics.get("trade_count", 0)},
            "參數掃描結果": {"rich_text": to_rich_text(params_text)},
        }
    }

    # 清除空的 date 欄位
    for key in ["回測期間_起", "回測期間_迄"]:
        if not payload["properties"][key]:
            del payload["properties"][key]

    page = notion_post("https://api.notion.com/v1/pages", payload)
    page_id = page.get("id", "")
    print(f"[write_notion] 寫入完成: {page_id}")
    return page_id

if __name__ == "__main__":
    # 測試
    test_metrics = {
        "total_return_pct": 35.2,
        "annual_return_pct": 12.8,
        "sharpe_ratio": 1.45,
        "max_drawdown_pct": 18.3,
        "win_rate_pct": 62.5,
        "trade_count": 24,
    }
    test_trades = [
        {"stock_id": "4755", "date_buy": "2023-01-10", "date_sell": "2023-03-15",
         "price_buy": 100, "price_sell": 115, "shares": 1000},
    ]
    test_params = {"start_date": "2023-01-01", "end_date": "2024-12-31", "lookback": 20}
    page_id = write_backtest_result("momentum", test_params, test_metrics, test_trades, "0050")
    print(f"測試完成: {page_id}")
