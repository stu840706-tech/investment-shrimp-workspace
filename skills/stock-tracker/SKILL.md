---
name: stock-tracker
description: 管理 Notion stock_tracking db(追蹤中個股樞紐)。加入、更新狀態、計算 outcome_review_date(立案日+90 天)。
---

# stock-tracker — 追蹤中個股管理

## 何時使用

- Kai 手動說「把 XXXX 加入追蹤」
- Task 1 新聞 signal=high 觸發自動加入(B6 後)
- Task 4 券商報告結論建議追蹤
- 查詢當前追蹤清單(給 Task 1 / 3 / 8 當 filter)

## 位置

樞紐 db。Task 1 加權、Task 3 事件對比、Task 8 outcome review 都吃這張表。

## 資料模型(Notion stock_tracking db)

| 欄位 | 類型 | 說明 |
|------|------|------|
| 個股代號 | title | 例 `2330.TW` |
| 公司名稱 | rich_text | 例 `台積電` |
| 立案日期 | date | YYYY-MM-DD |
| 來源 | select | `manual` / `news_signal` / `scan_anomaly` / `broker_report` |
| 立案理由 | rich_text | Kai 手動或 AI 生成摘要 |
| 狀態 | select | `追蹤中` / `已立案` / `已退出` / `觀望` |
| outcome_review_date | date | 預設立案日 + 90 天,可 Kai 覆蓋 |
| 退出日期 | date | 狀態 = `已退出` 時必填 |
| 退出原因 | rich_text | thesis 驗證 / 失效 / 價格偏離 等 |

完整 schema 見 `02_NOTION_SCHEMA_PLAN_v2.md`。

## 執行

### 加入追蹤

```bash
python3 skills/stock-tracker/scripts/add_tracking.py \
    --symbol 2330.TW \
    --source manual \
    --reason "法說會指引上調,毛利率擴張"
```

選用參數:

- `--review-days N`:outcome_review_date 天數(預設 90)
- `--name "台積電"`:公司名稱(未填則從 FinMind 抓)

### 查詢當前清單(給其他 skill / workflow 呼叫)

```python
from skills.stock_tracker.scripts.list_tracking import list_active
codes = list_active()  # 回傳 ["2330.TW", "2317.TW", ...]
```

## 驗證(B5 Step 3)

1. 測試用假標的 `TEST.TW`(EXECUTION_GUIDE 時機 3)
2. 執行 `add_tracking.py --symbol TEST.TW --source manual --reason "test"`
3. Notion stock_tracking db 有新 row
4. outcome_review_date = 立案日 + 90 天
5. 測試完 Kai 手動刪除該 row

## 骨架狀態

當前為骨架,B5 實測後補:

- [ ] FinMind 自動抓公司名稱
- [ ] 加入時檢查是否已存在(idempotency)
- [ ] 重複加入時更新立案理由而非建新 row
- [ ] list_active / list_all 查詢輔助函數
- [ ] Telegram 通知「已加入追蹤」

## 陷阱

- **誤加真實個股**:測試時用 `TEST.TW` / `9999.TW`,實際第一次加由 Kai 親自下指令
- **日期時區**:Notion date 用 ISO 8601 無時區,視為 Asia/Taipei 本地日
- **select option 必須先存在**:新增 `來源` 時要先 API 建 option
