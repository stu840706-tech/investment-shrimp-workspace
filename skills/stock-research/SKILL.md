---
name: stock_research
description: 台股個股研究報告自動生成。當 Kai 傳入 /research <股票代碼> 加上法說會memo時觸發。自動抓取FinMind財務數字、籌碼面、股價技術面、TWSE年報，結合券商報告摘要與法說會memo，呼叫M2.7產出七章節研究報告草稿，寫入Notion research_pages並透過Telegram傳回摘要。
---

# Stock Research Skill — 台股個股研究報告自動生成

## 觸發條件
Kai 傳入以下任一格式時啟動：
- /research <股票代碼>
- /research <股票代碼> <法說會memo>
- 幫我做 <股票代碼> 的研究報告
- 研究 <股票代碼>

## 執行步驟

### Step 1：解析輸入
從 Kai 的訊息中萃取：
- `stock_id`：股票代碼（4位數字，例：4755）
- `memo`：法說會重點文字（若無則留空）
- `pdf_path`：年報 PDF 路徑（若 Kai 有傳檔案）

### Step 2：確認前置條件
詢問 Kai：

收到！準備開始產生 {stock_id} 研究報告。
請確認：
 1. 是否有法說會 memo？（若有請補充，若無直接回「無」）
 2. 是否有年報 PDF？（若有請傳檔案，若無回「無」，將跳過年報分析）

### Step 3：執行主流程
收到確認後執行：
```bash
cd ~/.openclaw/workspace/skills/stock-research/scripts

# 有年報 PDF
python3 research_main.py {stock_id} "{memo}" --pdf {pdf_path}

# 無年報 PDF
python3 research_main.py {stock_id} "{memo}" --skip-annual
```

### Step 4：回報結果
- 若出現 `NEED_PDF:{stock_id}`：通知 Kai 請提供年報 PDF
- 若出現 `FinMind HTTP 402`：通知 Kai FinMind 額度耗盡，台灣時間凌晨1點後重置
- 正常完成：直接輸出 `[TELEGRAM]` 後面的摘要內容給 Kai

## 硬約束
- M2.7 thinking=off（事實萃取類）
- 單次輸入 ≤ 40K tokens
- 不得捏造任何數字
- 所有輸出使用繁體中文

## 檔案位置
skills/stock-research/
├── SKILL.md # 本檔
├── scripts/
│ ├── research_main.py # 主流程入口
│ ├── fetch_financials.py # S1: 月營收/季報三率/EPS
│ ├── fetch_chips.py # S2: 三大法人/融資餘額
│ ├── fetch_price.py # S2.5: 股價/均線/技術面
│ ├── fetch_annual_report.py # S3: 年報PDF轉文字+萃取
│ ├── fetch_broker_summary.py # S4: Notion券商報告摘要
│ ├── generate_report.py # S5: M2.7產報告草稿
│ └── write_notion.py # S6: 寫入Notion
└── references/
  └── report_template.md # 七章節報告模板

## 狀態檔案（state/）
每次執行產生：
- `research_{stock_id}_financials.json` — 月營收/季報
- `research_{stock_id}_chips.json` — 三大法人/融資
- `research_{stock_id}_price.json` — 股價技術面
- `research_{stock_id}_annual.json` — 年報萃取（有年報時）
- `research_{stock_id}_broker.json` — 券商報告摘要
- `research_{stock_id}_report.json` — 最終報告（主產出）