import json, urllib.request, time

secrets = json.load(open('/home/ubuntu/.openclaw/workspace/config/secrets.json'))
NOTION_KEY = secrets['notion_key']
BOOK_CONCEPTS_DB = "34e226f5-a398-8151-affd-d8b11b213ac1"
BOOK_ID = "35e226f5-a398-81c8-8757-dfd8451a0797"

headers = {
    "Authorization": f"Bearer {NOTION_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

def notion_req(method, url, payload=None):
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data, headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

# 分頁查出所有概念卡
all_ids = []
cursor = None
while True:
    payload = {"filter": {"property": "所屬書籍", "relation": {"contains": BOOK_ID}}, "page_size": 100}
    if cursor:
        payload["start_cursor"] = cursor
    resp = notion_req("POST", f"https://api.notion.com/v1/databases/{BOOK_CONCEPTS_DB}/query", payload)
    for p in resp.get("results", []):
        all_ids.append(p["id"])
    if not resp.get("has_more"):
        break
    cursor = resp.get("next_cursor")

print(f"準備刪除 {len(all_ids)} 張概念卡")

# Archive（Notion 不支援真刪除，用 archive）
for i, pid in enumerate(all_ids):
    notion_req("PATCH", f"https://api.notion.com/v1/pages/{pid}", {"archived": True})
    if (i+1) % 20 == 0:
        print(f" 已處理 {i+1}/{len(all_ids)}")
    time.sleep(0.15)

print(f"完成：{len(all_ids)} 張全部 archived")