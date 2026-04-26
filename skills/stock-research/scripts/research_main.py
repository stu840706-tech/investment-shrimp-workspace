#!/usr/bin/env python3
"""research_main.py - 個股研究報告主流程"""
import sys, json, argparse
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))

from fetch_financials import main as fetch_financials
from fetch_chips import main as fetch_chips
from fetch_annual_report import main as fetch_annual_report
from fetch_broker_summary import main as fetch_broker_summary
from generate_report import main as generate_report
from write_notion import main as write_notion

WORKSPACE = Path(__file__).parent.parent.parent.parent

def send_telegram(message):
    """印出 Telegram 格式訊息（實際由 OpenClaw 發送）"""
    print(f"\n[TELEGRAM]\n{message}")

def format_telegram_summary(stock_id, report_data):
    """格式化 Telegram 摘要（一、二、五、六章節）"""
    report = report_data.get("report", {})
    generated_at = report_data.get("generated_at", "")
    lines = [
        f"📊 **{report.get('title', stock_id)}**",
        f"評等：{report.get('rating', 'N/A')} | 目標價：{report.get('target_price', 'N/A')} 元",
        f"產生時間：{generated_at}",
        "",
        "─── 一、個股簡介 ───",
        report.get("section_1", "")[:500],
        "",
        "─── 二、成長引擎 ───",
        report.get("section_2", "")[:800],
        "",
        "─── 五、估值與投資建議 ───",
        report.get("section_5", "")[:500],
        "",
        "─── 六、潛在風險及觀察項目 ───",
        report.get("section_6", "")[:400],
        "",
        "✅ 完整報告已存入 Notion research_pages",
    ]
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="個股研究報告主流程")
    parser.add_argument("stock_id", help="股票代碼（例：4755）")
    parser.add_argument("memo", nargs="?", default="（無法說會memo）", help="法說會重點memo")
    parser.add_argument("--pdf", default=None, help="手動提供年報PDF路徑")
    parser.add_argument("--skip-annual", action="store_true", help="跳過年報下載（無年報資料時）")
    args = parser.parse_args()

    stock_id = args.stock_id
    memo = args.memo
    print(f"\n{'='*60}")
    print(f"🔬 個股研究報告生成：{stock_id}")
    print(f"開始時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # S1: 財務數字
    print("【S1】抓取財務數字...")
    fetch_financials(stock_id)

    # S2: 籌碼面
    print("\n【S2】抓取籌碼面...")
    fetch_chips(stock_id)

    # S3: 年報
    if not args.skip_annual:
        print("\n【S3】處理年報...")
        annual_result = fetch_annual_report(stock_id, args.pdf)
        if annual_result is None:
            send_telegram(
                f"⚠️ [{stock_id}] 年報 PDF 無法自動下載\n"
                f"請手動提供年報 PDF，執行：\n"
                f"python3 research_main.py {stock_id} \"<memo>\" --pdf <PDF路徑>"
            )
            print("年報下載失敗，繼續其他步驟（年報欄位將標註「資料待補」）")
    else:
        print("\n【S3】跳過年報")

    # S4: 券商報告
    print("\n【S4】讀取券商報告摘要...")
    fetch_broker_summary(stock_id)

    # S5: 產生報告草稿
    print("\n【S5】M2.7 產生報告草稿...")
    report_data = generate_report(stock_id, memo)
    if not report_data:
        send_telegram(f"❌ [{stock_id}] 報告生成失敗，請檢查 log")
        return

    # S6: 寫入 Notion
    print("\n【S6】寫入 Notion research_pages...")
    page_id = write_notion(stock_id)

    # S7: Telegram 回傳摘要
    summary = format_telegram_summary(stock_id, report_data)
    send_telegram(summary)

    print(f"\n{'='*60}")
    print(f"✅ {stock_id} 研究報告完成")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()