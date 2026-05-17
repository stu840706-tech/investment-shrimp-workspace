#!/usr/bin/env python3
"""
weekly_health_check.py - 投資蝦週健診
每週一 UTC 01:00（台北 09:00）執行
純 Python，不呼叫 M2.7，不做推理判斷。
只蒐集事實 → 寫 Notion → 發 Telegram。
推理判斷由 Claude 做。
"""

import json
import os
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import SECRETS, NOTION_KEY

import urllib.request
import urllib.error

# ── 常數 ────────────────────────────────────────────────────────────────────

WORKSPACE = Path(__file__).parent.parent
STATE_DIR = WORKSPACE / "state"
BASELINE_FILE = STATE_DIR / "health_baseline.json"

TZ_TAIPEI = timezone(timedelta(hours=8))
NOW_TW = datetime.now(TZ_TAIPEI)
WEEK_STR = NOW_TW.strftime("%Y-W%V")
DATE_STR = NOW_TW.strftime("%Y-%m-%d")

NOTION_API = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

TELEGRAM_TOKEN = SECRETS.get("telegram_bot_token", "")
TELEGRAM_CHAT_ID = SECRETS.get("telegram_dm", "")

# hub page ID（在投資蝦系統底下建立健診頁面）
HUB_PAGE_ID = SECRETS.get("notion_parent_db_id", "34e226f5-a398-802f-bf27-fa7a4fa19970")

# 需要 cron 的白名單（不在這裡的 .py 不算孤兒）
CRON_REQUIRED = {
    "memory_archive.py",
 "daily_scan_pipeline.sh",
    "daily_dashboard.py",
    "fetch_calendar.py",
    "outcome_review.py",
    "news_pipeline.py",
    "broker_digest.py",
}

# log 設定：{ 任務名: (log路徑, 成功關鍵字, 檢查天數) }
LOG_CHECKS = {
    "daily-scan":       ("/tmp/daily_scan_pipeline.log", "pipeline 全部完成",   7),
    "daily-dashboard":  ("/tmp/daily_dashboard.log", "完成",       7),
    "news-morning":     ("/tmp/news_morning.log", "管線完成",    5),
    "news-evening":     ("/tmp/news_evening.log", "管線完成",    5),
    "broker-digest":    ("/tmp/broker_digest.log",         "完成",       5),
    "fetch-calendar":   ("~/.openclaw/logs/fetch_calendar.log", "完成",   7),
    "outcome-review":   ("~/.openclaw/logs/outcome_review.log", "完成",   7),
}

# Notion DB 筆數監控：{ 名稱: (db_id, 每週最低新增, 每週最高新增) }
DB_CHECKS = {
    "scan_results":    ("34e226f5-a398-816e-93ca-c2f0d5a2456a", 3000, None),
    "event_calendar":  ("34e226f5-a398-81ea-8a99-ecd61b9e8795", None, 50),
    "broker_reports":  ("34e226f5-a398-81a0-b22d-fea135a192fd", None, None),  # 只記錄，不設警戒
}

# ── 工具函數 ─────────────────────────────────────────────────────────────────

def notion_request(method, path, body=None):
    url = f"{NOTION_API}/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=NOTION_HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": str(e), "body": e.read().decode()}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] 未設定 token/chat_id，跳過")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=body,
          headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            print("[Telegram] 發送成功")
    except Exception as e:
        print(f"[Telegram] 失敗: {e}")

def load_baseline():
    if BASELINE_FILE.exists():
        return json.loads(BASELINE_FILE.read_text())
    return {}

def save_baseline(data):
    STATE_DIR.mkdir(exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))

# ── 檢查 A：Crontab 完整性 ────────────────────────────────────────────────────

def check_crontab():
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    cron_content = result.stdout
    cron_scripts = set()
    for line in cron_content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        for part in line.split():
            if part.endswith(".py") or part.endswith(".sh"):
                cron_scripts.add(Path(part).name)

    missing = CRON_REQUIRED - cron_scripts
    extra = cron_scripts - CRON_REQUIRED  # 有 cron 但不在白名單（不一定是問題）

    return {
        "cron_scripts_found": sorted(cron_scripts),
        "expected": sorted(CRON_REQUIRED),
        "missing_from_cron": sorted(missing),
        "extra_in_cron": sorted(extra),
        "ok": len(missing) == 0,
    }

# ── 檢查 B：Log 健康度 ────────────────────────────────────────────────────────

def check_logs():
    results = {}
    cutoff = NOW_TW - timedelta(days=7)

    for task, (log_path, success_kw, check_days) in LOG_CHECKS.items():
        if log_path.startswith("/"):
            full_path = Path(log_path)
        else:
            full_path = Path(os.path.expanduser(log_path))

        if not full_path.exists():
            results[task] = {"status": "MISSING", "detail": "log 檔不存在"}
            continue

        mtime = datetime.fromtimestamp(full_path.stat().st_mtime, tz=TZ_TAIPEI)
        days_ago = (NOW_TW - mtime).days

        # 讀最後 100 行找成功關鍵字
        try:
            lines = full_path.read_text(errors="replace").splitlines()[-100:]
            last_success = None
            for line in reversed(lines):
                if success_kw in line:
                    last_success = line[:80]
                    break
        except Exception as e:
            results[task] = {"status": "READ_ERROR", "detail": str(e)}
            continue

        ok = last_success is not None and days_ago <= check_days
        results[task] = {
            "status": "OK" if ok else "WARN",
            "last_modified_days_ago": days_ago,
            "last_success_line": last_success,
            "ok": ok,
        }

    return results

# ── 檢查 C：Notion DB 筆數 ────────────────────────────────────────────────────

def get_db_count(db_id):
    """查詢 Notion DB 本週新增筆數"""
    cutoff = (NOW_TW - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    body2 = {
        "filter": {
            "timestamp": "created_time",
            "created_time": {"after": cutoff}
        },
        "page_size": 100
    }
    count = 0
    cursor = None
    for _ in range(20):  # 最多查 20 頁 = 2000 筆
        if cursor:
            body2["start_cursor"] = cursor
        resp = notion_request("POST", f"databases/{db_id}/query", body2)
        if "error" in resp:
            return None, str(resp.get("error"))
        count += len(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return count, None

def check_notion_dbs():
    results = {}
    for name, (db_id, min_weekly, max_weekly) in DB_CHECKS.items():
        count, err = get_db_count(db_id)
        if err:
            results[name] = {"status": "ERROR", "detail": err}
            continue

        warnings = []
        if min_weekly and count < min_weekly:
            warnings.append(f"本週新增 {count} 筆，低於警戒值 {min_weekly}")
        if max_weekly and count > max_weekly:
            warnings.append(f"本週新增 {count} 筆，超過警戒值 {max_weekly}（可能重複寫入）")

        results[name] = {
            "weekly_new": count,
            "min_threshold": min_weekly,
            "max_threshold": max_weekly,
            "warnings": warnings,
            "ok": len(warnings) == 0,
        }
    return results

# ── 檢查 D：孤兒檔案 ──────────────────────────────────────────────────────────

def check_orphans():
    workflows_dir = WORKSPACE / "workflows"
    all_py = {f.name for f in workflows_dir.glob("*.py")}

    # 不需要 cron 的白名單
    no_cron_ok = {
        "_common.py", "_scan_utils.py",
        "news_aggregator.py", "news_fetcher.py",
        "news_publisher.py", "news_sender.py",
        "scan_industry.py", "scan_institutional.py",
        "scan_news.py", "scan_quarterly.py", "scan_revenue.py",
        "setup_notion_databases.py", "show_manual.py",
        "weekly_health_check.py",  # 本腳本
 "daily-scan-summary.py", # 由 pipeline.sh 呼叫，不直接進 crontab
    }

    orphan_candidates = all_py - CRON_REQUIRED - no_cron_ok

    # 確認是否被其他 .py import
    confirmed_orphans = []
    for candidate in orphan_candidates:
        stem = candidate.replace(".py", "").replace("-", "_")
        referenced = False
        for py_file in workflows_dir.glob("*.py"):
            if py_file.name == candidate:
                continue
            content = py_file.read_text(errors="replace")
            if stem in content or candidate in content:
                referenced = True
                break
        if not referenced:
            confirmed_orphans.append(candidate)

    # Skills 孤島：有 SKILL.md 但 scripts/ 目錄下無 .py
    skill_orphans = []
    skills_dir = WORKSPACE / "skills"
    for skill_md in skills_dir.glob("*/SKILL.md"):
        scripts_dir = skill_md.parent / "scripts"
        if not scripts_dir.exists() or not list(scripts_dir.glob("*.py")):
            skill_name = skill_md.parent.name
            # 排除已知閒置 skills
            idle_skills = {
                "eastmoney-stock", "elite-longterm-memory", "elite-longterm-memory-1-2-3",
                "financial-analysis-agent", "knowledge-graph-skill",
                "stock-study", "trading-devbox", "tushare-stock-skill",
                "tw-revenue-backfill", "tw-stock-info",
                "us-stock-analysis", "web-scraping",
 "user-manual", # 僅有 SKILL.md，無本體，保留備用
            }
            if skill_name not in idle_skills:
                skill_orphans.append(skill_name)

    return {
        "workflow_orphans": sorted(confirmed_orphans),
        "skill_orphans": sorted(skill_orphans),
        "ok": len(confirmed_orphans) == 0 and len(skill_orphans) == 0,
    }

# ── 檢查 E：Syntax Check ──────────────────────────────────────────────────────

def check_syntax():
    errors = []
    for py_file in (WORKSPACE / "workflows").glob("*.py"):
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(py_file)],
            capture_output=True, text=True
        )
        if r.returncode != 0:
            errors.append({"file": py_file.name, "error": r.stderr.strip()[:200]})
    return {
        "total_checked": len(list((WORKSPACE / "workflows").glob("*.py"))),
        "errors": errors,
        "ok": len(errors) == 0,
    }

# ── 檢查 F：過期 State 檔案 ────────────────────────────────────────────────────

def check_stale_state():
    issues = []
    cutoff_14 = NOW_TW - timedelta(days=14)
    cutoff_7 = NOW_TW - timedelta(days=7)

    for f in STATE_DIR.glob("scan_results_*.json"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=TZ_TAIPEI)
        if mtime < cutoff_14:
            issues.append(f"{f.name}（{(NOW_TW - mtime).days} 天）")

    for f in STATE_DIR.glob("broker_morning_*.txt"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=TZ_TAIPEI)
        if mtime < cutoff_7:
            issues.append(f"{f.name}（{(NOW_TW - mtime).days} 天）")

    fingerprint = WORKSPACE / "memory" / "news-fingerprints.md"
    fp_count = 0
    if fingerprint.exists():
        fp_count = fingerprint.read_text(errors="replace").count("\n")
        if fp_count > 2000:
            issues.append(f"news-fingerprints.md 有 {fp_count} 行（警戒值 2000）")

    return {
        "stale_files": issues,
        "fingerprint_lines": fp_count,
        "ok": len(issues) == 0,
    }

# ── 組裝報告 + 寫 Notion ──────────────────────────────────────────────────────

def build_telegram_msg(report):
    lines = [f"🔍 <b>投資蝦週健診 {WEEK_STR}</b>"]
    lines.append("")

    warnings = []

    # A
    a = report["crontab"]
    if a["ok"]:
        lines.append(f"✅ crontab 完整（{len(a['cron_scripts_found'])}/{len(a['expected'])} 腳本）")
    else:
        msg = f"❌ crontab 缺少：{', '.join(a['missing_from_cron'])}"
        lines.append(msg)
        warnings.append(msg)

    # B
    b = report["logs"]
    warn_logs = [k for k, v in b.items() if not v.get("ok", True)]
    if not warn_logs:
        lines.append(f"✅ 全部 log 正常（{len(b)} 個任務）")
    else:
        msg = f"⚠️ log 異常：{', '.join(warn_logs)}"
        lines.append(msg)
        warnings.append(msg)

    # C
    c = report["notion_dbs"]
    for name, v in c.items():
        if v.get("warnings"):
            for w in v["warnings"]:
                msg = f"⚠️ {name}：{w}"
                lines.append(msg)
                warnings.append(msg)
        else:
            lines.append(f"✅ {name}：本週新增 {v.get('weekly_new', '?')} 筆")

    # D
    d = report["orphans"]
    if d["ok"]:
        lines.append("✅ 無孤兒腳本")
    else:
        items = d["workflow_orphans"] + [f"skill:{s}" for s in d["skill_orphans"]]
        msg = f"⚠️ 孤兒檔案：{', '.join(items)}"
        lines.append(msg)
        warnings.append(msg)

    # E
    e = report["syntax"]
    if e["ok"]:
        lines.append(f"✅ Syntax check 全過（{e['total_checked']} 個 py 檔）")
    else:
        msg = f"❌ Syntax 錯誤：{', '.join(x['file'] for x in e['errors'])}"
        lines.append(msg)
        warnings.append(msg)

    # F
    f = report["stale"]
    if f["ok"]:
        lines.append("✅ 無過期 state 檔案")
    else:
        msg = f"⚠️ 過期檔案：{'; '.join(f['stale_files'])}"
        lines.append(msg)
        warnings.append(msg)

    lines.append("")
    if warnings:
        lines.append(f"⚠️ 共 <b>{len(warnings)} 個警告</b>，請把完整報告貼給 Claude 診斷")
        notion_url = report.get("notion_url", "")
        if notion_url:
            lines.append(f"📄 {notion_url}")
    else:
        lines.append("🎉 系統一切正常，本週無需處理")

    return "\n".join(lines)

def _status_icon(ok):
    """把布林值或字串轉成 Notion select 選項名稱"""
    if isinstance(ok, bool):
        return "✅" if ok else "⚠️"
    if isinstance(ok, str):
        return ok
    return "✅"


def _count_warnings(report):
    """計算本週警告數量"""
    count = 0
    if not report.get("crontab", {}).get("ok", True):
        count += 1
    for v in report.get("logs", {}).values():
        if isinstance(v, dict) and not v.get("ok", True):
            count += 1
    for v in report.get("notion_dbs", {}).values():
        if isinstance(v, dict) and v.get("warnings"):
            count += 1
    if not report.get("orphans", {}).get("ok", True):
        count += 1
    if not report.get("syntax", {}).get("ok", True):
            count += 1
    if not report.get("stale", {}).get("ok", True):
            count += 1
    return count


def _build_blocks(report):
    """把健診報告組裝成 Notion blocks（分段，不截斷）"""
    def h2(text):
        return {"object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}}

    def para(text):
        chunks = [text[i:i+1900] for i in range(0, len(text), 1900)]
        return [{"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": [{"type": "text", "text": {"content": c}}]}}
                for c in chunks]

    def bullet(text):
        return {"object": "block", "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text",
                "text": {"content": text[:1900]}}]}}

    def divider():
        return {"object": "block", "type": "divider", "divider": {}}

    blocks = []

    # A. Crontab
    cron = report.get("crontab", {})
    blocks.append(h2("A. Crontab 完整性"))
    ok = cron.get("ok", True)
    blocks.append(bullet(f"狀態：{'✅ 正常' if ok else '⚠️ 異常'}"))
    missing = cron.get("missing_from_cron", [])
    if missing:
        blocks.append(bullet(f"缺少腳本：{', '.join(missing)}"))
    extra = cron.get("extra_in_cron", [])
    if extra:
        blocks.append(bullet(f"多餘腳本：{', '.join(extra)}"))
    blocks.append(divider())

    # B. Log 健康度
    logs = report.get("logs", {})
    blocks.append(h2("B. Log 健康度"))
    for name, info in logs.items():
        if isinstance(info, dict):
            status = "✅" if info.get("ok") else "⚠️"
            detail = info.get("last_success_line") or info.get("detail") or ""
            blocks.append(bullet(f"{status} {name}：{detail[:100]}"))
    blocks.append(divider())

    # C. Notion DB 筆數
    dbs = report.get("notion_dbs", {})
    blocks.append(h2("C. Notion DB 本週新增筆數"))
    for name, info in dbs.items():
        if isinstance(info, dict):
            status = "⚠️" if info.get("warnings") else "✅"
            count = info.get("weekly_new", "?")
            blocks.append(bullet(f"{status} {name}：{count} 筆"))
    blocks.append(divider())

    # D. 孤兒檔案
    orphans = report.get("orphans", {})
    blocks.append(h2("D. 孤兒檔案"))
    wf = orphans.get("workflow_orphans", [])
    sk = orphans.get("skill_orphans", [])
    if not wf and not sk:
        blocks.append(bullet("✅ 無"))
    else:
        for f in wf:
            blocks.append(bullet(f"⚠️ workflow 孤兒：{f}"))
        for f in sk:
            blocks.append(bullet(f"⚠️ skill 孤島：{f}"))
    blocks.append(divider())

    # E. Syntax Check
    syntax = report.get("syntax", {})
    blocks.append(h2("E. Syntax Check"))
    total = syntax.get("total_checked", 0)
    errors = syntax.get("errors", [])
    if not errors:
        blocks.append(bullet(f"✅ 全部 {total} 個 py 檔通過"))
    else:
        blocks.append(bullet(f"⚠️ {len(errors)}/{total} 個檔案有語法錯誤"))
        for e in errors:
            blocks.append(bullet(f" ❌ {e}"))
    blocks.append(divider())

    # F. 過期 State
    stale = report.get("stale", {})
    blocks.append(h2("F. 過期 State 檔案"))
    stale_files = stale.get("stale_files", [])
    if not stale_files:
        blocks.append(bullet("✅ 無過期 State 檔案"))
    else:
        for item in stale_files:
            blocks.append(bullet(f"⚠️ {item}"))
    blocks.append(divider())

    # 原始 JSON（分批，避免截斷）
    blocks.append(h2("原始 JSON"))
    raw_json = json.dumps(report, ensure_ascii=False, indent=2)
    for chunk in [raw_json[i:i+1800] for i in range(0, len(raw_json), 1800)]:
        blocks.extend(para(chunk))

    return blocks


def check_existing_health_record(week_str):
    """查詢週健診 DB 中是否已有本週記錄，有則回傳 page_id，沒有回傳 None"""
    HEALTH_DB_ID = "f0b59e91-4193-41c3-a23a-1a8c5b84f8a1"
    resp = notion_request("POST", f"databases/{HEALTH_DB_ID}/query", {
        "filter": {"property": "週次", "title": {"equals": week_str}},
        "page_size": 1,
    })
    pages = resp.get("results", [])
    return pages[0]["id"] if pages else None


def write_notion_report(report):
    """
    寫入週稽查記錄到 🏥 週稽查記錄 DB。
    - properties：結構化欄位（整體狀態、各模組 ✅/⚠️、警告數量）
    - 頁面內容：六個區塊 + 原始 JSON（分批寫入，不截斷）
    - 防重複：同一週次已有記錄則更新，沒有才新建
    """
    HEALTH_DB_ID = "f0b59e91-4193-41c3-a23a-1a8c5b84f8a1"

    cron = report.get("crontab", {})
    logs = report.get("logs", {})
    dbs = report.get("notion_dbs", {})
    orphans = report.get("orphans", {})
    syntax = report.get("syntax", {})
    stale = report.get("stale", {})

    warn_count = _count_warnings(report)

    # 判斷各模組狀態
    def safeness(v, key):
        val = v.get(key, {}) if isinstance(v, dict) else {}
        return val if isinstance(val, dict) else {}

    cron_ok = "✅" if cron.get("ok", True) else "⚠️"
    log_ok = "✅" if all(v.get("ok", True) for v in logs.values() if isinstance(v, dict)) else "⚠️"
    db_ok = "✅" if not any(v.get("warnings") for v in dbs.values() if isinstance(v, dict)) else "⚠️"
    orphan_ok = "✅" if (not orphans.get("workflow_orphans") and not orphans.get("skill_orphans")) else "⚠️"
    syntax_ok = "✅" if not syntax.get("errors") else "❌"
    stale_ok = "✅" if stale.get("ok", True) else "⚠️"

    overall = "✅ 正常" if warn_count == 0 else ("❌ 失敗" if syntax_ok == "❌" else "⚠️ 警告")

    props = {
        "週次": {"title": [{"text": {"content": WEEK_STR}}]},
        "執行日期": {"date": {"start": NOW_TW.strftime("%Y-%m-%d")}},
        "整體狀態": {"select": {"name": overall}},
        "Crontab": {"select": {"name": cron_ok}},
        "Log健康度": {"select": {"name": log_ok}},
        "DB新增筆數": {"select": {"name": db_ok}},
        "孤兒檔案": {"select": {"name": orphan_ok}},
        "Syntax Check": {"select": {"name": syntax_ok}},
        "過期State": {"select": {"name": stale_ok}},
        "警告數量": {"number": warn_count},
    }

    blocks = _build_blocks(report)

    # 防重複：查詢是否已有本週記錄
    existing_id = check_existing_health_record(WEEK_STR)

    if existing_id:
        print(f" [週稽查] 本週記錄已存在（{existing_id}），更新 properties...")
        notion_request("PATCH", f"pages/{existing_id}", {"properties": props})
        for i in range(0, len(blocks), 100):
            notion_request("PATCH", f"blocks/{existing_id}/children",
                          {"children": blocks[i:i+100]})
        page_id = existing_id
    else:
        print(f" [週稽查] 新建本週記錄...")
        resp = notion_request("POST", "pages", {
            "parent": {"database_id": HEALTH_DB_ID},
            "properties": props,
            "children": blocks[:100],
        })
        page_id = resp.get("id", "")
        for i in range(100, len(blocks), 100):
            notion_request("PATCH", f"blocks/{page_id}/children",
                          {"children": blocks[i:i+100]})

    url = f"https://notion.so/{page_id.replace('-', '')}"
    print(f" [週稽查] Notion 記錄：{url}")
    return url

# ── 主程序 ────────────────────────────────────────────────────────────────────

def main():
    print(f"{'='*60}")
    print(f"投資蝦週健診 {WEEK_STR}")
    print(f"執行時間：{NOW_TW.strftime('%Y-%m-%d %H:%M')} 台北")
    print(f"{'='*60}")

    report = {}

    print("[A] 檢查 crontab...")
    report["crontab"] = check_crontab()
    print(f"    → {'OK' if report['crontab']['ok'] else 'WARN: ' + str(report['crontab']['missing_from_cron'])}")

    print("[B] 檢查 log 健康度...")
    report["logs"] = check_logs()
    warn_logs = [k for k, v in report["logs"].items() if not v.get("ok", True)]
    print(f"    → {len(warn_logs)} 個異常" if warn_logs else "    → 全部正常")

    print("[C] 查詢 Notion DB 筆數（本週新增）...")
    report["notion_dbs"] = check_notion_dbs()
    for name, v in report["notion_dbs"].items():
        print(f"    → {name}: {v.get('weekly_new', 'ERROR')} 筆")

    print("[D] 掃描孤兒檔案...")
    report["orphans"] = check_orphans()
    print(f"    → workflow: {report['orphans']['workflow_orphans']}, skill: {report['orphans']['skill_orphans']}")

    print("[E] Syntax check...")
    report["syntax"] = check_syntax()
    print(f"    → {report['syntax']['total_checked']} 個檔案，{len(report['syntax']['errors'])} 個錯誤")

    print("[F] 過期 state 檔案...")
    report["stale"] = check_stale_state()
    print(f"    → {len(report['stale']['stale_files'])} 個問題")

    print("[Notion] 寫入週健診報告...")
    notion_url = write_notion_report(report)
    report["notion_url"] = notion_url
    print(f"    → {notion_url}")

    print("[Telegram] 發送摘要...")
    msg = build_telegram_msg(report)
    send_telegram(msg)
    print(msg)

    print(f"{'='*60}")
    print("週健診完成")

if __name__ == "__main__":
    main()
