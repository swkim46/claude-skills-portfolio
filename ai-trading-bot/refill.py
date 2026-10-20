#!/usr/bin/env python3
"""
매도 뒤 재투자 pass — 승인 뒤 따로 나간 매도(장중 `stops.py --send` · 감사 소급 · 재전송)가 판 돈을 **같은 run 안에서** 다시 쓴다.

왜(2026-10-15 사용자): "손절 매도 이후에 매수가 없어서 의도치 않은 현금 비중 증가가 있었다 — 매도 이후에 필요 시 매수를 더 할 수 있게."
10-14 미국 run은 매수 단계가 끝난 01:42에 VST 추적 매도(약 $17,400)가 나간 뒤 다시 사는 경로가 없어 투자비중이 95% → 79%로 떨어졌다.
매도 전용 pass가 체결된 매도로 재투자 대기 기록(`open` · 저장소 `ex_refill` 키 `<st>_<mkt>` — 옛 `journal/refill_<st>_<mkt>.json`)을
남기고(`stops.record_refill`), 이 스크립트가 채운다. 기록은 한 트랜잭션 안에서 읽고-고치고-쓴다(stops·stage가 같은 기록을 고친다).

    python3 refill.py --market us --stamp 261014                  # ① 새 잔고·시세 스냅샷 → risk_guard --refill → 재투자 승인서
    python3 execute.py signals/approved_261014_us_refill_0215.json                        # ② dry-run
    python3 execute.py signals/approved_261014_us_refill_0215.json --send --note <노트>   # ③ 전송(fill까지)
    python3 refill.py --market us --stamp 261014 --done signals/approved_261014_us_refill_0215.json   # ④ 체결 확정 → consumed

- 스냅샷은 **지금 잔고**(`kis_client.balance_for` — 판 돈이 현금에 들어 있다)와 **지금 시세**(`ingest.collect_prices` — 유니버스·보유·벤치마크)다.
  캡처 스냅샷(`data/snapshot_<st>_<mkt>.json`)은 읽기만 하고 다시 쓰지 않는다(`verify_numbers`·`theses check`의 원본).
  재투자 스냅샷의 잔고·보유는 저장소에도 남긴다(`ex_balance`·`ex_position` · phase 'refill').
- 심사는 같은 시그널의 매수 제안 + `refill_candidates`(제안과 같은 검증을 받은 "현금이 생기면 다음에 살 것")만 — 모델 SELL은 다시 내지 않는다.
- 같은 시그널·같은 주문 근거 검증·같은 한도 파일 위에서 나간다 — 이미 통과한 `dispatch` 토큰 위의 추가 집행이다(새 게이트 없음).
  run 마지막 `stage.py close`가 `open`이 남으면 닫지 않고, execute·no_trade 게이트가 그 점검표 파일을 본다.
- 못 채우면(시그널이 `refill_max_age_minutes`(기본 300)를 넘음 · 장이 30분 안에 닫힘 · 승인 매수 0) `stage.py refill-defer --why`로 미룬다.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import db

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
JOURNAL = HERE / "journal"          # 그날 거래 기록(trades_*.json) — 재투자 대기 기록은 저장소(ex_refill)에 있다
SIGNALS = HERE / "signals"
DATA = HERE / "data"
EVID = DATA / "run_evidence"
MIN_LEFT_MIN = 30          # 장 마감까지 이보다 적게 남았으면 미룬다 — 체결 추격(fill)이 들어갈 시간


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rf_name(st: str, mk: str) -> str:
    """재투자 기록의 이름(옛 파일명 그대로 — 승인서·스냅샷의 `refill_of`와 보고 문구가 이 이름을 쓴다 · 저장소 키는 `<st>_<mk>`)."""
    return f"refill_{st}_{mk}.json"


def _rf_update(st: str, mk: str, fn) -> dict:
    """재투자 기록 하나를 한 트랜잭션 안에서 읽고(fn으로) 고쳐 쓴다 — 그 사이 stops.record_refill이 사건을 덧붙여도 잃지 않는다."""
    with db.tx():
        d = db.refill_get(st, mk) or {}
        fn(d)
        db.refill_put(st, mk, d)
    return d


def build(market: str, st: str, now: datetime = None, client=None, session: dict = None) -> tuple:
    """(종료코드, 승인서 경로|None, 한 줄). 0 = 승인서를 냈거나 할 일 없음 · 2 = 미뤄야 한다(사유를 refill-defer로)."""
    import risk_guard as rg
    now = now or datetime.now(KST)
    mk, MK = market.lower(), market.upper()
    rf_name = _rf_name(st, mk)
    rf = db.refill_get(st, mk) or {}
    if rf.get("status") != "open":
        return 0, None, f"재투자 대기 없음 — {rf_name} 상태 {rf.get('status') or '기록 없음'}"
    sig_p, cap_p = SIGNALS / f"signal_{st}_{mk}.json", DATA / f"snapshot_{st}_{mk}.json"
    if not sig_p.exists():
        return 2, None, f"시그널이 없다({sig_p.name}) — 재투자할 근거가 없으니 refill-defer로 미룬다"
    if session is None:
        from kis_client import market_session
        session = market_session(MK, now)
    if not session.get("is_open"):
        return 2, None, f"{MK} 정규장이 닫혀 있다 — refill-defer로 미룬다"
    left = (session["close"] - now).total_seconds() / 60 if session.get("close") else None
    if left is not None and left < MIN_LEFT_MIN:
        return 2, None, f"장 마감까지 {left:.0f}분 — 체결 추격 시간이 없다. refill-defer로 미룬다"
    if client is None:
        from kis_client import KisClient
        client = KisClient.for_mode()
    from kis_client import balance_for
    import ingest
    wl = db.watchlist_doc()                       # 감시 목록 — 저장소 dim_company(config/watchlist.json은 렌더본)
    bal, err = balance_for(client, MK, wl, with_fx=True, now=now)
    if bal.get("cash") is None:
        return 2, None, f"현금을 못 읽었다({err or '구매력 미확인'}) — refill-defer로 미룬다"
    cap = _load(cap_p)
    prices = dict(cap.get("prices") or {})
    fresh, perr = ingest.collect_prices(client, MK, wl, bal.get("positions") or [])
    prices.update(fresh)
    tag = now.strftime("%H%M")
    snap = {"market": MK, "generated_at": now.isoformat(), "kind": "refill", "refill_of": rf_name,
            "capture_ref": str(cap_p.relative_to(HERE)) if cap_p.exists() else None,
            "svr": cap.get("svr"), "account_mode": cap.get("account_mode"), "balance": bal, "prices": prices,
            "price_errors": perr, "day_pnl_pct": cap.get("day_pnl_pct")}
    snap_p = EVID / f"refill_snap_{st}_{mk}_{tag}.json"
    _save(snap_p, snap)
    try:                                          # 잔고·보유를 저장소에도(기록 실패가 재투자 심사를 막지 않는다 — 심사 입력은 스냅샷 파일)
        db.balance_put(st, MK, "refill", bal)
        db.positions_put(st, MK, "refill", bal.get("positions") or [])
    except db.TestGuard:                          # 테스트가 임시 저장소 없이 왔다 — 삼키지 않는다
        raise
    except Exception as e:                        # noqa: BLE001
        print(f"  ★ 재투자 스냅샷 저장소 기록 실패({type(e).__name__}: {str(e)[:120]})", file=sys.stderr)
    out_p = SIGNALS / f"approved_{st}_{mk}_refill_{tag}.json"
    try:
        rg.run(sig_p, snap_p, out_p, refill=True)
    except rg.Rejection as e:
        _rf_update(st, mk, lambda d: d.setdefault("attempts", []).append(
            {"at": now.isoformat(timespec="seconds"), "snapshot": snap_p.name, "rejected": str(e)[:300]}))
        return 2, None, f"재투자 심사 거부 — {e} → refill-defer로 미룬다"
    ap = _load(out_p)
    buys = [o for o in ap.get("orders") or [] if o.get("action") == "BUY"]
    _rf_update(st, mk, lambda d: d.setdefault("attempts", []).append(
        {"at": now.isoformat(timespec="seconds"), "snapshot": snap_p.name, "approved": out_p.name, "buys": len(buys),
         "cash": bal.get("cash")}))
    if not buys:
        return 2, out_p, f"재투자 승인 매수 0건({out_p.name}) — 거부 사유를 보고 refill-defer로 미룬다"
    return 0, out_p, (f"재투자 승인서 {out_p.name} — 매수 {len(buys)}건 · 다음: execute.py dry-run → --send --note <노트> → "
                      f"refill.py --market {mk} --stamp {st} --done signals/{out_p.name}")


def done(market: str, st: str, approved: str, now: datetime = None) -> tuple:
    """(종료코드, 한 줄) — 재투자 승인서의 체결된 매수를 세어 재투자 기록을 `consumed`로. 체결 0이면 1(open 그대로)."""
    now = now or datetime.now(KST)
    mk = market.lower()
    name = Path(approved).name
    rf_name = _rf_name(st, mk)
    if not db.refill_get(st, mk):
        return 2, f"재투자 기록이 없다: {rf_name}"
    rows = (_load(JOURNAL / f"trades_{st}_{mk}.json").get("trades") or [])
    bought = []
    for t in rows:
        if t.get("action") != "BUY" or str(t.get("approved_ref") or "").split("/")[-1] != name:
            continue
        from stops import filled_of                      # 체결 확정 형식 둘(filled_qty · delta)을 같이 읽는다(2026-10-15)
        q, px = filled_of(t)
        if q > 0:
            bought.append({"ticker": t.get("ticker"), "qty": q, "avg_price": px})
    if not bought:
        return 1, f"{name}의 체결된 매수 0건 — {rf_name}은 open 그대로(다시 돌리거나 refill-defer로 미룬다)"
    spent = sum(x["qty"] * x["avg_price"] for x in bought)
    _rf_update(st, mk, lambda d: d.update(status="consumed", consumed_at=now.isoformat(timespec="seconds"), consumed_by=name,
                                          bought=bought, spent=round(spent, 2)))
    what = ", ".join("%s %d주" % (x["ticker"], x["qty"]) for x in bought)
    return 0, f"재투자 완료 — {what} · {spent:,.2f} → {rf_name} consumed (노트 §11에 [재투자] 줄)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--done", help="체결 확정 뒤 — 재투자 승인서 경로")
    a = ap.parse_args()
    if a.done:
        rc, msg = done(a.market, a.stamp, a.done)
    else:
        rc, _, msg = build(a.market, a.stamp)
    print(("★ " if rc else "") + msg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
