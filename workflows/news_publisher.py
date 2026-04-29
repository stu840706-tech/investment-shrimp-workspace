#!/usr/bin/env python3
"""
Layer 3: Publisher Agent
讀取 processed jsonl → 格式化 → Telegram + Notion

Usage: python3 news_publisher.py [HH]
"""

from _common import NOTION_KEY, NOTION_NEWS_DB, TELEGRAM_TOKEN, TELEGRAM_DM, TELEGRAM_GROUP, today_tw_str
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
from datetime import datetime, timedelta
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent / "memory"
STATE_DIR = Path(__file__).parent.parent / "state"
NOTION_TOKEN = NOTION_KEY
NOTION_DB = NOTION_NEWS_DB
NOTION_WORKSPACE = "阿凱投資亂糟糟"
TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

def get_hour_arg():
    if len(sys.argv) >= 2:
        return sys.argv[1].zfill(2)
    h = datetime.now().strftime("%H")
    return "07" if int(h) < 12 else "19"

def load_processed(hour):
    """讀取 processed jsonl"""
    today = today_tw_str()
    timestamp = f"{today}-{hour}"
    f = MEMORY_DIR / f"processed-{timestamp}.jsonl"
    if not f.exists():
        return None
    with open(f, encoding='utf-8') as fh:
        line = fh.readline().strip()
        if line:
            return json.loads(line)
    return None

def get_market_data():
    """取得台股背景數據"""
    try:
        import urllib.request
        req = urllib.request.Request(
            'https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX',
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data:
                idx = data[0]
                direction = idx.get('漲跌', '+')
                change = idx.get('漲跌點數', 'N/A')
                pct = idx.get('漲跌百分比', 'N/A')
                return f"今日台股 {direction}{change}點 ({pct}%)"
    except:
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

def main():
    hour = get_hour_arg()
    from datetime import timezone, timedelta
    TZ_TAIPEI = timezone(timedelta(hours=8))
    now = datetime.now(tz=timezone.utc).astimezone(TZ_TAIPEI)
    taipei_hour = (int(hour) + 8) % 24
    period = "AM" if taipei_hour < 12 else "PM"
    today_str = now.strftime("%Y-%m-%d")
    
    print("=" * 55)
    print(f"新聞發布 Layer 3  {now.strftime('%Y-%m-%d %H:%M')} ({period})")
    print("=" * 55)
    
    # 讀取 processed
    print(f"\n[1] 讀取 processed 檔案...")
    data = load_processed(hour)
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
    clustered = cluster_sources(active_items)
    print(f"  彙總後：{len(clustered)} 筆")
    
    # 分類
    high = [it for it in clustered if it.get('signal') == 'high']
    medium = [it for it in clustered if it.get('signal') == 'medium']
    low = [it for it in clustered if it.get('signal') == 'low']
    
    print(f"  高：{len(high)}，中：{len(medium)}，低：{len(low)}")
    
    # 格式化輸出
    print(f"\n[4] 格式化簡報...")
    lines = []
    lines.append(f"📰 投資蝦{'晨報' if period=='AM' else '晚報'} {now.strftime('%m/%d %H:%M')}")
    lines.append(market_bg)
    lines.append(f"處理 {len(items)} 則 | 🔴高{len(high)} 🟡中{len(medium)}")
    lines.append("")
    
    # 來源總結
    source_count = {}
    for it in active_items:
        src = it.get('source','')
        if src:
            source_count[src] = source_count.get(src, 0) + 1
    if source_count:
        src_parts = [f"{k}*{v}" for k, v in sorted(source_count.items(), key=lambda x: -x[1])]
        lines.append(f"📊 來源：{' '.join(src_parts)}")
        lines.append("")
    
    # 高重要性
    seen_titles = set()
    if high[:8]:
        lines.append("🔴 高重要性")
        for it in high[:8]:
            # 顯示新聞標題（[:50]）而不是公司代碼
            title_label = it.get('title', '')[:50]
            lines.append(f"• {title_label}")
            # 公司代碼/名稱放第二行
            companies = list(dict.fromkeys(it.get('companies', [])))
            if companies:
                company_label = "、".join([f"{c} {COMPANY_NAMES.get(c, c)}" for c in companies[:2]])
                lines.append(f"  {company_label}")
            
            fact = it.get('fact', '')
            if fact and fact != '無具體數字':
                lines.append(f"  核心事實：{fact}")
            else:
                lines.append(f"  核心事實：無具體數字")
            
            impact = it.get('impact', '')
            if impact:
                lines.append(f"  影響：{impact}")
            
            # 來源
            sources_list = it.get('sources_list', [it.get('source','')])
            if sources_list:
                src_parts = [f"{s}*{sources_list.count(s)}" for s in sorted(set(sources_list))]
                lines.append(f"  來源：{len(sources_list)}個來源：{'、'.join(src_parts)}")
            
            # Cluster badge
            if it.get('cluster_count', 1) >= 2:
                lines.append(f"  ✅{it.get('cluster_count')}來源")
            
            # Paywall warning
            if it.get('paywall'):
                lines.append(f"  ⚠️需人工確認全文")
            lines.append("")
        lines.append("")
    
    # 中重要性
    if medium[:5]:
        lines.append("🟡 中重要性")
        for it in medium[:5]:
            # 顯示新聞標題
            title_label = it.get('title', '')[:50]
            lines.append(f"• {title_label}")
            # 公司代碼放第二行
            companies = list(dict.fromkeys(it.get('companies', [])))
            if companies:
                company_label = "、".join([f"{c} {COMPANY_NAMES.get(c, c)}" for c in companies[:2]])
                lines.append(f"  {company_label}")
            
            fact = it.get('fact', '')
            if fact and fact != '無具體數字':
                lines.append(f"  核心事實：{fact}")
            else:
                lines.append(f"  核心事實：無具體數字")
            
            impact = it.get('impact', '')
            if impact:
                lines.append(f"  影響：{impact}")
            
            lines.append(f"  來源：{it.get('source','')}*1")
            lines.append("")
    
    # 待驗證（單一來源的高價值項目）
    pending_items = [it for it in clustered if it.get('signal') == 'pending']
    if pending_items[:5]:
        lines.append("⚪ 待驗證（單一來源）")
        for it in pending_items[:5]:
            title_key = it.get('title', '')[:20]
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            companies = list(dict.fromkeys(it.get('companies', [])))
            if companies:
                parts = []
                for c in companies[:2]:
                    name = COMPANY_NAMES.get(c, '')
                    if name:
                        parts.append(f"{c} {name}" if name and name != c else c)
                    else:
                        parts.append(c)
                label = "、".join(parts)
            else:
                label = it.get('title', '')[:15]
            lines.append(f"• {label}")
            fact = it.get('fact', '')
            if fact and fact not in ['無具體數字', '有具體數字', '']:
                lines.append(f"  核心事實：{fact[:50]}")

    # 低重要性（跳過標題<15字的截斷項目）
    valid_low = [it for it in low if len(it.get('title', '')) >= 15]
    if valid_low[:3]:
        lines.append("⚪ 低重要性")
        for it in valid_low[:3]:
            companies = list(dict.fromkeys(it.get('companies', [])))
            if companies:
                parts = []
                for c in companies[:2]:
                    name = COMPANY_NAMES.get(c, '')
                    if name:
                        parts.append(f"{c} {name}" if name and name != c else c)
                    else:
                        parts.append(c)
                label = "、".join(parts)
            else:
                label = it.get('title', '')[:15]
            lines.append(f"• {label}")
    
    lines.extend(["", f"⏰ {now.strftime('%H:%M')} | 📡 台灣+國際+DigiTimes+MOPS"])
    
    text = "\n".join(lines)
    print("\n" + text[:1500])
    
    # 發送 Telegram
    print(f"\n[5] 發送 Telegram...")
    ok, resp = send_telegram(text)
    # Notion 存檔（寫入「投資蝦晨報」資料庫）
    print(f"\n[6] Notion 存檔...")
    notion_headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    
    NOTION_MORNING_DB = "342226f5-a398-81ab-bfeb-fefda2d30a68"
    period = "AM" if hour == "07" else "PM"
    today_str = now.strftime("%Y-%m-%d")
    
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