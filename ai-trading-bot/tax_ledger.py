#!/usr/bin/env python3
"""
해외주식 양도소득세 원장 — 실계좌 미국 체결로 연간 실현 손익(원화)과 예상 세액을 낸다(2026-10-12 실계좌 전환).

세법(analysis/세제_실계좌_261012_v1_0.md §2): 연 순차익(국내 과세대상과 합산) − 기본공제 250만 원 × 22%(지방세 포함).
양도가액·취득가액은 **결제일 기준환율**로 원화 환산한다(소득세법 시행령 제178조의5①) — 통합증거금으로 원화 주문했어도
과세 환율은 실제 환전 환율이 아니라 결제일 기준환율이다. 손실은 다음 해로 넘어가지 않는다. 신고는 매년 5월.

이 원장은 **추정**이다. 결제일 환율은 결제일 run이 본 증권사 고시 환율(`exrt`)로 채우며 서울외국환중개 기준환율과
조금 다를 수 있다. 신고에는 증권사 양도세 자료를 쓴다 — 이 원장은 연말 손실 실현 판단과 금액 감을 위한 것이다.

    python3 tax_ledger.py --year 2026                 # 연간 실현 손익·예상 세액(실계좌 기록만)
    python3 tax_ledger.py --fill-fx                   # 결제일이 지난 체결에 환율을 채운다(journal.py --daily가 부른다)
    python3 tax_ledger.py --year 2026 --december      # + 보유 평가손 종목의 손실 실현 후보
    python3 tax_ledger.py --year 2026 --include-paper # 모의 기록으로 형식만 확인
"""
import argparse
import glob
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import costs as _costs

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))


def _us_records(include_paper: bool = False):
    for f in sorted(glob.glob(str(HERE / "journal" / "trades_*_us.json"))):
        if not re.search(r"trades_\d{6}_us\.json$", f):
            continue
        try:
            rec = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (rec.get("svr") or "paper") != "real" and not include_paper:
            continue
        yield Path(f), rec


def _us_today() -> date:
    from kis_client import market_session
    return market_session("US")["session_date"]          # 동부 날짜(서머타임 포함)


def _us_day_of(ts) -> str:
    """체결 시각(KST ISO) → 동부 날짜 문자열. 못 읽으면 앞 10자(예전 동작)."""
    try:
        from kis_client import market_session
        return market_session("US", datetime.fromisoformat(str(ts)))["session_date"].isoformat()
    except (ValueError, TypeError):
        return str(ts or "")[:10]


def fill_fx(rate: float = None, at: str = None, source: str = "", today: date = None,
            include_paper: bool = False) -> int:
    """결제일이 오늘 이전·당일인데 `settle_fx`가 비어 있는 미국 체결에 환율을 채운다. 반환 채운 건수.

    결제일 당일 run이 채우면 '결제일 당일', 늦게 채우면 '결제일 뒤 첫 기록 — 근사'로 출처를 남긴다.
    """
    today = today or _us_today()               # 결제일은 미국 날짜다 — 한국 새벽엔 한국 날짜가 하루 앞선다
    if not rate:
        rate = _costs.latest_fx()
        source = source or "최신 미국 스냅샷 exchange_rate"
    if not rate:
        return 0
    at = at or datetime.now(KST).isoformat()
    n = 0
    for path, rec in _us_records(include_paper):
        changed = False
        for t in rec.get("trades") or []:
            f = t.get("fill") or {}
            if f.get("verdict") not in ("FILLED", "PARTIAL") or f.get("settle_fx") not in (None, 0):
                continue
            sd = f.get("settle_date")
            if not sd or "settle_fx" not in f:
                continue
            try:
                sdd = date.fromisoformat(sd)
            except ValueError:
                continue
            if sdd > today:
                continue
            f["settle_fx"] = float(rate)
            f["settle_fx_at"] = at
            f["settle_fx_source"] = (f"{source} · " if source else "") + ("결제일 당일" if sdd == today else "결제일 뒤 첫 기록 — 근사")
            changed = True
            n += 1
        if changed:
            path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return n


def ledger(year: int, include_paper: bool = False) -> dict:
    """선입선출로 연간 실현 손익(원화)을 낸다. 수수료·SEC Fee는 필요경비로 뺀다(요율 추정)."""
    cfg = _costs.load()
    us = cfg.get("US") or {}
    comm = float(us.get("commission_pct") or 0) / 100
    sec = float(us.get("sec_fee_sell_pct") or 0) / 100
    fallback_fx = _costs.latest_fx()
    fills = []
    for _, rec in _us_records(include_paper):
        for t in rec.get("trades") or []:
            f = t.get("fill") or {}
            if f.get("verdict") not in ("FILLED", "PARTIAL"):
                continue
            q = f.get("filled_qty")
            if q is None:
                q = abs(int(f.get("delta") or 0))
            if not q:
                continue
            fx = f.get("settle_fx")
            fills.append({"ts": t.get("ts") or "", "ticker": t.get("ticker"), "action": t.get("action"),
                          "qty": int(q), "px": float(f.get("avg_price") or t.get("price") or 0),
                          "settle_date": f.get("settle_date") or _us_day_of(t.get("ts")),
                          "fx": float(fx) if fx else fallback_fx, "fx_est": not fx})
    fills.sort(key=lambda r: r["ts"])
    lots, rows = {}, []
    for r in fills:
        if r["action"] == "BUY":
            lots.setdefault(r["ticker"], []).append({**r, "left": r["qty"]})
            continue
        need, basis_krw, basis_fee_krw, est = r["qty"], 0.0, 0.0, r["fx_est"]
        for lot in lots.get(r["ticker"], []):
            if need <= 0:
                break
            take = min(need, lot["left"])
            if take <= 0:
                continue
            basis_krw += take * lot["px"] * lot["fx"]
            basis_fee_krw += take * lot["px"] * comm * lot["fx"]
            lot["left"] -= take
            need -= take
            est = est or lot["fx_est"]
        unmatched = need
        proceeds = r["qty"] * r["px"] * r["fx"]
        sell_fee = r["qty"] * r["px"] * (comm + sec) * r["fx"]
        if not str(r["settle_date"]).startswith(str(year)):
            continue
        gain = proceeds - basis_krw - basis_fee_krw - sell_fee
        rows.append({"settle_date": r["settle_date"], "ticker": r["ticker"], "qty": r["qty"], "sell_px": r["px"],
                     "sell_fx": r["fx"], "proceeds_krw": round(proceeds), "basis_krw": round(basis_krw),
                     "fees_krw": round(basis_fee_krw + sell_fee), "gain_krw": round(gain),
                     "fx_estimated": est, "unmatched_qty": unmatched})
    net = sum(x["gain_krw"] for x in rows)
    ded = float(us.get("basic_deduction_krw") or 2500000)
    rate = float(us.get("capital_gains_pct") or 22) / 100
    taxable = max(0.0, net - ded)
    return {"year": year, "rows": rows, "net_gain_krw": round(net), "deduction_krw": round(ded),
            "taxable_krw": round(taxable), "tax_krw": round(taxable * rate),
            "fx_estimated_rows": sum(1 for x in rows if x["fx_estimated"]),
            "include_paper": include_paper}


def december_candidates(led: dict, include_paper: bool = False) -> list:
    """보유 중 평가손 종목 — 연 순차익이 공제를 넘으면 실현해 통산할 후보(절감액 = 손실 × 22%, 순차익 초과분 한도)."""
    snaps = [f for f in glob.glob(str(HERE / "data" / "snapshot_*_us.json")) if re.search(r"snapshot_\d{6}_us\.json$", f)]
    if not snaps:
        return []
    best = max(snaps, key=lambda f: (json.loads(Path(f).read_text(encoding="utf-8")).get("generated_at") or ""))
    d = json.loads(Path(best).read_text(encoding="utf-8"))
    if (d.get("svr") or "paper") != "real" and not include_paper:
        return []
    fx = float((d.get("balance") or {}).get("exchange_rate") or 0) or _costs.latest_fx()
    room = max(0.0, float(led["net_gain_krw"]) - float(led["deduction_krw"]))
    out = []
    for p in (d.get("balance") or {}).get("positions") or []:
        qty, avg, px = int(p.get("qty") or 0), float(p.get("avg_price") or 0), float(p.get("price") or 0)
        loss = (px - avg) * qty * fx
        if qty and loss < 0:
            save = min(-loss, room) * 0.22
            out.append({"ticker": p.get("ticker"), "qty": qty, "loss_krw": round(loss), "tax_saving_krw": round(save)})
    return sorted(out, key=lambda x: x["loss_krw"])


def render(led: dict, cands: list = None) -> str:
    L = [f"# 해외주식 양도소득세 원장 — {led['year']}" + (" (모의 기록 — 형식 확인용)" if led["include_paper"] else ""), "",
         f"- 실현 순손익 **{led['net_gain_krw']:,}원** · 기본공제 {led['deduction_krw']:,}원 · 과세표준 {led['taxable_krw']:,}원 · "
         f"예상 세액 **{led['tax_krw']:,}원**(22%)",
         f"- 결제일 환율 미기록(최신 환율로 추정)한 행 {led['fx_estimated_rows']}건 — 신고는 증권사 양도세 자료로 한다", "",
         "| 결제일 | 종목 | 수량 | 매도가 | 환율 | 양도가액 | 취득가액 | 비용 | 손익 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in led["rows"]:
        L.append(f"| {r['settle_date']} | {r['ticker']} | {r['qty']} | {r['sell_px']:,.2f} | {r['sell_fx']:,.2f}"
                 f"{'*' if r['fx_estimated'] else ''} | {r['proceeds_krw']:,} | {r['basis_krw']:,} | {r['fees_krw']:,} | "
                 f"{r['gain_krw']:+,}" + (f" (매수 기록 없는 {r['unmatched_qty']}주)" if r["unmatched_qty"] else "") + " |")
    if not led["rows"]:
        L.append("| — | 실현 없음 | | | | | | | |")
    if cands is not None:
        L += ["", "## 손실 실현 후보(12월)", ""]
        if not cands:
            L.append("- 없음 — 평가손 보유가 없거나 순차익이 공제 이내")
        for c in cands:
            L.append(f"- {c['ticker']} {c['qty']}주 평가손 {c['loss_krw']:+,}원 → 실현 시 세금 절감 최대 {c['tax_saving_krw']:,}원")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="해외주식 양도소득세 원장(추정)")
    ap.add_argument("--year", type=int, help="연간 실현 손익·예상 세액")
    ap.add_argument("--fill-fx", action="store_true", help="결제일이 지난 미국 체결에 환율을 채운다")
    ap.add_argument("--rate", type=float, help="--fill-fx에 쓸 환율(없으면 최신 미국 스냅샷)")
    ap.add_argument("--december", action="store_true", help="손실 실현 후보도 낸다")
    ap.add_argument("--include-paper", action="store_true", help="모의 기록도 포함(형식 확인용)")
    ap.add_argument("--out", help="md로 저장")
    a = ap.parse_args()
    if a.fill_fx:
        n = fill_fx(a.rate, include_paper=a.include_paper)
        print(f"결제일 환율 채움 {n}건")
    if a.year:
        led = ledger(a.year, a.include_paper)
        text = render(led, december_candidates(led, a.include_paper) if a.december else None)
        print(text)
        if a.out:
            Path(a.out).write_text(text + "\n", encoding="utf-8")
    if not (a.fill_fx or a.year):
        ap.error("--year 또는 --fill-fx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
