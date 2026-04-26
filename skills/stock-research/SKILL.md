---
name: stock_research
description: 台股個股研究報告自動生成。當 Kai 傳入 /research <股票代碼> 加上法說會memo時觸發。自動抓取FinMind財務數字、籌碼面、TWSE年報，結合券商報告摘要與法說會memo，呼叫M2.7產出七章節研究報告草稿，寫入Notion research_pages並透過Telegram傳回摘要。
---

# Stock Research Skill — 台股個股研究報告自動生成

## 觸發方式
Kai 傳入：
/research 4755
法說會memo：[重點文字]

## 執行流程
1. fetch_financials.py — FinMind 抓月營收/季報/毛利/EPS（近8季）
2. fetch_chips.py — FinMind 抓三大法人/融資餘額（近30日）
3. fetch_annual_report.py — TWSE 下載最新年報PDF → pdf-reader轉文字 → M2.7萃取重點
4. fetch_broker_summary.py — 讀 Notion broker_reports DB 該股最近5份摘要
5. generate_report.py — M2.7產七章節草稿（thinking=off，輸入控制40K內）
6. write_notion.py — 寫入 Notion research_pages
7. Telegram 回傳一、二、五、六章節摘要

## 硬約束
- M2.7 thinking=off（事實萃取類）
- 單次輸入 ≤ 40K tokens
- 年報需先萃取重點再餵主流程（兩次M2.7呼叫）
- 抓不到年報PDF → Telegram通知Kai手動提供
