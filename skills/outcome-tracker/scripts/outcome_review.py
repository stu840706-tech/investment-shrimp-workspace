#!/usr/bin/env python3
"""outcome_review.py — S-7 掃 stock_tracking 的 outcome_review_date 到期項目

骨架狀態(B5 實測後補完):
  - [ ] FinMind 價格抓取 + 大盤同期
  - [ ] M2.7 判定(thinking=off)
  - [ ] outcome_log 雙寫(Notion db + jsonl)
  - [ ] stock_tracking 狀態更新
  - [ ] Telegram 通知

用法:
    python3 outcome_review.py                    # 掃所有到期
    python3 outcome_review.py --symbol 2330.TW   # 手動指定
    python3 outcome_review.py --dry-run          # 只列出,不寫
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"
OUTCOME_JSONL = WORKSPACE / "knowledge" / "outcome_log.jsonl"


def load_secrets():
    if not SECRETS_FILE.exists():
        raise FileNotFoundError(f"secrets.json 不存在於 {SECRETS_FILE}")
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def find_due_items(notion_client, stock_tracking_db_id: str, today: date, symbol_filter=None):
    """查 stock_tracking 中 outcome_review_date <= today 且狀態 != 已退出 的 row。

    骨架階段:查出來原樣回傳;B5 補完 pagination + 錯誤處理。
    """
    filter_cond = {
        "and": [
            {"property": "outcome_review_date", "date": {"on_or_before": today.isoformat()}},
            {"property": "狀態", "select": {"does_not_equal": "已退出"}},
        ]
    }
    if symbol_filter:
        filter_cond["and"].append(
            {"property": "個股代號", "title": {"equals": symbol_filter}}
        )

    # TODO (B5): pagination 支援(has_more)
    result = notion_client.databases.query(database_id=stock_tracking_db_id, filter=filter_cond)
    return result.get("results", [])


def fetch_price_movement(symbol: str, start_date: date, end_date: date):
    """FinMind 抓價格走勢 + 大盤同期。骨架 TODO。"""
    # TODO (B5):
    #   1. FinMind TaiwanStockPrice(adjust=True)抓 start_date → end_date
    #   2. TAIEX 同期
    #   3. 計算 total return %
    return {
        "stock_return_pct": None,
        "index_return_pct": None,
        "alpha_pct": None,
        "price_series": [],
    }


def llm_judge_thesis(thesis_reason: str, price_data: dict) -> dict:
    """呼叫 M2.7(thinking=off)判定 thesis。骨架 TODO。"""
    # TODO (B5): POST api.minimax.io/anthropic/v1/messages with thinking=off
    # prompt:給立案理由 + 價格走勢 + 大盤同期,判定 verified/failed/inconclusive + 教訓
    return {
        "verdict": "inconclusive",  # verified / failed / inconclusive
        "summary": "(骨架 placeholder)",
        "lesson": "(骨架 placeholder)",
    }


def write_outcome(notion_client, outcome_log_db_id: str, payload: dict, dry_run: bool):
    """寫 outcome_log db 和 jsonl。骨架 TODO。"""
    if dry_run:
        print(f"  [DRY-RUN] would write outcome: {payload['symbol']} → {payload['verdict']}")
        return

    # TODO (B5): 寫 Notion outcome_log db
    # TODO (B5): append jsonl
    OUTCOME_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUTCOME_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    print(f"  [WROTE] {payload['symbol']} → {payload['verdict']}")


def update_stock_status(notion_client, page_id: str, new_status: str, dry_run: bool):
    """stock_tracking 狀態更新為 已退出。骨架 TODO。"""
    if dry_run:
        print(f"  [DRY-RUN] would update page {page_id[:8]}... → {new_status}")
        return
    notion_client.pages.update(
        page_id=page_id,
        properties={
            "狀態": {"select": {"name": new_status}},
            "退出日期": {"date": {"start": date.today().isoformat()}},
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Thesis outcome review")
    parser.add_argument("--symbol", default=None, help="手動指定個股")
    parser.add_argument("--dry-run", action="store_true", help="只列出,不寫")
    args = parser.parse_args()

    try:
        secrets = load_secrets()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    stock_tracking_db = secrets.get("notion_stock_tracking_db")
    outcome_log_db = secrets.get("notion_outcome_log_db")

    if not stock_tracking_db or not outcome_log_db:
        print("[ERROR] secrets.json 缺 db_id,請先執行 B4", file=sys.stderr)
        return 1

    try:
        from notion_client import Client
    except ImportError:
        print("[ERROR] notion_client 未安裝", file=sys.stderr)
        return 1

    notion = Client(auth=secrets["notion_api_key"])
    today = date.today()

    print(f"=== outcome_review ({today.isoformat()}) ===")
    print(f"模式: {'DRY-RUN' if args.dry_run else 'LIVE'}")

    due_items = find_due_items(notion, stock_tracking_db, today, args.symbol)
    print(f"\n找到 {len(due_items)} 筆到期 row")

    for page in due_items:
        # 骨架:只 print,實際處理 B5 補
        props = page["properties"]
        try:
            symbol = props["個股代號"]["title"][0]["plain_text"]
            start_date_str = props["立案日期"]["date"]["start"]
            reason = props["立案理由"]["rich_text"][0]["plain_text"] if props["立案理由"]["rich_text"] else ""
        except (KeyError, IndexError) as e:
            print(f"  [WARN] 跳過不完整 row: {e}")
            continue

        print(f"\n處理 {symbol}(立案 {start_date_str})")
        # TODO (B5):以下全接 real API
        # price_data = fetch_price_movement(symbol, ...)
        # judgment = llm_judge_thesis(reason, price_data)
        # payload = {...}
        # write_outcome(notion, outcome_log_db, payload, args.dry_run)
        # update_stock_status(notion, page["id"], "已退出", args.dry_run)
        print(f"  [SKELETON] outcome review 邏輯 TODO,B5 補完")

    print(f"\n=== 完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
