# Workflow Dependency Graph

Generated from: `workflows/` at `/home/ubuntu/.openclaw/workspace/workflows/`

---

## Dependency Tree (local imports only)

```
_common.py          ← base: sys, json, pathlib.Path, datetime, timezone, timedelta
_scan_utils.py      ← from _common: NOTION_KEY, NOTION_LEGACY_DB, TELEGRAM_TOKEN, TELEGRAM_DM
  └── scan_industry.py, scan_institutional.py, scan_news.py, scan_quarterly.py, scan_revenue.py, daily-scan.py

broker_digest.py    ← stdlib only (json, sys, time, urllib, datetime, pathlib)
daily_dashboard.py  ← stdlib only (json, sys, argparse, urllib, pathlib, datetime)
memory_archive.py   ← stdlib only (argparse, re, shutil, sys, datetime, pathlib, json, urllib)
news_aggregator.py  ← from _common: FINMIND_TOKEN, MINIMAX_API_KEY, TELEGRAM_TOKEN, TELEGRAM_DM, SECRETS, NOTION_KEY, today_tw_str
news_fetcher.py     ← stdlib only (json, sys, time, re, requests, datetime, pathlib, concurrent.futures)
news_pipeline.py    ← stdlib only (subprocess, sys, datetime, timezone, timedelta)
news_publisher.py   ← from _common: NOTION_KEY, NOTION_NEWS_DB, TELEGRAM_TOKEN, TELEGRAM_DM, TELEGRAM_GROUP, today_tw_str, SECRETS
news_sender.py      ← from _common: TELEGRAM_TOKEN
show_manual.py      ← (no imports)
setup_notion_databases.py ← stdlib only (json, time, sys, argparse, urllib, pathlib, datetime)
weekly_health_check.py ← from _common: SECRETS, NOTION_KEY
daily-notion.py     ← from _common: now_tw, FINMIND_TOKEN, NOTION_KEY, SECRETS
```

---

## Imports by File

### _common.py
- `sys`, `json`, `pathlib.Path`, `datetime`, `timezone`, `timedelta`

### _scan_utils.py
- **Local:** `from _common import NOTION_KEY, NOTION_LEGACY_DB, TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `pathlib.Path`, `datetime`, `timedelta`, `time`, `json`, `requests`, `math`, `collections.defaultdict`

### broker_digest.py
- stdlib: `json`, `sys`, `time`, `urllib.request`, `urllib.parse`, `datetime`, `pathlib.Path`

### daily-notion.py
- **Local:** `from _common import now_tw, FINMIND_TOKEN, NOTION_KEY, SECRETS`
- stdlib: `json`, `time`, `urllib.request`, `urllib.parse`, `re`, `datetime`, `pathlib.Path`, `argparse`, `collections.defaultdict`

### daily-scan.py
- **Local:** `from _scan_utils import now_tw, load_json, save_json, STATE_DIR, BROWSER_HEADERS`; `from _common import TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `datetime`, `pathlib.Path`, `time`, `json`, `requests`

### daily_dashboard.py
- stdlib: `json`, `sys`, `argparse`, `urllib.request`, `urllib.error`, `pathlib.Path`, `datetime`, `timezone`, `timedelta`

### memory_archive.py
- stdlib: `argparse`, `re`, `shutil`, `sys`, `datetime`, `pathlib.Path`, `json`, `urllib.request`

### news_aggregator.py
- **Local:** `from _common import FINMIND_TOKEN, MINIMAX_API_KEY, TELEGRAM_TOKEN, TELEGRAM_DM, SECRETS, NOTION_KEY, today_tw_str`
- stdlib: `json`, `sys`, `time`, `re`, `requests`, `datetime`, `pathlib.Path`

### news_fetcher.py
- stdlib: `json`, `sys`, `time`, `re`, `requests`, `datetime`, `pathlib.Path`, `concurrent.futures`

### news_pipeline.py
- stdlib: `subprocess`, `sys`, `datetime`, `timezone`, `timedelta`

### news_publisher.py
- **Local:** `from _common import NOTION_KEY, NOTION_NEWS_DB, TELEGRAM_TOKEN, TELEGRAM_DM, TELEGRAM_GROUP, today_tw_str, SECRETS`
- stdlib: `json`, `sys`, `re`, `time`, `requests`, `datetime`, `pathlib.Path`

### news_sender.py
- **Local:** `from _common import TELEGRAM_TOKEN`
- stdlib: `json`, `re`, `time`, `datetime`, `pathlib.Path`, `requests`

### scan_industry.py
- **Local:** `from _scan_utils import ...`; `from _common import FINMIND_TOKEN, TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `datetime`, `pathlib.Path`, `collections.defaultdict`, `time`, `json`

### scan_institutional.py
- **Local:** `from _scan_utils import ...`; `from _common import FINMIND_TOKEN, TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `datetime`, `pathlib.Path`, `collections.defaultdict`, `time`, `json`

### scan_news.py
- **Local:** `from _scan_utils import ...`; `from _common import FINMIND_TOKEN, TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `datetime`, `pathlib.Path`, `collections.defaultdict`, `time`, `json`

### scan_quarterly.py
- **Local:** `from _scan_utils import ...`; `from _common import FINMIND_TOKEN, TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `datetime`, `pathlib.Path`, `collections.defaultdict`, `time`, `json`

### scan_revenue.py
- **Local:** `from _scan_utils import ...`; `from _common import FINMIND_TOKEN, TELEGRAM_TOKEN, TELEGRAM_DM`
- stdlib: `datetime`, `pathlib.Path`, `collections.defaultdict`, `time`, `json`

### setup_notion_databases.py
- stdlib: `json`, `time`, `sys`, `argparse`, `urllib.request`, `urllib.error`, `pathlib.Path`, `datetime`, `timezone`

### show_manual.py
- (no imports)

### weekly_health_check.py
- **Local:** `from _common import SECRETS, NOTION_KEY`
- stdlib: `json`, `os`, `sys`, `subprocess`, `datetime`, `pathlib.Path`, `urllib.request`, `urllib.error`

---

## External Dependencies (3rd-party)

| Library | Used by |
|---------|---------|
| `requests` | `_scan_utils.py`, `daily-scan.py`, `news_aggregator.py`, `news_fetcher.py`, `news_publisher.py`, `news_sender.py`, `scan_*.py` |
| `urllib` (stdlib but used like 3rd-party) | `broker_digest.py`, `daily-dashboard.py`, `memory_archive.py`, `news_sender.py`, `setup_notion_databases.py`, `weekly_health_check.py` |
| `concurrent.futures` | `news_fetcher.py` |
| `subprocess` | `news_pipeline.py`, `weekly_health_check.py` |
| `shutil` | `memory_archive.py` |

---

## Local Dependency Summary

```
           ┌──────────────┐
           │  _common.py  │  (base config: SECRETS, NOTION_KEY, TELEGRAM_*, FINMIND_TOKEN, today_tw_str, now_tw)
           └──────┬───────┘
      ┌───────────┼──────────────────┐
      ▼           ▼                  ▼
┌───────────┐ ┌──────────────┐   (standalone)
│_scan_utils│ │  broker_digest │
│    .py    │ │daily_dashboard│
└─────┬─────┘ └──────────────┘   (standalone)
  ┌───┼──────────────────────────────┐
  ▼   ▼   ▼   ▼   ▼   ▼             ▼
 scan scan scan scan daily        news
 _ind _ins _new _qtr _rev  daily- notion
             _utils    scan   sender publisher aggregator
                                  │        │
                               (others with no local deps)
```
