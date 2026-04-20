# TASK_PRIORITIES.md — 任務優先順序

_AGENTS.md Session step 8 會查此檔。規劃實作順序或回應「現在該做什麼」時參考。_

## 8 個核心任務總覽

| # | 任務 | 觸發類型 | 狀態 | 對應 skill / workflow |
|---|------|----------|------|----------------------|
| 1 | 每日新聞掃描 + 分類 | cron 06:00 / 18:00 | 已上線,B6 升級 | `workflows/news_aggregator.py` + `daily-news-scan` skill |
| 2 | 每日營收掃描(S-1) | cron 06:00 | 已上線 | `workflows/scan_*.py` |
| 3 | 重大事件日曆(S-2) | cron 09:00 | B5 上線 | `report-calendar` skill |
| 4 | 券商材料處理(S-3) | Telegram listener | B5 上線 | `broker-materials` skill |
| 5 | 個股追蹤(樞紐) | Kai 手動 + 自動觸發 | B5 上線 | `stock-tracker` skill |
| 6 | 個股研究報告 | Kai 請求觸發 | 骨架在 tar,深度實作延後 | (後期) |
| 7 | 書籍筆記 | Kai 上傳觸發 | 深度實作延後 | (後期) |
| 8 | Outcome Tracking(S-7) | cron 23:00 | B5 上線 | `outcome-tracker` skill |

## 實作順序(HANDOFF 已決策)

```
Task 5(樞紐) → Task 8(Outcome) → Task 4(券商) → Task 3(事件)
  → Task 1(新聞升級) → Task 6/7/8 深度(後期)
```

**為什麼是這個順序**

- **Task 5 先**:stock_tracking 是所有下游 task 的過濾鎖(Task 1 加權、Task 3 相關事件、Task 8 outcome_review 都吃這張表)
- **Task 8 緊接**:立案後如果沒有 Outcome Tracking,立案品質無從檢討
- **Task 4 再來**:券商材料是研究輸入,先有 Task 5 追蹤清單才知道哪些材料要重點處理
- **Task 3 之後**:事件日曆讓追蹤中個股的關鍵日期(法說會、財報)進視野
- **Task 1 升級最後**:升級前先讓上面四個運作一陣子,觀察需要哪些輸入增強

## 現在是哪個批次

這是重構 B1-B6 批次的一部分。詳細批次規格見 `05_CHANGE_CHECKLIST_v2.md`(workspace 外,Kai 手上)。

| 批次 | 內容 | 狀態 |
|------|------|------|
| B1 | Git 初始化 + GitHub remote | ✅ 2026-04-19,commit 5e6530b |
| B2 | 部署 blueprint + 清孤兒檔 | 🚧 進行中 |
| B3 | memory-archive cron | ⏳ |
| B4 | Notion 9 DB 建立 | ⏳ |
| B5 | pdf-reader 驗證 + Tasks 3/4/5/8 上線 | ⏳ |
| B6 | Task 1 升級 + 小坑修復 | ⏳ |

_完成批次時同步更新 HANDOFF.md 底部「執行進度」區。_

## 暫緩清單(B1-B6 完成後再處理)

這些提過但決定先不做:

- NotebookLM 整合(產業報告 → Google Drive API)
- T3 batch size 重跑測試(max_tokens=8192 甜蜜點)
- Task 6 深度實作(個股研究報告 Phase 2-4)
- Task 7 深度實作(book_concepts 拆分 db)
- Task 8 策略回測 skill(骨架在 tar)
- Dreaming(記憶自動蒸餾)啟用
- 舊 `openclaw-config` GitHub repo 清理

**不要主動提起這些**,Kai 會在適當時機回來處理。

## 當收到新任務時

先問:

1. 這能不能歸到 Task 1-8 之一?
2. 是當前批次範圍內嗎?
3. 若都不是 → 加到暫緩清單,完成當前批次再談

**不要為了單一新需求臨時擴張重構範圍。**

---

_上次更新:2026-04-19_
