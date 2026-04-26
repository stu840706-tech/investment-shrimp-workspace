#!/usr/bin/env python3
"""
setup_notion_databases.py — B4: 建立 9+1 個 Notion database + scan_results

設計原則（HANDOFF + checklist + Kai Q1/Q2 補充）:
- Idempotency: 同名 db 已存在 → 抓 id 不重建
- Rate limit: db 之間 sleep(0.5), property 之間 sleep(0.3)
- 雙寫: secrets.json + memory/runbooks/how-to-notion.md 必須同步
- Audit: 列出 hub page 底下所有既有 db 到 /tmp/b4_existing_dbs.txt
- 不動 legacy_db / news_db / parent_db (75b0) / 「New database」(791c)

執行模式:
  --dry-run       只列出會做什麼，不實際呼叫 Notion API write
  --resume        中斷後重跑，已存在的 db 直接抓 id
  (無參數)         正式執行
"""
import json
import time
import sys
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ============================================================
# 環境
# ============================================================
WORKSPACE = Path("/home/ubuntu/.openclaw/workspace")
SECRETS_PATH = WORKSPACE / "config" / "secrets.json"
RUNBOOK_PATH = WORKSPACE / "memory" / "runbooks" / "how-to-notion.md"
AUDIT_PATH = Path("/tmp/b4_existing_dbs.txt")

NOTION_VERSION = "2022-06-28"
NOTION_API = "https://api.notion.com/v1"

# Hub page id (Kai 在 Notion UI 建的「投資蝦資料庫總覽」)
HUB_PAGE_ID = "34e226f5-a398-802f-bf27-fa7a4fa19970"

# Rate limit (HANDOFF 陷阱 2)
SLEEP_AFTER_DB = 0.5
SLEEP_AFTER_PROP = 0.3  # 保留參數，目前 db 一次連 properties 一起建

# ============================================================
# Schema 定義 (依 02_NOTION_SCHEMA_PLAN_v2.md)
# ============================================================

# 共用 select options helper
def select_opts(*names):
    return {"options": [{"name": n} for n in names]}


# 9 個新 db + scan_results = 10 個
# 注意: 順序很重要，stock_tracking 必須先建（其他 db 用 relation 指它）
# 但 Notion API 限制: relation 必須在目標 db 已存在時才能建
# → 第一輪先建所有 db (relations 先空著或暫時 placeholder)
# → 第二輪 patch 加上 relation
#
# 為了腳本簡潔且可重跑，我們採: 先建所有不含 relation 的 base properties,
# 第二輪用 PATCH /databases/{id} 加上 relation。

DATABASES = [
    {
        "name": "stock_tracking",
        "secrets_key": "notion_stock_tracking_db",
        "title": "stock_tracking",
        "base_properties": {
            "股票代碼": {"title": {}},
            "公司名稱": {"rich_text": {}},
            "產業別": {"select": select_opts(
                "半導體", "電子零組件", "傳產", "金融", "消費", "AI概念", "記憶體", "其他"
            )},
            "狀態": {"select": select_opts(
                "持有", "未持有_看好", "未持有_感興趣", "不繼續追蹤"
            )},
            "thesis類型": {"select": select_opts(
                "需求驅動", "成本改善", "地緣紅利", "護城河擴大", "景氣循環", "其他"
            )},
            "初次加入日": {"date": {}},
            "核心thesis": {"rich_text": {}},
            "期待催化劑": {"rich_text": {}},
            "風險因素": {"rich_text": {}},
            "反證條件": {"rich_text": {}},
            "當前觀點更新": {"rich_text": {}},
            "下次驗證日": {"date": {}},
            "下次驗證內容": {"rich_text": {}},
            "Outcome狀態": {"select": select_opts(
                "待驗證", "已驗證符合", "部分符合", "已驗證反證"
            )},
            "最近Outcome結果": {"rich_text": {}},
        },
        "relations": [],  # 樞紐 db，被別人指
    },
    {
        "name": "trading_journal",
        "secrets_key": "notion_trading_journal_db",
        "title": "trading_journal",
        "base_properties": {
            "交易日期": {"title": {}},
            "標的": {"rich_text": {}},
            "股票代碼": {"rich_text": {}},
            "產業": {"select": select_opts(
                "半導體", "電子零組件", "傳產", "金融", "消費", "AI概念", "記憶體", "其他"
            )},
            "AI相關": {"checkbox": {}},
            "動作": {"select": select_opts("買入", "賣出", "加碼", "減碼")},
            "買入理由": {"rich_text": {}},
            "信心指數": {"select": select_opts("1", "2", "3", "4", "5")},
            "買入價格": {"number": {"format": "number"}},
            "買入股數": {"number": {"format": "number"}},
            "賣出價格": {"number": {"format": "number"}},
            "賣出股數": {"number": {"format": "number"}},
            "停損價": {"number": {"format": "number"}},
            "獲利百分比": {"formula": {
                "expression": 'if(and(prop("賣出價格") > 0, prop("買入價格") > 0), (prop("賣出價格") - prop("買入價格")) / prop("買入價格") * 100, toNumber(""))'
            }},
            "獲利額": {"formula": {
                "expression": 'if(and(prop("賣出價格") > 0, prop("賣出股數") > 0), (prop("賣出價格") - prop("買入價格")) * prop("賣出股數"), toNumber(""))'
            }},
            "最新策略": {"rich_text": {}},
            "前次策略": {"rich_text": {}},
            "凍結thesis": {"rich_text": {}},
        },
        "relations": [
            ("關聯追蹤", "notion_stock_tracking_db"),
        ],
    },
    {
        "name": "broker_reports",
        "secrets_key": "notion_broker_reports_db",
        "title": "broker_reports",
        "base_properties": {
            "股票代碼": {"title": {}},
            "報告日期": {"date": {}},
            "公司名稱": {"rich_text": {}},
            "券商名稱": {"select": select_opts()},  # 動態累積
            "評等": {"select": select_opts(
                "買進", "加碼", "中立", "減碼", "賣出", "未明確"
            )},
            "目標價": {"number": {"format": "number"}},
            "報告當日股價": {"number": {"format": "number"}},
            "潛在漲幅百分比": {"formula": {
                "expression": 'if(and(prop("目標價") > 0, prop("報告當日股價") > 0), (prop("目標價") - prop("報告當日股價")) / prop("報告當日股價") * 100, toNumber(""))'
            }},
            "核心觀點": {"rich_text": {}},
            "營收預測_今年": {"number": {"format": "number"}},
            "營收預測_明年": {"number": {"format": "number"}},
            "EPS預測_今年": {"number": {"format": "number"}},
            "EPS預測_明年": {"number": {"format": "number"}},
            "毛利率預測": {"number": {"format": "percent"}},
            "PE估值": {"number": {"format": "number"}},
            "原始內文": {"rich_text": {}},
        },
        "relations": [
            ("關聯追蹤", "notion_stock_tracking_db"),
        ],
    },
    {
        "name": "industry_reports",
        "secrets_key": "notion_industry_reports_db",
        "title": "industry_reports",
        "base_properties": {
            "產業主題": {"title": {}},
            "報告日期": {"date": {}},
            "產業分類": {"multi_select": select_opts(
                "半導體", "AI", "記憶體", "光學", "電動車", "生技", "金融", "傳產", "散熱", "機殼", "其他"
            )},
            "券商名稱": {"select": select_opts()},
            "核心觀點": {"rich_text": {}},
            "關鍵數字": {"rich_text": {}},
            "受惠標的": {"multi_select": select_opts()},
            "受害標的": {"multi_select": select_opts()},
            "原始內文_關鍵段落": {"rich_text": {}},
        },
        "relations": [],
    },
    {
        "name": "event_calendar",
        "secrets_key": "notion_event_calendar_db",
        "title": "event_calendar",
        "base_properties": {
            "預計日期": {"title": {}},
            "股票代碼": {"rich_text": {}},
            "公司名稱": {"rich_text": {}},
            "事件類型": {"select": select_opts(
                "法說會", "年報", "季報", "月營收", "股東會", "除權息"
            )},
            "重要性": {"select": select_opts("高", "中", "低")},
            "已提醒": {"checkbox": {}},
            "實際日期": {"date": {}},
            "連結": {"url": {}},
            "分析結果": {"rich_text": {}},
            "分析結果產出日": {"date": {}},
        },
        "relations": [
            ("關聯追蹤", "notion_stock_tracking_db"),
        ],
    },
    {
        "name": "research_pages",
        "secrets_key": "notion_research_pages_db",
        "title": "research_pages",
        "base_properties": {
            "一句話摘要": {"title": {}},
            "股票代碼": {"rich_text": {}},
            "公司名稱": {"rich_text": {}},
            "報告日期": {"date": {}},
            "版本": {"number": {"format": "number"}},
            "已作廢": {"checkbox": {}},
            "投資結論": {"select": select_opts(
                "A值得現在買", "B值得追蹤", "C不值得研究"
            )},
            "目標價_保守": {"number": {"format": "number"}},
            "目標價_基準": {"number": {"format": "number"}},
            "目標價_樂觀": {"number": {"format": "number"}},
        },
        "relations": [
            ("關聯追蹤", "notion_stock_tracking_db"),
            # 引用書籍概念 → 第二輪、book_concepts 建好後加
        ],
    },
    {
        "name": "book_notes",
        "secrets_key": "notion_book_notes_db",
        "title": "book_notes",
        "base_properties": {
            "書名": {"title": {}},
            "作者": {"rich_text": {}},
            "類別": {"multi_select": select_opts(
                "價值投資", "成長投資", "總經", "心理", "技術分析", "量化", "產業", "其他"
            )},
            "閱讀狀態": {"select": select_opts("未讀", "閱讀中", "已讀")},
            "閱讀完成日": {"date": {}},
            "總體評價": {"select": select_opts(
                "★", "★★", "★★★", "★★★★", "★★★★★"
            )},
            "一句話總結": {"rich_text": {}},
        },
        "relations": [],  # concept count rollup 在第二輪
    },
    {
        "name": "book_concepts",
        "secrets_key": "notion_book_concepts_db",
        "title": "book_concepts",
        "base_properties": {
            "概念名稱": {"title": {}},
            "觀點說明": {"rich_text": {}},
            "舉例": {"rich_text": {}},
            "如何使用": {"rich_text": {}},
            "適用情境": {"multi_select": select_opts(
                "選股", "估值", "風險管理", "心理", "總經", "策略", "其他"
            )},
            "重要度": {"select": select_opts("核心", "重要", "參考")},
        },
        "relations": [
            ("所屬書籍", "notion_book_notes_db"),
            ("關聯追蹤", "notion_stock_tracking_db"),
            ("關聯研究", "notion_research_pages_db"),
        ],
    },
    {
        "name": "backtest_results",
        "secrets_key": "notion_backtest_results_db",
        "title": "backtest_results",
        "base_properties": {
            "策略名稱": {"title": {}},
            "回測日期": {"date": {}},
            "回測期間_起": {"date": {}},
            "回測期間_迄": {"date": {}},
            "標的類別": {"multi_select": select_opts(
                "台股全市場", "0050", "追蹤清單", "特定產業"
            )},
            "策略一句話": {"rich_text": {}},
            "程式碼檔名": {"rich_text": {}},
            "總報酬率百分比": {"number": {"format": "percent"}},
            "年化報酬百分比": {"number": {"format": "percent"}},
            "夏普比率": {"number": {"format": "number"}},
            "最大回撤百分比": {"number": {"format": "percent"}},
            "勝率百分比": {"number": {"format": "percent"}},
            "交易次數": {"number": {"format": "number"}},
            "參數掃描結果": {"rich_text": {}},
            "備註": {"rich_text": {}},
        },
        "relations": [],
    },
    {
        "name": "outcome_log",
        "secrets_key": "notion_outcome_log_db",
        "title": "outcome_log",
        "base_properties": {
            "review_id": {"title": {}},
            "股票代碼": {"rich_text": {}},
            "review_date": {"date": {}},
            "期待催化劑_當時": {"rich_text": {}},
            "實際數據": {"rich_text": {}},
            "驗證狀態": {"select": select_opts(
                "已驗證符合", "部分符合", "已驗證反證"
            )},
            "偏差分析": {"rich_text": {}},
            "下一次review_date": {"date": {}},
            "下一次催化劑": {"rich_text": {}},
            "驗證當時狀態": {"select": select_opts(
                "持有", "看好", "感興趣"
            )},
        },
        "relations": [
            ("關聯追蹤", "notion_stock_tracking_db"),
        ],
    },
    # scan_results: 對齊 daily-notion.py line 320-345 現用的 11 col
    # B6 升級 daily-notion.py 改寫到這個固定 db
    {
        "name": "scan_results",
        "secrets_key": "notion_scan_results_db",
        "title": "scan_results",
        "base_properties": {
            "股票名稱": {"title": {}},
            "月營收": {"rich_text": {}},
            "月營收YoY": {"rich_text": {}},
            "月營收MoM": {"rich_text": {}},
            "營收利多": {"multi_select": {"options": [
                {"name": "營收連續成長月數 > 3"},
                {"name": "營收達成率異常(法人預估)"},
                {"name": "營收雙增(YoY/MoM>10%)"},
                {"name": "營收創歷史新高"},
                {"name": "營收創近兩年新高"},
            ]}},
            "三率利多": {"multi_select": select_opts(
                "毛利率YoY+", "營益率YoY+", "淨利率YoY+", "三率齊升"
            )},
            "籌碼面利多": {"multi_select": select_opts(
                "外資買超", "投信買超", "三大法人合計買超", "董監持股增"
            )},
            "股利條件": {"multi_select": select_opts(
                "現金殖利率>5%", "連續配息>5年", "配息率>50%"
            )},
            "產業相對強弱": {"multi_select": select_opts(
                "產業強勢", "個股強於產業"
            )},
            "重大訊息": {"rich_text": {}},
            "詳細內容": {"rich_text": {}},
        },
        "relations": [],
    },
]


# ============================================================
# Notion API helpers
# ============================================================

class NotionError(Exception):
    pass


def http_request(method, url, headers, payload=None):
    """純 stdlib HTTP，不依賴 notion-client。"""
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise NotionError(f"HTTP {e.code} {method} {url}: {body}")
    except urllib.error.URLError as e:
        raise NotionError(f"URLError {method} {url}: {e}")


def notion_headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def list_hub_children(api_key, hub_page_id):
    """列出 hub page 底下所有 child blocks。回傳 [{type, id, title}]。"""
    headers = notion_headers(api_key)
    out = []
    cursor = None
    while True:
        url = f"{NOTION_API}/blocks/{hub_page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        data = http_request("GET", url, headers)
        for b in data.get("results", []):
            t = b.get("type")
            bid = b["id"]
            title = "?"
            if t == "child_database":
                title = b.get("child_database", {}).get("title", "?")
            elif t == "child_page":
                title = b.get("child_page", {}).get("title", "?")
            out.append({"type": t, "id": bid, "title": title})
        if data.get("has_more"):
            cursor = data.get("next_cursor")
        else:
            break
    return out


def get_db_schema(api_key, db_id):
    headers = notion_headers(api_key)
    return http_request("GET", f"{NOTION_API}/databases/{db_id}", headers)


def create_database(api_key, hub_page_id, title, properties, dry_run=False):
    headers = notion_headers(api_key)
    payload = {
        "parent": {"type": "page_id", "page_id": hub_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties,
    }
    if dry_run:
        prop_count = len(properties)
        print(f"  [DRY-RUN] POST /databases  title={title}  properties={prop_count}")
        return {"id": f"DRY-RUN-{title}", "_dry_run": True}
    return http_request("POST", f"{NOTION_API}/databases", headers, payload)


def patch_database_relations(api_key, db_id, relation_map, dry_run=False):
    """relation_map = {prop_name: target_db_id}"""
    if not relation_map:
        return None
    headers = notion_headers(api_key)
    properties = {}
    for prop_name, target_db_id in relation_map.items():
        properties[prop_name] = {
            "relation": {
                "database_id": target_db_id,
                "type": "dual_property",
                "dual_property": {},
            }
        }
    payload = {"properties": properties}
    if dry_run:
        print(f"  [DRY-RUN] PATCH /databases/{db_id}  relations={list(relation_map.keys())}")
        return {"_dry_run": True}
    return http_request("PATCH", f"{NOTION_API}/databases/{db_id}", headers, payload)


# ============================================================
# Secrets / runbook 雙寫
# ============================================================

def load_secrets():
    with open(SECRETS_PATH) as f:
        return json.load(f)


def save_secrets(secrets):
    """Atomic write: 先寫 .tmp 再 rename。"""
    tmp = SECRETS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(secrets, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(SECRETS_PATH)


def update_runbook_with_db_ids(db_ids):
    """
    把 how-to-notion.md 內的 <TBD> 換成實際 db_id。
    依 secrets_key 對應 runbook 表格內 db 名稱。
    """
    if not RUNBOOK_PATH.exists():
        print(f"  [WARN] {RUNBOOK_PATH} 不存在，跳過 runbook 更新")
        return False

    text = RUNBOOK_PATH.read_text(encoding="utf-8")

    # 表格 row 格式: | 用途 | `db_name` | Task X | `<TBD>` |
    # 我們把對應 row 的 <TBD> (含 backticks) 換成實際 id
    # 為了穩定，採用「逐 db 名稱比對」: 表格 row 裡有 `<db_name>` 就在同一 row 替換 <TBD>
    lines = text.split("\n")
    name_to_id = {db["name"]: db_ids.get(db["secrets_key"]) for db in DATABASES}

    changed = 0
    for i, line in enumerate(lines):
        if "<TBD>" not in line and "<既有 id>" not in line:
            continue
        for db_name, db_id in name_to_id.items():
            if not db_id:
                continue
            if f"`{db_name}`" in line:
                lines[i] = line.replace("`<TBD>`", f"`{db_id}`")
                changed += 1
                break
        # 順便處理 news_db <既有 id>
        if "`news_db`" in lines[i]:
            news_id = name_to_id.get("news_db")  # 不會有，因為 news_db 不在 DATABASES
            # 但既有 id 在 secrets，從外面傳進來
            pass

    # 把「api_key」例子改正（順便修小坑）
    lines = [
        l.replace('SECRETS["notion_api_key"]', 'SECRETS["notion_key"]')
         .replace('config/secrets.json` → `"notion_api_key"',
                  'config/secrets.json` → `"notion_key"')
        for l in lines
    ]

    new_text = "\n".join(lines)

    # 加入更新時戳
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    if "_最後 setup_notion_databases.py 寫入" in new_text:
        # 替換既有時戳
        import re
        new_text = re.sub(
            r"_最後 setup_notion_databases\.py 寫入: .*_",
            f"_最後 setup_notion_databases.py 寫入: {stamp}_",
            new_text,
        )
    else:
        new_text += f"\n\n---\n_最後 setup_notion_databases.py 寫入: {stamp}_\n"

    tmp = RUNBOOK_PATH.with_suffix(".md.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(RUNBOOK_PATH)
    print(f"  [+] runbook 更新: 替換 {changed} 筆 <TBD>")
    return True


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="不實際呼叫 Notion write API")
    parser.add_argument("--resume", action="store_true",
                        help="從中斷處續跑（行為與正常跑相同，因為 idempotent）")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"B4 setup_notion_databases.py")
    print(f"執行模式: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    secrets = load_secrets()
    api_key = secrets.get("notion_key")
    if not api_key:
        print("[ERROR] secrets.json 找不到 notion_key")
        sys.exit(1)

    # ============================================================
    # Step 1: 列出 hub page 底下既有 child，產 audit 檔
    # ============================================================
    print("[Step 1] 偵測 hub page 既有 children …")
    children = list_hub_children(api_key, HUB_PAGE_ID)
    existing_dbs_by_title = {}
    audit_lines = [
        f"# B4 audit: hub page {HUB_PAGE_ID} 既有 children",
        f"產出時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"總計: {len(children)} 個 child block",
        "",
    ]
    for c in children:
        line = f"  {c['type']:18s}  {c['id']}  title={c['title']}"
        print(line)
        audit_lines.append(line)
        if c["type"] == "child_database":
            existing_dbs_by_title[c["title"]] = c["id"]

    # 對既有 db 取 schema 寫進 audit
    audit_lines.append("")
    audit_lines.append("## 既有 db schema (供你人工審視)")
    for title, db_id in existing_dbs_by_title.items():
        try:
            schema = get_db_schema(api_key, db_id)
            props = list(schema.get("properties", {}).keys())
            audit_lines.append(f"- {title} ({db_id}) properties={len(props)}: {props}")
        except NotionError as e:
            audit_lines.append(f"- {title} ({db_id}) ERROR: {e}")

    AUDIT_PATH.write_text("\n".join(audit_lines), encoding="utf-8")
    print(f"\n  [+] audit 寫入 {AUDIT_PATH}\n")

    # ============================================================
    # Step 2: 第一輪 — 建所有 db (base properties only)
    # ============================================================
    print("[Step 2] 第一輪: 建立 base properties (relations 後補)")
    db_ids = {}  # secrets_key -> db_id

    for db in DATABASES:
        title = db["title"]
        secrets_key = db["secrets_key"]

        # idempotency check
        if title in existing_dbs_by_title:
            db_ids[secrets_key] = existing_dbs_by_title[title]
            print(f"  [skip] {title} 已存在 → {db_ids[secrets_key]}")
            continue

        print(f"  [create] {title} …")
        try:
            result = create_database(
                api_key, HUB_PAGE_ID, title, db["base_properties"],
                dry_run=args.dry_run,
            )
            db_ids[secrets_key] = result["id"]
            if not args.dry_run:
                print(f"           → {result['id']}")
        except NotionError as e:
            print(f"  [FAIL] {title}: {e}")
            print(f"  停下來。已建的 db 已存進 secrets。請查 audit 後決定 resume 或人工處理。")
            # 把已建的 id 即時寫進 secrets，避免下次重跑漏掉
            if not args.dry_run and db_ids:
                _write_secrets(secrets, db_ids)
            sys.exit(2)
        time.sleep(SLEEP_AFTER_DB)

    # ============================================================
    # Step 3: 寫 secrets.json (第一輪結果，避免下一步失敗導致 id 遺失)
    # ============================================================
    if not args.dry_run:
        _write_secrets(secrets, db_ids)
        print(f"\n  [+] secrets.json 已更新 ({len(db_ids)} 個新 db_id)")

    # ============================================================
    # Step 4: 第二輪 — patch 加上 relation
    # ============================================================
    print("\n[Step 3] 第二輪: 加上 relation properties")
    for db in DATABASES:
        if not db["relations"]:
            continue
        secrets_key = db["secrets_key"]
        db_id = db_ids.get(secrets_key)
        if not db_id or db_id.startswith("DRY-RUN-"):
            if args.dry_run:
                print(f"  [DRY-RUN] {db['title']} 會 patch relations: {[r[0] for r in db['relations']]}")
            continue

        # 檢查 schema，已存在的 relation prop 不重 patch
        try:
            current = get_db_schema(api_key, db_id)
            current_props = set(current.get("properties", {}).keys())
        except NotionError as e:
            print(f"  [WARN] {db['title']} 取 schema 失敗: {e}，跳過 relation patch")
            continue

        relation_map = {}
        for prop_name, target_secrets_key in db["relations"]:
            target_db_id = db_ids.get(target_secrets_key)
            if not target_db_id:
                print(f"  [WARN] {db['title']} → {prop_name} 找不到目標 {target_secrets_key}")
                continue
            if prop_name in current_props:
                print(f"  [skip] {db['title']}.{prop_name} 已存在")
                continue
            relation_map[prop_name] = target_db_id

        if not relation_map:
            continue

        print(f"  [patch] {db['title']} 加入 {list(relation_map.keys())}")
        try:
            patch_database_relations(api_key, db_id, relation_map, dry_run=args.dry_run)
        except NotionError as e:
            print(f"  [FAIL] {db['title']} relation patch: {e}")
            print(f"  繼續其他 db (relation 可後續手動補)")
            continue
        time.sleep(SLEEP_AFTER_DB)

    # ============================================================
    # Step 5: 更新 how-to-notion.md
    # ============================================================
    print("\n[Step 4] 更新 memory/runbooks/how-to-notion.md")
    if not args.dry_run:
        update_runbook_with_db_ids(db_ids)
    else:
        print("  [DRY-RUN] 跳過 runbook 寫入")

    # ============================================================
    # 完成
    # ============================================================
    print(f"\n{'='*60}")
    print(f"B4 setup 完成")
    print(f"建立/重用 db: {len(db_ids)} 個")
    print(f"audit: {AUDIT_PATH}")
    print(f"secrets: {SECRETS_PATH}")
    print(f"runbook: {RUNBOOK_PATH}")
    print(f"{'='*60}\n")


def _write_secrets(secrets, db_ids):
    """合併新 db_id 進 secrets，atomic write。"""
    for k, v in db_ids.items():
        if v and not v.startswith("DRY-RUN-"):
            secrets[k] = v
    save_secrets(secrets)


if __name__ == "__main__":
    main()
