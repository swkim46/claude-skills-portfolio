#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run 사이 손절·목표 감시 — 보유 논지의 **가격 매도 조건**을 지금 시세로 판정하고, 닿았으면 판다.

왜: run은 하루 두 번(국내 09:30 · 미국 개장 5분 뒤 — `sessions.slot_for`)뿐이라 그 사이 손절선에 닿아도 아무도 안 본다. 9/16 국내 run은
13:15 시세 81,500 = 한화오션 손절선을 보고도 "종가 기준 판정은 다음 run"이라며 안 팔았다
(사용자: "손절선에 왔으면 종가가 아니더라도 팔아야지"). 이 스크립트는 LLM 없이 결정론으로 돈다:
시세 → `risk_guard.run(discipline_only=True)`(논지 매도 조건 판정) → `execute.py --send`(fill 포함).

사용(Desktop 루틴 · cron `*/20 0-5,9-15,22-23 * * 1-6` — 10-11부터 사용자가 꺼 둠 · run이 다시 켜지 않는다):
    요일 1-6 — 금요일 밤 미국장은 토요일 05:00(표준시 06:00) KST에 닫힌다. 시 0-5 — 11월 서머타임 종료 뒤
    미국장 마감이 06:00 KST로 밀린다. 장 밖 회차(월 새벽·토 낮)는 `market_session`이 닫힘으로 보고 그냥 끝난다.
    python3 stops.py --market auto --send
장이 닫혀 있으면 아무것도 하지 않고 0으로 끝난다. `--send`가 없으면 판정만 찍는다(dry-run).

장중 터치(run이 부른다):
    python3 stops.py --market kr --intraday            # 오늘 고가·저가로 판정만 → data/run_evidence/intraday_touch_*.json

미국 주간거래 손절(실계좌 · 국내 run이 부른다 — 09:30 캡처면 주간 창 전이라 `stage.py close`에서 — 2026-10-12 사용자 결정 "손절 전용으로"):
    python3 stops.py --market us --daytime --send      # 미국 보유의 손절·추적 다리를 주간 세션 시세로 판정 → 주간거래 지정가 매도
주간 창(KST 10:00–17:00 서머타임 · 18:00 표준시)이 아니거나 모의·조회 전용 계좌면 판정만 남긴다. 목표(이익실현) 다리와
신규 매수는 주간거래로 내지 않는다 — 정규장 run의 몫이다. 안 채워진 매도는 fill이 창 마감 전에 취소하고, 다리는 소진되지 않아
그날 밤 정규장 run이 다시 판정한다.
현재가가 아니라 **오늘 저가·고가**로 판정한다 — 감시 루프가 멈췄거나 20분 사이에 닿았다 돌아온 터치를 잡는다.

원장(논지·감시 목록·감사 소급·재투자 대기)과 감시 기록(stops_log)은 저장소(`db.py`)에서 읽고 쓴다(2026-10-17).
스냅샷·승인서는 파일로 남는다(risk_guard의 입력이고 run 증거다). 일봉은 `db.domestic_daily`·`db.overseas_daily`로만 받는다.
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import (KisClient, KisError, market_session, us_daytime_session, order_excd, EXCD_ORDER,
                        DAYTIME_QUOTE_EXCD, balance_for)
import risk_guard as rg
import db

HERE = Path(__file__).parent
EVID = HERE / "data" / "run_evidence"
SIGNALS = HERE / "signals"
KST = timezone(timedelta(hours=9))
PY = sys.executable


def held_theses(market: str) -> list:
    """이 시장의 held 논지(저장소 논지 원장)."""
    return [t for t in db.theses_all() if t.get("status") == "held"
            and str(t.get("market", "")).upper() == market.upper()]


def _watchlist() -> dict:
    """감시 목록 — 저장소 `dim_company`(config/watchlist.json과 같은 모양 · 그 파일은 렌더본)."""
    return db.watchlist_doc() or {}


def _record_snapshot(st: str, market: str, phase: str, bal: dict) -> None:
    """이 pass가 읽은 잔고·보유를 저장소에도 남긴다(`ex_balance`·`ex_position` · phase 'stops'). 스냅샷 파일은 그대로 쓴다.
    기록 실패가 판정·매도를 막지 않는다 — 판정의 입력은 스냅샷 파일이다."""
    try:
        db.balance_put(st, market, phase, bal)
        db.positions_put(st, market, phase, bal.get("positions") or [])
    except db.TestGuard:                             # 테스트가 임시 저장소 없이 왔다 — 삼키지 않는다
        raise
    except Exception as e:                           # noqa: BLE001
        print(f"  ★ 잔고 스냅샷 저장소 기록 실패({type(e).__name__}: {str(e)[:120]}) — 판정은 그대로 간다", file=sys.stderr)


FILLED_VERDICTS = ("FILLED", "PARTIAL")


def filled_of(t: dict) -> tuple:
    """거래 행 → (체결 수량, 체결 가격). 확정이 FILLED·PARTIAL이 아니면 (0, 0.0).
    ★ 체결 확정(`fill`) 형식은 둘이다 — `filled_qty`·`avg_price`가 있거나, 잔고 증감 `delta`와 `limit_price`만 남는다
    (10-15 국내 run: 삼성전기 손절 매도의 확정이 `delta: -1 · verdict: FILLED`뿐이라 재투자 기록이 '체결 0건'으로 빠졌다).
    수량은 filled_qty → |delta| → 주문 수량, 가격은 avg_price → 주문가 → 지정가 순(`loss_review.fills`와 같은 규칙)."""
    f = t.get("fill") or {}
    if not f or str(f.get("verdict") or "").upper() not in FILLED_VERDICTS:
        return 0, 0.0
    q = int(float(f.get("filled_qty") or abs(float(f.get("delta") or 0)) or t.get("qty") or 0))
    p = float(f.get("avg_price") or t.get("price") or f.get("limit_price") or 0)
    return q, p


def record_refill(market: str, st: str, approved_name: str, approved: dict, now: datetime = None) -> dict:
    """매도 전용 pass가 판 뒤 **재투자 대기 기록**(저장소 `ex_refill` 키 `<st>_<mkt>` · 옛 `journal/refill_<st>_<mkt>.json`)
    — 체결된 매도만 센다. 기록한 사건(dict) 또는 {}.

    ★ 2026-10-15 사용자: "손절 매도 이후에 매수가 없어서 의도치 않은 현금 비중 증가가 있었다 — 매도 이후 필요 시 매수를 더 할 수 있게."
    10-14 미국은 매수 단계 뒤 01:42 VST 추적 매도(약 $17,400)가 현금으로 남았다. 이 파일이 '판 돈이 아직 일하지 않는다'를 남기고,
    본 세션이 `refill.py`(→ `risk_guard --refill`)로 채우거나(`consumed`) 못 채운 사유를 적는다(`deferred` → 다음 run 이어받기 `## 재투자 대기`).
    `stage.py close`는 `open`이 남으면 run을 닫지 않는다. 건조 실행·주문 0 pass는 부르지 않는다(승인서를 지운다).
    ★ 저장소(2026-10-17): 읽고-고치고-쓰기를 한 트랜잭션으로 묶는다 — `stage.py refill-defer`·`refill.py`가 같은 기록을 고친다."""
    now = now or datetime.now(KST)
    need = approved.get("refill_need") or {}
    tp = HERE / "journal" / f"trades_{st}_{market.lower()}.json"      # 그날 거래 기록(파일 산출물)
    try:
        rows = json.loads(tp.read_text(encoding="utf-8")).get("trades") or []
    except (OSError, json.JSONDecodeError):
        rows = []
    fills = []
    for t in rows:
        if t.get("action") != "SELL" or str(t.get("approved_ref") or "").split("/")[-1] != approved_name:
            continue
        q, px = filled_of(t)
        if q > 0:
            fills.append({"ticker": t.get("ticker"), "qty": q, "avg_price": px, "source": t.get("source")})
    if not fills:
        return {}
    proceeds = sum(x["qty"] * x["avg_price"] for x in fills if x.get("source") != "funding")
    ev = {"at": now.isoformat(timespec="seconds"), "approved_ref": approved_name, "sells": fills,
          "proceeds": round(proceeds, 2), "target_invested_pct": need.get("target_invested_pct"),
          "based_on": need.get("based_on"), "invested_pct_after": need.get("invested_pct_after"),
          "gap_amt_after": need.get("gap_amt"), "short": need.get("short"), "why": need.get("why")}
    with db.tx():
        d = db.refill_get(st, market) or {"stamp": st, "market": market.lower(), "status": "none", "events": []}
        d["events"] = [e for e in d.get("events") or [] if e.get("approved_ref") != approved_name] + [ev]
        if need.get("short") is not False:             # 모자라거나(True) 잴 수 없으면(None) 본 세션이 판정해야 한다
            d["status"] = "open"
        d["updated_at"] = ev["at"]
        db.refill_put(st, market, d)
    print(("★ 재투자 필요 — " if d["status"] == "open" else "  · 재투자 판정 — ")
          + f"{', '.join(x['ticker'] for x in fills)} 매도 대금 {proceeds:,.2f} · {need.get('why') or '목표 미확인'} "
          + f"→ 저장소 ex_refill {db.run_id(st, market)}")
    return ev


def build_snapshot(client: KisClient, market: str, now: datetime) -> dict:
    """보유 잔고 + 보유 종목 현재가만 담은 미니 스냅샷(risk_guard가 읽는 형태).
    ★ 잔고는 수집과 같은 `balance_for`(2026-10-15) — 미국도 현금·환율·현금 공유 칸이 있어야 매도 뒤 gap을 잴 수 있다."""
    bal, _err = balance_for(client, market, _watchlist(), with_fx=False, now=now)
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


def _hilo_snapshots(client: KisClient, market: str, now: datetime, day: str) -> tuple:
    """보유 종목의 오늘 저가·고가로 만든 스냅샷 둘 + 일봉 원자료. 일봉이 오늘 것이 아니면 그 행을 쓰고 표시한다.
    일봉은 `db.domestic_daily`·`db.overseas_daily`로 받는다(반환 모양은 브로커와 같다 · 같은 구간 재조회는 저장소에서)."""
    bal = client.domestic_balance() if market == "KR" else client.overseas_balance()
    bars = {}
    for p in bal.get("positions", []):
        tic = p.get("ticker")
        try:
            if market == "KR":
                # 장중 고가·저가는 지금 값이어야 한다 — 저장소의 10분 캐시를 건너뛴다(ttl_min=0 · 받은 값은 저장)
                rows = db.domestic_daily(client, tic, day, day, ttl_min=0, need_final=False) or \
                    db.domestic_daily(client, tic, "", day, ttl_min=0, need_final=False)   # 장중 값(미확정으로 저장 · 마감 뒤 확정)
            else:
                quote = {v: k for k, v in EXCD_ORDER.items()}.get((p.get("excd") or "NASD").upper(), "NAS")
                rows = db.overseas_daily(client, tic, excd=quote, ttl_min=0, need_final=False)
            bars[tic] = rows[0] if rows else None
        except (KisError, KeyError, TypeError, ValueError) as e:
            bars[tic] = {"error": f"{type(e).__name__}: {str(e)[:80]}"}
    snaps = {}
    for which in ("low", "high"):
        b2 = json.loads(json.dumps(bal))
        prices = {}
        for p in b2.get("positions", []):
            bar = bars.get(p.get("ticker")) or {}
            px = float(bar.get(which) or 0)
            prices[p.get("ticker")] = {"price": px or None, "bar_date": bar.get("date"), "which": which}
            if px > 0:
                p["price"] = px
                p["eval_amt"] = px * int(p.get("qty") or 0)
                if p.get("avg_price"):
                    p["pnl_pct"] = (px - float(p["avg_price"])) / float(p["avg_price"]) * 100
        snaps[which] = {"market": market, "generated_at": now.isoformat(), "kind": f"stops_intraday_{which}",
                        "balance": b2, "prices": prices, "day_pnl_pct": None}
    return snaps, bars


def _entry_touches(market: str, bars: dict, client: KisClient, day: str) -> list:
    """armed·held 논지(저장소 논지 원장)의 아직 발화하지 않은 가격 진입 조건(`price <= N`·`price >= N`)이 오늘 닿았는가 — 보고만."""
    import re as _re
    rows = db.theses_all()
    out = []
    for t in rows:
        if t.get("status") not in ("armed", "held") or str(t.get("market", "")).upper() != market:
            continue
        tic = t.get("ticker")
        for e in t.get("entry_triggers") or []:
            m = _re.fullmatch(r"\s*price\s*(<=|>=)\s*([\d.]+)\s*", str(e.get("check") or ""))
            if not m or e.get("fired"):
                continue
            bar = bars.get(tic)
            if bar is None and market == "KR":
                try:
                    rows2 = db.domestic_daily(client, tic, day, day, ttl_min=0, need_final=False)
                    bar = bars[tic] = rows2[0] if rows2 else {}
                except (KisError, KeyError, TypeError, ValueError):
                    bar = bars[tic] = {}
            if not bar or "error" in bar:
                continue
            lvl = float(m.group(2))
            hit = (m.group(1) == "<=" and 0 < float(bar.get("low") or 0) <= lvl) or \
                  (m.group(1) == ">=" and float(bar.get("high") or 0) >= lvl)
            if hit:
                out.append({"thesis_id": t.get("id"), "ticker": tic, "trigger_id": e.get("id"),
                            "check": e.get("check"), "size_pct": e.get("size_pct"),
                            "bar_date": bar.get("date"), "low": bar.get("low"), "high": bar.get("high")})
    return out


def intraday(market: str, send: bool, stamp: str = "") -> dict:
    """오늘 저가·고가로 손절·목표 터치를 판정한다(감시 루프 공백 대비 · run이 1단에서 부른다).

    ★ 2026-10-01: 20분 감시 루프 기록이 09-26 이후 없었다(세션 전용 cron 만료). 그 사이 닿았다 돌아온
    두산퓨얼셀 x1·테스 x3·S-Oil e1을 run이 KIS 고저를 손으로 두 번 다시 조회해 찾았고, 승인 행에 논지 id가
    없어 6단이 원장을 손으로 맞췄다. 저가 스냅샷은 `<=` 조건을, 고가 스냅샷은 `>=` 조건을 정확히 잡는다 —
    반대쪽 스냅샷에서 발화하는 경우도 실제로 닿은 경우뿐이라 둘을 합쳐도 오탐이 없다.
    """
    now = datetime.now(KST)
    sess = market_session(market, now)
    day = sess["session_date"].strftime("%Y%m%d")
    st = stamp or sess["session_date"].strftime("%y%m%d")
    row = {"ts": now.isoformat(), "market": market, "mode": "intraday", "open": sess["is_open"],
           "checked": 0, "fired": [], "entries": [], "sent": 0, "rc": None}
    theses = held_theses(market)
    client = KisClient.for_mode()                   # 판정은 읽기만 — 전송은 execute.py가 계좌 모드로 한다
    snaps, bars = _hilo_snapshots(client, market, now, day)
    tag = now.strftime("%H%M")
    EVID.mkdir(parents=True, exist_ok=True)
    orders, seen, header = [], set(), {}
    for which, snap in snaps.items():
        sp = EVID / f"stops_intraday_{st}_{market.lower()}_{tag}_{which}.json"
        sp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
        out = SIGNALS / f"approved_{st}_{market.lower()}_intraday_{tag}_{which}.json"
        rg.run(None, sp, out, discipline_only=True)
        ap_ = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        out.unlink(missing_ok=True)
        sp.unlink(missing_ok=True)
        if ap_:
            header = {k: v for k, v in ap_.items() if k not in ("orders", "rejected")}
        for o in ap_.get("orders") or []:
            if o.get("source") == "audit_reversal":
                continue                # 감사 소급은 터치가 아니다 — 고가·저가 가격으로 내지 않는다(현재가로 `stops.py --send`·5단 risk_guard가 판다)
            k = (o.get("ticker"), o.get("trigger_id") or (o.get("reasons") or [""])[0])
            if k not in seen:
                seen.add(k)
                o["touch_basis"] = which
                orders.append(o)
    row["checked"] = len(theses)
    row["fired"] = [{"ticker": o["ticker"], "qty": o["qty"], "price": o["price"], "thesis_id": o.get("thesis_id"),
                     "trigger_id": o.get("trigger_id"), "full_exit": o.get("full_exit"),
                     "basis": o.get("touch_basis"), "why": (o.get("reasons") or [""])[0]} for o in orders]
    row["entries"] = _entry_touches(market, bars, client, day)
    report = EVID / f"intraday_touch_{st}_{market.lower()}.json"
    report.write_text(json.dumps({"generated_at": now.isoformat(), "market": market, "session_date": day,
                                  "bars": bars, "sell_touches": row["fired"], "entry_touches": row["entries"]},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        row["report"] = str(report.relative_to(HERE))
    except ValueError:
        row["report"] = str(report)
    if not orders:
        row["why"] = "오늘 고가·저가로 닿은 매도 조건 없음"
        return row
    if not send:
        row["why"] = "--send 없음 — 판정만(5단이 규율 SELL로 올린다)"
        return row
    out = SIGNALS / f"approved_{st}_{market.lower()}_intraday_{tag}.json"
    # ★ 2026-10-13: 합친 승인 파일에 risk_guard가 쓴 머리(한도 해시·스키마·계좌 모드·시장·주문당 상한)를 그대로 싣는다.
    #   예전에는 `orders`만 담아 execute.preflight가 '승인 당시 None'으로 **항상** 거부했다(10-13 01:32 MU·NVDA·LRCX·SNDK 4건).
    out.write_text(json.dumps({**header, "orders": orders, "rejected": [], "kind": "stops_intraday",
                               "generated_at": now.isoformat()},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    rc = subprocess.call([PY, str(HERE / "execute.py"), str(out), "--send", "--stamp", st, "--max-sec", "540"])
    row["rc"] = rc
    row["sent"] = len(orders)
    row["trades"] = f"journal/trades_{st}_{market.lower()}.json"
    return row


def watch(market: str, send: bool, stamp: str = "") -> dict:
    now = datetime.now(KST)
    sess = market_session(market, now)
    # ★ FB-261003-8 — 루프 점검마다 `mode: loop`를 남긴다(하트비트). run 1단이 마지막 loop 시각으로 감시 공백을 보고한다.
    row = {"ts": now.isoformat(), "market": market, "mode": "loop", "open": sess["is_open"], "checked": 0,
           "fired": [], "sent": 0, "rc": None}
    if not sess["is_open"]:
        row["why"] = f"장외 — {sess['why']}"
        return row
    theses = held_theses(market)
    try:                                         # 감사 소급 대기 행(2026-10-13) — 논지가 없어도 장중이면 같은 run에 판다
        n_rev = len(rg.reversals_pending(market))
    except db.TestGuard:                         # 테스트가 임시 저장소 없이 왔다 — 삼키지 않는다
        raise
    except Exception:                            # noqa: BLE001
        n_rev = 0
    if not theses and not n_rev:
        row["why"] = "held 논지 없음"
        return row
    client = KisClient.for_mode()
    snap = build_snapshot(client, market, now)
    st = stamp or sess["session_date"].strftime("%y%m%d")
    tag = now.strftime("%H%M")
    EVID.mkdir(parents=True, exist_ok=True)
    snap_path = EVID / f"stops_{st}_{market.lower()}_{tag}.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    _record_snapshot(st, market, "stops", snap.get("balance") or {})
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
    ev = record_refill(market, st, out.name, approved, now)   # 체결된 매도만 — rc와 무관하게 실제 체결을 센다
    if ev:
        row["refill"] = {"proceeds": ev["proceeds"], "short": ev.get("short")}
    return row


def daytime(send: bool, stamp: str = "") -> dict:
    """미국 보유의 **손절·추적** 다리를 주간거래 세션 시세로 판정하고, 닿았으면 주간거래 지정가로 판다.

    ★ 왜(2026-10-12 사용자 결정): 하루 두 번 run 시각에만 사고판다 — 그런데 미국 보유는 국내 run(09:30~11:00) 때 정규장이
    닫혀 있어 손절선을 넘어도 밤 22:35까지 12시간을 그냥 들고 있었다. 주간거래(KST 10:00~)가 그 구멍을 메운다.
    **매도 전용**이고 이익실현 목표 다리는 쓰지 않는다 — 주간 세션은 거래가 얇아 손실을 막는 데만 쓴다.
    """
    now = datetime.now(KST)
    sess = us_daytime_session(now)
    row = {"ts": now.isoformat(), "market": "US", "mode": "daytime", "session": "daytime", "open": sess["is_open"],
           "checked": 0, "fired": [], "sent": 0, "rc": None}
    if not sess["is_open"]:
        row["why"] = f"주간거래 창 밖 — {sess['why']}"
        return row
    theses = held_theses("US")
    if not theses:
        row["why"] = "미국 held 논지 없음"
        return row
    client = KisClient.for_mode()
    bal, _err = balance_for(client, "US", _watchlist(), now=now)   # 현금·환율 — 매도 뒤 gap(재투자 필요액)을 재려면 있어야 한다
    prices = {}
    for p in bal.get("positions", []):
        tic = p.get("ticker")
        q_ex = DAYTIME_QUOTE_EXCD.get(order_excd(p.get("excd") or "NASD"), "BAQ")
        try:
            q = client.overseas_price(tic, excd=q_ex)
            px = float(q.get("price") or 0)
        except (KisError, KeyError, TypeError, ValueError) as e:
            prices[tic] = {"price": None, "error": f"{type(e).__name__}: {str(e)[:80]}"}
            continue
        prices[tic] = {"price": px or None, "change_pct": q.get("change_pct"), "quote_excd": q_ex}
        if px > 0:
            p["price"] = px
            p["eval_amt"] = px * int(p.get("qty") or 0)
            if p.get("avg_price"):
                p["pnl_pct"] = (px - float(p["avg_price"])) / float(p["avg_price"]) * 100
    st = stamp or now.strftime("%y%m%d")
    tag = now.strftime("%H%M")
    EVID.mkdir(parents=True, exist_ok=True)
    snap = {"market": "US", "generated_at": now.isoformat(), "kind": "stops_daytime", "session": "daytime",
            "balance": bal, "prices": prices, "day_pnl_pct": None}
    snap_path = EVID / f"stops_daytime_{st}_us_{tag}.json"
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    _record_snapshot(st, "US", "stops", bal)
    out = SIGNALS / f"approved_{st}_us_daytime_{tag}.json"
    rg.run(None, snap_path, out, discipline_only=True)
    approved = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    allowed = [o for o in approved.get("orders") or [] if o.get("action") == "SELL" and o.get("leg_kind") in ("stop", "trail")]
    skipped = [o for o in approved.get("orders") or [] if o not in allowed]
    row["checked"] = len(theses)
    row["fired"] = [{"ticker": o["ticker"], "qty": o["qty"], "price": o["price"], "thesis_id": o.get("thesis_id"),
                     "trigger_id": o.get("trigger_id"), "leg_kind": o.get("leg_kind"),
                     "why": (o.get("reasons") or [""])[0]} for o in allowed]
    if skipped:
        row["skipped"] = [{"ticker": o["ticker"], "leg_kind": o.get("leg_kind"),
                           "why": "목표 다리 — 주간거래로 팔지 않는다(정규장 run의 몫)"} for o in skipped]
    if not allowed:
        row["why"] = "주간 시세로 닿은 손절·추적 다리 없음"
        out.unlink(missing_ok=True)
        return row
    if client.mode != "real" or not send:
        row["why"] = (f"판정만 — 계좌 모드 {client.mode}" if client.mode != "real" else "--send 없음 — 판정만") \
                     + " · 정규장 run(22:35)이 다시 판정한다"
        out.unlink(missing_ok=True)
        return row
    approved["orders"] = [{**o, "session": "daytime"} for o in allowed]
    approved["session"] = "daytime"
    out.write_text(json.dumps(approved, ensure_ascii=False, indent=2), encoding="utf-8")
    rc = subprocess.call([PY, str(HERE / "execute.py"), str(out), "--send", "--stamp", st, "--max-sec", "540"])
    row["rc"] = rc
    row["sent"] = len(allowed)
    row["approved"] = str(out.relative_to(HERE))
    row["trades"] = f"journal/trades_{st}_us.json"
    ev = record_refill("US", st, out.name, approved, now)
    if ev:
        row["refill"] = {"proceeds": ev["proceeds"], "short": ev.get("short")}
    if rc:
        row["daytime_rejected"] = True
        row["why"] = f"주간거래 매도 미확정·거부(rc={rc}) — 다리는 살아 있고 정규장 run이 다시 판정한다"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="run 사이 손절·목표 감시(결정론)")
    ap.add_argument("--market", choices=["auto", "kr", "us"], default="auto")
    ap.add_argument("--send", action="store_true", help="닿았으면 실제로 판다(없으면 판정만)")
    ap.add_argument("--stamp", default="", metavar="YYMMDD")
    ap.add_argument("--intraday", action="store_true",
                    help="현재가 대신 오늘 고가·저가로 판정(감시 루프 공백·장중 터치 확인 — run 1단)")
    ap.add_argument("--daytime", action="store_true",
                    help="미국 주간거래 세션 시세로 손절·추적 다리만 판정·매도(실계좌 · 국내 run이 부른다)")
    a = ap.parse_args()
    markets = ["KR", "US"] if a.market == "auto" else [a.market.upper()]
    if a.daytime:
        markets = ["US"]
    worst = 0
    for m in markets:
        try:
            row = (daytime(a.send, a.stamp) if a.daytime else
                   intraday(m, a.send, a.stamp) if a.intraday else watch(m, a.send, a.stamp))
        except (KisError, OSError, json.JSONDecodeError, sqlite3.Error) as e:   # 한 시장의 오류가 다른 시장 감시를 막지 않는다
            row = {"ts": datetime.now(KST).isoformat(), "market": m, "error": f"{type(e).__name__}: {str(e)[:120]}"}
            worst = max(worst, 2)
        try:
            db.log_append("stops_log", row)          # 감시 기록(하트비트 포함) — 저장소 op_stops(추가만)
        except sqlite3.Error as e:                   # 기록 실패가 다음 시장 감시·출력을 막지 않는다
            print(f"[{m}] ★ 감시 기록을 저장소에 못 남겼다({type(e).__name__}: {e})", file=sys.stderr)
            worst = max(worst, 2)
        fired = row.get("fired") or []
        print(f"[{m}] " + (row.get("error") or row.get("why") or
                            f"손절·목표 {len(fired)}건 → 전송 {row.get('sent', 0)} (rc={row.get('rc')})"))
        for x in fired:
            print(f"   SELL {x['ticker']} x{x['qty']} @{x['price']:,.2f} — {x['why']}"
                  + (f" · 터치 기준 {x['basis']} · 논지 {x.get('thesis_id')} {x.get('trigger_id')}" if x.get("basis") else ""))
        for e in row.get("entries") or []:
            print(f"   진입 터치 {e['ticker']} {e['trigger_id']} `{e['check']}` (저 {e.get('low')} · 고 {e.get('high')}) — 크기는 run이 판정")
        if row.get("report"):
            print(f"   → {row['report']}")
        if row.get("rc"):
            worst = max(worst, int(row["rc"]))
    return worst


if __name__ == "__main__":
    sys.exit(main())
