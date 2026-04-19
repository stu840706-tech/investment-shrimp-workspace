#!/usr/bin/env python3
"""
任務 1：新聞摘要過濾
每日 07:30 Taiwan Time 執行
TWSE/Tpex 官方新聞 + UDN RSS，過濾情緒性用字，多來源印證，寫入 Notion，發送 Telegram 早報
"""

from _common import NOTION_KEY, NOTION_LEGACY_DB, TELEGRAM_TOKEN, TELEGRAM_DM
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

import requests
import xml.etree.ElementTree as ET

# ============================================================
# 設定
# ============================================================
STATE_DIR = Path(__file__).parent.parent / "state"
STATE_DIR.mkdir(exist_ok=True)
STATE_FILE = STATE_DIR / "news_tracker.json"

NOTION_TOKEN = NOTION_KEY
NOTION_DATABASE = NOTION_LEGACY_DB

TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

# 情緒性關鍵字（標題含這些不處理）
EMOTIONAL_WORDS = [
    '爆', '噴', '暴涨', '暴跌', '狂瀉', '狂飆', '傻眼', '驚呆',
    '散戶小心', '主力慣壞', '韭菜', '誘多', '誘空', '刀口', '血肉模糊',
    '感謝有人跳車', '恭喜暴賺', '睡公園', '爽賺', '哭暈', '崩盤',
]

# 正面關鍵字
POSITIVE_KEYWORDS = [
    '擴廠', '資本支出上修', '取得重大合約', '策略合作', '新產線', '量產',
    '導入客戶', '獲利超標', '營收創高', '訂單湧入', '產能滿載', '新訂單',
    '轉單', '獨家供應', '市佔率提升', '授權金', '專利', '出貨超標',
    '目標價', '評等上調', '買進', '成長',
]

# 負面關鍵字（緊急）
NEGATIVE_KEYWORDS = [
    '裁員', '停工', '火災', '客戶終止', '違約', '解任', '辭職',
    '假帳', '掏空', '內線', '操縱股價', '下市', '破產',
]

# 產業關鍵字
INDUSTRY_KEYWORDS = [
    '半導體', 'AI', '電子', '傳產', '金融', '航運', '鋼鐵', '塑化',
    '能源', '車用', '製藥', '生技', '紡織', '水泥', '面板', 'LED',
]

# 台股相關關鍵字
STOCK_KEYWORDS = [
    '台積電', '聯發科', '鴻海', '聯電', '中華電', '富邦金', '國泰金',
    '中信金', '兆豐金', '華南金', '第一金', '開發金', '元大金',
    '台泥', '亞泥', '長榮', '陽明', '萬海', '華航', '長榮航',
    '台塑', '南亞', '台化', '遠東新', '中鋼', '中鴻', '燁輝',
    '廣達', '仁寶', '緯創', '英業達', '和碩', '佳世達', '友達',
    '群創', '彩晶', '可成', '華碩', '宏碁', '聯強', '通路',
    '元太', '億光', '隆達', '晶電', '佰鴻', '光寶', '仁星',
    '穩懋', '宏捷科', '全新', '聯鈞', '華星光', '聯亞', '矽創',
    '瑞昱', '聯詠', '奇景光電', '敦泰', '義隆', '松翰', '纾',
    '日月光', '矽品', '京元電子', '欣興', '景硕', '南電', '華通',
    '燿華', '敬鵬', '嘉聯益', '台郡', '華通',
]


# ============================================================
# 工具函數
# ============================================================

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"last_run": None, "seen_urls": []}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_rss(url: str) -> List[Dict]:
    """抓取並解析 RSS 格式"""
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        resp.encoding = 'utf-8'
        root = ET.fromstring(resp.text)
        items = []
        for item in root.findall('.//item'):
            title_el = item.find('title')
            link_el = item.find('link')
            desc_el = item.find('description')
            pub_el = item.find('pubDate')
            
            title = ''
            if title_el is not None and title_el.text:
                # 移除 CDATA 標記
                title = re.sub(r'<!\[CDATA\[|\]\]>', '', title_el.text).strip()
            
            link = ''
            if link_el is not None and link_el.text:
                link = link_el.text.strip()
            
            desc = ''
            if desc_el is not None and desc_el.text:
                desc = re.sub(r'<!\[CDATA\[|\]\]>', '', desc_el.text).strip()
                desc = re.sub(r'<[^>]+>', '', desc)  # 移除 HTML 標籤
                desc = desc[:200]
            
            pub = ''
            if pub_el is not None and pub_el.text:
                pub = pub_el.text.strip()
            
            if title and len(title) > 8:
                items.append({
                    'title': title,
                    'url': link,
                    'snippet': desc,
                    'source': 'UDN 經濟日報',
                    'date': pub,
                })
        return items
    except Exception as e:
        print(f"    RSS 失敗 {url}: {e}")
        return []


def get_udn_rss() -> List[Dict]:
    """UDN 經濟日報 RSS"""
    return fetch_rss('https://money.udn.com/rssfeed/news')


def get_udn_stock_rss() -> List[Dict]:
    """UDN 股市 RSS"""
    return fetch_rss('https://money.udn.com/rssfeed/stock')


def is_emotional_title(title: str) -> bool:
    """判斷標題是否為情緒性"""
    for word in EMOTIONAL_WORDS:
        if word in title:
            return True
    return False


def classify_signal(title: str, snippet: str = '') -> tuple:
    """分類訊號等級"""
    text = (title + ' ' + snippet).lower()
    
    # 緊急負面 → 高
    for kw in NEGATIVE_KEYWORDS:
        if kw in text:
            return '高', f'負面關鍵字: {kw}'
    
    # 正面關鍵字 → 中/高
    pos_count = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    if pos_count > 0:
        has_numbers = bool(re.search(r'\d+[%％元億萬]', text))
        return ('高', f'正面: {pos_count}個，有數字') if has_numbers else ('中', f'正面: {pos_count}個')
    
    # 台股相關
    stock_count = sum(1 for kw in STOCK_KEYWORDS if kw in text)
    if stock_count > 0:
        return '中', f'台股相關: {stock_count}個'
    
    # 產業關鍵字 → 低
    ind_count = sum(1 for kw in INDUSTRY_KEYWORDS if kw in text)
    if ind_count > 0:
        return '低', f'產業: {ind_count}個'
    
    return '低', '一般'


def extract_stock_codes(text: str) -> list:
    """從文字提取股票代碼（4位數字）"""
    codes = re.findall(r'\b([0-9]{4})\b', text)
    return [c for c in codes if not c.startswith(('19', '20'))]


def deduplicate(news_items: list) -> list:
    """去除標題重複"""
    unique = []
    seen = set()
    for item in news_items:
        key = item['title'][:25].lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def send_telegram(text: str) -> bool:
    """發送 Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': TELEGRAM_DM, 'text': text}, timeout=10)
        return resp.status_code == 200
    except:
        return False


def notion_write(database_id: str, properties: dict, content: str = '') -> bool:
    """寫入 Notion"""
    url = "https://api.notion.com/v1/pages"
    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Content-Type': 'application/json',
        'Notion-Version': '2022-06-28',
    }
    
    page_props = {}
    for key, value in properties.items():
        if key == "日期" and value:
            # 嘗試解析日期格式
            date_str = value[:10] if len(value) >= 10 else value
            page_props[key] = {"date": {"start": date_str}}
        elif key == "標題":
            page_props[key] = {"title": [{"text": {"content": str(value)[:200]}}]}
        elif isinstance(value, bool):
            page_props[key] = {"checkbox": value}
        elif isinstance(value, (int, float)):
            page_props[key] = {"number": value}
        elif isinstance(value, str):
            page_props[key] = {"rich_text": [{"text": {"content": value[:2000]}}]}
    
    payload = {
        "parent": {"database_id": database_id},
        "properties": page_props,
    }
    
    if content:
        payload["children"] = [{
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]}
        }]
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        return resp.status_code == 200
    except:
        return False


# ============================================================
# 主程式
# ============================================================

def main():
    print("=" * 60)
    print("任務 1：新聞摘要過濾")
    print("=" * 60)
    
    state = load_state()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    if state.get("last_run") == today_str:
        print(f"[跳过] 今日已執行過 ({today_str})")
        return
    
    all_articles = []
    
    # 來源 1: UDN 經濟日報 RSS
    print("  [UDN 經濟日報 RSS]")
    articles = get_udn_rss()
    all_articles.extend(articles)
    print(f"    → {len(articles)} 筆")
    
    time.sleep(0.5)
    
    # 來源 2: UDN 股市 RSS
    print("  [UDN 股市 RSS]")
    articles = get_udn_stock_rss()
    all_articles.extend(articles)
    print(f"    → {len(articles)} 筆")
    
    print(f"\n  總計: {len(all_articles)} 筆")
    
    # 去除重複
    all_articles = deduplicate(all_articles)
    print(f"  去重後: {len(all_articles)} 筆")
    
    # 過濾情緒性
    filtered = [a for a in all_articles if not is_emotional_title(a['title'])]
    print(f"  去除情緒性後: {len(filtered)} 筆")
    
    # 分類
    for item in filtered:
        level, reason = classify_signal(item['title'], item.get('snippet', ''))
        item['signal_level'] = level
        item['reason'] = reason
        item['stock_codes'] = extract_stock_codes(item['title'] + ' ' + item.get('snippet', ''))
    
    # 按等級排序
    priority = {'高': 0, '中': 1, '低': 2}
    filtered.sort(key=lambda x: (priority.get(x['signal_level'], 3), -len(x['title'])))
    
    high = [n for n in filtered if n['signal_level'] == '高']
    medium = [n for n in filtered if n['signal_level'] == '中']
    low = [n for n in filtered if n['signal_level'] == '低']
    
    print(f"  高: {len(high)} | 中: {len(medium)} | 低: {len(low)}")
    
    # 發送 Telegram 早報
    print("\n  [Telegram 早報]")
    if not high and not medium:
        send_telegram("📰 投資蝦晨報\n\n今日無顯著新信號\n\n" + datetime.now().strftime("%H:%M"))
        print("    → 無顯著信號")
    else:
        lines = ["📰 投資蝦晨報", ""]
        count = 0
        
        for item in high + medium:
            if count >= 5:
                break
            title = item['title'][:55] + ('...' if len(item['title']) > 55 else '')
            source = item.get('source', '')
            reason = item.get('reason', '')
            codes = ', '.join(item.get('stock_codes', [])[:3])
            
            emoji = '🔴' if item['signal_level'] == '高' else '🟡'
            lines.append(f"{emoji} {title}")
            lines.append(f"   {source} | {reason}")
            if codes:
                lines.append(f"   代碼: {codes}")
            lines.append("")
            count += 1
        
        lines.append(f"時間: {datetime.now().strftime('%H:%M')}")
        lines.append(f"來源: UDN 經濟日報 RSS")
        
        text = '\n'.join(lines)
        if send_telegram(text):
            print(f"    → 已發送 {count} 則")
        else:
            print("    → 發送失敗")
    
    # 寫入 Notion
    print("\n  [Notion 寫入]")
    written = 0
    for item in filtered[:30]:
        # 嘗試解析日期
        date_str = item.get('date', today_str)
        if date_str:
            # UDN RSS 日期格式: "Mon, 13 Apr 2026 02:48:55 +0800"
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str)
                date_str = dt.strftime('%Y-%m-%d')
            except:
                date_str = today_str
        
        props = {
            "日期": date_str,
            "標題": item['title'][:200],
            "摘要": item.get('snippet', '')[:500],
            "來源": item.get('source', ''),
            "訊號等級": item['signal_level'],
            "多源印證": False,
            "事實標記": item.get('reason', ''),
        }
        
        if notion_write(NOTION_DATABASE, props, item.get('snippet', '')):
            written += 1
        time.sleep(0.3)
    
    print(f"    → 寫入 {written} 筆")
    
    # 更新狀態
    state['last_run'] = today_str
    state['total'] = len(all_articles)
    state['high'] = len(high)
    state['medium'] = len(medium)
    save_state(state)
    
    print(f"\n完成：共 {len(filtered)} 則 (高:{len(high)} 中:{len(medium)} 低:{len(low)})")


if __name__ == "__main__":
    main()
