#!/usr/bin/env python3
"""outcome_review.py — S-7 掃 stock_tracking 的下次驗證日到期項目

流程：
  1. 查 stock_tracking：下次驗證日 <= 今天 且 狀態 != 不繼續追蹤
  2. FinMind 抓立案以來的價格走勢 + 大盤同期
  3. M2.7 (thinking=off) 判定 thesis 驗證結果
  4. 寫 outcome_log db + jsonl
  5. 更新 stock_tracking 的 Outcome狀態 + 最近Outcome結果
  6. Telegram 通知 Kai

用法:
    python3 outcome_review.py                  # 掃所有到期
    python3 outcome_review.py --symbol 2330.TW # 手動指定
    python3 outcome_review.py --dry-run        # 只列出，不寫
"""
import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"
OUTCOME_JSONL = WORKSPACE / "knowledge" / "outcome_log.jsonl"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
MINIMAX_BASE = "https://api.minimax.io/anthropic/v1"
MINIMAX_MODEL = "MiniMax-M2.7"


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


def find_due_items(secrets, today_str, symbol_filter=None):
    db_id = secrets["notion_stock_tracking_db"]
    filter_cond = {
        "and": [
            {"property": "下次驗證日", "date": {"on_or_before": today_str}},
            {"property": "狀態", "select": {"does_not_equal": "不繼續追蹤"}},
        ]
    }
    if symbol_filter:
        filter_cond["and"].append(
            {"property": "股票代碼", "title": {"equals": symbol_filter}}
        )
    items = []
    has_more = True
    start_cursor = None
    while has_more:
        payload = {"filter": filter_cond, "page_size": 100}
        if start_cursor:
            payload["start_cursor"] = start_cursor
        resp = requests.post(
            f"{NOTION_API}/databases/{db_id}/query",
            headers=notion_headers(secrets), json=payload, timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")
        time.sleep(0.3)
    return items


def fetch_price_movement(stock_id, start_date_str, token):
    result = {
        "stock_return_pct": None,
        "index_return_pct": None,
        "alpha_pct": None,
        "start_price": None,
        "end_price": None,
    }
    resp = requests.get(
        FINMIND_API,
        params={"dataset": "TaiwanStockPrice", "data_id": stock_id,
                "start_date": start_date_str, "token": token},
        timeout=15,
    )
    d = resp.json()
    if d.get("msg") == "success" and d.get("data"):
        prices = d["data"]
        result["start_price"] = prices[0]["close"]
        result["end_price"] = prices[-1]["close"]
        if result["start_price"] and result["start_price"] != 0:
            result["stock_return_pct"] = round(
                (result["end_price"] - result["start_price"]) / result["start_price"] * 100, 2
            )
    resp2 = requests.get(
        FINMIND_API,
        params={"dataset": "TaiwanStockPrice", "data_id": "TAIEX",
                "start_date": start_date_str, "token": token},
        timeout=15,
    )
    d2 = resp2.json()
    if d2.get("msg") == "success" and d2.get("data"):
        idx = d2["data"]
        idx_start = idx[0]["close"]
        idx_end = idx[-1]["close"]
        if idx_start and idx_start != 0:
            result["index_return_pct"] = round(
                (idx_end - idx_start) / idx_start * 100, 2
            )
    if result["stock_return_pct"] is not None and result["index_return_pct"] is not None:
        result["alpha_pct"] = round(
            result["stock_return_pct"] - result["index_return_pct"], 2
        )
    return result


def llm_judge_thesis(symbol, thesis, catalyst, price_data, secrets):
    stock_ret = price_data.get("stock_return_pct")
    idx_ret = price_data.get("index_return_pct")
    alpha = price_data.get("alpha_pct")
    price_summary = (
        f"股價報酬: {stock_ret}% | 大盤報酬: {idx_ret}% | Alpha: {alpha}%"
        if stock_ret is not None else "價格資料不可用"
    )
    prompt = (
        f"股票代碼：{symbol}\n核心thesis：{thesis}\n期待催化劑：{catalyst}\n"
        f"立案以來價格表現：{price_summary}\n\n"
        "請判定此 thesis 的驗證結果，輸出純 JSON：\n"
        '{"verdict":"已驗證符合"|"部分符合"|"已驗證反證"|"資料不足",'
        '"summary":"2句話說明驗證結果","lesson":"1句話學到什麼"}'
    )
    headers = {
        "Content-Type": "application/json",
        "x-api-key": secrets["minimax_api_key"],
    }
    payload = {
        "model": MINIMAX_MODEL,
        "max_tokens": 512,
        "thinking": {"type": "disabled"},
        "system": "你是投資 thesis 驗證分析師。直接輸出純 JSON，不加任何說明。",
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "{"},
        ],
    }
    resp = requests.post(
        f"{MINIMAX_BASE}/messages", headers=headers, json=payload, timeout=60,
    )
    resp.raise_for_status()
    blocks = resp.json()["content"]
    text_blocks = [b["text"] for b in blocks if b.get("type") == "text"]
    raw = text_blocks[0].strip() if text_blocks else ""
    if not raw.startswith("{"):
        raw = "{" + raw
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return json.loads(raw)


def write_outcome_log(symbol, page_id, start_date_str, thesis, catalyst,
                      price_data, judgment, secrets, dry_run):
    today = date.today().isoformat()
    review_id = f"{symbol.replace('.TW','').replace('.TWO','')}_{today}"
    verdict = judgment.get("verdict", "資料不足")
    summary = judgment.get("summary", "")
    lesson = judgment.get("lesson", "")
    payload = {
        "review_id": review_id, "symbol": symbol,
        "stock_tracking_page_id": page_id,
        "review_date": today, "thesis": thesis, "catalyst": catalyst,
        "stock_return_pct": price_data.get("stock_return_pct"),
        "index_return_pct": price_data.get("index_return_pct"),
        "alpha_pct": price_data.get("alpha_pct"),
        "verdict": verdict, "summary": summary, "lesson": lesson,
    }
    if dry_run:
        print(f"  [DRY-RUN] outcome: {symbol} → {verdict} | {summary[:60]}")
        return payload
    verdict_map = {
        "已驗證符合": "已驗證符合", "部分符合": "部分符合",
        "已驗證反證": "已驗證反證", "資料不足": "部分符合",
    }
    props = {
        "review_id": {"title": rt(review_id)},
        "股票代碼": {"rich_text": rt(symbol)},
        "review_date": {"date": {"start": today}},
        "驗證狀態": {"select": {"name": verdict_map.get(verdict, "部分符合")}},
    }
    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=notion_headers(secrets),
        json={"parent": {"database_id": secrets["notion_outcome_log_db"]}, "properties": props},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"  [OK] outcome_log 寫入: {review_id}")
    OUTCOME_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUTCOME_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def update_stock_tracking(page_id, verdict, summary, secrets, dry_run):
    verdict_map = {
        "已驗證符合": "已驗證符合", "部分符合": "部分符合",
        "已驗證反證": "已驗證反證", "資料不足": "待驗證",
    }
    today = date.today().isoformat()
    if dry_run:
        print(f"  [DRY-RUN] 更新 stock_tracking {page_id[:8]}... → {verdict}")
        return
    props = {
        "Outcome狀態": {"select": {"name": verdict_map.get(verdict, "待驗證")}},
        "最近Outcome結果": {"rich_text": rt(f"{today}: {summary[:100]}")},
    }
    resp = requests.patch(
        f"{NOTION_API}/pages/{page_id}",
        headers=notion_headers(secrets),
        json={"properties": props},
        timeout=15,
    )
    resp.raise_for_status()
    print(f"  [OK] stock_tracking 更新: {verdict}")
    time.sleep(0.3)


def notify_telegram(message, secrets):
    url = f"https://api.telegram.org/bot{secrets['telegram_bot_token']}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": secrets["telegram_dm"],
            "text": message, "parse_mode": "HTML",
        }, timeout=15)
    except Exception as e:
        print(f"[WARN] Telegram 失敗: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    secrets = load_secrets()
    today_str = date.today().isoformat()
    print(f"[START] outcome_review {today_str} (dry_run={args.dry_run})")

    print("[1/4] 查到期 stock_tracking...")
    due_items = find_due_items(secrets, today_str, args.symbol)
    print(f"  → {len(due_items)} 筆到期")
    if not due_items:
        print("[DONE] 無到期項目")
        return 0

    results = []
    for page in due_items:
        props = page["properties"]
        try:
            symbol = props["股票代碼"]["title"][0]["plain_text"].strip()
            start_date_str = (props.get("初次加入日", {}).get("date") or {}).get("start", "2025-01-01")
            thesis = ""
            if props.get("核心thesis", {}).get("rich_text"):
                thesis = props["核心thesis"]["rich_text"][0]["plain_text"]
            catalyst = ""
            if props.get("期待催化劑", {}).get("rich_text"):
                catalyst = props["期待催化劑"]["rich_text"][0]["plain_text"]
        except (KeyError, IndexError) as e:
            print(f"  [WARN] 跳過不完整 row: {e}")
            continue

        stock_id = symbol.replace(".TW", "").replace(".TWO", "")
        print(f"\n  處理: {symbol} (立案 {start_date_str})")

        print("  [2/4] 抓價格走勢...")
        try:
            price_data = fetch_price_movement(stock_id, start_date_str, secrets["finmind_token"])
            print(f"    股價: {price_data.get('stock_return_pct')}% | 大盤: {price_data.get('index_return_pct')}% | Alpha: {price_data.get('alpha_pct')}%")
        except Exception as e:
            print(f"    [WARN] 價格失敗: {e}")
            price_data = {"stock_return_pct": None, "index_return_pct": None, "alpha_pct": None}

        print("  [3/4] M2.7 判定...")
        try:
            judgment = llm_judge_thesis(symbol, thesis, catalyst, price_data, secrets)
            print(f"    verdict={judgment.get('verdict')} | {judgment.get('summary','')[:60]}")
        except Exception as e:
            print(f"    [WARN] M2.7 失敗: {e}")
            judgment = {"verdict": "資料不足", "summary": f"判定失敗: {e}", "lesson": ""}

        print("  [4/4] 寫入 outcome_log...")
        payload = write_outcome_log(
            symbol, page["id"], start_date_str,
            thesis, catalyst, price_data, judgment, secrets, args.dry_run,
        )
        results.append(payload)
        update_stock_tracking(
            page["id"], judgment.get("verdict", "資料不足"),
            judgment.get("summary", ""), secrets, args.dry_run,
        )
        time.sleep(0.5)

    if results and not args.dry_run:
        lines = [f"🔍 Outcome Review 完成：{len(results)} 筆\n"]
        for r in results:
            icon = {"已驗證符合": "✅", "部分符合": "⚠️", "已驗證反證": "❌"}.get(r["verdict"], "❓")
            lines.append(f"{icon} {r['symbol']} → {r['verdict']}")
            if r.get("alpha_pct") is not None:
                lines.append(f"   Alpha: {r['alpha_pct']}%")
        notify_telegram("\n".join(lines), secrets)

    print(f"\n[DONE] 完成 {len(results)} 筆")
    return 0


if __name__ == "__main__":
    sys.exit(main())
