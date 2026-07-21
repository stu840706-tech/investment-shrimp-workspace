#!/usr/bin/env python3
"""Backfill historical call memos from /tmp/callmemo_backfill/ into the
dedicated Notion DB. Deterministic only (no M2.7 calls).
  --dry : parse + plan only, zero network, zero writes
  real  : archive via receive_telegram.archive_call_memos(source=backfill),
          per-segment audit jsonl written under workspace state/.
Dedupe by content md5 happens inside archive_call_memos, so re-runs are safe."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(os.environ.get("BROKER_SCRIPTS_DIR")
                   or (Path.home() / ".openclaw" / "workspace" / "skills"
                       / "broker-materials" / "scripts"))
sys.path.insert(0, str(SCRIPTS_DIR))
import receive_telegram as RT

SRC_DIR = Path("/tmp/callmemo_backfill")
EXTS = (".txt", ".md", ".pdf", ".docx", ".doc", ".zip")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not SRC_DIR.is_dir():
        print("[ERROR] source dir missing: " + str(SRC_DIR))
        return 1
    files = sorted(p for p in SRC_DIR.iterdir()
                   if p.is_file() and not p.name.startswith(".")
                   and p.suffix.lower() in EXTS)
    if not files:
        print("[ERROR] no files in " + str(SRC_DIR))
        return 1
    print("[SCAN] files: " + str(len(files)) + " dry=" + str(bool(args.dry)))
    secrets = None if args.dry else RT.load_secrets()
    audit = []
    total_created = 0
    review = []
    parse_fail = []
    for f in files:
        try:
            text = RT.pdf_to_text(f)
        except Exception as e:
            print("[FAIL-PARSE] " + f.name + " : " + str(e)[:150])
            parse_fail.append(f.name)
            continue
        segs, mode = RT.detect_call_memos(text, f.name)
        if not segs:
            print("[REVIEW] no call-memo signal: " + f.name
                  + " chars=" + str(len(text)))
            review.append(f.name)
            continue
        print("[FILE] " + f.name + " mode=" + mode
              + " segments=" + str(len(segs)))
        for i, s in enumerate(segs, 1):
            md5 = hashlib.md5(s["text"].encode("utf-8")).hexdigest()
            print("   #" + str(i)
                  + " date=" + (s["date"] or "?")
                  + " code=" + (s["code"] or "?")
                  + " name=" + (s["company"] or "?")
                  + " broker=" + (s["broker"] or "?")
                  + " chars=" + str(len(s["text"]))
                  + " md5=" + md5[:8])
        if args.dry:
            continue
        n = RT.archive_call_memos(text, f.name, None, secrets,
                                  source="\u56de\u88dc", audit=audit)
        total_created += n
    if args.dry:
        print("[DRY_DONE] files=" + str(len(files))
              + " review=" + str(len(review))
              + " parse_fail=" + str(len(parse_fail)))
    else:
        state = Path.home() / ".openclaw" / "workspace" / "state"
        state.mkdir(parents=True, exist_ok=True)
        out = state / ("callmemo_backfill_audit_"
                       + time.strftime("%Y%m%d_%H%M%S") + ".jsonl")
        with open(out, "w", encoding="utf-8") as fh:
            for rec in audit:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        dup = sum(1 for a in audit if a.get("action") == "skip_dup")
        err = sum(1 for a in audit if a.get("action") == "error")
        print("[AUDIT] " + str(out))
        print("[BACKFILL_DONE] created=" + str(total_created)
              + " dup_skip=" + str(dup) + " errors=" + str(err)
              + " review=" + str(len(review))
              + " parse_fail=" + str(len(parse_fail)))
    if review:
        print("[REVIEW_LIST] " + ", ".join(review))
    if parse_fail:
        print("[PARSE_FAIL_LIST] " + ", ".join(parse_fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
