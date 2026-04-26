#!/usr/bin/env python3
"""add_tracking.py — 加入個股到 Notion stock_tracking db

骨架狀態(B5 實測後補完):
  - [ ] FinMind 抓公司名稱
  - [ ] idempotency 檢查(已存在就更新而非新建)
  - [ ] Telegram 通知

用法:
    python3 add_tracking.py --symbol 2330.TW --source manual --reason "..."

選用:
    --review-days N   outcome_review_date 天數(預設 90)
    --name "台積電"   公司名稱(未填從 FinMind 抓)
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"

DEFAULT_REVIEW_DAYS = 90
VALID_SOURCES = ["manual", "news_signal", "scan_anomaly", "broker_report"]


def load_secrets():
    if not SECRETS_FILE.exists():
        raise FileNotFoundError(f"secrets.json 不存在於 {SECRETS_FILE}")
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def fetch_company_name(symbol: str) -> str:
    """從 FinMind 抓公司名稱。骨架階段 TODO。"""
    # TODO (B5): 呼叫 FinMind TaiwanStockInfo 查 symbol
    # 骨架:先回代號
    return symbol.replace(".TW", "")


def add_to_stock_tracking(
    notion_client,
    db_id: str,
    symbol: str,
    name: str,
    source: str,
    reason: str,
    review_date: date,
):
    """寫入 stock_tracking db。骨架階段 TODO。"""
    # TODO (B5): idempotency — 先查 db 是否已有同 symbol row
    # TODO (B5): 若存在 → 更新「立案理由」「狀態」;否則新建
    today = date.today().isoformat()

    # 骨架實作(直接建 row,未做 idempotency):
    page = notion_client.pages.create(
        parent={"database_id": db_id},
        properties={
            "股票代碼": {"title": [{"text": {"content": symbol}}]},
            "公司名稱": {"rich_text": [{"text": {"content": name}}]},
            "初次加入日": {"date": {"start": today}},
            "期待催化劑": {"rich_text": [{"text": {"content": reason}}]},
            "狀態": {"select": {"name": "追蹤中"}},
            "下次驗證日": {"date": {"start": review_date.isoformat()}},
        },
    )
    return page["id"]


def main():
    parser = argparse.ArgumentParser(description="加入個股到 Notion stock_tracking")
    parser.add_argument("--symbol", required=True, help="個股代號(例 2330.TW)")
    parser.add_argument(
        "--source",
        required=True,
        choices=VALID_SOURCES,
        help=f"來源: {'/'.join(VALID_SOURCES)}",
    )
    parser.add_argument("--reason", required=True, help="期待催化劑")
    parser.add_argument("--name", default=None, help="公司名稱(未填從 FinMind 抓)")
    parser.add_argument(
        "--review-days",
        type=int,
        default=DEFAULT_REVIEW_DAYS,
        help=f"outcome_review_date 天數(預設 {DEFAULT_REVIEW_DAYS})",
    )
    args = parser.parse_args()

    # 安全檢查:TEST.TW 測試標的
    if args.symbol.upper().startswith(("TEST.", "9999.")):
        print(f"[INFO] 測試標的 {args.symbol},請測完後到 Notion 手動刪除", file=sys.stderr)

    # 載入 secrets
    try:
        secrets = load_secrets()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    db_id = secrets.get("notion_stock_tracking_db")
    if not db_id:
        print(
            "[ERROR] secrets.json 缺 notion_stock_tracking_db。請先執行 B4 setup_notion_databases.py",
            file=sys.stderr,
        )
        return 1

    name = args.name or fetch_company_name(args.symbol)
    review_date = date.today() + timedelta(days=args.review_days)

    try:
        from notion_client import Client
    except ImportError:
        print("[ERROR] notion_client 未安裝,請 pip install notion-client", file=sys.stderr)
        return 1

    notion = Client(auth=secrets["notion_key"])
    try:
        page_id = add_to_stock_tracking(
            notion, db_id, args.symbol, name, args.source, args.reason, review_date
        )
    except Exception as e:
        print(f"[ERROR] 寫入 Notion 失敗: {e}", file=sys.stderr)
        return 1

    print(f"[OK] 已加入 {args.symbol} ({name})")
    print(f"  page_id: {page_id}")
    print(f"  source: {args.source}")
    print(f"  立案日期: {date.today().isoformat()}")
    print(f"  outcome_review_date: {review_date.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
