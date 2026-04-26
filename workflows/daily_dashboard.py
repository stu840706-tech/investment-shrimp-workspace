#!/usr/bin/env python3
"""
daily_dashboard.py — B4: 每日 dashboard 頁面產生器

設計（02_NOTION_SCHEMA_PLAN_v2.md「每日 Dashboard Page」章節 + HANDOFF 已決策）:
- 每天 06:10 (daily-scan 完後 10 分鐘) 由 cron 觸發
- 在 hub_page_id 底下建立 child page，標題 "Dashboard YYYY-MM-DD"
- 內容是 programmatically 產生的 markdown blocks (不交給 AI 自由發揮)
- 30 天後 sweep 到 dashboard_archive 子頁面 (B6 處理；B4 只實作主流程)

資料來源:
- state/scan_results_YYYYMMDD.json (Task 2 產出)
- Notion stock_tracking db (透過 secrets.json id)
- Notion event_calendar db (B5 才有資料；B4 階段空著)
- Notion outcome_log db (B5/Task 5 才有資料；B4 階段空著)

容錯原則 (HANDOFF P-005):
- 任何資料源缺失 → 對應段落寫「資料源未上線 (Bn 完成後生效)」
- 不要 fail 整個 dashboard，部分資料缺失不阻塞其他段落
"""
import json
import sys
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta


WORKSPACE = Path("/home/ubuntu/.openclaw/workspace")
SECRETS_PATH = WORKSPACE / "config" / "secrets.json"
STATE_DIR = WORKSPACE / "state"

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

# Asia/Taipei (UTC+8) — workspace 硬偏好
TPE = timezone(timedelta(hours=8))


# ============================================================
# Notion helpers (與 setup 腳本同一套，重複是刻意的：dashboard 要能獨立執行)
# ============================================================

class NotionError(Exception):
    pass


def http_request(method, url, headers, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise NotionError(f"HTTP {e.code} {method}: {body}")
    except urllib.error.URLError as e:
        raise NotionError(f"URLError {method}: {e}")


def headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ============================================================
# Block builders (Notion 限制: 一個 block 最長 2000 字元)
# ============================================================

def h1(text):
    return {"object": "block", "type": "heading_1",
            "heading_1": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def h2(text):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}}


def para(text):
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def bullet(text):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


# ============================================================
# 資料載入
# ============================================================

def load_scan_results(date_str):
    """讀取 state/scan_results_YYYYMMDD.json，找不到回 None。"""
    path = STATE_DIR / f"scan_results_{date_str}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] 讀取 {path} 失敗: {e}")
        return None


def query_db(api_key, db_id, filter_payload=None, page_size=20):
    """Notion db query，失敗回空 list（不阻塞 dashboard）。"""
    if not db_id:
        return []
    payload = {"page_size": page_size}
    if filter_payload:
        payload["filter"] = filter_payload
    try:
        result = http_request(
            "POST",
            f"{NOTION_API}/databases/{db_id}/query",
            headers(api_key),
            payload,
        )
        return result.get("results", [])
    except NotionError as e:
        print(f"[WARN] query db {db_id} 失敗: {e}")
        return []


# ============================================================
# 段落產生器
# ============================================================

def section_focus(scan_results):
    """今日聚焦：命中標籤 ≥3 個。"""
    blocks = [h2("今日聚焦（命中標籤 ≥3 個）")]
    if not scan_results:
        blocks.append(para("資料源未上線：state/scan_results_YYYYMMDD.json 未找到。"
                           "確認 daily-scan cron 是否已執行。"))
        return blocks

    # daily-notion.py 的 scan_results 結構: results[category] = list of entries
    results = scan_results.get("results", {})
    # 用「公司被多分類提到」當「命中標籤數」的代理（與既有 daily-notion 邏輯一致）
    company_hits = {}  # code -> {name, categories: set}
    for category, entries in results.items():
        for entry in entries:
            code = str(entry.get("code", "")).strip()
            if not code:
                continue
            name = entry.get("name", "").strip()
            ch = company_hits.setdefault(code, {"name": name, "categories": set()})
            ch["categories"].add(category)
            if name and not ch["name"]:
                ch["name"] = name

    focus = sorted(
        [(code, d) for code, d in company_hits.items() if len(d["categories"]) >= 3],
        key=lambda x: -len(x[1]["categories"]),
    )

    if not focus:
        blocks.append(para("今日無命中 ≥3 個分類的個股。"))
        return blocks

    for code, d in focus[:15]:
        cats = "、".join(sorted(d["categories"]))
        blocks.append(bullet(f"{code} {d['name']}  命中 {len(d['categories'])} 類: {cats}"))
    return blocks


def section_industry_strength(scan_results):
    """今日產業相對強弱 Top 10。"""
    blocks = [h2("今日產業相對強弱 (Top 10)")]
    if not scan_results:
        blocks.append(para("資料源未上線。"))
        return blocks

    # 從 scan_results 找產業強度資料
    results = scan_results.get("results", {})
    industry_entries = results.get("產業強度", [])
    if not industry_entries:
        blocks.append(para("scan_results 內無「產業強度」分類資料。"))
        return blocks

    for i, entry in enumerate(industry_entries[:10], 1):
        industry = entry.get("industry", entry.get("name", "?"))
        strength = entry.get("strength", entry.get("score", "?"))
        blocks.append(bullet(f"{i}. {industry}  強度: {strength}"))
    return blocks


def section_tracking_changes(api_key, secrets, scan_results):
    """追蹤清單異動：新出現/異常變動。"""
    blocks = [h2("追蹤清單異動")]

    tracking_db_id = secrets.get("notion_stock_tracking_db")
    if not tracking_db_id:
        blocks.append(para("stock_tracking db 未建立 (B4 setup 完成後生效)。"))
        return blocks

    # 取得當前追蹤清單 (狀態 != 不繼續追蹤)
    tracking = query_db(api_key, tracking_db_id, page_size=100)
    tracked_codes = set()
    for page in tracking:
        props = page.get("properties", {})
        code_prop = props.get("股票代碼", {})
        if code_prop.get("type") == "title":
            for rt in code_prop.get("title", []):
                code = rt.get("plain_text", "").strip()
                if code:
                    tracked_codes.add(code)
                    break

    if not tracked_codes:
        blocks.append(para("stock_tracking 目前無追蹤項目（Task 5 上線後生效）。"))
        return blocks

    # 新出現在 scan 結果的追蹤股
    blocks.append(h2("新出現在掃描結果的追蹤股"))
    if scan_results:
        scan_codes = set()
        for entries in scan_results.get("results", {}).values():
            for e in entries:
                code = str(e.get("code", "")).strip()
                if code:
                    scan_codes.add(code)
        appeared = tracked_codes & scan_codes
        if appeared:
            for code in sorted(appeared):
                blocks.append(bullet(code))
        else:
            blocks.append(para("無。"))
    else:
        blocks.append(para("scan_results 未上線，跳過。"))

    return blocks


def section_upcoming_events(api_key, secrets):
    """本週重要事件 (event_calendar 未來 7 天)。"""
    blocks = [h2("本週重要事件")]
    db_id = secrets.get("notion_event_calendar_db")
    if not db_id:
        blocks.append(para("event_calendar db 未建立 (B4 setup 完成後生效)。"))
        return blocks

    today = datetime.now(TPE).date().isoformat()
    week_end = (datetime.now(TPE).date() + timedelta(days=7)).isoformat()

    events = query_db(api_key, db_id, filter_payload={
        "and": [
            {"property": "預計日期", "title": {"is_not_empty": True}},
            # 簡化：title 是日期字串時無法直接 range filter，先取全部後在 Python 過濾
        ]
    }, page_size=50)

    if not events:
        blocks.append(para("event_calendar 目前無資料 (Task 3 上線後生效)。"))
    else:
        # 簡單列出有日期欄位的
        count = 0
        for e in events:
            props = e.get("properties", {})
            date_field = props.get("實際日期", {}).get("date") or {}
            d = date_field.get("start", "")
            if today <= d <= week_end:
                code_prop = props.get("股票代碼", {}).get("rich_text", [])
                code = code_prop[0]["plain_text"] if code_prop else "?"
                evtype = props.get("事件類型", {}).get("select", {}) or {}
                blocks.append(bullet(f"{d}  {code}  {evtype.get('name','?')}"))
                count += 1
        if not count:
            blocks.append(para("未來 7 天無事件。"))

    return blocks


def section_outcome_due(api_key, secrets):
    """Outcome review 到期。"""
    blocks = [h2("Outcome Review 到期")]
    tracking_db_id = secrets.get("notion_stock_tracking_db")
    if not tracking_db_id:
        blocks.append(para("stock_tracking db 未建立 (B4 setup 完成後生效)。"))
        return blocks

    today = datetime.now(TPE).date().isoformat()
    due = query_db(api_key, tracking_db_id, filter_payload={
        "and": [
            {"property": "下次驗證日", "date": {"on_or_before": today}},
            {"property": "Outcome狀態", "select": {"equals": "待驗證"}},
        ]
    }, page_size=50)

    if not due:
        blocks.append(para("無到期項目。"))
        return blocks

    for page in due:
        props = page.get("properties", {})
        code_prop = props.get("股票代碼", {}).get("title", [])
        code = code_prop[0]["plain_text"] if code_prop else "?"
        blocks.append(bullet(f"{code}  下次驗證日已到期"))
    return blocks


# ============================================================
# Main
# ============================================================

def build_dashboard_blocks(api_key, secrets, date_str):
    blocks = []
    blocks.append(h1(f"掃描結果 Dashboard {date_str}"))
    blocks.append(para(f"自動產生於 {datetime.now(TPE).strftime('%Y-%m-%d %H:%M')} (Asia/Taipei)"))
    blocks.append(divider())

    scan_results = load_scan_results(date_str.replace("-", ""))
    if scan_results is None:
        blocks.append(para(
            f"[警告] state/scan_results_{date_str.replace('-','')}.json 未找到。"
            "Task 2 daily-scan 可能未執行或產出檔名不符。"
        ))

    # 各段落（每段失敗不影響其他段）
    for section_fn, args in [
        (section_focus, (scan_results,)),
        (section_industry_strength, (scan_results,)),
        (section_tracking_changes, (api_key, secrets, scan_results)),
        (section_upcoming_events, (api_key, secrets)),
        (section_outcome_due, (api_key, secrets)),
    ]:
        try:
            blocks.extend(section_fn(*args))
        except Exception as e:
            blocks.append(para(f"[ERROR] 段落 {section_fn.__name__} 失敗: {e}"))
        blocks.append(divider())

    return blocks


def create_dashboard_page(api_key, hub_page_id, title, blocks, dry_run=False):
    """建立 child page。Notion 單次 children 上限 100 個。"""
    payload = {
        "parent": {"type": "page_id", "page_id": hub_page_id},
        "properties": {
            "title": [{"type": "text", "text": {"content": title}}],
        },
        "children": blocks[:100],
    }
    if dry_run:
        print(f"[DRY-RUN] POST /pages  title={title}  blocks={len(blocks)}")
        return {"id": "DRY-RUN-PAGE", "_dry_run": True}

    result = http_request("POST", f"{NOTION_API}/pages", headers(api_key), payload)

    # 若 blocks > 100，後續 append
    if len(blocks) > 100:
        page_id = result["id"]
        for i in range(100, len(blocks), 100):
            chunk = blocks[i:i+100]
            try:
                http_request(
                    "PATCH",
                    f"{NOTION_API}/blocks/{page_id}/children",
                    headers(api_key),
                    {"children": chunk},
                )
            except NotionError as e:
                print(f"[WARN] append chunk {i} 失敗: {e}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD (預設今天 Asia/Taipei)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hub-page-id",
                        default="34e226f5-a398-802f-bf27-fa7a4fa19970",
                        help="dashboard 要掛在哪個 page 底下 (預設與 setup 同 hub)")
    args = parser.parse_args()

    date_str = args.date or datetime.now(TPE).strftime("%Y-%m-%d")
    print(f"[daily_dashboard.py] 產生 {date_str} dashboard "
          f"({'DRY-RUN' if args.dry_run else 'LIVE'})")

    with open(SECRETS_PATH) as f:
        secrets = json.load(f)
    api_key = secrets.get("notion_key")
    if not api_key:
        print("[ERROR] secrets.json 找不到 notion_key")
        sys.exit(1)

    blocks = build_dashboard_blocks(api_key, secrets, date_str)
    print(f"  built {len(blocks)} blocks")

    title = f"Dashboard {date_str}"
    try:
        result = create_dashboard_page(api_key, args.hub_page_id, title, blocks,
                                       dry_run=args.dry_run)
        if not args.dry_run:
            print(f"  [+] dashboard page: {result['id']}")
            print(f"  url: https://notion.so/{result['id'].replace('-', '')}")
    except NotionError as e:
        print(f"[FAIL] {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
