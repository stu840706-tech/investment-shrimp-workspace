#!/usr/bin/env python3
"""broker_status.py — 一眼看券商處理台帳現況。用法: python3 broker_status.py"""
import sys, fcntl
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
SCRIPTS = WORKSPACE / "skills/broker-materials/scripts"
LOCK_PATH = "/tmp/broker_batch.lock"
sys.path.insert(0, str(SCRIPTS))
import ledger


def batch_running():
    """嘗試非阻塞取得 batch 的鎖：取得到=沒人跑(立即釋放)；取不到=batch 正在跑。"""
    try:
        f = open(LOCK_PATH, "w")
    except Exception:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
        return False
    except BlockingIOError:
        f.close()
        return True
    except Exception:
        try:
            f.close()
        except Exception:
            pass
        return False


def main():
    ledger.init()
    st = ledger.stats()
    q = [f for f in QUEUE_DIR.iterdir() if f.is_file()] if QUEUE_DIR.exists() else []

    print("==== 券商處理台帳現況 ====")
    print("目前是否有處理在跑: " + ("⏳ 是，batch 執行中" if batch_running() else "○ 否，閒置"))
    print("台帳統計: 完成 {} / 失敗 {} / 處理中 {}".format(
        st.get("done", 0), st.get("failed", 0), st.get("processing", 0)))
    print("佇列待處理(實體檔): {} 份".format(len(q)))
    if st.get("processing", 0):
        print("⚠️ 有 {} 筆卡在『處理中』(可能上次中斷)，重跑會自動重試".format(st.get("processing")))
    if st.get("failed", 0):
        print("⚠️ 有 {} 筆失敗，重跑會自動只補失敗的(已完成的不重做)".format(st.get("failed")))

    print()
    print("---- 最近 15 筆紀錄 ----")
    rows = ledger.recent(15)
    if not rows:
        print("(台帳目前沒有任何紀錄)")
        return
    for name, status, started, finished, chars, attempts, error in rows:
        mark = {"done": "✅", "failed": "❌", "processing": "⏳"}.get(status, "・")
        line = mark + " " + status.ljust(10) + " " + (name or "")[:46]
        extra = []
        if chars is not None:
            extra.append(str(chars) + "字")
            if chars < 200:
                extra.append("⚠️字偏少")
        if attempts and attempts > 1:
            extra.append("試" + str(attempts) + "次")
        if error:
            extra.append("err:" + str(error)[:50])
        if extra:
            line += "  (" + ",".join(extra) + "))"
        print(line)


if __name__ == "__main__":
    main()
