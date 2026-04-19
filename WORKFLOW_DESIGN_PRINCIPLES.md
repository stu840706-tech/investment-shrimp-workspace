# Workflow 設計原則

_基於 OpenClaw 官方文件、資深用戶實踐、軟體工程原則整理_
_最後更新：2026-04-12_

---

## 一、核心架構原則

### 1. 三層記憶系統（借鑒 Shawn Harris 的認知架構）

```
┌─────────────────────────────────────────────────────┐
│  STRATEGIC MEMORY (MEMORY.md)                       │
│  - 身份、價值觀、長期學習、原則                      │
│  - 永久保存，時常更新                               │
├─────────────────────────────────────────────────────┤
│  OPERATIONAL MEMORY (active-context.md)             │
│  - 當前專案狀態、截止日期、承諾事項                  │
│  - 每次 session 開始時必定讀取                       │
├─────────────────────────────────────────────────────┤
│  TACTICAL MEMORY (memory/YYYY-MM-DD.md)             │
│  - 每日事件、原始筆記、工作日誌                      │
│  - 定期歸檔（30天以上）                             │
└─────────────────────────────────────────────────────┘
```

**資訊流動方向：**
- 向上：每日日誌 → 當前上下文 → 長期記憶（蒸餾後）
- 向下：目標 → 任務 → 行動（拆解後）

### 2. Input Gating — 什麼值得記憶

| 優先級 | 類型 | 目的地 | 例子 |
|--------|------|--------|------|
| P0 | 關鍵 | active-context.md | 截止日期、承諾、憑證 |
| P1 | 操作 | active-context.md | 專案狀態、決策、設定 |
| P2 | 上下文 | daily YYYY-MM-DD.md | 會議筆記、對話摘要 |
| P3 | 短暫 | Session 即可 | 偵錯步驟、一次性查找 |

### 3. 輸出 Gating — 何時觸發什麼記憶

| 情境 | 載入什麼 |
|------|---------|
| Session 開始 | active-context.md（必定）|
| 執行任務 2（財務）| + financial-config |
| 執行任務 3（報告追蹤）| + tracking-config |
| 模型切換後 | active-context.md + runbooks |

---

## 二、Session 與工作階段管理

### 4. 模型切換 Protocol（GP-007）

當模型切換（config 變更、/new、/reset）：
- 第一件事：讀取 `memory/active-context.md`
- 檢查「Session Handoff」區段，確認未完成的工作
- 如果 active-context.md 超過 24 小時未更新，執行：`node engine.js refresh`

### 5. Session 結束 Protocol

每次結束有意義的 session 前：
1. 執行：`node engine.js sync`
2. 更新今日日誌（如果有事發生）
3. 如果學到新流程，更新/建立 runbook
4. 如果有教訓，考慮提升到 MEMORY.md

### 6. Heartbeat 整合

每次 Heartbeat 最優先：
```bash
# 記憶檢查（永遠最優先）
node memory-engine/scripts/engine.js alert
# → P0：立即修復
# → P1：記下，稍後處理
# → 無警报：繼續其他檢查
```

---

## 三、工作流設計原則

### 7. Workflow 結構（n8n 風格對應）

| n8n 概念 | OpenClaw 對應 | 說明 |
|---------|--------------|------|
| Trigger | cron / heartbeat | 固定時間或事件觸發 |
| Node | 獨立 Python/Shell 腳本 | 每個腳本只做一件事 |
| Flow | 檔案/JSON 狀態傳遞 | workflow 之間透過檔案協調 |
| Condition | AI 決策 | 符合條件才執行 |
| Error Handle | try/catch + 重試 | 失敗優雅重試，不卡住 |
| Output | Telegram / Notion / GitHub | 輸出到確定的目的地 |

### 8. Workflow 獨立性原則

每個 Workflow 是完全獨立的腳本，不直接調用其他 workflow，而是透過狀態檔案協調：

```
# daily-news 發現感興趣的公司，寫入待處理清單
echo "2330,TW,半導體,新聞線,2026-04-12" >> memory/pending-research.csv

# daily-tracking workflow 定期檢查這個清單，決定是否推送
```

好處：
- 每個 workflow 可以單獨測試、修改、修復
- 一個失敗不會影響其他的
- 擴充時只是新增 workflow，不改現有邏輯

### 9. 錯誤處理與重試原則

```python
def run_with_retry(func, max_retries=3, base_delay=60):
    for i in range(max_retries):
        try:
            return func()
        except APIRateLimitError:
            if i == max_retries - 1:
                notify_kai(f"任務失敗，已重試 {max_retries} 次: {e}")
                raise
            sleep(base_delay * (2 ** i))  # 指數退避
        except NetworkError:
            if i == max_retries - 1:
                notify_kai(f"網路錯誤: {e}")
                raise
            sleep(base_delay * (i + 1))
```

**觸發重試的條件：**
- API rate limit（等額度重置）
- 網路瞬斷（等一下再試）
- 暫時性服務不可用

**不重試，直接匯報：**
- 認證失敗（憑證過期等）
- 資料格式錯誤（需要人工修正）
- 系統性失敗（回應 500 錯誤）

### 10. 穩定運行關鍵原則

```
1. 狀態持久化：每一個中斷點的狀態都要寫入磁碟
2. 等價性：workflow 開始和結束時的狀態要一致
3. 失敗隔離：一個 workflow 失敗不能影響另一個
4. 可追蹤：任何時間都要能回答「現在跑到哪了」
5. 可重現：失敗的 workflow 可以從上次中斷處繼續
```

### 11. 任務狀態生命週期（借鑒他人教訓）

每個立案追蹤的任務都必須有明確的狀態：

```
pending（初步觀察）
    ↓
active（正式立案，需持續驗證）
    ↓
validated（已取得明顯正向驗證）
    ↓
excluded（被反證或失去研究價值）
    或
completed（已走完主要驗證週期）
```

**狀態轉換規則：**
- `pending → active`：發現具體的 Structural Thesis 依據
- `active → validated`：關鍵 Binary 指標接連達標
- `active → excluded`：被反證或關鍵假設被打破
- `validated → downgraded`：強度下降，退回觀察
- 任何階段 → `completed`：已達到預定目標或失去追蹤價值

### 12. 異常處理狀態機（借鑒他人教訓）

當任務執行失敗或遇到資料缺失：

```
pending（掛起）：標註缺失科目，嘗試執行補救腳本
    ↓
retry（重試）：僅限網路錯誤，最大重試次數 = 1
    ↓
escalate（上報）：若補救失敗，立即通知 Kai，嚴禁腦補
    ↓
halt（熔斷）：若發現邏輯衝突（如兩來源數據矛盾 >50%），停止該項目分析並標註 [🚨 數據衝突待核]
```

**核心原則：當資料缺失時，先搜尋再標註。嚴禁在未嘗試找答案前就給出結論。**

---

## 四、軟體工程原則應用

### 11. 測試原則（對應你給我的第 4 項能力）

每個 workflow 腳本都要能：
- **單獨執行**：`python workflows/daily-financial.py` 不需要任何外部狀態
- **可驗證**：有明確的成功/失敗標準
- **有日誌**：每個步驟都寫入執行日誌
- **可回測**：邏輯可以被驗證

```python
# 測試框架範例
def test_financial_workflow():
    # Given: 模擬的 FinMind API 回應
    # When: 執行財務掃描
    # Then: 輸出應包含「異常營收成長 >20%」的公司列表
    pass
```

### 12. Code Review 原則（對應第 3 項能力）

每個新 workflow 或重大修改前：
- 自己先做 code review（對照 SKILL.md 的步驟）
- 確認：網路請求目標明確、沒有硬編碼憑證、錯誤處理完善
- 通過後才能部署到實際執行

### 13. 功能測試 + 穩定性測試

新 workflow 部署前：
1. **功能測試**：用已知資料輸入，確認輸出正確
2. **穩定性測試**：用邊界條件（大資料量、網路延遲）測試
3. **加壓測試**：連續執行 10 次，確認沒有記憶體洩漏

### 14. AI 幻覺防範原則

AI 擅長生成流暢但可能錯誤的內容。關鍵防線：

```
1. 所有數字類輸出（營收、EPS、股價）必須有source URL
2. 所有推測必須標記「推測」並說明假設前提
3. 當多個來源數字矛盾時，標記為「需要驗證」而不是猜
4. 涉及金錢/交易的判斷，最後決策權永遠留給人類
```

---

## 五、具體 Workflow 設計

### 15. 任務優先順序（已規劃）

| 優先 | 任務 | 理由 |
|------|------|------|
| 1 | 任務 2：每日財務掃描 | 數字最結構化，最容易自動化 |
| 2 | 任務 3：報告追蹤 | 時程管理是 AI 強項 |
| 3 | 任務 1：新聞摘要 | 回饋快，節省最多時間 |
| 4 | 任務 5：追蹤清單更新 | 整合 1-4 的輸出 |
| 5 | 任務 4：券商報告處理 | 依據你傳來的檔案 |
| 6 | 任務 8：程式交易策略 | 平行开发 |
| 7 | 任務 6：個股研究報告 | 需要較多人工 |
| 8 | 任務 7：書籍摘要 | 長期投資，優先級最低 |

### 16. 每日排程建議

```
06:00 ┌─ FinMind：前日營收出爐 → 異常篩選 → Telegram
07:30 ┌─ 新聞摘要（台股宏觀 + 產業）→ 事實過濾 → Telegram
08:00 ┌─ 今日提醒（法說會、重大發布）→ Telegram
09:00 ┌─ 任務 5：追蹤清單更新（寫入 Notion）
12:00 ┌─ 午盤：FinMind 日內監控（可選）
18:00 ┌─ 收盤總結 → Notion 更新
─────────────────────────────────────────
隨時 ┌─ 收到 Kai 傳來的報告 → 處理 → 存入 Notion
```

### 17. Workflow 目錄結構

```
workflows/
├── daily-news.py              # 新聞掃描（FinMind/Brave）
├── daily-financial.py          # 財務資料掃描
├── report-tracking.py          # 報告發布追蹤
├── process-report.py           # 處理 Kai 傳來的報告
├── process-book.py             # 處理書籍文字檔
├── update-tracking.py          # 整合輸出到 Notion
├── trading-backtest.py         # 策略回測
└── generate-research.py        # 個股研究報告

memory/
├── runbooks/                   # 標準作業程序
│   ├── how-to-finanmind.md
│   ├── how-to-tpex-api.md
│   ├── how-to-notion.md
│   └── how-to-github-release.md
├── pending-research.csv        # 待研究清單
├── report-calendar.json        # 報告發布日曆
└── active-context.md           # 當前狀態（見前述架構）
```

---

## 六、Gating Policies（從失敗中學習的規則）

每個政策都是實際失敗過後才制訂的：

| 政策 | 觸發時機 | 行動 |
|------|---------|------|
| GP-001 | 建立 cron job 後 | 驗證存在，儲存 ID |
| GP-002 | API 呼叫失敗 | 記錄錯誤類型，更新 API 狀態 |
| GP-003 | FinMind 額度快用完 | 停止，等額度重置 |
| GP-004 | Session 結束 | 執行 sync，更新日誌 |
| GP-005 | 建立 cron job 前 | 檢查是否已存在，避免重複 |
| GP-006 | 收到報告檔案 | 立即處理，更新 Notion |
| GP-007 | 模型切換後 | 讀取 active-context + runbooks |
| GP-008 | 學到新流程後 | 建立/更新 runbook |
| GP-009 | P0 等級事件 | 立即更新 active-context |
| GP-010 | 每週 | 執行 decay audit，歸檔舊日誌 |

---

## 七、擴充性與維護

### 18. 擴充時的檢查清單

新增 workflow 前，回答這些問題：
```
1. 這個 workflow 依賴哪些外部服務？
2. 失敗了會影響其他 workflow 嗎？
3. 它的狀態存檔在哪？
4. 如何驗證它正常運行？
5. 它會產生什麼輸出？誰來消費？
6. 它的觸發條件是什麼（時間？事件？）？
```

### 19. 監控與告警

每次 Heartbeat 檢查：
- FinMind API 剩餘額度（<100 時告警）
- 當前任務執行狀態
- 待處理報告數量（>5 時告警）
- 是否有任何 workflow 連續失敗（>3 次時告警）

---

## 八、與 n8n 的關鍵差異

| n8n | OpenClaw Workflow |
|------|-------------------|
| 視覺化節點設計 | 文字定義（.py 腳本）|
| 實時監控介面 | 透過日誌和 Telegram |
| 事件驅動 | 主要靠 cron/heartbeat |
| 內建錯誤處理 | 需要自己寫 |
| 付費托管版 | 完全免費，自架 |
| 適合 IT 人員 | 適合會寫程式的人 |

**OpenClaw 的優勢：**
- 完全可控，無供應商鎖定
- 免費，無使用量費用
- 與 AI 無縫整合（自然語言處理）

**OpenClaw 的劣勢：**
- 需要寫程式碼
- 監控界面不如 n8n 直觀
- 需要自己處理錯誤和重試

---

_這個文件是動態的。隨著我們發現新的失敗模式和更好的實踐，這個文件會持續更新。_
