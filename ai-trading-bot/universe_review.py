#!/usr/bin/env python3
"""유니버스를 양방향으로 검토한다 — 무엇이 들어와야 하고 무엇이 나가야 하는가.

왜 필요한가: 워치리스트는 한 번 정하고 끝내는 목록이 아니다. 시장이 움직이면 유망한 것이
바뀌고, 재료가 더 다루지 않는 종목은 **유니버스에 있어도 판단할 수가 없다.** 그런데 지금까지
'무엇을 뺄까'를 묻는 자리가 아예 없었고, 그래서 2026-09-03에 배관 테스트용으로 넣은 시드가
그대로 남아 있었다 — 미국 쪽은 AAPL·MSFT 둘 다 그날 재료에 **한 번도 안 나오는데**
마이크론·브로드컴·테슬라는 나오고 있었다.

이 스크립트는 **결정하지 않는다.** 재료 커버리지를 세어 사람이 판정할 표를 만들 뿐이다.
`watchlist.json`은 재료를 읽는 이 단계에서는 고치지 않는다(인젝션 방어). 변경은 재료와 분리된 자리에서 한다.

커버리지 이력은 저장소(op_coverage · 렌더본 `journal/universe_coverage.json`)에 쌓인다 — "몇 회 연속 안 나왔나"를
기억이 아니라 기록으로 판정하기 위해서다.

사용:
    python3 universe_review.py --market kr --material data/material_260908_kr.md
    python3 universe_review.py --market kr --material ... --stamp 260908   # 이력에 기록
"""
import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))

# 재료에 나오지 않은 채 이만큼 지나면 "판단 근거가 끊겼다"고 본다.
STALE_RUNS = 5


def aliases_for(entry: dict) -> list:
    """종목을 재료에서 찾을 때 쓸 표기들. 중복은 제거하고 **긴 것부터** 돌려준다.

    긴 것부터인 이유: '네이버클라우드'는 '네이버'를 품고 있어서, 짧은 것을 먼저 세면
    같은 자리를 두 번 센다. 정규식 교대에서 앞에 오는 것이 먼저 매칭되므로 순서가 곧 규칙이다.
    """
    out = [entry["ticker"], entry.get("name", "")] + (entry.get("aliases") or [])
    seen, uniq = set(), []
    for a in out:
        if a and a not in seen:
            seen.add(a)
            uniq.append(a)
    return sorted(uniq, key=len, reverse=True)


def count_mentions(text: str, entry: dict) -> tuple:
    """반환 (총 등장 횟수, 표기별 내역). 한 자리는 한 번만 센다."""
    names = aliases_for(entry)
    if not names:
        return 0, []
    pat = re.compile("|".join(re.escape(a) for a in names))
    tally = {}
    for m in pat.finditer(text):          # finditer는 겹치지 않게 훑는다
        tally[m.group(0)] = tally.get(m.group(0), 0) + 1
    used = [f"{k}×{v}" for k, v in sorted(tally.items(), key=lambda x: -x[1])]
    return sum(tally.values()), used


def load_coverage() -> tuple:
    """(커버리지, 읽었는가) — 저장소 op_coverage.

    ★ 예전에는 실패를 `{}`로 갈음했다. 그러면 전 종목의 `absent_runs`가 0으로 보여
    이번 run에서 1이 되고, `--stamp`가 그 값을 **되써서 누적 미등장 이력을 파괴한다.**
    제거 문턱(`STALE_RUNS`)에 영원히 도달할 수 없게 되므로, 유니버스 정리가 구조적으로
    불가능해진다 — 조용히 꺼진 규칙의 전형이다. 그래서 저장소를 못 읽으면 빈 이력으로 잇지 않고
    여기서 멈춘다(예외가 그대로 올라간다). 두 번째 값은 옛 호출 모양을 지키려고 남긴다(항상 True).
    """
    return db.coverage_load(), True


def review(market: str, material: Path, stamp: str = None) -> int:
    wl = db.watchlist_doc()
    entries = wl.get(market.upper()) or []
    if not entries:
        print(f"{market.upper()} 유니버스가 비어 있다.", file=sys.stderr)
        return 2
    text = material.read_text(encoding="utf-8")
    cov, cov_ok = load_coverage()

    print(f"유니버스 검토 — {market.upper()} · 재료 {material.name}\n")
    print(f"  {'종목':22} {'축':16} {'오늘':>5}  {'연속 미등장':>9}  판정")
    print(f"  {'-' * 74}")

    rows, stale = [], []
    for e in entries:
        hits, used = count_mentions(text, e)
        key = f"{market.upper()}:{e['ticker']}"
        prev = cov.get(key, {})
        absent = 0 if hits else int(prev.get("absent_runs", 0)) + 1
        verdict = "—"
        if hits == 0 and absent >= STALE_RUNS:
            verdict = f"★ 뺄 후보 ({absent}회 연속 미등장 — 판단 근거 없음)"
            stale.append(e)
        elif hits == 0:
            verdict = "재료 없음 (이번 run 판단 불가)"
        axis = e.get("axis") or "(축 미기재)"          # 저장소 행은 빈 칸이 ''로 온다 — .get 기본값으로는 안 걸린다
        print(f"  {e['ticker'] + ' ' + e.get('name', ''):22} {axis:16} {hits:>5}  {absent:>9}  {verdict}")
        if used:
            print(f"    └ {', '.join(used)}")
        rows.append((key, hits, absent))

    # 축 중복 — 같은 축에 여러 종목이면 사람이 볼 수 있게 띄운다(자동 제거는 하지 않는다).
    axes = {}
    for e in entries:
        axes.setdefault(e.get("axis") or "(축 미기재)", []).append(e.get("name", e["ticker"]))
    no_axis = axes.pop("(축 미기재)", [])               # 축이 없는 종목은 한 축이 아니다 — 묶음에서 빼고 따로 센다
    dup = {k: v for k, v in axes.items() if len(v) > 1}
    if dup:
        print("\n  같은 축에 묶인 종목 — 함께 움직이므로 실질 베팅 수는 종목 수보다 적다:")
        for k, v in dup.items():
            print(f"    · {k}: {', '.join(v)}")
    if no_axis:
        print(f"\n  축 미기재 {len(no_axis)}종목 — 축 중복 판정에서 뺐다(지도에 축을 달 대상): {', '.join(no_axis)}")

    if stamp and not cov_ok:
        print("\n  ★ 커버리지 이력을 읽지 못했으므로 **기록하지 않는다** — "
              "지금 쓰면 전 종목의 누적 미등장이 1로 리셋된다.", file=sys.stderr)
    elif stamp:
        # 날짜 칸은 이 run의 **세션 날짜**(2026-10-14 · 현지 거래일) — 새벽에 돌린 미국 run도 그 세션 날짜로 남는다.
        # 실제 기록 시각은 updated_kst에 따로 둔다.
        import sessions as _ss
        sess = _ss.session_iso(stamp, market)
        now_kst = datetime.now(KST).isoformat(timespec="seconds")
        # 종목 행만 바꾼다(한 트랜잭션) — 다른 시장 종목의 이력은 건드리지 않는다. 렌더본은 db가 커밋 뒤 내보낸다.
        with db.tx():
            for key, hits, absent in rows:
                db.coverage_put(key, {"absent_runs": absent, "last_seen": (cov.get(key, {}).get("last_seen")
                                      if not hits else sess),
                                      "updated": sess, "updated_kst": now_kst})
        print("\n  커버리지 이력 기록: op_coverage(렌더본 universe_coverage.json)")

    print("\n  들어올 후보는 분석 단계에서 `watchlist_candidates`로, 나갈 후보는")
    print("  `watchlist_removals`로 시그널에 올린다. 반영은 `universe_apply.py`가 규칙으로 검증해 저장소에 넣는다"
          "(config/watchlist.json은 저장소 렌더본 — 손으로 고치지 않는다).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="유니버스 커버리지를 세어 편입·제외 검토표를 낸다")
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--material", required=True, help="data/material_YYMMDD_mkt.md")
    ap.add_argument("--stamp", help="주면 커버리지 이력에 기록한다 (YYMMDD)")
    args = ap.parse_args()

    m = Path(args.material)
    if not m.is_absolute():
        m = HERE / m
    if not m.exists():
        print(f"재료 파일 없음: {m}", file=sys.stderr)
        return 2
    return review(args.market, m, args.stamp)


if __name__ == "__main__":
    sys.exit(main())
