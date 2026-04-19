# Notion API 使用指南

## 基本設定

- API Version: `2022-06-28`（`Notion-Version` header）
- Token 位置：`config/secrets.json` → `notion_key`
- Parent DB：`secrets.json` → `notion_parent_db_id`（每日掃描父資料庫）

## ⚠️ 關鍵限制

| 限制 | 數值 | 解法 |
|------|------|------|
| rich_text 單一 block | 2000 字元 | 用 `to_rich_text()` 切分 |
| children blocks/request | 100 個 | 分批 append |
| API rate limit | ~3 req/sec | sleep(0.35) |

## 切分函式（必用）

```python
def to_rich_text(text: str, limit: int = 1990) -> list:
    """Notion rich_text 2000 字元上限，切分為多個 block"""
    if not text:
        return []
    return [{"text": {"content": text[i:i+limit]}}
            for i in range(0, len(text), limit)]
```

## 11 欄位 Schema

| 欄位名 | 類型 | 說明 |
|--------|------|------|
| 股票名稱 | title | `公司名/代號` |
| 月營收 | rich_text | `$932,321,000(2026/03)` 元 |
| 月營收YoY | rich_text | `21.3%` |
| 月營收MoM | rich_text | `35.6%` |
| 營收利多 | multi_select | 標籤（5 個選項） |
| 三率利多 | multi_select | 標籤（5 個選項） |
| 籌碼面利多 | multi_select | 標籤（3 個選項） |
| 股利條件 | multi_select | 標籤（2 個選項） |
| 產業相對強弱 | multi_select | 標籤（2 個選項） |
| 重大訊息 | rich_text | 每則一行，用 to_rich_text() |
| 詳細內容 | rich_text | 所有標籤的實際數值 |

## 常見錯誤

- `400 body failed validation`: rich_text 超過 2000 字元 → 用 to_rich_text()
- `401 Unauthorized`: token 無效
- `404 Not Found`: DB ID 錯誤，確認 secrets.json 裡的 notion_parent_db_id
