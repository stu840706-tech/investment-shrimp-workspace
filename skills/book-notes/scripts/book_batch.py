#!/usr/bin/env python3
"""book_batch.py - 批次處理 books/input/ 下所有書籍（含 Telegram 通知）"""
import sys, json, re, time, subprocess, urllib.request, urllib.parse
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw/workspace"
INPUT_DIR = WORKSPACE / "books/input"
DONE_DIR = WORKSPACE / "books/processed"
FAIL_LOG = WORKSPACE / "books/failed.log"
BOOK_MAIN = WORKSPACE / "skills/book-notes/scripts/book_main.py"

SECRETS = json.load(open(WORKSPACE / "config/secrets.json"))
MINIMAX_TOKEN = SECRETS["minimax_api_key"]
TG_TOKEN = SECRETS["telegram_bot_token"]
TG_CHAT = SECRETS["telegram_dm"]

SKIP_PATTERN = re.compile(r'^\d{4}\s+\d+\.md$|^\d+月\.md$')
JUNK_SUFFIXES = re.compile(r'\s*\(Z-Library\)\s*|\s*\(Z-lib\)\s*', re.IGNORECASE)

def tg_send(msg):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f" [tg] 發送失敗: {e}")

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
        "max_tokens": 100,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}],
    }
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                "https://api.minimax.io/anthropic/v1/messages",
                json.dumps(payload).encode(),
                {"Content-Type": "application/json", "x-api-key": MINIMAX_TOKEN,
                 "anthropic-version": "2023-06-01"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = json.loads(r.read().decode())
                blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
                if blocks:
                    cat = blocks[0].get("text", "").strip()
                    valid = {"價值投資","技術分析","總體經濟","行為財務","企業分析","投資心理","其他"}
                    return cat if cat in valid else "其他"
        except Exception as e:
            print(f" [detect_category] 第{attempt+1}次失敗: {e}")
            if attempt < 2:
                time.sleep(10)
    return "其他"

def run_book(fpath, title, author, category):
    """執行單本書萃取，失敗自動重試一次"""
    cmd = [sys.executable, str(BOOK_MAIN), title, author, category, str(fpath)]
    for attempt in range(2):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=str(WORKSPACE), timeout=2400
            )
            if result.returncode == 0:
                return True, ""
            err = result.stderr[-200:] if result.stderr else "exit非0"
        except subprocess.TimeoutExpired:
            err = "TIMEOUT(40min)"
        except Exception as e:
            err = str(e)
        if attempt == 0:
            print(f" ⚠️ 第1次失敗({err[:80]})，等90秒重試...")
            time.sleep(90)
    return False, err

def main():
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    done_names = {f.name for f in DONE_DIR.glob("*.md")}
    files = sorted(INPUT_DIR.glob("*.md"))
    skip = [f for f in files if SKIP_PATTERN.match(f.name)]
    books = [f for f in files if not SKIP_PATTERN.match(f.name) and f.name not in done_names]

    total = len(books)
    print(f"books/input: {len(files)} 個檔案")
    print(f" 跳過月報: {len(skip)} 個")
    print(f" 已處理: {len(done_names)} 個")
    print(f" 待處理: {total} 本\n")

    tg_send(f"📚 書籍批次開始\n待處理：{total} 本")

    ok_list, fail_list = [], []

    for i, fpath in enumerate(books):
        title, author = parse_filename(fpath.name)
        print(f"[{i+1}/{total}] {fpath.name}")
        print(f" 書名: {title} | 作者: {author or '偵測中'}")

        text = fpath.read_text(encoding="utf-8", errors="replace")
        category = detect_category(text, title, author)
        if not author:
            author = "未知"
        print(f" 類別: {category}")

        success, err = run_book(fpath, title, author, category)
        if success:
            fpath.rename(DONE_DIR / fpath.name)
            ok_list.append(title)
            print(f" ✅ 完成\n")
        else:
            with open(FAIL_LOG, "a") as f:
                f.write(f"{fpath.name}\t{title}\t{err}\n")
            fail_list.append(title)
            print(f" ❌ 失敗: {err[:80]}\n")

        time.sleep(2)

    summary = f"📚 書籍批次完成\n✅ 成功：{len(ok_list)} 本\n❌ 失敗：{len(fail_list)} 本"
    if fail_list:
        summary += "\n\n失敗書目：\n" + "\n".join(f"・{t}" for t in fail_list)
    print(summary)
    tg_send(summary)

if __name__ == "__main__":
    main()
