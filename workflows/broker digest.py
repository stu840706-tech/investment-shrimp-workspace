#!/usr/bin/env python3
"""
broker_digest.py — 每日券商報告日摘
每日 台北時間 23:00（UTC 15:00）cron 觸發

三段式輸出：
 1. 晨訊重點（合併多份晨報，相同訊息不重複）
 2. 個股匯整（同標的各券商目標價/EPS/評等/觀點）
 3. 產業報告（各份產業研究摘要）

輸出：Telegram（4096 字元限制，超過分段）+ Notion hub page 子頁

【方案 C】不再依賴報告日期，只取從未被日摘標記的報告。
每份寫入 Notion 的報告都帶有 digest_mark="processed" 欄位。
broker_digest 查詢 digest_mark 為空的記錄，處理後更新為已標記。
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

def notion_query_unprocessed(db_id, token):
    """
    查詢 digest_mark 欄位為空的記錄（即尚未被寫入日摘的報告）。
    Notion filter: text 類型屬性 is_empty
    """
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "filter": {
            "property": "digest_mark",
            "rich_text": {"equals": "processed"}
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


def mark_reports_processed(page_ids, token):
    """
    將一批 page IDs 的 digest_mark 更新為 "digested-YYYY-MM-DD"。
    Notion 不支援 batch PATCH，改为逐一更新。
    """
    if not page_ids:
        return
    url_base = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    today_str = today_taipei()
    mark_value = f"digested-{today_str}"
    success = 0
    for pid in page_ids:
        payload = {
            "properties": {
                "digest_mark": {"rich_text": [{"text": {"content": mark_value}}]}
            }
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(f"{url_base}/{pid}", data, headers, method="PATCH")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                json.loads(r.read())
            success += 1
        except Exception as e:
            print(f"  [標記失敗] {pid}: {e}")
    print(f"  [標記完成] {success}/{len(page_ids)} 筆 → {mark_value}")


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

def fetch_stock_reports(secrets):
    """取所有尚未被日摘標記的個股報告。"""
    data = notion_query_unprocessed(secrets["notion_broker_reports_db"], secrets["notion_key"])
    reports = []
    for page in data.get("results", []):
        p = page.get("properties", {})
        reports.append({
            "page_id": page["id"],
            "code":    get_text(p.get("股票代碼")),
            "name":    get_text(p.get("公司名稱")),
            "broker":  get_text(p.get("券商名稱")),
            "rating":  get_text(p.get("評等")),
            "target":  get_text(p.get("目標價")),
            "eps_this":  get_text(p.get("EPS預測_今年")),
            "eps_next":  get_text(p.get("EPS預測_明年")),
            "rev_this":  get_text(p.get("營收預測_今年")),
            "rev_next":  get_text(p.get("營收預測_明年")),
            "gross":   get_text(p.get("毛利率預測")),
            "view":    get_text(p.get("核心觀點")),
            "excerpt": get_text(p.get("原始內文")),
            "report_date": get_text(p.get("報告日期")),
        })
    return reports


def fetch_industry_reports(secrets):
    """取所有尚未被日摘標記的產業報告。"""
    data = notion_query_unprocessed(secrets["notion_industry_reports_db"], secrets["notion_key"])
    reports = []
    for page in data.get("results", []):
        p = page.get("properties", {})
        reports.append({
            "page_id": page["id"],
            "title":   get_text(p.get("產業主題")),
            "broker":  get_text(p.get("券商名稱")),
            "industry": get_text(p.get("產業分類")),
            "view":    get_text(p.get("核心觀點")),
            "numbers": get_text(p.get("關鍵數字")),
            "excerpt": get_text(p.get("原始內文_關鍵段落")),
            "report_date": get_text(p.get("報告日期")),
        })
    return reports


def load_morning_briefs(date_compact):
    """讀取今日晨報合併檔（同時支援今日與昨日，Kai 在交易日盤中陸續傳送，凌晨執行時取前一日檔案）。"""
    today_file = STATE_DIR / f"broker_morning_{date_compact}.txt"
    # 也嘗試讀取前一日的晨報檔（覆盤时段内 Kai 仍在傳送）
    from datetime import datetime, timezone, timedelta
    TZ_TPE = timezone(timedelta(hours=8))
    d = datetime.now(TZ_TPE)
    yday = d - timedelta(days=1)
    yesterday_file = STATE_DIR / f"broker_morning_{yday.strftime('%Y%m%d')}.txt"
    texts = []
    if today_file.exists():
        texts.append(today_file.read_text(encoding="utf-8"))
    if yesterday_file.exists() and yesterday_file != today_file:
        texts.append(yesterday_file.read_text(encoding="utf-8"))
    return "\n".join(texts)


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



def parse_morning_sections(raw_text):
    import re
    sections = []
    raw_parts = [p.strip() for p in raw_text.split("=" * 40) if p.strip()]
    for part in raw_parts:
        broker = ""
        stocks = []
        view = ""
        body_lines = []
        for line in part.splitlines():
            line_s = line.strip()
            if line_s.startswith("券商："):
                broker = line_s[3:].strip()
            elif line_s.startswith("股票："):
                raw_codes = line_s[3:].strip()
                stocks = re.findall(r'\b\d{4}\b', raw_codes)
            elif line_s.startswith("核心觀點："):
                view = line_s[5:].strip()
            elif line_s.startswith("原文："):
                body_lines.append(line_s[3:].strip())
            elif body_lines:
                body_lines.append(line_s)
        sections.append({
            "broker": broker,
            "stocks": stocks,
            "view": view,
            "body": "\n".join(body_lines)[:1500],
        })
    return sections


def merge_morning_briefs(morning_text, secrets):
    import re, time
    if not morning_text.strip():
        return morning_text

    sections = parse_morning_sections(morning_text)
    if not sections:
        return morning_text

    print(" [晨報合併] 共 {} 份晨報".format(len(sections)))

    # 按股票代碼分組
    groups = {}
    for sec in sections:
        if sec["stocks"]:
            key = ",".join(sorted(sec["stocks"])[:3])
        else:
            key = "總經市場"
        if key not in groups:
            groups[key] = []
        groups[key].append(sec)

    print(" [晨報合併] 分成 {} 個主題組".format(len(groups)))

    merged_parts = []
    for key, group_secs in groups.items():
        if len(group_secs) == 1:
            sec = group_secs[0]
            label = "【{}】{}".format(key, sec["broker"]) if key != "總經市場" else "【總經市場】{}".format(sec["broker"])
            merged_parts.append(label + "\n" + (sec["view"] or sec["body"][:300]))
            continue

        combined = ""
        for i, sec in enumerate(group_secs):
            combined += "\n--- 第{}份 券商：{} ---\n".format(i+1, sec["broker"])
            combined += "核心觀點：" + sec["view"] + "\n"
            if sec["body"]:
                combined += "補充：" + sec["body"][:500] + "\n"

        prompt = ("以下是 {} 家券商對同一主題（{}）的市場觀點。"
                  "請用繁體中文合併成一份，規則："
                  "1. 以第一份為主體骨架"
                  "2. 其他券商有額外數字或不同觀點才補充，格式：「（XX 券商補充：...）」"
                  "3. 相同的描述不重複"
                  "4. 長度控制在 200 字以內"
                  "\n\n券商觀點：{}").format(len(group_secs), key, combined[:3000])

        merged = call_m27(prompt, secrets, max_tokens=400)
        if merged:
            merged_parts.append("【{}】\n{}".format(key, merged))
        else:
            sec = group_secs[0]
            merged_parts.append("【{}】{}\n{}".format(key, sec["broker"], sec["view"] or sec["body"][:300]))

        time.sleep(0.3)

    result = "\n\n".join(merged_parts)
    print(" [晨報合併] 完成，合併後 {} 字".format(len(result)))
    return result

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
請用繁體中文整理，每份報告 3-5 句重點，格式：
【產業主題】券商名稱
重點內容...

嚴格規則：
1. 只能使用報告資料中明確提供的數字，禁止自行推算或補充任何數字
2. 若「關鍵數字」欄位為空，只整理文字觀點，不要捏造數字
3. 沒有的資訊不要填，寧可省略也不要猜測

報告資料：
{text[:6000]}"""
    return call_m27(prompt, secrets, max_tokens=1500)


# ── 個股匯整（不用 AI，直接結構化）────────────────────────

def format_stock_section(reports):
    if not reports:
        return "（今日無個股報告）"

    # 按股票代碼分組（標準化：取數字部分，忽略 .TW/.TT 後綴）
    import re as _re
    stocks = {}
    for r in reports:
        raw_code = r["code"]
        norm_code = _re.sub(r"\.(TW|TT|TWO)$", "", raw_code, flags=_re.IGNORECASE)
        if norm_code not in stocks:
            stocks[norm_code] = {"name": r["name"], "reports": []}
        stocks[norm_code]["reports"].append(r)

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

def check_digest_exists(date_str, db_id, token):
    """查詢當天是否已有日摘，回傳 page_id 或 None"""
    url = "https://api.notion.com/v1/databases/" + db_id + "/query"
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "filter": {
            "property": "日期",
            "title": {"equals": date_str}
        },
        "page_size": 1,
    }
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            pages = result.get('results', [])
            return pages[0]['id'] if pages else None
    except Exception as e:
        print("[Notion 查詢失敗] " + str(e))
        return None


def build_notion_blocks(morning_summary, stock_section, industry_summary,
                         stock_count, industry_count, date_str, now_str):
    """將三段內容轉成結構化 Notion blocks"""
    blocks = []
    def h2(text):
        return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:180]}}]}}
    def para(text):
        return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}}
    def divider():
        return {"object": "block", "type": "divider", "divider": {}}
    def callout(text, emoji='📌'):
        return {"object": "block", "type": "callout",
            "callout": {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}], "icon": {"type": "emoji", "emoji": emoji}}}

    blocks.append(callout("產生時間：" + now_str + " (台北) | 個股報告：" + str(stock_count) + " 份 | 產業報告：" + str(industry_count) + " 份", "📊"))
    blocks.append(divider())

    blocks.append(h2("一、晨訊重點"))
    if morning_summary and morning_summary.strip() and morning_summary != '（今日無晨報）':
        for p in morning_summary.split('\n\n'):
            p = p.strip()
            if p:
                blocks.append(para(p))
    else:
        blocks.append(para("（今日無晨報）"))
    blocks.append(divider())

    blocks.append(h2("二、個股匯整"))
    if stock_section and stock_section.strip() and stock_section != '（今日無新個股報告）':
        for p in stock_section.split('\n\n'):
            p = p.strip()
            if p:
                blocks.append(para(p))
    else:
        blocks.append(para("（今日無新個股報告）"))
    blocks.append(divider())

    blocks.append(h2("三、產業報告"))
    if industry_summary and industry_summary.strip() and industry_summary != '（今日無產業報告）':
        for p in industry_summary.split('\n\n'):
            p = p.strip()
            if p:
                blocks.append(para(p))
    else:
        blocks.append(para("（今日無產業報告）"))

    return blocks


def write_notion_digest(date_str, morning_summary, stock_section, industry_summary,
                         stock_count, industry_count, secrets, morning_count=0):
    """寫入券商日摘，有防重複機制"""
    DIGEST_DB_ID = "5129cfe1-911f-453b-9b97-ea7b4df8f5e7"
    token = secrets['notion_key']
    now_str = datetime.now(tz=TZ_TAIPEI).strftime('%H:%M')

    existing_id = check_digest_exists(date_str, DIGEST_DB_ID, token)
    blocks = build_notion_blocks(morning_summary, stock_section, industry_summary,
                                 stock_count, industry_count, date_str, now_str)

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    if existing_id:
        print("[Notion] 今日日摘已存在，更新內容...")
        props_payload = {
            "properties": {
                "個股報告數": {"number": stock_count},
                "產業報告數": {"number": industry_count},
                "digest_mark": {"rich_text": [{"text": {"content": "updated-" + date_str}}]},
                "掃描日期": {"date": {"start": date_str}},
            }
        }
        req = urllib.request.Request(
            'https://api.notion.com/v1/pages/' + existing_id,
            json.dumps(props_payload).encode(), headers, method='PATCH')
        try:
            with urllib.request.urlopen(req, timeout=15): pass
        except Exception as e:
            print("[Notion 更新 properties 失敗] " + str(e))
        for i in range(0, len(blocks), 100):
            chunk = blocks[i:i+100]
            req2 = urllib.request.Request(
                'https://api.notion.com/v1/blocks/' + existing_id + '/children',
                json.dumps({'children': chunk}).encode(), headers, method='PATCH')
            try:
                with urllib.request.urlopen(req2, timeout=15): pass
            except Exception as e:
                print("[Notion append 失敗] " + str(e))
        print("[Notion] 更新完成：" + existing_id)

    else:
        print("[Notion] 新建今日日摘...")
        payload = {
            "parent": {"database_id": DIGEST_DB_ID},
            "properties": {
                "日期": {"title": [{"text": {"content": date_str}}]},
                "掃描日期": {"date": {"start": date_str}},
                "個股報告數": {"number": stock_count},
                "產業報告數": {"number": industry_count},
                "晨報份數": {"number": morning_count},
                "digest_mark": {"rich_text": [{"text": {"content": "digested-" + date_str}}]},
            },
            "children": blocks[:100],
        }
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            json.dumps(payload).encode(), headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                result = json.loads(r.read())
                page_id = result.get('id', '')
                print("[Notion] 日摘建立：" + page_id)
                if len(blocks) > 100:
                    for i in range(100, len(blocks), 100):
                        chunk = blocks[i:i+100]
                        req2 = urllib.request.Request(
                            "https://api.notion.com/v1/blocks/" + page_id + "/children",
                            json.dumps({'children': chunk}).encode(), headers, method='PATCH')
                        with urllib.request.urlopen(req2, timeout=15): pass
        except Exception as e:
            print("[Notion 寫入失敗] " + str(e))

# ── 主程式 ──────────────────────────────────────────────────

def main():
    secrets = load_secrets()
    date_str = today_taipei()
    date_compact = today_taipei_compact()

    print(f"=== broker_digest.py {date_str} ===")

    # 1. 讀取尚未被日摘標記的報告
    print("[1] 查詢尚未處理之個股報告（digest_mark=空）...")
    stock_reports = fetch_stock_reports(secrets)
    print(f" → {len(stock_reports)} 筆")

    print("[2] 查詢尚未處理之產業報告（digest_mark=空）...")
    industry_reports = fetch_industry_reports(secrets)
    print(f" → {len(industry_reports)} 筆")

    print("[3] 讀取晨報...")
    morning_text = load_morning_briefs(date_compact)
    morning_count = len(parse_morning_sections(morning_text)) if morning_text.strip() else 0
    print(f" → {'有資料' if morning_text.strip() else '無資料'}（{morning_count} 份）")

    if not stock_reports and not industry_reports and not morning_text.strip():
        print("[SKIP] 今日無任何新報告，仍寫入 0/0/0 日摘列作為健康訊號")
        write_notion_digest(
            date_str,
            "（今日無晨報）",
            "（今日無新個股報告）",
            "（今日無產業報告）",
            0,
            0,
            secrets,
            morning_count=0,
        )
        return

    # Log what we're working with
    parts = []
    if morning_text.strip(): parts.append("晨報")
    if stock_reports: parts.append(f"個股{len(stock_reports)}份")
    if industry_reports: parts.append(f"產業{len(industry_reports)}份")
    print(f" → 今日有內容：{', '.join(parts)}")

    # 2. 摘要生成
    print("[4] 智能合併晨報（M2.7 per 主題組）...")
    merged_morning = merge_morning_briefs(morning_text, secrets) if morning_text.strip() else morning_text

    morning_summary = merged_morning  # 直接用 merge_morning_briefs 輸出，不再二次壓縮

    print("[5] 生成產業報告摘要（M2.7）...")
    industry_summary = summarize_industry(industry_reports, secrets)

    print("[6] 整理個股匯整...")
    stock_section = format_stock_section(stock_reports)

    # 3. 組裝輸出
    now_str = datetime.now(tz=TZ_TAIPEI).strftime("%H:%M")
    output = f"""📊 券商日摘 {date_str}

━━━ 一、晨訊重點 ━━━
{morning_summary if morning_text.strip() else '（無新晨報）'}

━━━ 二、個股匯整 ━━━
{stock_section if stock_reports else '（今日無新個股報告）'}

━━━ 三、產業報告 ━━━
{industry_summary if industry_reports else '（今日無新產業報告）'}

⏰ {now_str} | 個股{len(stock_reports)}份 產業{len(industry_reports)}份"""

    # 4. 發送
    print("[7] 發送 Telegram...")
    send_telegram(output, secrets)
    print(" → 完成")

    print("[8] 寫入 Notion...")
    write_notion_digest(
        date_str,
        morning_summary if morning_text.strip() else "（今日無晨報）",
        stock_section if stock_reports else "（今日無新個股報告）",
        industry_summary if industry_reports else "（今日無產業報告）",
        len(stock_reports),
        len(industry_reports),
        secrets,
        morning_count=morning_count,
    )
    print(" → 完成")

    # 5. 標記已處理的報告
    print("[9] 標記已處理之報告（更新 digest_mark）...")
    stock_ids = [r["page_id"] for r in stock_reports]
    industry_ids = [r["page_id"] for r in industry_reports]
    if stock_ids:
        mark_reports_processed(stock_ids, secrets["notion_key"])
    if industry_ids:
        mark_reports_processed(industry_ids, secrets["notion_key"])
    if not stock_ids and not industry_ids:
        print("  （無須標記）")

    print("=== broker_digest.py 完成 ===")

    # 清理 broker_morning 檔（保留最近 7 天）
    import time as _time
    for f in STATE_DIR.glob("broker_morning_*.txt"):
        if f.stat().st_mtime < _time.time() - 86400 * 7:
            f.unlink()
            print(f"  [CLEAN] 刪除過期晨報: {f.name}")


if __name__ == "__main__":
    sys.exit(main())
