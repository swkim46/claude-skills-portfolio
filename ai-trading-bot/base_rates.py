#!/usr/bin/env python3
"""조건부 기저율 — **"오를까?"를 묻지 않고 "이 상태에서 과거에 무슨 일이 있었나"를 센다.**

왜 있는가. "이 주식이 오를까?"는 답이 없는 질문이라 결국 서사로 답하게 된다.
2026-09-08 첫 실 분석이 그랬다 — "이미 반영됐다 · 반대 증거가 있다 · 이벤트가 임박했다".
전부 어느 날에나 할 수 있는 말이고, 셀 수 없으니 반증도 안 된다.

질문을 바꾸면 셀 수 있다. **"이틀에 7% 오른 뒤 다음 5일에 무슨 일이 있었나"**는 과거
데이터에서 세면 답이 나온다. 방향을 맞히는 게 아니라 **분포를 아는 것**이고, 그 분포가
포지션 크기와 트리거 수준을 정한다.

정직한 한계 — 이것도 만능이 아니다.
  · **기저율은 예측이 아니다.** 과거 분포가 미래에 반복된다는 보장은 없다.
  · **표본 수(n)가 작으면 우연이다.** 그래서 모든 출력에 n을 함께 낸다. n이 한 자리면
    그건 기저율이 아니라 일화다.
  · **국면이 바뀌면 과거가 무효다.** 조회 창을 길게 잡을수록 다른 국면이 섞인다.
  · 이 스크립트는 **분포를 보여줄 뿐 판단하지 않는다.** 판단은 분석 단계의 몫이다.

사용:
    python3 base_rates.py 005930                       # 국내
    python3 base_rates.py MU --market us --excd NAS
    python3 base_rates.py 005930 --days 500 --up2 5.0   # 조건을 직접 지정
"""
import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import KisClient, KisError, drop_incomplete_session

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
HORIZONS = (1, 3, 5, 10, 20)


def fetch(client: KisClient, ticker: str, market: str, excd: str, days: int) -> list:
    """최신순 일별 시세. 해외는 100행 상한이라 기준일을 옮겨가며 이어 붙인다."""
    if market == "kr":
        # 국내 TR도 한 번에 ~100행이 상한이라, 조회 창을 과거로 밀어가며 이어 붙인다.
        # 표본 수가 곧 이 도구의 신뢰도라 여기서 아끼면 안 된다.
        rows, seen = [], set()
        # tz-naive로 통일한다 — strptime 결과(naive)와 섞으면 비교가 TypeError로 터진다.
        end = datetime.now(KST).replace(tzinfo=None)
        floor = end - timedelta(days=days)
        while end > floor:
            start = max(floor, end - timedelta(days=150))
            page = client.domestic_daily(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            page = [r for r in page if r["date"] not in seen]
            if not page:
                break
            rows += page
            seen |= {r["date"] for r in page}
            end = datetime.strptime(min(r["date"] for r in page), "%Y%m%d") - timedelta(days=1)
        rows.sort(key=lambda r: r["date"], reverse=True)
    else:
        rows, bymd, seen = [], "", set()
        while len(rows) < days:
            page = client.overseas_daily(ticker, excd=excd, bymd=bymd)
            page = [r for r in page if r["date"] not in seen]
            if not page:
                break
            rows += page
            seen |= {r["date"] for r in page}
            oldest = min(r["date"] for r in page)
            prev = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)
            bymd = prev.strftime("%Y%m%d")
    rows, _ = drop_incomplete_session(rows)
    return rows


def forward_returns(rows: list, idx: int) -> dict:
    """rows는 최신순. idx일 종가 기준 앞으로 n거래일 수익률(%)."""
    base = rows[idx]["close"]
    out = {}
    for h in HORIZONS:
        j = idx - h                      # 최신순이라 미래는 인덱스가 작다
        if j >= 0 and base:
            out[h] = (rows[j]["close"] - base) / base * 100
    return out


def summarize(samples: list) -> str:
    if not samples:
        return "표본 없음"
    up = sum(1 for v in samples if v > 0)
    med = statistics.median(samples)
    q = sorted(samples)
    lo, hi = q[len(q) // 10], q[-(len(q) // 10 + 1)]
    return (f"n={len(samples):>3}  상승 {up / len(samples) * 100:>5.1f}%  "
            f"중앙 {med:>+6.2f}%  하위10% {lo:>+7.2f}%  상위10% {hi:>+7.2f}%")


def condition_rows(rows: list, name: str, test) -> list:
    """조건을 만족하는 날의 인덱스. 미래 20일이 확보된 날만."""
    return [i for i in range(len(rows)) if i >= max(HORIZONS) and test(rows, i)]


def report(rows: list, up2: float, ticker: str) -> None:
    n = len(rows)
    print(f"{ticker} — 일별 {n}행 ({rows[-1]['date']} ~ {rows[0]['date']})\n")

    ma20 = lambda rs, i: sum(r["close"] for r in rs[i:i + 20]) / 20 if i + 20 <= len(rs) else None

    conds = [
        ("전체 (조건 없음 — 비교 기준선)", lambda rs, i: True),
        (f"직전 2거래일 누적 +{up2}% 이상 (급등 직후)",
         lambda rs, i: i + 2 < len(rs) and rs[i + 2]["close"]
         and (rs[i]["close"] - rs[i + 2]["close"]) / rs[i + 2]["close"] * 100 >= up2),
        (f"직전 2거래일 누적 -{up2}% 이하 (급락 직후)",
         lambda rs, i: i + 2 < len(rs) and rs[i + 2]["close"]
         and (rs[i]["close"] - rs[i + 2]["close"]) / rs[i + 2]["close"] * 100 <= -up2),
        ("종가가 20일선 위",
         lambda rs, i: (m := ma20(rs, i)) is not None and rs[i]["close"] > m),
        ("종가가 20일선 아래",
         lambda rs, i: (m := ma20(rs, i)) is not None and rs[i]["close"] < m),
        ("3거래일 연속 양봉 직후",
         lambda rs, i: i + 3 < len(rs) and all(rs[k]["close"] > rs[k + 1]["close"]
                                               for k in (i, i + 1, i + 2))),
    ]

    for label, test in conds:
        idxs = condition_rows(rows, label, test)
        print(f"■ {label}   (해당일 {len(idxs)}일)")
        if len(idxs) < 5:
            print("   표본이 5일 미만 — 기저율이 아니라 일화다. 판단 근거로 쓰지 말 것.\n")
            continue
        for h in HORIZONS:
            vals = [fr[h] for i in idxs if (fr := forward_returns(rows, i)) and h in fr]
            if vals:
                print(f"   +{h:>2}일  {summarize(vals)}")
        print()

    print("※ 이 표는 분포를 보여줄 뿐 방향을 예측하지 않는다. n이 작으면 우연이고,")
    print("  국면이 바뀌면 과거는 무효다. 크기와 트리거 수준을 정하는 데 쓴다.")


def main() -> int:
    ap = argparse.ArgumentParser(description="조건부 기저율 — 이 상태에서 과거에 무슨 일이 있었나")
    ap.add_argument("ticker")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--excd", default="NAS")
    ap.add_argument("--days", type=int, default=500, help="조회 일수(달력 기준)")
    ap.add_argument("--up2", type=float, default=5.0, help="'급등' 판정 기준 (2거래일 누적 %%)")
    ap.add_argument("--out", default="", help="결과를 파일에도 쓴다(append) — dispatch 게이트가 읽는다")
    args = ap.parse_args()

    c = KisClient(svr="paper")
    try:
        rows = fetch(c, args.ticker, args.market, args.excd, args.days)
    except KisError as e:
        print(f"조회 실패: {e}", file=sys.stderr)
        return 2
    if len(rows) < 60:
        print(f"일별 시세가 {len(rows)}행뿐 — 기저율을 낼 수 없다(최소 60행).", file=sys.stderr)
        return 2
    if args.out:
        import io as _io, contextlib as _ctx
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            report(rows, args.up2, args.ticker)
        text = buf.getvalue()
        print(text)
        # ★ 매수 전 확인의 증거다. 9/9 한국전력 논지가 "매수 전에 했어야 했는데 하지 않았다"고
        #   스스로 적었다 — 파일이 없으면 dispatch가 막힌다.
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.out).open("a", encoding="utf-8") as f:
            f.write(f"\n<!-- base_rates {args.ticker} {datetime.now(KST):%Y-%m-%d %H:%M} -->\n{text}")
        print(f"→ {args.out}")
    else:
        report(rows, args.up2, args.ticker)
    return 0


if __name__ == "__main__":
    sys.exit(main())
