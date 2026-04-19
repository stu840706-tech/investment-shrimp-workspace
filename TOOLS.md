# TOOLS.md - Local Notes

## 系統設定

### Notion 設定
- **API Token：** 見 `config/secrets.json` → `notion_key`
- **Database（台股研究追蹤資料庫）：** 見 `config/secrets.json` → `notion_legacy_db_id`
- ** Workspace：** 阿凱投資亂糟糟

### Telegram 設定
- **Bot Token：** 見 `config/secrets.json` → `telegram_bot_token`
- **券商報告接收 Group：** `-5290205228`（requireMention: false）
- **Kai 個人 DM：** `5604476530`

## API 額度與使用原則

### Brave Search
- **額度：** 每月 1000 次搜索
- **API Key：** 見 `config/secrets.json` → `brave_api_key`
- **原則：** 通用搜尋、分担 Tavily 額度壓力（無 AI 摘要功能）
- **觸發時機：** 當需要廣泛網路搜尋、且不需要 AI 摘要時優先使用

### Tavily Search
- **額度：** 每月 1000 次搜索
- **原則：** 保留給需要 AI 整合摘要的複雜研究
- **觸發時機：** 當免費資源無法取得、且需要 AI 整合摘要時才使用

### 免费 API 資源（優先使用）
- **台灣證券交易所（TWSE）：** https://openapi.twse.com.tw
- **證券櫃檯買賣中心（Tpex）：** https://openapi.tpex.org.tw
- **FinMind API：** https://finmindtrade.com （股票、總經數據）
- **其他免費來源：** 政府資料開放平台、公開財報、公開資訊觀測站

### FinMind API Token（已啟用較高用量）
```
見 config/secrets.json → finmind_token
```
- **用途：** 使用 FinMind API 獲取台股、個股、總經數據

### 爬蟲優先原則
- 能用爬蟲取得的数据 → 不用 Tavily
- 能用免費 API 取得的數據 → 不用 Tavily
- 只有當資料分散、難以結構化取得、且需要 AI 整合時 → 才用 Tavily

## API 使用規則（共通性原則）

### 速率限制一覽

| API | 限制 | 備註 |
|-----|------|------|
| **FinMind API** | 600次/小時 | 抵達 590 次後停止，等額度重置後再繼續 |
| **TWSE OpenAPI** | 3次/5秒 | 滾動計算，不可超限 |
| **Tpex OpenAPI** | 3次/5秒 | 滾動計算，不可超限 |
| **Tavily Search** | 1000次/月 | 保留給非股票研究用途 |

### 並行控制原則
- **同一 API 不可同時用於多個任務**
- 若某任務正在使用 FinMind/TWSE/Tpex，另一個需要相同 API 的任務必須排隊等候
- 排隊中的任務等前一個完全結束後才能開始

### 任務分流原則
- **台股相關數據**：優先用 TWSE/Tpex OpenAPI 或 FinMind API
- **General 搜尋（無需 AI 摘要）**：優先用 Brave Search
- **複雜研究、需要 AI 摘要**：使用 Tavily Search
- **Tavily 額度**只用於「沒有其他工具可以取代」的場景

### API 呼叫實作規則

#### 1. Header 偽裝（避免被認定為爬蟲）
所有 HTTP 請求**必須**攜帶瀏覽器風格的 Header，**禁止**使用 Python 預設 User-Agent。

```python
# ✅ 正確：瀏覽器風格
headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.google.com',
}

# ❌ 錯誤：明確的 Python 特徵
# 'User-Agent': 'python-requests/2.28.0'
```

**yfinance 自動處理此問題**，無需額外設定。

#### 2. TWSE / Tpex OpenAPI 特殊規定

**日期格式：**
- TWSE API：使用**西元**（YYYY-MM-DD）
- Tpex API：使用**民國年**（ROC 年份，例如 113/04/11）
  - 轉換方式：`民國年 = 西元年 - 1911`

**速率控制：**
```python
import time

# 每5秒最多3次呼叫，嚴格遵守
# 錯誤示範（會被 block）：
for item in items:
    response = requests.get(url)  # 太快了！

# 正確示範：
for item in items:
    response = requests.get(url, headers=browser_headers)
    time.sleep(2)  # 每筆間隔約2秒，5秒內不超過3次
```

**並發控制：** 使用 `threading.Semaphore(1)` 或 `asyncio.Semaphore(1)` 確保同一時間只有一個請求在執行。

#### 3. FinMind API 額度管理

```python
import time
from collections import deque

# 追蹤每小時呼叫次數（滾動視窗）
call_timestamps = deque()  # 存每次呼叫的時間戳

def can_call_finmind():
    now = time.time()
    # 移除1小時前的記錄
    while call_timestamps and now - call_timestamps[0] > 3600:
        call_timestamps.popleft()
    return len(call_timestamps) < 590  # 留10次buffer

def call_finmind(url, params):
    if not can_call_finmind():
        sleep_time = 3600 - (now - call_timestamps[0]) if call_timestamps else 3600
        time.sleep(sleep_time)  # 等額度重置
    call_timestamps.append(time.time())
    return requests.get(url, params=params, headers=browser_headers)
```

**當達到 590 次時，停止呼叫並通知 Kai，等額度出現後再繼續。**

#### 4. Yahoo Finance
- 直接使用 `yfinance` Python 套件（`pip install yfinance`）
- yfinance 已自動處理 cookie/crumb，無需手動管理
- 單一股票歷史資料查詢視為 1 次呼叫

### 任務排程策略（多公司查詢範例）

若需要查詢全台上市櫃公司（例如 1800 家）的財務資料：

1. **第一波**：用 TWSE/Tpex OpenAPI，每 2 秒一次呼叫
   - 約 900 秒（約 15 分鐘）完成第一輪
   - 期間若被 rate limit 阻擋，等 5 秒後自動重試
2. **第二波**：對第一波無法取得的資料，用 FinMind API 填補
   - 590 次額度仔細分配，優先填補最關鍵的缺口
3. **Tavily**：只用於finmind、twse、tpex 都找不到的非常規資訊

---

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

---

Add whatever helps you do your job. This is your cheat sheet.

