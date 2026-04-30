#!/usr/bin/env python3
"""
broker_digest.py — 每日券商報告日摘
每日 台北時間 23:00（UTC 15:00）cron 觸發

三段式輸出：
 1. 晨訊重點（合併多份晨報，相同訊息不重複）
 2. 個股匯整（同標的各券商目標價/EPS/評等/觀點）
 3. 產業報告（各份產業研究摘要）

輸出：Telegram（4096 字元限制，超過分段）+ Notion hub page 子頁
"""

import json
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKSPACE = Path(__file__).parent.parent
STATE_DIR = WORKSPACE / "state"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"

TZ_TAIPEI = timezone(timedelta(hours=8))


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def today_taipei():
    return datetime.now(tz=TZ_TAIPEI).strftime("%Y-%m-%d")


def today_taipei_compact():
    return datetime.now(tz=TZ_TAIPEI).strftime("%Y%m%d")


# ── Notion API ──────────────────────────────────────────────

def notion_query(db_id, token, filter_date):
    """撈當天的 Notion 資料庫記錄"""
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "filter": {
            "property": "報告日期",
            "date": {"equals": filter_date}
        },
        "page_size": 100,
    }
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[Notion 查詢失敗] {db_id}: {e}")
        return {"results": []}


def get_text(prop):
    """從 Notion property 取純文字"""
    if not prop:
        return ""
    t = prop.get("type", "")
    if t == "title":
        items = prop.get("title", [])
    elif t == "rich_text":
        items = prop.get("rich_text", [])
    elif t == "select":
        sel = prop.get("select")
        return sel.get("name", "") if sel else ""
    elif t == "number":
        v = prop.get("number")
        return str(v) if v is not None else ""
    elif t == "date":
        d = prop.get("date")
        return d.get("start", "") if d else ""
    else:
        return ""
    return "".join(item.get("plain_text", "") for item in items)


# ── 資料撈取 ────────────────────────────────────────────────

def fetch_stock_reports(secrets, date_str):
    data = notion_query(secrets["notion_broker_reports_db"], secrets["notion_key"], date_str)
    reports = []
    for page in data.get("results", []):
        p = page.get("properties", {})
        reports.append({
            "code": get_text(p.get("股票代碼")),
            "name": get_text(p.get("公司名稱")),
            "broker": get_text(p.get("券商名稱")),
            "rating": get_text(p.get("評等")),
            "target": get_text(p.get("目標價")),
            "eps_this": get_text(p.get("EPS預測_今年")),
            "eps_next": get_text(p.get("EPS預測_明年")),
            "rev_this": get_text(p.get("營收預測_今年")),
            "rev_next": get_text(p.get("營收預測_明年")),
            "gross": get_text(p.get("毛利率預測")),
            "view": get_text(p.get("核心觀點")),
        })
    return reports


def fetch_industry_reports(secrets, date_str):
    data = notion_query(secrets["notion_industry_reports_db"], secrets["notion_key"], date_str)
    reports = []
    for page in data.get("results", []):
        p = page.get("properties", {})
        reports.append({
            "title": get_text(p.get("產業主題")),
            "broker": get_text(p.get("券商名稱")),
            "industry": get_text(p.get("產業分類")),
            "view": get_text(p.get("核心觀點")),
            "numbers": get_text(p.get("關鍵數字")),
        })
    return reports


def load_morning_briefs(date_compact):
    """讀取今日晨報合併檔"""
    f = STATE_DIR / f"broker_morning_{date_compact}.txt"
    if not f.exists():
        return ""
    return f.read_text(encoding="utf-8")


# ── M2.7 摘要 ───────────────────────────────────────────────

def call_m27(prompt, secrets, max_tokens=2000):
    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": max_tokens,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": secrets["minimax_api_key"],
            "anthropic-version": "2023-06-01",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
            blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
            return blocks[0]["text"].strip() if blocks else ""
    except Exception as e:
        print(f"[M2.7 失敗] {e}")
        return ""


def summarize_morning(morning_text, secrets):
    if not morning_text.strip():
        return "（今日無晨報）"
    prompt = f"""以下是今日多份券商晨報的原始內容。
請整合成繁體中文重點摘要，規則：
1. 相同訊息只提一次，不同券商的補充資訊才額外標注
2. 按主題分組（市場概況、重點產業、個股異動）
3. 每點限 2-3 句，帶具體數字
4. 總長度控制在 800 字以內

晨報內容：
{morning_text[:8000]}"""
    return call_m27(prompt, secrets, max_tokens=1500)


def summarize_industry(reports, secrets):
    if not reports:
        return "（今日無產業報告）"
    text = "\n\n".join([
        f"【{r['title']}】{r['broker']}\n{r['view']}\n關鍵數字：{r['numbers']}"
        for r in reports
    ])
    prompt = f"""以下是今日券商產業報告摘要。
請用繁體中文整理，每份報告 3-5 句重點，帶具體數字，格式：
【產業主題】券商名稱
重點內容...

報告資料：
{text[:6000]}"""
    return call_m27(prompt, secrets, max_tokens=1500)


# ── 個股匯整（不用 AI，直接結構化）────────────────────────

def format_stock_section(reports):
    if not reports:
        return "（今日無個股報告）"

    # 按股票代碼分組
    stocks = {}
    for r in reports:
        code = r["code"]
        if code not in stocks:
            stocks[code] = {"name": r["name"], "reports": []}
        stocks[code]["reports"].append(r)

    lines = []
    for code, data in sorted(stocks.items()):
        rpts = data["reports"]
        name = data["name"] or code
        lines.append(f"\n【{code} {name}】{len(rpts)} 份報告")

        # 目標價
        targets = [f"{r['target']}（{r['broker']}）" for r in rpts if r["target"]]
        if targets:
            lines.append(f"目標價：{' / '.join(targets)}")

        # 評等
        ratings = [f"{r['rating']}（{r['broker']}）" for r in rpts if r["rating"]]
        if ratings:
            lines.append(f"評等：{' / '.join(ratings)}")

        # EPS
        eps = [f"{r['eps_this']}/{r['eps_next']}（{r['broker']}）" for r in rpts if r["eps_this"]]
        if eps:
            lines.append(f"EPS 今年/明年：{' / '.join(eps)}")

        # 核心觀點（各家各列一條）
        for r in rpts:
            if r["view"]:
                view_short = r["view"][:120] + ("…" if len(r["view"]) > 120 else "")
                lines.append(f" {r['broker']}：{view_short}")

    return "\n".join(lines)


# ── Telegram 發送 ───────────────────────────────────────────

def send_telegram(text, secrets):
    url = f"https://api.telegram.org/bot{secrets['telegram_bot_token']}/sendMessage"
    # 分段發送（4096 字元限制）
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        payload = {
            "chat_id": secrets["telegram_dm"],
            "text": chunk,
        }
        req = urllib.request.Request(url, json.dumps(payload).encode(),
                                    headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            print(f"[Telegram 發送失敗] {e}")
        time.sleep(0.5)


# ── Notion 寫入（hub page 子頁）────────────────────────────

def write_notion_digest(date_str, content_text, secrets):
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {secrets['notion_key']}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    # 切成多個 rich_text block（2000 字元限制）
    blocks = []
    for i in range(0, min(len(content_text), 18000), 1800):
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": content_text[i:i+1800]}}]
            }
        })
    payload = {
        "parent": {"page_id": secrets["notion_parent_db_id"]},
        "properties": {
            "title": {"title": [{"text": {"content": f"📊 券商報告日摘 {date_str}"}}]}
        },
        "children": blocks,
    }
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            page_url = result.get("url", "")
            print(f"[Notion] 日摘頁面建立：{page_url}")
    except Exception as e:
        print(f"[Notion 寫入失敗] {e}")


# ── 主程式 ──────────────────────────────────────────────────

def main():
    secrets = load_secrets()
    date_str = today_taipei()
    date_compact = today_taipei_compact()

    print(f"=== broker_digest.py {date_str} ===")

    # 1. 讀取資料
    print("[1] 讀取個股報告...")
    stock_reports = fetch_stock_reports(secrets, date_str)
    print(f" → {len(stock_reports)} 筆")

    print("[2] 讀取產業報告...")
    industry_reports = fetch_industry_reports(secrets, date_str)
    print(f" → {len(industry_reports)} 筆")

    print("[3] 讀取晨報...")
    morning_text = load_morning_briefs(date_compact)
    print(f" → {'有資料' if morning_text.strip() else '無資料'}")

    if not stock_reports and not industry_reports and not morning_text.strip():
        print("[SKIP] 今日無任何報告，跳過發送")
        return

    # 2. 摘要生成
    print("[4] 生成晨訊摘要（M2.7）...")
    morning_summary = summarize_morning(morning_text, secrets)

    print("[5] 生成產業報告摘要（M2.7）...")
    industry_summary = summarize_industry(industry_reports, secrets)

    print("[6] 整理個股匯整...")
    stock_section = format_stock_section(stock_reports)

    # 3. 組裝輸出
    now_str = datetime.now(tz=TZ_TAIPEI).strftime("%H:%M")
    output = f"""📊 券商報告日摘 {date_str}

━━━ 一、晨訊重點 ━━━
{morning_summary}

━━━ 二、個股匯整 ━━━
{stock_section}

━━━ 三、產業報告 ━━━
{industry_summary}

⏰ {now_str} | 個股{len(stock_reports)}份 產業{len(industry_reports)}份"""

    # 4. 發送
    print("[7] 發送 Telegram...")
    send_telegram(output, secrets)
    print(" → 完成")

    print("[8] 寫入 Notion...")
    write_notion_digest(date_str, output, secrets)
    print(" → 完成")

    print("=== broker_digest.py 完成 ===")


if __name__ == "__main__":
    sys.exit(main())
