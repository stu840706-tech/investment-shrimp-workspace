#!/usr/bin/env python3
"""
strategy_engine.py - 策略執行引擎
內建三種策略模板：momentum / revenue_growth / margin_improvement
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

def get_trading_dates(price_data, start_date, end_date):
    """取得回測期間所有交易日"""
    dates = sorted([
        d for d in price_data.keys()
        if start_date <= d <= end_date
    ])
    return dates

def next_trading_date(price_data, date, offset=1):
    """取得指定日期後第 offset 個交易日"""
    all_dates = sorted(price_data.keys())
    try:
        idx = all_dates.index(date)
        if idx + offset < len(all_dates):
            return all_dates[idx + offset]
    except ValueError:
        pass
    return None

# ==================== 策略模板 ====================

def strategy_momentum(stock_data, params):
    """
    動能策略：
    買入：收盤價突破 N 日高點
    賣出：跌破 M 日均線 或 持有超過 hold_days 天
    params: {lookback: 20, ma_exit: 10, hold_days: 30}
    """
    price = stock_data["price"]
    stock_id = stock_data["stock_id"]
    lookback = params.get("lookback", 20)
    ma_exit = params.get("ma_exit", 10)
    hold_days = params.get("hold_days", 30)
    start_date = params["start_date"]
    end_date = params["end_date"]

    dates = get_trading_dates(price, start_date, end_date)
    trades = []
    position = None  # {date_buy, price_buy, shares}

    for i, date in enumerate(dates):
        if i < lookback:
            continue
        closes = [price[d]["close"] for d in dates[i-lookback:i] if price[d]["close"] > 0]
        if not closes:
            continue
        high_n = max(closes)
        ma_closes = [price[d]["close"] for d in dates[max(0,i-ma_exit):i] if price[d]["close"] > 0]
        ma = sum(ma_closes) / len(ma_closes) if ma_closes else 0
        today_close = price[date]["close"]

        if position is None:
            # 買入條件：突破 N 日高點
            if today_close > high_n and today_close > 0:
                buy_date = next_trading_date(price, date)
                if buy_date and buy_date in price:
                    buy_price = price[buy_date]["open"]
                    if buy_price > 0:
                        position = {
                            "date_buy": buy_date,
                            "price_buy": buy_price,
                            "shares": 1000,
                            "entry_date_idx": i
                        }
        else:
            # 賣出條件：跌破均線 或 超過持有天數
            days_held = i - position["entry_date_idx"]
            if today_close < ma or days_held >= hold_days:
                sell_date = next_trading_date(price, date)
                if sell_date and sell_date in price:
                    sell_price = price[sell_date]["open"]
                    if sell_price > 0:
                        trades.append({
                            "stock_id": stock_id,
                            "date_buy": position["date_buy"],
                            "date_sell": sell_date,
                            "price_buy": position["price_buy"],
                            "price_sell": sell_price,
                            "shares": position["shares"],
                        })
                        position = None

    return trades

def strategy_revenue_growth(stock_data, params):
    """
    營收成長策略：
    買入：連續 N 個月 YoY > threshold%
    賣出：YoY 轉負 或 持有超過 hold_days 天
    params: {consecutive: 3, yoy_threshold: 10, hold_days: 60}
    """
    price = stock_data["price"]
    revenue = stock_data["revenue"]
    stock_id = stock_data["stock_id"]
    consecutive = params.get("consecutive", 3)
    yoy_threshold = params.get("yoy_threshold", 10)
    hold_days = params.get("hold_days", 60)
    start_date = params["start_date"]
    end_date = params["end_date"]

    rev_months = sorted(revenue.keys())
    trades = []
    position = None

    for i, month in enumerate(rev_months):
        if month < start_date[:7] or month > end_date[:7]:
            continue
        if i < consecutive:
            continue

        # 檢查連續 N 個月 YoY > threshold
        recent = rev_months[i-consecutive+1:i+1]
        all_positive = all(
            revenue[m].get("yoy") is not None and
            revenue[m]["yoy"] > yoy_threshold
            for m in recent
        )

        # 找當月最後一個交易日
        trading_dates = [d for d in sorted(price.keys()) if d.startswith(month)]
        if not trading_dates:
            continue
        signal_date = trading_dates[-1]

        if position is None and all_positive:
            buy_date = next_trading_date(price, signal_date)
            if buy_date and buy_date in price:
                buy_price = price[buy_date]["open"]
                if buy_price > 0:
                    position = {
                        "date_buy": buy_date,
                        "price_buy": buy_price,
                        "shares": 1000,
                        "signal_month_idx": i
                    }
        elif position is not None:
            current_yoy = revenue[month].get("yoy")
            months_held = i - position["signal_month_idx"]
            if (current_yoy is not None and current_yoy < 0) or months_held >= hold_days // 20:
                sell_date = next_trading_date(price, signal_date)
                if sell_date and sell_date in price:
                    sell_price = price[sell_date]["open"]
                    if sell_price > 0:
                        trades.append({
                            "stock_id": stock_id,
                            "date_buy": position["date_buy"],
                            "date_sell": sell_date,
                            "price_buy": position["price_buy"],
                            "price_sell": sell_price,
                            "shares": position["shares"],
                        })
                        position = None

    return trades

def strategy_margin_improvement(stock_data, params):
    """
    毛利改善策略：
    買入：毛利率 QoQ 提升 > threshold%
    賣出：毛利率 QoQ 下滑 或 持有超過 2 季
    params: {qoq_threshold: 1.5, hold_quarters: 2}
    """
    price = stock_data["price"]
    quarterly = stock_data["quarterly"]
    stock_id = stock_data["stock_id"]
    qoq_threshold = params.get("qoq_threshold", 1.5)
    hold_quarters = params.get("hold_quarters", 2)
    start_date = params["start_date"]
    end_date = params["end_date"]

    quarters_sorted = sorted(quarterly.keys())
    trades = []
    position = None

    for i, q in enumerate(quarters_sorted):
        if i < 1:
            continue
        q_date = quarterly[q].get("date", "")
        if q_date < start_date or q_date > end_date:
            continue

        prev_q = quarters_sorted[i-1]
        gm_now = quarterly[q].get("gross_margin")
        gm_prev = quarterly[prev_q].get("gross_margin")

        if gm_now is None or gm_prev is None:
            continue

        qoq_change = gm_now - gm_prev
        trading_dates = [d for d in sorted(price.keys()) if d >= q_date]
        if not trading_dates:
            continue
        signal_date = trading_dates[0]

        if position is None and qoq_change > qoq_threshold:
            buy_date = next_trading_date(price, signal_date)
            if buy_date and buy_date in price:
                buy_price = price[buy_date]["open"]
                if buy_price > 0:
                    position = {
                        "date_buy": buy_date,
                        "price_buy": buy_price,
                        "shares": 1000,
                        "entry_quarter_idx": i
                    }
        elif position is not None:
            quarters_held = i - position["entry_quarter_idx"]
            if qoq_change < 0 or quarters_held >= hold_quarters:
                sell_date = next_trading_date(price, signal_date)
                if sell_date and sell_date in price:
                    sell_price = price[sell_date]["open"]
                    if sell_price > 0:
                        trades.append({
                            "stock_id": stock_id,
                            "date_buy": position["date_buy"],
                            "date_sell": sell_date,
                            "price_buy": position["price_buy"],
                            "price_sell": sell_price,
                            "shares": position["shares"],
                        })
                        position = None

    return trades

# ==================== 策略分派 ====================
STRATEGIES = {
    "momentum": strategy_momentum,
    "revenue_growth": strategy_revenue_growth,
    "margin_improvement": strategy_margin_improvement,
}

def run_strategy(strategy_name, stock_data_list, params):
    """對所有標的執行策略，回傳所有交易紀錄"""
    if strategy_name not in STRATEGIES:
        raise ValueError(f"未知策略：{strategy_name}，可用：{list(STRATEGIES.keys())}")
    strategy_fn = STRATEGIES[strategy_name]
    all_trades = []
    for stock_data in stock_data_list:
        try:
            trades = strategy_fn(stock_data, params)
            all_trades.extend(trades)
        except Exception as e:
            print(f" [strategy_engine] {stock_data['stock_id']} 跳過: {e}")
    all_trades.sort(key=lambda x: x["date_buy"])
    return all_trades

if __name__ == "__main__":
    print("strategy_engine.py 載入成功")
    print(f"可用策略：{list(STRATEGIES.keys())}")
