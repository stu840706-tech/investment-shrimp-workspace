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
| 立案理由 | rich_text | AI 生成摘要（见加入流程） |
| 狀態 | select | `追蹤中` / `已立案` / `已退出` / `觀望` |
| 下次驗證日 | date | 第一個有意義的檢查點（非90天預設值）|
| 退出日期 | date | 狀態 = `已退出` 時必填 |
| 退出原因 | rich_text | thesis 驗證 / 失效 / 價格偏離 等 |

## 加入追蹤流程（強制）

**嚴禁跳過。每一支股票加入前，都必須完成以下流程。**

### Step 1：分析 + 提案（我來做）

我必須先自己分析研究報告內容，提出三個提案供 Kai 選擇，格式如下：

**Q1：第一個檢查點什麼時候？**
- 我提案 A：YYYY-MM-DD（具體日期 + 理由）
- 我提案 B：YYYY-MM-DD（另一個有意義的時間點）
- 我提案 C：YYYY-MM-DD（第三個選項）

→ 等 Kai 回覆或選擇

**Q2：要追蹤的核心指標是什麼？**
- 我提案 A：具體指標 + 預期什麼數據才算 thesis 有效
- 我提案 B：另一個核心維度
- 我提案 C：第三個維度

→ 等 Kai 回覆或選擇

**Q3：什麼情況會讓你考慮退出？**
- 我提案 A：量化觸發條件（例如：月營收 YoY 跌破 20%）
- 我提案 B：另一個失效條件
- 我提案 C：第三個條件

→ 等 Kai 回覆或選擇

### Step 2：Kai 選擇或修正

Kai 從三個提案中選一個（A/B/C）或提出修改。我根據回覆記錄最終設定。

### Step 3：寫入 Notion

收到 Kai 回覆後，我執行寫入，確保：
- outcome_review_date = Kai 指定的日期
- 立案理由 = Thesis 摘要 + Kai 指定的核心指標 + 退出條件
- 狀態 = 追蹤中

### Step 4：口頭確認

寫入完成後向 Kai 確認：「已寫入，請到 Notion 確認」

---

## 執行

### 加入追蹤（對話式，互動環節）

不使用 `--review-days` 等預設值。透過對話完成上述四個步驟。

### 查詢當前清單（給其他 skill / workflow 呼叫）

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

## 已實作功能

- [x] FinMind 自動抓公司名稱
- [x] idempotency 檢查（已存在就更新而非新建）
- [x] list_active / list_all 查詢輔助函數

## 骨架狀態（已廢除，改用對話式流程）

~~預設90天~~ → 已改為「Q1 三提案讓 Kai 選」

## 紅線

- **嚴禁** 在沒有完成 Step 1-2（Q1/Q2/Q3 三個問題 + Kai 回覆）的情況下直接寫入 Notion
- outcome_review_date **絕對不能用預設90天**，必須是有意義的檢查點
- 如果 Kai 跳過不回答，我應該：「請給我你的選擇，才能寫入 Notion」

## 陷阱

- **誤加真實個股**:測試時用 `TEST.TW` / `9999.TW`,實際第一次加由 Kai 親自下指令
- **日期時區**:Notion date 用 ISO 8601 無時區,視為 Asia/Taipei 本地日
- **select option 必須先存在**:新增 `來源` 時要先 API 建 option