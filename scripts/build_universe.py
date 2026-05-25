"""
Build TWSE/TPEX universe from FinMind TaiwanStockInfo.
Output: state/universe.json (list of stock codes, expires 7 days).
"""
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from workflows._finmind import fetch_one

UNIVERSE_CACHE = Path(__file__).parent.parent / "state" / "universe.json"
CACHE_TTL_DAYS = 7

EXCLUDE_INDUSTRIES = {
    "ETF", "ETN", "存託憑證", "受益證券", "認購權證", "認售權證", "封閉型基金",
    "牛證", "熊證", "債券ETF", "期貨ETF", "可轉換特別股", "海外指數股票型基金",
}


def is_cache_fresh(path, ttl_seconds):
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl_seconds


def build_universe(force=False):
    if not force and is_cache_fresh(UNIVERSE_CACHE, CACHE_TTL_DAYS * 86400):
        print(f"[universe] cache fresh: {UNIVERSE_CACHE}")
        with open(UNIVERSE_CACHE) as f:
            return json.load(f)
    print("[universe] fetching TaiwanStockInfo from FinMind...")
    resp = fetch_one("TaiwanStockInfo")
    rows = resp.get("data", [])
    print(f"[universe] received {len(rows)} rows from TaiwanStockInfo")

    common_equities = []
    for row in rows:
        stock_id = row.get("stock_id", "")
        industry = row.get("industry_category", "")
        stock_type = row.get("type", "")
        if stock_type not in ("twse", "tpex"):
            continue
        if not (stock_id.isdigit() and len(stock_id) == 4):
            continue
        if industry in EXCLUDE_INDUSTRIES:
            continue
        common_equities.append({
            "stock_id": stock_id,
            "stock_name": row.get("stock_name", ""),
            "industry_category": industry,
            "type": stock_type,
        })

    seen = set()
    deduped = []
    for item in common_equities:
        if item["stock_id"] not in seen:
            seen.add(item["stock_id"])
            deduped.append(item)
    deduped.sort(key=lambda x: x["stock_id"])

    UNIVERSE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE_CACHE, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)
    print(f"[universe] wrote {len(deduped)} symbols to {UNIVERSE_CACHE}")
    return deduped


if __name__ == "__main__":
    force = "--force" in sys.argv
    universe = build_universe(force=force)
    print(f"\n=== summary ===")
    print(f"total: {len(universe)}")
    print(f"twse: {sum(1 for x in universe if x['type'] == 'twse')}")
    print(f"otc: {sum(1 for x in universe if x['type'] == 'otc')}")
    print(f"\nfirst 5: {universe[:5]}")
    print(f"\nlast 5: {universe[-5:]}")
    from collections import Counter
    industries = Counter(x["industry_category"] for x in universe)
    print(f"\ntop 10 industries:")
    for ind, cnt in industries.most_common(10):
        print(f"  {ind}: {cnt}")
