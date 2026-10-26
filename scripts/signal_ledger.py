#!/usr/bin/env python3
"""signal_ledger.py (B1, 2026-10-25) - signal look-back ledger: do the signals the system pushes beat 0050?

Every signal is registered at the close of its registration day; 7/30/60 calendar days later the
excess return versus 0050 is filled in. One row per signal in the Notion DB, one report page a month.
Spec: Notion page 404226f5a398814b8ae3daf408490c72 (B1 spec v0.1). Zero LLM.

Subcommands (no double-dash flags on purpose: Telegram turns -- into a dash):
  check                read-only: secrets, Notion access, news files, sqlite, crontab
  dry                  read-only: build candidate signals from the start date and print counts
  status               read-only: ledger counts, matured averages, missing-price rate, last runs
  backfill CODE START  first fill from START (YYYY-MM-DD); passcode; long, run it in the background
  run                  nightly: register new signals, fill 7/30/60-day excess, sync Notion
  summary [YYYY-MM]    monthly report page + Telegram (default: previous month); does a run first
  install CODE         add the crontab lines and append them to cron/crontab.txt (passcode)
  commit CODE          git add + commit (+ push when nothing else is unpushed) the two files

The last output line is always RESULT=...
Telegram only on crash, source or Notion failure, missing prices above 5%, backfill start/end
and the monthly report.
"""
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.1"
HOME = Path.home()
WS = HOME / ".openclaw" / "workspace"
SECRETS_FILE = WS / "config" / "secrets.json"
STATE_DIR = WS / "state"
DB_FILE = STATE_DIR / "signal_ledger.sqlite"
LOCK_FILE = STATE_DIR / "signal_ledger.lock"
NAMES_FILE = STATE_DIR / "tw_stock_names.json"
MEMORY_DIR = WS / "memory"
LOG_DIR = HOME / ".openclaw" / "logs"
RUN_LOG = LOG_DIR / "signal_ledger.log"
BACKFILL_LOG = LOG_DIR / "signal_ledger_backfill.log"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TPE = timezone(timedelta(hours=8))
PASS_SHA256 = "0ec2a5ed96ba258166575ec84f7d1f674cabd75ef5fe58ae9b4685307ab8ae0c"
PY = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable

TOPN_DB = "bea3f040-7da1-4b6b-8acc-63f0b8e4f453"
BROKER_DB_DEFAULT = "34e226f5-a398-81a0-b22d-fea135a192fd"
LEDGER_DB = "ceabd79e-e978-4b24-9b58-7ca31216d685"
SPEC_PAGE = "404226f5-a398-814b-8ae3-daf408490c72"

BENCH = "0050"
HORIZONS = (7, 30, 60)
DEDUP_DAYS = 14            # same stock, same type: a repeat within 14 days of the last counted one is dropped
S2_MIN_BROKERS = 3
S2_WINDOW_TD = 5           # trading days, inclusive
S4_PREV_MAX_DAYS = 365
S4_JUMP_MAX = 3.0          # a target-price raise above 3x vs the same broker's previous one is a data error
RUN_WINDOW_DAYS = 45       # run mode recomputes candidates this far back; older signals are frozen
NEWS_SPLIT = date(2026, 10, 14)
DEFAULT_START = "2026-05-25"
MISSING_ALERT = 0.05
RUN_MAX_CALLS = 300
BACKFILL_MAX_CALLS = 2000
BACKFILL_INTERVAL = 10.0   # seconds between FinMind calls in backfill (leaves quota for other jobs)
STALE_DAYS = 20            # a stock close older than this before the target day counts as missing
JUMP_MAX = 0.105           # a one-day move beyond the 10% price limit = ex-rights, split or capital reduction
CREATED_GAP_MAX = 90       # broker rows whose report date is >90 days before creation are excluded

CRON_TAG_RUN = "scripts/signal_ledger.py run"
CRON_TAG_SUM = "scripts/signal_ledger.py summary"
CRON_COMMENT = "# B1 signal_ledger: weekdays 23:50 Taipei run, day 1 08:20 Taipei monthly summary"
CRON_RUN = ("50 15 * * 1-5 cd " + str(WS) + " && " + PY
            + " scripts/signal_ledger.py run >> " + str(RUN_LOG) + " 2>&1")
CRON_SUM = ("20 0 1 * * cd " + str(WS) + " && " + PY
            + " scripts/signal_ledger.py summary >> " + str(RUN_LOG) + " 2>&1")

TYPES = ("S1", "S2", "S3", "S4")
TYPE_LABEL = {"S1": "S1 Top N", "S2": "S2 \u591a\u5bb6\u5171\u8b58", "S3": "S3 \u65b0\u805e\U0001f534", "S4": "S4 \u4e0a\u4fee\u5347\u8a55"}
SEG_PRE = "\u65b0\u805e10-14\u524d"
SEG_POST = "\u65b0\u805e10-14\u5f8c"
ST_TRACK = "\u8ffd\u8e64\u4e2d"
ST_DONE = "\u5b8c\u6210"
ST_MISS = "\u7f3a\u50f9"
ST_EXCL = "\u6392\u9664"
ST_REVOKED = "\u64a4\u92b7"

BUY = {"\u8cb7\u9032", "\u5f37\u529b\u8cb7\u9032", "\u589e\u52a0\u6301\u80a1", "\u9022\u4f4e\u8cb7\u9032"}
NEUTRAL_OR_BELOW = {"\u4e2d\u7acb", "\u5340\u9593\u64cd\u4f5c", "\u6e1b\u78bc", "\u8ce3\u51fa"}
MEDIA = {"\u5de5\u5546\u6642\u5831", "\u7d93\u6fdf\u65e5\u5831", "CMoney", "\u5b9a\u9328\u7522\u696d\u7b46\u8a18", "\u842c\u5bf6\u9031\u520a", "\u5546\u696d\u5468\u520a", "MIC", "\u8ca1\u7d93M\u5e73\u65b9",
         "\u5148\u63a2\u6295\u8cc7\u9031\u520a", "\u5916\u96fb\u7d9c\u5408"}
QUOTE_MARKERS = ("\u8f49\u5f15", "\u8f49\u8f09", "\u6574\u7406\u81ea", "ETF\u884c\u92b7", "ETF \u884c\u92b7")

SECRETS = {}
SECRET_VALUES = []
_MASK_RES = [re.compile(p) for p in (
    r"secret_[A-Za-z0-9]{20,}",
    r"ntn_[A-Za-z0-9]{20,}",
    r"\b\d{8,11}:[A-Za-z0-9_-]{30,}",
    r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{5,}",
    r"\bsk-[A-Za-z0-9_-]{16,}",
    r"Bearer\s+[A-Za-z0-9._-]{16,}",
    r"token=[A-Za-z0-9._-]{16,}",
)]
CODE_FULL = re.compile(r"[0-9]{4,6}[A-Z]?")
CODE_FIND = re.compile(r"(?<![0-9])([0-9]{4,6})(?![0-9])")
NEWS_RE = re.compile(r"processed-([0-9]{8})-([0-9]{2})\.jsonl$")


# ---------------------------------------------------------------- basics
class SourceError(Exception):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def today_tpe():
    forced = os.environ.get("SIGNAL_LEDGER_TODAY", "")
    if forced:
        return date.fromisoformat(forced)
    return datetime.now(TPE).date()


def d2s(d):
    return d.isoformat()


def s2d(s):
    return date.fromisoformat(str(s)[:10])


def load_secrets():
    global SECRETS, SECRET_VALUES
    SECRETS = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    vals = []
    for k, v in SECRETS.items():
        lk = k.lower()
        if any(w in lk for w in ("key", "token", "secret", "password")):
            if isinstance(v, str) and len(v) >= 12:
                vals.append(v)
    SECRET_VALUES = sorted(set(vals), key=len, reverse=True)


def mask(s):
    s = str(s)
    for v in SECRET_VALUES:
        s = s.replace(v, "***")
    for r in _MASK_RES:
        s = r.sub("***", s)
    return s


def sh(args, timeout=30, stdin_text=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=stdin_text)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "not found: " + args[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def http_json(method, url, headers, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return e.code, payload


def notion(method, path, body=None):
    hdr = {"Authorization": "Bearer " + SECRETS.get("notion_key", ""),
           "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}
    code, js = 0, {"message": "not sent"}
    for attempt in range(4):
        try:
            code, js = http_json(method, NOTION_API + path, hdr, body)
        except Exception as e:  # timeout / URLError
            code, js = 0, {"message": "%s: %s" % (type(e).__name__, e)}
            if attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            return code, js
        if code == 429 and attempt < 3:
            time.sleep(20)
            continue
        if code in (500, 502, 503, 504) and attempt < 3:
            time.sleep(10 * (attempt + 1))
            continue
        return code, js
    return code, js


def telegram(text):
    try:
        url = "https://api.telegram.org/bot" + SECRETS["telegram_bot_token"] + "/sendMessage"
        code, _ = http_json("POST", url, {"Content-Type": "application/json"},
                            {"chat_id": SECRETS["telegram_dm"], "text": mask(text)[:3800],
                             "disable_web_page_preview": True}, timeout=20)
        return code == 200
    except Exception:
        return False


def passcode_ok(code):
    return hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest() == PASS_SHA256


def fmt_pct(x, digits=1):
    if x is None:
        return "\u2014"
    return ("%+." + str(digits) + "f%%") % (x * 100.0)


def fmt_px(x):
    if x is None:
        return "\u2014"
    return ("%.2f" % x).rstrip("0").rstrip(".")


class Tee:
    """Mirror stdout into a log file (the \u6295\u8cc7\u99ac background runner may drop stdout)."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")
        self.out = sys.stdout

    def write(self, s):
        self.out.write(s)
        try:
            self.f.write(s)
            self.f.flush()
        except Exception:
            pass

    def flush(self):
        self.out.flush()


# ---------------------------------------------------------------- broker name / rating rules
# Copied from skills/broker-materials/scripts/receive_telegram.py (rt_m3, 2026-10-22) so rows written
# before 10-22 are normalised the same way. Unknown names are kept as-is (no WARN print here).
_S2T = str.maketrans("\u8bc1\u4e1c\u5434\u56fd\u5e7f\u53d1\u534e\u8054\u4e07\u5f00\u4e30\u987e\u95ee\u8d44\u4ea7\u4e1a\u7535\u8fbe\u9645\u6c47\u5174\u957f\u8d22\u7ecf\u95fb\u89c2\u70b9\u62a5\u95e8\u94f6\u6cfd\u8363\u5b81\u51ef\u7edf\u8fdc\u4f18\u98ce\u946b",
                     "\u8b49\u6771\u5433\u570b\u5ee3\u767c\u83ef\u806f\u842c\u958b\u8c50\u9867\u554f\u8cc7\u7522\u696d\u96fb\u9054\u969b\u532f\u8208\u9577\u8ca1\u7d93\u805e\u89c0\u9ede\u5831\u9580\u9280\u6fa4\u69ae\u5be7\u51f1\u7d71\u9060\u512a\u98a8\u946b")
_BROKER_JUNK = {"\u672a\u77e5", "\u672a\u660e\u78ba", "\u672a\u660e\u786e", "\u672a\u6a19\u793a", "\u672a\u6a19\u660e", "\u672a\u6a19\u793a\u5238\u5546\u540d", "\u672a\u660e\u78ba\u6a19\u793a", "\u4e0d\u8a73", "\u7121", "none",
                "null", "\u5176\u4ed6", "\u7814\u7a76\u90e8", "\u6295\u9867", "\u6295\u9867\u89c0\u9ede", "\u5238\u5546\u6668\u5831", "\u7f8e\u7cfb\u5238\u5546", "\u7f8e\u7cfb\u5927\u884c", "\u671f\u8ca8\u5546",
                "\u80a1\u5e02\u8207\u7b56\u7565\u7d44", "\u5e02\u8207\u7b56\u7565\u7d44", "\u6295\u9867\u80a1\u5e02\u8207\u7b56\u7565\u7d44", "\u6295\u9867 \u80a1\u5e02\u8207\u7b56\u7565\u7d44", "\u7d9c\u5408\u6574\u7406", "\u53f0\u80a1\u7814\u7a76\u5718\u968a",
                "n/a", "0", "\u5426", "research", "securities", "investment", "consulting", "brokerage", "news",
                "(hong", "kong)", "fortune", "grand", "\u672c\u4e2d\u5fc3", "\u5e02\u5834\u8cc7\u6599", "\u4e9e\u7cfb\u5238\u5546", "\u6fb3\u7cfb\u5238\u5546", "\u8cb7\u65b9\u7814\u7a76",
                "\u6295\u9867\u7814\u7a76\u90e8", "\u6295\u9867\u5831\u544a"}
_BROKER_KNOWN_RAW = {"\u5de5\u5546\u6642\u5831", "\u7d93\u6fdf\u65e5\u5831", "CMoney", "\u5b9a\u9328\u7522\u696d\u7b46\u8a18", "\u842c\u5bf6\u9031\u520a", "\u5546\u696d\u5468\u520a", "MIC", "Morningstar",
                     "\u8ca1\u7d93M\u5e73\u65b9", "\u5148\u63a2\u6295\u8cc7\u9031\u520a", "\u5916\u96fb\u7d9c\u5408", "Aletheia Capital"}
_BROKER_KEEP_RAW = ("\u570b\u6cf0\u6d77\u901a", "\u570b\u6cf0\u541b\u5b89", "\u6d77\u901a")
_BROKER_FOREIGN = [
    ("morgan stanley", "Morgan Stanley"), ("\u6469\u6839\u58eb\u4e39\u5229", "Morgan Stanley"), ("\u5927\u6469", "Morgan Stanley"),
    ("goldman", "Goldman Sachs"), ("\u9ad8\u76db", "Goldman Sachs"),
    ("j.p. morgan", "J.P. Morgan"), ("jp morgan", "J.P. Morgan"), ("jpmorgan", "J.P. Morgan"), ("\u6469\u6839\u5927\u901a", "J.P. Morgan"),
    ("daiwa", "Daiwa"), ("\u5927\u548c", "Daiwa"), ("nomura", "Nomura"), ("\u91ce\u6751", "Nomura"),
    ("bofa", "BofA Securities"), ("bank of america", "BofA Securities"), ("\u7f8e\u9280", "BofA Securities"),
    ("citi", "Citi"), ("\u82b1\u65d7", "Citi"), ("ubs", "UBS"), ("\u745e\u9280", "UBS"), ("hsbc", "HSBC"), ("\u6ed9\u8c50", "HSBC"),
    ("bernstein", "Bernstein"), ("macquarie", "Macquarie"), ("\u9ea5\u683c\u7406", "Macquarie"), ("clsa", "CLSA"),
    ("jefferies", "Jefferies"), ("barclays", "Barclays"), ("mizuho", "Mizuho"), ("deutsche", "Deutsche Bank"),
    ("gf securities", "\u5ee3\u767c\u8b49\u5238"), ("\u5ee3\u767c", "\u5ee3\u767c\u8b49\u5238"),
    ("kgi", "\u51f1\u57fa\u6295\u9867"), ("sinopac", "\u6c38\u8c50\u6295\u9867"), ("fubon research", "\u5bcc\u90a6\u6295\u9867"), ("e.sun", "\u7389\u5c71\u6295\u9867"),
    ("mega investment", "\u5146\u8c50\u6295\u9867"), ("grand fortune", "\u798f\u90a6\u6295\u9867"), ("capital.com.tw", "\u7fa4\u76ca\u6295\u9867"),
    ("cim capital", "\u7fa4\u76ca\u6295\u9867"), ("merrill", "BofA Securities"), ("yuanta", "\u5143\u5927\u6295\u9867"),
    ("president capital", "\u7d71\u4e00\u6295\u9867"),
]
_BROKER_FOREIGN_EXACT = {"gs": "Goldman Sachs", "cim": "\u7fa4\u76ca\u6295\u9867", "capital": "\u7fa4\u76ca\u6295\u9867", "gf": "\u5ee3\u767c\u8b49\u5238",
                         "fubon": "\u5bcc\u90a6\u6295\u9867", "cathay": "\u570b\u6cf0\u671f\u8ca8"}
_BROKER_TW = [
    ("\u7b2c\u4e00\u91d1", "\u7b2c\u4e00\u91d1", "\u6295\u9867"), ("\u5408\u4f5c\u91d1\u5eab", "\u5408\u5eab", "\u6295\u9867"), ("\u5408\u5eab", "\u5408\u5eab", "\u6295\u9867"),
    ("\u4e2d\u570b\u4fe1\u8a17", "\u4e2d\u4fe1", "\u6295\u9867"), ("\u4e2d\u4fe1", "\u4e2d\u4fe1", "\u6295\u9867"), ("\u83ef\u5357\u6c38\u660c", "\u83ef\u5357", "\u6295\u9867"), ("\u83ef\u5357", "\u83ef\u5357", "\u6295\u9867"),
    ("\u7fa4\u76ca\u91d1\u9f0e", "\u7fa4\u76ca", "\u6295\u9867"), ("\u7fa4\u76ca", "\u7fa4\u76ca", "\u6295\u9867"), ("\u5146\u8c50", "\u5146\u8c50", "\u6295\u9867"), ("\u6c38\u8c50", "\u6c38\u8c50", "\u6295\u9867"),
    ("\u5143\u5927", "\u5143\u5927", "\u6295\u9867"), ("\u5143\u5bcc", "\u5143\u5bcc", "\u6295\u9867"), ("\u51f1\u57fa", "\u51f1\u57fa", "\u6295\u9867"), ("\u5bcc\u90a6", "\u5bcc\u90a6", "\u6295\u9867"),
    ("\u798f\u90a6", "\u798f\u90a6", "\u6295\u9867"), ("\u570b\u7968", "\u570b\u7968", "\u6295\u9867"), ("\u570b\u6cf0", "\u570b\u6cf0", "\u671f\u8ca8"), ("\u7d71\u4e00", "\u7d71\u4e00", "\u6295\u9867"),
    ("\u7389\u5c71", "\u7389\u5c71", "\u6295\u9867"), ("\u738b\u5c71", "\u7389\u5c71", "\u6295\u9867"), ("\u53f0\u65b0", "\u53f0\u65b0", "\u6295\u9867"), ("\u5eb7\u548c", "\u5eb7\u548c", "\u6295\u9867"),
    ("\u5b8f\u9060", "\u5b8f\u9060", "\u6295\u9867"), ("\u7f8e\u597d", "\u7f8e\u597d", "\u6295\u9867"), ("\u5fb7\u4fe1", "\u5fb7\u4fe1", "\u6295\u9867"), ("\u65e5\u76db", "\u65e5\u76db", "\u6295\u9867"),
]
_BROKER_NOT_RESEARCH = ("\u9280\u884c", "\u91d1\u63a7", "\u91d1\u878d\u96c6\u5718")
_BROKER_SUFFIXES = ("\u80a1\u4efd\u6709\u9650\u516c\u53f8", "\u6709\u9650\u516c\u53f8", "\u7814\u7a76\u6240", "\u7814\u7a76\u90e8", "\u7814\u7a76\u8655")


def canon_broker(name):
    s = str(name or "").strip().translate(_S2T)
    if not s or s.lower() in _BROKER_JUNK or s in _BROKER_JUNK or "\u91d1\u7ba1\u6295\u9867\u65b0\u5b57" in s:
        return "\u672a\u660e\u78ba"
    if s.startswith("\u672a") and any(w in s for w in ("\u660e\u78ba", "\u77e5", "\u63d0\u4f9b", "\u63ed\u9732", "\u6a19", "\u8a73")):
        return "\u672a\u660e\u78ba"
    if s in _BROKER_KNOWN_RAW:
        return s
    low = s.lower()
    if low in _BROKER_FOREIGN_EXACT:
        return _BROKER_FOREIGN_EXACT[low]
    for key, canon in _BROKER_FOREIGN:
        if key in low:
            return canon
    if not any(w in s for w in _BROKER_NOT_RESEARCH) and not any(w in s for w in _BROKER_KEEP_RAW):
        for alias, brand, default_type in _BROKER_TW:
            if alias in s:
                if "\u6295\u4fe1" in s:
                    return brand + "\u6295\u4fe1"
                if any(w in s for w in ("\u671f\u8ca8", "\u8b49\u671f", "\u671f\u7814\u7a76", "\u671f\u9867\u554f")):
                    return brand + "\u671f\u8ca8"
                if any(w in s for w in ("\u6295\u9867", "\u6295\u8cc7\u9867\u554f", "\u8b49\u5238", "\u7814\u7a76", "\u9867\u554f")):
                    return brand + "\u6295\u9867"
                return brand + default_type
    out = s
    for suf in _BROKER_SUFFIXES:
        if out.endswith(suf) and len(out) > len(suf) + 1:
            out = out[: -len(suf)]
    return out.strip() or "\u672a\u660e\u78ba"


_RATING_KEYS = [
    ("\u5f37\u529b\u8cb7\u9032", "\u5f37\u529b\u8cb7\u9032"), ("\u5f37\u70c8\u8cb7\u9032", "\u5f37\u529b\u8cb7\u9032"), ("\u5f37\u529b\u8cb7\u5165", "\u5f37\u529b\u8cb7\u9032"), ("strong buy", "\u5f37\u529b\u8cb7\u9032"),
    ("conviction buy", "\u5f37\u529b\u8cb7\u9032"),
    ("\u9022\u4f4e\u8cb7\u9032", "\u9022\u4f4e\u8cb7\u9032"), ("\u9022\u4f4e\u5e03\u5c40", "\u9022\u4f4e\u8cb7\u9032"),
    ("\u5340\u9593\u64cd\u4f5c", "\u5340\u9593\u64cd\u4f5c"), ("trading buy", "\u5340\u9593\u64cd\u4f5c"), ("\u5340\u9593", "\u5340\u9593\u64cd\u4f5c"),
    ("\u589e\u52a0\u6301\u80a1", "\u589e\u52a0\u6301\u80a1"), ("\u52a0\u78bc", "\u589e\u52a0\u6301\u80a1"), ("\u589e\u6301", "\u589e\u52a0\u6301\u80a1"), ("overweight", "\u589e\u52a0\u6301\u80a1"),
    ("\u8cb7\u9032", "\u8cb7\u9032"), ("\u8cb7\u5165", "\u8cb7\u9032"), ("\u63a8\u85a6", "\u8cb7\u9032"), ("\u512a\u65bc\u5927\u5e02", "\u8cb7\u9032"), ("\u512a\u65bc\u5927\u76e4", "\u8cb7\u9032"),
    ("outperform", "\u8cb7\u9032"), ("\u9996\u9078", "\u8cb7\u9032"), ("\u770b\u597d", "\u8cb7\u9032"), ("\u6b63\u5411", "\u8cb7\u9032"), ("buy", "\u8cb7\u9032"),
    ("\u5340\u9593\u6301\u6709", "\u4e2d\u7acb"), ("\u6301\u6709", "\u4e2d\u7acb"), ("\u4e2d\u7acb", "\u4e2d\u7acb"), ("\u4e2d\u6027", "\u4e2d\u7acb"), ("neutral", "\u4e2d\u7acb"),
    ("equal-weight", "\u4e2d\u7acb"), ("equal weight", "\u4e2d\u7acb"), ("market perform", "\u4e2d\u7acb"), ("hold", "\u4e2d\u7acb"),
    ("\u6e1b\u78bc", "\u6e1b\u78bc"), ("\u964d\u4f4e\u6301\u80a1", "\u6e1b\u78bc"), ("underweight", "\u6e1b\u78bc"),
    ("\u8ce3\u51fa", "\u8ce3\u51fa"), ("underperform", "\u8ce3\u51fa"), ("sell", "\u8ce3\u51fa"),
    ("\u672a\u660e\u78ba", "\u672a\u660e\u78ba"), ("\u672a\u8a55\u7b49", "\u672a\u660e\u78ba"), ("\u672a\u5c0d", "\u672a\u660e\u78ba"), ("not rated", "\u672a\u660e\u78ba"),
    ("not covered", "\u672a\u660e\u78ba"),
]
_RATING_ASCII_SHORT = {"ow": "\u589e\u52a0\u6301\u80a1", "uw": "\u6e1b\u78bc", "nr": "\u672a\u660e\u78ba", "o/f": "\u8cb7\u9032"}


def canon_rating(rating):
    raw = str(rating or "").strip()
    s = (raw.translate(_S2T).replace("\u4e70", "\u8cb7").replace("\u8fdb", "\u9032").replace("\u5f3a", "\u5f37")
         .replace("\u8350", "\u85a6").replace("\u4e8e", "\u65bc").replace("\u786e", "\u78ba"))
    if not s:
        return "\u672a\u660e\u78ba"
    low = s.lower()
    if low in _RATING_ASCII_SHORT:
        return _RATING_ASCII_SHORT[low]
    best = None
    for key, canon in _RATING_KEYS:
        if key.isascii():
            m = re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", low)
            pos = m.start() if m else -1
        else:
            pos = low.find(key)
        if pos < 0:
            continue
        cand = (pos, -len(key), canon)
        if best is None or cand < best:
            best = cand
    if best is None:
        m = re.match(r"^(ow|uw|nr)(?![a-z])", low)
        if m:
            return _RATING_ASCII_SHORT[m.group(1)]
        return "\u672a\u660e\u78ba"
    return best[2]


# ---------------------------------------------------------------- sqlite
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS prices (code TEXT, d TEXT, close REAL, spread REAL, PRIMARY KEY (code, d));
CREATE TABLE IF NOT EXISTS coverage (code TEXT PRIMARY KEY, from_d TEXT, to_d TEXT, last_row TEXT,
    fetches INTEGER DEFAULT 0, last_fetch TEXT);
CREATE TABLE IF NOT EXISTS signals (
    sid TEXT PRIMARY KEY, stype TEXT, code TEXT, name TEXT, reg_date TEXT, seg TEXT,
    source TEXT, source_url TEXT, detail TEXT, first_seen TEXT, status TEXT, note TEXT,
    t0 TEXT, p0 REAL, b0 REAL, t0n TEXT, p0n REAL, b0n REAL,
    t7 TEXT, p7 REAL, b7 REAL, e7 REAL,
    t30 TEXT, p30 REAL, b30 REAL, e30 REAL,
    t60 TEXT, p60 REAL, b60 REAL, e60 REAL,
    r30n REAL, a30 REAL,
    notion_id TEXT, notion_hash TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS ix_sig_type_code ON signals (stype, code, reg_date);
CREATE TABLE IF NOT EXISTS runs (ts TEXT, cmd TEXT, result TEXT, stats TEXT);
"""
SIG_COLS = ("sid", "stype", "code", "name", "reg_date", "seg", "source", "source_url", "detail",
            "first_seen", "status", "note", "t0", "p0", "b0", "t0n", "p0n", "b0n",
            "t7", "p7", "b7", "e7", "t30", "p30", "b30", "e30", "t60", "p60", "b60", "e60",
            "r30n", "a30", "notion_id", "notion_hash", "updated")


def db_open(readonly=False):
    if readonly:
        if not DB_FILE.exists():
            return None
        con = sqlite3.connect("file:%s?mode=ro" % DB_FILE, uri=True)
    else:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(DB_FILE))
        con.executescript(SCHEMA)
    con.row_factory = sqlite3.Row
    return con


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def meta_set(con, k, v):
    con.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def all_signals(con):
    return [dict(r) for r in con.execute("SELECT * FROM signals ORDER BY reg_date, stype, code")]


# ---------------------------------------------------------------- prices and calendar
class Prices:
    """Price cache backed by sqlite (or memory for dry runs)."""

    def __init__(self, con=None):
        self.con = con
        self.rows = defaultdict(dict)      # code -> {date_str: (close, spread)}
        self.cov = {}                      # code -> dict(from_d, to_d, last_row)
        if con is not None:
            for r in con.execute("SELECT code, d, close, spread FROM prices"):
                self.rows[r["code"]][r["d"]] = (r["close"], r["spread"])
            for r in con.execute("SELECT * FROM coverage"):
                self.cov[r["code"]] = {"from_d": r["from_d"], "to_d": r["to_d"], "last_row": r["last_row"]}
        self._sorted = {}

    def add(self, code, start, end, rows, today):
        for d, c, sp in rows:
            self.rows[code][d] = (c, sp)
        self._sorted.pop(code, None)
        cv = self.cov.get(code)
        last = max([d for d, _, _ in rows], default=None)
        # today counts as fetched only when today's close is already there (FinMind may publish late)
        to_d = end if (end < today or d2s(end) in self.rows[code]) else end - timedelta(days=1)
        if to_d < start:
            to_d = start - timedelta(days=1)
        if cv is None:
            cv = {"from_d": d2s(start), "to_d": d2s(to_d), "last_row": last}
        else:
            cv["from_d"] = min(cv["from_d"], d2s(start))
            cv["to_d"] = max(cv["to_d"], d2s(to_d))
            if last and (cv["last_row"] is None or last > cv["last_row"]):
                cv["last_row"] = last
        self.cov[code] = cv
        if self.con is not None:
            self.con.executemany(
                "INSERT INTO prices (code, d, close, spread) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(code, d) DO UPDATE SET close=excluded.close, spread=excluded.spread",
                [(code, d, c, sp) for d, c, sp in rows])
            self.con.execute(
                "INSERT INTO coverage (code, from_d, to_d, last_row, fetches, last_fetch) VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(code) DO UPDATE SET from_d=excluded.from_d, to_d=excluded.to_d, "
                "last_row=excluded.last_row, fetches=coverage.fetches+1, last_fetch=excluded.last_fetch",
                (code, cv["from_d"], cv["to_d"], cv["last_row"], now_utc().isoformat(timespec="seconds")))

    def dates(self, code):
        if code not in self._sorted:
            self._sorted[code] = sorted(self.rows.get(code, {}))
        return self._sorted[code]

    def close_state(self, code, day):
        """(close, actual_date, state) with state ok / pending / missing for the last close <= day."""
        ds = self.dates(code)
        target = d2s(day)
        lo = d2s(day - timedelta(days=STALE_DAYS))
        best = None
        # binary search for the last date <= target
        a, b = 0, len(ds)
        while a < b:
            m = (a + b) // 2
            if ds[m] <= target:
                a = m + 1
            else:
                b = m
        cv = self.cov.get(code)
        if cv is None or cv["to_d"] < target:
            return None, None, "pending"
        if a > 0 and ds[a - 1] >= lo:
            best = ds[a - 1]
        if best is not None:
            return self.rows[code][best][0], best, "ok"
        if cv["from_d"] <= target:
            return None, None, "missing"
        return None, None, "pending"

    def max_jump(self, code, t_from, t_to):
        """(change, date) of the largest one-day close-to-close move on days in (t_from, t_to].

        TWSE prints the change on ex-rights days as "X0.00" (no comparison), so FinMind's spread is 0 there and
        cannot be used to adjust; raw closes beyond the 10% limit are the reliable tell of a corporate action."""
        f, t = d2s(t_from), d2s(t_to)
        worst, wd, prev = 0.0, None, None
        for d in self.dates(code):
            if d > t:
                break
            c = self.rows[code][d][0]
            if d > f and prev:
                ch = c / prev - 1.0
                if abs(ch) > abs(worst):
                    worst, wd = ch, d
            prev = c
        return worst, wd


def weekdays_between(a, b):
    """Number of weekdays in (a, b]."""
    if b <= a:
        return 0
    n = (b - a).days
    full, rem = divmod(n, 7)
    cnt = full * 5
    for i in range(1, rem + 1):
        if (a + timedelta(days=full * 7 + i)).weekday() < 5:
            cnt += 1
    return cnt


class Calendar:
    def __init__(self, prices):
        self.days = [s2d(d) for d in prices.dates(BENCH)]
        cv = prices.cov.get(BENCH)
        self.known = s2d(cv["to_d"]) if (cv and self.days) else None
        self._idx = {d: i for i, d in enumerate(self.days)}

    def on_or_before(self, d):
        a, b = 0, len(self.days)
        while a < b:
            m = (a + b) // 2
            if self.days[m] <= d:
                a = m + 1
            else:
                b = m
        return self.days[a - 1] if a > 0 else None

    def first_after(self, d):
        for x in self.days:
            if x > d:
                return x
        return None

    def index(self, d):
        """Trading-day index of the last trading day <= d; weekdays extend the calendar past its end."""
        if not self.days:
            return weekdays_between(date(2000, 1, 2), d)
        if d <= self.days[-1]:
            t = self.on_or_before(d)
            return self._idx[t] if t is not None else -1
        return len(self.days) - 1 + weekdays_between(self.days[-1], d)


_FM = None


def finmind():
    global _FM
    if _FM is None:
        p = str(WS / "workflows")
        if p not in sys.path:
            sys.path.insert(0, p)
        import _finmind as fm  # noqa: E402
        _FM = fm
    return _FM


def fetch_prices(code, start, end):
    js = finmind().fetch_one("TaiwanStockPrice", data_id=code, start_date=d2s(start), end_date=d2s(end))
    if not isinstance(js, dict):
        raise RuntimeError("FinMind: non-dict response")
    st = js.get("status")
    if st not in (None, 200) and str(js.get("msg", "")).lower() != "success":
        raise RuntimeError("FinMind status %s %s" % (st, str(js.get("msg", ""))[:80]))
    rows = []
    for r in js.get("data") or []:
        try:
            d = str(r["date"])[:10]
            c = float(r["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if c <= 0:
            continue
        sp = r.get("spread")
        try:
            sp = float(sp) if sp not in (None, "") else None
        except (TypeError, ValueError):
            sp = None
        rows.append((d, c, sp))
    return rows


class Fetcher:
    def __init__(self, prices, max_calls, today, interval=None):
        self.prices = prices
        self.today = today
        self.max_calls = max_calls
        self.calls = 0
        self.errors = []
        self.stopped = ""
        if interval:
            try:
                finmind().MIN_INTERVAL = max(finmind().MIN_INTERVAL, interval)
            except Exception:
                pass

    def get(self, code, start, end):
        if start > end:
            return True
        if self.stopped:
            return False
        if self.calls >= self.max_calls:
            self.stopped = "budget"
            return False
        self.calls += 1
        try:
            rows = fetch_prices(code, start, end)
        except Exception as e:
            name = type(e).__name__
            self.errors.append("%s %s: %s" % (code, name, mask(str(e))[:120]))
            if "Quota" in name or "402" in str(e):
                self.stopped = "quota"
            if len(self.errors) >= 25:
                self.stopped = "errors"
            return False
        self.prices.add(code, start, end, rows, self.today)
        return True

    def ensure(self, code, need_from, need_to):
        cv = self.prices.cov.get(code)
        if cv is None:
            return self.get(code, need_from, need_to)
        ok = True
        if s2d(cv["from_d"]) > need_from:
            ok = self.get(code, need_from, s2d(cv["from_d"]) - timedelta(days=1)) and ok
        if s2d(cv["to_d"]) < need_to:
            ok = self.get(code, s2d(cv["to_d"]) + timedelta(days=1), need_to) and ok
        return ok


# ---------------------------------------------------------------- Notion readers
def p_text(props, name):
    p = props.get(name) or {}
    t = p.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in (p.get(t) or []))
    if t == "select":
        return ((p.get("select") or {}).get("name")) or ""
    if t == "date":
        return ((p.get("date") or {}).get("start")) or ""
    if t == "number":
        v = p.get("number")
        return "" if v is None else str(v)
    return ""


def p_num(props, name):
    p = props.get(name) or {}
    if p.get("type") == "number":
        v = p.get("number")
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return None


def notion_query_all(db_id, body=None, max_pages=400):
    out, cursor = [], None
    for _ in range(max_pages):
        b = dict(body or {})
        b["page_size"] = 100
        if cursor:
            b["start_cursor"] = cursor
        code, js = notion("POST", "/databases/%s/query" % db_id, b)
        if code != 200:
            raise SourceError("query %s HTTP %s %s" % (db_id[:8], code, mask(str(js.get("message", "")))[:150]))
        out.extend(js.get("results", []))
        if not js.get("has_more"):
            return out
        cursor = js.get("next_cursor")
        time.sleep(0.34)
    raise SourceError("query %s: more than %d pages" % (db_id[:8], max_pages))


def load_names():
    try:
        d = json.loads(NAMES_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- candidates
def cand(stype, code, name, d, source, url, detail=None, seg=""):
    return {"stype": stype, "code": code, "name": name or "", "reg_date": d, "source": source,
            "source_url": url or "", "detail": detail or {}, "seg": seg}


def gen_s1(start, end, stats):
    rows = notion_query_all(TOPN_DB)
    out = []
    for pg in rows:
        props = pg.get("properties") or {}
        ds = p_text(props, "\u65e5\u671f").strip()[:10]
        try:
            d = s2d(ds)
        except ValueError:
            stats["s1_bad_date"] += 1
            continue
        if d < start or d > end:
            continue
        txt = p_text(props, "\u7d9c\u5408\u6b63\u9762 Top15")
        rank = 0
        for seg in txt.split(","):
            seg = seg.strip()
            if "/" not in seg:
                continue
            name, code = seg.rsplit("/", 1)
            code = code.strip()
            if not CODE_FULL.fullmatch(code):
                stats["s1_bad_code"] += 1
                continue
            rank += 1
            out.append(cand("S1", code, name.strip(), d, "Top N %s \u7b2c%d\u540d" % (ds, rank), pg.get("url"),
                            {"rank": rank}))
    stats["s1_rows"] = len(rows)
    return out


def news_files():
    files = list(MEMORY_DIR.glob("processed-*.jsonl"))
    arch = MEMORY_DIR / "archive"
    if arch.exists():
        files += list(arch.rglob("processed-*.jsonl"))
    return files


def news_tpe_date(fname):
    m = NEWS_RE.search(fname)
    if not m:
        return None
    d = datetime.strptime(m.group(1), "%Y%m%d").date()
    hh = m.group(2)
    if hh == "07":          # UTC 23:00 cron run of the previous UTC day = Taipei 07:00
        return d + timedelta(days=1)
    if hh == "19":          # UTC 11:00 run = Taipei 19:00 same day
        return d
    h = int(hh)
    if h > 23:
        return None
    return datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc).astimezone(TPE).date()


def gen_s3(start, end, stats):
    out = []
    seen_files = set()
    nfiles = 0
    for f in sorted(news_files(), key=lambda p: p.name):
        if f.name in seen_files:      # same file in memory/ and archive/: read once
            continue
        d = news_tpe_date(f.name)
        if d is None or d < start or d > end:
            continue
        seen_files.add(f.name)
        try:
            with open(f, encoding="utf-8") as fh:
                line = fh.readline().strip()
            data = json.loads(line) if line else {}
        except Exception:
            stats["s3_bad_file"] += 1
            continue
        nfiles += 1
        items = [it for it in (data.get("items") or []) if isinstance(it, dict) and it.get("signal") != "skip"]
        high, keys = [], set()
        for it in items:
            if it.get("signal") != "high":
                continue
            k = str(it.get("title", ""))[:40]
            if k in keys:
                continue
            keys.add(k)
            high.append(it)
        seg = SEG_POST if d >= NEWS_SPLIT else SEG_PRE
        for it in high[:8]:                       # the brief shows the first 8 high items
            codes = []
            for c in it.get("companies") or []:
                c = str(c).strip()
                if CODE_FULL.fullmatch(c) and c not in codes:
                    codes.append(c)
            names = it.get("company_names") if isinstance(it.get("company_names"), dict) else {}
            title = str(it.get("title", ""))[:80]
            tag = str(it.get("tag", "") or "")
            label = "%s %s" % ({"07": "\u6668\u5831", "19": "\u665a\u5831"}.get(f.name[19:21], f.name[19:21] + "\u6642"),
                                d.strftime("%m/%d"))
            if tag:
                label += " " + tag
            for c in codes[:3]:                   # the brief header shows the first 3 companies
                out.append(cand("S3", c, names.get(c, ""), d, "%s\uff5c%s" % (label, title), it.get("url"),
                                {"file": f.name, "tag": tag}, seg))
    stats["s3_files"] = nfiles
    return out


def load_broker_rows(start, stats):
    broker_db = SECRETS.get("notion_broker_reports_db") or BROKER_DB_DEFAULT
    since = d2s(start - timedelta(days=S4_PREV_MAX_DAYS + 35))
    rows = notion_query_all(broker_db, {
        "filter": {"property": "\u5831\u544a\u65e5\u671f", "date": {"on_or_after": since}},
        "sorts": [{"property": "\u5831\u544a\u65e5\u671f", "direction": "ascending"}]})
    recs = []
    for pg in rows:
        props = pg.get("properties") or {}
        ds = p_text(props, "\u5831\u544a\u65e5\u671f")[:10]
        try:
            d = s2d(ds)
        except ValueError:
            stats["anom_no_date"] += 1
            continue
        try:
            created = datetime.fromisoformat(str(pg.get("created_time", "")).replace("Z", "+00:00")).astimezone(TPE).date()
        except ValueError:
            created = d
        codes = sorted(set(CODE_FIND.findall(p_text(props, "\u80a1\u7968\u4ee3\u78bc"))))
        if len(codes) != 1:
            stats["anom_code"] += 1
            continue
        if d > created:
            stats["anom_date_after_created"] += 1
            continue
        if (created - d).days > CREATED_GAP_MAX:
            stats["anom_date_gap90"] += 1
            continue
        broker = canon_broker(p_text(props, "\u5238\u5546\u540d\u7a31"))
        if broker in ("\u672a\u660e\u78ba", "\u5176\u4ed6") or broker in MEDIA:
            stats["anom_broker"] += 1
            continue
        view = p_text(props, "\u6838\u5fc3\u89c0\u9ede")
        if any(m in view for m in QUOTE_MARKERS):
            stats["anom_quote"] += 1
            continue
        kind = p_text(props, "\u5831\u544a\u578b\u614b")
        rating = canon_rating(p_text(props, "\u8a55\u7b49"))
        tp = p_num(props, "\u76ee\u6a19\u50f9")
        px = p_num(props, "\u5831\u544a\u7576\u65e5\u80a1\u50f9")
        if not kind:
            kind = "\u77ed\u8a55/\u5feb\u8a0a" if (not tp and not px and rating == "\u672a\u660e\u78ba") else "\u5b8c\u6574\u5831\u544a"
        if kind != "\u5b8c\u6574\u5831\u544a":
            stats["skip_short"] += 1
            continue
        tp = tp if (tp and tp > 0) else None
        if tp and px and px > 0 and not (0.3 <= tp / px <= 3.0):
            stats["anom_tp_px"] += 1          # target price and report-day price disagree: unit error
            tp = None
        recs.append({"code": codes[0], "name": p_text(props, "\u516c\u53f8\u540d\u7a31").strip(), "broker": broker,
                     "rating": rating, "date": d, "created": str(pg.get("created_time", "")),
                     "tp": tp, "url": pg.get("url") or "", "pid": pg.get("id")})
    # same broker, same stock, same day: keep the last created row
    keep = {}
    for r in recs:
        k = (r["broker"], r["code"], r["date"])
        if k in keep:
            stats["anom_same_day_dup"] += 1
            if r["created"] >= keep[k]["created"]:
                keep[k] = r
        else:
            keep[k] = r
    out = sorted(keep.values(), key=lambda r: (r["date"], r["created"]))
    stats["broker_rows"] = len(rows)
    stats["broker_used"] = len(out)
    return out


def gen_s2(recs, cal, start, end):
    by_code = defaultdict(list)
    for r in recs:
        if r["rating"] in BUY:
            by_code[r["code"]].append(r)
    out = []
    for code, lst in by_code.items():
        lst.sort(key=lambda r: (r["date"], r["created"]))
        for i, r in enumerate(lst):
            if r["date"] < start or r["date"] > end:
                continue
            ti = cal.index(r["date"])
            win = {}
            for q in lst[: i + 1]:
                if ti - cal.index(q["date"]) <= S2_WINDOW_TD - 1:
                    win[q["broker"]] = q
            if len(win) >= S2_MIN_BROKERS:
                qs = sorted(win.values(), key=lambda q: q["date"])
                src = "\u3001".join("%s %s" % (q["broker"], q["rating"]) for q in qs)
                out.append(cand("S2", code, r["name"], r["date"],
                                "%d \u5bb6\u8cb7\u9032\u985e\uff08%s\uff5e%s\uff09\uff1a%s" % (len(qs), qs[0]["date"].strftime("%m/%d"),
                                                          qs[-1]["date"].strftime("%m/%d"), src),
                                r["url"], {"brokers": [q["broker"] for q in qs]}))
    return out


def gen_s4(recs, start, end, stats):
    groups = defaultdict(list)
    for r in recs:
        if r["tp"]:                       # spec: S4 excludes short notes and rows without a target price
            groups[(r["broker"], r["code"])].append(r)
    out = []
    lo, hi = 1.0 / S4_JUMP_MAX, S4_JUMP_MAX
    for (broker, code), lst in groups.items():
        lst.sort(key=lambda r: (r["date"], r["created"]))
        anchor = None         # last accepted row: the reference for the next one
        rejected = None       # last row rejected as a target-price jump
        last_rated = None     # last accepted row with a known rating
        for r in lst:
            in_win = start <= r["date"] <= end
            if anchor is None:
                anchor = r
                last_rated = r if r["rating"] != "\u672a\u660e\u78ba" else None
                continue
            ratio = r["tp"] / anchor["tp"]
            if ratio > hi or ratio < lo:
                if rejected is not None and lo <= r["tp"] / rejected["tp"] <= hi:
                    anchor, rejected = r, None        # two rows agree: the old anchor was the bad one
                    last_rated = r if r["rating"] != "\u672a\u660e\u78ba" else last_rated
                else:
                    rejected = r
                    if in_win:
                        stats["anom_tp_jump"] += 1
                continue
            rejected = None
            reasons, detail = [], {"broker": broker}
            if in_win and 0 < (r["date"] - anchor["date"]).days <= S4_PREV_MAX_DAYS and ratio > 1.0001:
                reasons.append("\u76ee\u6a19\u50f9 %s\u2192%s\uff08\u524d\u6b21 %s\uff09" % (fmt_px(anchor["tp"]), fmt_px(r["tp"]),
                                                      anchor["date"].strftime("%m/%d")))
                detail.update({"tp_new": r["tp"], "tp_prev": anchor["tp"]})
            if (in_win and r["rating"] in BUY and last_rated is not None
                    and last_rated["rating"] in NEUTRAL_OR_BELOW
                    and 0 < (r["date"] - last_rated["date"]).days <= S4_PREV_MAX_DAYS):
                reasons.append("\u8a55\u7b49 %s\u2192%s" % (last_rated["rating"], r["rating"]))
                detail["rating_up"] = True
            if reasons:
                out.append(cand("S4", code, r["name"], r["date"], "%s %s" % (broker, "\uff1b".join(reasons)),
                                r["url"], detail))
            anchor = r
            if r["rating"] != "\u672a\u660e\u78ba":
                last_rated = r
    return out


def merge_same_day(cands):
    """One candidate per (type, code, day); sources are joined."""
    out = {}
    for c in cands:
        k = (c["stype"], c["code"], c["reg_date"])
        if k not in out:
            out[k] = dict(c)
            out[k]["detail"] = dict(c["detail"])
            continue
        o = out[k]
        if c["stype"] == "S2":       # same day, wider window: keep the one with the most brokers, not a list of all
            if len(c["detail"].get("brokers", [])) > len(o["detail"].get("brokers", [])):
                out[k] = dict(c)
                out[k]["detail"] = dict(c["detail"])
            continue
        if c["source"] and c["source"] not in o["source"]:
            o["source"] = (o["source"] + "\uff5c" + c["source"])[:1500]
        if not o["name"] and c["name"]:
            o["name"] = c["name"]
        for dk, dv in c["detail"].items():
            if dk not in o["detail"]:
                o["detail"][dk] = dv
            elif dk == "tp_new" and dv and dv > (o["detail"].get("tp_new") or 0):
                o["detail"]["tp_new"] = dv
                o["detail"]["tp_prev"] = c["detail"].get("tp_prev")
    return list(out.values())


def dedup(cands, seeds):
    """seeds: (type, code) -> last counted date before the window. Returns (kept, dropped_by_type)."""
    last = dict(seeds)
    kept, dropped = [], Counter()
    for c in sorted(cands, key=lambda c: (c["reg_date"], c["stype"], c["code"])):
        k = (c["stype"], c["code"])
        if k in last and (c["reg_date"] - last[k]).days < DEDUP_DAYS:
            dropped[c["stype"]] += 1
            continue
        last[k] = c["reg_date"]
        kept.append(c)
    return kept, dropped


def build_candidates(start, end, cal, stats):
    """Returns (candidates, ok_types, errors)."""
    cands, ok, errs = [], set(), []
    try:
        cands += gen_s1(start, end, stats)
        ok.add("S1")
    except Exception as e:
        errs.append("S1 %s: %s" % (type(e).__name__, mask(str(e))[:160]))
    try:
        recs = load_broker_rows(start, stats)
        cands += gen_s2(recs, cal, start, end)
        cands += gen_s4(recs, start, end, stats)
        ok.update(("S2", "S4"))
    except Exception as e:
        errs.append("S2/S4 %s: %s" % (type(e).__name__, mask(str(e))[:160]))
    try:
        s3 = gen_s3(start, end, stats)
        if stats.get("s3_files", 0) == 0 and (end - start).days >= 3:
            errs.append("S3: no news files between %s and %s" % (start, end))
        else:
            cands += s3
            ok.add("S3")
    except Exception as e:
        errs.append("S3 %s: %s" % (type(e).__name__, mask(str(e))[:160]))
    for c in cands:
        stats["raw_" + c["stype"]] += 1
    return merge_same_day(cands), ok, errs


# ---------------------------------------------------------------- evaluation
def evaluate(sig, cal, prices):
    """Fill prices/excess for one signal. Returns (changed_fields, needs) where needs = (code, from, to) or None."""
    if sig["status"] in (ST_DONE, ST_EXCL, ST_REVOKED):
        return {}, None
    upd = {}
    code = sig["code"]
    reg = s2d(sig["reg_date"])
    known = cal.known
    need_to = None
    missing = False
    if known is None or reg > known:
        return ({"status": ST_TRACK} if sig["status"] != ST_TRACK else {}), None
    t0 = cal.on_or_before(reg)
    if t0 is None:
        return {}, None
    b0 = prices.rows[BENCH].get(d2s(t0), (None,))[0]
    p0, _, st0 = prices.close_state(code, t0)
    if st0 == "pending":
        need_to = t0
    elif st0 == "missing":
        missing = True
    upd.update({"t0": d2s(t0), "b0": b0, "p0": p0})
    t0n = cal.first_after(reg)
    if t0n is not None and t0n <= known:
        p0n, _, stn = prices.close_state(code, t0n)
        if stn == "pending":
            need_to = max(need_to or t0n, t0n)
        upd.update({"t0n": d2s(t0n), "p0n": p0n, "b0n": prices.rows[BENCH].get(d2s(t0n), (None,))[0]})
    excl = ""
    note = []
    upd["a30"] = None          # v1.1: the spread-based dividend adjustment was wrong on ex-rights days; column retired
    det = json.loads(sig["detail"] or "{}")
    if p0 and det.get("tp_new") and not det.get("rating_up"):
        r_new = det["tp_new"] / p0
        r_prev = (det.get("tp_prev") or 0) / p0
        if not (0.5 <= r_new <= 3.0) or not (0.3 <= r_prev <= 3.0):
            excl = "\u76ee\u6a19\u50f9\u55ae\u4f4d\u7591\u8aa4\uff1a\u76ee\u6a19\u50f9\xf7\u767b\u8a18\u50f9 \u65b0 %.2f \u500d\u3001\u524d %.2f \u500d" % (r_new, r_prev)
    for h in HORIZONS:
        due = reg + timedelta(days=h)
        if due > known:
            continue
        th = cal.on_or_before(due)
        ph, _, sth = prices.close_state(code, th)
        bh = prices.rows[BENCH].get(d2s(th), (None,))[0]
        if sth == "pending":
            need_to = max(need_to or th, th)
        elif sth == "missing":
            missing = True
        e = None
        if ph and p0 and bh and b0:
            ratio = ph / p0
            if ratio < 0.25 or ratio > 4.0:
                excl = excl or "\u7591\u4f3c\u5206\u5272\u6216\u6e1b\u8cc7\uff1a\u80a1\u50f9\u6bd4 %.2f \u500d" % ratio
            e = (ph / p0 - 1.0) - (bh / b0 - 1.0)
        upd.update({"t%d" % h: d2s(th), "p%d" % h: ph, "b%d" % h: bh, "e%d" % h: e})
        if e is not None:
            jump, jd = prices.max_jump(code, t0, th)
            if abs(jump) > JUMP_MAX:
                excl = excl or "\u7591\u4f3c\u9664\u6b0a\u3001\u5206\u5272\u6216\u6e1b\u8cc7\uff1a%s \u55ae\u65e5 %+.0f%%\uff0c\u8d85\u904e\u6f32\u8dcc\u5e45\u9650\u5236" % (jd, jump * 100)
        if h == 30 and e is not None:
            p0n, b0n = upd.get("p0n"), upd.get("b0n")
            if p0n and b0n and upd.get("t0n") and upd["t0n"] <= d2s(th):
                upd["r30n"] = (ph / p0n - 1.0) - (bh / b0n - 1.0)
    if excl:
        upd["status"] = ST_EXCL
        note.append(excl)
    elif missing:
        upd["status"] = ST_MISS
    elif all(upd.get("e%d" % h) is not None for h in HORIZONS):
        upd["status"] = ST_DONE
    else:
        upd["status"] = ST_TRACK
    upd["note"] = "\uff1b".join(note)
    changed = {k: v for k, v in upd.items() if sig.get(k) != v}
    needs = (code, reg - timedelta(days=10), need_to) if need_to else None
    return changed, needs


# ---------------------------------------------------------------- Notion writer
def rt(text):
    text = str(text or "")
    return [{"type": "text", "text": {"content": text[:1900]}}] if text else []


def num(x, nd=4):
    return None if x is None else round(float(x), nd)


def detail_text(s):
    parts = []
    if s.get("t0"):
        parts.append("\u767b\u8a18 %s \u6536 %s\uff0f0050 %s" % (s["t0"], fmt_px(s.get("p0")), fmt_px(s.get("b0"))))
    for h in HORIZONS:
        if s.get("t%d" % h):
            parts.append("%d\u5929 %s \u6536 %s\uff0f0050 %s \u8d85\u984d %s" % (h, s["t%d" % h], fmt_px(s.get("p%d" % h)),
                                                      fmt_px(s.get("b%d" % h)), fmt_pct(s.get("e%d" % h), 2)))
    if s.get("t0n"):
        parts.append("\u9694\u65e5 %s \u6536 %s\uff0f0050 %s" % (s["t0n"], fmt_px(s.get("p0n")), fmt_px(s.get("b0n"))))
    if s.get("note"):
        parts.append(s["note"])
    return "\uff5c".join(parts)


def page_props(s):
    url = s.get("source_url") or None
    if url and (len(url) > 1900 or not url.startswith("http")):
        url = None
    name = s.get("name") or ""
    title = "%s(%s)\uff5c%s\uff5c%s" % (name, s["code"], s["stype"], s["reg_date"]) if name else \
        "%s\uff5c%s\uff5c%s" % (s["code"], s["stype"], s["reg_date"])
    return {
        "\u540d\u7a31": {"title": rt(title)},
        "\u985e\u578b": {"select": {"name": TYPE_LABEL[s["stype"]]}},
        "\u4ee3\u78bc": {"rich_text": rt(s["code"])},
        "\u516c\u53f8": {"rich_text": rt(name)},
        "\u767b\u8a18\u65e5": {"date": {"start": s["reg_date"]}},
        "\u767b\u8a18\u50f9": {"number": num(s.get("p0"), 2)},
        "7\u5929\u8d85\u984d": {"number": num(s.get("e7"))},
        "30\u5929\u8d85\u984d": {"number": num(s.get("e30"))},
        "60\u5929\u8d85\u984d": {"number": num(s.get("e60"))},
        "30\u5929\u9694\u65e5\u8d77\u7b97": {"number": num(s.get("r30n"))},
        "30\u5929\u9084\u539f\u6b0a\u606f": {"number": num(s.get("a30"))},
        "\u72c0\u614b": {"select": {"name": s["status"]}},
        "\u5206\u6bb5": {"select": {"name": s["seg"]} if s.get("seg") else None},
        "\u4f86\u6e90": {"rich_text": rt(s.get("source"))},
        "\u4f86\u6e90\u9023\u7d50": {"url": url},
        "\u660e\u7d30": {"rich_text": rt(detail_text(s))},
        "\u8a0a\u865fID": {"rich_text": rt(s["sid"])},
    }


def adopt_existing_pages(con):
    """Map ledger pages already in Notion (e.g. after the sqlite file was lost) to their signals."""
    try:
        pages = notion_query_all(LEDGER_DB)
    except SourceError as e:
        print("adopt: %s" % e)
        return 0
    n = 0
    for pg in pages:
        sid = p_text(pg.get("properties") or {}, "\u8a0a\u865fID").strip()
        if not sid or pg.get("archived"):
            continue
        cur = con.execute("UPDATE signals SET notion_id=? WHERE sid=? AND (notion_id IS NULL OR notion_id='')",
                          (pg["id"], sid))
        n += cur.rowcount
    con.commit()
    return n


def sync_notion(con, limit=None):
    created = updated = 0
    fails = []
    rows = [dict(r) for r in con.execute("SELECT * FROM signals ORDER BY reg_date, stype, code")]
    for s in rows:
        if s["status"] == ST_REVOKED and not s["notion_id"]:
            continue
        props = page_props(s)
        h = hashlib.md5(json.dumps(props, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        if s["notion_id"] and s["notion_hash"] == h:
            continue
        if limit is not None and created + updated >= limit:
            break
        if s["notion_id"]:
            code, js = notion("PATCH", "/pages/%s" % s["notion_id"], {"properties": props})
            if code == 200:
                updated += 1
            elif code == 404:
                con.execute("UPDATE signals SET notion_id=NULL, notion_hash=NULL WHERE sid=?", (s["sid"],))
                fails.append("%s 404 (page gone, recreate next run)" % s["sid"])
                continue
        else:
            code, js = notion("POST", "/pages", {"parent": {"database_id": LEDGER_DB}, "properties": props})
            if code == 200:
                created += 1
                con.execute("UPDATE signals SET notion_id=?, notion_hash=? WHERE sid=?", (js.get("id"), h, s["sid"]))
                con.commit()          # never lose a created page id (a rerun would duplicate it)
        if code == 200:
            con.execute("UPDATE signals SET notion_hash=? WHERE sid=?", (h, s["sid"]))
        else:
            fails.append("%s HTTP %s %s" % (s["sid"], code, mask(str(js.get("message", "")))[:100]))
        if (created + updated) % 25 == 0:
            con.commit()
        time.sleep(0.34)
    con.commit()
    return created, updated, fails


# ---------------------------------------------------------------- pipeline
def missing_rate(sigs, known):
    if known is None:
        return 0.0, 0, 0
    cutoff = known - timedelta(days=3)
    elig = [s for s in sigs if s["status"] not in (ST_REVOKED, ST_EXCL) and s2d(s["reg_date"]) <= cutoff]
    miss = [s for s in elig if s["status"] == ST_MISS]
    return (len(miss) / len(elig) if elig else 0.0), len(miss), len(elig)


def migrate_v11(con, stats):
    """One-time v1.1 fix: retire the 30-day dividend-adjusted column, reopen rows that its 15-point rule excluded
    and every finished row (re-checked with the one-day jump rule over all horizons), and recompute the whole
    window once so older S2 rows get the shorter source text. Returns True on that run."""
    if meta_get(con, "v11") == "done":
        return False
    con.execute("UPDATE signals SET a30=NULL")
    cur = con.execute("UPDATE signals SET status=?, note='' WHERE status=? AND note LIKE ?",
                      (ST_TRACK, ST_EXCL, "\u7591\u4f3c\u5206\u5272\u6216\u6e1b\u8cc7\uff1a\u9084\u539f\u8207\u672a\u9084\u539f\u5dee%"))
    n_excl = cur.rowcount
    # v1.0 only looked for corporate actions inside the 30-day window: re-check finished rows once
    cur = con.execute("UPDATE signals SET status=? WHERE status=?", (ST_TRACK, ST_DONE))
    stats["v11_reopened"] = n_excl + cur.rowcount
    meta_set(con, "v11", "done")
    con.commit()
    print("v1.1 migration: retired 30-day adjusted column, reopened %d excluded and %d finished rows"
          % (n_excl, cur.rowcount))
    return True


def pipeline(con, mode, start, today, max_calls, interval=None, notion_sync=True):
    """mode: backfill or run. Returns stats dict."""
    stats = Counter()
    full_once = migrate_v11(con, stats)
    prices = Prices(con)
    fetcher = Fetcher(prices, max_calls, today, interval)
    # 1) benchmark calendar
    bstart = start - timedelta(days=15)
    fetcher.ensure(BENCH, bstart, today)
    con.commit()
    cal = Calendar(prices)
    if cal.known is None or cal.known < start:
        raise SourceError("0050 prices unavailable: " + "; ".join(fetcher.errors[-3:]))
    # 2) candidates in the window
    win_start = start if (mode == "backfill" or full_once) else max(start, today - timedelta(days=RUN_WINDOW_DAYS))
    cands, ok_types, src_errs = build_candidates(win_start, today, cal, stats)
    existing = {s["sid"]: s for s in all_signals(con)}
    seeds = {}
    for s in existing.values():
        if s["status"] == ST_REVOKED or s2d(s["reg_date"]) >= win_start:
            continue
        k = (s["stype"], s["code"])
        d = s2d(s["reg_date"])
        if k not in seeds or d > seeds[k]:
            seeds[k] = d
    kept, dropped = dedup([c for c in cands if c["stype"] in ok_types], seeds)
    names = load_names()
    now_s = now_utc().isoformat(timespec="seconds")
    kept_ids = set()
    for c in kept:
        sid = "%s-%s-%s" % (c["stype"], c["code"], d2s(c["reg_date"]))
        kept_ids.add(sid)
        name = names.get(c["code"]) or c["name"]
        det = json.dumps(c["detail"], ensure_ascii=False, sort_keys=True)
        if sid in existing:
            e = existing[sid]
            fields = {"name": name, "source": c["source"], "source_url": c["source_url"], "detail": det,
                      "seg": c["seg"]}
            if e["status"] == ST_REVOKED:
                fields["status"] = ST_TRACK
            ch = {k: v for k, v in fields.items() if e.get(k) != v}
            if ch:
                con.execute("UPDATE signals SET %s, updated=? WHERE sid=?" % ", ".join("%s=?" % k for k in ch),
                            list(ch.values()) + [now_s, sid])
        else:
            con.execute("INSERT INTO signals (sid, stype, code, name, reg_date, seg, source, source_url, detail, "
                        "first_seen, status, note, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, c["stype"], c["code"], name, d2s(c["reg_date"]), c["seg"], c["source"],
                         c["source_url"], det, now_s, ST_TRACK, "", now_s))
            stats["new_" + c["stype"]] += 1
    present = set(f.name for f in news_files()) if "S3" in ok_types else set()
    for sid, e in existing.items():
        if e["stype"] == "S3" and json.loads(e["detail"] or "{}").get("file") not in present:
            continue                  # news file gone (cleanup), not a correction: keep the signal
        if (e["stype"] in ok_types and s2d(e["reg_date"]) >= win_start and sid not in kept_ids
                and e["status"] != ST_REVOKED):
            con.execute("UPDATE signals SET status=?, note=?, updated=? WHERE sid=?",
                        (ST_REVOKED, "\u4f86\u6e90\u8cc7\u6599\u5df2\u4e0d\u518d\u7522\u751f\u6b64\u8a0a\u865f", now_s, sid))
            stats["revoked"] += 1
    con.commit()
    for t in TYPES:
        stats["dedup_" + t] = dropped.get(t, 0)
    # 3) prices: evaluate, fetch what is pending, evaluate again
    sigs = all_signals(con)
    needs = {}
    for s in sigs:
        _, nd = evaluate(s, cal, prices)
        if nd:
            code, f, t = nd
            if code in needs:
                needs[code] = (min(needs[code][0], f), max(needs[code][1], t))
            else:
                needs[code] = (f, t)
    order = sorted(needs.items(), key=lambda kv: kv[1][1])     # oldest pending first
    for i, (code, (f, t)) in enumerate(order):
        fetcher.ensure(code, f, today)
        if (i + 1) % 20 == 0:
            con.commit()
            if mode == "backfill":
                print("  prices %d/%d codes, FinMind calls %d" % (i + 1, len(order), fetcher.calls))
        if fetcher.stopped:
            break
    con.commit()
    stats["fetch_calls"] = fetcher.calls
    still = set()
    for s in all_signals(con):
        ch, nd = evaluate(s, cal, prices)
        if nd:
            still.add(nd[0])
        if ch:
            ch["updated"] = now_s
            con.execute("UPDATE signals SET %s WHERE sid=?" % ", ".join("%s=?" % k for k in ch),
                        list(ch.values()) + [s["sid"]])
    con.commit()
    sigs = all_signals(con)
    rate, nmiss, nelig = missing_rate(sigs, cal.known)
    for s in sigs:
        stats["st_" + s["status"]] += 1
    # 4) Notion
    created = updated = 0
    fails = []
    if notion_sync:
        if mode == "backfill":
            adopted = adopt_existing_pages(con)
            if adopted:
                print("adopted %d existing Notion pages" % adopted)
        created, updated, fails = sync_notion(con)
    out = dict(stats)
    out.update({"known": d2s(cal.known), "window": d2s(win_start), "codes_waiting": len(still), "missing_rate": round(rate, 4),
                "missing": nmiss, "eligible": nelig, "notion_created": created, "notion_updated": updated,
                "notion_fail": len(fails), "fetch_errors": len(fetcher.errors), "fetch_stopped": fetcher.stopped,
                "source_errors": src_errs, "ok_types": sorted(ok_types)})
    out["_fails"] = fails[:5]
    out["_fetch_err"] = fetcher.errors[:5]
    return out


def counts_line(st):
    parts = []
    for t in TYPES:
        parts.append("%s \u65b0\u589e%d \u53bb\u91cd%d" % (t, st.get("new_" + t, 0), st.get("dedup_" + t, 0)))
    return "\uff0c".join(parts)


def record_run(con, cmd, result, st):
    clean = {k: v for k, v in st.items() if not k.startswith("_")}
    con.execute("INSERT INTO runs (ts, cmd, result, stats) VALUES (?, ?, ?, ?)",
                (now_utc().isoformat(timespec="seconds"), cmd, result, json.dumps(clean, ensure_ascii=False)))
    meta_set(con, "last_stats", json.dumps(clean, ensure_ascii=False))
    con.commit()


def alerts_after_run(con, st, label):
    """Telegram for source errors, Notion failures and missing prices above 5% (throttled)."""
    msgs = []
    if st.get("source_errors"):
        msgs.append("\u4f86\u6e90\u8b80\u53d6\u5931\u6557\uff1a" + "\uff1b".join(st["source_errors"])[:600])
    if st.get("notion_fail"):
        msgs.append("Notion \u5beb\u5165\u5931\u6557 %d \u7b46\uff08\u4e0b\u6b21 run \u6703\u91cd\u8a66\uff09\uff1a%s" % (st["notion_fail"], "\uff1b".join(st.get("_fails", []))[:400]))
    if st.get("fetch_stopped") in ("quota", "errors"):
        msgs.append("FinMind \u4e2d\u6b62\uff08%s\uff09\uff1a%s" % (st["fetch_stopped"], "\uff1b".join(st.get("_fetch_err", []))[:400]))
    rate = st.get("missing_rate", 0.0)
    if rate > MISSING_ALERT:
        last = json.loads(meta_get(con, "missing_alert", "{}") or "{}")
        last_ts = last.get("ts")
        recent = False
        if last_ts:
            recent = (now_utc() - datetime.fromisoformat(last_ts)).days < 7
        if not recent or rate > float(last.get("rate", 0)) + 0.01:
            msgs.append("\u7f3a\u50f9\u7387 %.1f%%\uff08%d\uff0f%d \u7b46\uff09\uff0c\u8d85\u904e 5%%" % (rate * 100, st.get("missing", 0), st.get("eligible", 0)))
            meta_set(con, "missing_alert", json.dumps({"ts": now_utc().isoformat(timespec="seconds"), "rate": rate}))
            con.commit()
    if msgs:
        telegram("\u26a0\ufe0f \u8a0a\u865f\u56de\u770b\u53f0\u5e33\uff08%s\uff09\n" % label + "\n".join("\u30fb" + m for m in msgs))
    return msgs


# ---------------------------------------------------------------- monthly report
def month_bounds(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    first = date(y, m, 1)
    nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return first, nxt - timedelta(days=1)


def row_key(s):
    if s["stype"] == "S3":
        return "S3b" if s.get("seg") == SEG_POST else "S3a"
    return s["stype"]


ROW_KEYS = ("S1", "S2", "S3a", "S3b", "S4")
ROW_LABEL = {"S1": "S1 Top N", "S2": "S2 \u591a\u5bb6\u5171\u8b58", "S3a": "S3 \u65b0\u805e\uff0810-14 \u524d\uff09", "S3b": "S3 \u65b0\u805e\uff0810-14 \u5f8c\uff09",
             "S4": "S4 \u4e0a\u4fee\u5347\u8a55"}


def avg(xs):
    return sum(xs) / len(xs) if xs else None


def month_stats(sigs, first, last, cumulative=False):
    res = {}
    for k in ROW_KEYS:
        mine = [s for s in sigs if row_key(s) == k and s["status"] != ST_REVOKED]
        reg = [s for s in mine if (cumulative or s2d(s["reg_date"]) >= first) and s2d(s["reg_date"]) <= last]
        usable = [s for s in mine if s["status"] != ST_EXCL]
        r = {"registered": len(reg)}
        for h in HORIZONS:
            vals = []
            for s in usable:
                if s.get("e%d" % h) is None:
                    continue
                due = s2d(s["reg_date"]) + timedelta(days=h)
                if due <= last and (cumulative or due >= first):
                    vals.append(s)
            r["n%d" % h] = len(vals)
            r["avg%d" % h] = avg([s["e%d" % h] for s in vals])
            if h == 30:
                r["win30"] = sum(1 for s in vals if s["e30"] > 0)
                r["ref30"] = avg([s["r30n"] for s in vals if s.get("r30n") is not None])
        res[k] = r
    return res


def cell_avg(r, h):
    n = r["n%d" % h]
    if n == 0:
        return "\u2014"
    return "%s\uff08%d\uff09" % (fmt_pct(r["avg%d" % h]), n)


def table_rows(stats, reg_label):
    head = ["\u985e", reg_label, "7 \u5929\u5e73\u5747\u8d85\u984d\uff08n\uff09", "30 \u5929\uff08n\uff09", "60 \u5929\uff08n\uff09", "30 \u5929\u8dd1\u8d0f", "30 \u5929\u9694\u65e5\u8d77\u7b97\uff08\u53c3\u8003\uff09"]
    rows = [head]
    for k in ROW_KEYS:
        r = stats[k]
        if r["n30"] < 3 and r["n7"] < 3:
            rows.append([ROW_LABEL[k], str(r["registered"]), "\u5019\u9078\u60f3\u6cd5\uff08\u6a23\u672c <3\uff09", "", "", "", ""])
            continue
        rows.append([ROW_LABEL[k], str(r["registered"]), cell_avg(r, 7), cell_avg(r, 30), cell_avg(r, 60),
                     ("%d\uff0f%d" % (r["win30"], r["n30"])) if r["n30"] else "\u2014", fmt_pct(r["ref30"])])
    return rows


def blk_table(rows):
    return {"object": "block", "type": "table", "table": {
        "table_width": len(rows[0]), "has_column_header": True, "has_row_header": False,
        "children": [{"object": "block", "type": "table_row",
                      "table_row": {"cells": [rt(c) for c in row]}} for row in rows]}}


def blk(kind, text):
    return {"object": "block", "type": kind, kind: {"rich_text": rt(text)}}


def prev_month(today):
    first = today.replace(day=1)
    p = first - timedelta(days=1)
    return "%04d-%02d" % (p.year, p.month)


def consecutive_negative(sigs, ym):
    """Types whose 30-day average excess was <0 in this month and the month before (n>=3 both)."""
    first, last = month_bounds(ym)
    pf, pl = month_bounds(prev_month(first))
    cur = month_stats(sigs, first, last)
    prv = month_stats(sigs, pf, pl)
    out = []
    for k in ("S1", "S2", "S3b", "S4"):
        a, b = cur[k], prv[k]
        if a["n30"] >= 3 and b["n30"] >= 3 and a["avg30"] < 0 and b["avg30"] < 0:
            out.append("%s\uff1a30 \u5929\u5e73\u5747\u8d85\u984d %s\uff08%s\uff09\u3001%s\uff08%s\uff09\u9023\u5169\u500b\u6708 <0 \u2192 \u63d0\u6848\u964d\u7d1a\u8a72\u985e\u63a8\u64ad\uff0cKai \u6c7a\u5b9a"
                       % (ROW_LABEL[k], fmt_pct(a["avg30"]), ym, fmt_pct(b["avg30"]), prev_month(first)))
    return out


def find_child_page(title):
    cursor = None
    for _ in range(20):
        path = "/blocks/%s/children?page_size=100" % SPEC_PAGE + ("&start_cursor=%s" % cursor if cursor else "")
        code, js = notion("GET", path)
        if code != 200:
            return None
        for b in js.get("results", []):
            if b.get("type") == "child_page" and (b.get("child_page") or {}).get("title") == title:
                return b["id"]
        if not js.get("has_more"):
            return None
        cursor = js.get("next_cursor")
    return None


def write_month_page(ym, sigs, st, start):
    first, last = month_bounds(ym)
    cur = month_stats(sigs, first, last)
    cum = month_stats(sigs, first, last, cumulative=True)
    excl = Counter()
    for s in sigs:
        if s["status"] == ST_EXCL:
            excl[(s.get("note") or "\u6392\u9664").split("\uff1a")[0][:20]] += 1
    flags = consecutive_negative(sigs, ym)
    gen = datetime.now(TPE).strftime("%Y-%m-%d %H:%M")
    title = "\u8a0a\u865f\u56de\u770b\u6708\u5831 %s" % ym
    blocks = [
        {"object": "block", "type": "callout", "callout": {
            "rich_text": rt("\u53e3\u5f91\uff08Kai 10-24\uff09\uff1a\u767b\u8a18\u65e5\u6536\u76e4\u8d77\u7b97\uff0c7\uff0f30\uff0f60 \u500b\u81ea\u7136\u65e5\u5f8c\u5c0d 0050 \u7684\u8d85\u984d\uff08\u4f11\u5e02\u53d6\u524d\u4e00\u4ea4\u6613\u65e5\uff09\uff1b"
                            "\u540c\u4e00\u6a94\u540c\u4e00\u985e 14 \u5929\u5167\u53ea\u7b97\u7b2c\u4e00\u6b21\u3002\u672c\u6708\u6b04\uff1d\u5230\u671f\u65e5\u843d\u5728 %s \u7684\u8a0a\u865f\uff1b\u8cc7\u6599\u5230 %s\uff1b\u7522\u751f\u65bc %s \u53f0\u5317\u3002"
                            "\u53c3\u8003\u6b04\uff1a\u9694\u65e5\u6536\u76e4\u8d77\u7b97\uff0c\u53ea\u7576\u53c3\u8003\uff08\u9084\u539f\u6b0a\u606f\u6b04 10-26 \u8d77\u505c\u7528\uff1a\u8b49\u4ea4\u6240\u5728\u9664\u6b0a\u606f\u65e5\u7684\u6f32\u8dcc\u50f9\u5dee\u8a18 X0.00\uff0c"
                            "FinMind \u8ddf\u8457\u8a18 0\uff0c\u7121\u6cd5\u9084\u539f\uff1b\u55ae\u65e5\u6f32\u8dcc\u8d85\u904e 10.5%% \u7684\u9664\u6b0a\u3001\u5206\u5272\u3001\u6e1b\u8cc7\u6539\u70ba\u6574\u7b46\u6392\u9664\uff09\u3002"
                            % (ym, st.get("known", "?"), gen)),
            "icon": {"type": "emoji", "emoji": "\U0001f9fe"}, "color": "gray_background"}},
        blk("heading_2", "\u672c\u6708\u5230\u671f\uff08%s\uff09" % ym),
        blk_table(table_rows(cur, "\u672c\u6708\u767b\u8a18")),
        blk("heading_2", "\u7d2f\u8a08\uff08%s \u8d77\uff09" % start),
        blk_table(table_rows(cum, "\u7d2f\u8a08\u767b\u8a18")),
        blk("heading_2", "\u63d0\u6848"),
    ]
    if flags:
        blocks += [blk("bulleted_list_item", f) for f in flags]
    else:
        blocks.append(blk("paragraph", "\u7121\uff08\u6c92\u6709\u4efb\u4f55\u4e00\u985e 30 \u5929\u5e73\u5747\u8d85\u984d\u9023\u5169\u500b\u6708 <0\uff09\u3002"))
    q = ["\u7f3a\u50f9\u7387 %.1f%%\uff08%d\uff0f%d \u7b46\uff09" % (st.get("missing_rate", 0) * 100, st.get("missing", 0), st.get("eligible", 0)),
         "\u6392\u9664\uff1a" + ("\u3001".join("%s %d" % kv for kv in excl.most_common()) if excl else "0"),
         "\u5238\u5546\u8cc7\u6599\u7570\u5e38\uff08\u6700\u8fd1\u4e00\u6b21 run \u5254\u9664\uff09\uff1a\u5831\u544a\u65e5\u665a\u65bc\u5efa\u7acb\u65e5 %d\u3001\u5831\u544a\u65e5\u65e9\u65bc\u5efa\u7acb\u65e5 90 \u5929\u4ee5\u4e0a %d\u3001\u4ee3\u78bc\u7f3a\u6f0f\u6216\u591a\u6a94 %d\u3001"
         "\u975e\u5238\u5546\u6216\u5238\u5546\u4e0d\u660e %d\u3001\u8f49\u5f15 %d\u3001\u540c\u65e5\u91cd\u8907 %d\u3001\u76ee\u6a19\u50f9\u8207\u7576\u65e5\u80a1\u50f9\u4e0d\u7b26 %d\u3001\u76ee\u6a19\u50f9\u8df3\u5e45\u8d85\u904e 3 \u500d %d" % (
             st.get("anom_date_after_created", 0), st.get("anom_date_gap90", 0), st.get("anom_code", 0),
             st.get("anom_broker", 0), st.get("anom_quote", 0), st.get("anom_same_day_dup", 0),
             st.get("anom_tp_px", 0), st.get("anom_tp_jump", 0)),
         "\u65b0\u805e 10-14 \u8d77\u63db M3\uff0bv4\uff0c\U0001f534 \u6a19\u6e96\u524d\u5f8c\u4e0d\u540c \u2192 \u5206\u5169\u5217\u770b\uff1b\u6a23\u672c <3 \u53ea\u5217\u300c\u5019\u9078\u60f3\u6cd5\u300d\u3002"]
    blocks.append(blk("heading_2", "\u8cc7\u6599\u54c1\u8cea"))
    blocks += [blk("bulleted_list_item", x) for x in q]
    blocks.append(blk("paragraph", "\u9010\u7b46\u660e\u7d30\uff1a\U0001f9fe \u8a0a\u865f\u56de\u770b\u53f0\u5e33 https://www.notion.so/%s" % LEDGER_DB.replace("-", "")))
    old = find_child_page(title)
    if old:
        notion("PATCH", "/pages/%s" % old, {"archived": True})
    code, js = notion("POST", "/pages", {
        "parent": {"page_id": SPEC_PAGE}, "icon": {"type": "emoji", "emoji": "\U0001f9fe"},
        "properties": {"title": {"title": rt(title)}}, "children": blocks})
    if code != 200:
        return None, "HTTP %s %s" % (code, mask(str(js.get("message", "")))[:150]), cur, flags
    return js.get("url") or "", "", cur, flags


def summary_telegram(ym, cur, flags, url):
    lines = ["\U0001f9fe \u8a0a\u865f\u56de\u770b\u6708\u5831 %s\uff0830 \u5929\u5230\u671f\uff09" % ym]
    for k in ROW_KEYS:
        r = cur[k]
        if r["n30"] == 0:
            lines.append("%s\uff1a\u672c\u6708\u7121\u5230\u671f" % ROW_LABEL[k])
        elif r["n30"] < 3:
            lines.append("%s\uff1a\u5019\u9078\u60f3\u6cd5\uff08n=%d\uff09" % (ROW_LABEL[k], r["n30"]))
        else:
            lines.append("%s\uff1a%s\uff08n=%d\uff0c\u8dd1\u8d0f %d\uff09" % (ROW_LABEL[k], fmt_pct(r["avg30"]), r["n30"], r["win30"]))
    for f in flags:
        lines.append("\u26a0\ufe0f " + f)
    if url:
        lines.append(url)
    return "\n".join(lines)


# ---------------------------------------------------------------- subcommands
def acquire_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return lock


def cron_text():
    rc, txt, err = sh(["crontab", "-l"])
    if rc != 0:
        return ""
    return txt


def cmd_check():
    problems = []
    try:
        load_secrets()
        for k in ("notion_key", "telegram_bot_token", "telegram_dm"):
            print("secret %-20s %s" % (k, "ok" if SECRETS.get(k) else "MISSING"))
            if not SECRETS.get(k):
                problems.append("secret " + k)
        fm_ok = any(SECRETS.get(k) for k in ("finmind_token", "finmind_key", "FINMIND_TOKEN", "FINMIND_KEY"))
        print("secret %-20s %s" % ("finmind token", "ok" if fm_ok else "MISSING"))
        if not fm_ok:
            problems.append("finmind token")
        print("secret %-20s %s" % ("broker db override", "set" if SECRETS.get("notion_broker_reports_db") else "default"))
    except Exception as e:
        print("secrets.json: %s" % type(e).__name__)
        problems.append("secrets.json")
    for label, path in (("Top N DB", "/databases/%s" % TOPN_DB),
                        ("broker DB", "/databases/%s" % (SECRETS.get("notion_broker_reports_db") or BROKER_DB_DEFAULT)),
                        ("ledger DB", "/databases/%s" % LEDGER_DB),
                        ("spec page", "/pages/%s" % SPEC_PAGE)):
        code, _ = notion("GET", path)
        print("notion %-10s HTTP %s" % (label, code))
        if code != 200:
            problems.append("%s HTTP %s" % (label, code))
    files = news_files()
    dated = sorted(d for d in (news_tpe_date(f.name) for f in files) if d)
    print("news files: %d (archive %d) %s .. %s" % (
        len(files), sum(1 for f in files if "archive" in str(f)),
        dated[0] if dated else "-", dated[-1] if dated else "-"))
    if not files:
        problems.append("no news files")
    print("names file: %s" % ("ok %d" % len(load_names()) if NAMES_FILE.exists() else "missing (names from sources)"))
    try:
        fm = finmind()
        print("_finmind: ok MIN_INTERVAL=%s CALL_LIMIT=%s" % (getattr(fm, "MIN_INTERVAL", "?"), getattr(fm, "CALL_LIMIT", "?")))
    except Exception as e:
        print("_finmind: import failed %s" % type(e).__name__)
        problems.append("_finmind import")
    con = db_open(readonly=True)
    if con is None:
        print("sqlite: not created yet (%s)" % DB_FILE)
    else:
        n = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        print("sqlite: start=%s signals=%d" % (meta_get(con, "start", "-"), n))
    ct = cron_text()
    print("crontab: run=%s summary=%s" % (CRON_TAG_RUN in ct, CRON_TAG_SUM in ct))
    print("python: %s  version %s" % (sys.version.split()[0], VERSION))
    if problems:
        print("problems: " + "; ".join(problems))
        print("RESULT=CHECK_FAIL")
        return 1
    print("RESULT=CHECK_OK")
    return 0


def cmd_dry():
    load_secrets()
    today = today_tpe()
    con = db_open(readonly=True)
    start = s2d((meta_get(con, "start") if con is not None else None) or DEFAULT_START)
    prices = Prices(None)
    fetcher = Fetcher(prices, 2, today)
    fetcher.ensure(BENCH, start - timedelta(days=15), today)
    cal = Calendar(prices)
    print("0050 rows %d, known through %s%s" % (len(prices.dates(BENCH)), cal.known,
                                                ("; errors: " + "; ".join(fetcher.errors)) if fetcher.errors else ""))
    stats = Counter()
    cands, ok, errs = build_candidates(start, today, cal, stats)
    kept, dropped = dedup([c for c in cands if c["stype"] in ok], {})
    print("window %s..%s sources ok %s" % (start, today, ",".join(sorted(ok))))
    for e in errs:
        print("source error: " + e)
    by_type = Counter(c["stype"] for c in kept)
    by_month = defaultdict(Counter)
    for c in kept:
        by_month[c["reg_date"].strftime("%Y-%m")][c["stype"]] += 1
    print("kept (raw/dedup): " + "  ".join("%s %d (%d/%d)" % (t, by_type.get(t, 0), stats.get("raw_" + t, 0),
                                                              dropped.get(t, 0)) for t in TYPES))
    for ym in sorted(by_month):
        print("  %s  %s" % (ym, " ".join("%s=%d" % (t, by_month[ym].get(t, 0)) for t in TYPES)))
    keys = ("s1_rows", "s3_files", "broker_rows", "broker_used", "skip_short", "anom_date_after_created",
            "anom_date_gap90", "anom_code", "anom_broker", "anom_quote", "anom_same_day_dup", "anom_tp_px",
            "anom_tp_jump")
    print("sources: " + " ".join("%s=%d" % (k, stats.get(k, 0)) for k in keys))
    for t in TYPES:
        last = [c for c in kept if c["stype"] == t][-1:]
        for c in last:
            print("  last %s %s %s %s" % (t, c["reg_date"], c["code"], c["source"][:60]))
    print("codes to price: %d" % len(set(c["code"] for c in kept)))
    print("RESULT=DRY_OK s1=%d s2=%d s3=%d s4=%d errors=%d" % (
        by_type.get("S1", 0), by_type.get("S2", 0), by_type.get("S3", 0), by_type.get("S4", 0), len(errs)))
    return 0


def cmd_status():
    con = db_open(readonly=True)
    if con is None:
        print("sqlite not created yet")
        print("RESULT=STATUS_EMPTY")
        return 0
    sigs = all_signals(con)
    print("start %s  signals %d  price rows %d  codes %d" % (
        meta_get(con, "start", "-"), len(sigs), con.execute("SELECT COUNT(*) FROM prices").fetchone()[0],
        con.execute("SELECT COUNT(*) FROM coverage").fetchone()[0]))
    st = Counter((s["stype"], s["status"]) for s in sigs)
    for t in TYPES:
        print("%s %s" % (t, "  ".join("%s=%d" % (k, st.get((t, k), 0)) for k in (ST_TRACK, ST_DONE, ST_MISS, ST_EXCL, ST_REVOKED))))
    last = today_tpe()
    cum = month_stats(sigs, date(2000, 1, 1), last, cumulative=True)
    for k in ROW_KEYS:
        r = cum[k]
        print("%-6s 7d %s  30d %s  60d %s  win30 %s" % (k, cell_avg(r, 7), cell_avg(r, 30), cell_avg(r, 60),
                                                    "%d/%d" % (r["win30"], r["n30"]) if r["n30"] else "-"))
    no_page = sum(1 for s in sigs if not s["notion_id"] and s["status"] != ST_REVOKED)
    print("without Notion page: %d" % no_page)
    for r in con.execute("SELECT ts, cmd, result FROM runs ORDER BY ts DESC LIMIT 5"):
        print("run %s %s %s" % (r["ts"], r["cmd"], r["result"]))
    ls = json.loads(meta_get(con, "last_stats", "{}") or "{}")
    print("last: known=%s missing=%s/%s fetch_calls=%s stopped=%s" % (
        ls.get("known"), ls.get("missing"), ls.get("eligible"), ls.get("fetch_calls"), ls.get("fetch_stopped")))
    print("RESULT=STATUS_OK signals=%d" % len(sigs))
    return 0


def cmd_backfill(code, start_s):
    if not passcode_ok(code):
        print("RESULT=BACKFILL_DENIED")
        return 3
    try:
        start = s2d(start_s)
    except ValueError:
        print("bad START, use YYYY-MM-DD")
        print("RESULT=USAGE")
        return 2
    sys.stdout = Tee(BACKFILL_LOG)
    print("[START] signal_ledger %s backfill %s utc=%s" % (VERSION, start, now_utc().strftime("%Y-%m-%d %H:%M")))
    lock = acquire_lock()
    if lock is None:
        print("RESULT=LOCKED")
        return 0
    load_secrets()
    con = db_open()
    old = meta_get(con, "start")
    if old and old != d2s(start):
        print("ledger already started at %s; refusing a different start" % old)
        print("RESULT=BACKFILL_CONFLICT")
        return 1
    meta_set(con, "start", d2s(start))
    con.commit()
    telegram("\u25b6\ufe0f \u8a0a\u865f\u56de\u770b\u53f0\u5e33\u56de\u586b\u958b\u59cb\uff1a\u81ea %s\uff0cFinMind \u6bcf %.0f \u79d2\u4e00\u6a94\uff0c\u9810\u4f30 1\u20132 \u5c0f\u6642\u3002\u5b8c\u6210\u6703\u518d\u901a\u77e5\u3002" % (start, BACKFILL_INTERVAL))
    t0 = time.time()
    st = pipeline(con, "backfill", start, today_tpe(), BACKFILL_MAX_CALLS, interval=BACKFILL_INTERVAL)
    mins = (time.time() - t0) / 60.0
    pending = st.get("st_" + ST_TRACK, 0)
    result = "BACKFILL_OK" if not (st["notion_fail"] or st["source_errors"] or st["fetch_stopped"]) else "BACKFILL_PARTIAL"
    line = ("RESULT=%s s1=%d s2=%d s3=%d s4=%d missing=%.1f%% notion_new=%d notion_fail=%d fetch=%d minutes=%.0f" % (
        result, st.get("new_S1", 0), st.get("new_S2", 0), st.get("new_S3", 0), st.get("new_S4", 0),
        st["missing_rate"] * 100, st["notion_created"], st["notion_fail"], st["fetch_calls"], mins))
    record_run(con, "backfill", result, st)
    msg = ["%s \u8a0a\u865f\u56de\u770b\u53f0\u5e33\u56de\u586b%s\uff08%.0f \u5206\u9418\uff09" % ("\u2705" if result == "BACKFILL_OK" else "\u26a0\ufe0f",
                                          "\u5b8c\u6210" if result == "BACKFILL_OK" else "\u90e8\u5206\u5b8c\u6210", mins),
           counts_line(st),
           "\u72c0\u614b\uff1a\u8ffd\u8e64\u4e2d %d\u3001\u5b8c\u6210 %d\u3001\u7f3a\u50f9 %d\u3001\u6392\u9664 %d" % (pending, st.get("st_" + ST_DONE, 0),
                                                st.get("st_" + ST_MISS, 0), st.get("st_" + ST_EXCL, 0)),
           "\u7f3a\u50f9\u7387 %.1f%%\uff08%d\uff0f%d\uff09\uff1bNotion \u65b0\u589e %d\u3001\u5931\u6557 %d\uff1bFinMind %d \u6b21%s" % (
               st["missing_rate"] * 100, st["missing"], st["eligible"], st["notion_created"], st["notion_fail"],
               st["fetch_calls"], ("\uff0c\u4e2d\u6b62\uff1a" + st["fetch_stopped"]) if st["fetch_stopped"] else "")]
    if st["source_errors"]:
        msg.append("\u4f86\u6e90\u932f\u8aa4\uff1a" + "\uff1b".join(st["source_errors"])[:500])
    if st["fetch_stopped"] == "budget":
        msg.append("\u50f9\u683c\u672a\u6293\u5b8c\u7684\u90e8\u5206\uff0c\u4e4b\u5f8c\u6bcf\u665a run \u6703\u7e8c\u6293\u3002")
    telegram("\n".join(msg))
    print(counts_line(st))
    print(line)
    return 0 if result == "BACKFILL_OK" else 1


def cmd_run(label="run"):
    print("[START] signal_ledger %s %s utc=%s" % (VERSION, label, now_utc().strftime("%Y-%m-%d %H:%M")))
    lock = acquire_lock()
    if lock is None:
        print("RESULT=LOCKED")
        return 0, None
    load_secrets()
    ro = db_open(readonly=True)
    start = meta_get(ro, "start") if ro is not None else None
    if ro is not None:
        ro.close()
    if not start:
        print("ledger not initialised: run backfill first (passcode)")
        print("RESULT=NOT_INITIALIZED")
        return 0, None
    con = db_open()
    st = pipeline(con, "run", s2d(start), today_tpe(), RUN_MAX_CALLS)
    msgs = alerts_after_run(con, st, label)
    result = "RUN_WARN" if msgs else "RUN_OK"
    record_run(con, label, result, st)
    print(counts_line(st))
    print("RESULT=%s new=%d pending=%d done=%d missing=%.1f%% notion_new=%d notion_upd=%d notion_fail=%d fetch=%d "
          "fetch_err=%d" % (
              result, sum(st.get("new_" + t, 0) for t in TYPES), st.get("st_" + ST_TRACK, 0),
              st.get("st_" + ST_DONE, 0), st["missing_rate"] * 100, st["notion_created"], st["notion_updated"],
              st["notion_fail"], st["fetch_calls"], st["fetch_errors"]))
    return (0 if result == "RUN_OK" else 1), (con, st, start, lock)


def cmd_summary(ym):
    if ym:
        if not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", ym):
            print("bad month, use YYYY-MM")
            print("RESULT=USAGE")
            return 2
    else:
        ym = prev_month(today_tpe())
    rc, ctx = cmd_run("summary")
    if ctx is None:
        print("RESULT=SUMMARY_SKIPPED")
        return rc
    con, st, start, _lock = ctx
    sigs = all_signals(con)
    url, err, cur, flags = write_month_page(ym, sigs, st, start)
    if err:
        telegram("\u26a0\ufe0f \u8a0a\u865f\u56de\u770b\u6708\u5831 %s \u5beb\u5165 Notion \u5931\u6557\uff1a%s" % (ym, err))
        print("RESULT=SUMMARY_WRITE_FAIL")
        return 1
    telegram(summary_telegram(ym, cur, flags, url))
    record_run(con, "summary " + ym, "SUMMARY_OK", st)
    print("page: " + url)
    print("RESULT=SUMMARY_OK month=%s flags=%d" % (ym, len(flags)))
    return 0


def cmd_install(code):
    if not passcode_ok(code):
        print("RESULT=INSTALL_DENIED")
        return 3
    rc, old, err = sh(["crontab", "-l"])
    if rc != 0 and "no crontab" not in (err or ""):
        print("crontab -l failed: " + mask(err.strip())[:200])
        print("RESULT=INSTALL_FAIL")
        return 1
    if rc != 0:
        old = ""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if CRON_TAG_RUN in old and CRON_TAG_SUM in old:
        print(sync_repo_crontab())
        print("RESULT=INSTALL_ALREADY")
        return 0
    if CRON_TAG_RUN in old or CRON_TAG_SUM in old:
        print("only one of the two lines is installed; fix by hand")
        print("RESULT=INSTALL_FAIL")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    bak = STATE_DIR / ("crontab.bak_signal_ledger_" + now_utc().strftime("%Y%m%d%H%M%S"))
    bak.write_text(old, encoding="utf-8")
    new = (old.rstrip("\n") + "\n" if old.strip() else "") + CRON_COMMENT + "\n" + CRON_RUN + "\n" + CRON_SUM + "\n"
    rc, _, err = sh(["crontab", "-"], stdin_text=new)
    rc2, after, _ = sh(["crontab", "-l"])

    def norm(t):
        return set(x.strip() for x in t.splitlines() if x.strip())
    added = norm(after) - norm(old)
    removed = norm(old) - norm(after)
    if rc != 0 or rc2 != 0 or removed or added != {CRON_COMMENT, CRON_RUN, CRON_SUM}:
        sh(["crontab", str(bak)])
        print("verify failed: rc=%s rc2=%s added=%d removed=%d -> restored %s" % (
            rc, rc2, len(added), len(removed), bak.name))
        print("RESULT=INSTALL_FAIL")
        return 1
    print("crontab backup: " + str(bak))
    print("added: " + CRON_RUN)
    print("added: " + CRON_SUM)
    print(sync_repo_crontab())
    print("RESULT=INSTALL_OK")
    return 0


def sync_repo_crontab():
    repo_file = WS / "cron" / "crontab.txt"
    if not repo_file.exists():
        return "repo crontab.txt missing"
    rtxt = repo_file.read_text(encoding="utf-8")
    if CRON_TAG_RUN in rtxt and CRON_TAG_SUM in rtxt:
        return "repo crontab.txt already has the lines"
    repo_file.write_text(rtxt.rstrip("\n") + "\n\n" + CRON_COMMENT + "\n" + CRON_RUN + "\n" + CRON_SUM + "\n",
                         encoding="utf-8")
    return "repo crontab.txt appended"


def git(*args, timeout=60):
    return sh(["git", "-C", str(WS)] + list(args), timeout=timeout)


def cmd_commit(code):
    if not passcode_ok(code):
        print("RESULT=COMMIT_DENIED")
        return 3
    files = ["scripts/signal_ledger.py", "cron/crontab.txt"]
    rc, t, _ = git("status", "-sb", "--untracked-files=no")
    first = t.splitlines()[0] if t else ""
    ahead_before = int(re.search(r"ahead (\d+)", first).group(1)) if "ahead" in first else 0
    rc, _, err = git("add", "--", *files)
    if rc != 0:
        print("git add failed: " + mask(err.strip())[:200])
        print("RESULT=COMMIT_FAIL")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    msgf = STATE_DIR / "signal_ledger_commit_msg.txt"
    msgf.write_text(
        "B1 signal_ledger v1.1: retire spread-based dividend adjustment, flag >10.5% one-day moves\n\n"
        "- TWSE prints ex-rights days as X0.00 and FinMind stores 0, so the 30-day adjusted column and its\n"
        "  15-point exclusion rule were wrong; rows it excluded are reopened once and re-evaluated\n"
        "- corporate actions (ex-rights, split, capital reduction) = a one-day move beyond the 10% limit\n"
        "- S2 rows keep only the widest broker list in the source text\n\n"
        "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01NhgrUyKz6bWjHLLn7NJwEj\n",
        encoding="utf-8")
    rc, out, err = git("commit", "-F", str(msgf), "--", *files)
    if rc != 0:
        print("git commit failed: " + mask((out + err).strip())[:300])
        print("RESULT=COMMIT_FAIL")
        return 1
    rc, t, _ = git("log", "--oneline", "-1")
    print("commit: " + t.strip())
    if ahead_before > 0:
        print("push skipped: %d other local commits were already unpushed" % ahead_before)
        print("RESULT=COMMIT_OK_PUSH_SKIPPED")
        return 0
    rc, out, err = git("push", "origin", "HEAD", timeout=120)
    if rc != 0:
        print("push failed: " + mask((out + err).strip())[:300])
        print("RESULT=COMMIT_OK_PUSH_FAIL")
        return 0
    print("RESULT=COMMIT_OK_PUSHED")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    arg = argv[2] if len(argv) > 2 else ""
    arg2 = argv[3] if len(argv) > 3 else ""
    if cmd not in ("backfill", "run", "summary"):
        print("[START] signal_ledger %s %s utc=%s" % (VERSION, cmd, now_utc().strftime("%Y-%m-%d %H:%M")))
    if cmd == "check":
        return cmd_check()
    if cmd == "dry":
        return cmd_dry()
    if cmd == "status":
        return cmd_status()
    if cmd == "backfill":
        return cmd_backfill(arg, arg2)
    if cmd == "run":
        return cmd_run()[0]
    if cmd == "summary":
        return cmd_summary(arg)
    if cmd == "install":
        return cmd_install(arg)
    if cmd == "commit":
        return cmd_commit(arg)
    print("usage: signal_ledger.py check|dry|status|backfill CODE START|run|summary [YYYY-MM]|install CODE|commit CODE")
    print("RESULT=USAGE")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except Exception as exc:
        print(mask(traceback.format_exc()))
        try:
            if not SECRETS:
                load_secrets()
            telegram("\U0001f534 signal_ledger CRASH\uff08%s\uff09\uff1a%s: %s" % (
                sys.argv[1] if len(sys.argv) > 1 else "check", type(exc).__name__, mask(str(exc))[:300]))
        except Exception:
            pass
        print("RESULT=CRASH")
        sys.exit(2)
