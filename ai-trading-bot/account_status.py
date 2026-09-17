#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
계좌 현황 — **run이 끝날 때마다 보고의 마지막에 붙는 블록**을 파일에서 만든다.

    python3 account_status.py --market us --stamp 260916 [--refresh] [--out data/run_evidence/account_status_260916_us.md]

왜 있는가 (2026-09-17 사용자 지시): "항상 끝날 때마다 지금 계좌 현황(주식/현금 비율, 수익률)을
정리해서 출력하라." 보고는 기억이 아니라 파일에서 나와야 하므로(`SKILL.md` 〈보고〉), 이 스크립트가
`journal/equity_curve.jsonl`·`config/benchmark.json`·최신 스냅샷의 환율만 읽어 **시장별 + 포트폴리오
합산** 한 표를 찍고, 같은 내용을 `data/run_evidence/account_status_<stamp>_<mkt>.md`로 남긴다 —
게이트(`execute`·`no_trade`)가 그 파일을 본다. 보고자는 이 출력을 **그대로** 옮긴다(재계산 금지).

- `--refresh`: 찍기 전에 `journal.py --daily`를 **양 시장** 돌려 오늘 행을 새로 쓴다(브로커 호출).
  run의 마지막이라 브로커를 다시 불러도 "노트 안 숫자 불일치" 문제는 없다 — 노트는 이미 확정됐다.
  한쪽이 실패하면(잔고 TR 500 등) 그 시장은 마지막 행으로 물러서고 **★ 낡음**을 표에 찍는다.
- 합산의 환율은 최신 US 스냅샷(`snapshot_<6자리>_us.json`의 `exchange_rate`)이다 — 파일명 정렬이
  아니라 `generated_at`으로 고른다(리허설 파일 오선택 방지, risk_guard와 같은 규칙).
- 투자비중의 목표·하한은 `config/limits.json`(`target_invested_pct`·`min_invested_pct`)에서 읽는다.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
JOURNAL_DIR = HERE / "journal"
CONFIG_DIR = HERE / "config"
DATA_DIR = HERE / "data"
EVID_DIR = DATA_DIR / "run_evidence"
EQUITY_PATH = JOURNAL_DIR / "equity_curve.jsonl"
BENCHMARK_PATH = CONFIG_DIR / "benchmark.json"
LIMITS_PATH = CONFIG_DIR / "limits.json"
KST = timezone(timedelta(hours=9))
STALE_HOURS = 6   # 이보다 오래된 행은 '낡음'으로 표시한다(장 두 개가 6시간 넘게 떨어져 있다)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:            # noqa: BLE001
        return default


def latest_rows() -> dict:
    """시장별 **마지막** 행(파일 순서 = 기록 순서). 같은 날 여러 행이면 뒤의 것."""
    out = {}
    if not EQUITY_PATH.exists():
        return out
    for line in EQUITY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:        # noqa: BLE001
            continue
        if r.get("equity", 0) and r.get("cash") is not None:
            out[(r.get("market") or "").upper()] = r
    return out


def latest_fx() -> tuple:
    """(환율, as-of) — 최신 US 스냅샷의 `balance.exchange_rate`. 못 찾으면 (None, None)."""
    best, best_at = None, ""
    for f in DATA_DIR.glob("snapshot_*_us.json"):
        if not re.fullmatch(r"snapshot_\d{6}_us\.json", f.name):
            continue
        d = _read_json(f, {}) or {}
        at = d.get("generated_at") or ""
        fx = (d.get("balance") or {}).get("exchange_rate")
        if fx and at > best_at:
            best, best_at = float(fx), at
    return best, (best_at[:16].replace("T", " ") if best_at else None)


def refresh(markets: list) -> dict:
    """`journal.daily`를 돌려 오늘 행을 새로 쓴다. 실패한 시장은 사유를 돌려준다."""
    errs = {}
    try:
        import journal
    except Exception as e:       # noqa: BLE001
        return {m: f"journal import 실패: {e}" for m in markets}
    for m in markets:
        try:
            rc = journal.daily(m.upper())
            if rc:
                errs[m.upper()] = f"journal rc={rc}"
        except Exception as e:   # noqa: BLE001
            errs[m.upper()] = f"{type(e).__name__}: {str(e)[:80]}"
    return errs


def _age_h(ts: str, now: datetime) -> float:
    try:
        t = datetime.fromisoformat(ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=KST)
        return (now - t).total_seconds() / 3600
    except Exception:            # noqa: BLE001
        return 9e9


def _pct(x, digits=2, sign=True):
    if x is None:
        return "—"
    return f"{x:+.{digits}f}%" if sign else f"{x:.{digits}f}%"


def _pnl(x):
    return f"{x:+.1f}%" if isinstance(x, (int, float)) else "—"


def build(market: str, stamp: str, rows: dict, errs: dict) -> str:
    """읽는 사람 기준의 짧은 블록 — 시장마다 세 줄(자산·보유·수익률), 마지막에 합산 한 줄."""
    now = datetime.now(KST)
    limits = _read_json(LIMITS_PATH, {}) or {}
    target = limits.get("target_invested_pct")
    floor = limits.get("min_invested_pct")
    fx, fx_at = latest_fx()
    L = [f"■ 계좌 현황 — {now.strftime('%Y-%m-%d %H:%M')} KST (run {market.lower()}:{stamp})", ""]
    krw_total = krw_inv = 0.0
    parts_ok = True
    for m, label, cur_fmt in (("US", "[미국 USD]", lambda v: f"{v:,.0f}"), ("KR", "[한국 KRW]", lambda v: f"{v:,.0f}")):
        r = rows.get(m)
        if not r:
            L.append(f"{label} 기록 없음")
            L.append("")
            parts_ok = False
            continue
        eq = float(r.get("equity") or 0)
        cash = float(r.get("cash") or 0)
        inv = max(eq - cash, 0.0)
        inv_pct = inv / eq * 100 if eq else 0.0
        age = _age_h(r.get("ts") or "", now)
        stale = f"  ★ {age:.0f}시간 전 기록" if age > STALE_HOURS else ""
        if errs.get(m):
            stale += f" · 갱신 실패({errs[m]})"
        L.append(f"{label}  자산 {cur_fmt(eq)} · 주식 {cur_fmt(inv)} ({inv_pct:.0f}%) · 현금 {cur_fmt(cash)} ({100 - inv_pct:.0f}%){stale}")
        pos = r.get("positions") or []
        if pos:
            items = []
            for p in pos:
                nm = p.get("ticker") if m == "US" else (p.get("name") or p.get("ticker"))
                items.append(f"{nm} {p.get('qty')}주 {_pnl(p.get('pnl_pct'))}")
            L.append(f"  보유 {len(pos)}종목 — " + " · ".join(items))
        else:
            L.append("  보유 없음")
        b = r.get("benchmark") or {}
        bench = (b.get("ticker") if m == "US" else b.get("name")) or b.get("ticker") or "벤치마크"
        cum, cb, ex = r.get("cum_pnl_pct"), r.get("cum_benchmark_pct"), r.get("excess_pct")
        line = f"  수익률 오늘 {_pct(r.get('day_pnl_pct'))} · 누적 {_pct(cum)}"
        if cb is not None:
            line += f" ({bench} {_pct(cb)} → 초과 {_pct(ex)}p)"
        L.append(line)
        L.append("")
        if m == "US":
            if fx:
                krw_total += eq * fx; krw_inv += inv * fx
            else:
                parts_ok = False
        else:
            krw_total += eq; krw_inv += inv
    if parts_ok and krw_total:
        inv_pct = krw_inv / krw_total * 100
        tail = ""
        if target is not None:
            tail = (f"  ← 목표 {target:.0f}%" + (f"·하한 {floor:.0f}%" if floor is not None else "")
                    + (f" (미달 {target - inv_pct:.1f}%p)" if inv_pct < target else " (목표 이상)"))
        L.append(f"[합산(원화)]  자산 {krw_total:,.0f}원 · 주식:현금 = {inv_pct:.1f}% : {100 - inv_pct:.1f}%{tail}")
        L.append(f"  환율 {fx:,.2f}" + (f" ({fx_at[5:]})" if fx_at else "") + " · 출처 journal/equity_curve.jsonl")
    else:
        L.append("[합산(원화)]  못 쟀다 — 한 시장의 행 또는 환율이 없다('없음'이 아니라 '못 쟀다') · 주식:현금 = ?")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="계좌 현황 — 보고 마지막 블록")
    ap.add_argument("--market", choices=["kr", "us"], required=True, help="이번 run의 시장")
    ap.add_argument("--stamp", required=True, help="YYMMDD")
    ap.add_argument("--refresh", action="store_true", help="찍기 전에 journal.py --daily를 양 시장 돌린다(브로커 호출)")
    ap.add_argument("--out", help="기본 data/run_evidence/account_status_<stamp>_<mkt>.md")
    a = ap.parse_args()
    errs = refresh(["US", "KR"]) if a.refresh else {}
    rows = latest_rows()
    text = build(a.market.upper(), a.stamp, rows, errs)
    out = Path(a.out) if a.out else EVID_DIR / f"account_status_{a.stamp}_{a.market}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\n→ {out.relative_to(HERE) if out.is_absolute() and HERE in out.parents else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
