#!/usr/bin/env python3
"""
calc_metrics.py - 回測績效指標計算
"""
import math
from datetime import datetime

def calc_metrics(trades, initial_capital=1000000):
    """
    計算回測績效指標
    trades: list of dict {date_buy, date_sell, price_buy, price_sell, shares}
    """
    if not trades:
        return {
            "total_return_pct": 0,
            "annual_return_pct": 0,
            "sharpe_ratio": 0,
            "max_drawdown_pct": 0,
            "win_rate_pct": 0,
            "trade_count": 0,
        }

    FEE = 0.001425  # 手續費
    TAX = 0.003      # 交易稅（賣出）

    pnl_list = []
    wins = 0
    total_pnl = 0

    for t in trades:
        buy = t["price_buy"] * t["shares"] * (1 + FEE)
        sell = t["price_sell"] * t["shares"] * (1 - FEE - TAX)
        pnl = sell - buy
        pnl_pct = pnl / buy * 100
        pnl_list.append(pnl_pct)
        total_pnl += pnl
        if pnl > 0:
            wins += 1

    # 總報酬率
    total_return = total_pnl / initial_capital * 100

    # 年化報酬率
    if trades:
        start = datetime.strptime(trades[0]["date_buy"], "%Y-%m-%d")
        end = datetime.strptime(trades[-1]["date_sell"], "%Y-%m-%d")
        years = max((end - start).days / 365, 0.01)
        annual_return = ((1 + total_return/100) ** (1/years) - 1) * 100
    else:
        annual_return = 0

    # 夏普比率（簡化：用每筆交易報酬計算）
    if len(pnl_list) > 1:
        avg = sum(pnl_list) / len(pnl_list)
        std = math.sqrt(sum((x-avg)**2 for x in pnl_list) / len(pnl_list))
        sharpe = (avg / std * math.sqrt(252)) if std > 0 else 0
    else:
        sharpe = 0

    # 最大回撤
    cumulative = [initial_capital]
    for pnl_pct in pnl_list:
        cumulative.append(cumulative[-1] * (1 + pnl_pct/100))
    peak = cumulative[0]
    max_dd = 0
    for val in cumulative:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "total_return_pct": round(total_return, 2),
        "annual_return_pct": round(annual_return, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "win_rate_pct": round(wins / len(trades) * 100, 2) if trades else 0,
        "trade_count": len(trades),
    }

if __name__ == "__main__":
    # 測試
    test_trades = [
        {"date_buy": "2023-01-10", "date_sell": "2023-03-15", "price_buy": 100, "price_sell": 115, "shares": 1000},
        {"date_buy": "2023-04-01", "date_sell": "2023-06-20", "price_buy": 200, "price_sell": 190, "shares": 500},
        {"date_buy": "2023-07-05", "date_sell": "2023-09-30", "price_buy": 150, "price_sell": 175, "shares": 800},
    ]
    metrics = calc_metrics(test_trades)
    for k, v in metrics.items():
        print(f"{k}: {v}")
