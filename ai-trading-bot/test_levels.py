#!/usr/bin/env python3
"""
지지 구간·손절선 규칙 테스트 — `price_levels.py`(구간 lo·hi) · `theses.py`(구간 다리 규칙 · 손절 내리기 금지).

왜(2026-10-13 차트스쿨 01 반영 · 사용자 승인): 지지는 평균 한 값이 아니라 반응한 저가들의 **구간**이고, 손절은 구간 아랫선 아래에,
진입은 구간 안에 건다. 가격이 빠졌다고 손절선을 내리는 것은 기계가 거부한다(정당한 재설정은 --allow-loosen). 실제 원장·시세는 건드리지 않는다.
원장은 저장소(SQLite)다 — 테스트 전체가 임시 저장소(`testkit.db_case`) 안에서 돌고, 논지는 행으로 심는다.

    python3 test_levels.py          # run_tests.py가 부른다
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import db                   # noqa: E402
import price_levels as pl   # noqa: E402
import theses as th         # noqa: E402
from testkit import db_case, seed  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []

import atexit  # noqa: E402
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_levels.py ran={n} failed={nf} last={_STATE['current']}")
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
        print(f"[{FAIL}] {fn.__name__} — 중단({type(e).__name__}: {str(e)[:160]})")


def report(label, problems):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


def _rows():
    """최신 행이 앞. 저가 99,000·99,400·98,600 근처에서 세 번 되돌아선 가상 일봉 40개(현재가 103,000)."""
    dates = [(10, x) for x in range(28, 0, -1)] + [(9, x) for x in range(30, 17, -1)]
    # 최신 행이 앞 · 평소 저가는 뒤로 갈수록 조금씩 높아 스윙 저점이 되지 않는다(저가가 전부 같으면 모든 봉이 스윙 저점이 된다)
    base = [{"date": f"2026{m:02d}{d:02d}", "open": 102000, "high": 104000 + i * 20, "low": 101000 + i * 20, "close": 103000, "volume": 1000}
            for i, (m, d) in enumerate(dates)][:40]
    for i, low in ((5, 99000), (15, 99400), (25, 98600)):      # 스윙 저점 셋 — 앞뒤 3봉보다 낮다
        base[i] = {**base[i], "low": low, "close": 100500}
    return base


def t1_zone_lo_hi():
    p = []
    sr = pl.support_resistance(_rows())
    s1 = sr.get("support_1") or {}
    if (s1.get("lo"), s1.get("hi")) != (98600.0, 99400.0):
        p.append(f"구간 lo·hi가 세 저가의 최저·최고여야: {s1}")
    if "구간 98,600~99,400" not in str(s1.get("basis")):
        p.append(f"basis 문구에 구간이 없다: {s1.get('basis')}")
    if s1.get("price") != 99000.0:
        p.append(f"평균값(price)은 그대로 남아야 한다(기존 소비자 호환): {s1.get('price')}")
    report("(1) price_levels — 지지 구간의 위·아래 끝과 basis 문구", p)


def t2_leg_rules_zone():
    p = []
    t = {"id": "x", "entry_triggers": [], "exit_triggers": []}
    zb = "support_1 99,000 — 스윙 저점 3회 · 구간 98,600~99,400"
    r = th.leg_problems(t, {"id": "x2", "check": "price <= 99000", "basis": zb}, "exit")
    if not any("구간" in x for x in r):
        p.append("구간 안에 건 손절을 안 잡았다")
    r = th.leg_problems(t, {"id": "x2", "check": "price <= 98500", "basis": zb}, "exit")
    if any("구간" in x for x in r):
        p.append(f"구간 아랫선 아래의 손절을 잡았다: {r}")
    r = th.leg_problems(t, {"id": "e1", "check": "price <= 99300", "basis": zb, "reach_prob": 0.5, "reach_n": 10}, "entry")
    if any("구간" in x for x in r):
        p.append(f"구간 안 진입을 잡았다: {r}")
    r = th.leg_problems(t, {"id": "e1", "check": "price <= 98000", "basis": zb, "reach_prob": 0.5, "reach_n": 10}, "entry")
    if not any("구간" in x for x in r):
        p.append("구간 아래 진입을 안 잡았다")
    r = th.leg_problems(t, {"id": "x2", "check": "price <= 99000", "basis": "support_1 99,000 — 스윙 저점 2회(옛 표기)"}, "exit")
    if any("구간" in x for x in r):
        p.append("구간 표기 없는 옛 다리에 새 규칙을 적용했다")
    report("(2) theses.leg_problems — 손절은 구간 아래 · 진입은 구간 안 · 옛 다리는 그대로", p)


def t3_set_trigger_refuses_loosening():
    p = []
    with db_case():                                   # 논지 원장 = 저장소 행(jd_thesis) — 임시 저장소에 심는다
        seed(theses=[{"id": "t1", "ticker": "AAA", "market": "KR", "status": "held",
                      "exit_triggers": [{"id": "x1", "check": "price <= 100", "basis": "low_20 100", "size_pct": 100}]}])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                th.cmd_set_trigger("t1", "x1", "price <= 95", "low_20 95", "빠져서 내림")
                p.append("손절 내리기를 거부하지 않았다")
            except SystemExit as e:
                if "거부" not in str(e):
                    p.append(f"거부 사유가 다르다: {e}")
            try:
                th.cmd_set_trigger("t1", "x1", "price <= 105", "low_20 105", "올림")
            except SystemExit as e:
                p.append(f"손절 올리기를 막았다: {e}")
            try:
                th.cmd_set_trigger("t1", "x1", "price <= 95", "low_20 95 (과거 고정 기준으로 정정)", "현재가 의존 기준 정정", allow_loosen=True)
            except SystemExit as e:
                p.append(f"--allow-loosen인데 막았다: {e}")
        d = db.thesis_get("t1")
        if d["exit_triggers"][0]["check"] != "price <= 95":
            p.append(f"허용된 내리기가 반영되지 않았다: {d['exit_triggers'][0]}")
        lo = [c for c in d.get("corrections") or [] if c.get("loosened")]
        if len(lo) != 1:
            p.append(f"내린 교정에 loosened 표시가 하나여야: {d.get('corrections')}")
    report("(3) theses.set-trigger — 손절 내리기 거부 · 올리기 허용 · --allow-loosen이면 loosened 표시", p)


def t4_last_close():
    """직전 세션 종가 — 장중이면 전일 종가 칸(없으면 되짚기), 장 밖이면 현재가."""
    import support_break as sb
    p = []
    intraday_kr = "2026-10-14T10:40:00+09:00"
    after_kr = "2026-10-14T16:10:00+09:00"
    intraday_us = "2026-10-13T23:30:00+09:00"
    c, how = sb.last_close({"price": 234000, "change_pct": -6.59, "prev_close": 250500}, "KR", intraday_kr)
    if c != 250500 or "전일 종가" not in how:
        p.append(f"국내 장중 · 전일 종가 칸: {c} {how}")
    c, how = sb.last_close({"price": 234000, "change_pct": -6.59}, "KR", intraday_kr)
    if c != 250500 or "되짚기" not in how:
        p.append(f"국내 장중 · 옛 스냅샷 되짚기(호가 반올림): {c} {how}")
    c, how = sb.last_close({"price": 234000, "change_pct": -6.59, "prev_close": 250500}, "KR", after_kr)
    if c != 234000:
        p.append(f"국내 장 밖이면 현재가가 직전 종가: {c} {how}")
    c, how = sb.last_close({"price": 343.22, "change_pct": 0.66, "prev_close": 340.97}, "US", intraday_us)
    if c != 340.97:
        p.append(f"미국 장중 · base: {c} {how}")
    c, how = sb.last_close({"price": 343.22, "change_pct": 0.66}, "US", "")
    if c is not None:
        p.append("캡처 시각이 없으면 재지 않는다")
    report("(4) support_break.last_close — 장중은 전일 종가(칸·되짚기) · 장 밖은 현재가", p)


def _held(tid, tk, basis, market="KR", status="held", kind="entry_triggers"):
    t = {"id": tid, "ticker": tk, "name": tk, "market": market, "status": status, "entry_triggers": [], "exit_triggers": []}
    t[kind] = [{"id": "e1" if kind == "entry_triggers" else "x2", "check": "price <= 99300", "basis": basis}]
    return t


def t5_breaks_and_carry():
    """보유 + 구간 다리 + 종가 < lo 만 이탈 · 구간 안·옛 표기·미보유·다른 시장은 아니다 · 이어받기 절 · 매수 거부."""
    import support_break as sb
    import risk_guard as rg
    p = []
    zb = "support_1 99,000 — 스윙 저점 3회 · 구간 98,600~99,400"
    rows = [_held("t-brk", "AAA", zb), _held("t-in", "BBB", zb), _held("t-old", "CCC", "support_1 99,000 — 스윙 저점 2회"),
            _held("t-arm", "DDD", zb, status="armed"), _held("t-us", "EEE", zb, market="US"),
            _held("t-x", "FFF", zb, kind="exit_triggers")]
    snap = {"generated_at": "2026-10-14T16:10:00+09:00",       # 장 밖 — 현재가 = 직전 종가
            "prices": {"AAA": {"price": 98000}, "BBB": {"price": 99000}, "CCC": {"price": 90000},
                       "DDD": {"price": 90000}, "EEE": {"price": 90000}, "FFF": {"price": 98500}}}
    broken, unmeasured, n = sb.scan("kr", snap, rows)
    got = sorted(r["ticker"] for r in broken)
    if got != ["AAA", "FFF"]:
        p.append(f"이탈은 AAA(진입 구간)·FFF(손절 다리 구간)만이어야: {got}")
    if n != 3:
        p.append(f"구간 다리를 가진 국내 보유 논지는 3개(AAA·BBB·FFF): {n}")
    tmp = Path(tempfile.mkdtemp(prefix="sbtest_"))
    try:
        sp = tmp / "snapshot_261014_kr.json"
        sp.write_text(json.dumps(snap), encoding="utf-8")
        md = sb.carry_block("kr", sp, rows)
        if not md.startswith("## 지지 이탈") or "| t-brk | AAA" not in md or "BBB" in md.split("|---|")[-1]:
            p.append(f"이어받기 절: {md[:300]}")
        md0 = sb.carry_block("kr", sp, [rows[1]])
        if "없음" not in md0:
            p.append(f"이탈 0건이면 '없음': {md0}")
        md_none = sb.carry_block("kr", sp, [rows[2]])
        if "보유 논지 없음" not in md_none:
            p.append(f"구간 다리 논지 0개: {md_none}")
        sp2 = tmp / "snapshot_intraday.json"
        sp2.write_text(json.dumps({"generated_at": "2026-10-14T10:40:00+09:00", "prices": {"AAA": {"price": 98000}}}), encoding="utf-8")
        if "종가를 못 잰" not in sb.carry_block("kr", sp2, [rows[0]]):
            p.append("장중인데 전일 종가·등락률이 없으면 '못 잰' 줄")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # risk_guard — 이탈 종목 매수는 거부, 다른 종목은 이 검사로 막지 않는다
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim["scenario_cond_required"] = False            # 형식 적용일은 그 형식을 재는 시험이 켠다
    wl = {"KR": [{"ticker": "AAA", "name": "Alpha"}, {"ticker": "BBB", "name": "Beta"}]}
    bal = {"cash": 100_000_000, "currency": "KRW", "positions": []}
    sig = {"market": "KR", "proposals": [{"ticker": t, "name": t, "action": "BUY", "weight_target_pct": 3.0, "confidence": 0.7,
                                          "size_why": "t", "expected_upside_pct": 30.0,
                                          "evidence": [{"claim": "c", "source_url": "https://x.example/a", "verdict": "MATCH", "tier": "T1"}]}
                                         for t in ("AAA", "BBB")]}
    saved_pe = rg.portfolio_equity
    rg.portfolio_equity = lambda m, b: {"krw": 1e8, "cash_krw": 1e8, "invested_krw": 0.0, "ok": True,
                                        "asof": "t", "fx": 1.0, "parts": {}, "why": "t"}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _acc, rej = rg.screen_proposals(sig, lim, wl, bal, {"AAA": {"price": 98000.0}, "BBB": {"price": 99000.0}},
                                            sup_break=sb.ticker_map("kr", snap, rows))
    finally:
        rg.portfolio_equity = saved_pe
    why = {r["ticker"]: r["why"] for r in rej}
    if "지지 구간 종가 이탈" not in why.get("AAA", ""):
        p.append(f"이탈 종목 매수를 거부하지 않았다: {why.get('AAA')}")
    if "지지 구간 종가 이탈" in why.get("BBB", ""):
        p.append("이탈 아닌 종목을 이 사유로 거부했다")
    report("(5) support_break.scan·carry_block — 보유·구간 다리·종가<lo만 이탈 · 이어받기 절 · risk_guard 추가 매수 거부", p)


def t6_crosscheck_support_break():
    """crosscheck — 이어받기 `## 지지 이탈` 종목마다 노트 §6에 `[지지 이탈]` 줄이 있어야 통과."""
    import crosscheck as cc
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="cctest_"))
    saved = cc.HERE
    try:
        cc.HERE = tmp
        (tmp / "journal").mkdir()
        cf = tmp / "journal" / "carry_261014_kr.md"
        cf.write_text("# 이어받기\n\n## 감사 소급\n\n(없음)\n\n## 지지 이탈\n\n설명\n\n| 논지 | 종목 | 구간 | 직전 종가 | 어떻게 쟀나 | 오늘 판정할 것 |\n"
                      "|---|---|---|---|---|---|\n| t-brk | AAA Alpha | 98,600.00~99,400.00(다리 e1) | 98,000.00 | 장 밖 | 무효 조건 재판정 |\n\n"
                      "## 미체결분 행선지\n\n- \n", encoding="utf-8")
        bad = "## §6 종목\n\n| AAA | 보유 |\n\n## §7 시나리오\n"
        good = "## §6 종목\n\n- [지지 이탈] AAA — 무효 조건 ① 미충족 · 유지(구간 회복 대기)\n\n## §7 시나리오\n"
        if cc.check_support_break("kr", "261014", bad)[0][1]:
            p.append("[지지 이탈] 줄이 없는데 통과했다")
        if not cc.check_support_break("kr", "261014", good)[0][1]:
            p.append(f"[지지 이탈] 줄이 있는데 막혔다: {cc.check_support_break('kr', '261014', good)}")
        cf.write_text("# 이어받기\n\n## 지지 이탈\n\n(지지 구간(`구간 lo~hi`)을 기준으로 가진 보유 논지 없음)\n\n## 미체결분 행선지\n", encoding="utf-8")
        if not cc.check_support_break("kr", "261014", bad)[0][1]:
            p.append("이탈 0건인데 막혔다")
        cf.write_text("# 이어받기\n\n## 미체결분 행선지\n", encoding="utf-8")
        if cc.check_support_break("kr", "261014", good)[0][1]:
            p.append("절이 없는 이어받기를 통과시켰다")
    finally:
        cc.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(6) crosscheck.check_support_break — 이탈 종목마다 §6 [지지 이탈] 재판정 줄 · 절 누락은 실패", p)


with db_case():                                       # 어느 구간도 실제 journal/trading.db를 열지 않는다
    for fn in (t1_zone_lo_hi, t2_leg_rules_zone, t3_set_trigger_refuses_loosening, t4_last_close, t5_breaks_and_carry,
               t6_crosscheck_support_break):
        _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_levels.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
