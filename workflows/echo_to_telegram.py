#!/usr/bin/env python3
"""
echo_to_telegram — 將長內容以檔案附件發送到 Telegram
用法：
    python3 echo_to_telegram.py "內容" "標題.txt"
"""
import sys
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

TZ = timezone(timedelta(hours=8))
WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def today():
    return datetime.now(tz=TZ).strftime("%Y-%m-%d")


def send_document(filepath: str, filename: str, secrets: dict) -> bool:
    """用 multipart/form-data 發送檔案到 Telegram"""
    import urllib.request
    import json

    token = secrets["telegram_bot_token"]
    dm = secrets["telegram_dm"]

    url = f"https://api.telegram.org/bot{token}/sendDocument"

    boundary = "----FormBoundary7MA4YWxkTrZu0gW"
    with open(filepath, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{dm}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
            if resp.get("ok"):
                doc = resp["result"].get("document", {})
                print(f"OK: file_id={doc.get('file_id')}")
                return True
            else:
                print(f"FAIL: {resp}")
                return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def main():
    secrets = load_secrets()
    content = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    filename = sys.argv[2] if len(sys.argv) > 2 else "output.txt"

    # 寫入暫存檔
    with NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp_path = f.name

    print(f"暫存檔：{tmp_path}")
    ok = send_document(tmp_path, filename, secrets)
    if ok:
        print(f"已發送到 Telegram：{filename}")
    else:
        print("發送失敗")


if __name__ == "__main__":
    main()
