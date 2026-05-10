#!/usr/bin/env python3
"""
Layer 1: Fetcher - 抓取各來源新聞，寫入 jsonl
支援兩種模式：
  1. 直接 RSS/HTML fetch（無 agent）
  2. 由 sessions_spawn 的 sub-agent 呼叫

Usage:
  python3 news_fetcher.py tw      # 抓台灣新聞
  python3 news_fetcher.py mops    # 抓 MOPS
  python3 news_fetcher.py intl    # 抓國際新聞
  python3 news_fetcher.py industry # 抓 DigiTimes
  python3 news_fetcher.py all     # 全部抓取（供 Orchestrator 用）
"""

import json, sys, time, re, requests
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

MEMORY_DIR = Path(__file__).parent.parent / "memory"
MEMORY_DIR.mkdir(exist_ok=True)

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
}

def get_hour_arg():
    # sys.argv[1] = mode (tw/mops/intl/industry/all)
    # sys.argv[2] = hour (optional, defaults to current hour)
    if len(sys.argv) >= 3:
        return sys.argv[2].zfill(2)
    return datetime.now().strftime("%H")

def is_recent(pub_text, max_age_hours=15):
    """檢查是否在 N 小時內"""
    if not pub_text or not pub_text.strip():
        return True
    clean = re.sub(r'\s*\(.*\)', '', pub_text)
    clean = re.sub(r'\s*GMT[+-]\d{4}', '', clean).strip()
    parts = clean.split()
    if len(parts) >= 5:
        clean = ' '.join(parts[1:])
    formats = ["%d %b %Y %H:%M:%S %z","%d %b %Y %H:%M:%S",
               "%Y-%m-%dT%H:%M:%S%z","%Y-%m-%dT%H:%M:%S","%Y-%m-%d %H:%M:%S","%Y-%m-%d"]
    for fmt in formats:
        try:
            dt_obj = datetime.strptime(clean, fmt)
            age = datetime.now() - dt_obj
            return age.total_seconds() < max_age_hours * 3600
        except: continue
    return True

def fetch_rss(url, source_name, max_items=20):
    """從 RSS URL 抓取"""
    items = []
    errors = []
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        
        root = None
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(resp.text)
        except:
            return [], "XML parse error"
        
        for item in root.findall('.//item')[:max_items]:
            title_el = item.find('title')
            link_el = item.find('link')
            pub_el = item.find('pubDate')
            desc_el = item.find('description')
            
            title = re.sub(r'<!\[CDATA\[|\]\]>', '', title_el.text).strip() if title_el is not None and title_el.text else ''
            link = link_el.text or '' if link_el is not None else ''
            pub = pub_el.text.strip() if pub_el is not None else ''
            desc = re.sub(r'<[^>]+>', '', desc_el.text or '').strip()[:800] if desc_el is not None and desc_el.text else ''
            
            if title and is_recent(pub):
                items.append({
                    "title": title,
                    "url": link,
                    "published_at": pub,
                    "source": source_name,
                    "body_snippet": desc,
                    "paywall": False
                })
        
        return items, None
    except Exception as e:
        return [], str(e)

def fetch_google_news(query, source_name, max_items=30):
    """從 Google News RSS 抓取"""
    items = []
    encoded = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=zh-TW&gl=TW&ceid=TW:zh-TW"
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        count = 0
        for item in root.findall('.//item'):
            if count >= max_items:
                break
            title_el = item.find('title')
            link_el = item.find('link')
            pub_el = item.find('pubDate')
            desc_el = item.find('description')
            
            title = re.sub(r'<!\[CDATA\[|\]\]>', '', title_el.text).strip() if title_el is not None and title_el.text else ''
            link = link_el.text or '' if link_el is not None else ''
            pub = pub_el.text.strip() if pub_el is not None else ''
            desc = re.sub(r'<[^>]+>', '', desc_el.text or '').strip()[:800] if desc_el is not None and desc_el.text else ''
            
            if title and is_recent(pub):
                items.append({
                    "title": title,
                    "url": link,
                    "published_at": pub,
                    "source": source_name,
                    "body_snippet": desc,
                    "paywall": False
                })
                count += 1
        
        return items, None
    except Exception as e:
        return [], str(e)

def fetch_mops():
    """抓取 MOPS 重大訊息"""
    items = []
    try:
        resp = requests.post(
            'https://mops.twse.com.tw/mops/web/ajax_t51sb12',
            data={
                'encodeURIComponent': '1',
                'step': '1',
                'firstin': '1',
                'off': '1',
                'keyword4': '',
                'code1': '',
                'TYPEK': 'pub',
                'checkbtn': '',
                'queryName': 'co_id',
                'inpuType': 'co_id',
                'pmem': '*',
                'co_id': '',
                'date1': '',
                'date2': datetime.now().strftime('%Y%m%d'),
            },
            headers={**BROWSER_HEADERS, 'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=20
        )
        # Parse HTML for company announcements
        # Look for table rows with company code and title
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.DOTALL | re.IGNORECASE)
        for row in rows[:30]:
            # Extract company code (4 digits)
            codes = re.findall(r'\b([0-9]{4})\b', row)
            # Extract announcement title
            titles = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if codes and titles:
                title_text = re.sub(r'<[^>]+>', '', titles[-1]).strip()[:100] if titles else ''
                if title_text and len(title_text) > 10:
                    items.append({
                        "title": title_text,
                        "url": f"https://mops.twse.com.tw/mops/web/t05st10_ifrs?co_id={codes[0]}",
                        "published_at": datetime.now().strftime('%Y-%m-%d'),
                        "source": "MOPS",
                        "body_snippet": title_text,
                        "paywall": False
                    })
        return items, None
    except Exception as e:
        return [], str(e)

def fetch_source(source_type, hour):
    """根據類型抓取"""
    all_items = []
    errors = []
    
    if source_type == "tw":
        print(f"  [TW-News] 抓取中...")
        # Google News for major TW sources
        sources = [
            ("site:ctee.com.tw", "工商時報"),
            ("site:cnyes.com", "鉅亨網"),
            ("site.moneydj.com", "MoneyDJ"),
            ("site:udn.com", "UDN"),
            ("site:ltn.com.tw", "LTN"),
        ]
        for query, name in sources:
            print(f"    - {name}...", end='', flush=True)
            items, err = fetch_google_news(query, "Google新聞", max_items=30)
            if err:
                print(f" 失敗({err[:30]})")
                errors.append(f"{name}: {err}")
            else:
                print(f" 成功({len(items)}則)")
                all_items.extend(items)
            time.sleep(0.3)
    
    elif source_type == "mops":
        print(f"  [MOPS] 抓取中...")
        items, err = fetch_mops()
        if err:
            errors.append(f"MOPS: {err}")
            print(f"    失敗: {err[:50]}")
        else:
            print(f"    成功({len(items)}則)")
            all_items.extend(items)
    
    elif source_type == "intl":
        print(f"  [Intl-News] 抓取中...")
        sources = [
            ("https://www.cnbc.com/id/10000664/device/rss/rss.html", "CNBC"),
            ("https://feeds.bloomberg.com/markets/news.rss", "Bloomberg"),
            ("https://techcrunch.com/feed/", "TechCrunch"),
            ("https://www.investing.com/rss/news.rss", "Investing"),
            ("https://asia.nikkei.com/rss/feed/nar", "Nikkei"),
        ]
        for url, name in sources:
            print(f"    - {name}...", end='', flush=True)
            items, err = fetch_rss(url, name, max_items=20)
            if err:
                print(f" 失敗({err[:30]})")
                errors.append(f"{name}: {err}")
            else:
                print(f" 成功({len(items)}則)")
                all_items.extend(items)
            time.sleep(0.3)
        
        # Reuters: 用 Google News 搜尋替代（RSS 已停用）
        print(f"    - Reuters...", end='', flush=True)
        items, err = fetch_google_news("site:reuters.com finance", "Reuters", max_items=20)
        if err:
            print(f" 失敗({err[:30]})")
            errors.append(f"Reuters: {err}")
        else:
            print(f" 成功({len(items)}則)")
            all_items.extend(items)
    
    elif source_type == "industry":
        print(f"  [DigiTimes] 抓取中...")
        url = "https://www.digitimes.com/rss/daily.xml"
        items, err = fetch_rss(url, "DigiTimes", max_items=25)
        if err:
            errors.append(f"DigiTimes: {err}")
            print(f"    失敗: {err[:50]}")
        else:
            print(f"    成功({len(items)}則)")
            all_items.extend(items)
    
    return all_items, errors

def dedup_by_title(items):
    """title 前 30 字去重"""
    seen = set()
    unique = []
    for it in items:
        key = it.get('title', '')[:30].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(it)
    return unique

def write_jsonl(items, output_file):
    """寫入 jsonl"""
    if not items:
        output_file.write_text('', encoding='utf-8')
        return 0
    with open(output_file, 'w', encoding='utf-8') as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + '\n')
    return len(items)

def main():
    mode = sys.argv[1] if len(sys.argv) >= 2 else "all"
    hour = get_hour_arg()
    today = datetime.now().strftime("%Y%m%d")
    timestamp = f"{today}-{hour}"
    
    print("=" * 55)
    print(f"新聞抓取 Layer 1  {datetime.now().strftime('%Y-%m-%d %H:%M')} ({mode})")
    print("=" * 55)
    
    all_errors = []
    sources_to_fetch = []
    
    if mode == "all":
        sources_to_fetch = ["tw", "mops", "intl", "industry"]
    elif mode in ["tw", "mops", "intl", "industry"]:
        sources_to_fetch = [mode]
    else:
        print(f"未知模式：{mode}")
        sys.exit(1)
    
    for src in sources_to_fetch:
        output_file = MEMORY_DIR / f"raw-{src}-{timestamp}.jsonl"
        
        items, errors = fetch_source(src, hour)
        all_errors.extend(errors)
        
        unique = dedup_by_title(items)
        count = write_jsonl(unique, output_file)
        
        if count > 0:
            print(f"  → 寫入 {count} 筆到 {output_file.name}")
        else:
            print(f"  → 無資料，寫入空檔案")
    
    # 錯誤日誌
    if all_errors:
        error_file = MEMORY_DIR / f"fetch-errors-{today}.log"
        with open(error_file, 'a', encoding='utf-8') as f:
            f.write(f"\n--- {datetime.now().isoformat()} ({mode}) ---\n")
            for err in all_errors:
                f.write(f"{err}\n")
        print(f"\n⚠️ {len(all_errors)} 個錯誤 → {error_file.name}")
    
    # Summary
    print(f"\n完成！")
    total_articles = 0
    for src in sources_to_fetch:
        f = MEMORY_DIR / f"raw-{src}-{timestamp}.jsonl"
        if f.exists():
            count = sum(1 for _ in open(f, encoding='utf-8'))
            total_articles += count
            print(f"  {src}: {count} 筆")
    
    return 0 if (not all_errors or total_articles >= 50) else 1

if __name__ == "__main__":
    sys.exit(main())