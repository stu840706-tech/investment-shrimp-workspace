# Telegram Bot 使用指南

## 基本設定

- Bot Token：`config/secrets.json` → `telegram_bot_token`
- Kai 個人 DM：`secrets.json` → `telegram_dm`（`5604476530`）
- 券商報告 Group：`secrets.json` → `telegram_group`（`-5290205228`）

## 發送訊息

```python
import requests

def send_telegram(text, chat_id=None):
    from _common import TELEGRAM_TOKEN, TELEGRAM_DM
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    target = chat_id or TELEGRAM_DM
    try:
        resp = requests.post(url, json={
            'chat_id': target,
            'text': text,
            'disable_web_page_preview': True
        }, timeout=15)
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram 發送失敗] {e}")
        return False
```

## 訊息格式規則

- Telegram **不支援 Markdown table**，改用 bullet list
- Markdown 特殊字元（`_`, `*`, `[`, `]`）在 MarkdownV2 需要跳脫
- 最簡單的做法：用純文字（不加 parse_mode），只用 `\n` 換行
- 每則訊息上限：4096 字元，超過需分拆

## 通知紀律（HEARTBEAT.md 的規則）

```
✅ 發送時機：
  - 有實質異常（營收 YoY>30%、MoM>20%）
  - Workflow 連續失敗 >3 次
  - Standing Orders 觸發條件達成

❌ 不發送：
  - 例行 heartbeat 無異常
  - 資訊重複（已在 Notion，不再 Telegram 贅述）
  - 沉默時段（00:00-06:00 Taipei）
```
