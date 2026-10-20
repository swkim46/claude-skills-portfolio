#!/usr/bin/env python3
"""
계산·보고 묶음 회귀 — 원장이 저장소(SQLite)로 옮겨진 뒤 피드백·아쉬움·손실 판정·실전 점검·세금·일봉 캐시가 표를 읽고 쓰는가.

왜(2026-10-17 Phase 3 저장소 이전): 옛 테스트는 원장 파일을 임시 폴더에 심었다 — 저장소로 바뀐 뒤 그 방식은 조용히 실패하고
실제 데이터로 통과하는 사고가 된다. 그래서 전 구간이 임시 저장소(`testkit.db_case`) 안에서 돌고, 필요한 행을 직접 넣는다.
네트워크 없음 — 브로커는 호출 수를 세는 가짜 클라이언트다.

    python3 test_reports.py          # run_tests.py가 부를 수 있다

구간
  (1) feedback run 시간 지표 넷 — 행이 없으면 None · 7일 창 · 적용 후 재측정 · propose add가 새 지표를 받는다
  (2) feedback 원장 — propose add·set이 op_feedback 행을 쓰고 고친다
  (3) regret 아쉬움 기록 — op_regret 적재·기간 거르기·주간 집계
  (4) loss_review — 손실 판정 파일 → op_loss_review(멱등) · 고점·축은 저장소에서
  (5) real_check — 12항목 → op_real_check · feedback이 표에서 같은 머리줄을 만든다
  (6) tax_ledger.fill_fx — 거래 기록 파일과 ex_fill_fx 두 곳에 결제일 환율
  (7) 일봉 캐시 — 같은 run에서 가격 수준·기저율·놓친 급등이 같은 종목 일봉을 브로커에 한 번만 묻는다 · 반환은 원래 구간 그대로
  (8) 계산 결과 행 — 가격 수준(co_level_calc)·기저율(co_base_rate)
  (9) 보고 읽기 — review·dashboard·costs·support_break가 자산 곡선·run 기록·벤치마크·논지를 저장소에서 · real_probe 잔고 스냅샷
"""
import contextlib
import io
import json
import math
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import db                   # noqa: E402
from testkit import db_case, seed  # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []

import atexit  # noqa: E402
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_reports.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn):
    _STATE["current"] = fn.__name__
    try:
        with db_case():                              # 구간마다 빈 저장소
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


def _stamp(days_ago: int) -> str:
    return (datetime.now(KST) - timedelta(days=days_ago)).strftime("%y%m%d")


def _iso(days_ago: int, hm: str) -> str:
    d = (datetime.now(KST) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return f"{d}T{hm}:00+09:00"


# ───────────────────────────────────────────── (1)(2) feedback

RUN_KEYS = ("post_fill_minutes_avg_7d", "capture_to_send_minutes_avg_7d", "before_news_facts_7d", "material_repeat_pct_7d")


def _seed_runs():
    a, b, c, old = _stamp(2), _stamp(1), _stamp(1), _stamp(30)
    db.run_set(a, "kr", capture_at=_iso(2, "09:00"), sent_at=_iso(2, "10:00"), filled_at=_iso(2, "10:10"),
               closed_at=_iso(2, "10:40"), before_news_n=2, facts_repeat=3, facts_new=7)
    db.run_set(b, "us", capture_at=_iso(1, "22:00"), sent_at=_iso(1, "23:30"), filled_at=_iso(1, "23:40"),
               closed_at=_iso(0, "00:00"), before_news_n=1, facts_repeat=1, facts_new=9)
    db.run_set(c, "kr", capture_at=_iso(1, "09:00"))                    # 반쪽 run — 시각·사실 칸이 비어 있다(평균에 안 들어간다)
    db.run_set(old, "kr", capture_at=_iso(30, "09:00"), sent_at=_iso(29, "09:00"), filled_at=_iso(30, "10:00"),
               closed_at=_iso(29, "10:00"), before_news_n=50, facts_repeat=90, facts_new=10)   # 7일 창 밖
    return a, b


def t1_feedback_run_metrics():
    import feedback as fb
    p = []
    empty = fb.run_metrics(_stamp(7))
    if any(empty[k] is not None for k in RUN_KEYS):
        p.append(f"run 행이 없는데 None이 아니다(0으로 읽으면 '잘 됐다'로 오독): {empty}")
    a, b = _seed_runs()
    rm = fb.run_metrics(_stamp(7))
    want = {"post_fill_minutes_avg_7d": 25.0, "capture_to_send_minutes_avg_7d": 75.0, "before_news_facts_7d": 3,
            "material_repeat_pct_7d": 20.0}
    for k, v in want.items():
        if rm[k] is None or abs(float(rm[k]) - v) > 0.05:
            p.append(f"{k} {rm[k]} ≠ {v}")
    saved = fb.HERE
    tmp = Path(tempfile.mkdtemp(prefix="fbm_"))
    try:
        fb.HERE = tmp                                   # 거래 기록·스냅샷 glob은 빈 폴더 — 저장소 지표만 본다
        with contextlib.redirect_stderr(io.StringIO()):
            m = fb.metrics()
    finally:
        fb.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    for k, v in want.items():
        if k not in m or m[k] is None or abs(float(m[k]) - v) > 0.05:
            p.append(f"metrics()[{k}] {m.get(k)} ≠ {v}")
    if m.get("watch_loop_checks_7d") is not None:
        p.append(f"장중 감시 기록이 하나도 없는데 watch_loop_checks_7d={m.get('watch_loop_checks_7d')} (None이어야)")
    v, n, _ = fb.post_metric("post_fill_minutes_avg_7d", b)
    if v != 20.0 or n != 1:
        p.append(f"적용 후 재측정(post_metric) — 적용일 {b} 이후 run 하나(20분)만: {v} n={n}")
    if not all(k in fb.POST_METRICS for k in RUN_KEYS):
        p.append("새 지표가 POST_METRICS에 없다 — verify_pre_mixed_rows로 잘못 센다")
    row = {"kind": "tool", "verdict": "defect", "evidence": [{"ticker": "AAA", "name": "Alpha"}], "cause": "체결 뒤 마감 절차가 길다",
           "change": {"file": "stage.py", "section": "close", "summary": "마감 검사 병렬화"},
           "expected": {"metric": "post_fill_minutes_avg_7d", "baseline": 94, "target": 20}, "next_check": {"date": "2026-10-24"}}
    errs = fb.validate(row)
    if errs:
        p.append(f"propose add가 새 지표를 거부했다: {errs}")
    for k in RUN_KEYS:
        if any("expected.metric" in e for e in fb.validate({**row, "expected": {"metric": k, "baseline": 1, "target": 0}})):
            p.append(f"{k}가 expected.metric으로 안 받아진다")
    report("(1) feedback run 시간 지표 — 행 없으면 None · 7일 창 평균·합·비율 · 적용 후 재측정 · propose add가 받는다", p)


def t2_feedback_ledger():
    import feedback as fb
    p = []
    real = HERE / "journal" / "feedback.jsonl"            # 실제 렌더본 — 테스트 전후 글자가 같아야 한다(같은 주 id가 실제 원장에 있어도 된다)
    real_before = real.read_bytes() if real.exists() else None
    tmp = Path(tempfile.mkdtemp(prefix="fbl_"))
    try:
        f = tmp / "prop.json"
        f.write_text(json.dumps([
            {"week": "261017", "kind": "tool", "verdict": "defect", "evidence": [{"ticker": "AAA", "name": "Alpha"}],
             "cause": "체결 뒤 마감이 길다", "change": {"file": "stage.py", "section": "close", "summary": "마감 병렬화"},
             "expected": {"metric": "capture_to_send_minutes_avg_7d", "baseline": 120, "target": 60},
             "next_check": {"date": "2026-10-24"}},
            {"week": "261017", "kind": "rule", "verdict": "right_call", "evidence": [{"ticker": "BBB"}], "cause": "기저율 거부가 맞았다"},
            {"week": "261017", "kind": "rule", "verdict": "luck", "evidence": [{"ticker": "CCC", "name": "Gamma"}],
             "cause": "Gamma가 운 좋게 올랐다"}],                     # 사례 문장 — 거부돼야 한다
            ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = fb.cmd_propose_add(str(f), "")
        rows = db.log_rows("feedback")
        if rc != 1 or [r["id"] for r in rows] != ["FB-261017-1", "FB-261017-2"]:
            p.append(f"propose add: rc={rc} rows={[r.get('id') for r in rows]}")
        if rows and (rows[0].get("expected") or {}).get("metric") != "capture_to_send_minutes_avg_7d":
            p.append(f"새 지표가 원장 행에 안 남았다: {rows[0].get('expected')}")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc2 = fb.cmd_propose_set("FB-261017-2", "rejected", "기록만")
            rc3 = fb.cmd_propose_set("FB-261017-1", "", "", next_check="2026-10-31")
        rows = {r["id"]: r for r in db.log_rows("feedback")}
        if rc2 != 0 or rows.get("FB-261017-2", {}).get("status") != "rejected" or len(rows) != 2:
            p.append(f"propose set(상태): rc={rc2} {rows.get('FB-261017-2', {}).get('status')} 행 {len(rows)}")
        if rc3 != 0 or (rows.get("FB-261017-1", {}).get("next_check") or {}).get("date") != "2026-10-31":
            p.append(f"propose set(검증일): rc={rc3} {rows.get('FB-261017-1', {}).get('next_check')}")
        if "원장 2행" not in fb.status_line():
            p.append(f"status_line: {fb.status_line()}")
        if (real.read_bytes() if real.exists() else None) != real_before:
            p.append("테스트 행이 실제 journal/feedback.jsonl 렌더본에 들어갔다")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report("(2) feedback 원장 — propose add(거부 포함)·set이 op_feedback 행을 쓰고 고친다 · 실제 렌더본은 안 건드린다", p)


# ───────────────────────────────────────────── (3) regret

def t3_regret_log():
    import regret as rg
    p = []
    row = {"stamp": "261016", "market": "kr", "ticker": "A", "name": "에이", "ret": 14.0, "axis": True, "forecast": True,
           "thesis": False, "thesis_live": False, "position": False, "blockers": [{"type": "no_thesis"}], "blocked": "논지 없음"}
    rg.record([row, {**row, "stamp": "261010", "ticker": "B", "ret": 9.0}])
    got = db.log_rows("regret")
    if len(got) != 2 or got[0]["ticker"] != "A":
        p.append(f"op_regret 적재: {got}")
    if [r["ticker"] for r in rg.load_ledger("261015")] != ["A"] or rg.load_ledger("261017"):
        p.append("load_ledger 기간 거르기")
    w = rg.weekly(7000)
    if "급등 2종목" not in w or "| 논지 없음 | 2 |" not in w:
        p.append(f"주간 집계가 저장소 기록을 안 읽었다: {w[:200]}")
    try:
        db.log_replace("regret", [])
        p.append("아쉬움 기록이 통째 다시 쓰기를 허용했다(추가만이어야)")
    except RuntimeError:
        pass
    report("(3) regret — 아쉬움 기록 op_regret 적재(추가만)·기간 거르기·주간 집계", p)


# ───────────────────────────────────────────── (4) loss_review

def t4_loss_review():
    import loss_review as lr
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="lrv_"))
    saved = lr.OUTDIR
    try:
        lr.OUTDIR = tmp
        (tmp / "손실판정_261012.json").write_text(json.dumps({"week": "261012", "rows": [
            {"thesis_id": "t-a", "ticker": "AAA", "market": "KR", "kind": "closed", "verdict": "오판", "why": "동인 불일치", "pnl": -10},
            {"thesis_id": "t-b", "ticker": "BBB", "market": "KR", "kind": "held_weak", "verdict": "일시적", "why": "가격만", "pnl": -5},
            {"thesis_id": "t-c", "ticker": "CEG", "market": "US", "kind": "closed", "verdict": "관찰", "why": "엇갈림", "pnl": -1},
            {"thesis_id": "t-c2", "ticker": "CEG", "market": "US", "kind": "held_weak", "verdict": "오판", "why": "재진입 논지",
             "pnl": -2}]},                                              # 같은 주 한 종목 논지 둘 — 행도 둘이어야
            ensure_ascii=False), encoding="utf-8")
        n1, n2 = lr.sync_verdicts(), lr.sync_verdicts()
        rows = db.q("SELECT market, ticker, thesis_id, verdict, basis FROM op_loss_review WHERE week='261012' ORDER BY thesis_id")
        if n1 != 4 or len(rows) != 4 or rows[0]["verdict"] != "오판" or rows[0]["basis"] != "동인 불일치" or rows[2]["market"] != "us":
            p.append(f"손실 판정 → op_loss_review(같은 종목 논지 둘 포함): n={n1} {rows}")
        if n2 != 4 or db.q1("SELECT COUNT(*) AS n FROM op_loss_review")["n"] != 4:
            p.append("두 번 옮기면 행이 늘었다(멱등이어야)")
        if lr.misjudged_rate("261017") != 50.0 or lr.misjudged_rate("261011") is not None:
            p.append(f"오판 비율(표에서 · 그 주 이전 판정만): {lr.misjudged_rate('261017')} · {lr.misjudged_rate('261011')}")
        seed(theses=[{"id": "t-b", "ticker": "BBB", "market": "KR", "status": "closed"},
                     {"id": "t-c2", "ticker": "CEG", "market": "US", "status": "held"}])
        fl = lr.verdict_flips("261019", [{"thesis_id": "t-b", "pnl": -7, "from_peak_pct": -9.0},
                                         {"thesis_id": "t-c2", "pnl": -1, "from_peak_pct": 0.5}])
        if fl["file"] != "손실판정_261012.json" or fl["judged"] != 2 or fl["loose"] != ["t-b"] or fl["strict"] != ["t-c2"] or fl["rate"] != 100.0:
            p.append(f"보정표(지난주 판정 · 표에서): {fl}")
        if lr.verdict_flips("261012", [])["file"]:
            p.append("그 주 자신의 판정을 '지난주'로 읽었다")
    finally:
        lr.OUTDIR = saved
        shutil.rmtree(tmp, ignore_errors=True)
    seed(peaks={"KR": {"AAA": {"peak_price": 120.0, "peak_at": "2026-10-10T10:00:00+09:00"}}},
         axes=[{"id": "ax1", "status": "hit", "direction": "+"}])
    if (lr._peaks().get("KR") or {}).get("AAA", {}).get("peak_price") != 120.0:
        p.append(f"포지션 고점을 저장소에서 못 읽었다: {lr._peaks()}")
    if (lr._axes().get("ax1") or {}).get("status") != "hit":
        p.append(f"축을 저장소에서 못 읽었다: {lr._axes()}")
    report("(4) loss_review — 손실 판정 파일 → op_loss_review(키 주·논지 · 멱등) · 오판 비율·보정표는 표에서 · 고점·축 읽기", p)


# ───────────────────────────────────────────── (5) real_check

def t5_real_check():
    import real_check as rc
    import feedback as fb
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="rcd_"))
    saved = (rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS, rc.LEDGER)
    old_argv = sys.argv
    try:
        rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS = tmp / "ev", tmp / "j", tmp / "s", tmp / "a"
        rc.LEDGER = tmp / "ledger.jsonl"
        for d in (rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS):
            d.mkdir()
        db.refill_put("261015", "kr", {"status": "open", "market": "kr", "events": [{"proceeds": 5}]})
        db.log_append("push_log", {"stamp": "261015", "market": "kr", "sent": True, "at": "2026-10-15T11:00"})
        (rc.EVID / "real_shadow_261015_kr.md").write_text(
            "## 그림자 점검 — x\n\n- risk_guard 종료코드 0 · 승인 매수 2건 · 상한 초과 0건 · 미달 아니오\n", encoding="utf-8")
        out = tmp / "real_check_261015_kr.md"
        sys.argv = ["real_check.py", "--stamp", "261015", "--market", "kr", "--out", str(out)]
        with contextlib.redirect_stdout(io.StringIO()):
            rc.main()
        md = out.read_text(encoding="utf-8")
        head = next(ln for ln in md.splitlines() if ln.startswith("[실전점검]"))
        rows = {r["item"]: r for r in db.q("SELECT item, status, note FROM op_real_check WHERE run_id='261015_kr'")}
        if len(rows) != 12:
            p.append(f"op_real_check 12행이어야: {len(rows)}")
        if (rows.get("② 매도 뒤 재투자") or {}).get("status") != "★":
            p.append(f"재투자 open(저장소 ex_refill)이 ★가 아니다: {rows.get('② 매도 뒤 재투자')}")
        if (rows.get("⑩ 보고 전달") or {}).get("status") != "✓" or (rows.get("① 실계좌라면 샀을까") or {}).get("status") != "✓":
            p.append(f"푸시(op_push)·그림자: {rows.get('⑩ 보고 전달')} {rows.get('① 실계좌라면 샀을까')}")
        r = fb.real_checks("261017")
        if len(r["runs"]) != 1 or r["runs"][0]["head"] != head:
            p.append(f"feedback 머리줄이 점검표 파일과 다르다: {r['runs']} vs {head}")
        if (r["items"].get("② 매도 뒤 재투자") or {}).get("star") != 1 or r["items"].get("⑩ 보고 전달", {}).get("star") != 0:
            p.append(f"feedback 항목 집계: {r['items'].get('② 매도 뒤 재투자')}")
        if fb.real_checks("261030", days=7)["runs"]:
            p.append("창 밖 점검표를 셌다")
    finally:
        sys.argv = old_argv
        rc.EVID, rc.JOURNAL, rc.SIGNALS, rc.ANALYSIS, rc.LEDGER = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(5) real_check — 12항목 → op_real_check · 마크다운 그대로 · feedback이 표에서 같은 머리줄·★ 집계", p)


# ───────────────────────────────────────────── (6) tax_ledger

def t6_tax_fill_fx():
    import tax_ledger as tl
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="tax_"))
    saved = tl.HERE
    try:
        (tmp / "journal").mkdir()
        rec = {"svr": "real", "trades": [
            {"ts": "2026-10-13T23:00:00+09:00", "ticker": "AAA", "action": "BUY", "qty": 10, "status": "SENT",
             "fill": {"verdict": "FILLED", "filled_qty": 10, "avg_price": 100.0, "settle_date": "2026-10-14", "settle_fx": 1300.0}},
            {"ts": "2026-10-13T23:30:00+09:00", "ticker": "AAA", "action": "SELL", "qty": 10, "status": "SENT",
             "fill": {"verdict": "FILLED", "filled_qty": 10, "avg_price": 120.0, "settle_date": "2026-10-21", "settle_fx": None}}]}
        tp = tmp / "journal" / "trades_261013_us.json"
        tp.write_text(json.dumps(rec), encoding="utf-8")
        imp = db.import_run_artifacts("261013", "us", here=tmp)
        if imp.get("fills") != 2:
            p.append(f"픽스처 이전: {imp}")
        tl.HERE = tmp
        n0 = tl.fill_fx(1400.0, today=date(2026, 10, 20))
        n1 = tl.fill_fx(1400.0, today=date(2026, 10, 21))
        n2 = tl.fill_fx(1400.0, today=date(2026, 10, 22))
        if (n0, n1, n2) != (0, 1, 0):
            p.append(f"채운 건수(결제일 전·당일·다음 날) {(n0, n1, n2)} ≠ (0, 1, 0)")
        f = json.loads(tp.read_text(encoding="utf-8"))["trades"][1]["fill"]
        if f.get("settle_fx") != 1400.0 or "결제일 당일" not in str(f.get("settle_fx_source")):
            p.append(f"거래 기록 파일 settle_fx: {f}")
        tax = {r["side"]: r["settle_fx"] for r in db.q("SELECT side, settle_fx FROM v_tax WHERE ticker='AAA'")}
        if tax != {"BUY": 1300.0, "SELL": 1400.0}:
            p.append(f"v_tax(ex_fill_fx) 결제일 환율: {tax}")
        if db.q1("SELECT COUNT(*) AS n FROM ex_fill_fx")["n"] != 2:
            p.append("ex_fill_fx 행이 2가 아니다(한 체결에 두 번 썼다?)")
        # 환율을 붙인 뒤 같은 주문의 새 관측(평단 정정)이 들어와도 환율은 주문 단위로 남는다(v7 · R11)
        rec2 = json.loads(tp.read_text(encoding="utf-8"))
        rec2["trades"][1]["fill"]["avg_price"] = 120.5
        tp.write_text(json.dumps(rec2), encoding="utf-8")
        db.import_run_artifacts("261013", "us", here=tmp)
        tax2 = {r["side"]: (r["settle_fx"], r["avg_price"]) for r in db.q("SELECT side, settle_fx, avg_price FROM v_tax WHERE ticker='AAA'")}
        if tax2 != {"BUY": (1300.0, 100.0), "SELL": (1400.0, 120.5)} or db.fills_find(db.run_id("261013", "us"), "AAA", side="SELL"):
            p.append(f"새 관측 뒤 v_tax(주문 단위 환율 · 마지막 관측): {tax2}")
        led = tl.ledger(2026)
        if len(led["rows"]) != 1 or led["rows"][0]["sell_fx"] != 1400.0:
            p.append(f"세금 원장이 거래 기록의 환율을 못 읽었다: {led['rows']}")
    finally:
        tl.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(6) tax_ledger.fill_fx — 결제일 당일만 · 거래 기록 파일 + ex_fill_fx(v_tax) 두 곳 · 한 번만 · 새 관측 뒤에도 주문 단위 환율", p)


# ───────────────────────────────────────────── (7)(8) 일봉 캐시 · 계산 결과 행

class FakeClient:
    """브로커 일봉 두 가지를 흉내 낸다 — 호출 수를 센다. 국내는 구간의 달력일마다 한 행(최신순), 미국은 기준일까지 100행."""

    def __init__(self):
        self.dd, self.od = [], []

    @staticmethod
    def _bar(d: date, i: int) -> dict:
        c = 1000 + int(80 * math.sin(i / 3.0)) + i
        return {"date": d.strftime("%Y%m%d"), "close": c, "open": c - 3, "high": c + 12, "low": c - 15, "volume": 10000 + i}

    def domestic_daily(self, ticker, start, end):
        self.dd.append((ticker, start, end))
        s, e = datetime.strptime(start, "%Y%m%d").date(), datetime.strptime(end, "%Y%m%d").date()
        days = [(s + timedelta(days=k)) for k in range((e - s).days + 1)]
        return [self._bar(d, (d - date(2024, 1, 1)).days) for d in reversed(days)]

    def overseas_daily(self, ticker, excd="NAS", bymd=""):
        self.od.append((ticker, excd, bymd))
        # 기준일 없는 조회 = 마지막 닫힌 세션까지(브로커는 아직 안 열린 세션의 행을 주지 않는다 · v5 확정 계약과 맞춘다)
        import kis_client as _kc
        e = datetime.strptime(bymd, "%Y%m%d").date() if bymd else _kc.last_session_date("US")
        return [self._bar(e - timedelta(days=k), (e - date(2024, 1, 1)).days - k) for k in range(100)]


def t7_bar_cache():
    import base_rates as br
    import price_levels as pl
    import regret as rg
    p = []
    import kis_client as _kc
    today = datetime.now(KST)
    st = today.strftime("%y%m%d")
    last_kr = _kc.last_session_date("KR").strftime("%Y%m%d")   # 확정 읽기는 마지막 닫힌 세션까지(v5 · 장중이면 전 거래일)
    # 국내 — 가격 수준(90일) → 기저율(150일 한 쪽) → 놓친 급등(5거래일 창)
    fc = FakeClient()
    lv = pl.fetch_rows(fc, "kr", "005930", 90)
    rates = br.fetch(fc, "005930", "kr", "NAS", 150)
    rr = rg.daily_rows(fc, "005930", "kr", "", st, 5)
    if len(fc.dd) != 1:
        p.append(f"국내: 세 도구가 같은 종목 일봉을 브로커에 {len(fc.dd)}번 물었다(1번이어야): {fc.dd}")
    ref = FakeClient()
    want = ref.domestic_daily("005930", (today - timedelta(days=90)).strftime("%Y%m%d"), last_kr)
    if [(r["date"], r["close"]) for r in lv] != [(r["date"], r["close"]) for r in want]:
        p.append(f"가격 수준 일봉이 원래 구간(90일)과 다르다: {len(lv)}행 vs {len(want)}행")
    if len(rates) < 140 or rates[0]["date"] != last_kr:
        p.append(f"기저율 일봉(캐시에서): {len(rates)}행 · 첫 행 {rates[0]['date'] if rates else '-'}")
    if len(rr) != 6 or rr[-1]["date"] != last_kr or rr[0]["date"] >= rr[-1]["date"]:
        p.append(f"놓친 급등 창: {len(rr)}행 {[r['date'] for r in rr]}")
    # 기저율 기본(500일) — 다른 두 도구는 기저율 혼자 받을 때보다 한 번도 더 묻지 않는다
    with db_case():
        alone = FakeClient()
        br.fetch(alone, "000660", "kr", "NAS", 500)
    with db_case():
        allc = FakeClient()
        pl.fetch_rows(allc, "kr", "000660", 90)
        br.fetch(allc, "000660", "kr", "NAS", 500)
        rg.daily_rows(allc, "000660", "kr", "", st, 5)
    if len(allc.dd) != len(alone.dd):
        p.append(f"기저율 500일: 셋이 {len(allc.dd)}번 · 기저율 혼자 {len(alone.dd)}번(같아야)")
    # 미국 — 가격 수준(기준일 없음) → 기저율 첫 쪽 → 놓친 급등(주문용 NASD → 시세용 NAS · 오늘 기준일 = 최신)
    fu = FakeClient()
    pl.fetch_rows(fu, "us", "MU", excd="NAS")
    us_rates = br.fetch(fu, "MU", "us", "NAS", 100)
    urr = rg.daily_rows(fu, "MU", "us", "NASD", st, 5)
    if len(fu.od) != 1:
        p.append(f"미국: 브로커 {len(fu.od)}번(1번이어야): {fu.od}")
    if len(us_rates) != 100 or len(urr) != 6:
        p.append(f"미국 반환: 기저율 {len(us_rates)}행 · 놓친 급등 {len(urr)}행")
    if db.q1("SELECT COUNT(*) AS n FROM mk_bar WHERE subject='KR:005930'")["n"] < 140:
        p.append("받은 일봉이 mk_bar에 남지 않았다")
    report("(7) 일봉 캐시 — 같은 run의 가격 수준·기저율·놓친 급등이 같은 종목을 브로커에 한 번만(국내·미국) · 반환은 원래 구간", p)


def t8_computed_rows():
    import base_rates as br
    import price_levels as pl
    p = []
    fc = FakeClient()
    rows = br.fetch(fc, "005930", "kr", "NAS", 300)
    n = br.record(rows, 5.0, "005930", "kr", "261016")
    got = db.base_rates("kr", "005930")
    base = [r for r in got if r["condition"] == "all" and r["window"] == 5]
    cond = [r for r in got if r["condition"] == "above_ma20" and r["window"] == 5]
    if not n or len(got) != n or not base or not cond:
        p.append(f"기저율 행: n={n} 표 {len(got)}행")
    elif not (0 <= base[0]["hit_rate"] <= 1) or cond[0]["base_n"] != base[0]["n"] or cond[0]["run_id"] != "261016_kr":
        p.append(f"기저율 칸(상승 비율 0~1 · 기준선 · run): {cond[0]}")
    br.record_touch(rows, "005930", "kr", float(rows[0]["close"]) * 0.97, br.touch_prob(rows, float(rows[0]["close"]) * 0.97, 20))
    if not [r for r in db.base_rates("kr", "005930") if r["condition"].startswith("touch_le_") and r["window"] == 20]:
        p.append("도달 확률 행이 없다")
    lv = pl.fetch_rows(fc, "kr", "005930", 90)
    computed = pl.levels(lv)
    cl = pl.confluence(computed, lv[0]["close"])
    k = pl.record("kr", "005930", computed, cl, lv[0]["date"])
    lr = {r["kind"]: r for r in db.levels("kr", "005930")}
    if k != len(computed) + len(cl) or "low_20" not in lr or lr["low_20"]["as_of"] != datetime.now(KST).strftime("%Y-%m-%d"):
        p.append(f"가격 수준 행: k={k} kinds={sorted(lr)[:8]}")
    sup = [v for kk, v in lr.items() if kk.startswith("support_")]
    if sup and (sup[0]["lo"] is None or sup[0]["hi"] is None):
        p.append(f"지지 구간 lo·hi가 비었다: {sup[0]}")
    pl.record("kr", "005930", {"low_20": computed["low_20"]}, [], lv[0]["date"])     # 같은 날 다시 — 줄어든 기준만 남아야
    again = [r["kind"] for r in db.levels("kr", "005930")]
    if again != ["low_20"]:
        p.append(f"같은 날 재계산 뒤 옛 행이 남았다(level_clear): {again}")
    report("(8) 계산 결과 행 — 기저율(co_base_rate: 조건·창·상승 비율·기준선·run) · 도달 확률 · 가격 수준(co_level_calc)", p)


# ───────────────────────────────────────────── (9) 보고 읽기

def t9_report_readers():
    import costs
    import dashboard
    import real_probe as rp
    import review
    import support_break as sb
    from kis_client import account_mode
    p = []
    mode = account_mode()
    seed(equity=[
        {"date": "2026-10-14", "ts": "2026-10-14T16:00:00+09:00", "market": "KR", "currency": "KRW", "equity": 10_000_000.0,
         "cash": 1_000_000.0, "positions": [], "account_mode": mode},
        {"date": "2026-10-15", "ts": "2026-10-15T16:00:00+09:00", "market": "KR", "currency": "KRW", "equity": 10_100_000.0,
         "cash": 1_000_000.0, "positions": [], "account_mode": mode},
        {"date": "2026-10-15", "ts": "2026-10-15T16:30:00+09:00", "market": "KR", "currency": "KRW", "equity": 10_200_000.0,
         "cash": 1_000_000.0, "positions": [], "account_mode": mode},
        {"date": "2026-10-15", "ts": "2026-10-16T06:00:00+09:00", "market": "US", "currency": "USD", "equity": 1_000.0,
         "cash": 100.0, "positions": [], "account_mode": mode}],
        theses=[{"id": "t-z", "ticker": "AAA", "name": "AAA", "market": "KR", "status": "held",
                 "entry_triggers": [{"id": "e1", "check": "price <= 99300", "basis": "support_1 99,000 — 구간 98,600~99,400"}],
                 "exit_triggers": []}],
        benchmark={"KR": {"start_equity": 10_000_000.0, "benchmark_ticker": "069500"}})
    er = review.equity_rows("KR")
    if [r["equity"] for r in er] != [10_000_000.0, 10_200_000.0]:
        p.append(f"review.equity_rows — 날짜마다 마지막 행: {[r['equity'] for r in er]}")
    with contextlib.redirect_stdout(io.StringIO()):
        review.review_without_signal("KR", "2001-01-01", record=True)
    rv = db.log_rows("reviews")
    if len(rv) != 1 or rv[0]["session"] != "2001-01-01":
        p.append(f"회고 기록(op_review): {rv}")
    import portfolio
    tot = portfolio.nav(portfolio.parts_from_curve(portfolio.family()), 1400.0)["krw"]     # 옛 costs._portfolio_krw 자리(2026-10-19 R06)
    if abs(tot - (10_200_000.0 + 1_000.0 * 1400.0)) > 1:
        p.append(f"portfolio.nav(곡선) — 시장별 마지막 행 원화 합(공유 표시 없음): {tot}")
    db.log_append("run_log", {"ts": "2026-10-15T09:30:00+09:00", "market": "kr", "outcome": "ok",
                              "journal": {"equity_row_added": True}})
    d = dashboard.collect()
    if len(d["equity"]) != 3 or len(d["runs"]) != 1:
        p.append(f"dashboard.collect — 자산 곡선(날짜·시장마다 마지막) {len(d['equity'])} · run 기록 {len(d['runs'])}")
    if mode == "paper" and "KR" not in (d["benchmark"] or {}):
        p.append(f"dashboard 벤치마크 기준점(op_benchmark_anchor): {d['benchmark']}")
    broken, _, n = sb.scan("kr", {"generated_at": "2026-10-14T16:10:00+09:00", "prices": {"AAA": {"price": 98000}}})
    if n != 1 or [r["ticker"] for r in broken] != ["AAA"]:
        p.append(f"support_break가 저장소 논지를 안 읽었다: n={n} {broken}")
    raw = {"kr_balance": {"rt_cd": "0", "output1": [{"pdno": "AAA"}],
                          "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1500000"}]},
           "us_psamount": {"rt_cd": "0", "output": {"frcr_ord_psbl_amt1": "700.5", "ovrs_max_ord_psbl_qty": "2", "exrt": "1400"}},
           "intgr_margin": {"output": {"ovrs_stck_itgr_mgna_dvsn_name": "통합증거금", "stck_itgr_cash100_ord_psbl_amt": "900000"}}}
    rp.record_balances("261017", raw, "2026-10-17T09:00:00+09:00")
    kb, ub = db.balance_last("kr", "probe"), db.balance_last("us", "probe")
    if not kb or kb["cash"] != 1_000_000.0 or kb["equity"] != 1_500_000.0 or kb["orderable"] != 900_000.0 or kb["run_id"] != "261017_kr":
        p.append(f"real_probe 국내 잔고 스냅샷: {kb}")
    if not ub or ub["buying_power"] != 700.5 or ub["exchange_rate"] != 1400.0 or ub["cash_shared"] != 1:
        p.append(f"real_probe 미국 잔고 스냅샷: {ub}")
    report("(9) 보고 읽기 — review·dashboard·costs·support_break가 저장소에서 · 회고 기록 op_review · real_probe 잔고(ex_balance probe)", p)


def t10_feedback_measurement():
    """FB-261017-1 — 늦은 매도는 고점일에 보유 중이던 것만 · 추적 다리 안의 되돌림은 늦은 매도가 아니다 ·
    잔고 증감으로 확정된 국내 매수의 단가는 앞뒤 캡처 스냅샷의 수량·평단에서(주문 지정가가 아니라)."""
    import types
    import feedback as fb
    import loss_review as lr
    p = []
    def snap(st, qty):
        return (st, {"balance": {"cash": 1000.0, "positions": ([{"ticker": "AAA", "qty": qty, "eval_amt": 100.0 * qty}] if qty else [])}})
    ctx = types.SimpleNamespace(stamp="261017", trades=[], theses=[],
                                snaps=[snap("260918", 4), snap("260921", 0), snap("260923", 0), snap("261014", 3)])
    sold_before = {"ticker": "AAA", "name": "A", "ret": 15.0, "trough_date": "260915", "peak_date": "260923",
                   "retrace_pct": -24.0, "peak": 285.0}
    rows, n = fb.late_sells(ctx, [sold_before])
    if rows or n != 0:
        p.append(f"고점 전에 판 보유를 늦은 매도로 셌다: {rows} n={n}")
    ctx.theses = [{"id": "t-a", "ticker": "AAA", "status": "held",
                   "exit_triggers": [{"id": "x3", "check": "from_peak_pct <= -11.4", "fired": False}]}]
    in_trail = {"ticker": "AAA", "name": "A", "ret": 9.0, "trough_date": "261001", "peak_date": "261014",
                "retrace_pct": -8.3, "peak": 198.8}
    rows, n = fb.late_sells(ctx, [in_trail])
    if rows or n != 1:
        p.append(f"추적 다리(−11.4%) 안의 되돌림(−8.3%)을 늦은 매도로 셌다: {rows} n={n}")
    ctx.theses = []
    rows, n = fb.late_sells(ctx, [dict(in_trail, retrace_pct=-12.0)])
    if len(rows) != 1 or rows[0]["profit_legs"] != ["이익실현 다리 없음"]:
        p.append(f"다리 없이 −12% 되돌린 보유는 늦은 매도여야: {rows}")
    c = db.connect()
    for rid, qty, avg in (("261013_kr", 1, 1520000.0), ("261015_kr", 5, 130440.0), ("261016_kr", 10, 128020.0)):
        c.execute("INSERT INTO ex_position (run_id, market, phase, ticker, qty, avg_price) VALUES (?,?,?,?,?,?)",
                  (rid, "kr", "capture", "BBB" if qty == 1 else "CCC", qty, avg))
    if lr._lot_price_from_snaps("kr", "261012", "BBB", 1) != 1520000.0:
        p.append(f"새 매수 단가(앞 스냅샷 없음 · 뒤 평단): {lr._lot_price_from_snaps('kr', '261012', 'BBB', 1)}")
    if lr._lot_price_from_snaps("kr", "261015", "CCC", 5) != 125600.0:
        p.append(f"추가 매수 단가((10×128,020 − 5×130,440) ÷ 5 = 125,600): {lr._lot_price_from_snaps('kr', '261015', 'CCC', 5)}")
    if lr._lot_price_from_snaps("kr", "261015", "CCC", 3) != 0.0 or lr._lot_price_from_snaps("kr", "261016", "CCC", 5) != 0.0:
        p.append("수량이 맞지 않거나 뒤 스냅샷이 없으면 0(주문가로 돌아간다)이어야")
    report("(10) 피드백 측정 — 늦은 매도는 고점일 보유만 · 추적 다리 안 되돌림 제외 · 잔고 증감 체결의 실제 단가", p)


# ───────────────────────────────────────────── (11)~(14) 통합자산·계좌 계열(2026-10-19 · Codex R06·R07)

@contextlib.contextmanager
def _pf_dirs():
    """portfolio·costs가 읽는 폴더를 빈 임시 폴더로 — 실제 스냅샷·거래 기록이 시험 숫자에 섞이지 않게."""
    import costs
    import portfolio
    tmp = Path(tempfile.mkdtemp(prefix="pf_"))
    (tmp / "data").mkdir()
    (tmp / "journal").mkdir()
    saved = (portfolio.DATA_DIR, costs.HERE)
    portfolio.DATA_DIR, costs.HERE = tmp / "data", tmp
    try:
        yield tmp
    finally:
        portfolio.DATA_DIR, costs.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _eq(date_, ts, mk, eq, cash, mode, shared=False):
    return {"date": date_, "ts": ts, "market": mk, "currency": "KRW" if mk == "KR" else "USD", "equity": float(eq),
            "cash": float(cash), "positions": [], "account_mode": mode, "cash_shared": shared}


def t11_flat_shared_cash():
    """(11) ← flat_shared_cash_performance(Codex R06) — 공유 현금(통합증거금)을 두 번 세어 주식이 그대로인데 5백만 원을 산 날 −25%였다.
    올바른 동작: 그날 환율·공유 현금 한 번 → 수익률 0.0 · 끝 환율 고정 참고값도 0.0 · NAV 양 끝 10,000,000."""
    import portfolio
    p = []
    with _pf_dirs():
        db.obs_put("mk_fx", pair="USDKRW", ts="2026-10-17T09:00:00+09:00", rate=1000.0, source="test")
        seed(equity=[_eq("2026-10-18", "2026-10-18T16:00:00+09:00", "KR", 10_000_000, 10_000_000, "real", True),
                     _eq("2026-10-18", "2026-10-18T23:00:00+09:00", "US", 10_000, 10_000, "real", True),
                     _eq("2026-10-19", "2026-10-19T16:00:00+09:00", "KR", 10_000_000, 5_000_000, "real", True),
                     _eq("2026-10-19", "2026-10-19T23:00:00+09:00", "US", 5_000, 5_000, "real", True)])
        r = portfolio.account_return("real", 30)
        f = portfolio.account_return("real", 30, fixed_fx=True)
        if (r.get("pct"), f.get("pct"), r.get("nav0"), r.get("nav1")) != (0.0, 0.0, 10_000_000, 10_000_000):
            p.append(f"공유 현금 수익률: {r} · 고정 환율 {f.get('pct')}")
        a = portfolio.account_return("paper", 30)
        if a.get("pct") is not None:
            p.append(f"모의 행이 없는데 모의 수익률을 냈다: {a}")
    report("(11) 공유 현금 수익률 — 주식 그대로면 0.0(그날 환율·고정 환율) · NAV 양 끝 같음 · 다른 계열은 산정 불가", p)


def t12_nav_symmetry_and_trade():
    """(12) 통합자산 대칭·매매 중립 — 국내에 5백만 원 투자와 미국에 5천 달러 투자는 같은 NAV · 사고팔기만으로는 NAV가 그대로 ·
    미국 조각이 있는데 환율이 없으면 못 쟀다(0이 아니다)."""
    import portfolio
    p = []
    a = portfolio.nav({"KR": {"equity": 10e6, "cash": 5e6, "invested": 5e6, "cash_shared": True, "ts": "2"},
                       "US": {"equity": 5000, "cash": 5000, "invested": 0, "cash_shared": True, "ts": "1"}}, 1000)
    b = portfolio.nav({"KR": {"equity": 5e6, "cash": 5e6, "invested": 0, "cash_shared": True, "ts": "1"},
                       "US": {"equity": 10000, "cash": 5000, "invested": 5000, "cash_shared": True, "ts": "2"}}, 1000)
    if (a["krw"], b["krw"], a["invested_krw"], b["invested_krw"]) != (10e6, 10e6, 5e6, 5e6):
        p.append(f"대칭: 국내 {a['krw']}/{a['invested_krw']} · 미국 {b['krw']}/{b['invested_krw']}")
    before = portfolio.nav({"KR": {"equity": 10e6, "cash": 10e6, "invested": 0}}, None)
    after = portfolio.nav({"KR": {"equity": 10e6, "cash": 0, "invested": 10e6}}, None)
    if before["krw"] != after["krw"] or after["invested_pct"] != 100.0:
        p.append(f"매수만으로 NAV가 바뀌었다: {before['krw']} → {after['krw']}")
    no = portfolio.nav({"KR": {"equity": 1e6, "cash": 1e6, "invested": 0}, "US": {"equity": 10, "cash": 10, "invested": 0}}, None)
    if no["ok"] or no["krw"] is not None:
        p.append(f"환율 없이 미국 조각을 합산했다: {no}")
    report("(12) 통합자산 — 시장 대칭 · 매매 중립 · 환율 없으면 못 쟀다", p)


def t13_family_split():
    """(13) ← paper_cost_leaks_to_real_denominator(Codex R07) — 비용 분자가 계좌를 안 가려 모의 체결 비용이 실계좌 자산 대비 %로 나왔다.
    올바른 동작: costs.drag는 계좌 계열이 필수 · 모의·실계좌 분자(체결)·분모(통합자산)가 서로 섞이지 않는다 · 다른 계열 파일 수를 돌려준다."""
    import costs
    p = []
    with _pf_dirs() as tmp:
        today = datetime.now(KST)
        st = today.strftime("%y%m%d")
        buy = lambda px, q: {"action": "BUY", "price": px, "qty": q, "fill": {"verdict": "FILLED", "filled_qty": q, "avg_price": px}}
        (tmp / "journal" / f"trades_{st}_us.json").write_text(json.dumps({"svr": "paper", "trades": [buy(100, 100)]}), encoding="utf-8")
        (tmp / "journal" / f"trades_{st}_kr.json").write_text(json.dumps({"account_mode": "real", "trades": [buy(10_000, 10)]}),
                                                              encoding="utf-8")
        db.obs_put("mk_fx", pair="USDKRW", ts=(today - timedelta(days=1)).isoformat(), rate=1000.0, source="test")
        d0 = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        seed(equity=[_eq(d0, f"{d0}T16:00:00+09:00", "KR", 50_000_000, 50_000_000, None),          # 표시 없는 옛 행 = 모의
                     _eq(d0, f"{d0}T17:00:00+09:00", "KR", 10_000_000, 10_000_000, "real")])
        pa, re_ = costs.drag(30, "paper", now=today), costs.drag(30, "real", now=today)
        if (pa["fills"], re_["fills"], pa["files_other_family"], re_["files_other_family"]) != (1, 1, 1, 1):
            p.append(f"분자 계열 분리: 모의 {pa['fills']}건/다른 {pa['files_other_family']} · 실계좌 {re_['fills']}건/다른 {re_['files_other_family']}")
        if (pa["equity_krw"], re_["equity_krw"]) != (50_000_000, 10_000_000):
            p.append(f"분모 계열 분리: 모의 {pa['equity_krw']} · 실계좌 {re_['equity_krw']}")
        if not (pa["turnover_krw"] == 10_000_000 and re_["turnover_krw"] == 100_000):
            p.append(f"회전(원화): 모의 {pa['turnover_krw']} · 실계좌 {re_['turnover_krw']}")
        try:
            costs.drag(30, None)
            p.append("계좌 계열 없이 비용을 냈다(기본값이 남아 있다)")
        except (ValueError, TypeError):
            pass
    report("(13) 계좌 계열 분리 — 비용 분자·분모가 같은 계열 · 다른 계열 파일 수 · 계열 필수", p)


def t14_flows_and_decompose():
    """(14) 입출금 — 5백만 원을 넣은 것은 수익이 아니다(Modified Dietz → 0.0) · 분해표의 입출금 5,000,000 · 가격 손익 0 · 출금은 음수만."""
    import portfolio
    p = []
    with _pf_dirs():
        seed(equity=[_eq("2026-10-01", "2026-10-01T16:00:00+09:00", "KR", 10_000_000, 10_000_000, "real"),
                     _eq("2026-10-15", "2026-10-15T16:00:00+09:00", "KR", 15_000_000, 15_000_000, "real")])
        portfolio.cash_flow_add("real", "deposit", "KRW", 5_000_000, "2026-10-10", note="시험 입금")
        r = portfolio.account_return("real", 30)
        if (r.get("pct"), r.get("flows_krw")) != (0.0, 5_000_000):
            p.append(f"입금 뒤 수익률: {r}")
        d = portfolio.decompose("real", "2026-10-01", "2026-10-15")
        if (d.get("flows_krw"), d.get("price_pnl_krw"), d.get("d_nav")) != (5_000_000, 0, 5_000_000):
            p.append(f"분해: {d}")
        with contextlib.redirect_stderr(io.StringIO()):
            rc = portfolio._main(["flow", "add", "--kind", "withdraw", "--currency", "KRW", "--amount", "1000", "--date", "2026-10-11",
                                  "--family", "real"])
        if rc != 2 or len(portfolio.cash_flows("real")) != 1:
            p.append("양수 출금을 받았다")
    report("(14) 입출금 — 입금은 수익 아님(0.0) · 분해 입출금 5백만·가격 손익 0 · 양수 출금 거부", p)


for fn in (t1_feedback_run_metrics, t2_feedback_ledger, t3_regret_log, t4_loss_review, t5_real_check, t6_tax_fill_fx,
           t7_bar_cache, t8_computed_rows, t9_report_readers, t10_feedback_measurement, t11_flat_shared_cash,
           t12_nav_symmetry_and_trade, t13_family_split, t14_flows_and_decompose):
    _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_reports.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
