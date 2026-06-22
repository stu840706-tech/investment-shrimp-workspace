#!/usr/bin/env python3
import os, sqlite3, threading, time, hashlib, hmac, base64, logging
from flask import Flask, request, abort
import requests

def _load_token():
    v = os.environ.get("LINE_TOKEN")
    if v:
        return v.strip()
    with open(os.path.expanduser("~/.openclaw/line_token.txt")) as f:
        return f.read().strip()

LINE_TOKEN = _load_token()
LINE_SECRET = os.environ.get("LINE_SECRET", "")
INBOUND_DIR = "/tmp/broker_queue_inbound"
DB_PATH = os.path.expanduser("~/.openclaw/line_receiver.db")
LOG_PATH = os.path.expanduser("~/.openclaw/line_receiver.log")
PORT = 5000
DL_TIMEOUT = 60
MAX_ATTEMPTS = 4
ALLOWED_EXT = (".pdf", ".docx", ".doc", ".zip", ".txt")
MIN_TEXT_LEN = 40

logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("line_receiver")
app = Flask(__name__)
_wake = threading.Event()

def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS tasks(msg_id TEXT PRIMARY KEY, file_name TEXT, "
              "status TEXT DEFAULT 'PENDING', attempts INTEGER DEFAULT 0, error TEXT, created REAL, done REAL)")
    return c

def enqueue(msg_id, file_name):
    c = db()
    try:
        c.execute("INSERT OR IGNORE INTO tasks(msg_id,file_name,created) VALUES(?,?,?)",
                  (msg_id, file_name, time.time()))
        c.commit()
    finally:
        c.close()

def save_text_message(msg_id, text):
    text = (text or "").strip()
    if len(text) < MIN_TEXT_LEN:
        log.info("SKIP text(short) %s len=%d", msg_id, len(text))
        return False
    os.makedirs(INBOUND_DIR, exist_ok=True)
    dst = os.path.join(INBOUND_DIR, "line_text_" + str(msg_id) + ".txt")
    tmp = dst + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, dst)
    c = db()
    try:
        c.execute("INSERT OR IGNORE INTO tasks(msg_id,file_name,status,created,done) "
                  "VALUES(?,?, 'DONE', ?, ?)", (msg_id, os.path.basename(dst), time.time(), time.time()))
        c.commit()
    finally:
        c.close()
    log.info("DONE text %s len=%d", msg_id, len(text))
    return True

def safe_name(name):
    name = os.path.basename(str(name)).strip()
    return name or "unnamed.bin"

def dest_path(file_name, msg_id):
    base = safe_name(file_name)
    dst = os.path.join(INBOUND_DIR, base)
    if os.path.exists(dst):
        stem, ext = os.path.splitext(base)
        dst = os.path.join(INBOUND_DIR, stem + "_" + str(msg_id)[:8] + ext)
    return dst

@app.route("/line", methods=["POST"])
def line_hook():
    body = request.get_data()
    if LINE_SECRET:
        sig = request.headers.get("X-Line-Signature", "")
        mac = base64.b64encode(hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(mac, sig):
            log.warning("簽章不符，拒絕請求")
            abort(400)
    events = (request.get_json(force=True, silent=True) or {}).get("events", [])
    n = 0
    for ev in events:
        if ev.get("type") != "message":
            continue
        m = ev.get("message", {})
        mtype = m.get("type")
        msg_id = m.get("id")
        if not msg_id:
            continue
        if mtype == "file":
            fname = m.get("fileName") or (str(msg_id) + ".bin")
            enqueue(msg_id, fname); n += 1
        elif mtype == "text":
            if save_text_message(msg_id, m.get("text", "")):
                n += 1
    if n:
        _wake.set()
    return "OK", 200

@app.route("/", methods=["GET"])
def status():
    c = db()
    rows = c.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
    c.close()
    return "LINE receiver alive\n" + "\n".join(s + ": " + str(n) for s, n in rows) + "\n", 200

def download_one(msg_id, file_name):
    if not safe_name(file_name).lower().endswith(ALLOWED_EXT):
        return True, "skipped(ext)"
    os.makedirs(INBOUND_DIR, exist_ok=True)
    url = "https://api-data.line.me/v2/bot/message/" + str(msg_id) + "/content"
    headers = {"Authorization": "Bearer " + LINE_TOKEN}
    dst = dest_path(file_name, msg_id)
    tmp = dst + ".part"
    with requests.get(url, headers=headers, stream=True, timeout=DL_TIMEOUT) as r:
        if r.status_code in (404, 410):
            return False, "LINE " + str(r.status_code)
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
    os.replace(tmp, dst)
    return True, ""

def worker():
    while True:
        c = db()
        row = c.execute("SELECT msg_id,file_name,attempts FROM tasks WHERE status='PENDING' ORDER BY created LIMIT 1").fetchone()
        c.close()
        if not row:
            _wake.wait(timeout=30)
            _wake.clear()
            continue
        msg_id, file_name, attempts = row
        try:
            ok, msg = download_one(msg_id, file_name)
        except Exception as e:
            ok, msg = False, str(e)[:200]
        c = db()
        if ok:
            c.execute("UPDATE tasks SET status='DONE',done=?,error=? WHERE msg_id=?", (time.time(), msg, msg_id))
            log.info("DONE %s %s", file_name, msg or "")
        else:
            attempts += 1
            if attempts >= MAX_ATTEMPTS:
                c.execute("UPDATE tasks SET status='FAILED',attempts=?,error=? WHERE msg_id=?", (attempts, msg, msg_id))
                log.error("FAILED %s: %s", file_name, msg)
            else:
                c.execute("UPDATE tasks SET attempts=?,error=? WHERE msg_id=?", (attempts, msg, msg_id))
                log.warning("RETRY(%d) %s: %s", attempts, file_name, msg)
        c.commit()
        c.close()
        time.sleep(0.3)

if __name__ == "__main__":
    os.makedirs(INBOUND_DIR, exist_ok=True)
    threading.Thread(target=worker, daemon=True).start()
    log.info("接收端啟動 127.0.0.1:%d 收件目錄 %s", PORT, INBOUND_DIR)
    app.run(host="127.0.0.1", port=PORT, threaded=True)
