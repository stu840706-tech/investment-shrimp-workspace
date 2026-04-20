---
name: report-calendar
description: S-2 每日 09:00 抓 TWSE/TPEX 公告的法說會、年報、季報發布日期,寫入 event_calendar;追蹤中個股主動通知。
---

# report-calendar — 重大事件日曆

## 何時使用

- **自動觸發**:S-2 cron 每日 09:00
- **手動觸發**:Kai 問「這週有哪些法說會」「2330 最近有什麼活動」

## 資料來源

- **TWSE 公告**:法說會資訊(twse.com.tw 的「法人說明會資訊」專區)
- **TPEX 公告**:上櫃的同類公告
- **公司個別公告**:財報發布日(MOPS)
- **FinMind**:輔助(財報日、除權息日)

**注意**:爬蟲目標是公開公告;若撞到反爬,fallback FinMind 付費端點。

## 執行

```bash
# cron 自動跑
python3 skills/report-calendar/scripts/fetch_calendar.py

# 指定日期範圍
python3 skills/report-calendar/scripts/fetch_calendar.py --days 14
```

## 流程

```
1. 抓未來 14 天的法說會、財報、除權息日
2. 寫入 Notion event_calendar db(idempotent,同日期同個股不重建)
3. 交叉比對 stock_tracking:追蹤中個股的事件 → Telegram 通知
4. dashboard 的「今日重大事件」區塊 query 這張 db
```

## 資料模型(event_calendar db)

| 欄位 | 類型 | 說明 |
|------|------|------|
| 事件標題 | title | 例「台積電 Q1 法說會」 |
| 個股代號 | rich_text | 2330.TW |
| 事件日期 | date | YYYY-MM-DD |
| 事件時間 | rich_text | 14:00(若有) |
| 類型 | select | 法說會 / 年報 / 季報 / 除權息 / 其他 |
| 來源 | url | 公告 URL |
| 追蹤中 | checkbox | 是否是 stock_tracking 的個股(自動標) |

## 驗證(B5 Step 6)

```bash
python3 skills/report-calendar/scripts/fetch_calendar.py --days 14
```

**成功判定**:

- Notion event_calendar db 新增未來 14 天的事件(至少數十筆)
- 追蹤中個股有相關事件時 → Telegram 通知
- 重跑不會重複建(idempotency)

## 骨架狀態

當前為骨架,B5 實測後補:

- [ ] TWSE / TPEX 爬蟲(注意 robots.txt + rate limit)
- [ ] idempotency(同個股同日期不重建)
- [ ] stock_tracking 交叉比對
- [ ] Telegram 通知格式

## 陷阱

- **TWSE 網頁結構可能變**:把爬蟲 selector 集中一處,B5 驗證時如果抓不到資料 fallback FinMind
- **重複建 row**:idempotency 檢查 key = (個股代號, 事件日期, 類型)
- **時區**:事件時間是 Asia/Taipei
- **已取消的事件**:公司偶爾會延期法說會,B5 觀察幾次後決定要不要抓「異動公告」
