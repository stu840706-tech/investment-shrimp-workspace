#!/usr/bin/env python3
content = open("workflows/news_aggregator.py").read()

old = """def score_item(it):
    """預篩選評分：數字/關鍵字/公司代碼"""
    text = (it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')).lower()
    score = 0
    if re.search(r'\\d+[%％元億萬]', text): score += 2
    for kw in ['營收','EPS','目標價','評等','買進','漲','擴產','訂單','量產','新高','增','擴']:
        if kw in text: score += 1
    for kw in ['裁員','停工','火災','違約','假帳','掏空','下市','破產']:
        score += 3
    if re.search(r'\\b[12][0-9]{3}\\b', text): score += 1
    return score"""

if old in content:
    new = """_TRACKING_CODES = None

def load_tracking_codes():
    import urllib.request, json
    db_id = SECRETS.get("notion_stock_tracking_db", "")
    if not db_id:
        return set()
    payload = json.dumps({
        "filter": {
            "or": [
                {"property": "狀態", "select": {"equals": "持有"}},
                {"property": "狀態", "select": {"equals": "未持有_看好"}},
                {"property": "狀態", "select": {"equals": "未持有_感興趣"}},
            ]
        },
        "page_size": 100
    }).encode()
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        data=payload,
        headers={
            "Authorization": f"Bearer {NOTION_KEY}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    codes = set()
    import re as _re
    for row in data.get("results", []):
        title = row.get("properties", {}).get("股票代碼", {}).get("title", [])
        if title:
            code = title[0].get("plain_text", "").strip()
            m = _re.search(r"\\d{4}", code)
            if m:
                codes.add(m.group(0))
    return codes

def get_tracking_codes():
    global _TRACKING_CODES
    if _TRACKING_CODES is None:
        _TRACKING_CODES = load_tracking_codes()
    return _TRACKING_CODES

def score_item(it):
    text = (it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')).lower()
    score = 0
    if re.search(r'\\d+[%％元億萬]', text): score += 2
    for kw in ['營收','EPS','目標價','評等','買進','漲','擴產','訂單','量產','新高','增','擴']:
        if kw in text: score += 1
    for kw in ['裁員','停工','火災','違約','假帳','掏空','下市','破產']:
        score += 3
    if re.search(r'\\b[12][0-9]{3}\\b', text): score += 1
    tracking = get_tracking_codes()
    if tracking:
        full_text = it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')
        for code in tracking:
            if code in full_text:
                score += 5
                it['_is_tracking'] = True
                break
    return score"""
    content = content.replace(old, new, 1)
    print("OK: score_item + tracking loaded")
else:
    print("ERROR: target not found, searching...")
    idx = content.find("def score_item")
    print(f"score_item at: {idx}")
    print(repr(content[idx:idx+200]))
    exit(1)

open("workflows/news_aggregator.py", "w").write(content)
