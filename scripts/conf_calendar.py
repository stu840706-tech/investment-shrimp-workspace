#!/usr/bin/env python3
"""conf_calendar.py (D0-1, 2026-10-26) - investor conferences from MOPS, written to the calendar before they happen.

The calendar DB used to get conference rows only after the fact (receive_telegram writes one when a memo arrives).
This reads the public MOPS list (t100sb02_1, listed + OTC, this month and next) once a weekday and adds a row for
every conference of a stock we hold or have a valuation card for. Zero LLM.

Subcommands (no double-dash flags on purpose: Telegram turns -- into a dash):
  check          read-only: secrets, Notion access, one MOPS request, crontab
  dry            read-only: fetch MOPS, show the conferences that would be added
  run            fetch MOPS, add new rows to the calendar DB, Telegram the new ones
  install CODE   add the crontab line (weekdays 09:10 Taipei) and append it to cron/crontab.txt (passcode)
  commit CODE    git add + commit (+ push when nothing else is unpushed) the two files (passcode)

The last output line is always RESULT=...
Telegram: new conferences found, Notion write failure, MOPS unreachable two runs in a row, crash.
MOPS request: POST https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1 (form TYPEK/year/month/co_id, ROC year),
reply HTML table#myTable with a two-row header; "\u67e5\u7121\u8cc7\u6599" when empty.
"""
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

VERSION = "1.1"
HOME = Path.home()
WS = HOME / ".openclaw" / "workspace"
SECRETS_FILE = WS / "config" / "secrets.json"
STATE_DIR = WS / "state"
STATE_FILE = STATE_DIR / "conf_calendar_state.json"
LOCK_FILE = STATE_DIR / "conf_calendar.lock"
LOG_DIR = HOME / ".openclaw" / "logs"
RUN_LOG = LOG_DIR / "conf_calendar.log"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TPE = timezone(timedelta(hours=8))
PASS_SHA256 = "6deb28659fee95de2fa93c040a145a56fd3a70f9a4e678af5d08ec39195979e7"
PY = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable

EVENT_DB_DEFAULT = "34e226f5-a398-81ea-8a99-ecd61b9e8795"     # calendar DB (event_calendar)
TRACKING_DB_DEFAULT = "34e226f5-a398-814f-b457-c9e2fe01c1c0"  # stock tracking DB
MONITOR_DB = "f856637f-d60a-4b18-bf02-fb08c27e6b5c"           # holdings target-price monitor DB
LADDER_DB = "12dce5bc-5771-4231-a75d-da12739985ea"            # valuation ladder DB

MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1"
MOPS_PAGE = "https://mops.twse.com.tw/mops/#/web/t100sb02_1"
MOPS_HIDDEN = {"encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1"}
MARKETS = (("sii", "\u4e0a\u5e02"), ("otc", "\u4e0a\u6ac3"))
MOPS_GAP = 3.0             # seconds between MOPS requests (it throttles crawlers)
KEEP_PAST_DAYS = 7         # a conference up to 7 days ago is still added (late announcements, D4 cards)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")

CRON_TAG = "scripts/conf_calendar.py run"
CRON_COMMENT = "# D0-1 conf_calendar: weekdays 09:10 Taipei (01:10 UTC) MOPS investor conferences -> calendar DB"
CRON_LINE = ("10 1 * * 1-5 cd " + str(WS) + " && " + PY
             + " scripts/conf_calendar.py run >> " + str(RUN_LOG) + " 2>&1")

H_CODE, H_NAME, H_DATE, H_TIME, H_PLACE, H_SUMMARY = ("\u516c\u53f8\u4ee3\u865f", "\u516c\u53f8\u540d\u7a31", "\u53ec\u958b\u6cd5\u4eba\u8aaa\u660e\u6703\u65e5\u671f",
                                                      "\u53ec\u958b\u6cd5\u4eba\u8aaa\u660e\u6703\u6642\u9593", "\u53ec\u958b\u6cd5\u4eba\u8aaa\u660e\u6703\u5730\u9ede", "\u6cd5\u4eba\u8aaa\u660e\u6703\u64c7\u8981\u8a0a\u606f")
H_FILES, H_ZH, H_EN, H_OTHER = "\u6cd5\u4eba\u8aaa\u660e\u6703\u7c21\u5831\u5167\u5bb9", "\u4e2d\u6587\u6a94\u6848", "\u82f1\u6587\u6a94\u6848", "\u5176\u4ed6\u61c9\u6558\u660e\u4e8b\u9805"
H_WEB = ("\u516c\u53f8\u7db2\u7ad9\u662f\u5426\u63d0\u4f9b\u6cd5\u4eba\u8aaa\u660e\u6703\u76f8\u95dc\u8cc7\u8a0a", "\u516c\u53f8\u7db2\u7ad9\u662f\u5426\u63d0\u4f9b\u6cd5\u4eba\u8aaa\u660e\u6703\u5167\u5bb9")
H_VIDEO = "\u5f71\u97f3\u9023\u7d50\u8cc7\u8a0a"
NO_DATA = "\u67e5\u7121\u8cc7\u6599"
INVITED = ("\u53d7\u9080", "\u9080\u8acb", "\u53c3\u52a0", "\u8ad6\u58c7", "Forum", "Summit", "Conference")

SECRETS = {}
SECRET_VALUES = []
_MASK_RES = [re.compile(p) for p in (
    r"secret_[A-Za-z0-9]{20,}", r"ntn_[A-Za-z0-9]{20,}", r"\b\d{8,11}:[A-Za-z0-9_-]{30,}",
    r"Bearer\s+[A-Za-z0-9._-]{16,}")]
CODE_FIND = re.compile(r"(?<![0-9])([0-9]{4,6})(?![0-9])")
ROC_DATE = re.compile(r"([0-9]{2,3})\s*/\s*([0-9]{1,2})\s*/\s*([0-9]{1,2})")
WEEKDAY = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u65e5"


class MopsError(Exception):
    pass


# ---------------------------------------------------------------- basics
def now_utc():
    return datetime.now(timezone.utc)


def today_tpe():
    return datetime.now(TPE).date()


def load_secrets():
    global SECRETS, SECRET_VALUES
    SECRETS = json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    vals = []
    for k, v in SECRETS.items():
        if any(w in k.lower() for w in ("key", "token", "secret", "password")):
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
    for attempt in range(3):
        try:
            code, js = http_json(method, NOTION_API + path, hdr, body)
        except Exception as e:
            code, js = 0, {"message": "%s: %s" % (type(e).__name__, e)}
            if attempt < 2:
                time.sleep(10)
                continue
            return code, js
        if code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(15)
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


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def event_db():
    return SECRETS.get("notion_event_calendar_db") or EVENT_DB_DEFAULT


def tracking_db():
    return SECRETS.get("notion_stock_tracking_db") or TRACKING_DB_DEFAULT


# ---------------------------------------------------------------- MOPS
class _Table(HTMLParser):
    """Rows of the first <table id=myTable> as lists of cell texts (nested tables folded into the cell)."""

    def __init__(self, table_id="myTable"):
        super().__init__(convert_charrefs=True)
        self.table_id = table_id
        self.depth = 0
        self.rows = []
        self._row = None
        self._cell = None
        self.done = False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            if self.depth:
                self.depth += 1
            elif dict(attrs).get("id") == self.table_id:
                self.depth = 1
            return
        if tag == "br" and self._cell is not None:
            self._cell.append(" ")
        if self.depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if not self.depth or self.done:
            return
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                if self._row:
                    self.rows.append(self._row)
                self.done = True
            return
        if self.depth != 1:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self.depth and self._cell is not None:
            self._cell.append(data)


def parse_conferences(html):
    """List of dicts from a t100sb02_1 reply; [] for \u67e5\u7121\u8cc7\u6599; MopsError for anything unexpected."""
    p = _Table()
    p.feed(html)
    rows = p.rows
    if len(rows) < 2:
        if NO_DATA in html:
            return []
        snippet = " ".join(re.sub(r"<[^>]+>", " ", html).split())[:80]
        raise MopsError("unexpected page (no table): " + snippet)
    top, sub = rows[0], rows[1]
    want_sub = [H_ZH, H_EN] if H_FILES in top else []
    if sub != want_sub:
        raise MopsError("second header row changed: %s" % sub)
    names = []
    for t in top:
        names += [t + "/" + H_ZH, t + "/" + H_EN] if t == H_FILES else [t]
    for need in (H_CODE, H_NAME, H_DATE, H_TIME, H_PLACE, H_SUMMARY):
        if need not in names:
            raise MopsError("header lost column " + need)
    web = next((n for n in H_WEB if n in names), None)
    out = []
    for r in rows[2:]:
        if len(r) != len(names):
            # the last column (history button) is sometimes missing: accept one short cell
            if len(r) == len(names) - 1:
                r = r + [""]
            else:
                raise MopsError("row has %d cells, header %d: %s" % (len(r), len(names), r[:3]))
        d = dict(zip(names, r))
        out.append({"code": d.get(H_CODE, "").strip(), "name": d.get(H_NAME, "").strip().rstrip("*").strip(),
                    "date_raw": d.get(H_DATE, ""), "time": d.get(H_TIME, ""), "place": d.get(H_PLACE, ""),
                    "summary": d.get(H_SUMMARY, ""), "file_zh": d.get(H_FILES + "/" + H_ZH, ""),
                    "file_en": d.get(H_FILES + "/" + H_EN, ""), "web": d.get(web, "") if web else "",
                    "video": d.get(H_VIDEO, ""), "other": d.get(H_OTHER, "")})
    return out


def roc_to_date(s):
    m = ROC_DATE.search(s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def mops_post(typek, roc_year, month):
    form = dict(MOPS_HIDDEN)
    form.update({"TYPEK": typek, "year": str(roc_year), "month": "%02d" % month, "co_id": ""})
    data = urllib.parse.urlencode(form).encode("ascii")
    req = urllib.request.Request(MOPS_URL, data=data, method="POST", headers={
        "User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://mopsov.twse.com.tw/mops/web/t100sb02_1", "Accept": "text/html,*/*"})
    last = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
            text = raw.decode("utf-8", errors="replace")
            if H_CODE not in text and NO_DATA not in text:
                alt = raw.decode("cp950", errors="replace")      # some legacy pages are Big5
                if H_CODE in alt or NO_DATA in alt:
                    return alt
            return text
        except Exception as e:
            last = e
            time.sleep(10)
    raise MopsError("%s: %s" % (type(last).__name__, str(last)[:120]))


def months_to_query(today):
    first = today.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return [(first.year - 1911, first.month), (nxt.year - 1911, nxt.month)]


def fetch_mops(today):
    """Returns (conferences, errors, requests). Conferences carry market label and parsed date."""
    confs, errors, n = [], [], 0
    for roc_year, month in months_to_query(today):
        for typek, label in MARKETS:
            if n:
                time.sleep(MOPS_GAP)
            n += 1
            try:
                rows = parse_conferences(mops_post(typek, roc_year, month))
            except MopsError as e:
                errors.append("%s %d/%02d: %s" % (typek, roc_year, month, e))
                continue
            for r in rows:
                r["market"] = label
                r["date"] = roc_to_date(r["date_raw"])
                confs.append(r)
    return confs, errors, n


# ---------------------------------------------------------------- Notion
def p_text(props, name):
    p = props.get(name) or {}
    t = p.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in (p.get(t) or []))
    if t == "select":
        return ((p.get("select") or {}).get("name")) or ""
    if t == "date":
        return ((p.get("date") or {}).get("start")) or ""
    return ""


def query_all(db_id, body=None, max_pages=60):
    out, cursor = [], None
    for _ in range(max_pages):
        b = dict(body or {})
        b["page_size"] = 100
        if cursor:
            b["start_cursor"] = cursor
        code, js = notion("POST", "/databases/%s/query" % db_id, b)
        if code != 200:
            return None, "HTTP %s %s" % (code, mask(str(js.get("message", "")))[:120])
        out.extend(js.get("results", []))
        if not js.get("has_more"):
            return out, ""
        cursor = js.get("next_cursor")
        time.sleep(0.34)
    return out, "more than %d pages" % max_pages


def norm_code(s):
    m = CODE_FIND.findall(s or "")
    return m[0] if len(m) == 1 else ""


def interest_codes():
    """code -> {name, holding(bool), page (tracking page id or None), src}; plus notes for unreadable sources."""
    codes, notes = {}, []
    rows, err = query_all(tracking_db(), {"filter": {"property": "\u72c0\u614b", "select": {"does_not_equal": "\u4e0d\u7e7c\u7e8c\u8ffd\u8e64"}}})
    if rows is None:
        raise RuntimeError("\U0001f4c8 \u500b\u80a1\u8ffd\u8e64 " + err)
    for pg in rows:
        props = pg.get("properties") or {}
        c = norm_code(p_text(props, "\u80a1\u7968\u4ee3\u78bc"))
        if c:
            e = codes.setdefault(c, {"name": p_text(props, "\u516c\u53f8\u540d\u7a31").strip(), "holding": True, "page": None,
                                     "src": set()})
            e["page"] = e["page"] or pg.get("id")
            e["src"].add("\u8ffd\u8e64")
    rows, err = query_all(MONITOR_DB)
    if rows is None:
        notes.append("\U0001f3af \u76e3\u63a7 " + err)
    else:
        for pg in rows:
            props = pg.get("properties") or {}
            if p_text(props, "\u72c0\u614b") == "\u5df2\u51fa\u6e05":
                continue
            c = norm_code(p_text(props, "\u4ee3\u78bc"))
            if c:
                e = codes.setdefault(c, {"name": p_text(props, "\u6a19\u7684").strip(), "holding": True, "page": None,
                                         "src": set()})
                e["holding"] = True
                e["src"].add("\u76e3\u63a7")
    rows, err = query_all(LADDER_DB)
    if rows is None:
        notes.append("\U0001f4d0 \u4f30\u503c\u968e\u68af " + err)
    else:
        for pg in rows:
            props = pg.get("properties") or {}
            c = norm_code(p_text(props, "\u4ee3\u78bc"))
            if c:
                e = codes.setdefault(c, {"name": p_text(props, "\u6a19\u7684").strip(), "holding": False, "page": None,
                                         "src": set()})
                e["src"].add("\u968e\u68af")
    return codes, notes


def existing_events():
    """(code, date) pairs already in the calendar as \u6cd5\u8aaa\u6703, whatever code format was used."""
    rows, err = query_all(event_db(), {"filter": {"property": "\u4e8b\u4ef6\u985e\u578b", "select": {"equals": "\u6cd5\u8aaa\u6703"}}})
    if rows is None:
        raise RuntimeError("\u6cd5\u8aaa\u6703\u884c\u4e8b\u66c6 " + err)
    have = set()
    for pg in rows:
        props = pg.get("properties") or {}
        c = norm_code(p_text(props, "\u80a1\u7968\u4ee3\u78bc"))
        d = (p_text(props, "\u5be6\u969b\u65e5\u671f") or p_text(props, "\u9810\u8a08\u65e5\u671f"))[:10]
        if c and d:
            have.add((c, d))
    return have, len(rows)


def rt(text):
    text = str(text or "")
    return [{"type": "text", "text": {"content": text[:1900]}}] if text else []


def is_invited(c):
    s = c.get("summary", "") + " " + c.get("other", "")
    return any(w in s for w in INVITED) and "\u71df\u904b" not in c.get("summary", "")


def event_payload(c, info):
    url = None
    for u in (c.get("video"), c.get("web")):
        u = (u or "").strip()
        if u.startswith("http") and len(u) < 1900:
            url = u
            break
        if u.startswith("www.") and len(u) < 1900:
            url = "http://" + u
            break
    url = url or MOPS_PAGE
    props = {
        "\u9810\u8a08\u65e5\u671f": {"title": rt(c["date"].isoformat())},
        "\u80a1\u7968\u4ee3\u78bc": {"rich_text": rt(c["code"])},
        "\u516c\u53f8\u540d\u7a31": {"rich_text": rt(c["name"] or info.get("name", ""))},
        "\u4e8b\u4ef6\u985e\u578b": {"select": {"name": "\u6cd5\u8aaa\u6703"}},
        "\u91cd\u8981\u6027": {"select": {"name": "\u9ad8" if (info.get("holding") and not is_invited(c)) else "\u4e2d"}},
        "\u5df2\u63d0\u9192": {"checkbox": False},
        "\u5be6\u969b\u65e5\u671f": {"date": {"start": c["date"].isoformat()}},
        "\u9023\u7d50": {"url": url},
    }
    if info.get("page"):
        props["\u95dc\u806f\u8ffd\u8e64"] = {"relation": [{"id": info["page"]}]}
    lines = ["MOPS \u6cd5\u4eba\u8aaa\u660e\u6703\uff08%s\uff09\uff1a%s %s\uff5c%s" % (c["market"], c["date_raw"], c["time"], c["place"])]
    if c.get("summary"):
        lines.append("\u64c7\u8981\uff1a" + c["summary"])
    if c.get("file_zh") or c.get("file_en"):
        lines.append("\u7c21\u5831\u6a94\uff1a%s\uff0f%s" % (c.get("file_zh") or "\u2014", c.get("file_en") or "\u2014"))
    if c.get("other") and c["other"] not in ("\u7121", ""):
        lines.append("\u5176\u4ed6\uff1a" + c["other"])
    lines.append("\u4f86\u6e90\uff1a\u516c\u958b\u8cc7\u8a0a\u89c0\u6e2c\u7ad9 t100sb02_1\uff08conf_calendar.py \u81ea\u52d5\u5beb\u5165\uff09")
    children = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rt(x)}} for x in lines]
    return {"parent": {"database_id": event_db()}, "properties": props, "children": children}


def short_line(c, info):
    d = c["date"]
    mark = "\u2b50" if info.get("holding") else "\u30fb"
    tag = "\uff08\u53d7\u9080\u8ad6\u58c7\uff09" if is_invited(c) else ""
    return "%s %s %s\uff1a%d/%d\uff08%s\uff09%s\uff5c%s%s" % (mark, c["name"] or info.get("name", ""), c["code"], d.month, d.day,
                                         WEEKDAY[d.weekday()], c["time"] or "\u6642\u9593\u672a\u5b9a", (c["place"] or "")[:20], tag)


# ---------------------------------------------------------------- core
def select_new(confs, codes, have, today):
    """Conferences of interest, not yet in the calendar, dated from today-7 on; one per (code, date)."""
    seen, skipped = {}, {"old": 0, "known": 0, "nodate": 0}
    for c in confs:
        info = codes.get(c["code"])
        if not info:
            continue
        if c["date"] is None:
            skipped["nodate"] += 1
            continue
        if c["date"] < today - timedelta(days=KEEP_PAST_DAYS):
            skipped["old"] += 1
            continue
        k = (c["code"], c["date"].isoformat())
        if k in have:
            skipped["known"] += 1
            continue
        if k in seen:                 # two rows the same day: keep the company's own call over a broker forum
            skipped["known"] += 1
            if is_invited(seen[k][0]) and not is_invited(c):
                seen[k] = (c, info)
            continue
        seen[k] = (c, info)
    out = sorted(seen.values(), key=lambda x: (not x[1].get("holding"), x[0]["date"], x[0]["code"]))
    return out, skipped


def collect(today):
    codes, notes = interest_codes()
    have, n_existing = existing_events()
    confs, errors, nreq = fetch_mops(today)
    new, skipped = select_new(confs, codes, have, today)
    return {"codes": codes, "notes": notes, "have": have, "n_existing": n_existing, "confs": confs,
            "errors": errors, "nreq": nreq, "new": new, "skipped": skipped}


def acquire_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return lock


def summary_line(s):
    holding = sum(1 for v in s["codes"].values() if v["holding"])
    return ("codes %d (holding %d)  existing \u6cd5\u8aaa\u6703 rows %d  MOPS requests %d errors %d  conferences %d  "
            "new %d  skipped old %d known %d nodate %d" % (
                len(s["codes"]), holding, s["n_existing"], s["nreq"], len(s["errors"]), len(s["confs"]),
                len(s["new"]), s["skipped"]["old"], s["skipped"]["known"], s["skipped"]["nodate"]))


# ---------------------------------------------------------------- subcommands
def cmd_check():
    problems = []
    try:
        load_secrets()
        for k in ("notion_key", "telegram_bot_token", "telegram_dm"):
            print("secret %-28s %s" % (k, "ok" if SECRETS.get(k) else "MISSING"))
            if not SECRETS.get(k):
                problems.append("secret " + k)
        for k in ("notion_event_calendar_db", "notion_stock_tracking_db"):
            print("secret %-28s %s" % (k, "set" if SECRETS.get(k) else "default"))
    except Exception as e:
        print("secrets.json: %s" % type(e).__name__)
        problems.append("secrets.json")
    for label, db, required in (("calendar DB", event_db(), True), ("tracking DB", tracking_db(), True),
                                ("monitor DB", MONITOR_DB, False), ("ladder DB", LADDER_DB, False)):
        code, _ = notion("GET", "/databases/%s" % db)
        print("notion %-12s HTTP %s%s" % (label, code, "" if required else " (optional)"))
        if required and code != 200:
            problems.append("%s HTTP %s" % (label, code))
    y, m = months_to_query(today_tpe())[0]
    try:
        rows = parse_conferences(mops_post("sii", y, m))
        print("MOPS sii %d/%02d: %d rows, first %s" % (y, m, len(rows),
                                                     (rows[0]["code"] + " " + rows[0]["date_raw"]) if rows else "-"))
    except MopsError as e:
        print("MOPS: %s" % e)
        problems.append("MOPS")
    rc, txt, _ = sh(["crontab", "-l"])
    print("crontab: installed=%s" % (CRON_TAG in (txt if rc == 0 else "")))
    print("python: %s  version %s" % (sys.version.split()[0], VERSION))
    if problems:
        print("problems: " + "; ".join(problems))
        print("RESULT=CHECK_FAIL")
        return 1
    print("RESULT=CHECK_OK")
    return 0


def cmd_dry():
    load_secrets()
    s = collect(today_tpe())
    for n in s["notes"]:
        print("note: " + n)
    for e in s["errors"]:
        print("MOPS error: " + e)
    print(summary_line(s))
    for c, info in s["new"][:25]:
        print("  would add " + short_line(c, info))
    if len(s["new"]) > 25:
        print("  ... %d more" % (len(s["new"]) - 25))
    print("RESULT=DRY_OK new=%d errors=%d" % (len(s["new"]), len(s["errors"])))
    return 0


def cmd_run():
    lock = acquire_lock()
    if lock is None:
        print("RESULT=LOCKED")
        return 0
    try:
        return _run()
    finally:
        lock.close()


def _run():
    load_secrets()
    today = today_tpe()
    st = load_state()
    s = collect(today)
    for n in s["notes"]:
        print("note: " + n)
    for e in s["errors"]:
        print("MOPS error: " + e)
    print(summary_line(s))
    all_failed = s["nreq"] > 0 and len(s["errors"]) == s["nreq"]
    st["mops_fail_streak"] = (st.get("mops_fail_streak", 0) + 1) if all_failed else 0
    created, fails, lines = 0, [], []
    for c, info in s["new"]:
        code, js = notion("POST", "/pages", event_payload(c, info))
        if code == 200:
            created += 1
            lines.append(short_line(c, info))
            print("  added " + short_line(c, info))
        else:
            fails.append("%s %s HTTP %s %s" % (c["code"], c["date"], code, mask(str(js.get("message", "")))[:80]))
        time.sleep(0.34)
    st["last_run"] = now_utc().isoformat(timespec="seconds")
    st["last_result"] = {"new": created, "fails": len(fails), "errors": len(s["errors"])}
    save_state(st)
    if lines:
        upcoming = [x for x in lines]
        telegram("\U0001f4c5 \u65b0\u6cd5\u8aaa\uff08\u516c\u958b\u8cc7\u8a0a\u89c0\u6e2c\u7ad9\uff0c\u5df2\u5beb\u5165\u6cd5\u8aaa\u6703\u884c\u4e8b\u66c6\uff09\n" + "\n".join(upcoming[:15])
                 + ("\n\u2026\u5171 %d \u7b46" % len(upcoming) if len(upcoming) > 15 else ""))
    alerts = []
    if fails:
        alerts.append("Notion \u5beb\u5165\u5931\u6557 %d \u7b46\uff1a%s" % (len(fails), "\uff1b".join(fails[:3])))
    if st["mops_fail_streak"] >= 2:
        alerts.append("\u516c\u958b\u8cc7\u8a0a\u89c0\u6e2c\u7ad9\u9023\u7e8c %d \u6b21\u8b80\u4e0d\u5230\uff1a%s" % (st["mops_fail_streak"], "\uff1b".join(s["errors"][:2])))
    if alerts:
        telegram("\u26a0\ufe0f \u6cd5\u8aaa\u884c\u4e8b\u66c6\uff08conf_calendar\uff09\n" + "\n".join("\u30fb" + a for a in alerts))
    if fails:
        result = "RUN_WRITE_FAIL"
    elif all_failed:
        result = "RUN_MOPS_FAIL"
    elif s["errors"]:
        result = "RUN_PARTIAL"
    else:
        result = "RUN_OK"
    print("RESULT=%s new=%d fails=%d mops_errors=%d streak=%d" % (result, created, len(fails), len(s["errors"]),
                                                                  st["mops_fail_streak"]))
    return 0 if result == "RUN_OK" else 1


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
    if CRON_TAG in old:
        print(sync_repo_crontab())
        print("RESULT=INSTALL_ALREADY")
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    bak = STATE_DIR / ("crontab.bak_conf_calendar_" + now_utc().strftime("%Y%m%d%H%M%S"))
    bak.write_text(old, encoding="utf-8")
    new = (old.rstrip("\n") + "\n" if old.strip() else "") + CRON_COMMENT + "\n" + CRON_LINE + "\n"
    rc, _, err = sh(["crontab", "-"], stdin_text=new)
    rc2, after, _ = sh(["crontab", "-l"])

    def norm(t):
        return set(x.strip() for x in t.splitlines() if x.strip())
    added = norm(after) - norm(old)
    removed = norm(old) - norm(after)
    if rc != 0 or rc2 != 0 or removed or added != {CRON_COMMENT, CRON_LINE}:
        sh(["crontab", str(bak)])
        print("verify failed: rc=%s rc2=%s added=%d removed=%d -> restored %s" % (
            rc, rc2, len(added), len(removed), bak.name))
        print("RESULT=INSTALL_FAIL")
        return 1
    print("crontab backup: " + str(bak))
    print("added: " + CRON_LINE)
    print(sync_repo_crontab())
    print("RESULT=INSTALL_OK")
    return 0


def sync_repo_crontab():
    repo_file = WS / "cron" / "crontab.txt"
    if not repo_file.exists():
        return "repo crontab.txt missing"
    rtxt = repo_file.read_text(encoding="utf-8")
    if CRON_TAG in rtxt:
        return "repo crontab.txt already has the line"
    repo_file.write_text(rtxt.rstrip("\n") + "\n\n" + CRON_COMMENT + "\n" + CRON_LINE + "\n", encoding="utf-8")
    return "repo crontab.txt appended"


def git(*args, timeout=60):
    return sh(["git", "-C", str(WS)] + list(args), timeout=timeout)


def cmd_commit(code):
    if not passcode_ok(code):
        print("RESULT=COMMIT_DENIED")
        return 3
    files = ["scripts/conf_calendar.py", "cron/crontab.txt"]
    rc, t, _ = git("status", "-sb", "--untracked-files=no")
    first = t.splitlines()[0] if t else ""
    ahead_before = int(re.search(r"ahead (\d+)", first).group(1)) if "ahead" in first else 0
    rc, _, err = git("add", "--", *files)
    if rc != 0:
        print("git add failed: " + mask(err.strip())[:200])
        print("RESULT=COMMIT_FAIL")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    msgf = STATE_DIR / "conf_calendar_commit_msg.txt"
    msgf.write_text(
        "D0-1 conf_calendar: MOPS investor conferences into the calendar DB before they happen\n\n"
        "- scripts/conf_calendar.py: check / dry / run / install / commit (zero LLM)\n"
        "- MOPS t100sb02_1 listed + OTC, this month and next; holdings, tracked stocks, valuation cards\n"
        "- cron/crontab.txt: weekdays 01:10 UTC (09:10 Taipei)\n\n"
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
    print("[START] conf_calendar %s %s utc=%s" % (VERSION, cmd, now_utc().strftime("%Y-%m-%d %H:%M")))
    if cmd == "check":
        return cmd_check()
    if cmd == "dry":
        return cmd_dry()
    if cmd == "run":
        return cmd_run()
    if cmd == "install":
        return cmd_install(arg)
    if cmd == "commit":
        return cmd_commit(arg)
    print("usage: conf_calendar.py check|dry|run|install CODE|commit CODE")
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
            telegram("\U0001f534 conf_calendar CRASH\uff08%s\uff09\uff1a%s: %s" % (
                sys.argv[1] if len(sys.argv) > 1 else "check", type(exc).__name__, mask(str(exc))[:300]))
        except Exception:
            pass
        print("RESULT=CRASH")
        sys.exit(2)
