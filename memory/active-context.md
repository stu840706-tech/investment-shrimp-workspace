# Active Context
_最後更新：2026-05-04_

## 當前狀態
B1-B8 + Task 4/6/7/8 + Dreaming + B6增強 全部完成。
後期維護階段，無進行中的重構任務。

## 新聞管線重大 bug（2026-05-04 23:42 已修復）

### 問題
news_pipeline.py 行 85：`return` 語句寫在 `if __name__ == "__main__"` 區塊內（非 function 內）。
Python 不允許在 script top-level 使用 `return`，導致每次 cron 執行都因 SyntaxError 失敗。

### 修復
```python
# 修復前（SyntaxError）
if lock_file.exists():
    print(f"[SKIP] 今日 {taipei_h} 時已執行過 pipeline，跳過")
    return  # ← 錯誤：不在 function 內

# 修復後
if lock_file.exists():
    print(f"[SKIP] 今日 {taipei_h} 時已執行過 pipeline，跳過")
    sys.exit(0)
```

### 觀察到的失敗模式
- `returncode=1` + `SyntaxError: 'return' outside function`
- 導致 pipeline 在早上 07:00/19:00 自動執行時失敗
- 今天手動補了：
  - `python3 workflows/news_aggregator.py 07 20260504`（成功，111則，高2/中27）
  - `python3 workflows/news_publisher.py 07 20260504 AM`（成功，Telegram+Notion）
- sent state 已更新：`2026-05-04-AM: true`

### 待觀察
- [ ] 明日 07:00 UTC cron 是否正常（修復後第一次真正考驗）
- [ ] lock file 機制是否真的生效

## 暫緩任務
- news_aggregator cluster dedup 改善（同一 story 重複出現）
- T3 批次大小重跑
- NotebookLM 整合
- Task 4 自動化（待 port 8888 開放）
- 晨晚報數量差異過大問題（需研究 raw data 是否正常）

## 無進行中任務