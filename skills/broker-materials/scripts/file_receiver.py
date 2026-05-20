#!/usr/bin/env python3
"""file_receiver.py — 獨立 Telegram 文件接收 daemon（F漏 v2）
設計：
 - timeout=0 短輪詢每 2 秒，不阻塞，比 OpenClaw long-poll 更快搶到 update
 - 靜默排隊：不對每份發通知，避免 Telegram 20msg/min 限流
 - 批次彙報：最後一份收到後靜候 120 秒，再發一則彙整通知給 Kai
 - 獨立 offset（state/file_receiver_offset.txt），與 OpenClaw 不互搶
 - 出錯不中斷主迴圈
"""
import json, time, requests
from datetime import datetime
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
OFFSET_FILE = WORKSPACE / "state" / "file_receiver_offset.txt"
LOG_FILE = WORKSPACE / "state" / "file_receiver.log"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"

VALID_EXTS = {".pdf", ".docx", ".doc", ".zip", ".txt", ".md"}
POLL_INTERVAL = 2
IDLE_THRESHOLD = 120

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))

def get_offset():
    try:
        return int(OFFSET_FILE.read_text().strip())
    except Exception:
        return 0

def save_offset(n):
    OFFSET_FILE.write_text(str(n))

def download_to_queue(token, file_id, filename):
    ext = Path(filename).suffix.lower()
    if ext not in VALID_EXTS:
        return None
    info = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id}, timeout=15
    )
    info.raise_for_status()
    fp = info.json()["result"]["file_path"]
    r = requests.get(f"https://api.telegram.org/file/bot{token}/{fp}",
                     timeout=60, stream=True)
    r.raise_for_status()
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    dest = QUEUE_DIR / filename
    if dest.exists():
        dest = QUEUE_DIR / f"{dest.stem}_{int(time.time())}{ext}"
    with open(dest, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return dest

def send_tg(token, chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=15
        )
    except Exception as e:
        log(f"send_tg 失敗: {e}")

def main():
    secrets = load_secrets()
    token = secrets["telegram_bot_token"]
    chat_id = secrets["telegram_dm"]
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    log("=== file_receiver 啟動 ===")
    offset = get_offset()
    log(f"起始 offset={offset}")

    session_files = []
    last_file_time = None

    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": offset, "timeout": 0,
                        "limit": 100, "allowed_updates": ["message"]},
                timeout=15
            )
            r.raise_for_status()
            updates = r.json().get("result", [])

            for upd in updates:
                offset = upd["update_id"] + 1
                doc = upd.get("message", {}).get("document")
                if not doc:
                    continue
                fname = doc.get("file_name", f"unknown_{doc['file_id']}.bin")
                try:
                    dest = download_to_queue(token, doc["file_id"], fname)
                    if dest:
                        log(f"QUEUED {dest.name} ({dest.stat().st_size // 1024}KB)")
                        session_files.append(dest.name)
                        last_file_time = time.time()
                    else:
                        log(f"SKIP {fname}")
                except Exception as e:
                    log(f"ERROR {fname}: {e}")

            if updates:
                save_offset(offset)

            if last_file_time and (time.time() - last_file_time >= IDLE_THRESHOLD):
                total = len([f for f in QUEUE_DIR.iterdir()
                             if f.is_file() and f.suffix.lower() in VALID_EXTS])
                send_tg(token, chat_id,
                    f"📥 自動收齊 {len(session_files)} 份券商材料\n"
                    f"佇列目前共 {total} 份\n"
                    f"說「處理券商報告」開始執行")
                log(f"批次彙報已發送 ({len(session_files)} 份)")
                session_files.clear()
                last_file_time = None

        except KeyboardInterrupt:
            log("file_receiver 正常停止")
            break
        except Exception as e:
            log(f"poll 錯誤: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()