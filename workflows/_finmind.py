"""
FinMind API wrapper with rate limiting and error dispatch.
Design: memory/runbooks/margin_scanner_design.md
How-to: memory/runbooks/how-to-finmind.md
"""
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from collections import deque
from pathlib import Path

CALL_LIMIT = 590
CALL_WINDOW = 3600
MIN_INTERVAL = 6.1
BACKOFF_SCHEDULE = [30, 60, 120, 240]

_recent_calls = deque()
_last_call_ts = 0.0


class FinMindError(Exception):
  pass


class FinMindTierError(FinMindError):
  pass


class FinMindQuotaError(FinMindError):
  pass


def _load_token():
  secrets_path = Path(__file__).parent.parent / "config" / "secrets.json"
  with open(secrets_path) as f:
    secrets = json.load(f)
  for k in ("finmind_token", "finmind_key", "FINMIND_TOKEN", "FINMIND_KEY"):
    if k in secrets and secrets[k]:
      return secrets[k]
  raise FinMindError("finmind_token not found in secrets.json")


def rate_limit_finmind():
  global _last_call_ts
  now = time.time()
  while _recent_calls and now - _recent_calls[0] > CALL_WINDOW:
    _recent_calls.popleft()
  if len(_recent_calls) >= CALL_LIMIT:
    sleep_for = CALL_WINDOW - (now - _recent_calls[0]) + 1
    print(f"[finmind] rate limit reached ({CALL_LIMIT}/{CALL_WINDOW}s), sleep {sleep_for:.1f}s")
    time.sleep(sleep_for)
    now = time.time()
    while _recent_calls and now - _recent_calls[0] > CALL_WINDOW:
      _recent_calls.popleft()
  elapsed = now - _last_call_ts
  if elapsed < MIN_INTERVAL:
    time.sleep(MIN_INTERVAL - elapsed)
  _last_call_ts = time.time()
  _recent_calls.append(_last_call_ts)


def fetch_one(dataset, data_id=None, start_date=None, end_date=None, **extra_params):
  token = _load_token()
  base_url = "https://api.finmindtrade.com/api/v4/data"
  params = {"dataset": dataset, "token": token}
  if data_id:
    params["data_id"] = data_id
  if start_date:
    params["start_date"] = start_date
  if end_date:
    params["end_date"] = end_date
  params.update(extra_params)
  url = base_url + "?" + urllib.parse.urlencode(params)
  attempt = 0
  while attempt <= len(BACKOFF_SCHEDULE):
    rate_limit_finmind()
    try:
      req = urllib.request.Request(url, headers={"User-Agent": "openclaw-margin/1.0"})
      with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)
    except urllib.error.HTTPError as e:
      status = e.code
      try:
        body = e.read().decode("utf-8")
      except Exception:
        body = ""
      if status == 400:
        raise FinMindTierError(f"400: {body[:200]}")
      elif status == 402:
        if attempt == 0:
          print(f"[finmind] 402 quota exceeded, sleeping {CALL_WINDOW}s then retry once")
          time.sleep(CALL_WINDOW)
          attempt += 1
          continue
        raise FinMindQuotaError(f"402: {body[:200]}")
      elif 500 <= status < 600:
        if attempt < len(BACKOFF_SCHEDULE):
          wait = BACKOFF_SCHEDULE[attempt]
          print(f"[finmind] {status} backoff {wait}s (attempt {attempt+1})")
          time.sleep(wait)
          attempt += 1
          continue
        raise FinMindError(f"{status} (max retries): {body[:200]}")
      else:
        raise FinMindError(f"HTTP {status}: {body[:200]}")
    except (urllib.error.URLError, TimeoutError) as e:
      if attempt < len(BACKOFF_SCHEDULE):
        wait = BACKOFF_SCHEDULE[attempt]
        print(f"[finmind] network {e}, backoff {wait}s (attempt {attempt+1})")
        time.sleep(wait)
        attempt += 1
        continue
      raise FinMindError(f"network (max retries): {e}")
  raise FinMindError("fetch_one: unreachable")


if __name__ == "__main__":
  print("--- smoke test 1: TaiwanStockPrice 2330 / 2026-05-22 ---")
  r = fetch_one("TaiwanStockPrice", data_id="2330", start_date="2026-05-22", end_date="2026-05-22")
  print(f"keys: {list(r.keys())}, rows: {len(r.get('data', []))}")
  if r.get('data'):
    print(f"row[0]: {r['data'][0]}")
  print("\n--- smoke test 2: TaiwanStockMarginPurchaseShortSale 2330 / 2026-05-19 to 2026-05-23 ---")
  r2 = fetch_one("TaiwanStockMarginPurchaseShortSale", data_id="2330", start_date="2026-05-19", end_date="2026-05-23")
  print(f"keys: {list(r2.keys())}, rows: {len(r2.get('data', []))}")
  if r2.get('data'):
    print(f"row[0]: {r2['data'][0]}")
