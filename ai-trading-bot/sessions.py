#!/usr/bin/env python3
"""일과 원장 — **산출물에서 파생한다.** 어느 세션이 어디까지 갔고, 무엇이 밀렸는가.

왜 파생인가: "각 단계가 원장에 한 줄 쓴다"로 만들면 **그 쓰기를 잊은 run은 원장에
안 남는다** — 그리고 잊는 run이 바로 원장이 잡아야 할 run이다. 그래서 쓰지 않고
**디스크에 실제로 남은 산출물로 도달 단계를 역산한다.** 잊을 수가 없다.

무엇을 잡는가:
- **반쪽 run** — 결정(5단)까지 못 간 세션. *실측 2026-09-07~10: 미국 2일*(9/8은 3단,
  9/9는 1단에서 죽었다 — 재료만 남았다).
- **건너뛴 단계** — 도달은 6단인데 중간 산출물이 빈 경우. *죽은 run과 다른 사건이다.*
- **시각 이탈** — 정규 슬롯(국내 09:35 · 미국 22:35 KST)에서 ±90분을 벗어난 실행.
- **밀린 세션** — 평일인데 그 시장 세션이 아예 없는 날.

**정직한 한계 — 시각은 mtime에서 온다.** 파일을 나중에 고치면 그 시각이 뒤로 밀리므로
`시작`은 그 세션 산출물들의 **최소 mtime**으로 근사한다. 재발행·수동 편집이 있었던
과거 세션의 슬롯 판정은 참고치이고, **앞으로의 세션부터 정확하다**(원장을 지금 시작하는
이유가 그것이다 — 소급으로 만들면 이 한계가 영구히 섞인다).

사용:
    python3 sessions.py scan                    # 원장 재생성(journal/sessions.jsonl)
    python3 sessions.py due                     # 밀린 것·이탈만 (1단 캡처에 넣는다)
    python3 sessions.py due --days 10
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOURNAL = HERE / "journal"
DATA = HERE / "data"
ANALYSIS = HERE / "analysis"
SIGNALS = HERE / "signals"
LEDGER = JOURNAL / "sessions.jsonl"
KST = timezone(timedelta(hours=9))

# 정규 슬롯(KST) — `SKILL.md` 〈하루 일과〉와 같은 값이어야 한다.
SLOTS = {"kr": (9, 35), "us": (22, 35)}
SLOT_TOLERANCE_MIN = 90          # 이 밖이면 '시각 이탈'

# 도달 단계 = 그 단계의 산출물이 디스크에 있는가. 순서대로 본다.
STAGES = [
    ("1 capture", lambda st, m: [DATA / f"material_{st}_{m}.md",
                                 DATA / f"snapshot_{st}_{m}.json"]),
    ("2 carry",   lambda st, m: [JOURNAL / f"carry_{st}_{m}.md"]),
    # ★ `섹터_*` 파일명에는 시장 토큰이 없다 — 그래서 아래 reached()가 **1단 산출물을 먼저 요구**한다.
    #   (실측 2026-09-14: 국내 run의 섹터 리서치가 미국 run의 3단으로도 잡혀 없는 run이 생겼다.)
    ("3 map",     lambda st, m: list(ANALYSIS.glob(f"섹터_*_{st}_*.md"))
                                + [ANALYSIS / f"전망_{st}_{m}.md"]),
    ("4 note",    lambda st, m: list(ANALYSIS.glob(f"분석노트_{st}_{m}_v*.md"))),
    ("5 decide",  lambda st, m: list(SIGNALS.glob(f"signal_{st}_{m}*.json"))),
    ("6 execute", lambda st, m: [JOURNAL / f"trades_{st}_{m}.json"]),
]


def stamp_of(d: date) -> str:
    return d.strftime("%y%m%d")


def reached(st: str, market: str) -> tuple:
    """(가장 멀리 간 단계, 그 시각, 비어 있는 하위 단계 목록).

    ★ **첫 공백에서 멈추지 않는다.** 그렇게 세면 6단 분리 *이전*의 run이 전부
    "1단에서 끊김"으로 나온다 — 그때는 2단 산출물이 규격에 없었을 뿐, 노트와 시그널까지
    갔다. 도달은 **최댓값**으로 보고 빈 단계는 따로 나열해야 두 가지가 구분된다:
    *중간을 건너뛴 run*과 *중간에 죽은 run*.
    """
    best, when, missing = "0 없음", None, []
    # ★ 캡처 없이 3단에 도달한 run은 없다. 1단 산출물(그 시장의 재료·스냅샷)이 없으면 다른 단계의
    #   파일이 보여도 **이 시장의 run이 아니다** — 시장 토큰 없는 파일(섹터 리서치)이 반대편 run에
    #   잘못 귀속되는 것을 여기서 막는다. 6단 분리 이전 run도 1단 산출물은 있었으므로 "최댓값" 의미는 유지된다.
    if not any(p.exists() for p in STAGES[0][1](st, market)):
        return best, when, missing
    for name, finder in STAGES:
        paths = [p for p in finder(st, market) if p.exists()]
        if paths:
            best = name
            when = max(datetime.fromtimestamp(p.stat().st_mtime, KST) for p in paths)
        else:
            missing.append(name)
    # 도달 단계보다 위의 공백은 '아직 안 한 것'이므로 결손이 아니다.
    order = [n for n, _ in STAGES]
    if best in order:
        cut = order.index(best)
        missing = [m for m in missing if order.index(m) < cut]
    return best, when, missing


def first_touch(st: str, market: str) -> datetime:
    """그 세션에 **처음** 손댄 시각 — 슬롯 준수는 시작 시각으로 판정한다."""
    cands = []
    for _, finder in STAGES:
        for p in finder(st, market):
            if p.exists():
                cands.append(datetime.fromtimestamp(p.stat().st_mtime, KST))
    return min(cands) if cands else None


def slot_verdict(market: str, d: date, started: datetime) -> tuple:
    """(판정, 설명). 정규 슬롯과의 거리로 본다."""
    if started is None:
        return "미실행", "산출물이 없다"
    h, m = SLOTS[market]
    # 미국 세션일도 **같은 날짜**다: 22:35 KST = 09:35 EDT(시차 13h)로 날이 넘지 않는다.
    # 날을 하나 빼면 모든 미국 세션이 하루치 이탈로 잘못 찍힌다.
    target = datetime(d.year, d.month, d.day, h, m, tzinfo=KST)
    off = (started - target).total_seconds() / 60
    if abs(off) <= SLOT_TOLERANCE_MIN:
        return "준수", f"슬롯 {target:%m-%d %H:%M} 대비 {off:+.0f}분"
    return "이탈", f"슬롯 {target:%m-%d %H:%M} 대비 **{off:+.0f}분**"


def scan(days: int) -> list:
    """최근 `days`일의 (시장 × 평일) 세션을 전수로 판정한다 — **없는 날도 행으로 남긴다.**"""
    today = datetime.now(KST).date()
    rows = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:
            continue                    # 주말은 세션이 없다(휴장은 별도 문제)
        st = stamp_of(d)
        for market in ("kr", "us"):
            stage, last, missing = reached(st, market)
            started = first_touch(st, market)
            verdict, why = slot_verdict(market, d, started)
            rows.append({
                "session_date": d.isoformat(),
                "market": market,
                "reached": stage,
                "missing_below": missing,
                "complete": stage.startswith("5") or stage.startswith("6"),
                "started_kst": started.isoformat() if started else None,
                "last_kst": last.isoformat() if last else None,
                "slot": verdict,
                "slot_detail": why,
            })
    return rows


def _run_log_rows() -> list:
    """`run_log.jsonl` — 무인 드라이버가 남긴 결과(outcome·reason). 없을 수 있다."""
    f = JOURNAL / "run_log.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _reviewed() -> set:
    """`reviews.jsonl`에 회고 기록이 있는 (시장, 세션일) 집합."""
    f = JOURNAL / "reviews.jsonl"
    if not f.exists():
        return set()
    out = set()
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("phantom"):
            continue                        # 허깨비 회고(존재하지 않는 run) — 회고로 치지 않는다
        if r.get("market") and r.get("session"):
            out.add((str(r["market"]).lower(), str(r["session"])))
    return out


def runs(days: int) -> list:
    """**산출물이 있는 run만** 시간순으로. 회고 대상을 고르는 기준이 이것이다.

    ★ 왜 `scan`과 따로 두는가 — `scan`은 "없는 날도 행으로" 남기는 점검표다. 회고는
    *실제로 돈 run*을 대상으로 하므로 산출물이 없는 칸을 빼고 **시작 시각순**으로 세운다.
    """
    seen = _reviewed()
    log = _run_log_rows()
    out = []
    for r in scan(days):
        if r["reached"] == "0 없음" or not r["started_kst"]:
            continue
        lg = [x for x in log
              if x.get("market") == r["market"] and x.get("session_date") == r["session_date"]]
        lg = lg[-1] if lg else {}
        r = dict(r)
        r["outcome"] = lg.get("outcome")
        r["reason"] = lg.get("reason")
        r["orders"] = lg.get("orders")
        r["no_trade"] = lg.get("no_trade")
        r["reviewed"] = (r["market"], r["session_date"]) in seen
        out.append(r)
    out.sort(key=lambda x: x["started_kst"])
    return out


def prev_run(days: int, market: str = "", stamp: str = "") -> dict:
    """**직전 run** — 시장으로 유도하지 않고 시간순으로 바로 앞의 run.

    ★ 이 함수가 있는 이유. 예전에는 `stage.py`가 회고 대상을
    `other = "us" if market == "kr" else "kr"`로 **시장에서 유도**했다. 그러면
    **엄격한 교대 실행을 전제**하게 되고, 한쪽이 빠지면 그 전 같은 시장 run을
    아무도 회고하지 않는다.
    *실측: 미국 run이 2026-09-08·09-09에 반쪽으로 죽어서 KR 9/8·9/9가 영구 미회고였다
    — 세션 4개 중 2개.*

    run은 빠진다(아침도 저녁도). 그래서 **무조건 시간순 직전 run**을 보고,
    그것이 같은 시장이든 반쪽으로 죽었든 그 run에 피드백한다.
    """
    rs = runs(days)
    if market and stamp:
        d = f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]}"
        cur = next((r for r in rs if r["market"] == market and r["session_date"] == d), None)
        cutoff = cur["started_kst"] if cur else None
        rs = [r for r in rs
              if not (r["market"] == market and r["session_date"] == d)
              and (cutoff is None or r["started_kst"] < cutoff)]
    return rs[-1] if rs else {}


def cmd_prev(a) -> int:
    """직전 run이 **언제·어떻게** 돌았는지. 회고는 여기서 시작한다."""
    r = prev_run(a.days, a.market or "", a.stamp or "")
    if not r:
        # ★ 폴백으로 오늘 것을 주지 않는다 — 자기 회고는 회고가 아니다.
        print("■ 직전 run — **없다**")
        print(f"  최근 {a.days}일에 산출물을 남긴 run이 없다(이번 run 제외).")
        print("  [직전run] 없음")
        print("  → 회고할 대상이 없다는 사실을 노트 §1-A에 그대로 적는다. "
              "오늘 것으로 대신하지 말 것.")
        return 0
    now = datetime.now(KST)
    started = datetime.fromisoformat(r["started_kst"])
    last = datetime.fromisoformat(r["last_kst"]) if r["last_kst"] else started
    gap_h = (now - last).total_seconds() / 3600
    print(f"■ 직전 run — {r['market'].upper()} {r['session_date']}")
    print()
    print("  ── 언제")
    print(f"     시작 {started:%m-%d %H:%M} · 마지막 산출 {last:%m-%d %H:%M} "
          f"· 지금까지 {gap_h:.1f}시간 전")
    print(f"     정규 슬롯 {r['slot']} — {r['slot_detail']}")
    print("  ── 어떻게")
    print(f"     도달 {r['reached']}" + ("  · **완주**" if r["complete"] else "  · **미완주**"))
    if r["missing_below"]:
        print(f"     건너뛴 단계 {', '.join(r['missing_below'])}")
    if r.get("outcome"):
        print(f"     드라이버 판정 {r['outcome']}"
              + (f" — {str(r['reason'])[:90]}" if r.get("reason") else ""))
    else:
        print("     드라이버 기록 없음(세션 밖에서 손으로 돌린 run)")
    if r.get("orders"):
        print(f"     주문 {json.dumps(r['orders'], ensure_ascii=False)}")
    if r.get("no_trade") is not None:
        print(f"     기권 여부 {r['no_trade']}")
    print(f"     회고 기록 {'있음' if r['reviewed'] else '**없음 — 이번 run이 해야 한다**'}")
    # 게이트·스크립트가 읽는 기계 판독 줄
    print()
    print(f"  [직전run] {r['market']}:{r['session_date']} · 도달 {r['reached']} "
          f"· 완주 {r['complete']} · 회고 {'있음' if r['reviewed'] else '없음'}")
    print("  ※ 시그널이 없어도 회고한다 — 언제·어디까지 갔고 왜 멈췄으며 "
          "남긴 산출물 중 오늘 쓸 것이 무엇인지가 회고다.")
    return 0


def cmd_scan(a) -> int:
    rows = scan(a.days)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"■ 일과 원장 — 최근 {a.days}일 · {len(rows)}행 → {LEDGER.relative_to(HERE)}\n")
    print(f"{'세션일':12s} {'시장':4s} {'도달':10s} {'슬롯':6s} 비고")
    for r in rows:
        mark = "  " if r["complete"] else "★ "
        gap = f"  · 건너뜀: {', '.join(r['missing_below'])}" if r["missing_below"] else ""
        print(f"{mark}{r['session_date']:10s} {r['market']:4s} {r['reached']:10s} "
              f"{r['slot']:6s} {r['slot_detail']}{gap}")
    half = [r for r in rows if r["reached"] != "0 없음" and not r["complete"]]
    none = [r for r in rows if r["reached"] == "0 없음"]
    off = [r for r in rows if r["slot"] == "이탈"]
    skipped = [r for r in rows if r["missing_below"]]
    print(f"\n반쪽 run {len(half)}건 · 미실행 {len(none)}건 · 시각 이탈 {len(off)}건 "
          f"· 단계 건너뜀 {len(skipped)}건")
    print("※ 시각은 mtime 근사다 — 나중에 고친 파일은 늦게 찍힌다. 과거 세션의 슬롯 판정은 참고치.")
    return 0


def cmd_due(a) -> int:
    """1단 캡처에 박는 블록 — **오늘 이어받아야 할 것**만 짧게."""
    rows = scan(a.days)
    today = datetime.now(KST).date().isoformat()
    buf = io.StringIO()
    print("■ 밀린 일과 — 오늘 이어받을 것", file=buf)
    half = [r for r in rows if r["reached"] != "0 없음" and not r["complete"]
            and r["session_date"] != today]
    none = [r for r in rows if r["reached"] == "0 없음" and r["session_date"] != today]
    off = [r for r in rows if r["slot"] == "이탈" and r["session_date"] != today]
    if not (half or none or off):
        print("  밀린 것 없음 · 시각 이탈 없음.", file=buf)
    for r in half:
        print(f"  ★ 반쪽 run — {r['session_date']} {r['market'].upper()}: "
              f"{r['reached']}에서 끊겼다. **그 회고를 오늘 §1-B에 이어붙인다.**", file=buf)
    for r in none:
        print(f"  ★ 미실행 — {r['session_date']} {r['market'].upper()}: 산출물이 없다. "
              f"그날 판단이 비었으므로 오늘 노트에 그 사실을 적는다.", file=buf)
    for r in off:
        print(f"  · 시각 이탈 — {r['session_date']} {r['market'].upper()}: {r['slot_detail']}",
              file=buf)
    # ★ 회고 안 된 run — 이것이 피드백 고리의 구멍이다.
    #   *실측(2026-09-11): run 10개 중 8개가 회고된 적이 없었다.*
    unrev = [r for r in runs(a.days) if not r["reviewed"] and r["session_date"] != today]
    if unrev:
        print(f"\n  ★ **회고 안 된 run {len(unrev)}건** — 이번 run이 이어붙인다:", file=buf)
        for r in unrev:
            print(f"     {r['market'].upper()} {r['session_date']} · 도달 {r['reached']}"
                  + ("" if r["complete"] else " · 미완주"), file=buf)
        print("     → `python3 review.py --market <mkt> --session <날짜> --record`", file=buf)
        print("     판단이 없었던 run도 회고한다 — 왜 멈췄고 무엇을 남겼는지가 회고다.", file=buf)
    print("\n  **밀린 세션을 안 적으면 '안 한 것'과 '없었던 것'이 구분되지 않는다.**", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"→ {a.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="원장 재생성 + 전수 표")
    s.add_argument("--days", type=int, default=14)
    pv = sub.add_parser("prev", help="직전 run이 언제·어떻게 돌았나 (회고 대상)")
    pv.add_argument("--days", type=int, default=14)
    pv.add_argument("--market", default="", help="이번 run의 시장(자기 자신을 제외하려면)")
    pv.add_argument("--stamp", default="", help="이번 run의 YYMMDD")
    d = sub.add_parser("due", help="밀린 것·이탈만 (1단 캡처용)")
    d.add_argument("--days", type=int, default=7)
    d.add_argument("--out", default="")
    a = ap.parse_args()
    return {"scan": cmd_scan, "due": cmd_due, "prev": cmd_prev}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
