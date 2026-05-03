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
SCAN_RESULTS_PAGE_ID = "34e226f5-a398-816e-93ca-c2f0d5a2456a"

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

NEGATIVE_TAGS = {"董監事或大股東申報轉讓 > 持股 5%"}

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
        d["all_neg_count"] = len(d["chip_neg_tags"])

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
        tags_str = "、".join(d["chip_neg_tags"])
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

    date_str = args.date or (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
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
    print(f"\n[✓] 完成！")
    print(f"摘要頁面: https://notion.so/{page_id.replace('-', '')}")
    print("=" * 60)

if __name__ == "__main__":
    main()
