# Active Context
_最後更新：2026-04-29_

## 當前狀態
B1-B8 + Task 4/6/7/8 + Dreaming + B6增強 全部完成。
後期維護階段，無進行中的重構任務。

## 最近完成（2026-04-29）
- news_phase3.py dangling job 修正
- show_manual.py 重寫 + cron 時間修正
- AGENTS/TOOLS/HEARTBEAT 修正（移除重複 read bootstrap）
- Bearer→x-api-key 修正（3支腳本）
- news-fingerprints.md 30天 eviction
- news_publisher 多個 bug 修正（時間/晨晚報/代碼/防重複）
- Notion 命名改善（hub + 11個DB）
- daily_dashboard 兩個 bug 修正
- broker-materials SKILL.md 更新 + AGENTS S-4 明確化
- approvalPolicy 設定
- news_publisher 防重複發送機制（state/news_brief_sent.json）

## 暫緩任務
- news_aggregator cluster dedup 改善（同一 story 重複出現）
- T3 批次大小重跑
- NotebookLM 整合
- Task 4 自動化（待 port 8888 開放）

## 無進行中任務
