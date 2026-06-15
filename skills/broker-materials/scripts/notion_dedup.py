#!/usr/bin/env python3
"""notion_dedup.py — 清理券商報告 DB 在指定日期的重複 record。

重複定義：同一個 DB 內、標題完全相同、且建立日期同一天 → 視為重複，
保留建立時間最早的一筆，其餘封存(archive)。

用法：
  python3 notion_dedup.py                  # dry-run，預設清 2026-06-08，只印不刪
  python3 notion_dedup.py --since=2026-06-08
  python3 notion_dedup.py --commit         # 真的封存重複

注意：標題相異但疑似同源（例：「兆豐國際」vs「兆丰證券」這種 broker_name
萃取不一致造成的重複）不會被自動判定，會在每組末尾列出「同日同DB全部標題」
供人工檢視。封存是可復原的（Notion 垃圾桶 30 天內可還原）。
"""
import sys
import requests
from collections import defaultdict
from pathlib import Path

SCRIPTS = Path.home() / ".openclaw" / "workspace" / "skills/broker-materials/scripts"
sys.path.insert(0, str(SCRIPTS))
from receive_telegram import load_secrets

DBS = {
    "券商個股報告": "34e226f5-a398-81a0-b22d-fea135a192fd",
    "券商產業報告": "34e226f5-a398-81fa-8c7e-ea8d8b32de67",
}
NV = "2022-06-28"


def title_of(page):
    for p in page.get("properties", {}).values():
        if p.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in p["title"]).strip()
    return "(無標題)"


def query_db(db_id, key, since):
    url = "https://api.notion.com/v1/databases/" + db_id + "/query"
    h = {"Authorization": "Bearer " + key, "Notion-Version": NV,
         "Content-Type": "application/json"}
    out = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(url, headers=h, json=body, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += d["results"]
        if not d.get("has_more"):
            break
        cursor = d["next_cursor"]
    return [p for p in out if p.get("created_time", "")[:10] >= since]


def archive(page_id, key):
    url = "https://api.notion.com/v1/pages/" + page_id
    h = {"Authorization": "Bearer " + key, "Notion-Version": NV,
         "Content-Type": "application/json"}
    r = requests.patch(url, headers=h, json={"archived": True}, timeout=30)
    r.raise_for_status()


def main():
    commit = "--commit" in sys.argv
    since = "2026-06-08"
    for a in sys.argv[1:]:
        if a.startswith("--since="):
            since = a.split("=", 1)[1]

    sec = load_secrets()
    key = sec["notion_key"]
    total_arch = 0
    suspect = []  # 同日同DB、標題相異但需人工檢視的

    for name, db in DBS.items():
        pages = query_db(db, key, since)
        groups = defaultdict(list)
        for p in pages:
            groups[(title_of(p), p.get("created_time", "")[:10])].append(p)
        print("== " + name + ": " + str(len(pages)) + " 筆 (>= " + since + ") ==")

        # 自動處理：完全同標題同日的重複
        for (title, day), ps in sorted(groups.items()):
            if len(ps) > 1:
                ps.sort(key=lambda x: x.get("created_time", ""))
                keep, dups = ps[0], ps[1:]
                print("  重複「" + title + "」" + day + "：共 " + str(len(ps))
                      + " 筆，保留最早(" + keep["created_time"][11:19]
                      + ")，archive " + str(len(dups)) + " 筆")
                for d in dups:
                    print("      - " + d["created_time"][11:19] + "  " + d["id"])
                    if commit:
                        archive(d["id"], key)
                        total_arch += 1

        # 人工檢視清單：同日所有標題（看有沒有同券商不同寫法的漏網）
        by_day = defaultdict(list)
        for (title, day), ps in groups.items():
            by_day[day].append((title, len(ps)))
        for day, titles in sorted(by_day.items()):
            suspect.append((name, day, sorted(titles)))

    print("\n===== 需人工檢視（同日全部標題，抓同券商不同寫法的漏網重複）=====")
    for name, day, titles in suspect:
        print("[" + name + " " + day + "]")
        for t, c in titles:
            print("    " + str(c) + "x  " + t)

    if commit:
        print("\n已封存重複筆數合計：" + str(total_arch) + "（Notion 垃圾桶 30 天內可還原）")
    else:
        print("\nDRY-RUN：以上「archive N 筆」均未執行。確認無誤後加 --commit 真正封存。")


if __name__ == "__main__":
    sys.exit(main())
