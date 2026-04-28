# TOOLS.md — 投資蝦本地設定

## 系統設定

### Notion 設定
- API Token：見 config/secrets.json → notion_key
- Database（台股研究追蹤資料庫）：見 config/secrets.json → notion_legacy_db_id
- Workspace：阿凱投資亂糟糟

### Telegram 設定
- Bot Token：見 config/secrets.json → telegram_bot_token
- 券商報告接收 Group：-5290205228（requireMention: false）
- Kai 個人 DM：5604476530

## API 額度與使用原則

### Brave Search
- 額度：每月 1000 次
- API Key：見 config/secrets.json → brave_api_key
- 觸發時機：通用搜尋、不需 AI 摘要時優先使用

### Tavily Search
- 額度：每月 1000 次
- 觸發時機：需要 AI 整合摘要的複雜研究，其他工具無法取代時才用

## 免費 API 資源（優先使用）
- TWSE：https://openapi.twse.com.tw
- Tpex：https://openapi.tpex.org.tw
- FinMind：https://finmindtrade.com

## 速率限制

| API | 限制 | 備註 |
|-----|------|------|
| FinMind API | 600次/小時 | 抵達 590 次後停止等重置 |
| TWSE OpenAPI | 3次/5秒 | 滾動計算 |
| Tpex OpenAPI | 3次/5秒 | 滾動計算 |

## API 呼叫實作規則

### Header 偽裝
```python
headers = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept': 'application/json, text/html, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.google.com',
}
```

### 特殊規定
- TWSE 日期格式：西元（YYYY-MM-DD）
- Tpex 日期格式：民國年（減 1911）
- Yahoo Finance：使用 yfinance 套件

### 任務分流原則
- 台股數據：TWSE/Tpex/FinMind 優先
- 通用搜尋：Brave Search
- 複雜研究需 AI 摘要：Tavily（保留用）