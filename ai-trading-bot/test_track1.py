#!/usr/bin/env python3
"""
주문 근거 검증(갈래 ①)·노트 필수 요소 검사 테스트 — `fc_track1.py`·`note_lint.py`를 임시 폴더에서 끝까지 돈다.

왜(2026-10-13): 주문 근거는 주문 전에 fact-check 감사까지 PASS여야 하고(7회까지 고치고 안 되면 그 제안만 뺀다),
노트는 매매에 실제로 쓰인 요소와 그 출처만 기계로 검문한다. 결정 규칙(언제 결정을 거부하나 · 무엇을 빼나)이 틀리면
검증 안 된 근거로 주문이 나가거나 멀쩡한 제안이 빠진다 — 그래서 경계(감사 0회 · FAIL<7 · PASS · FAIL 7회)를 전부 돈다.

규칙: 실제 `signals/`·`analysis/`·`journal/`·승격 원장을 건드리지 않는다(모듈 HERE를 임시 폴더로 · 원장은 임시 파일).

    python3 test_track1.py          # run_tests.py가 부른다
"""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import fc_track1 as t1     # noqa: E402
import reversals as rv     # noqa: E402
import risk_guard as rg    # noqa: E402
import note_lint as nl     # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []

import atexit  # noqa: E402
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n★★ 테스트가 중간에 멈췄다 — 마지막으로 시작한 케이스: {_STATE['current']} · 그때까지 {n}건(실패 {nf})",
          file=sys.stderr)
    print(f"TEST-HALTED file=test_track1.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def _safe(fn, *a, **k):
    name = getattr(fn, "__name__", str(fn))
    _STATE["current"] = name
    try:
        return fn(*a, **k)
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        results.append((FAIL, f"{name} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {name} — 중단({type(e).__name__}: {str(e)[:160]})")


def report(label: str, problems: list):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


KST = timezone(timedelta(hours=9))
ST, MK = "261013", "us"


def _sig():
    return {
        "proposals": [
            {"action": "BUY", "ticker": "AAA", "evidence": [
                {"claim": "AAA guided FY revenue $10B per 8-K", "source_url": "https://example.com/aaa-8k", "verdict": "MATCH", "tier": "T1"},
                {"claim": "Backlog rose 20% according to IR deck", "source_url": "https://example.com/aaa-ir", "verdict": "MATCH", "tier": "T1"}]},
            {"action": "BUY", "ticker": "BBB", "evidence": [
                {"claim": "BBB won a 500MW contract", "source_url": "https://example.com/", "verdict": "MATCH", "tier": "T1"}]},
            {"action": "BUY", "ticker": "CCC", "evidence": [
                {"claim": "CCC margin 40% per 10-Q", "source_url": "https://example.com/ccc", "verdict": "MATCH", "tier": "T1"}]},
            {"action": "SELL", "ticker": "DDD", "source": "discipline", "evidence": [{"claim": "stop hit 95"}]},
        ],
        "holds": [{"ticker": "XOM", "why": "Brent $65 per EIA STEO; thesis intact"},
                  {"ticker": "XLV", "why": "no change"}],
        "allocation": {"target_invested_pct": 70,
                       "regime": "VIX 18 (tools_261013_us.md 〈국면 재료〉). Kalshi prices 72% odds of a hold per R3-4.",
                       "cash_reason": ""},
    }


def _env():
    tmp = Path(tempfile.mkdtemp(prefix="t1test_"))
    for d in ("signals", "analysis", "config", "journal"):
        (tmp / d).mkdir()
    (tmp / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(_sig(), ensure_ascii=False), encoding="utf-8")
    return tmp


def _verdict(tmp: Path, rows: list) -> dict:
    """rows = [(클레임 번호, 판정, 티어)] → 판정표를 쓰고 {클레임 번호: 판정표 줄}을 돌려준다."""
    lines = ["# 판정", "", "| # | 원 문구 | 판정 | 티어 | URL | 비고 |", "|---|---|---|---|---|---|"]
    at = {}
    for i, (n, v, t) in enumerate(rows, 1):
        lines.append(f"| {i} | 클레임 {n} \"근거 문구 {n}\" | {v} | {t} | https://src.example/claim{n} | - |")
        at[n] = len(lines)
    lines += ["", "LINT OK (test)", "PROPAGATE OK (test)"]
    (tmp / "analysis" / f"AI클레임_{ST}_{MK}_주문근거_판정.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return at


def _ledger(tmp: Path, rows: list) -> Path:
    p = tmp / "ledger.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for k, (status, findings) in enumerate(rows):
            f.write(json.dumps({"type": "finding", "ts": f"2026-10-13 12:{k:02d}:00", "skill": "fact-check", "cp": "verdict",
                                "run": f"ai클레임_{ST}_{MK}_주문근거_판정.md", "status": status, "findings": findings},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "finding", "ts": "2026-10-13 13:00:00", "skill": "fact-check", "cp": "verdict",
                            "run": f"ai클레임_{ST}_{MK}_판정.md", "status": "PASS", "findings": ""}) + "\n")   # 갈래 ② — 섞이면 안 된다
    return p


@contextlib.contextmanager
def _here(tmp: Path):
    saved = t1.HERE
    t1.HERE = tmp
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        t1.HERE = saved


def _extract(tmp: Path) -> dict:
    with _here(tmp):
        rc = t1.cmd_extract(ST, MK)
    mp = json.loads((tmp / "analysis" / f"AI클레임_{ST}_{MK}_주문근거.json").read_text(encoding="utf-8"))
    return {"rc": rc, "items": mp["items"]}


def _decide(tmp: Path, ledger: Path, max_rounds: int = 5, mode: str = "pre") -> int:
    with _here(tmp):
        return t1.cmd_decide(ST, MK, max_rounds, ledger, mode=mode, jdir=tmp / "journal")


def _trades(tmp: Path, rows: list):
    """rows = [(종목, 체결 판정, filled_qty, delta)] → journal/trades_<st>_<mk>.json (이 run 승인 파일로 낸 매수)."""
    tr = [{"ticker": tk, "action": "BUY", "status": "SENT", "qty": 9, "approved_ref": f"signals/approved_{ST}_{MK}.json",
           "fill": {k: v for k, v in (("verdict", vd), ("filled_qty", fq), ("delta", dl)) if v is not None}}
          for tk, vd, fq, dl in rows]
    (tmp / "journal" / f"trades_{ST}_{MK}.json").write_text(json.dumps({"trades": tr}), encoding="utf-8")


def _read_sig(tmp: Path) -> dict:
    return json.loads((tmp / "signals" / f"signal_{ST}_{MK}.json").read_text(encoding="utf-8"))


def _num(items, ref):
    return next(it["n"] for it in items if it["ref"] == ref)


# ───────────────────────────────────────────────────────────── 케이스

def t01_extract_targets():
    tmp = _env()
    try:
        x = _extract(tmp)
        refs = [it["ref"] for it in x["items"]]
        p = []
        if x["rc"] != 0:
            p.append(f"rc={x['rc']}")
        for want in ("BUY AAA E1", "BUY AAA E2", "BUY BBB E1", "BUY CCC E1", "HOLD XOM"):
            if want not in refs:
                p.append(f"{want} 누락")
        if any("DDD" in r for r in refs):
            p.append("규율 매도(DDD)를 근거 검증 대상으로 넣었다")
        if any("XLV" in r for r in refs):
            p.append("숫자 없는 보유 판단(XLV)을 넣었다")
        alloc = [it["text"] for it in x["items"] if it["kind"] == "allocation"]
        if any("VIX 18" in s for s in alloc):
            p.append("도구 캡처 숫자만인 배분 문장을 넣었다")
        if not any("Kalshi" in s for s in alloc):
            p.append("외부 사실(R행 인용) 배분 문장을 빠뜨렸다")
        report("(1) extract — 제안 근거·숫자 있는 보유·외부 사실 배분만 뽑는다(규율 매도·도구 숫자 제외)", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t02_refuse_before_pass():
    tmp = _env()
    try:
        x = _extract(tmp)
        _verdict(tmp, [(it["n"], "MATCH", "T1") for it in x["items"]])
        p = []
        rcpost = _decide(tmp, _ledger(tmp, []), mode="post")
        if rcpost != 1:
            p.append(f"감사 기록 0회인데 post rc={rcpost}(1이어야)")
        vp = tmp / "analysis" / f"AI클레임_{ST}_{MK}_주문근거_판정.md"
        vp.write_text(vp.read_text(encoding="utf-8").replace("PROPAGATE OK (test)", ""), encoding="utf-8")
        rcp = _decide(tmp, _ledger(tmp, []))
        if rcp != 1:
            p.append(f"판정표에 PROPAGATE OK 줄이 없으면 pre도 거부해야 — rc={rcp}")
        if "track1" in _read_sig(tmp):
            p.append("거부했는데 시그널에 track1을 썼다")
        vp.write_text(vp.read_text(encoding="utf-8") + "\nPROPAGATE OK (test)\n", encoding="utf-8")
        rc0 = _decide(tmp, _ledger(tmp, []))
        t = _read_sig(tmp).get("track1") or {}
        if rc0 != 0 or t.get("audit") != "PENDING" or t.get("rounds") != 0:
            p.append(f"pre는 감사 0회에도 표 기준으로 결정해야(2026-10-13 17:30) — rc={rc0} track1={t}")
        report("(2) decide — pre는 감사를 기다리지 않는다 · post는 감사 기록이 있어야 · LINT/PROPAGATE OK 줄 없으면 거부", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t03_pass_overwrites_and_drops_mismatch():
    tmp = _env()
    try:
        x = _extract(tmp)
        it = x["items"]
        rows = [(it_["n"], "MATCH", "T1") for it_ in it]
        rows = [(n, "MISMATCH" if n == _num(it, "BUY BBB E1") else v, t) for n, v, t in rows]
        at = _verdict(tmp, rows)
        rc = _decide(tmp, _ledger(tmp, []))
        s = _read_sig(tmp)
        tr = s.get("track1") or {}
        p = []
        if rc != 0:
            p.append(f"rc={rc}")
        if tr.get("pass") != ["AAA", "CCC"] or tr.get("drop") != ["BBB"]:
            p.append(f"pass={tr.get('pass')} drop={tr.get('drop')}")
        if tr.get("audit") != "PENDING" or tr.get("rounds") != 0:
            p.append(f"audit={tr.get('audit')} rounds={tr.get('rounds')}")
        bbb = s["proposals"][1]["evidence"][0]
        if bbb.get("verdict") != "MISMATCH" or bbb.get("track1") != "FAIL":
            p.append(f"BBB 근거 판정을 판정표 값으로 덮어쓰지 않았다: {bbb}")
        aaa = s["proposals"][0]["evidence"][0]
        want_row = f"AI클레임_{ST}_{MK}_주문근거_판정.md:L{at[_num(it, 'BUY AAA E1')]}"
        if aaa.get("fc_row") != [want_row] or aaa.get("track1") != "PASS":
            p.append(f"AAA 근거 행 번호·통과 표기: {aaa}")
        _decide(tmp, _ledger(tmp, []))
        vt = (tmp / "analysis" / f"AI클레임_{ST}_{MK}_주문근거_판정.md").read_text(encoding="utf-8")
        if vt.count("TRACK1 RESULT:") != 1:
            p.append(f"두 번 돌리면 결과 줄이 {vt.count('TRACK1 RESULT:')}개")
        report("(3) decide pre — 판정표 판정을 시그널에 덮어쓰고 MISMATCH 근거의 매수만 뺀다 · 결과 줄은 하나", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t04_post_regrade_reverses_filled_buy():
    tmp = _env()
    try:
        x = _extract(tmp)
        it = x["items"]
        _verdict(tmp, [(i["n"], "MATCH", "T1") for i in it])
        _decide(tmp, _ledger(tmp, []))                                   # pre — 전부 통과
        _trades(tmp, [("AAA", "FILLED", 5, None), ("BBB", "FILLED", None, 3), ("CCC", "REJECTED", None, None)])
        a2 = _num(it, "BUY AAA E2")
        _verdict(tmp, [(i["n"], "MISMATCH" if i["n"] == a2 else "MATCH", "T1") for i in it])   # 감사 뒤 재채점
        rc = _decide(tmp, _ledger(tmp, [("FAIL", "L6 근거 시효")]), mode="post")
        rows_ = rv.rows(tmp / "journal")
        t = _read_sig(tmp).get("track1") or {}
        p = []
        if rc != 0:
            p.append(f"rc={rc}")
        if [(r["ticker"], r["qty"], r["status"]) for r in rows_] != [("AAA", 5, "pending")]:
            p.append(f"되돌림 원장 — AAA 5주 대기 하나여야: {rows_}")
        if (t.get("post") or {}).get("reversals") != [rows_[0]["id"]] if rows_ else True:
            p.append(f"시그널 track1.post.reversals: {t.get('post')}")
        if t.get("pass") != ["AAA", "BBB", "CCC"]:
            p.append(f"주문 당시 pre 기록(pass)을 덮어썼다: {t.get('pass')}")
        sig = _read_sig(tmp)
        if sig["proposals"][0]["evidence"][1].get("verdict") != "MATCH":
            p.append("시그널 근거 판정(주문 당시 기록)을 post가 바꿨다")
        report("(4) decide post — 재채점으로 근거가 무너진 체결 매수만 되돌림 원장에(거부된 주문·여전히 통과인 종목은 아님)", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t05_post_idempotent_and_no_change_no_reversal():
    tmp = _env()
    try:
        x = _extract(tmp)
        it = x["items"]
        _verdict(tmp, [(i["n"], "MATCH", "T1") for i in it])
        _decide(tmp, _ledger(tmp, []))
        _trades(tmp, [("AAA", "FILLED", 5, None), ("BBB", "PARTIAL", 2, None)])
        p = []
        _decide(tmp, _ledger(tmp, [("FAIL", "배분 설명의 신용잔고 시효")]), mode="post")    # 10-13 국내 같은 경우 — 표 그대로
        if rv.rows(tmp / "journal"):
            p.append(f"표가 안 바뀌었는데 되돌림이 생겼다: {rv.rows(tmp / 'journal')}")
        b1 = _num(it, "BUY BBB E1")
        _verdict(tmp, [(i["n"], "MISMATCH" if i["n"] == b1 else "MATCH", "T1") for i in it])
        fails6 = [("FAIL", "x")] * 6
        for _ in range(2):
            _decide(tmp, _ledger(tmp, fails6), mode="post")
        rows_ = rv.rows(tmp / "journal")
        if [(r["ticker"], r["qty"]) for r in rows_] != [("BBB", 2)]:
            p.append(f"두 번 돌려도 BBB 부분 체결 2주 하나여야: {rows_}")
        vt = (tmp / "analysis" / f"AI클레임_{ST}_{MK}_주문근거_판정.md").read_text(encoding="utf-8")
        if vt.count("TRACK1 POST:") != 1:                              # (_verdict가 표를 통째로 다시 써서 pre의 RESULT 줄은 여기선 없다)
            p.append(f"두 번 돌려도 TRACK1 POST 줄은 하나 — {vt.count('TRACK1 POST:')}")
        if not (_read_sig(tmp)["track1"].get("post") or {}).get("over_limit"):
            p.append("FAIL 6회(>5)인데 over_limit 표시가 없다")
        report("(5) decide post — 표가 그대로면 되돌림 0(10-13 국내 경우) · 멱등 · 부분 체결 수량 · 5회 초과 표시", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t06_t3_only_drops():
    tmp = _env()
    try:
        x = _extract(tmp)
        it = x["items"]
        rows = [(i["n"], "MATCH", "T3" if i["ref"] == "BUY CCC E1" else "T1") for i in it]
        _verdict(tmp, rows)
        _decide(tmp, _ledger(tmp, []))
        tr = _read_sig(tmp).get("track1") or {}
        report("(6) decide — T3만으로 MATCH인 근거의 매수는 뺀다",
               [] if "CCC" in (tr.get("drop") or []) else [f"drop={tr.get('drop')}"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t08_verified_url_replaces_signal_url():
    tmp = _env()
    try:
        x = _extract(tmp)
        it = x["items"]
        _verdict(tmp, [(i["n"], "MATCH", "T1") for i in it])
        _decide(tmp, _ledger(tmp, []))
        s = _read_sig(tmp)
        bbb = s["proposals"][1]["evidence"][0]
        n = _num(it, "BUY BBB E1")
        p = []
        if bbb.get("source_url") != f"https://src.example/claim{n}":
            p.append(f"판정표 주소로 바꾸지 않았다: {bbb.get('source_url')}")
        if bbb.get("source_url_signal") != "https://example.com/":
            p.append(f"시그널 원래 주소를 남기지 않았다: {bbb.get('source_url_signal')}")
        report("(8) decide — 근거 주소를 판정표에서 검증한 주소로 바꾸고 시그널 원래 주소(도메인 루트)는 따로 남긴다", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t09_risk_guard_enforces_track1():
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim["fc_track1_required"] = True
    wl = {"US": [{"ticker": "AAA", "name": "Alpha", "excd": "NAS"}]}
    bal = {"cash": 100_000, "currency": "USD", "positions": [], "exchange_rate": 1300.0}

    def sig(track1, ev):
        s_ = {"market": "US", "proposals": [{"ticker": "AAA", "name": "Alpha", "action": "BUY", "weight_target_pct": 3.0,
                                             "confidence": 0.7, "excd": "NAS", "size_why": "테스트",
                                             "expected_upside_pct": 30.0, "evidence": ev}]}
        if track1 is not None:
            s_["track1"] = track1
        return s_
    good = [{"claim": "c", "source_url": "https://src.example/a", "verdict": "MATCH", "tier": "T1", "track1": "PASS"}]
    t1ok = {"result": "PASS", "audit": "PASS", "pass": ["AAA"], "drop": []}
    saved_pe = rg.portfolio_equity
    rg.portfolio_equity = lambda m, b: {"krw": 1.3e8, "cash_krw": 1.3e8, "invested_krw": 0.0, "ok": True,
                                        "asof": "t", "fx": 1300.0, "parts": {}, "why": "t"}

    def screen(s_, lm=lim):
        with contextlib.redirect_stdout(io.StringIO()):
            return rg.screen_proposals(s_, lm, wl, bal, {"AAA": {"price": 100.0}})
    p = []
    try:
        acc, rej = screen(sig(t1ok, good))
        if not acc:
            p.append(f"검증 통과 근거인데 거부: {rej}")
        acc, rej = screen(sig(None, good))
        if acc or not any("주문 근거 검증 기록이 없다" in r["why"] for r in rej):
            p.append(f"track1 기록 없는데 승인/다른 사유: {rej}")
        acc, rej = screen(sig({**t1ok, "drop": ["AAA"], "pass": []}, good))
        if acc or not any("주문 근거 검증 미통과" in r["why"] for r in rej):
            p.append(f"drop 종목인데 승인: {rej}")
        acc, rej = screen(sig(t1ok, good + [{"claim": "d", "verdict": "UNVERIFIED_T1", "tier": "T1"}]))
        if acc or not any("UNVERIFIED_T1" in r["why"] for r in rej):
            p.append(f"UNVERIFIED_T1 근거가 섞였는데 승인: {rej}")
        acc, rej = screen(sig(t1ok, [{**good[0], "track1": "FAIL"}]))
        if acc or not any("MATCH(T1/T2)로 확인된 근거가 하나도 없다" in r["why"] for r in rej):
            p.append(f"모델이 적은 MATCH(track1 FAIL)로 승인: {rej}")
        if not any("주문 근거 검증" in v for v in rg.VALIDITY_REJECTS):
            p.append("'주문 근거 검증' 거부가 타당성 거부 목록에 없다(논지 armed 유지가 안 된다)")
        off = dict(lim, fc_track1_required=False)
        acc, rej = screen(sig(None, good), off)
        if not acc:
            p.append(f"플래그를 끄면 예전처럼 track1 없이 승인돼야 한다: {rej}")
    finally:
        rg.portfolio_equity = saved_pe
    report("(9) risk_guard — 주문 근거 검증 기록·drop·UNVERIFIED_T1·모델 자기 MATCH를 거부(플래그 off면 예전대로)", p)


def t11_reversal_sells_generator():
    tmp = Path(tempfile.mkdtemp(prefix="rvtest_"))
    saved = rg.JOURNAL_DIR
    try:
        rg.JOURNAL_DIR = tmp
        rv.add(ST, "KR", "005930", 5, "근거 MISMATCH", jdir=tmp)
        rv.add(ST, "KR", "000660", 2, "근거 MISMATCH", jdir=tmp)
        rv.add(ST, "KR", "035420", 1, "근거 MISMATCH", jdir=tmp)
        rv.add(ST, "US", "AAPL", 3, "근거 MISMATCH", jdir=tmp)
        snap = {"balance": {"positions": [
            {"ticker": "005930", "name": "삼성전자", "qty": 3, "price": 71050},
            {"ticker": "000660", "name": "SK하이닉스", "qty": 4, "price": 180000}]},
            "prices": {"005930": {"price": 71050}, "000660": {"price": 180000}}}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            out = rg.audit_reversal_sells(snap, "KR", already={"000660"})
        p = []
        if [(o["ticker"], o["qty"], o["full_exit"], o["source"]) for o in out] != [("005930", 3, True, "audit_reversal")]:
            p.append(f"보유 3 < 대기 5 → 3주 전량 · 이미 규율 매도가 있는 종목은 건너뜀: {out}")
        if out and (out[0]["price"] % 50 != 0 or not out[0].get("exempt_from_count_limit") or out[0].get("leg_kind") != "stop"):
            p.append(f"국내 호가 단위·건수 면제·손절급 표시: {out[0]}")
        st_ = {r["ticker"]: r["status"] for r in rv.rows(tmp)}
        if st_ != {"005930": "pending", "000660": "pending", "035420": "void", "AAPL": "pending"}:
            p.append(f"보유 0 종목은 void · 다른 시장은 그대로: {st_}")
        report("(11) risk_guard — 감사 소급 대기 행 → 규율급 SELL(보유 상한 · 보유 0이면 void · 다른 시장 무시)", p)
    finally:
        rg.JOURNAL_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)


def t12_reversal_settle_and_carry():
    tmp = Path(tempfile.mkdtemp(prefix="rvtest_"))
    try:
        a, _ = rv.add(ST, "KR", "005930", 5, "근거 MISMATCH", jdir=tmp)
        t = {"ticker": "005930", "market": "KR", "action": "SELL", "source": "audit_reversal",
             "result": {"order_no": "001"}, "fill": {"verdict": "PARTIAL", "filled_qty": 2}}
        quiet = lambda *x, **k: None
        rv.settle_trade(t, tmp, log=quiet)
        rv.settle_trade(t, tmp, log=quiet)                           # 같은 주문번호 두 번 — 한 번만
        p = []
        r = rv.rows(tmp)[0]
        if (r["status"], r["qty"]) != ("pending", 3):
            p.append(f"부분 체결 2/5 → 잔량 3 대기(두 번 불러도): {r}")
        blk = rv.carry_block("KR", tmp)
        if "## 감사 소급" not in blk or a["id"] not in blk or "자동 전송" not in blk:
            p.append("이어받기 절에 대기 행·자동 전송 안내가 없다")
        t2 = {**t, "result": {"order_no": "002"}, "fill": {"verdict": "FILLED", "delta": -3}}
        rv.settle_trade(t2, tmp, log=quiet)
        if rv.rows(tmp)[0]["status"] != "filled" or rv.pending("KR", tmp):
            p.append(f"잔량 체결 → filled · 대기 0: {rv.rows(tmp)}")
        if "없음" not in rv.carry_block("KR", tmp):
            p.append("대기 0이면 '없음' 줄")
        rv.settle_trade({**t2, "source": "discipline"}, tmp, log=quiet)
        report("(12) reversals — 체결 확정으로 원장을 닫는다(부분은 잔량 · 주문번호로 멱등) · 이어받기 절", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t13_risk_guard_run_discipline_only_emits_reversal():
    """다음 run 경로 — 규율 전용 risk_guard.run이 대기 행을 승인 파일 주문으로 싣는다(머리 포함)."""
    tmp = Path(tempfile.mkdtemp(prefix="rvrun_"))
    names = ("LIMITS_PATH", "WATCHLIST_PATH", "KILL_PATH", "SIGNALS_DIR", "PEAKS_PATH", "DATA_DIR", "JOURNAL_DIR", "CALENDAR_PATH")
    saved = {n: getattr(rg, n) for n in names}
    import allocation as _al
    saved_al = _al.JOURNAL_DIR
    try:
        cfg = tmp / "config"
        cfg.mkdir()
        lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
        (cfg / "limits.json").write_text(json.dumps(lim), encoding="utf-8")
        shutil.copy(rg.WATCHLIST_PATH, cfg / "watchlist.json")
        for d in ("data", "journal"):
            (tmp / d).mkdir()
        rg.LIMITS_PATH, rg.WATCHLIST_PATH, rg.KILL_PATH = cfg / "limits.json", cfg / "watchlist.json", cfg / "KILL"
        rg.SIGNALS_DIR, rg.PEAKS_PATH, rg.DATA_DIR = tmp, tmp / "peaks.json", tmp / "data"
        rg.JOURNAL_DIR = _al.JOURNAL_DIR = tmp / "journal"
        rg.CALENDAR_PATH = tmp / "journal" / "calendar.json"
        rv.add(ST, "KR", "005930", 4, "근거 MISMATCH(테스트)", jdir=tmp / "journal")
        snap = {"market": "KR", "generated_at": datetime.now(KST).isoformat(), "day_pnl_pct": 0.0,
                "balance": {"currency": "KRW", "cash": 10_000_000, "positions": [
                    {"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 10, "avg_price": 70000,
                     "price": 71000, "eval_amt": 710000, "pnl_pct": 1.43}]},
                "prices": {"005930": {"price": 71000, "change_pct": 1.43}}}
        sp, out = tmp / "snap.json", tmp / "approved.json"
        sp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rg.run(None, sp, out, discipline_only=True)
        ap = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        o = [x for x in ap.get("orders") or [] if x.get("source") == "audit_reversal"]
        p = []
        if [(x["ticker"], x["qty"], x["full_exit"]) for x in o] != [("005930", 4, False)]:
            p.append(f"승인 파일에 되돌림 4주(부분)가 없다: {ap.get('orders')} · 거부 {ap.get('rejected')}")
        if not ap.get("limits_sha256") or ap.get("discipline_only") is not True:
            p.append("승인 파일 머리(한도 해시·discipline_only)가 없다 — execute.preflight가 거부한다")
        report("(13) risk_guard.run 규율 전용 — 대기 행이 승인 파일 주문으로 실린다(머리 그대로)", p)
    finally:
        for n, v in saved.items():
            setattr(rg, n, v)
        _al.JOURNAL_DIR = saved_al
        shutil.rmtree(tmp, ignore_errors=True)


NOTE_OK = """# 분석노트 261013 US
## §0 결론
투자 비중 목표 70% — 국면 숫자 [AI] Kalshi 72% hold [S1]
## §6 종목
AAA · V(거부 — 추격 금지) · XOM 보유 유지
## §7 제안
| 종목 | 근거 |
|---|---|
| AAA | 8-K |
| S1 | 조건 → 대응 |
## 출처
| S1 | https://example.com |
"""


def t07_note_lint():
    sig = {"proposals": [{"action": "BUY", "ticker": "AAA", "basis_refs": ["R3-4", "S1"]}],
           "scenarios": [{"id": "S1", "sources": ["cal:fomc_1028"]}],
           "allocation": {"target_invested_pct": 70, "regime_refs": ["tools:국면재료"]},
           "holds": [{"ticker": "XOM", "why": "Brent $65", "sources": ["S1"]}],
           "rejected_candidates": [{"ticker": "V", "sources": ["data/levels_261013_us.json"]}]}
    p = []
    e, w = nl.lint(sig, NOTE_OK, True)
    if e or w:
        p.append(f"갖춘 노트에서 위반 {e} 경고 {w}")
    bad = NOTE_OK.replace("AAA", "ZZZ").replace("70%", "60%")
    e, _ = nl.lint(sig, bad, False)
    if not any("AAA" in x for x in e):
        p.append("노트에 없는 제안 종목을 못 잡았다")
    if not any("70" in x for x in e):
        p.append("§0에 없는 배분 목표를 못 잡았다")
    bare = json.loads(json.dumps(sig))
    for k in ("proposals", "holds", "rejected_candidates", "scenarios"):
        for r in bare[k]:
            r.pop("basis_refs", None)
            r.pop("sources", None)
    bare["allocation"].pop("regime_refs")
    e, w = nl.lint(bare, NOTE_OK, False)
    if e or len(w) != 5:
        p.append(f"출처 칸이 비면 경고만(첫날) — 위반 {len(e)} 경고 {len(w)}(5여야)")
    e, w = nl.lint(bare, NOTE_OK, True)
    if len(e) != 5 or w:
        p.append(f"note_sources_required면 위반 — 위반 {len(e)}(5여야) 경고 {len(w)}")
    e, _ = nl.lint(sig, NOTE_OK.replace("[S1]", ""), True)
    if not any("[S번호]" in x for x in e):
        p.append("[AI] 문장 끝 출처 번호 누락을 못 잡았다")
    sig2 = json.loads(json.dumps(sig))
    sig2["proposals"][0]["basis_refs"] = ["somewhere"]
    e, _ = nl.lint(sig2, NOTE_OK, True)
    if not any("basis_refs" in x for x in e):
        p.append("형식이 아닌 출처 값(somewhere)을 통과시켰다")
    sr = nl.sources_required
    if not (sr("2026-10-14", "2026-10-14") and sr("2026-10-14", "2026-10-15") and not sr("2026-10-14", "2026-10-13")
            and sr(True) and not sr(False) and not sr(None)):
        p.append("note_sources_required 날짜 값 — 그날부터 위반, 전날은 경고여야 한다")
    report("(7) note_lint — 노트↔시그널 필수 요소 대조 · 출처 칸(첫날 경고/이후 위반 · 날짜 값) · [AI]→[S번호]", p)


def t10_note_lint_note_mode():
    p = []
    e, w = nl.lint_note(NOTE_OK, True)
    if e or w:
        p.append(f"갖춘 노트에서 위반 {e} 경고 {w}")
    e, _ = nl.lint_note(NOTE_OK.replace("| S1 | 조건 → 대응 |", ""), False)
    if not any("| S1 |" in x for x in e):
        p.append("§7 시나리오 행 누락을 못 잡았다")
    e, _ = nl.lint_note(NOTE_OK.replace("70%", "칠십").replace("72%", "칠십이"), False)
    if not any("§0" in x for x in e):
        p.append("§0 목표 투자 비중(%) 누락을 못 잡았다")
    e, w = nl.lint_note(NOTE_OK.replace(" [S1]", ""), False)
    if e or not w:
        p.append(f"[S번호] 누락은 첫날 경고만 — 위반 {e} 경고 {w}")
    tmp = Path(tempfile.mkdtemp(prefix="nltest_"))
    saved = nl.HERE
    try:
        for d in ("analysis", "config", "signals"):
            (tmp / d).mkdir()
        (tmp / "config" / "limits.json").write_text("{}", encoding="utf-8")
        npth = tmp / "analysis" / f"분석노트_{ST}_{MK}_v1_0.md"
        npth.write_text(NOTE_OK, encoding="utf-8")
        nl.HERE = tmp
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc1 = nl.main(["note", "--stamp", ST, "--market", MK])
            rc2 = nl.main(["note", "--stamp", ST, "--market", MK])
            rc3 = nl.main(["signal", "--stamp", ST, "--market", MK])
        body = npth.read_text(encoding="utf-8")
        if rc1 != 0 or rc2 != 0 or body.count("<!-- NOTE-LINT OK") != 1:
            p.append(f"note 명령 결과 줄: rc={rc1},{rc2} 줄 수={body.count('<!-- NOTE-LINT OK')}(1이어야)")
        if not body.startswith("# 분석노트") or "| S1 | 조건 → 대응 |" not in body:
            p.append("결과 줄을 쓰며 노트 본문을 바꿨다")
        if rc3 != 2:
            p.append(f"시그널이 없는데 signal 명령 rc={rc3}(2여야)")
    finally:
        nl.HERE = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(10) note_lint note — 노트 안 필수 요소(§0 비중·§7 S행·[S번호]) · 결과 줄은 노트 끝에 하나 · signal은 시그널이 있어야", p)


def t14_batches_and_unreflected():
    """배경화(2026-10-14) — 클레임 묶음 A·B(B는 A에 없는 줄만 · 번호 이어서) · run이 닫힌 뒤 도착한 ① 감사를 다음 run이 잡는다."""
    import subprocess
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="t1b_"))
    try:
        note = tmp / "분석노트_261014_kr_v1_0.md"
        note.write_text("# n\n\n## §2\n\n- 가 사실 [AI] 1\n- 나 사실 [AI] 2\n", encoding="utf-8")
        run = lambda b: subprocess.run([sys.executable, str(HERE / "extract_ai_claims.py"), str(note), "--batch", b],
                                       capture_output=True, text=True)
        run("A")
        with note.open("a", encoding="utf-8") as f:
            f.write("\n## §6\n\n- 다 사실 [AI] 3\n")
        run("B")
        a_txt = (tmp / "AI클레임_261014_kr_A.md").read_text(encoding="utf-8")
        b_txt = (tmp / "AI클레임_261014_kr_B.md").read_text(encoding="utf-8")
        if "1. (L5) 가 사실 1" not in a_txt or "다 사실" in a_txt:
            p.append(f"묶음 A: {a_txt}")
        if "3. (L10) 다 사실 3" not in b_txt or "가 사실" in b_txt:
            p.append(f"묶음 B(A에 없는 줄만 · 번호 3부터): {b_txt}")
        if run("A").returncode != 3:
            p.append("고정된 묶음 A를 다시 덮어썼다")
        # 미반영 감사 — 원장 감사 2회 · 판정표 TRACK1 POST rounds=1 → 잡힘 / rounds=2 → 안 잡힘 / 감사 0 → 안 잡힘
        led = tmp / "ledger.jsonl"
        led.write_text("".join(json.dumps({"type": "finding", "skill": "fact-check", "run": "ai클레임_261013_kr_주문근거_판정.md",
                                           "ts": f"2026-10-13 1{i}:00:00", "status": "FAIL"}) + "\n" for i in range(2)), encoding="utf-8")
        v = tmp / "AI클레임_261013_kr_주문근거_판정.md"
        v.write_text("| 표 |\nTRACK1 POST: audit=FAIL×1 rounds=1/5 pass=- drop=-\n", encoding="utf-8")
        (tmp / "AI클레임_261012_kr_주문근거_판정.md").write_text("| 표 |\n", encoding="utf-8")
        got = t1.unreflected("kr", "261014", ledger=led, adir=tmp)
        if got != [("261013", 2, 1)]:
            p.append(f"미반영 감사를 못 잡았다: {got}")
        md = t1.carry_note("kr", "261014", ledger=led, adir=tmp)
        if "① 감사 결과 미반영" not in md or "--stamp 261013 --market kr" not in md:
            p.append(f"이어받기 줄: {md}")
        v.write_text("| 표 |\nTRACK1 POST: audit=FAIL×2 rounds=2/5 pass=- drop=-\n", encoding="utf-8")
        if t1.unreflected("kr", "261014", ledger=led, adir=tmp) or t1.carry_note("kr", "261014", ledger=led, adir=tmp):
            p.append("다 반영된 감사를 미반영으로 잡았다")
        if t1.unreflected("kr", "261013", ledger=led, adir=tmp):
            p.append("자기 스탬프(같은 run)를 직전 run으로 봤다")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    report("(14) 배경화 — 클레임 묶음 A·B · run이 닫힌 뒤 도착한 ① 감사를 다음 run 이어받기가 잡는다", p)


for fn in (t01_extract_targets, t02_refuse_before_pass, t03_pass_overwrites_and_drops_mismatch,
           t04_post_regrade_reverses_filled_buy, t05_post_idempotent_and_no_change_no_reversal, t06_t3_only_drops, t07_note_lint,
           t08_verified_url_replaces_signal_url, t10_note_lint_note_mode, t09_risk_guard_enforces_track1,
           t11_reversal_sells_generator, t12_reversal_settle_and_carry, t13_risk_guard_run_discipline_only_emits_reversal,
           t14_batches_and_unreflected):
    _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_track1.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
