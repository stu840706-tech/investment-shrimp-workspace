#!/usr/bin/env python3
"""
poll_broker_bot.py - 用券商報告 bot token 輪詢群組，發現 PDF 自動處理
每 5 分鐘由 cron 觸發，用 state/broker_bot_offset.json 記錄進度防止重複處理
"""
import sys, json, urllib.request, urllib.parse, subprocess, time
from datetime import datetime
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
sys.path.insert(0, str(WORKSPACE / "workflows"))
from _common import SECRETS

BOT_TOKEN = SECRETS.get("telegram_broker_bot_token", "")
GROUP_ID = SECRETS.get("telegram_group", "-5290205228")
OFFSET_FILE = WORKSPACE / "state" / "broker_bot_offset.json"
RECEIVE_SCRIPT = WORKSPACE / "skills/broker-materials/scripts/receive_telegram.py"
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def load_offset():
    if OFFSET_FILE.exists():
        return json.load(open(OFFSET_FILE)).get("offset", 0)
    return 0

def save_offset(offset):
    json.dump({"offset": offset, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
        open(OFFSET_FILE, "w"), ensure_ascii=False)

def tg_get(method, params=None):
    url = f"{TG_API}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode())

def download_file(file_id, out_path):
    """下載 Telegram 檔案"""
    info = tg_get("getFile", {"file_id": file_id})
    file_path = info.get("result", {}).get("file_path", "")
    if not file_path:
        raise RuntimeError(f"無法取得 file_path: {info}")
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    with urllib.request.urlopen(url, timeout=60) as r:
        out_path.write_bytes(r.read())

def send_telegram(text):
    """回報給 Kai"""
    try:
        dm_id = SECRETS.get("telegram_dm", "")
        if not dm_id:
            return
        payload = json.dumps({"chat_id": dm_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{SECRETS['telegram_bot_token']}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[warn] Telegram 回報失敗: {e}")

def process_pdf(file_id, file_name, message_id):
    """下載 PDF 並呼叫 receive_telegram.py 處理"""
    tmp_path = Path(f"/tmp/broker_{message_id}_{file_name}")
    print(f"[poll_broker_bot] 下載: {file_name} ({file_id})")
    download_file(file_id, tmp_path)
    print(f"[poll_broker_bot] 處理: {tmp_path} ({tmp_path.stat().st_size/1024:.0f} KB)")

    result = subprocess.run(
        ["python3", str(RECEIVE_SCRIPT), "--test-file", str(tmp_path)],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode == 0:
        print(f"[poll_broker_bot] ✅ {file_name} 處理完成")
        print(result.stdout[-500:] if result.stdout else "")
    else:
        print(f"[poll_broker_bot] ❌ {file_name} 處理失敗")
        print(result.stderr[-300:] if result.stderr else "")
        send_telegram(f"⚠️ 券商報告處理失敗：{file_name}\n{result.stderr[-200:]}")

    # 清理暫存
    try:
        tmp_path.unlink()
    except Exception:
        pass

def main():
    if not BOT_TOKEN:
        print("ERROR: telegram_broker_bot_token 未設定")
        sys.exit(1)

    print(f"[poll_broker_bot] 開始輪詢 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    offset = load_offset()
    print(f"[poll_broker_bot] 從 offset={offset} 開始")

    # 抓最新 updates
    params = {
        "offset": offset,
        "limit": 100,
        "timeout": 0,
        "allowed_updates": '["message"]'
    }
    data = tg_get("getUpdates", params)
    updates = data.get("result", [])
    print(f"[poll_broker_bot] 收到 {len(updates)} 則更新")

    pdf_count = 0
    max_update_id = offset

    for update in updates:
        update_id = update.get("update_id", 0)
        max_update_id = max(max_update_id, update_id)

        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        # 只處理目標群組的訊息
        if chat_id != str(GROUP_ID):
            continue

        doc = msg.get("document", {})
        file_name = doc.get("file_name", "")
        mime_type = doc.get("mime_type", "")
        file_id = doc.get("file_id", "")

        # 只處理 PDF
        if not file_id:
            continue
        if mime_type != "application/pdf" and not file_name.lower().endswith(".pdf"):
            continue

        message_id = msg.get("message_id", update_id)
        print(f"[poll_broker_bot] 發現 PDF: {file_name} (message_id={message_id})")
        try:
            process_pdf(file_id, file_name, message_id)
            pdf_count += 1
        except Exception as e:
            print(f"[poll_broker_bot] 處理失敗: {e}")
            send_telegram(f"⚠️ 券商報告下載失敗：{file_name}\n{e}")

    # 更新 offset（下次從這裡繼續）
    if updates:
        save_offset(max_update_id + 1)
        print(f"[poll_broker_bot] offset 更新至 {max_update_id + 1}")

    print(f"[poll_broker_bot] 完成，處理 {pdf_count} 份 PDF")

if __name__ == "__main__":
    main()