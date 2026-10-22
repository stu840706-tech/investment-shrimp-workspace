#!/usr/bin/env python3
"""batch_process.py — 處理 state/broker_queue/ 中所有券商材料
v6 (2026-10-22): ZIP 安全解壓（序號＋截短原名落地、cp950 檔名還原、解壓失敗計入 fail）；
  PDF 超過 HOLD_MB 移到 state/broker_queue_hold/ 不硬跑 OCR；
  結束／LOCK／崩潰一律 Telegram 回報（可 setsid nohup 背景執行，不靠 agent 回報）。
v5: 加入 process_file timeout wrapper（子進程，實際 600 秒），防止 pdfplumber hang 造成整批卡死。
v4: flock 鎖 + ledger 冪等 + 時間戳 log + 字數旗標。
  - ZIP 自動拆解：遇 .zip 解壓，逐份內部文件「各自」走內容指紋冪等與處理。
  - 過濾 macOS 資源 fork（._* 與 __MACOSX/）這類雜訊，不送進處理。
  - 冪等鍵一律用「實體檔案內容 sha256」。
"""
import sys, fcntl, re, zipfile, tempfile, shutil, html
from pathlib import Path
from datetime import datetime

WORKSPACE = Path.home() / ".openclaw" / "workspace"
QUEUE_DIR = WORKSPACE / "state" / "broker_queue"
HOLD_DIR = WORKSPACE / "state" / "broker_queue_hold"
LOG_DIR = WORKSPACE / "state" / "broker_logs"
LOCK_PATH = "/tmp/broker_batch.lock"
SCRIPTS = WORKSPACE / "skills/broker-materials/scripts"
sys.path.insert(0, str(SCRIPTS))
from receive_telegram import load_secrets, notify_telegram
from _process_file_timeout import run_with_timeout
import ledger

VALID_EXTS = {".pdf", ".docx", ".doc", ".zip", ".txt", ".md"}
INNER_EXTS = {".pdf", ".docx", ".doc", ".txt", ".md"}
FILE_TIMEOUT_SEC = 600
HOLD_MB = 30          # PDF 大於此值 → 移到 HOLD_DIR，不跑（多半是整份掃描檔，OCR 會超過 timeout）
NAME_MAX_BYTES = 150  # ZIP 內檔落地名（不含序號與副檔名）的 UTF-8 上限；Linux 單一檔名上限 255 bytes


def _tg(msg):
    """背景執行時唯一的回報管道；失敗只印 WARN，不影響批次結果。"""
    try:
        notify_telegram(msg[:3800], load_secrets())
    except Exception as e:
        print("[WARN] Telegram 回報失敗: " + str(e), file=sys.stderr)


def _is_noise(p):
    return p.name.startswith("._") or "__MACOSX" in p.parts


def _decode_zip_name(info) -> str:
    """ZIP 內檔名：有 UTF-8 旗標(0x800) 時 Python 已正確解碼；
    否則 Python 用 cp437 解讀，台灣券商 zip 多半其實是 cp950 → 還原。失敗回傳原字串。"""
    raw_name = info.filename
    if info.flag_bits & 0x800:
        return raw_name
    try:
        b = raw_name.encode("cp437")
    except Exception:
        return raw_name
    for enc in ("cp950", "utf-8"):
        try:
            s = b.decode(enc)
        except Exception:
            continue
        if any("一" <= ch <= "鿿" for ch in s):
            return s
    return raw_name


def _short_name(stem: str) -> str:
    """去掉路徑字元／控制字元／開頭的點，UTF-8 截到 NAME_MAX_BYTES 以內（不切壞字）。"""
    s = "".join("_" if (c in "/\\" or ord(c) < 32) else c for c in stem).lstrip(".").strip()
    b = s.encode("utf-8")[:NAME_MAX_BYTES]
    return b.decode("utf-8", errors="ignore") or "file"


def _safe_extract(zf, dest: Path):
    """安全解壓：不用 zip 內建路徑；落地名＝三位序號_截短原名.副檔名
    （保留原名：receive_telegram 用檔名判斷 Call Memo、寫 Notion「原始檔名」、轉發券商群組）。
    回傳 (results, errors)：results=[(落地路徑, 顯示檔名)]，errors=[錯誤字串]"""
    results, errors = [], []
    for idx, info in enumerate(zf.infolist(), 1):
        if info.is_dir():
            continue
        if "__MACOSX" in info.filename:
            continue
        shown = _decode_zip_name(info).replace("\\", "/").split("/")[-1]
        if shown.startswith("._"):
            continue
        suffix = Path(shown).suffix.lower()
        if suffix not in INNER_EXTS:
            continue
        target = dest / ("%03d_%s%s" % (idx, _short_name(Path(shown).stem), suffix))
        try:
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        except Exception as e:
            errors.append(shown + ": 解壓失敗 " + str(e))
            continue
        results.append((target, shown))
    return results, errors


def process_one(real_path, display_name, log, secrets):
    """處理單一實體檔案。回傳 (status, payload)：
    ('done', char_count|None) / ('skip', None) / ('fail', error_str)"""
    real_path = Path(real_path)
    try:
        h = ledger.file_hash(real_path)
    except Exception as e:
        return ("fail", display_name + ": hash失敗 " + str(e))

    if ledger.is_done(h):
        return ("skip", None)

    ledger.mark_processing(h, display_name, real_path.stat().st_size)
    try:
        out, err = run_with_timeout(real_path, FILE_TIMEOUT_SEC)
        if err:
            raise RuntimeError(err)
        for ln in (out or "").splitlines():
            if ln.strip():
                log("    " + ln)
        m = re.search(r"(\d+)\s*字元", out or "")
        cc = int(m.group(1)) if m else None
        ledger.mark_done(h, cc)
        return ("done", cc)
    except Exception as e:
        ledger.mark_failed(h, str(e))
        return ("fail", display_name + ": " + str(e))


def _hold(fp):
    """大檔移到 HOLD_DIR（持久目錄，不在 /tmp），同名加時間戳。回傳落點。"""
    HOLD_DIR.mkdir(parents=True, exist_ok=True)
    dest = HOLD_DIR / fp.name
    if dest.exists():
        dest = HOLD_DIR / (fp.stem + "_" + str(int(fp.stat().st_mtime)) + fp.suffix)
    shutil.move(str(fp), str(dest))
    return dest


def main():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    lock_fp = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[LOCK] 已有另一個 batch 實例在執行，本次直接退出（避免 race）")
        _tg("⏸ 券商批次未啟動：已有另一個 batch 實例在執行（RESULT=LOCKED）")
        return 0

    log_path = LOG_DIR / ("run_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    logf = open(log_path, "a", encoding="utf-8")

    def log(msg):
        line = datetime.now().strftime("%H:%M:%S ") + msg
        print(line)
        logf.write(line + "\n")
        logf.flush()

    ledger.init()

    # 清掉 macOS 雜訊檔
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
        log("[CLEAN] 清掉 " + str(purged) + " 個 macOS 雜訊檔")
    if not queue:
        log("[INFO] broker_queue 是空的，沒有檔案需要處理")
        log("=== BATCH END === success=0 failed=0 skipped=0 held=0 exit=0")
        logf.close()
        _tg("📭 券商批次：佇列是空的（RESULT=EMPTY）")
        return 0

    log("[INFO] 佇列共 " + str(len(queue)) + " 份")
    _tg("▶️ 券商批次開始：佇列 " + str(len(queue)) + " 份，結束時另行回報")
    secrets = load_secrets()
    success, skipped, fail, held = 0, 0, [], []

    def record(status, payload, label):
        nonlocal success, skipped
        if status == "done":
            success += 1
            cc = payload
            if cc is not None and cc < 200:
                log(label + " [DONE] ⚠️ 轉出字數偏少(" + str(cc) + ")，建議人工檢視")
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

        if fp.suffix.lower() == ".pdf" and fp.stat().st_size > HOLD_MB * 1024 * 1024:
            mb = fp.stat().st_size / 1024 / 1024
            try:
                dest = _hold(fp)
                held.append(fp.name + " (%.0fMB)" % mb)
                log(prefix + " [HOLD] %.0fMB > %dMB，移到 %s，不處理" % (mb, HOLD_MB, dest))
            except Exception as e:
                fail.append(fp.name + ": HOLD 移檔失敗 " + str(e))
                log(prefix + " [ERROR] HOLD 移檔失敗 " + str(e))
            continue

        if fp.suffix.lower() == ".zip":
            log(prefix + " [ZIP] 解壓後逐份處理")
            tmp = Path(tempfile.mkdtemp(prefix="brokerzip_"))
            try:
                with zipfile.ZipFile(fp) as z:
                    extracted, x_errors = _safe_extract(z, tmp)
            except Exception as e:
                log(prefix + " [ZIP-ERROR] 解壓失敗 " + str(e))
                fail.append(fp.name + ": zip解壓失敗 " + str(e))
                shutil.rmtree(tmp, ignore_errors=True)
                continue

            zip_had_fail = False
            for xe in x_errors:
                fail.append(fp.name + "::" + xe)
                log(prefix + " [ZIP-ERROR] " + xe)
                zip_had_fail = True

            if not extracted:
                log(prefix + " [ZIP] 內部沒有有效文件")
            else:
                log(prefix + " [ZIP] 內含 " + str(len(extracted)) + " 份有效文件")
            for j, (ip, shown) in enumerate(extracted, 1):
                label = prefix + "  └(" + str(j) + "/" + str(len(extracted)) + ") " + shown
                status, payload = process_one(ip, fp.name + "::" + shown, log, secrets)
                record(status, payload, label)
                if status == "fail":
                    zip_had_fail = True

            shutil.rmtree(tmp, ignore_errors=True)
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
            if status in ("done", "skip"):
                try:
                    fp.unlink()
                except FileNotFoundError:
                    pass

    log("")
    log("完成：成功 " + str(success) + " / 失敗 " + str(len(fail)) + " / 跳過(重複) " + str(skipped) + " / 移到 hold " + str(len(held)))
    for f in fail:
        log(" - " + f)
    for h in held:
        log(" - [HOLD] " + h)
    exit_code = 0 if not fail else 1
    log("=== BATCH END === success=" + str(success) + " failed=" + str(len(fail)) + " skipped=" + str(skipped) + " held=" + str(len(held)) + " exit=" + str(exit_code))
    logf.close()

    msg = ("✅" if not fail else "⚠️") + " 券商批次結束（RESULT=" + ("OK" if not fail else "PARTIAL") + "）\n"
    msg += "成功 %d／失敗 %d／跳過 %d／hold %d\n" % (success, len(fail), skipped, len(held))
    for f in fail[:15]:
        msg += "❌ " + html.escape(f[:160]) + "\n"
    if len(fail) > 15:
        msg += "…另 %d 筆失敗見 log\n" % (len(fail) - 15)
    for h in held:
        msg += "📦 " + html.escape(h) + "\n"
    msg += "log: " + html.escape(str(log_path))
    _tg(msg)
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as e:
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        _tg("🔴 券商批次崩潰（RESULT=CRASH）\n" + html.escape(repr(e))[:300] + "\n" + html.escape(tb[-1200:]))
        sys.exit(2)
