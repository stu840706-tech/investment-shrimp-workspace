# active-context.md — 當前任務狀態(L2)

_每次任務切換時更新。此檔 ALWAYS READ(AGENTS.md Session 啟動 step 5)。_

## 當前進行中任務

任務:B1-B6 workspace 深度重構(執行中,目前到 B2 已完成)
狀態:等待進入 B3
最後更新:2026-04-20

## B1-B6 進度概觀

| 批次 | 內容 | 狀態 | commit |
|------|------|------|--------|
| B1 | Git 初始化 + GitHub remote | ✅ 完成 | 5e6530b |
| B2 | 部署 blueprint tar + 清孤兒檔 | ✅ 完成 | d36bdf8 |
| B3 | memory-archive cron 啟用 | 🚧 下一步 | — |
| B4 | Notion 9 DB 建立 + daily dashboard | ⏳ 等 B3 | — |
| B5 | pdf-reader 驗證 + Task 3/4/5/8 上線 | ⏳ 等 B4 | — |
| B6 | Task 1 升級 + 小坑修復 + cron 總檢查 | ⏳ 等 B5 | — |

## 下一步

B3:啟用 memory-archive daily cron(23:30)
- workflows/memory_archive.py 已部署(B2 tar 內)
- B3 只做:dry-run 驗證 → 實跑一次 → 註冊 cron job → commit
- 預估 1 小時

## B6 待處理小坑(已知,不急)

1. .gitignore 未擋 memory/YYYY-MM-DD.md L3 日誌 → B2 commit 時誤進 git
2. .gitignore 未擋 memory/news-fingerprints.md → B2 commit 時誤進 git

兩點實際沒害到執行(dry-run 不寫檔),B6 一起加進 .gitignore。

## 背景運行中的任務

目前仍運行的 cron:
- daily-scan(06:00)
- 其他既有 cron(B6 會做總檢查)

尚未註冊的 cron(各批次會加):
- memory-archive-daily(B3)
- daily-dashboard-page(B4)
- report-calendar-daily(B5)
- outcome-review-daily(B5)
- git-autocommit-daily(B6)
- rule-governance-weekly(B6,Task R)

## 使用須知

- Session 啟動時先讀本檔,若有「進行中任務」優先完成後再處理 Kai 新請求
- 切換任務前,把當前進度寫回本檔
- B1-B6 全部完成後,本檔改回 idle 模板
- 本檔目標字數 < 2000 字元;超過代表有任務沒收尾
- 跨對話銜接真相來源以 Kai 手上的 HANDOFF.md 為準(本檔只是快速查閱)
