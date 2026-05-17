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

# 用 AND filter 查這本書的概念卡
payload = {
    "filter": {
        "and": [
            {"property": "所屬書籍", "relation": {"contains": BOOK_ID}}
        ]
    },
    "page_size": 100
}
resp = notion_post(f"https://api.notion.com/v1/databases/{BOOK_CONCEPTS_DB}/query", payload)
pages = resp.get("results", [])
print(f"用 AND filter 找到 {len(pages)} 張概念卡")
for p in pages:
    title = p.get("properties", {}).get("概念名稱", {}).get("title", [])
    name = title[0]["text"]["content"] if title else "?"
    print(f" ID: {p['id']} | 名稱: {name}")

# 也查 book_notes DB 看有幾本書
BOOK_NOTES_DB = "e6a3ea64e34e49c5a0059a870c1e1db1"
payload2 = {"page_size": 10}
resp2 = notion_post(f"https://api.notion.com/v1/databases/{BOOK_NOTES_DB}/query", payload2)
pages2 = resp2.get("results", [])
print(f"\nbook_notes DB: 找到 {len(pages2)} 本書")
for p in pages2:
    title = p.get("properties", {}).get("書名", {}).get("title", [])
    name = title[0]["text"]["content"] if title else "?"
    print(f"  ID: {p['id']} | 書名: {name}")