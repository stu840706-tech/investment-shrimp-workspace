#!/usr/bin/env python3
"""collect_broker_files.py — 從 /tmp 已知券商目錄收集材料至 broker_queue/
使用白名單避免掃到書籍或其他非券商檔案。
"""
import sys, shutil
from pathlib import Path
from datetime import datetime, timedelta

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
TMP = Path("/tmp")
MAX_AGE_DAYS = 7

# 白名單：只掃 openclaw-gateway 已知的券商目錄
BROKER_DIRS = [
    "broker_pdfs",
    "morning_reports",
    "yf_reports",
    "yfong_pdfs",
    "ms_foundry",
    "ct_reports",
    "broker_queue_inbound",  # 預留未來可能的目錄
]

# 只收券商報告格式，排除 .md（書籍）
VALID_EXTS = {".pdf", ".PDF", ".docx", ".doc", ".zip", ".txt"}

def collect():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    collected = []

    for dir_name in BROKER_DIRS:
        d = TMP / dir_name
        if not d.exists():
            continue
        try:
            for f in sorted(d.rglob("*")):
                if not f.is_file():
                    continue
                if f.suffix not in VALID_EXTS:
                    continue
                if f.name.startswith((".", "._", "__")):
                    continue
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    continue
                dest = QUEUE_DIR / f.name
                if dest.exists():
                    dest = QUEUE_DIR / f"{f.stem}_{int(f.stat().st_mtime)}{f.suffix}"
                shutil.move(str(f), str(dest))
                collected.append(dest.name)
                print(f"  MOVED {f.name}")
        except (PermissionError, OSError) as e:
            print(f"  SKIP {d}: {e}")
            continue

    print(f"[INFO] 收集完成：{len(collected)} 份移入 broker_queue/")
    return len(collected)

if __name__ == "__main__":
    collect()