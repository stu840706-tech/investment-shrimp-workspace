#!/usr/bin/env python3
"""
daily-scan.py — 每日掃描 Orchestrator
呼叫 5 個獨立 scan 模組，合併結果，儲存 scan_results_YYYYMMDD.json，發送 Telegram 摘要

執行方式：
  python3 workflows/daily-scan.py          # 執行今日掃描
  python3 workflows/daily-scan.py --debug  # 顯示詳細輸出

架構說明（P-007：每個 workflow 獨立運作）：
  scan_revenue.py      → 月營收異常偵測
  scan_news.py         → 重大訊息掃描
  scan_institutional.py → 三大法人 + 內部人追蹤
  scan_quarterly.py    → 季財報異常（財報季才跑）
  scan_industry.py     → 產業相對強弱分析
"""

from _scan_utils import now_tw,  load_json, save_json, STATE_DIR, BROWSER_HEADERS
from _common import TELEGRAM_TOKEN, TELEGRAM_DM
from datetime import datetime, timedelta
from pathlib import Path
import time, json, requests

# 自動歸檔超過 14 天的 scan_results
def archive_old_scan_results():
    archive_dir = STATE_DIR.parent / "memory" / "archive" / "scan_results"
    cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
    archived = 0
    for f in STATE_DIR.glob("scan_results_*.json"):
        ymd = f.stem.split("_")[-1]
        if ymd < cutoff:
            archive_dir.mkdir(parents=True, exist_ok=True)
            f.rename(archive_dir / f.name)
            archived += 1
    if archived:
        print(f"  [歸檔] {archived} 筆舊 scan_results 移至 memory/archive/")

def send_telegram_summary(results, all_anomalies, notion_url=None):
    """發送壓縮版摘要到 Telegram"""
    BOT_TOKEN = TELEGRAM_TOKEN
    CHAT_ID = TELEGRAM_DM
    today = now_tw().strftime('%Y/%m/%d')
    total = len(all_anomalies)

    if total == 0:
        print("[Telegram] 無異常，不發送")
        return

    print(f"\n[Telegram] 發送摘要到 Kai...")

    def top_items(items, n=5):
        def sort_key(x):
            return x.get('revenue', 0) if x.get('revenue') else len(x.get('detail', ''))
        return sorted(items, key=sort_key, reverse=True)[:n]

    lines = [f"📊 *投資蝦每日掃描* {today}", f"共 {total} 筆異常\n"]

    cat_emoji = {'月營收異常': '💰', '重大訊息': '📢', '三大法人': '🏛️',
        '季財報異常': '📋', '產業相對強弱': '📊'}

    for cat_name, items in results.items():
        if not items or cat_name == 'timestamp':
            continue
        emoji = cat_emoji.get(cat_name, '📌')
        lines.append(f"{emoji} *{cat_name}* ({len(items)} 筆)")
        for item in top_items(items, 5):
            code = item.get('code', '')
            name = item.get('name', '')[:8]
            detail = item.get('detail', '')[:40]
            lines.append(f" • {code} {name}: {detail}")
        lines.append("")

    if notion_url:
        lines.append(f"📄 完整報告: {notion_url}")

    message = "\n".join(lines)

    try:
        # Fallback: 發送純文字（避免 MarkdownV2 特殊字元問題）
        plain = message.replace('*', '').replace('_', '').replace('`', '')
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={
                "chat_id": CHAT_ID,
                "text": plain,
                "disable_web_page_preview": "true"
            },
            timeout=15
        )
        if resp.status_code == 200:
            print(f" → Telegram 發送成功")
        else:
            print(f" → Telegram 失敗: {resp.status_code}")
    except Exception as e:
        print(f" → Telegram 錯誤: {e}")

# ==================== 主程式 ====================


def main():
    archive_old_scan_results()
    print("=" * 50)
    print(f"投資蝦每日市場掃描 {now_tw().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # 執行 5 個獨立 scan 模組（P-007：每個 workflow 獨立運作）
    from scan_revenue import scan_monthly_revenue
    from scan_news import scan_material_news
    from scan_institutional import scan_3insti_chip
    from scan_quarterly import scan_quarterly_financials, check_financial_season
    from scan_industry import scan_industry_strength

    monthly_anomalies = scan_monthly_revenue()
    news_anomalies = scan_material_news()
    chip_anomalies = scan_3insti_chip()
    quarterly_anomalies = scan_quarterly_financials()
    industry_anomalies = scan_industry_strength()

    results = {
        '月營收異常': monthly_anomalies,
        '重大訊息': news_anomalies,
        '三大法人': chip_anomalies,
        '季財報': quarterly_anomalies,
        '產業強度': industry_anomalies,
        'timestamp': datetime.now().isoformat(),
    }

    all_anomalies = (monthly_anomalies + news_anomalies + chip_anomalies +
        quarterly_anomalies + industry_anomalies)

    print("\n" + "=" * 50)
    print(f"掃描完成：共 {len(all_anomalies)} 筆異常")
    for k, v in results.items():
        if k != 'timestamp' and v:
            print(f"  {k}: {len(v)} 筆")
    print("=" * 50)

    # 儲存結果
    output_file = STATE_DIR / f"scan_results_{now_tw().strftime('%Y%m%d')}.json"
    save_json(output_file, {'results': results, 'anomalies': all_anomalies})
    print(f"\n結果已儲存: {output_file}")

    # 發送 Telegram 摘要（不使用 Notion link）
    send_telegram_summary(results, all_anomalies, None)

    # P-007：每個 workflow 獨立運作。
    # daily-notion.py 由 cron 排程獨立呼叫，此處不再 subprocess 串接。

    return results, all_anomalies

if __name__ == "__main__":
    main()
