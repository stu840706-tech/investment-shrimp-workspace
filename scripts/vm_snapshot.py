#!/usr/bin/env python3
"""vm_snapshot.py (A2, 2026-10-24) - read-only nightly VM health snapshot -> Notion.

Subcommands (no double-dash flags on purpose: Telegram turns -- into a dash):
  check          read-only: secrets keys, Notion access, crontab, systemd, git, log paths
  dry            collect everything and print a summary; writes nothing
  run            collect, replace today's row in the Notion "VM snapshot" DB, save own state
  install CODE   add the daily crontab line (passcode required) and append it to cron/crontab.txt
  commit CODE    git add + commit (+ push when nothing else is unpushed) the two files above

The last output line is always RESULT=...
check/dry/run never modify the VM; they only write ~/.vm_snapshot/ (own state).
Telegram is sent only on CRASH, Notion write failure, or FAIL-level findings.
Secrets are never printed: known secret values and token patterns are masked everywhere.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERSION = "1.1"
HOME = Path.home()
WS = HOME / ".openclaw" / "workspace"
SECRETS_FILE = WS / "config" / "secrets.json"
STATE_DIR = HOME / ".vm_snapshot"
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "lock"
SNAP_DB = "37d778d4-2fef-4d9e-b3cf-c88ad1e200fa"
NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
TPE = timezone(timedelta(hours=8))
PASS_SHA256 = "32db2631f45612ce36e8a25915917b83983e39473b5dd7d80f87c19efa76d1f5"
PY = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
CRON_TAG = "scripts/vm_snapshot.py run"
CRON_COMMENT = "# A2 vm_snapshot: daily 07:35 Taipei (23:35 UTC) -> Notion VM snapshot DB"
CRON_LINE = ("35 23 * * * cd " + str(WS) + " && " + PY
             + " scripts/vm_snapshot.py run >> /tmp/vm_snapshot.log 2>&1")
KEEP_DAYS = 40

# (name, database id, expectation) expectation: trading = rows on weekday evenings,
# daily = rows every day (missing row means the system broke), any = no expectation
MONITORED_DBS = [
    ("\u6bcf\u65e5\u6383\u63cf\u7d50\u679c", "34e226f5-a398-816e-93ca-c2f0d5a2456a", "trading"),
    ("\u6bcf\u65e5 Top N", "bea3f040-7da1-4b6b-8acc-63f0b8e4f453", "trading"),
    ("\u6bcf\u65e5 Dashboard", "dc9ca081-55a3-4726-b2d4-da9da67fcba5", "trading"),
    ("\u5238\u5546\u65e5\u6458", "5129cfe1-911f-453b-9b97-ea7b4df8f5e7", "daily"),
    ("\u5238\u5546\u500b\u80a1\u5831\u544a", "34e226f5-a398-81a0-b22d-fea135a192fd", "any"),
    ("\u5238\u5546\u7522\u696d\u5831\u544a", "34e226f5-a398-81fa-8c7e-ea8d8b32de67", "any"),
    ("\u6cd5\u8aaa\u6703 Memo", "3a4226f5-a398-814a-869e-fb05bbd58ae8", "any"),
    ("\u6cd5\u8aaa\u6703\u884c\u4e8b\u66c6", "34e226f5-a398-81ea-8a99-ecd61b9e8795", "any"),
    ("Outcome \u7d00\u9304", "34e226f5-a398-81fe-b0f3-d029abc41d2a", "any"),
]

# logs that are not in the user crontab: (name, path, max age in hours or None)
EXTRA_LOGS = [
    ("broker_batch_bg", "/tmp/broker_batch_bg.log", None),
    ("coldtrack_monthly", str(HOME / "coldtrack" / "monthly" / "monthly.log"), 24 * 32),
]

SECRETS = {}
SECRET_VALUES = []
_MASK_RES = [re.compile(p) for p in (
    r"secret_[A-Za-z0-9]{20,}",
    r"ntn_[A-Za-z0-9]{20,}",
    r"\b\d{8,11}:[A-Za-z0-9_-]{30,}",
    r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{5,}",
    r"\bsk-[A-Za-z0-9_-]{16,}",
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"Bearer\s+[A-Za-z0-9._-]{16,}",
    r"\bBSA[A-Za-z0-9_-]{16,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
)]


# ---------------------------------------------------------------- basics
def now_utc():
    return datetime.now(timezone.utc)


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


def sh(args, timeout=30, env=None, stdin_text=None):
    """Run a command without a shell. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env=env, input=stdin_text)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", "not found: " + args[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def user_env():
    env = dict(os.environ)
    uid = os.getuid()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/%d" % uid)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/%d/bus" % uid)
    return env


def http_json(method, url, headers, body=None, timeout=30):
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
    for attempt in (1, 2):
        try:
            code, js = http_json(method, NOTION_API + path, hdr, body)
        except Exception as e:  # timeout / URLError
            if attempt == 2:
                return 0, {"message": "%s: %s" % (type(e).__name__, e)}
            time.sleep(15)
            continue
        if code in (429, 500, 502, 503, 504) and attempt == 1:
            time.sleep(15)
            continue
        return code, js
    return 0, {"message": "unreachable"}


def telegram(text):
    try:
        url = "https://api.telegram.org/bot" + SECRETS["telegram_bot_token"] + "/sendMessage"
        code, _ = http_json("POST", url, {"Content-Type": "application/json"},
                            {"chat_id": SECRETS["telegram_dm"], "text": mask(text)[:3500]},
                            timeout=20)
        return code == 200
    except Exception:
        return False


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def passcode_ok(code):
    return hashlib.sha256((code or "").strip().encode("utf-8")).hexdigest() == PASS_SHA256


# ---------------------------------------------------------------- collectors
def parse_crontab(text):
    jobs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6 or not (parts[0][0].isdigit() or parts[0][0] == "*"):
            continue
        m, h, dom, mon, dow, cmd = parts
        if CRON_TAG in cmd:
            continue
        log = None
        mm = re.search(r">>?\s*(\S+)", cmd)
        if mm:
            log = mm.group(1)
            if log.startswith("~"):
                log = str(HOME) + log[1:]
        if log:
            name = Path(log).stem
        else:
            sm = re.search(r"([\w.-]+\.(?:py|sh))", cmd)
            name = sm.group(1) if sm else cmd[:30]
        if dom == "*" and dow == "*":
            max_age = 26
        elif dow in ("1-5", "1,2,3,4,5", "mon-fri"):
            max_age = 74
        elif re.fullmatch(r"\d", dow):
            max_age = 170
        elif dom != "*":
            max_age = 24 * 32
        else:
            max_age = 26
        jobs.append({"name": name, "sched": " ".join([m, h, dom, mon, dow]),
                     "log": log, "max_age_h": max_age})
    return jobs


def inspect_log(path, max_age_h, now):
    p = Path(path)
    if not p.exists():
        return {"status": "MISSING"}
    st = p.stat()
    age_h = (now.timestamp() - st.st_mtime) / 3600.0
    with open(p, "rb") as f:
        f.seek(max(0, st.st_size - 65536))
        data = f.read().decode("utf-8", "replace")
    lines = [ln.rstrip() for ln in data.splitlines() if ln.strip()]
    tail = lines[-200:]
    start = None
    for i, ln in enumerate(tail):
        if "[START]" in ln or "START" in ln.split()[:2]:
            start = i
    recent = tail[start:] if start is not None else tail[-60:]
    results = [ln.strip() for ln in tail if "RESULT=" in ln]
    last_result = results[-1] if results else ""
    sums = [ln.strip() for ln in recent if "[SUMMARY]" in ln]
    last_summary = sums[-1] if sums else ""
    errs = sum(1 for ln in recent if ("Traceback" in ln or "[ERROR]" in ln or "FATAL" in ln))
    # judge only the first word after RESULT= (#106: "notion_fail=0" in the counters is not a failure)
    up = last_result.split("RESULT=", 1)[1].split()[0].upper() if "RESULT=" in last_result and last_result.split("RESULT=", 1)[1].split() else ""
    if max_age_h and age_h > max_age_h:
        status = "STALE"
    elif last_result and any(k in up for k in ("CRASH", "FAIL", "ABORT", "ERROR")):
        status = "FAIL"
    elif last_result and any(k in up for k in ("PARTIAL", "WARN")):
        status = "WARN"
    elif errs > 0 and not last_result:
        status = "WARN"
    else:
        status = "OK"
    mt = datetime.fromtimestamp(st.st_mtime, TPE).strftime("%m-%d %H:%M")
    return {"status": status, "age_h": round(age_h, 1), "mtime_tpe": mt, "size": st.st_size,
            "last_result": mask(last_result)[:160], "summary": mask(last_summary)[:200], "errors": errs,
            "last_lines": [mask(x)[:160] for x in lines[-2:]]}


def collect_schedulers(now):
    out = {}
    rc, txt, err = sh(["crontab", "-l"])
    if rc != 0 and "no crontab" not in (err or ""):
        out["crontab_error"] = mask(err.strip())[:200]
        txt = ""
    out["crontab_text"] = txt
    live = set(ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#"))
    repo_file = WS / "cron" / "crontab.txt"
    if repo_file.exists():
        rtxt = repo_file.read_text(encoding="utf-8", errors="replace")
        repo = set(ln.strip() for ln in rtxt.splitlines() if ln.strip() and not ln.strip().startswith("#"))
        out["only_live"] = sorted(mask(x) for x in live - repo)
        out["only_repo"] = sorted(mask(x) for x in repo - live)
    else:
        out["only_live"], out["only_repo"] = [], []
        out["repo_crontab_missing"] = True
    out["jobs"] = parse_crontab(txt)
    env = user_env()
    rc, t, e = sh(["systemctl", "--user", "list-timers", "--all", "--no-pager", "--no-legend"], env=env)
    out["timers"] = [mask(x)[:200] for x in t.splitlines() if x.strip()] if rc == 0 else ["error: " + mask(e.strip())[:200]]
    rc, t, e = sh(["systemctl", "--user", "--failed", "--no-pager", "--no-legend"], env=env)
    out["failed_units"] = [mask(x)[:200] for x in t.splitlines() if x.strip()] if rc == 0 else []
    if rc != 0:
        out["failed_units_error"] = mask(e.strip())[:200]
    rc, t, e = sh(["systemctl", "--user", "is-active", "openclaw-gateway"], env=env)
    out["gateway"] = t.strip()[:40] if t.strip() else "unknown: " + mask(e.strip())[:80]
    return out


def collect_logs(jobs, now):
    rows = []
    seen = set()
    for j in jobs:
        if j["log"]:
            if j["log"] in seen:
                continue
            seen.add(j["log"])
        info = inspect_log(j["log"], j["max_age_h"], now) if j["log"] else {"status": "NOLOG"}
        info.update({"name": j["name"], "sched": j.get("sched", "-"), "log": j["log"]})
        rows.append(info)
    for name, path, max_age in EXTRA_LOGS:
        info = inspect_log(path, max_age, now)
        if info["status"] == "MISSING":
            info["status"] = "ABSENT"  # optional logs: absence is normal
        info.update({"name": name, "sched": "-", "log": path})
        rows.append(info)
    return rows


def git(*args, timeout=60):
    return sh(["git", "-C", str(WS)] + list(args), timeout=timeout)


def collect_git(now, prev_md5):
    out = {}
    rc, t, _ = git("rev-parse", "--short", "HEAD")
    out["head"] = t.strip() if rc == 0 else "?"
    rc, t, _ = git("log", "-1", "--format=%cI %s")
    out["head_info"] = mask(t.strip())[:160]
    rc, t, _ = git("status", "-sb", "--untracked-files=no")
    lines = t.splitlines()
    out["branch_line"] = lines[0] if lines else "?"
    out["modified"] = [mask(x.strip())[:120] for x in lines[1:]]
    rc, t, _ = git("status", "--porcelain", "--untracked-files=normal")
    out["untracked"] = sum(1 for x in t.splitlines() if x.startswith("??"))
    rc, t, _ = git("ls-files")
    tracked = [x for x in t.splitlines() if x]
    md5 = {}
    for rel in tracked:
        if rel.endswith((".py", ".sh")):
            fp = WS / rel
            try:
                md5[rel] = hashlib.md5(fp.read_bytes()).hexdigest()
            except Exception:
                pass
    out["md5"] = md5
    changed, added, removed = [], [], []
    if prev_md5:
        for rel, h in md5.items():
            if rel not in prev_md5:
                added.append(rel)
            elif prev_md5[rel] != h:
                changed.append("%s %s->%s" % (rel, prev_md5[rel][:8], h[:8]))
        removed = [r for r in prev_md5 if r not in md5]
    out["changed"], out["added"], out["removed"] = changed, added, removed
    recent = []
    cutoff = now.timestamp() - 24 * 3600
    for root, dirs, files in os.walk(WS):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "books", "node_modules")]
        for fn in files:
            if fn.endswith((".py", ".sh")):
                fp = Path(root) / fn
                try:
                    mt = fp.stat().st_mtime
                except Exception:
                    continue
                if mt >= cutoff:
                    rel = str(fp.relative_to(WS))
                    recent.append("%s %s" % (datetime.fromtimestamp(mt, TPE).strftime("%m-%d %H:%M"), rel))
    out["recent_mtime"] = sorted(recent)[-30:]
    out["tracked"] = tracked
    return out


def secret_scan(tracked):
    hits = []
    for rel in tracked:
        if rel.startswith("books/"):
            continue
        fp = WS / rel
        try:
            if fp.stat().st_size > 2_000_000:
                continue
            data = fp.read_bytes()
        except Exception:
            continue
        if b"\x00" in data[:4096]:
            continue
        txt = data.decode("utf-8", "replace")
        found = any(v in txt for v in SECRET_VALUES)
        if not found:
            for r in _MASK_RES:
                m = r.search(txt)
                if m and not ("process.env" in txt[max(0, m.start() - 40):m.start()]):
                    found = True
                    break
        if found:
            hits.append(rel)
    return hits


SECRET_FILES = ("config/secrets.json", ".env")


def unignored_secret_files():
    """Secret files that exist in the workspace but are not git-ignored (public repo!)."""
    bad = []
    for rel in SECRET_FILES:
        if (WS / rel).exists():
            rc, _, _ = git("check-ignore", "-q", rel)
            if rc != 0:
                bad.append(rel)
    return bad


def count_created(db_id, since_iso, max_pages=40):
    body = {"filter": {"timestamp": "created_time", "created_time": {"on_or_after": since_iso}},
            "page_size": 100}
    total, pages = 0, 0
    while True:
        code, js = notion("POST", "/databases/%s/query" % db_id, body)
        if code != 200:
            return None, "HTTP %s %s" % (code, mask(js.get("message", ""))[:80])
        total += len(js.get("results", []))
        pages += 1
        if not js.get("has_more") or pages >= max_pages:
            return total, ("capped" if js.get("has_more") else "")
        body["start_cursor"] = js.get("next_cursor")


def collect_counts(now, history):
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    prev_day = (now.astimezone(TPE) - timedelta(days=1))
    out = []
    for name, db_id, expect in MONITORED_DBS:
        n, note = count_created(db_id, since)
        hist = [c for c in history.get(db_id, []) if isinstance(c, int) and c > 0][-5:]
        med = sorted(hist)[len(hist) // 2] if len(hist) >= 3 else None
        flag = ""
        if n is None:
            flag = "ERR"
        elif expect == "daily" and n == 0:
            flag = "ZERO"
        elif expect == "trading" and n == 0 and prev_day.weekday() < 5:
            flag = "ZERO?"
        elif med and n > 0 and expect in ("trading", "daily") and n < 0.7 * med:
            flag = "LOW"
        out.append({"name": name, "db": db_id, "expect": expect, "count": n,
                    "median5": med, "flag": flag, "note": note})
    return out


def collect_system():
    out = {}
    try:
        up = float(Path("/proc/uptime").read_text().split()[0])
        out["uptime_d"] = round(up / 86400, 1)
    except Exception:
        out["uptime_d"] = None
    try:
        out["load"] = " ".join(Path("/proc/loadavg").read_text().split()[:3])
    except Exception:
        out["load"] = "?"
    try:
        mi = {}
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, v = ln.split(":", 1)
            mi[k] = int(v.strip().split()[0])
        out["mem_avail_pct"] = round(100.0 * mi["MemAvailable"] / mi["MemTotal"], 1)
        out["mem_total_gb"] = round(mi["MemTotal"] / 1048576.0, 1)
    except Exception:
        out["mem_avail_pct"] = None
    for label, path in (("disk_root", "/"), ("disk_tmp", "/tmp")):
        try:
            du = shutil.disk_usage(path)
            out[label] = {"used_pct": round(100.0 * du.used / du.total, 1),
                          "free_gb": round(du.free / 1073741824.0, 1)}
        except Exception:
            out[label] = None
    out["python"] = sys.version.split()[0]
    return out


def collect(now, state, with_counts=True):
    snap = {"version": VERSION, "time_utc": now.strftime("%Y-%m-%d %H:%M"),
            "time_tpe": now.astimezone(TPE).strftime("%Y-%m-%d %H:%M")}
    snap["system"] = collect_system()
    sched = collect_schedulers(now)
    snap["sched"] = {k: v for k, v in sched.items() if k not in ("crontab_text", "jobs")}
    snap["logs"] = collect_logs(sched["jobs"], now)
    g = collect_git(now, state.get("md5"))
    snap["git"] = {k: v for k, v in g.items() if k not in ("md5", "tracked")}
    snap["_md5"] = g["md5"]
    snap["secret_hits"] = secret_scan(g["tracked"])
    snap["unignored_secrets"] = unignored_secret_files()
    snap["counts"] = collect_counts(now, state.get("counts", {})) if with_counts else []
    snap["findings"] = judge(snap)
    levels = [f[0] for f in snap["findings"]]
    snap["result"] = "FAIL" if "FAIL" in levels else ("WARN" if "WARN" in levels else "OK")
    return snap


def judge(s):
    f = []
    g = s["sched"]
    gw = g.get("gateway", "")
    if gw in ("inactive", "failed", "activating", "deactivating"):
        f.append(("FAIL", "openclaw-gateway \u72c0\u614b\uff1a%s" % gw))
    elif gw != "active":
        f.append(("WARN", "\u7121\u6cd5\u5224\u65b7 openclaw-gateway \u72c0\u614b\uff1a%s" % gw))
    for u in g.get("failed_units", []):
        f.append(("FAIL", "systemd \u5931\u6557\u55ae\u5143\uff1a%s" % u[:120]))
    if g.get("crontab_error"):
        f.append(("WARN", "\u8b80\u4e0d\u5230 crontab\uff1a%s" % g["crontab_error"]))
    for x in g.get("only_live", []):
        f.append(("WARN", "crontab \u6709\u3001\u6b63\u672c cron/crontab.txt \u6c92\u6709\uff1a%s" % x[:150]))
    for x in g.get("only_repo", []):
        f.append(("WARN", "\u6b63\u672c\u6709\u3001\u7dda\u4e0a crontab \u6c92\u6709\uff1a%s" % x[:150]))
    for r in s["logs"]:
        st = r["status"]
        if st == "FAIL":
            f.append(("FAIL", "%s \u5931\u6557\uff1a%s" % (r["name"], r.get("last_result", ""))))
        elif st == "STALE":
            f.append(("WARN", "%s \u8d85\u904e %s \u5c0f\u6642\u6c92\u66f4\u65b0\uff08\u6700\u5f8c %s\uff09" % (r["name"], int(r["age_h"]), r.get("mtime_tpe"))))
        elif st == "WARN":
            f.append(("WARN", "%s\uff1a%s\uff08\u932f\u8aa4\u884c %s\uff09" % (r["name"], r.get("last_result") or "\u7121 RESULT \u884c", r.get("errors"))))
        elif st == "MISSING":
            f.append(("WARN", "%s \u7684 log \u4e0d\u5b58\u5728\uff1a%s" % (r["name"], r.get("log"))))
    if s["secret_hits"]:
        f.append(("FAIL", "git \u8ffd\u8e64\u6a94\u7591\u4f3c\u542b\u91d1\u9470\uff08repo \u662f\u516c\u958b\u7684\uff09\uff1a%s" % ", ".join(s["secret_hits"][:10])))
    for rel in s.get("unignored_secrets", []):
        f.append(("WARN", "%s \u6c92\u88ab .gitignore \u64cb\u4f4f\uff08repo \u516c\u958b\uff0cgit add -A \u5c31\u6703\u5916\u6d29\uff09" % rel))
    gi = s["git"]
    if gi.get("modified"):
        f.append(("WARN", "workspace \u6709\u672a commit \u7684\u4fee\u6539 %d \u6a94\uff1a%s" % (len(gi["modified"]), "; ".join(gi["modified"][:6]))))
    if gi.get("changed") or gi.get("added") or gi.get("removed"):
        f.append(("WARN", "\u8173\u672c md5 \u548c\u4e0a\u6b21\u5feb\u7167\u4e0d\u540c\uff1a\u6539 %d\u3001\u589e %d\u3001\u522a %d" % (len(gi["changed"]), len(gi["added"]), len(gi["removed"]))))
    for c in s["counts"]:
        if c["flag"] == "ERR":
            f.append(("WARN", "Notion %s \u67e5\u8a62\u5931\u6557\uff1a%s" % (c["name"], c["note"])))
        elif c["flag"] == "ZERO":
            f.append(("FAIL", "Notion %s \u8fd1 24 \u5c0f\u6642 0 \u7b46\uff08\u9019\u500b DB \u6bcf\u5929\u90fd\u8a72\u6709\u5217\uff09" % c["name"]))
        elif c["flag"] == "ZERO?":
            f.append(("WARN", "Notion %s \u8fd1 24 \u5c0f\u6642 0 \u7b46\uff08\u82e5\u6628\u5929\u4f11\u5e02\u5247\u6b63\u5e38\uff09" % c["name"]))
        elif c["flag"] == "LOW":
            f.append(("WARN", "Notion %s \u53ea\u6709 %d \u7b46\uff0c\u8fd1 5 \u6b21\u4e2d\u4f4d\u6578 %d" % (c["name"], c["count"], c["median5"])))
    sy = s["system"]
    for label in ("disk_root", "disk_tmp"):
        d = sy.get(label)
        if d and d["used_pct"] >= 85:
            f.append(("WARN", "%s \u4f7f\u7528 %.1f%%\uff08\u5269 %.1f GB\uff09" % (label, d["used_pct"], d["free_gb"])))
    if sy.get("mem_avail_pct") is not None and sy["mem_avail_pct"] < 10:
        f.append(("WARN", "\u53ef\u7528\u8a18\u61b6\u9ad4\u53ea\u5269 %.1f%%" % sy["mem_avail_pct"]))
    return f


# ---------------------------------------------------------------- rendering
def lines_jobs(s):
    out = []
    for r in s["logs"]:
        if r["status"] in ("MISSING", "ABSENT", "NOLOG"):
            out.append("%-6s %-22s %s" % (r["status"], r["name"][:22], r.get("log") or ""))
            continue
        out.append("%-6s %-22s %s age=%sh %s" % (r["status"], r["name"][:22], r.get("mtime_tpe"),
                                                  r.get("age_h"), r.get("last_result") or "-"))
        if r.get("summary"):
            out.append("       SUMMARY " + r["summary"])
    return out


def lines_counts(s):
    out = []
    for c in s["counts"]:
        out.append("%-5s %-14s %s (median5=%s) %s" % (c["flag"] or "ok", c["name"],
                                                     "?" if c["count"] is None else c["count"],
                                                     c["median5"], c["note"]))
    return out


def lines_git(s):
    g = s["git"]
    out = ["HEAD %s  %s" % (g.get("head"), g.get("head_info")), g.get("branch_line", ""),
           "modified %d, untracked %d" % (len(g.get("modified", [])), g.get("untracked", 0))]
    out += ["  M " + x for x in g.get("modified", [])[:20]]
    out += ["  changed " + x for x in g.get("changed", [])[:20]]
    out += ["  added " + x for x in g.get("added", [])[:20]]
    out += ["  removed " + x for x in g.get("removed", [])[:20]]
    if g.get("recent_mtime"):
        out.append("py/sh files modified in last 24h:")
        out += ["  " + x for x in g["recent_mtime"]]
    return out


def lines_sched(s):
    g = s["sched"]
    out = ["gateway: " + str(g.get("gateway"))]
    out.append("failed units: " + (", ".join(g.get("failed_units", [])) or "none"))
    out.append("crontab only-live: %d, only-repo: %d" % (len(g.get("only_live", [])), len(g.get("only_repo", []))))
    out.append("timers:")
    out += ["  " + x for x in g.get("timers", [])[:20]]
    return out


def lines_system(s):
    y = s["system"]
    return ["uptime %s d, load %s, mem avail %s%% of %s GB, python %s" % (
                y.get("uptime_d"), y.get("load"), y.get("mem_avail_pct"), y.get("mem_total_gb"), y.get("python")),
            "disk / %s, /tmp %s" % (y.get("disk_root"), y.get("disk_tmp"))]


def summary_text(s):
    head = "%s %s\uff08\u53f0\u5317 %s\uff0c%d \u9805\uff09" % (s["result"], "VM \u5feb\u7167", s["time_tpe"], len(s["findings"]))
    body = ["- [%s] %s" % (lv, tx) for lv, tx in s["findings"]] or ["- \u6c92\u6709\u7570\u5e38"]
    return mask("\n".join([head] + body))


def rt(text):
    return [{"type": "text", "text": {"content": text[:1990]}}]


def code_blocks(lines):
    blocks, buf = [], ""
    for ln in lines:
        ln = mask(ln)[:400]
        if len(buf) + len(ln) + 1 > 1900:
            blocks.append({"object": "block", "type": "code",
                           "code": {"language": "plain text", "rich_text": rt(buf)}})
            buf = ""
        buf += ln + "\n"
    if buf:
        blocks.append({"object": "block", "type": "code",
                       "code": {"language": "plain text", "rich_text": rt(buf)}})
    return blocks


def h2(text):
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": rt(text)}}


def bullet(text):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rt(mask(text)[:1990])}}


def build_blocks(s):
    b = [h2("\u767c\u73fe")]
    if s["findings"]:
        b += [bullet("[%s] %s" % (lv, tx)) for lv, tx in s["findings"][:60]]
    else:
        b.append(bullet("\u6c92\u6709\u7570\u5e38"))
    b.append(h2("\u6392\u7a0b\u8207 log\uff08\u72c0\u614b\u3001\u6700\u5f8c\u66f4\u65b0\u53f0\u5317\u6642\u9593\u3001RESULT\uff09"))
    b += code_blocks(lines_jobs(s))
    b.append(h2("Notion \u5beb\u5165\u7b46\u6578\uff08\u8fd1 24 \u5c0f\u6642\u65b0\u5efa\uff09"))
    b += code_blocks(lines_counts(s))
    b.append(h2("\u7a0b\u5f0f\u8207 git"))
    b += code_blocks(lines_git(s))
    b.append(h2("\u6392\u7a0b\u5668"))
    b += code_blocks(lines_sched(s))
    b.append(h2("\u7cfb\u7d71"))
    b += code_blocks(lines_system(s))
    b.append(h2("\u91d1\u9470\u6383\u63cf"))
    b.append(bullet("git \u8ffd\u8e64\u6a94\u7591\u4f3c\u542b\u91d1\u9470\uff1a" + (", ".join(s["secret_hits"]) if s["secret_hits"] else "0 \u6a94")))
    return b


# ---------------------------------------------------------------- notion write
def upsert_page(s):
    date_tpe = s["time_tpe"][:10]
    code, js = notion("POST", "/databases/%s/query" % SNAP_DB,
                      {"filter": {"property": "\u540d\u7a31", "title": {"equals": date_tpe}}, "page_size": 20})
    if code != 200:
        return None, "query HTTP %s %s" % (code, mask(js.get("message", ""))[:120])
    old_ids = [p["id"] for p in js.get("results", [])]
    blocks = build_blocks(s)
    props = {
        "\u540d\u7a31": {"title": rt(date_tpe)},
        "\u65e5\u671f": {"date": {"start": date_tpe}},
        "\u7d50\u679c": {"select": {"name": s["result"]}},
        "\u8b66\u544a\u6578": {"number": len(s["findings"])},
        "\u6458\u8981": {"rich_text": rt(summary_text(s))},
    }
    code, js = notion("POST", "/pages", {"parent": {"database_id": SNAP_DB},
                                          "properties": props, "children": blocks[:90]})
    if code != 200:
        return None, "create HTTP %s %s" % (code, mask(js.get("message", ""))[:160])
    page_id, url = js.get("id"), js.get("url", "")
    rest = blocks[90:]
    while rest:
        chunk, rest = rest[:90], rest[90:]
        code, js2 = notion("PATCH", "/blocks/%s/children" % page_id, {"children": chunk})
        if code != 200:
            return url, "append HTTP %s %s" % (code, mask(js2.get("message", ""))[:120])
    for pid in old_ids:
        notion("PATCH", "/pages/%s" % pid, {"archived": True})
    return url, ""


def save_state(s, state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    counts = state.get("counts", {})
    for c in s["counts"]:
        if isinstance(c["count"], int):
            counts.setdefault(c["db"], []).append(c["count"])
            counts[c["db"]] = counts[c["db"]][-10:]
    state = {"md5": s["_md5"], "counts": counts, "last_run_utc": s["time_utc"], "version": VERSION}
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=True, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)
    snap_copy = {k: v for k, v in s.items() if k != "_md5"}
    day = s["time_tpe"][:10].replace("-", "")
    (STATE_DIR / ("snap_%s.json" % day)).write_text(json.dumps(snap_copy, ensure_ascii=True, indent=1),
                                                     encoding="utf-8")
    cutoff = time.time() - KEEP_DAYS * 86400
    for fp in STATE_DIR.glob("snap_*.json"):
        if fp.stat().st_mtime < cutoff:
            fp.unlink()


# ---------------------------------------------------------------- subcommands
def cmd_check():
    problems = []
    try:
        load_secrets()
        for k in ("notion_key", "telegram_bot_token", "telegram_dm"):
            print("secret %-18s %s" % (k, "ok" if SECRETS.get(k) else "MISSING"))
            if not SECRETS.get(k):
                problems.append("secret " + k)
    except Exception as e:
        print("secrets.json: %s" % type(e).__name__)
        problems.append("secrets.json")
    code, js = notion("GET", "/databases/%s" % SNAP_DB)
    print("notion snapshot DB: HTTP %s" % code)
    if code != 200:
        problems.append("snapshot DB HTTP %s" % code)
    for name, db_id, _ in MONITORED_DBS:
        code, _ = notion("GET", "/databases/%s" % db_id)
        print("notion %-14s HTTP %s" % (name, code))
    rc, txt, err = sh(["crontab", "-l"])
    if rc != 0 and "no crontab" not in (err or ""):
        problems.append("crontab")
    print("crontab: rc=%s lines=%d vm_snapshot_installed=%s" % (
        rc, len([x for x in txt.splitlines() if x.strip()]), CRON_TAG in txt))
    rc, t, e = sh(["systemctl", "--user", "is-active", "openclaw-gateway"], env=user_env())
    print("gateway: %s" % (t.strip() or mask(e.strip())[:80]))
    rc, t, _ = git("rev-parse", "--short", "HEAD")
    print("git HEAD: %s" % (t.strip() if rc == 0 else "ERROR"))
    if rc != 0:
        problems.append("git")
    for j in parse_crontab(txt):
        print("log %-6s %s" % ("ok" if j["log"] and Path(j["log"]).exists() else "--", j["log"]))
    print("python: %s  state dir: %s" % (sys.version.split()[0], STATE_DIR))
    if problems:
        print("problems: " + "; ".join(problems))
        print("RESULT=CHECK_FAIL")
        return 1
    print("RESULT=CHECK_OK")
    return 0


def cmd_dry():
    load_secrets()
    now = now_utc()
    s = collect(now, load_state())
    print(summary_text(s))
    print("--- jobs")
    print("\n".join(mask(x) for x in lines_jobs(s)))
    print("--- notion counts (24h)")
    print("\n".join(lines_counts(s)))
    print("--- git")
    print("\n".join(mask(x) for x in lines_git(s)[:12]))
    print("blocks=%d" % len(build_blocks(s)))
    print("RESULT=DRY_OK result=%s findings=%d" % (s["result"], len(s["findings"])))
    return 0


def cmd_run():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("RESULT=LOCKED")
        return 0
    load_secrets()
    now = now_utc()
    state = load_state()
    s = collect(now, state)
    url, err = upsert_page(s)
    if err:
        print("notion write failed: " + err)
        telegram("\u26a0\ufe0f VM \u5feb\u7167\u5beb\u5165 Notion \u5931\u6557\uff1a%s\n\uff08\u5feb\u7167\u7d50\u679c %s\uff0c%d \u9805\uff09" % (err, s["result"], len(s["findings"])))
        print("RESULT=WRITE_FAIL")
        return 1
    save_state(s, state)
    if s["result"] == "FAIL":
        telegram("\U0001f534 VM \u5feb\u7167\u6709 FAIL \u9805\u76ee\n" + summary_text(s)[:3000] + "\n" + url)
    print(summary_text(s))
    print("page: " + url)
    print("RESULT=%s findings=%d" % (s["result"], len(s["findings"])))
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
    if CRON_TAG in old:
        print(sync_repo_crontab())
        print(ensure_gitignore())
        print("RESULT=INSTALL_ALREADY")
        return 0
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    bak = STATE_DIR / ("crontab.bak_" + now_utc().strftime("%Y%m%d%H%M%S"))
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
    print(ensure_gitignore())
    print("RESULT=INSTALL_OK")
    return 0


def ensure_gitignore():
    gi = WS / ".gitignore"
    txt = gi.read_text(encoding="utf-8") if gi.exists() else ""
    have = set(x.strip() for x in txt.splitlines())
    missing = [x for x in SECRET_FILES if x not in have]
    if not missing:
        return ".gitignore already lists secret files"
    gi.write_text(txt.rstrip("\n") + ("\n" if txt.strip() else "")
                  + "# secrets never go to git (repo is public)\n" + "\n".join(missing) + "\n",
                  encoding="utf-8")
    return ".gitignore appended: " + ", ".join(missing)


def sync_repo_crontab():
    repo_file = WS / "cron" / "crontab.txt"
    if not repo_file.exists():
        return "repo crontab.txt missing"
    rtxt = repo_file.read_text(encoding="utf-8")
    if CRON_TAG in rtxt:
        return "repo crontab.txt already has the line"
    repo_file.write_text(rtxt.rstrip("\n") + "\n\n" + CRON_COMMENT + "\n" + CRON_LINE + "\n",
                         encoding="utf-8")
    return "repo crontab.txt appended"


def cmd_commit(code):
    if not passcode_ok(code):
        print("RESULT=COMMIT_DENIED")
        return 3
    files = ["scripts/vm_snapshot.py", "cron/crontab.txt", ".gitignore"]
    rc, t, _ = git("status", "-sb", "--untracked-files=no")
    first = t.splitlines()[0] if t else ""
    ahead_before = int(re.search(r"ahead (\d+)", first).group(1)) if "ahead" in first else 0
    rc, _, err = git("add", "--", *files)
    if rc != 0:
        print("git add failed: " + mask(err.strip())[:200])
        print("RESULT=COMMIT_FAIL")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    msgf = STATE_DIR / "commit_msg.txt"
    msgf.write_text(
        "A2 vm_snapshot: nightly read-only VM health snapshot to Notion\n\n"
        "- scripts/vm_snapshot.py: check / dry / run / install / commit\n"
        "- cron/crontab.txt: daily 23:35 UTC (07:35 Taipei)\n"
        "- .gitignore: config/secrets.json and .env (repo is public)\n\n"
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
    print("[START] vm_snapshot %s %s utc=%s" % (VERSION, cmd, now_utc().strftime("%Y-%m-%d %H:%M")))
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
    print("usage: vm_snapshot.py check|dry|run|install CODE|commit CODE")
    print("RESULT=USAGE")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except SystemExit:
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        print(mask(tb))
        try:
            if not SECRETS:
                load_secrets()
            telegram("\U0001f534 vm_snapshot CRASH\uff1a%s: %s" % (type(exc).__name__, exc))
        except Exception:
            pass
        print("RESULT=CRASH")
        sys.exit(2)
