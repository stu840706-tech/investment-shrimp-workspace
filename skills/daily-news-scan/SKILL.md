---
name: daily-news-scan
description: 包裝現有 workflows/news_aggregator.py,讓 agent 在被問「今天新聞」「掃一次新聞」時呼叫對應 workflow 並彙整結果。
---

# daily-news-scan — 每日新聞掃描

## 何時使用

- Kai 問「今天有什麼新聞」「幫我掃一次」「追蹤清單有沒有新聞」
- Daily dashboard 產生時,讀取本日 `news_db` 的高 signal 條目

## 與現有 workflow 的關係

**這個 skill 不產新 scripts**,它是 agent 和現有 Python workflow 之間的橋:

- 現有主腳本:`workflows/news_aggregator.py`
  - cron 06:00 / 18:00 自動跑
  - ingest → dedupe → 分類 → score → 寫 Notion news_db
- B6 會升級:Fact 三層預審、強制第二來源驗證、追蹤清單加權

## 執行

### 手動觸發一次掃描

```bash
cd ~/.openclaw/workspace
python3 workflows/news_aggregator.py
```

### 讀取今日結果給 Kai

當 Kai 問「今天新聞」,agent 要做的:

1. 先檢查 Notion news_db 今日有無寫入(避免重抓)
2. 沒有 → 執行 `workflows/news_aggregator.py`
3. 有 → query 今日 signal=high 條目
4. 依優先順序排序:
   - P1:追蹤中個股(交叉 stock_tracking)
   - P2:大盤事件
   - P3:其他 high signal
5. 回覆 Kai 依 SOUL.md 格式(先結論、再依據、不鋪陳)

## 紀律

依 P-011 事實三層 + P-012 強制第二來源(B6 上線):

- Layer 1 硬事實可進分析
- Layer 2 觀點標「觀點非事實」
- Layer 3 純敘事跳過,不浪費 token
- signal=high 單一來源 → 標「待驗證」
- 追蹤中個股的新聞優先進 Top 10(即使 signal 不是最高)

## 不做什麼

- 不自己重寫 news ingest 邏輯(那是 workflows/news_aggregator.py 的事)
- 不跨 session 維護 state(workflow 自己管 state/news_state.json)
- 不直接對外 Telegram push(這由 workflow 或 daily_dashboard 負責)

## 陷阱

- **重複掃描**:call workflow 前先檢查 state/news_state.json 的 last_run_ts
- **M2.7 context overflow**:讀回的新聞清單不要全塞 prompt,取 Top 10 即可
- **對 Kai 回報**:先結論(例「今日追蹤清單中 2 檔有高 signal 新聞」),不要列出所有低 signal
