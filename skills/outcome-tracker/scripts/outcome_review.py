#!/usr/bin/env python3
"""outcome_review.py v2（2026-10-24）— 持股證偽檢查器

兩層：
  A. 數字條件（每天，程式比對，不經模型）：讀 📈 個股追蹤「反證條件」第一行【數字條件】，
     用 FinMind 最新已公布的月營收 YoY／單季毛利率比對；新一期資料觸發 → 立刻 Telegram 🔴（同一期只報一次）。
  B. thesis 判斷（M3，只在有新資料時跑）：新月營收或新季報出現、或「下次驗證日」到期 →
     把 thesis、反證條件、營收／毛利率數字、近期券商報告交給模型判定；股價只當背景、不當證據。
     下次驗證日一律設成「下一個月營收公布日」（下個 11 日），不再用模型給的天數。

數字條件語法（反證條件第一行，項目用 ｜ 分隔）：
  營收YoY<15        最新一個月營收 YoY（%）低於 15
  營收YoY連續2月<20  最近 2 個月 YoY 都低於 20
  毛利率<36         最新一季單季毛利率（%）低於 36
  EPS<2.1           最新一季單季 EPS 低於 2.1

用法：
  python3 outcome_review.py            # 正式：寫 Notion、狀態檔、發 Telegram
  python3 outcome_review.py dry        # 只讀：照常抓資料與呼叫模型，但不寫任何東西、不發 Telegram
  python3 outcome_review.py dry 8103   # 只跑指定代碼
"""
import ast
import json
import re
import sys
import time
import traceback
import html as _html
from datetime import date, timedelta
from pathlib import Path

import requests

WORKSPACE = Path.home() / ".openclaw" / "workspace"
SECRETS_FILE = WORKSPACE / "config" / "secrets.json"
OUTCOME_JSONL = WORKSPACE / "knowledge" / "outcome_log.jsonl"
STATE_FILE = WORKSPACE / "knowledge" / "outcome_state.json"

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
MINIMAX_BASE = "https://api.minimax.io/anthropic/v1"
MINIMAX_MODEL = "MiniMax-M3"
VERDICTS = ("已驗證符合", "部分符合", "已驗證反證", "資料不足")

STATS = {"warn": 0, "llm_fail": 0, "data_fail": 0}


def warn(msg):
    STATS["warn"] += 1
    print("  [WARN] " + msg)


def load_secrets():
    return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))


def notion_headers(secrets):
    return {"Authorization": "Bearer " + secrets["notion_key"],
            "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def rt(content):
    return [{"text": {"content": str(content)[:2000]}}]


def plain(prop):
    """Notion title／rich_text → 純文字（全部片段串起來）"""
    if not prop:
        return ""
    arr = prop.get("title") or prop.get("rich_text") or []
    return "".join(x.get("plain_text", "") for x in arr).strip()


# ---------------- 數字條件 ----------------
_RE_COND = re.compile(r"^(營收YoY|毛利率|EPS)(?:連續(\d+)月)?\s*<\s*(-?\d+(?:\.\d+)?)$")


def parse_conditions(text):
    """回傳 (conds, bad)。conds=[{"metric","months","lt","raw"}]；bad=認不得的片段"""
    first = (text or "").strip().split("\n", 1)[0]
    if "【數字條件】" not in first:
        return [], []
    body = first.split("【數字條件】", 1)[1]
    conds, bad = [], []
    for part in re.split(r"[｜|]", body):
        p = part.strip().replace(" ", "").replace("＜", "<")
        if not p:
            continue
        m = _RE_COND.match(p)
        if not m:
            bad.append(part.strip())
            continue
        conds.append({"metric": m.group(1), "months": int(m.group(2) or 1),
                      "lt": float(m.group(3)), "raw": part.strip()})
    return conds, bad


def finmind(dataset, stock_id, start, token):
    r = requests.get(FINMIND_API, params={"dataset": dataset, "data_id": stock_id,
                                          "start_date": start, "token": token}, timeout=20)
    d = r.json()
    if d.get("msg") != "success":
        raise RuntimeError(dataset + " " + str(d.get("msg"))[:80])
    return d.get("data") or []


def revenue_series(stock_id, token, today):
    """回傳 [(period 'YYYY-MM', revenue, yoy%或None)]，舊→新"""
    start = date(today.year - 3, 1, 1).isoformat()
    rows = finmind("TaiwanStockMonthRevenue", stock_id, start, token)
    by = {}
    for x in rows:
        try:
            by[(int(x["revenue_year"]), int(x["revenue_month"]))] = float(x["revenue"])
        except (KeyError, TypeError, ValueError):
            continue
    out = []
    for (y, m) in sorted(by):
        prev = by.get((y - 1, m))
        yoy = round((by[(y, m)] - prev) / prev * 100, 2) if prev else None
        out.append(("%04d-%02d" % (y, m), by[(y, m)], yoy))
    return out


def quarter_series(stock_id, token, today):
    """回傳 [(quarter_end 'YYYY-MM-DD', gm%或None, eps或None)]，舊→新"""
    start = date(today.year - 2, 1, 1).isoformat()
    rows = finmind("TaiwanStockFinancialStatements", stock_id, start, token)
    q = {}
    for x in rows:
        q.setdefault(x.get("date"), {})[x.get("type")] = x.get("value")
    out = []
    for d in sorted(k for k in q if k):
        v = q[d]
        gm = None
        try:
            if v.get("Revenue") and v.get("GrossProfit") is not None and float(v["Revenue"]) != 0:
                gm = round(float(v["GrossProfit"]) / float(v["Revenue"]) * 100, 2)
        except (TypeError, ValueError):
            gm = None
        eps = v.get("EPS")
        try:
            eps = float(eps) if eps is not None else None
        except (TypeError, ValueError):
            eps = None
        out.append((d, gm, eps))
    return out


def check_conditions(conds, rev, qtr):
    """回傳 (hits, notes)。hits=[{"raw","detail","period","metric"}]"""
    hits, notes = [], []
    for c in conds:
        if c["metric"] == "營收YoY":
            vals = [r for r in rev if r[2] is not None]
            if len(vals) < c["months"]:
                notes.append(c["raw"] + "：月營收資料不足")
                continue
            last = vals[-c["months"]:]
            if all(r[2] < c["lt"] for r in last):
                hits.append({"raw": c["raw"], "metric": "rev", "period": last[-1][0],
                             "detail": "、".join("%s YoY %.1f%%" % (r[0], r[2]) for r in last)})
        else:
            idx = 1 if c["metric"] == "毛利率" else 2
            vals = [q for q in qtr if q[idx] is not None]
            if not vals:
                notes.append(c["raw"] + "：季報資料不足")
                continue
            d, v = vals[-1][0], vals[-1][idx]
            if v < c["lt"]:
                unit = "%" if idx == 1 else " 元"
                hits.append({"raw": c["raw"], "metric": "qtr", "period": d,
                             "detail": "%s 單季%s %.2f%s" % (d, c["metric"], v, unit)})
    return hits, notes


def next_review_date(today):
    """下一個月營收公布日（11 日）"""
    if today.day < 11:
        return date(today.year, today.month, 11)
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(y, m, 11)


# ---------------- Notion ----------------
def tracked_rows(secrets, symbol=None):
    flt = {"property": "狀態", "select": {"does_not_equal": "不繼續追蹤"}}
    out, cur = [], None
    while True:
        body = {"filter": flt, "page_size": 100}
        if cur:
            body["start_cursor"] = cur
        r = requests.post(NOTION_API + "/databases/" + secrets["notion_stock_tracking_db"] + "/query",
                          headers=notion_headers(secrets), json=body, timeout=30)
        r.raise_for_status()
        d = r.json()
        out += d.get("results", [])
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")
        time.sleep(0.3)
    if symbol:
        out = [p for p in out if plain(p["properties"].get("股票代碼")).replace(".TW", "").replace(".TWO", "") == symbol]
    return out


def recent_broker_reports(secrets, stock_id, today, limit=5):
    """近 45 天券商個股報告摘要（讀不到不影響主流程）"""
    db = secrets.get("notion_broker_reports_db")
    if not db:
        return []
    body = {"filter": {"and": [
        {"property": "股票代碼", "title": {"contains": stock_id}},
        {"property": "報告日期", "date": {"on_or_after": (today - timedelta(days=45)).isoformat()}}]},
        "sorts": [{"property": "報告日期", "direction": "descending"}], "page_size": limit}
    try:
        r = requests.post(NOTION_API + "/databases/" + db + "/query",
                          headers=notion_headers(secrets), json=body, timeout=30)
        r.raise_for_status()
        out = []
        for p in r.json().get("results", []):
            pr = p["properties"]
            sel = lambda k: ((pr.get(k) or {}).get("select") or {}).get("name", "")
            num = (pr.get("目標價") or {}).get("number")
            dt = ((pr.get("報告日期") or {}).get("date") or {}).get("start", "")
            out.append("%s %s %s 目標價%s：%s" % (dt, sel("券商名稱"), sel("評等"),
                                                 num if num is not None else "—", plain(pr.get("核心觀點"))[:120]))
        return out
    except Exception as e:
        warn("券商報告讀取失敗 %s: %s" % (stock_id, repr(e)[:80]))
        return []


def price_context(stock_id, start, token):
    try:
        a = finmind("TaiwanStockPrice", stock_id, start, token)
        b = finmind("TaiwanStockPrice", "TAIEX", start, token)
        if a and b:
            s = (a[-1]["close"] - a[0]["close"]) / a[0]["close"] * 100
            i = (b[-1]["close"] - b[0]["close"]) / b[0]["close"] * 100
            return "立案（%s）以來股價 %.1f%%、大盤 %.1f%%" % (start, s, i)
    except Exception as e:
        warn("股價讀取失敗 %s: %s" % (stock_id, repr(e)[:80]))
    return "股價資料不可用"


# ---------------- M3 ----------------
def _parse_judgment(text):
    s = text.strip()
    if "```" in s:
        seg = s.split("```", 1)[1]
        seg = seg.split("\n", 1)[1] if "\n" in seg else seg
        s = seg.rsplit("```", 1)[0]
    a, b = s.find("{"), s.rfind("}")
    obj = s[a:b + 1] if (a != -1 and b > a) else ""
    for loader in (json.loads, ast.literal_eval):
        try:
            v = loader(obj)
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    raise ValueError("模型輸出無法解析: " + repr(text[:120]))


def llm_judge(code, name, thesis, falsify, catalyst, rev, qtr, hits, reports, price_txt, secrets):
    rev_txt = "；".join("%s %s" % (p, ("YoY %.1f%%" % y) if y is not None else "YoY 無") for p, _, y in rev[-6:]) or "無"
    q_txt = "；".join("%s 毛利率 %s EPS %s" % (d, ("%.1f%%" % g) if g is not None else "無", e if e is not None else "無")
                     for d, g, e in qtr[-4:]) or "無"
    hit_txt = "；".join(h["detail"] + "（條件 " + h["raw"] + "）" for h in hits) or "無"
    rep_txt = "\n".join("- " + x for x in reports) or "（近 45 天無）"
    prompt = (
        "股票：" + code + " " + name + "\n"
        "核心thesis：" + thesis + "\n"
        "反證條件：" + falsify + "\n"
        "期待催化劑：" + catalyst + "\n"
        "近 6 個月營收：" + rev_txt + "\n"
        "近 4 季：" + q_txt + "\n"
        "已觸發的數字條件：" + hit_txt + "\n"
        "近期券商報告：\n" + rep_txt + "\n"
        "股價背景（只供參考，不是 thesis 證據）：" + price_txt + "\n\n"
        "請根據營收、毛利率、券商報告等基本面事實，判定 thesis 目前是否仍成立。"
        "不要用股價漲跌或相對大盤表現當作判定依據。數字條件已觸發時不可判「已驗證符合」。"
        "輸出純 JSON，key 必須完全是：\n"
        '{"verdict":"已驗證符合|部分符合|已驗證反證|資料不足 四選一",'
        '"summary":"2 句話說明依據（繁體中文）",'
        '"falsified":["已成立的反證條件原文，沒有就空陣列"],'
        '"watch":"下一個要看的具體數字或事件"}'
    )
    payload = {"model": MINIMAX_MODEL, "max_tokens": 1024, "thinking": {"type": "disabled"},
               "system": "你是投資 thesis 證偽檢查員。只輸出一個 JSON 物件，不要 markdown，不要說明文字。所有文字用繁體中文。",
               "messages": [{"role": "user", "content": prompt}]}
    r = requests.post(MINIMAX_BASE + "/messages", json=payload, timeout=90,
                      headers={"Content-Type": "application/json", "x-api-key": secrets["minimax_api_key"]})
    r.raise_for_status()
    blocks = r.json().get("content") or []
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text").strip()
    if not text:
        raise ValueError("模型無 text block: " + str([b.get("type") for b in blocks if isinstance(b, dict)]))
    j = _parse_judgment(text)
    v = str(j.get("verdict", "")).strip()
    j["verdict"] = next((x for x in VERDICTS if x in v), "資料不足")
    if hits and j["verdict"] == "已驗證符合":
        j["verdict"] = "部分符合"
        j["summary"] = "（數字條件已觸發，由程式降為部分符合）" + str(j.get("summary", ""))
    return j


# ---------------- 寫入 ----------------
def write_outcome_log(code, page_id, j, hits, secrets, today):
    review_id = code + "_" + today.isoformat()
    vmap = {"已驗證符合": "已驗證符合", "部分符合": "部分符合", "已驗證反證": "已驗證反證", "資料不足": "部分符合"}
    props = {"review_id": {"title": rt(review_id)}, "股票代碼": {"rich_text": rt(code)},
             "review_date": {"date": {"start": today.isoformat()}},
             "驗證狀態": {"select": {"name": vmap.get(j["verdict"], "部分符合")}}}
    r = requests.post(NOTION_API + "/pages", headers=notion_headers(secrets), timeout=15,
                      json={"parent": {"database_id": secrets["notion_outcome_log_db"]}, "properties": props})
    r.raise_for_status()
    OUTCOME_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUTCOME_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"review_id": review_id, "symbol": code, "stock_tracking_page_id": page_id,
                            "review_date": today.isoformat(), "verdict": j["verdict"],
                            "summary": j.get("summary", ""), "falsified": j.get("falsified", []),
                            "watch": j.get("watch", ""), "numeric_hits": hits, "model": MINIMAX_MODEL},
                           ensure_ascii=False) + "\n")


def update_tracking(page_id, j, today, secrets):
    vmap = {"已驗證符合": "已驗證符合", "部分符合": "部分符合", "已驗證反證": "已驗證反證", "資料不足": "待驗證"}
    props = {"Outcome狀態": {"select": {"name": vmap.get(j["verdict"], "待驗證")}},
             "最近Outcome結果": {"rich_text": rt("%s：%s｜下一步看：%s" % (today.isoformat(), j.get("summary", ""), j.get("watch", "")))},
             "下次驗證日": {"date": {"start": next_review_date(today).isoformat()}}}
    r = requests.patch(NOTION_API + "/pages/" + page_id, headers=notion_headers(secrets),
                       json={"properties": props}, timeout=15)
    r.raise_for_status()


def notify(msg, secrets):
    try:
        r = requests.post("https://api.telegram.org/bot%s/sendMessage" % secrets["telegram_bot_token"],
                          json={"chat_id": secrets["telegram_dm"], "text": msg[:3900], "parse_mode": "HTML"}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print("[WARN] Telegram 失敗: " + repr(e)[:120])


def esc(s):
    return _html.escape(str(s))


# ---------------- 主流程 ----------------
def main(argv):
    dry = "dry" in argv or "--dry-run" in argv
    symbol = next((a for a in argv if re.fullmatch(r"\d{4,6}", a)), None)
    today = date.today()
    secrets = load_secrets()
    token = secrets["finmind_token"]
    print("[START] outcome_review v2 %s dry=%s model=%s" % (today.isoformat(), dry, MINIMAX_MODEL))
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            warn("狀態檔讀取失敗，視為空白: " + repr(e)[:80])
    rows = tracked_rows(secrets, symbol)
    print("[1/3] 追蹤中 %d 檔" % len(rows))
    alerts, reviews, problems, baseline = [], [], [], []
    for page in rows:
        pr = page["properties"]
        code = plain(pr.get("股票代碼")).replace(".TW", "").replace(".TWO", "")
        name = plain(pr.get("公司名稱"))
        thesis, falsify, catalyst = plain(pr.get("核心thesis")), plain(pr.get("反證條件")), plain(pr.get("期待催化劑"))
        start = ((pr.get("初次加入日") or {}).get("date") or {}).get("start") or (today - timedelta(days=90)).isoformat()
        due_s = ((pr.get("下次驗證日") or {}).get("date") or {}).get("start")
        print("\n  處理 %s %s" % (code, name))
        if not re.fullmatch(r"\d{4,6}", code):
            warn("代碼格式不對，跳過: %r" % code)
            problems.append("%s 代碼格式不對" % code)
            continue
        conds, bad = parse_conditions(falsify)
        for b in bad:
            warn("認不得的數字條件: " + b)
            problems.append("%s 數字條件寫法認不得：%s" % (code, b))
        try:
            rev = revenue_series(code, token, today)
            time.sleep(0.5)
            qtr = quarter_series(code, token, today)
            time.sleep(0.5)
        except Exception as e:
            STATS["data_fail"] += 1
            warn("FinMind 失敗: " + repr(e)[:120])
            problems.append("%s 營收／季報抓取失敗" % code)
            continue
        rev_p = rev[-1][0] if rev else None
        q_p = qtr[-1][0] if qtr else None
        print("  [2/3] 最新月營收 %s %s｜最新季報 %s 毛利率 %s" % (
            rev_p, ("YoY %.1f%%" % rev[-1][2]) if rev and rev[-1][2] is not None else "—",
            q_p, ("%.1f%%" % qtr[-1][1]) if qtr and qtr[-1][1] is not None else "—"))
        first_time = code not in state
        st = state.get(code, {})
        hits, notes = check_conditions(conds, rev, qtr)
        for n in notes:
            print("    [NOTE] " + n)
        for h in hits:
            key = h["raw"] + "@" + h["period"]
            fresh = key not in st.get("alerted", [])
            print("    [HIT] %s → %s%s" % (h["raw"], h["detail"], "" if fresh else "（已通報過）"))
            if fresh:
                # 第一次看到這檔：既有資料只列為基準、不發 🔴（避免拿舊季報誤報）
                (baseline if first_time else alerts).append((code, name, h))
                st.setdefault("alerted", []).append(key)
        new_rev = rev_p is not None and rev_p != st.get("rev_period")
        new_q = q_p is not None and q_p != st.get("q_period")
        due = bool(due_s) and due_s <= today.isoformat()
        if new_rev or new_q or due:
            why = "、".join(w for w, f in (("新月營收 " + str(rev_p), new_rev), ("新季報 " + str(q_p), new_q), ("驗證日到期", due)) if f)
            print("  [3/3] 模型判定（%s）..." % why)
            reports = recent_broker_reports(secrets, code, today)
            try:
                j = llm_judge(code, name, thesis, falsify, catalyst, rev, qtr, hits, reports,
                              price_context(code, start, token), secrets)
            except Exception as e:
                STATS["llm_fail"] += 1
                warn("模型判定失敗: " + repr(e)[:160])
                problems.append("%s 模型判定失敗（沒有改狀態）" % code)
                j = None
            if j is not None:
                print("    verdict=%s｜%s" % (j["verdict"], str(j.get("summary", ""))[:80]))
                reviews.append((code, name, j, why))
                if not dry:
                    try:
                        write_outcome_log(code, page["id"], j, hits, secrets, today)
                        update_tracking(page["id"], j, today, secrets)
                    except Exception as e:
                        warn("Notion 寫入失敗: " + repr(e)[:120])
                        problems.append("%s Notion 寫入失敗" % code)
                st["rev_period"], st["q_period"] = rev_p, q_p
        else:
            print("  [3/3] 沒有新資料、未到期，略過模型判定")
        state[code] = st
        time.sleep(0.5)

    print("\n[SUMMARY] 數字觸發 %d｜基準（首次）%d｜模型判定 %d｜問題 %d｜WARN %d｜模型失敗 %d｜資料失敗 %d" % (
        len(alerts), len(baseline), len(reviews), len(problems), STATS["warn"], STATS["llm_fail"], STATS["data_fail"]))
    if dry:
        print("[DRY] 不寫狀態檔、不寫 Notion、不發 Telegram")
        print("RESULT=DRY_DONE")
        return 0
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if alerts:
        lines = ["🔴 <b>持股證偽條件觸發</b>"]
        for code, name, h in alerts:
            lines.append("%s %s：%s（條件 %s）" % (esc(code), esc(name), esc(h["detail"]), esc(h["raw"])))
        lines.append("→ 照出場計畫處理；thesis 判定見下一則或 📈 個股追蹤")
        notify("\n".join(lines), secrets)
    if reviews or problems or baseline:
        icon = {"已驗證符合": "✅", "部分符合": "⚠️", "已驗證反證": "❌", "資料不足": "❓"}
        lines = ["🔍 <b>持股 thesis 檢查</b>（%s）" % MINIMAX_MODEL]
        for code, name, j, why in reviews:
            lines.append("%s %s %s → %s（%s）" % (icon.get(j["verdict"], "❓"), esc(code), esc(name), esc(j["verdict"]), esc(why)))
            lines.append("   " + esc(str(j.get("summary", ""))[:160]))
            if j.get("falsified"):
                lines.append("   已成立反證：" + esc("；".join(map(str, j["falsified"]))[:160]))
        for code, name, h in baseline:
            lines.append("ℹ️ 基準（首次檢查、既有資料）%s %s：%s（條件 %s）" % (esc(code), esc(name), esc(h["detail"]), esc(h["raw"])))
        for p in problems:
            lines.append("⚠️ " + esc(p))
        notify("\n".join(lines), secrets)
    print("RESULT=OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        print(tb)
        if "dry" not in sys.argv[1:]:
            try:
                notify("🔴 outcome_review 崩潰\n" + esc(tb[-1200:]), load_secrets())
            except Exception:
                pass
        print("RESULT=CRASH")
        sys.exit(2)
