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
import contextlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import (KisClient, KisError, market_session, order_excd, quote_excd, snap_kr_price,
                        EXCD_ORDER, buy_qty_within, us_daytime_session, next_trading_day, DAYTIME_QUOTE_EXCD)
import db

HERE = Path(__file__).parent
JOURNAL_DIR = HERE / "journal"          # 그날 거래 기록(trades_*.json)이 있는 곳 — 논지·사고·감사 소급은 저장소(db.py)에 쓴다
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


# ★ 하루 기록 행의 `status` = 전송 상태(2026-10-19 · Codex R01·R02). 종결 상태는 `fill.verdict`다.
#   PENDING  — 보내기 **전에** 적은 의도. 결과가 아직 안 적혔다(그 사이 프로세스가 죽었을 수 있다) → 접수됐을 수 있다.
#   SENT     — 증권사가 접수했다(주문번호 있음).
#   UNKNOWN  — 보낸 뒤 응답을 잃었다(타임아웃·끊김·주문번호 없는 성공) → 접수됐을 수 있다.
#   REJECTED — 증권사가 분명히 거절했다(접수 0) · UNSENT — 우리가 보내지 않았다(가격 이탈·상한·수량 0·검증 오류).
#   FAILED   — 옛 기록의 값(REJECTED·UNSENT를 가르지 않던 시절). 접수 0으로 읽는다.
# PENDING·UNKNOWN은 **해소 전까지 같은 주문도, 같은 종목·방향도 다시 보내지 않는다** — `resolve_pending`이 당일 주문 목록으로 푼다.
UNSENT_STATES = ("FAILED", "REJECTED", "UNSENT")      # 접수된 것이 없다 — 같은 승인으로 다시 보낼 수 있다
UNRESOLVED = ("PENDING", "UNKNOWN")                   # 접수됐을 수 있다 — 해소 전 재전송 금지


class DayLocked(RuntimeError):
    """그날 거래 기록을 다른 프로세스가 쓰고 있다 — 끝난 뒤 다시(종료코드 5). 사람 결정이 아니라 재시도다."""


class DayRecordCorrupt(RuntimeError):
    """그날 거래 기록을 읽을 수 없다 — 빈 기록으로 보고 다시 보내면 이미 나간 주문이 두 번 나간다. 멈춘다."""


_HELD_LOCKS: dict = {}
# 잠금 파일이 이보다 오래됐으면 남은 찌꺼기로 본다 — 거래 기록은 세션 하루치이고 그것을 하루 넘게 쓰는 프로세스는 없다.
DAY_LOCK_STALE_SEC = 24 * 3600


def _lock_info(lp: Path) -> dict:
    try:
        return json.loads(lp.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def day_lock(path: Path, cmd: str = ""):
    """그날 거래 기록 하나에 **한 번에 한 쓰기만**(2026-10-19 · Codex R02). `journal/.trades_<st>_<mk>.lock`을
    O_CREAT|O_EXCL로 잡는다. 같은 프로세스 안에서는 다시 잡을 수 있다(execute --send가 안에서 fill.confirm을 부른다).
    주인 프로세스가 죽었거나 하루가 넘은 잠금은 찌꺼기로 보고 치운다. 다른 살아 있는 쓰기가 있으면 `DayLocked`."""
    path = Path(path)
    lp = path.with_name(f".{path.stem}.lock")
    key = str(lp.absolute())
    if _HELD_LOCKS.get(key):
        _HELD_LOCKS[key] += 1
        try:
            yield lp
        finally:
            _HELD_LOCKS[key] -= 1
        return
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    for _ in range(3):
        try:
            fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            info = _lock_info(lp)
            try:
                age = time.time() - lp.stat().st_mtime
            except OSError:
                continue
            mine_dead = int(info.get("pid") or 0) == os.getpid()           # 이 프로세스가 전에 잡고 못 푼 찌꺼기
            if mine_dead or not _pid_alive(info.get("pid")) or age > DAY_LOCK_STALE_SEC:
                with contextlib.suppress(FileNotFoundError):
                    lp.unlink()
                continue
            raise DayLocked(f"다른 쓰기(pid {info.get('pid')} · {str(info.get('cmd') or '')[:80]}) 진행 중 — "
                            f"끝난 뒤 다시 돌려라({lp.name})")
    if fd is None:
        raise DayLocked(f"잠금을 잡지 못했다({lp.name}) — 잠시 뒤 다시")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "ts": datetime.now(KST).isoformat(),
                   "cmd": (cmd or " ".join(sys.argv))[:200]}, fh, ensure_ascii=False)
    _HELD_LOCKS[key] = 1
    try:
        yield lp
    finally:
        _HELD_LOCKS.pop(key, None)
        with contextlib.suppress(FileNotFoundError):
            lp.unlink()


def read_day(path: Path, strict: bool = True) -> dict:
    """그날 거래 기록. 없으면 {}. **깨졌으면 `DayRecordCorrupt`**(strict) — 빈 기록으로 읽으면 중복 검사가 아무것도
    못 보고 이미 나간 주문을 다시 보낸다(2026-10-19 · Codex R02). 화면 표시용만 strict=False로 {}를 받는다."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as e:
        if strict:
            raise DayRecordCorrupt(f"그날 거래 기록을 읽을 수 없다 — {path} ({type(e).__name__}: {str(e)[:120]}). "
                                   f"빈 기록으로 보고 다시 보내지 않는다 — 파일을 확인·복구한 뒤 다시") from e
        return {}
    if not isinstance(d, dict):
        if strict:
            raise DayRecordCorrupt(f"그날 거래 기록 형식이 아니다 — {path} (최상위 {type(d).__name__})")
        return {}
    return d


def was_sent(t: dict) -> bool:
    """증권사에 걸렸을 수 있는 행인가 — SENT·UNKNOWN·PENDING·(손수 고친) FILLED. 접수 0(거절·미전송·옛 FAILED)과 빈 상태는 아니다."""
    st = t.get("status")
    return st is not None and st not in UNSENT_STATES


def is_open_trade(t: dict) -> bool:
    """확정이 필요한 건인가 — 전송됐고(SENT/UNKNOWN/PENDING) 아직 종결 판정이 없는 것."""
    # status는 전송 상태다 — 접수 0(거절·미전송·옛 FAILED)만 '나가지 않은 것'이다(손수 FILLED로 고쳐 쓴 행도 나간 것).
    if not was_sent(t):
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


def _daytime(t: dict) -> bool:
    """미국 주간거래 행인가(손절·추적 매도 전용 · 2026-10-12)."""
    return t.get("market") == "US" and t.get("session") == "daytime"


def _session(t: dict, now: datetime) -> dict:
    """이 행의 세션 창 — 주간거래 행은 주간 창(KST 10:00–17/18:00), 나머지는 정규장."""
    return us_daytime_session(now) if _daytime(t) else market_session(t.get("market", "KR"), now)


def settle_date(market: str, trade_day) -> str:
    """결제일 — 국내 T+2 · 미국 T+1(2024-05-28 미국 T+1 전환) 거래일. 세금 원장(tax_ledger.py)의 환율 기준일이다."""
    d = trade_day
    for _ in range(2 if (market or "KR").upper() == "KR" else 1):
        d = next_trading_day((market or "KR").upper(), d)
    return d.isoformat()


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
        q = DAYTIME_QUOTE_EXCD.get(order_excd(excd), "BAQ") if _daytime(t) else quote_excd(excd)
        return float(client.overseas_price(t["ticker"], excd=q)["price"])
    except (KisError, KeyError, TypeError, ValueError):
        return None


def chase_price(side: str, market: str, live: float, cur_limit: float,
                approved_px: float, max_pct: float, floor: bool = True):
    """정정가. 불리한 쪽으로만 옮기고 승인가 ±max_pct 안에 가둔다. 바뀌지 않으면 None.
    `floor=False` — **위험 축소 매도**(손절·추적·논지 무효·소급 · `risk_reducing`)는 한도 없이 시세를 따라간다
    (2026-10-19 사용자 결정 · Codex R08). 한도는 추격을 막는 장치인데 손절의 목적은 그 반대다. 횟수·대기 제한은 그대로."""
    if not live or live <= 0:
        return None
    if not floor:
        max_pct = float("inf")
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
    """정정·재발주 수량 — 매수는 (승인 금액 − 이미 체결 금액) ÷ 새 가격의 내림, **내림이 0주면 1주**
    (`buy_qty_within` — 가격은 `chase_price`·`reorder_price`가 이미 이탈 한도 안으로 묶었다). 매도는 잔량 그대로."""
    if side != "BUY":
        return remain
    return buy_qty_within(remain, approved_amt - filled_amt, new_px)


def _modify(client, t: dict, order_no: str, orgno: str, excd: str, qty: int, price: float,
            cancel: bool) -> dict:
    if t.get("market", "KR") == "KR":
        # 원주문이 나간 거래소 구분으로 정정·취소한다(SOR 거부 뒤 KRX로 재전송된 건은 KRX).
        excg = (t.get("result") or {}).get("excg") or None
        kw = {"excg": excg} if excg and excg != getattr(client, "kr_excg", excg) else {}   # 기본과 같으면 넘기지 않는다
        return client.domestic_modify(order_no, orgno, qty, int(price), cancel=cancel, **kw)
    if _daytime(t):
        return client.overseas_daytime_modify(order_no, t["ticker"], excd, qty, price, cancel=cancel)
    return client.overseas_modify(order_no, t["ticker"], excd, qty, price, cancel=cancel)


def _place_new(client, t: dict, excd: str, qty: int, price: float) -> dict:
    """재발주 — 새 주문번호. 살아 있는 주문이 없을 때만 부른다(호출자가 취소 확정을 먼저 본다)."""
    if t.get("market", "KR") == "KR":
        excg = (t.get("result") or {}).get("excg") or None
        kw = {"excg": excg} if excg and excg != getattr(client, "kr_excg", excg) else {}
        return client.domestic_order(ticker=t["ticker"], side=t["action"], qty=qty, price=int(price), **kw)
    if _daytime(t):
        return client.overseas_daytime_order(ticker=t["ticker"], side=t["action"], qty=qty,
                                             limit_price=float(price), excd=excd)
    return client.overseas_order(ticker=t["ticker"], side=t["action"], qty=qty,
                                 limit_price=float(price), excd=excd)


def reorder_price(side: str, market: str, live: float, approved_px: float, max_pct: float, floor: bool = True):
    """재발주 가격 — 최신가. 승인가 ±max_pct 밖이면 None(가격 이탈 — 쫓지 않는다).
    `floor=False`(위험 축소 매도 · 2026-10-19 사용자 결정)면 한도 없이 최신가."""
    if not live or live <= 0 or approved_px <= 0:
        return None
    if not floor:
        return float(snap_kr_price(live, side)) if market == "KR" else round(live, 2)
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
                 approved_px: float, approved_amt: float, filled: int, filled_amt: float, log, persist=None) -> str:
    """취소가 **확정된** 자리에서 1회 재발주를 시도한다. 반환: "sent" · "skip:<사유>".

    이중 매수 방지 — ① 호출 조건이 '잔량 0 + 취소 표시'(살아 있는 주문 없음) ② 수량은
    `approved_qty − filled`와 승인 금액 잔여 안 ③ 횟수 `fill_reorder_max` ④ 장중만
    ⑤ ★ 보내기 **전에** 재발주 의도(번호 없는 링크)를 기록에 넣고 `persist()`로 디스크에 쓴다(2026-10-19 · Codex W01).
       접수 직후 프로세스가 죽어도 다음 실행은 그 링크를 보고 다시 보내지 않고 당일 주문 목록으로 붙이거나(접수) 두 번 없음을
       확인한 뒤 뺀다(미접수 · `_resolve_links`). 분명한 거절이면 의도를 거둔다.
    """
    persist = persist or (lambda: None)
    f = t.setdefault("fill", {})
    reorders = f.setdefault("reorders", [])
    max_n = int(cfg.get("fill_reorder_max", 0) or 0)
    if len(reorders) >= max_n:
        return "skip:재발주 한도 %d회 소진" % max_n
    market = t.get("market", "KR")
    side = t.get("action", "BUY")
    sess = _session(t, now)
    if now > sess["close"]:
        return "skip:장 마감"
    live = _live_price(client, t, excd)
    px = reorder_price(side, market, live, approved_px, float(cfg.get("fill_reorder_max_pct", 1.0)),
                       floor=not t.get("risk_reducing"))
    if px is None:
        return ("skip:가격 이탈 — 시세 %s, 승인가 %.2f ±%.1f%% 밖 — 재발주 안 함(다음 run 재판단)"
                % (f"{live:,.2f}" if live else "미확인", approved_px, float(cfg.get("fill_reorder_max_pct", 1.0))))
    qty = chase_qty(side, approved_qty - filled, filled_amt, approved_amt, px)
    if qty <= 0:
        return "skip:승인 금액 안에서 0주"
    # ★ 보내기 전 기록(W01) — 재발주 이력·체인에 번호 없는 링크를 넣고 디스크에 쓴 뒤에 보낸다. 새 주문의 대기는 여기서 다시
    #   센다. 취소 표시는 옛 주문의 것이므로 지운다(분명한 거절이면 아래에서 되돌린다).
    ts = now.isoformat()
    cancel_why = f.get("cancel_why") or ""
    entry = {"ts": ts, "order_no": "", "pending": True, "qty": qty, "price": px, "live": live,
             "why": f"취소 확정 뒤 재발주 {len(reorders) + 1}/{max_n} — " + cancel_why}
    link = {"order_no": "", "orgno": "", "pending": True, "qty": qty, "price": px, "ts": ts, "why": "재발주"}
    saved = {k: f[k] for k in ("cancel_sent", "cancel_why", "cancel_order_no", "reorder_started") if k in f}
    reorders.append(entry)
    _chain(t).append(link)
    f["reorder_started"] = ts
    for k in ("cancel_sent", "cancel_why", "cancel_order_no"):
        f.pop(k, None)
    persist()
    try:
        res = _place_new(client, t, excd, qty, px)
    except Exception as e:                              # noqa: BLE001 — 아래에서 접수 가능성으로 가른다
        kind = getattr(e, "kind", None) if isinstance(e, KisError) else ("rejected" if isinstance(e, ValueError) else "unknown")
        f["last_error"] = f"재발주 실패({kind}): {e}"[:200]
        if kind != "unknown":
            # 분명히 접수되지 않았다 — 의도를 거두고 원래 상태(취소 확정)로 되돌린다
            reorders.remove(entry)
            _chain(t).remove(link)
            f.pop("reorder_started", None)
            f.update(saved)
            persist()
            return f"skip:재발주 실패 {e}"[:200]
        # ★ 응답을 잃은 재발주 — 증권사가 받았을 수 있다. 번호 없는 링크 그대로 두고 다음 폴링이 당일 주문 목록으로
        #   붙이거나(접수) 두 번 없음을 확인하면 뺀다(미접수 · 2026-10-19 · R01).
        entry["why"] = f"취소 확정 뒤 재발주 {len(reorders)}/{max_n} — 응답 유실(접수 확인 중)"
        persist()
        log(f"  ? 재발주 응답 유실 {t['ticker']} x{qty} @ {px:,.2f} — 다시 보내지 않고 당일 목록으로 확인한다")
        return "sent"
    no = str(res.get("order_no") or "")
    if not _norm_no(no):
        # 성공 응답인데 주문번호가 없다 — 접수됐을 수 있다. 번호 없는 링크로 두고 당일 목록으로 확인한다(다시 보내지 않는다).
        entry["why"] = f"취소 확정 뒤 재발주 {len(reorders)}/{max_n} — 주문번호 없는 성공 응답(접수 확인 중)"
        f["last_error"] = f"재발주 응답에 주문번호 없음: {str(res.get('raw') or res)[:120]}"
        persist()
        log(f"  ? 재발주 {t['ticker']} — 응답에 주문번호가 없다 · 당일 목록으로 확인한다")
        return "sent"
    entry.update({"order_no": no, "orgno": res.get("orgno", "")})
    entry.pop("pending", None)
    link.update({"order_no": no, "orgno": res.get("orgno", "")})
    link.pop("pending", None)
    persist()
    log(f"  ↺ 재발주 {t['ticker']} x{qty} @ {px:,.2f} (시세 {live:,.2f}) → 주문번호 {no}")
    return "sent"


def _finish(t: dict, verdict: str, filled: int, avg: float, now: datetime, source: str, note: str = ""):
    f = t.setdefault("fill", {})
    f.update({"verdict": verdict, "filled_qty": filled, "avg_price": avg,
              "confirmed_at": now.isoformat(), "source": source})
    # ★ 결제일·결제일 환율(2026-10-12 실계좌 전환 계획) — 해외주식 양도소득세는 결제일 기준환율로 원화 환산한다.
    #   환율은 결제일이 지나야 정해지므로 여기서는 날짜만 적고, `tax_ledger.py --fill-fx`가 결제일 뒤 채운다.
    if verdict in ("FILLED", "PARTIAL") and filled:
        try:
            mk = t.get("market", "KR")
            f.setdefault("settle_date", settle_date(mk, _session(t, now)["session_date"]))   # 주간거래 행은 KST 날짜 기준
            if mk == "US":
                f.setdefault("settle_fx", None)
        except Exception:                              # noqa: BLE001 — 결제일 계산 실패가 확정을 막지 않는다
            pass
    if note:
        f["note"] = note
    f.pop("last_error", None)


def summarize(record: dict, now: datetime) -> dict:
    trades = record.get("trades") or []
    sent = [t for t in trades if was_sent(t)]
    verdicts = [(t.get("fill") or {}).get("verdict") for t in sent]
    summ = {
        "at": now.isoformat(),
        "sent": len(sent),
        "filled": verdicts.count("FILLED"),
        "partial": verdicts.count("PARTIAL"),
        "cancelled": verdicts.count("CANCELLED"),
        "expired": verdicts.count("EXPIRED"),
        # 접수 0(status REJECTED·UNSENT·옛 FAILED)은 애초에 걸린 주문이 없어 여기서 세지 않고 fill_summary가 센다.
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
    sid = _stage_start_try(Path(path))
    rc = None
    try:
        with day_lock(Path(path), "fill.confirm"):
            rc = _confirm_locked(Path(path), client, cfg, now_fn, sleep_fn, max_sec, chase, note, quiet)
            return rc
    finally:
        if sid:      # 단계 결과(v4 · U02) — 전건 종결이면 ok(ex_run.filled_at) · 미확정이 남으면 unknown · 예외면 failed
            try:
                db.stage_finish(sid, "ok" if rc == 0 else ("unknown" if rc == 3 else "failed"), rc, (), None,
                                "" if rc == 0 else ("미확정 잔존" if rc == 3 else "확정 중 중단"))
            except Exception as e:                   # noqa: BLE001 — 기록 실패가 체결 확정을 막지 않는다
                print(f"  (단계 기록 실패: {type(e).__name__}: {str(e)[:80]})", file=sys.stderr)


def _stage_start_try(path: Path):
    m = re.match(r"trades_(\d{6})_(kr|us)\.json$", path.name)
    if not m:
        return None
    try:
        return db.stage_start(m.group(1), m.group(2), "fill")
    except Exception as e:                           # noqa: BLE001
        print(f"  (단계 기록 실패: {type(e).__name__}: {str(e)[:80]})", file=sys.stderr)
        return None


def _confirm_locked(path: Path, client, cfg: dict, now_fn, sleep_fn, max_sec: int, chase: bool, note, quiet: bool) -> int:
    record = read_day(path, strict=True)
    trades = record.get("trades") or []
    todo = [t for t in trades if is_open_trade(t)]
    log = (lambda *a, **k: None) if quiet else (lambda *a, **k: print(*a, **k))

    if not todo:
        log("확정할 주문 없음 — 전건 종결 상태.")
    else:
        if client is None:
            # 실계좌 기록이면 지금 계좌 모드로 연다(real → 주문 가능 · real_readonly → 조회만 — 정정·취소는 거부된다).
            client = (KisClient.for_mode(orders=True) if record.get("svr") == "real"
                      else KisClient(svr="paper"))
        log(f"체결 확정 시작 — 미확정 {len(todo)}건 · 대기 {cfg['fill_wait_minutes']}분 · "
            f"정정 {cfg['fill_chase_max']}회/±{cfg['fill_chase_max_pct']}% · "
            f"취소 확정 뒤 재발주 {cfg.get('fill_reorder_max', 0)}회/±{cfg.get('fill_reorder_max_pct', 1.0)}%")
        t0 = now_fn()

        def persist():
            # ★ 외부 요청(정정·재발주) 전에 의도를, 취소 뒤에 결과를 디스크에 쓴다(W01) — 폴링 끝까지 기다리면 그사이 죽을 때 잃는다
            db.write_json_atomic(path, record)
        while True:
            now = now_fn()
            pending = 0
            # ★ 접수 불명(UNKNOWN·PENDING)을 먼저 푼다 — 다시 보내지 않고 당일 주문 목록·잔고로(2026-10-19 · R01·R02).
            try:
                resolve_pending(client, record, now, log)
            except Exception as e:                       # noqa: BLE001 — 해소 실패는 다음 폴링에 다시
                log(f"  ★ 접수 불명 해소 예외 — {type(e).__name__}: {str(e)[:100]} (다음 폴링에 계속)", file=sys.stderr)
            claimed = claimed_order_nos(record)
            for t in todo:
                if not is_open_trade(t):
                    continue
                try:
                    _step(client, t, cfg, now, chase, log, claimed, persist)
                except Exception as e:                   # noqa: BLE001 — 한 건의 예외가 다른 건의 확정을 막지 않는다
                    t.setdefault("fill", {})["last_error"] = f"{type(e).__name__}: {str(e)[:160]}"
                    log(f"  ★ {t.get('ticker')} 폴링 예외 — {type(e).__name__}: {str(e)[:100]} (다음 폴링에 계속)", file=sys.stderr)
                if is_open_trade(t):
                    pending += 1
                else:
                    _close_theses_after_exit(t, now, log)
            db.write_json_atomic(path, _with_summary(record, now_fn(), path))
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
    db.write_json_atomic(path, record)
    summ = record["fill_confirmed"]
    if note:
        write_note_line(Path(note), summ)
    log(_line(summ))
    if summ.get("overfill"):
        log(f"★★ 승인 초과 {summ['overfill']}건 — 사고 기록 {', '.join(summ.get('incidents') or []) or '(기록 실패)'} "
            f"(저장소 op_incident). 복구 주문을 내지 말 것.", file=sys.stderr)
    for t in trades:
        f = t.get("fill") or {}
        if was_sent(t):
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

    ★ 저장소(2026-10-17): 논지는 `db.thesis_update`로 **한 논지씩 한 트랜잭션 안에서** 읽고-고치고-쓴다. 예전에는
    theses.json 통째를 읽고 다시 써서, 같은 run의 `theses.py set-status`·`execute.refresh_thesis_unsynced`가 그 사이에
    고친 판을 덮어쓸 수 있었다. 고칠 것이 있는 논지만 연다(읽기 전용 훑기 → 그 논지만 갱신 · 갱신 안에서 같은 판정을 다시 한다).
    """
    f = t.get("fill") or {}
    if t.get("action") != "SELL" or f.get("verdict") not in ("FILLED", "PARTIAL"):
        return
    if t.get("source") == "audit_reversal":
        # 감사 소급 매도(2026-10-13) — 체결 확정을 저장소에 적는다(전량 filled · 부분이면 잔량 pending). 멱등(주문번호로 한 번만).
        try:
            _settle_reversal(t, log)
        except db.TestGuard:                     # 테스트가 임시 저장소 없이 왔다 — 삼키지 않는다
            raise
        except Exception as e:                   # noqa: BLE001 — 기록 갱신 실패가 체결 확정을 막지 않게
            log(f"  ★ 감사 소급 기록 갱신 실패 — {type(e).__name__}: {e}", file=sys.stderr)
    tid, gid = _leg_of(t)
    qty = f.get("filled_qty") or abs(int(f.get("delta") or 0)) or t.get("qty")
    px = f.get("avg_price") or t.get("price")
    order_no = (t.get("result") or {}).get("order_no") or ""
    full = bool(t.get("full_exit")) and f.get("verdict") == "FILLED"

    def _apply(th: dict, marked: list) -> int:
        """논지 하나에 이 체결을 반영한다(제자리 수정) — 바꾼 칸 수."""
        changed = 0
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
        if same and th.get("status") == "held" and full:
            th["status"] = "closed"
            th.setdefault("history", []).append(
                {"at": now.isoformat(), "status": "closed",
                 "note": f"{t.get('source')} 매도 전량 체결 {f.get('filled_qty')}주 @{f.get('avg_price')}"
                         + (f" — {t.get('discipline_reasons')[0]}" if t.get("discipline_reasons") else "")})
            changed += 1
        return changed

    changed, marked = 0, []
    try:
        for th in db.theses_all():
            if not th.get("id") or not _apply(json.loads(json.dumps(th)), []):
                continue                             # 고칠 것이 없는 논지는 열지 않는다
            got = {"n": 0, "marked": []}

            def fn(cur, got=got):
                got["marked"] = []                   # 갱신 안에서 다시 판정한다(그 사이 다른 쓰기가 찍었으면 0)
                got["n"] = _apply(cur, got["marked"])
                return cur
            db.thesis_update(th["id"], fn)
            changed += got["n"]
            marked += got["marked"]
    except db.TestGuard:                             # 테스트가 임시 저장소 없이 왔다 — 삼키지 않는다
        raise
    except Exception as e:                           # noqa: BLE001 — 논지 갱신 실패가 체결 기록 쓰기를 막지 않게(다음 확정 호출이 다시 한다 · 멱등)
        log(f"  ★ 논지 원장 갱신 실패 — {type(e).__name__}: {str(e)[:160]} · 체결 기록은 그대로 쓴다. "
            f"`python3 fill.py <거래 기록>`을 다시 돌리면 다리 소진·논지 종료를 다시 시도한다", file=sys.stderr)
    if changed:
        if marked:
            log(f"  다리 소진 표시 {', '.join(marked)} — 같은 다리는 다시 발화하지 않는다")
        if full:
            log(f"  논지 closed — {t.get('ticker')} 전량 매도 체결")


def _settle_reversal(t: dict, log=print) -> None:
    """체결이 확정된 감사 소급 되돌림 매도로 소급 기록을 닫는다(저장소 `ex_reversal_event`에 한 행 덧붙임 · 멱등).
    전량 → filled · 부분 → 남은 수량으로 pending · 취소·만료·거부 → 그대로 pending(다음 run이 다시 보낸다).
    옛 `reversals.settle_trade`와 같은 규칙이고, 읽기와 덧붙이기를 한 트랜잭션으로 묶는다(같은 주문을 두 번 세지 않게)."""
    if t.get("source") != "audit_reversal" or t.get("action") != "SELL":
        return
    f = t.get("fill") or {}
    v = f.get("verdict")
    if v not in ("FILLED", "PARTIAL"):
        return
    import risk_guard as rg                      # 대기 행 규칙(id 있는 pending만)을 risk_guard와 같이 쓴다
    sold = int(f.get("filled_qty") or abs(int(f.get("delta") or 0)) or (t.get("qty") if v == "FILLED" else 0) or 0)
    mk = str(t.get("market", "")).upper()
    order_no = (t.get("result") or {}).get("order_no") or ""
    with db.tx():
        for r in rg.reversals_pending(mk):
            if r.get("ticker") != t.get("ticker"):
                continue
            if order_no and order_no in (r.get("settled_orders") or []):
                return
            left = max(0, int(r.get("qty") or 0) - sold)
            done = (r.get("settled_orders") or []) + ([order_no] if order_no else [])
            if left == 0 or v == "FILLED":
                db.reversal_mark(r["id"], "filled", sold_qty=int(r.get("sold_qty") or 0) + sold, qty=0, settled_orders=done)
                log(f"  · 감사 소급 {r['id']} {t.get('ticker')} {sold}주 매도 체결 — 닫음")
            else:
                db.reversal_mark(r["id"], "pending", sold_qty=int(r.get("sold_qty") or 0) + sold, qty=left, settled_orders=done)
                log(f"  · 감사 소급 {r['id']} {t.get('ticker')} {sold}주 부분 체결 — 잔량 {left}주는 다음 run이 보낸다")
            return


def _with_summary(record: dict, now: datetime, path: Path = None) -> dict:
    summ = summarize(record, now)
    # 논지 동기화 여부도 **지금** 다시 센다 — 파일에 남은 옛 값이 게이트를 막았다(2026-09-16).
    try:
        import execute as ex
        fills = {t["ticker"]: (t.get("fill") or {}) for t in (record.get("trades") or [])
                 if t.get("action") == "BUY" and was_sent(t)}
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
#   여기서 종목·방향별 집행 합을 승인 합과 대조하고, 넘으면 사고(저장소 `op_incident` · 옛 `journal/incidents.jsonl`)로
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
        if not was_sent(t):
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
    """사고 기록 전체(저장소 `op_incident` — 추가만 하는 표)."""
    return db.log_rows("incidents")


def record_incidents(record: dict, path: Path, now: datetime) -> list:
    """초과가 있으면 사고 기록(저장소 `op_incident`)에 `INC-<stamp>-<n>`으로 남긴다 — (stamp, market, 종목, 방향)당 한 번.
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
    ids = []
    for ov in rec["overfill_list"]:
        key = (stamp, market, ov["ticker"], ov["action"])
        hit = next((e for e in existing
                    if (e.get("stamp"), e.get("market"), e.get("ticker"), e.get("action")) == key), None)
        rows = [t for t in record.get("trades") or []
                if was_sent(t)
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
            db.log_append("incidents", row)
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


def backfill_settle(record: dict) -> int:
    """체결된 행에 빠진 결제일을 채운다 — 거래 기록을 쓸 때마다(`finalize`). 반환 채운 건수.

    ★ 왜(2026-10-13): 결제일은 `_finish`(체결 TR 폴링으로 닫힌 행)에서만 찍혔다. 전송 직후 잔고 대조로 바로 FILLED가 된 행은
    `confirm`이 다시 보지 않아 결제일이 영영 비었고(10-13 US 7건 중 6건), 세금 원장이 체결일·최신 환율로 대신 계산했다.
    날짜는 그 행의 전송 시각(`ts`) 기준 세션일 — 기록을 나중에 다시 써도 바뀌지 않는다. 이미 있으면 손대지 않는다.
    """
    n = 0
    for t in record.get("trades") or []:
        f = t.get("fill") or {}
        if not was_sent(t) or f.get("verdict") not in ("FILLED", "PARTIAL") or "settle_date" in f:
            continue
        try:
            qty = int(f.get("filled_qty") or abs(int(f.get("delta") or 0)))
            if qty <= 0:
                continue
            mk = t.get("market", "KR")
            f["settle_date"] = settle_date(mk, _session(t, _parse_ts(t["ts"]))["session_date"])
            if mk == "US":
                f.setdefault("settle_fx", None)
            n += 1
        except Exception:                              # noqa: BLE001 — 결제일 계산 실패가 기록을 막지 않는다
            continue
    return n


def finalize(record: dict, path: Path, now: datetime) -> dict:
    """대조 + 사고 기록 + `fill_confirmed`에 초과 건수 복사. `execute.merge_day_record`와 `_with_summary`가 부른다."""
    backfill_settle(record)
    record["reconcile"] = reconcile(record)
    record_incidents(record, path, now)
    fc = record.get("fill_confirmed")
    if isinstance(fc, dict):
        fc["overfill"] = int(record["reconcile"].get("overfill") or 0)
        fc["incidents"] = list(record["reconcile"].get("incidents") or [])
    return record


def _step(client, t: dict, cfg: dict, now: datetime, chase: bool, log, claimed: set = None, persist=None) -> None:
    """주문 1건을 한 번 살핀다 — 종결이면 판정을 쓰고, 아니면 정정·취소 여부를 정한다.
    `claimed` = 기록 전체가 차지한 주문번호(`claimed_order_nos`) — 응답을 잃은 링크가 남의 주문을 채택하지 않게.
    `persist` = 그날 기록 전체를 디스크에 원자적으로 쓰는 함수(W01 — 정정·재발주는 보내기 전에, 취소는 응답 직후에 부른다)."""
    persist = persist or (lambda: None)
    f = t.setdefault("fill", {})
    market = t.get("market", "KR")
    side = t.get("action", "BUY")
    excd = order_excd((t.get("proposal") or {}).get("excd") or t.get("excd") or "NASD") \
        if market != "KR" else ""
    chain = _chain(t)
    if not chain:
        # 주문번호 없는 행(UNKNOWN·PENDING)은 confirm이 폴링마다 기록 전체로 `resolve_pending`을 먼저 돌려 푼다 —
        # 행 하나만 보고 채택하면 같은 종목의 다른 행 주문을 가져올 수 있다.
        return
    if _resolve_links(client, t, now, log, claimed if claimed is not None else claimed_order_nos({"trades": [t]})):
        return                                      # 응답을 잃은 재발주·정정 링크 — 확인될 때까지 판정하지 않는다
    chain = _chain(t)
    approved_qty = int(t.get("qty") or 0)
    approved_px = float(t.get("price") or 0)
    approved_amt = approved_qty * approved_px

    # 체인 전체의 체결을 합산한다 — 정정하면 잔량이 새 번호로 옮겨가고 옛 번호엔 체결분만 남는다.
    filled, filled_amt, latest = 0, 0.0, None
    for link in chain:
        if not _norm_no(link.get("order_no")):
            continue                                # 번호 없는 링크는 상태 조회에 넣지 않는다("0"과 맞는 사고)
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
                               filled, filled_amt, log, persist)
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
        sess = _session(t, now)
        if now > sess["close"]:
            return _finish(t, "EXPIRED", 0, 0.0, now, src, "장 마감으로 소멸 — 체결 0")
        return

    # 여기부터 잔량이 있다.
    sess = _session(t, now)
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
                persist()                            # 취소는 응답 직후 바로 쓴다(되풀이 취소는 증권사가 거절할 뿐 노출을 늘리지 않는다)
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
    new_px = chase_price(side, market, live, cur_limit, approved_px, float(cfg["fill_chase_max_pct"]),
                         floor=not t.get("risk_reducing"))
    if new_px is None:
        return
    new_qty = chase_qty(side, remain, filled_amt, approved_amt, new_px)
    if new_qty <= 0:
        chases.append({"ts": now.isoformat(), "skipped": True, "live": live, "price": new_px,
                       "why": "승인 금액 안에서 0주 — 정정 없이 대기"})
        return
    # ★ 보내기 전 기록(W01) — 정정 의도를 번호 없는 '정정' 링크(원주문 = 지금 꼬리 번호)로 넣고 디스크에 쓴 뒤 보낸다.
    #   접수 직후 죽으면 다음 실행이 당일 목록의 원주문 번호(orgn_odno) 행으로 붙이거나 두 번 없음을 확인한 뒤 뺀다.
    ts = now.isoformat()
    plink = {"order_no": "", "orgno": tail.get("orgno", ""), "pending": True, "qty": new_qty, "price": new_px,
             "ts": ts, "why": "정정(보내기 전 기록)", "orgn_order_no": tail["order_no"]}
    chain.append(plink)
    persist()
    try:
        res = _modify(client, t, tail["order_no"], tail.get("orgno", ""), excd, new_qty, new_px, cancel=False)
    except KisError as e:
        if getattr(e, "kind", "unknown") != "unknown":
            chain.remove(plink)                      # 분명한 거절 — 의도를 거둔다
            persist()
            return _modify_rejected(client, t, f, cfg, now, tail, excd, remain, new_px, new_qty, live, chases, e, log, persist)
        # ★ 응답을 잃은 정정 — 정정이 반영됐을 수 있다(잔량이 새 번호로 옮겨 갔을 수 있다). 옛 번호를 바로 취소하고
        #   재발주로 가면 살아 있는 새 주문 위에 또 얹는다(2026-10-19 · R01). 번호 없는 '정정' 링크를 그대로 두고 다음 폴링이
        #   당일 목록의 원주문 번호(orgn_odno = 지금 꼬리 번호) 행으로 붙이거나, 두 번 없음을 확인하면 뺀다.
        chases.append({"ts": ts, "price": new_px, "qty": new_qty, "live": live, "order_no": "",
                       "pending": True, "error": str(e)[:160], "why": "정정 응답 유실 — 접수 확인 중"})
        plink["why"] = "정정(응답 유실)"
        f["last_error"] = f"정정 응답 유실: {e}"[:200]
        persist()
        log(f"  ? 정정 응답 유실 {t['ticker']} — 옛 번호를 취소하지 않고 당일 목록으로 확인한다", file=sys.stderr)
        return
    except ValueError as e:
        chain.remove(plink)
        persist()
        return _modify_rejected(client, t, f, cfg, now, tail, excd, remain, new_px, new_qty, live, chases, e, log, persist)
    entry = {"ts": ts, "from_price": cur_limit, "price": new_px, "qty": new_qty,
             "live": live, "order_no": res.get("order_no", ""),
             "why": f"미체결 {int((now - last_change).total_seconds())}s — 현재가 쪽으로 정정"
                    + (f" · 수량 {remain}→{new_qty}(승인 금액 안)" if new_qty < remain else "")}
    chases.append(entry)
    plink.update({"order_no": res.get("order_no") or tail["order_no"], "orgno": res.get("orgno", tail.get("orgno", "")),
                  "why": "정정"})
    plink.pop("pending", None)
    plink.pop("orgn_order_no", None)
    persist()
    log(f"  ↻ 정정 {t['ticker']} {cur_limit:,.2f}→{new_px:,.2f} x{new_qty} → 주문번호 {res.get('order_no')}")


def _modify_rejected(client, t, f, cfg, now, tail, excd, remain, new_px, new_qty, live, chases, e, log, persist=None) -> None:
    """정정이 **분명히 거절**됐다 — 한 번만 기록하고 취소→재발주로 간다(모의 `[40660000] 잔량정정취소만 가능`)."""
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
            (persist or (lambda: None))()            # 취소는 응답 직후 바로 쓴다(W01)
            log(f"  ✂ 취소 전송 {t['ticker']} 잔량 {remain} — {f['cancel_why']}")
        except (KisError, ValueError) as ce:
            f["last_error"] = f"취소 실패: {ce}"[:200]
            log(f"  ★ 취소 실패 {t['ticker']}: {ce}", file=sys.stderr)


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


# ────────────────────────────────────────────────────────── 접수 불명 해소(2026-10-19 · Codex R01·R02)
# 응답을 잃은 주문(UNKNOWN)·결과를 못 적은 의도(PENDING)·응답을 잃은 재발주/정정 링크(pending)는 **다시 보내지 않고**
# 증권사의 당일 주문 목록으로 푼다. 사람에게 묻지 않는다 — 목록이 답하면 그 주문을 행에 붙이고, 목록이 '없다'고
# 확정하고(실계좌 KIOK0560 · 다음 쪽 없음) 잔고도 그대로면 미접수(UNSENT)로 내려 다시 보낼 수 있게 한다.
# 둘 다 아니면 열린 채로 두고 마감 뒤 잔고가 그대로면 만료(EXPIRED)로 닫는다 — 어느 쪽도 이중 주문을 만들지 않는다.

# 증권사 접수 시각과 이 맥의 시계 차 — 후보를 '의도 시각 이후'로 거를 때의 여유. 주 필터는 종목·방향·수량·미배정이고,
# 시각은 어제 남은 주문을 빼는 데만 쓴다(목록이 이미 당일·세션일로 묶여 있다).
RESOLVE_CLOCK_SLACK_SEC = 120
# '없다'를 확정으로 쓰는 관측 수 — 접수 직후 목록 반영이 늦을 수 있어 서로 다른 두 폴링에서 같은 답을 받아야 내린다.
RESOLVE_EMPTY_OBS = 2


def _norm_no(no) -> str:
    return str(no or "").strip().lstrip("0") or ("0" if str(no or "").strip() else "")


def claimed_order_nos(record: dict) -> set:
    """기록이 이미 차지한 주문번호 전부 — 접수 불명 행이 남의 주문을 채택하지 않게."""
    out = set()
    for t in record.get("trades") or []:
        res = t.get("result") or {}
        f = t.get("fill") or {}
        nos = [res.get("order_no"), f.get("cancel_order_no")]
        for k in ("order_chain", "reorders", "chase"):
            nos += [x.get("order_no") for x in (f.get(k) or []) if isinstance(x, dict)]
        out |= {_norm_no(n) for n in nos if _norm_no(n)}
    return out


def _day_orders(client, t: dict):
    """이 행 종목의 당일 주문 목록. 조회 실패면 None(행은 열린 채로 다음 폴링에 다시)."""
    try:
        if t.get("market", "KR") == "KR":
            return client.domestic_orders(ticker=t["ticker"])
        excd = order_excd((t.get("proposal") or {}).get("excd") or t.get("excd") or "NASD")
        return client.overseas_orders(ticker=t["ticker"], excd=excd)
    except (KisError, AttributeError, TypeError, ValueError) as e:
        t.setdefault("fill", {})["last_error"] = f"당일 주문 목록 조회 실패: {e}"[:200]
        return None


def _since(ts: str):
    try:
        return _parse_ts(ts) - timedelta(seconds=RESOLVE_CLOCK_SLACK_SEC) if ts else None
    except (TypeError, ValueError):
        return None


def _after(row: dict, since) -> bool:
    if since is None or not row.get("ord_at"):
        return True
    try:
        return _parse_ts(row["ord_at"]) >= since
    except (TypeError, ValueError):
        return True


def _empty_twice(f: dict, key: str, now: datetime) -> bool:
    """'없다'는 관측을 이번 폴링 시각으로 쌓고, 서로 다른 폴링에서 두 번 이상이면 True."""
    seen = list(f.get(key) or [])
    stamp = now.isoformat()
    if stamp not in seen:
        seen.append(stamp)
    f[key] = seen[-4:]
    return len(set(seen)) >= RESOLVE_EMPTY_OBS


def resolve_pending(client, record: dict, now: datetime, log) -> int:
    """접수 불명 행(UNKNOWN·PENDING)을 당일 주문 목록으로 푼다. 반환: 이번에 상태가 정해진 행 수."""
    rows = [t for t in (record.get("trades") or []) if t.get("status") in UNRESOLVED and is_open_trade(t)]
    if not rows:
        return 0
    claimed = claimed_order_nos(record)
    cache, n = {}, 0
    for t in sorted(rows, key=lambda x: str(x.get("ts") or "")):
        key = (t.get("market", "KR"), t.get("ticker"))
        if key not in cache:
            cache[key] = _day_orders(client, t)
        lst = cache[key]
        f = t.setdefault("fill", {})
        prev = t.get("status")
        if lst and lst.get("ok"):
            since = _since(t.get("ts"))
            # 대사는 **실제로 보낸 요청** 수량으로(W02 · execute가 보내기 전에 request를 쓴다 — 옛 행은 qty)
            want_qty = int((t.get("request") or {}).get("qty") or t.get("qty") or 0)
            cands = [r for r in lst.get("rows") or []
                     if r.get("is_original") and r.get("side") == t.get("action") and r.get("ticker") == t.get("ticker")
                     and int(r.get("ord_qty") or 0) == want_qty
                     and _norm_no(r.get("order_no")) and _norm_no(r.get("order_no")) not in claimed and _after(r, since)]
            if cands:
                c = min(cands, key=lambda r: r.get("ord_at") or "")
                t["status"] = "SENT"
                res = t.setdefault("result", {})
                res.update({"order_no": c["order_no"], "orgno": c.get("orgno", ""),
                            "resolved": {"how": "day-list", "at": now.isoformat(), "from": prev,
                                         "candidate": {k: c.get(k) for k in ("order_no", "ord_at", "ord_qty", "price",
                                                                            "filled_qty", "excg")}}})
                if c.get("excg") and t.get("market", "KR") == "KR":
                    res.setdefault("excg", c["excg"])
                f.pop("order_chain", None)            # 채택한 번호로 체인을 새로 짓는다(_chain)
                claimed.add(_norm_no(c["order_no"]))
                log(f"  ✓ 접수 확인 {t.get('action')} {t.get('ticker')} — 당일 목록의 주문 {c['order_no']}"
                    f"({c.get('ord_at') or '시각 미상'})를 이 행에 붙였다({prev} → SENT · 재전송 없음)")
                n += 1
                continue
            if lst.get("authoritative_empty") and lst.get("complete"):
                _fallback_balance(client, t, int(t.get("qty") or 0), t.get("action", "BUY"), now, log)
                if not is_open_trade(t):              # 잔고가 요청만큼 움직였다 — 체결로 닫혔다
                    n += 1
                    continue
                if f.get("qty_after") is not None and int(f.get("delta") or 0) == 0 and _empty_twice(f, "empty_seen", now):
                    t["status"] = "UNSENT"
                    t.setdefault("result", {})["resolved"] = {"how": "day-list-empty", "at": now.isoformat(), "from": prev}
                    f.update({"verdict": "REJECTED", "confirmed_at": now.isoformat(), "source": "resolve_pending",
                              "note": f"접수 불명 → 미접수 확인(당일 목록에 이 종목 주문 없음 {RESOLVE_EMPTY_OBS}회 · 잔고 불변) "
                                      f"— 걸린 주문이 없다 · 같은 승인으로 다시 보낼 수 있다"})
                    log(f"  ⊘ 미접수 확인 {t.get('action')} {t.get('ticker')} — 당일 목록에 없고 잔고 불변({prev} → UNSENT)")
                    n += 1
                continue
        # 목록을 못 읽었거나 결론을 못 냈다 — 잔고로만 체결을 확인하고, 마감 뒤 잔고 불변이면 만료로 닫는다.
        _fallback_balance(client, t, int(t.get("qty") or 0), t.get("action", "BUY"), now, log)
        if not is_open_trade(t):
            n += 1
            continue
        if now > _session(t, now)["close"] and f.get("qty_after") is not None and int(f.get("delta") or 0) == 0:
            _finish(t, "EXPIRED", 0, 0.0, now, "resolve_pending",
                    "접수 불명 — 마감 뒤 잔고 불변 · 당일 목록으로 확인하지 못했다(체결 0)")
            n += 1
    return n


def _resolve_links(client, t: dict, now: datetime, log, claimed: set) -> bool:
    """응답을 잃은 재발주·정정 링크(order_no 빈 pending)를 푼다. 반환 True = 아직 미해소(이번 폴링은 판정하지 않는다)."""
    f = t.setdefault("fill", {})
    chain = f.get("order_chain") or []
    pend = [lk for lk in chain if lk.get("pending") and not _norm_no(lk.get("order_no"))]
    if not pend:
        return False
    lst = _day_orders(client, t)
    if not lst or not lst.get("ok"):
        return True
    for lk in pend:
        if lk.get("why", "").startswith("정정"):
            want = _norm_no(lk.get("orgn_order_no"))
            cands = [r for r in lst.get("rows") or [] if want and _norm_no(r.get("orgn_order_no")) == want
                     and _norm_no(r.get("order_no")) not in claimed]
        else:
            since = _since(lk.get("ts"))
            cands = [r for r in lst.get("rows") or []
                     if r.get("is_original") and r.get("side") == t.get("action") and r.get("ticker") == t.get("ticker")
                     and int(r.get("ord_qty") or 0) == int(lk.get("qty") or 0)
                     and _norm_no(r.get("order_no")) and _norm_no(r.get("order_no")) not in claimed and _after(r, since)]
        if cands:
            c = min(cands, key=lambda r: r.get("ord_at") or "")
            lk.update({"order_no": c["order_no"], "orgno": c.get("orgno", "") or lk.get("orgno", ""),
                       "pending": False, "resolved": {"how": "day-list", "at": now.isoformat()}})
            for rr in f.get("reorders") or []:
                if rr.get("pending") and rr.get("ts") == lk.get("ts"):
                    rr.update({"order_no": c["order_no"], "pending": False})
            claimed.add(_norm_no(c["order_no"]))
            log(f"  ✓ {lk.get('why')} 접수 확인 {t.get('ticker')} — 당일 목록 주문 {c['order_no']}")
        elif lst.get("complete") and _empty_twice(lk, "absent_seen", now):
            # 목록이 끝까지 왔는데 두 번 다 없다 — 그 재발주·정정은 접수되지 않았다. 링크를 빼고 원래 흐름으로 돌아간다.
            chain.remove(lk)
            f["reorders"] = [rr for rr in (f.get("reorders") or []) if not (rr.get("pending") and rr.get("ts") == lk.get("ts"))]
            log(f"  ⊘ {lk.get('why')} 미접수 확인 {t.get('ticker')} — 당일 목록에 없다(링크 제거)")
    return any(lk.get("pending") and not _norm_no(lk.get("order_no")) for lk in chain)



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
    try:
        return confirm(path, max_sec=args.max_sec, chase=not args.no_chase,
                       note=Path(args.note) if args.note else None)
    except DayLocked as e:
        print(f"확정 보류 — {e}", file=sys.stderr)
        return 5
    except DayRecordCorrupt as e:
        print(f"확정 중단 — {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
