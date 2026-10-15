#!/usr/bin/env python3
"""
감사 소급 원장 — 주문 뒤 감사가 **주문 결정을 뒤집으면** 산 것을 되돌려 판다(2026-10-13 사용자 결정).

왜: 주문 근거 감사를 주문 **전**에 기다리게 했더니(10-13 국내) 5시간 동안 감사 두 번이 배분 설명의 숫자만 짚었고, 그 사이 시그널이
낡아(302분) 주문이 전부 거부됐다. 사용자: "매매 하고 나서 감사 마무리하는 이전 구조로 · 5차까지 · 매매 의사결정을 바꿔야 하는 감사
결과가 나오면 소급 적용." 기준은 **판정표 재채점**(근거 행이 MISMATCH가 되거나 MATCH·T1/T2 근거가 하나도 안 남음 — risk_guard가
매수를 거부하는 규칙과 같다). 행동은 같은 run에서 되돌림 매도(장이 열려 있으면), 닫혔으면 다음 그 시장 run의 첫 주문.

원장 `journal/audit_reversals.jsonl` — 행을 덧붙이기만 하고 같은 `id`는 **마지막 행**이 현재 상태다.
  {id, ts, stamp, market, ticker, name, kind: buy_unsupported|hold_overturned, qty(남은 수량), filled_qty, why, round,
   fc_rows[], buy_ref, thesis_id, status: pending|filled|void, sold_qty?, note?}
쓰는 곳: fc_track1.py decide --post·reverse(추가) · risk_guard.audit_reversal_sells(SELL 생성 · 보유 0이면 void) ·
fill(체결 확정 시 filled·부분이면 잔량 pending) · stage.py carry(`## 감사 소급` 절) · stops.py watch(대기 행이 있으면 장중에 보낸다).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
NAME = "audit_reversals.jsonl"
SOURCE = "audit_reversal"


def _path(jdir=None) -> Path:
    return Path(jdir or (HERE / "journal")) / NAME


def rows(jdir=None) -> list:
    """id별 마지막 행(현재 상태) — 처음 나온 순서대로."""
    p = _path(jdir)
    cur, order = {}, []
    if not p.exists():
        return []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        rid = r.get("id")
        if not rid:
            continue
        if rid not in cur:
            order.append(rid)
        cur[rid] = {**cur.get(rid, {}), **r}
    return [cur[i] for i in order]


def pending(market: str = "", jdir=None) -> list:
    return [r for r in rows(jdir) if r.get("status") == "pending"
            and (not market or str(r.get("market", "")).upper() == market.upper())]


def _append(row: dict, jdir=None) -> dict:
    p = _path(jdir)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {**row, "ts": datetime.now(KST).isoformat(timespec="seconds")}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def add(stamp: str, market: str, ticker: str, qty: int, why: str, kind: str = "buy_unsupported",
        name: str = "", round_: int = 0, fc_rows=None, buy_ref: str = "", thesis_id: str = "", jdir=None) -> tuple:
    """(행, 새로 썼나). 같은 (stamp, market, ticker, kind)의 살아 있는 행(pending·filled)이 있으면 새로 쓰지 않는다 — 멱등."""
    mk = market.upper()
    for r in rows(jdir):
        if (r.get("stamp") == stamp and str(r.get("market", "")).upper() == mk and r.get("ticker") == ticker
                and r.get("kind") == kind and r.get("status") in ("pending", "filled")):
            return r, False
    n = 1 + sum(1 for r in rows(jdir) if r.get("stamp") == stamp and str(r.get("market", "")).upper() == mk)
    row = {"id": f"REV-{stamp}-{mk.lower()}-{n}", "stamp": stamp, "market": mk, "ticker": ticker, "name": name,
           "kind": kind, "qty": int(qty), "filled_qty": int(qty), "why": why, "round": round_,
           "fc_rows": list(fc_rows or []), "buy_ref": buy_ref, "thesis_id": thesis_id, "status": "pending"}
    return _append(row, jdir), True


def mark(rid: str, status: str, jdir=None, **extra) -> dict:
    return _append({"id": rid, "status": status, **extra}, jdir)


def settle_trade(t: dict, jdir=None, log=print) -> None:
    """체결이 확정된 되돌림 매도 행으로 원장을 닫는다(fill이 부른다 · 멱등). 전량 → filled · 부분 → 남은 수량으로 pending ·
    취소·만료·거부 → 그대로 pending(다음 run이 다시 보낸다)."""
    if t.get("source") != SOURCE or t.get("action") != "SELL":
        return
    f = t.get("fill") or {}
    v = f.get("verdict")
    if v not in ("FILLED", "PARTIAL"):
        return
    sold = int(f.get("filled_qty") or abs(int(f.get("delta") or 0)) or (t.get("qty") if v == "FILLED" else 0) or 0)
    mk = str(t.get("market", "")).upper()
    order_no = (t.get("result") or {}).get("order_no") or ""
    for r in pending(mk, jdir):
        if r.get("ticker") != t.get("ticker"):
            continue
        if order_no and order_no in (r.get("settled_orders") or []):
            return
        left = max(0, int(r.get("qty") or 0) - sold)
        done = (r.get("settled_orders") or []) + ([order_no] if order_no else [])
        if left == 0 or v == "FILLED":
            mark(r["id"], "filled", jdir, sold_qty=int(r.get("sold_qty") or 0) + sold, qty=0, settled_orders=done)
            log(f"  · 감사 소급 {r['id']} {t.get('ticker')} {sold}주 매도 체결 — 닫음")
        else:
            mark(r["id"], "pending", jdir, sold_qty=int(r.get("sold_qty") or 0) + sold, qty=left, settled_orders=done)
            log(f"  · 감사 소급 {r['id']} {t.get('ticker')} {sold}주 부분 체결 — 잔량 {left}주는 다음 run이 보낸다")
        return


def carry_block(market: str, jdir=None) -> str:
    """이어받기 파일의 `## 감사 소급` 절 — 대기 행이 있으면 이 run의 risk_guard가 자동으로 판다."""
    p = pending(market, jdir)
    head = "## 감사 소급\n\n"
    if not p:
        return head + "(대기 중인 되돌림 매도 없음)\n"
    lines = [head + "직전 run의 주문 뒤 감사가 매수 근거를 뒤집었다 — **이 run의 risk_guard가 아래를 규율 매도로 자동 전송한다**"
             "(사람 확인 없음 · 보유 0이면 void로 닫힌다).\n",
             "| id | 종목 | 남은 수량 | 사유 | 판정표 행 |", "|---|---|---|---|---|"]
    for r in p:
        lines.append(f"| {r['id']} | {r.get('ticker')} {r.get('name') or ''} | {r.get('qty')} | {str(r.get('why') or '')[:120]} "
                     f"| {', '.join(r.get('fc_rows') or []) or '—'} |")
    return "\n".join(lines) + "\n"
