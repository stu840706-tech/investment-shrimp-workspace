#!/usr/bin/env python3
content = open('workflows/news_aggregator.py').read()

old = (
    'def score_item(it):\n'
    '    """預篩選評分：數字/關鍵字/公司代碼"""\n'
    "    text = (it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')).lower()\n"
    '    score = 0\n'
    "    if re.search(r'\\d+[%％元億萬]', text): score += 2\n"
    "    for kw in ['營收','EPS','目標價','評等','買進','漲','擴產','訂單','量產','新高','增','擴']:\n"
    '        if kw in text: score += 1\n'
    "    for kw in ['裁員','停工','火災','違約','假帳','掏空','下市','破產']:\n"
    '        score += 3\n'
    "    if re.search(r'\\b[12][0-9]{3}\\b', text): score += 1\n"
    '    return score\n'
)

new = (
    '_TRACKING_CODES = None\n'
    '\n'
    'def load_tracking_codes():\n'
    '    import urllib.request, json\n'
    '    db_id = SECRETS.get("notion_stock_tracking_db", "")\n'
    '    if not db_id:\n'
    '        return set()\n'
    '    payload = json.dumps({\n'
    '        "filter": {\n'
    '            "or": [\n'
    '                {"property": "狀態", "select": {"equals": "持有"}},\n'
    '                {"property": "狀態", "select": {"equals": "未持有_看好"}},\n'
    '                {"property": "狀態", "select": {"equals": "未持有_感興趣"}},\n'
    '            ]\n'
    '        },\n'
    '        "page_size": 100\n'
    '    }).encode()\n'
    '    req = urllib.request.Request(\n'
    '        f"https://api.notion.com/v1/databases/{db_id}/query",\n'
    '        data=payload,\n'
    '        headers={\n'
    '            "Authorization": f"Bearer {NOTION_KEY}",\n'
    '            "Notion-Version": "2022-06-28",\n'
    '            "Content-Type": "application/json"\n'
    '        },\n'
    '        method="POST"\n'
    '    )\n'
    '    with urllib.request.urlopen(req, timeout=10) as r:\n'
    '        data = json.loads(r.read().decode())\n'
    '    codes = set()\n'
    '    import re as _re\n'
    '    for row in data.get("results", []):\n'
    '        title = row.get("properties", {}).get("股票代碼", {}).get("title", [])\n'
    '        if title:\n'
    '            code = title[0].get("plain_text", "").strip()\n'
    '            m = _re.search(r"\\d{4}", code)\n'
    '            if m:\n'
    '                codes.add(m.group(0))\n'
    '    return codes\n'
    '\n'
    'def get_tracking_codes():\n'
    '    global _TRACKING_CODES\n'
    '    if _TRACKING_CODES is None:\n'
    '        _TRACKING_CODES = load_tracking_codes()\n'
    '    return _TRACKING_CODES\n'
    '\n'
    'def score_item(it):\n'
    "    text = (it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')).lower()\n"
    '    score = 0\n'
    "    if re.search(r'\\d+[%％元億萬]', text): score += 2\n"
    "    for kw in ['營收','EPS','目標價','評等','買進','漲','擴產','訂單','量產','新高','增','擴']:\n"
    '        if kw in text: score += 1\n'
    "    for kw in ['裁員','停工','火災','違約','假帳','掏空','下市','破產']:\n"
    '        score += 3\n'
    "    if re.search(r'\\b[12][0-9]{3}\\b', text): score += 1\n"
    '    tracking = get_tracking_codes()\n'
    '    if tracking:\n'
    "        full_text = it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')\n"
    '        for code in tracking:\n'
    '            if code in full_text:\n'
    '                score += 5\n'
    "                it['_is_tracking'] = True\n"
    '                break\n'
    '    return score\n'
)

if old in content:
    content = content.replace(old, new, 1)
    open('workflows/news_aggregator.py', 'w').write(content)
    print('OK')
else:
    print('ERROR: target not found')
    idx = content.find('def score_item')
    print(f'score_item at byte {idx}')
