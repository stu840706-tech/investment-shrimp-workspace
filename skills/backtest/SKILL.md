---
name: backtest
description: 台股策略回測。當 Kai 傳入 /backtest <策略名稱> 時觸發，從 FinMind 抓取歷史股價與財務資料，執行策略回測，輸出總報酬率、年化報酬、夏普比率、最大回撤、勝率，寫入 Notion backtest_results DB 並透過 Telegram 回傳摘要。
---

# Backtest Skill — 台股策略回測

## 觸發條件
- /backtest <策略名稱>
- 幫我回測 <策略描述>

## 執行步驟

### Step 1：確認策略參數
詢問 Kai：
收到回測請求！請確認：

策略名稱：
策略類型：技術面/基本面/混合
標的範圍：台股全市場/0050成分股/追蹤清單/特定產業
回測期間：起始日～結束日（例：2020-01-01～2024-12-31）
策略邏輯：（用自然語言描述買入/賣出條件）

### Step 2：執行回測
cd ~/.openclaw/workspace/skills/backtest/scripts
python3 backtest_main.py "<策略名稱>" "<策略類型>" "<標的範圍>" "<起始日>" "<結束日>" "<策略邏輯>"

### Step 3：回報結果
回測完成後 Telegram 回傳：
- 策略名稱與期間
- 總報酬率 / 年化報酬 / 夏普比率
- 最大回撤 / 勝率 / 交易次數
- 與大盤（0050）比較
- Notion 連結

## 內建策略模板
- `momentum`：動能策略（daily-scan 命中標籤數 ≥ N）
- `revenue_growth`：營收成長策略（YoY > N%，連續 M 個月）
- `margin_improvement`：毛利改善策略（毛利率 QoQ > N%）

## 硬約束
- 回測資料來源：FinMind API
- 不使用槓桿、不做空
- 每筆交易假設：以次日開盤價成交、手續費 0.1425%、交易稅 0.3%
- 全程繁體中文輸出

## 檔案位置
skills/backtest/
├── SKILL.md
└── scripts/
├── backtest_main.py # 主流程
├── fetch_universe.py # 抓取標的池（0050/全市場/追蹤清單）
├── fetch_history.py # 抓取歷史股價與財務資料
├── strategy_engine.py # 策略執行引擎
├── calc_metrics.py # 績效指標計算
└── write_notion.py # 寫入 backtest_results
