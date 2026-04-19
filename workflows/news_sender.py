#!/usr/bin/env python3
"""
任務 1：新聞監控系統 - Phase 2 (規則版，無需 LLM)
直接發送 Telegram 簡報
"""

from _common import TELEGRAM_TOKEN
import json
import re
import time
from datetime import datetime
from pathlib import Path
import requests

STATE_DIR = Path(__file__).parent.parent / "state"
STATE_DIR.mkdir(exist_ok=True)

FETCH_FILE = STATE_DIR / "news_fetch_test.json"
FINGERPRINT_FILE = STATE_DIR / "news_fingerprints.json"

TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN
TELEGRAM_DM = "5604476530"

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

EMOTIONAL_WORDS = [
    '爆', '噴', '暴涨', '暴跌', '狂瀉', '狂飆', '傻眼', '驚呆',
    '散戶小心', '主力慣壞', '韭菜', '誘多', '誘空', '刀口', '血肉模糊',
    '感謝有人跳車', '恭喜暴賺', '睡公園', '爽質', '哭暈', '崩盤',
]

POSITIVE_KEYWORDS = [
    '擴廠', '資本支出', '重大合約', '策略合作', '新產線', '量產',
    '導入客戶', '獲利超標', '營收創高', '訂單湧入', '產能滿載',
    '轉單', '獨家供應', '市佔率提升', '授權金', '專利', '出貨超標',
    '目標價', '評等上調', '買進', '大幅成長', '超標', '創高',
]

NEGATIVE_KEYWORDS = [
    '裁員', '停工', '火災', '客戶終止', '違約', '解任', '辭職',
    '假帳', '掏空', '內線', '操縱股價', '下市', '破產', '裁撤',
]


def is_emotional(title: str) -> bool:
    return any(w in title for w in EMOTIONAL_WORDS)


def neutralize(text: str) -> str:
    replacements = {
        '大漲': '上漲', '暴跌': '大跌', '噴出': '大漲',
        '崩跌': '大跌', '狂瀉': '重挫', '傻眼': '驚訝',
        '慘跌': '下跌', '跳水': '重挫',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def classify(item: dict) -> str:
    text = (item.get('title', '') + ' ' + item.get('snippet', '')).lower()
    
    for kw in NEGATIVE_KEYWORDS:
        if kw in text:
            return '高'
    
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    if pos > 0:
        has_num = bool(re.search(r'\d+[%％元億萬]', text))
        return '高' if has_num else '中'
    
    return '低'


def load_fingerprints() -> dict:
    if FINGERPRINT_FILE.exists():
        with open(FINGERPRINT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"fingerprints": {}, "last_cleanup": None}


def save_fingerprints(db: dict):
    with open(FINGERPRINT_FILE, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def is_recent(published_str: str) -> bool:
    if not published_str or not published_str.strip():
        return True
    
    from datetime import timedelta
    clean = re.sub(r'\s*\(.*\)', '', published_str)
    clean = re.sub(r'\s*GMT[+-]\d{4}', '', clean).strip()
    parts = clean.split()
    if len(parts) >= 5:
        clean = ' '.join(parts[1:])
    
    formats = ["%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S",
               "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            age = datetime.now(dt.tzinfo) - dt if dt.tzinfo else datetime.now() - dt
            return age.total_seconds() < 15 * 3600
        except ValueError:
            continue
    return True


def extract_codes(text: str) -> list:
    codes = re.findall(r'\b([0-9]{4})\b', text)
    return [c for c in codes if not c.startswith(('19', '20'))]


def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={'chat_id': TELEGRAM_DM, 'text': text}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram 失敗: {e}")
        return False


def main():
    print("=" * 50)
    print(f"新聞彙整 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)
    
    if not FETCH_FILE.exists():
        print("無新聞資料")
        return
    
    with open(FETCH_FILE, 'r', encoding='utf-8') as f:
        items = json.load(f)
    
    print(f"原始: {len(items)} 筆")
    
    # 1. 時間過濾
    items = [it for it in items if is_recent(it.get('published', ''))]
    print(f"15小時內: {len(items)} 筆")
    
    # 2. 情緒過濾
    before = len(items)
    items = [dict(it, title=neutralize(it.get('title', ''))) for it in items]
    items = [it for it in items if not is_emotional(it.get('title', ''))]
    print(f"去除情緒後: {len(items)} 筆")
    
    # 3. 分類
    for it in items:
        it['signal'] = classify(it)
        it['codes'] = extract_codes(it.get('title', '') + ' ' + it.get('snippet', ''))
    
    high = [it for it in items if it.get('signal') == '高']
    medium = [it for it in items if it.get('signal') == '中']
    low = [it for it in items if it.get('signal') == '低']
    official = [it for it in items if any(k in it.get('source', '') for k in ['TWSE', 'Tpex'])]
    
    print(f"高:{len(high)} 中:{len(medium)} 低:{len(low)} 官方:{len(official)}")
    
    # 4. 指紋去重（簡單版）
    seen = set()
    unique_high = []
    for it in high:
        key = it.get('title', '')[:25].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique_high.append(it)
    
    unique_medium = []
    for it in medium:
        key = it.get('title', '')[:25].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique_medium.append(it)
    
    print(f"去重後 高:{len(unique_high)} 中:{len(unique_medium)}")
    
    # 5. 產出 Telegram 簡報
    now = datetime.now()
    lines = [
        f"📰 投資蝦晨報 {now.strftime('%m/%d %H:%M')}",
        f"候選 {len(items)}則 | 🔴高{len(unique_high)} 🟡中{len(unique_medium)}",
        "",
    ]
    
    if unique_high:
        lines.append("🔴 高重要性")
        for it in unique_high[:5]:
            title = it.get('title', '')[:55]
            codes = ', '.join(it.get('codes', [])[:3])
            src = it.get('source', '')[:20]
            lines.append(f"• {title}")
            if codes:
                lines.append(f"  {codes} | {src}")
            else:
                lines.append(f"  {src}")
        lines.append("")
    
    if unique_medium:
        lines.append("🟡 中重要性")
        for it in unique_medium[:5]:
            title = it.get('title', '')[:55]
            lines.append(f"• {title}")
        lines.append("")
    
    if official:
        lines.append("📋 官方公告")
        for it in official[:5]:
            title = it.get('title', '')[:55]
            lines.append(f"• {title}")
    
    lines.append("")
    lines.append(f"時間: {now.strftime('%H:%M')}")
    lines.append("📡 UDN+DigiTimes+CNBC+Bloomberg+官方")
    
    text = '\n'.join(lines)
    print("\n" + "=" * 40)
    print(text[:500])
    
    # 6. 發送
    if send_telegram(text):
        print("\n✅ Telegram 發送成功")
    else:
        print("\n❌ Telegram 發送失敗")
    
    # 7. 存檔
    output = STATE_DIR / "news_brief_history.json"
    history = []
    if output.exists():
        with open(output, 'r', encoding='utf-8') as f:
            history = json.load(f)
    
    history.append({
        "datetime": now.isoformat(),
        "total": len(items),
        "high": len(unique_high),
        "medium": len(unique_medium),
        "official": len(official),
    })
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(history[-30:], f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
