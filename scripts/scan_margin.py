# Daily margin scanner. Design: memory/runbooks/margin_scanner_design.md
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workflows._finmind import fetch_one, FinMindError, FinMindTierError, FinMindQuotaError

WORKSPACE = Path(__file__).parent.parent
UNIVERSE_PATH = WORKSPACE / "state" / "universe.json"
CHECKPOINT_PATH = WORKSPACE / "state" / "scan_margin_checkpoint.json"
RAW_DIR = WORKSPACE / "state" / "raw" / "margin"
MARGIN_DB_ID = "36b226f5-a398-812b-a4ac-e48e3f5b734c"

THRESH_HIGH_LEVEL = 0.75
THRESH_MARGIN_SURGE_RATIO = 2.0
THRESH_MARGIN_SURGE_VOL = 500
THRESH_SHORT_SURGE_RATIO = 2.0
THRESH_SHORT_SURGE_VOL = 200
THRESH_SR_RATIO = 0.30
THRESH_SR_SHORT_VOL = 1000
LOOKBACK_DAYS = 5

with open(WORKSPACE / "config" / "secrets.json") as f:
    SECRETS = json.load(f)
NOTION_KEY = SECRETS["notion_key"]
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def load_universe():
    with open(UNIVERSE_PATH) as f:
        return json.load(f)


def load_checkpoint():
    if not CHECKPOINT_PATH.exists():
        return {"date": "", "last_idx": -1, "completed": False}
    with open(CHECKPOINT_PATH) as f:
        return json.load(f)


def save_checkpoint(state):
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(state, f)


def delete_checkpoint():
    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()


def get_date_range(end_date_iso, lookback_days):
    end = datetime.fromisoformat(end_date_iso)
    start = end - timedelta(days=lookback_days + 5)
    return start.strftime("%Y-%m-%d"), end_date_iso


def fetch_stock_margin(stock_id, start_date, end_date):
    try:
        resp = fetch_one("TaiwanStockMarginPurchaseShortSale", data_id=stock_id, start_date=start_date, end_date=end_date)
        return resp.get("data", [])
    except (FinMindTierError, FinMindQuotaError):
        raise
    except FinMindError as e:
        print(f"[scan] {stock_id}: {e}")
        return None


def write_raw(date_iso, rows):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"margin_{date_iso}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def detect_anomalies(stock_id, stock_name, history, target_date):
    if not history:
        return []
    today = history[-1]
    if today.get("date") != target_date:
        return []
    margin_balance = today.get("MarginPurchaseTodayBalance", 0)
    margin_limit = today.get("MarginPurchaseLimit", 0)
    margin_buy = today.get("MarginPurchaseBuy", 0)
    short_sell = today.get("ShortSaleSell", 0)
    short_balance = today.get("ShortSaleTodayBalance", 0)
    anomalies = []
    if margin_limit > 0:
        ratio = margin_balance / margin_limit
        if ratio > THRESH_HIGH_LEVEL:
            anomalies.append({"type": "融資高水位", "value": round(ratio * 100, 2), "detail": f"融資使用率 {ratio*100:.2f}%（餘額 {margin_balance}/限額 {margin_limit} 張）"})
    historical = history[:-1][-LOOKBACK_DAYS:]
    if len(historical) >= LOOKBACK_DAYS:
        margin_buys = [r.get("MarginPurchaseBuy", 0) for r in historical]
        margin_avg = sum(margin_buys) / len(margin_buys) if margin_buys else 0
        if margin_avg > 0 and margin_buy > margin_avg * THRESH_MARGIN_SURGE_RATIO and margin_buy > THRESH_MARGIN_SURGE_VOL:
            anomalies.append({"type": "融資暴增", "value": round(margin_buy / margin_avg, 2), "detail": f"今日融資買 {margin_buy} 張，{LOOKBACK_DAYS}日均 {margin_avg:.0f} 張，{margin_buy/margin_avg:.2f}x 暴增"})
        short_sells = [r.get("ShortSaleSell", 0) for r in historical]
        short_avg = sum(short_sells) / len(short_sells) if short_sells else 0
        if short_avg > 0 and short_sell > short_avg * THRESH_SHORT_SURGE_RATIO and short_sell > THRESH_SHORT_SURGE_VOL:
            anomalies.append({"type": "融券暴增", "value": round(short_sell / short_avg, 2), "detail": f"今日融券賣 {short_sell} 張，{LOOKBACK_DAYS}日均 {short_avg:.0f} 張，{short_sell/short_avg:.2f}x 暴增"})
        if margin_balance > 0:
            sr_ratio = short_balance / margin_balance
            if sr_ratio > THRESH_SR_RATIO and short_balance > THRESH_SR_SHORT_VOL:
                anomalies.append({"type": "券資比警戒", "value": round(sr_ratio * 100, 2), "detail": f"券資比 {sr_ratio*100:.2f}%（融券餘額 {short_balance}/融資餘額 {margin_balance} 張）"})
    return anomalies


def write_notion_record(stock_id, stock_name, date_iso, anomaly):
    url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": MARGIN_DB_ID},
        "properties": {
            "日期/股票": {"title": [{"text": {"content": f"{date_iso}/{stock_id}"}}]},
            "日期": {"date": {"start": date_iso}},
            "股票代碼": {"rich_text": [{"text": {"content": stock_id}}]},
            "股票名稱": {"rich_text": [{"text": {"content": stock_name}}]},
            "類型": {"select": {"name": anomaly["type"]}},
            "訊號值": {"number": anomaly["value"]},
            "詳情": {"rich_text": [{"text": {"content": anomaly["detail"]}}]},
            "來源": {"select": {"name": "FinMind"}},
        },
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=NOTION_HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        print(f"[notion] {stock_id} 寫入失敗: {e.code} {body[:200]}")
        return e.code


def main(target_date=None, limit=None, offset=0):
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    print(f"[scan_margin] target_date={target_date}, limit={limit}")
    universe = load_universe()
    print(f"[scan_margin] universe size: {len(universe)}")
    if offset:
        universe = universe[offset:]
        print(f"[scan_margin] offset by {offset}")
    if limit:
        universe = universe[:limit]
        print(f"[scan_margin] limited to {limit} stocks")
    checkpoint = load_checkpoint()
    if checkpoint.get("date") == target_date and checkpoint.get("last_idx", -1) >= 0 and not checkpoint.get("completed"):
        start_idx = checkpoint["last_idx"] + 1
        print(f"[scan_margin] resume from idx {start_idx}")
    else:
        start_idx = 0
    save_checkpoint({"date": target_date, "last_idx": -1, "completed": False})
    start_date, end_date = get_date_range(target_date, LOOKBACK_DAYS)
    print(f"[scan_margin] date range: {start_date} to {end_date}")
    total_anomalies = 0
    total_with_data = 0
    no_data = 0
    error_count = 0
    for idx in range(start_idx, len(universe)):
        item = universe[idx]
        stock_id = item["stock_id"]
        stock_name = item["stock_name"]
        try:
            rows = fetch_stock_margin(stock_id, start_date, end_date)
        except FinMindTierError as e:
            print(f"[scan_margin] FATAL tier at {stock_id}: {e}")
            sys.exit(1)
        except FinMindQuotaError as e:
            print(f"[scan_margin] FATAL quota at idx {idx} ({stock_id}): {e}")
            save_checkpoint({"date": target_date, "last_idx": idx - 1, "completed": False})
            sys.exit(2)
        if rows is None:
            error_count += 1
            save_checkpoint({"date": target_date, "last_idx": idx, "completed": False})
            continue
        if not rows:
            no_data += 1
            save_checkpoint({"date": target_date, "last_idx": idx, "completed": False})
            continue
        rows.sort(key=lambda r: r["date"])
        write_raw(target_date, rows)
        anomalies = detect_anomalies(stock_id, stock_name, rows, target_date)
        for a in anomalies:
            write_notion_record(stock_id, stock_name, target_date, a)
            total_anomalies += 1
            print(f" [{stock_id} {stock_name}] {a['type']}: {a['detail']}")
        total_with_data += 1
        save_checkpoint({"date": target_date, "last_idx": idx, "completed": False})
    save_checkpoint({"date": target_date, "last_idx": len(universe) - 1, "completed": True})
    print(f"\n[scan_margin] DONE")
    print(f" with_data: {total_with_data}")
    print(f" no_data: {no_data}")
    print(f" errors: {error_count}")
    print(f" anomalies: {total_anomalies}")
    delete_checkpoint()
    print("[scan_margin] checkpoint cleared")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    main(target_date=args.date, limit=args.limit, offset=args.offset)
