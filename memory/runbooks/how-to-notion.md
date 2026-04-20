# how-to-notion.md — Notion 使用指南

_AGENTS.md Session step 8 會查此檔。寫 Notion / 讀 Notion 前必看。_

_注意:本檔的 `db_id` 欄位將在 B4 完成後由 `workflows/setup_notion_databases.py` 實際建庫後補上。目前顯示為佔位符。_

## 基本原則

- **主要資料庫**:Notion(不換工具,HANDOFF 已決策)
- **API**:用 `notion_client` Python SDK
- **api_key**:`config/secrets.json` → `"notion_api_key"`
- **rate limit 防呆**:每個 db 建立後 `time.sleep(0.5)`,每個 property 設定後 `time.sleep(0.3)`

## 9 + 1 個資料庫清單

| 用途 | db 名稱 | 對應任務 | db_id(B4 後補) |
|------|---------|----------|----------------|
| 追蹤中個股 | `stock_tracking` | Task 5(樞紐) | `<TBD>` |
| 交易紀錄 | `trading_journal` | Task 8 | `<TBD>` |
| 券商個股報告 | `broker_reports` | Task 4 | `<TBD>` |
| 產業報告 | `industry_reports` | Task 4 | `<TBD>` |
| 新聞 db(現有,擴充「來源」欄位) | `news_db` | Task 1 | `<既有 id>` |
| 重大事件日曆 | `event_calendar` | Task 3 | `<TBD>` |
| 研究報告 | `research_pages` | Task 6 | `<TBD>` |
| 書籍筆記(主) | `book_notes` | Task 7 | `<TBD>` |
| 書籍概念(子,拆分 book_notes) | `book_concepts` | Task 7 | `<TBD>` |
| 回測結果 | `backtest_results` | Task 8 後期 | `<TBD>` |
| Outcome log | `outcome_log` | Task 8 | `<TBD>` |

**加起來共 10 個 db**(book_notes + book_concepts 算兩個)。完整 schema 見 Kai 手上的 `02_NOTION_SCHEMA_PLAN_v2.md`。

## 架構:database + 每日 dashboard 頁面

混合模式(HANDOFF 已決策):

- **大量資料存 database**(例:scan_results 每天多筆、news_db 每天數十筆)
- **每天自動產生一個 markdown dashboard 子頁面**做「聚焦入口」
- dashboard 由 `workflows/daily_dashboard.py` programmatically 產生,格式穩定
- dashboard 不交給 AI 自由發揮(避免格式漂移)
- 30 天後自動歸檔到 `dashboard_archive/` 子頁面

## 常用操作模板

### 寫入一筆 row

```python
from notion_client import Client
import json, time
from pathlib import Path

SECRETS = json.loads((Path.home() / ".openclaw/workspace/config/secrets.json").read_text())
notion = Client(auth=SECRETS["notion_api_key"])

DB_ID = SECRETS["notion_stock_tracking_db"]

notion.pages.create(
    parent={"database_id": DB_ID},
    properties={
        "個股代號": {"title": [{"text": {"content": "2330.TW"}}]},
        "公司名稱": {"rich_text": [{"text": {"content": "台積電"}}]},
        "立案日期": {"date": {"start": "2026-04-19"}},
        "狀態": {"select": {"name": "追蹤中"}},
        # ...
    }
)
time.sleep(0.5)  # rate limit 防呆
```

### 查詢 db(filter + sort)

```python
result = notion.databases.query(
    database_id=DB_ID,
    filter={
        "property": "狀態",
        "select": {"equals": "追蹤中"}
    },
    sorts=[{"property": "立案日期", "direction": "descending"}]
)
for page in result["results"]:
    # 取 properties
    code = page["properties"]["個股代號"]["title"][0]["plain_text"]
    ...
```

### idempotency(腳本可重跑不污染)

建 db 前檢查是否已存在:

```python
def find_or_create_db(parent_page_id, db_name, schema):
    # 查 parent 下是否已有同名 db
    children = notion.blocks.children.list(block_id=parent_page_id)
    for child in children["results"]:
        if child["type"] == "child_database" and child["child_database"]["title"] == db_name:
            return child["id"]
    # 不存在 → 建
    new_db = notion.databases.create(parent={"page_id": parent_page_id}, title=[...], properties=schema)
    time.sleep(0.5)
    return new_db["id"]
```

## 陷阱與教訓

- **Rate limit**:Notion API 約 3 req/sec,批次操作要 sleep
- **property 名稱帶空格或中文**:OK,但寫 code 時要一致
- **select option 要先建**:新增 option 時用 API 先建 option,再寫 row
- **API 只能 archive 不能 delete**:rollback 時 Kai 要手動到 UI 刪除
- **page property 的 type 不能改**:建錯要砍掉重建,所以 schema 要確認後才執行
- **rich_text 有 2000 字元上限**:長文要拆多個 block

## Dashboard 頁面格式(B4 產出)

每日 06:10 產生的子頁面內容大綱:

```
# YYYY-MM-DD Dashboard

## 追蹤中個股今日動態
- 2330.TW 台積電:營收 YoY +X%,法說會 4/28
- ...

## 今日重大事件(event_calendar)
- 09:00 鴻海法說會
- ...

## 今日新聞高 signal(news_db)
- ...

## 待 Kai 裁決
- ...
```

實際格式由 `workflows/daily_dashboard.py` 控制,要改格式改腳本,不要手動改頁面(會被隔天覆蓋)。

## 存取路徑

- parent page id:`config/secrets.json` → `"notion_parent_page_id"`
- 各 db_id:`config/secrets.json` → `"notion_<db_name>_db"`

**絕不 hardcode id**,一律讀 secrets.json。

---

_上次更新:2026-04-19(B4 後補 db_id)_
