# HEARTBEAT.md - 投資蝦心跳檢查清單

_版本：v0.2.0（更新於 SYSTEM_ARCHITECTURE.md 建立後）_

---

## 心跳觸發條件

- **頻率**：每 30 分鐘
- **活躍時段**：06:00 - 23:59（Taipei Time）
- **沉默時段**：00:00 - 05:59（Taipei Time）→ 回覆 HEARTBEAT_OK

---

## 檢查清單（按順序執行）

### 1. API 額度檢查（每次都要）
```
檢查 FinMind API 剩餘額度
- 若 <100 次：立即通知 Kai「FinMind 額度快用完」
- 若 100-200 次：標記在日誌
- 若正常：不需特別動作
```

### 2. 待處理任務檢查
```
檢查 state/ 目錄下的狀態檔
- pending-notifications.json：是否有待發送通知
- revenue-tracker.json：是否有異常營收待確認
- report-tracker.json：是否有報告待處理
```

### 3. Workflow 失敗檢查
```
檢查 workflows/ 的執行日誌
- 連續失敗 >3 次：立即通知 Kai
- 單次失敗：標記，稍後重試
```

### 4. 主动出击判断
```
根據以下條件判斷是否需要主動通知 Kai：

條件 A：營收異常
- FinMind API 有新的單月營收
- YoY >30% 或 MoM >20%
- 有明確的實質原因可以解釋

條件 B：重要事件
- 重大政經事件（Fed 利率、中國資料）
- 個股重大消息（法說會、新產品）

條件 C：需要 Kai 決策
- 狀態轉換触发（pending → active）
- 關鍵假設被突破
- 補救失敗，需要人工介入
```

### 5. 正常情況
```
如果以上都沒有異常：
- 回覆 HEARTBEAT_OK
- 不要發送任何額外訊息
```

---

## Kai 直接說話時
```
當 Kai 直接發訊息給我時：
- 不要回覆 HEARTBEAT_OK
- 正常處理並回覆
```

---

## HEARTBEAT_OK 使用規則
```
正確：這次心跳沒有需要關注的事 → 回覆「HEARTBEAT_OK」（只有這個字）
錯誤：報告完成後加一句「HEARTBEAT_OK」在結尾
```

---

## 備註
- 心跳是一個輕量級檢查，不需要做任何復雜的分析
- 複雜的營收分析留給 workflows/daily-revenue.py（06:00 cron）
- 心跳只是決定「有沒有需要馬上處理的」

---

_這個檔案是投資蝦系統的一部分，遵守 SYSTEM_ARCHITECTURE.md 中的所有原則_
