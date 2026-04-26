#!/usr/bin/env python3
"""write_notion.py - 將研究報告寫入 Notion research_pages DB"""
import sys, json, urllib.request, time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import SECRETS, NOTION_KEY

NOTION_VERSION = "2022-06-28"
RESEARCH_PAGES_DB = SECRETS["notion_research_pages_db"]
STOCK_TRACKING_DB = SECRETS["notion_stock_tracking_db"]

def notion_post(url, payload, method='POST'):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={
            'Authorization': f'Bearer {NOTION_KEY}',
            'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json'
        },
        method=method
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def to_rich_text(text, limit=1990):
    if not text:
        return []
    return [{"text": {"content": text[i:i+limit]}} for i in range(0, len(text), limit)]

def text_to_blocks(text):
    """將長文字切成 Notion paragraph blocks"""
    blocks = []
    for para in text.split('\n\n'):
        para = para.strip()
        if not para:
            continue
        if para.startswith('## ') or para.startswith('### '):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": to_rich_text(para.lstrip('#').strip())}
            })
        elif para.startswith('- ') or para.startswith('• '):
            for line in para.split('\n'):
                line = line.lstrip('-•').strip()
                if line:
                    blocks.append({
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {"rich_text": to_rich_text(line)}
                    })
        else:
            for i in range(0, len(para), 1990):
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": to_rich_text(para[i:i+1990])}
                })
    return blocks

def write_research_page(stock_id, report_data):
    """寫入 Notion research_pages"""
    report = report_data.get("report", {})
    today = datetime.now().strftime("%Y-%m-%d")
    title = report.get("title", f"{stock_id} 個股研究報告")

    page_payload = {
        "parent": {"database_id": RESEARCH_PAGES_DB},
        "properties": {
            "一句話摘要": {"title": to_rich_text(title[:100])},
            "股票代碼": {"rich_text": to_rich_text(stock_id)},
            "報告日期": {"date": {"start": today}},
            "版本": {"number": 1},
            "已作廢": {"checkbox": False},
            "投資結論": {
                "select": {
                    "name": (
                        "A值得現在買" if report.get("rating") == "買進"
                        else "B值得追蹤" if report.get("rating") == "中立"
                        else "C不值得研究"
                    )
                }
            },
        }
    }

    if report.get("target_price"):
        page_payload["properties"]["目標價_基準"] = {"number": report["target_price"]}

    page = notion_post("https://api.notion.com/v1/pages", page_payload)
    page_id = page.get("id")
    print(f"[write_notion] 頁面建立: {page_id}")
    time.sleep(0.5)

    sections = [
        ("一、個股簡介", report.get("section_1", "")),
        ("二、成長引擎", report.get("section_2", "")),
        ("三、基本面佐證", report.get("section_3", "")),
        ("四、技術與籌碼面分析", report.get("section_4", "")),
        ("五、估值與投資建議", report.get("section_5", "")),
        ("六、潛在風險及觀察項目", report.get("section_6", "")),
        ("七、資料來源", report.get("section_7", "")),
    ]

    all_blocks = []
    for heading, content in sections:
        all_blocks.append({
            "object": "block", "type": "heading_1",
            "heading_1": {"rich_text": to_rich_text(heading)}
        })
        all_blocks.extend(text_to_blocks(content))

    for i in range(0, len(all_blocks), 100):
        batch = all_blocks[i:i+100]
        notion_post(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            {"children": batch},
            method='PATCH'
        )
        time.sleep(0.3)

    print(f"[write_notion] 寫入 {len(all_blocks)} 個 blocks 完成")
    return page_id

def update_stock_tracking(stock_id, report):
    """更新 stock_tracking 的當前觀點"""
    query_payload = {
        "filter": {
            "property": "股票代碼",
            "title": {"equals": stock_id}
        },
        "page_size": 1
    }
    result = notion_post(
        f"https://api.notion.com/v1/databases/{STOCK_TRACKING_DB}/query",
        query_payload
    )
    rows = result.get("results", [])
    if not rows:
        print(f"[write_notion] stock_tracking 無 {stock_id} 紀錄，跳過更新")
        return

    row_id = rows[0]["id"]
    today = datetime.now().strftime("%Y-%m-%d")
    update_payload = {
        "properties": {
            "當前觀點更新": {
                "rich_text": to_rich_text(
                    f"[{today}] {report.get('rating','')} TP:{report.get('target_price','')} | "
                    f"報告草稿已自動產生"
                )
            }
        }
    }
    notion_post(f"https://api.notion.com/v1/pages/{row_id}", update_payload, method='PATCH')
    print(f"[write_notion] stock_tracking 更新完成")

def main(stock_id):
    print(f"[write_notion] 寫入 {stock_id} 研究報告到 Notion...")
    report_path = Path(__file__).parent.parent.parent.parent / "state" / f"research_{stock_id}_report.json"
    if not report_path.exists():
        print(f"[write_notion] 找不到 {report_path}，請先執行 generate_report.py")
        return None

    report_data = json.load(open(report_path))
    page_id = write_research_page(stock_id, report_data)
    update_stock_tracking(stock_id, report_data.get("report", {}))
    print(f"[write_notion] 完成: https://notion.so/{page_id.replace('-','')}")
    return page_id

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    main(stock_id)