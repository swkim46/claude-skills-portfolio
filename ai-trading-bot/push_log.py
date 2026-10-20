#!/usr/bin/env python3
"""
보고 푸시 기록 — 세션이 푸시 알림(세션 도구)을 보낸 직후 한 줄을 남긴다. 실전 점검표 ⑩이 이것을 센다.

왜(2026-10-15): 푸시는 코드가 아니라 세션 도구로 나가서 '보냈는가'를 파일로 확인할 길이 없었다 — 실전에서 무인 run의 결과가
운영자에게 닿았는지가 점검 항목이다.

    python3 push_log.py --stamp 261015 --market kr --text "<보고 첫 줄>"      # 저장소 op_push(db.log_append('push_log'))
    python3 push_log.py --stamp 261015 --market kr --failed --text "<사유>"   # 도구가 없거나 실패했으면 그 사실을

저장(2026-10-17): 기록은 저장소 하나(`db.py` · 표 op_push · 추가만)에 남는다 — 예전 `journal/push_log.jsonl`과 같은 행 모양
(at·stamp·market·sent·text). 읽는 쪽(real_check ⑩)은 `db.log_rows('push_log')`로 센다. 명령·인자는 그대로다(스킬 문서가 부른다).
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import db  # noqa: E402 — 저장소를 여는 것은 db.py뿐이다

KST = timezone(timedelta(hours=9))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--failed", action="store_true", help="푸시를 못 보냈다(도구 없음·실패)")
    a = ap.parse_args(argv)
    row = {"at": datetime.now(KST).isoformat(timespec="seconds"), "stamp": a.stamp, "market": a.market,
           "sent": not a.failed, "text": a.text[:200]}
    db.log_append("push_log", row)            # 추가만 — 같은 run에 두 번 보냈으면 두 행이 맞다
    print(f"푸시 기록 {'실패' if a.failed else '전송'} — {a.stamp} {a.market}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
