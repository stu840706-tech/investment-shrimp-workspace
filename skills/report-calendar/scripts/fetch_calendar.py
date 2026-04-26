#!/usr/bin/env python3
"""fetch_calendar.py — 抓重大事件寫入 Notion event_calendar

實作範圍（B5b）：
  - FinMind TaiwanStockMonthRevenue → 月營收公告日（每月10日截止）
  - FinMind TaiwanStockFinancialStatements → 季報/年報法定截止日
  - idempotency: key = stock_code + date + event_type
  - 追蹤中個股有新事件 → Telegram 摘要通知

法說會：由 broker-materials/receive_telegram.py 解析券商報告時順帶寫入，
        本腳本不處理（無可靠公開 API）。

用法:
    python3 fetch_calendar.py           # 抓未來 60 天
    python3 fetch_calendar.py --days 30
    python3 fetch_calendar.py --dry-run # 只印出，不寫 Notion
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"

MONTH_REVENUE_DAY = 10  # 台灣規定：每月10日前公告上月營收


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def notion_headers(secrets):
    return {
        "Authorization": f"Bearer {secrets['notion_key']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def rt(content):
    return [{"text": {"content": str(content)[:2000]}}]


# ─── 抓追蹤清單 ──────────────────────────────────────────────────────────────

def fetch_tracking_stocks(secrets):
    """從 Notion stock_tracking 撈出所有追蹤中個股（排除不繼續追蹤）。"""
    db_id = secrets["notion_stock_tracking_db"]
    headers = notion_headers(secrets)
    payload = {
        "filter": {
            "property": "狀態",
            "select": {"does_not_equal": "不繼續追蹤"},
        },
        "page_size": 100,
    }
    resp = requests.post(
        f"{NOTION_API}/databases/{db_id}/query",
        headers=headers, json=payload, timeout=30,
    )
    resp.raise_for_status()
    stocks = []
    for page in resp.json().get("results", []):
        props = page.get("properties", {})
        title = props.get("股票代碼", {}).get("title", [])
        if title:
            code = title[0].get("text", {}).get("content", "").strip()
            if code:
                stock_id = code.replace(".TW", "").replace(".TWO", "").strip()
                stocks.append({"code": code, "stock_id": stock_id})
    return stocks


# ─── FinMind 事件推算 ─────────────────────────────────────────────────────────

def fetch_month_revenue_dates(stock_id, token, start_date, end_date):
    """推算月營收公告日（每月10日）。先確認 FinMind 有此股資料。"""
    resp = requests.get(
        FINMIND_API,
        params={"dataset": "TaiwanStockMonthRevenue",
                "data_id": stock_id,
                "start_date": start_date,
                "token": token},
        timeout=15,
    )
    d = resp.json()
    if d.get("msg") != "success" or not d.get("data"):
        return []

    events = []
    cur = datetime.strptime(start_date, "%Y-%m-%d").replace(day=1)
    end = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    while cur <= end:
        announce_date = cur.replace(day=MONTH_REVENUE_DAY)
        if start_dt <= announce_date <= end:
            prev_month = (cur - timedelta(days=1)).strftime("%Y-%m")
            events.append({
                "date": announce_date.strftime("%Y-%m-%d"),
                "event_type": "月營收",
                "description": f"{prev_month} 月營收公告",
            })
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return events


def fetch_financial_statement_dates(stock_id, token, start_date):
    """推算季報/年報法定截止日（Q1:5/15, Q2:8/14, Q3:11/14, Q4:3/31）。"""
    resp = requests.get(
        FINMIND_API,
        params={"dataset": "TaiwanStockFinancialStatements",
                "data_id": stock_id,
                "start_date": "2025-01-01",
                "token": token},
        timeout=15,
    )
    d = resp.json()
    if d.get("msg") != "success" or not d.get("data"):
        return []

    year = datetime.now().year
    deadlines = [
        (f"{year}-03-31", "年報",  f"{year-1}年 年報"),
        (f"{year}-05-15", "季報",  f"{year}Q1 季報"),
        (f"{year}-08-14", "季報",  f"{year}Q2 季報"),
        (f"{year}-11-14", "季報",  f"{year}Q3 季報"),
        (f"{year+1}-03-31", "年報", f"{year}年 年報"),
    ]
    today = datetime.now()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    events = []
    for date_str, etype, desc in deadlines:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt >= start_dt and dt >= today:
            events.append({
                "date": date_str,
                "event_type": etype,
                "description": desc,
            })
    return events


# ─── Notion 寫入（idempotent）────────────────────────────────────────────────

def event_exists(stock_code, event_date, event_type, secrets):
    db_id = secrets["notion_event_calendar_db"]
    payload = {
        "filter": {
            "and": [
                {"property": "股票代碼", "rich_text": {"equals": stock_code}},
                {"property": "事件類型", "select": {"equals": event_type}},
                {"property": "預計日期", "title": {"equals": event_date}},
            ]
        },
        "page_size": 1,
    }
    resp = requests.post(
        f"{NOTION_API}/databases/{db_id}/query",
        headers=notion_headers(secrets), json=payload, timeout=15,
    )
    resp.raise_for_status()
    return len(resp.json().get("results", [])) > 0


def write_event(stock_code, event, secrets, dry_run=False):
    if dry_run:
        print(f"  [DRY-RUN] {stock_code} {event['date']} {event['event_type']}: {event['description']}")
        return True

    if event_exists(stock_code, event["date"], event["event_type"], secrets):
        print(f"  [SKIP] 已存在: {stock_code} {event['date']} {event['event_type']}")
        return False

    db_id = secrets["notion_event_calendar_db"]
    props = {
        "預計日期": {"title": [{"text": {"content": event["date"]}}]},
        "股票代碼": {"rich_text": rt(stock_code)},
        "事件類型": {"select": {"name": event["event_type"]}},
        "重要性":   {"select": {"name": "高"}},
        "已提醒":   {"checkbox": False},
    }
    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=notion_headers(secrets),
        json={"parent": {"database_id": db_id}, "properties": props},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"  [OK] 寫入: {stock_code} {event['date']} {event['event_type']}")
    time.sleep(0.3)
    return True


# ─── Telegram 通知 ───────────────────────────────────────────────────────────

def notify_telegram(message, secrets):
    url = f"https://api.telegram.org/bot{secrets['telegram_bot_token']}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": secrets["telegram_dm"],
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
    except Exception as e:
        print(f"[WARN] Telegram 失敗: {e}")


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secrets = load_secrets()
    token = secrets["finmind_token"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    end_date = (datetime.now(timezone.utc) + timedelta(days=args.days)).strftime("%Y-%m-%d")

    print(f"[START] fetch_calendar: {today} → {end_date} (dry_run={args.dry_run})")

    print("[1/3] 載入 stock_tracking...")
    stocks = fetch_tracking_stocks(secrets)
    print(f"  → {len(stocks)} 檔追蹤中")
    if not stocks:
        print("[WARN] 追蹤清單為空，結束")
        return 0

    print("[2/3] 抓 FinMind 事件...")
    new_events = []
    for s in stocks:
        code, stock_id = s["code"], s["stock_id"]
        print(f"  處理: {code}")
        try:
            for ev in fetch_month_revenue_dates(stock_id, token, today, end_date):
                if write_event(code, ev, secrets, args.dry_run):
                    new_events.append((code, ev))
            time.sleep(0.5)
        except Exception as e:
            print(f"  [WARN] 月營收失敗 {code}: {e}")
        try:
            for ev in fetch_financial_statement_dates(stock_id, token, today):
                if write_event(code, ev, secrets, args.dry_run):
                    new_events.append((code, ev))
            time.sleep(0.5)
        except Exception as e:
            print(f"  [WARN] 季報失敗 {code}: {e}")

    print("[3/3] Telegram 通知...")
    if new_events and not args.dry_run:
        lines = [f"📅 event_calendar 新增 {len(new_events)} 筆\n"]
        for code, ev in new_events[:10]:
            lines.append(f"• {code} {ev['date']} {ev['event_type']}")
        if len(new_events) > 10:
            lines.append(f"...共 {len(new_events)} 筆")
        notify_telegram("\n".join(lines), secrets)

    print(f"[DONE] 新增 {len(new_events)} 筆事件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
