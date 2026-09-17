#!/usr/bin/env python3
"""아쉬움 감사 — "저거 미리 샀어야 했는데"를 **깔때기 숫자**로 바꾼다.

왜 있는가. 급등 뉴스를 볼 때마다 아쉽다는 말은 감정이고, 감정으로는 무엇을 고칠지 알 수 없다.
크게 움직인 종목마다 다섯 칸을 세면 어느 단에서 새는지가 나온다:
    축 있었나 → 전망 있었나 → 논지 있었나 → 포지션 있었나 → 무엇이 막았나
"막은 것"이 기저율·규칙이면 그건 결함이 아니라 **맞은 판단**이다(9/9 두산에너빌리티 제외는 그 뒤
4일 연속 하락으로 맞았다). 그것과 진짜 결함(축은 있었는데 전망이 없었다 · 유니버스 밖이었다)을
가르는 것이 이 감사의 일이다. 주간 집계가 다음 주 고칠 순서를 정한다.

사용:
    python3 regret.py --market kr --stamp 260914             # 이번 run
    python3 regret.py --market kr --stamp 260914 --out data/run_evidence/regret_260914_kr.md
    python3 regret.py --weekly --days 7                        # 깔때기 집계
"""
import argparse
import glob
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
LEDGER = HERE / "journal" / "regret.jsonl"
MOVE_PCT = 8.0                      # 5일 수익률 이 이상이면 '크게 움직였다'
SECTOR_MOVE_PCT = 10.0


def _j(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def snapshots(market: str) -> list:
    out = []
    for f in sorted(glob.glob(str(HERE / "data" / f"snapshot_2*_{market}.json"))):
        m = re.search(r"snapshot_(\d{6})_", f)
        if m and (d := _j(f)):
            out.append((m.group(1), d))
    return out


def moves(market: str, stamp: str, lookback: int = 5) -> list:
    """스냅샷 시계열로 **유니버스 종목의 N-run 수익률**을 잰다(오프라인 — 호출 0).

    스냅샷은 run이 돈 날에만 있으니 '5 거래일'이 아니라 '최근 5개 run'이다. 그 사실을 적는다.
    """
    snaps = [(s, d) for s, d in snapshots(market) if s <= stamp]
    if len(snaps) < 2:
        return []
    cur_s, cur = snaps[-1]
    base_s, base = snaps[max(0, len(snaps) - 1 - lookback)]
    cp, bp = cur.get("prices") or {}, base.get("prices") or {}
    wl = _j(HERE / "config" / "watchlist.json", {}) or {}
    names = {r["ticker"]: r.get("name", "") for r in (wl.get(market.upper()) or [])}
    out = []
    for tk, v in cp.items():
        p1 = (v or {}).get("price"); p0 = (bp.get(tk) or {}).get("price")
        if p1 and p0:
            out.append({"ticker": tk, "name": names.get(tk, ""), "ret": (p1 / p0 - 1) * 100,
                        "from": base_s, "to": cur_s})
    return sorted(out, key=lambda r: -r["ret"])


def moves_fetch(market: str, stamp: str, days: int = 5) -> list:
    """KIS 일봉으로 **진짜 5거래일 수익률**을 잰다 — 유니버스 + 지도 축의 수혜 종목(유니버스 밖 포함).

    ★ 왜 — 스냅샷은 유니버스 종목만, 그것도 run이 돈 날만 있다. 9/9에 편입된 두산에너빌리티는
    9/9 이전 스냅샷이 없어 '5 run 수익률'이 안 나온다. 그런데 가장 큰 아쉬움은 **유니버스
    밖에서** 난다 — 그래서 지도가 이름을 아는 종목까지 실제 일봉으로 잰다. 호출 ~40회.
    """
    sys.path.insert(0, str(HERE))
    import market_map as mm
    from kis_client import KisClient, KisError
    wl = _j(HERE / "config" / "watchlist.json", {}) or {}
    mk = market.upper()
    names = {r["ticker"]: (r.get("name", ""), r.get("excd", "")) for r in (wl.get(mk) or [])}
    _, by_id = mm.axes_index()
    for a in by_id.values():
        for n in (a.get("names") or []) + (a.get("beneficiaries") or []):
            tk = str(n.get("ticker") or "").strip()
            if tk and mm._market_of(tk) == mk and tk not in names:
                names[tk] = (n.get("name", ""), n.get("excd", ""))
    c = KisClient(svr="paper")
    end = datetime.strptime("20" + stamp, "%Y%m%d")
    start = end - timedelta(days=days * 2 + 12)
    out = []
    for tk, (nm, excd) in names.items():
        try:
            if mk == "KR":
                rows = c.domestic_daily(tk, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            else:
                rows = c.overseas_daily(tk, excd or "NAS", end.strftime("%Y%m%d"))
        except KisError:
            continue
        rows = [r for r in rows if r.get("close") and r.get("date", "") <= end.strftime("%Y%m%d")]
        if len(rows) <= days:
            continue
        out.append({"ticker": tk, "name": nm, "ret": (rows[0]["close"] / rows[days]["close"] - 1) * 100,
                    "from": rows[days]["date"][2:], "to": rows[0]["date"][2:]})
    return sorted(out, key=lambda r: -r["ret"])


def _first_mention(ticker: str, name: str) -> str:
    for f in sorted(glob.glob(str(HERE / "data" / "material_2*_*.md"))):
        m = re.search(r"material_(\d{6})_", f)
        try:
            txt = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if m and (ticker in txt or (len(name) >= 2 and name in txt)):
            return m.group(1)
    return ""


def what_blocked(ticker: str, market: str, stamp: str) -> str:
    """무엇이 막았나 — approved의 rejected · 노트 §6 제외 사유 · 후보 냉각. 못 찾으면 '기록 없음'."""
    why = []
    for f in sorted(glob.glob(str(HERE / "signals" / f"approved_*_{market}.json"))):
        m = re.search(r"approved_(\d{6})_", f)
        if not m or m.group(1) > stamp:
            continue
        for r in (_j(f, {}) or {}).get("rejected") or []:
            if r.get("ticker") == ticker:
                why.append(f"{m.group(1)} 거부: {str(r.get('why'))[:50]}")
    for f in sorted(glob.glob(str(HERE / "analysis" / f"분석노트_*_{market}_v*.md"))):
        m = re.search(r"분석노트_(\d{6})_", f)
        if not m or m.group(1) > stamp or m.group(1) < _prev_stamp(stamp, 7):
            continue
        try:
            txt = Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in txt.splitlines():
            if ticker in line and ("제외" in line or "기각" in line or "냉각" in line):
                cleaned = line.replace("|", "·").strip()[:70]
                why.append(f"{m.group(1)} 노트: {cleaned}")
                break
    # 유니버스 후보로 냈는데 대기 중인가 — 후보였다가 안 들어간 것이 왜인지 깔때기에 보이게.
    pend = _j(HERE / "journal" / "universe_pending.json", {}) or {}
    key = f"{market.upper()}:{ticker}"
    if key in pend:
        d = pend[key].get("dates") or []
        why.append(f"유니버스 후보 대기(제안일 {', '.join(d)} · 냉각 {len(set(d))}/2일)")
    return " / ".join(why[-2:]) if why else "기록 없음"


def _prev_stamp(stamp: str, days: int) -> str:
    d = datetime.strptime("20" + stamp, "%Y%m%d") - timedelta(days=days)
    return d.strftime("%y%m%d")


def funnel(market: str, stamp: str, threshold: float = MOVE_PCT, fetch: bool = False) -> list:
    sys.path.insert(0, str(HERE))
    import market_map as mm
    m = mm._read(mm.MAP, {"axes": []})
    by_tk, by_id = mm.axes_index()
    theses = (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or []
    snaps = [(s, d) for s, d in snapshots(market) if s <= stamp]
    positions = {p.get("ticker") for p in ((snaps[-1][1].get("balance") or {}).get("positions") or [])} if snaps else set()
    rows = []
    mvs = moves_fetch(market, stamp) if fetch else moves(market, stamp)
    uni = {r["ticker"] for r in ((_j(HERE / "config" / "watchlist.json", {}) or {}).get(market.upper()) or [])}
    for mv in mvs:
        if mv["ret"] < threshold:
            continue
        tk = mv["ticker"]
        axes = by_tk.get(tk, [])
        fc = [a for a in axes if (by_id.get(a) or {}).get("direction")
              and any(b.get("ticker") == tk for b in (by_id[a].get("beneficiaries") or []))]
        th = [t for t in theses if t.get("ticker") == tk and (t.get("created") or "")[2:].replace("-", "") <= stamp]
        rows.append({"stamp": stamp, "market": market, "ticker": tk, "name": mv["name"],
                     "ret": round(mv["ret"], 2), "window": f"{mv['from']}→{mv['to']}",
                     "in_universe": tk in uni,
                     "axis": bool(axes), "axis_ids": axes,
                     "forecast": bool(fc), "thesis": bool(th), "position": tk in positions,
                     "first_mention": _first_mention(tk, mv["name"]) or "없음",
                     "blocked": what_blocked(tk, market, stamp)})
    return rows


def render(rows: list, market: str, stamp: str) -> str:
    L = [f"## 아쉬움 감사 — {stamp} · {market.upper()}", "",
         f"[아쉬움] 급등 {len(rows)}건 · 축 {sum(r['axis'] for r in rows)} · 전망 {sum(r['forecast'] for r in rows)} · "
         f"논지 {sum(r['thesis'] for r in rows)} · 포지션 {sum(r['position'] for r in rows)}", "",
         f"> 최근 5개 run 수익률 ≥ +{MOVE_PCT:.0f}%인 유니버스 종목. 다섯 칸 중 **어디서 끊겼는가**가 고칠 자리다. "
         f"'막은 것'이 기저율·규칙이면 맞은 판단이지 결함이 아니다.", ""]
    if not rows:
        L.append("*(해당 없음)*")
        return "\n".join(L) + "\n"
    L += ["| 종목 | 수익 | 유니버스 | 축 | 전망 | 논지 | 포지션 | 재료 첫 등장 | 막은 것 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        y = lambda b: "✓" if b else "✗"
        L.append(f"| {r['ticker']} {r['name']} | {r['ret']:+.1f}% | {'안' if r.get('in_universe', True) else '**밖**'} | "
                 f"{y(r['axis'])} {','.join(r['axis_ids'][:2])} | "
                 f"{y(r['forecast'])} | {y(r['thesis'])} | {y(r['position'])} | {r['first_mention']} | {r['blocked']} |")
    leak = {"축 없음": sum(not r["axis"] for r in rows),
            "축은 있는데 전망 없음": sum(r["axis"] and not r["forecast"] for r in rows),
            "전망은 있는데 논지 없음": sum(r["forecast"] and not r["thesis"] for r in rows),
            "논지는 있는데 포지션 없음": sum(r["thesis"] and not r["position"] for r in rows)}
    top = max(leak.items(), key=lambda kv: kv[1])
    L += ["", f"**가장 많이 새는 단: {top[0]} ({top[1]}건)** — " +
          {"축 없음": "지도가 그 사건을 몰랐다 → 3단 전망 질문 1번(돈이 어디로 가는가)",
           "축은 있는데 전망 없음": "지도는 알았는데 앞을 안 봤다 → `forecast add`로 방향·수혜 순서를 세워라",
           "전망은 있는데 논지 없음": "수혜를 적고 안 샀다 → 미반영 2차 수혜에 e0 선진입",
           "논지는 있는데 포지션 없음": "사려 했는데 막혔다 → '막은 것' 열이 기저율·규칙이면 맞은 것, 자금·냉각이면 결함"}[top[0]]]
    return "\n".join(L) + "\n"


def record(rows: list) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def weekly(days: int) -> str:
    since = (datetime.now(KST) - timedelta(days=days)).strftime("%y%m%d")
    rows, seen = [], set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = (r.get("stamp"), r.get("market"), r.get("ticker"))
            if r.get("stamp", "") >= since and k not in seen:
                seen.add(k); rows.append(r)
    n = len(rows)
    L = ["## 아쉬움 깔때기 — 성공 지표 ④의 뒷면", "",
         f"[아쉬움] 급등 {n}건 · 축 {sum(r['axis'] for r in rows)} · 전망 {sum(r['forecast'] for r in rows)} · "
         f"논지 {sum(r['thesis'] for r in rows)} · 포지션 {sum(r['position'] for r in rows)}", ""]
    if not n:
        L.append("*(최근 급등 기록 없음 — `regret.py`가 아직 run에서 안 돌았거나 급등이 없었다)*")
        return "\n".join(L) + "\n"
    L += ["| 단 | 통과 | 비율 |", "|---|---|---|"]
    for k in ("axis", "forecast", "thesis", "position"):
        c = sum(r[k] for r in rows)
        L.append(f"| {dict(axis='축', forecast='전망', thesis='논지', position='포지션')[k]} | {c}/{n} | {100*c/n:.0f}% |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--stamp", default=datetime.now(KST).strftime("%y%m%d"))
    ap.add_argument("--threshold", type=float, default=MOVE_PCT)
    ap.add_argument("--out", default="")
    ap.add_argument("--record", action="store_true", help="journal/regret.jsonl에 적재")
    ap.add_argument("--fetch", action="store_true", help="KIS 일봉으로 5거래일 수익률(유니버스 밖 축 종목 포함)")
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    if a.weekly:
        print(weekly(a.days)); return 0
    rows = funnel(a.market, a.stamp, a.threshold, a.fetch)
    s = render(rows, a.market, a.stamp)
    print(s)
    if a.record:
        record(rows)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(s, encoding="utf-8"); print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
