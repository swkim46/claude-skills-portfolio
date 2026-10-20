#!/usr/bin/env python3
"""
주문 근거 검증(갈래 ①)·노트 필수 요소 검사 테스트 — `facts.py`(queue·verdict·lint·decide)·`risk_guard`·`note_lint.py`를
빈 임시 저장소에서 끝까지 돈다.

왜(2026-10-13): 주문 근거는 주문 전에 판정으로 서야 하고(판정에서 안 서는 근거의 매수만 뺀다), 감사는 주문 뒤에 받아
결정을 뒤집는 결과만 소급한다. 노트는 매매에 실제로 쓰인 요소와 그 출처만 기계로 검문한다. 결정 규칙(언제 결정을 거부하나 ·
무엇을 빼나)이 틀리면 검증 안 된 근거로 주문이 나가거나 멀쩡한 제안이 빠진다 — 그래서 경계를 전부 돈다.

2026-10-17 저장소 전환: 근거는 문장(`evidence[{claim, source_url, tier, verdict}]`)이 아니라 사실 id(`facts[{id, role, support}]`)다.
옛 `fc_track1.py`(시그널 문장을 뽑아 고정본·판정표를 파일로 대조)·`reversals.py`(되돌림 원장 파일)는 `_archive/`로 갔고,
여기서는 `db.import_run_artifacts`(시그널 → ex_proposal_fact) → `facts.py queue --batch track1` → 판정(`facts.add_verdicts`) →
`facts.lint` → `facts.decide --mode pre`(시그널 파일과 ex_signal에 track1 블록) → `risk_guard` → 집행 → `decide --mode post`
(되돌림 = `db.reversal_append`)를 그대로 돈다. (15)가 가짜 증권사로 주문 경로 전체를 한 번에 돈다.

규칙: 실제 `signals/`·`analysis/`·`journal/`·저장소(`journal/trading.db`)·게이트 승격 원장을 건드리지 않는다 —
케이스마다 `testkit.db_case()`의 빈 임시 저장소 · 산출 파일은 임시 폴더 · 감사 원장은 임시 파일(없으면 없는 경로)을 넘긴다.

    python3 test_track1.py          # run_tests.py가 부른다
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import db                  # noqa: E402
import facts               # noqa: E402
import testkit             # noqa: E402
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
        import traceback
        tb = " | ".join(traceback.format_exc().strip().splitlines()[-3:])[:300]
        results.append((FAIL, f"{name} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {name} — 중단({type(e).__name__}: {str(e)[:160]}) {tb}")


def report(label: str, problems: list):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


KST = timezone(timedelta(hours=9))
ST, MK = "261013", "us"
RID = db.run_id(ST, MK)
WATCHLIST_DOC = json.loads((HERE / "config" / "watchlist.json").read_text(encoding="utf-8"))   # 렌더본 — 읽기만 하고 임시 저장소에 심는다


# ───────────────────────────────────────────────────────────── 도구

def quiet(fn, *a, **k):
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        r = fn(*a, **k)
    return r, o.getvalue(), e.getvalue()


def fact(ticker, n=1, tier=1, url=None, run=(ST, MK), **kw) -> str:
    """종류별 사실 한 행(실적) — id를 돌려준다. T1/T2는 원문 인용 20자 이상·URL이 있어야 등록된다."""
    row = {"kind": "earnings", "ticker": ticker, "period": "2026Q3", "metric": f"M{n}", "actual": 10 + n, "unit": "B USD",
           "as_of": "2026-10-12", "url": url or f"https://src.example/{ticker.lower()}-{n}",
           "quote": f"{ticker} reported metric {n} of {10 + n} billion dollars for the quarter", "tier": tier,
           "source": f"{ticker} 8-K"}
    row.update(kw)
    r, _, err = quiet(facts.add_fact, row, run)
    if not r or not r.get("id"):
        raise AssertionError(f"사실 등록 실패: {err[:160]}")
    return r["id"]


def vrow(fid, verdict, tier=1, batch="track1", run_id=RID, **kw) -> dict:
    """판정 한 행 — 판정 검사(lint)를 통과하는 기록 품질(T1 시도·비고 시각·받치는 것)을 갖춘다."""
    r = {"fact_id": fid, "verdict": verdict, "tier": tier, "batch": batch, "run_id": run_id,
         "sub": "값·출처", "t1": "T1 자체(공시 원문)", "note": "fetch.py 23:41 · 원문 표의 값과 같다",
         "support": "논지 — 이 종목 논지의 실적 다리를 받친다"}
    # 검증자는 큐 행의 fact_rev(그때의 지금 판)를 그대로 옮긴다(2026-10-19 · W03) — fact_rev=None을 주면 칸을 비운 행(시험용)
    if "fact_rev" not in kw:
        x = db.q1("SELECT rev FROM ev_fact WHERE id=?", (fid,))
        r["fact_rev"] = x["rev"] if x else None
    r.update(kw)
    if r.get("fact_rev") is None:
        r.pop("fact_rev", None)
    return r


def verdicts(rows: list, corrections: bool = True) -> None:
    """판정을 넣는다 — MISMATCH·STALE에는 교정(`facts.correct`)을 잇는다(판정 검사 (e)가 교정 없는 MISMATCH를 막는다)."""
    quiet(facts.add_verdicts, rows)
    if corrections:
        for r in rows:
            if r["verdict"] in ("MISMATCH", "STALE"):
                cur = db.q1("SELECT actual FROM co_earnings WHERE fact_id=?", (r["fact_id"],)) or {}
                quiet(facts.correct, r["fact_id"], {"actual": str(float(cur.get("actual") or 0) + 0.1)}, "원문 값으로 교정")


def proposal(ticker, action, fids, roles=None, **kw) -> dict:
    p = {"ticker": ticker, "name": ticker, "action": action, "thesis": f"{ticker} 테스트 논지", "confidence": 0.7,
         "weight_target_pct": 5.0, "size_why": "테스트 — 기대 +15% · 손절 -7%", "expected_upside_pct": 15.0,
         "facts": [{"id": f, "role": (roles or {}).get(f, "support"), "support": f"논지 — {ticker} 실적 다리"} for f in fids]}
    if action == "BUY":     # 2026-10-19 R05 — 명분 없는 매수는 gap 채우기가 집중선에서 멈추고 미달로 남는다. 이 파일은 근거 경로를 잰다.
        p["concentration_why"] = "테스트 — 이 종목 집중을 감수하는 명분(근거 경로 시험 · 집중선은 test_risk_guard (64)가 잰다)"
    p.update(kw)
    return p


def signal(proposals, run=(ST, MK), **kw) -> dict:
    st, mk = run
    s = {"schema_version": rg.SCHEMA_VERSION, "run_id": db.run_id(st, mk), "market": mk.upper(),
         "generated_at": datetime.now(KST).isoformat(), "no_trade": False,
         "allocation": {"based_on": None, "target_invested_pct": 95, "regime": "테스트 국면 — 벤치마크 20일 +1.0% · 상승 섹터 8/14",
                        "cash_reason": "없음", "cash_release_when": "", "reserved": [], "change": None},
         "proposals": proposals}
    s.update(kw)
    return s


def write_signal(tmp: Path, sig: dict, run=(ST, MK)) -> Path:
    p = tmp / "signals" / f"signal_{run[0]}_{run[1]}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sig, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def ledger(tmp: Path, rows: list, run=(ST, MK)) -> Path:
    """게이트 승격 원장 흉내(임시 파일) — rows = [(status, findings)]. 갈래 ② 감사 줄을 하나 섞는다(세면 안 된다)."""
    st, mk = run
    # run은 감사보다 먼저 시작했다(v4 · U03 — 줄 시각이 run 시작보다 이르면 그 run 감사가 아니다)
    db.run_start(st, mk)
    db.connect().execute("UPDATE ex_run SET started='2026-10-13T09:00:00+09:00' WHERE run_id=?", (db.run_id(st, mk),))
    p = tmp / "ledger.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for k, (status, findings) in enumerate(rows):
            f.write(json.dumps({"type": "finding", "ts": f"2026-10-13 12:{k:02d}:00", "skill": "fact-check", "cp": "verdict",
                                "run": f"ai클레임_{st}_{mk}_주문근거_판정.md", "status": status, "findings": findings},
                               ensure_ascii=False) + "\n")
        f.write(json.dumps({"type": "finding", "ts": "2026-10-13 13:00:00", "skill": "fact-check", "cp": "verdict",
                            "run": f"ai클레임_{st}_{mk}_판정.md", "status": "PASS", "findings": ""}) + "\n")
    return p


def trades(tmp: Path, rows: list, run=(ST, MK), ref: str = "") -> None:
    """rows = [(종목, 체결 판정, filled_qty, delta)] → journal/trades_<st>_<mk>.json(이 run 승인 파일로 낸 매수) → 저장소 이전.
    `ref`를 주면 그 승인 파일(예: 재투자 승인 approved_<st>_<mk>_refill_<tag>.json)로 낸 매수다."""
    st, mk = run
    tr = [{"ts": f"2026-10-13T23:0{i}:00+09:00", "ticker": tk, "action": "BUY", "status": "SENT", "qty": 9, "price": 100.0,
           "approved_ref": ref or f"approved_{st}_{mk}.json", "result": {"order_no": f"00{i}"},
           "fill": {k: v for k, v in (("verdict", vd), ("filled_qty", fq), ("delta", dl)) if v is not None}}
          for i, (tk, vd, fq, dl) in enumerate(rows)]
    (tmp / "journal").mkdir(exist_ok=True)
    (tmp / "journal" / f"trades_{st}_{mk}.json").write_text(json.dumps({"trades": tr}), encoding="utf-8")
    db.import_run_artifacts(st, mk, here=tmp)


def decide(mode="pre", led=None, sig_path="", run=(ST, MK), tmp=None):
    """facts.decide — 감사 원장은 늘 임시 경로(없으면 없는 파일)를 넘긴다(실제 게이트 원장을 읽지 않게)."""
    led = led or ((tmp or Path(tempfile.gettempdir())) / "w2b_no_ledger_track1.jsonl")
    return quiet(facts.decide, run[0], run[1], mode, ledger=led, signal_path=str(sig_path) if sig_path else "")


@contextlib.contextmanager
def case():
    """빈 임시 저장소 + 임시 폴더(signals·journal·analysis) — 장 열림 판정은 '닫힘'으로 고정(되돌림 안내 문구만 바뀐다)."""
    saved_open = facts._market_open
    facts._market_open = lambda mk: (False, "테스트 — 닫힘")
    try:
        with testkit.db_case() as tmp:
            for d in ("signals", "journal", "analysis", "data"):
                (tmp / d).mkdir(exist_ok=True)
            yield tmp
    finally:
        facts._market_open = saved_open


def base_case(tmp: Path) -> dict:
    """옛 _sig()와 같은 모양 — 매수 셋(AAA 근거 둘 · BBB · CCC) · 규율 매도 DDD · 보유 XOM(근거 있음)·XLV(없음)."""
    ids = {"a1": fact("AAA", 1), "a2": fact("AAA", 2), "b1": fact("BBB", 1), "c1": fact("CCC", 1), "d1": fact("DDD", 1),
           "h1": fact("XOM", 1)}
    sig = signal([proposal("AAA", "BUY", [ids["a1"], ids["a2"]]), proposal("BBB", "BUY", [ids["b1"]]),
                  proposal("CCC", "BUY", [ids["c1"]]),
                  proposal("DDD", "SELL", [ids["d1"]], source="discipline")],
                 holds=[{"ticker": "XOM", "why": "Brent $65 per EIA STEO; thesis intact", "facts": [{"id": ids["h1"]}]},
                        {"ticker": "XLV", "why": "no change"}])
    ids["path"] = write_signal(tmp, sig)
    ids["import"] = db.import_run_artifacts(ST, MK, here=tmp)
    return ids


# ───────────────────────────────────────────────────────────── 케이스

def t01_order_basis_from_signal():
    """시그널의 facts → ex_proposal_fact·ex_hold_fact → track1 큐(규율 매도 제외 · 재투자 후보 포함 · 없는 id는 missing)."""
    p = []
    with case() as tmp:
        ids = {"a1": fact("AAA", 1), "a2": fact("AAA", 2), "bg": fact("AAA", 3), "d1": fact("DDD", 1),
               "h1": fact("XOM", 1), "r1": fact("RRR", 1)}
        sig = signal([proposal("AAA", "BUY", [ids["a1"], ids["a2"], ids["bg"]], roles={ids["bg"]: "background"}),
                      proposal("DDD", "SELL", [ids["d1"]], source="discipline"),
                      proposal("ZZZ", "BUY", ["F-999999"])],
                     holds=[{"ticker": "XOM", "why": "Brent $65", "facts": [{"id": ids["h1"]}]}],
                     refill_candidates=[proposal("RRR", "BUY", [ids["r1"]])])
        write_signal(tmp, sig)
        imp = db.import_run_artifacts(ST, MK, here=tmp)
        if imp.get("missing_facts") != ["F-999999"]:
            p.append(f"없는 사실 id를 missing으로 알리지 않았다: {imp.get('missing_facts')}")
        pf = {(r["idx"], r["fact_id"]): r["role"] for r in db.q("SELECT idx, fact_id, role FROM ex_proposal_fact WHERE run_id=?", (RID,))}
        if pf.get((0, ids["bg"])) != "background" or pf.get((0, ids["a1"])) != "support":
            p.append(f"제안 근거 역할이 저장소에 안 들어갔다: {pf}")
        if (1000, ids["r1"]) not in pf:
            p.append("재투자 후보의 근거가 ex_proposal_fact에 없다(db 요청 R1)")
        if not db.q1("SELECT 1 AS o FROM ex_hold_fact WHERE run_id=? AND fact_id=?", (RID, ids["h1"])):
            p.append("보유 판단의 근거가 ex_hold_fact에 없다(db 요청 R2)")
        rows, _ = facts.queue(ST, MK, "track1")
        got = [r["fact_id"] for r in rows]
        for k in ("a1", "a2", "bg", "h1", "r1"):
            if ids[k] not in got:
                p.append(f"주문 근거 큐에 {k}({ids[k]})가 없다")
        if ids["d1"] in got:
            p.append("규율 매도(DDD)의 근거를 주문 근거 큐에 넣었다")
    report("(1) 시그널 facts → 저장소 근거 연결 → track1 큐 — 규율 매도 제외 · 보유·재투자 후보 포함 · 없는 id는 missing", p)


def t02_refuse_before_pass():
    p = []
    with case() as tmp:
        ids = base_case(tmp)
        rc, _, err = decide(sig_path=ids["path"], tmp=tmp)
        if rc != 2 or "queue" not in err:
            p.append(f"큐(track1) 고정 전에 결정했다 — rc={rc}(2여야)")
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MATCH", 1, t1="") for r in rows])        # T1 시도 칸이 빈 판정 — 판정 검사 FAIL
        rc, _, err = decide(sig_path=ids["path"], tmp=tmp)
        if rc != 1 or "LINT FAIL" not in err:
            p.append(f"판정 검사(LINT) FAIL인데 결정했다 — rc={rc}(1이어야) {err[:80]!r}")
        if "track1" in read_json(ids["path"]):
            p.append("거부했는데 시그널 파일에 track1을 썼다")
        verdicts([vrow(r["fact_id"], "MATCH", 1) for r in rows])               # 재채점(round 2) — 기록 품질을 갖춘 판정
        rc, _, err = decide(mode="post", sig_path=ids["path"], tmp=tmp)
        if rc != 1 or "감사 기록이 없다" not in err:
            p.append(f"감사 기록 0회인데 post rc={rc}(1이어야)")
        rc, out, err = decide(sig_path=ids["path"], tmp=tmp)
        t = read_json(ids["path"]).get("track1") or {}
        if rc != 0 or t.get("audit") != "PENDING" or t.get("rounds") != 0 or "TRACK1 RESULT:" not in out:
            p.append(f"pre는 감사 0회에도 판정 기준으로 결정해야(2026-10-13 17:30) — rc={rc} track1={t} {err[:80]!r}")
    report("(2) decide — 큐·판정 검사가 먼저 · pre는 감사를 기다리지 않는다 · post는 감사 기록이 있어야", p)


def t03_pre_drops_mismatch():
    p = []
    with case() as tmp:
        ids = base_case(tmp)
        before = read_json(ids["path"])["proposals"]
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MISMATCH" if r["fact_id"] == ids["b1"] else "MATCH", 1, note=(
            "fetch.py 23:41 · 원문 값 12.0과 다르다" if r["fact_id"] == ids["b1"] else "fetch.py 23:41 · 원문 표의 값과 같다"))
                  for r in rows])
        rc, out, err = decide(sig_path=ids["path"], tmp=tmp)
        s = read_json(ids["path"])
        tr = s.get("track1") or {}
        if rc != 0:
            p.append(f"rc={rc} {err[:120]!r}")
        if tr.get("pass") != ["AAA", "CCC"] or tr.get("drop") != ["BBB"] or tr.get("result") != "PARTIAL":
            p.append(f"pass={tr.get('pass')} drop={tr.get('drop')} result={tr.get('result')}")
        if tr.get("audit") != "PENDING" or tr.get("rounds") != 0 or "line" in tr:
            p.append(f"track1 블록 모양(audit·rounds · line 칸 없음): {tr}")
        for k in ("result", "audit", "rounds", "pass", "drop", "holds_unverified", "allocation_unverified", "ref", "at"):
            if k not in tr:
                p.append(f"track1 블록에 {k}가 없다(risk_guard·dispatch 게이트가 읽는 모양)")
        if s["proposals"] != before:
            p.append("decide가 시그널 근거 칸(proposals[].facts)을 고쳤다 — 판정은 저장소에만 산다")
        row = db.q1("SELECT track1 FROM ex_signal WHERE run_id=?", (RID,))
        t_db = json.loads(row["track1"]) if row and row.get("track1") else {}
        if {k: v for k, v in t_db.items() if k != "line"} != tr:
            p.append("ex_signal.track1과 시그널 파일 track1 블록이 다르다")
        decide(sig_path=ids["path"], tmp=tmp)
        if (read_json(ids["path"]).get("track1") or {}).get("drop") != ["BBB"]:
            p.append("두 번 돌리자 결과가 바뀌었다")
    report("(3) decide pre — MISMATCH 근거의 매수만 뺀다 · 시그널 파일과 ex_signal에 같은 track1 블록 · 근거 칸 불변 · 멱등", p)


def t04_post_regrade_reverses_filled_buy():
    p = []
    with case() as tmp:
        ids = base_case(tmp)
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MATCH", 1) for r in rows])
        decide(sig_path=ids["path"], tmp=tmp)                                 # pre — 전부 통과
        props_pre = read_json(ids["path"])["proposals"]
        trades(tmp, [("AAA", "FILLED", 5, None), ("BBB", "FILLED", None, 3), ("CCC", "REJECTED", None, None)])
        verdicts([vrow(ids["a2"], "MISMATCH", 1, note="감사 재채점 23:58 — 원문 값 11.9와 다르다")])   # 감사 뒤 재채점
        rc, out, err = decide(mode="post", led=ledger(tmp, [("FAIL", "L6 근거 시효")]), sig_path=ids["path"], tmp=tmp)
        revs = db.reversals_all()
        t = read_json(ids["path"]).get("track1") or {}
        if rc != 0:
            p.append(f"rc={rc} {err[:120]!r}")
        if [(r["ticker"], r["qty"], r["status"]) for r in revs] != [("AAA", 5, "pending")]:
            p.append(f"되돌림 — AAA 5주 대기 하나여야: {[(r.get('ticker'), r.get('qty'), r.get('status')) for r in revs]}")
        if revs and (t.get("post") or {}).get("reversals") != [revs[0]["id"]]:
            p.append(f"시그널 track1.post.reversals: {t.get('post')}")
        if t.get("pass") != ["AAA", "BBB", "CCC"]:
            p.append(f"주문 당시 pre 기록(pass)을 덮어썼다: {t.get('pass')}")
        if revs and not any(ids["a2"] in str(x) for x in [revs[0].get("why")]):
            p.append(f"되돌림 사유에 무너진 근거 사실 id가 없다: {revs[0].get('why')}")
        if "TRACK1 POST: audit=FAIL×1 rounds=1/" not in out:
            p.append(f"TRACK1 POST 줄: {out[:160]!r}")
        if read_json(ids["path"])["proposals"] != props_pre:
            p.append("post가 시그널 근거 칸(주문 당시 기록)을 바꿨다")
    report("(4) decide post — 재채점으로 근거가 무너진 체결 매수만 되돌림(db)에(거부된 주문·여전히 통과인 종목은 아님)", p)


def t05_post_idempotent_and_no_change_no_reversal():
    p = []
    with case() as tmp:
        ids = base_case(tmp)
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MATCH", 1) for r in rows])
        decide(sig_path=ids["path"], tmp=tmp)
        trades(tmp, [("AAA", "FILLED", 5, None), ("BBB", "PARTIAL", 2, None)])
        decide(mode="post", led=ledger(tmp, [("FAIL", "배분 설명의 신용잔고 시효")]), sig_path=ids["path"], tmp=tmp)   # 10-13 국내 — 판정 그대로
        if db.reversals_all():
            p.append(f"판정이 안 바뀌었는데 되돌림이 생겼다: {db.reversals_all()}")
        verdicts([vrow(ids["b1"], "MISMATCH", 1, note="감사 재채점 23:58 — 원문 값 10.9와 다르다")])
        fails6 = [("FAIL", f"x{i}") for i in range(6)]
        for _ in range(2):
            rc, out, _ = decide(mode="post", led=ledger(tmp, fails6), sig_path=ids["path"], tmp=tmp)
        revs = db.reversals_all()
        if [(r["ticker"], r["qty"]) for r in revs] != [("BBB", 2)]:
            p.append(f"두 번 돌려도 BBB 부분 체결 2주 하나여야: {[(r.get('ticker'), r.get('qty')) for r in revs]}")
        if "이미 기록됨" not in out:
            p.append("두 번째 post가 '이미 기록됨'을 말하지 않았다")
        if not ((read_json(ids["path"]).get("track1") or {}).get("post") or {}).get("over_limit"):
            p.append("FAIL 6회 넘게(>5)인데 over_limit 표시가 없다")
    report("(5) decide post — 판정이 그대로면 되돌림 0(10-13 국내 경우) · 멱등 · 부분 체결 수량 · 5회 초과 표시", p)


def t06_t3_only_drops():
    p = []
    with case() as tmp:
        ids = base_case(tmp)
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MATCH", 3 if r["fact_id"] == ids["c1"] else 1) for r in rows])
        decide(sig_path=ids["path"], tmp=tmp)
        tr = read_json(ids["path"]).get("track1") or {}
        if "CCC" not in (tr.get("drop") or []):
            p.append(f"drop={tr.get('drop')}")
    report("(6) decide — T3만으로 MATCH인 근거의 매수는 뺀다", p)


def t08_root_url_cannot_pass():
    """10-13 VST 재발 방지의 새 자리 — 근거 주소는 사실 행의 URL 하나다(시그널은 id만 싣는다). 그 URL이 도메인 루트뿐이면
    MATCH 판정을 판정 검사(lint)가 막고 decide가 결정을 거부한다 — 루트 주소로는 매수 근거가 서지 않는다."""
    p = []
    with case() as tmp:
        root = fact("VST", 1, url="https://www.ferc.gov/")
        good = fact("VST", 2)
        sig = signal([proposal("VST", "BUY", [root, good])])
        path = write_signal(tmp, sig)
        db.import_run_artifacts(ST, MK, here=tmp)
        if "url" in json.dumps(read_json(path)["proposals"][0]["facts"]):
            p.append("시그널 근거 칸에 URL이 실렸다 — id만 싣는다")
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MATCH", 1) for r in rows])
        errs, _, _, _ = facts.lint(ST, MK, "track1")
        if not any("도메인 루트" in e for e in errs):
            p.append(f"도메인 루트 주소의 MATCH를 판정 검사가 막지 않았다: {errs[:2]}")
        rc, _, err = decide(sig_path=path, tmp=tmp)
        if rc != 1 or "track1" in read_json(path):
            p.append(f"루트 주소 MATCH로 결정했다 — rc={rc} {err[:80]!r}")
    report("(8) 근거 주소는 사실 행의 URL 하나 — 도메인 루트 MATCH는 판정 검사가 막고 decide가 거부한다(10-13 VST)", p)


def t09_risk_guard_enforces_track1():
    """risk_guard 이중 검사 — 근거 사실의 이번 run 판정을 저장소에서 읽는다(모델이 적은 판정 칸은 없다)."""
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim["fc_track1_required"] = True
    lim["reject_rules"] = {**(lim.get("reject_rules") or {}), "reject_if_any_mismatch": True, "require_match_t1t2_for_buy": False}
    wl = {"US": [{"ticker": "AAA", "name": "Alpha", "excd": "NAS"}]}
    bal = {"cash": 100_000, "currency": "USD", "positions": [], "exchange_rate": 1300.0}
    t1ok = {"result": "PASS", "audit": "PENDING", "pass": ["AAA"], "drop": []}
    saved_pe = rg.portfolio_equity
    rg.portfolio_equity = lambda m, b: {"krw": 1.3e8, "cash_krw": 1.3e8, "invested_krw": 0.0, "ok": True,
                                        "asof": "t", "fx": 1300.0, "parts": {}, "why": "t"}

    def sig(track1, fids, roles=None, run_id=RID):
        s_ = {"market": "US", "run_id": run_id,
              "proposals": [proposal("AAA", "BUY", fids, roles=roles, name="Alpha", excd="NAS", expected_upside_pct=30.0)]}
        if track1 is not None:
            s_["track1"] = track1
        return s_

    def screen(s_, lm=lim):
        with contextlib.redirect_stdout(io.StringIO()):
            return rg.screen_proposals(s_, lm, wl, bal, {"AAA": {"price": 100.0}})

    def why(rej):
        return " / ".join(r["why"] for r in rej)
    p = []
    try:
        with case():
            g = fact("AAA", 1)                      # track1 MATCH T1
            g2 = fact("AAA", 2)                     # track1 MATCH T1 (배경으로 쓴다)
            t3 = fact("AAA", 3, tier=3, url="https://blog.example/aaa-3")   # track1 MATCH T3
            un = fact("AAA", 4)                     # 판정 없음
            uv = fact("AAA", 5)                     # track1 UNVERIFIABLE
            mm = fact("AAA", 6)                     # track1 MISMATCH
            verdicts([vrow(g, "MATCH", 1), vrow(g2, "MATCH", 1), vrow(t3, "MATCH", 3),
                      vrow(uv, "UNVERIFIABLE", 1, note="검색: 'AAA metric 5' · IR 페이지 · 23:58 — 원문을 열지 못함"),
                      vrow(mm, "MISMATCH", 1, note="fetch.py 23:41 · 원문 값 15.9와 다르다")])
            acc, rej = screen(sig(t1ok, [g]))
            if not acc:
                p.append(f"검증 통과 근거인데 거부: {why(rej)}")
            elif [f["id"] for f in acc[0].get("facts") or []] != [g] or acc[0]["facts"][0].get("verdict") != "MATCH":
                p.append(f"승인 행에 근거 기록(id·판정)이 없다: {acc[0].get('facts')}")
            acc, rej = screen(sig(None, [g]))
            if acc or "주문 근거 검증 기록이 없다" not in why(rej) or "facts.py decide" not in why(rej):
                p.append(f"track1 기록 없는데 승인/다른 사유: {why(rej)}")
            acc, rej = screen(sig({**t1ok, "drop": ["AAA"], "pass": []}, [g]))
            if acc or "주문 근거 검증 미통과" not in why(rej):
                p.append(f"drop 종목인데 승인: {why(rej)}")
            acc, rej = screen(sig(t1ok, [g, un]))
            if acc or "UNVERIFIED_T1" not in why(rej) or un not in why(rej):
                p.append(f"판정 없는 받치는 사실이 섞였는데 승인(또는 id 없음): {why(rej)}")
            acc, rej = screen(sig(t1ok, [g, un, mm], roles={un: "background", mm: "against"}))
            if not acc:
                p.append(f"배경 사실 미검증·반대 사실 MISMATCH는 통과·제외를 바꾸지 않는다(facts.decide와 같은 규칙): {why(rej)}")
            acc, rej = screen(sig(t1ok, [t3]))
            if acc or "UNVERIFIED_T1" not in why(rej):
                p.append(f"T3만 MATCH인 근거로 승인: {why(rej)}")
            acc, rej = screen(sig(t1ok, [uv, g2], roles={g2: "background"}))
            if acc or "MATCH(T1/T2)로 확인된 근거가 하나도 없다" not in why(rej):
                p.append(f"받치는 사실이 UNVERIFIABLE이고 T1은 배경뿐인데 승인: {why(rej)}")
            acc, rej = screen(sig(t1ok, [g2], roles={g2: "against"}))
            if acc or "role=support) 0개" not in why(rej):
                p.append(f"받치는 사실 0개(반대 사실만 T1)인데 승인: {why(rej)}")
            acc, rej = screen(sig(t1ok, [g, mm], roles={mm: "background"}))
            if acc or "MISMATCH" not in why(rej) or mm not in why(rej):
                p.append(f"MISMATCH 근거가 섞였는데 승인(또는 id 없음): {why(rej)}")
            acc, rej = screen(sig(t1ok, [g, "F-999999"]))
            if acc or "근거 사실 없음" not in why(rej) or "F-999999" not in why(rej):
                p.append(f"저장소에 없는 사실 id로 승인: {why(rej)}")
            for k in ("주문 근거 검증", "근거 사실 없음", "MISMATCH", "MATCH(T1/T2)"):
                if not any(k in v for v in rg.VALIDITY_REJECTS):
                    p.append(f"'{k}' 거부가 타당성 거부 목록에 없다(논지 armed 유지·미달 계산이 틀린다)")
            off = dict(lim, fc_track1_required=False)
            acc, rej = screen(sig(None, [g]), off)
            if not acc:
                p.append(f"플래그를 끄면 track1 없이 승인돼야 한다: {why(rej)}")
            strict = dict(off, reject_rules={**off["reject_rules"], "require_match_t1t2_for_buy": True})
            acc, rej = screen(sig(None, [t3]), strict)
            if acc or "BUY에 MATCH(T1/T2) 근거가 없다" not in why(rej):
                p.append(f"require_match인데 T3 근거로 승인: {why(rej)}")
            # 이번 run 판정이 없으면(다른 run 시그널) 사실의 검증 상태로 — verified T1은 서고 disputed는 MISMATCH
            acc, rej = screen(sig(None, [g], run_id="261020_us"), strict)
            if not acc:
                p.append(f"검증 상태 verified(T1) 사실을 다른 run에서 못 썼다: {why(rej)}")
            dd = fact("AAA", 7)                     # 앞 run에서 MISMATCH · 교정 전(disputed)
            verdicts([vrow(dd, "MISMATCH", 1, batch="A", run_id=db.run_id("261012", "us"))], corrections=False)
            if (db.q1("SELECT verified_status FROM ev_fact WHERE id=?", (dd,)) or {}).get("verified_status") != "disputed":
                p.append("심기 실패 — 교정 전 MISMATCH 사실이 disputed가 아니다")
            acc, rej = screen(sig(None, [g, dd], roles={dd: "background"}, run_id="261020_us"), off)
            if acc or "MISMATCH" not in why(rej) or dd not in why(rej):
                p.append(f"disputed 사실(이번 run 판정 없음)로 승인: {why(rej)}")
            acc, rej = screen(sig(None, [g, mm], roles={mm: "background"}, run_id="261020_us"), off)
            if not acc:
                p.append(f"교정된 사실(검증 상태 none — 다시 판정 대기)을 MISMATCH로 막았다: {why(rej)}")
    finally:
        rg.portfolio_equity = saved_pe
    report("(9) risk_guard — track1 기록·drop·판정 없는 근거(UNVERIFIED_T1)·T3·받치는 사실 0·MISMATCH·없는 id를 저장소로 거부"
           "(플래그 off면 검증 상태로)", p)


def t09b_validate_signal_facts_schema():
    """스키마 — 근거는 facts[{id, role, support}]만 받는다. 옛 evidence만 있는 제안은 run 전체 거부(호환 경로 없음)."""
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    now = datetime.now(KST)
    p = []

    def check(prop, want):
        s_ = signal([prop])
        try:
            rg.validate_signal(s_, lim, now)
            got = ""
        except rg.Rejection as e:
            got = str(e)
        if want and want not in got:
            p.append(f"'{want}' 거부여야 한다: {got or '통과'} · {json.dumps(prop, ensure_ascii=False)[:80]}")
        if not want and got:
            p.append(f"통과여야 한다: {got}")
    old = {"ticker": "AAA", "action": "BUY", "thesis": "t", "confidence": 0.7,
           "evidence": [{"claim": "x", "source_url": "https://example.com/a", "tier": "T1", "verdict": "MATCH"}]}
    check(old, "옛 evidence 칸만 있다")
    check({**old, "facts": [{"id": "F-000001", "role": "support", "support": "논지 — 실적 다리"}]}, "")
    check(proposal("AAA", "BUY", ["X-1"]), "사실 id 형식이 아니다")
    check(proposal("AAA", "BUY", ["F-000001"], roles={"F-000001": "maybe"}), "role은")
    check({**proposal("AAA", "BUY", []), "facts": [{"id": "SRC-0012", "role": "support", "support": " "}]}, "support(")
    check({**proposal("AAA", "BUY", []), "facts": "F-000001"}, "facts가 배열이 아니다")
    check(proposal("AAA", "BUY", ["SRC-0012", "F-000731"]), "")
    s_ = signal([proposal("AAA", "BUY", ["F-000001"])], refill_candidates=[{**old, "ticker": "BBB"}])
    try:
        rg.validate_signal(s_, lim, now)
        p.append("재투자 후보의 옛 evidence를 통과시켰다")
    except rg.Rejection as e:
        if "refill_candidates[0]" not in str(e):
            p.append(f"재투자 후보 거부 사유: {e}")
    report("(9-b) validate_signal — 근거는 facts[{id, role, support}] · 옛 evidence만 있으면 전체 거부 · 재투자 후보도 같다", p)


def t11_reversal_sells_generator():
    p = []
    with case():
        testkit.seed(reversals=[
            {"id": f"REV-{ST}-kr-{i}", "stamp": ST, "market": mk, "ticker": tk, "qty": q, "why": "근거 MISMATCH", "status": "pending",
             "kind": "buy_unsupported", "buy_ref": f"approved_{ST}_{mk.lower()}.json", "fc_rows": ["ev_verdict:1"]}
            for i, (mk, tk, q) in enumerate(
                [("KR", "005930", 5), ("KR", "000660", 2), ("KR", "035420", 1), ("US", "AAPL", 3)], 1)])
        if len(rg.reversals_pending("KR")) != 3:
            p.append(f"심은 되돌림 대기 행이 저장소에서 안 보인다: {db.reversals_all()}")
        snap = {"balance": {"positions": [
            {"ticker": "005930", "name": "삼성전자", "qty": 3, "price": 71050},
            {"ticker": "000660", "name": "SK하이닉스", "qty": 4, "price": 180000}]},
            "prices": {"005930": {"price": 71050}, "000660": {"price": 180000}}}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            out = rg.audit_reversal_sells(snap, "KR", already={"000660"})
        if [(o["ticker"], o["qty"], o["full_exit"], o["source"]) for o in out] != [("005930", 3, True, "audit_reversal")]:
            p.append(f"보유 3 < 대기 5 → 3주 전량 · 이미 규율 매도가 있는 종목은 건너뜀: {out}")
        if out and (out[0]["price"] % 50 != 0 or not out[0].get("exempt_from_count_limit") or out[0].get("leg_kind") != "stop"
                    or out[0].get("reversal_id") != f"REV-{ST}-kr-1"):
            p.append(f"국내 호가 단위·건수 면제·손절급 표시·되돌림 id: {out[0]}")
        st_ = {r["ticker"]: r["status"] for r in db.reversals_all()}
        if st_ != {"005930": "pending", "000660": "pending", "035420": "void", "AAPL": "pending"}:
            p.append(f"보유 0 종목은 void · 다른 시장은 그대로: {st_}")
    report("(11) risk_guard — 감사 소급 대기 행(저장소) → 규율급 SELL(보유 상한 · 보유 0이면 void · 다른 시장 무시)", p)


def t12_reversal_settle():
    """체결 확정이 되돌림을 닫는다 — fill._settle_reversal(옛 reversals.settle_trade 규칙 · 저장소 ex_reversal_event)."""
    import fill
    p = []
    with case():
        testkit.seed(reversals=[{"id": f"REV-{ST}-kr-1", "stamp": ST, "market": "KR", "ticker": "005930", "qty": 5,
                                 "why": "근거 MISMATCH", "status": "pending", "kind": "buy_unsupported",
                                 "buy_ref": f"approved_{ST}_kr.json", "fc_rows": ["ev_verdict:1"]}])
        t = {"ticker": "005930", "market": "KR", "action": "SELL", "source": "audit_reversal",
             "result": {"order_no": "001"}, "fill": {"verdict": "PARTIAL", "filled_qty": 2}}
        q_ = lambda *x, **k: None  # noqa: E731
        fill._settle_reversal(t, log=q_)
        fill._settle_reversal(t, log=q_)                              # 같은 주문번호 두 번 — 한 번만
        r = db.reversals_all()[0]
        if (r["status"], r["qty"]) != ("pending", 3):
            p.append(f"부분 체결 2/5 → 잔량 3 대기(두 번 불러도): {r}")
        if len(rg.reversals_pending("KR")) != 1:
            p.append("부분 체결 뒤 대기 행이 사라졌다(다음 run이 잔량을 보내야 한다)")
        import stage                                                  # 이어받기 `## 감사 소급` 절(옛 reversals.carry_block 자리)
        blk = stage.reversal_block("KR")
        if "## 감사 소급" not in blk or f"REV-{ST}-kr-1" not in blk or "자동 전송" not in blk:
            p.append(f"이어받기 절에 대기 행·자동 전송 안내가 없다: {blk[:160]!r}")
        fill._settle_reversal({**t, "source": "discipline", "result": {"order_no": "009"}}, log=q_)
        if db.reversals_all()[0]["qty"] != 3:
            p.append("규율 매도 체결이 되돌림 수량을 바꿨다")
        t2 = {**t, "result": {"order_no": "002"}, "fill": {"verdict": "FILLED", "delta": -3}}
        fill._settle_reversal(t2, log=q_)
        if db.reversals_all()[0]["status"] != "filled" or rg.reversals_pending("KR"):
            p.append(f"잔량 체결 → filled · 대기 0: {db.reversals_all()}")
        if "없음" not in stage.reversal_block("KR"):
            p.append("대기 0이면 이어받기 절에 '없음' 줄")
    report("(12) 되돌림 체결 확정 — 부분은 잔량 대기 · 주문번호로 멱등 · 규율 매도 무관 · 전량이면 filled(저장소)", p)


def t13_risk_guard_run_discipline_only_emits_reversal():
    """다음 run 경로 — 규율 전용 risk_guard.run이 저장소의 대기 행을 승인 파일 주문으로 싣는다(머리 포함)."""
    names = ("LIMITS_PATH", "KILL_PATH", "SIGNALS_DIR", "DATA_DIR", "JOURNAL_DIR")
    saved = {n: getattr(rg, n) for n in names}
    p = []
    try:
        with case() as tmp:
            testkit.seed(watchlist=WATCHLIST_DOC, reversals=[
                {"id": f"REV-{ST}-kr-1", "stamp": ST, "market": "KR", "ticker": "005930", "qty": 4, "why": "근거 MISMATCH(테스트)",
                 "status": "pending", "kind": "buy_unsupported", "buy_ref": f"approved_{ST}_kr.json", "fc_rows": ["ev_verdict:1"]}])
            if not db.companies("KR") or len(rg.reversals_pending("KR")) != 1:
                p.append("심기 실패 — 감시 목록·되돌림 대기 행이 저장소에 안 보인다")
            cfg = tmp / "config"
            cfg.mkdir()
            lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
            lim["scenario_cond_required"] = False    # 형식 적용일은 그 형식을 재는 시험이 켠다
            (cfg / "limits.json").write_text(json.dumps(lim), encoding="utf-8")
            rg.LIMITS_PATH, rg.KILL_PATH = cfg / "limits.json", cfg / "KILL"
            rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR = tmp / "signals", tmp / "data", tmp / "journal"
            snap = {"market": "KR", "generated_at": datetime.now(KST).isoformat(), "day_pnl_pct": 0.0,
                    "balance": {"currency": "KRW", "cash": 10_000_000, "positions": [
                        {"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 10, "avg_price": 70000,
                         "price": 71000, "eval_amt": 710000, "pnl_pct": 1.43}]},
                    "prices": {"005930": {"price": 71000, "change_pct": 1.43}}}
            sp, out = tmp / "snap.json", tmp / "approved.json"
            sp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rg.run(None, sp, out, discipline_only=True)
            ap = read_json(out) if out.exists() else {}
            o = [x for x in ap.get("orders") or [] if x.get("source") == "audit_reversal"]
            if [(x["ticker"], x["qty"], x["full_exit"]) for x in o] != [("005930", 4, False)]:
                p.append(f"승인 파일에 되돌림 4주(부분)가 없다: {ap.get('orders')} · 거부 {ap.get('rejected')}")
            if not ap.get("limits_sha256") or ap.get("discipline_only") is not True:
                p.append("승인 파일 머리(한도 해시·discipline_only)가 없다 — execute.preflight가 거부한다")
    finally:
        for n, v in saved.items():
            setattr(rg, n, v)
    report("(13) risk_guard.run 규율 전용 — 저장소 대기 행이 승인 파일 주문으로 실린다(머리 그대로)", p)


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


def t14_batches_a_b():
    """배경화(2026-10-14) — 사실 큐 묶음 A·B(B는 A에 없는 사실만 · 번호 이어서) · 고정된 묶음은 다시 뽑지 않는다 ·
    클레임 목록 렌더본은 덮어쓰지 않는다. (옛 extract_ai_claims의 노트 줄 뽑기 대신 이 run이 가리킨 사실 = 큐)"""
    p = []
    with case() as tmp:
        a, b = fact("AAA", 1), fact("BBB", 1)
        note = f"분석노트_{ST}_{MK}_v1_0.md"
        for f in (a, b):                                    # 노트 §2가 가리킨 사실(render-note가 다는 참조) = 묶음 A
            facts.add_ref(f, (ST, MK), "note", note, "§2", role="note")
        rows_a, _ = facts.queue(ST, MK, "A")
        if [(r["fact_id"], r["n"]) for r in rows_a] != [(a, 1), (b, 2)]:
            p.append(f"묶음 A: {[(r['fact_id'], r['n']) for r in rows_a]}")
        c = fact("CCC", 1)
        facts.add_ref(c, (ST, MK), "note", note, "§6", role="note")   # §3 이후 = 묶음 B
        rows_b, _ = facts.queue(ST, MK, "B")
        if [(r["fact_id"], r["n"]) for r in rows_b] != [(c, 3)]:
            p.append(f"묶음 B(A에 없는 사실만 · 번호 3부터): {[(r['fact_id'], r['n']) for r in rows_b]}")
        again, _ = facts.queue(ST, MK, "A")
        if [r["fact_id"] for r in again] != [a, b]:
            p.append(f"고정된 묶음 A를 다시 뽑았다: {[r['fact_id'] for r in again]}")
        out = tmp / "analysis" / "claims_A.md"
        rc1, _ = facts.render_claims(ST, MK, "A", out_path=str(out))
        rc2, _ = facts.render_claims(ST, MK, "A", out_path=str(out))
        txt = out.read_text(encoding="utf-8") if out.exists() else ""
        if rc1 != 0 or rc2 != 3:
            p.append(f"클레임 목록 렌더 rc={rc1},{rc2}(0·3이어야 — 고정본 덮어쓰기 금지)")
        if "1. [" not in txt or "↳ 대조" not in txt:
            p.append("클레임 목록에 번호 줄·대조 줄이 없다")
    report("(14) 배경화 — 사실 큐 묶음 A·B(B는 A에 없는 것만 · 번호 이어서) · 고정 묶음 재추출 금지 · 렌더본 덮어쓰기 금지", p)


def t16_decide_and_risk_guard_same_rule():
    """같은 입력에 facts.decide(track1 pass·drop)와 risk_guard 이중 검사가 같은 결론인가 — 역할×판정 조합 표.
    규칙(2026-10-17): 통과 = 받치는 사실(support) 중 MATCH(T1/T2) ≥1 · support에 미검증(UNVERIFIED_T1)·MISMATCH 없음 ·
    배경(background) 사실에 MISMATCH 없음 · 반대(against) 사실은 영향 없음. risk_guard는 decide의 pass 목록을 빼고(전부 pass로 둔
    track1 블록) 자기 규칙만으로 재서 decide 결과와 대조한다 — 둘이 갈리면 pass 목록과 승인 결과가 어긋난다."""
    combos = [  # (종목, [(역할, 판정)], 기대 통과)
        ("P01", [("support", "M1")], True),
        ("P02", [("support", "M3")], False),
        ("P03", [("support", "UV")], False),
        ("P04", [("support", "M1"), ("support", "UV")], True),
        ("P05", [("support", "M1"), ("support", "M3")], False),
        ("P06", [("support", "M1"), ("background", "M3")], True),
        ("P07", [("support", "M1"), ("background", "MM")], False),
        ("P08", [("support", "M1"), ("against", "MM")], True),
        ("P09", [("background", "M1")], False),
        ("P10", [("against", "M1")], False),
        ("P11", [("support", "M1"), ("support", "MM")], False),
        ("P12", [("support", "M1"), ("against", "M3")], True),
        ("P13", [("support", "UV"), ("background", "M1")], False),
    ]
    p = []
    saved_pe = rg.portfolio_equity
    rg.portfolio_equity = lambda m, b: {"krw": 1.3e9, "cash_krw": 1.3e9, "invested_krw": 0.0, "ok": True,
                                        "asof": "t", "fx": 1300.0, "parts": {}, "why": "t"}
    try:
        with case() as tmp:
            props, vrows = [], []
            for tk, slots, _ in combos:
                fids, roles = [], {}
                for k, (role, v) in enumerate(slots, 1):
                    tier = 3 if v == "M3" else 1
                    f = fact(tk, k, tier=tier, url=f"https://src.example/{tk.lower()}-{k}")
                    fids.append(f)
                    roles[f] = role
                    if v == "UV":
                        vrows.append(vrow(f, "UNVERIFIABLE", 1, note="검색: 'metric' · IR 페이지 · 23:58 — 원문을 열지 못함"))
                    elif v == "MM":
                        vrows.append(vrow(f, "MISMATCH", 1, note="fetch.py 23:41 · 원문 값 9.9와 다르다"))
                    else:
                        vrows.append(vrow(f, "MATCH", tier))
                props.append(proposal(tk, "BUY", fids, roles=roles, weight_target_pct=3.0, expected_upside_pct=30.0, excd="NAS"))
            spath = write_signal(tmp, signal(props))
            db.import_run_artifacts(ST, MK, here=tmp)
            facts.queue(ST, MK, "track1")
            verdicts(vrows)
            rc, _, err = decide(sig_path=spath, tmp=tmp)
            t1 = read_json(spath).get("track1") or {}
            if rc != 0:
                p.append(f"decide pre rc={rc} {err[:160]!r}")
            want = sorted(tk for tk, _, ok in combos if ok)
            if t1.get("pass") != want:
                p.append(f"decide 통과 {t1.get('pass')} (규칙 기대 {want})")
            lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
            lim.update({"fc_track1_required": True, "concentration_disclose_pct": {}})
            lim["reject_rules"] = {**(lim.get("reject_rules") or {}), "reject_if_any_mismatch": True,
                                   "require_match_t1t2_for_buy": False}
            wl = {"US": [{"ticker": tk, "name": tk, "excd": "NAS"} for tk, _, _ in combos]}
            bal = {"cash": 1_000_000, "currency": "USD", "positions": [], "exchange_rate": 1300.0}
            sig = {**read_json(spath), "track1": {"result": "PASS", "audit": "PENDING", "pass": [tk for tk, _, _ in combos], "drop": []}}
            with contextlib.redirect_stdout(io.StringIO()):
                acc, rej = rg.screen_proposals(sig, lim, wl, bal, {tk: {"price": 100.0} for tk, _, _ in combos})
            got = sorted(o["ticker"] for o in acc)
            if got != sorted(t1.get("pass") or []):
                p.append(f"risk_guard 단독 규칙 승인 {got} ≠ decide 통과 {t1.get('pass')}")
            fact_reason = ("MISMATCH", "UNVERIFIED_T1", "MATCH(T1/T2)", "근거 사실 없음")
            other = [(r["ticker"], r["why"][:60]) for r in rej if not any(k in r["why"] for k in fact_reason)]
            if other:
                p.append(f"근거 규칙이 아닌 사유로 거부(대조가 흐려진다): {other}")
    finally:
        rg.portfolio_equity = saved_pe
    report("(16) 같은 입력 — facts.decide 통과·제외와 risk_guard 이중 검사가 같은 결론(역할×판정 13조합)", p)


# ───────────────────────────────────────────────────────────── (15) 주문 경로 종단

class FakeBroker:
    """덕 타이핑 가짜 증권사(국내만) — 주문에 번호를 주고 상태 조회 첫 폴링에 전량 체결로 답한다. 네트워크 없음."""
    current = None

    def __init__(self, prices=None, positions=None, cash=10_000_000.0):
        self.svr, self.mode, self.kr_excg, self.read_only = "paper", "paper", "KRX", False
        self.calls, self.orders = [], {}
        self.prices = dict(prices or {})
        self.positions = {p["ticker"]: dict(p) for p in (positions or [])}
        self.cash = cash
        self._n = 1000
        FakeBroker.current = self

    @classmethod
    def for_mode(cls, orders=False, cfg=None):
        return cls.current

    def domestic_price(self, ticker):
        return {"ticker": ticker, "price": float(self.prices.get(ticker, 70000)), "change_pct": 0.0}

    def domestic_balance(self):
        pos = [dict(p, market="KR", price=self.prices.get(p["ticker"], p.get("avg_price", 0)))
               for p in self.positions.values() if int(p.get("qty") or 0) > 0]
        for x in pos:
            x["eval_amt"] = float(x["price"]) * int(x["qty"])
        return {"currency": "KRW", "cash": self.cash, "positions": pos}

    def domestic_daily(self, ticker, start="", end=""):
        return []

    def domestic_order(self, ticker, side, qty, price=0, market_order=False, excg=None):
        self._n += 1
        no = f"{self._n:010d}"
        self.calls.append(("domestic_order", {"ticker": ticker, "side": side, "qty": qty, "price": price}))
        self.orders[no] = {"ticker": ticker, "side": side, "qty": int(qty), "price": float(price), "applied": False}
        return {"order_no": no, "order_time": "000000", "orgno": "00950", "excg": excg, "raw": {"rt_cd": "0"}}

    def domestic_order_status(self, order_no, ticker="", date=""):
        o = self.orders.get(str(order_no))
        if not o:
            return {"order_no": order_no, "found": False, "ord_qty": 0, "filled_qty": 0, "remain_qty": 0, "avg_price": 0.0,
                    "cancelled": False, "rejected": False, "reject_reason": "", "orgno": "", "raw": {}}
        if not o["applied"]:
            o["applied"] = True
            p = self.positions.setdefault(o["ticker"], {"ticker": o["ticker"], "qty": 0, "avg_price": o["price"]})
            p["qty"] = int(p.get("qty") or 0) + (o["qty"] if o["side"] == "BUY" else -o["qty"])
        return {"order_no": order_no, "found": True, "ord_qty": o["qty"], "filled_qty": o["qty"], "remain_qty": 0,
                "avg_price": o["price"], "cancelled": False, "rejected": False, "reject_reason": "", "orgno": "00950", "raw": {}}

    def domestic_modify(self, *a, **k):
        self.calls.append(("domestic_modify", k))
        return {"order_no": "9999999999"}

    def order_calls(self):
        return [c for c in self.calls if c[0] == "domestic_order"]


def _open_session(*a, **k):
    now = datetime.now(KST)
    return {"market": "KR", "open": now - timedelta(hours=1), "close": now + timedelta(hours=1), "is_open": True,
            "holiday": False, "session_date": now.date(), "why": "테스트 — 열림"}


@contextlib.contextmanager
def _order_env(tmp: Path):
    """집행 모듈 전역을 임시 폴더·가짜 증권사로 갈아끼운다(test_execute_path와 같은 자리) — 끝나면 되돌린다."""
    import execute as ex
    import fill
    cfg = tmp / "config"
    cfg.mkdir(exist_ok=True)
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim.update({"account_mode": "paper", "real_trading_enabled": False, "halt_all": False, "fc_track1_required": True,
                "scenario_cond_required": False,       # 형식 적용일은 그 형식을 재는 시험이 켠다
                # 이 시험은 주문 근거 경로가 목적이다 — 미달 최소 건수·축(사용자 정책 · (59) 케이스가 따로 본다)을 이 임시 한도에서만 끈다.
                # 켜 두면 승인 파일이 deficit_short=true가 되고, 집행기는 미달 표시가 참인 승인을 보내지 않는다(R03).
                "min_proposals_below_target": None, "min_distinct_axes_below_target": None})
    (cfg / "limits.json").write_text(json.dumps(lim, ensure_ascii=False), encoding="utf-8")
    saved = {"rg": (rg.LIMITS_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR),
             "ex": (ex.JOURNAL_DIR, ex.KisClient, ex.market_session, ex.us_daytime_session, ex._send_cfg),
             "fill": (fill.JOURNAL_DIR, fill.LIMITS_PATH, fill.confirm), "argv": list(sys.argv),
             "temp": os.environ.get("TEMP")}
    os.environ["TEMP"] = str(tmp / "gate")         # 게이트 통과 기록 폴더 격리(R03 — 집행기가 통과 기록을 확인한다)
    rg.LIMITS_PATH, rg.KILL_PATH = cfg / "limits.json", cfg / "KILL"
    rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR = tmp / "signals", tmp / "data", tmp / "journal"
    ex.JOURNAL_DIR = tmp / "journal"
    ex.KisClient = FakeBroker
    ex.market_session = ex.us_daytime_session = _open_session
    ex._send_cfg = lambda: {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 0}
    fill.JOURNAL_DIR, fill.LIMITS_PATH = tmp / "journal", cfg / "limits.json"
    _orig = saved["fill"][2]
    fill.confirm = lambda *a, **k: _orig(*a, **{**k, "sleep_fn": lambda s: None, "quiet": True})
    try:
        yield ex
    finally:
        (rg.LIMITS_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR) = saved["rg"]
        (ex.JOURNAL_DIR, ex.KisClient, ex.market_session, ex.us_daytime_session, ex._send_cfg) = saved["ex"]
        (fill.JOURNAL_DIR, fill.LIMITS_PATH, fill.confirm) = saved["fill"]
        sys.argv = saved["argv"]
        if saved["temp"] is None:
            os.environ.pop("TEMP", None)
        else:
            os.environ["TEMP"] = saved["temp"]


def _gate_pass(ex, apath):
    """전송 직전 점검(judge) 통과 흉내 — 실제 게이트 엔진으로 이 승인 파일 바이트의 해시를 서명해 남긴다(R03)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_sg_t1", str(ex.STEPGATE_PY))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.append_pass_record("trade-run", "dispatch", "d_test", {"approved": str(apath), "deliverable": str(apath)})


def t15_order_path_end_to_end():
    """시그널(facts) → db.import_run_artifacts → facts queue·verdict·lint·decide(pre) → risk_guard(가짜 증권사 시세) → execute 전송 →
    체결 확정 → 저장소 이전 → 감사 FAIL·재채점 → decide(post) 되돌림 → 다음 run risk_guard가 되돌림 매도를 싣는다."""
    st, mk = "261013", "kr"
    rid = db.run_id(st, mk)
    kr = [w["ticker"] for w in WATCHLIST_DOC.get("KR") or [] if w.get("ticker")]
    A, B, R = kr[0], kr[1], kr[2]
    p = []
    with case() as tmp:
        testkit.seed(watchlist=WATCHLIST_DOC)
        run = (st, mk)
        a1, b1, r1, h1 = fact(A, 1, run=run), fact(B, 1, run=run), fact(R, 1, run=run), fact("XOM", 1, run=run)
        sig = signal([proposal(A, "BUY", [a1]), proposal(B, "BUY", [b1])], run=run,
                     holds=[{"ticker": "XOM", "why": "보유 유지", "facts": [{"id": h1}]}],
                     refill_candidates=[proposal(R, "BUY", [r1])])
        spath = write_signal(tmp, sig, run=run)
        # ① 시그널 → 저장소(ex_signal·ex_proposal·ex_proposal_fact·ex_hold_fact)
        imp = db.import_run_artifacts(st, mk, here=tmp)
        if imp.get("missing_facts"):
            p.append(f"등록한 사실을 missing으로 봤다: {imp.get('missing_facts')}")
        # ② 주문 근거 큐 → 판정 → 판정 검사 → 결정(pre)
        rows, _ = facts.queue(st, mk, "track1")
        q = {r["fact_id"] for r in rows}
        if not {a1, b1, r1, h1} <= q:
            p.append(f"track1 큐에 빠진 근거: {sorted({a1, b1, r1, h1} - q)}")
        verdicts([vrow(f, "MISMATCH" if f == b1 else "MATCH", 1, run_id=rid,
                       note="fetch.py 23:41 · 원문 값 10.9와 다르다" if f == b1 else "fetch.py 23:41 · 원문 표의 값과 같다")
                  for f in sorted(q)])
        errs, _, _, _ = facts.lint(st, mk, "track1")
        if errs:
            p.append(f"판정 검사 FAIL: {errs[:2]}")
        rc, out, err = decide(sig_path=spath, run=run, tmp=tmp)
        t1 = read_json(spath).get("track1") or {}
        if rc != 0 or t1.get("pass") != sorted([A, R]) or t1.get("drop") != [B]:
            p.append(f"decide pre: rc={rc} pass={t1.get('pass')} drop={t1.get('drop')} {err[:120]!r}")
        # ③ risk_guard — 시그널 파일의 track1 블록 + 저장소 판정으로 승인·거부
        with _order_env(tmp) as ex:
            fb = FakeBroker(prices={A: 70000, B: 180000, R: 50000})
            snap = {"market": "KR", "generated_at": datetime.now(KST).isoformat(), "day_pnl_pct": 0.0,
                    "balance": fb.domestic_balance(),
                    "prices": {A: {"price": 70000, "change_pct": 0.0}, B: {"price": 180000, "change_pct": 0.0}}}
            sp = tmp / "data" / f"snapshot_{st}_{mk}.json"
            sp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            apath = tmp / "signals" / f"approved_{st}_{mk}.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc_g = rg.run(spath, sp, apath)
            ap = read_json(apath) if apath.exists() else {}
            buys = [o for o in ap.get("orders") or [] if o.get("action") == "BUY"]
            rej = {r["ticker"]: r["why"] for r in ap.get("rejected") or []}
            if rc_g != 0 or [o["ticker"] for o in buys] != [A]:
                p.append(f"risk_guard 승인: rc={rc_g} {[o['ticker'] for o in buys]} 거부 {rej}")
            if "주문 근거 검증 미통과" not in rej.get(B, "") and "MISMATCH" not in rej.get(B, ""):
                p.append(f"근거가 무너진 {B} 매수를 막지 않았다: {rej.get(B)}")
            if buys and [f.get("id") for f in buys[0].get("facts") or []] != [a1]:
                p.append(f"승인 행에 근거 사실 기록이 없다: {buys[0].get('facts')}")
            if (ap.get("track1") or {}).get("drop") != [B]:
                p.append("승인 파일에 track1 블록(시그널에서 옮김)이 없다 — dispatch 게이트가 본다")
            # ④ 전송 → 체결 확정(가짜 증권사) → 거래 기록에 근거 사실이 실린다
            _gate_pass(ex, apath)                       # judge PASS(이 승인 파일 그 바이트) — 집행기가 확인한다(R03)
            sys.argv = ["execute.py", str(apath), "--send", "--stamp", st, "--max-sec", "60"]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc_x = ex.main()
            tf = tmp / "journal" / f"trades_{st}_{mk}.json"
            tr = (read_json(tf).get("trades") or []) if tf.exists() else []
            filled = [t for t in tr if t.get("ticker") == A and (t.get("fill") or {}).get("verdict") == "FILLED"]
            if rc_x != 0 or not filled or len(fb.order_calls()) != 1:
                p.append(f"전송·체결: rc={rc_x} 주문 {len(fb.order_calls())}건 기록 "
                         f"{[(t.get('ticker'), (t.get('fill') or {}).get('verdict')) for t in tr]} {buf.getvalue()[-160:]!r}")
            if filled and [f.get("id") for f in filled[0].get("facts") or []] != [a1]:
                p.append(f"거래 기록에 근거 사실(facts)이 실리지 않았다: {filled[0].get('facts')}")
            qty = int((filled[0].get("fill") or {}).get("filled_qty") or filled[0].get("qty") or 0) if filled else 0
        # ⑤ 체결 → 저장소(ex_order·ex_fill) → 감사 FAIL · 재채점 → decide(post) 되돌림
        db.import_run_artifacts(st, mk, here=tmp)
        if facts.filled_buys(st, mk).get(A) != qty or not qty:
            p.append(f"저장소 체결 매수 {facts.filled_buys(st, mk)} (기대 {A}: {qty})")
        verdicts([vrow(a1, "MISMATCH", 1, run_id=rid, note="감사 재채점 23:58 — 원문 값 10.9와 다르다")])
        rc, out, err = decide(mode="post", led=ledger(tmp, [("FAIL", "근거 값")], run=run), sig_path=spath, run=run, tmp=tmp)
        revs = [r for r in db.reversals_all() if r.get("ticker") == A]
        if rc != 0 or len(revs) != 1 or revs[0].get("qty") != qty or revs[0].get("status") != "pending":
            p.append(f"decide post 되돌림: rc={rc} {revs} {err[:120]!r}")
        # ⑥ 다음 run — 규율 전용 risk_guard가 되돌림 매도를 싣는다
        with _order_env(tmp):
            snap2 = {"market": "KR", "generated_at": datetime.now(KST).isoformat(), "day_pnl_pct": 0.0,
                     "balance": {"currency": "KRW", "cash": 1_000_000, "positions": [
                         {"market": "KR", "ticker": A, "name": A, "qty": qty, "avg_price": 70000, "price": 70000,
                          "eval_amt": 70000 * qty, "pnl_pct": 0.0}]},
                     "prices": {A: {"price": 70000, "change_pct": 0.0}}}
            sp2, out2 = tmp / "data" / "snapshot_next.json", tmp / "signals" / "approved_next.json"
            sp2.write_text(json.dumps(snap2, ensure_ascii=False), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rg.run(None, sp2, out2, discipline_only=True)
            o2 = [o for o in (read_json(out2).get("orders") if out2.exists() else []) or [] if o.get("source") == "audit_reversal"]
            if [(o["ticker"], o["qty"]) for o in o2] != [(A, qty)] or (o2 and o2[0].get("reversal_id") != (revs or [{}])[0].get("id")):
                p.append(f"다음 run 되돌림 매도: {[(o.get('ticker'), o.get('qty'), o.get('reversal_id')) for o in o2]}")
    report("(15) 주문 경로 종단 — 시그널 facts → 저장소 → 큐·판정·검사·결정 → risk_guard → 전송·체결 → 감사 재채점 → 되돌림 → 다음 run 매도", p)


def t17_post_stale_support_records_not_sells():
    """낡은 근거(FB-261017-9 · "위험 없게") — 통과한 체결 매수의 받치는 사실 하나가 STALE이면 post는 팔지 않고 stale_support로 낸다
    (TRACK1 POST 줄 · ex_signal.track1.post) · 그 사실을 다시 확인해 새 판정이 MISMATCH면 post가 소급(buy_ref·fc_rows 있음) ·
    새 판정이 MATCH면 stale_support에서 빠진다."""
    p = []
    for final in ("MISMATCH", "MATCH"):
        with case() as tmp:
            ids = base_case(tmp)
            rows, _ = facts.queue(ST, MK, "track1")
            verdicts([vrow(r["fact_id"], "MATCH", 1) for r in rows])
            decide(sig_path=ids["path"], tmp=tmp)
            trades(tmp, [("AAA", "FILLED", 5, None)])
            verdicts([vrow(ids["a2"], "STALE", 1, note="감사 재채점 23:58 — as_of 2026-07-01 · 분기 갱신 전 값")])
            led = ledger(tmp, [("FAIL", "L6 근거 시효")])
            rc, out, err = decide(mode="post", led=led, sig_path=ids["path"], tmp=tmp)
            post = (read_json(ids["path"]).get("track1") or {}).get("post") or {}
            if rc != 0 or db.reversals_all():
                p.append(f"[{final}] STALE 하나 + 받치는 MATCH(T1) — 팔면 안 된다: rc={rc} {db.reversals_all()} {err[:120]!r}")
            if f"stale_support=AAA({ids['a2']})" not in out or post.get("stale_support") != [{"ticker": "AAA", "facts": [ids["a2"]]}]:
                p.append(f"[{final}] stale_support 기록: {out[:240]!r} {post.get('stale_support')}")
            if "※ 낡은 근거 AAA" not in out or "stage.py close" not in out:
                p.append(f"[{final}] 같은 run에서 끝내라는 안내가 없다: {out[-300:]!r}")
            # ① 다시 확인 — 교정(verdicts가 correct를 잇는다) 뒤 새 판정 → post 다시
            verdicts([vrow(ids["a2"], final, 1, note="재확인 00:20 — 출처 원문 다시 열람 · 분기 갱신 값")])
            rc2, out2, err2 = decide(mode="post", led=led, sig_path=ids["path"], tmp=tmp)
            post2 = (read_json(ids["path"]).get("track1") or {}).get("post") or {}
            revs = db.reversals_all()
            if final == "MISMATCH":
                if rc2 != 0 or [(r["ticker"], r["qty"], r["kind"]) for r in revs] != [("AAA", 5, "buy_unsupported")]:
                    p.append(f"재확인 MISMATCH → 소급 1: rc={rc2} {revs} {err2[:120]!r}")
                elif revs[0].get("buy_ref") != f"approved_{ST}_{MK}.json" or not revs[0].get("fc_rows"):
                    p.append(f"소급 행의 도구 출처(buy_ref·fc_rows): {revs[0]}")
                if post2.get("stale_support"):
                    p.append(f"제외된 매수가 stale_support에 남았다: {post2.get('stale_support')}")
            else:
                if rc2 != 0 or revs or post2.get("stale_support") or "stale_support=-" not in out2:
                    p.append(f"재확인 MATCH → stale_support 해소: rc={rc2} {revs} {post2.get('stale_support')} {out2[:200]!r}")
    report("(17) post 낡은 근거 — 팔지 않고 stale_support로 기록 · 재확인 MISMATCH면 post가 소급(도구 출처) · MATCH면 해소", p)


def t18_refill_fills_are_retro_eligible():
    """재투자 승인(approved_<st>_<mk>_refill_<tag>.json)으로 체결된 매수도 소급 대상이다 — 같은 근거 규칙 · buy_ref는 그 승인 파일."""
    p = []
    with case() as tmp:
        ids = base_case(tmp)
        rows, _ = facts.queue(ST, MK, "track1")
        verdicts([vrow(r["fact_id"], "MATCH", 1) for r in rows])
        decide(sig_path=ids["path"], tmp=tmp)
        ref = f"approved_{ST}_{MK}_refill_0142.json"
        trades(tmp, [("BBB", "FILLED", 3, None)], ref=ref)
        fb = facts.filled_buys(ST, MK, refs=True)
        if fb.get("BBB") != {"qty": 3, "refs": [ref]} or facts.filled_buys(ST, MK).get("BBB") != 3:
            p.append(f"재투자 승인 체결이 filled_buys에 없다: {fb}")
        verdicts([vrow(ids["b1"], "MISMATCH", 1, note="감사 재채점 23:58 — 원문 값 10.9와 다르다")])
        rc, out, err = decide(mode="post", led=ledger(tmp, [("FAIL", "근거 값")]), sig_path=ids["path"], tmp=tmp)
        revs = db.reversals_all()
        if rc != 0 or [(r["ticker"], r["qty"], r.get("buy_ref")) for r in revs] != [("BBB", 3, ref)]:
            p.append(f"재투자 매수 소급: rc={rc} {[(r.get('ticker'), r.get('qty'), r.get('buy_ref')) for r in revs]} {err[:120]!r}")
    report("(18) 재투자 승인 체결도 소급 대상 — filled_buys(refs) · buy_ref = 재투자 승인 파일", p)


def t19_consumers_void_hand_made_unsupported():
    """소급 행의 출처는 도구 하나 — buy_ref·fc_rows 없는 buy_unsupported 행은 소비자(rg.reversals_pending)가 void로 닫고
    SELL을 만들지 않는다 · 보유 재판정(hold_overturned) 행은 그대로 · `_reversal(kind=buy_unsupported)`는 출처 없으면 거부."""
    p = []
    with case():
        testkit.seed(reversals=[
            {"id": f"REV-{ST}-kr-1", "stamp": ST, "market": "KR", "ticker": "005930", "qty": 3, "why": "손으로 만든 소급",
             "status": "pending", "kind": "buy_unsupported"},
            {"id": f"REV-{ST}-kr-2", "stamp": ST, "market": "KR", "ticker": "000660", "qty": 2, "why": "근거 MISMATCH",
             "status": "pending", "kind": "buy_unsupported", "buy_ref": f"approved_{ST}_kr.json", "fc_rows": ["ev_verdict:3"]},
            {"id": f"REV-{ST}-kr-3", "stamp": ST, "market": "KR", "ticker": "035420", "qty": 1, "why": "보유 근거 판정 실패",
             "status": "pending", "kind": "hold_overturned"}])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pend = [r["id"] for r in rg.reversals_pending("KR")]
        if pend != [f"REV-{ST}-kr-2", f"REV-{ST}-kr-3"]:
            p.append(f"도구 행·보유 재판정 행만 남아야: {pend}")
        st_ = {r["id"]: r for r in db.reversals_all()}
        if st_[f"REV-{ST}-kr-1"]["status"] != "void" or "FB-261017-9" not in str(st_[f"REV-{ST}-kr-1"].get("note")) \
                or "void — 도구 출처 없음" not in err.getvalue():
            p.append(f"출처 없는 소급 행 void·알림: {st_[f'REV-{ST}-kr-1']} {err.getvalue()[:160]!r}")
        snap = {"balance": {"positions": [
            {"ticker": "005930", "qty": 10, "price": 71000}, {"ticker": "000660", "qty": 4, "price": 180000},
            {"ticker": "035420", "qty": 2, "price": 200000}]},
            "prices": {"005930": {"price": 71000}, "000660": {"price": 180000}, "035420": {"price": 200000}}}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            sells = rg.audit_reversal_sells(snap, "KR")
        if sorted(o["ticker"] for o in sells) != ["000660", "035420"]:
            p.append(f"손으로 만든 소급 행이 SELL이 됐거나 도구 행이 빠졌다: {[o['ticker'] for o in sells]}")
        try:
            facts._reversal(ST, MK, "AAA", 1, "판단으로 되판다", kind="buy_unsupported")
            p.append("출처 없는 buy_unsupported를 _reversal이 받았다")
        except ValueError as e:
            if "decide --mode post만 낸다" not in str(e):
                p.append(f"거부 문구: {e}")
    report("(19) 소비자 — 출처 없는 소급 행 void(SELL 없음) · 보유 재판정 행은 그대로 · _reversal이 출처 없는 소급 거부", p)


def t20_reroll_rejected_only_that_proposal():
    """거부 회피 재실행은 그 제안만 막는다(2026-10-19) — 1회차 B MISMATCH 거부 → B의 받치는 사실을 다른 MATCH 사실로
    바꿔 다시 결정·검사 → 2회차 B만 '거부 회피 재실행', A는 그대로 승인 → 전송(가짜 증권사) A 1건 · 회차 기억 2회차 →
    되돌려 3회차 → B는 MISMATCH로 다시 거부(같은 지문은 회피가 아니다)."""
    st, mk = "261013", "kr"
    rid = db.run_id(st, mk)
    kr = [w["ticker"] for w in WATCHLIST_DOC.get("KR") or [] if w.get("ticker")]
    A, B = kr[0], kr[1]
    p = []
    with case() as tmp:
        testkit.seed(watchlist=WATCHLIST_DOC)
        run = (st, mk)
        a1, b1, b2 = fact(A, 1, run=run), fact(B, 1, run=run), fact(B, 2, run=run)
        sig = signal([proposal(A, "BUY", [a1]), proposal(B, "BUY", [b1])], run=run)
        spath = write_signal(tmp, sig, run=run)
        db.import_run_artifacts(st, mk, here=tmp)
        rows, _ = facts.queue(st, mk, "track1")
        verdicts([vrow(f, "MISMATCH" if f == b1 else "MATCH", 1, run_id=rid,
                       note="fetch.py 23:41 · 원문 값 10.9와 다르다" if f == b1 else "fetch.py 23:41 · 원문 표의 값과 같다")
                  for f in sorted(r["fact_id"] for r in rows)])
        decide(sig_path=spath, run=run, tmp=tmp)

        def guard(ex, label):
            fb = FakeBroker(prices={A: 70000, B: 180000})
            snap = {"market": "KR", "generated_at": datetime.now(KST).isoformat(), "day_pnl_pct": 0.0,
                    "balance": fb.domestic_balance(),
                    "prices": {A: {"price": 70000, "change_pct": 0.0}, B: {"price": 180000, "change_pct": 0.0}}}
            sp = tmp / "data" / f"snapshot_{st}_{mk}.json"
            sp.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            apath = tmp / "signals" / f"approved_{st}_{mk}.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = rg.run(spath, sp, apath)
            ap = read_json(apath) if apath.exists() else {}
            if rc != 0:
                p.append(f"{label} risk_guard rc={rc}")
            return fb, apath, ap
        with _order_env(tmp) as ex:
            _, _, ap1 = guard(ex, "1회차")
            rj1 = {r["ticker"]: r for r in ap1.get("rejected") or []}
            if [o["ticker"] for o in ap1.get("orders") or [] if o.get("action") == "BUY"] != [A] \
                    or rj1.get(B, {}).get("why_class") != "validity" or (ap1.get("screen") or {}).get("pass") != 1:
                p.append(f"1회차: 승인 {[o['ticker'] for o in ap1.get('orders') or []]} 거부 {rj1} screen {ap1.get('screen')}")
        # B의 받치는 사실을 바꿔치기 — 새 사실 b2(MATCH)로 다시 결정
        sig2 = signal([proposal(A, "BUY", [a1]), proposal(B, "BUY", [b2])], run=run)
        spath = write_signal(tmp, sig2, run=run)
        db.import_run_artifacts(st, mk, here=tmp)
        facts.queue(st, mk, "track1", append=True)
        verdicts([vrow(b2, "MATCH", 1, run_id=rid)])
        decide(sig_path=spath, run=run, tmp=tmp)
        if (read_json(spath).get("track1") or {}).get("pass") != sorted([A, B]):
            p.append(f"바꿔치기 뒤 decide pre가 B를 통과시켜야 시험이 된다: {read_json(spath).get('track1')}")
        with _order_env(tmp) as ex:
            fb, apath, ap2 = guard(ex, "2회차")
            rj2 = {r["ticker"]: r for r in ap2.get("rejected") or []}
            buys2 = [o["ticker"] for o in ap2.get("orders") or [] if o.get("action") == "BUY"]
            if buys2 != [A] or rj2.get(B, {}).get("why_class") != "taint" or not str(rj2.get(B, {}).get("why")).startswith("거부 회피 재실행"):
                p.append(f"2회차: 승인 {buys2} · B 거부 {rj2.get(B)}")
            if [t.get("ticker") for t in (ap2.get("screen") or {}).get("tainted") or []] != [B] or (ap2.get("screen") or {}).get("pass") != 2:
                p.append(f"2회차 screen 블록: {ap2.get('screen')}")
            _gate_pass(ex, apath)                       # judge PASS(이 승인 파일 그 바이트) — 집행기가 확인한다(R03)
            sys.argv = ["execute.py", str(apath), "--send", "--stamp", st, "--max-sec", "60"]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc_x = ex.main()
            calls = [c[1].get("ticker") for c in fb.order_calls()]
            if rc_x != 0 or calls != [A]:
                p.append(f"전송: rc={rc_x} 브로커 주문 {calls} — 회피한 B만 빠지고 A는 나가야 한다 {buf.getvalue()[-160:]!r}")
        got = db.screen_rows(rid)
        if sorted({r["pass_no"] for r in got}) != [1, 2] or [r["outcome"] for r in got if r["ticker"] == B] != ["rejected", "rejected"]:
            p.append(f"회차 기억: {[(r['pass_no'], r['ticker'], r['outcome'], r['why_class']) for r in got]}")
        # 되돌림 — 1회차와 같은 제안이면 회피가 아니다(MISMATCH로 다시 판정)
        sig3 = signal([proposal(A, "BUY", [a1]), proposal(B, "BUY", [b1])], run=run)
        spath = write_signal(tmp, sig3, run=run)
        db.import_run_artifacts(st, mk, here=tmp)
        decide(sig_path=spath, run=run, tmp=tmp)
        with _order_env(tmp) as ex:
            _, _, ap3 = guard(ex, "3회차")
            rj3 = {r["ticker"]: r for r in ap3.get("rejected") or []}
            if rj3.get(B, {}).get("why_class") != "validity" or (ap3.get("screen") or {}).get("tainted"):
                p.append(f"3회차(되돌림): B 거부 {rj3.get(B)} · tainted {(ap3.get('screen') or {}).get('tainted')}")
    report("(20) 거부 회피 재실행은 그 제안만 — 회피한 B만 거부 · A는 승인·전송 · 회차 기억 · 되돌리면 회피 아님", p)


with testkit.db_case():                       # 어느 케이스도 실제 journal/trading.db를 열지 않는다(note_lint main 포함)
    for fn in (t01_order_basis_from_signal, t02_refuse_before_pass, t03_pre_drops_mismatch,
               t04_post_regrade_reverses_filled_buy, t05_post_idempotent_and_no_change_no_reversal, t06_t3_only_drops, t07_note_lint,
               t08_root_url_cannot_pass, t10_note_lint_note_mode, t09_risk_guard_enforces_track1, t09b_validate_signal_facts_schema,
               t11_reversal_sells_generator, t12_reversal_settle, t13_risk_guard_run_discipline_only_emits_reversal,
               t14_batches_a_b, t15_order_path_end_to_end, t16_decide_and_risk_guard_same_rule,
               t17_post_stale_support_records_not_sells, t18_refill_fills_are_retro_eligible, t19_consumers_void_hand_made_unsupported,
               t20_reroll_rejected_only_that_proposal):
        _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_track1.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
