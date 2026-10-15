#!/usr/bin/env python3
"""
실전 전환 주간(2026-10-15) 테스트 — 매도 뒤 재투자 · 예약 계산 · 실계좌 최소 1주 · 잔고 함수 · 슬롯 · 점검표.

왜: 10-14 미국 run은 매수 단계 뒤 손절·추적·소급 매도가 판 돈을 다시 쓰지 않아 투자비중이 95% → 79%로 떨어졌고(사용자 지적),
예약 계산이 이번 run이 사는 종목의 첫 칸까지 묶어 회전 매도를 불렀다. 실계좌 그림자 점검에서는 칸이 1주보다 작아 매수가 전부 0건이었다.
이 파일이 그 고친 동작을 고정한다. 실제 원장·시세·포인터는 건드리지 않는다(임시 폴더).

    python3 test_realweek.py          # run_tests.py가 부른다
"""
import atexit
import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import risk_guard as rg      # noqa: E402
import allocation as al      # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_realweek.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn):
    _STATE["current"] = fn.__name__
    try:
        fn()
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        results.append((FAIL, f"{fn.__name__} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {fn.__name__} — 중단({type(e).__name__}: {str(e)[:200]})")


def report(label, problems):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


def _now():
    return datetime.now(KST).isoformat()


def _th(tid, tk, rungs, status="armed", market="KR", expires=None):
    t = {"id": tid, "ticker": tk, "market": market, "status": status, "entry_triggers": rungs, "exit_triggers": []}
    if expires:
        t["expires"] = expires
    return t


def t1_reserved_exclusions():
    """예약 기본값 — 안 산 가격 칸만 · 판단 칸·체결 칸·만기·이번 매수 종목은 뺀다."""
    p = []
    theses = [
        _th("a", "AAA", [{"id": "e1", "check": "price <= 100", "size_pct": 2.0}]),                        # 남는다
        _th("b", "BBB", [{"id": "e0", "check": "judge", "size_pct": 2.0}]),                               # 판단 칸 — 뺀다
        _th("c", "CCC", [{"id": "e1", "check": "price <= 90", "size_pct": 2.0, "fired": True},
                         {"id": "e2", "check": "price <= 80", "size_pct": 1.0}]),                           # 다음 칸 e2
        _th("d", "DDD", [{"id": "e1", "check": "price <= 50", "size_pct": 2.0}], expires="2026-01-01"),     # 만기 — 뺀다
        _th("e", "EEE", [{"id": "e1", "check": "price <= 70", "size_pct": 2.0}]),                          # 이번 매수 — 뺀다
        _th("f", "FFF", [{"id": "e1", "check": "price <= 70", "size_pct": 2.0}], status="held"),           # 보유 — 대상 아님
    ]
    out = al.default_reserved("KR", {"KR": 1_000_000}, theses=theses, buying={"EEE"}, today="2026-10-15")
    got = {r["thesis_id"]: r["amount"] for r in out}
    if got != {"a": 20000, "c": 10000}:
        p.append(f"예약 대상이 다르다: {got}")
    if not any("e2" in r["why"] for r in out if r["thesis_id"] == "c"):
        p.append("체결된 칸 다음 칸(e2)을 쓰지 않았다")
    report("(1) 예약 기본값 — 판단 칸·체결 칸·만기·이번 매수 종목 제외(10-14 미국 $4,887 묶임)", p)


def t2_after_sells():
    p = []
    bal = {"cash": 100.0, "positions": [{"ticker": "X", "qty": 10, "eval_amt": 500.0, "price": 50.0},
                                         {"ticker": "Y", "qty": 4, "eval_amt": 400.0, "price": 100.0}]}
    sells = [{"ticker": "X", "action": "SELL", "qty": 10, "price": 50.0, "source": "discipline"},
             {"ticker": "Y", "action": "SELL", "qty": 1, "price": 100.0, "source": "funding"}]
    post, proceeds = rg._after_sells(bal, sells)
    if proceeds != 500.0 or post["cash"] != 600.0:
        p.append(f"대금·현금: {proceeds} {post['cash']} (회전 매도는 빼야 한다)")
    if [q["ticker"] for q in post["positions"]] != ["Y"] or post["positions"][0]["qty"] != 4:
        p.append(f"보유: {post['positions']}")
    same, z = rg._after_sells(bal, [])
    if z != 0.0 or same is not bal:
        p.append("매도가 없으면 그대로여야 한다")
    report("(2) 매도 뒤 잔고 — 비회전 매도 대금은 현금으로, 보유는 줄인다", p)


def _guard(signal, snapshot, limits_patch=None, theses=None, ledger=None, discipline_only=False, refill=False):
    tmp = Path(tempfile.mkdtemp(prefix="rw_"))
    cfg = tmp / "config"
    cfg.mkdir()
    limits = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    limits["fc_track1_required"] = False
    limits.update(limits_patch or {})
    (cfg / "limits.json").write_text(json.dumps(limits), encoding="utf-8")
    shutil.copy(rg.WATCHLIST_PATH, cfg / "watchlist.json")
    sp = tmp / "snapshot.json"
    sp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    sg = tmp / "signal_261015_kr.json"
    if signal is not None:
        sg.write_text(json.dumps(signal, ensure_ascii=False), encoding="utf-8")
    out = tmp / "approved.json"
    saved = (rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.PEAKS_PATH, rg.DATA_DIR,
             rg.JOURNAL_DIR, rg.CALENDAR_PATH, al.JOURNAL_DIR)
    rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH = cfg / "limits.json", cfg / "watchlist.json", cfg / "KILL"
    rg.SIGNALS_DIR, rg.PEAKS_PATH = tmp, tmp / "peaks.json"
    rg.DATA_DIR, rg.JOURNAL_DIR = tmp / "data", tmp / "journal"
    rg.DATA_DIR.mkdir()
    rg.JOURNAL_DIR.mkdir()
    al.JOURNAL_DIR = rg.JOURNAL_DIR
    rg.CALENDAR_PATH = rg.JOURNAL_DIR / "calendar.json"
    (rg.JOURNAL_DIR / "theses.json").write_text(json.dumps({"theses": theses or []}), encoding="utf-8")
    if ledger:
        (rg.JOURNAL_DIR / "allocation.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ledger), encoding="utf-8")
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = rg.run(None if discipline_only else sg, sp, out, discipline_only=discipline_only, refill=refill)
        approved = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        if refill:
            approved["_ledger_written"] = (rg.JOURNAL_DIR / "allocation.jsonl").exists()
    except rg.Rejection as e:
        code, approved = 2, {"_rejection": str(e)}
    finally:
        (rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.PEAKS_PATH, rg.DATA_DIR,
         rg.JOURNAL_DIR, rg.CALENDAR_PATH, al.JOURNAL_DIR) = saved
        shutil.rmtree(tmp, ignore_errors=True)
    return code, approved, buf.getvalue()


def _snap(kind=None):
    s = {"market": "KR", "generated_at": _now(), "day_pnl_pct": 0.0,
         "balance": {"currency": "KRW", "cash": 200_000.0,
                     "positions": [{"ticker": "042700", "name": "한미반도체", "qty": 10, "price": 80_000.0,
                                    "eval_amt": 800_000.0, "avg_price": 100_000.0, "pnl_pct": -20.0}]},
         "prices": {"005930": {"price": 50_000.0}, "042700": {"price": 80_000.0}}}
    if kind:
        s["kind"] = kind
    return s


def _signal():
    return {"schema_version": rg.SCHEMA_VERSION, "run_id": "t", "market": "KR", "generated_at": _now(),
            "allocation": {"target_invested_pct": 98, "regime": "테스트 벤치마크 20일 +1.0%", "cash_reason": "없음",
                           "reserved": [], "reserved_why": "테스트 — 예약 0", "based_on": None},
            "proposals": [{"ticker": "005930", "name": "삼성전자", "action": "BUY", "thesis": "테스트 논지", "weight_target_pct": 10.0,
                           "confidence": 0.7, "size_why": "테스트", "expected_upside_pct": 30.0,
                           "evidence": [{"claim": "c", "source_url": "https://x.example/a", "verdict": "MATCH", "tier": "T1"}]}]}


def t3_same_run_sell_refills():
    """같은 run의 손절 매도 대금이 gap에 들어가 매수가 그만큼 커진다(매도 체결 확인 뒤 전송)."""
    p = []
    code, ap, out = _guard(_signal(), _snap(), limits_patch={"stop_loss_pct": -10.0})
    if code != 0:
        p.append(f"종료코드 {code}: {ap.get('_rejection') or out[-300:]}")
    else:
        orders = ap.get("orders") or []
        sells = [o for o in orders if o["action"] == "SELL"]
        buys = [o for o in orders if o["action"] == "BUY"]
        if not sells or sells[0]["ticker"] != "042700":
            p.append(f"손절 매도가 없다: {orders}")
        ps = (ap.get("allocation") or {}).get("post_sell") or {}
        if not ps or ps.get("invested_pct_after_sells", 99) >= ps.get("invested_pct_capture", 0):
            p.append(f"매도 뒤 투자비중 기록 없음: {ps}")
        if not buys:
            p.append(f"매수가 없다 — 판 돈이 현금으로 남았다: {ap.get('rejected')}")
        else:
            amt = sum(int(o["qty"]) * float(o["price"]) for o in buys)
            if amt <= 200_000:
                p.append(f"매수가 손절 대금을 쓰지 않았다(현금 20만 안): {amt:,.0f}")
            if not any(o.get("needs_sell_fill") for o in buys):
                p.append("매도 대금 의존 매수에 needs_sell_fill 표시가 없다")
        why = str((ap.get("deficit") or {}).get("why") or "")
        if "제안이 모자라다" in why:              # 건수·축 규칙(제안 2건·축 2개)은 이 케이스가 재는 것이 아니다 — 금액만 본다
            p.append(f"매도 대금을 채우지 못해 금액 미달로 찍혔다: {why}")
        if "투자비중 0.0%" not in why:
            p.append(f"미달 검사가 매도 뒤 투자비중(0%)으로 재지 않았다: {why[:80]}")
    report("(3) 같은 run 손절 대금 → gap에 넣고 매수로 채운다 · 매도 체결 뒤 전송", p)


def t4_discipline_refill_need():
    """매도 전용 pass(stops)는 판 뒤 gap을 refill_need로 남긴다 — 장중 극값 판정 pass는 남기지 않는다."""
    p = []
    led = [{"id": "AL-261014-kr", "stamp": "261014", "market": "kr", "target_invested_pct": 98}]
    code, ap, _ = _guard(None, _snap("stops"), limits_patch={"stop_loss_pct": -10.0}, ledger=led, discipline_only=True)
    rn = ap.get("refill_need") or {}
    if not rn.get("short") or rn.get("based_on") != "AL-261014-kr" or float(rn.get("gap_amt") or 0) <= 0:
        p.append(f"refill_need: {rn}")
    code, ap2, _ = _guard(None, _snap("stops_intraday_low"), limits_patch={"stop_loss_pct": -10.0}, ledger=led,
                          discipline_only=True)
    if ap2.get("refill_need"):
        p.append("장중 극값 pass가 refill_need를 냈다")
    report("(4) 매도 전용 pass — 판 뒤 gap(refill_need) · 직전 배분 목표 기준 · 장중 극값 pass 제외", p)


def t5_real_one_share():
    """실계좌는 칸이 1주보다 작아도 1주로 올린다(모의는 지금 규칙 — 0.6주 미만이면 0주 거부)."""
    p = []
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim["fc_track1_required"] = False
    wl = {"KR": [{"ticker": "005930", "name": "삼성전자"}]}
    bal = {"cash": 10_000_000.0, "currency": "KRW", "positions": []}
    sig = {"market": "KR", "proposals": [{"ticker": "005930", "name": "삼성전자", "action": "BUY", "weight_target_pct": 2.0,
                                          "confidence": 0.7, "size_why": "t", "expected_upside_pct": 30.0,
                                          "evidence": [{"claim": "c", "source_url": "https://x.example/a",
                                                        "verdict": "MATCH", "tier": "T1"}]}]}
    saved = rg.portfolio_equity
    rg.portfolio_equity = lambda m, b: {"krw": 1e7, "cash_krw": 1e7, "invested_krw": 0.0, "ok": True,
                                        "asof": "t", "fx": 1.0, "parts": {}, "why": "t"}
    try:
        prices = {"005930": {"price": 400_000.0}}          # 2% = 20만 < 1주 40만
        with contextlib.redirect_stdout(io.StringIO()):
            acc_p, rej_p = rg.screen_proposals(sig, {**lim, "account_mode": "paper"}, wl, bal, prices)
            acc_r, rej_r = rg.screen_proposals(sig, {**lim, "account_mode": "real"}, wl, bal, prices)
    finally:
        rg.portfolio_equity = saved
    if acc_p or not any("0주" in r["why"] for r in rej_p):
        p.append(f"모의는 0주 거부여야 한다: {acc_p} {rej_p}")
    if not acc_r or acc_r[0]["qty"] != 1 or not acc_r[0].get("rounded_up_to_1"):
        p.append(f"실계좌는 1주로 올려야 한다: {acc_r} {rej_r}")
    report("(5) 실계좌 최소 1주 — 칸이 1주보다 작으면 1주(모의는 그대로)", p)


def t6_gapfill_topup_and_deficit():
    """비례 확대 뒤 자투리를 1주씩 돌려 채운다 · 미달은 가장 싼 승인 종목 1주로 센다."""
    p = []
    ctx = {"need": 1_000_000.0, "gap_amt": 1_000_000.0, "deployable": 1_000_000.0, "reserved_here": 0.0}
    buys = [{"ticker": "A", "action": "BUY", "qty": 1, "price": 30_000.0, "source": "llm"},
            {"ticker": "B", "action": "BUY", "qty": 1, "price": 70_000.0, "source": "llm"}]
    bal = {"cash": 1_000_000.0, "currency": "KRW", "positions": []}
    lim = {"funding_max_per_run": 0}
    saved = rg.spent_today
    rg.spent_today = lambda m, c: (0.0, 0)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            final, rej = rg.apply_run_limits(buys, lim, bal, 0.0, "KR", ctx)
    finally:
        rg.spent_today = saved
    amt = sum(int(o["qty"]) * float(o["price"]) for o in final if o["action"] == "BUY")
    if 1_000_000 - amt >= 30_000:
        p.append(f"자투리가 가장 싼 1주 이상 남았다: {amt:,.0f}")
    if ctx.get("min_buy_price") != 30_000.0:
        p.append(f"min_buy_price: {ctx.get('min_buy_price')}")
    d = rg.deficit_check({"allocation": {"target_invested_pct": 98},
                          "proposals": [{"action": "BUY", "ticker": "A"}, {"action": "BUY", "ticker": "B"}]},
                         {}, "KR", 50.0, [], "2026-10-15",
                         {"target_invested_pct": 98, "need": 1_000_000.0, "min_buy_price": 30_000.0, "max_buy_price": 70_000.0},
                         [{"action": "BUY", "qty": 31, "price": 30_000.0}])   # 93만 — 남은 7만 > 가장 싼 3만
    if not d.get("short"):
        p.append(f"남은 7만(가장 싼 3만 초과)이 미달로 안 잡혔다: {d.get('why')}")
    report("(6) gap 채우기 자투리 1주씩 · 미달은 가장 싼 1주 기준", p)


def t7_record_refill_and_gate():
    """stops가 체결된 매도로 재투자 기록(open)을 남기고, close는 open이면 3 · 미루면 다음 run 이어받기가 받는다."""
    import stops
    import stage
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwr_"))
    saved = (stops.HERE, stage.JOURNAL, stage.PROJ)
    try:
        stops.HERE = tmp
        stage.JOURNAL = tmp / "journal"
        stage.PROJ = tmp
        (tmp / "journal").mkdir()
        name = "approved_261015_kr_stops_1030.json"
        (tmp / "journal" / "trades_261015_kr.json").write_text(json.dumps({"trades": [
            {"ticker": "042700", "action": "SELL", "approved_ref": name, "source": "discipline", "price": 80_000,
             "fill": {"filled_qty": 10, "avg_price": 79_900, "verdict": "FILLED"}},
            # 실제 국내 매도 확정 형식(10-15 run이 찾은 결함) — filled_qty·avg_price 없이 delta·limit_price만
            {"ticker": "009150", "action": "SELL", "approved_ref": name, "source": "discipline", "qty": 1, "price": 1_175_000,
             "fill": {"qty_before": 1, "qty_after": 0, "delta": -1, "requested": -1, "limit_price": 1_175_000.0, "verdict": "FILLED"}},
            {"ticker": "005930", "action": "BUY", "approved_ref": name, "fill": {"filled_qty": 1, "avg_price": 50_000}}]}),
            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            ev = stops.record_refill("KR", "261015", name, {"refill_need": {"short": True, "gap_amt": 790_000,
                                                                           "target_invested_pct": 98, "why": "t"}})
        rf = json.loads((tmp / "journal" / "refill_261015_kr.json").read_text(encoding="utf-8"))
        if not ev or rf.get("status") != "open" or abs(rf["events"][0]["proceeds"] - (799_000 + 1_175_000)) > 1:
            p.append(f"재투자 기록: {rf}")
        with contextlib.redirect_stdout(io.StringIO()):
            none = stops.record_refill("KR", "261015", "approved_other.json", {"refill_need": {"short": True}})
        if none:
            p.append("체결 없는 승인서로 기록했다")
        rc, md = stage.refill_gate("kr", "261015")
        if rc != 3 or "재투자 미처리" not in md:
            p.append(f"close가 open을 막지 않았다: {rc}")
        stale = tmp / "data" / "run_evidence" / "real_check_261015_kr.md"     # 앞선 close가 남긴 점검표
        stale.parent.mkdir(parents=True)
        stale.write_text("[실전점검] 이전", encoding="utf-8")
        ca = type("C", (), {"market": "kr", "stamp": "261015"})()
        with contextlib.redirect_stdout(io.StringIO()):
            crc = stage.cmd_close(ca)
        if crc != 3 or stale.exists():
            p.append(f"재투자 미처리인데 close가 점검표를 남겼다(게이트가 못 막는다): rc={crc} 남음={stale.exists()}")
        a = type("A", (), {"market": "kr", "stamp": "261015", "why": "시그널 나이 300분 초과 — 다음 run이 채운다"})()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            stage.cmd_refill_defer(a)
        rc2, _ = stage.refill_gate("kr", "261015")
        if rc2 != 0:
            p.append("미룬 뒤에도 막는다")
        blk = stage.refill_block("kr", "261016")
        if "042700" not in blk or "시그널 나이" not in blk:
            p.append(f"다음 run 이어받기 절: {blk[:200]}")
        if json.loads((tmp / "journal" / "refill_261015_kr.json").read_text(encoding="utf-8")).get("status") != "carried":
            p.append("이어받은 기록이 carried로 바뀌지 않았다")
        if "없음" not in stage.refill_block("kr", "261017"):
            p.append("한 번 이어받은 대기를 다시 그렸다")
    finally:
        stops.HERE, stage.JOURNAL, stage.PROJ = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(7) 재투자 기록(체결된 매도만) · close는 open이면 3·점검표 치움 · refill-defer → 다음 run '## 재투자 대기' 1회", p)


def _refill_signal(age_min=250):
    sig = _signal()
    sig["generated_at"] = (datetime.now(KST) - timedelta(minutes=age_min)).isoformat()
    sig["proposals"].append({"ticker": "042700", "name": "한미반도체", "action": "SELL", "thesis": "테스트 매도", "confidence": 0.6,
                             "weight_target_pct": 0.0, "evidence": [{"claim": "c", "verdict": "MATCH"}]})
    sig["refill_candidates"] = [{"ticker": "000660", "name": "SK하이닉스", "action": "BUY", "thesis": "테스트 후보 논지",
                                 "weight_target_pct": 10.0, "confidence": 0.6, "size_why": "테스트", "expected_upside_pct": 30.0,
                                 "evidence": [{"claim": "c", "source_url": "https://x.example/b", "verdict": "MATCH", "tier": "T1"}]}]
    return sig


def t11_refill_mode():
    """재투자 pass — 시그널 나이 300분까지 · 매수 제안 + 재투자 후보만 · 모델 SELL은 다시 안 낸다 · 배분 원장에 안 쓴다."""
    p = []
    snap = _snap("refill")
    snap["balance"]["cash"] = 1_000_000.0                       # 판 돈이 현금에 들어온 뒤의 잔고
    snap["prices"]["000660"] = {"price": 200_000.0}
    snap["refill_of"] = "refill_261015_kr.json"
    code, ap, out = _guard(_refill_signal(), snap, limits_patch={"stop_loss_pct": -50.0}, refill=True)
    if code != 0:
        p.append(f"종료코드 {code}: {ap.get('_rejection') or out[-300:]}")
    else:
        orders = ap.get("orders") or []
        if any(o["action"] == "SELL" for o in orders):
            p.append(f"재투자 pass가 모델 SELL을 다시 냈다: {[o['ticker'] for o in orders]}")
        tick = {o["ticker"] for o in orders if o["action"] == "BUY"}
        if "000660" not in tick:
            p.append(f"재투자 후보가 심사되지 않았다: {tick} · 거부 {[(r['ticker'], r['why'][:60]) for r in ap.get('rejected') or []]}")
        if not ap.get("refill") or ap.get("refill_of") != "refill_261015_kr.json":
            p.append(f"승인서 표시: refill={ap.get('refill')} refill_of={ap.get('refill_of')}")
        if ap.get("_ledger_written"):
            p.append("재투자 pass가 배분 원장에 썼다(같은 run 판단 행을 덮는다)")
        if any(o.get("needs_sell_fill") for o in orders):
            p.append("새 잔고에 대금이 이미 현금인데 needs_sell_fill이 붙었다")
    code2, ap2, _ = _guard(_refill_signal(), snap, limits_patch={"stop_loss_pct": -50.0})
    if code2 == 0 and "_rejection" not in ap2:
        p.append("정상 run이 250분 된 시그널을 받았다(180분 상한)")
    code3, ap3, _ = _guard(_refill_signal(age_min=320), snap, limits_patch={"stop_loss_pct": -50.0}, refill=True)
    if "_rejection" not in ap3:
        p.append("재투자 pass가 320분 된 시그널을 받았다(300분 상한)")
    bad = _refill_signal()
    bad["refill_candidates"][0]["action"] = "SELL"
    code4, ap4, _ = _guard(bad, snap, limits_patch={"stop_loss_pct": -50.0}, refill=True)
    if "_rejection" not in ap4:
        p.append("SELL 재투자 후보를 받았다")
    report("(11) 재투자 pass — 300분 상한 · 매수 제안+후보만 · 모델 SELL 제외 · 배분 원장 안 씀 · 후보도 같은 검증", p)


def t12_refill_script():
    """refill.py — 새 잔고·시세 스냅샷(캡처 스냅샷은 안 씀) → 승인서 · 장 마감 30분 전이면 미룸 · 체결 뒤 consumed → close 통과."""
    import refill
    import stage
    import kis_client
    import ingest
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwf_"))
    saved = (refill.JOURNAL, refill.SIGNALS, refill.DATA, refill.EVID, refill.HERE, stage.JOURNAL,
             kis_client.balance_for, ingest.collect_prices, rg.run)
    calls = {}
    try:
        refill.HERE = tmp
        refill.JOURNAL, refill.SIGNALS, refill.DATA = tmp / "journal", tmp / "signals", tmp / "data"
        refill.EVID = refill.DATA / "run_evidence"
        stage.JOURNAL = refill.JOURNAL
        for d in (refill.JOURNAL, refill.SIGNALS, refill.EVID, tmp / "config"):
            d.mkdir(parents=True, exist_ok=True)
        (tmp / "config" / "watchlist.json").write_text("{}", encoding="utf-8")
        (refill.SIGNALS / "signal_261015_kr.json").write_text(json.dumps(_signal()), encoding="utf-8")
        cap = _snap()
        (refill.DATA / "snapshot_261015_kr.json").write_text(json.dumps(cap), encoding="utf-8")
        cap_bytes = (refill.DATA / "snapshot_261015_kr.json").read_bytes()
        (refill.JOURNAL / "refill_261015_kr.json").write_text(json.dumps({"status": "open", "events": [{"proceeds": 800000}]}),
                                                             encoding="utf-8")
        kis_client.balance_for = lambda c, m, w, with_fx=True, now=None: ({"currency": "KRW", "cash": 1_000_000.0, "positions": []}, "")
        ingest.collect_prices = lambda c, m, w, pos: ({"005930": {"price": 51_000.0}}, {})

        def fake_run(sig_p, snap_p, out_p, discipline_only=False, live=False, refill=False):
            calls["snap"] = json.loads(Path(snap_p).read_text(encoding="utf-8"))
            calls["refill"] = refill
            Path(out_p).write_text(json.dumps({"orders": [{"ticker": "005930", "action": "BUY", "qty": 3, "price": 51_000.0}]}),
                                   encoding="utf-8")
            return 0
        rg.run = fake_run
        now = datetime(2026, 10, 15, 13, 0, tzinfo=KST)
        late = {"is_open": True, "close": datetime(2026, 10, 15, 15, 10, tzinfo=KST)}
        rc0, _, msg0 = refill.build("kr", "261015", now=datetime(2026, 10, 15, 14, 50, tzinfo=KST), client=object(), session=late)
        if rc0 != 2 or "마감" not in msg0:
            p.append(f"마감 20분 전인데 미루지 않았다: {rc0} {msg0}")
        sess = {"is_open": True, "close": datetime(2026, 10, 15, 15, 30, tzinfo=KST)}
        rc, outp, msg = refill.build("kr", "261015", now=now, client=object(), session=sess)
        if rc != 0 or not outp or not outp.name.startswith("approved_261015_kr_refill_1300"):
            p.append(f"승인서: {rc} {outp} {msg}")
        sn = calls.get("snap") or {}
        if not calls.get("refill") or sn.get("kind") != "refill" or sn.get("refill_of") != "refill_261015_kr.json":
            p.append(f"재투자 모드·스냅샷 표시: {calls.get('refill')} {sn.get('kind')} {sn.get('refill_of')}")
        if (sn.get("balance") or {}).get("cash") != 1_000_000.0 or (sn.get("prices") or {}).get("005930", {}).get("price") != 51_000.0:
            p.append("스냅샷이 새 잔고·새 시세가 아니다")
        if (sn.get("prices") or {}).get("042700", {}).get("price") != 80_000.0:
            p.append("캡처 시세(새로 못 받은 종목)를 이어 쓰지 않았다")
        if (refill.DATA / "snapshot_261015_kr.json").read_bytes() != cap_bytes:
            p.append("캡처 스냅샷을 다시 썼다")
        rcd, msgd = refill.done("kr", "261015", str(outp), now=now)
        if rcd != 1:
            p.append(f"체결 0인데 consumed로 바꿨다: {rcd} {msgd}")
        (refill.JOURNAL / "trades_261015_kr.json").write_text(json.dumps({"trades": [
            {"ticker": "005930", "action": "BUY", "approved_ref": f"signals/{outp.name}", "qty": 3, "price": 51_000.0,
             "fill": {"qty_before": 0, "qty_after": 3, "delta": 3, "requested": 3, "limit_price": 51_000.0, "verdict": "FILLED"}}]}),
            encoding="utf-8")
        rcd2, msgd2 = refill.done("kr", "261015", str(outp), now=now)
        rf = json.loads((refill.JOURNAL / "refill_261015_kr.json").read_text(encoding="utf-8"))
        if rcd2 != 0 or rf.get("status") != "consumed" or rf.get("spent") != 153_000.0:
            p.append(f"consumed 기록: {rcd2} {rf.get('status')} {rf.get('spent')}")
        grc, _ = stage.refill_gate("kr", "261015")
        if grc != 0:
            p.append("재투자를 마쳤는데 close가 막는다")
        rcn, _, msgn = refill.build("kr", "261015", now=now, client=object(), session=sess)
        if rcn != 0 or "없음" not in msgn:
            p.append(f"consumed 뒤에 다시 돌았다: {msgn}")
    finally:
        (refill.JOURNAL, refill.SIGNALS, refill.DATA, refill.EVID, refill.HERE, stage.JOURNAL,
         kis_client.balance_for, ingest.collect_prices, rg.run) = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(12) refill.py — 새 잔고·시세 스냅샷 · 마감 30분 전 미룸 · 체결 뒤 consumed → close 통과 · 캡처 스냅샷 불변", p)


def t13_track1_no_reextract():
    """주문 근거 고정본 — 판정표가 생긴 뒤 다시 뽑기 거부 · `--append`는 새 번호로 덧붙이고 판정은 그 자리의 새 번호를 쓴다 · 후보도 고정."""
    import fc_track1 as f1
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwt_"))
    saved = f1.HERE
    try:
        f1.HERE = tmp
        (tmp / "signals").mkdir()
        (tmp / "analysis").mkdir()
        sig = _signal()
        sig["proposals"][0]["evidence"][0]["claim"] = "HBM 매출 2배(원 문구)"
        sig["refill_candidates"] = [{"ticker": "000660", "action": "BUY", "thesis": "t", "confidence": 0.6,
                                     "evidence": [{"claim": "후보 근거 문구", "verdict": "MATCH"}]}]
        P = f1.paths("261015", "kr")
        P["signal"].write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc1 = f1.cmd_extract("261015", "kr")
        mp = json.loads(P["map"].read_text(encoding="utf-8"))
        if rc1 != 0 or not any(i.get("ticker") == "000660" for i in mp["items"]):
            p.append(f"첫 고정 또는 재투자 후보 고정 실패: rc={rc1} {[i.get('ref') for i in mp['items']]}")
        P["verdict"].write_text("# 판정표\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc2 = f1.cmd_extract("261015", "kr")
        if rc2 != 3:
            p.append(f"판정표가 있는데 다시 뽑았다: rc={rc2}")
        sig["proposals"][0]["evidence"][0]["claim"] = "HBM 매출 2배 근처(교정 뒤 문구)"
        P["signal"].write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc3 = f1.cmd_extract("261015", "kr", append=True)
        mp2 = json.loads(P["map"].read_text(encoding="utf-8"))
        items = {it["n"]: it for it in mp2["items"]}
        old = next(it for it in items.values() if it.get("text") == "HBM 매출 2배(원 문구)")
        new = [it for it in items.values() if it.get("text") == "HBM 매출 2배 근처(교정 뒤 문구)"]
        if rc3 != 0 or not new or old.get("superseded_by") != new[0]["n"]:
            p.append(f"덧붙임: rc={rc3} old={old} new={new}")
        if "HBM 매출 2배(원 문구)" not in P["claims"].read_text(encoding="utf-8"):
            p.append("덧붙임이 옛 원 문구를 지웠다")
        claim_ok = {k: (True, [(k, "MATCH", "T1", "https://x.example/ok")]) for k in items}
        claim_ok[old["n"]] = (False, [(old["n"], "MISMATCH", "T1", "https://x.example/old")])
        f1._grade(sig, items, claim_ok, "판정.md")
        if sig["proposals"][0]["evidence"][0].get("verdict") != "MATCH":
            p.append(f"판정이 새 번호를 쓰지 않는다(옛 번호의 MISMATCH가 붙었다): {sig['proposals'][0]['evidence'][0]}")
        if sig["refill_candidates"][0]["evidence"][0].get("verdict") != "MATCH":
            p.append(f"재투자 후보 근거가 판정되지 않았다: {sig['refill_candidates'][0]['evidence'][0]}")
    finally:
        f1.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(13) 주문 근거 고정본 — 판정 뒤 다시 뽑기 거부 · --append 새 번호 · 판정은 새 번호 · 재투자 후보도 고정", p)


def t14_mode_filter_other_market():
    """실계좌 run은 모의 반대편(스냅샷·자산 곡선 행)을 포트폴리오에 더하지 않는다 — 모의 run은 그대로."""
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwm_"))
    saved = (rg.DATA_DIR, rg.JOURNAL_DIR, rg.LIMITS_PATH)
    try:
        rg.DATA_DIR, rg.JOURNAL_DIR = tmp / "data", tmp / "journal"
        rg.DATA_DIR.mkdir()
        rg.JOURNAL_DIR.mkdir()
        lim = json.loads(saved[2].read_text(encoding="utf-8"))
        us_paper = {"generated_at": _now(), "account_mode": "paper",
                    "balance": {"currency": "USD", "cash": 1000.0, "exchange_rate": 1400.0,
                                "positions": [{"ticker": "AAPL", "qty": 100, "eval_amt": 50_000.0}]}}
        (rg.DATA_DIR / "snapshot_261014_us.json").write_text(json.dumps(us_paper), encoding="utf-8")
        (rg.JOURNAL_DIR / "equity_curve.jsonl").write_text(json.dumps(
            {"market": "US", "ts": _now(), "cash": 1000.0, "account_mode": "paper", "currency": "USD",
             "positions": [{"ticker": "AAPL", "eval_amt": 50_000.0}]}) + "\n", encoding="utf-8")
        kr = {"currency": "KRW", "cash": 10_000_000.0, "exchange_rate": 1400.0, "cash_shared": True, "positions": []}

        def pf_with(mode):
            lim2 = dict(lim, account_mode=mode)
            (tmp / "limits.json").write_text(json.dumps(lim2), encoding="utf-8")
            rg.LIMITS_PATH = tmp / "limits.json"
            return rg.portfolio_equity("KR", kr)
        pf_paper = pf_with("paper")
        if not pf_paper.get("ok") or "US" not in pf_paper.get("parts", {}):
            p.append(f"모의 run이 모의 반대편을 못 읽었다: {pf_paper.get('why')}")
        pf_real = pf_with("real")
        if pf_real.get("ok") or "다른 계좌 계열" not in str(pf_real.get("why")):
            p.append(f"실계좌 run이 모의 미국 보유를 반대편으로 더했다: ok={pf_real.get('ok')} {str(pf_real.get('why'))[:120]}")
        pfl = rg.pf_local(pf_real, "KR", kr)
        if pfl["invested_pct"] != 0.0 or pfl["equity"] != 10_000_000.0:
            p.append(f"실계좌 첫날 분모가 이 시장 자산이 아니다: {pfl}")
        us_real = dict(us_paper, account_mode="real",
                       balance={"currency": "USD", "cash": 7000.0, "exchange_rate": 1400.0, "positions": []})
        (rg.DATA_DIR / "snapshot_261015_us.json").write_text(json.dumps(us_real), encoding="utf-8")
        pf_real2 = pf_with("real")
        if not pf_real2.get("ok") or pf_real2["parts"]["US"].get("invested") != 0:
            p.append(f"실계좌 반대편 스냅샷을 쓰지 않았다: {pf_real2.get('why')}")
        if rg._latest_curve_row("US", "real") is not None or rg._latest_curve_row("US", "paper") is None:
            p.append("자산 곡선 행을 계좌 계열로 거르지 않는다")
    finally:
        rg.DATA_DIR, rg.JOURNAL_DIR, rg.LIMITS_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(14) 계좌 계열 필터 — 실계좌 run은 모의 반대편(스냅샷·자산 행)을 안 더한다 · 모의 run은 그대로", p)


def t15_market_share():
    """시장 몫 — 구조 검사(합 100·why) · 직전과 다르면 change.why · 실계좌(현금 공유)는 몫 없으면 거부 · gap = 목표 × 몫 × 자산 − 이 시장 투자."""
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rws_"))
    saved_j = al.JOURNAL_DIR
    try:
        al.JOURNAL_DIR = tmp
        base = {"target_invested_pct": 98, "regime": "테스트 20일 +1.0%", "reserved": [], "based_on": None}
        if al.validate(dict(base, market_share={"KR": 60, "US": 30, "why": "합이 90인 잘못된 몫"}), "AL-261015-kr") == []:
            p.append("합 100이 아닌 몫을 받았다")
        if al.validate(dict(base, market_share={"KR": 60, "US": 40, "why": ""}), "AL-261015-kr") == []:
            p.append("why 없는 몫을 받았다")
        ok_ms = {"KR": 60, "US": 40, "why": "국내 살아 있는 축 3 · 미국 2 — 테스트"}
        if al.validate(dict(base, market_share=ok_ms), "AL-261015-kr"):
            p.append(f"바른 몫을 거부했다: {al.validate(dict(base, market_share=ok_ms), 'AL-261015-kr')}")
        (tmp / "allocation.jsonl").write_text(json.dumps({"id": "AL-261014-us", "target_invested_pct": 98, "cash_reason": None,
                                                          "market_share": {"KR": 50, "US": 50, "why": "직전"}}) + "\n", encoding="utf-8")
        e = al.validate(dict(base, based_on="AL-261014-us", market_share=ok_ms), "AL-261015-kr")
        if not any("change.why" in x for x in e):
            p.append(f"직전 몫과 다른데 change.why 없이 받았다: {e}")
        (tmp / "allocation.jsonl").write_text(json.dumps({"id": "AL-261014-us", "target_invested_pct": 98, "cash_reason": None}) + "\n",
                                              encoding="utf-8")
        if al.validate(dict(base, based_on="AL-261014-us", market_share=ok_ms), "AL-261015-kr"):
            p.append("직전 행에 몫이 없는데(첫 판단) change.why를 요구했다")
    finally:
        al.JOURNAL_DIR = saved_j
        shutil.rmtree(tmp, ignore_errors=True)
    snap = _snap()
    snap["balance"]["cash"] = 1_000_000.0
    snap["balance"]["cash_shared"] = True
    sig = _signal()
    code, ap, out = _guard(sig, snap, limits_patch={"stop_loss_pct": -50.0})
    if "시장 몫" not in str(ap.get("_rejection")):
        p.append(f"실계좌(현금 공유)인데 시장 몫 없이 받았다: {str(ap.get('_rejection') or out[-200:])[:160]}")
    sig2 = _signal()
    sig2["allocation"]["market_share"] = {"KR": 60, "US": 40, "why": "국내 살아 있는 축 3 · 미국 2 — 테스트"}
    code2, ap2, out2 = _guard(sig2, snap, limits_patch={"stop_loss_pct": -50.0})
    a2 = ap2.get("allocation") or {}
    want = 1_800_000 * 0.98 * 0.60 - 800_000
    if code2 != 0 or abs(float(a2.get("gap_amt") or 0) - want) > 2:
        p.append(f"시장 몫 gap: rc={code2} gap={a2.get('gap_amt')} 기대 {want:,.0f} {ap2.get('_rejection') or ''}")
    if "74.1%" not in str((ap2.get("deficit") or {}).get("why")):
        p.append(f"미달 검사가 이 시장 몫 대비 투자비중(74.1%)으로 재지 않았다: {str((ap2.get('deficit') or {}).get('why'))[:90]}")
    paper = _snap()
    code3, ap3, _ = _guard(_signal(), paper, limits_patch={"stop_loss_pct": -50.0})
    if code3 != 0 or "_rejection" in ap3:
        p.append(f"모의(현금 공유 아님)가 시장 몫 없이 거부됐다: {ap3.get('_rejection')}")
    report("(15) 시장 몫 — 합 100·why · 직전과 다르면 change.why · 실계좌는 필수 · gap·미달은 이 시장 몫 기준 · 모의는 그대로", p)


def t16_feedback_reads_real_check():
    """토요일 피드백 수집이 이번 주 실전 점검표를 모아 항목별 ★ 수를 센다(창 밖 점검표는 뺀다)."""
    import feedback as fb
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwf2_"))
    saved = fb.HERE
    try:
        fb.HERE = tmp
        ev = tmp / "data" / "run_evidence"
        ev.mkdir(parents=True)
        body = ("# 실전 점검표\n\n[실전점검] {st} KR · 통과 10/12 · ★ ② 매도 뒤 재투자\n\n| 항목 | 판정 | 결과 |\n|---|---|---|\n"
                "| ② 매도 뒤 재투자 | ★ | 상태 open · 판 돈 {n} |\n| ③ 시각 | ✓ | 수집→전송 70분 |\n")
        (ev / "real_check_261015_kr.md").write_text(body.format(st="261015", n="1,000"), encoding="utf-8")
        (ev / "real_check_261016_us.md").write_text(body.format(st="261016", n="2,000"), encoding="utf-8")
        (ev / "real_check_260901_kr.md").write_text(body.format(st="260901", n="9"), encoding="utf-8")
        r = fb.real_checks("261017")
        it = r["items"].get("② 매도 뒤 재투자") or {}
        if len(r["runs"]) != 2 or it.get("star") != 2 or (r["items"].get("③ 시각") or {}).get("star") != 0:
            p.append(f"집계: runs={len(r['runs'])} {r['items']}")
        res = {"week": "261017", "days": 7, "threshold": 10.0, "at": "x", "markets": {"kr": {"movers": [], "below": [], "errors": [], "measured": 0},
               "us": {"movers": [], "below": [], "errors": [], "measured": 0}}, "early_sells": {"rows": [], "n_sells": 0},
               "late_sells": {"rows": [], "n_held": 0}, "due": [], "metrics": {}, "weekly_excerpt": "", "real_checks": r}
        try:
            md = fb.render_collect(res)
        except Exception as e:                        # noqa: BLE001 — 렌더의 다른 절(계좌 수익)이 실제 원장을 읽다 실패해도 이 절만 본다
            md = ""
            p.append(f"렌더 실패: {type(e).__name__}: {e}")
        if md and ("## 실전 준비" not in md or "| ② 매도 뒤 재투자 | 2 | 2 |" not in md):
            p.append("수집 보고서에 실전 준비 표가 없다")
    finally:
        fb.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(16) 토요일 피드백 — 이번 주 실전 점검표를 모아 항목별 ★ 수(창 밖 제외)", p)


def t17_status_needs_close_in_real():
    """실계좌 — 계좌 현황은 실전 점검표(close) 뒤에만 · 점검표가 거래 기록보다 오래됐으면 거부 · 모의는 그대로."""
    import account_status as A
    import kis_client
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwa_"))
    saved = (A.EVID_DIR, A.JOURNAL_DIR, kis_client.account_mode, sys.argv)
    try:
        A.EVID_DIR, A.JOURNAL_DIR = tmp / "ev", tmp / "j"
        A.EVID_DIR.mkdir()
        A.JOURNAL_DIR.mkdir()
        kis_client.account_mode = lambda cfg=None: "real"
        sys.argv = ["account_status.py", "--market", "kr", "--stamp", "261019"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc1 = A.main()
        if rc1 != 3:
            p.append(f"점검표 없이 찍었다: rc={rc1}")
        import os
        import time as _t
        (A.EVID_DIR / "real_check_261019_kr.md").write_text("[실전점검]", encoding="utf-8")
        tp = A.JOURNAL_DIR / "trades_261019_kr.json"
        tp.write_text("{}", encoding="utf-8")
        later = _t.time() + 5
        os.utime(tp, (later, later))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc2 = A.main()
        if rc2 != 3:
            p.append(f"거래 기록보다 오래된 점검표로 찍었다: rc={rc2}")
    finally:
        A.EVID_DIR, A.JOURNAL_DIR, kis_client.account_mode, sys.argv = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(17) 실계좌 계좌 현황 — 실전 점검표(close) 뒤에만 · 낡은 점검표 거부", p)


def t18_quote_bound_sources():
    """[AI] 줄은 인용과 함께 — 새 T1 사실은 인용·URL 없으면 등록 거부 · 옛 사실 재인용은 경고 · 노트 검사가 출처 행 품질을 본다 ·
    클레임 목록에 대조 줄(판정 기준 줄은 그대로)."""
    import srcledger as sl
    import note_lint as nl
    import extract_ai_claims as ex
    import types
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwq_"))
    saved = (sl.SHARED, sl.EVID)
    try:
        sl.SHARED, sl.EVID = tmp / "sources.json", tmp
        A = lambda **k: types.SimpleNamespace(**{**dict(stamp="261015", market="us", fact="", as_of="2026-10-15", source="s",
                                                       url="", quote="", tier=1, minor=False, batch=""), **k})
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc1 = sl.cmd_add(A(fact="CPI core +0.33% m/m", url="https://bls.gov/x"))
            rc2 = sl.cmd_add(A(fact="CPI core +0.33% m/m", url="https://bls.gov/x", quote="core index rose 0.3 percent in September"))
            rc3 = sl.cmd_add(A(fact="tool number", tier="data"))
            rc4 = sl.cmd_add(A(fact="minor fact", minor=True))
        if rc1 != 2 or rc2 != 0 or rc3 != 0 or rc4 != 0:
            p.append(f"등록 규칙: 인용 없음 {rc1}(2여야) · 인용 있음 {rc2} · data {rc3} · 참고 {rc4}")
        d = json.loads(sl.SHARED.read_text(encoding="utf-8"))
        old = {"id": "SRC-0900", "fact": "old fact", "url": "https://x/old", "tier": "1", "as_of": "2026-10-01",
               "retrieved": "2026-10-01 10:00", "runs": ["us:261001"]}
        d["sources"].append(old)
        sl.SHARED.write_text(json.dumps(d), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc5 = sl.cmd_add(A(fact="old fact", url="https://x/old"))
        if rc5 != 0:
            p.append("옛 사실 재인용을 거부했다(경고여야)")
        sh = json.loads(sl.SHARED.read_text(encoding="utf-8"))["sources"]
        new = next(r for r in sh if r["fact"].startswith("CPI"))
        new_noq = dict(new, id="SRC-0901", fact="new no quote", quote="", retrieved="2026-10-15 23:00")
        sh.append(new_noq)
        note = ("x [AI] CPI [S1]\ny [AI] old [S2]\nz [AI] bad [S3]\n\n## §12 출처 원장\n\n| S | 사실 | 원문 인용 | as-of | retrieved | written | 출처 |\n|---|---|---|---|---|---|---|\n"
                f"| S1 | CPI | q | 2026-10-15 | r | w | [s](u) (T1) · {new['id']} |\n| S2 | old | — | 2026-10-01 | r | w | [s](u) (T1) · SRC-0900 |\n"
                "| S3 | bad | — | 2026-10-15 | r | w | [s](u) (T1) · SRC-0901 |\n")
        errs, warns = nl.s_quality(note, sh)
        if not errs or "S3" not in errs[0] or "S1" in errs[0] or not warns or "S2" not in warns[0]:
            p.append(f"노트 출처 행 품질: errs={errs} warns={warns}")
        src = ex.source_lines(note, sh)
        if "core index rose 0.3 percent" not in src.get("S1", "") or "원문 인용 없음" not in src.get("S3", ""):
            p.append(f"대조 줄 내용: {src}")
        lines = ex.with_sources(["1. (L1) CPI rose [AI] [S1]", "2. (L2) other"], {"S1": "   ↳ 대조 S1 · u"})
        if lines != ["1. (L1) CPI rose [AI] [S1]", "   ↳ 대조 S1 · u", "2. (L2) other"]:
            p.append(f"대조 줄 배치: {lines}")
        import re as _re
        if any(_re.match(r"^\d+\.\s", l) for l in lines if l.startswith("   ↳")):
            p.append("대조 줄이 판정 기준 줄로 읽힌다")
    finally:
        sl.SHARED, sl.EVID = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(18) 인용 결속 — 새 T1 사실은 인용·URL 필수 · 옛 사실 재인용 경고 · 노트가 출처 행 품질을 본다 · 클레임 대조 줄", p)


def t19_fetch_keeps_raw():
    """fetch.py — 받은 본문 전체를 data/_raw_fc/에 보관하고 접속 기록에 raw_path(판정표 원문 대조의 입력) · 실패는 보관 안 함."""
    import fetch as fe
    import ingest
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwr2_"))
    saved = (fe.HERE, fe.RAW_DIR, fe.FETCH_LOG, ingest._fetch_text_meta)
    try:
        fe.HERE, fe.RAW_DIR, fe.FETCH_LOG = tmp, tmp / "data" / "_raw_fc", tmp / "journal" / "fetch_log.jsonl"
        long = "본문 " * 15000
        ingest._fetch_text_meta = lambda url: (long if "ok" in url else "", "" if "ok" in url else "403", {"kind": "html"})
        b, e = fe.fetch("https://x.example/ok", max_chars=100)
        fe.fetch("https://x.example/blocked")
        rows = [json.loads(l) for l in fe.FETCH_LOG.read_text(encoding="utf-8").splitlines()]
        rp = rows[0].get("raw_path")
        if len(b) != 100 or not rp or len((tmp / rp).read_text(encoding="utf-8")) != len(long):
            p.append(f"보관: 출력 {len(b)}자 · raw_path={rp}")
        if rows[1].get("raw_path"):
            p.append("실패한 열기도 보관했다")
        if "full" in rows[0]:
            p.append("접속 기록에 본문이 실렸다")
    finally:
        fe.HERE, fe.RAW_DIR, fe.FETCH_LOG, ingest._fetch_text_meta = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(19) fetch.py 원문 보관 — 전체 본문을 data/_raw_fc/ · 접속 기록 raw_path · 실패는 보관 안 함", p)


def t8_balance_for():
    """잔고 함수 하나 — 미국은 현금·환율·현금 공유 칸이 있다(매도 전용 pass도 같은 것)."""
    import kis_client as kc
    p = []

    class Fake:
        tr = {"ov_psamount": "X"}

        def domestic_balance(self):
            return {"currency": "KRW", "cash": 1000.0, "positions": []}

        def overseas_balance(self, *a, **k):
            return {"currency": "USD", "positions": [{"ticker": "AAPL", "qty": 1}]}

        def overseas_price(self, *a, **k):
            return {"price": 200.0}

        def overseas_orderable(self, *a, **k):
            return {"orderable_cash": 500.0, "field": "frcr_ord_psbl_amt1", "exchange_rate": 1340.0, "shared": True}

        def overseas_present_balance(self):
            return {"cash": 0.0}

    us, err = kc.balance_for(Fake(), "US", {"US": [{"ticker": "AAPL", "excd": "NASD"}]})
    if err or us.get("cash") != 500.0 or us.get("exchange_rate") != 1340.0 or not us.get("cash_shared"):
        p.append(f"미국 잔고: {us} {err}")
    kr, _ = kc.balance_for(Fake(), "KR", {}, with_fx=False)
    if "exchange_rate" in kr:
        p.append("with_fx=False인데 환율을 붙였다")
    kr2, _ = kc.balance_for(Fake(), "KR", {"US": [{"ticker": "AAPL", "excd": "NASD"}]})
    if kr2.get("exchange_rate") != 1340.0 or not kr2.get("exchange_rate_at"):
        p.append(f"국내 잔고 환율: {kr2}")
    report("(8) balance_for — 미국 현금·환율·현금 공유 · 국내 환율 선택", p)


def t9_slots():
    import sessions as ss
    p = []
    want = {("kr", date(2026, 10, 15)): "09:30", ("us", date(2026, 10, 15)): "22:35",
            ("us", date(2026, 10, 30)): "22:35", ("us", date(2026, 11, 2)): "23:35"}
    for (m, d), hm in want.items():
        got = ss.slot_for(m, d).strftime("%H:%M")
        if got != hm:
            p.append(f"{m} {d} → {got} (기대 {hm})")
    v, _ = ss.slot_verdict("us", date(2026, 11, 3), datetime(2026, 11, 3, 23, 40, tzinfo=KST))
    if v != "준수":
        p.append("서머타임 뒤 23:40 미국 run이 이탈로 나온다")
    report("(9) 슬롯 — 국내 09:30 · 미국 개장 5분 뒤(11-02부터 23:35)", p)


def t10_real_check():
    import real_check as rc
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rwc_"))
    saved = (rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS, rc.LEDGER)
    try:
        rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS = tmp / "ev", tmp / "j", tmp / "s", tmp / "a"
        rc.LEDGER = tmp / "ledger.jsonl"
        for d in (rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS):
            d.mkdir()
        (rc.JOURNAL / "refill_261015_kr.json").write_text(json.dumps({"status": "open", "events": [{"proceeds": 5}]}),
                                                         encoding="utf-8")
        (rc.JOURNAL / "push_log.jsonl").write_text(json.dumps({"stamp": "261015", "market": "kr", "sent": True,
                                                               "at": "2026-10-15T11:00"}) + "\n", encoding="utf-8")
        (rc.EVID / "real_shadow_261015_kr.md").write_text(
            "## 그림자 점검 — x\n\n- risk_guard 종료코드 0 · 승인 매수 2건 · 상한 초과 0건 · 미달 아니오\n", encoding="utf-8")
        head, md = rc.build("261015", "kr")
        rows = {ln.split("|")[1].strip(): ln.split("|")[2].strip() for ln in md.splitlines() if ln.startswith("| ") and "항목" not in ln}
        if rows.get("② 매도 뒤 재투자") != "★":
            p.append(f"재투자 open이 ★가 아니다: {rows.get('② 매도 뒤 재투자')}")
        if rows.get("⑩ 보고 전달") != "✓" or rows.get("① 실계좌라면 샀을까") != "✓":
            p.append(f"푸시·그림자: {rows}")
        if not head.startswith("[실전점검] 261015 KR") or "② 매도 뒤 재투자" not in head:
            p.append(f"머리줄: {head}")
        if md.index("② 매도 뒤 재투자") > md.index("① 실계좌라면 샀을까"):
            p.append("★ 줄이 맨 앞에 오지 않았다")
    finally:
        rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS, rc.LEDGER = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(10) 실전 점검표 — ★ 줄이 맨 앞 · 재투자 open ★ · 그림자·푸시 판정", p)


for fn in (t1_reserved_exclusions, t2_after_sells, t3_same_run_sell_refills, t4_discipline_refill_need, t5_real_one_share,
           t6_gapfill_topup_and_deficit, t7_record_refill_and_gate, t8_balance_for, t9_slots, t10_real_check,
           t11_refill_mode, t12_refill_script, t13_track1_no_reextract,
           t14_mode_filter_other_market, t15_market_share, t16_feedback_reads_real_check,
           t17_status_needs_close_in_real, t18_quote_bound_sources,
           t19_fetch_keeps_raw):
    _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_realweek.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
