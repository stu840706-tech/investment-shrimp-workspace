#!/usr/bin/env python3
"""book_main.py - 書籍概念萃取主流程"""
import sys, json, argparse, re
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import SECRETS, NOTION_KEY, MINIMAX_API_KEY as MINIMAX_TOKEN

NOTION_VERSION = "2022-06-28"

def notion_post(url, payload, method='POST'):
    import urllib.request
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

def read_book_txt(path):
    """讀取 txt 檔案，分段（每段 35K 字）"""
    text = Path(path).read_text(encoding='utf-8', errors='ignore')
    # 分段：每 35K 字一段
    chunks = []
    for i in range(0, len(text), 35000):
        chunks.append(text[i:i+35000])
    return chunks

def extract_concepts_from_chunk(chunk_text, book_title):
    """M2.7 萃取概念（thinking=off）"""
    import urllib.request

    prompt = f"""以下是一本書的節錄內容。請萃取其中重要的投資/商業概念，每個概念產出以下欄位的 JSON：

{{
 "concepts": [
 {{
 "概念名稱": "概念的中文名稱",
 "觀點說明": "這個概念的核心觀點（2-3句）",
 "舉例": "書中提到的實際案例或數字",
 "如何使用": "這個概念如何應用在投資分析中",
 "適用情境": "這個概念適用的分析場景",
 "重要度": "高/中/低"
 }},
 ...
 ]
}}

規則：
- 全程使用繁體中文
- 只萃取書中明確提到的概念，不要捏造
- 一個章節或段落至少萃取 2-3 個概念，最多 8 個
- 重要度：高 = 核心投資框架，中 = 實用工具，低 = 延伸知識
- 只輸出 JSON，不要其他文字

書本節錄：
{chunk_text[:35000]}
"""

    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 4000,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": "{"}],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MINIMAX_TOKEN}",
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())

    text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
    if not text_blocks:
        return []
    raw = "{" + text_blocks[0]["text"].strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        data = json.loads(raw.strip())
        return data.get("concepts", [])
    except Exception:
        return []

def write_book_notes_page(book_title, author, category, total_concepts):
    """在 book_notes DB 建立書籍主檔"""
    db_id = SECRETS["notion_book_notes_db"]
    payload = {
        "parent": {"database_id": db_id},
        "properties": {
            "書名": {"title": to_rich_text(book_title)},
            "作者": {"rich_text": to_rich_text(author)},
            "類別": {"select": {"name": category}},
            "萃取日期": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
            "概念數量": {"number": total_concepts},
            "已作廢": {"checkbox": False},
        }
    }
    page = notion_post("https://api.notion.com/v1/pages", payload)
    return page.get("id")

def write_concept_cards(concepts, book_page_id):
    """批次寫入概念卡到 book_concepts DB"""
    db_id = SECRETS["notion_book_concepts_db"]
    results = []
    for c in concepts:
        select_map = {"高": "高", "中": "中", "低": "低"}
        importance = select_map.get(c.get("重要度", "中"), "中")
        scenario = c.get("適用情境", "")
        # 適用情境轉成 multi_select 格式
        scenarios = []
        if scenario:
            for s in scenario.split(","):
                s = s.strip()
                if s:
                    scenarios.append({"name": s})

        payload = {
            "parent": {"database_id": db_id},
            "properties": {
                "概念名稱": {"title": to_rich_text(c.get("概念名稱", ""))},
                "觀點說明": {"rich_text": to_rich_text(c.get("觀點說明", ""))},
                "舉例": {"rich_text": to_rich_text(c.get("舉例", ""))},
                "如何使用": {"rich_text": to_rich_text(c.get("如何使用", ""))},
                "適用情境": {"multi_select": scenarios},
                "重要度": {"select": {"name": importance}},
                "所屬書籍": {"relation": [{"id": book_page_id}]},
            }
        }
        page = notion_post("https://api.notion.com/v1/pages", payload)
        results.append(page.get("id"))
    return results

def main():
    parser = argparse.ArgumentParser(description="書籍概念萃取")
    parser.add_argument("book_title", help="書名")
    parser.add_argument("author", help="作者")
    parser.add_argument("category", help="類別")
    parser.add_argument("txt_path", help="txt 檔案路徑")
    args = parser.parse_args()

    book_title = args.book_title
    author = args.author
    category = args.category
    txt_path = Path(args.txt_path)

    if not txt_path.exists():
        print(f"[book_main] 找不到檔案: {txt_path}")
        return

    print(f"\n{'='*60}")
    print(f"📚 書籍概念萃取：{book_title}")
    print(f"作者：{author} | 類別：{category}")
    print(f"{'='*60}\n")

    # Step 1: 讀取並分段書籍
    print("【1】讀取書籍...")
    chunks = read_book_txt(txt_path)
    print(f"    共 {len(chunks)} 段")

    all_concepts = []
    for i, chunk in enumerate(chunks):
        print(f"\n【2.{i+1}】萃取第 {i+1}/{len(chunks)} 段概念...")
        concepts = extract_concepts_from_chunk(chunk, book_title)
        print(f"    萃取 {len(concepts)} 個概念")
        all_concepts.extend(concepts)

    print(f"\n共萃取 {len(all_concepts)} 個概念")

    # Step 3: 寫入 Notion
    print("\n【3】寫入 Notion...")
    book_page_id = write_book_notes_page(book_title, author, category, len(all_concepts))
    print(f"    書籍主檔：{book_page_id}")

    concept_ids = write_concept_cards(all_concepts, book_page_id)
    print(f"    已寫入 {len(concept_ids)} 個概念卡")

    # Step 4: 預覽前5個概念
    print("\n【4】概念預覽（前5個）：")
    for c in all_concepts[:5]:
        print(f"  • {c.get('概念名稱')}（{c.get('重要度')}）")

    print(f"\n{'='*60}")
    print(f"✅ 完成：{book_title} | 共 {len(all_concepts)} 個概念")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()