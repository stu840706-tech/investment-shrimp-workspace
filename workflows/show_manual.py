#!/usr/bin/env python3
"""show_manual.py - 投資蝦使用手冊"""

MANUAL = [
    {
        "name": "📰 新聞晨報／晚報",
        "trigger": "自動執行（平日 07:00 / 19:00 台北時間）",
        "description": "三層管線：抓取 UDN RSS + 官方重訊 → M2.7 Fact 三層預審 + 信號分類 → 發送 Telegram + 寫入 Notion。",
        "steps": [
            "系統自動執行，無需手動觸發",
            "追蹤清單內個股新聞優先進入 Top 10",
            "Layer 3 純敘事（多空對決、後市怎麼走）自動跳過",
        ],
        "notes": "手動觸發：python3 workflows/news_pipeline.py",
    },
    {
        "name": "📊 每日財務掃描 Dashboard",
        "trigger": "自動執行（平日 20:00 台北時間），20:10 產出 Dashboard",
        "description": "掃描台股全市場，找命中營收／三率／籌碼面標籤個股，產出每日 Notion Dashboard 頁面。",
        "steps": [
            "系統每日自動執行",
            "20:10 後在 Notion 查看當日 Dashboard 頁面",
        ],
        "notes": "手動觸發：python3 workflows/daily-scan.py",
    },
    {
        "name": "📅 法說會行事曆",
        "trigger": "自動執行（平日 09:00 台北時間）",
        "description": "抓取追蹤清單個股的法說會、財報、月營收重要日期，提前 3 天／當天 Telegram 提醒，寫入 Notion event_calendar。",
        "steps": [
            "系統每日自動執行",
            "只追蹤 stock_tracking 中持有／看好／感興趣的個股",
            "Notion event_calendar 可查看未來 14 天事件",
        ],
    },
    {
        "name": "✅ Outcome Review",
        "trigger": "自動執行（每日 23:00 台北時間）",
        "description": "掃描 stock_tracking 下次驗證日到期個股，Telegram 通知 Kai 進行 thesis 驗證，結果寫入 Notion outcome_log。",
        "steps": [
            "系統每日自動執行",
            "到期時收到 Telegram 通知",
            "回報驗證結果後寫入 outcome_log（已驗證符合／部分符合／已驗證反證）",
        ],
    },
    {
        "name": "📈 個股追蹤",
        "trigger": "對話觸發：「追蹤 台勝科，放在持有名單」或「追蹤 4755，看好，原因是…」",
        "description": "將個股加入 Notion stock_tracking，記錄核心 thesis、期待催化劑、風險因素、反證條件，自動設定下次驗證日。股票代碼或名稱都接受，OpenClaw 自動查代碼。",
        "steps": [
            "對 OpenClaw 說追蹤指令（代碼或名稱皆可）",
            "OpenClaw 確認代碼後詢問缺少的欄位（thesis／反證條件）",
            "確認後寫入 Notion stock_tracking",
        ],
        "notes": "狀態選項：持有 / 未持有_看好 / 未持有_感興趣",
    },
    {
        "name": "📋 券商報告批次處理",
        "trigger": "傳多份 PDF 給 OpenClaw DM，傳完說「開始處理」",
        "description": "M2.7 自動分類（個股／產業／晨報），萃取目標價、EPS、毛利率預測等，寫入 Notion broker_reports 或 industry_reports。",
        "steps": [
            "把券商 PDF 逐份傳給 OpenClaw DM",
            "全部傳完後說「開始處理」",
            "OpenClaw 批次處理，回傳每份分類結果",
        ],
        "notes": "PDF 由 pdf-reader skill 自動轉文字，無需手動轉換",
    },
    {
        "name": "📊 個股研究報告",
        "trigger": "對話觸發：/research 台勝科 或 /research 4755",
        "description": "自動抓取財務數字、籌碼面、技術面，結合法說會 memo 和年報，產出研究報告草稿，寫入 Notion research_pages。股票代碼或名稱都接受。",
        "steps": [
            "說 /research 台勝科（或代碼 4755）",
            "OpenClaw 確認代碼後問「有法說會 memo 嗎？」→ 回答內容或說無",
            "OpenClaw 問「有年報 PDF 嗎？」→ 傳 PDF 或說無",
            "等待約 2-3 分鐘，回傳報告摘要",
        ],
        "notes": "年報 PDF 需手動提供（TWSE DNS 在 VM 無法解析）",
    },
    {
        "name": "📚 書籍概念萃取",
        "trigger": "對話觸發：/book",
        "description": "M2.7 分段萃取書籍重要概念（名稱、觀點、舉例、使用方法、適用情境），批次寫入 Notion book_concepts。",
        "steps": [
            "說 /book",
            "OpenClaw 問書名、作者、類別",
            "回答後，OpenClaw 說「請傳 txt 檔案」",
            "傳 txt 檔案",
            "等待萃取完成（依書長約 3-10 分鐘）",
        ],
        "notes": "書籍需為 txt 格式；PDF 請先用 pdf-reader skill 轉換",
    },
    {
        "name": "📋 券商日摘",
        "trigger": "自動執行（每日 23:00 台北時間）",
        "description": "彙整當日券商晨報摘要與個股報告，用 M2.7 產出三段式日摘（晨訊重點／個股匯整／產業報告），發送 Telegram。有什麼內容就發什麼，三段都空的話才跳過。",
        "steps": [
            "系統每日自動執行",
            "三段內容：有晨報→晨訊重點，有個股報告→個股匯整，有產業報告→產業報告",
            "晨報摘要來自 receive_telegram.py 存入的 broker_morning_{date}.txt",
        ],
        "notes": "手動觸發：python3 workflows/broker_digest.py",
    },
    {
        "name": "🔬 策略回測",
        "trigger": "對話觸發：自然語言描述策略",
        "description": "描述策略邏輯，OpenClaw 對應內建策略或撰寫新策略，用 FinMind 歷史資料回測，計算總報酬、年化報酬、夏普比率、最大回撤、勝率。",
        "steps": [
            "描述策略（例：「找近3個月營收持續成長且毛利率改善的台股，買進持有60天」）",
            "OpenClaw 確認對應策略：momentum／revenue_growth／margin_improvement 或新寫",
            "確認後執行回測（約 5-15 分鐘）",
            "回傳績效摘要，寫入 Notion backtest_results",
        ],
    },
]

IDLE_SKILLS = [
    "eastmoney-stock", "elite-longterm-memory", "financial-analysis-agent",
    "knowledge-graph-skill", "stock-study", "trading-devbox",
    "tushare-stock-skill", "tw-revenue-backfill", "tw-stock-info",
    "us-stock-analysis", "web-scraping",
]

def build():
    lines = []
    lines.append("======================================================================")
    lines.append("🦐 投資蝦 OpenClaw 使用手冊")
    lines.append(f"共 {len(MANUAL)} 個功能")
    lines.append("======================================================================")

    for i, item in enumerate(MANUAL, 1):
        lines.append(f"\n{i}. {item['name']}")
        lines.append(f"觸發：{item['trigger']}")
        lines.append(f"說明：{item['description']}")
        if item.get("steps"):
            lines.append("步驟：")
            for step in item["steps"]:
                lines.append(f" • {step}")
        if item.get("notes"):
            lines.append(f"注意：{item['notes']}")

    lines.append(f"\n======================================================================")
    lines.append(f"閒置 skills（不使用）：{', '.join(IDLE_SKILLS)}")
    lines.append("======================================================================")
    return "\n".join(lines)

def send_telegram(text):
    import json, urllib.request
    secrets = json.load(open("/home/ubuntu/.openclaw/workspace/config/secrets.json"))
    token = secrets["telegram_bot_token"]
    chat_id = secrets["telegram_dm"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": f"<pre>{text}</pre>",
        "parse_mode": "HTML"
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())["result"]["message_id"]

if __name__ == "__main__":
    import sys
    text = build()
    if "--telegram" in sys.argv:
        msg_id = send_telegram(text)
        print(f"Sent to Telegram: message_id={msg_id}")
    else:
        print(f"<pre>{text}</pre>")