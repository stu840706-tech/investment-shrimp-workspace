# FinMind API 使用指南

## 基本資訊

- Base URL: `https://api.finmindtrade.com/api/v4/data`
- Rate Limit: **600 次/小時**，我們設定 590 次停止（留 10 次緩衝）
- 每次呼叫間隔：最少 6.1 秒（確保 1 小時不超過 590 次）
- Token 位置：`config/secrets.json` → `finmind_token`

## 常用 Datasets

| Dataset | 說明 |
|---------|------|
| TaiwanStockMonthRevenue | 月營收（單位：**元**） |
| TaiwanStockFinancialStatements | 綜合損益表 |
| TaiwanStockInstitutionalInvestors | 三大法人 |

## ⚠️ 關鍵：FinMind revenue 單位是「元」

```python
# 正確：FinMind 回傳元，存入 history 要 /1000
def finmind_to_qianyuan(rev_raw):
    if not rev_raw or rev_raw <= 0:
        return 0
    return rev_raw / 1000  # 元 → 千元，與 TWSE 一致
```

## Rate Limit 實作（backfill 用）

```python
from collections import deque
_call_times = deque()
CALL_LIMIT = 590
CALL_WINDOW = 3600
MIN_INTERVAL = 6.1

def rate_limit_finmind():
    now = time.time()
    while _call_times and now - _call_times[0] > CALL_WINDOW:
        _call_times.popleft()
    if len(_call_times) >= CALL_LIMIT:
        sleep_time = CALL_WINDOW - (now - _call_times[0]) + 0.5
        time.sleep(sleep_time)
        # 清理舊記錄
        now = time.time()
        while _call_times and now - _call_times[0] > CALL_WINDOW:
            _call_times.popleft()
    if _call_times and (now - _call_times[-1]) < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - (now - _call_times[-1]))
    _call_times.append(time.time())
```

## 常見錯誤

- `402 Payment Required`：額度耗盡，等 1 小時後重試
- `msg: error`：token 無效或過期，需重新取得
- 空 data 陣列：股票代碼不存在或時間範圍無資料（正常）
