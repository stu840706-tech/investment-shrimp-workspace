#!/usr/bin/env python3
"""
Taiwan Stock Monthly Revenue Backfill Script

- Uses FinMind TaiwanStockMonthRevenue API to backfill missing months
- FinMind revenue 欄位單位為「元」，存入 history 時統一轉為「千元」（/1000）
- TWSE t187ap05_L 單位為「千元」，daily-scan.py 直接存入，兩者一致
- 24-month window: 202404 to 202603
- FinMind rate limit: 600 calls/hour
"""

from _common import FINMIND_TOKEN
import json
import time
import requests
from pathlib import Path
from collections import deque
from datetime import datetime

# ========== CONFIG ==========

STATE_FILE = Path("/home/ubuntu/.openclaw/workspace/state/revenue_history.json")
FINMIND_HEADERS = {
    "Authorization": f"Bearer {FINMIND_TOKEN}",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}
FINMIND_BASE_URL = "https://api.finmindtrade.com/api/v4/data"

REQUIRED_MONTHS = []
for y in range(2024, 2027):
    for m in range(1, 13):
        ym = f"{y}{m:02d}"
        if "202404" <= ym <= "202603":
            REQUIRED_MONTHS.append(ym)

_call_times = deque()
CALL_LIMIT = 590
CALL_WINDOW = 3600
MIN_INTERVAL = 6.1

def rate_limit_finmind():
    now = time.time()
    while _call_times and now - _call_times[0] > CALL_WINDOW:
        _call_times.popleft()
    if len(_call_times) >= CALL_LIMIT:
        sleep_time = CALL_WINDOW - (now - _call_times[0]) + 0.5
        print(f" [RateLimit] Waiting {sleep_time:.0f}s...")
        time.sleep(sleep_time)
        now = time.time()
        while _call_times and now - _call_times[0] > CALL_WINDOW:
            _call_times.popleft()
    if _call_times:
        elapsed = now - _call_times[-1]
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)
    _call_times.append(time.time())

def fetch_finmind_revenue(stock_id, start_date="2024-04-01", end_date="2026-03-31"):
    """Fetch monthly revenue from FinMind. Returns list or None (rate limit)."""
    rate_limit_finmind()
    params = {
        "dataset": "TaiwanStockMonthRevenue",
        "data_id": str(stock_id),
        "start_date": start_date,
        "end_date": end_date,
    }
    try:
        resp = requests.get(FINMIND_BASE_URL, params=params, headers=FINMIND_HEADERS, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("msg") == "success":
                return data.get("data", [])
            return []
        # Bug fix: 402 判斷移到 200 block 外層，才能實際觸發
        elif resp.status_code == 402:
            print(f" [RateLimit] FinMind quota exceeded! Status 402.")
            return None
        return []
    except Exception as e:
        print(f" [Error] FinMind fetch failed for {stock_id}: {e}")
        return []

def finmind_to_qianyuan(rev_raw):
    """
    FinMind TaiwanStockMonthRevenue.revenue 單位為「元」。
    除以 1000 轉為「千元」，與 TWSE t187ap05_L 一致。
    """
    if not rev_raw or rev_raw <= 0:
        return 0
    return rev_raw / 1000

def to_ym(date_str):
    """Convert 2024-04-01 to 202404"""
    parts = str(date_str).split("-")
    if len(parts) >= 2:
        return parts[0] + parts[1]
    return None

def load_history():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_history(history):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting revenue backfill...")
    print(f"Required months: {REQUIRED_MONTHS[0]} to {REQUIRED_MONTHS[-1]} ({len(REQUIRED_MONTHS)} months)")
    print(f"單位說明：FinMind 回傳元，存入 history 時 /1000 統一轉千元（與 TWSE 一致）")
    history = load_history()
    print(f"Loaded {len(history)} stocks from existing DB")

    needs_full = []
    needs_partial = []

    for code, data in history.items():
        months = data.get("months", {})
        missing = [m for m in REQUIRED_MONTHS if m not in months]
        if len(months) == 0:
            continue
        has_2024_2025 = any(k.startswith("2024") or k.startswith("2025") for k in months.keys())
        if not has_2024_2025:
            needs_full.append((code, data, missing))
        elif missing:
            needs_partial.append((code, data, missing))

    print(f"\nStocks needing full backfill: {len(needs_full)}")
    print(f"Stocks needing partial backfill: {len(needs_partial)}")
    print(f"Stocks already complete: {len(history) - len(needs_full) - len(needs_partial)}")

    updated_count = 0
    error_count = 0

    print(f"\n=== Processing {len(needs_full)} stocks needing FULL backfill ===")
    for i, (code, data, missing) in enumerate(needs_full):
        if i > 0 and i % 50 == 0:
            print(f" Progress: {i}/{len(needs_full)} processed")
            save_history(history)

        finmind_data = fetch_finmind_revenue(code)
        if finmind_data is None:
            print(f" [WARN] Rate limit hit for {code}, waiting 60s...")
            time.sleep(60)
            finmind_data = fetch_finmind_revenue(code)

        if finmind_data:
            revenues_added = 0
            for row in finmind_data:
                ym = to_ym(row.get("date", ""))
                rev = finmind_to_qianyuan(row.get("revenue", 0))  # 元 → 千元
                if ym and rev > 0:
                    existing = history[code]["months"].get(ym, 0)
                    if existing == 0:
                        history[code]["months"][ym] = rev
                        revenues_added += 1
                    elif abs(rev - existing) / max(existing, 1) > 0.01:
                        history[code]["months"][ym] = rev
                        revenues_added += 1
            updated_count += 1
            if revenues_added > 0:
                history[code]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        else:
            error_count += 1

        if (i + 1) % 100 == 0:
            print(f" [{i+1}/{len(needs_full)}] Processed")

    print(f"\n=== Processing {len(needs_partial)} stocks needing PARTIAL backfill ===")
    for i, (code, data, missing) in enumerate(needs_partial):
        if i > 0 and i % 50 == 0:
            print(f" Progress: {i}/{len(needs_partial)} processed")
            save_history(history)

        missing_sorted = sorted(missing)
        start_ym = missing_sorted[0]
        end_ym = missing_sorted[-1]
        start_date = f"{start_ym[:4]}-{start_ym[4:]}-01"
        end_date = f"{end_ym[:4]}-{end_ym[4:]}-01"

        finmind_data = fetch_finmind_revenue(code, start_date, end_date)
        if finmind_data is None:
            time.sleep(60)
            finmind_data = fetch_finmind_revenue(code, start_date, end_date)

        if finmind_data:
            revenues_added = 0
            for row in finmind_data:
                ym = to_ym(row.get("date", ""))
                rev = finmind_to_qianyuan(row.get("revenue", 0))  # 元 → 千元
                if ym and rev > 0 and ym in missing:
                    existing = history[code]["months"].get(ym, 0)
                    if existing == 0:
                        history[code]["months"][ym] = rev
                        revenues_added += 1
                    elif abs(rev - existing) / max(existing, 1) > 0.01:
                        history[code]["months"][ym] = rev
                        revenues_added += 1
            if revenues_added > 0:
                history[code]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
            updated_count += 1
        else:
            error_count += 1

        if (i + 1) % 100 == 0:
            print(f" [{i+1}/{len(needs_partial)}] Processed")

    save_history(history)

    complete_after = sum(1 for code, data in history.items()
        if all(m in data.get("months", {}) for m in REQUIRED_MONTHS))

    print(f"\n{'='*50}")
    print(f"BACKFILL COMPLETE")
    print(f"{'='*50}")
    print(f"Total stocks in DB: {len(history)}")
    print(f"Stocks updated: {updated_count}")
    print(f"Stocks with errors: {error_count}")
    print(f"Stocks with ALL 24 months: {complete_after}/{len(history)}")
    print(f"State file saved: {STATE_FILE}")

if __name__ == "__main__":
    main()
