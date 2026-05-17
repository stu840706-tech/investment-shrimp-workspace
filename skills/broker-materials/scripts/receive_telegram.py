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
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import re


# 股票代碼中英文對照（從 news_publisher.py 同步）
COMPANY_NAMES = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '2317': '鴻海',
    '2408': '南亞科', '2881': '富邦金', '2344': '華邦電', '2337': '旺宏',
    '8299': '群聯', '3037': '欣興', '4958': '臻鼎', '3712': '大聯大',
    '6175': '聯亞', '8086': '宏捷科', '6274': '台燿', '2383': '台光電',
    '3122': '萬潤', '3711': '日月光', '8261': '矽品', '3189': '景碩',
    '3105': '穩懋', '2409': '友達', '3481': '群創', '6515': '穎崴',
    '2345': '智邦', '2327': '國巨', '6116': '彩晶', '2357': '華碩',
    '2353': '宏碁', '2376': '技嘉', '2377': '微星', '6150': '撼訊',
    '3443': '創意', '3035': '智原', '3661': '世芯', '6531': '愛普',
    '3529': '力旺', '3131': '弘塑', '6939': '天虹', '2456': '全新',
    '6213': '聯茂', '6269': '台郡', '2313': '華通', '2367': '燿華',
    '8046': '南電', '3528': '景崎', '1560': '中砂', '6442': '光聖',
    '6285': '啟碁', '3312': '至上', '1319': '東陽',
}


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
    # 純文字檔案直接讀取，不走 pdf-reader
    if pdf_path.suffix.lower() in (".txt", ".md"):
        return pdf_path.read_text(encoding="utf-8", errors="replace")
    
    # Step 1: 嘗試 pdfplumber，若文字層內容豐富則直接使用
    script_dir = PDF_READER.parent.resolve()
    sys.path.insert(0, str(script_dir))
    try:
        from extract_pdfplumber import extract_with_pdfplumber
        text, _ = extract_with_pdfplumber(pdf_path)
        # 計算有意義字元（排除 page separators）
        import re
        cleaned = re.sub(r"--- Page \d+ ---", "", text)
        cleaned = re.sub(r"\s+", "", cleaned)
        meaningful_chars = len(cleaned)
        if meaningful_chars >= 300:
            return text  # 文字層充足，直接用
    except Exception:
        pass
    
    # Step 2: 文字層不足，改走 OCR
    try:
        from extract_ocr import extract_with_ocr
        text, _ = extract_with_ocr(pdf_path)
        if len(text.strip()) > 0:
            return text
    except Exception:
        pass
    
    # Fallback: dispatch（最後手段）
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

    5層 fallback：
    1. 直接 json.loads(raw)（可能截斷）
    2. 截斷 JSON 修復
    3. ast.literal_eval（Python dict repr 格式）
    4. Markdown 表格解析（針對多 stock morning_brief）
    5. Thinking block 文字分析（針對只有 thinking 的回應）
    """
    import re, ast

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
        "x-api-key": secrets["minimax_api_key"],
    }
    payload = {
        "model": MINIMAX_MODEL,
        "max_tokens": 6000,
        "thinking": {"type": "disabled"},
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": f"請分類並萃取以下報告：\n\n{trimmed}"},
        ],
    }
    resp = requests.post(f"{MINIMAX_BASE}/messages", headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    blocks = resp.json()["content"]

    # Layer 0: collect text blocks (filter empty, prefer longest)
    candidates = [b["text"] for b in blocks if b.get("type") == "text" and b.get("text", "").strip()]
    text_blocks = sorted(candidates, key=len, reverse=True) if candidates else []

    if not text_blocks:
        # Layer 5: 只有 thinking block → 從 thinking 文字分析
        result = _fallback_from_thinking(blocks)
        if result:
            return result
        raise RuntimeError(f"M2.7 回應無有效 text block: {[b.get('type') for b in blocks]}")

    raw = text_blocks[0].strip().lstrip("\n")  # 移除前導換行
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    # Layer 1: 直接 JSON（可能截斷）
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            if e.msg in ("Unterminated string starting at", "Expecting value",
                         "Unterminated object", "Invalid control character"):
                result = _try_complete_json(raw)
                if result:
                    print("  [FALLBACK] truncated JSON 修復成功")
                    return result

    # Layer 2: 修補（找到第一個 {，並嘗試解析）
    original_raw = raw
    if not raw.startswith("{"):
        brace_pos = raw.find("{")
        if brace_pos > 0:
            raw = raw[brace_pos:]
        elif brace_pos == -1:
            pass  # 純 markdown，直接交給 Layer 4

    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            if e.msg in ("Unterminated string starting at", "Expecting value",
                         "Unterminated object", "Invalid control character"):
                result = _try_complete_json(raw)
                if result:
                    print("  [FALLBACK] truncated JSON 修復成功 (L2)")
                    return result

    # Layer 3: ast.literal_eval（Python dict repr）
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict) and parsed.get("category"):
            print("  [FALLBACK] ast.literal_eval 成功")
            return parsed
    except (ValueError, SyntaxError):
        pass

    # Layer 4: Markdown 表格解析
    result = _fallback_from_markdown(original_raw)
    if result:
        return result

    # Last resort: 嘗試從 thinking block 擷取
    result = _fallback_from_thinking(blocks)
    if result:
        return result
    raise RuntimeError(f"M2.7 回應無法解析: {raw[:100]}")


def _try_complete_json(raw: str) -> dict | None:
    """嘗試修復被截斷的 JSON。"""
    import json

    # Method 1: 直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Method 2: 找最後一個完整閉合點截斷
    markers = ['"}', '\n]']
    for m in markers:
        idx = raw.rfind(m)
        if idx > 0:
            candidate = raw[:idx + len(m)]
            try:
                result = json.loads(candidate)
                if result.get("category"):
                    return result
            except:
                pass

    # Method 3: 砍掉最後一行再試
    lines = raw.split("\n")
    if len(lines) > 1:
        for i in range(len(lines)-1, max(0, len(lines)-10), -1):
            candidate = "\n".join(lines[:i])
            try:
                result = json.loads(candidate)
                if result.get("category"):
                    return result
            except:
                pass

    return None



def _fallback_from_markdown(md_raw: str) -> dict | None:
    """從 M2.7 回傳的 markdown 表格格式中萃取欄位。"""
    import re

    if md_raw.count("{") > 0 or "|" not in md_raw:
        return None

    fields = {}
    has_bold_key = False

    # First pass: **bold** key format: | **Key** | value |
    for line in md_raw.split("\n"):
        line = line.strip()
        if not line.startswith("|") or "---" in line or ":--" in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3:
            key_cell = parts[1]
            val_cell = parts[2]
            km = re.search(r"\*\*([^*]+)\*\*", key_cell)
            if km:
                has_bold_key = True
                key = km.group(1).strip()
                val = re.sub(r"\*+", "", val_cell).strip().rstrip("/").strip()
                if val:
                    fields[key] = val

    # Second pass: header-row mapping (multi-stock table: | 代碼 | 公司 | 評等 |)
    if not has_bold_key and not fields:
        table_lines = [l.strip() for l in md_raw.split("\n")
                       if l.strip().startswith("|") and "---" not in l and ":--" not in l]
        if len(table_lines) >= 2:
            header_parts = [p.strip() for p in table_lines[0].split("|")]
            header_parts = [p for p in header_parts if p]
            for data_line in table_lines[1:]:
                data_parts = [p.strip() for p in data_line.split("|")]
                data_parts = [p for p in data_parts if p]
                for col_idx in range(min(len(header_parts), len(data_parts))):
                    header = header_parts[col_idx]
                    value = re.sub(r"\*+", "", data_parts[col_idx]).strip().rstrip("/").strip()
                    if value and header:
                        fields[header] = value

    if not fields:
        return None

    # Multi-stock detection: stock code like "6239 TT" in column 1
    stock_code_col = fields.get("股票代碼", "") or fields.get("代碼", "") or ""
    company_col = fields.get("公司名稱", "") or fields.get("公司", "") or ""
    rating_col = fields.get("投資評等", "") or fields.get("評等", "") or ""
    target_col = fields.get("目標價", "") or fields.get("目標價位", "") or ""

    is_stock_code_value = bool(re.match(r"^\d{4}\s*[Tt][Tt]$", stock_code_col))

    if is_stock_code_value and rating_col:
        result = {
            "category": "morning_brief",
            "confidence": "medium",
            "broker_name": fields.get("報告來源", "") or fields.get("券商", "") or "其他",
            "core_view": f"股票：{stock_code_col} {company_col}，評等：{rating_col}，目標價：{target_col}"
        }
        print(f"  [FALLBACK] markdown multi-stock: {stock_code_col}")
        return result

    # Single-stock detection
    rtype = fields.get("報告類型", "") or fields.get("文件類型", "") or ""
    if any(k in rtype for k in ["晨報", "盤後", "晨訊", "晨會"]):
        result = {"category": "morning_brief", "confidence": "low",
                  "broker_name": fields.get("報告來源", "") or fields.get("券商", "") or "其他",
                  "core_view": str(fields)}
        print(f"  [FALLBACK] markdown: morning_brief")
        return result
    elif "產業報告" in rtype:
        result = {"category": "industry_report", "confidence": "low",
                  "topic": fields.get("研究產業", "") or "未命名",
                  "broker_name": fields.get("報告來源", "") or "其他",
                  "core_view": str(fields)}
        print(f"  [FALLBACK] markdown: industry_report")
        return result
    else:
        stock = fields.get("目標公司", "") or fields.get("公司名稱", "") or ""
        result = {"category": "stock_report", "confidence": "low",
                  "stock_code": fields.get("股票代碼", ""), "company_name": stock,
                  "broker_name": fields.get("報告來源", "") or "其他",
                  "rating": fields.get("評等", "未明確"),
                  "core_view": str(fields), "key_excerpt": str(fields)}
        print(f"  [FALLBACK] markdown: stock_report")
        return result


def _fallback_from_thinking(blocks: list) -> dict | None:
    """當 M2.7 只回傳 thinking block 時，從文字內容萃取欄位。"""
    import re

    for b in blocks:
        if b.get("type") == "thinking" and b.get("thinking", "").strip():
            thinking = b.get("thinking", "")

            # Find all stock codes: "6239 TT" pattern
            all_codes = re.findall(r"(\d{4})\s+[Tt][Tt]", thinking)

            rating_m = re.search(r"(強力買進|買進|持有|中立|減碼|賣出)", thinking)
            target_m = re.search(r"目標[價價位]?\s*(\d+(?:\.\d+)?)", thinking)
            is_multi = any(k in thinking for k in ["3家", "三家", "涵蓋", "包含", "多家", "多檔"])
            is_brief = any(k in thinking for k in ["晨報", "晨訊", "盤後", "投顧股市"])

            if is_brief or is_multi or len(all_codes) > 1:
                result = {
                    "category": "morning_brief",
                    "confidence": "low",
                    "broker_name": "福邦",
                    "core_view": f"來源：福邦投顧報告。涵蓋股票：{', '.join(all_codes) if all_codes else '未識別'}. {thinking[:300]}"
                }
                print(f"  [FALLBACK] thinking: morning_brief (codes={all_codes})")
                return result
            elif all_codes:
                stock_code = all_codes[0] + ".TW"
                rating = rating_m.group(1) if rating_m else "未明確"
                tp = target_m.group(1) if (target_m and target_m.group(1).replace(".", "").isdigit()) else "0"
                result = {
                    "category": "stock_report",
                    "confidence": "low",
                    "stock_code": stock_code,
                    "company_name": "",
                    "broker_name": "福邦",
                    "rating": rating,
                    "target_price": float(tp) if tp != "0" else 0,
                    "core_view": f"來源：福邦投顧報告。評等：{rating}。目標價：{tp}元。內容：{thinking[:300]}",
                    "key_excerpt": thinking[:500]
                }
                print(f"  [FALLBACK] thinking: stock_report ({stock_code})")
                return result

    return None




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
    _rd = str(fields.get("report_date") or ""); report_date = _rd if re.match(r"^\d{4}-\d{2}-\d{2}$", _rd) else today
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
            "券商名稱": {"select": {"name": (str(fields.get("broker_name") or "其他"))[:100]}},
            "評等": {"select": {"name": str(fields.get("rating") or "未明確")}},
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
            "券商名稱": {"select": {"name": (str(fields.get("broker_name") or "其他"))[:100]}},
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

    props["digest_mark"] = {"rich_text": [{"text": {"content": "processed"}}]}
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




def classify_with_m27_with_retry(text: str, secrets: dict) -> dict:
    """classify_with_m27 包裝，最多重試 3 次（Timeout/ConnError/5xx）。"""
    import time as _t
    _attempts, _delay = 3, 20
    for _i in range(_attempts):
        try:
            return classify_with_m27(text, secrets)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as _e:
            if _i < _attempts - 1:
                print(f" [RETRY] M2.7 連線失敗，{_delay}s 後重試 ({_i+1}/{_attempts-1})...")
                _t.sleep(_delay)
            else:
                raise
        except requests.exceptions.HTTPError as _e:
            if _e.response.status_code >= 500 and _i < _attempts - 1:
                print(f" [RETRY] M2.7 伺服器錯誤 ({_e.response.status_code})，{_delay}s 後重試...")
                _t.sleep(_delay)
            else:
                raise

def process_file(file_path: Path, secrets: dict):
    print(f"[START] 處理: {file_path.name}")

    # Step 1: PDF → text
    print("[1/4] pdf-reader 轉文字...")
    text = pdf_to_text(file_path)
    print(f"  → {len(text)} 字元")

    # Step 2: M2.7 分類 + 萃取
    print("[2/4] M2.7 分類中...")
    result = classify_with_m27_with_retry(text, secrets)
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
        # 存入今日晨報合併檔
        # 使用台北時間（UTC+8），與 broker_digest.py 的 date_compact 一致
        TPE = timezone(timedelta(hours=8))
        today_str = datetime.now(TPE).strftime("%Y%m%d")
        morning_file = WORKSPACE / "state" / f"broker_morning_{today_str}.txt"
        morning_file.parent.mkdir(parents=True, exist_ok=True)
        broker_name = result.get('broker_name', '')
        report_date = result.get('report_date', '')
        core_view = str(result.get('core_view', ''))
        # 取出股票代碼清單
        found_codes = list(dict.fromkeys(re.findall(r'\b[12][0-9]{3}\b', text)))[:8]
        code_parts = []
        for c in found_codes:
            name = COMPANY_NAMES.get(c, '')
            code_parts.append(f"{c} {name}" if name else c)
        code_line = "、".join(code_parts) if code_parts else "（未偵測到代碼）"
        with open(morning_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*40}\n")
            f.write(f"券商：{broker_name}\n")
            f.write(f"日期：{report_date}\n")
            f.write(f"股票：{code_line}\n")
            f.write(f"核心觀點：{core_view}\n")
            f.write(f"原文：{text[:3000]}\n")
        # 一行確認
        notify_telegram(f"✅ 已收晨報 {broker_name}｜股票：{code_line}", secrets)

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
    parser.add_argument("--text", help="直接處理本地純文字檔案（.txt/.md）")
    args = parser.parse_args()

    secrets = load_secrets()

    if args.test_file:
        file_path = Path(args.test_file).expanduser().resolve()
        if not file_path.exists():
            print(f"[ERROR] 檔案不存在: {file_path}", file=sys.stderr)
            return 1
        process_file(file_path, secrets)
        return 0

    if args.text:
        file_path = Path(args.text).expanduser().resolve()
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