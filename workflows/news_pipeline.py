#!/usr/bin/env python3
"""新聞三層管線 orchestrator: fetcher -> aggregator -> publisher"""
import subprocess, sys
from datetime import datetime

WS = str(__file__).replace("workflows/news_pipeline.py", "")

def run(cmd):
    print(f"\n>>> {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=WS)
    if r.returncode != 0:
        print(f"[ERROR] returncode={r.returncode}")
        sys.exit(r.returncode)

if __name__ == "__main__":
    h = datetime.utcnow().strftime("%H")
    print(f"=== 新聞管線啟動 UTC {h} ===")
    run(["python3", "workflows/news_fetcher.py", "all", h])
    run(["python3", "workflows/news_aggregator.py", h])
    run(["python3", "workflows/news_publisher.py", h])
    print("=== 管線完成 ===")