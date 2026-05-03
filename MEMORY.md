# MEMORY.md — Long-Term Memory

*只在主 session 讀取，不在群組/公開 session 中載入*

## Kai 的投資方法論

- **核心差異化：** 法說會追蹤（多數散戶不看）
- **驗證邏輯：** 月營收異常 → 季報三率驗證 → 法說會確認 thesis
- **篩選起點：** 系統性下跌中的強勢個股（產業跌但個股漲）
- **賣出紀律：** thesis 失效或法說會數字不符預期就出場

## 系統關鍵決策

- **2026-04-12：** 確定架構：OpenClaw + Notion + GitHub
- **2026-04-12：** 任務優先順序：財務掃描 > 新聞 > 報告追蹤 > 其他
- **2026-04-18：** daily-scan.py 與 daily-notion.py 拆分為獨立 workflow（P-007）
- **2026-04-18：** Token 集中管理在 config/secrets.json

## 已知 Bug 與修復記錄

- TWSE t187ap05_L revenue 單位是**千元**（不是元），backfill 過去錯誤地存了元值
- revenue_history.json 已在 2026-04-18 執行三輪標準化修正
- Notion rich_text 上限 2000 字元/block，to_rich_text() 已切分處理
- scan_results 的 flags 欄位在 detail 字串裡（不在 flags array）
- **exec + cat 會截斷大檔案**：超過約100行會變成 `⚠️ [... middle content omitted ...]`，從此只用 `read` 工具讀檔

## API 重要資訊

- FinMind: 600次/小時，590次時停止等待；token 建立於 2026-04-12，需定期輪替
- TWSE OpenAPI: 3次/5秒，民國年格式（YYYMMDD）
- Notion rich_text: 每個 block 最多 2000 字元
- MiniMax: 透過 /anthropic/v1 端點呼叫（Anthropic 相容格式）

## 偏好設定

- 程式碼風格：Python，簡潔，有 type hint，錯誤有 fallback
- 報告格式：先結論，再數字，最後 next action
- 通知原則：有實質內容才發 Telegram，例行無異常 → HEARTBEAT_OK
- 禁止：假設知道、模糊估計、未查證的數字
- **檔案讀取**：大檔案（>100行）一律用 `read` 工具，禁用 exec+cat

## 投資組合（追蹤中的 thesis）

*目前未有正式 active 追蹤記錄，待任務 5 建立後填入*

---
*更新方式：每次 session 結束有重要發現時，蒸餾進這裡*
