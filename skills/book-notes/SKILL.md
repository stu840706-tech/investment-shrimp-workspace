---
name: book_notes
description: 書籍概念萃取。當 Kai 傳入書籍 txt 檔案時觸發，M2.7 分段萃取書中每個重要概念，產出概念名稱、觀點說明、舉例、如何使用、適用情境、重要度，批次寫入 Notion book_concepts DB，同時在 book_notes DB 建立書籍主檔。
---

# Book Notes Skill — 書籍概念萃取

## 觸發條件
Kai 傳入以下任一格式時啟動：
- `/book <書名>`（同時傳 txt 檔案）
- `幫我萃取這本書的概念`（同時傳 txt 檔案）

## 執行步驟

### Step 1：確認書籍資訊
詢問 Kai：

收到書籍檔案！請確認：
 1. 書名：
 2. 作者：
 3. 類別：價值投資/成長投資/總經/心理/技術分析/量化/產業/其他

### Step 2：執行萃取
```bash
cd ~/.openclaw/workspace/skills/book-notes/scripts
python3 book_main.py "<書名>" "<作者>" "<類別>" "<txt檔案路徑>"
```

### Step 3：回報結果
萃取完成後回報：
- 共萃取幾個概念卡
- 已寫入 Notion book_concepts
- 列出前5個概念名稱預覽

## 硬約束
- M2.7 thinking=off
- 每段輸入 ≤ 40K tokens（書籍分段處理）
- 全程繁體中文
- 不得捏造書中沒有的概念