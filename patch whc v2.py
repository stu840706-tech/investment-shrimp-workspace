import re, hashlib, sys, py_compile
P = "/home/ubuntu/.openclaw/workspace/workflows/weekly_health_check.py"
if len(sys.argv) > 1: P = sys.argv[1]
IND = chr(10) + " " * 4
s = open(P, encoding="utf-8").read()
h = hashlib.md5(s.encode("utf-8")).hexdigest()
assert h == "d8dbd179f31443e74fad8826b2884eea", "PRE_MD5_MISMATCH:" + h
a1 = '"broker_digest.py",'
assert s.count(a1) == 1, "A1_COUNT:" + str(s.count(a1))
s = s.replace(a1, a1 + IND + '"scan_margin.py",')
a2 = 'fetch_calendar.log"'
assert s.count(a2) == 1, "A2_COUNT:" + str(s.count(a2))
s2, n2 = re.subn(r'(fetch_calendar\.log", )"[^"]+"', r'\1"[DONE]"', s)
assert n2 == 1, "A2_SUBN:" + str(n2)
s = s2
a3 = "orphan_candidates = all_py - CRON_REQUIRED - no_cron_ok"
assert s.count(a3) == 1, "A3_COUNT:" + str(s.count(a3))
s = s.replace(a3, a3 + IND + 'orphan_candidates = {c for c in orphan_candidates if not c.startswith("_")}')
post = hashlib.md5(s.encode("utf-8")).hexdigest()
assert post == "59f62e8bbea187a6c1329faa45c289d2", "POST_MISMATCH_ABORT_NO_WRITE:" + post
open(P, "w", encoding="utf-8").write(s)
py_compile.compile(P, doraise=True)
print("PATCH_OK POST_MD5=" + post)
