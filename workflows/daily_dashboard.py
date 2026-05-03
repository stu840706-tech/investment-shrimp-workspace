#!/usr/bin/env python3  
"""  
daily_dashboard.py — 每日 Dashboard 產生器（改版）  
每日台北時間 23:30（UTC 15:30）cron 觸發，確保在所有任務完成後執行。  
  
寫入對象：📅 每日 Dashboard DB（Notion）  
DB ID：dc9ca081-55a3-4726-b2d4-da9da67fcba5  
  
七個區塊：  
 1. 市場掃描摘要（連結到 daily-scan-summary 子頁）  
 2. 券商日摘（從 📰 券商日摘 DB 讀今日摘要前 500 字）  
 3. 追蹤個股異動（修復 bug 版本）  
 4. 未來 14 天法說會  
 5. Outcome 未來 7 天到期  
 6. 今日新聞（checkbox 狀態 + notion_news_db 前 3 條）  
 7. 週健診待辦（只在週一顯示）  
  
防重複：同一天已有記錄則更新 blocks，沒有才新建。  
產出後發 Telegram DM 通知（只發連結，不發全文）。  
"""  
  
import json  
import sys  
import time  
import urllib.request  
import urllib.error  
from pathlib import Path  
from datetime import datetime, timezone, timedelta  
  
WORKSPACE = Path("/home/ubuntu/.openclaw/workspace")  
SECRETS_PATH = WORKSPACE / "config" / "secrets.json"  
STATE_DIR = WORKSPACE / "state"  
  
NOTION_VERSION = "2022-06-28"  
NOTION_API = "https://api.notion.com/v1"  
TPE = timezone(timedelta(hours=8))  
  
DASHBOARD_DB_ID = "dc9ca081-55a3-4726-b2d4-da9da67fcba5"  
DIGEST_DB_ID    = "5129cfe1-911f-453b-9b97-ea7b4df8f5e7"  
  
  
# ── Notion helpers ────────────────────────────────────────────  
  
class NotionError(Exception):  
    pass  
  
  
def _http(method, url, token, payload=None):  
    data = json.dumps(payload).encode() if payload else None  
    req = urllib.request.Request(  
        url, data=data,  
        headers={  
            "Authorization": f"Bearer {token}",  
            "Notion-Version": NOTION_VERSION,  
            "Content-Type": "application/json",  
        },  
        method=method,  
    )  
    try:  
        with urllib.request.urlopen(req, timeout=20) as r:  
            return json.loads(r.read())  
    except urllib.error.HTTPError as e:  
        body = e.read().decode("utf-8", errors="replace")  
        raise NotionError(f"HTTP {e.code} {method} {url}: {body[:300]}")  
    except urllib.error.URLError as e:  
        raise NotionError(f"URLError {method}: {e}")  
  
  
def nget(token, url):  
    return _http("GET", url, token)  
  
  
def npost(token, url, payload):  
    return _http("POST", url, token, payload)  
  
  
def npatch(token, url, payload):  
    return _http("PATCH", url, token, payload)  
  
  
def query_db(token, db_id, filter_payload=None, page_size=50):  
    if not db_id:  
        return []  
    payload = {"page_size": page_size}  
    if filter_payload:  
        payload["filter"] = filter_payload  
    try:  
        r = npost(token, f"{NOTION_API}/databases/{db_id}/query", payload)  
        return r.get("results", [])  
    except NotionError as e:  
        print(f"  [WARN] query_db {db_id}: {e}")  
        return []  
  
  
def get_text(prop):  
    if not prop:  
        return ""  
    t = prop.get("type", "")  
    if t in ("title", "rich_text"):  
        return "".join(i.get("plain_text", "") for i in prop.get(t, []))  
    if t == "select":  
        s = prop.get("select") or {}  
        return s.get("name", "")  
    if t == "number":  
        v = prop.get("number")  
        return str(v) if v is not None else ""  
    if t == "date":  
        d = prop.get("date") or {}  
        return d.get("start", "")  
    if t == "url":  
        return prop.get("url", "")  
    return ""  
  
  
# ── Block builders ────────────────────────────────────────────  
  
def h2(text):  
    return {"object": "block", "type": "heading_2",  
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}  
  
  
def para(text):  
    return {"object": "block", "type": "paragraph",  
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}  
  
  
def bullet(text):  
    return {"object": "block", "type": "bulleted_list_item",  
            "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}  
  
  
def callout(text, emoji="📌"):  
    return {"object": "block", "type": "callout",  
            "callout": {  
                "rich_text": [{"type": "text", "text": {"content": text[:1900]}}],  
                "icon": {"type": "emoji", "emoji": emoji},  
            }}  
  
  
def divider():  
    return {"object": "block", "type": "divider", "divider": {}}  
  
  
def bookmark(url, caption=""):  
    return {"object": "block", "type": "bookmark",  
            "bookmark": {"url": url, "caption": [{"type": "text", "text": {"content": caption}}] if caption else []}}  
  
  
# ── 防重複機制 ────────────────────────────────────────────────  
  
def check_dashboard_exists(token, date_str):  
    """查詢 Dashboard DB 當天是否已有記錄，有則回傳 page_id，沒有回傳 None"""  
    pages = query_db(token, DASHBOARD_DB_ID, filter_payload={  
        "property": "日期",  
        "title": {"equals": date_str}  
    }, page_size=1)  
    return pages[0]["id"] if pages else None  
  
  
# ── 區塊一：市場掃描摘要 ──────────────────────────────────────  
  
def section_scan_summary(token, date_str):  
    """連結到 daily-scan-summary 子頁面（搜尋今日掃描摘要）"""  
    blocks = [h2("📊 市場掃描摘要")]  
    try:  
        # 搜尋今日掃描摘要頁面  
        date_iso = date_str  # YYYY-MM-DD  
        title = f"📋 掃描摘要 {date_iso}"  
        result = npost(token, f"{NOTION_API}/search", {  
            "query": title,  
            "filter": {"value": "page", "property": "object"},  
            "page_size": 3,  
        })  
        found = False  
        for page in result.get("results", []):  
            props = page.get("properties", {})  
            page_title = "".join(  
                t.get("plain_text", "")  
                for t in props.get("title", {}).get("title", [])  
            )  
            if title in page_title or date_iso in page_title:  
                url = page.get("url", "")  
                blocks.append(callout(f"今日掃描摘要已產出，包含六個 Top N 分析", "📊"))  
                if url:  
                    blocks.append(bookmark(url, "點此查看完整 Top N 分析"))  
                found = True  
                break  
        if not found:  
            blocks.append(para("今日掃描摘要尚未產出（daily-scan-summary 未執行或執行中）"))  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取掃描摘要失敗: {e}"))  
    return blocks  
  
  
# ── 區塊二：券商日摘 ─────────────────────────────────────────  
  
def section_broker_digest(token, date_str):  
    """從 📰 券商日摘 DB 讀今日記錄"""  
    blocks = [h2("📰 券商日摘")]  
    try:  
        pages = query_db(token, DIGEST_DB_ID, filter_payload={  
            "property": "日期",  
            "title": {"equals": date_str}  
        }, page_size=1)  
        if not pages:  
            blocks.append(para("今日券商日摘尚未產出（broker_digest 未執行或無新報告）"))  
            return blocks  
        page = pages[0]  
        props = page.get("properties", {})  
        stock_count = get_text(props.get("個股報告數")) or "0"  
        industry_count = get_text(props.get("產業報告數")) or "0"  
        url = page.get("url", "")  
        blocks.append(callout(  
            f"個股報告 {stock_count} 份　｜　產業報告 {industry_count} 份",  
            "📰"  
        ))  
        if url:  
            blocks.append(bookmark(url, "點此查看完整券商日摘（三段式：晨訊 / 個股 / 產業）"))  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取券商日摘失敗: {e}"))  
    return blocks  
  
  
# ── 區塊三：追蹤個股異動 ─────────────────────────────────────  
  
def section_tracking(token, secrets, date_str):  
    """今日出現在掃描結果的追蹤個股"""  
    blocks = [h2("📋 追蹤個股異動")]  
    tracking_db_id = secrets.get("notion_stock_tracking_db")  
    scan_db_id = secrets.get("notion_scan_results_db")  
  
    if not tracking_db_id:  
        blocks.append(para("stock_tracking DB 未設定"))  
        return blocks  
  
    try:  
        # 取追蹤清單的股票代碼  
        tracking_pages = query_db(token, tracking_db_id, page_size=100)  
        tracked = {}  
        for p in tracking_pages:  
            props = p.get("properties", {})  
            # 嘗試多個可能的欄位名稱  
            for key in ["股票代碼", "代碼", "Code"]:  
                code_prop = props.get(key)  
                if code_prop:  
                    code = get_text(code_prop).strip()  
                    if code:  
                        name_prop = props.get("公司名稱") or props.get("名稱") or {}  
                        tracked[code] = get_text(name_prop) or code  
                    break  
  
        if not tracked:  
            blocks.append(para("追蹤清單目前為空"))  
            return blocks  
  
        # 從今日 scan_results DB 取出今日有出現的股票  
        if scan_db_id:  
            today_pages = query_db(token, scan_db_id, filter_payload={  
                "property": "掃描日期",  
                "date": {"equals": date_str}  
            }, page_size=100)  
            today_codes = set()  
            for p in today_pages:  
                props = p.get("properties", {})  
                name_raw = get_text(props.get("股票名稱") or {})  
                # 格式是「公司名稱/代碼」  
                if "/" in name_raw:  
                    code = name_raw.split("/")[-1].strip()  
                    today_codes.add(code)  
  
            appeared = {c: n for c, n in tracked.items() if c in today_codes}  
            if appeared:  
                blocks.append(callout(f"今日掃描命中 {len(appeared)} 檔追蹤股", "⭐"))  
                for code, name in sorted(appeared.items()):  
                    blocks.append(bullet(f"{code} {name}"))  
            else:  
                blocks.append(para("今日掃描結果中無追蹤個股"))  
        else:  
            blocks.append(para(f"共追蹤 {len(tracked)} 檔，scan_results DB 未設定，無法比對今日命中"))  
  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取追蹤個股失敗: {e}"))  
    return blocks  
  
  
# ── 區塊四：未來 14 天法說 ───────────────────────────────────  
  
def section_events(token, secrets):  
    """未來 14 天的法說會行事曆"""  
    blocks = [h2("📅 未來 14 天法說會")]  
    db_id = secrets.get("notion_event_calendar_db")  
    if not db_id:  
        blocks.append(para("event_calendar DB 未設定"))  
        return blocks  
    try:  
        today = datetime.now(TPE).date()  
        end = today + timedelta(days=14)  
        today_str = today.isoformat()  
        end_str = end.isoformat()  
  
        events = query_db(token, db_id, page_size=50)  
        upcoming = []  
        for e in events:  
            props = e.get("properties", {})  
            # 日期欄位：嘗試「預計日期」（title 類型）和「實際日期」（date 類型）  
            d = ""  
            actual = props.get("實際日期", {}).get("date") or {}  
            d = actual.get("start", "")  
            if not d:  
                # 預計日期是 title 類型，裡面存日期字串  
                d = get_text(props.get("預計日期") or {})[:10]  
            if today_str <= d <= end_str:  
                code = get_text(props.get("股票代碼") or {})  
                name = get_text(props.get("公司名稱") or {})  
                etype = get_text(props.get("事件類型") or {})  
                upcoming.append((d, code, name, etype))  
  
        if upcoming:  
            upcoming.sort()  
            for d, code, name, etype in upcoming:  
                blocks.append(bullet(f"{d}　{code} {name}　{etype}"))  
        else:  
            blocks.append(para("未來 14 天無法說會事件"))  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取法說行事曆失敗: {e}"))  
    return blocks  
  
  
# ── 區塊五：Outcome 未來 7 天到期 ───────────────────────────  
  
def section_outcome(token, secrets):  
    """未來 7 天內即將到期的 Outcome Review"""  
    blocks = [h2("⚠️ Outcome 即將到期（7天內）")]  
    db_id = secrets.get("notion_stock_tracking_db")  
    if not db_id:  
        blocks.append(para("stock_tracking DB 未設定"))  
        return blocks  
    try:  
        today = datetime.now(TPE).date()  
        in_7 = (today + timedelta(days=7)).isoformat()  
        today_str = today.isoformat()  
  
        due = query_db(token, db_id, filter_payload={  
            "and": [  
                {"property": "下次驗證日", "date": {"on_or_before": in_7}},  
                {"property": "下次驗證日", "date": {"on_or_after": today_str}},  
                {"property": "Outcome狀態", "select": {"equals": "待驗證"}},  
            ]  
        }, page_size=50)  
  
        if not due:  
            blocks.append(para("未來 7 天無 Outcome 到期"))  
            return blocks  
  
        today_due = []  
        soon_due = []  
        for p in due:  
            props = p.get("properties", {})  
            code = get_text(props.get("股票代碼") or {})  
            d = get_text(props.get("下次驗證日") or {})[:10]  
            if d == today_str:  
                today_due.append((d, code))  
            else:  
                soon_due.append((d, code))  
  
        if today_due:  
            blocks.append(callout(f"今日到期 {len(today_due)} 筆", "🔴"))  
            for d, code in sorted(today_due):  
                blocks.append(bullet(f"{code}　到期日：{d}"))  
        if soon_due:  
            blocks.append(para(f"即將到期（7天內）{len(soon_due)} 筆："))  
            for d, code in sorted(soon_due):  
                blocks.append(bullet(f"{code}　到期日：{d}"))  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取 Outcome 到期失敗: {e}"))  
    return blocks  
  
  
# ── 區塊六：今日新聞 ─────────────────────────────────────────  
  
def section_news(token, secrets, date_str):  
    """從 notion_news_db 讀今日新聞前 5 條"""  
    blocks = [h2("📰 今日新聞")]  
    db_id = secrets.get("notion_news_db")  
    if not db_id:  
        blocks.append(para("notion_news_db 未設定"))  
        return blocks  
    try:  
        news = query_db(token, db_id, filter_payload={  
            "property": "日期",  
            "date": {"equals": date_str}  
        }, page_size=10)  
  
        if not news:  
            # 嘗試用 createdTime 近似  
            news = query_db(token, db_id, page_size=5)  
  
        if not news:  
            blocks.append(para("今日無新聞記錄（news_pipeline 可能尚未執行）"))  
            return blocks  
  
        blocks.append(callout(f"今日共 {len(news)} 則新聞", "📰"))  
        for n in news[:5]:  
            props = n.get("properties", {})  
            title = get_text(props.get("標題") or props.get("title") or props.get("新聞標題") or {})  
            if title:  
                blocks.append(bullet(title[:150]))  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取新聞失敗: {e}"))  
    return blocks  
  
  
# ── 區塊七：週健診待辦（週一才顯示）─────────────────────────  
  
def section_weekly_health(token):  
    """搜尋最新週健診頁面，顯示待修改項目（只在週一顯示）"""  
    blocks = [h2("📋 週健診待辦")]  
    now = datetime.now(TPE)  
    if now.weekday() != 0:  # 0 = 週一  
        blocks.append(para("（僅週一顯示週健診摘要）"))  
        return blocks  
    try:  
        result = npost(token, f"{NOTION_API}/search", {  
            "query": "週健診",  
            "filter": {"value": "page", "property": "object"},  
            "page_size": 3,  
        })  
        pages = result.get("results", [])  
        if not pages:  
            blocks.append(para("找不到週健診頁面"))  
            return blocks  
        # 取最新一筆  
        page = pages[0]  
        url = page.get("url", "")  
        props = page.get("properties", {})  
        title = get_text(props.get("title") or {})  
        blocks.append(callout(f"最新週健診：{title}", "🏥"))  
        if url:  
            blocks.append(bookmark(url, "點此查看週健診完整內容"))  
    except Exception as e:  
        blocks.append(para(f"[ERROR] 讀取週健診失敗: {e}"))  
    return blocks  
  
  
# ── 建立 Notion 頁面 ─────────────────────────────────────────  
  
def build_blocks(token, secrets, date_str):  
    """組裝所有區塊，每個區塊失敗不影響其他"""  
    now_str = datetime.now(TPE).strftime("%Y-%m-%d %H:%M")  
    blocks = []  
    blocks.append(callout(  
        f"產生時間：{now_str} (台北)　|　日期：{date_str}",  
        "📅"  
    ))  
    blocks.append(divider())  
  
    sections = [  
        ("市場掃描摘要", section_scan_summary,    (token, date_str)),  
        ("券商日摘",     section_broker_digest,   (token, date_str)),  
        ("追蹤個股",     section_tracking,        (token, secrets, date_str)),  
        ("法說行事曆",   section_events,          (token, secrets)),  
        ("Outcome到期",  section_outcome,         (token, secrets)),  
        ("今日新聞",     section_news,            (token, secrets, date_str)),  
        ("週健診",       section_weekly_health,   (token,)),  
    ]  
  
    for name, fn, args in sections:  
        try:  
            blocks.extend(fn(*args))  
        except Exception as e:  
            blocks.append(h2(f"[ERROR] {name}"))  
            blocks.append(para(str(e)))  
        blocks.append(divider())  
  
    return blocks  
  
  
def write_dashboard(token, date_str, blocks, stock_count=0):  
    """寫入 Dashboard DB，有防重複機制"""  
    existing_id = check_dashboard_exists(token, date_str)  
    props = {  
        "日期": {"title": [{"text": {"content": date_str}}]},  
        "掃描日期": {"date": {"start": date_str}},  
        "個股掃描數": {"number": stock_count},  
        "狀態": {"select": {"name": "完整"}},  
    }  
    headers_ = {  
        "Authorization": f"Bearer {token}",  
        "Notion-Version": NOTION_VERSION,  
        "Content-Type": "application/json",  
    }  
  
    if existing_id:  
        print(f"  [Notion] 今日 Dashboard 已存在，更新...")  
        # 更新 properties  
        req = urllib.request.Request(  
            f"{NOTION_API}/pages/{existing_id}",  
            json.dumps({"properties": props}).encode(),  
            headers_, method="PATCH"  
        )  
        with urllib.request.urlopen(req, timeout=20):  
            pass  
        # Append blocks  
        for i in range(0, len(blocks), 100):  
            chunk = blocks[i:i+100]  
            req2 = urllib.request.Request(  
                f"{NOTION_API}/blocks/{existing_id}/children",  
                json.dumps({"children": chunk}).encode(),  
                headers_, method="PATCH"  
            )  
            with urllib.request.urlopen(req2, timeout=20):  
                pass  
        return f"https://notion.so/{existing_id.replace('-', '')}"  
    else:  
        print(f"  [Notion] 新建今日 Dashboard...")  
        payload = {  
            "parent": {"database_id": DASHBOARD_DB_ID},  
            "properties": props,  
            "children": blocks[:100],  
        }  
        req = urllib.request.Request(  
            f"{NOTION_API}/pages",  
            json.dumps(payload).encode(),  
            headers_, method="POST"  
        )  
        with urllib.request.urlopen(req, timeout=20) as r:  
            result = json.loads(r.read())  
            page_id = result["id"]  
        # 分批 append 剩餘 blocks  
        for i in range(100, len(blocks), 100):  
            chunk = blocks[i:i+100]  
            req2 = urllib.request.Request(  
                f"{NOTION_API}/blocks/{page_id}/children",  
                json.dumps({"children": chunk}).encode(),  
                headers_, method="PATCH"  
            )  
            with urllib.request.urlopen(req2, timeout=20):  
                pass  
            time.sleep(0.3)  
        url = f"https://notion.so/{page_id.replace('-', '')}"  
        print(f"  [Notion] Dashboard 建立：{url}")  
        return url  
  
  
# ── Telegram 通知 ────────────────────────────────────────────  
  
def send_telegram(secrets, date_str, notion_url):  
    bot_token = secrets.get("telegram_bot_token")  
    chat_id = secrets.get("telegram_dm")  
    if not bot_token or not chat_id:  
        print("  [Telegram] token 或 chat_id 未設定，跳過")  
        return  
    text = (  
        f"📅 每日 Dashboard {date_str} 已產出\n"  
        f"包含：市場掃描 / 券商日摘 / 追蹤個股 / 法說 / Outcome / 新聞\n"  
        f"👉 {notion_url}"  
    )  
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()  
    req = urllib.request.Request(  
        f"https://api.telegram.org/bot{bot_token}/sendMessage",  
        payload,  
        headers={"Content-Type": "application/json"},  
    )  
    try:  
        urllib.request.urlopen(req, timeout=10)  
        print(f"  [Telegram] 通知已發送")  
    except Exception as e:  
        print(f"  [Telegram] 發送失敗（不影響主流程）: {e}")  
  
  
# ── 主程式 ───────────────────────────────────────────────────  
  
def main():  
    with open(SECRETS_PATH) as f:  
        secrets = json.load(f)  
    token = secrets.get("notion_key")  
    if not token:  
        print("[ERROR] notion_key 未設定")  
        sys.exit(1)  
  
    date_str = datetime.now(TPE).strftime("%Y-%m-%d")  
    print(f"=== daily_dashboard.py {date_str} ===")  
  
    # 讀取今日掃描筆數（供 properties 用）  
    scan_date_compact = date_str.replace("-", "")  
    scan_file = STATE_DIR / f"scan_results_{scan_date_compact}.json"  
    stock_count = 0  
    if scan_file.exists():  
        try:  
            data = json.loads(scan_file.read_text())  
            codes = set()  
            for entries in data.get("results", {}).values():  
                for e in entries:  
                    if e.get("code"):  
                        codes.add(str(e["code"]))  
            stock_count = len(codes)  
        except Exception:  
            pass  
    print(f"  今日掃描個股數：{stock_count}")  
  
    print("  組裝 Dashboard blocks...")  
    blocks = build_blocks(token, secrets, date_str)  
    print(f"  共 {len(blocks)} 個 blocks")  
  
    print("  寫入 Notion...")  
    notion_url = write_dashboard(token, date_str, blocks, stock_count)  
  
    print("  發送 Telegram 通知...")  
    send_telegram(secrets, date_str, notion_url)  
  
    print(f"=== 完成 ===")  
    print(f"Dashboard: {notion_url}")  
  
  
if __name__ == "__main__":  
    main()  
