---
name: outcome-tracker
description: S-7 每日 23:00 掃 stock_tracking 的 outcome_review_date 到期項目,啟動 outcome review 並寫入 outcome_log。
---

# outcome-tracker — Thesis Outcome 驗證

## 何時使用

- **自動觸發**(預設):S-7 cron 每日 23:00 掃 `stock_tracking`,`outcome_review_date <= today` 的 row
- **手動觸發**:Kai 指定 review 某檔(覆蓋排程)

## 目的

立案 ≠ 結案。Kai 的研究品質要靠事後驗證提升。Outcome Tracking 做的是:

1. 90 天後(預設)回頭看當初 thesis
2. 實際價格/營收/事件是否符合預期
3. 判定 `thesis_verified` / `thesis_failed` / `inconclusive`
4. 寫入 `outcome_log` db + `knowledge/outcome_log.jsonl` 供跨時間分析
5. 更新 stock_tracking 狀態(`已立案` → `已退出`)

## 資料模型(outcome_log db)

| 欄位 | 類型 | 說明 |
|------|------|------|
| 個股代號 | title | |
| 立案日期 | date | |
| Review 日期 | date | |
| 立案理由(快照) | rich_text | |
| 實際走勢摘要 | rich_text | |
| 判定 | select | `thesis_verified` / `thesis_failed` / `inconclusive` |
| 教訓 | rich_text | 給下次立案參考 |
| 價格變化 % | number | 立案日 → review 日 |
| 大盤同期 % | number | 加權指數同期,比對 alpha |

## 執行

```bash
# cron 自動跑
python3 skills/outcome-tracker/scripts/outcome_review.py

# 手動指定
python3 skills/outcome-tracker/scripts/outcome_review.py --symbol 2330.TW

# dry-run(只列出到期項目,不寫 Notion)
python3 skills/outcome-tracker/scripts/outcome_review.py --dry-run
```

## 內部流程

```
1. 查 stock_tracking where outcome_review_date <= today and 狀態 != 已退出
2. 對每一筆:
   a. FinMind 抓立案日 → 今天的價格走勢 + 大盤同期
   b. 讀立案理由
   c. 呼叫 M2.7 (thinking=off) 判定 verified / failed / inconclusive
   d. 寫 outcome_log db + jsonl
   e. 更新 stock_tracking 狀態 → 已退出
3. Telegram 通知 Kai 今日 review 結果
```

## 驗證(B5 Step 4)

1. 手動建一個假 row:`outcome_review_date = 昨天`
2. 跑 `outcome_review.py`(非 dry-run)
3. 確認 outcome_log db 有新 row
4. 確認原 stock_tracking 狀態變 `已退出`
5. 確認 `knowledge/outcome_log.jsonl` 有 append 一筆

## 骨架狀態

當前為骨架,B5 實測後補:

- [ ] FinMind 價格抓取 + 大盤同期計算
- [ ] M2.7 判定 prompt(thinking=off)
- [ ] 寫 outcome_log db + jsonl 雙寫
- [ ] stock_tracking 狀態更新
- [ ] Telegram 通知格式

## 陷阱

- **價格要還原權值**(除權息):FinMind 有 adjust=True 參數
- **大盤基準**:預設加權指數(TAIEX),長期考慮改櫃買指數對小型股
- **review 日遇週末**:自動延到下個交易日;價格資料用最近交易日收盤
- **thesis 的模糊性**:M2.7 判定要降溫度,保守派:寧可 `inconclusive` 也不硬套結論
- **outcome_log jsonl 是 append-only**:不改舊紀錄,錯了補新的一筆 with reason
