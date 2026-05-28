---
name: broker-materials
description: 處理券商材料（PDF/DOCX/ZIP/TXT 與純文字 Telegram 訊息，個股/產業/晨報三類）。當 Kai 傳來券商材料或說「開始處理」時觸發。自動分類、萃取結構化欄位、寫入 Notion。
---

# broker-materials skill

## 觸發條件
- Kai 傳來 PDF/DOCX/ZIP 檔案（DM 或 Group -5290205228）
- Kai 說「開始處理」（批次模式）
- Group -5290205228 收到 PDF（S-3 常駐授權）
- Group -5290205228 收到純文字券商訊息（無檔案附件，內容含股票代碼/評等/目標價等 call memo 特徵）：S-3 常駐授權，依下方純文字訊息路徑執行

## 執行方式

### PDF / 檔案路徑
python3 skills/broker-materials/scripts/receive_telegram.py --test-file <檔案路徑>
腳本同時支援 .pdf / .docx / .doc / .zip / .txt / .md。

### 純文字訊息路徑
1. 取台北時間戳：TS=$(TZ=Asia/Taipei date +%Y%m%d_%H%M%S)
2. 確保目錄存在：mkdir -p /tmp/broker_text
3. 將訊息原文寫入 /tmp/broker_text/text_${TS}.txt（多則訊息每則獨立檔案，不合併）
4. 執行 python3 skills/broker-materials/scripts/receive_telegram.py --test-file /tmp/broker_text/text_${TS}.txt
5. 後續流程同 PDF 路徑

流程：
1. 檔案讀取（.pdf 走 pdf-reader skill；.txt/.md 直讀；.docx 用 python-docx；.zip 遞迴展開）
2. M2.7 分類（個股/產業/晨報）+ 萃取欄位
3. 依分類寫入 Notion：個股報告寫 broker_reports；產業報告寫 industry_reports；晨報寫 industry_reports（產業分類 multi_select=晨報，並注入訊息中偵測到的股票代碼到受惠標的）
4. Telegram 回報分類結果

信心等級處理：
- high（單一公司明確 + 目標價/EPS）→ 直接寫 Notion
- medium（單一主題但數字不完整）→ 寫 Notion + Telegram 詢問確認
- low → 仍寫入 Notion，core_view 加 [待確認] 前綴，並 Telegram 通知 Kai

關鍵檔案：
- scripts/receive_telegram.py（主腳本，已完整實作；支援 .pdf/.docx/.doc/.zip/.txt/.md）
- 依賴 skills/pdf-reader/（僅 PDF 路徑使用）

Secrets 需求：
- notion_key
- notion_broker_reports_db
- notion_industry_reports_db
- notion_event_calendar_db（stock_report 有法說會日期時寫入）
- minimax_api_key（分類用，thinking=off）
- telegram_bot_token / telegram_dm（回報用）
