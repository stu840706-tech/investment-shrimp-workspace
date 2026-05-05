# HEARTBEAT.md - 投資蝦心跳檢查清單

---

## 心跳觸發條件

- **頻率**：每 30 分鐘
- **活躍時段**：06:00 - 23:59（Taipei Time）
- **沉默時段**：00:00 - 05:59（Taipei Time）→ 回覆 NO_REPLY

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

### 4. 主動出擊判斷
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
- 狀態轉換觸發（pending → active）
- 關鍵假設被突破
- 補救失敗，需要人工介入
```

### 5. 正常情況
```
如果以上都沒有異常：
→ 回覆 NO_REPLY（不要回任何文字，不要回 HEARTBEAT_OK）
→ OpenClaw 會安靜結束，不發訊息到 Telegram
```

---

## Kai 直接說話時
```
當 Kai 直接發訊息給我時：
- 不要回覆 NO_REPLY
- 正常處理並回覆
```

---

## NO_REPLY 使用規則
```
正確：沒有任何需要告知的事 → 回覆 NO_REPLY（只有這個字）
錯誤：回「HEARTBEAT_OK」或任何文字（「一切正常」「HEARTBEAT_OK」等）
```

---

## 備註
- 心跳是一個輕量級檢查，不需要做任何複雜的分析
- 複雜的營收分析留給 workflows/daily-revenue.py（06:00 cron）
- 心跳只是決定「有沒有需要馬上處理的」
- Telegram 是公開對話，heartbeat 的安靜回覆不應出現在這裡