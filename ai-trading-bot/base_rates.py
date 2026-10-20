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
import re
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db
from kis_client import KisClient, KisError

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
HORIZONS = (1, 3, 5, 10, 20)
KR_WINDOW_DAYS = 150      # 국내 일봉 한 번 조회 창(달력일) — 아래 fetch의 한 쪽 크기와 같다


def kr_daily(client, ticker: str, start: str, end: str) -> list:
    """국내 일봉 — `client.domestic_daily(ticker, start, end)`와 같은 반환(최신순 · date=YYYYMMDD).

    일봉은 저장소(mk_bar)에 한 번 받아 두고 같은 조회는 표에서 준다(`db.domestic_daily`). 그런데 그 재사용은 **조회 구간이
    같을 때만** 걸린다 — 가격 수준(90일)·놓친 급등(22일)·기저율(150일 쪽)이 같은 종목을 저마다 다른 구간으로 물으면 한 run에서
    같은 일봉을 세 번 받는다. 그래서 끝 날짜가 같으면 150일 창 하나로 묻고 필요한 앞부분만 잘라 쓴다(반환은 원래 구간 그대로).
    150일보다 긴 구간은 그대로 묻는다."""
    s0 = (datetime.strptime(end, "%Y%m%d") - timedelta(days=KR_WINDOW_DAYS)).strftime("%Y%m%d")
    if start < s0:
        return db.domestic_daily(client, ticker, start, end)
    return [r for r in db.domestic_daily(client, ticker, s0, end) if r["date"] >= start]


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
            page = kr_daily(client, ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
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
            page = db.overseas_daily(client, ticker, excd=excd, bymd=bymd)
            page = [r for r in page if r["date"] not in seen]
            if not page:
                break
            rows += page
            seen |= {r["date"] for r in page}
            oldest = min(r["date"] for r in page)
            prev = datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)
            bymd = prev.strftime("%Y%m%d")
    # 미완성 세션은 db 일봉이 이미 뺐다(need_final — 관측 시각 > 그 세션 마감인 확정 행만 · 2026-10-19 Codex U04)
    return rows


def touch_prob(rows: list, level: float, horizon: int) -> dict:
    """★ FB-261003-5 — 진입 기준가가 **기간 안에 실제로 닿을 확률**. rows는 최신순.

    지금 가격 대비 기준가의 깊이(%)를 과거 모든 날에 대입해, 그날 종가에서 그 깊이만큼 아래가 이후 `horizon`거래일
    안의 저가에 닿은 비율을 센다. 첫 피드백: 최근 45일 진입 다리 39개 중 7개만 발화했다(테스는 현재가보다 37.7% 아래)."""
    asc = list(reversed([r for r in rows if r.get("close")]))
    if len(asc) < horizon + 20 or level <= 0:
        return {"prob": None, "n": 0}
    now = float(asc[-1]["close"])
    depth = level / now - 1
    hit = n = 0
    for i in range(len(asc) - horizon):
        base = float(asc[i]["close"]) * (1 + depth)
        lows = [float(r.get("low") or r["close"]) for r in asc[i + 1:i + 1 + horizon]]
        n += 1
        hit += min(lows) <= base
    return {"prob": hit / n if n else None, "n": n, "depth_pct": depth * 100, "now": now, "horizon": horizon}


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


def conditions(up2: float) -> list:
    """(저장 코드, 표시 문구, 판정 함수) — 보고(report)와 저장(record)이 같은 조건을 쓴다."""
    ma20 = lambda rs, i: sum(r["close"] for r in rs[i:i + 20]) / 20 if i + 20 <= len(rs) else None
    return [
        ("all", "전체 (조건 없음 — 비교 기준선)", lambda rs, i: True),
        (f"up2_ge_{up2:g}", f"직전 2거래일 누적 +{up2}% 이상 (급등 직후)",
         lambda rs, i: i + 2 < len(rs) and rs[i + 2]["close"]
         and (rs[i]["close"] - rs[i + 2]["close"]) / rs[i + 2]["close"] * 100 >= up2),
        (f"down2_le_-{up2:g}", f"직전 2거래일 누적 -{up2}% 이하 (급락 직후)",
         lambda rs, i: i + 2 < len(rs) and rs[i + 2]["close"]
         and (rs[i]["close"] - rs[i + 2]["close"]) / rs[i + 2]["close"] * 100 <= -up2),
        ("above_ma20", "종가가 20일선 위",
         lambda rs, i: (m := ma20(rs, i)) is not None and rs[i]["close"] > m),
        ("below_ma20", "종가가 20일선 아래",
         lambda rs, i: (m := ma20(rs, i)) is not None and rs[i]["close"] < m),
        ("up_streak_3", "3거래일 연속 양봉 직후",
         lambda rs, i: i + 3 < len(rs) and all(rs[k]["close"] > rs[k + 1]["close"]
                                               for k in (i, i + 1, i + 2))),
    ]


STATE_PRECEDENCE = ("up2_ge_", "down2_le_", "up_streak_3", "below_ma20", "above_ma20", "all")   # 구체적인 조건 먼저


def state_of(rows: list, codes) -> str:
    """지금 상태에 맞는 조건 코드(2026-10-19 · Codex J01) — 저장된 코드(`codes`) 중 구체적인 것부터(급등·급락 직후 → 연속 양봉 →
    20일선 아래/위 → 전체) 오늘(rows[0] · 최신순) 참인 첫 코드. 판정 함수는 기록과 같은 conditions(). 없으면 'all'."""
    codes = list(codes or [])
    for pre in STATE_PRECEDENCE:
        for code in [c for c in codes if c == pre or (pre.endswith("_") and c.startswith(pre))]:
            m = re.search(r"(\d+(?:\.\d+)?)$", code) if pre.endswith("_") else None
            up2 = float(m.group(1)) if m else 5.0
            test = next((t for c, _, t in conditions(up2) if c == code), None)
            try:
                if test and rows and test(rows, 0):
                    return code
            except (KeyError, TypeError, ZeroDivisionError):
                continue
    return "all"


def report(rows: list, up2: float, ticker: str) -> None:
    n = len(rows)
    print(f"{ticker} — 일별 {n}행 ({rows[-1]['date']} ~ {rows[0]['date']})\n")

    for _, label, test in conditions(up2):
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


def _iso_day(d: str) -> str:
    d = str(d)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d


def record(rows: list, up2: float, ticker: str, market: str, stamp: str = None) -> int:
    """조건부 기저율을 저장소(co_base_rate)에 행으로 남긴다 — 보고와 같은 조건·창(HORIZONS). 반환 쓴 행 수.

    보고가 숫자를 내지 않는 조건(해당일 5일 미만 — 일화)은 저장하지 않는다. `hit_rate`는 상승 비율(0~1),
    `avg_ret`·`median_ret`는 %. 조건 행에는 같은 창의 '전체' 기준선(n·상승 비율·중앙값)을 같이 적는다 — 기준선과 비교해야 읽힌다."""
    as_of = _iso_day(rows[0]["date"])
    stats = {}
    for code, _, test in conditions(up2):
        idxs = condition_rows(rows, code, test)
        if len(idxs) < 5:
            continue
        for h in HORIZONS:
            vals = [fr[h] for i in idxs if (fr := forward_returns(rows, i)) and h in fr]
            if vals:
                stats[(code, h)] = {"n": len(vals), "hit_rate": sum(1 for v in vals if v > 0) / len(vals),
                                    "avg_ret": sum(vals) / len(vals), "median_ret": statistics.median(vals)}
    with db.tx():
        for (code, h), s in stats.items():
            db.base_rate_put(market, ticker, code, h, s["n"], s["hit_rate"], s["avg_ret"], s["median_ret"], as_of=as_of,
                             stamp=stamp, baseline=None if code == "all" else stats.get(("all", h)))
    return len(stats)


def record_touch(rows: list, ticker: str, market: str, level: float, tp: dict, stamp: str = None) -> None:
    """진입 기준가 도달 확률(reach_prob)도 같은 표에 — 조건 `touch_le_<기준가>`, 창 = 기간(거래일)."""
    db.base_rate_put(market, ticker, f"touch_le_{level:g}", int(tp.get("horizon") or 0), int(tp.get("n") or 0), tp.get("prob"),
                     as_of=_iso_day(rows[0]["date"]), stamp=stamp)


def _stamp_of(out: str, market: str):
    """`--out data/run_evidence/base_rates_<YYMMDD>_<mkt>.txt`의 스탬프 — 저장 행을 그 run에 잇는다(없으면 None)."""
    m = re.search(r"_(\d{6})_(kr|us)\b", Path(out).name) if out else None
    return m.group(1) if m and m.group(2) == market else None


def main() -> int:
    ap = argparse.ArgumentParser(description="조건부 기저율 — 이 상태에서 과거에 무슨 일이 있었나")
    ap.add_argument("ticker")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--excd", default="NAS")
    ap.add_argument("--days", type=int, default=500, help="조회 일수(달력 기준)")
    ap.add_argument("--up2", type=float, default=5.0, help="'급등' 판정 기준 (2거래일 누적 %%)")
    ap.add_argument("--out", default="", help="결과를 파일에도 쓴다(append) — dispatch 게이트가 읽는다")
    ap.add_argument("--touch", type=float, default=None, help="진입 기준가 — 기간 안 도달 확률(reach_prob)을 낸다")
    ap.add_argument("--horizon", type=int, default=20, help="--touch의 기간(거래일, 기본 20 — 논지 horizon_days를 넣어라)")
    args = ap.parse_args()

    c = KisClient.for_mode()
    try:
        rows = fetch(c, args.ticker, args.market, args.excd, args.days)
    except KisError as e:
        print(f"조회 실패: {e}", file=sys.stderr)
        return 2
    if len(rows) < 60:
        print(f"일별 시세가 {len(rows)}행뿐 — 기저율을 낼 수 없다(최소 60행).", file=sys.stderr)
        return 2
    stamp = _stamp_of(args.out, args.market)
    try:
        record(rows, args.up2, args.ticker, args.market, stamp)
    except sqlite3.Error as e:                    # 저장소 기록 실패가 기저율 출력(증거 파일)을 막지 않는다
        print(f"★ 기저율을 저장소에 못 남겼다({type(e).__name__}: {e})", file=sys.stderr)
    if args.touch is not None:
        tp = touch_prob(rows, args.touch, args.horizon)
        if tp.get("n"):
            try:
                record_touch(rows, args.ticker, args.market, args.touch, tp, stamp)
            except sqlite3.Error as e:
                print(f"★ 도달 확률을 저장소에 못 남겼다({type(e).__name__}: {e})", file=sys.stderr)
        line = (f"[도달 확률] {args.ticker} 기준가 {args.touch:,.2f} (현재 {tp.get('now', 0):,.2f} 대비 {tp.get('depth_pct', 0):+.1f}%) · "
                f"{args.horizon}거래일 안 도달 {('%.0f%%' % (tp['prob'] * 100)) if tp.get('prob') is not None else '산정 불가'} "
                f"(n={tp.get('n')}) → 논지 다리에 reach_prob {round(tp['prob'], 2) if tp.get('prob') is not None else 'null'} · reach_n {tp.get('n')}")
        print(line)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            with Path(args.out).open("a", encoding="utf-8") as f:
                f.write(f"\n{line}\n")
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
