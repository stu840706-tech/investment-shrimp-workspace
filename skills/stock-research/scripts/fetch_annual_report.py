#!/usr/bin/env python3
"""fetch_annual_report.py - 從 TWSE 下載最新年報 PDF 並萃取重點文字"""
import sys, json, urllib.request, urllib.parse, time, re, subprocess
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / 'workflows'))
from _common import SECRETS, MINIMAX_API_KEY as MINIMAX_TOKEN

WORKSPACE = Path(__file__).parent.parent.parent.parent
PDF_DISPATCH = WORKSPACE / "skills/pdf-reader/scripts/pdf_dispatch.py"

def find_annual_report_url(stock_id):
    """查 TWSE 公開資訊觀測站找年報 PDF 連結"""
    url = (
        f"https://doc.twse.com.tw/server-java/t57sb01?"
        f"id=&key=&step=1&co_id={stock_id}&sedate=&mtype=F&dtype=F04"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read().decode("utf-8", errors="ignore")
        links = re.findall(r'href=["\']([^"\']*\.pdf)["\']', content, re.IGNORECASE)
        if not links:
            links = re.findall(r'(https?://doc\.twse\.com\.tw[^\s"\'<>]+)', content)
        return links[0] if links else None
    except Exception as e:
        print(f"[fetch_annual_report] TWSE 查詢失敗: {e}")
        return None

def download_pdf(url, out_path):
    """下載 PDF"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        out_path.write_bytes(r.read())
    print(f"[fetch_annual_report] PDF 下載完成: {out_path} ({out_path.stat().st_size/1024:.0f} KB)")

def extract_text(pdf_path):
    """用 pdf-reader skill 轉文字"""
    result = subprocess.run(
        ["python3", str(PDF_DISPATCH), str(pdf_path)],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdf_dispatch 失敗: {result.stderr}")
    return result.stdout

def extract_key_points(text, stock_id):
    """M2.7 萃取年報重點（thinking=off，輸入截斷至 40K chars）"""
    text_truncated = text[:40000]
    prompt = f"""以下是台股 {stock_id} 的年報文字內容。請萃取以下資訊，以 JSON 格式輸出：

{{
 "business_overview": "公司主要業務說明（2-3句）",
 "products": [
   {{"name": "產品/業務線名稱", "description": "說明", "revenue_pct": "營收佔比（若有）"}}
 ],
 "supply_chain": {{
   "upstream": "上游原料/供應商描述",
   "downstream": "下游客戶/應用描述"
 }},
 "competitive_advantage": "核心競爭優勢（護城河）",
 "major_customers": ["客戶1", "客戶2"],
 "competitors": ["競爭對手1（股號）", "競爭對手2（股號）"],
 "key_risks": ["風險1", "風險2"],
 "capex_plan": "近期資本支出計畫（若有）",
 "source_year": "年報年份"
}}

年報內容：
{text_truncated}

只輸出 JSON，不要其他文字。"""

    payload = {
        "model": "MiniMax-Text-01",
        "max_tokens": 1000,
        "thinking": {"type": "disabled"},
        "messages": [{"role": "user", "content": prompt}],
    }
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        "https://api.minimax.io/anthropic/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": MINIMAX_TOKEN,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as r:
        resp = json.loads(r.read().decode())

    text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
    if not text_blocks:
        raise RuntimeError("M2.7 無 text block 回應")
    raw = text_blocks[0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())

def main(stock_id, pdf_path=None):
    print(f"[fetch_annual_report] 處理 {stock_id} 年報...")
    out_path = WORKSPACE / "state" / f"research_{stock_id}_annual.json"

    if pdf_path:
        pdf_file = Path(pdf_path)
        print(f"[fetch_annual_report] 使用手動提供的 PDF: {pdf_file}")
    else:
        print("[fetch_annual_report] 嘗試從 TWSE 下載...")
        url = find_annual_report_url(stock_id)
        if not url:
            print(f"[fetch_annual_report] ⚠️ 找不到年報 PDF，請手動提供")
            print(f"NEED_PDF:{stock_id}")
            return None
        pdf_file = WORKSPACE / "state" / f"annual_report_{stock_id}.pdf"
        download_pdf(url, pdf_file)

    print("[fetch_annual_report] 轉換 PDF 為文字...")
    text = extract_text(pdf_file)
    print(f"[fetch_annual_report] 萃取文字 {len(text):,} 字元")

    print("[fetch_annual_report] M2.7 萃取年報重點...")
    key_points = extract_key_points(text, stock_id)

    result = {
        "stock_id": stock_id,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "pdf_path": str(pdf_file),
        "text_length": len(text),
        "key_points": key_points,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[fetch_annual_report] 完成，輸出至 {out_path}")
    return result

if __name__ == "__main__":
    stock_id = sys.argv[1] if len(sys.argv) > 1 else "4755"
    pdf_path = sys.argv[2] if len(sys.argv) > 2 else None
    main(stock_id, pdf_path)