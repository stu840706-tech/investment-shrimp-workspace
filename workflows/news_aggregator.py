#!/usr/bin/env python3
"""
Layer 2: Aggregator Agent - 批次版
- 每批 10 則新聞一起送 LLM 分析
- 嚴格的 signal 等級規則（來源的 source_count 必須在 cluster 後計算）
- Impact 推論必須有內文依據
"""

from _common import FINMIND_TOKEN, MINIMAX_API_KEY, TELEGRAM_TOKEN, TELEGRAM_DM
import json, sys, time, re, requests
from datetime import datetime, timedelta
from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent / "memory"
MINIMAX_BASE = "https://api.minimax.io/anthropic/v1"
# ⚠️ 原始程式碼中 MINIMAX_TOKEN 與 FINMIND_TOKEN 使用相同 value
# 兩個 API 共用 token 不是標準做法，建議確認是否為誤用
MINIMAX_TOKEN = MINIMAX_API_KEY
TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN

BATCH_SIZE = 3           # 每批 3 則
MAX_LLM_ITEMS = 120       # 最多 LLM 分析 80 則

# ============================================================
# 工具函式
# ============================================================

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try: requests.post(url, json={'chat_id': TELEGRAM_DM, 'text': text}, timeout=10)
    except: pass

def get_hour_arg():
    return sys.argv[1].zfill(2) if len(sys.argv) >= 2 else datetime.now().strftime("%H")

def check_raw_files(hour):
    ts = f"{datetime.now().strftime('%Y%m%d')}-{hour}"
    missing = [f"memory/raw-{c}-{ts}.jsonl" for c in ["tw","mops","intl","industry"]
               if not (MEMORY_DIR / f"raw-{c}-{ts}.jsonl").exists()]
    return missing

def load_raw_files(hour):
    ts = f"{datetime.now().strftime('%Y%m%d')}-{hour}"
    all_items = []
    for cat in ["tw","mops","intl","industry"]:
        f = MEMORY_DIR / f"raw-{cat}-{ts}.jsonl"
        if f.exists():
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    if line.strip():
                        try: all_items.append(json.loads(line))
                        except: pass
    return all_items

def load_fingerprints():
    fp_file = MEMORY_DIR / "news-fingerprints.md"
    if not fp_file.exists(): return {}
    fp = {}
    with open(fp_file, encoding='utf-8') as f:
        for line in f:
            if '\t' in line:
                p = line.strip().split('\t', 1)
                if len(p) == 2: key = p[0].strip().lstrip("-").strip(); fp[key] = p[1].strip()
    return fp

def save_fingerprints(fp_dict):
    fp_file = MEMORY_DIR / "news-fingerprints.md"
    lines = [f"{fp}\t{date}" for fp, date in fp_dict.items()]
    with open(fp_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

def score_item(it):
    """預篩選評分：數字/關鍵字/公司代碼"""
    text = (it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')).lower()
    score = 0
    if re.search(r'\d+[%％元億萬]', text): score += 2
    for kw in ['營收','EPS','目標價','評等','買進','漲','擴產','訂單','量產','新高','增','擴']:
        if kw in text: score += 1
    for kw in ['裁員','停工','火災','違約','假帳','掏空','下市','破產']:
        score += 3
    if re.search(r'\b[12][0-9]{3}\b', text): score += 1
    return score

def fact_layer_filter(items):
    import re
    layer3_patterns = [
        '多空對決', '後市怎麼走', '行情展望', '盤勢分析',
        '本週操作策略', '下週展望', '技術面分析', '籌碼面解讀',
        '大盤走勢', '指數預測', '市場氣氛', '投資人情緒',
    ]
    layer1_patterns = [
        '%', 'EPS', '目標價', '營收',
        '法說會', '財報', '除權息', '配息', '增資',
        '擴產', '量產', '出貨', '訂單', '簽約',
    ]
    layer1, layer2, layer3 = [], [], []
    for it in items:
        text = it.get('title', '') + ' ' + (it.get('body_snippet', '') or '')
        is_layer3 = any(p in text for p in layer3_patterns)
        has_number = bool(re.search(r'\d+[%元億萬]|\d{4}年|\d+月', text))
        if is_layer3 and not has_number:
            it['_fact_layer'] = 3
            layer3.append(it)
        elif any(p in text for p in layer1_patterns):
            it['_fact_layer'] = 1
            layer1.append(it)
        else:
            it['_fact_layer'] = 2
            layer2.append(it)
    print(f"  Fact預審：L1={len(layer1)} L2={len(layer2)} L3={len(layer3)}（跳過）")
    return layer1 + layer2, layer3


def dedup_title(items):
    """第一階段去重：title 前 25 字相同者合併來源"""
    seen = {}
    result = []
    for it in items:
        key = it.get('title', '')[:25].lower().strip()
        if not key: continue
        if key not in seen:
            seen[key] = it
            result.append(it)
        else:
            exist = seen[key]
            srcs = exist.get('_sources', [exist.get('source','')])
            if it.get('source', '') not in srcs:
                srcs.append(it.get('source', ''))
            exist['_sources'] = srcs
            exist['_count'] = exist.get('_count', 1) + 1
    return result


def garbage_filter(items):
    """Step 2.5: 過濾垃圾標題"""
    import re
    kept = []
    dropped = 0
    for it in items:
        title = it.get('title', '')
        
        # 1. 純年份數字標題（如「2026、2026」）
        if re.match(r'^[\d{4},，,\s]+$', title.strip()):
            dropped += 1
            continue
        
        # 2. title 少於 6 個中文字元且無英文實質內容
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', title))
        has_meaningful_english = len(re.findall(r'[a-zA-Z]{4,}', title)) > 0
        if chinese_count < 6 and not has_meaningful_english:
            dropped += 1
            continue
        
        # 3. title 開頭是「- 」加來源名稱（如「- 工商時報」）
        if re.match(r'^[-–]\s*[\u4e00-\u9fff]', title):
            dropped += 1
            continue
        
        if any(kw in title for kw in ['閃兵', '藝人', '起訴', '代言', 'MV', '緋聞', '整形', '八卦', '紅毯', '時尚']):
            dropped += 1
            continue
        kept.append(it)
    
    if dropped:
        print(f"  [過濾] 垃圾標題 {dropped} 筆")
    return kept

# ============================================================
# LLM 呼叫（批次模式）
# ============================================================

LLM_PROMPT = """你是台股新聞分析師。請分析以下 N 則新聞，對每則輸出信號等级、事實、影響、公司代碼和事件指紋。

【輸出格式】只輸出 JSON array，不要任何說明文字或 markdown：
[{"id":1,"signal":"high/medium/low","fact":"核心數字事實","impact":"一句話影響","companies":["代號"],"cluster_id":"公司_數字_事件"},{"id":2,...}]

【Signal 評級規則】必須同時滿足以下條件才能進 high：
(A) 新聞有具體個股代碼或公司名稱，且事件直接影響該公司營運、獲利、或股價
    或 新聞直接影響台灣半導體/AI/電子供應鏈的具體訂單、產能、或出貨數字
(B) 有具體數字（目標價、營收、漲跌幅、產能%、EPS等）

以下情況只能是 medium：
- 純大盤行情統計（台股漲跌點數、成交量、加權指數）
- 國際政治/總經新聞無具體台股受益或受害廠商

以下情況是 low：
- 分析師觀點無具體數字
- 國際政治事件無具體台股影響
- 公司相關傳聞但無具體數字

【影響欄位規則】每句推論必須有內文依據：
- 只寫能從 title 或 body_snippet 中找到直接依據的推論
- 每句推論必須對應一個具體的數字、事件、或當事人說法
- 禁止出現這些無專屬性的通用語句：
  ❌「顯示市場信心增強」
  ❌「帶動族群上漲」
  ❌「反映需求持續強勁」
  ❌「短期股價可能持續有撐」
  ❌「多頭格局延續」
  ❌ 任何可以複製貼上到任何一天台股新聞的句子
  ❌「[推論] 外資/法人看好」
  ❌「[推論] 需進一步確認」
  ❌「[推論] 法說結果將決定」

好的範例（每一句都有內文具體數字支撐）：
「大摩將目標價升至2750元，直接反映法人對其先进製程後市的樂觀態度」
「台積電CoWoS產能擴增30%，直接受益：2330；封裝供應鏈：3034、2454」

不好的範例（沒有具體數字支撐，適用於任何新聞）：
「台積電引領半導體族群，顯示AI晶片需求持續強勁，多頭格局延續」

【公司代碼】用 regex \\d{4} 從 title 或 body 中找，只填四位數數字

【事件指紋】格式「公司_核心數字_事件類型」，例如「台積電_2750元_目標價」

以下是新聞：
"""

def call_llm_batch(items, retries=2):
    """批次呼叫 LLM：一次分析 BATCH_SIZE 則新聞"""
    import urllib.request

    news_list = [
        {
            "id": i + 1,
            "title": it.get('title', '')[:60],
            "body": (it.get('body_snippet', '') or '')[:100],
            "source": it.get('source', '')
        }
        for i, it in enumerate(items)
    ]

    prompt = LLM_PROMPT + json.dumps(news_list, ensure_ascii=False)

    for attempt in range(retries):
        try:
            payload = {
                "model": "MiniMax-M2.7",
                "max_tokens": 3072,
                "thinking": {"type": "disabled"},
                "messages": [{"role": "user", "content": prompt}]
            }
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                f"{MINIMAX_BASE}/messages",
                data=data,
                headers={
                    'x-api-key': MINIMAX_TOKEN,
                    'Content-Type': 'application/json',
                    'anthropic-version': '2023-06-01',
                },
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=240) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                for block in result.get('content', []):
                    if block.get('type') == 'text' and block.get('text', '').strip():
                        text = block.get('text', '').strip()
                        text = re.sub(r'^```json\s*', '', text)
                        text = re.sub(r'^```\s*', '', text)
                        text = re.sub(r'\s*```$', '', text)
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return None
                return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(15 * (attempt + 1))
    return None

# ============================================================
# 規則分類（用於 LLM 失敗或非 top  items）
# ============================================================

def rule_classify(item):
    """規則分類：用於 LLM 失敗或非預選項目"""
    text = (item.get('title', '') + ' ' + (item.get('body_snippet', '') or '')).lower()
    has_num = bool(re.search(r'\d+[%％元億萬]', text))
    has_code = bool(re.search(r'\b[12][0-9]{3}\b', text))
    has_pos = any(kw in text for kw in ['營收','EPS','目標價','評等','買進','擴產','訂單','量產','新高'])
    has_neg = any(kw in text for kw in ['裁員','停工','火災','違約','假帳','掏空','下市','破產'])
    companies = re.findall(r'\b[12][0-9]{3}\b', item.get('title', '') + (item.get('body_snippet', '') or ''))[:5]

    sig = "low"
    if has_neg:
        sig = "high"
    elif has_pos and has_num and has_code:
        sig = "high"
    elif has_num and not any(kw in text for kw in ['台股漲','台股跌','加權指數','台股收盤']):
        sig = "medium"

    return {
        "title": item.get('title', ''),
        "url": item.get('url', ''),
        "source": item.get('source', ''),
        "_sources": item.get('_sources', [item.get('source', '')]),
        "fact": "無具體數字" if not has_num else "有具體數字",
        "impact": "",
        "signal": sig,
        "companies": companies,
        "cluster_id": "",
        "paywall": item.get('paywall', False),
    }

# ============================================================
# 第二階段：Cluster 彙總（關鍵：source_count 在此計算）
# ============================================================

def cluster_items(items):
    """按 title 前 30 字分組，彙總同事件不同來源"""
    clusters = {}
    for it in items:
        key = it.get('title', '')[:30].lower().strip()
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(it)

    result = []
    for key, group in clusters.items():
        if len(group) == 1:
            base = group[0]
            result.append({
                "title": base.get('title', ''),
                "url": base.get('url', ''),
                "source": base.get('source', ''),
                "sources_list": base.get('_sources', [base.get('source', '')]),
                "published_at": base.get('published_at', ''),
                "fact": base.get('fact', ''),
                "impact": base.get('impact', ''),
                "signal": base.get('signal', 'low'),
                "companies": base.get('companies', []),
                "cluster_id": base.get('cluster_id', ''),
                "source_count": len(base.get('_sources', [base.get('source', '')])),
                "paywall": base.get('paywall', False),
            })
        else:
            # 合併多個來源
            base = group[0]
            all_srcs = []
            all_comps = set()
            all_facts = []
            all_impacts = []

            for g in group:
                all_srcs.extend(g.get('_sources', [g.get('source', '')]))
                if g.get('companies'):
                    all_comps.update(g['companies'])
                if g.get('fact') and '無具體數字' not in g.get('fact', ''):
                    all_facts.append(g['fact'][:50])
                if g.get('impact'):
                    all_impacts.append(g['impact'][:80])

            all_srcs = list(set(all_srcs))

            # 保留事實最完整的 impact
            best_impact = base.get('impact', '')
            for imp in all_impacts:
                if imp and len(imp) > len(best_impact):
                    best_impact = imp

            result.append({
                "title": base.get('title', ''),
                "url": base.get('url', ''),
                "source": all_srcs[0] if all_srcs else '',
                "sources_list": all_srcs,
                "published_at": base.get('published_at', ''),
                "fact": ' | '.join(all_facts[:2]) if all_facts else base.get('fact', ''),
                "impact": best_impact,
                "signal": base.get('signal', 'low'),
                "companies": list(dict.fromkeys(all_comps))[:5],
                "cluster_id": base.get('cluster_id', '') or key,
                "source_count": len(all_srcs),  # 關鍵：合併後的來源數
                "paywall": any(g.get('paywall', False) for g in group),
            })
    # 將 companies 改為 tuple 後再做 dict dedup（解決 list unhashable 問題）
    def make_hashable(d):
        new_d = {}
        for k, v in d.items():
            if isinstance(v, list):
                new_d[k] = tuple(v)
            elif isinstance(v, dict):
                new_d[k] = make_hashable(v)
            else:
                new_d[k] = v
        return new_d
    unique_items = {tuple(sorted(make_hashable(d).items())): d for d in result}
    result = list(unique_items.values())

    return merge_by_company(result)


# ============================================================
# 公司代碼層級合併（針對同公司同季度財報）
# ============================================================
def merge_by_company(result):
    """同一公司代碼的新聞合併，保留事實最完整的一則"""
    # Step 1: 先按 cluster_id 去重（同一事件指紋只留一則）
    cluster_seen = {}
    deduped = []
    for it in result:
        cid = it.get('cluster_id', '')
        if cid and cid in cluster_seen:
            continue  # 已有，跳過
        cluster_seen[cid] = len(deduped)
        deduped.append(it)
    
    # Step 2: 再按公司代碼去重（同一公司只留一則，fact 最完整的）
    company_groups = {}
    for it in deduped:
        for c in it.get('companies', []):
            if c not in company_groups:
                company_groups[c] = []
            company_groups[c].append(it)
    
    merged = []
    seen_ids = set()
    for c, group in company_groups.items():
        if len(group) < 2:
            merged.extend([g for g in group if id(g) not in seen_ids])
            continue
        best = max(group, key=lambda x: len(x.get('fact','')))
        new_item = dict(best)
        all_srcs = list(set(sum([g.get('sources_list', [g.get('source','')]) for g in group], [])))
        all_comps = list(dict.fromkeys(sum([g.get('companies', []) for g in group], [])))
        new_item['sources_list'] = all_srcs
        new_item['source_count'] = len(all_srcs)
        new_item['fact'] = best.get('fact','')
        new_item['companies'] = all_comps[:5] if all_comps else [c]
        seen_ids.update(id(g) for g in group)
        merged.append(new_item)
    
    for it in deduped:
        if id(it) not in seen_ids:
            merged.append(it)
            seen_ids.add(id(it))
    return merged


def apply_signal_rules(clustered):
    """根據來源數量套用嚴格的信號規則"""
    for it in clustered:
        sc = it.get('source_count', 1)
        sig = it.get('signal', 'low')
        srcs = it.get('sources_list', [])
        is_mops = any('MOPS' in s for s in srcs)
        has_fact = it.get('fact', '') not in ['無具體數字', '', '有具體數字']

        # 來源數量過濾
        if sc >= 2:
            # 2+ 來源：根據有事實數字來決定
            if sig == 'high' and has_fact:
                it['signal'] = 'high'
            else:
                it['signal'] = 'medium'
        elif sc == 1:
            # 單一來源：MOPS 進 high，可信媒體 + LLM high 維持 high
            if is_mops:
                it['signal'] = 'high'
            elif sig == 'high':
                trusted = {'工商時報', '經濟日報', '鉅亨網', 'MoneyDJ', 'LTN', 'UDN',
                          'DigiTimes', 'CNBC', 'Bloomberg', 'Reuters', 'Nikkei', 'Google新聞'}
                src_set = set(it.get('sources_list', [it.get('source', '')]))
                if src_set & trusted or has_fact:
                    it['signal'] = 'high'
                else:
                    it['signal'] = 'pending'
            elif sig == 'medium':
                it['signal'] = 'medium'
            else:
                it['signal'] = 'pending'

        # 大盤行情降級
        title_lower = it.get('title', '').lower()
        if any(kw in title_lower for kw in ['台股漲', '台股跌', '加權指數', '台股收盤', '週線', '日線']):
            if not any(kw in title_lower for kw in ['半導體', 'AI', '訂單', '營收', 'EPS', '目標價', '擴產', '量產']):
                it['signal'] = 'medium'

        # 國際政治降級：國際來源若無台灣關聯則降 low
        INTL_SOURCES = {'CNBC', 'Bloomberg', 'Reuters', 'TechCrunch', 'Investing', 'Nikkei'}
        is_intl = it.get('source', '') in INTL_SOURCES or any(s in INTL_SOURCES for s in it.get('sources_list', []))
        if is_intl:
            tw_keywords = ['taiwan', 'tsmc', 'nvidia', 'apple', 'samsung', 'semiconductor', 'ai chip',
                          'hbm', 'cowos', 'supply chain', 'tariff', '台積', '輝達', '蘋果', '關稅', 'nand', 'memory']
            body_lower = (it.get('body_snippet', '') or '').lower()
            tw_related = any(kw in title_lower or kw in body_lower for kw in tw_keywords)
            if not tw_related:
                it['signal'] = 'low'
        else:
            # 非國際來源：中文關鍵字降級
            if any(kw in title_lower for kw in ['伊朗', '川普', '中美', 'Fed', '升息', '降息']):
                if not it.get('companies'):
                    it['signal'] = 'low'


        # Medium 品質過濾：無公司代碼且無具體數字 → 降為 pending
        if it.get('signal') == 'medium' and not it.get('companies'):
            fact = it.get('fact', '')
            if fact in ['無具體數字', '', '有具體數字']:
                it['signal'] = 'pending'

    return clustered

# ============================================================
# 主程式
# ============================================================

def main():
    hour = get_hour_arg()
    ts = f"{datetime.now().strftime('%Y%m%d')}-{hour}"

    print("=" * 55)
    print(f"新聞彙總 Layer 2  {datetime.now().strftime('%Y-%m-%d %H:%M')} ({hour}:00)")
    print("=" * 55)

    # 檢查 raw 檔案
    missing = check_raw_files(hour)
    if missing:
        msg = f"⚠️ 缺少：{', '.join(missing)}"
        print(msg); send_telegram(msg); sys.exit(1)

    # Step 1: 讀取 raw
    print(f"\n[1] 讀取 raw 檔案...")
    all_items = load_raw_files(hour)
    print(f"  總計：{len(all_items)} 筆")

    # Step 2: 第一階段去重
    print(f"\n[2] 第一階段去重（title 前25字）...")
    unique = dedup_title(all_items)
    print(f"  去重後：{len(unique)} 筆")

    # Step 2.5: 垃圾標題過濾
    print(f"\n[2.5] 垃圾標題過濾...")
    unique = garbage_filter(unique)
    
    # Step 3: 指紋比對
    print(f"\n[3] 指紋比對...")
    fp = load_fingerprints()
    new_items = []
    repeated = []
    for it in unique:
        key = it.get('title', '')[:25].lower().strip()
        if key and key in fp:
            repeated.append(it)
        else:
            new_items.append(it)
    print(f"  新聞：{len(new_items)} 筆（指紋已存在：{len(repeated)} 筆）")

    # Step 5: 評分預篩選
    print(f"\n[4] 評分預篩選（取 top {MAX_LLM_ITEMS}）...")
    scored = [(score_item(it), it) for it in new_items]
    scored.sort(key=lambda x: -x[0])
    top_items = [it for _, it in scored[:MAX_LLM_ITEMS]]
    rest_items = [it for _, it in scored[MAX_LLM_ITEMS:]]
    print(f"  LLM 分析：{len(top_items)} 筆，其餘 {len(rest_items)} 筆用規則分類")

    # Step 4.5: Fact 三層預審（P-011）
    print(f"\n[4.5] Fact 三層預審...")
    top_items, layer3_skipped = fact_layer_filter(top_items)
    rest_items = rest_items + layer3_skipped  # Layer 3 改用規則分類


    # Step 6: LLM 批次分析
    print(f"\n[5] LLM 批次分析（{len(top_items)}則，每批{BATCH_SIZE}則）...")
    llm_results = []
    for i in range(0, len(top_items), BATCH_SIZE):
        batch = top_items[i:i + BATCH_SIZE]
        bn = i // BATCH_SIZE + 1
        total = (len(top_items) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  批次 {bn}/{total}...", end='', flush=True)

        result = call_llm_batch(batch)

        if result and isinstance(result, list):
            print(f" ✅ ({len(result)}則)")
            for j, lr in enumerate(result):
                if lr is None or j >= len(batch):
                    continue
                item = batch[j]
                llm_results.append({
                        "title": item.get('title', ''),
                        "url": item.get('url', ''),
                        "source": item.get('source', ''),
                        "_sources": item.get('_sources', [item.get('source', '')]),
                        "published_at": item.get('published_at', ''),
                        "fact": (lr.get('fact', '') or '無具體數字')[:60],
                        "impact": (lr.get('impact', '') or ''),
                        "signal": lr.get('signal', 'low'),
                        "companies": [c for c in (lr.get('companies', []) or []) if re.match(r'^\d{4}$', str(c))],
                        "cluster_id": lr.get('cluster_id', ''),
                        "body_snippet": item.get('body_snippet', ''),
                        "paywall": item.get('paywall', False),
                    })
        else:
            print(f" ❌ LLM失敗，改用規則分類")
            for item in batch:
                llm_results.append(rule_classify(item))

        if i + BATCH_SIZE < len(top_items):
            time.sleep(4)

    # Step 7: 規則分類
    print(f"\n[6] 規則分類剩餘 {len(rest_items)} 筆...")
    for item in rest_items:
        llm_results.append(rule_classify(item))

    # Step 7: 加入重複項目（標記為 skip）
    for item in repeated:
        llm_results.append({
            "title": item.get('title', ''),
            "url": item.get('url', ''),
            "source": item.get('source', ''),
            "_sources": item.get('_sources', [item.get('source', '')]),
            "published_at": item.get('published_at', ''),
            "fact": "（7天內已出現，不重複推播）",
            "impact": "",
            "signal": "skip",
            "companies": [],
            "cluster_id": "",
            "body_snippet": "",
            "paywall": item.get('paywall', False),
        })

    # Step 8: 第二階段：cluster 彙總
    print(f"\n[7] 第二階段：cluster 彙總...")
    clustered = cluster_items(llm_results)
    print(f"  彙總後：{len(clustered)} 筆")

    # Step 9: 套用嚴格信號規則
    print(f"\n[8] 信號等級應用（來源驗證）...")
    final = apply_signal_rules(clustered)

    # Step 10: 更新指紋
    print(f"\n[9] 更新指紋資料庫...")
    now_str = datetime.now().strftime("%Y-%m-%d")
    for it in final:
        if it.get('signal') == 'skip':
            continue
        key = it.get('title', '')[:25].lower().strip()
        if key and it.get('signal') != 'skip':
            fp[key] = now_str

    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    to_del = [k for k, v in fp.items() if v < cutoff]
    for k in to_del: del fp[k]
    print(f"  新增 {len(clustered)} 筆，刪除 {len(to_del)} 筆過期，現有 {len(fp)} 筆")
    save_fingerprints(fp)

    # Step 11: 寫入 processed
    out_file = MEMORY_DIR / f"processed-{ts}.jsonl"
    result_obj = {
        "timestamp": ts,
        "total_items": len(all_items),
        "unique_items": len(unique),
        "processed_items": len(final),
        "items": final
    }
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(json.dumps(result_obj, ensure_ascii=False) + '\n')

    # 統計
    high = [i for i in final if i.get('signal') == 'high']
    medium = [i for i in final if i.get('signal') == 'medium']
    pending = [i for i in final if i.get('signal') == 'pending']
    low = [i for i in final if i.get('signal') == 'low']
    skip = [i for i in final if i.get('signal') == 'skip']

    print(f"\n  ✅ 高：{len(high)}，中：{len(medium)}，待驗證：{len(pending)}，低：{len(low)}，跳過：{len(skip)}")
    print(f"\n完成！結果寫入 {out_file.name}")

if __name__ == "__main__":
    main()