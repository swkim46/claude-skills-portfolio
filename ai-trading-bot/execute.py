#!/usr/bin/env python3
"""
주문 집행 — dry-run이 기본이고, 실제 전송은 --send를 명시해야 한다.

집 규칙(daily-routine/apple_동기화.py)과 같다: 읽기가 기본, 파괴적 행위는 명시 플래그,
그리고 쓰기 뒤에는 반드시 재조회로 검증한다.

approved 파일을 그냥 믿지 않는다. risk_guard가 찍어둔 limits.json 해시가 지금 파일과
같은지, 만들어진 지 얼마나 됐는지를 다시 본다. 사람이 approved를 손으로 고치거나
오래된 파일을 다시 돌리는 경로를 막기 위해서다.

사용:
    python3 execute.py signals/approved_260903_kr.json              # dry-run
    python3 execute.py signals/approved_260903_kr.json --send       # 실제 모의주문
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import (KisClient, KisError, snap_kr_price, kr_tick_size, market_session,
                        order_excd, quote_excd)
import risk_guard as rg

HERE = Path(__file__).parent
JOURNAL_DIR = HERE / "journal"
KST = timezone(timedelta(hours=9))


# 그날 기록 `runs[]`에 남기는 run 메타 — `approved_orders`(승인 주문 스냅샷)가 있어야
# 승인 대비 대조가 approved 파일을 다시 열지 않는다(risk_guard 재실행이 그 파일을 덮어쓴다).
RUN_KEYS = ("generated_at", "approved_ref", "approved_at", "approved_orders", "skipped",
            "signal_ref", "limits_sha256")


def approved_snapshot(approved: dict) -> list:
    """대조용 승인 주문 스냅샷 — 종목·방향·source·수량·가격만."""
    return [{"ticker": o.get("ticker"), "action": o.get("action"), "source": o.get("source"),
             "qty": int(o.get("qty") or 0), "price": o.get("price")}
            for o in (approved.get("orders") or [])]


def _stamp_from(path: Path) -> str:
    """approved 파일명 `approved_<YYMMDD>_<mkt>…json`의 세션일. 없으면 오늘."""
    m = re.search(r"_(\d{6})_(?:kr|us)", path.name)
    return m.group(1) if m else format(datetime.now(KST), "%y%m%d")


def refresh_thesis_unsynced(trades_path: Path) -> list:
    """그날 거래 기록의 `thesis_unsynced`를 **지금 원장 상태로** 다시 센다(theses.py set-status 뒤).

    2026-09-16 국내 run: 전송 시점엔 논지가 armed였고, held로 옮긴 뒤에도 파일의 `thesis_unsynced`가
    옛 값이라 `execute` 게이트가 막혔다. 원장을 옮기는 쪽이 이 값을 같이 갱신한다.
    """
    if not trades_path.exists():
        return []
    try:
        rec = json.loads(trades_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    fills = {}
    for t in rec.get("trades") or []:
        if t.get("action") == "BUY" and t.get("status") != "FAILED":
            fills[t["ticker"]] = t.get("fill") or {}
    before = rec.get("thesis_unsynced")
    rec["thesis_unsynced"] = _thesis_unsynced(fills)
    if before != rec["thesis_unsynced"]:
        rec.setdefault("thesis_resync", []).append(
            {"at": datetime.now(KST).isoformat(), "before": before, "after": rec["thesis_unsynced"]})
        trades_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return rec["thesis_unsynced"]


def preflight(approved: dict, path: Path, limits: dict) -> None:
    """집행 직전 재검증. 하나라도 어긋나면 아무것도 보내지 않는다."""
    if rg.KILL_PATH.exists():
        raise rg.Rejection(f"KILL 파일 존재 ({rg.KILL_PATH})")
    if limits.get("halt_all"):
        raise rg.Rejection("limits.halt_all=true")
    bad_latch = rg.real_latch_needs_cap(limits)
    if bad_latch:
        raise rg.Rejection(bad_latch)

    stamped = approved.get("limits_sha256")
    current = rg.sha256_of(rg.LIMITS_PATH)
    if stamped != current:
        raise rg.Rejection(
            "limits.json이 승인 이후 바뀌었다 — approved 파일 무효. risk_guard를 다시 돌려라.\n"
            f"  승인 당시: {stamped}\n  현재     : {current}"
        )

    age_min = (datetime.now(KST) - rg._parse_dt(
        approved["generated_at"], "approved.generated_at")).total_seconds() / 60
    max_age = limits.get("approved_max_age_minutes", 30)
    if age_min > max_age:
        raise rg.Rejection(f"approved 파일이 낡았다: {age_min:.0f}분 전 (허용 {max_age}분)")

    if approved.get("schema_version") != rg.SCHEMA_VERSION:
        raise rg.Rejection(f"approved schema_version 불일치: {approved.get('schema_version')!r}")


def _send_cfg() -> dict:
    """전송 직전 갱신의 한도 — `config/limits.json`의 `send_refresh_max_pct`(기본 2.0%)."""
    try:
        return json.loads((HERE / "config" / "limits.json").read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}


def refresh_limit(client: KisClient, order: dict, limits: dict = None) -> tuple:
    """전송 **직전**에 현재가를 다시 읽어 지정가·수량을 갱신한다. (가격, 수량, 사유, 미전송사유)

    왜: 승인 가격은 **스냅샷 시점** 값이다. 스냅샷 → 노트 → 검증 → 게이트 → 집행 사이에
    수십 분이 흐르고, 그 사이 가격이 움직이면 지정가가 빗나가 **조용히 미체결**로 남는다.
    로그에는 '주문번호 발급'으로 찍혀 집행된 것처럼 보인다(`snap_kr_price` 주석의 그 실패다).
    *실사례(2026-09-10): 승인가 84,500 / 40분 뒤 시장가 84,700 → 미체결. 그날 보고는
    "매수했다"였고 실제 보유는 늘지 않았다.* *실사례(2026-09-16): 이 함수가 해외를 건너뛰어
    AVGO 지정가가 09:39 ET 캡처값(341.35)으로 나갔고, 10:34 ET 시세는 그 위라 20분 뒤 취소됐다 —
    그래서 **해외도 갱신한다**(2026-09-17 사용자 지시 "캡처 시점이 아니라 최신 시세로").*

    갱신은 **불리한 쪽으로만** 한다 — 매수는 올리고 매도는 내린다. 유리한 쪽으로 옮기면
    체결을 포기하는 것이고, 그게 애초의 문제다. 승인가보다 유리해졌으면 승인가를 그대로 쓴다
    (지정가가 시세 위에 있으면 시세에 체결된다 — 더 싸게 사는 것을 막을 이유가 없다).
    **수량은 승인 금액을 넘지 않게 내린다**(가격이 올랐으면 주 수를 줄인다 — 한도는 금액이다).
    시세가 승인가에서 `send_refresh_max_pct`(기본 2%)보다 더 불리하게 달아났으면 **보내지 않는다** —
    그건 캡처 뒤에 오른 것을 쫓는 것이고, 다음 run이 새 가격으로 다시 판단한다.
    """
    approved_px = float(order["price"])
    qty = int(order["qty"])
    approved_amt = approved_px * qty
    market = order.get("market", "KR")
    try:
        if market == "KR":
            live = float(client.domestic_price(order["ticker"])["price"])
        else:
            excd_q = quote_excd(order.get("excd"))
            live = float(client.overseas_price(order["ticker"], excd=excd_q)["price"])
    except (KisError, KeyError, TypeError, ValueError) as e:
        return approved_px, qty, f"현재가 재조회 실패({type(e).__name__}) — 승인가 유지", None
    if live <= 0:
        return approved_px, qty, "현재가 0 — 승인가 유지", None
    side = order["action"]
    cfg = limits or _send_cfg()
    worse = live > approved_px if side == "BUY" else live < approved_px
    drift = (live - approved_px) / approved_px * 100
    max_pct = float(cfg.get("send_refresh_max_pct") or 2.0)
    if worse and abs(drift) > max_pct:
        return approved_px, qty, "", (f"가격 이탈 — 집행 시점 시세 {live:,.2f}가 승인가 {approved_px:,.2f} 대비 "
                                      f"{drift:+.2f}% (한도 ±{max_pct}%) — 전송 안 함, 다음 run이 재판단")
    # ★ 체결 속도 우선(2026-09-22): 지정가를 **시세와 승인가 중 불리한 쪽에서 `fill_aggressive_ticks`틱 더** 시장 쪽으로 둔다 —
    #   매수는 위로, 매도는 아래로. 지정가가 마지막 체결가와 같으면 호가 한 틱 차이로 20분을 기다리다 취소되는 일이 잦았다
    #   (execute 단계 9~24분의 대부분이 이 대기였다). 비용은 틱 × n, 상한은 승인 금액(수량을 줄여 맞춘다).
    ticks = int(cfg.get("fill_aggressive_ticks") if cfg.get("fill_aggressive_ticks") is not None else 1)
    base = max(live, approved_px) if side == "BUY" else min(live, approved_px)
    if market == "KR":
        px0 = float(snap_kr_price(base, side))
        step = kr_tick_size(px0)
        px = px0 + step * ticks if side == "BUY" else max(step, px0 - step * ticks)
        px = float(snap_kr_price(px, side))
    else:
        px0 = round(base, 2)
        bump = 0.001 * ticks                          # 해외: 0.1%/틱 상당(호가 단위 미조회 — 센트 반올림)
        px = round(px0 * (1 + bump), 2) if side == "BUY" else round(px0 * (1 - bump), 2)
    if px == approved_px and not worse:
        return approved_px, qty, "", None
    new_qty = qty
    if side == "BUY":
        # 틱 분(px − px0)만큼은 승인 금액 초과를 허용한다 — 한 틱 올린 대가로 한 주를 잃으면 채우기 목적이 깨진다.
        # 시세 드리프트(px0 − 승인가)는 그대로 수량으로 흡수한다(한도는 금액이다).
        allowed = approved_amt + max(0.0, px - px0) * qty
        new_qty = min(qty, int(allowed // px)) if px > 0 else 0
        if new_qty <= 0:
            return px, 0, "", f"승인 금액 {approved_amt:,.0f} 안에서 {px:,.2f}로는 0주 — 전송 안 함"
    why = (f"승인가 {approved_px:,.2f} → {px:,.2f} (집행 시점 시세 {live:,.2f}, 승인 대비 {drift:+.2f}% · "
           f"체결 우선 {ticks}틱)" + (f" · 수량 {qty}→{new_qty}(승인 금액 안)" if new_qty != qty else ""))
    return px, new_qty, why, None


def place(client: KisClient, order: dict) -> dict:
    """주문 1건 전송. 국내는 지정가, 미국은 지정가(모의는 지정가만 지원)."""
    market = order.get("market", "KR")
    if market == "KR":
        return client.domestic_order(
            ticker=order["ticker"], side=order["action"],
            qty=order["qty"], price=int(order["price"]),
        )
    # 시세용 코드(NYS)가 실려 오면 주문용(NYSE)으로 바꾼다 — 2026-09-15 1차 전송이 여기서 막혔다.
    excd = order_excd(order.get("excd") or "NASD")
    return client.overseas_order(
        ticker=order["ticker"], side=order["action"],
        qty=order["qty"], limit_price=float(order["price"]), excd=excd,
    )


def verify(client: KisClient, market: str) -> dict:
    """쓰기 후 검증 — 주문 뒤 잔고를 다시 읽어 상태를 확인한다."""
    try:
        return client.domestic_balance() if market == "KR" else client.overseas_balance()
    except KisError as e:
        return {"error": str(e)}


def _read_day(path: Path) -> dict:
    """그날 거래 기록(없거나 깨졌으면 {})."""
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def dedupe_against_day(orders: list, prior_day: dict, approved_name: str) -> tuple:
    """★ 같은 승인은 한 번만 나간다 — (보낼 것, 건너뛴 것[중복], 보류[열린 주문]).

    왜: 2026-09-10(042660 4주 두 번)·2026-09-21(두산 46주 두 번) 모두 **같은 approved로
    `execute.py --send`를 다시 돌려서** 났다. 승인은 파일이고 파일은 다시 돌릴 수 있으므로,
    전송이 멱등이어야 한다 — "다시 돌리지 마라"는 규칙은 두 번 깨졌다.

    - 중복: 그날 기록에 **같은 승인 파일명**(옛 행처럼 `approved_ref`가 없으면 같은 것으로 본다) +
      같은 (종목, 방향, source) + `status != FAILED`인 행이 있으면 **판정과 무관하게** 건너뛴다.
      열린 건은 `fill.py`의 몫이고 종결 건은 끝난 것이며 REJECTED도 재전송하지 않는다 —
      정당한 재발행은 파일명을 바꾼다(`_v2`·`_reissue`, 이미 그렇게 쓰고 있다).
    - 보류: 승인 파일과 무관하게 같은 (종목, 방향)의 **열린** 행이 있으면 보류한다 —
      `stops.py`가 20분마다 새 파일명(`_stops_HHMM`)으로 같은 매도를 다시 내는 구멍.
    """
    try:
        from fill import is_open_trade
    except ImportError:                                 # pragma: no cover — fill 없이도 중복만은 막는다
        def is_open_trade(t):
            return False
    rows = [t for t in (prior_day.get("trades") or []) if t.get("status") not in ("FAILED", None)]
    to_send, dup, held = [], [], []
    for o in orders:
        key = (o.get("ticker"), o.get("action"), o.get("source"))
        same = [t for t in rows
                if (t.get("ticker"), t.get("action"), t.get("source")) == key
                and Path(t.get("approved_ref") or approved_name).name == approved_name]
        if same:
            t = same[-1]
            dup.append({**o, "_why": f"오늘 기록에 같은 승인({approved_name})의 같은 건이 있다 — 전송 "
                                     f"{str(t.get('ts', ''))[11:19]} · {t.get('status')}/"
                                     f"{(t.get('fill') or {}).get('verdict') or '미확정'}"})
            continue
        opened = [t for t in rows if (t.get("ticker"), t.get("action")) == key[:2] and is_open_trade(t)]
        if opened:
            t = opened[-1]
            held.append({**o, "_why": f"같은 종목·방향의 열린 주문이 있다(승인 "
                                      f"{Path(t.get('approved_ref') or '?').name} · 주문번호 "
                                      f"{(t.get('result') or {}).get('order_no') or '?'})"})
            continue
        to_send.append(o)
    return to_send, dup, held


def _overfill(path: Path) -> dict:
    """그날 기록의 승인 대비 대조 결과(`fill.finalize`가 쓴다). 없으면 {}."""
    return (_read_day(path).get("reconcile") or {}) if path.exists() else {}


def merge_day_record(path: Path, record: dict) -> dict:
    """그날 거래 기록에 이번 run을 **덧붙인다**. 덮어쓰지 않는다.

    왜 중요한가: 이 파일은 감사 기록이면서 동시에 안전장치의 입력이다.
    `risk_guard.spent_today()`가 여기서 "오늘 이미 얼마 썼는지"를 읽어 일일 상한을
    깎는다. 국내장은 하루 두 번(09:35·11:05) 돌 예정이므로, 두 번째 run이 파일을
    덮어쓰면 상한이 초기화되어 하루에 두 배가 나갈 수 있다.
    2026-09-08 매도 테스트에서 아침 매수 기록이 실제로 사라지며 발견됐다.

    앞선 run들의 메타데이터는 `runs`에 쌓고, 최상위에는 항상 최신 run을 둔다 —
    `balance_after`는 가장 마지막 상태여야 의미가 있기 때문이다.
    """
    prior = None
    if path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            # 못 읽는다고 덮어쓰면 이미 나간 주문의 유일한 증거가 사라진다.
            # 옆으로 치워 보존하고, 사람이 알아채도록 크게 알린다.
            aside = path.with_suffix(f".corrupt_{datetime.now(KST):%H%M%S}.json")
            path.rename(aside)
            print(f"\n★ 기존 거래 기록을 읽을 수 없어 {aside.name} 로 옮겨 보존했다 ({e}).\n"
                  f"  오늘 이미 나간 주문이 일일 상한 계산에서 빠질 수 있으니 직접 확인할 것.",
                  file=sys.stderr)

    if isinstance(prior, dict) and prior.get("trades") is not None:
        merged = dict(record)
        merged["trades"] = list(prior.get("trades") or []) + list(record.get("trades") or [])
        runs = list(prior.get("runs") or [])
        if not runs:      # 이 형식 이전에 쓰인 파일 — 앞선 run을 하나로 복원해 둔다
            runs.append({k: prior.get(k) for k in RUN_KEYS if k in prior})
        runs.append({k: record.get(k) for k in RUN_KEYS if k in record})
        merged["runs"] = runs
    else:
        merged = dict(record)
        merged["runs"] = [{k: record.get(k) for k in RUN_KEYS if k in record}]

    # ★ 승인 대비 대조 — 종목·방향별 집행 합이 승인 합을 넘으면 사고로 기록한다(`fill.finalize`).
    #   여기서 하는 이유: `--no-wait`·회전 매도 선기록처럼 fill.confirm을 안 거치는 쓰기도 잡아야 한다.
    #   대조가 실패해도 기록은 쓴다 — 이미 나간 주문의 유일한 증거를 대조 버그가 막으면 안 된다.
    try:
        import fill
        merged = fill.finalize(merged, path, datetime.now(KST))
    except Exception as e:                              # noqa: BLE001
        print(f"★ 승인 대조 실패({type(e).__name__}: {e}) — 기록은 그대로 쓴다", file=sys.stderr)

    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _send_orders(client: KisClient, orders: list, market: str,
                 approved_name: str = "", approved_at: str = "") -> tuple:
    """주문 목록을 전송하고 trades 행을 만든다. 반환 (trades, failures).

    행마다 `approved_ref`(승인 파일명)·`approved_at`을 적는다 — 멱등 전송(`dedupe_against_day`)과
    승인 대비 대조(`fill.reconcile`)가 이 두 값으로 "어느 승인의 건인가"를 안다.
    """
    trades = []
    failures = 0
    limits = _send_cfg()
    for o in orders:
        try:
            px, qty, why, skip = refresh_limit(client, o, limits)
            if skip:
                # 보내지 않은 건 — 브로커에 걸린 주문이 없다. FAILED(client-side)로 적어 fill이 기다리지 않게 한다.
                print(f"  ⊘ 미전송 {o['action']} {o['ticker']}: {skip}", file=sys.stderr)
                trades.append({
                    "ts": datetime.now(KST).isoformat(), "status": "FAILED", "market": market,
                    "approved_ref": approved_name, "approved_at": approved_at,
                    "ticker": o["ticker"], "name": o.get("name", ""), "action": o["action"],
                    "qty": o["qty"], "price": o["price"], "source": o.get("source"),
                    "full_exit": bool(o.get("full_exit")), "thesis_id": o.get("thesis_id"),
                    "trigger_id": o.get("trigger_id"),
                    "discipline_reasons": o.get("reasons", []), "proposal": o.get("proposal"),
                    "result": {"error": skip, "client_side": True},
                    "fill": {"verdict": "REJECTED", "note": f"client-side: {skip}", "requested": o["qty"]},
                })
                failures += 1
                continue
            if why:
                print(f"  · {o['ticker']} {why}")
                o = {**o, "price": px, "qty": qty}
            res = place(client, o)
            if res.get("order_no"):
                print(f"  OK {o['action']} {o['ticker']} → 주문번호 {res.get('order_no')}")
                status = "SENT"
            else:
                # 브로커가 성공(rt_cd=0)을 줬는데 주문번호를 못 읽었다.
                # 주문이 나갔을 수 있으므로 FAILED로 적으면 안 된다 — 기록이 거짓이 된다.
                # UNKNOWN으로 남기고 사람이 확인하게 한다(raw 응답을 통째로 보존).
                print(f"  UNKNOWN {o['action']} {o['ticker']}: 응답은 성공인데 주문번호가 "
                      f"없다 — 체결 여부를 반드시 직접 확인할 것. raw={res.get('raw')}",
                      file=sys.stderr)
                status, failures = "UNKNOWN", failures + 1
        except (KisError, ValueError) as e:
            print(f"  FAIL {o['action']} {o['ticker']}: {e}", file=sys.stderr)
            res, status, failures = {"error": str(e)}, "FAILED", failures + 1
        trades.append({
            "ts": datetime.now(KST).isoformat(),
            "status": status,
            "market": market,
            "approved_ref": approved_name,
            "approved_at": approved_at,
            "ticker": o["ticker"],
            "name": o.get("name", ""),
            "action": o["action"],
            "qty": o["qty"],
            "price": o["price"],
            "source": o.get("source"),
            "full_exit": bool(o.get("full_exit")),
            "thesis_id": o.get("thesis_id"),
            # ★ 어느 논지의 **어느 다리**가 집행됐는지 — fill이 그 다리를 소진 표시하는 데 쓴다(2026-09-23).
            #   예전에는 risk_guard가 쓴 trigger_id가 여기서 떨어져, 체결 뒤에도 다리가 살아 매 run 재발화했다.
            "trigger_id": o.get("trigger_id"),
            "discipline_reasons": o.get("reasons", []),
            # 근거 스냅샷 — 이 파일 하나로 "왜 샀는지"가 재구성돼야 한다.
            "proposal": o.get("proposal"),
            "result": res,
        })

    return trades, failures


def main() -> int:
    ap = argparse.ArgumentParser(description="approved 주문을 집행한다 (기본 dry-run)")
    ap.add_argument("approved", help="signals/approved_YYMMDD_kr.json")
    ap.add_argument("--send", action="store_true",
                    help="실제로 주문을 전송한다. 없으면 계획만 출력한다.")
    ap.add_argument("--real", action="store_true",
                    help="실전 서버. 모의 6개월 검증 전에는 쓰지 않는다.")
    ap.add_argument("--stamp", metavar="YYMMDD",
                    help="산출물 파일명에 쓸 세션 날짜. 미국장은 KST 자정을 넘겨 "
                         "한 run이 두 날짜로 갈라지므로 호출자가 고정해 넘긴다.")
    ap.add_argument("--note", help="분석노트 — 체결 확정 줄을 §11에 쓴다")
    ap.add_argument("--no-wait", action="store_true",
                    help="전송만 하고 체결 확정(fill)을 건너뛴다. 기본은 확정까지 기다린다.")
    ap.add_argument("--max-sec", type=int, default=540, help="체결 확정에 쓸 이 호출의 시간 예산")
    ap.add_argument("--force-hours", action="store_true",
                    help="장외에도 전송한다(기본은 거부 — 브로커가 장종료로 거부하고 run만 낭비된다)")
    args = ap.parse_args()

    path = Path(args.approved)
    approved = rg._read_json(path, "approved")
    limits = rg._read_json(rg.LIMITS_PATH, "limits")

    try:
        preflight(approved, path, limits)
    except rg.Rejection as e:
        print(f"집행 거부 — 주문 0건: {e}", file=sys.stderr)
        return 2

    orders = approved.get("orders", [])
    market = approved.get("market", "KR")
    if not orders:
        print("승인된 주문 0건 — 집행할 것이 없다.")
        return 0

    print(f"{'[실제 전송]' if args.send else '[DRY-RUN — 전송 안 함]'} "
          f"{market} 시장 · {len(orders)}건")
    for o in orders:
        src = "규율" if o.get("source") == "discipline" else "제안"
        amt = o["qty"] * o["price"]
        print(f"  [{src}] {o['action']:4} {o['ticker']} {o.get('name', '')} "
              f"x{o['qty']} @ {o['price']:,.2f} = {amt:,.0f}")

    # ★ 스탬프는 approved 파일명의 세션일에서 딴다 — 미국 run은 KST 자정을 넘겨 실행 날짜로 쓰면
    #   같은 run의 기록이 두 날짜로 갈라진다(9/15 run이 `trades_260916_us.json`을 만들어 회고가 "거래 없음"으로 오판).
    stamp = args.stamp or _stamp_from(path)
    out = JOURNAL_DIR / f"trades_{stamp}_{market.lower()}.json"
    approved_at = str(approved.get("generated_at") or "")

    # ★ 멱등 — 같은 승인으로 다시 돌려도 이미 나간 건은 다시 나가지 않는다(dry-run에서도 미리 보여준다).
    orders, dup, held = dedupe_against_day(orders, _read_day(out), path.name)
    skipped = []
    for o in dup:
        print(f"  ⊘ 건너뜀 {o['action']} {o['ticker']} x{o['qty']} — {o['_why']} — 다시 보내지 않는다(멱등). "
              f"새 승인이면 파일명을 바꿔라(_v2/_reissue)", file=sys.stderr)
        skipped.append({k: o.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": "중복 — " + o["_why"]})
    for o in held:
        print(f"  ⊘ 보류 {o['action']} {o['ticker']} x{o['qty']} — {o['_why']} — 먼저 `python3 fill.py {out}` 로 "
              f"확정하라(이 approved를 다시 돌려도 이미 나간 건은 다시 나가지 않는다)", file=sys.stderr)

    if not args.send:
        print("\n실제로 보내려면 --send 를 붙여라.")
        return 0

    # ★ 장외 전송 차단 — 2026-09-10 MSFT를 11:31 KST에 보내 `[40580000] 모의투자 장종료`로
    #   거부됐다. 브로커 거부는 무해하지만 그 run의 판단이 통째로 증발한다. 시간을 여기서 본다.
    sess = market_session(market)
    if not sess["is_open"] and not args.force_hours:
        print(f"집행 거부 — 장외: {sess['why']} (지금 {datetime.now(KST):%H:%M} KST). "
              f"정규장에 다시 돌리거나 --force-hours.", file=sys.stderr)
        return 2
    # 마감 임박 안내(거부 아님) — 국내 15:20 이후 주문은 동시호가로 15:30에 체결된다(2026-09-22 KR 15:20 전송 → 15:30 체결).
    left_min = (sess["close"] - datetime.now(KST)).total_seconds() / 60
    if 0 <= left_min <= 10:
        print(f"  · 마감 {left_min:.0f}분 전 전송 — {'동시호가(15:30 체결)' if market == 'KR' else '마감 직전'}; 정정·취소 시간이 거의 없다")

    client = KisClient(svr="real" if args.real else "paper", allow_real=args.real)

    # 체결 확인의 기준선 — 전송 **전** 보유 수량. 주문번호가 나왔다는 것은 접수됐다는 뜻이지
    # 체결됐다는 뜻이 아니다. 이 기준선과 비교해야 "샀다"고 말할 수 있다.
    # ★ 미국도 기준선을 실제로 읽는다. 예전에는 `else {}`여서 **미국 run의 기준선이 빈
    #   dict**였고, 그러면 이미 들고 있던 수량이 전부 '새로 늘어난 것'으로 계산돼 판정이
    #   통째로 틀렸다. 기준선이 없으면 `{}`가 아니라 `None`이어야 한다 —
    #   `{}`는 "보유 0"이라는 **주장**이고 `None`은 "모른다"다.
    qty_before = {}
    try:
        bal_before = client.domestic_balance() if market == "KR" else client.overseas_balance()
        for p in bal_before.get("positions", []):
            qty_before[p["ticker"]] = int(p.get("qty") or 0)
    except (KisError, KeyError, TypeError, AttributeError):
        qty_before = None                   # 조회 실패 — 아래에서 '확인 불가'로 보고한다

    import fill
    limits_send = _send_cfg()
    blocked = 0
    # ★ 보류 건(같은 종목·방향의 열린 주문)은 먼저 확정을 시도한 뒤 다시 판정한다 — 살아 있는 주문 위에
    #   같은 주문을 얹으면 두 번 산다/판다.
    if held:
        print(f"  열린 주문 {len(held)}건 먼저 확정 시도 …")
        fill.confirm(out, client=client, max_sec=min(180, max(60, args.max_sec // 3)), chase=True, quiet=False)
        again, dup2, held = dedupe_against_day([{k: v for k, v in o.items() if k != "_why"} for o in held],
                                               _read_day(out), path.name)
        orders += again
        for o in dup2:
            print(f"  ⊘ 건너뜀 {o['action']} {o['ticker']} x{o['qty']} — {o['_why']}(멱등)", file=sys.stderr)
            skipped.append({k: o.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": "중복 — " + o["_why"]})
        for o in held:
            print(f"  ⊘ 보류 유지 {o['action']} {o['ticker']} x{o['qty']} — {o['_why']} — "
                  f"`python3 fill.py {out}` 를 종료코드 0까지 돌린 뒤 같은 approved로 다시 돌려라", file=sys.stderr)
            skipped.append({k: o.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": "보류 — " + o["_why"]})
            blocked += 1

    # ★ 매도 상한 — 보유보다 많이 팔 수는 없다(부분 체결 뒤 같은 매도가 다시 오는 경우를 막는다).
    if qty_before is not None:
        capped = []
        for o in orders:
            if o.get("action") == "SELL":
                have = int(qty_before.get(o.get("ticker"), 0))
                if have <= 0:
                    print(f"  ⊘ 건너뜀 SELL {o['ticker']} x{o['qty']} — 보유 0주, 매도할 것이 없다", file=sys.stderr)
                    skipped.append({k: o.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": "보유 0주"})
                    continue
                if int(o.get("qty") or 0) > have:
                    print(f"  · SELL {o['ticker']} 수량 {o['qty']}→{have} (보유 상한)")
                    o = {**o, "qty": have}
            capped.append(o)
        orders = capped

    if not orders:
        print("승인 주문 전건이 이미 오늘 기록에 있거나 보류됐다 — 보낼 것 없음(멱등).")
        rc = 0
        if out.exists() and not args.no_wait:
            rc = fill.confirm(out, client=client, max_sec=args.max_sec, chase=True,
                              note=Path(args.note) if args.note else None)
        if _report_overfill(out):
            return 4
        return 1 if (rc != 0 or blocked) else 0

    # ★ 회전 매도(source=funding)가 있으면 **매도가 체결된 뒤에** 매수를 보낸다 — 매도 대금이
    #   주문가능금액에 잡혀야 매수가 거부되지 않는다. 그 밖의 매도는 매수와 같이 나간다.
    #   (같은 approved 재호출의 중복 전송은 위 `dedupe_against_day`가 막는다 — 2026-09-21 두산 46주 사고.)
    # ★ 전송 누적은 여기서 시작한다 — `_send_orders`로 분리하면서 main의 초기화가 빠져, 회전 매도 없는 run이
    #   주문 접수 **뒤** `trades += t2`에서 UnboundLocalError로 죽고 기록 파일을 못 썼다(2026-09-26 US 260925 — 4건 접수·기록 0).
    trades: list = []
    failures = 0
    funding = [o for o in orders if o.get("source") == "funding"]
    rest = [o for o in orders if o.get("source") != "funding"]
    # ★ 회전 매도는 **그 돈을 쓸 매수가 나갈 수 있을 때만** 판다(2026-09-23). 매도는 되돌릴 수 없고 매수는 다음 run이
    #   다시 낼 수 있으니, 되돌릴 수 없는 쪽을 뒤에 둔다. 실사례: 09-23 KR — 삼성생명 1주를 "테스 매수 자금 부족"만을
    #   이유로 팔아 체결(12:49)됐는데, 테스는 집행 시점 시세가 승인가 대비 +2.13%(한도 ±2.0%)라 `refresh_limit`가
    #   전송조차 안 했다. 두 안전장치가 각각은 옳게 작동했는데 순서가 엮여 **판 돈이 아무것도 안 샀다.**
    if funding:
        blocked_f = []
        for so in funding:
            tgt = next((b for b in rest if b.get("action") == "BUY"
                        and (b.get("ticker") == so.get("funds")
                             or (b.get("funding") or {}).get("sold") == so.get("ticker"))), None)
            if tgt is None:
                continue                          # 자금 대상이 없는 회전 매도는 없지만, 있으면 그대로 보낸다
            try:
                _px, _q, _why, skip = refresh_limit(client, tgt, limits_send)
            except Exception as e:                # noqa: BLE001 — 사전 점검 실패가 집행을 막지 않는다
                print(f"  · 회전 매도 사전 점검 실패({type(e).__name__}) — 그대로 보낸다", file=sys.stderr)
                continue
            if skip:
                blocked_f.append((so, tgt, skip))
        for so, tgt, skip in blocked_f:
            why = (f"회전 매도 보류 — 자금 대상 {tgt['ticker']}가 미전송된다({skip}). 팔아도 쓸 곳이 없다 — "
                   f"매도는 되돌릴 수 없고 매수는 다음 run이 다시 낸다")
            print(f"  ⊘ 보류 {so['action']} {so['ticker']} x{so['qty']} — {why}", file=sys.stderr)
            skipped.append({k: so.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": why})
        funding = [o for o in funding if all(o is not b[0] for b in blocked_f)]
    if funding:
        t1, f1 = _send_orders(client, funding, market, path.name, approved_at)
        trades += t1
        failures += f1
        # ★ 전송 전 기준선을 행에 박는다 — 모의서버 국내 체결 TR이 아무것도 안 돌려줄 때
        #   fill.py의 잔고 대조(_fallback_balance)는 `fill.qty_before`가 있어야 판정한다.
        #   2026-09-21: 두산 46주 회전 매도가 잔고에서는 확정(98→52)됐는데 이 값이 없어 미확정으로 남았다.
        if qty_before is not None:
            for t in t1:
                t.setdefault("fill", {})["qty_before"] = qty_before.get(t.get("ticker"), 0)
        pre = {"generated_at": datetime.now(KST).isoformat(), "svr": client.svr,
               "approved_ref": str(path), "approved_at": approved_at,
               "approved_orders": approved_snapshot(approved), "skipped": skipped,
               "signal_ref": approved.get("signal_ref"),
               "limits_sha256": approved.get("limits_sha256"), "trades": t1,
               "fill_summary": {"sent": len(funding)}}
        merge_day_record(out, pre)
        if not args.no_wait:
            print("  회전 매도 체결 확정 대기 …")
            try:
                rc_f = fill.confirm(out, client=client, max_sec=max(120, args.max_sec // 2),
                                    chase=True, quiet=False)
            except Exception as ex_:                      # noqa: BLE001 — 확정 중 예외로 프로세스를 죽이지 않는다
                print(f"  ★ 확정 중 예외({type(ex_).__name__}: {str(ex_)[:120]}) — 회전 매도 주문은 이미 나갔고 기록은 {out}에 있다. "
                      f"`python3 fill.py {out}` 로 확정한 뒤 같은 approved로 다시 돌려라(멱등 — 이미 나간 건은 다시 나가지 않는다)",
                      file=sys.stderr)
                return 1
            if rc_f != 0:
                print(f"  ★ 회전 매도가 미확정이라 매수를 보내지 않는다 — `python3 fill.py {out}` 로 확정한 뒤 "
                      f"같은 approved로 다시 돌려라(이미 나간 주문은 다시 보내지 않는다 — 멱등)",
                      file=sys.stderr)
                return 1
        # 확정된 매도 행은 이미 파일에 있다 — 아래 병합에서 중복되지 않게 뺀다.
        trades = []
        orders_left = rest
    else:
        orders_left = orders
    t2, f2 = _send_orders(client, orders_left, market, path.name, approved_at)
    trades += t2
    failures += f2

    balance_after = verify(client, market)

    # ── 체결 확인 — **주문 접수와 체결은 다른 사건이다.**
    # 주문번호가 나왔다고 "샀다"고 말하면 안 된다. 지정가는 조용히 미체결로 남고
    # 로그에는 성공으로 찍힌다. 잔고 수량이 실제로 늘었는지가 유일한 증거다.
    #
    # ★ 기록 **전에** 판정한다. 예전에는 파일을 쓴 뒤에 확인해서, 이 run의 가장 중요한
    #   사실(정말 샀는가)이 터미널에만 찍히고 사라졌다 — `status`는 접수를 뜻하는 SENT뿐이라
    #   다음 run의 회고가 "제안한 것"을 "집행한 것"으로 읽었다.
    unfilled, fills = _report_fills(orders_left, qty_before, balance_after,
                                    {t["ticker"]: t["status"] for t in trades})
    for t in trades:
        t["fill"] = fills.get(t.get("ticker"), {"verdict": "UNKNOWN",
                                                "note": "잔고 대조 불가 — 체결 여부 미확인"})

    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "generated_at": datetime.now(KST).isoformat(),
        "svr": client.svr,
        "approved_ref": str(path),
        "approved_at": approved_at,
        "approved_orders": approved_snapshot(approved),
        "skipped": skipped,
        "signal_ref": approved.get("signal_ref"),
        "limits_sha256": approved.get("limits_sha256"),
        "trades": trades,
        "fill_summary": {
            "sent": len(orders_left),
            "filled": sum(1 for f in fills.values() if f["verdict"] == "FILLED"),
            "unfilled": sum(1 for f in fills.values() if f["verdict"] == "UNFILLED"),
            "partial": sum(1 for f in fills.values() if f["verdict"] == "PARTIAL"),
            # 거부는 미체결과 따로 센다 — 기다릴 것이 없는 건이다.
            "rejected": sum(1 for f in fills.values() if f["verdict"] == "REJECTED"),
            "unverified": sum(1 for f in fills.values() if f["verdict"] == "UNKNOWN"),
        },
        "thesis_unsynced": _thesis_unsynced(fills),
        "balance_after": balance_after,
    }
    merge_day_record(out, record)
    if record["thesis_unsynced"]:
        print("\n★ 체결됐는데 논지가 `held`가 아니다 — **매도 조건이 감시되지 않는다.**",
              file=sys.stderr)
        for u in record["thesis_unsynced"]:
            print(f"   {u['ticker']}: 논지 상태 {u['thesis_status']} "
                  f"→ `theses.py set-status --id <id> --status held`", file=sys.stderr)

    print(f"\n거래 기록: {out}")
    if isinstance(balance_after, dict) and "error" not in balance_after:
        # 해외 잔고 TR은 예수금을 주지 않아 cash가 None일 수 있다(구매력은 별도 TR).
        cash = balance_after.get("cash")
        cash_str = f"{cash:,.2f}" if isinstance(cash, (int, float)) else "미확인"
        print(f"검증 재조회: 예수금 {cash_str} "
              f"/ 보유 {len(balance_after.get('positions', []))}종목")
    elif isinstance(balance_after, dict):
        print(f"검증 재조회 실패: {balance_after['error'][:120]}")

    # ★ 체결 확인이 **전면 실패**하면 그것도 실패다. 예전에는 `failures or unfilled`만 봐서,
    #   잔고 조회가 죽어 판정을 하나도 못 했는데도 종료코드 0(성공)이 나갔다 —
    #   호출자에게는 "주문 다 나가고 문제 없음"으로 보인다. **모른다는 것은 성공이 아니다.**
    unverified = bool(orders_left) and not fills
    if unverified:
        print("\n★ 체결 판정 0건 — 무엇이 체결됐는지 확인하지 못했다. "
              "이 run을 성공으로 기록하지 말 것.", file=sys.stderr)

    # ★ 접수는 run의 끝이 아니다 — 주문이 체결·취소·만료 중 하나로 확정될 때까지 붙어 있는다.
    #   위 잔고 대조는 전송 직후의 스냅샷일 뿐이라 미체결(UNFILLED)이 정상이고, 그 상태로
    #   run을 닫으면 그 주문의 운명을 아무도 모른다(2026-09-15 VST). `fill`이 종결시킨다.
    if not args.no_wait:
        try:
            rc = fill.confirm(out, client=client, max_sec=args.max_sec, chase=True,
                              note=Path(args.note) if args.note else None)
        except Exception as ex_:                          # noqa: BLE001 — 2026-09-22 KR: 상태 TR 타임아웃으로 프로세스가 죽었다
            print(f"\n★ 확정 중 예외({type(ex_).__name__}: {str(ex_)[:120]}) — 주문은 나갔고 기록은 {out}에 있다. "
                  f"`python3 fill.py {out}` 로 확정한 뒤 같은 approved로 다시 돌려라(멱등).", file=sys.stderr)
            rc = 3
        if rc != 0:
            print(f"\n★ 미확정 주문이 남았다 — `python3 fill.py {out}"
                  + (f" --note {args.note}" if args.note else "") + "`를 종료코드 0까지 다시 돌려라"
                  " (execute.py를 같은 approved로 다시 돌려도 이미 전송된 건은 건너뛴다 — 멱등).",
                  file=sys.stderr)
        if _report_overfill(out):
            return 4
        return 1 if (failures or rc != 0 or blocked) else 0
    if _report_overfill(out):
        return 4
    return 1 if (failures or unfilled or unverified or blocked) else 0


def _report_overfill(out: Path) -> bool:
    """승인 초과가 기록됐으면 크게 알린다 — 종료코드 4. 복구 주문은 내지 않는다(그것도 승인 밖 집행이다)."""
    rec = _overfill(out)
    n = int(rec.get("overfill") or 0)
    if not n:
        return False
    print(f"\n★★ 승인 초과 {n}건 — 사고 기록 {', '.join(rec.get('incidents') or []) or '(기록 실패)'} "
          f"(journal/incidents.jsonl). 종목·방향별 집행 합이 승인 합을 넘었다: "
          + "; ".join(f"{o['ticker']} {o['action']} 승인 {o['approved']}/집행 {o['executed']}"
                      for o in rec.get("overfill_list") or [])
          + ". 복구 주문을 내지 말 것 — 시그널 밖 주문은 또 다른 승인 밖 집행이다.", file=sys.stderr)
    return True


def _thesis_unsynced(fills: dict) -> list:
    """체결됐는데 논지가 아직 `held`가 아닌 티커. **비어 있지 않으면 손절이 감시되지 않는다.**

    `armed` 상태에서는 `theses.py check`가 *진입* 조건만 본다. 체결 후 상태를 안 옮기면
    걸어둔 목표·손절·시간만료가 전부 잠들어 있고, 로그 어디에도 그 사실이 안 남는다.
    *실사례(2026-09-10): 한화오션 4주를 체결하고 상태를 안 옮겨 손절 81,500이
    이틀 가까이 감시되지 않았다 — 유니버스 표를 만들다 우연히 발견했다.*
    """
    store = JOURNAL_DIR / "theses.json"
    if not store.exists():
        return []
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    by_tic = {}
    for x in (data.get("theses") or []):
        by_tic.setdefault(str(x.get("ticker")), []).append(x.get("status"))
    out = []
    for tic, f in fills.items():
        if f.get("verdict") not in ("FILLED", "PARTIAL"):
            continue
        st = by_tic.get(tic) or []
        if "held" not in st:
            out.append({"ticker": tic, "thesis_status": st or ["(논지 없음)"]})
    return out


def _report_fills(orders: list, qty_before, balance_after, statuses: dict = None) -> tuple:
    """전송 전후 보유 수량을 대조해 체결/미체결을 판정한다.

    돌려주는 것은 (미체결 건수, {티커: 판정 레코드}). **레코드가 파일로 남아야**
    다음 run의 회고가 '제안한 것'이 아니라 '집행된 것'을 채점할 수 있다.

    `statuses`는 티커별 전송 결과(`SENT`/`UNKNOWN`/`FAILED`)다. **거부된 주문은
    미체결이 아니다** — 미체결은 "걸려 있다"이고 거부는 "아무것도 없다"여서 행선지가
    다르다(전자는 대기, 후자는 재발행 또는 포기). 둘을 섞으면 다음 run이 있지도 않은
    주문을 기다린다.
    *실사례(2026-09-10): MSFT가 `[40580000] 모의투자 장종료`로 거부됐는데 보유 수량이
    안 늘었다는 이유로 '미체결'로 판정·보고됐다.*
    """
    statuses = statuses or {}
    if qty_before is None or not isinstance(balance_after, dict) or "error" in balance_after:
        print("\n★ 체결 확인 불가 — 잔고 조회가 실패했다. "
              "'주문을 냈다'까지만 보고하고 '샀다'고 하지 말 것.", file=sys.stderr)
        return 0, {}
    after = {p["ticker"]: int(p.get("qty") or 0) for p in balance_after.get("positions", [])}
    print("\n체결 확인 (주문 접수 ≠ 체결):")
    unfilled, fills = 0, {}
    for o in orders:
        t = o["ticker"]
        if statuses.get(t) == "FAILED":
            fills[t] = {"verdict": "REJECTED", "qty_before": qty_before.get(t, 0),
                        "qty_after": after.get(t, 0), "delta": 0,
                        "requested": o["qty"] if o["action"] == "BUY" else -o["qty"],
                        "limit_price": o["price"],
                        "note": "브로커가 주문을 거부했다 — 접수된 것이 없다. "
                                "대기 중인 주문이 아니므로 기다리지 말 것"}
            print(f"  ⊘ **거부** {o['action']} {t} {o.get('name','')} "
                  f"— 브로커가 받지 않았다. 걸려 있는 주문이 아니다.")
            continue
        delta = after.get(t, 0) - qty_before.get(t, 0)
        want = o["qty"] if o["action"] == "BUY" else -o["qty"]
        rec = {"qty_before": qty_before.get(t, 0), "qty_after": after.get(t, 0),
               "delta": delta, "requested": want, "limit_price": o["price"]}
        if delta == want:
            rec["verdict"] = "FILLED"
            print(f"  ✓ 체결 {o['action']} {t} {o.get('name','')} {abs(delta)}주")
        elif delta == 0:
            rec["verdict"] = "UNFILLED"
            rec["note"] = "주문 접수됐으나 보유 수량 불변 — 장 마감까지 안 닿으면 자동 취소"
            print(f"  ✗ **미체결** {o['action']} {t} {o.get('name','')} "
                  f"— 주문은 접수됐으나 보유 수량이 그대로다. 지정가 {o['price']:,.0f}에 "
                  f"걸려 있고, 장 마감까지 안 닿으면 자동 취소된다.")
            unfilled += 1
        else:
            # ★ 전송 직후의 부분체결은 종결이 아니다 — 잔량이 살아 있다. 판정은 PARTIAL로 두되(회귀 테스트 ㉞의 계약),
            #   fill.py가 `confirmed_at` 없는 PARTIAL을 **열린 건**으로 다시 폴링한다(2026-09-21 LG엔솔: 즉시 1주 →
            #   실제 11주 전량 체결인데 기록이 PARTIAL 1로 닫혔던 결함).
            rec["verdict"] = "PARTIAL"
            rec["partial_seen"] = abs(delta)
            print(f"  △ 부분체결 {o['action']} {t} {o.get('name','')} "
                  f"— 요청 {abs(want)}주 중 {abs(delta)}주 · 잔량은 fill.py가 확정")
            unfilled += 1
        fills[t] = rec
    if unfilled:
        print(f"\n★ 미체결·부분체결 {unfilled}건 — **이 run은 '매수했다'가 아니라 "
              f"'주문을 걸어뒀다'로 보고해야 한다.**", file=sys.stderr)
    return unfilled, fills


if __name__ == "__main__":
    sys.exit(main())
