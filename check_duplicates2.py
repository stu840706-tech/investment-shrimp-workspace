import json, urllib.request

secrets = json.load(open('/home/ubuntu/.openclaw/workspace/config/secrets.json'))
NOTION_KEY = secrets['notion_key']
BOOK_CONCEPTS_DB = "34e226f5-a398-8151-affd-d8b11b213ac1"

headers = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def notion_post(url, payload):
    req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

# 不篩選，直接看前10張概念卡
payload = {"page_size": 10}
resp = notion_post(f"https://api.notion.com/v1/databases/{BOOK_CONCEPTS_DB}/query", payload)
pages = resp.get("results", [])
print(f"總概念卡數: {len(pages)}")
for p in pages:
    title = p.get("properties", {}).get("概念名稱", {}).get("title", [])
    name = title[0]["text"]["content"] if title else "?"
    rel = p.get("properties", {}).get("所屬書籍", {}).get("relation", [])
    rel_ids = [r.get("id") for r in rel]
    print(f" ID: {p['id']} | 名稱: {name} | 所屬書籍: {rel_ids}")