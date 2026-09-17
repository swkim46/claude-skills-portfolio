#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run 사이 손절·목표 감시 — 보유 논지의 **가격 매도 조건**을 지금 시세로 판정하고, 닿았으면 판다.

왜: run은 하루 두 번(10:33·22:35)뿐이라 그 사이 손절선에 닿아도 아무도 안 본다. 9/16 국내 run은
13:15 시세 81,500 = 한화오션 손절선을 보고도 "종가 기준 판정은 다음 run"이라며 안 팔았다
(사용자: "손절선에 왔으면 종가가 아니더라도 팔아야지"). 이 스크립트는 LLM 없이 결정론으로 돈다:
시세 → `risk_guard.run(discipline_only=True)`(논지 매도 조건 판정) → `execute.py --send`(fill 포함).

사용(Desktop 루틴 · cron `*/20 0-4,9-15,22-23 * * 1-5`):
    python3 stops.py --market auto --send
장이 닫혀 있으면 아무것도 하지 않고 0으로 끝난다. `--send`가 없으면 판정만 찍는다(dry-run).
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import KisClient, KisError, market_session, EXCD_ORDER
import risk_guard as rg

HERE = Path(__file__).parent
EVID = HERE / "data" / "run_evidence"
SIGNALS = HERE / "signals"
LOG = HERE / "journal" / "stops_log.jsonl"
KST = timezone(timedelta(hours=9))
PY = sys.executable


def held_theses(market: str) -> list:
    store = HERE / "journal" / "theses.json"
    if not store.exists():
        return []
    try:
        rows = json.loads(store.read_text(encoding="utf-8")).get("theses") or []
    except (OSError, json.JSONDecodeError):
        return []
    return [t for t in rows if t.get("status") == "held"
            and str(t.get("market", "")).upper() == market.upper()]


def build_snapshot(client: KisClient, market: str, now: datetime) -> dict:
    """보유 잔고 + 보유 종목 현재가만 담은 미니 스냅샷(risk_guard가 읽는 형태)."""
    bal = client.domestic_balance() if market == "KR" else client.overseas_balance()
    prices = {}
    for p in bal.get("positions", []):
        tic = p.get("ticker")
        try:
            if market == "KR":
                q = client.domestic_price(tic)
            else:
                quote = {v: k for k, v in EXCD_ORDER.items()}.get((p.get("excd") or "NASD").upper(), "NAS")
                q = client.overseas_price(tic, excd=quote)
            prices[tic] = {"price": float(q.get("price") or 0), "change_pct": q.get("change_pct")}
            if prices[tic]["price"] > 0:
                p["price"] = prices[tic]["price"]
                p["eval_amt"] = p["price"] * int(p.get("qty") or 0)
                if p.get("avg_price"):
                    p["pnl_pct"] = (p["price"] - float(p["avg_price"])) / float(p["avg_price"]) * 100
        except (KisError, KeyError, TypeError, ValueError) as e:
            prices[tic] = {"price": None, "error": f"{type(e).__name__}: {str(e)[:80]}"}
    return {"market": market, "generated_at": now.isoformat(), "kind": "stops",
            "balance": bal, "prices": prices, "day_pnl_pct": None}


def watch(market: str, send: bool, stamp: str = "") -> dict:
    now = datetime.now(KST)
    sess = market_session(market, now)
    row = {"ts": now.isoformat(), "market": market, "open": sess["is_open"], "checked": 0,
           "fired": [], "sent": 0, "rc": None}
    if not sess["is_open"]:
        row["why"] = f"장외 — {sess['why']}"
        return row
    theses = held_theses(market)
    if not theses:
        row["why"] = "held 논지 없음"
        return row
    client = KisClient()
    snap = build_snapshot(client, market, now)
    st = stamp or sess["session_date"].strftime("%y%m%d")
    tag = now.strftime("%H%M")
    EVID.mkdir(parents=True, exist_ok=True)
    snap_path = EVID / f"stops_{st}_{market.lower()}_{tag}.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    out = SIGNALS / f"approved_{st}_{market.lower()}_stops_{tag}.json"
    rg.run(None, snap_path, out, discipline_only=True)
    approved = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    orders = approved.get("orders") or []
    row["checked"] = len(theses)
    row["fired"] = [{"ticker": o["ticker"], "qty": o["qty"], "price": o["price"],
                     "why": (o.get("reasons") or [""])[0]} for o in orders]
    row["approved"] = str(out.relative_to(HERE))
    if not orders:
        row["why"] = "닿은 조건 없음"
        out.unlink(missing_ok=True)                 # 빈 승인서는 남기지 않는다
        return row
    if not send:
        row["why"] = "--send 없음 — 판정만"
        out.unlink(missing_ok=True)                 # 보내지 않은 승인서는 남기지 않는다(전송된 것만 signals/에)
        row.pop("approved", None)
        return row
    rc = subprocess.call([PY, str(HERE / "execute.py"), str(out), "--send", "--stamp", st,
                          "--max-sec", "540"])
    row["rc"] = rc
    row["sent"] = len(orders)
    row["trades"] = f"journal/trades_{st}_{market.lower()}.json"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="run 사이 손절·목표 감시(결정론)")
    ap.add_argument("--market", choices=["auto", "kr", "us"], default="auto")
    ap.add_argument("--send", action="store_true", help="닿았으면 실제로 판다(없으면 판정만)")
    ap.add_argument("--stamp", default="", metavar="YYMMDD")
    a = ap.parse_args()
    markets = ["KR", "US"] if a.market == "auto" else [a.market.upper()]
    worst = 0
    for m in markets:
        try:
            row = watch(m, a.send, a.stamp)
        except (KisError, OSError, json.JSONDecodeError) as e:
            row = {"ts": datetime.now(KST).isoformat(), "market": m, "error": f"{type(e).__name__}: {str(e)[:120]}"}
            worst = max(worst, 2)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        fired = row.get("fired") or []
        print(f"[{m}] " + (row.get("error") or row.get("why") or
                            f"손절·목표 {len(fired)}건 → 전송 {row.get('sent', 0)} (rc={row.get('rc')})"))
        for x in fired:
            print(f"   SELL {x['ticker']} x{x['qty']} @{x['price']:,.2f} — {x['why']}")
        if row.get("rc"):
            worst = max(worst, int(row["rc"]))
    return worst


if __name__ == "__main__":
    sys.exit(main())
