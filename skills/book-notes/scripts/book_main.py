#!/usr/bin/env python3
"""book_main.py - 書籍概念萃取主流程"""
import sys, json, time, urllib.request, argparse, re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import SECRETS, NOTION_KEY, MINIMAX_API_KEY as MINIMAX_TOKEN

NOTION_VERSION = "2022-06-28"
BOOK_NOTES_DB = SECRETS["notion_book_notes_db"]
BOOK_CONCEPTS_DB = SECRETS["notion_book_concepts_db"]

#  Carole: 30000 chars per chunk（約 7-8K tokens），M2.7 可正常處理
CHUNK_SIZE = 30000
#  臨界檔案大小（超過此值才會切片）
SIZE_SKIP_SPLIT = 200000  # 20萬字元（約 50K tokens）以上才啟用章節/段落切片
SIZE_WARN = 500000        # 50萬字元以上警告（可能需數十分鐘）

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

def split_text(text, chunk_size=CHUNK_SIZE):
    MIN_CHUNK = 2000
    chapter_pattern = re.compile(
        r'(第[一二三四五六七八九十百\d]+[章節篇部][^\n]*\n|Chapter\s+\d+[^\n]*\n|CHAPTER\s+\d+[^\n]*\n)',
        re.IGNORECASE
    )
    tokens = chapter_pattern.split(text)
    parts = []
    i = 0
    if tokens and not chapter_pattern.match(tokens[0]):
        if tokens[0].strip():
            parts.append(tokens[0])
        i = 1
    while i < len(tokens):
        chunk = tokens[i]
        if i + 1 < len(tokens):
            chunk += tokens[i + 1]
            i += 2
        else:
            i += 1
        if chunk.strip():
            parts.append(chunk)
    if len(parts) < 2:
        parts = [text[j:j+chunk_size] for j in range(0, len(text), chunk_size)]
    result = []
    buffer = ""
    for part in parts:
        buffer += part
        if len(buffer) >= MIN_CHUNK:
            if len(buffer) > chunk_size:
                for j in range(0, len(buffer), chunk_size):
                    sub = buffer[j:j+chunk_size]
                    if sub.strip():
                        result.append(sub)
            else:
                result.append(buffer)
            buffer = ""
    if buffer.strip():
        if result and len(result[-1]) + len(buffer) <= chunk_size:
            result[-1] = result[-1] + buffer
        else:
            result.append(buffer)
    return result
def extract_concepts(chunk, book_title, chunk_idx, total_chunks, dry_run=False):
    if dry_run:
        print(f"  [DRY-RUN] 跳過 LLM 萃取（模擬 3 個概念）")
        return [
            {"概念名稱": "測試概念1", "觀點說明": "這是乾測試模式", "舉例": "無", "如何使用": "無", "適用情境": ["策略"], "重要度": "參考"},
            {"概念名稱": "測試概念2", "觀點說明": "只驗證文字處理流程", "舉例": "無", "如何使用": "無", "適用情境": ["心理"], "重要度": "重要"},
            {"概念名稱": "測試概念3", "觀點說明": "不做真實 API 呼叫", "舉例": "無", "如何使用": "無", "適用情境": ["選股"], "重要度": "核心"},
        ]
    prompt = f"""你是一位書籍概念萃取專家。以下是《{book_title}》第 {chunk_idx+1}/{total_chunks} 段的內容。

請萃取這段文字中作者想傳達的每一個重要概念，以 JSON array 格式輸出：

[
  {{
    "概念名稱": "簡短的概念標題（10字以內）",
    "觀點說明": "作者在說什麼，2-4句話說清楚核心觀點",
    "舉例": "書中給出的例子或案例（若無則填「書中未提供例子」）",
    "如何使用": "投資實務上如何應用這個概念，要具體",
    "適用情境": ["選股", "估值", "風險管理", "心理", "總經", "策略"],
    "重要度": "核心/重要/參考"
  }}
]

注意事項：
- 全程使用繁體中文，嚴禁輸出簡體中文
- 只萃取真正重要的概念（每段 3-8 個概念）
- 適用情境只能從以下選擇：選股/估值/風險管理/心理/總經/策略
- 重要度：核心（改變思維框架）/ 重要（實用工具）/ 參考（背景知識）
- 不得捏造書中沒有的內容
- 只輸出 JSON array，不要其他文字

書籍內容：
{chunk}
"""
    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 3000,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}, {"role": "assistant", "content": "["}],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": MINIMAX_TOKEN,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())

        text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
    if not text_blocks:
        print(f" [warn] M2.7 無 text block，content: {resp.get('content')}")
        return []
    raw_text = text_blocks[0].get("text", "")
    if not raw_text or not raw_text.strip():
        print(f" [warn] M2.7 text block 為空")
        return []
    raw = "[" + raw_text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def create_book_notes_page(title, author, category):
    query = notion_post(
        f"https://api.notion.com/v1/databases/{BOOK_NOTES_DB}/query",
        {"filter": {"property": "書名", "title": {"equals": title}}, "page_size": 1}
    )
    if query.get("results"):
        page_id = query["results"][0]["id"]
        print(f"[book_main] 書籍已存在: {page_id}")
        return page_id

    payload = {
        "parent": {"database_id": BOOK_NOTES_DB},
        "properties": {
            "書名": {"title": to_rich_text(title)},
            "作者": {"rich_text": to_rich_text(author)},
            "類別": {"multi_select": [{"name": category}]},
            "閱讀狀態": {"select": {"name": "已讀"}},
            "閱讀完成日": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
        }
    }
    page = notion_post("https://api.notion.com/v1/pages", payload)
    page_id = page.get("id")
    print(f"[book_main] 書籍主檔建立: {page_id}")
    return page_id

def write_concept_card(concept, book_page_id):
    valid_situations = {"選股", "估值", "風險管理", "心理", "總經", "策略"}
    situations = [{"name": s} for s in concept.get("適用情境", []) if s in valid_situations]
    valid_levels = {"核心", "重要", "參考"}
    level = concept.get("重要度", "參考")
    if level not in valid_levels:
        level = "參考"

    payload = {
        "parent": {"database_id": BOOK_CONCEPTS_DB},
        "properties": {
            "概念名稱": {"title": to_rich_text(concept.get("概念名稱", "未命名")[:100])},
            "觀點說明": {"rich_text": to_rich_text(concept.get("觀點說明", ""))},
            "舉例": {"rich_text": to_rich_text(concept.get("舉例", ""))},
            "如何使用": {"rich_text": to_rich_text(concept.get("如何使用", ""))},
            "適用情境": {"multi_select": situations},
            "重要度": {"select": {"name": level}},
            "所屬書籍": {"relation": [{"id": book_page_id}]},
        }
    }
    result = notion_post("https://api.notion.com/v1/pages", payload)
    return result.get("id")

def main():
    parser = argparse.ArgumentParser(description="書籍概念萃取")
    parser.add_argument("title", help="書名")
    parser.add_argument("author", help="作者")
    parser.add_argument("category", help="類別")
    parser.add_argument("file_path", help="書籍檔案路徑（支援 .txt / .md）")
    parser.add_argument("--dry-run", action="store_true", help="只萃取不寫入 Notion")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"📚 書籍概念萃取：{args.title}")
    print(f"作者：{args.author} | 類別：{args.category}")
    print(f"{'='*60}\n")

    file_path = Path(args.file_path)
    if not file_path.exists():
        print(f"ERROR: 找不到檔案 {file_path}")
        sys.exit(1)

    # 支援 .txt 和 .md
    suffix = file_path.suffix.lower()
    if suffix not in (".txt", ".md"):
        print(f"ERROR: 不支援的檔案格式 '{suffix}'，僅支援 .txt 和 .md")
        sys.exit(1)

    text = file_path.read_text(encoding="utf-8", errors="replace")
    size = len(text)
    print(f"[book_main] 讀取完成，共 {size:,} 字元（{size/1000:.0f}K）")

    # 警告超大檔案（預估處理時間）
    if size > SIZE_WARN:
        print(f"[WARN] 檔案超大（{size/1000:.0f}K 字元），預估處理時間 {size//CHUNK_SIZE * 2:.0f}+ 分鐘")

    chunks = split_text(text)
    print(f"[book_main] 分成 {len(chunks)} 段處理")

    if not args.dry_run:
        book_page_id = create_book_notes_page(args.title, args.author, args.category)
    else:
        book_page_id = "dry-run"

    def extract_one(i, chunk):
        print(f"\n[book_main] 處理第 {i+1}/{len(chunks)} 段（{len(chunk):,} 字元）...")
        for attempt in range(3):
            try:
                concepts = extract_concepts(chunk, args.title, i, len(chunks), dry_run=args.dry_run)
                if concepts is None:
                    concepts = []
                return i, concepts
            except Exception as e:
                print(f"  RETRY {attempt+1}/3 第 {i+1} 段: {e}")
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
        return i, []

    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(extract_one, i, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            i, concepts = future.result()
            results[i] = concepts
            print(f" 段 {i+1} 完成：{len(concepts)} 個概念")

    all_concepts = []
    for i in range(len(chunks)):
        concepts = results.get(i, [])
        print(f" 萃取到 {len(concepts)} 個概念")
        for c in concepts:
            print(f" - [{c.get('重要度','?')}] {c.get('概念名稱','?')}")
        all_concepts.extend(concepts)
        if not args.dry_run:
            for c in concepts:
                write_concept_card(c, book_page_id)
                time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"✅ 完成！共萃取 {len(all_concepts)} 個概念卡")
    if not args.dry_run:
        print(f"已寫入 Notion book_concepts DB")
    print(f"{'='*60}\n")

    print("概念預覽（前5個）：")
    for c in all_concepts[:5]:
        print(f" [{c.get('重要度')}] {c.get('概念名稱')} — {c.get('觀點說明','')[:50]}")

if __name__ == "__main__":
    main()