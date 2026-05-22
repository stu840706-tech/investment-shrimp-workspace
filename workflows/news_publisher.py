#!/usr/bin/env python3
"""
Layer 3: Publisher Agent
讀取 processed jsonl → 格式化 → Telegram + Notion

Usage: python3 news_publisher.py [HH]
"""

from _common import NOTION_KEY, NOTION_NEWS_DB, TELEGRAM_TOKEN, TELEGRAM_DM, TELEGRAM_GROUP, today_tw_str, SECRETS
import json, sys, re, time, requests

COMPANY_NAMES = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '2317': '鴻海',
    '2408': '南亞科',
    '2881': '富邦金', '2344': '華邦電', '2337': '旺宏', '8299': '群聯', '3037': '欣興', '4958': '臻鼎',
    '2881': '富邦金',
    '3712': '大聯大', '6175': '聯亞', '8086': '宏捷科', '6274': '台燿',
    '2383': '台光電', '3122': '萬潤', '3711': '日月光', '8261': '矽品',
    '3189': '景碩', '3037': '欣興', '3105': '穩懋', '2409': '友達',
    '3481': '群創', '6515': '穎崴', '2345': '智邦', '2327': '國巨', '6116': '彩晶', '2357': '華碩', '2353': '宏碁',
    '2376': '技嘉', '2377': '微星', '6150': '撼訊', '2399': '映泰',
    '3443': '創意', '3035': '智原', '3661': '世芯', '6531': '愛普',
    '3529': '力旺', '3131': '弘塑', '6939': '天虹', '2456': '全新',
    '6213': '聯茂', '6269': '台郡', '2313': '華通', '2367': '燿華',
    '8046': '南電', '3528': '景崎', '1560': '中砂', '6442': '光聖',
    '6285': '啟碁', '3312': '至上', '1319': '東陽',
}


SOURCE_ABBREV = {
    'Google新聞': 'Google', 'TechCrunch': 'TC', 'Bloomberg': 'BBG',
    'DigiTimes': 'DigiTimes', 'CNBC': 'CNBC', 'Reuters': 'Reuters',
    'Investing.com': 'Invest', 'Investing': 'Invest',
}

def abbrev_source(src):
    return SOURCE_ABBREV.get(src, src[:10])

def fmt_hdr(it):
    companies = list(dict.fromkeys(it.get('companies', [])))
    dyn = it.get('company_names', {}) or {} # LLM 動態填的公司名稱
    tag = it.get('tag', '')
    tag_str = ' 🟢' if tag == '利多' else (' 🔴' if tag == '利空' else '')
    if companies:
        parts = []
        for c in companies[:3]:
            name = COMPANY_NAMES.get(c) or dyn.get(c, '')
            parts.append(f'{c} {name}' if name else c)
        return '▸ ' + ' / '.join(parts) + tag_str
    return '▸ ' + it.get('title', '')[:45] + tag_str

def make_concl(impact, max_len=40):
    if not impact:
        return ''
    for sep in ['。', '，', '；']:
        idx = impact.find(sep)
        if 4 < idx <= max_len:
            return impact[:idx]
    return impact[:max_len]

def fix_yr(text, year):
    if not text:
        return text
    return re.sub(r'202[01]年', str(year) + '年', text)

from datetime import datetime, timedelta, timezone
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent / "memory"
STATE_DIR = Path(__file__).parent.parent / "state"
NOTION_TOKEN = NOTION_KEY
NOTION_DB = SECRETS.get('notion_news_db', NOTION_NEWS_DB)
NOTION_WORKSPACE = "阿凱投資亂糟糟"
TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

def get_hour_arg():
    """Returns (hour, date_str)"""
    if len(sys.argv) >= 2:
        hour = sys.argv[1].zfill(2)
    else:
        _tw = datetime.now(tz=timezone.utc).astimezone(timezone(timedelta(hours=8)))
        hour = "07" if _tw.hour < 12 else "19"
    date_str = sys.argv[2] if len(sys.argv) >= 3 else datetime.now().strftime("%Y%m%d")
    return hour, date_str

def load_processed(hour, date_str):
    """讀取 processed jsonl"""
    timestamp = f"{date_str}-{hour}"
    f = MEMORY_DIR / f"processed-{timestamp}.jsonl"
    if not f.exists():
        return None
    with open(f, encoding='utf-8') as fh:
        line = fh.readline().strip()
        if line:
            return json.loads(line)
    return None

def get_market_data():
    """取得台股加權指數背景數據"""
    try:
        import urllib.request
        req = urllib.request.Request(
            'https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            taiex = next((d for d in data if '發行量加權股價指數' in d.get('指數','')), None)
            if not taiex:
                return "今日台股 N/A"
            roc = taiex.get('日期', '')
            if len(roc) == 7:
                date_str = f"{int(roc[:3])+1911}/{roc[3:5]}/{roc[5:7]}"
            else:
                date_str = roc
            sign = taiex.get('漲跌', '+')
            change = taiex.get('漲跌點數', 'N/A')
            pct = taiex.get('漲跌百分比', 'N/A')
            return f"台股({date_str}) {sign}{change}點 ({pct}%)"
    except Exception:
        pass
    return "今日台股 N/A"

def format_company_list(companies):
    """格式化公司代號列表"""
    if not companies:
        return ""
    return " ".join(companies[:5])  # 最多5個

def send_telegram(text):
    """發送到 Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': TELEGRAM_DM, 'text': text}, timeout=15)
        return resp.status_code == 200, resp.text[:200]
    except Exception as e:
        return False, str(e)

def notion_archive(date_str, period, high_count, content_text):
    """存檔到 Notion"""
    # Find or create the database
    # First try to find existing page for this date
    search_url = "https://api.notion.com/v1/databases/" + NOTION_DB + "/query"
    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Content-Type': 'application/json',
        'Notion-Version': '2022-06-28',
    }
    
    # Check if page for this date already exists
    try:
        resp = requests.post(search_url, headers=headers, json={
            "filter": {
                "property": "日期",
                "rich_text": {"equals": date_str}
            }
        }, timeout=10)
        if resp.status_code == 200:
            existing = resp.json().get('results', [])
            if existing:
                # Update existing page
                page_id = existing[0]['id']
                update_url = f"https://api.notion.com/v1/pages/{page_id}"
                upd = requests.patch(update_url, headers=headers, json={
                    "properties": {
                        "時段": {"select": {"name": period}},
                        "高重要性則數": {"number": high_count},
                        "完整簡報": {"rich_text": [{"text": {"content": content_text[:2000]}}]}
                    }
                }, timeout=10)
                return True, "Updated existing page"
    except Exception as e:
        pass
    
    # Create new page
    try:
        create_url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": NOTION_DB},
            "properties": {
                "日期": {"title": [{"text": {"content": date_str}}]},
                "時段": {"select": {"name": period}},
                "高重要性則數": {"number": high_count},
            },
            "children": [{
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": content_text[:2000]}}]
                }
            }]
        }
        resp = requests.post(create_url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            return True, "Created new page"
        else:
            return False, f"Notion error: {resp.status_code} - {resp.text[:200]}"
    except Exception as e:
        return False, f"Notion exception: {str(e)}"

def cluster_sources(items):
    """直接使用 processed 資料的 sources_list 和 source_count，不再重新計算"""
    return items  # processed already has all cluster info


SENT_STATE_FILE = Path(__file__).parent.parent / "state" / "news_brief_sent.json"

def check_already_sent(today_str: str, period: str) -> bool:
    """檢查今天這個時段是否已發送過 Telegram 簡報"""
    try:
        if SENT_STATE_FILE.exists():
            state = json.loads(SENT_STATE_FILE.read_text(encoding="utf-8"))
            key = f"{today_str}-{period}"
            if state.get(key):
                print(f"[SKIP] 今天 {period} 簡報已發送過（{key}），跳過")
                return True
    except Exception as e:
        print(f"[WARN] 讀取 sent state 失敗：{e}")
    return False

def mark_sent(today_str: str, period: str):
    """記錄今天這個時段已發送"""
    try:
        state = {}
        if SENT_STATE_FILE.exists():
            state = json.loads(SENT_STATE_FILE.read_text(encoding="utf-8"))
        key = f"{today_str}-{period}"
        state[key] = True
        # 只保留最近 30 天的記錄（每個時段2個key，所以60是30天的量）
        if len(state) > 60:
            keys = sorted(state.keys())
            for old_key in keys[:-60]:
                del state[old_key]
        SENT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[WARN] 寫入 sent state 失敗：{e}")


def main():
    hour, date_str = get_hour_arg()
    from datetime import timezone, timedelta
    TZ_TAIPEI = timezone(timedelta(hours=8))
    # Normalize date_str (YYYYMMDD) to YYYY-MM-DD for sent_state key
    if len(date_str) == 8:
        today_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    else:
        today_str = date_str
    now = datetime.now(tz=timezone.utc).astimezone(TZ_TAIPEI)
    # 優先使用 pipeline 傳入的 period 參數，避免重複 +8 換算
    if len(sys.argv) >= 4:
        period = sys.argv[3]  # "AM" or "PM" from pipeline
    else:
        period = "AM" if hour == "07" else "PM"
    
    print("=" * 55)
    print(f"新聞發布 Layer 3  {now.strftime('%Y-%m-%d %H:%M')} ({period})")
    print("=" * 55)
    
    # 讀取 processed
    print(f"\n[1] 讀取 processed 檔案...")
    data = load_processed(hour, date_str)
    if not data:
        print("  錯誤：找不到 processed 檔案")
        sys.exit(1)
    
    items = data.get('items', [])
    print(f"  總計：{len(items)} 筆")
    
    # 過濾 skip 項目
    active_items = [it for it in items if it.get('signal') != 'skip']
    skipped = len(items) - len(active_items)
    print(f"  有效：{len(active_items)} 筆（跳過：{skipped} 筆）")
    
    # 取得台股背景
    print(f"\n[2] 取得台股背景...")
    market_bg = get_market_data()
    print(f"  {market_bg}")
    
    # 彙總相同 cluster
    print(f"\n[3] 彙總 cluster...")
    clustered = active_items
    print(f"  彙總後：{len(clustered)} 筆")
    
    # 分類
    high = [it for it in clustered if it.get('signal') == 'high']
    medium = [it for it in clustered if it.get('signal') == 'medium']
    low = [it for it in clustered if it.get('signal') == 'low']
    
    print(f"  高：{len(high)}，中：{len(medium)}，低：{len(low)}")
    

    _seen_keys = set()
    def _dedup(lst):
        out = []
        for it in lst:
            k = it.get('title', '')[:40]
            if k in _seen_keys: continue
            _seen_keys.add(k)
            out.append(it)
        return out
    high = _dedup(high)
    medium = _dedup(medium)

    # 去重項目分離：signal=medium 但 fact 含去重字串 → 降到 ⚪
    DEDUP_MARKER = "7天內已出現"
    INSUFF_MARKER = "資訊量不足"
    medium_clean = [it for it in medium if DEDUP_MARKER not in it.get("fact", "") and INSUFF_MARKER not in it.get("impact", "")]
    medium_dup = [it for it in medium if DEDUP_MARKER in it.get("fact", "")]
    medium_insuff = [it for it in medium if INSUFF_MARKER in it.get("impact", "") and DEDUP_MARKER not in it.get("fact", "")]

    # 格式化輸出
    print(f"\n[4] 格式化簡報...")
    SEP = "─" * 22
    CUR_YEAR = now.year
    period_label = '晨報' if period == 'AM' else '晚報'
    lines = []
    lines.append(f"📰 投資蝦{period_label} {now.strftime('%m/%d %H:%M')}")
    lines.append(market_bg)
    lines.append(f"處理 {len(items)} 則 | 🔴高{len(high)} 🟡中{len(medium_clean)}")

    if high:
        lines.append("")
        lines.append(f"🔴 高重要性（{len(high)}則）")
        lines.append(SEP)
        for it in high[:8]:
            lines.append(fmt_hdr(it))
            impact = fix_yr(it.get('impact', ''), CUR_YEAR)
            fact = fix_yr(it.get('fact', ''), CUR_YEAR)
            if fact and fact not in ('無具體數字', ''):
                lines.append(f"核心事實：{fact}")
            if impact:
                lines.append(f"影響：{impact}")
            if it.get('cluster_count', 1) >= 2:
                lines.append(f"✅ {it['cluster_count']}個來源")
            elif it.get('paywall'):
                lines.append("⚠️ 需人工確認全文")
            lines.append("")

    if medium_clean:
        lines.append(f"🟡 中重要性（{len(medium_clean)}則）")
        lines.append(SEP)
        for it in medium_clean[:8]:
            lines.append(fmt_hdr(it))
            impact = fix_yr(it.get('impact', ''), CUR_YEAR)
            fact = fix_yr(it.get('fact', ''), CUR_YEAR)
            if fact and fact not in ('無具體數字', ''):
                lines.append(f"核心事實：{fact}")
            if impact:
                lines.append(f"影響：{impact}")
            lines.append("")

    pending_items = [it for it in clustered if it.get('signal') == 'pending']
    valid_low = [it for it in low if len(it.get('title', '')) >= 15]
    compact_items = _dedup(pending_items + valid_low + medium_dup + medium_insuff)[:9]
    if compact_items:
        lines.append(SEP)
        lines.append("⚪ 待驗證 / 低重要")
        for it in compact_items:
            companies = list(dict.fromkeys(it.get('companies', [])))
            if companies:
                c = companies[0]
                name = it.get('company_names', {}).get(c, '') or COMPANY_NAMES.get(c, '')
                label = f"{c} {name}" if name else c
            else:
                label = it.get('title', '')[:45]
            fact = it.get('fact', '')
            if fact and fact not in ('無具體數字', '有具體數字', ''):
                lines.append(f"• {label}｜{fact[:45]}")
            else:
                lines.append(f"• {label}")

        lines.append("")
        lines.append(SEP)

    source_count = {}
    for it in active_items:
        src = it.get('source', '')
        if src:
            source_count[src] = source_count.get(src, 0) + 1
    if source_count:
        src_parts = [f"{abbrev_source(k)}*{v}" for k, v in sorted(source_count.items(), key=lambda x: -x[1])]
        lines.append(f"📡 {' '.join(src_parts)}")
    lines.append(f"⏰ {now.strftime('%H:%M')}")

    
    text = "\n".join(lines)
    print("\n" + text[:1500])
    
    # 發送 Telegram
    print(f"\n[5] 發送 Telegram...")
    # 防重複：檢查今天這個時段是否已發送過
    if check_already_sent(today_str, period):
        return

    ok, resp = send_telegram(text)
    if ok:
        mark_sent(today_str, period)
    # Notion 存檔（寫入「投資蝦晨報」資料庫）
    print(f"\n[6] Notion 存檔...")
    notion_headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    
    NOTION_MORNING_DB = SECRETS.get("notion_news_db", "342226f5-a398-81ab-bfeb-fefda2d30a68")
    
    page_payload = {
        "parent": {"database_id": NOTION_MORNING_DB},
        "properties": {
            "日期": {"title": [{"text": {"content": today_str}}]},
            "時段": {"select": {"name": period}},
            "高重要性則數": {"number": len(high)},
            "完整簡報": {"rich_text": [
 {"type": "text", "text": {"content": text[i:i+1800]}}
 for i in range(0, min(len(text), 9000), 1800)
 ]},
        }
    }
    
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=notion_headers, json=page_payload, timeout=15)
        if resp.status_code == 200:
            print(f"  ✅ Notion 寫入成功")
        else:
            print(f"  ❌ 寫入失敗：{resp.text[:150]}")
    except Exception as e:
        print(f"  ❌ 寫入錯誤：{e}")
    
    print(f"\n完成！")

if __name__ == "__main__":
    main()