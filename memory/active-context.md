# Active Context
_最後更新：2026-05-10_

## 當前狀態
B1-B8 + Task 4/6/7/8 + Dreaming + B6增強 全部完成。
後期維護階段，無進行中的重構任務。

## 新聞管線重大 bug（已知問題）

### 問題：UTC vs 台北日期錯位（2026-05-04 發現）
cron UTC 23:00 執行 pipeline，`datetime.now().strftime("%Y%m%d")` 取 **UTC 日期**，而非台北日期。
當 UTC 日與台北日不同時，aggregator 找不到 raw 檔案，pipeline 失敗。

**觸發條件：**
- UTC 23:00 = 台北 07:00（次日晨報）
- 若 UTC 日 ≠ 台北日，則 `utc_date` 落後一天

**繞過方式：** aggregator 支援 `python3 workflows/news_aggregator.py <hour> <date>` 手動指定日期
- 例如：`python3 workflows/news_aggregator.py 07 20260510`

**待修復：**
- [ ] `news_pipeline.py`：UTC date 改台北 date（`datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y%m%d")`）

## 執行紀錄
- 2026-05-10 23:22：手動指定 `07 20260510` 執行成功，232 raw → 231 unique → 9 new news

## 暫緩任務
- news_aggregator cluster dedup 改善（同一 story 重複出現）
- T3 批次大小重跑
- NotebookLM 整合
- Task 4 自動化（待 port 8888 開放）
- 晨晚報數量差異過大問題
- Pending 信號過多（150/233）

## 無進行中任務
