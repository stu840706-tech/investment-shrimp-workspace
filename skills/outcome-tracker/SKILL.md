---
name: outcome-tracker
description: 持股證偽與升級檢查器（v3，2026-10-25）。平日 23:00 由 crontab 自動執行：每天比對 📈 個股追蹤的【數字條件】（任一成立發 🔴）與【升級條件】（全部成立發 🟢）；只有出現新月營收、新季報或驗證日到期時，才用 MiniMax-M3 判定 thesis。
---

# outcome-tracker — 持股證偽與升級檢查（v3）

## 不要做的事（agent 必讀）

- **不要手動執行正式模式**（不帶 `dry`）：它會寫 Notion、改狀態檔、發 Telegram。crontab 每個平日 23:00（台北）會自動跑，不需要人工補跑。
- 測試只能用 `dry`。
- **舊參數已不支援**：`--symbol 2330.TW` 會被忽略，結果變成「全部持股正式跑一次」。代碼一律寫 4–6 位數字，不加 `.TW`。
- 不要修改 📈 個股追蹤 DB 的內容（含【數字條件】與【升級條件】）：由 Claude 依 Investment Context 維護。

## 執行

```bash
# crontab（UTC）：0 15 * * 1-5  → 台北平日 23:00
python3 skills/outcome-tracker/scripts/outcome_review.py

# 只讀試跑：照常抓資料、呼叫模型，但不寫 Notion、不寫狀態檔、不發 Telegram
python3 skills/outcome-tracker/scripts/outcome_review.py dry

# 只讀試跑單一檔
python3 skills/outcome-tracker/scripts/outcome_review.py dry 8103
```

輸出最後一行：`RESULT=OK`（正式）／`RESULT=DRY_DONE`（試跑）／`RESULT=CRASH`（崩潰，正式模式會發 Telegram）。

## 兩層檢查

1. **數字條件（每天，程式比對，不經模型）**：用 FinMind 最新已公布資料比對兩種條件。
   - 【數字條件】（證偽）：任一項成立 → 🔴。
   - 【升級條件】（事前劇本）：全部成立 → 🟢，訊息附上「→」後面寫的動作；還沒全部成立時，有新資料那天在 thesis 檢查訊息列 🎯 進度（例：2/3）。
   - 只有**新一期資料**觸發才通報；用舊資料就成立的（第一次檢查、條件剛新增或改寫）只列 ℹ️ 基準。同一條件同一期只報一次。
2. **thesis 判定（MiniMax-M3，只在有新資料時跑）**：出現新月營收、新季報，或「下次驗證日」到期時，才把 thesis、反證條件、升級條件進度、近 6 個月營收 YoY、近 4 季毛利率／營益率／EPS／營業費用、近 45 天券商報告交給模型。股價只當背景，**不當證據**；數字條件已觸發時，程式不允許判「已驗證符合」。

「下次驗證日」一律設為下一個月營收公布日（下個 11 日），不由模型決定。

## 條件語法

項目用 `｜` 分隔；比較符號 `<`、`<=`、`>`、`>=` 都可以（全形 ＜＞≦≧ 也認得）；金額單位一律是**億元**。

| 寫法 | 意思 |
|---|---|
| `營收YoY<15` | 最新一個月營收 YoY（%）低於 15 |
| `營收YoY連續2月<20` | 最近 2 個月 YoY 都低於 20（「連續N月」只能用在月資料） |
| `月營收>=3.9億` | 最新一個月營收至少 3.9 億元 |
| `毛利率<36` | 最新一季**單季**毛利率（%）低於 36 |
| `營益率<15` | 最新一季**單季**營業利益率（%）低於 15 |
| `EPS>=2.1` | 最新一季**單季** EPS 至少 2.1 元 |
| `營業費用<=2.0億` | 最新一季**單季**營業費用不超過 2.0 億元 |

- **【數字條件】**寫在「反證條件」第一行：`【數字條件】毛利率<36｜營收YoY<15`。
- **【升級條件】**寫在「下次驗證內容」任一行：`【升級條件】毛利率>=38｜EPS>=2.1｜營業費用<=2.0億 → 部位上限 1,600→2,500 股（3Q26 財報）`。季資料項目必須都是同一個最新季別才算成立。

寫法認不得的項目會列在 Telegram 的 ⚠️，不會被靜默忽略。

## 資料來源

- 月營收：FinMind `TaiwanStockMonthRevenue`（YoY 由程式用去年同月自算）。
- 季報：FinMind `TaiwanStockFinancialStatements`，`Revenue`／`GrossProfit`／`OperatingIncome`／`OperatingExpenses`／`EPS` 都是**單季值**（不是累計）。2026-10-25 用瀚荃 2Q26 對帳：營業費用 2.28 億、營益率 16.2%，與財報一致。
- 券商報告：Notion 📋 券商個股報告，近 45 天、最多 5 筆。

## 寫入位置

- Notion outcome_log：`review_id`（代碼_日期）、股票代碼、review_date、驗證狀態。
- `knowledge/outcome_log.jsonl`：append-only，不改舊紀錄；含 verdict、summary、已成立反證、下一步要看的、數字觸發、升級條件進度、模型名。
- 📈 個股追蹤：Outcome狀態、最近Outcome結果、下次驗證日。
- `knowledge/outcome_state.json`：每檔已處理的月營收期別、季報期別、已通報的證偽觸發（`alerted`）與升級觸發（`up_alerted`）。刪掉它等於所有持股重新建立基準。

## 失敗處理

- FinMind 抓取失敗、模型失敗、Notion 寫入失敗：列在 Telegram 的 ⚠️，該檔狀態不改、期別不前進，隔天自動重試。
- 不用股價 Alpha 推估結論。

## 回滾

- v2 備份：`~/.orv3/outcome_review.py.v2`（md5 05d5beb0）與 `~/.orv3/SKILL.md.v2`；部署工具 `~/mm_probe/orv3.py rollback 通關碼` 會還原兩檔。
- git：v2 為 commit `5b896d6`（程式）、`682805d`（本檔）。
