#!/usr/bin/env python3
"""batch_process.py — 處理 state/broker_queue/ 中所有券商材料（F漏 修復用）"""
import sys
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
sys.path.insert(0, str(WORKSPACE / "skills/broker-materials/scripts"))
from receive_telegram import load_secrets, process_file

VALID_EXTS = {".pdf", ".docx", ".doc", ".zip", ".txt", ".md"}

def main():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    queue = sorted(
        [f for f in QUEUE_DIR.iterdir() if f.is_file() and f.suffix.lower() in VALID_EXTS],
        key=lambda f: f.stat().st_mtime,
    )
    if not queue:
        print("[INFO] broker_queue 是空的，沒有檔案需要處理")
        return 0
    print(f"[INFO] 共 {len(queue)} 份待處理")
    secrets = load_secrets()
    success, fail = 0, []
    for i, fp in enumerate(queue, 1):
        print(f"\n[{i}/{len(queue)}] {fp.name}")
        try:
            process_file(fp, secrets)
            fp.unlink()
            success += 1
        except Exception as e:
            print(f" [ERROR] {e}")
            fail.append(f"{fp.name}: {e}")
    print(f"\n完成：成功 {success} 份 / 失敗 {len(fail)} 份")
    for f in fail:
        print(f" - {f}")
    return 0 if not fail else 1

if __name__ == "__main__":
    sys.exit(main())