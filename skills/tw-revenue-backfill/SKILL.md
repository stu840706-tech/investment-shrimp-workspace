---
name: tw-revenue-backfill
description: "補充台股月營收歷史資料。當 revenue_history.json 缺少 2024~2025 年月份資料時使用。從 FinMind TaiwanStockMonthRevenue API 回補，自動處理 rate limit（590次/小時），FinMind revenue 為元，存入時除以 1000 統一轉千元。觸發時機：(1) Kai 說「補充歷史營收資料」、(2) 月營收標籤（近兩年新高、連續成長）因資料不足無法觸發、(3) 新機器部署後第一次執行。"
---

# 台股月營收歷史回補

## 何時使用

- `revenue_history.json` 裡大量公司缺少 2024~2025 年月份
- 月營收標籤（`近2年高`、`連3月遞增`）大量不觸發
- 新環境部署後首次初始化

## 執行方式

```bash
cd ~/.openclaw/workspace
python3 skills/tw-revenue-backfill/scripts/backfill_revenue.py
```

## 注意事項

- 執行時間較長（~1.5 小時，受 FinMind 590次/小時 rate limit 限制）
- 不可與 daily-scan.py 同時執行（共用 FinMind API 額度）
- 若中途中斷，重新執行會從已有資料繼續，不會重複
- 完成後確認：state/revenue_history.json 主要公司（台積電/聯華/鴻海）最近 6 個月數值一致

## 資料單位說明

```
FinMind revenue 欄位 → 元（原始）
存入 revenue_history.json → 千元（/1000 轉換）
TWSE t187ap05_L → 千元（不需轉換）
顯示給使用者 → 元（×1000 格式化）
```

## 驗證指令

執行完後可用以下指令確認：
```bash
python3 -c "
import json
d = json.load(open('state/revenue_history.json'))
# 台積電近 4 個月應在 3000億~5000億元 範圍
months = sorted(d['2330']['months'].items(), reverse=True)[:4]
for m, v in months:
    print(f'{m}: {v:,.0f} 千元 = {v*1000/1e8:.1f} 億元')
"
```
