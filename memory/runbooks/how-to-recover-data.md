# 資料修復指南

## revenue_history.json 單位問題

**症狀：** 同一間公司不同月份數值差 1000 倍（如 202603: 932,321 vs 202602: 996,429,000）

**根本原因：** backfill 腳本原本未做元→千元轉換，直接存了 FinMind 的元值

**診斷：**
```python
import json, statistics
d = json.load(open('state/revenue_history.json'))
# 找混合單位的公司
mixed = [(c, sorted(v['months'].items(), reverse=True)[:3])
         for c, v in d.items()
         if len(v.get('months',{})) >= 2
         and max(v['months'].values()) / min(x for x in v['months'].values() if x > 0) > 500]
print(f"混合單位公司: {len(mixed)}")
```

**修復流程：**
```python
# 1. 對全部 history，用中位數偵測異常值
for code, v in d.items():
    months = v.get('months', {})
    vals = [x for x in months.values() if x and x > 0]
    if len(vals) < 3: continue
    med = statistics.median(vals)
    for ym, val in list(months.items()):
        if val > 0:
            if val > med * 100:      # 太大 → /1000
                months[ym] = val / 1000
            elif val < med / 100:    # 太小 → *1000
                months[ym] = val * 1000

# 2. 用 scan_results 校驗最新月份
with open('state/scan_results_YYYYMMDD.json') as f:
    sr = json.load(f)
for item in sr['anomalies']:
    code = str(item['code'])
    if code in d and item.get('revenue', 0) > 0:
        hist_months = sorted(d[code]['months'].keys(), reverse=True)
        if hist_months:
            hist_val = d[code]['months'][hist_months[0]]
            ratio = hist_val / item['revenue']
            if not 0.98 < ratio < 1.02:
                d[code]['months'][hist_months[0]] = item['revenue']
```

## scan_results 檔案過多

**症狀：** state/ 目錄裡有大量 scan_results_*.json，佔用空間

**解法：** daily-scan.py 執行時會自動歸檔 14 天前的檔案到 memory/archive/scan_results/
手動歸檔：
```bash
mkdir -p memory/archive/scan_results
ls state/scan_results_*.json | sort | head -n -14 | xargs -I{} mv {} memory/archive/scan_results/
```

## news memory 清理

**症狀：** memory/ 有大量 raw-*.jsonl、processed-*.jsonl 佔用空間

**解法：** 這些是新聞 pipeline 的暫存檔，7 天後可以安全刪除
```bash
find memory/ -name 'raw-*.jsonl' -mtime +7 -delete
find memory/ -name 'processed-*.jsonl' -mtime +7 -delete
```
