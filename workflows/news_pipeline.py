#!/usr/bin/env python3
"""新聞三層管線 orchestrator: fetcher -> aggregator -> publisher"""
import subprocess, sys
from datetime import datetime, timezone, timedelta

WS = str(__file__).replace("workflows/news_pipeline.py", "")

def run(cmd):
    print(f"\n>>> {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=WS)
    if r.returncode != 0:
        print(f"[ERROR] returncode={r.returncode}")
        sys.exit(r.returncode)

def utc_to_taipei_hour(utc_hour):
    """Convert UTC hour to Taipei run hour for file naming.

    Cron at UTC 23 = Taipei 07:00 morning run.
    Fetcher uses datetime.now().strftime('%Y%m%d') which is UTC date + UTC hour → 20260430-23.
    But the files we want to aggregate are named with UTC date + run hour (07/19).

    Fetcher writes:
    - Morning cron (UTC 23): raw-*-20260430-23 (uses UTC date)
    - But we want morning files to be raw-*-YYYYMM01-07 (Taipei morning run)

    Looking at actual files:
    - raw-tw-20260430-07.jsonl = April 30 UTC, 07:00 Taipei = correct morning run
    - raw-tw-20260430-23.jsonl = April 30 UTC, 23:00 Taipei = actually wrong cron

    The cron at UTC 23 wrote files with hour=23 but we actually need hour=07.
    Files with hour 07 are from... a different mechanism.

    Actually looking at history:
    - 20260430-23 = UTC cron run at April 30 23:00 UTC
    - 20260430-07 = April 30 07:00 Taipei = someone else

    The FIX we need: make the UTC cron write hour=07 files (correct morning run hour).
    Pipeline passes hour=07 to fetcher → fetcher uses datetime.now() = 20260501 + hour 07 → 20260501-07
    But datetime.now() is UTC so at UTC 23:59 it gives 20260430 not 20260501.

    CONFUSION: Let me just check what date-time.now() gives at UTC 23:
    - datetime.now() → UTC date
    - So at UTC 23, datetime.now().strftime('%Y%m%d') = 20260430

    For morning run files, we want: date of the morning run (Taipei morning) + hour 07.
    At UTC 23 (April 30), Taipei is May 1 07:00. Files should be named 20260501-07.
    But fetcher uses datetime.now() (UTC) → 20260430 → 20260430-07.

    So the cleanest fix: pipeline should pass UTC date + run hour to fetcher,
    and pass the SAME UTC date + run hour to aggregator.

    Solution: pipeline computes UTC date, converts hour, passes BOTH to aggregator.
   Aggregator uses passed date (not today_tw_str).

    Actually simplest: just add date override to aggregator.
    """
    h = int(utc_hour)
    if h == 23:
        return '07'   # UTC 23 = Taipei morning run → hour 07
    elif h == 11:
        return '19'   # UTC 11 = Taipei evening run → hour 19
    return utc_hour.zfill(2)

if __name__ == "__main__":
    utc_h = datetime.utcnow().strftime("%H")
    taipei_h = utc_to_taipei_hour(utc_h)

    # Fetcher uses datetime.now() (UTC) → UTC date + hour
    # Aggregator must match: use UTC date + taipei_h
    utc_date = datetime.now().strftime("%Y%m%d")

    print(f"=== 新聞管線 UTC {utc_h} → Taipei run hour {taipei_h} (UTC date: {utc_date}) ===")

    # Fetcher: UTC date + UTC hour
    period = "AM" if taipei_h == "07" else "PM"
    print(f"=== 新聞管線 UTC {utc_h} → Taipei {period}（{taipei_h}） UTC date: {utc_date} ===")

    # 三層統一用 taipei_h，避免 UTC hour 和 raw filename hour 混用
    # 防重複：同一 hour 已跑過 Layer 1 則跳過
    from pathlib import Path
    state_dir = Path.home() / ".openclaw" / "workspace" / "state"
    lock_file = state_dir / f"news_pipeline_ran_{taipei_h}_{utc_date}.lock"
    if lock_file.exists():
        print(f"[SKIP] 今日 {taipei_h} 時已執行過 pipeline，跳過")
        return
    lock_file.touch()

    run(["python3", "workflows/news_fetcher.py", "all", taipei_h])

    # Aggregator: UTC date + taipei_h (to match fetcher's file naming)
    run(["python3", "workflows/news_aggregator.py", taipei_h, utc_date])

    # Publisher: same
    run(["python3", "workflows/news_publisher.py", taipei_h, utc_date, period])

    print("=== 管線完成 ===")