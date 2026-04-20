#!/usr/bin/env python3
"""memory_archive.py — L3 日誌自動歸檔 + MEMORY.md 字元數檢查

B3 上線。每日 23:30 cron 觸發。

功能:
  1. 掃 memory/YYYY-MM-DD.md,> 30 天前的搬到 memory/archive/
  2. MEMORY.md > 12000 字元時 stdout 印警告(提醒 Kai 手動蒸餾)
  3. (未來擴充)news-fingerprints.md 30 天 eviction

安全防護:
  - 搬檔前檢查目標位置已存在 → 不搬,印警告
  - --dry-run 只列印不動檔

用法:
  python3 workflows/memory_archive.py --dry-run   # 只列出要搬的
  python3 workflows/memory_archive.py             # 實際搬檔
"""
import argparse
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
ARCHIVE_DIR = MEMORY_DIR / "archive"
MEMORY_FILE = WORKSPACE / "MEMORY.md"

ARCHIVE_AGE_DAYS = 30
MEMORY_CHAR_WARN = 12000

DATE_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def find_l3_files_to_archive(today: date):
    """回傳 (filepath, file_date) tuple list,日期 > ARCHIVE_AGE_DAYS 天前的 YYYY-MM-DD.md"""
    if not MEMORY_DIR.exists():
        return []

    cutoff = today - timedelta(days=ARCHIVE_AGE_DAYS)
    candidates = []

    for f in MEMORY_DIR.iterdir():
        if not f.is_file():
            continue
        m = DATE_FILE_RE.match(f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            candidates.append((f, file_date))

    return sorted(candidates, key=lambda x: x[1])


def archive_file(src: Path, dry_run: bool):
    """搬一個檔到 ARCHIVE_DIR。若目標已存在,skip 並印警告。"""
    dst = ARCHIVE_DIR / src.name

    if dst.exists():
        print(f"  [SKIP] {src.name}: archive 目標已存在 ({dst}),跳過避免覆蓋")
        return False

    if dry_run:
        print(f"  [DRY-RUN] would move {src.name} → archive/")
        return True

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    print(f"  [MOVED] {src.name} → archive/")
    return True


def check_memory_size():
    """檢查 MEMORY.md 字元數,> 12000 印警告。回傳實際字元數(沒有 MEMORY.md 時回傳 0)。"""
    if not MEMORY_FILE.exists():
        print(f"[INFO] MEMORY.md 不存在於 {MEMORY_FILE},跳過字元數檢查")
        return 0

    chars = len(MEMORY_FILE.read_text(encoding="utf-8"))
    if chars > MEMORY_CHAR_WARN:
        print(
            f"[WARN] MEMORY.md 字元數 {chars} 超過 {MEMORY_CHAR_WARN},"
            f"建議手動蒸餾(詳見 memory/runbooks/memory-management.md)"
        )
    else:
        print(f"[OK] MEMORY.md 字元數 {chars} / {MEMORY_CHAR_WARN}")
    return chars


def main():
    parser = argparse.ArgumentParser(description="L3 日誌自動歸檔 + MEMORY.md 字元數檢查")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列印要搬的檔案,不實際搬動",
    )
    args = parser.parse_args()

    today = date.today()
    print(f"=== memory_archive.py ({today.isoformat()}) ===")
    if args.dry_run:
        print("模式: DRY-RUN(不動檔案)")
    else:
        print("模式: LIVE(實際搬檔)")

    # 1. 掃 L3 日誌
    print(f"\n[STEP 1] 掃 {MEMORY_DIR}/ 下 YYYY-MM-DD.md(> {ARCHIVE_AGE_DAYS} 天前)")
    candidates = find_l3_files_to_archive(today)

    if not candidates:
        print("  (無檔案需要歸檔)")
    else:
        print(f"  找到 {len(candidates)} 個候選檔:")
        moved = 0
        skipped = 0
        for src, file_date in candidates:
            age = (today - file_date).days
            print(f"  - {src.name} (age: {age} 天)")
            if archive_file(src, args.dry_run):
                moved += 1
            else:
                skipped += 1
        action = "would move" if args.dry_run else "moved"
        print(f"\n  結果:{action} {moved} 檔,skip {skipped} 檔(已存在於 archive)")

    # 2. MEMORY.md 字元數檢查
    print(f"\n[STEP 2] 檢查 MEMORY.md 字元數")
    check_memory_size()

    print(f"\n=== 完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
