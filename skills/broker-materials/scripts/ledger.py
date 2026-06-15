#!/usr/bin/env python3
"""ledger.py — 券商檔案處理台帳 (SQLite)
記錄每份檔案的處理生命週期，提供：
  - 冪等去重：用內容指紋(sha256)當鍵，同內容只處理一次
  - 狀態查詢：pending/processing/done/failed
  - 稽核：開始/完成時間、失敗原因、處理次數、字數
不改動處理引擎(receive_telegram)，由 batch_process 外層呼叫。
"""
import sqlite3, hashlib
from pathlib import Path
from datetime import datetime

WORKSPACE = Path.home() / ".openclaw" / "workspace"
LEDGER_DB = WORKSPACE / "state" / "broker_ledger.db"


def _conn():
    LEDGER_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(LEDGER_DB), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    c = _conn()
    c.execute(
        "CREATE TABLE IF NOT EXISTS files("
        "file_hash TEXT PRIMARY KEY, file_name TEXT, file_size INTEGER, "
        "status TEXT, char_count INTEGER, started_at TEXT, finished_at TEXT, "
        "attempts INTEGER DEFAULT 0, error TEXT)"
    )
    c.commit()
    c.close()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def is_done(file_hash):
    c = _conn()
    r = c.execute(
        "SELECT 1 FROM files WHERE file_hash=? AND status='done'", (file_hash,)
    ).fetchone()
    c.close()
    return r is not None


def get_status(file_hash):
    c = _conn()
    r = c.execute(
        "SELECT status FROM files WHERE file_hash=?", (file_hash,)
    ).fetchone()
    c.close()
    return r[0] if r else None


def mark_processing(file_hash, name, size):
    c = _conn()
    now = _now()
    c.execute(
        "INSERT INTO files(file_hash,file_name,file_size,status,started_at,attempts) "
        "VALUES(?,?,?,'processing',?,1) "
        "ON CONFLICT(file_hash) DO UPDATE SET "
        "status='processing', started_at=?, attempts=attempts+1, "
        "file_name=excluded.file_name, file_size=excluded.file_size, error=NULL",
        (file_hash, name, size, now, now),
    )
    c.commit()
    c.close()


def mark_done(file_hash, char_count=None):
    c = _conn()
    c.execute(
        "UPDATE files SET status='done', finished_at=?, char_count=?, error=NULL "
        "WHERE file_hash=?",
        (_now(), char_count, file_hash),
    )
    c.commit()
    c.close()


def mark_failed(file_hash, error):
    c = _conn()
    c.execute(
        "UPDATE files SET status='failed', finished_at=?, error=? WHERE file_hash=?",
        (_now(), str(error)[:500], file_hash),
    )
    c.commit()
    c.close()


def stats():
    c = _conn()
    rows = c.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall()
    c.close()
    return dict(rows)


def recent(n=20):
    c = _conn()
    rows = c.execute(
        "SELECT file_name,status,started_at,finished_at,char_count,attempts,error "
        "FROM files ORDER BY started_at DESC, rowid DESC LIMIT ?",
        (n,),
    ).fetchall()
    c.close()
    return rows
