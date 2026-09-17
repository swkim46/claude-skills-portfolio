#!/usr/bin/env python3
"""트리거의 가격 기준을 **계산**한다 — 고르지 않는다.

왜 있는가: 2026-09-08에 "9/7 급등분을 되돌리면 산다"는 트리거를 걸면서 268,000원을 썼는데,
진짜 급등 전 종가는 255,500이었고 268,000은 현재가 대비 -0.6%라 되돌림이 전혀 아니었다.
근거가 있는 척하는 숫자였다. 일일 상한 ₩300,000을 감으로 고른 것과 같은 실수다.

**가격 트리거는 기준(basis)을 먼저 정하고 그 기준에서 값을 계산해야 한다.** 이 스크립트는
쓸 수 있는 기준들을 실제 데이터로 계산해 보여준다. 숫자를 만들어내는 자리이지,
어느 기준을 쓸지 고르는 자리는 아니다 — 그건 논지가 정한다.

기준들
  retrace_100 / retrace_50  급등분을 얼마나 되돌리는가. '급등일'은 최근 N일에서 등락이
                            가장 큰 날로 잡고, 그 전날 종가가 100% 되돌림 지점이다.
  low_20 / low_60           최근 저점. 논지가 '지지선에서 산다'일 때.
  band_1d                   일중 변동폭 중앙값 1일치. 종목 자신의 변동성에 맞춘 폭이라
                            '몇 % 빠지면'을 임의로 정하지 않아도 된다.
  per_target                PER 기준. 목표 PER × EPS. 밸류에이션 논지일 때만.

사용:
    python3 price_levels.py 005930
    python3 price_levels.py 005930 --days 40 --target-per 35
"""
import argparse
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import KisClient, KisError, drop_incomplete_session

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))


def surge_day(rows: list, lookback: int = 15, on: str = None) -> tuple:
    """급등일의 (종가, 전날 종가, 날짜).

    `on`(YYYYMMDD)을 주면 그 날로 고정한다. 논지가 특정 사건을 가리키는데 자동으로
    '최근 최대 상승일'을 잡으면 엉뚱한 날을 되돌림 기준으로 삼는다 — 2026-09-08에
    9/7 급등(+14,500)을 뜻했는데 8/20(+23,500)이 잡혔다.
    """
    if on:
        for i, r in enumerate(rows[:-1]):
            if r["date"] == on:
                return r["close"], rows[i + 1]["close"], r["date"]
        return None, None, None
    best = None
    for i in range(min(lookback, len(rows) - 1)):
        gain = rows[i]["close"] - rows[i + 1]["close"]
        if best is None or gain > best[0]:
            best = (gain, rows[i]["close"], rows[i + 1]["close"], rows[i]["date"])
    if not best or best[0] <= 0:
        return None, None, None
    return best[1], best[2], best[3]


def confluence(levels_map: dict, cur: float, tol_pct: float = 1.5) -> list:
    """서로 다른 기준이 **같은 가격대에 모이는** 자리. 반환 [{price, members, n}] (n 내림차순).

    독립적으로 계산된 기준(이동평균·되돌림·스윙 지지·변동성 밴드)이 우연히 한 자리에 겹치면
    그 가격대는 **여러 이유로 동시에 의미가 있는** 자리다. 트레이더들이 '합류'라고 부르는 것이고,
    한 계정의 표현으로는 "4개가 한 자리에서 같은 말을 할 때만 믿는 거"다.

    ★ 이걸 쓰는 이유도 예측력이 아니다(§가격 기준 규칙과 동일). **기준이 하나뿐인 자리보다
    여러 개가 겹친 자리를 트리거로 고르면, 그 숫자가 우연히 골라졌을 가능성이 낮아진다.**
    합류가 미래를 맞힌다는 주장은 하지 않는다 — 기준 선택의 임의성을 줄이는 장치다.
    """
    items = [(k, v["price"]) for k, v in levels_map.items() if isinstance(v.get("price"), (int, float))]
    used, out = set(), []
    for name, price in sorted(items, key=lambda kv: -kv[1]):
        if name in used:
            continue
        group = [(n, p) for n, p in items
                 if n not in used and abs(p - price) / price * 100 <= tol_pct]
        if len(group) >= 2:
            for n, _ in group:
                used.add(n)
            avg = sum(p for _, p in group) / len(group)
            out.append({"price": round(avg, 2), "n": len(group),
                        "members": [n for n, _ in group]})
    return sorted(out, key=lambda z: (-z["n"], abs(z["price"] - cur)))


def moving_averages(rows: list, windows=(20, 60, 120)) -> dict:
    """이동평균. 많은 참여자가 같은 선을 보므로 **자기실현적인 면**이 있어 기준으로 쓸 만하다
    — 예측력이 있어서가 아니라, 그 근처에서 실제로 매매가 몰리기 때문이다."""
    out = {}
    for n in windows:
        if len(rows) >= n:
            ma = sum(r["close"] for r in rows[:n]) / n
            out[f"ma_{n}"] = {"price": round(ma, 2), "basis": f"{n}일 이동평균"}
    return out


def swing_points(rows: list, span: int = 3) -> tuple:
    """국소 고점·저점. 앞뒤 span봉보다 높으면(낮으면) 스윙 포인트다.

    지지·저항의 재료다. '차트가 예뻐서'가 아니라 **시장이 실제로 되돌아선 자리**라서 쓴다.
    """
    highs, lows = [], []
    for i in range(span, len(rows) - span):
        window = rows[i - span:i + span + 1]
        if rows[i]["high"] == max(r["high"] for r in window):
            highs.append((rows[i]["high"], rows[i]["date"], rows[i]["volume"]))
        if rows[i]["low"] == min(r["low"] for r in window):
            lows.append((rows[i]["low"], rows[i]["date"], rows[i]["volume"]))
    return highs, lows


def zones(points: list, tol_pct: float = 1.5) -> list:
    """가까운 스윙 포인트를 묶어 '구간'으로. 반환 [{price, touches, dates}] — 터치가 많을수록 세다."""
    if not points:
        return []
    out = []
    for price, date, vol in sorted(points, key=lambda x: -x[0]):
        for z in out:
            if abs(price - z["price"]) / z["price"] * 100 <= tol_pct:
                z["prices"].append(price)
                z["dates"].append(date)
                z["price"] = sum(z["prices"]) / len(z["prices"])
                break
        else:
            out.append({"price": float(price), "prices": [price], "dates": [date]})
    for z in out:
        z["touches"] = len(z["prices"])
        z["price"] = round(z["price"], 2)
    return out


def support_resistance(rows: list, span: int = 3, tol_pct: float = 1.5) -> dict:
    """현재가 기준 1차·2차 지지선과 저항선. 터치 2회 이상만 — 한 번 스친 자리는 구간이 아니다."""
    cur = rows[0]["close"]
    highs, lows = swing_points(rows, span)
    sup = [z for z in zones(lows, tol_pct) if z["price"] < cur and z["touches"] >= 2]
    res = [z for z in zones(highs, tol_pct) if z["price"] > cur and z["touches"] >= 2]
    out = {}
    for i, z in enumerate(sorted(sup, key=lambda x: -x["price"])[:2], 1):
        out[f"support_{i}"] = {"price": z["price"],
                               "basis": f"{i}차 지지 — 스윙 저점 {z['touches']}회 "
                                        f"({', '.join(sorted(z['dates'])[-2:])})"}
    for i, z in enumerate(sorted(res, key=lambda x: x["price"])[:2], 1):
        out[f"resistance_{i}"] = {"price": z["price"],
                                  "basis": f"{i}차 저항 — 스윙 고점 {z['touches']}회 "
                                           f"({', '.join(sorted(z['dates'])[-2:])})"}
    return out


def valuation_position(rows: list, eps: float, per: float) -> dict:
    """PER이 **자기 이력에서 어디쯤인가**. 'PER 41은 비싸다'는 기준 없이는 말이 안 된다.

    근사: 과거 PER ≈ 과거 종가 / **현재** EPS. 실적 발표 사이에는 EPS가 고정이라 쓸 만하지만,
    발표를 건너뛴 구간은 왜곡된다 — 그래서 '근사'라고 적는다.
    """
    if not eps or eps <= 0 or len(rows) < 30:
        return {}
    hist = sorted(r["close"] / eps for r in rows)
    below = sum(1 for v in hist if v < per)
    pct = below / len(hist) * 100
    return {"_valuation": {"per": per, "percentile": round(pct, 1), "n": len(hist),
                           "per_low": round(hist[0], 1), "per_high": round(hist[-1], 1),
                           "basis": f"현재 PER {per}는 최근 {len(hist)}거래일 자기 이력의 "
                                    f"{pct:.0f}분위 (범위 {hist[0]:.1f}~{hist[-1]:.1f}, "
                                    f"과거 종가÷현재 EPS 근사)"}}


def levels(rows: list, per: float = None, eps: float = None,
           target_per: float = None, surge_on: str = None, unit: str = "원") -> dict:
    """반환 {기준이름: {"price": 값, "basis": 설명}}. 계산할 수 없는 기준은 뺀다."""
    cur = rows[0]["close"]
    out = {}
    # 국내는 정수 원, 해외는 소수 달러 — 근거 문구의 단위·자릿수를 시장에 맞춘다.
    g = (lambda v: f"{v:+,.0f}원") if unit == "원" else (lambda v: f"${v:+,.2f}")

    peak, pre, day = surge_day(rows, on=surge_on)
    if peak:
        gain = peak - pre
        out["retrace_100"] = {"price": pre,
                              "basis": f"{day} 급등({g(gain)}) 전 종가로 100% 되돌림"}
        out["retrace_50"] = {"price": round(peak - gain / 2, 2),
                             "basis": f"{day} 급등분 {g(gain)}의 50% 되돌림"}

    for n in (20, 60):
        window = rows[:n]
        if len(window) >= max(5, n // 3):
            lo = min(r["low"] for r in window)
            out[f"low_{n}"] = {"price": lo, "basis": f"최근 {len(window)}거래일 저점"}

    rng = [(r["high"] - r["low"]) / r["close"] * 100 for r in rows[:20] if r["close"]]
    if rng:
        med = statistics.median(rng)
        out["band_1d"] = {"price": round(cur * (1 - med / 100), 2),
                          "basis": f"일중 변동폭 중앙값 {med:.2f}%(최근 {len(rng)}일) 1일치 하락"}

    out.update(moving_averages(rows))
    out.update(support_resistance(rows))
    if per and eps:
        out.update(valuation_position(rows, eps, per))

    if target_per and eps:
        out["per_target"] = {"price": round(target_per * eps, 2),
                             "basis": f"목표 PER {target_per} × EPS {eps:,.0f} (현재 PER {per})"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="트리거 가격 기준을 실제 데이터로 계산한다")
    ap.add_argument("ticker")
    ap.add_argument("--days", type=int, default=90, help="조회할 일수")
    ap.add_argument("--target-per", type=float, help="PER 기준을 쓸 때 목표 PER")
    ap.add_argument("--surge-date", help="되돌림 기준으로 삼을 급등일 (YYYYMMDD). "
                                         "생략하면 최근 상승폭이 가장 큰 날")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--excd", default="NAS", help="해외 거래소 코드 (NAS·NYS·AMS)")
    args = ap.parse_args()

    c = KisClient(svr="paper")
    end = datetime.now(KST)
    start = end - timedelta(days=args.days)
    try:
        if args.market == "kr":
            rows = c.domestic_daily(args.ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            q = c._request("GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
                           headers=c._headers(c.tr["dom_price"]),
                           params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": args.ticker})
            o = q.get("output", {}) or {}
            per_key, eps_key, unit = "per", "eps", "원"
        else:
            rows = c.overseas_daily(args.ticker, excd=args.excd)
            q = c._request("GET", "/uapi/overseas-price/v1/quotations/price-detail",
                           headers=c._headers("HHDFS76200200"),
                           params={"AUTH": "", "EXCD": args.excd, "SYMB": args.ticker})
            o = q.get("output", {}) or {}
            per_key, eps_key, unit = "perx", "epsx", "$"
    except KisError as e:
        print(f"조회 실패: {e}", file=sys.stderr)
        return 2

    # 장중·프리마켓이면 그날 행이 미완성이다 — 종가가 확정되지 않은 값을 기준으로 쓰면 안 된다.
    rows, dropped = drop_incomplete_session(rows)
    if dropped:
        print(f"  ※ {dropped['date']} 행은 거래량 {dropped['volume']:,}로 미완성 세션 "
              f"(장중·프리마켓) — 기준 계산에서 제외했다.\n")
    if len(rows) < 5:
        print(f"일별 시세가 {len(rows)}행뿐 — 기준을 계산할 수 없다.", file=sys.stderr)
        return 2

    cur = rows[0]["close"]
    per = float(o.get(per_key) or 0) or None
    eps = float(o.get(eps_key) or 0) or None
    fmt = "{:,.0f}" if args.market == "kr" else "{:,.2f}"
    head = f"{args.ticker} — 현재 {unit if unit=='$' else ''}{fmt.format(cur)}{unit if unit=='원' else ''} (기준일 {rows[0]['date']})"
    print(f"{head} · PER {per} · EPS {eps:,.2f}\n" if eps and per else f"{head}\n")
    computed = levels(rows, per, eps, args.target_per, args.surge_date, unit)
    val = computed.pop("_valuation", None)
    if val:
        print(f"  밸류에이션 위치: {val['basis']}\n")
    print(f"  {'기준':14} {'가격':>10} {'현재대비':>9}  근거")
    for name, v in sorted(computed.items(), key=lambda kv: -kv[1]["price"]):
        mark = "←현재가" if abs(v["price"] - cur) / cur < 0.005 else ""
        print(f"  {name:14} {fmt.format(v['price']):>11} {(v['price']/cur-1)*100:>+8.1f}%  {v['basis']} {mark}")
    cl = confluence(computed, cur)
    if cl:
        print("\n  ★ 합류 — 여러 기준이 한 자리에 모인 곳 (기준 선택의 임의성이 낮다)")
        for z in cl:
            print(f"  {'':14} {fmt.format(z['price']):>11} {(z['price']/cur-1)*100:>+8.1f}%  "
                  f"기준 {z['n']}개 겹침: {', '.join(z['members'])}")

    print("\n  트리거에는 고른 기준의 이름을 `basis`로 함께 적는다 — 나중에 그 숫자가")
    print("  어디서 왔는지 복원할 수 없으면 감으로 고른 것과 구분이 안 된다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
