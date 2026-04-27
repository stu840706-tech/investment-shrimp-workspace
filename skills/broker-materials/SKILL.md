---
name: broker-materials
description: 處理 Telegram Group -5290205228 傳來的券商 PDF,M2.7 自動分類(個股/產業/晨報)並寫入對應 Notion db。
---

# broker-materials — 券商材料處理

## 何時使用

- S-3 常駐授權: Telegram Group `-5290205228` 收到 PDF / 圖片時自動觸發
- Kai 手動轉寄券商報告到該 channel
- Kai 的 DM 收到 PDF 檔案時自動觸發（Line → Render → OpenClaw bot → DM）

## 為什麼是單一 channel + 自動分類

HANDOFF 已決策方案。Kai 不想每次分三個資料夾手動丟,全部進同一個 channel,讓 M2.7 辨識是:

- **個股報告**(某檔的深度研究)→ Notion `broker_reports` db
- **產業報告**(AI、車用、面板等 thematic)→ Notion `industry_reports` db
- **晨報**(每日 morning brief)→ Notion `broker_reports` db 的晨報 tag

## 流程

```
1. Telegram listener 收到檔案 → 存 /tmp/<filename>.pdf
2. 呼叫 pdf-reader skill 轉純文字(紅線:PDF 不直接進 M2.7)
3. 取前 N 字(avoid context overflow)
4. M2.7 (thinking=off) 分類 + 萃取結構化欄位
5. 依分類寫入對應 Notion db
6. 分類 confidence low → Telegram 請 Kai 人工裁決
7. Telegram 回報 Kai「已處理 XX 報告」
```

## M2.7 萃取的結構化欄位

### 個股報告

- 個股代號、公司名稱
- 券商(發行方,如 統一、元大、摩根士丹利)
- 發行日期
- 評等(買入/中立/賣出)
- 目標價
- 核心論點(3 句內)
- 關鍵數字(EPS 預估、毛利率、營收成長率)

### 產業報告

- 產業別(select option:半導體/AI/車用/面板/...)
- 券商
- 發行日期
- 核心論點
- 提及個股(cross-reference stock_tracking 加權)

### 晨報

- 日期
- 券商
- Highlight 個股(最多 5 檔)
- 大盤觀點

## 執行

```bash
# Telegram listener(持續常駐,cron 或 systemd 管理)
python3 skills/broker-materials/scripts/receive_telegram.py
```

## 分類 confidence 處理

- `high`:直接寫 Notion,Telegram 通知「已分類為 XXX」
- `medium`:寫 Notion,Telegram 附「分類為 XXX,是否正確?」按鈕
- `low`:**不寫 Notion**,Telegram 請 Kai 選分類

誤分類由 Kai 糾正 → 加入 `references/classification_examples.md` 當 few-shot

## 時間序列 view

同一個個股的券商報告累積後,可產 timeline view(Notion timeline 或 gallery):

- X 軸:發行日期
- Y 軸:目標價
- 顏色:評等(買入/中立/賣出)

手動觸發:

```bash
python3 skills/broker-materials/scripts/build_timeline.py --symbol 2330.TW
```

## 驗證(B5 Step 5)

1. Telegram 上傳一份個股 PDF → 5 分鐘內 Notion broker_reports 有新 row
2. 上傳一份產業 PDF → 分類到 industry_reports
3. 上傳一份「明顯不是券商」的 PDF(例 Kai 個人筆記)→ M2.7 應回 confidence low + 請裁決

## 骨架狀態

當前為骨架,B5 實測後補:

- [ ] Telegram Bot API listener(接收檔案)
- [ ] 呼叫 pdf-reader 子腳本
- [ ] M2.7 分類 prompt(含 few-shot)
- [ ] 三個 db 的寫入邏輯
- [ ] confidence 閾值 + Telegram 互動
- [ ] build_timeline.py

## 陷阱

- **M2.7 context 超限**:券商報告常 > 20K 字元,取前 5-8K + 末 2K 即可判斷分類
- **分類邊界 case**:「個股與產業混合」的報告,以「佔比 > 70%」的類別為主,另類別只作 tag
- **掃描件券商報告**:少見但存在,pdf-reader 會 fallback OCR
- **Telegram file_id 有 24 小時 TTL**:接到立即下載,不要延後
- **晨報大量 mention 個股**:不要每檔都自動加 stock_tracking,只 log,由 Kai 決定
