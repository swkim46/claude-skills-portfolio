#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio.py — 통합자산(NAV)·계좌 계열·입출금. 분모와 수익률은 여기 하나로 잰다(2026-10-19 · Codex R06·R07).

왜: '공유 현금'(통합증거금 실계좌 — 국내 원화 주문가능과 미국 달러 주문가능은 같은 돈을 두 통화로 잰 것) 규칙이 세 곳에 서로 다르게
있었고(account_status·costs는 더 오래된 행 현금을 빼고, risk_guard는 반대편을 0으로), feedback.account_return은 아예 두 번 셌다 —
주식이 그대로인데 5백만 원을 산 날 수익률이 −25%로 나왔다. 비용 분자는 계좌를 안 가리고 분모는 지금 계좌만 읽어 모의 비용이 실계좌
분모에 섞였다. 입출금 기록이 없어 돈을 넣으면 수익으로 보였다.

규칙(한 문장): 어느 쪽이든 cash_shared면 현금은 시각(ts)이 가장 새 쪽 것 한 번만 세고, 모든 쪽은 보유 평가액을 낸다.

    python3 portfolio.py nav [--family real|paper]
    python3 portfolio.py return [--family …] [--days 30] [--fixed-fx]
    python3 portfolio.py decompose [--family …] --since YYYY-MM-DD [--until YYYY-MM-DD]
    python3 portfolio.py flow add --kind deposit|withdraw|fx_convert --currency KRW|USD --amount N --date YYYY-MM-DD
                              [--fx R] [--family …] [--pair-id N] --note "…"
    python3 portfolio.py flow list [--family …]

입출금 0행이면 모든 식이 단순 수익률과 같다. 계좌로 들어오면 +, 나가면 −. 환전(fx_convert)은 두 행(−KRW, +USD @fx)이
pair_id를 나누고, 외부 흐름이 아니라 수익률 분자에 넣지 않는다(그 원화 차이는 비용으로 분해표에 보인다).
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE))

import db                                   # noqa: E402
from kis_client import KST, acct_family    # noqa: E402

DATA_DIR = HERE / "data"
LIMITS_PATH = HERE / "config" / "limits.json"
MARKETS = ("KR", "US")


def family(mode=None) -> str:
    """계좌 계열 'paper' | 'real'(real · real_readonly) — kis_client.acct_family 그대로(모드를 안 주면 지금 설정)."""
    return acct_family(mode)


def _fam_of(mode) -> str:
    return db.acct_family_of(mode)                   # 표시 없는 옛 행은 모의 — 계열 정의는 한 곳(db.acct_family_of)


def file_family(rec: dict) -> str:
    """거래 기록·승인·스냅샷 파일 하나의 계좌 계열 — 머리의 account_mode → svr → 마지막 runs[]의 것 · 표시 없는 옛 파일은 모의."""
    rec = rec if isinstance(rec, dict) else {}
    m = rec.get("account_mode") or rec.get("svr")
    if not m:
        for r in reversed(rec.get("runs") or []):
            if isinstance(r, dict) and (r.get("account_mode") or r.get("svr")):
                m = r.get("account_mode") or r.get("svr")
                break
    return _fam_of(m)


# ───────────────────────────── 순수 계산

def nav(parts: dict, fx, fx_at=None) -> dict:
    """시장별 조각 → 원화 통합자산. parts = {"KR": {equity, cash, invested, cash_shared, ts, currency, positions, source}, "US": {...}}.
    반환 {krw, cash_krw, invested_krw, invested_pct, parts(+krw · shared_cash_dropped), fx, fx_at, shared, ok, why}. 미국 조각이 있는데
    환율이 없으면 ok=False(못 쟀다 — 0이 아니다)."""
    ps = {m: dict(v) for m, v in (parts or {}).items() if v}
    shared = any(v.get("cash_shared") for v in ps.values()) and len(ps) > 1
    if shared:
        newest = max(ps, key=lambda m: (str(ps[m].get("ts") or ""), m == "KR"))
        for m, v in ps.items():
            if m != newest:
                v["shared_cash_dropped"] = v.get("cash") or 0
                v["cash"] = 0
                v["equity"] = v.get("invested") or 0
    if "US" in ps and not fx:
        return {"krw": None, "cash_krw": None, "invested_krw": None, "invested_pct": None, "parts": ps, "fx": fx, "fx_at": fx_at,
                "shared": shared, "ok": False, "why": "환율을 못 구했다 — 미국 조각을 원화로 바꿀 수 없다"}
    krw = cash_krw = inv_krw = 0.0
    for m, v in ps.items():
        mult = float(fx) if m == "US" else 1.0
        v["krw"] = float(v.get("equity") or 0) * mult
        krw += v["krw"]
        cash_krw += float(v.get("cash") or 0) * mult
        inv_krw += float(v.get("invested") or 0) * mult
    return {"krw": krw, "cash_krw": cash_krw, "invested_krw": inv_krw,
            "invested_pct": round(inv_krw / krw * 100, 2) if krw else None, "parts": ps, "fx": fx, "fx_at": fx_at,
            "shared": shared, "ok": bool(ps), "why": "" if ps else "조각이 없다"}


def part_from_balance(balance: dict, source: str, ts=None) -> dict:
    """잔고(스냅샷 balance · 이번 run 잔고) → 조각. 보유 평가액 = 종목 eval_amt 합 · 자산 = 현금 + 보유."""
    pos = balance.get("positions") or []
    inv = sum(float(p.get("eval_amt", 0) or 0) for p in pos)
    cash = float(balance.get("cash") or 0)
    return {"equity": cash + inv, "cash": cash, "invested": inv, "cash_shared": bool(balance.get("cash_shared")),
            "ts": ts, "currency": balance.get("currency"), "positions": pos, "source": source}


def part_from_row(r: dict, source: str = "") -> dict:
    """자산 곡선 행 → 조각. 보유 = 자산 − 현금(account_status와 같은 정의)."""
    eq, cash = float(r.get("equity") or 0), float(r.get("cash") or 0)
    return {"equity": eq, "cash": cash, "invested": max(eq - cash, 0.0), "cash_shared": bool(r.get("cash_shared")),
            "ts": r.get("ts") or r.get("date"), "currency": r.get("currency"), "positions": r.get("positions") or [],
            "source": source or f"equity_curve {str(r.get('ts') or r.get('date'))[:16]}",
            "account_mode": r.get("account_mode"), "date": r.get("date")}


# ───────────────────────────── 저장소 읽기

def curve_rows(fam: str, market: str = None) -> list:
    """자산 곡선 행 — 그 계좌 계열만(표시 없는 옛 행은 모의) · 현금이 적힌 행만."""
    return [r for r in db.equity_rows(market) if _fam_of(r.get("account_mode")) == fam and r.get("cash") is not None
            and r.get("equity")]


def curve_last(fam: str, market: str, upto: str = None):
    last = None
    for r in curve_rows(fam, market):
        if upto and str(r.get("date") or "") > upto:
            continue
        last = r
    return last


def parts_from_curve(fam: str = None, upto: str = None) -> dict:
    """시장마다 그 계열의 마지막 자산 곡선 행(날짜 ≤ upto)."""
    fam = fam or family()
    out = {}
    for m in MARKETS:
        r = curve_last(fam, m, upto)
        if r:
            out[m] = part_from_row(r)
    return out


def _t(s):
    """시각 문자열 → 비교용 datetime(KST). 날짜만이면 그날 끝. 못 읽으면 None."""
    if not s:
        return None
    s = str(s)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return datetime.fromisoformat(s + "T23:59:59").replace(tzinfo=KST)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=KST)
    except ValueError:
        return None


def fx_at(when=None, fam: str = None) -> tuple:
    """(USD/KRW 환율, 관측 시각) — `when` 이하 가장 최근 관측. 같은 계좌 계열의 잔고 환율(ex_balance · 스냅샷)을 먼저 보고, 없으면 시장
    공통 관측(mk_fx — 1단이 적는다)으로 물러선다. 환율은 시장 값이라 계열은 우선순위일 뿐이다. 없으면 (None, None) — 0을 지어내지 않는다."""
    fam = fam or family()
    lim = _t(when) if when else None
    cands = []                                       # (시각, 환율, 같은 계열?, 출처)
    try:
        for r in db.q("SELECT b.exchange_rate AS fx, COALESCE(b.exchange_rate_at, b.at) AS at, r.account_mode AS mode FROM ex_balance b"
                      " LEFT JOIN ex_run r ON r.run_id = b.run_id WHERE b.exchange_rate IS NOT NULL"):
            cands.append((_t(r["at"]), float(r["fx"]), _fam_of(r["mode"]) == fam, "ex_balance"))
        for r in db.q("SELECT rate, ts FROM mk_fx WHERE pair='USDKRW' AND rate IS NOT NULL"):
            cands.append((_t(r["ts"]), float(r["rate"]), None, "mk_fx"))
    except Exception:                                # noqa: BLE001 — 저장소가 없으면 스냅샷만
        pass
    for f in DATA_DIR.glob("snapshot_*_*.json"):
        if not re.fullmatch(r"snapshot_\d{6}_[a-z]{2}\.json", f.name):
            continue
        try:
            sj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rate = (sj.get("balance") or {}).get("exchange_rate")
        if rate:
            cands.append((_t((sj.get("balance") or {}).get("exchange_rate_at") or sj.get("generated_at")), float(rate),
                          file_family(sj) == fam, f.name))
    cands = [c for c in cands if c[0] is not None and c[1] > 0 and (lim is None or c[0] <= lim)]
    for pick in (lambda c: c[2] is True, lambda c: c[2] is None, lambda c: True):
        sel = [c for c in cands if pick(c)]
        if sel:
            best = max(sel, key=lambda c: c[0])
            return best[1], best[0].isoformat(timespec="seconds")
    return None, None


def nav_now(fam: str = None) -> dict:
    fam = fam or family()
    fx, at = fx_at(None, fam)
    return {**nav(parts_from_curve(fam), fx, at), "family": fam}


def nav_at(fam: str, day: str) -> dict:
    """그 날짜 끝 기준 통합자산 — 날짜 ≤ day인 마지막 곡선 행 · 그날 끝 이하 환율."""
    fx, at = fx_at(day, fam)
    return {**nav(parts_from_curve(fam, day), fx, at), "family": fam, "day": day}


# ───────────────────────────── 입출금

def cash_flows(fam: str = None, since: str = None, until: str = None, kinds=("deposit", "withdraw", "fx_convert")) -> list:
    fam = fam or family()
    w, a = ["family=?"], [fam]
    if since:
        w.append("date > ?")
        a.append(since)
    if until:
        w.append("date <= ?")
        a.append(until)
    w.append(f"kind IN ({','.join('?' * len(kinds))})")
    a += list(kinds)
    return db.q(f"SELECT * FROM op_cash_flow WHERE {' AND '.join(w)} ORDER BY date, id", tuple(a))


def cash_flow_add(fam: str, kind: str, currency: str, amount: float, day: str, fx: float = None, note: str = "",
                  pair_id: int = None) -> int:
    """입출금 한 행(추가만 · 표 CHECK가 종류·통화·0 아님·환율 양수를 막는다). 날짜는 YYYY-MM-DD."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day or "")):
        raise ValueError(f"날짜는 YYYY-MM-DD: {day!r}")
    now = datetime.now(KST).isoformat(timespec="seconds")
    return db.connect().execute(
        "INSERT INTO op_cash_flow (family, date, ts, kind, currency, amount, fx, pair_id, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (fam, day, now, kind, currency, float(amount), fx, pair_id, note or None, now)).lastrowid


def _flow_krw(r: dict, fam: str) -> float:
    if r["currency"] == "KRW":
        return float(r["amount"])
    rate = r.get("fx") or fx_at(r["date"], fam)[0]
    return float(r["amount"]) * float(rate or 0)


# ───────────────────────────── 수익률·분해

def _profit_floor() -> float:
    """수익 바닥(2026-10-03 사용자 — 하한이지 목표가 아니다 · 상한 없음) — feedback과 같은 한도 칸."""
    try:
        lim = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
        return float((lim.get("position_sizing") or {}).get("min_target_gap_pct") or 10.0)
    except (OSError, ValueError, TypeError):
        return 10.0


def _window(fam: str, days: int) -> tuple:
    """(시작일, 끝일) — 끝 = 시장들의 마지막 곡선 날짜 중 이른 것 · 시작 = 끝 − days(가동이 짧으면 첫 행)."""
    lasts, firsts = [], []
    for m in MARKETS:
        rows = curve_rows(fam, m)
        if rows:
            lasts.append(str(rows[-1]["date"]))
            firsts.append(str(rows[0]["date"]))
    if not lasts:
        return None, None
    until = min(lasts)
    cut = (date.fromisoformat(until) - timedelta(days=days)).isoformat()
    return max(cut, max(firsts)), until


def account_return(fam: str = None, days: int = 30, fixed_fx: bool = False) -> dict:
    """통합자산 수익률(원화) — 최근 `days`일(가동이 짧으면 가동 이후). 입출금은 Modified Dietz로 뺀다:
    R = (NAV1 − NAV0 − F) / (NAV0 + Σ wᵢFᵢ), wᵢ = (T − tᵢ)/T · 흐름은 그날 환율로 원화. 양 끝 NAV는 그날 환율(fixed_fx면 끝 환율 하나 —
    환율 효과를 뺀 참고값). 반환 {pct, since, until, nav0, nav1, flows_krw, krw_now, floor_gap_pp, method, family, fx0, fx1}."""
    fam = fam or family()
    since, until = _window(fam, days)
    if not since:
        return {"pct": None, "family": fam, "why": "자산 곡선 행이 없다"}
    n0, n1 = nav_at(fam, since), nav_at(fam, until)
    if fixed_fx and n1.get("fx"):
        n0 = {**nav(parts_from_curve(fam, since), n1["fx"], n1["fx_at"]), "family": fam}
    if not (n0.get("ok") and n1.get("ok")) or not n0.get("krw"):
        return {"pct": None, "family": fam, "since": since, "until": until, "why": n0.get("why") or n1.get("why") or "통합자산을 못 쟀다"}
    T = max((date.fromisoformat(until) - date.fromisoformat(since)).days, 1)
    flows = [r for r in cash_flows(fam, since, until, kinds=("deposit", "withdraw"))]
    F = wF = 0.0
    for r in flows:
        k = _flow_krw(r, fam)
        F += k
        wF += (T - (date.fromisoformat(r["date"]) - date.fromisoformat(since)).days) / T * k
    base = n0["krw"] + wF
    pct = (n1["krw"] - n0["krw"] - F) / base * 100 if base > 0 else None
    floor = _profit_floor()
    return {"pct": round(pct, 2) if pct is not None else None, "since": since, "until": until, "nav0": round(n0["krw"]),
            "nav1": round(n1["krw"]), "flows_krw": round(F), "krw_now": round(n1["krw"]),
            "floor_gap_pp": round(pct - floor, 2) if pct is not None else None, "floor_pct": floor, "family": fam,
            "fx0": n0.get("fx"), "fx1": n1.get("fx"), "shared": n1.get("shared"),
            "method": "Modified Dietz · " + ("끝 환율 고정(참고)" if fixed_fx else "그날 환율")}


def decompose(fam: str, d0: str, d1: str) -> dict:
    """통합자산 변화의 최소 분해 — NAV0·NAV1(그날 환율) · 입출금(정확 · op_cash_flow) · 환율 효과(정의된 근사: 끝 미국 달러 가치 ×
    환율 변화) · 비용(추정 · costs 요율) · 가격 손익(잔차) · 실현(추정: 매도 체결 평단 − 그 run 캡처 평단) · 평가(잔차 추정)."""
    n0, n1 = nav_at(fam, d0), nav_at(fam, d1)
    out = {"family": fam, "since": d0, "until": d1, "nav0": n0.get("krw"), "nav1": n1.get("krw"), "fx0": n0.get("fx"), "fx1": n1.get("fx")}
    if not (n0.get("ok") and n1.get("ok")):
        return {**out, "ok": False, "why": n0.get("why") or n1.get("why")}
    flows = cash_flows(fam, d0, d1)
    ext = sum(_flow_krw(r, fam) for r in flows if r["kind"] in ("deposit", "withdraw"))
    conv = sum(_flow_krw(r, fam) for r in flows if r["kind"] == "fx_convert")      # 환전 두 행의 원화 차이(스프레드)
    us_usd = float(((n1.get("parts") or {}).get("US") or {}).get("equity") or 0)
    fx_eff = us_usd * (float(n1["fx"] or 0) - float(n0["fx"] or 0)) if us_usd and n0.get("fx") and n1.get("fx") else 0.0
    try:
        import costs as _costs
        days = max((date.fromisoformat(d1) - date.fromisoformat(d0)).days, 1)
        fees = _costs.drag(days, fam, now=datetime.fromisoformat(d1 + "T23:59:59").replace(tzinfo=KST),
                           equity_krw=n1["krw"]).get("cost_krw")
    except Exception:                                # noqa: BLE001
        fees = None
    d_nav = n1["krw"] - n0["krw"]
    price = d_nav - ext - fx_eff
    realized = _realized_krw(fam, d0, d1, n1.get("fx"))
    return {**out, "ok": True, "d_nav": round(d_nav), "flows_krw": round(ext), "fx_convert_net_krw": round(conv),
            "fx_effect_krw": round(fx_eff), "fees_krw_est": fees, "price_pnl_krw": round(price),
            "realized_krw_est": None if realized is None else round(realized),
            "unrealized_krw_est": None if realized is None else round(price - realized),
            "note": "입출금은 정확 · 환율 효과는 끝 달러 가치 × 환율 변화(근사) · 비용·실현·평가는 추정 · 가격 손익은 잔차"}


def _realized_krw(fam: str, d0: str, d1: str, fx) -> float:
    """매도 체결(주문마다 마지막 관측) × (체결 평단 − 그 run 캡처 때 평단) — 계열이 맞는 run만. 캡처 평단이 없으면 그 매도는 뺀다(추정)."""
    try:
        st0, st1 = d0[2:].replace("-", ""), d1[2:].replace("-", "")
        tot = 0.0
        for r in db.q("SELECT o.run_id, o.ticker, o.market, f.filled_qty, f.avg_price, r.account_mode, r.stamp FROM ex_order o"
                      " JOIN v_fill_latest f ON f.order_id = o.id JOIN ex_run r ON r.run_id = o.run_id"
                      " WHERE UPPER(o.side)='SELL' AND f.verdict IN ('FILLED','PARTIAL') AND r.stamp > ? AND r.stamp <= ?", (st0, st1)):
            if _fam_of(r["account_mode"]) != fam or not r["filled_qty"] or not r["avg_price"]:
                continue
            p = db.q1("SELECT avg_price FROM ex_position WHERE run_id=? AND ticker=? AND phase='capture' ORDER BY id DESC LIMIT 1",
                      (r["run_id"], r["ticker"]))
            if not p or not p["avg_price"]:
                continue
            mult = float(fx or 0) if str(r["market"]).lower() == "us" else 1.0
            tot += (float(r["avg_price"]) - float(p["avg_price"])) * abs(float(r["filled_qty"])) * mult
        return tot
    except Exception:                                # noqa: BLE001
        return None


# ───────────────────────────── CLI

def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="통합자산·수익률·입출금(portfolio.py)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name in ("nav", "return", "decompose"):
        p = sp.add_parser(name)
        p.add_argument("--family", choices=["paper", "real"], default=None)
        if name == "return":
            p.add_argument("--days", type=int, default=30)
            p.add_argument("--fixed-fx", action="store_true")
        if name == "decompose":
            p.add_argument("--since", required=True)
            p.add_argument("--until", default=None)
    fl = sp.add_parser("flow")
    fsp = fl.add_subparsers(dest="fcmd", required=True)
    fa = fsp.add_parser("add")
    fa.add_argument("--kind", required=True, choices=["deposit", "withdraw", "fx_convert"])
    fa.add_argument("--currency", required=True, choices=["KRW", "USD"])
    fa.add_argument("--amount", required=True, type=float, help="계좌로 들어오면 +, 나가면 −")
    fa.add_argument("--date", required=True)
    fa.add_argument("--fx", type=float, default=None)
    fa.add_argument("--family", choices=["paper", "real"], default=None)
    fa.add_argument("--pair-id", type=int, default=None)
    fa.add_argument("--note", default="")
    flst = fsp.add_parser("list")
    flst.add_argument("--family", choices=["paper", "real"], default=None)
    a = ap.parse_args(argv)
    fam = a.family or family()
    if a.cmd == "nav":
        print(json.dumps(nav_now(fam), ensure_ascii=False, indent=1, default=str))
    elif a.cmd == "return":
        print(json.dumps(account_return(fam, a.days, a.fixed_fx), ensure_ascii=False, indent=1))
    elif a.cmd == "decompose":
        print(json.dumps(decompose(fam, a.since, a.until or date.today().isoformat()), ensure_ascii=False, indent=1))
    elif a.fcmd == "add":
        if a.kind == "withdraw" and a.amount > 0:
            print("출금은 음수 금액이다(계좌에서 나가면 −)", file=sys.stderr)
            return 2
        print(cash_flow_add(fam, a.kind, a.currency, a.amount, a.date, a.fx, a.note, a.pair_id))
    else:
        for r in cash_flows(fam):
            print(f"{r['id']:>4} {r['date']} {r['kind']:<10} {r['currency']} {r['amount']:>14,.2f}"
                  + (f" @ {r['fx']}" if r.get("fx") else "") + (f" pair {r['pair_id']}" if r.get("pair_id") else "")
                  + f"  {r.get('note') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
