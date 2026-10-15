#!/usr/bin/env python3
"""
매매 비용 — `config/costs.json`을 읽어 체결 한 건의 비용과 30일 비용 부담을 낸다(2026-10-12 실계좌 전환).

왜 있나: 모의는 비용을 거의 안 떼지만 실계좌는 국내 매도 0.20%, 미국 왕복 약 1.3%가 빠진다. 같은 판단이라도
회전이 잦으면 비용이 수익을 먹는다 — 그것을 매주 숫자로 보기 위해서다(`feedback.py` 지표 `net_cost_drag_30d_pct`).
비용은 **추정**이다: 국내 수수료·세금은 요율 그대로이고, 미국 환전 비용은 기준 스프레드를 편도 1%로 가정했다
(`config/costs.json` `_note`). 실제 금액은 증권사 체결 내역이 정본이다.

    python3 costs.py --days 30          # 30일 체결 비용 추정과 자산 대비 %
"""
import argparse
import glob
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
COSTS_PATH = HERE / "config" / "costs.json"
KST = timezone(timedelta(hours=9))


def load() -> dict:
    try:
        return json.loads(COSTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def is_etf(name: str, cfg: dict = None) -> bool:
    cfg = cfg if cfg is not None else load()
    hay = f" {name or ''} ".upper()
    return any(p.upper() in hay for p in cfg.get("etf_name_patterns") or [])


def oneway_pct(market: str, side: str, name: str = "", cfg: dict = None) -> float:
    """체결 금액 대비 그 한쪽(매수 또는 매도)의 비용 %."""
    cfg = cfg if cfg is not None else load()
    m = (market or "KR").upper()
    c = cfg.get(m) or {}
    if m == "KR":
        etf = is_etf(name, cfg)
        fee = float(c.get("etf_commission_pct" if etf else "commission_pct") or 0)
        tax = float(c.get("etf_sell_tax_pct" if etf else "sell_tax_pct") or 0) if side == "SELL" else 0.0
        return fee + tax
    fx = float(c.get("fx_spread_oneway_pct") or 0) * (1 - float(c.get("fx_discount_pct") or 0) / 100)
    sec = float(c.get("sec_fee_sell_pct") or 0) if side == "SELL" else 0.0
    return float(c.get("commission_pct") or 0) + sec + fx


def trade_amount(t: dict) -> float:
    """체결 금액(그 시장 통화). 체결 판정이 없으면 0."""
    f = t.get("fill") or {}
    if f.get("verdict") not in ("FILLED", "PARTIAL"):
        return 0.0
    q = f.get("filled_qty")
    if q is None:
        q = abs(int(f.get("delta") or 0))
    px = float(f.get("avg_price") or t.get("price") or 0)
    return float(q or 0) * px


def latest_fx() -> float:
    """가장 최근 미국 스냅샷의 환율(없으면 0)."""
    best = ("", 0.0)
    for f in glob.glob(str(HERE / "data" / "snapshot_*_us.json")):
        if not re.search(r"snapshot_\d{6}_us\.json$", f):
            continue
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fx = float((d.get("balance") or {}).get("exchange_rate") or 0)
        if fx and (d.get("generated_at") or "") > best[0]:
            best = (d.get("generated_at") or "", fx)
    return best[1]


def drag(days: int = 30, svr: str = None, now: datetime = None, equity_krw: float = None) -> dict:
    """최근 `days`일 체결의 비용 추정(원화)과 자산 대비 %. `svr`가 있으면 그 계좌(paper·real) 기록만."""
    now = now or datetime.now(KST)
    since = (now - timedelta(days=days)).strftime("%y%m%d")
    cfg = load()
    fx = latest_fx()
    cost = turnover = 0.0
    n = 0
    for f in sorted(glob.glob(str(HERE / "journal" / "trades_*_*.json"))):
        m = re.search(r"trades_(\d{6})_(kr|us)\.json$", f)
        if not m or m.group(1) < since:
            continue
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if svr and (rec.get("svr") or "paper") != svr:
            continue
        mk = m.group(2).upper()
        mult = 1.0 if mk == "KR" else fx
        for t in rec.get("trades") or []:
            amt = trade_amount(t) * mult
            if amt <= 0:
                continue
            n += 1
            turnover += amt
            cost += amt * oneway_pct(mk, t.get("action", "BUY"), t.get("name", ""), cfg) / 100
    if equity_krw is None:
        equity_krw = _portfolio_krw(fx)
    return {"days": days, "fills": n, "turnover_krw": round(turnover), "cost_krw": round(cost),
            "equity_krw": round(equity_krw or 0),
            "pct": round(cost / equity_krw * 100, 3) if equity_krw else None,
            "fx": fx, "note": "추정 — 국내 요율·미국 환전 편도 1% 가정(config/costs.json)"}


def _same_account(r: dict) -> bool:
    from kis_client import same_account
    return same_account(r)


def _portfolio_krw(fx: float) -> float:
    """equity_curve 마지막 행 두 개(KR·US)의 원화 합 — 통합증거금 공유 현금은 한 번만."""
    path = HERE / "journal" / "equity_curve.jsonl"
    last = {}
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if r.get("market") in ("KR", "US") and r.get("equity") and _same_account(r):   # 지금 계좌 계열만(2026-10-15)
                last[r["market"]] = r
    except OSError:
        return 0.0
    tot = 0.0
    for m, r in last.items():
        tot += float(r.get("equity") or 0) * (fx if m == "US" else 1.0)
    if len(last) == 2 and all(r.get("cash_shared") for r in last.values()):
        old = min(last, key=lambda k: str(last[k].get("ts") or ""))
        tot -= float(last[old].get("cash") or 0) * (fx if old == "US" else 1.0)
    return tot


def main() -> int:
    ap = argparse.ArgumentParser(description="체결 비용 추정(30일)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--svr", choices=["paper", "real"], help="그 계좌 기록만")
    a = ap.parse_args()
    d = drag(a.days, a.svr)
    pct = f"{d['pct']:.3f}%" if d["pct"] is not None else "?"
    print(f"최근 {d['days']}일 체결 {d['fills']}건 · 회전 {d['turnover_krw']:,}원 · 비용 추정 {d['cost_krw']:,}원 "
          f"= 자산 {d['equity_krw']:,}원의 {pct} ({d['note']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
