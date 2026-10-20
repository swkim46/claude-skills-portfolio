#!/usr/bin/env python3
"""
매매 비용 — `config/costs.json`을 읽어 체결 한 건의 비용과 30일 비용 부담을 낸다(2026-10-12 실계좌 전환 계획).

왜 있나: 모의는 비용을 거의 안 떼지만 실계좌는 국내 매도 0.20%, 미국 왕복 약 1.3%가 빠진다. 같은 판단이라도
회전이 잦으면 비용이 수익을 먹는다 — 그것을 매주 숫자로 보기 위해서다(`feedback.py` 지표 `net_cost_drag_30d_pct`).
비용은 **추정**이다: 국내 수수료·세금은 요율 그대로이고, 미국 환전 비용은 기준 스프레드를 편도 1%로 가정했다
(`config/costs.json` `_note`). 실제 금액은 증권사 체결 내역이 정본이다.

    python3 costs.py --days 30 [--family paper|real]   # 30일 체결 비용 추정과 자산 대비 %(기본 = 지금 계좌 계열)

★ 계좌 계열은 필수다(2026-10-19 · Codex R07) — 분자(체결 비용)와 분모(통합자산)가 같은 계좌여야 한다. 예전엔 분자가 계좌를 안 가리고
분모는 지금 계좌만 읽어, 모의 체결 비용이 실계좌 자산 대비 %로 나왔다. 분모·환율은 portfolio.py 한 곳에서.
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


def drag(days: int, family: str, now: datetime = None, equity_krw: float = None) -> dict:
    """최근 `days`일 체결의 비용 추정(원화)과 자산 대비 % — **그 계좌 계열(paper·real)의 기록만** 분자로, 그 계열의 통합자산을 분모로.
    다른 계열 파일 수는 `files_other_family`로 돌려준다(읽지 않았다는 증거). 환율은 그 계열의 `now` 이하 최근 관측."""
    import portfolio
    if family not in ("paper", "real"):
        raise ValueError(f"계좌 계열은 paper|real — {family!r}")
    now = now or datetime.now(KST)
    since = (now - timedelta(days=days)).strftime("%y%m%d")
    until = now.strftime("%y%m%d")
    cfg = load()
    fx = portfolio.fx_at(now.isoformat(), family)[0] or 0.0
    cost = turnover = 0.0
    n = other = 0
    for f in sorted(glob.glob(str(HERE / "journal" / "trades_*_*.json"))):
        m = re.search(r"trades_(\d{6})_(kr|us)\.json$", f)
        if not m or m.group(1) < since or m.group(1) > until:
            continue
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if portfolio.file_family(rec) != family:
            other += 1
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
        equity_krw = portfolio.nav_now(family).get("krw")
    return {"days": days, "family": family, "fills": n, "turnover_krw": round(turnover), "cost_krw": round(cost),
            "equity_krw": round(equity_krw or 0), "files_other_family": other,
            "pct": round(cost / equity_krw * 100, 3) if equity_krw else None,
            "fx": fx, "note": "추정 — 국내 요율·미국 환전 편도 1% 가정(config/costs.json)"}


def main() -> int:
    from kis_client import acct_family
    ap = argparse.ArgumentParser(description="체결 비용 추정(30일)")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--family", choices=["paper", "real"], default=None, help="계좌 계열(기본 = 지금 설정)")
    a = ap.parse_args()
    d = drag(a.days, a.family or acct_family())
    pct = f"{d['pct']:.3f}%" if d["pct"] is not None else "?"
    print(f"최근 {d['days']}일 체결 {d['fills']}건 · 회전 {d['turnover_krw']:,}원 · 비용 추정 {d['cost_krw']:,}원 "
          f"= 자산 {d['equity_krw']:,}원의 {pct} ({d['note']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
