#!/usr/bin/env python3
import sys, json, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path("skills/book-notes/scripts").parent.parent.parent.parent / 'workflows'))
from _common import MINIMAX_API_KEY

payload = {
    "model": "MiniMax-M2.7",
    "max_tokens": 100,
    "thinking": {"type": "disabled"},
    "messages": [{"role": "user", "content": "你好"}],
}
data = json.dumps(payload).encode()
req = urllib.request.Request(
    "https://api.minimax.io/anthropic/v1/messages",
    data=data,
    headers={
        "Content-Type": "application/json",
        "x-api-key": MINIMAX_API_KEY,
        "anthropic-version": "2023-06-01",
    }
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read().decode())
    text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
    print(f"OK: {text_blocks[0]['text'][:50] if text_blocks else resp}")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"HTTP {e.code}: {body}")
except Exception as e:
    print(f"ERROR: {e}")
