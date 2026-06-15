#!/usr/bin/env python3
"""batch_process.py — 處理 state/broker_queue/ 中所有券商材料
v4: 在 v3(flock 鎖 + ledger 冪等 + 時間戳 log + 字數旗標)基礎上：
  - ZIP 自動拆解：遇 .zip 解壓，逐份內部文件「各自」走內容指紋冪等與處理
    （不再整包餵 M2.7，根治多份合併過長被截斷 + 重複入庫）。
  - 過濾 macOS 資源 fork（._* 與 __MACOSX/）這類雜訊，不送進處理。
  - 冪等鍵一律用「實體檔案內容 sha256」：同一份 PDF 不論來自哪個 ZIP、
    重傳或重跑幾次，都只處理一次。
引擎 receive_telegram.process_file / load_secrets 不變。
"""
import sys, fcntl, io, contextlib, re, zipfile, tempfile, shutil
from pathlib import Path
from datetime import datetime

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
LOG_DIR = WORKSPACE / "state" / "broker_logs"
LOCK_PATH = "/tmp/broker_batch.lock"
SCRIPTS = WORKSPACE / "skills/broker-materials/scripts"
sys.path.insert(0, str(SCRIPTS))
from receive_telegram import load_secrets, process_file
import ledger

# 進得了佇列的副檔名（.zip 會被解壓，其餘直接處理）
VALID_EXTS = {".pdf", ".docx", ".doc", ".zip", ".txt", ".md"}
# ZIP 內部會處理的文件副檔名（不遞迴處理 zip-in-zip）
INNER_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md"}


def _is_noise(p):
    """macOS 打包帶進來的資源 fork / 中繼資料，不是真實報告。"""
    return p.name.startswith("._") or "__MACOSX" in p.parts


def process_one(real_path, display_name, log, secrets):
    """處理單一實體檔案。回傳 (status, payload)：
    ('done', char_count|None) / ('skip', None) / ('fail', error_str)
    冪等與台帳都以檔案內容指紋為鍵。"""
    real_path = Path(real_path)
    try:
        h = ledger.file_hash(real_path)
    except Exception as e:
        return ("fail", display_name + ": hash失敗 " + str(e))

    if ledger.is_done(h):
        return ("skip", None)

    ledger.mark_processing(h, display_name, real_path.stat().st_size)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            process_file(real_path, secrets)
        out = buf.getvalue()
        for ln in out.splitlines():
            if ln.strip():
                log("    " + ln)
        m = re.search(r"(\d+)\s*字元", out)
        cc = int(m.group(1)) if m else None
        ledger.mark_done(h, cc)
        return ("done", cc)
    except Exception as e:
        ledger.mark_failed(h, str(e))
        return ("fail", display_name + ": " + str(e))


def main():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    lock_fp = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[LOCK] 已有另一個 batch 實例在執行，本次直接退出（避免 race）")
        return 0

    log_path = LOG_DIR / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    logf = open(log_path, "a", encoding="utf-8")

    def log(msg):
        line = datetime.now().strftime("%H:%M:%S ") + msg
        print(line)
        logf.write(line + "\n")
        logf.flush()

    ledger.init()
    # 清掉佇列裡的 macOS 資源 fork / 中繼雜訊（._* 與 __MACOSX/），不是真實報告
    purged = 0
    for f in list(QUEUE_DIR.iterdir()):
        if f.is_file() and _is_noise(f):
            try:
                f.unlink()
                purged += 1
            except OSError:
                pass
    queue = sorted(
        [f for f in QUEUE_DIR.iterdir()
         if f.is_file() and not _is_noise(f) and f.suffix.lower() in VALID_EXTS],
        key=lambda f: f.stat().st_mtime,
    )
    log("=== BATCH START === log=" + str(log_path))
    if purged:
        log("[CLEAN] 清掉 " + str(purged) + " 個 macOS 雜訊檔(._*/__MACOSX)")
    if not queue:
        log("[INFO] broker_queue 是空的，沒有檔案需要處理")
        log("=== BATCH END === success=0 failed=0 skipped=0 exit=0")
        logf.close()
        return 0

    log("[INFO] 佇列共 " + str(len(queue)) + " 份")
    secrets = load_secrets()
    success, skipped, fail = 0, 0, []

    def record(status, payload, label):
        nonlocal success, skipped
        if status == "done":
            success += 1
            cc = payload
            if cc is not None and cc < 200:
                log(label + " [DONE] \u26a0\ufe0f 轉出字數偏少(" + str(cc) + ")，建議人工檢視")
            else:
                log(label + " [DONE]" + (" (" + str(cc) + "字)" if cc is not None else ""))
        elif status == "skip":
            skipped += 1
            log(label + " [SKIP] 同內容已處理過（冪等）")
        else:
            fail.append(payload)
            log(label + " [ERROR] " + payload)

    for i, fp in enumerate(queue, 1):
        prefix = "[" + str(i) + "/" + str(len(queue)) + "] " + fp.name

        if fp.suffix.lower() == ".zip":
            log(prefix + " [ZIP] 解壓後逐份處理")
            tmp = Path(tempfile.mkdtemp(prefix="brokerzip_"))
            try:
                with zipfile.ZipFile(fp) as z:
                    z.extractall(tmp)
            except Exception as e:
                log(prefix + " [ZIP-ERROR] 解壓失敗 " + str(e))
                fail.append(fp.name + ": zip解壓失敗 " + str(e))
                shutil.rmtree(tmp, ignore_errors=True)
                continue

            inner = sorted(
                p for p in tmp.rglob("*")
                if p.is_file() and not _is_noise(p) and p.suffix.lower() in INNER_EXTS
            )
            if not inner:
                log(prefix + " [ZIP] 內部沒有有效文件（可能全是雜訊/資源檔）")
            else:
                log(prefix + " [ZIP] 內含 " + str(len(inner)) + " 份有效文件")
            zip_had_fail = False
            for j, ip in enumerate(inner, 1):
                label = prefix + "  └(" + str(j) + "/" + str(len(inner)) + ") " + ip.name
                status, payload = process_one(ip, fp.name + "::" + ip.name, log, secrets)
                record(status, payload, label)
                if status == "fail":
                    zip_had_fail = True

            shutil.rmtree(tmp, ignore_errors=True)
            # 內部全成功/重複才刪 ZIP；有失敗則保留供重試（成功份下次冪等自動跳過）
            if not zip_had_fail:
                try:
                    fp.unlink()
                except FileNotFoundError:
                    pass
            else:
                log(prefix + " [ZIP] 內有處理失敗，保留此 ZIP 供重試")

        else:
            status, payload = process_one(fp, fp.name, log, secrets)
            record(status, payload, prefix)
            # 成功或重複都刪檔；失敗保留供重試
            if status in ("done", "skip"):
                try:
                    fp.unlink()
                except FileNotFoundError:
                    pass

    log("")
    log("完成：成功 " + str(success) + " / 失敗 " + str(len(fail)) + " / 跳過(重複) " + str(skipped))
    for f in fail:
        log(" - " + f)
    exit_code = 0 if not fail else 1
    log("=== BATCH END === success=" + str(success) + " failed=" + str(len(fail)) + " skipped=" + str(skipped) + " exit=" + str(exit_code))
    logf.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
