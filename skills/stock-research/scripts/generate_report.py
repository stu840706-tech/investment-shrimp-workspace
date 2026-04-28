#!/usr/bin/env python3
"""generate_report.py - 整合所有資料，呼叫 M2.7 產生七章節研究報告草稿"""
import sys, json, urllib.request
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import MINIMAX_API_KEY as MINIMAX_TOKEN

WORKSPACE = Path(__file__).parent.parent.parent.parent
TEMPLATE = WORKSPACE / "skills/stock-research/references/report_template.md"

def load_state(stock_id):
    state = WORKSPACE / "state"
    def load(suffix):
        p = state / f"research_{stock_id}_{suffix}.json"
        if p.exists():
            return json.load(open(p))
        return {}
    return {
        "financials": load("financials"),
        "chips": load("chips"),
        "price": load("price"),
        "annual": load("annual"),
        "broker": load("broker"),
    }

def build_prompt(stock_id, data, memo):
    """組裝 M2.7 輸入 prompt，控制在 40K tokens 內"""
    fin = data.get("financials", {})
    chips = data.get("chips", {})
    price = data.get("price", {})
    annual = data.get("annual", {})
    broker = data.get("broker", {})

    # 月營收表（最近14筆）
    rev_table = "年月 | 月營收(元) | MoM% | YoY%\n"
    for r in fin.get("monthly_revenue", []):
        rev_table += f"{r['date']} | {r['revenue']:,} | {r.get('revenue_mom','N/A')} | {r.get('revenue_yoy','N/A')}\n"

    # 季報三率表（最近8季）
    q_table = "季度 | 營收(元) | 毛利率% | 營益率% | 淨利率% | EPS\n"
    for q in fin.get("quarterly", []):
        q_table += (f"{q['quarter']} | {q['revenue']:,.0f} | "
                    f"{q.get('gross_margin','N/A')} | {q.get('operating_margin','N/A')} | "
                    f"{q.get('net_margin','N/A')} | {q.get('eps','N/A')}\n")

    # 三大法人摘要
    inst = chips.get("institutional", {})
    inst_summary = inst.get("summary", {})
    inst_table = "日期 | 外資 | 投信 | 自營 | 合計\n"
    for r in inst.get("daily", [])[-10:]:
        inst_table += f"{r['date']} | {r['foreign']:+,} | {r['trust']:+,} | {r['dealer']:+,} | {r['total']:+,}\n"

    # 融資摘要
    margin = chips.get("margin", {})
    margin_summary = margin.get("summary", {})
    margin_latest = margin_summary.get("margin_latest", "N/A")
    margin_change = margin_summary.get("margin_change_30d", "N/A")

    # 年報重點
    annual_kp = annual.get("key_points", {})
    annual_text = json.dumps(annual_kp, ensure_ascii=False, indent=2) if annual_kp else "（無年報資料）"

    # 券商報告摘要
    broker_text = ""
    for r in broker.get("reports", []):
        broker_text += (f"- {r['date']} {r['broker']} {r['rating']} "
                        f"TP:{r['target_price']} | {r['core_view']}\n")
    if not broker_text:
        broker_text = "（無券商報告資料）"

    template_snippet = TEMPLATE.read_text()[:3000]

    prompt = f"""你是一位台股個股研究員，請依照以下資料產生一份完整的個股研究報告草稿。

## 股票代碼
{stock_id}

## 法說會重點（Kai 整理）
{memo}

## 年報萃取重點
{annual_text}

## 月營收數據
{rev_table}

## 季報三率（近8季）
{q_table}

## 三大法人近10日買賣超
{inst_table}
30日合計：外資 {inst_summary.get('foreign_30d',0):+,} / 投信 {inst_summary.get('trust_30d',0):+,} / 自營 {inst_summary.get('dealer_30d',0):+,}

## 融資餘額
最新：{margin_latest} 張，30日變化：{margin_change:+} 張

## 股價技術面
現價：{price.get("price", {}).get("current_price", "N/A")} 元（{price.get("price", {}).get("date", "")}）
均線：MA5={price.get("price", {}).get("ma5")} / MA10={price.get("price", {}).get("ma10")} / MA20={price.get("price", {}).get("ma20")} / MA60={price.get("price", {}).get("ma60")}
60日高點：{price.get("price", {}).get("high_60d")} / 60日低點：{price.get("price", {}).get("low_60d")}
現價偏離MA20：{price.get("price", {}).get("price_vs_ma20")}%
20日均量：{price.get("price", {}).get("avg_volume_20d", "N/A"):,} 股
外資持股比率：{price.get("shareholding", {}).get("foreign_pct")}%

## 券商報告摘要
{broker_text}

## 輸出格式（JSON）
{{
 "title": "公司名稱 (股票代碼) 個股研究報告",
 "rating": "買進/中立/賣出",
 "target_price": 數字,
 "section_1": "一、個股簡介...",
 "section_2": "二、成長引擎...",
 "section_3": "三、基本面佐證...",
 "section_4": "四、技術與籌碼面分析...",
 "section_5": "五、估值與投資建議...",
 "section_6": "六、潛在風險及觀察項目...",
 "section_7": "七、資料來源"
}}

注意事項：
- 全程使用繁體中文，嚴禁輸出簡體中文
- 所有數字必須來自上方提供的資料，不得捏造
- 月營收單位：元（非千元）
- 季報 EPS 單位：元/股
- 若某項資料不足，在該章節標註「資料待補」
- 只輸出 JSON，不要其他文字

報告模板：
{template_snippet}
"""
    return prompt

def call_minimax(prompt):
    """呼叫 M2.7 產生報告（thinking=off）"""
    payload = {
        "model": "MiniMax-M2.7",
        "max_tokens": 8000,
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "{"}
        ],
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            'x-api-key': MINIMAX_TOKEN,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode())

    text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
    if not text_blocks:
        raise RuntimeError("M2.7 無 text block 回應")
    raw = "{" + text_blocks[0]["text"].strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def main(stock_id, memo=""):
    print(f"[generate_report] 產生 {stock_id} 研究報告...")
    data = load_state(stock_id)

    missing = []
    if not data["financials"]:
        missing.append("financials")
    if not data["chips"]:
        missing.append("chips")
    if missing:
        print(f"[generate_report] ⚠️ 缺少資料: {missing}，請先執行對應腳本")
        return None

    prompt = build_prompt(stock_id, data, memo)
    print(f"[generate_report] prompt 長度: {len(prompt):,} 字元")

    report = call_minimax(prompt)
    out_path = WORKSPACE / "state" / f"research_{stock_id}_report.json"
    result = {
        "stock_id": stock_id,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "memo": memo,
        "report": report,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[generate_report] 完成，輸出至 {out_path}")
    print(f"評等：{report.get('rating')} 目標價：{report.get('target_price')}")
    return result

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    memo = sys.argv[2] if len(sys.argv) > 2 else "（無法說會memo）"
    main(stock_id, memo)