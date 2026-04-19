# TWSE / Tpex API 使用指南

## 基本資訊

| API | Base URL | Rate Limit |
|-----|----------|------------|
| TWSE OpenAPI | https://openapi.twse.com.tw/v1 | 3次/5秒 |
| Tpex OpenAPI | https://www.tpex.org.tw/openapi/v1 | 3次/5秒 |

## 關鍵 Endpoints

| 用途 | TWSE | Tpex |
|------|------|------|
| 月營收 | /opendata/t187ap05_L | /mopsfin_t187ap05_O |
| 重大訊息 | /opendata/t187ap04_L | /mopsfin_t187ap04_O |
| 董監事 | /opendata/t187ap11_L | /mopsfin_t187ap11_O |
| 季財報 | /opendata/t187ap06_L_ci | /mopsfin_t187ap06_O_ci |

## ⚠️ 關鍵：月營收單位

**TWSE t187ap05_L 的 revenue 欄位單位是「千元」（不是元）**

驗證方式：台泥(1101) 月營收約 100~150 億元，CSV 值約 10,000,000~15,000,000
→ 10,000,000 千元 = 1000 億（不對）
→ 但仔細看：t187ap05_L 的 `8,593,689` = 85.9 億元（千元）✓

```
TWSE t187ap05_L: 千元（直接存）
FinMind TaiwanStockMonthRevenue: 元（/1000 後存）
revenue_history.json: 統一千元儲存
顯示給使用者: ×1000 轉回元
```

## 日期格式

- TWSE：資料年月欄位 = 民國年 YYYMM（5碼），如 `11503` = 2026年3月
- 轉換：`西元年 = 民國年 + 1911`
- TWSE API 請求用西元（YYYY-MM-DD）

```python
date_val = "11503"  # 民國 115 年 03 月
roc_y = int(date_val[:3])   # 115
ym = f"{roc_y + 1911}{date_val[3:]}"  # "202603"
```

## Rate Limit 實作

```python
import time
_last_call = 0.0

def rate_limit_wait():
    global _last_call
    now = time.time()
    if now - _last_call < 1.7:
        time.sleep(1.7 - (now - _last_call))
    _last_call = time.time()
```

## 常見錯誤

- `HTTP 429`：rate limit，等 5 秒後重試
- 空 response：某些端點在非交易日回空陣列，正常
- 民國年計算錯誤：記得是 3 碼（115），不是 2 碼（15）
