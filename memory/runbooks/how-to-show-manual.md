# how-to-show-manual.md — 使用手冊輸出標準流程

## 規則

「使用手冊」三個字出現時，**必須**執行：

```bash
cd /home/ubuntu/.openclaw/workspace && python3 workflows/show_manual.py
```

把 script 原文**一字不漏**直接發給 Kai，**禁止**：
- 濃縮成 bullets
- 重新排版
- 自行增刪內容
- 翻譯成「更易讀」的格式

---

## 觸發關鍵字

- 「使用手冊」
- 「功能說明」
- 「怎麼用」
- 「操作說明」
- 「操作手冊」

---

## 其他需要输出一覽

| 需求 | 指令 |
|------|------|
| 使用手冊 | `python3 workflows/show_manual.py` |
| 健康檢查報告 | `python3 workflows/weekly_health_check.py` |
| 每日 Dashboard | `python3 workflows/daily_dashboard.py` |

---

## 檔案讀取規則

**大檔案（>~100行）嚴禁使用 `exec + cat`**：
- exec + cat 對大檔案會觸發系統截斷，輸出會變成 `⚠️ [... middle content omitted ...]`
- 正確做法：**一律使用 `read` 工具**讀取檔案內容，再貼給 Kai

**操作流程：**
1. 優先用 `read` 工具讀取檔案
2. 只有確定檔案很小（<50行）時才用 exec + cat
3. exec + cat 的輸出如果出現 `⚠️ [... middle content omitted ...]`，立即用 `read` 工具補救

---

## 更新歷史

- 2026-05-03：新增嚴格規則，確保原文直發不重新排版
- 2026-05-03：新增檔案讀取規則，強制使用 read 工具避免截斷
