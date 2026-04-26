#!/usr/bin/env python3
"""
backtest_main.py - 回測主流程
用法：python3 backtest_main.py <策略> <標的範圍> <起始日> <結束日> [參數JSON]
"""
import sys, json, time, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'workflows'))

from fetch_universe import get_universe
from fetch_history import fetch_all
from strategy_engine import run_strategy
from calc_metrics import calc_metrics
from write_notion import write_backtest_result

def format_telegram_summary(strategy_name, universe_type, params, metrics, page_id):
    return f"""📊 回測完成：{strategy_name}
標的範圍：{universe_type}
回測期間：{params.get('start_date')} ～ {params.get('end_date')}

總報酬率：{metrics['total_return_pct']:+.2f}%
年化報酬：{metrics['annual_return_pct']:+.2f}%
夏普比率：{metrics['sharpe_ratio']:.2f}
最大回撤：{metrics['max_drawdown_pct']:.2f}%
勝率：{metrics['win_rate_pct']:.1f}%
交易次數：{metrics['trade_count']} 筆

✅ 已寫入 Notion backtest_results
https://notion.so/{page_id.replace('-', '')}"""

def main():
    parser = argparse.ArgumentParser(description="台股策略回測")
    parser.add_argument("strategy", help="策略名稱：momentum / revenue_growth / margin_improvement")
    parser.add_argument("universe", help="標的範圍：0050 / 追蹤清單 / 台股全市場")
    parser.add_argument("start_date", help="回測起始日（YYYY-MM-DD）")
    parser.add_argument("end_date", help="回測結束日（YYYY-MM-DD）")
    parser.add_argument("--params", default="{}", help="策略參數 JSON（選填）")
    parser.add_argument("--dry-run", action="store_true", help="不寫入 Notion")
    args = parser.parse_args()

    # 解析參數
    try:
        extra_params = json.loads(args.params)
    except Exception:
        print("ERROR: --params 格式錯誤，請用 JSON 格式，例：'{\"lookback\": 20}'")
        sys.exit(1)

    params = {
        "start_date": args.start_date,
        "end_date": args.end_date,
        **extra_params
    }

    print(f"\n{'='*60}")
    print(f"🔬 回測開始：{args.strategy}")
    print(f"標的範圍：{args.universe}")
    print(f"期間：{args.start_date} ～ {args.end_date}")
    print(f"參數：{params}")
    print(f"{'='*60}\n")

    # Step 1: 取得標的池
    print("【S1】取得標的池...")
    stocks = get_universe(args.universe)
    if not stocks:
        print("ERROR: 標的池為空")
        sys.exit(1)
    print(f" 共 {len(stocks)} 檔標的")

    # Step 2: 抓取歷史資料
    print(f"\n【S2】抓取歷史資料（{len(stocks)} 檔）...")
    stock_data_list = []
    for i, stock_id in enumerate(stocks):
        try:
            data = fetch_all(stock_id, args.start_date, args.end_date)
            if data["price"]:
                stock_data_list.append(data)
            time.sleep(0.5)  # FinMind rate limit
        except Exception as e:
            print(f" {stock_id} 跳過: {e}")
        if (i+1) % 10 == 0:
            print(f" 進度：{i+1}/{len(stocks)}")

    print(f" 成功抓取 {len(stock_data_list)} 檔資料")

    # Step 3: 執行策略
    print(f"\n【S3】執行策略：{args.strategy}...")
    all_trades = run_strategy(args.strategy, stock_data_list, params)
    print(f" 產生 {len(all_trades)} 筆交易")

    if not all_trades:
        print("⚠️ 無交易產生，請調整策略參數")
        sys.exit(0)

    # 顯示前5筆交易
    print(" 前5筆交易：")
    for t in all_trades[:5]:
        pct = round((t["price_sell"]-t["price_buy"])/t["price_buy"]*100, 2)
        print(f" {t['stock_id']} {t['date_buy']}→{t['date_sell']} {pct:+.1f}%")

    # Step 4: 計算績效
    print(f"\n【S4】計算績效指標...")
    metrics = calc_metrics(all_trades)
    print(f" 總報酬：{metrics['total_return_pct']:+.2f}%")
    print(f" 年化：{metrics['annual_return_pct']:+.2f}%")
    print(f" 夏普：{metrics['sharpe_ratio']:.2f}")
    print(f" 最大回撤：{metrics['max_drawdown_pct']:.2f}%")
    print(f" 勝率：{metrics['win_rate_pct']:.1f}%")

    # Step 5: 寫入 Notion
    page_id = ""
    if not args.dry_run:
        print(f"\n【S5】寫入 Notion...")
        page_id = write_backtest_result(
            args.strategy, params, metrics, all_trades, args.universe
        )

    # Step 6: Telegram 摘要
    summary = format_telegram_summary(args.strategy, args.universe, params, metrics, page_id)
    print(f"\n[TELEGRAM]\n{summary}")

    print(f"\n{'='*60}")
    print(f"✅ 回測完成")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
