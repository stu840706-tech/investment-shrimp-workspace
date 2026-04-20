# memory-management.md — 記憶管理

_AGENTS.md Session step 8 會查此檔。關於寫 MEMORY、歸檔、蒸餾的所有操作。_

## 四層記憶系統

| 層級 | 檔案 | 用途 | 誰寫 | 誰讀 |
|------|------|------|------|------|
| L1 戰略 | `MEMORY.md` | Kai 方法論、已驗證 thesis、犯過的錯 | Claude 蒸餾 + Kai 確認 | 每次 session(主 session) |
| L2 操作 | `memory/active-context.md` | 當前任務狀態、未完成步驟 | Claude 任務切換時 | 每次 session |
| L3 戰術 | `memory/YYYY-MM-DD.md` | 每日日誌、觀察、錯誤 | Claude 執行過程 | 當天的 session |
| L4 知識 | `memory/runbooks/*.md` | 標準 how-to | Claude 學到新流程 | Session step 8 按需 |

## 分層紀律

- Heartbeat 流水帳、例行檢查結果 → **只寫 L3**,不進 MEMORY.md
- L2 `active-context.md` 應該短(< 2000 字元);長了代表有任務沒收尾
- L3 超過 30 天 → `memory_archive.py` 自動搬到 `memory/archive/`
- L1 MEMORY.md 超過 12000 字元 → 觸發手動蒸餾(OpenClaw 20000 字元截斷上限)

## 從 L3 蒸餾進 L1 的判斷

L3 的東西什麼才該進 L1?

- **跨 session 仍相關**:這週的 bug,下週解決完就不必進 L1
- **方法論變化**:Kai 說「以後所有個股研究都要加這步」→ 進 L1
- **踩過的重大坑**:已經寫進 `lessons_learned.md` 就不必再進 L1
- **已驗證 thesis**:立案 → 實際發展 → 結果符合/不符合,這種 outcome 結論進 L1
- **USER.md 沒寫到的 Kai 個人偏好**(例:「Kai 討厭 3C 題材股」)→ 考慮更新 USER.md 而不是 L1

**進 L1 的門檻**:三個月後還會想起才留,不然進 archive。

## 歸檔流程

自動歸檔由 `workflows/memory_archive.py` 執行(B3 上線),每日 23:30 cron。

**歸檔條件**:

- `memory/YYYY-MM-DD.md` 檔案的日期 > 30 天前
- 搬到 `memory/archive/YYYY-MM-DD.md`
- 目標位置已有同名檔 → 不搬(避免覆蓋)

**同時做的事**:

- 檢查 `MEMORY.md` 字元數,>12000 印警告
- 掃 `memory/news-fingerprints.md`,保留 30 天內的,其餘搬到 archive

手動跑 dry-run:

```bash
cd ~/.openclaw/workspace
python3 workflows/memory_archive.py --dry-run
```

實跑(cron 會自動跑):

```bash
python3 workflows/memory_archive.py
```

## 今日 L3 日誌的寫法

`memory/YYYY-MM-DD.md` 格式(自由,但建議):

```markdown
# 2026-04-19

## 執行紀錄
- 06:00 daily-scan 完成,異常 X 檔,詳見 scan_results
- 09:05 event_calendar 抓到 2330 法說會 4/28
- ...

## 觀察
- 新聞 signal 濃度今天較低,疑似假日前效應

## 錯誤
- news_aggregator 重複處理 2317 兩次(fingerprint 誤判,詳細見 issue log)

## 給明天自己
- 追蹤 2330 法說會結果
```

**寫的時候注意**:

- Heartbeat 檢查成功不必寫(太多、沒價值)
- API 額度耗盡、workflow 失敗 → 一定要寫
- Kai 給的指令和你的回應 → 寫
- 純閒聊 → 不寫

## MEMORY.md 蒸餾

超過 12000 字元時(`memory_archive.py` 會提醒),手動操作:

1. 讀整份 MEMORY.md
2. 問自己:哪些條目「三個月後還會重要」?
3. 不重要的搬到 `memory/archive/memory_YYYYMMDD.md`
4. 重要的用更精煉語言重寫(1 條 = 1-3 句話)
5. 保留章節結構(投資方法論 / 已驗證 thesis / 犯過的錯 / Kai 偏好)
6. 蒸餾後 commit 並告訴 Kai 做了什麼

**不要**:

- 自動蒸餾(現階段不啟用 Dreaming,HANDOFF 暫緩清單)
- 把今天的流水帳塞進 MEMORY.md
- 刪除 Kai 明確提過「這很重要」的條目

## Runbook(L4)什麼時候該新增

學到新流程才寫新 runbook,判斷標準:

- 下次同樣情境會查的資訊 → L4(runbook)
- 只有今天會查的資訊 → L3(日誌)
- 個人化偏好 → USER.md
- 方法論 → MEMORY.md

新 runbook 寫完後記得:

- 在 `AGENTS.md` 目錄結構或 `TASK_PRIORITIES.md` 適當處 cross-reference
- 依 GP-015 commit

---

_上次更新:2026-04-19_
