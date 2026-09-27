#!/usr/bin/env python3
"""
체결 확정 — 접수된 주문을 **체결·부분체결·취소·만료·거부 중 하나로 확정**한다.

왜 있나: 주문번호는 접수의 증거지 체결의 증거가 아니다. 예전 6단은 전송 직후 잔고를 한 번
대조하고 미체결이면 "주문을 걸어뒀다"로 적고 run을 닫았다 — 그러면 그 주문이 체결됐는지
소멸했는지 아무도 모르는 채 다음 run이 시작된다. *실사례(2026-09-15 VST): 00:23 접수 →
00:29 미체결로 게이트 통과 → 사용자 지시 뒤 00:38 재조회에서 체결 확인.* 그래서 이제
**run은 주문이 종결 상태가 될 때까지 닫히지 않는다**(사용자 지시 2026-09-16).

무엇을 하나: 주문마다 체결 TR을 폴링하고, 안 닿으면 승인가 ±`fill_chase_max_pct` 안에서
현재가로 **정정**(수량은 승인 금액을 넘지 않게 내린다), `fill_wait_minutes`가 지나면 잔량을
**취소**한다. **살아 있는 주문은 다시 보내지 않는다** — 두 번 산다. 대신 **취소가 브로커 TR로
확정된 뒤**(잔량 0·취소 표시)에는 승인 금액 안에서 최신가로 **`fill_reorder_max`회 재발주**한다
(2026-09-17 사용자 지시). 정정을 거부하는 서버(모의: `[40660000] 잔량정정취소만 가능`)에서는
정정 대신 곧바로 취소→재발주로 간다 — 20분을 기다렸다가 0주로 끝내는 대신.

사용:
    python3 fill.py journal/trades_260916_us.json --note analysis/분석노트_260915_us_v1_0.md
    (종료코드 0 = 전건 종결 · 3 = 미확정 잔존 → 다시 부르면 파일에서 이어간다)
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import (KisClient, KisError, market_session, order_excd, quote_excd, snap_kr_price,
                        EXCD_ORDER)

HERE = Path(__file__).parent
JOURNAL_DIR = HERE / "journal"
LIMITS_PATH = HERE / "config" / "limits.json"
KST = timezone(timedelta(hours=9))

TERMINAL = ("FILLED", "PARTIAL", "CANCELLED", "EXPIRED", "REJECTED")

DEFAULTS = {"fill_wait_minutes": 20, "fill_poll_sec": 20, "fill_chase_after_sec": 90,
            "fill_chase_max": 3, "fill_chase_max_pct": 0.5,
            # 취소 확정 뒤 재발주 — 횟수 · 승인가 대비 허용 이탈 · 재발주 건의 대기
            "fill_reorder_max": 1, "fill_reorder_max_pct": 1.0, "fill_reorder_wait_minutes": 10}


def load_limits() -> dict:
    try:
        return json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def fill_cfg(limits: dict) -> dict:
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        if limits.get(k) is not None:
            out[k] = limits[k]
    return out


def _parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def is_open_trade(t: dict) -> bool:
    """확정이 필요한 건인가 — 전송됐고(SENT/UNKNOWN) 아직 종결 판정이 없는 것."""
    # status는 전송 상태다 — FAILED만 '나가지 않은 것'이다(손수 FILLED로 고쳐 쓴 행도 나간 것).
    if t.get("status") in ("FAILED", None):
        return False
    fl = t.get("fill") or {}
    # ★ execute.py의 **즉시 판정** PARTIAL(잔고 한 번 대조)은 종결이 아니다 — 잔량이 살아 있다. fill.py가 `_finish`로
    #   닫은 건만 `confirmed_at`이 있으므로, 그것이 없는 PARTIAL은 열린 건으로 다시 폴링한다.
    #   (2026-09-21 LG엔솔: 즉시 1주 → 실제 11주 전량 체결인데 PARTIAL 1로 닫혔다)
    if fl.get("verdict") == "PARTIAL" and not fl.get("confirmed_at"):
        return True
    return fl.get("verdict") not in TERMINAL


def _chain(t: dict) -> list:
    """주문번호 체인 — 정정할 때마다 새 번호가 붙는다. 첫 항은 최초 접수."""
    f = t.setdefault("fill", {})
    ch = f.get("order_chain")
    if not ch:
        res = t.get("result") or {}
        first = res.get("order_no") or ""
        orgno = res.get("orgno") or ((res.get("raw") or {}).get("output") or {}).get("KRX_FWDG_ORD_ORGNO", "")
        ch = [{"order_no": first, "orgno": orgno, "qty": t.get("qty"), "price": t.get("price"),
               "ts": t.get("ts"), "why": "최초 접수"}] if first else []
        f["order_chain"] = ch
    return ch


def _status(client, t: dict, order_no: str, excd_hint: str = ""):
    """주문 1건의 브로커 상태. 반환 status dict 또는 None(조회 실패)."""
    try:
        if t.get("market", "KR") == "KR":
            return client.domestic_order_status(order_no, t["ticker"])
        return client.overseas_order_status(order_no, t["ticker"], excd_hint)
    except KisError as e:
        t.setdefault("fill", {})["last_error"] = str(e)[:200]
        return None


def _live_price(client, t: dict, excd: str):
    try:
        if t.get("market", "KR") == "KR":
            return float(client.domestic_price(t["ticker"])["price"])
        return float(client.overseas_price(t["ticker"], excd=quote_excd(excd))["price"])
    except (KisError, KeyError, TypeError, ValueError):
        return None


def chase_price(side: str, market: str, live: float, cur_limit: float,
                approved_px: float, max_pct: float):
    """정정가. 불리한 쪽으로만 옮기고 승인가 ±max_pct 안에 가둔다. 바뀌지 않으면 None."""
    if not live or live <= 0:
        return None
    if side == "BUY":
        bound = approved_px * (1 + max_pct / 100)
        px = min(max(live, cur_limit), bound)
        if market == "KR":
            px = float(snap_kr_price(px, "BUY"))
            if px > bound:                      # 스냅으로 한도를 넘으면 한 틱 내린다
                px = float(snap_kr_price(bound, "SELL"))
        px = round(px, 2)
        return px if px > cur_limit + 1e-9 else None
    bound = approved_px * (1 - max_pct / 100)
    px = max(min(live, cur_limit), bound)
    if market == "KR":
        px = float(snap_kr_price(px, "SELL"))
        if px < bound:
            px = float(snap_kr_price(bound, "BUY"))
    px = round(px, 2)
    return px if px < cur_limit - 1e-9 else None


def chase_qty(side: str, remain: int, filled_amt: float, approved_amt: float, new_px: float) -> int:
    """정정 수량 — 매수는 (승인 금액 − 이미 체결 금액) ÷ 새 가격을 넘지 않는다. 매도는 잔량 그대로."""
    if side != "BUY":
        return remain
    room = approved_amt - filled_amt
    return max(0, min(remain, int(room // new_px))) if new_px > 0 else 0


def _modify(client, t: dict, order_no: str, orgno: str, excd: str, qty: int, price: float,
            cancel: bool) -> dict:
    if t.get("market", "KR") == "KR":
        return client.domestic_modify(order_no, orgno, qty, int(price), cancel=cancel)
    return client.overseas_modify(order_no, t["ticker"], excd, qty, price, cancel=cancel)


def _place_new(client, t: dict, excd: str, qty: int, price: float) -> dict:
    """재발주 — 새 주문번호. 살아 있는 주문이 없을 때만 부른다(호출자가 취소 확정을 먼저 본다)."""
    if t.get("market", "KR") == "KR":
        return client.domestic_order(ticker=t["ticker"], side=t["action"], qty=qty, price=int(price))
    return client.overseas_order(ticker=t["ticker"], side=t["action"], qty=qty,
                                 limit_price=float(price), excd=excd)


def reorder_price(side: str, market: str, live: float, approved_px: float, max_pct: float):
    """재발주 가격 — 최신가. 승인가 ±max_pct 밖이면 None(가격 이탈 — 쫓지 않는다)."""
    if not live or live <= 0 or approved_px <= 0:
        return None
    if side == "BUY":
        bound = approved_px * (1 + max_pct / 100)
        if live > bound + 1e-9:
            return None
        px = float(snap_kr_price(live, "BUY")) if market == "KR" else round(live, 2)
        return px if px <= bound + 1e-9 else None
    bound = approved_px * (1 - max_pct / 100)
    if live < bound - 1e-9:
        return None
    px = float(snap_kr_price(live, "SELL")) if market == "KR" else round(live, 2)
    return px if px >= bound - 1e-9 else None


def _try_reorder(client, t: dict, cfg: dict, now: datetime, excd: str, approved_qty: int,
                 approved_px: float, approved_amt: float, filled: int, filled_amt: float, log) -> str:
    """취소가 **확정된** 자리에서 1회 재발주를 시도한다. 반환: "sent" · "skip:<사유>".

    이중 매수 방지 — ① 호출 조건이 '잔량 0 + 취소 표시'(살아 있는 주문 없음) ② 수량은
    `approved_qty − filled`와 승인 금액 잔여 안 ③ 횟수 `fill_reorder_max` ④ 장중만.
    """
    f = t.setdefault("fill", {})
    reorders = f.setdefault("reorders", [])
    max_n = int(cfg.get("fill_reorder_max", 0) or 0)
    if len(reorders) >= max_n:
        return "skip:재발주 한도 %d회 소진" % max_n
    market = t.get("market", "KR")
    side = t.get("action", "BUY")
    sess = market_session(market, now)
    if now > sess["close"]:
        return "skip:장 마감"
    live = _live_price(client, t, excd)
    px = reorder_price(side, market, live, approved_px, float(cfg.get("fill_reorder_max_pct", 1.0)))
    if px is None:
        return ("skip:가격 이탈 — 시세 %s, 승인가 %.2f ±%.1f%% 밖 — 재발주 안 함(다음 run 재판단)"
                % (f"{live:,.2f}" if live else "미확인", approved_px, float(cfg.get("fill_reorder_max_pct", 1.0))))
    qty = chase_qty(side, approved_qty - filled, filled_amt, approved_amt, px)
    if qty <= 0:
        return "skip:승인 금액 안에서 0주"
    try:
        res = _place_new(client, t, excd, qty, px)
    except (KisError, ValueError) as e:
        f["last_error"] = f"재발주 실패: {e}"[:200]
        return f"skip:재발주 실패 {e}"[:200]
    entry = {"ts": now.isoformat(), "order_no": res.get("order_no", ""), "orgno": res.get("orgno", ""),
             "qty": qty, "price": px, "live": live,
             "why": f"취소 확정 뒤 재발주 {len(reorders) + 1}/{max_n} — " + (f.get("cancel_why") or "")}
    reorders.append(entry)
    _chain(t).append({"order_no": res.get("order_no", ""), "orgno": res.get("orgno", ""),
                      "qty": qty, "price": px, "ts": now.isoformat(), "why": "재발주"})
    # 새 주문의 대기는 여기서 다시 센다. 취소 표시는 옛 주문의 것이므로 지운다.
    f["reorder_started"] = now.isoformat()
    for k in ("cancel_sent", "cancel_why", "cancel_order_no"):
        f.pop(k, None)
    log(f"  ↺ 재발주 {t['ticker']} x{qty} @ {px:,.2f} (시세 {live:,.2f}) → 주문번호 {res.get('order_no')}")
    return "sent"


def _finish(t: dict, verdict: str, filled: int, avg: float, now: datetime, source: str, note: str = ""):
    f = t.setdefault("fill", {})
    f.update({"verdict": verdict, "filled_qty": filled, "avg_price": avg,
              "confirmed_at": now.isoformat(), "source": source})
    if note:
        f["note"] = note
    f.pop("last_error", None)


def summarize(record: dict, now: datetime) -> dict:
    trades = record.get("trades") or []
    sent = [t for t in trades if t.get("status") not in ("FAILED", None)]
    verdicts = [(t.get("fill") or {}).get("verdict") for t in sent]
    summ = {
        "at": now.isoformat(),
        "sent": len(sent),
        "filled": verdicts.count("FILLED"),
        "partial": verdicts.count("PARTIAL"),
        "cancelled": verdicts.count("CANCELLED"),
        "expired": verdicts.count("EXPIRED"),
        # 브로커 거부(status FAILED)는 애초에 걸린 주문이 없어 여기서 세지 않고 fill_summary가 센다.
        "rejected": verdicts.count("REJECTED"),
        "unknown": sum(1 for v in verdicts if v not in TERMINAL),
    }
    summ["open_orders"] = summ["unknown"]
    return summ


def confirm(path: Path, client=None, cfg: dict = None, now_fn=None, sleep_fn=None,
            max_sec: int = 540, chase: bool = True, note: Path = None, quiet: bool = False) -> int:
    """파일의 미확정 주문을 전부 종결시킨다. 반환 종료코드(0 전건 종결 / 3 미확정 잔존)."""
    now_fn = now_fn or (lambda: datetime.now(KST))
    sleep_fn = sleep_fn or time.sleep
    cfg = cfg or fill_cfg(load_limits())
    record = json.loads(path.read_text(encoding="utf-8"))
    trades = record.get("trades") or []
    todo = [t for t in trades if is_open_trade(t)]
    log = (lambda *a, **k: None) if quiet else (lambda *a, **k: print(*a, **k))

    if not todo:
        log("확정할 주문 없음 — 전건 종결 상태.")
    else:
        if client is None:
            client = KisClient(svr=record.get("svr") or "paper",
                               allow_real=(record.get("svr") == "real"))
        log(f"체결 확정 시작 — 미확정 {len(todo)}건 · 대기 {cfg['fill_wait_minutes']}분 · "
            f"정정 {cfg['fill_chase_max']}회/±{cfg['fill_chase_max_pct']}% · "
            f"취소 확정 뒤 재발주 {cfg.get('fill_reorder_max', 0)}회/±{cfg.get('fill_reorder_max_pct', 1.0)}%")
        t0 = now_fn()
        while True:
            now = now_fn()
            pending = 0
            for t in todo:
                if not is_open_trade(t):
                    continue
                try:
                    _step(client, t, cfg, now, chase, log)
                except Exception as e:                   # noqa: BLE001 — 한 건의 예외가 다른 건의 확정을 막지 않는다
                    t.setdefault("fill", {})["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
                    log(f"  ★ {t.get('ticker')} 폴링 예외 — {type(e).__name__}: {str(e)[:100]} (다음 폴링에 계속)", file=sys.stderr)
                if is_open_trade(t):
                    pending += 1
                else:
                    _close_theses_after_exit(t, now, log)
            path.write_text(json.dumps(_with_summary(record, now_fn(), path), ensure_ascii=False, indent=2),
                            encoding="utf-8")
            if not pending:
                break
            if (now_fn() - t0).total_seconds() + cfg["fill_poll_sec"] > max_sec:
                log(f"★ 호출 예산 {max_sec}s 소진 — 미확정 {pending}건. 다시 부르면 이어간다.",
                    file=sys.stderr)
                break
            sleep_fn(cfg["fill_poll_sec"])

    now = now_fn()
    # 전량 매도가 확정된 행은(이번 호출에서든 이전에든) 논지를 닫는다 — 멱등이다.
    for t in trades:
        if not is_open_trade(t):
            _close_theses_after_exit(t, now, log)
    record = _with_summary(record, now, path)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    summ = record["fill_confirmed"]
    if note:
        write_note_line(Path(note), summ)
    log(_line(summ))
    if summ.get("overfill"):
        log(f"★★ 승인 초과 {summ['overfill']}건 — 사고 기록 {', '.join(summ.get('incidents') or []) or '(기록 실패)'} "
            f"(journal/incidents.jsonl). 복구 주문을 내지 말 것.", file=sys.stderr)
    for t in trades:
        f = t.get("fill") or {}
        if t.get("status") not in ("FAILED", None):
            log(f"  {f.get('verdict', '미확정'):9} {t.get('action')} {t.get('ticker')} "
                f"{t.get('name', '')} 요청 {t.get('qty')} → 체결 {f.get('filled_qty', abs(f.get('delta') or 0) or '?')}"
                + (f" @ {f.get('avg_price'):,.2f}" if f.get("avg_price") else "")
                + (f" · {f.get('note')}" if f.get("note") else ""))
    return 0 if summ["open_orders"] == 0 else 3


def _leg_of(t: dict):
    """이 매도 행이 집행한 (논지 id, 트리거 id). `trigger_id`가 없으면 사유 문구에서 되찾는다(옛 기록)."""
    tid, gid = t.get("thesis_id"), t.get("trigger_id")
    if not (tid and gid):
        for r in (t.get("discipline_reasons") or []):
            m = re.search(r"논지\s+(\S+)\s+(\S+)\s+(?:손절|목표)", str(r))
            if m:
                tid, gid = tid or m.group(1), gid or m.group(2)
                break
    return tid, gid


def _close_theses_after_exit(t: dict, now: datetime, log) -> None:
    """체결된 규율·회전 매도를 논지 원장에 반영한다 — **소진된 다리는 `fired`로 찍고**, 전량이면 논지를 닫는다.

    ★ 왜 다리를 찍나(2026-09-23). `risk_guard.thesis_exit_sells`와 `theses.check`는 `if trg.get("fired"): continue`를
    이미 읽는데 **`fired`를 쓰는 코드가 없었다** — 감시자는 있고 기록이 없었다. 그래서 이미 집행된 목표 다리가
    가격이 그 위에 머무는 한 매 run 다시 발화했다(TSM x3 `price >= 431.68` 50%: 09-21에 6/13주 집행 → 09-22 run이
    잔량 7주의 절반인 4주를 또 승인). **매 run 잔량의 절반씩 사라지는 톱니**이고, `stops.py`는 20분마다 모델 없이
    같은 판정을 보낸다. 체결이 다리를 소진시키는 자리가 여기다(이미 멱등 스윕이라 다시 불려도 안전하다).

    취소·만료·거부는 찍지 않는다 — 목표를 **못 가져간** 것이라 다리는 살아 있어야 한다.
    손절이 나갔는데 논지가 held로 남으면 다음 run이 없는 보유의 매도 조건을 계속 본다.
    """
    f = t.get("fill") or {}
    if t.get("action") != "SELL" or f.get("verdict") not in ("FILLED", "PARTIAL"):
        return
    store = JOURNAL_DIR / "theses.json"
    if not store.exists():
        return
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    changed, marked = 0, []
    tid, gid = _leg_of(t)
    qty = f.get("filled_qty") or abs(int(f.get("delta") or 0)) or t.get("qty")
    px = f.get("avg_price") or t.get("price")
    order_no = (t.get("result") or {}).get("order_no") or ""
    for th in data.get("theses") or []:
        same = (th.get("ticker") == t.get("ticker")
                and str(th.get("market", "")).upper() == str(t.get("market", "")).upper())
        # ① 다리 소진 — 규율·회전 매도가 집행한 트리거를 찍는다(논지 id가 있으면 그것으로, 없으면 종목·시장으로)
        if gid and (th.get("id") == tid or (not tid and same)):
            for trg in th.get("exit_triggers") or []:
                if trg.get("id") != gid or trg.get("fired"):
                    continue
                trg["fired"] = True
                trg["fired_at"] = now.isoformat()
                trg["fired_note"] = (f"{t.get('source')} SELL {qty}주 @{px}"
                                     + (f" · 주문 {order_no}" if order_no else "")
                                     + f" ({f.get('verdict')})")
                marked.append(f"{th.get('id')}.{gid}")
                changed += 1
        # ② 전량 매도면 논지를 닫는다(부분이면 held 유지)
        if (same and th.get("status") == "held"
                and t.get("full_exit") and f.get("verdict") == "FILLED"):
            th["status"] = "closed"
            th.setdefault("history", []).append(
                {"at": now.isoformat(), "status": "closed",
                 "note": f"{t.get('source')} 매도 전량 체결 {f.get('filled_qty')}주 @{f.get('avg_price')}"
                         + (f" — {t.get('discipline_reasons')[0]}" if t.get("discipline_reasons") else "")})
            changed += 1
    if changed:
        store.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if marked:
            log(f"  다리 소진 표시 {', '.join(marked)} — 같은 다리는 다시 발화하지 않는다")
        if t.get("full_exit") and f.get("verdict") == "FILLED":
            log(f"  논지 closed — {t.get('ticker')} 전량 매도 체결")


def _with_summary(record: dict, now: datetime, path: Path = None) -> dict:
    summ = summarize(record, now)
    # 논지 동기화 여부도 **지금** 다시 센다 — 파일에 남은 옛 값이 게이트를 막았다(2026-09-16).
    try:
        import execute as ex
        fills = {t["ticker"]: (t.get("fill") or {}) for t in (record.get("trades") or [])
                 if t.get("action") == "BUY" and t.get("status") != "FAILED"}
        record["thesis_unsynced"] = ex._thesis_unsynced(fills)
    except Exception:                                  # noqa: BLE001 — 동기화 계산이 확정을 막지 않는다
        pass
    fs = record.get("fill_summary") or {}
    fs.update({"filled": summ["filled"], "partial": summ["partial"],
               "cancelled": summ["cancelled"], "expired": summ["expired"],
               "unverified": summ["unknown"],
               "unfilled": 0})            # 미체결은 더 이상 종결 상태가 아니다
    record["fill_summary"] = fs
    # ★ 승인 대비 대조 — 경로를 알면 매 쓰기마다 다시 센다(사고 기록은 멱등).
    rec = None
    if path is not None:
        try:
            finalize(record, Path(path), now)
            rec = record.get("reconcile")
        except Exception as e:                         # noqa: BLE001 — 대조 실패가 확정을 막지 않는다
            print(f"★ 승인 대조 실패({type(e).__name__}: {e}) — 기록은 그대로 쓴다", file=sys.stderr)
    else:
        rec = record.get("reconcile")
    if rec:
        summ["overfill"] = int(rec.get("overfill") or 0)
        summ["incidents"] = list(rec.get("incidents") or [])
    record["fill_confirmed"] = summ
    return record


# ────────────────────────────────────────────────────────── 승인 대비 대조 · 사고
# ★ 왜 — 2026-09-21 두산 46주가 승인 없이 한 번 더 팔렸는데(승인 46 / 집행 92) `execute` 게이트가
#   그대로 열렸다. 접수·체결 건수는 셌지만 **승인 수량과 집행 수량을 대조하는 자리가 없었다.**
#   여기서 종목·방향별 집행 합을 승인 합과 대조하고, 넘으면 사고(`journal/incidents.jsonl`)로
#   자동 기록한다. 게이트는 "초과 0건" 또는 "사고 기록됨"만 통과시킨다 — 조용히 지나가는 길이 없다.

def _resolve_approved(ref: str):
    """옛 기록(승인 스냅샷 없음)의 approved 파일 경로 — 절대경로 → 프로젝트 상대 → signals/<파일명>."""
    if not ref:
        return None
    for cand in (Path(ref), HERE / ref, HERE / "signals" / Path(ref).name):
        if cand.exists():
            return cand
    return None


def _approved_sets(record: dict) -> tuple:
    """(파일명, approved_at)별 승인 주문 스냅샷 — `runs[]`와 최상위에서 한 번씩. (sets, unknown_refs)"""
    entries = list(record.get("runs") or [])
    top = {k: record.get(k) for k in ("approved_ref", "approved_at", "approved_orders")}
    if top.get("approved_ref"):
        entries.append(top)
    sets, unknown = {}, []
    for r in entries:
        ref = r.get("approved_ref")
        if not ref:
            continue
        key = (Path(ref).name, str(r.get("approved_at") or ""))
        if key in sets:
            continue
        orders = r.get("approved_orders")
        if orders is None:                             # 이 형식 이전의 기록 — 파일을 찾아본다
            p = _resolve_approved(ref)
            try:
                orders = [{"ticker": o.get("ticker"), "action": o.get("action"), "source": o.get("source"),
                           "qty": int(o.get("qty") or 0), "price": o.get("price")}
                          for o in (json.loads(p.read_text(encoding="utf-8")).get("orders") or [])] if p else None
            except (OSError, json.JSONDecodeError, ValueError, AttributeError):
                orders = None
        if orders is None:
            if Path(ref).name not in unknown:
                unknown.append(Path(ref).name)
            continue
        sets[key] = orders
    return sets, unknown


def reconcile(record: dict) -> dict:
    """종목·방향별 승인 합 vs 집행 합. 순수 함수 — 파일을 쓰지 않는다."""
    sets, unknown = _approved_sets(record)
    approved = {}
    for orders in sets.values():
        for o in orders:
            k = f"{o.get('ticker')}:{o.get('action')}"
            approved[k] = approved.get(k, 0) + int(o.get("qty") or 0)
    executed, rows_by_key = {}, {}
    sent_rows = 0
    for t in record.get("trades") or []:
        if t.get("status") in ("FAILED", None):
            continue
        sent_rows += 1
        f = t.get("fill") or {}
        q = f.get("filled_qty")
        if q is None:
            q = abs(int(f.get("delta") or 0)) if f.get("verdict") in ("FILLED", "PARTIAL") else 0
        k = f"{t.get('ticker')}:{t.get('action')}"
        executed[k] = executed.get(k, 0) + int(q)
        rows_by_key.setdefault(k, []).append(t)
    known = bool(sets) and not unknown
    over = []
    for k, ex_q in executed.items():
        ap_q = approved.get(k)
        if ap_q is None and not known:                 # 승인을 모르는 건은 초과라고 말할 수 없다
            continue
        ap_q = ap_q or 0
        if ex_q > ap_q:
            tk, act = k.split(":", 1)
            rows = rows_by_key[k]
            over.append({"ticker": tk, "action": act, "approved": ap_q, "executed": ex_q,
                         "rows": [t.get("ts") for t in rows],
                         "order_nos": [(t.get("result") or {}).get("order_no") for t in rows]})
    prev = record.get("reconcile") or {}
    return {"at": datetime.now(KST).isoformat(),
            "refs": [k[0] for k in sets], "unknown_refs": unknown, "approved_known": known,
            "approved": approved, "executed": executed,
            "overfill_list": over, "overfill": len(over), "sent_rows": sent_rows,
            "approved_orders_n": sum(len(v) for v in sets.values()),
            "incident_recorded": bool(prev.get("incident_recorded")) and bool(over),
            "incidents": list(prev.get("incidents") or []) if over else []}


def _incident_rows() -> list:
    p = JOURNAL_DIR / "incidents.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def record_incidents(record: dict, path: Path, now: datetime) -> list:
    """초과가 있으면 `journal/incidents.jsonl`에 `INC-<stamp>-<n>`으로 남긴다 — (stamp, market, 종목, 방향)당 한 번.
    초과를 만든 행(누적이 승인을 넘어선 뒤의 행)에 `incident_id`를 찍는다. 기존 `incident` 서술은 보존."""
    rec = record.get("reconcile") or {}
    if not rec.get("overfill"):
        rec["incident_recorded"] = False
        rec["incidents"] = []
        return []
    m = re.match(r"trades_(\d{6})_(kr|us)", Path(path).name)
    stamp = m.group(1) if m else format(now, "%y%m%d")
    market = (m.group(2) if m else str((record.get("trades") or [{}])[0].get("market") or "")).lower()
    existing = _incident_rows()
    store = JOURNAL_DIR / "incidents.jsonl"
    ids = []
    for ov in rec["overfill_list"]:
        key = (stamp, market, ov["ticker"], ov["action"])
        hit = next((e for e in existing
                    if (e.get("stamp"), e.get("market"), e.get("ticker"), e.get("action")) == key), None)
        rows = [t for t in record.get("trades") or []
                if t.get("status") not in ("FAILED", None)
                and (t.get("ticker"), t.get("action")) == (ov["ticker"], ov["action"])]
        if hit:
            iid = hit["id"]
        else:
            iid = f"INC-{stamp}-{1 + sum(1 for e in existing if e.get('stamp') == stamp)}"
            row = {"id": iid, "at": now.isoformat(), "stamp": stamp, "market": market,
                   "ticker": ov["ticker"], "action": ov["action"],
                   "approved": ov["approved"], "executed": ov["executed"],
                   "order_nos": ov["order_nos"], "rows": ov["rows"],
                   "approved_refs": rec.get("refs"), "trades": f"journal/{Path(path).name}",
                   "why": "승인 초과 — 같은 종목·방향의 집행 수량이 승인 합계를 넘었다(중복 전송 의심)",
                   "note": next((t.get("incident") for t in rows if isinstance(t.get("incident"), str)), "")}
            store.parent.mkdir(parents=True, exist_ok=True)
            with store.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            existing.append(row)
        ids.append(iid)
        # 누적이 승인을 넘어선 행부터 사고다 — 첫 46주는 승인된 것이고 둘째 46주가 사고다.
        cum = 0
        for t in rows:
            f = t.get("fill") or {}
            q = f.get("filled_qty")
            if q is None:
                q = abs(int(f.get("delta") or 0)) if f.get("verdict") in ("FILLED", "PARTIAL") else 0
            cum += int(q)
            if cum > ov["approved"] and q:
                t.setdefault("incident_id", iid)
                if not isinstance(t.get("incident"), str):
                    t["incident"] = f"★ 승인 초과 — {iid} (승인 {ov['approved']} / 집행 {ov['executed']})"
    rec["incidents"] = ids
    rec["incident_recorded"] = bool(ids)
    return ids


def finalize(record: dict, path: Path, now: datetime) -> dict:
    """대조 + 사고 기록 + `fill_confirmed`에 초과 건수 복사. `execute.merge_day_record`와 `_with_summary`가 부른다."""
    record["reconcile"] = reconcile(record)
    record_incidents(record, path, now)
    fc = record.get("fill_confirmed")
    if isinstance(fc, dict):
        fc["overfill"] = int(record["reconcile"].get("overfill") or 0)
        fc["incidents"] = list(record["reconcile"].get("incidents") or [])
    return record


def _step(client, t: dict, cfg: dict, now: datetime, chase: bool, log) -> None:
    """주문 1건을 한 번 살핀다 — 종결이면 판정을 쓰고, 아니면 정정·취소 여부를 정한다."""
    f = t.setdefault("fill", {})
    market = t.get("market", "KR")
    side = t.get("action", "BUY")
    excd = order_excd((t.get("proposal") or {}).get("excd") or t.get("excd") or "NASD") \
        if market != "KR" else ""
    chain = _chain(t)
    if not chain:
        _adopt_unknown(client, t, now, log)
        chain = _chain(t)
        if not chain:
            return                                  # 여전히 주문번호를 모른다 → 미확정으로 남는다
    approved_qty = int(t.get("qty") or 0)
    approved_px = float(t.get("price") or 0)
    approved_amt = approved_qty * approved_px

    # 체인 전체의 체결을 합산한다 — 정정하면 잔량이 새 번호로 옮겨가고 옛 번호엔 체결분만 남는다.
    filled, filled_amt, latest = 0, 0.0, None
    for link in chain:
        st = _status(client, t, link["order_no"], excd)
        if st is None:
            return                                  # 조회 실패 — 다음 폴에 다시
        link["status"] = {k: st.get(k) for k in ("found", "filled_qty", "remain_qty", "avg_price",
                                                  "cancelled", "rejected")}
        if st.get("found"):
            filled += st["filled_qty"]
            filled_amt += st["filled_qty"] * (st["avg_price"] or link.get("price") or 0)
            latest = st                             # 마지막으로 **찾은** 주문이 잔량의 주인이다
    tail = chain[-1]
    src = f"{'inquire-daily-ccld' if market == 'KR' else 'inquire-ccnl'} {now:%Y-%m-%d %H:%M}"
    if latest is not None and tail.get("why") in ("재발주", "정정") and not (tail.get("status") or {}).get("found"):
        # 새 번호가 아직 체결 TR에 안 잡힌다 — 옛 번호(취소 표시)만 보고 닫으면 살아 있는 새 주문을
        # 기록에서 잃는다(그 뒤 체결되면 추적 안 되는 보유가 생긴다). 판정하지 않고 다음 폴.
        return

    if latest is None:
        # 모의서버 국내 TR은 당일만 답하고, 가끔 아무것도 안 돌려준다 — 잔고 대조로 물러선다.
        return _fallback_balance(client, t, approved_qty, side, now, log)

    avg = (filled_amt / filled) if filled else 0.0
    remain = int(latest.get("remain_qty") or 0)
    closed_by_us = bool(f.get("cancel_sent") or latest.get("cancelled"))
    if latest.get("rejected") and filled == 0:
        return _finish(t, "REJECTED", 0, 0.0, now, src,
                       f"브로커 거부 — {latest.get('reject_reason') or ''}".strip())
    n_re = len(f.get("reorders") or [])
    re_note = f" · 재발주 {n_re}회" if n_re else ""
    if filled >= approved_qty:
        return _finish(t, "FILLED", filled, avg, now, src, ("전량 체결" + re_note) if n_re else "")
    if remain == 0:
        if closed_by_us:
            # ★ 취소가 브로커 TR로 확정된 자리 — 살아 있는 주문이 없으므로 여기서만 재발주한다.
            why = _try_reorder(client, t, cfg, now, excd, approved_qty, approved_px, approved_amt,
                               filled, filled_amt, log)
            if why == "sent":
                return                              # 새 주문을 다음 폴부터 본다
            skip = why.split(":", 1)[1] if ":" in why else why
            if filled > 0:
                return _finish(t, "PARTIAL", filled, avg, now, src,
                               f"요청 {approved_qty} 중 {filled} 체결 · " + f.get("cancel_why", "잔량 취소")
                               + f" · {skip}" + re_note)
            return _finish(t, "CANCELLED", 0, 0.0, now, src,
                           f.get("cancel_why", "잔량 취소") + f" · {skip}" + re_note)
        if filled > 0:
            # 정정·재발주로 수량을 줄였고 그 수량이 다 체결됐다 — 승인 금액 안에서 끝까지 간 것이다.
            how = "재발주" if n_re else "정정"
            return _finish(t, "FILLED", filled, avg, now, src,
                           f"{how}로 수량 {approved_qty}→{filled} 축소(승인 금액 안) 후 전량 체결" + re_note)
        # 잔량도 체결도 0인데 취소 표시가 없다 — 마감 뒤 소멸로 본다(장중이면 다음 폴에 다시).
        sess = market_session(market, now)
        if now > sess["close"]:
            return _finish(t, "EXPIRED", 0, 0.0, now, src, "장 마감으로 소멸 — 체결 0")
        return

    # 여기부터 잔량이 있다.
    sess = market_session(market, now)
    if f.get("reorder_started"):
        started = _parse_ts(f["reorder_started"])
        wait_min = float(cfg.get("fill_reorder_wait_minutes", 10))
    else:
        started = _parse_ts(t.get("ts") or now.isoformat())
        wait_min = float(cfg["fill_wait_minutes"])
    deadline = started + timedelta(minutes=wait_min)
    if now > sess["close"]:
        # 마감 뒤에도 잔량이 보이면 소멸 예정이다 — 체결분이 있으면 부분체결로 닫는다.
        if filled:
            return _finish(t, "PARTIAL", filled, avg, now, src, f"요청 {approved_qty} 중 {filled} · 잔량 {remain} 마감 소멸")
        return _finish(t, "EXPIRED", 0, 0.0, now, src, f"장 마감 — 잔량 {remain} 소멸")

    if now >= deadline or f.get("cancel_sent"):
        if not f.get("cancel_sent"):
            try:
                res = _modify(client, t, tail["order_no"], tail.get("orgno", ""), excd, remain, 0, cancel=True)
                f["cancel_sent"] = now.isoformat()
                f["cancel_why"] = (f"대기 {wait_min:g}분 초과 — 잔량 {remain} 취소"
                                   + (f" (체결 {filled})" if filled else ""))
                # 취소 번호는 체인에 넣지 않는다 — 잔량의 주인은 원주문이고, 취소 반영은 원주문의
                # 잔량 0·취소 표시로 확인한다(취소 번호는 조회에 안 잡힐 수 있다).
                f["cancel_order_no"] = res.get("order_no", "")
                log(f"  ✂ 취소 전송 {t['ticker']} 잔량 {remain} — {f['cancel_why']}")
            except (KisError, ValueError) as e:
                f["last_error"] = f"취소 실패: {e}"[:200]
                log(f"  ★ 취소 실패 {t['ticker']}: {e}", file=sys.stderr)
            return                                   # 다음 폴에서 취소 반영을 확인한다
        # 취소를 보냈는데 아직 잔량이 보인다 — 다음 폴에 다시(취소가 반영되면 위 분기가 닫는다).
        return

    if not chase:
        return
    chases = f.setdefault("chase", [])
    last_change = _parse_ts(chases[-1]["ts"]) if chases else started
    if len(chases) >= int(cfg["fill_chase_max"]):
        return
    if (now - last_change).total_seconds() < float(cfg["fill_chase_after_sec"]):
        return
    live = _live_price(client, t, excd)
    cur_limit = float(tail.get("price") or approved_px)
    new_px = chase_price(side, market, live, cur_limit, approved_px, float(cfg["fill_chase_max_pct"]))
    if new_px is None:
        return
    new_qty = chase_qty(side, remain, filled_amt, approved_amt, new_px)
    if new_qty <= 0:
        chases.append({"ts": now.isoformat(), "skipped": True, "live": live, "price": new_px,
                       "why": "승인 금액 안에서 0주 — 정정 없이 대기"})
        return
    try:
        res = _modify(client, t, tail["order_no"], tail.get("orgno", ""), excd, new_qty, new_px, cancel=False)
    except (KisError, ValueError) as e:
        # 정정 실패는 **한 번만** 기록하고(예전엔 폴마다 되풀이해 13번 찍혔다) 취소→재발주로 간다.
        f["last_error"] = f"정정 실패: {e}"[:200]
        chases.append({"ts": now.isoformat(), "failed": True, "price": new_px, "qty": new_qty,
                       "live": live, "error": str(e)[:160], "why": "정정 거부 — 취소 뒤 재발주로 전환"})
        log(f"  ★ 정정 실패 {t['ticker']}: {e}", file=sys.stderr)
        if int(cfg.get("fill_reorder_max", 0) or 0) > len(f.get("reorders") or []):
            try:
                cres = _modify(client, t, tail["order_no"], tail.get("orgno", ""), excd, remain, 0, cancel=True)
                f["cancel_sent"] = now.isoformat()
                f["cancel_why"] = f"정정 불가(서버 거부) — 잔량 {remain} 취소 뒤 최신가 재발주"
                f["cancel_order_no"] = cres.get("order_no", "")
                log(f"  ✂ 취소 전송 {t['ticker']} 잔량 {remain} — {f['cancel_why']}")
            except (KisError, ValueError) as ce:
                f["last_error"] = f"취소 실패: {ce}"[:200]
                log(f"  ★ 취소 실패 {t['ticker']}: {ce}", file=sys.stderr)
        return
    entry = {"ts": now.isoformat(), "from_price": cur_limit, "price": new_px, "qty": new_qty,
             "live": live, "order_no": res.get("order_no", ""),
             "why": f"미체결 {int((now - last_change).total_seconds())}s — 현재가 쪽으로 정정"
                    + (f" · 수량 {remain}→{new_qty}(승인 금액 안)" if new_qty < remain else "")}
    chases.append(entry)
    chain.append({"order_no": res.get("order_no") or tail["order_no"],
                  "orgno": res.get("orgno", tail.get("orgno", "")),
                  "qty": new_qty, "price": new_px, "ts": now.isoformat(), "why": "정정"})
    log(f"  ↻ 정정 {t['ticker']} {cur_limit:,.2f}→{new_px:,.2f} x{new_qty} → 주문번호 {res.get('order_no')}")


def _fallback_balance(client, t: dict, approved_qty: int, side: str, now: datetime, log) -> None:
    """상태 TR이 주문을 못 찾을 때 — 전송 전 기준선 대비 잔고 차이로 판정한다(있을 때만)."""
    f = t.setdefault("fill", {})
    before = f.get("qty_before")
    if before is None:
        return
    try:
        bal = client.domestic_balance() if t.get("market", "KR") == "KR" else client.overseas_balance()
    except KisError as e:
        f["last_error"] = str(e)[:200]
        return
    after = next((int(p.get("qty") or 0) for p in bal.get("positions", []) if p.get("ticker") == t["ticker"]), 0)
    delta = after - int(before)
    want = approved_qty if side == "BUY" else -approved_qty
    f["qty_after"] = after
    f["delta"] = delta
    if delta == want:
        avg = next((float(p.get("avg_price") or 0) for p in bal.get("positions", [])
                    if p.get("ticker") == t["ticker"]), 0.0)
        # ★ 매도는 남은 포지션 평단이 체결가가 아니다 — 지정가를 쓴다(2026-09-21 두산 46주: 평단 84,201 vs 실제 85,400).
        if side == "SELL":
            avg = float(t.get("price") or 0) or avg
        _finish(t, "FILLED", approved_qty, avg or float(t.get("price") or 0), now,
                f"balance delta {now:%Y-%m-%d %H:%M} (상태 TR 미조회)")
    elif delta != 0 and abs(delta) < approved_qty:
        f["partial_seen"] = abs(delta)


def _adopt_unknown(client, t: dict, now: datetime, log) -> None:
    """주문번호를 못 읽은 건(UNKNOWN) — 그날 체결내역에서 같은 종목·방향·수량·시각의 주문을 찾는다."""
    # 조회 TR이 주문번호 없이 목록을 주지 않으므로 여기서는 잔고 대조만 시도한다.
    _fallback_balance(client, t, int(t.get("qty") or 0), t.get("action", "BUY"), now, log)


def _line(summ: dict) -> str:
    try:
        import stage
        return stage.gen_fill_line(summ)
    except Exception:                                  # noqa: BLE001 — stage 없이도 돌아간다
        at = summ.get("at", "")[11:16]
        inc = summ.get("incidents") or []
        return (f"<!-- gen:fill -->체결 확정 — 전송 {summ['sent']} · 체결 {summ['filled']} · "
                f"부분 {summ['partial']} · 취소 {summ['cancelled']} · 만료 {summ['expired']} · "
                f"거부 {summ['rejected']} · 미확정 **{summ['open_orders']}건** · "
                f"승인 초과 **{summ.get('overfill', 0)}건**"
                + (f" · 사고 기록 {', '.join(inc)}" if inc else "") + f" ({at} 확인)")


def write_note_line(note: Path, summ: dict) -> bool:
    """§11 절 안의 `<!-- gen:fill -->` 줄을 갱신한다(없으면 절 끝에 추가). 멱등."""
    if not note.exists():
        return False
    text = note.read_text(encoding="utf-8")
    lines = text.split("\n")
    line = _line(summ)
    start = next((i for i, l in enumerate(lines) if l.startswith("## §11")), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    for i in range(start, end):
        if lines[i].startswith("<!-- gen:fill -->"):
            lines[i] = line
            break
    else:
        # 스탬프 앞에 넣는다 — 스탬프가 절의 마지막 표식이라 그 뒤에 쓰면 '스탬프 뒤 내용'이 된다.
        stamp = next((i for i in range(start, end) if lines[i].startswith("<!-- ✓ §11-집행")), None)
        at = stamp if stamp is not None else end
        while at > start and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = ["", line]
    note.write_text("\n".join(lines), encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="접수된 주문을 체결·취소·만료 중 하나로 확정한다")
    ap.add_argument("trades", help="journal/trades_YYMMDD_<mkt>.json")
    ap.add_argument("--note", help="분석노트 — §11에 확정 줄을 쓴다")
    ap.add_argument("--max-sec", type=int, default=540, help="이 호출의 시간 예산(기본 540s)")
    ap.add_argument("--no-chase", action="store_true", help="정정 없이 기다리기만 한다")
    args = ap.parse_args()
    path = Path(args.trades)
    if not path.exists():
        print(f"파일 없음: {path}", file=sys.stderr)
        return 2
    return confirm(path, max_sec=args.max_sec, chase=not args.no_chase,
                   note=Path(args.note) if args.note else None)


if __name__ == "__main__":
    sys.exit(main())
