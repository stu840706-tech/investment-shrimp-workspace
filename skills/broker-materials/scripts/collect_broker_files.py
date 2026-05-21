#!/usr/bin/env python3
"""collect_broker_files.py — 掃 /tmp 子目錄，將券商材料搬入 broker_queue/"""
import sys, shutil
from pathlib import Path
from datetime import datetime, timedelta

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
VALID_EXTS = {".pdf", ".docx", ".doc", ".zip", ".txt", ".md"}
TMP = Path("/tmp")
MAX_AGE_DAYS = 30

def collect():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    collected = []
    try:
        dirs_to_scan = [d for d in TMP.iterdir()
                        if d.is_dir() and not d.name.startswith(".")]
    except PermissionError:
        dirs_to_scan = []
    for d in dirs_to_scan:
        try:
            for f in sorted(d.rglob("*")):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in VALID_EXTS:
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
                print(f"  MOVED {f.name} → broker_queue/")
        except (PermissionError, OSError):
            continue
    print(f"[INFO] 收集完成：{len(collected)} 份移入 broker_queue/")
    return len(collected)

if __name__ == "__main__":
    collect()