#!/usr/bin/env python3
"""fetch_calendar.py — 抓未來 N 天的重大事件寫入 Notion event_calendar

骨架狀態(B5 實測後補完):
  - [ ] TWSE 爬蟲(法說會)
  - [ ] TPEX 爬蟲
  - [ ] MOPS 財報發布日
  - [ ] FinMind fallback
  - [ ] idempotency(key = symbol + date + type)
  - [ ] stock_tracking 交叉比對 + Telegram 通知

用法:
    python3 fetch_calendar.py               # 未來 14 天(預設)
    python3 fetch_calendar.py --days 30
    python3 fetch_calendar.py --dry-run     # 不寫 Notion
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"

DEFAULT_DAYS = 14
VALID_TYPES = ["法說會", "年報", "季報", "除權息", "其他"]


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def fetch_twse_events(start_date: date, end_date: date):
    """TWSE 公告爬蟲。骨架 TODO。回傳 list of dict。"""
    # TODO (B5): requests + BeautifulSoup,抓「法人說明會資訊」
    return []


def fetch_tpex_events(start_date: date, end_date: date):
    """TPEX 公告爬蟲。骨架 TODO。"""
    # TODO (B5)
    return []


def fetch_mops_earnings(start_date: date, end_date: date):
    """MOPS 財報發布日。骨架 TODO。"""
    # TODO (B5)
    return []


def fetch_finmind_fallback(start_date: date, end_date: date):
    """FinMind fallback(爬蟲失敗時用)。骨架 TODO。"""
    # TODO (B5)
    return []


def dedupe_events(events):
    """key = (個股代號, 事件日期, 類型),重複的丟掉。"""
    seen = set()
    out = []
    for e in events:
        key = (e.get("symbol"), e.get("date"), e.get("type"))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def write_to_notion(events, db_id: str, secrets: dict, dry_run: bool):
    """寫 Notion event_calendar,idempotent。骨架 TODO。"""
    if dry_run:
        for e in events:
            print(f"  [DRY-RUN] would write: {e.get('title')} @ {e.get('date')}")
        return

    # TODO (B5):
    #   1. 先 query db 抓現有 (symbol, date, type) set
    #   2. 只寫不在 set 內的
    #   3. 追蹤中個股(跨比對 stock_tracking)→ mark 追蹤中 checkbox
    pass


def notify_telegram_for_tracked(events, secrets):
    """追蹤中個股有相關事件 → Telegram 通知。骨架 TODO。"""
    # TODO (B5):
    #   1. 讀 stock_tracking 的 active list
    #   2. 過濾 events 中 symbol in active list 且 date <= 7 天內
    #   3. Telegram sendMessage
    pass


def main():
    parser = argparse.ArgumentParser(description="抓重大事件寫 event_calendar")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secrets = load_secrets()
    db_id = secrets.get("notion_event_calendar_db")
    if not db_id:
        print("[ERROR] secrets.json 缺 notion_event_calendar_db(B4 後才有)", file=sys.stderr)
        return 1

    today = date.today()
    end = today + timedelta(days=args.days)

    print(f"=== fetch_calendar ({today.isoformat()} ~ {end.isoformat()}) ===")
    print(f"模式: {'DRY-RUN' if args.dry_run else 'LIVE'}")

    all_events = []
    for fetcher_name, fetcher in [
        ("TWSE", fetch_twse_events),
        ("TPEX", fetch_tpex_events),
        ("MOPS", fetch_mops_earnings),
    ]:
        try:
            events = fetcher(today, end)
            print(f"  [{fetcher_name}] 抓到 {len(events)} 筆")
            all_events.extend(events)
        except Exception as e:
            print(f"  [{fetcher_name}] 失敗: {e},走 FinMind fallback")
            try:
                events = fetch_finmind_fallback(today, end)
                all_events.extend(events)
            except Exception as e2:
                print(f"  [FinMind fallback] 也失敗: {e2}")

    all_events = dedupe_events(all_events)
    print(f"\n去重後共 {len(all_events)} 筆事件")

    write_to_notion(all_events, db_id, secrets, args.dry_run)

    if not args.dry_run:
        notify_telegram_for_tracked(all_events, secrets)

    print("=== 完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
