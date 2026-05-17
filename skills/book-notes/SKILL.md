---
name: book_notes
description: 書籍概念萃取。當 Kai 傳入書籍 txt/md 檔案時觸發，M2.7 分段萃取書中每個重要概念，產出概念名稱、觀點說明、舉例、如何使用、適用情境、重要度，批次寫入 Notion book_concepts DB，同時在 book_notes DB 建立書籍主檔。
---

# Book Notes Skill — 書籍概念萃取

## 觸發條件
Kai 傳入以下任一格式時啟動：
- `/book <書名>`（同時傳 txt 或 md 檔案）
- `幫我萃取這本書的概念`（同時傳 txt 或 md 檔案）

支援格式：`.txt`、`.md`（純文字 Markdown）

## 執行流程

### Step 0：檔案歸檔
收到書籍檔案後，立即複製到 `books/input/` 目錄：
```bash
cp <來源檔案> books/input/<書名>_<日期>.md
```
未來可隨時從 `books/input/` 取用原始檔案。

### Step 1：確認書籍資訊
詢問 Kai（如果訊息中未附上足夠資訊）：

收到書籍檔案！請確認：
 1. 書名：
 2. 作者：
 3. 類別：價值投資/成長投資/總經/心理/技術分析/量化/產業/其他

### Step 2：執行萃取
```bash
cd ~/.openclaw/workspace/skills/book-notes/scripts
python3 book_main.py "<書名>" "<作者>" "<類別>" "books/input/<書名>_<日期>.md"
```

### Step 3：回報結果
萃取完成後回報：
- 共萃取幾個概念卡
- 已寫入 Notion book_concepts
- 列出前5個概念名稱預覽

## 檔案大小說明

- 小檔案（<20萬字元）：直接章節/段落分段，處理快速
- 大檔案（>20萬字元）：自動切片 + 進度顯示
- 非常大的檔案（>50萬字元）：顯示預估時間，處理中會有分段進度

**不需要手動分割檔案**，系統會自動處理。

## 硬約束
- M2.7 thinking=off
- 每段輸入 ≤ 30K 字元（約 7-8K tokens）
- 全程繁體中文
- 不得捏造書中沒有的概念