#!/usr/bin/env python3
"""book_batch.py - 批次處理 books/input/ 下所有書籍"""
import sys, json, re, time, subprocess, urllib.request
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw/workspace"
INPUT_DIR = WORKSPACE / "books/input"
DONE_DIR = WORKSPACE / "books/processed"
FAIL_LOG = WORKSPACE / "books/failed.log"
BOOK_MAIN = WORKSPACE / "skills/book-notes/scripts/book_main.py"

SECRETS = json.load(open(WORKSPACE / "config/secrets.json"))
MINIMAX_TOKEN = SECRETS["minimax_token"]

SKIP_PATTERN = re.compile(r'^\d{4}\s+\d+\.md$|^\d+月\.md$')
JUNK_SUFFIXES = re.compile(r'\s*\(Z-Library\)\s*|\s*\(Z-lib\)\s*', re.IGNORECASE)

def parse_filename(fname):
    name = re.sub(r'^\d{4}', '', Path(fname).stem).strip()
    name = JUNK_SUFFIXES.sub('', name).strip()
    author = ""
    m = re.search(r'\(([^)]+)\)\s*$', name)
    if m:
        author = m.group(1).strip()
        name = name[:m.start()].strip()
    return name, author

def detect_category(text_sample, title, author):
    prompt = f"""以下是一本書的開頭內容，請判斷它屬於哪個投資相關類別。

書名：{title}
作者：{author if author else '未知'}

內容開頭：
{text_sample[:1500]}

請只回覆以下其中一個類別，不要其他文字：
價值投資 / 技術分析 / 總體經濟 / 行為財務 / 企業分析 / 投資心理 / 其他"""
    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 20,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json", "x-api-key": MINIMAX_TOKEN, "anthropic-version": "2023-06-01"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
    if not blocks:
        return "其他"
    cat = blocks[0].get("text", "").strip()
    valid = {"價值投資","技術分析","總體經濟","行為財務","企業分析","投資心理","其他"}
    return cat if cat in valid else "其他"

def main():
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    done_names = {f.name for f in DONE_DIR.glob("*.md")}
    files = sorted(INPUT_DIR.glob("*.md"))
    skip = [f for f in files if SKIP_PATTERN.match(f.name)]
    books = [f for f in files if not SKIP_PATTERN.match(f.name) and f.name not in done_names]

    print(f"books/input: {len(files)} 個檔案")
    print(f"  跳過月報: {len(skip)} 個")
    print(f"  已處理: {len(done_names)} 個")
    print(f"  待處理: {len(books)} 本\n")

    for i, fpath in enumerate(books):
        title, author = parse_filename(fpath.name)
        print(f"[{i+1}/{len(books)}] {fpath.name}")
        print(f"  書名: {title} | 作者: {author or '偵測中'}")
        text = fpath.read_text(encoding="utf-8", errors="replace")
        try:
            category = detect_category(text, title, author)
        except Exception as e:
            category = "其他"
            print(f"  類別偵測失敗: {e}")
        if not author:
            author = "未知"
        print(f"  類別: {category}")
        cmd = [sys.executable, str(BOOK_MAIN), title, author, category, str(fpath)]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(WORKSPACE))
        if result.returncode == 0:
            fpath.rename(DONE_DIR / fpath.name)
            print(f"  完成，移至 processed/\n")
        else:
            with open(FAIL_LOG, "a") as f:
                f.write(f"{fpath.name}\t{title}\t{result.returncode}\n")
            print(f"  失敗（exit {result.returncode}），記錄至 failed.log\n")
            print(result.stderr[-300:] if result.stderr else "")
        time.sleep(2)

    print("全部完成！")

if __name__ == "__main__":
    main()