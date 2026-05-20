#!/usr/bin/env python3
"""
daily-scan-summary.py - 每日掃描 Top N 摘要子頁面
在「📊 每日掃描結果」DB 頁面底下建立子頁面，內容為六個 Top N 表格。
每天執行一次，同一天若已存在子頁面則先刪除再建立。

資料來源：scan_results_YYYYMMDD.json（與 daily-notion.py 相同）
輸出位置：SCAN_RESULTS_PAGE_ID 底下的子頁面
"""

import json, time, urllib.request, urllib.error, argparse
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ==================== 設定 ====================

STATE_DIR = Path("/home/ubuntu/.openclaw/workspace/state")
WORKSPACE = Path("/home/ubuntu/.openclaw/workspace")

from _common import now_tw, NOTION_KEY, SECRETS
NOTION_VERSION = "2022-06-28"

# 「📊 每日掃描結果」DB 的父頁面 ID（摘要子頁面掛在這裡）
SCAN_RESULTS_PAGE_ID = "34e226f5-a398-802f-bf27-fa7a4fa19970"

# ==================== Notion API ====================

def notion_request(method, url, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {NOTION_KEY}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"
        },
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"HTTP {e.code} {method} {url}: {body[:300]}")

def notion_post(url, payload):
    return notion_request("POST", url, payload)

def notion_patch(url, payload):
    return notion_request("PATCH", url, payload)

# ==================== 資料載入 ====================

def load_scan_results(date_str):
    file = STATE_DIR / f"scan_results_{date_str}.json"
    if not file.exists():
        yesterday = datetime.strptime(date_str, "%Y%m%d") - timedelta(days=1)
        file = STATE_DIR / f"scan_results_{yesterday.strftime('%Y%m%d')}.json"
    if file.exists():
        with open(file) as f:
            return json.load(f)
    return None

# ==================== 計分邏輯 ====================

NEGATIVE_TAGS = {"董監事或大股東申報轉讓 > 持股 5%", "營收下滑"}

def build_company_scores(scan_results):
    """
    從 scan_results 建立每間公司的各維度 tag 清單與計分。
    回傳 dict: code -> {
        name, code,
        rev_tags, fin_tags, chip_pos_tags, chip_neg_tags, ind_tags,
        rev_yoy, rev_mom, # 用於顯示
        ind_yoy_diff, # 個股YoY - 產業YoY
        all_pos_count, all_neg_count
    }
    """
    companies = {}
    results = scan_results.get("results", {})

    # 收集基本資訊
    all_codes = set()
    for cat in ["月營收異常", "重大訊息", "三大法人", "產業強度"]:
        for e in results.get(cat, []):
            code = str(e.get("code", "")).strip()
            if code:
                all_codes.add(code)
                if code not in companies:
                    companies[code] = {
                        "name": e.get("name", ""),
                        "code": code,
                        "rev_tags": [],
                        "fin_tags": [],
                        "chip_pos_tags": [],
                        "chip_neg_tags": [],
                        "ind_tags": [],
                        "rev_yoy": None,
                        "rev_mom": None,
                        "ind_yoy_diff": None,  # 個股YoY - 產業YoY
                    }
                elif e.get("name") and not companies[code]["name"]:
                    companies[code]["name"] = e.get("name", "")

    # 月營收
    flag_to_tag = {
        "雙增": "營收雙增(YoY/MoM>10%)",
        "上市新高": "營收創歷史新高",
        "近2年高": "營收創近兩年新高",
        "連3月遞增": "營收連續成長月數 > 3",
    "營收下滑": "營收下滑(YoY<-10%)",
    }
    for e in results.get("月營收異常", []):
        code = str(e.get("code", "")).strip()
        if code not in companies:
            continue
        for flag in e.get("flags", []):
            tag = flag_to_tag.get(flag)
            if tag:
                companies[code]["rev_tags"].append(tag)
        companies[code]["rev_yoy"] = e.get("yoy_pct")
        companies[code]["rev_mom"] = e.get("mom_pct")

    # 季財報（三率/毛利/EPS）→ fin_tags
    def _q_flag_to_fin(flag):
        if flag == "三率齊升": return "毛/營/淨利率三率齊升"
        if flag == "三率齊升(營收衰退-轉型信號)": return "三率齊升(轉型信號)"
        if flag.startswith("毛利跳升"): return flag
        if flag.startswith("EPS加速"): return flag
        if flag.startswith("業外偏高"): return flag  # 負面訊號，暫放 fin_tags
        return None

    for e in results.get("季財報", []):
        code = str(e.get("code", "")).strip()
        if not code:
            continue
        if code not in companies:
            companies[code] = {
                "name": e.get("name", ""), "code": code,
                "rev_tags": [], "fin_tags": [], "chip_pos_tags": [],
                "chip_neg_tags": [], "ind_tags": [], "rev_neg_tags": [],
                "rev_yoy": None, "rev_mom": None, "ind_yoy_diff": None,
            }
        for flag in e.get("flags", []):
            fin_tag = _q_flag_to_fin(flag)
            if fin_tag:
                companies[code]["fin_tags"].append(fin_tag)

    # 三大法人（目前只有內部人警示 = 負面）
    for e in results.get("三大法人", []):
        code = str(e.get("code", "")).strip()
        if code not in companies:
            continue
        if e.get("type") == "內部人警示":
            companies[code]["chip_neg_tags"].append("董監事或大股東申報轉讓 > 持股 5%")

    # 產業強度
    for e in results.get("產業強度", []):
        code = str(e.get("code", "")).strip()
        if code not in companies:
            continue
        detail = e.get("detail", "")
        yoy = e.get("yoy_pct")
        ind_yoy = e.get("industry_yoy")
        if yoy is not None and ind_yoy is not None:
            companies[code]["ind_yoy_diff"] = yoy - ind_yoy
        if "市佔率掠奪" in detail:
            companies[code]["ind_tags"].append("個股營收 YOY - 產業平均 > 10%")
        if "逆勢抗跌" in detail:
            companies[code]["ind_tags"].append("個股強於產業")

    # 計算正負面總數
    for code, d in companies.items():
        d["all_pos_count"] = (
            len(d["rev_tags"]) +
            len(d["fin_tags"]) +
            len(d["chip_pos_tags"]) +
            len(d["ind_tags"])
        )
        rev_neg_tags = [t for t in d["rev_tags"] if t in NEGATIVE_TAGS]
        d["rev_tags"] = [t for t in d["rev_tags"] if t not in NEGATIVE_TAGS]
        d["rev_neg_tags"] = rev_neg_tags
        d["all_neg_count"] = len(d["chip_neg_tags"]) + len(rev_neg_tags)

    return companies

# ==================== 格式化輸出 ====================

def fmt_yoy(val):
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"

def make_table_block(headers, rows):
    """產生 Notion table block（最多 100 列）"""
    table_rows = []
    # header row
    table_rows.append({
        "type": "table_row",
        "table_row": {
            "cells": [[{"type": "text", "text": {"content": h}}] for h in headers]
        }
    })
    for row in rows[:99]:  # header + 99 data rows = 100
        table_rows.append({
            "type": "table_row",
            "table_row": {
                "cells": [[{"type": "text", "text": {"content": str(c)}}] for c in row]
            }
        })
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": len(headers),
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows
        }
    }

def h2(text):
    return {
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}
    }

def para(text):
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}
    }

def divider():
    return {"object": "block", "type": "divider", "divider": {}}

# ==================== 六個區塊 ====================

def section_fin_top10(companies):
    """三率利多表現最好 Top 10（依 fin_tags 數量，目前資料源是財報季報）"""
    ranked = sorted(
        [(c, d) for c, d in companies.items() if d["fin_tags"]],
        key=lambda x: -len(x[1]["fin_tags"])
    )[:10]
    blocks = [h2("📊 三率利多 Top 10")]
    if not ranked:
        blocks.append(para("今日無三率利多命中個股。"))
        return blocks
    rows = []
    for i, (code, d) in enumerate(ranked, 1):
        tags_str = "、".join(d["fin_tags"])
        rows.append([f"{i}", f"{d['name']}/{code}", str(len(d["fin_tags"])), tags_str])
    blocks.append(make_table_block(["#", "股票", "標籤數", "命中標籤"], rows))
    return blocks

def section_rev_top10(companies):
    """營收相關表現最好 Top 10（依 rev_tags 數量，YoY 為第二排序）"""
    ranked = sorted(
        [(c, d) for c, d in companies.items() if d["rev_tags"]],
        key=lambda x: (-len(x[1]["rev_tags"]), -(x[1]["rev_yoy"] or 0))
    )[:10]
    blocks = [h2("💰 營收相關 Top 10")]
    if not ranked:
        blocks.append(para("今日無營收利多命中個股。"))
        return blocks
    rows = []
    for i, (code, d) in enumerate(ranked, 1):
        tags_str = "、".join(d["rev_tags"])
        rows.append([
            f"{i}",
            f"{d['name']}/{code}",
            fmt_yoy(d["rev_yoy"]),
            fmt_yoy(d["rev_mom"]),
            tags_str
        ])
    blocks.append(make_table_block(["#", "股票", "YoY", "MoM", "命中標籤"], rows))
    return blocks

def section_industry_top10(companies):
    """產業相對強弱 Top 10（依個股YoY - 產業YoY差距降序）"""
    ranked = sorted(
        [(c, d) for c, d in companies.items() if d["ind_tags"]],
        key=lambda x: -(x[1]["ind_yoy_diff"] or 0)
    )[:10]
    blocks = [h2("🏭 產業相對強弱 Top 10")]
    if not ranked:
        blocks.append(para("今日無產業強弱命中個股。"))
        return blocks
    rows = []
    for i, (code, d) in enumerate(ranked, 1):
        diff = d["ind_yoy_diff"]
        diff_str = f"+{diff:.1f}%" if diff and diff >= 0 else (f"{diff:.1f}%" if diff else "—")
        tags_str = "、".join(d["ind_tags"])
        rows.append([f"{i}", f"{d['name']}/{code}", diff_str, tags_str])
    blocks.append(make_table_block(["#", "股票", "超越產業(YoY差)", "命中標籤"], rows))
    return blocks

def section_chip_top10(companies):
    """籌碼面正面 Top 10（依正面籌碼 tag 數量）"""
    ranked = sorted(
        [(c, d) for c, d in companies.items() if d["chip_pos_tags"]],
        key=lambda x: -len(x[1]["chip_pos_tags"])
    )[:10]
    blocks = [h2("🧩 籌碼面利多 Top 10")]
    if not ranked:
        blocks.append(para("今日無正面籌碼面標籤命中個股。"))
        return blocks
    rows = []
    for i, (code, d) in enumerate(ranked, 1):
        tags_str = "、".join(d["chip_pos_tags"])
        rows.append([f"{i}", f"{d['name']}/{code}", str(len(d["chip_pos_tags"])), tags_str])
    blocks.append(make_table_block(["#", "股票", "標籤數", "命中標籤"], rows))
    return blocks

def section_pos_top15(companies):
    """最多正面標籤 Top 15（跨所有維度加總）"""
    ranked = sorted(
        [(c, d) for c, d in companies.items() if d["all_pos_count"] > 0],
        key=lambda x: -x[1]["all_pos_count"]
    )[:15]
    blocks = [h2("⭐ 正面標籤最多 Top 15")]
    if not ranked:
        blocks.append(para("今日無正面標籤命中個股。"))
        return blocks
    rows = []
    for i, (code, d) in enumerate(ranked, 1):
        breakdown = []
        if d["rev_tags"]: breakdown.append(f"營收×{len(d['rev_tags'])}")
        if d["fin_tags"]: breakdown.append(f"三率×{len(d['fin_tags'])}")
        if d["chip_pos_tags"]: breakdown.append(f"籌碼×{len(d['chip_pos_tags'])}")
        if d["ind_tags"]: breakdown.append(f"產業×{len(d['ind_tags'])}")
        rows.append([
            f"{i}",
            f"{d['name']}/{code}",
            str(d["all_pos_count"]),
            " ".join(breakdown)
        ])
    blocks.append(make_table_block(["#", "股票", "正面總數", "各維度明細"], rows))
    return blocks

def section_neg_top15(companies):
    """最多負面標籤 Top 15（目前主要是內部人警示）"""
    ranked = sorted(
        [(c, d) for c, d in companies.items() if d["all_neg_count"] > 0],
        key=lambda x: -x[1]["all_neg_count"]
    )[:15]
    blocks = [h2("⚠️ 負面訊號 Top 15")]
    if not ranked:
        blocks.append(para("今日無負面標籤命中個股。"))
        return blocks
    rows = []
    for i, (code, d) in enumerate(ranked, 1):
        all_neg_t = d.get("chip_neg_tags", []) + d.get("rev_neg_tags", [])
        tags_str = "、".join(all_neg_t)
        rows.append([f"{i}", f"{d['name']}/{code}", str(d["all_neg_count"]), tags_str])
    blocks.append(make_table_block(["#", "股票", "負面標籤數", "命中標籤"], rows))
    return blocks

# ==================== 子頁面建立 ====================

def find_existing_summary_page(date_str):
    """查詢是否已有同一天的摘要子頁面，有則回傳 page_id"""
    title = f"📋 掃描摘要 {date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    search_payload = {
        "query": title,
        "filter": {"value": "page", "property": "object"},
        "page_size": 5
    }
    try:
        result = notion_post("https://api.notion.com/v1/search", search_payload)
        for page in result.get("results", []):
            props = page.get("properties", {})
            title_prop = props.get("title", {}).get("title", [])
            page_title = "".join(t.get("plain_text", "") for t in title_prop)
            if page_title == title:
                return page["id"]
    except Exception as e:
        print(f" [!] 搜尋已有摘要頁面失敗: {e}")
    return None

def archive_page(page_id):
    """將頁面移到 trash"""
    try:
        notion_patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            {"archived": True}
        )
        print(f" [+] 舊摘要頁面已刪除: {page_id}")
    except Exception as e:
        print(f" [!] 刪除舊頁面失敗: {e}")

def append_blocks(page_id, blocks):
    """分批 append blocks（每批最多 100 個）"""
    for i in range(0, len(blocks), 100):
        chunk = blocks[i:i+100]
        notion_patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            {"children": chunk}
        )
        if len(blocks) > 100:
            time.sleep(0.5)


def query_topn_today(headers, db_id, date_str):
    """Query Top N DB for existing record with given date. Returns page_id or None."""
    import requests as _rq
    r = _rq.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=headers,
        json={"filter": {"property": "日期", "title": {"equals": date_str}}}
    )
    results = r.json().get("results", [])
    return results[0]["id"] if results else None

def create_summary_page(date_str, companies):
    date_iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    title = f"📋 掃描摘要 {date_iso}"
    now_str = now_tw().strftime("%Y-%m-%d %H:%M")

    # 建立頁面（先空白）
    page_payload = {
        "parent": {"type": "page_id", "page_id": SCAN_RESULTS_PAGE_ID},
        "properties": {
            "title": {"title": [{"text": {"content": title}}]}
        }
    }
    page = notion_post("https://api.notion.com/v1/pages", page_payload)
    page_id = page.get("id")
    print(f" [+] 摘要頁面建立: {page_id}")

    # 組裝所有 blocks
    blocks = []
    blocks.append(para(f"自動產生於 {now_str} (Asia/Taipei) | 掃描日期：{date_iso} | 總公司數：{len(companies)}"))
    blocks.append(divider())

    for section_fn in [
        section_fin_top10,
        section_rev_top10,
        section_industry_top10,
        section_chip_top10,
        section_pos_top15,
        section_neg_top15,
    ]:
        try:
            blocks.extend(section_fn(companies))
        except Exception as e:
            blocks.append(para(f"[ERROR] {section_fn.__name__} 失敗: {e}"))
        blocks.append(divider())

    # 分批寫入
    append_blocks(page_id, blocks)
    print(f" [+] 共寫入 {len(blocks)} 個 blocks")
    return page_id

# ==================== 主程式 ====================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="指定日期 YYYYMMDD，預設昨天")
    parser.add_argument("--test", action="store_true", help="測試模式：資料只取前 50 間公司")
    args = parser.parse_args()

    print("=" * 60)
    print(f"daily-scan-summary.py 執行中 {now_tw().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    date_str = args.date or next((f.stem.replace("scan_results_","") for f in sorted(Path("/home/ubuntu/.openclaw/workspace/state").glob("scan_results_*.json"), reverse=True)), (datetime.now() - timedelta(days=1)).strftime("%Y%m%d"))
    print(f"目標日期: {date_str}")

    scan_results = load_scan_results(date_str)
    if not scan_results:
        print(f"[ERROR] 找不到 scan_results_{date_str}.json，中止")
        return

    results = scan_results.get("results", {})
    for cat in ["月營收異常", "重大訊息", "三大法人", "產業強度"]:
        print(f"  {cat}: {len(results.get(cat, []))} 筆")

    companies = build_company_scores(scan_results)
    print(f"  總公司數: {len(companies)}")

    if args.test:
        companies = dict(list(companies.items())[:50])
        print(" *** 測試模式：只取前 50 間 ***")

    # 刪除同一天已有的舊摘要
    existing_id = find_existing_summary_page(date_str)
    if existing_id:
        print(f" [*] 發現已有摘要頁面，先刪除: {existing_id}")
        archive_page(existing_id)
        time.sleep(1)

    page_id = create_summary_page(date_str, companies)
    notion_url = f"https://notion.so/{page_id.replace('-', '')}"

    # ── 排序輔助（複用 section 函式內的邏輯）──
    # ── 排序輔助（複用 section 函式內的邏輯）──
    def ranked_list(key, n, neg=False):
        sign = 1 if neg else -1
        def _val(d):
            v = d.get(key, 0)
            return len(v) if isinstance(v, list) else (v or 0)
        items = [(c, d) for c, d in companies.items() if _val(d) > 0]
        items.sort(key=lambda x: sign * _val(x[1]))
        return items[:n]
    POS_TAG_FIELDS = [("rev_tags","💰"),("fin_tags","📊"),("chip_pos_tags","🧩"),("ind_tags","🏭")]
    NEG_TAG_FIELDS = [("chip_neg_tags","⚠️"), ("rev_neg_tags","📉")]

    def fmt_rows(items, key, neg=False):
        lines = []
        tag_fields = NEG_TAG_FIELDS if neg else POS_TAG_FIELDS
        for i, (code, d) in enumerate(items, 1):
            val = d.get(key, 0)
            cnt = len(val) if isinstance(val, list) else val
            header = f"{i}. {d.get('name', code)}/{code}（{cnt}）"
            if isinstance(val, list) and val:
                # 單一維度（fin_tags / rev_tags 等）：直接換行列標籤
                tag_lines = "\n ".join(val)
                lines.append(f"{header}\n {tag_lines}")
            else:
                # 綜合計數（all_pos_count / all_neg_count）：各維度換行
                breakdown = []
                for tk, emoji in tag_fields:
                    tv = d.get(tk, [])
                    if tv:
                        tag_lines = "\n ".join(tv)
                        breakdown.append(f" {emoji} {tag_lines}")
                if breakdown:
                    lines.append(header + "\n" + "\n".join(breakdown))
                else:
                    lines.append(header)
        return "\n".join(lines) if lines else "（無資料）"

    pos15 = ranked_list("all_pos_count", 15)
    neg15 = ranked_list("all_neg_count", 15, neg=True)
    fin10 = ranked_list("fin_tags", 10)
    rev10 = ranked_list("rev_tags", 10)
    chip10 = ranked_list("chip_pos_tags", 10)
    ind10 = ranked_list("ind_tags", 10)

    # ── Telegram 推播（各類 Top 5）──
    tg_msg = (
        f"📊 每日 Top N｜{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}\n\n"
        f"⭐ 綜合正面 Top5：\n{fmt_rows(pos15[:5], 'all_pos_count')}\n\n"
        f"⚠️ 綜合負面 Top5：\n{fmt_rows(neg15[:5], 'all_neg_count')}\n\n"
        f"📊 三率利多 Top5：\n{fmt_rows(fin10[:5], 'fin_tags')}\n\n"
        f"💰 營收亮眼 Top5：\n{fmt_rows(rev10[:5], 'rev_tags')}\n\n"
        f"🧩 籌碼利多 Top5：\n{fmt_rows(chip10[:5], 'chip_pos_tags')}\n\n"
        f"🏭 產業強弱 Top5：\n{fmt_rows(ind10[:5], 'ind_tags')}\n\n"
        f"📎 完整版：{notion_url}"
    )
    try:
        import requests as _req
        r = _req.post(
            f"https://api.telegram.org/bot{SECRETS['telegram_bot_token']}/sendMessage",
            json={"chat_id": SECRETS["telegram_dm"], "text": tg_msg},
            timeout=10
        )
        print(f"[Telegram] Top N 推播{'成功' if r.ok else '失敗: ' + r.text[:80]}")
    except Exception as e:
        print(f"[Telegram] 推播失敗（不影響主流程）: {e}")

    # ── 寫入每日 Top N DB ──
    def to_str(items, key, n):
        return ", ".join(
            f"{d.get('name', c)}/{c}"
            for _, (c, d) in enumerate(items[:n])
        ) or "（無資料）"

    try:
        import requests as _req2
        r2 = _req2.post(
            "https://api.notion.com/v1/pages",
            headers={
                "Authorization": f"Bearer {SECRETS['notion_key']}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
            json={
                "parent": {"database_id": "bea3f040-7da1-4b6b-8acc-63f0b8e4f453"},
                "properties": {
                    "日期": {"title": [{"text": {"content": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"}}]},
                    "綜合正面 Top15": {"rich_text": [{"text": {"content": to_str(pos15, "all_pos_count", 15)}}]},
                    "綜合負面 Top15": {"rich_text": [{"text": {"content": to_str(neg15, "all_neg_count", 15)}}]},
                    "三率利多 Top10": {"rich_text": [{"text": {"content": to_str(fin10, "fin_tags", 10)}}]},
                    "營收亮眼 Top10": {"rich_text": [{"text": {"content": to_str(rev10, "rev_tags", 10)}}]},
                    "產業強弱 Top10": {"rich_text": [{"text": {"content": to_str(ind10, "ind_tags", 10)}}]},
                    "籌碼利多 Top10": {"rich_text": [{"text": {"content": to_str(chip10, "chip_pos_tags", 10)}}]},
                    "Notion連結": {"url": notion_url},
                }
            },
            timeout=10
        )
        print(f"[Notion] 每日 Top N DB 寫入{'成功' if r2.ok else '失敗: ' + r2.text[:80]}")
    except Exception as e:
        print(f"[Notion] Top N DB 寫入失敗（不影響主流程）: {e}")

    print(f"\n[✓] 完成！")
    print(f"摘要頁面: {notion_url}")
    print("=" * 60)

if __name__ == "__main__":
    main()
