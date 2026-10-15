#!/usr/bin/env python3
"""
보고 푸시 기록 — 세션이 푸시 알림(세션 도구)을 보낸 직후 한 줄을 남긴다. 실전 점검표 ⑩이 이것을 센다.

왜(2026-10-15): 푸시는 코드가 아니라 세션 도구로 나가서 '보냈는가'를 파일로 확인할 길이 없었다 — 실전에서 무인 run의 결과가
운영자에게 닿았는지가 점검 항목이다.

    python3 push_log.py --stamp 261015 --market kr --text "<보고 첫 줄>"      # journal/push_log.jsonl
    python3 push_log.py --stamp 261015 --market kr --failed --text "<사유>"   # 도구가 없거나 실패했으면 그 사실을
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
LOG = HERE / "journal" / "push_log.jsonl"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--failed", action="store_true", help="푸시를 못 보냈다(도구 없음·실패)")
    a = ap.parse_args()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(KST).isoformat(timespec="seconds"), "stamp": a.stamp, "market": a.market,
           "sent": not a.failed, "text": a.text[:200]}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"푸시 기록 {'실패' if a.failed else '전송'} — {a.stamp} {a.market}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
