#!/usr/bin/env python3
"""receive_telegram.py — 券商材料接收器（B5a 完整實作）

用法:
    python3 receive_telegram.py --test-file /tmp/report.pdf  # 直接處理本地檔案
    python3 receive_telegram.py --once                        # 單次（供 OpenClaw 呼叫）

設計說明:
    Telegram getUpdates 已被 OpenClaw long-polling 佔用。
    正式流程: OpenClaw 收到 PDF → 存 /tmp → 呼叫本腳本 --test-file。
    本腳本不做 Telegram polling，只做 PDF 處理 + Notion 寫入 + Telegram 回報。
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"
PDF_READER = WORKSPACE / "skills" / "pdf-reader" / "scripts" / "pdf_dispatch.py"

VALID_CATEGORIES = ["stock_report", "industry_report", "morning_brief"]
CLASSIFY_TEXT_HEAD = 8000
CLASSIFY_TEXT_TAIL = 2000

MINIMAX_BASE = "https://api.minimax.io/anthropic/v1"
MINIMAX_MODEL = "MiniMax-M2.7"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def pdf_to_text(pdf_path: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(PDF_READER), str(pdf_path)],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdf-reader 失敗: {result.stderr}")
    return result.stdout


def trim_for_classify(text: str) -> str:
    """取前 8000 + 後 2000 字元，避免超出 M2.7 甜蜜點。"""
    if len(text) <= CLASSIFY_TEXT_HEAD + CLASSIFY_TEXT_TAIL:
        return text
    return text[:CLASSIFY_TEXT_HEAD] + "\n...[中略]...\n" + text[-CLASSIFY_TEXT_TAIL:]


def classify_with_m27(text: str, secrets: dict) -> dict:
    """M2.7 分類 + 萃取結構化欄位。thinking=off（事實萃取類）。

    設計：system prompt 定義格式 + assistant prefill "{"
    強制模型直接接續輸出 JSON，不會把答案塞進 thinking block。
    """
    trimmed = trim_for_classify(text)

    system_prompt = (
        "你是台股券商報告分類器。收到報告後，直接輸出純 JSON，不加任何說明文字。\n\n"
        "分類規則：\n"
        "- stock_report：針對單一個股的研究報告（含評等/目標價）\n"
        "- industry_report：產業趨勢或主題報告\n"
        "- morning_brief：每日晨報（市場綜覽/多股簡評）\n\n"
        'stock_report 格式（數值欄位填數字，無資料填0）：\n'
        '{"category":"stock_report","confidence":"high/medium/low","report_date":"YYYY-MM-DD",'
        '"stock_code":"如2330.TW","company_name":"公司名","broker_name":"券商名",'
        '"rating":"買進/加碼/中立/減碼/賣出/未明確","target_price":0,"current_price":0,'
        '"core_view":"核心觀點","revenue_forecast_this_year":0,"revenue_forecast_next_year":0,'
        '"eps_forecast_this_year":0,"eps_forecast_next_year":0,"gross_margin_forecast":0,'
        '"pe_valuation":0,"key_excerpt":"關鍵段落200字內","investor_meeting_date":"YYYY-MM-DD或空字串"}\n\n'
        'industry_report 格式：\n'
        '{"category":"industry_report","confidence":"high/medium/low","report_date":"YYYY-MM-DD",'
        '"topic":"主題標題","industry_tags":["產業"],"broker_name":"券商名","core_view":"核心觀點",'
        '"key_numbers":"關鍵數字","beneficiary_stocks":["代碼"],"risk_stocks":["代碼"],'
        '"key_excerpt":"關鍵段落200字內"}\n\n'
        'morning_brief 格式：\n'
        '{"category":"morning_brief","confidence":"high/medium/low","report_date":"YYYY-MM-DD",'
        '"broker_name":"券商名","core_view":"重點3句內"}'
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {secrets['minimax_api_key']}",
    }
    payload = {
        "model": MINIMAX_MODEL,
        "max_tokens": 1024,
        "thinking": {"type": "disabled"},
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": f"請分類並萃取以下報告：\n\n{trimmed}"},
            {"role": "assistant", "content": "{"},
        ],
    }
    resp = requests.post(f"{MINIMAX_BASE}/messages", headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    blocks = resp.json()["content"]
    text_blocks = [b["text"] for b in blocks if b.get("type") == "text"]
    if not text_blocks:
        raise RuntimeError(f"M2.7 回應無 text block: {[b.get('type') for b in blocks]}")
    raw = text_blocks[0].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # assistant prefill 已送出 "{"，回傳內容是 JSON 的剩餘部分
    if not raw.startswith("{"):
        raw = "{" + raw
    return json.loads(raw)



def rt(content: str) -> list:
    """Notion rich_text 格式，截斷 2000 字元。"""
    return [{"text": {"content": str(content)[:2000]}}]


def write_investor_meeting(stock_code: str, meeting_date: str, secrets: dict):
    """若券商報告提到法說會日期，寫入 event_calendar（idempotent）。"""
    import time as _time
    db_id = secrets["notion_event_calendar_db"]
    headers = {
        "Authorization": f"Bearer {secrets['notion_key']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    # idempotency check
    check = requests.post(
        f"{NOTION_API}/databases/{db_id}/query",
        headers=headers,
        json={"filter": {"and": [
            {"property": "股票代碼", "rich_text": {"equals": stock_code}},
            {"property": "事件類型", "select": {"equals": "法說會"}},
            {"property": "預計日期", "title": {"equals": meeting_date}},
        ]}, "page_size": 1},
        timeout=15,
    )
    check.raise_for_status()
    if check.json().get("results"):
        print(f"  [SKIP] 法說會已存在: {stock_code} {meeting_date}")
        return
    props = {
        "預計日期": {"title": [{"text": {"content": meeting_date}}]},
        "股票代碼": {"rich_text": [{"text": {"content": stock_code}}]},
        "事件類型": {"select": {"name": "法說會"}},
        "重要性":   {"select": {"name": "高"}},
        "已提醒":   {"checkbox": False},
    }
    resp = requests.post(
        f"{NOTION_API}/pages",
        headers=headers,
        json={"parent": {"database_id": db_id}, "properties": props},
        timeout=15,
    )
    resp.raise_for_status()


def write_to_notion(category: str, fields: dict, secrets: dict) -> str:
    """寫入對應 Notion DB，回傳 page URL。morning_brief 不寫 Notion，回傳空字串。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_date = fields.get("report_date") or today
    headers = {
        "Authorization": f"Bearer {secrets['notion_key']}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }

    if category == "stock_report":
        db_id = secrets["notion_broker_reports_db"]
        props = {
            "股票代碼": {"title": rt(fields.get("stock_code", "未知"))},
            "公司名稱": {"rich_text": rt(fields.get("company_name", ""))},
            "券商名稱": {"select": {"name": str(fields.get("broker_name", "其他"))[:100]}},
            "評等": {"select": {"name": str(fields.get("rating", "未明確"))}},
            "報告日期": {"date": {"start": report_date}},
            "核心觀點": {"rich_text": rt(fields.get("core_view", ""))},
            "原始內文": {"rich_text": rt(fields.get("key_excerpt", ""))},
        }
        for py_key, notion_key in [
            ("target_price", "目標價"),
            ("current_price", "報告當日股價"),
            ("revenue_forecast_this_year", "營收預測_今年"),
            ("revenue_forecast_next_year", "營收預測_明年"),
            ("eps_forecast_this_year", "EPS預測_今年"),
            ("eps_forecast_next_year", "EPS預測_明年"),
            ("gross_margin_forecast", "毛利率預測"),
            ("pe_valuation", "PE估值"),
        ]:
            val = fields.get(py_key, 0)
            try:
                if val and float(val) != 0:
                    props[notion_key] = {"number": float(val)}
            except (TypeError, ValueError):
                pass

    elif category == "industry_report":
        db_id = secrets["notion_industry_reports_db"]
        props = {
            "產業主題": {"title": rt(fields.get("topic", "未命名產業報告"))},
            "券商名稱": {"select": {"name": str(fields.get("broker_name", "其他"))[:100]}},
            "報告日期": {"date": {"start": report_date}},
            "核心觀點": {"rich_text": rt(fields.get("core_view", ""))},
            "關鍵數字": {"rich_text": rt(fields.get("key_numbers", ""))},
            "原始內文_關鍵段落": {"rich_text": rt(fields.get("key_excerpt", ""))},
        }
        tags = fields.get("industry_tags") or []
        if tags:
            props["產業分類"] = {"multi_select": [{"name": str(t)[:100]} for t in tags[:5]]}
        for fkey, nkey in [("beneficiary_stocks", "受惠標的"), ("risk_stocks", "受害標的")]:
            stocks = fields.get(fkey) or []
            if stocks:
                props[nkey] = {"multi_select": [{"name": str(s)[:100]} for s in stocks[:10]]}

    else:  # morning_brief
        return ""

    body = {"parent": {"database_id": db_id}, "properties": props}
    resp = requests.post(f"{NOTION_API}/pages", headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json().get("url", "")


def notify_telegram(message: str, secrets: dict):
    """POST Telegram Bot API sendMessage 給 Kai DM。"""
    url = f"https://api.telegram.org/bot{secrets['telegram_bot_token']}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": secrets["telegram_dm"],
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Telegram 通知失敗: {e}", file=sys.stderr)


def process_file(file_path: Path, secrets: dict):
    print(f"[START] 處理: {file_path.name}")

    # Step 1: PDF → text
    print("[1/4] pdf-reader 轉文字...")
    text = pdf_to_text(file_path)
    print(f"  → {len(text)} 字元")

    # Step 2: M2.7 分類 + 萃取
    print("[2/4] M2.7 分類中...")
    result = classify_with_m27(text, secrets)
    category = result.get("category", "unknown")
    confidence = result.get("confidence", "low")
    print(f"  → category={category}, confidence={confidence}")

    # Step 3: confidence / category 分流
    if category not in VALID_CATEGORIES:
        msg = (f"⚠️ 無法分類\n檔案: {file_path.name}\n"
               f"M2.7 回傳: {json.dumps(result, ensure_ascii=False)[:300]}")
        notify_telegram(msg, secrets)
        print(f"[WARN] 分類失敗，已通知 Kai")
        return

    if confidence == "low":
        msg = (f"🤔 分類信心低，請人工確認\n"
               f"檔案: {file_path.name}\n"
               f"分類: {category}\n"
               f"摘要: {json.dumps(result, ensure_ascii=False)[:400]}")
        notify_telegram(msg, secrets)
        print("[INFO] 信心低，通知 Kai，停止寫入 Notion")
        return

    # Step 4: 寫入 Notion
    print("[3/4] 寫入 Notion...")
    page_url = write_to_notion(category, result, secrets)

    # Step 5: Telegram 回報
    print("[4/4] Telegram 通知...")
    if category == "stock_report":
        msg = (f"✅ 券商個股報告已存入\n"
               f"📌 {result.get('stock_code','')} {result.get('company_name','')}\n"
               f"🏦 {result.get('broker_name','')} | 評等: {result.get('rating','')}\n"
               f"🎯 目標價: {result.get('target_price','')} | 現價: {result.get('current_price','')}\n"
               f"💬 {str(result.get('core_view',''))[:120]}\n"
               f"🔗 {page_url}")
    elif category == "industry_report":
        tags_str = ", ".join(result.get("industry_tags") or [])
        msg = (f"✅ 券商產業報告已存入\n"
               f"📋 {result.get('topic','')}\n"
               f"🏦 {result.get('broker_name','')} | {tags_str}\n"
               f"💬 {str(result.get('core_view',''))[:120]}\n"
               f"🔗 {page_url}")
    else:  # morning_brief
        msg = (f"📰 晨報收到（未存 Notion）\n"
               f"🏦 {result.get('broker_name','')}\n"
               f"📅 {result.get('report_date','')}\n"
               f"💬 {str(result.get('core_view',''))[:200]}")
    notify_telegram(msg, secrets)

    # Step 5b: 若 stock_report 有法說會日期，寫入 event_calendar
    if category == "stock_report":
        meeting_date = str(result.get("investor_meeting_date", "")).strip()
        stock_code = result.get("stock_code", "")
        if meeting_date and len(meeting_date) == 10 and meeting_date != "YYYY-MM-DD":
            try:
                write_investor_meeting(stock_code, meeting_date, secrets)
                print(f"  [OK] 法說會事件寫入 event_calendar: {stock_code} {meeting_date}")
            except Exception as e:
                print(f"  [WARN] 法說會寫入失敗: {e}")

    print(f"[DONE] {category} 處理完成")


def main():
    parser = argparse.ArgumentParser(description="Telegram 券商材料接收")
    parser.add_argument("--once", action="store_true", help="單次處理（OpenClaw 整合用）")
    parser.add_argument("--test-file", help="直接測試本地 PDF 檔案")
    args = parser.parse_args()

    secrets = load_secrets()

    if args.test_file:
        file_path = Path(args.test_file).expanduser().resolve()
        if not file_path.exists():
            print(f"[ERROR] 檔案不存在: {file_path}", file=sys.stderr)
            return 1
        process_file(file_path, secrets)
        return 0

    # getUpdates 被 OpenClaw long-polling 佔用，本腳本不做 polling
    print("[INFO] Telegram polling 由 OpenClaw 負責。")
    print("[INFO] 使用方式: python3 receive_telegram.py --test-file /tmp/report.pdf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
