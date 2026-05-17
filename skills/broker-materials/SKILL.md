---
name: broker-materials
description: 處理券商報告 PDF（個股/產業/晨報三類）。當 Kai 傳來 PDF 檔案，或說「開始處理」時觸發。自動分類、萃取結構化欄位、寫入 Notion。
---

# broker-materials skill

## 觸發條件
- Kai 傳來 PDF 檔案（DM 或 Group -5290205228）
- Kai 說「開始處理」（批次模式）
- Group -5290205228 收到 PDF（S-3 常駐授權）

## 執行方式
```bash
python3 skills/broker-materials/scripts/receive_telegram.py --test-file <pdf路徑>
```

流程：
 1. pdf-reader skill 轉文字
 2. M2.7 分類（個股/產業/晨報）+ 萃取欄位
 3. 依分類寫入 Notion：
    - 個股報告 → 📋 券商個股報告（broker_reports）
    - 產業報告 → 🏭 券商產業報告（industry_reports）
    - 晨報 → 跳過（low 等級）
 4. Telegram 回報分類結果

信心等級處理：
 - high（單一公司明確 + 目標價/EPS）→ 直接寫 Notion
 - medium（單一主題但數字不完整）→ 寫 Notion + Telegram 詢問確認
 - low（晨報/市場概況）→ 跳過，不寫 Notion

關鍵檔案：
 - scripts/receive_telegram.py（主腳本，已完整實作）
 - 依賴 skills/pdf-reader/

Secrets 需求：
 - notion_key
 - notion_broker_reports_db
 - notion_industry_reports_db
 - minimax_api_key（分類用，thinking=off）