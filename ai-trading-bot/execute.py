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
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import (KisClient, KisError, snap_kr_price, kr_tick_size, market_session,
                        order_excd, quote_excd, buy_qty_within, account_mode, us_daytime_session,
                        DAYTIME_QUOTE_EXCD)
import risk_guard as rg
import db

HERE = Path(__file__).parent
JOURNAL_DIR = HERE / "journal"          # 그날 거래 기록(trades_*.json) — 파일로 남는 산출물(run 끝 db.import_run_artifacts가 옮긴다)
KST = timezone(timedelta(hours=9))


# 그날 기록 `runs[]`에 남기는 run 메타 — `approved_orders`(승인 주문 스냅샷)가 있어야
# 승인 대비 대조가 approved 파일을 다시 열지 않는다(risk_guard 재실행이 그 파일을 덮어쓴다).
RUN_KEYS = ("generated_at", "approved_ref", "approved_at", "approved_orders", "skipped",
            "signal_ref", "limits_sha256",
            # ★ 2026-10-19(Codex R01·R02·R03): 이 집행 호출 하나의 열쇠 · 보낸 승인 파일의 바이트 해시 · 승인 종류(main/refill/discipline)
            #   · 관문 통과 기록 · 계좌. run_key가 같은 runs[] 항목은 덧붙이지 않고 갱신한다(보내기 전 한 번 + 끝에 한 번).
            "run_key", "approved_sha256", "approved_kind", "gate", "svr", "account_mode", "snapshot_age_min",
            # 이 승인을 낸 심사 회차(W05) — 승인 파일이 덮여도 그날 기록에서 승인 행을 되살릴 때 결과 표의 열쇠가 따라온다
            "screen_pass")


def approved_snapshot(approved: dict) -> list:
    """대조용 승인 주문 스냅샷 — 종목·방향·source·수량·가격 + 승인 파일 안의 순번(idx · 주문 의도 키와 저장소 승인 연결에 쓴다)."""
    return [{"ticker": o.get("ticker"), "action": o.get("action"), "source": o.get("source"),
             "qty": int(o.get("qty") or 0), "price": o.get("price"), "idx": i}
            for i, o in enumerate(approved.get("orders") or [])]


def _stamp_from(path: Path) -> str:
    """approved 파일명 `approved_<YYMMDD>_<mkt>…json`의 세션일. 없으면 오늘."""
    m = re.search(r"_(\d{6})_(?:kr|us)", path.name)
    return m.group(1) if m else format(datetime.now(KST), "%y%m%d")


def refresh_thesis_unsynced(trades_path: Path) -> list:
    """그날 거래 기록의 `thesis_unsynced`를 **지금 원장 상태로** 다시 센다(theses.py set-status 뒤).

    2026-09-16 국내 run: 전송 시점엔 논지가 armed였고, held로 옮긴 뒤에도 파일의 `thesis_unsynced`가
    옛 값이라 `execute` 게이트가 막혔다. 원장을 옮기는 쪽이 이 값을 같이 갱신한다.
    """
    import fill
    if not trades_path.exists():
        return []
    with fill.day_lock(trades_path, "execute.refresh_thesis_unsynced"):
        try:
            rec = fill.read_day(trades_path, strict=True)
        except fill.DayRecordCorrupt:
            return []
        fills = {}
        for t in rec.get("trades") or []:
            if t.get("action") == "BUY" and fill.was_sent(t):
                fills[t["ticker"]] = t.get("fill") or {}
        before = rec.get("thesis_unsynced")
        rec["thesis_unsynced"] = _thesis_unsynced(fills)
        if before != rec["thesis_unsynced"]:
            rec.setdefault("thesis_resync", []).append(
                {"at": datetime.now(KST).isoformat(), "before": before, "after": rec["thesis_unsynced"]})
            db.write_json_atomic(trades_path, rec)
    return rec["thesis_unsynced"]


def _send_status(rc, out: Path, approved_sha: str) -> str:
    """전송 단계 상태 — 0이면 ok · 이 승인의 주문이 하나라도 나갔으면 reduced(나갔지만 미체결·거부 섞임) · 하나도 안 나갔으면 failed."""
    if rc == 0:
        return "ok"
    try:
        import fill
        rec = fill.read_day(out, strict=False) if out.exists() else {}
        if any(fill.was_sent(t) for t in (rec.get("trades") or []) if t.get("approved_sha256") == approved_sha):
            return "reduced"
    except Exception:                                # noqa: BLE001
        pass
    return "failed"


def _stage_try(fn):
    """단계 결과 기록(저장소) — 실패해도 전송·체결 경로를 막지 않는다(v4 · 주문 의도를 저장소에 먼저 쓰지 않는다는 결정과 같은 이유)."""
    try:
        return fn()
    except Exception as e:                           # noqa: BLE001
        print(f"  (단계 기록 실패: {type(e).__name__}: {str(e)[:80]})", file=sys.stderr)
        return None


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

    # ★ 실계좌 전환(2026-10-12): 승인이 만들어진 계좌 모드와 지금 모드가 다르면 보내지 않는다 — 모의 잔고로 잰 수량을
    #   실계좌로 보내거나 그 반대가 되는 길이다. 모드가 적히기 전의 승인(키 없음)은 모의에서만 받는다.
    ap_mode = approved.get("account_mode") or "paper"
    if ap_mode != account_mode(limits):
        raise rg.Rejection(f"승인의 계좌 모드 {ap_mode!r} ≠ 지금 {account_mode(limits)!r} — risk_guard를 지금 모드로 다시 돌려라")

    # ★ 승인 뒤 근거 값이 바뀌었으면 보내지 않는다(2026-10-19 · Codex R04) — 승인 행이 적은 사실 판(version = 그때 rev)보다
    #   값 판(value_rev)이 뒤면 risk_guard가 본 MATCH는 이제 세지 않는다. 표기만 바뀐 것(rev만 오름)은 통과한다.
    changed = []
    for o in approved.get("orders") or []:
        if str(o.get("action") or "").upper() != "BUY":
            continue
        for f in o.get("facts") or []:
            if not isinstance(f, dict) or not f.get("id") or f.get("version") is None:
                continue
            cur = db.q1("SELECT rev, value_rev FROM ev_fact WHERE id=?", (str(f["id"]),))
            if cur is None or int(cur.get("value_rev") or 1) > int(f["version"]):
                changed.append(f"{o.get('ticker')} {f['id']} 승인 rev {f['version']} < 값 rev "
                               f"{(cur or {}).get('value_rev', '없음')}")
    if changed:
        raise rg.Rejection("승인 뒤 근거가 고쳐졌다 — " + "; ".join(changed[:6]) + " · risk_guard를 다시 돌려라(재판정 필요)")


# ── 집행 관문(2026-10-19 · Codex R03) — 실행기가 스스로 "이 승인 파일이 전송 직전 점검을 통과했다"를 확인한다.
STEPGATE_PY = HERE.parent / ".claude" / "skills" / "_stepgate" / "stepgate.py"
SHORT_FLAGS = ("deficit_short", "factor_short", "undersized_short")
DISCIPLINE_SOURCES = ("discipline", "audit_reversal")


def gate_passes(cp: str) -> list:
    """게이트 엔진의 서명 통과 기록(`read_pass_records`) — 엔진에게 묻는다(경로·서명 규칙을 두 곳에 적지 않는다).
    엔진을 못 읽으면 예외 — 호출자가 '보내지 않음'으로 닫는다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_stepgate_engine", str(STEPGATE_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                      # `__main__` 가드가 있어 CLI는 안 돈다
    return mod.read_pass_records("trade-run", cp)


def approved_kind_of(approved: dict) -> str:
    """승인 종류 — main(본 승인) · refill(재투자 · 같은 run 본 승인의 관문 위) · discipline(규율 전용 · 관문 없이 매도만)."""
    if approved.get("discipline_only") is True:
        return "discipline"
    if approved.get("refill") is True:
        return "refill"
    return "main"


def gate_check(approved: dict, approved_sha: str, path: Path, stamp: str, market: str) -> dict:
    """★ 전송 직전 관문 확인 — 통과하면 runs[]에 남길 `gate` 기록, 아니면 `rg.Rejection`(주문 0건).

    - 미달 표시(`deficit_short`·`factor_short`·`undersized_short`) 중 하나라도 참이면 어떤 승인도 보내지 않는다
      (점검 문항이 이미 요구하지만 집행기도 스스로 막는다 — 점검을 건너뛴 전송의 마지막 그물).
    - **본 승인** — 지금 보내는 파일 **그 바이트**의 sha256이 적힌 dispatch PASS 기록이 있어야 한다. judge 뒤 risk_guard를 다시
      돌려 파일이 바뀌면 해시가 달라 보내지 않는다 — 그때는 judge를 다시 받는다.
    - **재투자 승인**(refill) — 그 run 본 승인(`approved_<st>_<mk>.json`)의 dispatch PASS가 있어야 한다(새 관문 없이 그 위의 추가 집행).
    - **규율 전용 승인**(장중 손절·미국 주간·규율 pass) — 관문 없이 보내되 **규율 매도만** 들어 있어야 한다
      (`SELL` + source ∈ discipline·audit_reversal · 시그널 심사 결과 없음). 매수나 모델 매도가 섞이면 거부.
    엔진을 못 읽으면 보내지 않는다(닫히는 쪽)."""
    bad = [k for k in SHORT_FLAGS if approved.get(k)]
    if bad:
        raise rg.Rejection(f"승인 파일에 미달 표시 {', '.join(bad)} = true — 이 승인은 보내지 않는다(4·5단으로 돌아가 채운 뒤 다시)")
    kind = approved_kind_of(approved)
    if kind == "discipline":
        orders = approved.get("orders") or []
        odd = [f"{o.get('action')} {o.get('ticker')}({o.get('source')})" for o in orders
               if str(o.get("action") or "").upper() != "SELL" or o.get("source") not in DISCIPLINE_SOURCES]
        if odd or approved.get("track1") is not None or approved.get("note_lint") is not None:
            raise rg.Rejection("규율 전용 승인인데 규율 매도가 아닌 주문·시그널 심사 결과가 섞였다 — "
                               + (", ".join(odd[:5]) or "track1/note_lint 있음") + " — 본 승인으로 다시 돌려 관문을 받아라")
        return {"cp": "discipline", "approved_sha256": approved_sha}
    try:
        recs = gate_passes("dispatch")
    except Exception as e:                                # noqa: BLE001 — 엔진을 못 읽으면 닫는다
        raise rg.Rejection(f"게이트 엔진 통과 기록을 읽지 못했다({type(e).__name__}: {str(e)[:120]}) — 보내지 않는다")
    if kind == "refill":
        need = f"approved_{stamp}_{market.lower()}.json"
        hit = [r for r in recs if Path(str(r.get("approved") or "")).name == need]
        if not hit:
            raise rg.Rejection(f"재투자 승인 — 그 run 본 승인({need})의 dispatch GATE PASS 기록이 없다 — 보내지 않는다")
        return {"cp": "dispatch", "ts": hit[-1].get("ts"), "approved_sha256": hit[-1].get("approved_sha256"), "via": need}
    hit = [r for r in recs if r.get("approved_sha256") == approved_sha]
    if not hit:
        named = [r for r in recs if Path(str(r.get("approved") or "")).name == path.name]
        why = (" — 같은 이름의 승인은 통과했지만 내용(해시)이 다르다: judge 뒤 승인 파일이 바뀌었다" if named else "")
        raise rg.Rejection(f"dispatch GATE PASS 기록이 이 승인 파일(sha {approved_sha[:12]})에 없다{why} — "
                           f"승인을 바꿨으면 dispatch를 form부터 다시 받아라(judge … approved=<이 파일 절대경로> · 같은 체크포인트 재판정은 순서 위반이 아니다)")
    return {"cp": "dispatch", "ts": hit[-1].get("ts"), "approved_sha256": approved_sha}


def _send_cfg() -> dict:
    """전송 직전 갱신의 한도 — `config/limits.json`의 `send_refresh_max_pct`(기본 2.0%)."""
    try:
        return json.loads((HERE / "config" / "limits.json").read_text(encoding="utf-8"))
    except Exception:                                   # noqa: BLE001
        return {}


def _live_quote(client: KisClient, order: dict) -> tuple:
    """전송 시점 시세 한 번(가격, 시각, 오류) — refresh_limit과 전제 재검사가 같은 값을 쓴다(2026-10-19 · Codex J04)."""
    market = order.get("market", "KR")
    try:
        if market == "KR":
            live = float(client.domestic_price(order["ticker"])["price"])
        else:
            excd_q = quote_excd(order.get("excd"))
            if order.get("session") == "daytime":
                excd_q = DAYTIME_QUOTE_EXCD.get(order_excd(order.get("excd")), "BAQ")
            q = client.overseas_price(order["ticker"], excd=excd_q)
            live = float(q["price"])
            if q.get("excd") and q.get("excd") != excd_q:
                order["_quote_excd_found"] = q["excd"]        # 다른 거래소에서 값이 왔다(빈 값 재조회) — 거래소 없는 주문이면 그 거래소로 보낸다
    except (KisError, KeyError, TypeError, ValueError) as e:
        return None, None, f"현재가 재조회 실패({type(e).__name__})"
    return (live if live > 0 else None), datetime.now(KST).isoformat(timespec="seconds"), ("" if live > 0 else "현재가 0")


def premise_check(order: dict, live: float, limits: dict) -> str:
    """★ 전송 직전 전제 재검사(2026-10-19 · Codex J04) — 모델 매수만(규율·자금 매도는 대상 아님 · R08). 심사 규칙의 재적용이지 새 상수가 아니다:
    ① 손절선(제안·논지의 `price <=` 최댓값) 이하면 위반 ② 진입 구간(제안 entry.basis·논지 진입 다리 basis의 '구간 lo~hi')이 있으면 그 밖이면 위반
    ③ 1차 목표(또는 목표 기준 수준)까지 거리가 심사 바닥(target_floor) 아래면 위반. 없는 항목은 건너뛴다.
    반환 위반 문장 | ''(검사한 항목이 전부 유지) | None(대상 아님 또는 검사할 항목이 하나도 없음 — '유지'라고 적지 않는다)."""
    if order.get("action") != "BUY" or order.get("source") != "llm" or not live:
        return None
    import theses as th
    p = order.get("proposal") if isinstance(order.get("proposal"), dict) else {}
    thesis = rg.thesis_by_id(order.get("thesis_id") or p.get("thesis_id"))
    fmt = ",.0f" if order.get("market", "KR") == "KR" else ",.2f"
    checked = False
    stop = rg.first_stop(p, thesis)
    if stop is not None:
        checked = True
        if live <= stop:
            return f"손절선 {stop:{fmt}} 아래(시세 {live:{fmt}})"
    bases = [str((p.get("entry") or {}).get("basis") or "")] if isinstance(p.get("entry"), dict) else []
    for leg in (thesis or {}).get("entry_triggers") or []:
        if leg.get("id") and leg.get("id") == (order.get("trigger_id") or p.get("trigger_id")):
            bases.append(str(leg.get("basis") or ""))
    for b in bases:
        m = th.ZONE_RE.search(b) if hasattr(th, "ZONE_RE") else None
        if m:
            checked = True
            lo, hi = (float(x.replace(",", "")) for x in m.groups()[:2])
            if not (lo <= live <= hi):
                return f"진입 구간 {lo:{fmt}}~{hi:{fmt}} 밖(시세 {live:{fmt}})"
            break
    tgt = rg.first_target(p, thesis)
    if tgt is None and isinstance(p.get("target_basis"), dict):
        try:
            tgt = float(p["target_basis"].get("level_price"))
        except (TypeError, ValueError):
            tgt = None
    floor = rg.target_floor(limits or {}, order.get("market", "KR"))
    if tgt is not None and floor > 0:
        checked = True
        if (tgt / live - 1) * 100 < floor:
            return f"목표 {tgt:{fmt}}까지 {(tgt / live - 1) * 100:+.1f}%(< 바닥 {floor:g}%) — 그 사이 올라 노릴 폭이 바닥 아래(시세 {live:{fmt}})"
    return "" if checked else None


def refresh_limit(client: KisClient, order: dict, limits: dict = None, live: float = None, quote_err: str = None) -> tuple:
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
    단 내림이 0주면 1주를 보낸다 — 아래 이탈 한도 안이라 초과는 한 주 값의 그 % 이내다(`buy_qty_within`).
    시세가 승인가에서 `send_refresh_max_pct`(기본 2%)보다 더 불리하게 달아났으면 **보내지 않는다** —
    그건 캡처 뒤에 오른 것을 쫓는 것이고, 다음 run이 새 가격으로 다시 판단한다.
    ★ 예외 — **위험 축소 매도**(손절·추적 손절·논지 무효·감사 소급 · `risk_reducing`)는 더 빠져도 보낸다(2026-10-19 사용자
      결정 · Codex R08). 이탈 한도는 비싸진 것을 쫓지 않으려는 매수 규칙인데, 손절은 가격이 빠질수록 더 급하다.
      지정가는 아래 그대로 시세(승인가와 불리한 쪽) − 공격 틱으로 다시 잡는다. 모델 매도·자금 조달·이익실현·매수는 그대로.
    """
    approved_px = float(order["price"])
    qty = int(order["qty"])
    approved_amt = approved_px * qty
    market = order.get("market", "KR")
    if live is None and quote_err:                    # 호출자가 이미 읽으려다 실패했다(W04) — 다시 읽지 않는다(같은 시세 하나로 검사·계산)
        return approved_px, qty, f"{quote_err} — 승인가 유지", None
    if live is None:                                  # 호출자가 이미 읽은 시세가 없으면 여기서 한 번(주간거래 매도는 주간 세션 시세)
        live, _at, err = _live_quote(client, order)
        if live is None and err.startswith("현재가 재조회 실패"):
            return approved_px, qty, f"{err} — 승인가 유지", None
        live = live or 0.0
    if live <= 0:
        return approved_px, qty, "현재가 0 — 승인가 유지", None
    side = order["action"]
    cfg = limits or _send_cfg()
    worse = live > approved_px if side == "BUY" else live < approved_px
    drift = (live - approved_px) / approved_px * 100
    max_pct = float(cfg.get("send_refresh_max_pct") or 2.0)
    risk_reducing = bool(order.get("risk_reducing")) if "risk_reducing" in order else rg.is_risk_reducing(order)
    if worse and abs(drift) > max_pct and not risk_reducing:
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
    new_qty, floor_one = qty, ""
    if side == "BUY":
        # 틱 분(px − px0)과 호가·센트 반올림 분(px0 − base)은 승인 금액 초과를 허용한다 — 체결 우선 장치가 한 주를 잃게
        # 하면 채우기 목적이 깨진다(반센트 승인가 1,791.005는 시세가 같아도 반올림 0.005 때문에 0주였다 — 2026-10-09).
        # 시세 드리프트(px0 − 승인가)는 수량으로 흡수하되(한도는 금액이다), 내림이 0주면 1주를 보낸다(`buy_qty_within`).
        allowed = approved_amt + (max(0.0, px - px0) + max(0.0, px0 - base)) * qty
        new_qty = buy_qty_within(qty, allowed, px)
        if new_qty <= 0:
            return px, 0, "", f"승인 금액 {approved_amt:,.0f} 안에서 {px:,.2f}로는 0주 — 전송 안 함"
        if new_qty * px > allowed + 1e-6:
            floor_one = f" · 1주 하한(승인 금액 {approved_amt:,.2f} 대비 {(new_qty * px / approved_amt - 1) * 100:+.2f}%)"
    why = (f"승인가 {approved_px:,.2f} → {px:,.2f} (집행 시점 시세 {live:,.2f}, 승인 대비 {drift:+.2f}% · "
           f"체결 우선 {ticks}틱)" + (f" · 수량 {qty}→{new_qty}(승인 금액 안)" if new_qty != qty else "") + floor_one
           + (f" · 위험 축소 매도라 이탈 한도 ±{max_pct}% 밖이어도 보낸다" if (worse and abs(drift) > max_pct) else ""))
    return px, new_qty, why, None


# SOR 주문이 거래소 구분 때문에 거부됐는지 — 응답 문구로 본다(조회만으로는 거부 코드를 미리 알 수 없다 · 2026-10-12).
EXCG_REJECT_HINTS = ("SOR", "NXT", "거래소", "EXCG", "대체거래")


def is_excg_reject(err: Exception) -> bool:
    return any(h in str(err) for h in EXCG_REJECT_HINTS)


def place(client: KisClient, order: dict) -> dict:
    """주문 1건 전송. 국내는 지정가, 미국은 지정가(모의는 지정가만 지원).

    ★ 실계좌 국내는 SOR로 보낸다(2026-10-12 사용자 결정). SOR이 거래소 구분 사유로 **거부**되면 같은 주문을 KRX로
    한 번 다시 보낸다 — 거부는 접수되지 않은 것이라 중복 주문이 아니다. 결과에 `fallback`을 남기고 보고 첫 줄에 적는다.
    미국 `session: daytime` 행은 주간거래 매도로 보낸다(손절·추적 전용 — kis_client가 매수를 거부한다).
    """
    market = order.get("market", "KR")
    if market == "KR":
        kw = dict(ticker=order["ticker"], side=order["action"], qty=order["qty"], price=int(order["price"]))
        try:
            return client.domestic_order(**kw)
        except KisError as e:
            # KRX 재전송은 **분명한 거절**일 때만 — 응답 유실(unknown)은 SOR이 받았을 수 있어 다시 보내면 이중 주문이다(R01).
            if client.kr_excg == "KRX" or getattr(e, "kind", "unknown") != "rejected" or not is_excg_reject(e):
                raise
            res = client.domestic_order(**kw, excg="KRX")
            res["fallback"] = {"from": client.kr_excg, "to": "KRX", "error": str(e)[:200]}
            print(f"  ↪ {order['ticker']} SOR 거부 → KRX 재전송 ({str(e)[:80]})")
            return res
    # 시세용 코드(NYS)가 실려 오면 주문용(NYSE)으로 바꾼다 — 2026-09-15 1차 전송이 여기서 막혔다.
    excd = order_excd(order.get("excd") or "NASD")
    if order.get("session") == "daytime":
        return client.overseas_daytime_order(ticker=order["ticker"], side=order["action"], qty=order["qty"],
                                             limit_price=float(order["price"]), excd=excd)
    return client.overseas_order(
        ticker=order["ticker"], side=order["action"],
        qty=order["qty"], limit_price=float(order["price"]), excd=excd,
    )


def cap_check(order: dict, px: float, qty: int, cap: dict) -> tuple:
    """★ 전송 직전 주문당 상한 재검사(2026-10-12). 반환 (보낼 수량, 메모, 거부 사유).

    - 승인 수량 자체가 승인가 기준 상한을 넘으면 **우회된 승인**이다 → 보내지 않는다(거부 · 사고 기록 대상).
    - 승인은 상한 안이었는데 전송 직전 시세 상승으로 넘었으면 상한 안으로 수량을 내린다(1주 미만이면 보내지 않는다).
    매도는 검사하지 않는다(위험을 줄이는 주문).
    """
    if not cap or order.get("action") != "BUY":
        return qty, "", ""
    mult = 1.0 if order.get("market", "KR") == "KR" else float(cap.get("fx") or 0)
    ap_q, _ = rg.order_cap_qty(float(order["price"]), mult, cap)
    if ap_q is not None and int(order["qty"]) > ap_q:
        return 0, "", (f"주문당 상한 우회 — 승인 {order['qty']}주가 승인가 기준 상한 {ap_q}주를 넘는다"
                       f"(상한 {cap.get('max_krw', 0):,.0f}원 · 1주 초과 종목 {cap.get('max_shares')}주) — 전송 안 함")
    now_q, why = rg.order_cap_qty(px, mult, cap)
    if now_q is None or qty <= now_q:
        return qty, "", ""
    if now_q <= 0:
        return 0, "", f"{why} — 전송 직전 시세로는 1주도 상한 안에 안 든다 — 전송 안 함"
    return now_q, f"{why} — 전송 직전 시세 상승으로 {qty}→{now_q}주", ""


def verify(client: KisClient, market: str) -> dict:
    """쓰기 후 검증 — 주문 뒤 잔고를 다시 읽어 상태를 확인한다."""
    try:
        return client.domestic_balance() if market == "KR" else client.overseas_balance()
    except KisError as e:
        return {"error": str(e)}


def _read_day(path: Path) -> dict:
    """그날 거래 기록 — **화면 표시용**(없거나 깨졌으면 {}). 중복 검사·병합은 `fill.read_day(strict=True)`를 쓴다
    (깨진 기록을 빈 것으로 읽으면 이미 나간 주문이 두 번 나간다 · 2026-10-19 · R02)."""
    import fill
    return fill.read_day(path, strict=False)


def upsert_row(path: Path, row: dict) -> None:
    """그날 거래 기록에 주문 행 하나를 **바로** 넣거나(같은 `intent_key`면) 바꾼다 — 원자적 쓰기(2026-10-19 · R02).
    `_send_orders`가 보내기 **전**(PENDING)과 결과 직후 두 번 부른다. 호출자가 `fill.day_lock`을 쥐고 있어야 한다."""
    import fill
    rec = fill.read_day(path, strict=True)
    trades = list(rec.get("trades") or [])
    k = row.get("intent_key")
    for i, t in enumerate(trades):
        if k and t.get("intent_key") == k:
            trades[i] = row
            break
    else:
        trades.append(row)
    rec["trades"] = trades
    db.write_json_atomic(path, rec)


def _snapshot_age_min(approved: dict):
    """승인이 기댄 스냅샷의 나이(분) — 재기만 하고 막지 않는다(전제 재검사가 숫자를 다시 본다 · J04). 못 재면 None."""
    try:
        sj = json.loads(Path(str(approved.get("snapshot_ref") or "")).read_text(encoding="utf-8"))
        g = datetime.fromisoformat(str(sj.get("generated_at")))
        return round((datetime.now(KST) - (g if g.tzinfo else g.replace(tzinfo=KST))).total_seconds() / 60, 1)
    except (OSError, ValueError, TypeError):
        return None


class _PremiseBroken(Exception):
    """전송 직전 전제 재검사 위반(J04) — UNSENT로 적는다."""


def intent_key_of(approved_sha: str, approved_name: str, idx, attempt) -> str:
    """주문 의도 키 — 승인 파일 바이트 해시(없으면 파일명) 앞 16자 : 승인 안 순번 : 시도 번호. 저장소 ex_order에서도 유일하다."""
    base = (approved_sha or hashlib.sha256(str(approved_name or "").encode("utf-8")).hexdigest())[:16]
    return f"{base}:{idx}:{int(attempt or 1)}"


def adjust_sell_dependent(buys: list, presell: list, day: dict, approved_name: str) -> tuple:
    """★ FB-261003-10 — 매도 대금에 기대는 매수를 **실제 체결 대금**으로 다시 맞춘다. 반환 (보낼 매수, 건너뛴 것).

    `risk_guard`는 같은 승인의 매도 대금을 예산에 넣고 그 돈을 쓰는 매수에 `needs_sell_fill`·`sell_dependent_amt`를 붙인다.
    매도가 덜 체결되면 그만큼 매수를 줄인다 — 미체결 대금은 쓰지 않는다(이중 지출 방지)."""
    expected = sum(int(o["qty"]) * float(o["price"]) for o in presell)
    tk = {o["ticker"] for o in presell}
    got = 0.0
    for t in (day.get("trades") or []):
        if t.get("ticker") in tk and t.get("action") == "SELL" and t.get("approved_ref") == approved_name:
            f = t.get("fill") or {}
            if f.get("verdict") in ("FILLED", "PARTIAL"):
                got += float(f.get("filled_qty") or t.get("qty") or 0) * float(f.get("avg_price") or t.get("price") or 0)
    ratio = min(1.0, got / expected) if expected > 0 else 0.0
    out, skip = [], []
    for b in buys:
        dep = float(b.get("sell_dependent_amt") or 0)
        if not b.get("needs_sell_fill") or dep <= 0 or ratio >= 0.999:
            out.append(b)
            continue
        amt = int(b["qty"]) * float(b["price"]) - dep * (1 - ratio)
        q = int(amt // float(b["price"])) if float(b["price"]) > 0 else 0
        if q <= 0:
            skip.append({k: b.get(k) for k in ("ticker", "action", "source", "qty")}
                        | {"why": f"매도 대금 미체결 — 의존 매도 체결률 {ratio:.0%}라 살 돈이 없다(다음 run이 다시 낸다)"})
            continue
        out.append({**b, "qty": q, "reasons": list(b.get("reasons") or [])
                    + [f"매도 체결률 {ratio:.0%} — 의존분만큼 {b['qty']}→{q}주로 줄였다"]})
    return out, skip


def dedupe_against_day(orders: list, prior_day: dict, approved_name: str) -> tuple:
    """★ 같은 승인은 한 번만 나간다 — (보낼 것, 건너뛴 것[중복], 보류[열린 주문·접수 불명]).

    왜: 2026-09-10(042660 4주 두 번)·2026-09-21(두산 46주 두 번) 모두 **같은 approved로
    `execute.py --send`를 다시 돌려서** 났다. 승인은 파일이고 파일은 다시 돌릴 수 있으므로,
    전송이 멱등이어야 한다 — "다시 돌리지 마라"는 규칙은 두 번 깨졌다.

    - 중복: 그날 기록에 **같은 승인 파일명**(옛 행처럼 `approved_ref`가 없으면 같은 것으로 본다) +
      같은 (종목, 방향, source)이고 **접수됐을 수 있는** 행(SENT·UNKNOWN·PENDING — `fill.was_sent`)이 있으면
      **판정과 무관하게** 건너뛴다. 열린 건은 `fill.py`의 몫이고 종결 건은 끝난 것이며 접수 뒤 거부도 재전송하지 않는다 —
      정당한 재발행은 파일명을 바꾼다(`_v2`·`_reissue`, 이미 그렇게 쓰고 있다).
    - 재시도: 같은 승인의 같은 건이 **접수 0**(REJECTED·UNSENT·옛 FAILED)뿐이면 다시 보낸다 — 거절·미전송은 걸린 주문이
      없어 중복이 될 수 없다(2026-10-19 · R01). 시도 번호(`_attempt`)를 올려 의도 키가 겹치지 않게 한다.
    - 보류: 승인 파일과 무관하게 같은 (종목, 방향)의 **열린** 행이나 **접수 불명**(UNKNOWN·PENDING) 행이 있으면 보류한다 —
      `stops.py`가 새 파일명(`_stops_HHMM`)으로 같은 매도를 다시 내는 구멍 · 응답을 잃은 주문 위에 같은 주문을 얹는 구멍.
    """
    import fill
    rows = [t for t in (prior_day.get("trades") or []) if t.get("status") is not None]
    to_send, dup, held = [], [], []
    for o in orders:
        key = (o.get("ticker"), o.get("action"), o.get("source"))
        same = [t for t in rows
                if (t.get("ticker"), t.get("action"), t.get("source")) == key
                and Path(t.get("approved_ref") or approved_name).name == approved_name]
        sent_same = [t for t in same if fill.was_sent(t)]
        if sent_same:
            t = sent_same[-1]
            dup.append({**o, "_why": f"오늘 기록에 같은 승인({approved_name})의 같은 건이 있다 — 전송 "
                                     f"{str(t.get('ts', ''))[11:19]} · {t.get('status')}/"
                                     f"{(t.get('fill') or {}).get('verdict') or '미확정'}"})
            continue
        if same:                                    # 전부 접수 0 — 다시 보낸다(시도 번호를 올린다)
            o = {**o, "_attempt": len(same) + 1}
        opened = [t for t in rows if (t.get("ticker"), t.get("action")) == key[:2]
                  and (fill.is_open_trade(t) or t.get("status") in fill.UNRESOLVED)]
        if opened:
            t = opened[-1]
            what = ("접수 불명(응답 유실) 주문" if t.get("status") in fill.UNRESOLVED else "열린 주문")
            held.append({**o, "_why": f"같은 종목·방향의 {what}이 있다(승인 "
                                      f"{Path(t.get('approved_ref') or '?').name} · 주문번호 "
                                      f"{(t.get('result') or {}).get('order_no') or '?'} · {t.get('status')})"})
            continue
        to_send.append(o)
    return to_send, dup, held


def _overfill(path: Path) -> dict:
    """그날 기록의 승인 대비 대조 결과(`fill.finalize`가 쓴다). 없으면 {}."""
    return (_read_day(path).get("reconcile") or {}) if path.exists() else {}


def merge_day_record(path: Path, record: dict) -> dict:
    """그날 거래 기록에 이번 run을 **덧붙인다**. 덮어쓰지 않는다.

    왜 중요한가: 이 파일은 감사 기록이면서 동시에 안전장치의 입력이다(중복 전송 검사·승인 대조·지출 합).
    2026-09-08 매도 테스트에서 아침 매수 기록이 실제로 사라지며 발견됐다.

    앞선 run들의 메타데이터는 `runs`에 쌓고, 최상위에는 항상 최신 run을 둔다 —
    `balance_after`는 가장 마지막 상태여야 의미가 있기 때문이다.
    ★ 2026-10-19(R01·R02): 주문 행은 보내기 전·직후에 `upsert_row`가 이미 적는다 — 같은 `intent_key` 행은 **바꾸고**
      (덧붙이면 같은 주문이 두 줄이 된다), 같은 `run_key`의 runs[] 항목도 바꾼다. 기록을 못 읽으면 **멈춘다**
      (`fill.DayRecordCorrupt`) — 옛 판은 옆으로 치우고 새로 썼는데, 그러면 중복 검사가 빈 기록을 보고 다시 보낸다.
      쓰기는 원자적이다(`db.write_json_atomic`).
    """
    import fill
    prior = fill.read_day(path, strict=True)

    if isinstance(prior, dict) and prior.get("trades") is not None:
        merged = dict(record)
        trades = list(prior.get("trades") or [])
        pos = {t.get("intent_key"): i for i, t in enumerate(trades) if t.get("intent_key")}
        for t in record.get("trades") or []:
            k = t.get("intent_key")
            if k and k in pos:
                trades[pos[k]] = t
            else:
                trades.append(t)
        merged["trades"] = trades
        runs = list(prior.get("runs") or [])
        if not runs:      # 이 형식 이전에 쓰인 파일 — 앞선 run을 하나로 복원해 둔다
            runs.append({k: prior.get(k) for k in RUN_KEYS if k in prior})
        entry = {k: record.get(k) for k in RUN_KEYS if k in record}
        rk = record.get("run_key")
        at = next((i for i, r in enumerate(runs) if rk and r.get("run_key") == rk), None)
        if at is None:
            runs.append(entry)
        else:
            runs[at] = {**runs[at], **entry}
        merged["runs"] = runs
    else:
        merged = dict(record)
        merged["runs"] = [{k: record.get(k) for k in RUN_KEYS if k in record}]

    # ★ 승인 대비 대조 — 종목·방향별 집행 합이 승인 합을 넘으면 사고로 기록한다(`fill.finalize`).
    #   여기서 하는 이유: `--no-wait`·회전 매도 선기록처럼 fill.confirm을 안 거치는 쓰기도 잡아야 한다.
    #   대조가 실패해도 기록은 쓴다 — 이미 나간 주문의 유일한 증거를 대조 버그가 막으면 안 된다.
    try:
        merged = fill.finalize(merged, path, datetime.now(KST))
    except Exception as e:                              # noqa: BLE001
        print(f"★ 승인 대조 실패({type(e).__name__}: {e}) — 기록은 그대로 쓴다", file=sys.stderr)

    db.write_json_atomic(path, merged)
    return merged


def _send_orders(client: KisClient, orders: list, market: str,
                 approved_name: str = "", approved_at: str = "", cap: dict = None, *,
                 journal: Path = None, approved_sha: str = "", qty_before: dict = None, run_key: str = "") -> tuple:
    """주문 목록을 전송하고 trades 행을 만든다. 반환 (trades, failures).

    행마다 `approved_ref`(승인 파일명)·`approved_at`을 적는다 — 멱등 전송(`dedupe_against_day`)과
    승인 대비 대조(`fill.reconcile`)가 이 두 값으로 "어느 승인의 건인가"를 안다.

    ★ 주문 상태(2026-10-19 · Codex R01·R02) — `journal`(그날 거래 기록 경로)을 주면 주문마다 **보내기 전에** PENDING 행을
      원자적으로 적고, 결과가 오면 그 행을 바로 바꾼다. 도중에 죽어도 '보냈을 수 있는 주문'이 파일에 남고, 다음 실행은
      그 행을 다시 보내지 않고 당일 주문 목록으로 푼다(`fill.resolve_pending`). 결과 상태:
      SENT(접수) · UNKNOWN(응답 유실 — 접수됐을 수 있다) · REJECTED(증권사 거절 — 접수 0) · UNSENT(우리가 안 보냄 — 접수 0).
      `journal`이 없으면(단위 테스트) 메모리에서만 만든다.
    """
    trades = []
    failures = 0
    limits = _send_cfg()
    for n, o in enumerate(orders):
        idx = o.get("_idx", n)
        attempt = int(o.get("_attempt") or 1)
        rr = bool(o.get("risk_reducing")) if "risk_reducing" in o else rg.is_risk_reducing(o)
        row = {
            "ts": datetime.now(KST).isoformat(), "status": "PENDING", "market": market,
            "approved_ref": approved_name, "approved_at": approved_at,
            "approved_sha256": approved_sha or None, "approved_idx": idx, "attempt": attempt,
            "intent_key": intent_key_of(approved_sha, approved_name, idx, attempt), "run_key": run_key or None,
            "ticker": o["ticker"], "name": o.get("name", ""), "action": o["action"],
            "qty": o["qty"], "price": o["price"], "source": o.get("source"),
            # 승인 수량·가격(W02) — qty·price는 전송 직전 갱신·상한 뒤 **실제로 보낸 요청**으로 바뀐다(request 칸과 같다)
            "approved_qty": o["qty"], "approved_price": o["price"],
            "full_exit": bool(o.get("full_exit")), "thesis_id": o.get("thesis_id"),
            # ★ 어느 논지의 **어느 다리**가 집행됐는지 — fill이 그 다리를 소진 표시하는 데 쓴다(2026-09-23).
            #   예전에는 risk_guard가 쓴 trigger_id가 여기서 떨어져, 체결 뒤에도 다리가 살아 매 run 재발화했다.
            "trigger_id": o.get("trigger_id"),
            "session": o.get("session") or "regular",
            "risk_reducing": rr,
            "discipline_reasons": o.get("reasons", []),
            # 근거 스냅샷 — 이 파일 하나로 "왜 샀는지"가 재구성돼야 한다.
            "proposal": o.get("proposal"),
            # 근거 사실(2026-10-17) — 승인 행의 facts(사실 id·역할·받치는 것 + 승인 시점 검증 상태·판정·사실 판 번호)를 그대로 옮긴다.
            #   규율·소급 매도처럼 제안이 없는 행은 None.
            "facts": o.get("facts"),
            "result": {},
            # 전송 **전** 보유 수량 — 모든 행에 둔다(접수 불명 해소·잔고 대조가 이것으로 판정한다 · 2026-09-21 두산 46주).
            "fill": ({"qty_before": int(qty_before.get(o["ticker"], 0))} if qty_before is not None else {}),
        }
        if journal is not None:
            upsert_row(journal, row)                 # ← 보내기 전에 적는다(죽어도 '보냈을 수 있음'이 남는다)
        # ── 전송 전(아무것도 안 보냄): 이탈·상한·수량 0·검증 오류·전제 이탈은 UNSENT
        try:
            live_q, q_err = None, None
            if o.get("action") == "BUY" and o.get("source") == "llm":
                # ★ 전제 재검사(J04) — 전송 시점 시세로 손절선·진입 구간·목표 여유를 다시 본다 · 전송 시점 시세는 행과 mk_quote에 남긴다.
                #   실패하면 한 번 더 읽고, 그렇게 얻은 **시세 하나**로 전제 검사와 지정가·수량 계산을 함께 한다(W04 — 예전엔 재조회가
                #   refresh_limit 안에서 일어나 전제 검사를 건너뛰었다). 끝내 못 읽으면 '전제 미검사'로 기록하고 승인가로 보낸다(stage6 명세).
                live_q, q_at, q_err = _live_quote(client, o)
                first_err, tries = None, 1
                if not live_q:
                    first_err = q_err
                    live_q, q_at, q_err = _live_quote(client, o)
                    tries = 2
                row["quote_at_send"] = {"price": live_q, "at": q_at, "source": "send", "excd": o.get("excd"),
                                        "error": (q_err or None) if not live_q else None, "tries": tries,
                                        **({"first_error": first_err} if first_err else {})}
                if live_q:
                    try:
                        db.quote_put(market, o["ticker"], live_q, ts=q_at, source="send", stamp=_stamp_from(Path(approved_name or "x")))
                    except Exception:                # noqa: BLE001 — 시세 기록 실패가 전송을 막지 않는다
                        pass
                    viol = premise_check(o, live_q, limits)
                    row["premise"] = viol if viol else ("전제 검사 대상 없음" if viol is None else "전제 유지")
                    if viol:
                        raise _PremiseBroken(f"전제 이탈 — {viol}")
                else:
                    row["premise"] = "시세 조회 실패 — 전제 미검사"
            px, qty, why, skip = refresh_limit(client, o, limits, live=live_q,
                                               quote_err=(q_err or "시세 조회 실패") if (o.get("action") == "BUY" and o.get("source") == "llm"
                                                                                       and not live_q) else None)
            if not skip:
                if why:
                    print(f"  · {o['ticker']} {why}")
                    o = {**o, "price": px, "qty": qty}
                cq, cnote, cbad = cap_check(o, float(o["price"]), int(o["qty"]), cap)
                if cbad:
                    skip = cbad
                elif cnote:
                    print(f"  · {o['ticker']} {cnote}")
                    o = {**o, "qty": cq}
        except _PremiseBroken as e:                  # 전제 이탈 — 보내지 않고 다음 run이 다시 판단한다(접수 0)
            skip = str(e)
        except Exception as e:                       # noqa: BLE001 — 아직 아무것도 안 보냈다(접수 0)
            skip = f"전송 전 점검 실패({type(e).__name__}: {str(e)[:120]})"
        row.update({"qty": o["qty"], "price": o["price"]})
        if skip:
            # 보내지 않은 건 — 브로커에 걸린 주문이 없다. UNSENT(client-side)로 적어 fill이 기다리지 않게 한다.
            print(f"  ⊘ 미전송 {o['action']} {o['ticker']}: {skip}", file=sys.stderr)
            row.update({"ts": datetime.now(KST).isoformat(), "status": "UNSENT",
                        "result": {"error": skip, "client_side": True, "kind": "unsent"},
                        "fill": {**row["fill"], "verdict": "REJECTED", "note": f"client-side: {skip}",
                                 "requested": o["qty"]}})
            failures += 1
            if journal is not None:
                upsert_row(journal, row)
            trades.append(row)
            continue
        # 거래소 코드 없이 온 미국 주문인데 시세가 다른 거래소에서 왔다 — 그 거래소(주문용 코드)로 보낸다(틀린 거래소로 보내면 거부된다)
        found = o.get("_quote_excd_found") or (orders[n].get("_quote_excd_found") if isinstance(orders[n], dict) else None)
        if market != "KR" and not o.get("excd") and found and o.get("session") != "daytime":
            o = {**o, "excd": order_excd(found)}
            row["excd"] = o["excd"]
            row["excd_from_quote"] = found
        # ★ 실제로 보낼 요청을 보내기 **전에** 디스크에 쓴다(W02) — 갱신·상한으로 수량·가격이 바뀌었으면 PENDING 행이 승인 수량을
        #   들고 있어, 접수 직후 죽으면 당일 목록 대사(수량 일치)가 접수된 주문을 못 찾았다. 승인 값은 approved_qty·approved_price에.
        row["request"] = {"qty": int(o["qty"]), "price": (int(o["price"]) if market == "KR" else float(o["price"])),
                          "at": datetime.now(KST).isoformat()}
        if journal is not None:
            upsert_row(journal, row)
        # ── 전송: 결과를 접수 가능성으로 가른다(kis_client.KisError.kind)
        # ★ 2026-10-13 복구 — 10-12 20:56 주문 상한(cap_check) 추가 때 이 전송 줄이 빠져 `res` 미정의로
        #   모든 전송이 브로커 호출 전에 죽었다(UnboundLocalError · 주문 0건). 이 줄은 반드시 남는다.
        try:
            res = place(client, o)
            if res.get("order_no"):
                print(f"  OK {o['action']} {o['ticker']} → 주문번호 {res.get('order_no')}")
                status = "SENT"
            else:
                # 브로커가 성공(rt_cd=0)을 줬는데 주문번호를 못 읽었다 — 주문이 나갔을 수 있다(UNKNOWN · raw 보존).
                print(f"  UNKNOWN {o['action']} {o['ticker']}: 응답은 성공인데 주문번호가 "
                      f"없다 — 다시 보내지 않고 당일 주문 목록으로 확인한다. raw={res.get('raw')}",
                      file=sys.stderr)
                status, failures = "UNKNOWN", failures + 1
        except KisError as e:
            kind = getattr(e, "kind", "unknown")
            status = {"rejected": "REJECTED", "unsent": "UNSENT"}.get(kind, "UNKNOWN")
            res = {"error": str(e), "kind": kind}
            failures += 1
            if status == "UNKNOWN":
                print(f"  UNKNOWN {o['action']} {o['ticker']}: 응답 유실 — {str(e)[:160]} — 접수됐을 수 있어 다시 보내지 "
                      f"않는다(당일 주문 목록으로 확인)", file=sys.stderr)
            else:
                print(f"  FAIL {status} {o['action']} {o['ticker']}: {e}", file=sys.stderr)
        except ValueError as e:                      # 주문 함수의 입력 검증(호가 단위·수량) — 요청 전이다(접수 0)
            status, res = "UNSENT", {"error": str(e), "kind": "unsent", "client_side": True}
            failures += 1
            print(f"  FAIL UNSENT {o['action']} {o['ticker']}: {e}", file=sys.stderr)
        except Exception as e:                       # noqa: BLE001 — 보낸 뒤 무엇이 났는지 모른다: 접수됐을 수 있다
            status, res = "UNKNOWN", {"error": f"{type(e).__name__}: {str(e)[:200]}", "kind": "unknown"}
            failures += 1
            print(f"  UNKNOWN {o['action']} {o['ticker']}: 전송 중 예외 {type(e).__name__} — 접수됐을 수 있어 다시 "
                  f"보내지 않는다", file=sys.stderr)
        row.update({"ts": datetime.now(KST).isoformat(), "status": status, "result": res})
        if status in ("REJECTED", "UNSENT"):
            row["fill"] = {**row["fill"], "verdict": "REJECTED", "requested": o["qty"],
                           "note": ("client-side 미전송 — 증권사에 보낸 것이 없다" if status == "UNSENT"
                                    else "브로커가 주문을 거부했다 — 접수된 것이 없다. 대기 중인 주문이 아니므로 기다리지 말 것")}
        if journal is not None:
            upsert_row(journal, row)                 # ← 결과 직후 바로 적는다
        trades.append(row)

    return trades, failures


def main() -> int:
    ap = argparse.ArgumentParser(description="approved 주문을 집행한다 (기본 dry-run)")
    ap.add_argument("approved", help="signals/approved_YYMMDD_kr.json")
    ap.add_argument("--send", action="store_true",
                    help="실제로 주문을 전송한다. 없으면 계획만 출력한다.")
    ap.add_argument("--real", action="store_true",
                    help="실전 서버. 모의 6개월 검증 전에는 쓰지 않는다.")
    ap.add_argument("--stamp", metavar="YYMMDD",
                    help="이 run의 스탬프(거래하는 세션의 현지 날짜 · 2026-10-14). 없으면 approved 파일 이름의 "
                         "스탬프 — 자정을 넘긴 미국 run도 같은 스탬프로 기록된다.")
    ap.add_argument("--note", help="분석노트 — 체결 확정 줄을 §11에 쓴다")
    ap.add_argument("--no-wait", action="store_true",
                    help="전송만 하고 체결 확정(fill)을 건너뛴다. 기본은 확정까지 기다린다.")
    ap.add_argument("--max-sec", type=int, default=540, help="체결 확정에 쓸 이 호출의 시간 예산")
    ap.add_argument("--force-hours", action="store_true",
                    help="장외에도 전송한다(기본은 거부 — 브로커가 장종료로 거부하고 run만 낭비된다)")
    args = ap.parse_args()

    path = Path(args.approved)
    # ★ 승인 파일은 **한 번** 바이트로 읽어 해시와 내용을 같은 바이트에서 만든다(2026-10-19 · R03·R10) — 읽는 사이 파일이
    #   바뀌어도 '해시를 본 파일'과 '보낸 파일'이 갈리지 않는다. 해시는 관문 통과 기록·주문 의도 키·저장소 승인 연결에 쓴다.
    try:
        ap_bytes = path.read_bytes()
        approved = json.loads(ap_bytes.decode("utf-8"))
    except (OSError, ValueError) as e:
        raise rg.Rejection(f"approved 파일을 읽을 수 없다: {path} ({type(e).__name__}: {e})")
    approved_sha = hashlib.sha256(ap_bytes).hexdigest()
    limits = rg._read_json(rg.LIMITS_PATH, "limits")
    mode = account_mode(limits)
    # ★ 계좌는 `account_mode` 하나가 정한다(2026-10-12). `--real`은 옛 호출과의 대조용 — 모드와 어긋나면 거부한다.
    if args.real and mode != "real":
        print(f"집행 거부 — --real인데 account_mode가 {mode!r}다. 계좌는 config/limits.json의 account_mode가 정한다.",
              file=sys.stderr)
        return 2

    try:
        preflight(approved, path, limits)
    except rg.Rejection as e:
        print(f"집행 거부 — 주문 0건: {e}", file=sys.stderr)
        return 2

    # 승인 안 순번(_idx)을 붙인다 — 중복 검사·수량 조정 사본이 다 `{**o, …}`라 그대로 따라간다(주문 의도 키 · 승인 연결).
    orders = [{**o, "_idx": i} for i, o in enumerate(approved.get("orders", []))]
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
    #   기록이 깨졌으면 멈춘다 — 빈 기록으로 읽고 보내면 이미 나간 주문이 두 번 나간다(2026-10-19 · R02).
    import fill
    try:
        prior_day = fill.read_day(out, strict=True)
    except fill.DayRecordCorrupt as e:
        print(f"집행 거부 — 주문 0건: {e}", file=sys.stderr)
        return 2
    # ★ 계좌 계열 대조(2026-10-19 · Codex R09) — run은 시작 때 정한 계좌 계열로만 집행한다. 그날 기록이 다른 계열의 것이거나
    #   run 행이 다른 계열로 시작했으면 보내지 않는다(모의 기록 위에서 실계좌 주문의 중복 검사가 돌거나 그 반대).
    fam = db.acct_family_of(mode)
    prior_modes = [r.get("account_mode") or r.get("svr") for r in (prior_day.get("runs") or []) if isinstance(r, dict)]
    if prior_day.get("svr"):
        prior_modes.append(prior_day.get("svr"))
    other = [m for m in prior_modes if m and db.acct_family_of(m) != fam]
    if other:
        print(f"집행 거부 — 주문 0건: 오늘 기록 {out.name}은 {other[-1]} 계좌 것 — 같은 날 다른 계좌({mode})로 집행하지 않는다",
              file=sys.stderr)
        return 2
    try:
        run_row = db.q1("SELECT account_mode FROM ex_run WHERE run_id=?", (db.run_id(stamp, market.lower()),)) or {}
    except Exception:                                # noqa: BLE001 — 저장소를 못 읽으면 대조만 건너뛴다(전송을 막지 않는다)
        run_row = {}
    if run_row.get("account_mode") and db.acct_family_of(run_row["account_mode"]) != fam:
        print(f"집행 거부 — 주문 0건: run {db.run_id(stamp, market.lower())}는 {run_row['account_mode']} 계좌로 시작했다 — "
              f"지금 {mode}로 보내지 않는다(계좌 전환은 날이 바뀐 뒤)", file=sys.stderr)
        return 2
    orders_all = list(orders)
    orders, dup, held = dedupe_against_day(orders, prior_day, path.name)
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
    if mode == "real_readonly":
        print("집행 거부 — account_mode=real_readonly(조회 전용 주간): 실계좌는 읽기만 한다. 주문은 dry-run까지다.",
              file=sys.stderr)
        return 2
    # ★ 관문 확인(R03) — 이 승인 파일 바이트가 dispatch를 통과했는가 · 미달 표시 · 규율 전용은 매도만.
    try:
        gate = gate_check(approved, approved_sha, path, stamp, market)
    except rg.Rejection as e:
        print(f"집행 거부 — 주문 0건: {e}", file=sys.stderr)
        return 2

    # ★ 장외 전송 차단 — 2026-09-10 MSFT를 11:31 KST에 보내 `[40580000] 모의투자 장종료`로
    #   거부됐다. 브로커 거부는 무해하지만 그 run의 판단이 통째로 증발한다. 시간을 여기서 본다.
    daytime = market == "US" and bool(orders) and all(o.get("session") == "daytime" for o in orders)
    sess = us_daytime_session() if daytime else market_session(market)
    if not sess["is_open"] and not args.force_hours:
        print(f"집행 거부 — 장외: {sess['why']} (지금 {datetime.now(KST):%H:%M} KST). "
              f"정규장에 다시 돌리거나 --force-hours.", file=sys.stderr)
        return 2
    # 마감 임박 안내(거부 아님) — 국내 15:20 이후 주문은 동시호가로 15:30에 체결된다(2026-09-22 KR 15:20 전송 → 15:30 체결).
    left_min = (sess["close"] - datetime.now(KST)).total_seconds() / 60
    if 0 <= left_min <= 10:
        print(f"  · 마감 {left_min:.0f}분 전 전송 — {'동시호가(15:30 체결)' if market == 'KR' else '마감 직전'}; 정정·취소 시간이 거의 없다")

    # ★ 그날 거래 기록에 한 번에 한 쓰기만(2026-10-19 · R02) — 잠금을 쥔 뒤 기록을 **다시** 읽어 중복 검사를 새로 한다
    #   (잠금 전 검사와 전송 사이에 다른 프로세스가 같은 주문을 보냈을 수 있다).
    # 단계 결과(v4 · U02) — 전송 단계 행(시작 = 첫 전송 · ex_run.sent_at). 저장소 쓰기 실패가 전송을 막지 않는다(기록만 빠진다).
    sid = _stage_try(lambda: db.stage_start(stamp, market.lower(), "execute"))
    rc_send = None
    try:
        with fill.day_lock(out, f"execute --send {path.name}"):
            rc_send = _send_locked(args, path, approved, approved_sha, limits, market, out, approved_at, orders_all, gate)
            return rc_send
    except fill.DayLocked as e:
        print(f"집행 보류 — 주문 0건: {e}", file=sys.stderr)
        return 5
    except fill.DayRecordCorrupt as e:
        print(f"집행 중단 — {e}", file=sys.stderr)
        return 2
    finally:
        if sid:
            _stage_try(lambda: db.stage_finish(sid, _send_status(rc_send, out, approved_sha), rc_send, (),
                                               {"trades": out} if out.exists() else None))


def _send_locked(args, path: Path, approved: dict, approved_sha: str, limits: dict, market: str, out: Path,
                 approved_at: str, orders_all: list, gate: dict = None) -> int:
    """잠금을 쥔 채 실제 전송부터 체결 확정까지(옛 main의 뒷부분)."""
    import fill
    rec_now = fill.read_day(out, strict=True)
    orders, dup, held = dedupe_against_day(orders_all, rec_now, path.name)
    # ★ 저장소 대조(v7 · Codex R02) — 이 승인 파일의 주문 의도가 저장소(이전 마감이 옮긴 ex_order)에 있는데 그날 기록에 없으면 기록이 덮인
    #   것이다 — 그 기록으로 중복 검사를 하면 이미 나간 주문이 다시 나간다. 못 읽어도 멈춘다(대조할 수 없으면 보내지 않는다).
    try:
        have = db.order_intents(db.run_id(args.stamp or _stamp_from(path), market.lower()), approved_sha)
    except Exception as e:                           # noqa: BLE001
        print(f"집행 거부 — 주문 0건: 저장소의 주문 의도를 읽지 못했다({type(e).__name__}: {e}) — 그날 기록과 대조할 수 없다",
              file=sys.stderr)
        return 2
    lost = sorted(have - {t.get("intent_key") for t in (rec_now.get("trades") or []) if t.get("intent_key")})
    if lost:
        print(f"집행 거부 — 주문 0건: 저장소에는 이 승인 파일의 주문 {len(lost)}건({', '.join(lost[:3])})이 있는데 그날 기록 {out.name}에 "
              f"없다 — 기록이 덮였다(다시 보내면 두 번 산다). 기록을 되살린 뒤 다시 돌린다", file=sys.stderr)
        return 2
    skipped = []
    for o in dup:
        skipped.append({k: o.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": "중복 — " + o["_why"]})
    run_key = f"{datetime.now(KST).isoformat()}#{approved_sha[:12]}"
    approved_kind = approved_kind_of(approved)
    client = KisClient.for_mode(orders=True)
    cap = approved.get("order_cap") or rg.order_cap_cfg(limits) or None
    if cap and market != "KR" and not cap.get("fx"):
        cap = {**cap, "fx": float(approved.get("order_cap_fx") or 0)}

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

    limits_send = _send_cfg()
    blocked = 0
    # ★ 보내기 **전에** 이 집행의 runs[] 항목(승인 스냅샷)을 먼저 적는다 — 도중에 죽어도 승인 대조가 무엇이 승인됐는지 안다.
    run_meta = {"generated_at": datetime.now(KST).isoformat(), "svr": client.svr, "account_mode": client.mode,
                "approved_ref": str(path), "approved_at": approved_at, "approved_orders": approved_snapshot(approved),
                "skipped": skipped, "signal_ref": approved.get("signal_ref"), "limits_sha256": approved.get("limits_sha256"),
                "run_key": run_key, "approved_sha256": approved_sha, "approved_kind": approved_kind, "gate": gate,
                "snapshot_age_min": _snapshot_age_min(approved),
                "screen_pass": (approved.get("screen") or {}).get("pass") if isinstance(approved.get("screen"), dict) else None}
    merge_day_record(out, {**run_meta, "trades": []})
    # ★ 접수 불명(UNKNOWN·PENDING) 행은 보내기 전에 당일 주문 목록으로 먼저 푼다(다시 보내지 않는다 · R01·R02).
    try:
        rec0 = fill.read_day(out, strict=True)
        n_res = fill.resolve_pending(client, rec0, datetime.now(KST), print)
        db.write_json_atomic(out, rec0)              # 관측(없음 확인 횟수·잔고)도 남긴다 — 다음 폴링이 이어받는다
        if n_res:
            orders, dup2, held = dedupe_against_day(orders_all, rec0, path.name)
            for o in dup2:
                if not any((o.get("ticker"), o.get("action")) == (d.get("ticker"), d.get("action")) for d in dup):
                    skipped.append({k: o.get(k) for k in ("ticker", "action", "source", "qty")} | {"why": "중복 — " + o["_why"]})
    except fill.DayRecordCorrupt:
        raise
    except Exception as e:                                # noqa: BLE001 — 해소 실패는 보류로 남는다(아래 held)
        print(f"  · 접수 불명 해소 실패({type(e).__name__}: {str(e)[:100]}) — 그 종목은 보류로 둔다", file=sys.stderr)
    # ★ 보류 건(같은 종목·방향의 열린 주문)은 먼저 확정을 시도한 뒤 다시 판정한다 — 살아 있는 주문 위에
    #   같은 주문을 얹으면 두 번 산다/판다.
    if held:
        print(f"  열린 주문 {len(held)}건 먼저 확정 시도 …")
        fill.confirm(out, client=client, max_sec=min(180, max(60, args.max_sec // 3)), chase=True, quiet=False)
        again, dup2, held = dedupe_against_day([{k: v for k, v in o.items() if k != "_why"} for o in held],
                                               fill.read_day(out, strict=True), path.name)
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
    # ★ FB-261003-10 — 매수가 대금을 기대는 일반 매도도 회전 매도처럼 **먼저 보내고 체결을 확인한다**.
    dep_t = {t for o in orders if o.get("action") == "BUY" for t in (o.get("needs_sell_fill") or [])}
    presell = [o for o in orders if o.get("action") == "SELL" and o.get("source") != "funding" and o.get("ticker") in dep_t]
    rest = [o for o in orders if o.get("source") != "funding" and not any(o is x for x in presell)]
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
    first = funding + presell
    if first:
        t1, f1 = _send_orders(client, first, market, path.name, approved_at, cap, journal=out,
                              approved_sha=approved_sha, qty_before=qty_before, run_key=run_key)
        trades += t1
        failures += f1
        # ★ 전송 전 기준선을 행에 박는다 — 모의서버 국내 체결 TR이 아무것도 안 돌려줄 때
        #   fill.py의 잔고 대조(_fallback_balance)는 `fill.qty_before`가 있어야 판정한다.
        #   2026-09-21: 두산 46주 회전 매도가 잔고에서는 확정(98→52)됐는데 이 값이 없어 미확정으로 남았다.
        if qty_before is not None:
            for t in t1:
                t.setdefault("fill", {})["qty_before"] = qty_before.get(t.get("ticker"), 0)
        pre = {**run_meta, "generated_at": datetime.now(KST).isoformat(), "skipped": skipped, "trades": t1,
               "fill_summary": {"sent": len(first)}}
        merge_day_record(out, pre)
        if not args.no_wait:
            print("  회전·의존 매도 체결 확정 대기 …")
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
        if presell and not args.no_wait:
            orders_left, dep_skip = adjust_sell_dependent(rest, presell, fill.read_day(out, strict=True), path.name)
            for sk in dep_skip:
                print(f"  ⊘ 보류 BUY {sk['ticker']} — {sk['why']}", file=sys.stderr)
            skipped += dep_skip
    else:
        orders_left = orders
    t2, f2 = _send_orders(client, orders_left, market, path.name, approved_at, cap, journal=out,
                          approved_sha=approved_sha, qty_before=qty_before, run_key=run_key)
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
    # ★ 행 단위로 판정한다(2026-10-19) — 옛 판은 종목으로 묶어 같은 종목 두 주문이 섞였다. 접수 불명(UNKNOWN·PENDING)은
    #   판정하지 않는다(fill.confirm이 당일 목록·잔고로 푼다).
    unfilled, fills = _report_fills_rows(trades, qty_before, balance_after)

    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        **run_meta,
        "generated_at": datetime.now(KST).isoformat(),
        "fallbacks": [{"ticker": t["ticker"], **((t.get("result") or {}).get("fallback") or {})}
                      for t in trades if (t.get("result") or {}).get("fallback")],
        "skipped": skipped,
        "trades": trades,
        "fill_summary": {
            "sent": len(orders_left),
            "filled": sum(1 for t in trades if (t.get("fill") or {}).get("verdict") == "FILLED"),
            "unfilled": sum(1 for t in trades if (t.get("fill") or {}).get("verdict") == "UNFILLED"),
            "partial": sum(1 for t in trades if (t.get("fill") or {}).get("verdict") == "PARTIAL"),
            # 거부는 미체결과 따로 센다 — 기다릴 것이 없는 건이다.
            "rejected": sum(1 for t in trades if (t.get("fill") or {}).get("verdict") == "REJECTED"),
            # 판정 못 한 건(잔고 조회 실패 · 접수 불명) — fill.confirm이 확정한다
            "unverified": sum(1 for t in trades if not (t.get("fill") or {}).get("verdict")),
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
    논지 상태는 저장소 논지 원장(`db.theses_all`)에서 **지금** 읽는다 — theses.py set-status가 같은 run에 옮긴 판이다.
    """
    by_tic = {}
    # ★ 전송 **뒤**·거래 기록 쓰기 **전**에 불린다 — 여기서 예외가 나면 그날 기록이 안 남아 같은 approved 재실행이
    #   이미 나간 주문을 다시 보낸다(멱등 대조가 그 기록을 본다). 옛 파일 판의 "못 읽으면 빈 목록" 방어를 잇되,
    #   못 읽은 사실을 숨기지 않고 체결 티커를 '원장 읽기 실패'로 올린다(execute 게이트가 막고 set-status 뒤 다시 센다).
    try:
        theses = db.theses_all()
    except db.TestGuard:                          # 테스트가 임시 저장소 없이 왔다 — 삼키지 않는다
        raise
    except Exception as e:                        # noqa: BLE001
        print(f"  ★ 논지 원장 읽기 실패({type(e).__name__}: {str(e)[:120]}) — 체결 티커를 미동기로 올린다", file=sys.stderr)
        theses, by_tic = [], None
    for x in theses:
        by_tic.setdefault(str(x.get("ticker")), []).append(x.get("status"))
    out = []
    for tic, f in fills.items():
        if f.get("verdict") not in ("FILLED", "PARTIAL"):
            continue
        if by_tic is None:
            out.append({"ticker": tic, "thesis_status": ["(논지 원장 읽기 실패)"]})
            continue
        st = by_tic.get(tic) or []
        if "held" not in st:
            out.append({"ticker": tic, "thesis_status": st or ["(논지 없음)"]})
    return out


def _report_fills_rows(trades: list, qty_before, balance_after) -> tuple:
    """전송 직후 **행마다** 체결을 판정해 `t["fill"]`에 쓴다(2026-10-19 · 옛 판은 종목으로 묶어 같은 종목 두 주문이 섞였다).

    - UNSENT·REJECTED(접수 0) → 이미 `_send_orders`가 REJECTED와 사유를 적었다(그대로 둔다).
    - UNKNOWN·PENDING(접수됐을 수 있다) → 판정하지 않는다 — `fill.confirm`이 당일 주문 목록·잔고로 푼다.
    - SENT → 전송 전후 보유 수량 대조(`_report_fills`와 같은 규칙). 같은 종목·방향에 SENT가 여럿이면 잔고 차이로 가를 수 없어
      판정을 미루고(UNFILLED) 체결 TR이 주문번호로 확정한다.
    반환 (미체결·부분 건수, {종목: 그 종목 마지막 행의 fill}) — 잔고 조회가 실패하면 (0, {})."""
    if qty_before is None or not isinstance(balance_after, dict) or "error" in balance_after:
        print("\n★ 체결 확인 불가 — 잔고 조회가 실패했다. "
              "'주문을 냈다'까지만 보고하고 '샀다'고 하지 말 것.", file=sys.stderr)
        return 0, {}
    sent = [t for t in trades if t.get("status") == "SENT"]
    multi = {k for k in {(t["ticker"], t["action"]) for t in sent}
             if sum(1 for t in sent if (t["ticker"], t["action"]) == k) > 1}
    to_judge, out = [], {}
    for t in trades:
        prev = t.get("fill") or {}
        if t.get("status") in ("UNSENT", "REJECTED", "FAILED"):
            out[t["ticker"]] = prev
            continue
        if t.get("status") in ("UNKNOWN", "PENDING"):
            print(f"  ? 접수 불명 {t['action']} {t['ticker']} — 판정 보류(fill이 당일 주문 목록으로 확인한다)")
            out[t["ticker"]] = prev
            continue
        if (t["ticker"], t["action"]) in multi:
            t["fill"] = {**prev, "verdict": "UNFILLED",
                         "note": "같은 종목·방향 주문이 여럿 — 잔고 차이로 가르지 않는다(체결 TR이 주문번호로 확정)"}
            out[t["ticker"]] = t["fill"]
            continue
        to_judge.append(t)
    unfilled, fills = _report_fills([{k: t.get(k) for k in ("ticker", "name", "action", "qty", "price")} for t in to_judge],
                                    qty_before, balance_after, {t["ticker"]: "SENT" for t in to_judge})
    for t in to_judge:
        if t["ticker"] in fills:
            t["fill"] = {**(t.get("fill") or {}), **fills[t["ticker"]]}
            out[t["ticker"]] = t["fill"]
    unfilled += sum(1 for t in trades if (t["ticker"], t["action"]) in multi and t.get("status") == "SENT")
    return unfilled, out


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
        if statuses.get(t) == "UNSENT":
            # 우리가 보내지 않았다(전송 직전 갱신의 가격 이탈 등) — 브로커 거부가 아니다. 판정 어휘는 종결 6값을 지키되
            # 화면·메모는 '미전송'으로 쓴다. *실사례(2026-10-09 US): 1주 0주 미전송이 '⊘ 거부 — 브로커가 받지 않았다'로
            # 찍혀 노트 §11이 따로 해명해야 했다.*
            fills[t] = {"verdict": "REJECTED", "qty_before": qty_before.get(t, 0),
                        "qty_after": after.get(t, 0), "delta": 0,
                        "requested": o["qty"] if o["action"] == "BUY" else -o["qty"],
                        "limit_price": o["price"],
                        "note": "client-side 미전송 — 브로커에 보낸 것이 없다(거부 아님)"}
            print(f"  ⊘ 미전송 {o['action']} {t} {o.get('name','')} "
                  f"— 클라이언트 측(브로커 거부 아님) · 걸려 있는 주문이 없다.")
            continue
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
