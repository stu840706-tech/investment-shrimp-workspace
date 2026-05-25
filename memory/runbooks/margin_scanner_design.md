# 融資融券全市場掃描器 設計文件 (margin_scanner)

## 1. 目的

對台股全市場（~1800 檔上市櫃普通股）每日掃描融資融券資料，偵測籌碼面異常並產出 signal，餵入投資蝦研究流程。

## 2. 資料流
FinMind API (per-stock query)
 → Raw layer (data/margin/yyyy-mm-dd.jsonl)
 → Signal layer (異常偵測)
 → Notion DB「融資融券異常」(僅命中標的)

## 3. 規模與排程

- Universe: 上市櫃普通股 ~1800 檔（filter ETF / 受益憑證 / 權證 / 特別股）
- API cost: 1800 × 6.1s ≈ 3 小時/次（FinMind register tier）
- 排程: 凌晨 02:00–05:00 Taipei（市場收盤後、隔日 publisher 前）
- 避開 07:00 / 19:00 publisher 窗口；具體 cron 行待 implementation 階段對齊 cron/crontab.txt

## 4. 模組設計

### 4.1 Universe Builder (`scripts/build_universe.py`)

從 FinMind TaiwanStockInfo 取得清單，filter 後 cache 本地。Cache 過期 7 天才重抓。
def is_common_stock(row):
 sid = row["stock_id"]
 cat = row.get("industry_category", "")
 if any(k in cat for k in ("ETF", "受益憑證", "權證")):
 return False
 if not sid.isdigit() or len(sid) != 4:
 return False
 return True

產出兩份檔案：
- data/universe.json: stock_id 列表
- data/universe_stats.json: 含 generated_at / total_raw / common_count / excluded_count / excluded_categories / excluded_ids_sample，用於追蹤 universe 變化（新上市、下市、分類變更）

實際分類字串可能與上方常數有 drift，第一次跑後肉眼校驗 excluded_categories 分佈再 finalize filter。

### 4.2 Checkpoint-aware Scanner (`scripts/scan_margin.py`)

對 universe 每檔拉融資融券資料，per-stock 即時 checkpoint。
def scan_margin_full_market(target_date):
 universe = load_universe()
 checkpoint = f"data/margin_{target_date}.checkpoint"
 done = load_checkpoint_set(checkpoint)
 raw_file = f"data/margin/{target_date}.jsonl"

 for sid in universe:
 if sid in done:
 continue
 rate_limit_finmind() # 既有 wrapper
 rows = fetch_one(sid, target_date)
 append_jsonl(raw_file, rows)
 mark_done(checkpoint, sid) # 即時 flush

 os.remove(checkpoint) # 成功跑完即砍

Checkpoint 設計：成功跑完直接刪除。崩潰時保留供下次續跑，audit trail 由 raw data 提供。

### 4.3 Status Dispatch
MAX_RETRY_402 = 3
MAX_BACKOFF_5XX = 240 # 30 → 60 → 120 → 240

def fetch_one(sid, target_date, retry_402=0, backoff=30):
 resp = requests.get(URL, params={
 "dataset": "TaiwanStockMarginPurchaseShortSale",
 "data_id": sid,
 "start_date": target_date,
 "end_date": target_date,
 }, headers={"Authorization": f"Bearer {token}"})

 code = resp.status_code
 if code == 200:
 return resp.json().get("data", [])
 if code == 400:
 # per-stock 不該 400 (只有全市場 query 才會)。若發生 → 配置錯
 alert(f"FinMind 400 on per-stock {sid} (tier/config error)")
 raise PermanentError(f"400 on {sid}")
 if code == 402:
 if retry_402 >= MAX_RETRY_402:
 log.error(f"402 exhausted on {sid}, skip"); return []
 log.warn(f"quota exhausted, sleep 3600s (retry {retry_402+1}/{MAX_RETRY_402})")
 time.sleep(3600)
 return fetch_one(sid, target_date, retry_402 + 1, backoff)
 if code in (500, 502, 503, 504):
 if backoff > MAX_BACKOFF_5XX:
 log.error(f"5xx exhausted on {sid}, skip"); return []
 time.sleep(backoff)
 return fetch_one(sid, target_date, retry_402, backoff * 2)
 log.error(f"unexpected {code} for {sid}: {resp.text[:200]}")
 return []

## 5. 資料儲存

### 5.1 Raw Layer

- Path: data/margin/yyyy-mm-dd.jsonl
- Format: JSON Lines，每行一筆股票記錄
- Schema (FinMind TaiwanStockMarginPurchaseShortSale 原樣):
 date, stock_id,
 MarginPurchaseBuy, MarginPurchaseCashRepayment, MarginPurchaseSell,
 MarginPurchaseTodayBalance, MarginPurchaseYesterdayBalance, MarginPurchaseLimit,
 ShortSaleBuy, ShortSaleCashRepayment, ShortSaleSell,
 ShortSaleTodayBalance, ShortSaleYesterdayBalance, ShortSaleLimit,
 OffsetLoanAndShort, Note- Retention: 保留 365 天，cleanup job 每週砍 1 年前的舊檔

### 5.2 Signal Layer

只把命中異常條件的標的寫 Notion。預估 30-50 筆/天。

- Notion DB: 「融資融券異常」(UUID 待 Kai 建立後填入)
- 寫入時用直接 requests，遵循 how-to-notion.md
- 欄位 schema 待定

## 6. 異常偵測規則 (TBD)

候選條件，閾值待 Kai 定義：
- 融資餘額 day-over-day 變動 > X%
- 券資比突破歷史 N 日 P percentile
- 單日融資使用率 > Y%
- 融券餘額 day-over-day 變動 > Z%（軋空候選）

需要 baseline data → 第一次 deploy 要 backfill。

## 7. Backfill 策略

FinMind 單次 call 支援 date range，所以 backfill 60 天一檔 = 1 call，1800 檔 ≈ 3 小時（跟 daily scan 同成本）。

建議第一次 deploy：backfill 60 天建立 baseline，之後 daily incremental 只拉 T-day。

## 8. 未來 Claude 查找指南（Stock Research 場景）

當 Kai 在後續對話請 Claude 做個股研究時，融資融券相關資料位置：

| 需求 | 來源 | 操作方式 |
|------|------|---------|
| 某日某股 raw | data/margin/yyyy-mm-dd.jsonl | grep '"stock_id": "XXXX"' file |
| 某股近期趨勢 | 多日 jsonl 聚合 | 跨檔 jq / pandas |
| 當日異常標的 | Notion「融資融券異常」DB | notion-fetch + date filter |
| Universe 變動史 | data/universe_stats.json | diff over time |
| Anomaly 閾值 | 本文件第 6 節 | 直接讀 |

Stock research skill 應加入規則：優先 query 本地 margin data 再 fall back FinMind API。

## 9. 開放問題

1. **異常條件閾值**：第 6 節候選條件的具體數字
2. **Backfill 起點**：60 天夠嗎？某些 signal 需要更長 baseline
3. **Wrapper drift check**：現有 rate_limit_finmind 與 FinMind fetch wrapper 實際 code 待 Kai paste，對齊本 spec 第 4.3 節
4. **Notion DB schema**：「融資融券異常」具體欄位設計
5. **Cron 行落地**：02:00–05:00 Taipei 對應 UTC cron，需對齊既有 crontab 不衝突

## 10. Cross-references

- 上游 rate limit & retry: memory/runbooks/how-to-finmind.md
- Notion 寫入規範: memory/runbooks/how-to-notion.md
- 排程衝突檢查: cron/crontab.txt
- 變更歷史：本文件初版由 design discussion 產出，後續以 git log 為準
