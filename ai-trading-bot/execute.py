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

from kis_client import KisClient, KisError, snap_kr_price, market_session, order_excd
import risk_guard as rg

HERE = Path(__file__).parent
JOURNAL_DIR = HERE / "journal"
KST = timezone(timedelta(hours=9))

# 시세 조회용 코드(EXCD)와 주문용 코드가 다르다. 주문용 → 시세용 변환.
EXCD_QUOTE = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}


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
            excd_q = EXCD_QUOTE.get(order_excd(order.get("excd") or "NASD"), "NAS")
            live = float(client.overseas_price(order["ticker"], excd=excd_q)["price"])
    except (KisError, KeyError, TypeError, ValueError) as e:
        return approved_px, qty, f"현재가 재조회 실패({type(e).__name__}) — 승인가 유지", None
    if live <= 0:
        return approved_px, qty, "현재가 0 — 승인가 유지", None
    side = order["action"]
    worse = live > approved_px if side == "BUY" else live < approved_px
    if not worse:
        return approved_px, qty, "", None
    drift = (live - approved_px) / approved_px * 100
    max_pct = float((limits or _send_cfg()).get("send_refresh_max_pct") or 2.0)
    if abs(drift) > max_pct:
        return approved_px, qty, "", (f"가격 이탈 — 집행 시점 시세 {live:,.2f}가 승인가 {approved_px:,.2f} 대비 "
                                      f"{drift:+.2f}% (한도 ±{max_pct}%) — 전송 안 함, 다음 run이 재판단")
    px = float(snap_kr_price(live, side)) if market == "KR" else round(live, 2)
    new_qty = qty
    if side == "BUY":
        new_qty = min(qty, int(approved_amt // px)) if px > 0 else 0
        if new_qty <= 0:
            return px, 0, "", f"승인 금액 {approved_amt:,.0f} 안에서 {px:,.2f}로는 0주 — 전송 안 함"
    why = (f"승인가 {approved_px:,.2f} → {px:,.2f} 갱신 (집행 시점 시세 {live:,.2f}, 승인 대비 {drift:+.2f}%)"
           + (f" · 수량 {qty}→{new_qty}(승인 금액 안)" if new_qty != qty else ""))
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
            runs.append({k: prior.get(k) for k in
                         ("generated_at", "approved_ref", "signal_ref", "limits_sha256")})
        runs.append({k: record.get(k) for k in
                     ("generated_at", "approved_ref", "signal_ref", "limits_sha256")})
        merged["runs"] = runs
    else:
        merged = dict(record)
        merged["runs"] = [{k: record.get(k) for k in
                           ("generated_at", "approved_ref", "signal_ref", "limits_sha256")}]

    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def _send_orders(client: KisClient, orders: list, market: str) -> tuple:
    """주문 목록을 전송하고 trades 행을 만든다. 반환 (trades, failures)."""
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
                    "ticker": o["ticker"], "name": o.get("name", ""), "action": o["action"],
                    "qty": o["qty"], "price": o["price"], "source": o.get("source"),
                    "full_exit": bool(o.get("full_exit")), "thesis_id": o.get("thesis_id"),
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
            "ticker": o["ticker"],
            "name": o.get("name", ""),
            "action": o["action"],
            "qty": o["qty"],
            "price": o["price"],
            "source": o.get("source"),
            "full_exit": bool(o.get("full_exit")),
            "thesis_id": o.get("thesis_id"),
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

    # ★ 회전 매도(source=funding)가 있으면 **매도가 체결된 뒤에** 매수를 보낸다 — 매도 대금이
    #   주문가능금액에 잡혀야 매수가 거부되지 않는다. 그 밖의 매도는 매수와 같이 나간다.
    funding = [o for o in orders if o.get("source") == "funding"]
    rest = [o for o in orders if o.get("source") != "funding"]
    trades, failures = [], 0
    if funding:
        t1, f1 = _send_orders(client, funding, market)
        trades += t1
        failures += f1
        stamp_tmp = args.stamp or _stamp_from(path)
        out_tmp = JOURNAL_DIR / f"trades_{stamp_tmp}_{market.lower()}.json"
        pre = {"generated_at": datetime.now(KST).isoformat(), "svr": client.svr,
               "approved_ref": str(path), "signal_ref": approved.get("signal_ref"),
               "limits_sha256": approved.get("limits_sha256"), "trades": t1,
               "fill_summary": {"sent": len(funding)}}
        merge_day_record(out_tmp, pre)
        if not args.no_wait:
            import fill
            print("  회전 매도 체결 확정 대기 …")
            rc_f = fill.confirm(out_tmp, client=client, max_sec=max(120, args.max_sec // 2),
                                chase=True, quiet=False)
            if rc_f != 0:
                print("  ★ 회전 매도가 미확정이라 매수를 보내지 않는다 — fill.py로 확정한 뒤 다시 돌려라",
                      file=sys.stderr)
                return 1
        # 확정된 매도 행은 이미 파일에 있다 — 아래 병합에서 중복되지 않게 뺀다.
        trades = []
        orders_left = rest
    else:
        orders_left = orders
    t2, f2 = _send_orders(client, orders_left, market)
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

    # ★ 스탬프는 approved 파일명의 세션일에서 딴다 — 미국 run은 KST 자정을 넘겨 실행 날짜로 쓰면
    #   같은 run의 기록이 두 날짜로 갈라진다(9/15 run이 `trades_260916_us.json`을 만들어 회고가 "거래 없음"으로 오판).
    stamp = args.stamp or _stamp_from(path)
    out = JOURNAL_DIR / f"trades_{stamp}_{market.lower()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "generated_at": datetime.now(KST).isoformat(),
        "svr": client.svr,
        "approved_ref": str(path),
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
        import fill
        rc = fill.confirm(out, client=client, max_sec=args.max_sec, chase=True,
                          note=Path(args.note) if args.note else None)
        if rc != 0:
            print(f"\n★ 미확정 주문이 남았다 — `python3 fill.py {out}"
                  + (f" --note {args.note}" if args.note else "") + "`를 종료코드 0까지 다시 돌려라.",
                  file=sys.stderr)
        return 1 if (failures or rc != 0) else 0
    return 1 if (failures or unfilled or unverified) else 0


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
            rec["verdict"] = "PARTIAL"
            print(f"  △ 부분체결 {o['action']} {t} {o.get('name','')} "
                  f"— 요청 {abs(want)}주 중 {abs(delta)}주")
            unfilled += 1
        fills[t] = rec
    if unfilled:
        print(f"\n★ 미체결·부분체결 {unfilled}건 — **이 run은 '매수했다'가 아니라 "
              f"'주문을 걸어뒀다'로 보고해야 한다.**", file=sys.stderr)
    return unfilled, fills


if __name__ == "__main__":
    sys.exit(main())
