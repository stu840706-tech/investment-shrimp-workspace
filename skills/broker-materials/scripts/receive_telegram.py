#!/usr/bin/env python3
"""receive_telegram.py — Telegram Group 券商材料接收器

骨架狀態(B5 實測後補完):
  - [ ] Telegram Bot API long-polling(python-telegram-bot)
  - [ ] 檔案下載 → /tmp
  - [ ] 呼叫 pdf-reader 子腳本
  - [ ] M2.7 分類 prompt + API 呼叫
  - [ ] 寫入對應 Notion db
  - [ ] confidence 低時 Telegram 請示

用法:
    python3 receive_telegram.py           # 持續 polling(cron 或 systemd 管理)
    python3 receive_telegram.py --once    # 處理一次 queue 就退出(測試用)

監聽的 chat_id:
    -5290205228(S-3 常駐授權 channel)
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"
PDF_READER = WORKSPACE / "skills" / "pdf-reader" / "scripts" / "pdf_dispatch.py"

TARGET_CHAT_ID = -5290205228
VALID_CATEGORIES = ["stock_report", "industry_report", "morning_brief"]

# M2.7 context 保護:PDF 純文字只取前 N 字做分類
CLASSIFY_TEXT_HEAD = 8000
CLASSIFY_TEXT_TAIL = 2000


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def pdf_to_text(pdf_path: Path) -> str:
    """呼叫 pdf-reader 子腳本。"""
    result = subprocess.run(
        [sys.executable, str(PDF_READER), str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdf-reader 失敗: {result.stderr}")
    return result.stdout


def classify_with_m27(text: str) -> dict:
    """M2.7 分類 + 萃取結構化欄位。骨架 TODO。"""
    # TODO (B5):
    #   1. 取前 CLASSIFY_TEXT_HEAD 字 + 末 CLASSIFY_TEXT_TAIL 字
    #   2. POST api.minimax.io/anthropic/v1/messages,thinking=off
    #   3. prompt 附 few-shot(讀 references/classification_examples.md)
    #   4. 回 JSON: {category, confidence, extracted_fields, ...}
    return {
        "category": "stock_report",  # placeholder
        "confidence": "low",  # 讓骨架階段走人工裁決路徑
        "extracted_fields": {},
        "note": "(骨架 placeholder,B5 接真實 M2.7 API)",
    }


def write_to_notion(category: str, fields: dict, secrets: dict):
    """依 category 寫對應 db。骨架 TODO。"""
    # TODO (B5): 依 category 選 db_id:
    #   stock_report / morning_brief → notion_broker_reports_db
    #   industry_report → notion_industry_reports_db
    pass


def notify_telegram(message: str, secrets: dict, with_keyboard=False):
    """Telegram 回報 Kai。骨架 TODO。"""
    # TODO (B5): POST Telegram Bot API sendMessage
    print(f"[TELEGRAM] {message}")


def process_file(file_path: Path, secrets: dict):
    """單一檔案處理 pipeline。"""
    print(f"\n[PROCESS] {file_path.name}")

    # Step 1: PDF → text
    try:
        text = pdf_to_text(file_path)
    except Exception as e:
        print(f"[ERROR] pdf-reader 失敗: {e}")
        notify_telegram(f"❌ {file_path.name}: PDF 解析失敗 ({e})", secrets)
        return

    # Step 2: M2.7 分類
    try:
        result = classify_with_m27(text)
    except Exception as e:
        print(f"[ERROR] M2.7 分類失敗: {e}")
        notify_telegram(f"❌ {file_path.name}: 分類失敗 ({e})", secrets)
        return

    category = result["category"]
    confidence = result["confidence"]
    print(f"  category={category}, confidence={confidence}")

    # Step 3: 依 confidence 分流
    if confidence == "high":
        write_to_notion(category, result["extracted_fields"], secrets)
        notify_telegram(f"✅ {file_path.name}: 已分類為 {category}", secrets)
    elif confidence == "medium":
        write_to_notion(category, result["extracted_fields"], secrets)
        notify_telegram(
            f"⚠️  {file_path.name}: 分類為 {category}(中等信心),請 Kai 確認",
            secrets,
            with_keyboard=True,
        )
    else:  # low
        notify_telegram(
            f"❓ {file_path.name}: 分類信心低,請 Kai 選類別",
            secrets,
            with_keyboard=True,
        )


def main():
    parser = argparse.ArgumentParser(description="Telegram 券商材料接收")
    parser.add_argument("--once", action="store_true", help="處理一次 queue 就退出")
    parser.add_argument("--test-file", help="直接測試本地檔案(跳過 Telegram)")
    args = parser.parse_args()

    secrets = load_secrets()

    if args.test_file:
        # 骨架測試路徑:Kai 可以不透過 Telegram 直接測試
        file_path = Path(args.test_file).expanduser().resolve()
        if not file_path.exists():
            print(f"[ERROR] 測試檔不存在: {file_path}", file=sys.stderr)
            return 1
        process_file(file_path, secrets)
        return 0

    # TODO (B5): Telegram long-polling
    print("[SKELETON] Telegram listener 未實作,B5 補完")
    print("  目前可用 --test-file <pdf_path> 做本地測試")
    return 0


if __name__ == "__main__":
    sys.exit(main())
