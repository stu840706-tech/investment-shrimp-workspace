import json, urllib.request

secrets = json.load(open('/home/ubuntu/.openclaw/workspace/config/secrets.json'))
NOTION_KEY = secrets['notion_key']
BOOK_CONCEPTS_DB = "34e226f5-a398-8151-affd-d8b11b213ac1"
BOOK_ID = "35e226f5-a398-81c8-8757-dfd8451a0797"

headers = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def notion_post(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

# 計算這本書的總概念卡數（自動分頁）
all_ids = []
payload = {
    "filter": {"property": "所屬書籍", "relation": {"contains": BOOK_ID}},
    "page_size": 100
}
while True:
    resp = notion_post(f"https://api.notion.com/v1/databases/{BOOK_CONCEPTS_DB}/query", payload)
    pages = resp.get("results", [])
    for p in pages:
        all_ids.append(p['id'])
    if not resp.get("has_more"):
        break
    payload["start_cursor"] = resp.get("next_cursor")

print(f"總概念卡數: {len(all_ids)}")

# 統計重複名稱
from collections import Counter
names = []
names2 = []
for p in pages:
    title = p.get("properties", {}).get("概念名稱", {}).get("title", [])
    name = title[0]["text"]["content"] if title else "?"
    names.append(name)

# 需要重新抓全部頁面才能統計，先做一個簡單的 name list
# 重新跑一次取所有名稱
all_names = []
payload = {"filter": {"property": "所屬書籍", "relation": {"contains": BOOK_ID}}, "page_size": 100}
while True:
    resp = notion_post(f"https://api.notion.com/v1/databases/{BOOK_CONCEPTS_DB}/query", payload)
    for p in resp.get("results", []):
        title = p.get("properties", {}).get("概念名稱", {}).get("title", [])
        name = title[0]["text"]["content"] if title else "?"
        all_names.append(name)
    if not resp.get("has_more"):
        break
    payload["start_cursor"] = resp.get("next_cursor")

dup = Counter(all_names)
dup_list = [(name, count) for name, count in dup.items() if count > 1]
print(f"\n重複名稱 ({len(dup_list)} 組):")
for name, count in sorted(dup_list, key=lambda x: -x[1]):
    print(f"  [{count}次] {name}")