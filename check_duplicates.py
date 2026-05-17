import json, urllib.request

secrets = json.load(open('/home/ubuntu/.openclaw/workspace/config/secrets.json'))
NOTION_KEY = secrets['notion_key']
BOOK_CONCEPTS_DB = "34e226f5-a398-8151-affd-d8b11b213ac1"
BOOK_ID = "35e226f5-a398-802f-bf27-fa7a4fa19970"

headers = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def notion_post(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

# 查詢這本書的所有概念卡
payload = {
    "filter": {"property": "所屬書籍", "relation": {"contains": BOOK_ID}},
    "page_size": 100
}
resp = notion_post(f"https://api.notion.com/v1/databases/{BOOK_CONCEPTS_DB}/query", payload)
pages = resp.get("results", [])
print(f"找到 {len(pages)} 張概念卡")
for p in pages[:5]:
    title = p.get("properties", {}).get("概念名稱", {}).get("title", [])
    name = title[0]["text"]["content"] if title else "?"
    print(f" ID: {p['id']} | 名稱: {name}")