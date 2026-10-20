#!/usr/bin/env python3
"""
판단 원장 저장소 전환(2026-10-17) 테스트 — 논지·시나리오·지도·일정·배분·유니버스·시총 상위가 저장소(journal/trading.db)를
거쳐서도 옛 명령과 같은 뜻으로 돌고, **동시에 쓰는 프로세스가 서로의 기록을 덮지 않는지**를 고정한다.

왜: 원장 JSON을 스크립트마다 통째로 읽고-고치고-썼다. 배경 판정 셋이 같은 시각에 scenarios.json을 저장하면 마지막 하나만
남았고(scenarios.save 6곳), 지도는 _write 8곳이 같은 파일을 덮었다. 이제 쓰기는 행 단위(`db.*_update(id, fn)`)이거나
읽기부터 한 트랜잭션이다. 이 파일은 임시 저장소(testkit.db_case)만 연다 — 실제 원장·시세·포인터는 건드리지 않는다.

    python3 test_judgment.py          # run_tests.py가 부른다
"""
import argparse
import atexit
import contextlib
import io
import json
import re
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import db                     # noqa: E402
import testkit                # noqa: E402
from testkit import db_case, seed   # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_judgment.py ran={n} failed={nf} last={_STATE['current']}")
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


def _run(fn, *a, **kw):
    """명령 함수를 조용히 부른다 → (반환값 또는 ('exit', 메시지), 출력)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = fn(*a, **kw)
        except SystemExit as e:
            rc = ("exit", str(e.code))
    return rc, buf.getvalue()


def _ns(**kw):
    return argparse.Namespace(**kw)


def _write(tmp: Path, name: str, obj) -> Path:
    p = tmp / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p


def _rendered(name: str):
    """db가 커밋 뒤 내보낸 렌더본(임시 저장소 옆 exports/)을 읽는다."""
    p = db.export_path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


AX1 = {"id": "ax1", "name": "AI 전력", "label": "AI전력", "thesis": "전력 수요가 늘어난다", "direction": "+",
       "expected": "+5%p", "horizon_weeks": 4, "confidence": 0.6, "status": "open", "opened": "2026-10-01",
       "names": [{"ticker": "AAA", "name": "에이", "sign": "+"}, {"ticker": "BBB", "name": "비", "sign": "+"}],
       "beneficiaries": [{"ticker": "AAA", "name": "에이", "order": 1, "priced": None, "market": "KR"}],
       "chain": [{"step": 1, "claim": "수요 증가", "status": "확인", "indicator": "전력 수요 지표"}]}
AX2 = {"id": "ax2", "name": "금리", "thesis": "금리가 내려간다", "direction": "-", "expected": "-", "horizon_weeks": 6,
       "confidence": 0.5, "status": "open", "names": [{"ticker": "CCC", "sign": "+"}]}
AX9 = {"id": "ax9", "name": "무관", "thesis": "건드리지 않는 축", "names": []}
SEC_KR = {"code": "0013", "market": "KR", "name": "전기·전자", "grade": "—", "status": None, "chains": [], "names": [],
          "next_event": None, "resolves_when": None, "updated": None}
SEC_US = {"code": "XLK", "market": "US", "name": "기술", "grade": "B", "status": "관찰", "chains": [], "names": [],
          "next_event": None, "resolves_when": None, "updated": "2026-10-10"}
TH_B = {"id": "t-bbb", "market": "KR", "ticker": "BBB", "name": "비", "axis": "AI전력", "axis_id": "ax1", "thesis": "b",
        "expires": "2099-12-31", "status": "armed",
        "entry_triggers": [{"id": "e1", "when": "가격", "check": "price <= 100", "size_pct": 2.0, "reach_prob": 0.6, "reach_n": 30}],
        "exit_triggers": []}


# ─────────────────────────────── 논지 ───────────────────────────────

def t1_thesis_roundtrip():
    """add → set-status → set-trigger(손절 내리기 거부 포함) → fire/undo → set-driver가 저장소에서 왕복한다."""
    import theses as th
    p = []
    with db_case() as tmp:
        seed(axes=[AX1, AX2], theses=[TH_B])
        db.connect().execute("INSERT INTO ev_fact (id, kind, verified_status, legacy_text) VALUES ('F-800001', 'legacy', 'verified', '전력 수요 사실')")
        new = {"id": "t-aaa", "market": "KR", "ticker": "AAA", "name": "에이", "axis": "AI전력", "axis_id": "ax1",
               "thesis": "전력 수요 수혜", "expires": "2099-12-31",
               "evidence": [{"fact_id": "F-800001", "role": "support", "claim": "전력 수요 사실"}],
               "entry_triggers": [{"id": "e0", "when": "선진입 2%", "check": "judge", "size_pct": 2}],
               "exit_triggers": [{"id": "x1", "when": "손절", "check": "price <= 90", "size_pct": 100, "basis": "지지 하단 — 과거 고정"},
                                 {"id": "x2", "when": "추적", "check": "from_peak_pct <= -8", "size_pct": 100, "basis": "추적"}]}
        rc, out = _run(th.cmd_add, _write(tmp, "new.json", new))
        t = db.thesis_get("t-aaa")
        if rc != 0 or t is None:
            p.append(f"add 실패: {rc} {out[-300:]}")
            report("(1) 논지 왕복 — add·status·set-trigger·fire·set-driver", p)
            return
        if (t.get("status"), t.get("price_driver"), t.get("forecast_id"), t.get("horizon_days")) != ("armed", "ax1", "ax1", 20):
            p.append(f"add 기본값: {t.get('status')} {t.get('price_driver')} {t.get('forecast_id')} {t.get('horizon_days')}")
        legs = db.q("SELECT src, leg_id, check_kind, level FROM jd_thesis_leg WHERE pid=(SELECT rid FROM jd_thesis WHERE id='t-aaa') ORDER BY src, ord")
        if ("exit_triggers", "x1", "price_le", 90.0) not in [tuple(r.values()) for r in legs]:
            p.append(f"다리가 jd_thesis_leg(check_kind·level)로 안 갈렸다: {legs}")
        rc, _ = _run(th.cmd_add, _write(tmp, "dup.json", new))
        if rc == 0:
            p.append("같은 id를 두 번 넣었다")
        # 근거는 저장소 사실 번호로(2026-10-19) — 근거 없음·번호 없음·없는 번호는 거부
        for ev, why in (([], "근거 없음"), ([{"claim": "번호 없음", "role": "support"}], "번호 없는 근거"),
                        ([{"fact_id": "F-999999", "role": "support"}], "없는 번호")):
            rc, out = _run(th.cmd_add, _write(tmp, "ev.json", {**new, "id": "t-ev", "evidence": ev}))
            if rc == 0 or "저장소 사실 번호" not in out + str(rc):
                p.append(f"{why} 논지를 받았다: {rc}")
        _run(th.cmd_status, "t-aaa", "held", "체결 10주")
        t = db.thesis_get("t-aaa")
        if t["status"] != "held" or not t.get("entered_on") or t["history"][-1]["note"] != "체결 10주":
            p.append(f"set-status: {t['status']} {t.get('entered_on')} {t.get('history')}")
        _run(th.cmd_set_trigger, "t-aaa", "x1", "price <= 95", "지지 하단 95 — 과거 고정", "올림")
        t = db.thesis_get("t-aaa")
        x1 = next(g for g in t["exit_triggers"] if g["id"] == "x1")
        if x1["check"] != "price <= 95" or t["corrections"][-1]["before"]["check"] != "price <= 90":
            p.append(f"set-trigger: {x1} {t.get('corrections')}")
        lv = db.q1("SELECT level FROM jd_thesis_leg WHERE leg_id='x1' AND pid=(SELECT rid FROM jd_thesis WHERE id='t-aaa')")
        if not lv or lv["level"] != 95.0:
            p.append(f"다리 level 컬럼이 안 따라왔다: {lv}")
        rev = db.theses.rev("t-aaa")
        rc, out = _run(th.cmd_set_trigger, "t-aaa", "x1", "price <= 80", "b", "내림")
        if rc == 0 or db.theses.rev("t-aaa") != rev:
            p.append("손절을 내리는 set-trigger가 통과했거나 거부된 뒤에도 행이 바뀌었다(트랜잭션이 안 되돌렸다)")
        _run(th.cmd_fire, "t-aaa", "x2", "10주 매도 · 주문 1")
        x2 = next(g for g in db.thesis_get("t-aaa")["exit_triggers"] if g["id"] == "x2")
        if not (x2.get("fired") is True and x2.get("fired_at") and x2.get("fired_note") == "10주 매도 · 주문 1"):
            p.append(f"fire: {x2}")
        _run(th.cmd_fire, "t-aaa", "x2", "되돌림", True)
        x2 = next(g for g in db.thesis_get("t-aaa")["exit_triggers"] if g["id"] == "x2")
        if "fired" in x2 or "fired_at" in x2:
            p.append(f"fire --undo: {x2}")
        _run(th.cmd_set_driver, "t-aaa", "ax1", "AI 전력 테마로 거래")
        if db.thesis_get("t-aaa")["corrections"][-1]["what"] != "price_driver ax1 → ax1 (set-driver)":
            p.append("set-driver가 교정 기록을 안 남겼다")
        rc, _ = _run(th.cmd_set_driver, "t-aaa", "ax2", "엉뚱한 축")
        if rc == 0:
            p.append("열린 축이 아닌 price_driver를 받았다")
        rc, out = _run(th.cmd_status, "nope", "held", "")
        if rc != ("exit", "그런 id가 없다: nope"):
            p.append(f"없는 id: {rc}")
        if db.thesis_get("t-bbb") != TH_B:
            p.append("다른 논지(t-bbb)가 바뀌었다")
        rend = _rendered("theses")
        if not rend or {x["id"]: x["status"] for x in rend["theses"]} != {"t-bbb": "armed", "t-aaa": "held"}:
            p.append(f"렌더본 theses.json이 저장소를 안 따라왔다: {rend and [x['id'] for x in rend['theses']]}")
        data = th.load()
        if [x["id"] for x in data["theses"]] != ["t-bbb", "t-aaa"] or data.get("schema_version") != "1.0":
            p.append(f"load 모양: {list(data)} {[x['id'] for x in data['theses']]}")
    report("(1) 논지 왕복 — add·status·set-trigger(내리기 거부·되돌림)·fire·set-driver·렌더본", p)


def t2_thesis_concurrent_save():
    """load→save 사이에 다른 프로세스가 **다른 논지**를 고치면 둘 다 남고, **같은 논지**를 고치면 StaleWrite로 멈춘다."""
    import theses as th
    p = []
    with db_case():
        a = dict(TH_B, id="t-aaa", ticker="AAA")
        seed(axes=[AX1], theses=[a, TH_B])
        other = db.DocStore(db.SPEC["thesis"])          # 다른 프로세스의 통째 읽기
        docs = other.load()
        db.thesis_mark_fired("t-bbb", "e1", note="체결 1주")   # 그 사이 fill.py가 다른 논지에 체결 표시
        next(x for x in docs if x["id"] == "t-aaa")["status"] = "closed"
        try:
            other.save(docs)
        except db.StaleWrite as e:
            p.append(f"안 고친 행 때문에 거짓 충돌: {e}")
        if db.thesis_get("t-aaa")["status"] != "closed" or db.thesis_get("t-bbb")["entry_triggers"][0].get("fired") is not True:
            p.append("한쪽 기록이 사라졌다(덮어쓰기 유실)")
        docs = other.load()
        db.thesis_set_status("t-aaa", "held", note="다른 프로세스")
        next(x for x in docs if x["id"] == "t-aaa")["status"] = "expired"
        try:
            other.save(docs)
            p.append("같은 논지를 옛 판으로 덮어썼다(StaleWrite가 없다)")
        except db.StaleWrite:
            pass
        if db.thesis_get("t-aaa")["status"] != "held":
            p.append("먼저 쓴 프로세스의 상태가 사라졌다")
        d = th.load()
        next(x for x in d["theses"] if x["id"] == "t-bbb")["name"] = "비2"
        th.save(d)
        if db.thesis_get("t-bbb")["name"] != "비2":
            p.append("theses.load()/save() 짝이 안 쓴다")
    report("(2) 논지 동시 쓰기 — 다른 행은 둘 다 남고 같은 행은 StaleWrite", p)


def t3_thesis_ctx_peaks():
    """build_ctx의 고점 대비 하락률이 저장소 고점(ex_position_peak)을 쓴다."""
    import theses as th
    p = []
    with db_case():
        db.peaks_set("KR", "AAA", 120.0, "2026-10-10T10:00:00+09:00")
        snap = {"prices": {"AAA": {"price": 110.0}, "BBB": {"price": 50.0}},
                "balance": {"positions": [{"ticker": "AAA", "pnl_pct": 5.0}, {"ticker": "BBB", "pnl_pct": 1.0}]}}
        ctx = th.build_ctx({"ticker": "AAA", "market": "KR"}, snap)
        if abs((ctx.get("from_peak_pct") or 0) - (110 / 120 - 1) * 100) > 1e-9:
            p.append(f"AAA from_peak_pct {ctx.get('from_peak_pct')}")
        ctx = th.build_ctx({"ticker": "BBB", "market": "KR"}, snap)
        if ctx.get("from_peak_pct") != 0.0:
            p.append(f"고점 기록이 없으면 지금 가격이 고점이다: {ctx.get('from_peak_pct')}")
    report("(3) 논지 판정 문맥 — 고점은 저장소에서", p)


# ─────────────────────────────── 시나리오 ───────────────────────────────

def _sc(sid, **kw):
    r = {"id": sid, "opened_by": "kr:261016", "market": "kr", "opened": "261016", "local_id": "S1", "status": "open",
         "realized": None, "acted": None, "axis": "", "sources": [f"signal_261016_kr.json#{sid}"], "judgments": [],
         "condition": f"{sid} 조건", "actions": [{"market": "kr", "ticker": "", "what": "대응", "done": False}]}
    r.update(kw)
    return r


def t4_scenario_interleaved():
    """두 프로세스가 각자 통째로 읽고(load) 서로 다른 시나리오를 판정해 저장하면 둘 다 남는다 — 옛 JSON에서는 뒤 저장이 앞을 지웠다."""
    import scenarios as sc
    p = []
    with db_case():
        seed(scenarios=[_sc("SC-1"), _sc("SC-2")])
        A, B = db.DocStore(db.SPEC["scenario"]), db.DocStore(db.SPEC["scenario"])
        da, dbb = A.load(), B.load()
        next(r for r in da if r["id"] == "SC-1")["judgments"].append({"when": "2026-10-17 10:00", "note": "A 판정"})
        next(r for r in dbb if r["id"] == "SC-2")["judgments"].append({"when": "2026-10-17 10:00", "note": "B 판정"})
        A.save(da)
        try:
            B.save(dbb)
        except db.StaleWrite as e:
            p.append(f"다른 행인데 충돌: {e}")
        n1 = [j["note"] for j in db.scenario_get("SC-1")["judgments"]]
        n2 = [j["note"] for j in db.scenario_get("SC-2")["judgments"]]
        if n1 != ["A 판정"] or n2 != ["B 판정"]:
            p.append(f"유실: SC-1 {n1} · SC-2 {n2}")
        da, dbb = A.load(), B.load()
        next(r for r in da if r["id"] == "SC-1")["judgments"].append({"note": "A2"})
        next(r for r in dbb if r["id"] == "SC-1")["judgments"].append({"note": "B2"})
        A.save(da)
        try:
            B.save(dbb)
            p.append("같은 행을 옛 판으로 덮었다")
        except db.StaleWrite:
            pass
        j = lambda sid, note: _ns(id=sid, realized="n", acted=None, note=note, batch="", carry_forward="",
                                  next_check="", price_cond=None, expire=False)
        _run(sc.cmd_judge, j("SC-1", "명령 A"))
        _run(sc.cmd_judge, j("SC-2", "명령 B"))
        if db.scenario_get("SC-1")["judgments"][-1]["note"] != "명령 A" or db.scenario_get("SC-2")["judgments"][-1]["note"] != "명령 B":
            p.append("cmd_judge 두 건 중 하나가 빠졌다")
    report("(4) 시나리오 번갈아 저장 — 다른 행은 둘 다 남고 같은 행은 StaleWrite", p)


_RACE = """
import contextlib, io, sys, types
sys.path.insert(0, {here!r})
import scenarios as sc
for i in range({n}):
    for sid in {sids!r}:
        a = types.SimpleNamespace(id=sid, realized="n", acted=None, note=f"{tag}-{{i}}", batch="", carry_forward="",
                                  next_check="", price_cond=None, expire=False)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = sc.cmd_judge(a)
        if rc != 0:
            sys.exit(rc)
"""


def t5_scenario_two_processes():
    """프로세스 둘이 **같은 시각에** 판정을 쏟아도(같은 행 포함) 판정 하나도 사라지지 않는다 — 배경 판정 셋이 도는 자리."""
    p = []
    n = 12
    with db_case():
        seed(scenarios=[_sc("SC-1"), _sc("SC-2"), _sc("SC-3")])
        procs = [subprocess.Popen([sys.executable, "-c", _RACE.format(here=str(HERE), n=n, sids=sids, tag=tag)],
                                  cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  env={**os.environ})
                 for tag, sids in (("P", ["SC-1", "SC-2"]), ("Q", ["SC-1", "SC-3"]))]
        outs = [pr.communicate(timeout=120) for pr in procs]
        for pr, (o, e) in zip(procs, outs):
            if pr.returncode != 0:
                p.append(f"프로세스 실패 rc={pr.returncode}: {e[-300:]}")
        notes = {sid: [j["note"] for j in db.scenario_get(sid)["judgments"]] for sid in ("SC-1", "SC-2", "SC-3")}
        want = {"SC-1": 2 * n, "SC-2": n, "SC-3": n}
        got = {k: len(v) for k, v in notes.items()}
        if got != want:
            p.append(f"판정 수 {got} (기대 {want}) — 동시 쓰기가 덮었다")
        if sorted(x for x in notes["SC-1"] if x.startswith("P")) != sorted(f"P-{i}" for i in range(n)):
            p.append("SC-1의 P 판정이 일부 빠졌다")
    report(f"(5) 시나리오 동시 판정 — 프로세스 2개 × {n}회(같은 행 포함) 유실 0", p)


def t6_scenario_commands():
    """promote(재실행 멱등)·respond·act·judge·carry-forward·merge가 행 단위로 돈다."""
    import scenarios as sc
    p = []
    with db_case() as tmp:
        seed(watchlist={"_comment": "", "schema_version": "1.0",
                        "KR": [{"ticker": "005930", "name": "삼성전자", "aliases": ["삼성전자"]}],
                        "US": [{"ticker": "MSFT", "excd": "NASD", "name": "Microsoft", "aliases": []}], "benchmarks": {}},
             scenarios=[_sc("SC-9", next_check="2099-01-01")])
        sig = _write(tmp, "signal_261016_kr.json", {"scenarios": [
            {"id": "S1", "condition": "10/20 CPI 하회", "expected": "금리 하락", "response": "KR 삼성전자 e1 추가; MSFT 유지"},
            {"id": "S2", "condition": "조건 둘", "response": "미국 대응", "next_check": "2026-10-23", "price_cond": "y"}]})
        rc, out = _run(sc.cmd_promote, _ns(signal=str(sig)))
        r1, r2 = db.scenario_get("SC-261016-1"), db.scenario_get("SC-261016-2")
        if rc != 0 or not r1 or not r2:
            p.append(f"promote: {rc} {out[-300:]}")
            report("(6) 시나리오 명령 — promote·respond·act·judge·carry-forward·merge", p)
            return
        if (r2.get("next_check"), r2.get("price_cond"), r1.get("next_check")) != ("2026-10-23", True, None):
            p.append(f"시그널의 next_check·price_cond가 원장에 안 실렸다: {r2.get('next_check')} {r2.get('price_cond')} · {r1.get('next_check')}")
        if {(x["market"], x["ticker"]) for x in r1["actions"]} != {("kr", "005930"), ("us", "MSFT")}:
            p.append(f"대응 추출(유니버스는 저장소에서): {r1['actions']}")
        n0 = len(db.scenarios_all())
        _run(sc.cmd_promote, _ns(signal=str(sig)))
        if len(db.scenarios_all()) != n0 or len(db.scenario_get("SC-261016-1")["sources"]) != 1:
            p.append("promote 재실행이 행이나 출처를 늘렸다")
        rc, _ = _run(sc.cmd_respond, _ns(id="SC-261016-2", market="kr", what="국내 대응", ticker="", note="", replace=False))
        if rc != 0 or not any(x["market"] == "kr" for x in db.scenario_get("SC-261016-2")["actions"]):
            p.append(f"respond: {rc}")
        rc, _ = _run(sc.cmd_respond, _ns(id="SC-261016-2", market="kr", what="또", ticker="", note="", replace=False))
        if rc != 2:
            p.append("같은 시장 대응을 --replace 없이 또 받았다")
        _run(sc.cmd_act, _ns(id="SC-261016-1", n=1, note="집행"))
        if db.scenario_get("SC-261016-1")["actions"][0].get("done") is not True:
            p.append("act가 안 남았다")
        rc, _ = _run(sc.cmd_act, _ns(id="SC-261016-1", n=9, note=""))
        if rc != 2:
            p.append("범위 밖 대응 번호를 받았다")
        _run(sc.cmd_judge, _ns(id="SC-261016-1", realized="y", acted="y", note="실현", batch="", carry_forward="",
                               next_check="", price_cond=None, expire=False))
        if db.scenario_get("SC-261016-1")["status"] != "closed":
            p.append("실현 판정이 행을 닫지 않았다")
        before2 = db.scenario_get("SC-261016-2")
        _run(sc.cmd_carry_forward, "2026-10-17")
        s9 = db.scenario_get("SC-9")
        if not (s9["judgments"] and s9["judgments"][-1].get("auto") is True and s9["realized"] is False):
            p.append(f"carry-forward: {s9['judgments']}")
        if db.scenario_get("SC-261016-2") != before2:
            p.append("carry-forward가 판정 대상(next_check 없음)까지 건드렸다")
        _run(sc.cmd_merge, _ns(src="SC-261016-2", into="SC-9"))
        src, dst = db.scenario_get("SC-261016-2"), db.scenario_get("SC-9")
        if src["status"] != "merged" or src.get("merged_into") != "SC-9" or "조건 둘" not in (dst.get("aliases") or []):
            p.append(f"merge: {src['status']} {dst.get('aliases')}")
        for sid, why in (("SC-261016-2", "병합된 행"), ("SC-404", "없는 id")):
            rc, _ = _run(sc.cmd_judge, _ns(id=sid, realized="n", acted=None, note="", batch="", carry_forward="",
                                          next_check="", price_cond=None, expire=False))
            if rc != 2:
                p.append(f"{why} 판정이 rc {rc}")
        rc, out = _run(sc.cmd_due, _ns(market="kr"))
        if "SC-261016-1" in out:
            p.append("대응을 다 마친 실현 행이 집행 대기에 나왔다")
        # 요인 행(FB-261017-7) — 시그널 axis_id가 원장 axis로 · factor면 price_cond · carry-forward가 안 넘김 · 실현돼도 열림
        sig2 = _write(tmp, "signal_261017_kr.json", {"scenarios": [
            {"id": "S9", "condition": "ai_capex가 꺾이면 — 바스켓 20세션 고점 대비 ≥ 일중폭×3", "axis_id": "ai_memory",
             "factor": "ai_capex", "next_check": "2099-01-01", "responses": {"kr": "요인 판정", "us": "요인 판정"}}]})
        rc, out = _run(sc.cmd_promote, _ns(signal=str(sig2)))
        fr = db.scenario_get("SC-261017-1") or {}
        if (fr.get("axis"), fr.get("factor"), fr.get("price_cond")) != ("ai_memory", "ai_capex", True):
            p.append(f"요인 행 승격(axis_id→axis · factor · price_cond): {fr.get('axis')} {fr.get('factor')} {fr.get('price_cond')} {out[-200:]}")
        if not sc._needs_check(fr, "2026-10-17"):
            p.append("요인 행이 --needs-check 대상이 아니다")
        before_f = db.scenario_get("SC-261017-1")
        _run(sc.cmd_carry_forward, "2026-10-17")
        if db.scenario_get("SC-261017-1") != before_f:
            p.append("carry-forward가 요인 행을 자동 이월했다")
        _run(sc.cmd_judge, _ns(id="SC-261017-1", realized="y", acted="y", note="[요인 판정] ai_capex — 꺾임", batch="",
                               carry_forward="", next_check="", price_cond="y", expire=False))
        fr2 = db.scenario_get("SC-261017-1")
        if fr2["status"] != "open" or fr2["realized"] is not True:
            p.append(f"요인 행이 실현 뒤 닫혔다: {fr2['status']} {fr2['realized']}")
        # 옛 '꺾이면' 행을 persistent_id로 이어받으면서 factor를 실으면 그 행이 요인 행이 된다(가격 조건 · 자동 이월 안 됨)
        sig3 = _write(tmp, "signal_261018_kr.json", {"scenarios": [
            {"id": "S1", "condition": "문구를 다듬은 같은 조건", "persistent_id": "SC-9", "factor": "rates_up"}]})
        _run(sc.cmd_promote, _ns(signal=str(sig3)))
        s9b = db.scenario_get("SC-9")
        if (s9b.get("factor"), s9b.get("price_cond")) != ("rates_up", True) or not sc._needs_check(s9b, "2026-10-18"):
            p.append(f"이어받은 행이 요인 행이 안 됐다: {s9b.get('factor')} {s9b.get('price_cond')}")
    report("(6) 시나리오 명령 — promote(멱등)·respond·act·judge·carry-forward·merge · 요인 행(axis_id·price_cond·안 닫힘)", p)


# ─────────────────────────────── 지도·일정 ───────────────────────────────

def _revs():
    out = {("axis", a["id"]): db.axes.rev(a["id"]) for a in db.axes.all()}
    out.update({("sector", s["code"]): db.sectors.rev(s["code"]) for s in db.sectors.all()})
    return out


def _changed(before, after):
    return sorted(k[1] for k in after if before.get(k) != after.get(k))


def t7_market_map_row_scope():
    """grade·forecast judge·axis-tag·forecast add·add-axis가 **대상 행만** 바꾼다(다른 축·섹터의 판은 그대로)."""
    import market_map as mm
    p = []
    with db_case() as tmp:
        seed(axes=[AX1, AX2, AX9], sectors=[SEC_KR, SEC_US])
        r0 = _revs()
        _run(mm.cmd_grade, _ns(code="0013", grade="A", market="kr", name="", note="주목", research="r.md"))
        r1 = _revs()
        if _changed(r0, r1) != ["0013"] or db.sector_get("0013")["grade"] != "A" or db.sector_get("0013")["research"] != ["r.md"]:
            p.append(f"grade: 바뀐 행 {_changed(r0, r1)}")
        _run(mm.cmd_grade, _ns(code="0029", grade="B", market="kr", name="IT 서비스", note="", research=""))
        if not db.sector_get("0029") or db.sector_get("0029")["market"] != "KR":
            p.append("grade가 새 섹터 자리를 안 열었다")
        r1 = _revs()
        _run(mm.cmd_forecast, _ns(sub="judge", axis="ax1", status="hit", excess=3.5, note="적중"))
        r2 = _revs()
        if _changed(r1, r2) != ["ax1"] or db.axis_get("ax1").get("excess_pct") != 3.5 or not db.axis_get("ax1").get("judged_at"):   # 판정 타입 칸(v9 · U06)
            p.append(f"forecast judge: 바뀐 행 {_changed(r1, r2)}")
        _run(mm.cmd_axis_tag, _ns(axis="ax1", driver="ai_capex", conflicts_with="ax2"))
        r3 = _revs()
        if _changed(r2, r3) != ["ax1", "ax2"]:
            p.append(f"axis-tag: 바뀐 행 {_changed(r2, r3)}")
        if db.axis_get("ax1").get("conflicts_with") != ["ax2"] or db.axis_get("ax2").get("conflicts_with") != ["ax1"]:
            p.append("충돌이 양방향으로 안 남았다")
        if db.axis_get("ax1").get("driver") != "ai_capex" or not db.axis_get("ax1").get("tagged"):
            p.append("driver·tagged가 안 남았다")
        rc, _ = _run(mm.cmd_axis_tag, _ns(axis="ax1", driver="", conflicts_with="ax404"))
        if rc == 0 or _revs() != r3:
            p.append("없는 충돌 축을 받았거나 일부만 썼다")
        item = {"id": "ax3", "name": "원전", "thesis": "원전 수주가 이어진다", "direction": "+", "expected": "+5%p",
                "horizon_weeks": 6, "confidence": 0.5,
                "beneficiaries": [{"ticker": "034020", "name": "두산에너빌리티", "order": 1},
                                  {"ticker": "052690", "name": "한전기술", "order": 2},
                                  {"ticker": "CEG", "name": "Constellation", "order": 2}]}
        rc, out = _run(mm.cmd_forecast, _ns(sub="add", file=str(_write(tmp, "fc.json", item)), replace_beneficiaries=False))
        r4 = _revs()
        if rc != 0 or _changed(r3, r4) != ["ax3"]:
            p.append(f"forecast add(새 축): rc {rc} 바뀐 행 {_changed(r3, r4)} {out[-200:]}")
        db.axis_update("ax3", lambda a: (a["beneficiaries"][1].update(priced=False, priced_basis="20일 -12%p"), a)[1])
        item2 = dict(item, expected="+7%p", beneficiaries=[{"ticker": "052690", "name": "한전기술", "order": 2}])
        _run(mm.cmd_forecast, _ns(sub="add", file=str(_write(tmp, "fc2.json", item2)), replace_beneficiaries=False))
        a3 = db.axis_get("ax3")
        b = {x["ticker"]: x for x in a3["beneficiaries"]}
        if a3["expected"] != "+7%p" or b["052690"].get("priced") is not False or set(b) != {"034020", "052690", "CEG"}:
            p.append(f"forecast add(갱신)이 수혜 표를 병합하지 않았다: {a3['expected']} {b.get('052690')}")
        r5 = _revs()
        _run(mm.add, _write(tmp, "ax.json", {"id": "ax2", "name": "금리2", "thesis": "교체"}), "axis")
        r6 = _revs()
        if _changed(r5, r6) != ["ax2"] or db.axis_get("ax2").get("direction") is not None:
            p.append(f"add-axis(같은 id 통째 교체): 바뀐 행 {_changed(r5, r6)}")
        if [a["id"] for a in db.market_map_load()["axes"]] != ["ax1", "ax2", "ax9", "ax3"]:
            p.append(f"축 순서: {[a['id'] for a in db.market_map_load()['axes']]}")
        rend = _rendered("market_map")
        if rend != db.market_map_load():
            p.append("렌더본 market_map.json이 저장소와 다르다")
        if mm._read(mm.MAP, {"axes": []}) != db.market_map_load():
            p.append("옛 호출 _read(MAP, …) 호환이 저장소 지도를 안 준다")
    report("(7) 지도 — grade·forecast judge·axis-tag·forecast add·add-axis가 대상 행만 바꾼다", p)


def t8_market_map_logs_calendar():
    """섹터 이력(같은 날 재실행 멱등)·관찰 기록·대응 예측 로그·섹터 짝·일정 추가가 저장소로 간다."""
    import market_map as mm
    p = []
    with db_case() as tmp:
        seed(sectors=[SEC_KR, SEC_US])
        rows = lambda c: [{"key": "0013", "name": "전기·전자", "d1": 1.0, "d5": 2.0, "d20": 3.0, "close": c}]
        _run(mm._record_board, rows(100.0), "kr", 0.5, "2026-10-15")
        _run(mm._record_board, rows(110.0), "kr", 0.6, "2026-10-16")
        _run(mm._record_board, rows(111.0), "kr", 0.7, "2026-10-16")          # 같은 날 재실행 — 덮는다
        hist = db.log_rows("sector_history")
        if [(r["date"], r["sectors"][0]["close"]) for r in hist] != [("2026-10-15", 100.0), ("2026-10-16", 111.0)]:
            p.append(f"섹터 이력: {[(r['date'], r['sectors'][0]['close']) for r in hist]}")
        if mm.history_since("0013", "2026-10-15") != " · 관찰일(2026-10-15) 대비 +11.00%":
            p.append(f"history_since: {mm.history_since('0013', '2026-10-15')!r}")
        if db.sector_get("0013").get("last_seen") != "2026-10-16":
            p.append("보드가 섹터 자리(last_seen)를 안 남겼다")
        rc, out = _run(mm.cycle, "kr", "전기·전자 관찰", "0013")
        w = db.log_rows("sector_watch")
        if rc != 0 or len(w) != 1 or w[0]["sectors"] != ["0013"]:
            p.append(f"cycle 기록: {rc} {w} {out[-200:]}")
        rc, _ = _run(mm.cycle, "kr", "", "9999")
        if rc != 2 or len(db.log_rows("sector_watch")) != 1:
            p.append("로스터 밖 섹터 코드를 기록했다")
        mm._record_bridge("us", "kr", [{"src": "XLK", "dst": "0013", "sign": "+"}])
        if [r["prep"] for r in db.log_rows("bridge_log")] != ["kr"]:
            p.append("대응 예측 로그가 안 남았다")
        db.sector_bridge_save({"schema_version": "1.0", "us_sectors": {"XLK": {"name": "기술", "excd": "AMS", "gics": True}},
                               "links": [{"us": "XLK", "kr": "0013", "kr_name": "전기·전자", "sign": "+", "confidence": "high", "note": "n"}],
                               "retired_links": [], "kr_without_us": [], "us_without_kr": []})
        _run(mm.cmd_grade, _ns(code="0013", grade="A", market="kr", name="", note="", research=""))
        pg = mm.paired_grades(["XLK"])
        if [(x["pair"], x["grade"]) for x in pg] != [("0013", "A")]:
            p.append(f"짝 등급: {pg}")
        old_argv = sys.argv
        try:
            sys.argv = ["market_map.py", "bridge-retire", "--us", "XLK", "--kr", "0013", "--why", "3회 역행", "--by", "test"]
            rc, _ = _run(mm.main)
        finally:
            sys.argv = old_argv
        br = db.sector_bridge_doc()
        if rc != 0 or br["links"] or [x.get("why") for x in br["retired_links"]] != ["3회 역행"]:
            p.append(f"bridge-retire: rc {rc} links {br['links']} retired {br['retired_links']}")
        ev = [{"date": "2026-10-20", "event": "미국 CPI", "kind": "지표", "key": "us_cpi_1020"}]
        rc, _ = _run(mm.add, _write(tmp, "ev.json", ev), "event")
        rc2, out2 = _run(mm.add, _write(tmp, "ev.json", ev), "event")
        rc3, _ = _run(mm.add, _write(tmp, "bad.json", [{"date": "2026-10-21", "event": "휴간", "kind": "공지"}]), "event")
        cal = db.calendar_all()
        if rc != 0 or rc2 != 0 or "이미 있음" not in out2 or rc3 != 2 or [e["event"] for e in cal] != ["미국 CPI"]:
            p.append(f"add-event: {rc} {rc2} {rc3} {[e['event'] for e in cal]}")
        if not cal or not cal[0].get("added"):
            p.append("일정에 added가 없다")
    report("(8) 섹터 이력 멱등·관찰·대응 로그·섹터 짝(retire)·일정 추가 — 저장소", p)


# ─────────────────────────────── 배분 ───────────────────────────────

def t9_allocation_record():
    """같은 id를 두 번 기록하면 한 행으로 바뀌고(중복 없음), 그 행이 가장 최근 판단(last)이 된다 — 옛 원장과 같다."""
    import allocation as al
    p = []
    with db_case():
        seed(theses=[TH_B])
        mk = lambda m, tgt: {"market": m, "allocation": {"target_invested_pct": tgt, "regime": "20일 +3%", "based_on": None,
                                                         "reserved": [{"thesis_id": "t-bbb", "market": "KR", "amount": 1000, "why": "w"}]}}
        al.record(mk("kr", 90), "signals/approved_261016_kr.json")
        al.record(mk("us", 80), "signals/approved_261016_us.json")
        al.record(mk("kr", 95), "signals/approved_261016_kr.json")          # 재투자 pass — 같은 run 재기록
        rows = al.rows()
        if [(r["id"], r["target_invested_pct"]) for r in rows] != [("AL-261016-us", 80), ("AL-261016-kr", 95)]:
            p.append(f"행: {[(r['id'], r['target_invested_pct']) for r in rows]}")
        if al.last().get("id") != "AL-261016-kr" or al.last(exclude_id="AL-261016-kr").get("id") != "AL-261016-us":
            p.append("last()가 가장 최근 기록을 안 준다")
        if al.last()["reserved"] != [{"thesis_id": "t-bbb", "market": "KR", "amount": 1000, "why": "w"}]:
            p.append(f"예약 왕복: {al.last()['reserved']}")
        res = al.default_reserved("KR", {"KR": 1_000_000}, today="2026-10-17")
        if [(r["thesis_id"], r["amount"]) for r in res] != [("t-bbb", 20000)]:
            p.append(f"예약 기본값(논지는 저장소에서): {res}")
        db.thesis_mark_fired("t-bbb", "e1", note="체결")
        if al.default_reserved("KR", {"KR": 1_000_000}, today="2026-10-17"):
            p.append("체결된 칸을 예약에 남겼다")
        if "AL-261016-kr" not in al.block():
            p.append("prev 블록이 직전 판단을 안 보인다")
        errs = al.validate({"target_invested_pct": 95, "regime": "20일 +3%", "reserved": [], "based_on": "AL-261016-us",
                            "cash_reason": None}, "AL-261017-kr")
        if not any("based_on" in e for e in errs):
            p.append("직전 판단(AL-261016-kr)이 아닌 based_on을 받았다")
    report("(9) 배분 — 같은 id 재기록은 한 행·가장 최근 · 예약 기본값은 저장소 논지", p)


# ─────────────────────────────── 유니버스·시총 상위·커버리지 ───────────────────────────────

class _NoBroker:
    @staticmethod
    def for_mode(*a, **kw):
        return object()


def t10_universe_apply():
    """편입·제외가 dim_company(in_watchlist)와 렌더본에 반영되고, 대기열은 바뀐 후보만, 이력은 universe_log에 한 행."""
    import universe_apply as ua
    p = []
    saved = (ua.KisClient, ua.check_kr, ua.check_us)
    try:
        with db_case() as tmp:
            kr = [{"ticker": f"K{i}", "name": f"케이{i}", "added": "2026-09-09", "why": "w", "axis": "a", "aliases": []} for i in range(1, 6)]
            kr.append({"ticker": "OLD1", "name": "올드", "added": "2026-09-09", "why": "w", "axis": "a", "aliases": []})
            wl0 = {"_comment": "c", "schema_version": "1.0", "KR": kr,
                   "US": [{"ticker": "MSFT", "excd": "NASD", "name": "Microsoft", "added": "2026-09-09", "why": "w", "axis": "a", "aliases": []}],
                   "benchmarks": {"KR": {"ticker": "069500"}}}
            seed(watchlist=wl0,
                 pending={"KR:K1": {"dates": ["2026-10-01"], "runs": ["r0"], "name": "케이1", "axis": "", "excd": ""},
                          "KR:NEW1": {"dates": ["2026-10-14", "2026-10-15"], "runs": ["r1", "r2"], "name": "뉴원", "axis": "", "excd": ""},
                          "US:WAIT": {"dates": ["2026-10-15"], "runs": ["r2"], "name": "대기", "axis": "", "excd": "NASD"}})
            db.main_names_refresh("KR", [{"ticker": "MAIN1", "name": "메인", "mcap_100m_krw": 1e6}], "2026-10-16T09:00:00+09:00", "test")
            snap = _write(tmp, "snapshot.json", {"balance": {"cash": 10_000_000, "positions": [], "currency": "KRW"}})
            sig = _write(tmp, "signal_261016_kr.json", {
                "run_id": "kr:261016", "generated_at": "2026-10-16T10:00:00+09:00", "snapshot_ref": str(snap),
                "watchlist_candidates": [{"ticker": "NEW1", "market": "KR", "name": "뉴원", "axis": "ax"}],
                "watchlist_removals": [{"ticker": "OLD1", "market": "KR", "reason_code": "premise_falsified", "reason": "전제 반증"}]})
            ua.KisClient = _NoBroker
            ua.check_kr = lambda client, ticker, rules, equity: {"price": 10_000.0, "market": "KOSPI200", "sector": "",
                                                                 "mcap_100m_krw": 100_000.0, "turnover_krw": 1e11, "currency": "KRW"}
            rc, out = _run(ua.run, sig, False)                        # dry-run — 아무것도 안 쓴다
            if rc != 0 or db.watchlist_doc() != wl0:
                p.append(f"dry-run이 유니버스를 바꿨다: {rc}")
            rc, out = _run(ua.run, sig, True)
            wl = db.watchlist_doc()
            tk = [e["ticker"] for e in wl["KR"]]
            if rc != 0 or "NEW1" not in tk or "MAIN1" not in tk or "OLD1" in tk:
                p.append(f"반영: rc {rc} KR {tk} {out[-300:]}")
            if next((e for e in wl["KR"] if e["ticker"] == "MAIN1"), {}).get("source") != "main_names":
                p.append("시총 상위 편입분에 source=main_names가 없다")
            if any("market" in e for m in ("KR", "US") for e in wl[m]):
                p.append("렌더 행에 표 키(market)가 샜다")
            if wl["US"] != wl0["US"] or wl["benchmarks"] != wl0["benchmarks"] or list(wl) != list(wl0):
                p.append("건드리지 않은 칸(US·benchmarks·키 순서)이 바뀌었다")
            pend = db.pending_load()
            if set(pend) != {"US:WAIT"} or pend["US:WAIT"] != {"dates": ["2026-10-15"], "runs": ["r2"], "name": "대기", "axis": "", "excd": "NASD"}:
                p.append(f"대기열: {pend}")
            log = db.log_rows("universe_log")
            if len(log) != 1 or sorted(a["entry"]["ticker"] for a in log[0]["added"]) != ["MAIN1", "NEW1"] or log[0]["removed"][0]["ticker"] != "OLD1":
                p.append(f"universe_log: {log}")
            if _rendered("watchlist") != wl:
                p.append("렌더본 watchlist.json이 저장소와 다르다")
            before = db.watchlist_doc()
            ok, _ = _run(ua._write_watchlist_guarded, {"KR": before["KR"][:1]}, before, 0)
            if ok or db.watchlist_doc() != before:
                p.append("규모가 무너진 유니버스를 썼다(가드)")
    finally:
        ua.KisClient, ua.check_kr, ua.check_us = saved
    report("(10) 유니버스 반영 — dim_company·렌더본·바뀐 대기열만·universe_log 한 행·규모 가드", p)


def t11_main_names_refresh():
    """시총 상위 갱신이 회사 행 순위(main_rank)로 가고, load()가 옛 main_names.json 모양을 돌려준다(감시 목록 렌더는 그대로)."""
    import kis_client
    import main_names as mn
    p = []
    saved = (kis_client.KisClient.__dict__.get("for_mode"), mn._kr_mcap, mn._us_mcap)
    try:
        kis_client.KisClient.for_mode = staticmethod(lambda *a, **kw: object())
        with db_case():
            wl0 = {"_comment": "", "schema_version": "1.0",
                   "KR": [{"ticker": "005930", "name": "삼성전자", "aliases": ["삼성"]}], "US": [], "benchmarks": {}}
            seed(watchlist=wl0)
            kr_v = {tk: 1000.0 - i for i, (tk, _n) in enumerate(mn.POOL_KR)}
            kr_v["000660"] = 0.0                                           # 시총 공백 → errors
            mn._kr_mcap = lambda c, tk: kr_v[tk]
            mn._us_mcap = lambda c, tk, ex: (500.0 if tk == "TSM" else 1.0, "NYSE" if tk == "TSM" else ex)
            rc, _ = _run(mn.refresh, 3)
            d = mn.load()
            if [r["ticker"] for r in d["KR"]] != ["005930", "373220", "207940"] or d["KR"][0].get("mcap_100m_krw") != 1000.0:
                p.append(f"KR 상위: {d['KR']}")
            if d["US"][0] != {"ticker": "TSM", "name": "TSMC", "excd": "NYSE", "mcap_usd": 500.0}:
                p.append(f"US 첫 행: {d['US'][0]}")
            if d.get("top_n") != 3 or not d.get("as_of") or not any("000660" in e for e in d.get("errors") or []):
                p.append(f"문서 머리: {[k for k in d]} {d.get('errors')}")
            if list(d) != ["as_of", "source", "pool_size", "top_n", "KR", "US", "errors"]:
                p.append(f"키 순서: {list(d)}")
            kr_v.update({"005930": 1.0, "000270": 5000.0})
            _run(mn.refresh, 3)
            m = db.main_names("KR")
            if [r["ticker"] for r in m] != ["000270", "373220", "207940"] or [r["main_rank"] for r in m] != [1, 2, 3]:
                p.append(f"재갱신 순위: {[(r['ticker'], r['main_rank']) for r in m]}")
            if db.watchlist_doc() != wl0:
                p.append("시총 상위 갱신이 감시 목록 렌더를 바꿨다")
    finally:
        if saved[0] is not None:
            kis_client.KisClient.for_mode = saved[0]
        mn._kr_mcap, mn._us_mcap = saved[1], saved[2]
    report("(11) 시총 상위 — main_rank 갱신·옛 모양 load·감시 목록 그대로", p)


def t12_universe_review_coverage():
    """커버리지 기록이 이번 시장 종목 행만 바꾸고(다른 시장 이력 그대로) 렌더본이 따라온다."""
    import universe_review as ur
    p = []
    with db_case() as tmp:
        seed(watchlist={"_comment": "", "schema_version": "1.0",
                        "KR": [{"ticker": "000660", "name": "SK하이닉스", "aliases": [], "axis": "메모리"},
                               {"ticker": "005930", "name": "삼성전자", "aliases": ["삼성"], "axis": "메모리"},
                               {"ticker": "316140", "name": "우리금융지주", "aliases": [], "axis": ""},
                               {"ticker": "105560", "name": "KB금융", "aliases": [], "axis": ""}],
                        "US": [], "benchmarks": {}},
             coverage={"KR:000660": {"absent_runs": 2, "last_seen": "2026-10-01", "updated": "2026-10-15"},
                       "US:AAPL": {"absent_runs": 1, "last_seen": "2026-10-14", "updated": "2026-10-15"}})
        md = tmp / "material_261016_kr.md"
        md.write_text("오늘 삼성전자 실적 — 삼성 메모리 가격 상승", encoding="utf-8")
        rc, out = _run(ur.review, "kr", md, "261016")
        cov = db.coverage_load()
        if rc != 0 or list(cov) != ["KR:000660", "US:AAPL", "KR:005930", "KR:316140", "KR:105560"]:
            p.append(f"키: {rc} {list(cov)} {out[-200:]}")
        if "· : " in out or "축 미기재 2종목" not in out or "universe_apply.py" not in out or "사람이 watchlist.json" in out:
            p.append(f"축 빈 종목 묶음·반영 안내(예행 D6): {out[-400:]}")
        cov = {k: v for k, v in cov.items() if k not in ("KR:316140", "KR:105560")}
        if cov.get("KR:000660", {}).get("absent_runs") != 3 or cov.get("KR:000660", {}).get("last_seen") != "2026-10-01":
            p.append(f"미등장 누적: {cov.get('KR:000660')}")
        if cov.get("KR:005930", {}).get("absent_runs") != 0 or not cov.get("KR:005930", {}).get("last_seen"):
            p.append(f"등장 종목: {cov.get('KR:005930')}")
        if cov.get("US:AAPL") != {"absent_runs": 1, "last_seen": "2026-10-14", "updated": "2026-10-15"}:
            p.append("다른 시장 이력이 바뀌었다")
        if {k: v for k, v in (_rendered("universe_coverage") or {}).items() if k not in ("KR:316140", "KR:105560")} != cov:
            p.append("렌더본 universe_coverage.json이 저장소와 다르다(봉인 게이트가 읽는 파일)")
        lc, ok_ = ur.load_coverage()
        if ({k: v for k, v in lc.items() if k not in ("KR:316140", "KR:105560")}, ok_) != (cov, True):
            p.append("load_coverage 모양")
    import market_map as mm
    if mm._word_in("V", "GEV 3Q · VLO") or not mm._word_in("V", "Visa(V) 3Q") or not mm._word_in("005930", "삼성전자(005930)"):
        p.append("gaps 티커 맞춤이 낱말 경계를 안 본다(예행 D3)")
    report("(12) 유니버스 커버리지 — 이번 시장 행만 · 렌더본 동기 · 축 미기재 따로 · gaps 티커 낱말 경계", p)


# ─────────────────────────────── 실데이터 사본 연기 시험 ───────────────────────────────

def t13_smoke_real_ledgers():
    """실제 원장 사본(journal/·config/의 파일)을 임시 저장소에 읽어 들여 읽기 경로가 돌고, 저장소 → 원장 모양이 파일과 같다.
    (전환 뒤 파일이 렌더본만 남았으면 있는 것만 본다 — 사본이 없으면 건수 0 = 0.)"""
    import allocation as al
    import market_map as mm
    import scenarios as sc
    import theses as th
    p = []
    with db_case():
        n = testkit.load_ledgers(HERE / "journal", HERE / "config")
        files = {}
        for name, key in (("theses.json", "theses"), ("scenarios.json", "scenarios"), ("market_map.json", "axes"),
                          ("calendar.json", "events")):
            f = HERE / "journal" / name
            files[name] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
        if files["theses.json"] and db.render("theses") != files["theses.json"]:
            p.append("theses.json ↔ 저장소 왕복이 다르다")
        if files["scenarios.json"] and {"scenarios": db.scenarios_all()} != files["scenarios.json"]:
            p.append("scenarios.json ↔ 저장소 왕복이 다르다")
        if files["market_map.json"] and db.market_map_load() != files["market_map.json"]:
            p.append("market_map.json ↔ 저장소 왕복이 다르다")
        if files["calendar.json"] and db.calendar_doc() != files["calendar.json"]:
            p.append("calendar.json ↔ 저장소 왕복이 다르다")
        if len(al.rows()) != n.get("allocation", 0):
            p.append(f"배분 행 {len(al.rows())} ≠ {n.get('allocation', 0)}")
        for label, fn, args in (("theses list", th.cmd_list, ()), ("market_map show", mm.show, ()),
                                ("market_map chains", mm.chains, ()),
                                ("forecast list", mm.cmd_forecast, (_ns(sub="list", open=True, driver=""),)),
                                ("scenarios list", sc.cmd_list, (_ns(affects=None, open=True, id="", stale=False, days=None,
                                                                     needs_check="", brief=True),)),
                                ("scenarios audit", sc.cmd_audit, (_ns(days=14, affects=None, out=""),)),
                                ("allocation prev", al.block, ())):
            rc, out = _run(fn, *args)
            if isinstance(rc, tuple) or (isinstance(rc, int) and rc not in (0,)):
                p.append(f"{label} rc {rc}: {out[-200:]}")
        if n.get("theses") and not mm.axes_index()[1]:
            p.append("축 색인이 비었다")
    report(f"(13) 실데이터 사본 — 왕복 같음·읽기 경로 동작 (적재 {n})", p)


def t14_factor_state():
    """(14) 요인 상태(FB-261017-7) — market_map.exposure_by_driver(stamp)가 요인마다 보유 바스켓의 20세션 고점 대비 하락 ·
    일중폭 중앙값×3 · 지난 세션 같은 요인 규칙 매도(손절·추적 · 목표 다리 제외)를 재서 mk_factor_state에 쓰고 `## 요인 상태`를 렌더한다 ·
    stamp 없이 부르면 그 절이 없다 · 일봉 없는 바스켓은 unknown."""
    import market_map as mm
    import risk_guard as rg
    from datetime import date, timedelta as _td
    p = []
    saved = (mm.MATERIAL_DIR, rg.DATA_DIR)
    with db_case() as tmp:
        try:
            mm.MATERIAL_DIR = rg.DATA_DIR = tmp
            pos = [{"market": "KR", "ticker": tk, "name": tk, "qty": 10, "avg_price": 100, "price": 100, "eval_amt": ev, "pnl_pct": 0}
                   for tk, ev in (("111111", 300_000), ("222222", 300_000), ("333333", 200_000), ("444444", 200_000), ("555555", 100_000))]
            _write(tmp, "snapshot_261016_kr.json", {"market": "KR", "generated_at": "2026-10-16T09:40:00+09:00",
                                                    "balance": {"currency": "KRW", "cash": 900_000, "positions": pos}, "prices": {}})
            seed(axes=[{"id": "axA", "direction": "+", "status": "open", "driver": "dA",
                        "names": [{"ticker": "111111", "sign": "+"}, {"ticker": "222222", "sign": "+"}]},
                       {"id": "axB", "direction": "+", "status": "open", "driver": "dB",
                        "names": [{"ticker": "333333", "sign": "+"}, {"ticker": "444444", "sign": "+"}]},
                       {"id": "axC", "direction": "+", "status": "open", "driver": "dC", "names": [{"ticker": "555555", "sign": "+"}]}],
                 theses=[{"id": "kr-333333-t", "ticker": "333333", "market": "KR", "status": "closed",
                          "exit_triggers": [{"id": "x1", "check": "price <= 90"}]},
                         {"id": "kr-444444-t", "ticker": "444444", "market": "KR", "status": "held",
                          "exit_triggers": [{"id": "x2", "check": "price >= 130"}]}])
            days, d = [], date(2026, 10, 16)
            while len(days) < 25:
                if d.weekday() < 5:
                    days.append(d)
                d -= _td(days=1)
            days.reverse()
            for tk, path in (("111111", [100 + i for i in range(20)] + [118, 114, 110, 107, 106]),     # 고점 119 → 106(−11%)
                             ("222222", [100 + i for i in range(20)] + [117, 113, 109, 106, 105]),
                             ("333333", [100] * 25), ("444444", [100 + (i % 2) for i in range(25)])):
                db._bars_store(f"KR:{tk}", "test", [{"date": x.isoformat(), "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                                                     "volume": 1000} for x, c in zip(days, path)])
            res = mm.exposure_by_driver("KR", "261016")
            st = {r["driver"]: r for r in db.factor_state("261016_kr")}
            if res.get("fired") != ["dA"] or set(res.get("calm") or []) != {"dB"} or res.get("unknown") != ["dC"]:
                p.append(f"꺾임·유지·unknown: {res.get('fired')} {res.get('calm')} {res.get('unknown')}")
            a = st.get("dA") or {}
            if not (a.get("fired_dd") and a.get("drawdown_pct") is not None and a["drawdown_pct"] <= -10 and abs(a["band3_pct"] - 6.0) < 0.2
                    and a.get("peak_date") == "2026-10-09" and len(a.get("members") or []) == 2):
                p.append(f"dA 상태(고점 대비 ≤ −10% · 폭×3≈6% · 고점일 · 멤버 2): {a}")
            if set(st) != {"dA", "dB", "dC"}:
                p.append(f"mk_factor_state 행: {sorted(st)}")
            md = mm.render_exposure(res)
            if "## 요인 상태" not in md or "| dA | 2·2 |" not in md or "**꺾임**" not in md \
                    or "FACTOR-STATE run=261016_kr fired=dA unknown=dC calm=dB" not in md:
                p.append(f"렌더: {md[-700:]!r}")
            if "## 요인 상태" in mm.render_exposure(mm.exposure_by_driver("KR")):
                p.append("stamp 없이 부르면 요인 상태 절이 없어야 한다")
            # 지난 세션 규칙 매도 — 손절(x1 price <=)은 센다 · 목표(x2 price >=)는 안 센다
            db.run_start("261015", "kr")
            c = db.connect()
            for tk, tid, trg in (("333333", "kr-333333-t", "x1"), ("444444", "kr-444444-t", "x2")):
                cur = c.execute("INSERT INTO ex_order (run_id, ticker, market, side, qty, status, trigger_id, thesis_id, sent_at, trade_key, body)"
                                " VALUES (?,?,?,?,?,?,?,?,'t','k',?)", ("261015_kr", tk, "kr", "SELL", 3, "SENT", trg, tid,
                                                                json.dumps({"source": "discipline"})))
                c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,?,?,?,?,?,'{}')",
                          (cur.lastrowid, "261015_kr", tk, "kr", 3, "FILLED"))
            res2 = mm.exposure_by_driver("KR", "261016", no_db=True)
            b = next((r["state"] for r in res2["rows"] if r["driver"] == "dB"), {})
            if not (b.get("fired_sell") and b.get("sells_tickers") == ["333333"] and "dB" in res2.get("fired", [])):
                p.append(f"규칙 매도 꺾임(손절만 · 목표 제외): {b.get('sells_tickers')} {res2.get('fired')}")
            if (db.factor_state("261016_kr") and {r["driver"]: r["fired"] for r in db.factor_state("261016_kr")}.get("dB")):
                p.append("--no-db가 저장소를 덮었다")
        finally:
            mm.MATERIAL_DIR, rg.DATA_DIR = saved
    report("(14) 요인 상태 — 바스켓 고점 대비·폭×3·규칙 매도(목표 제외) → mk_factor_state · `## 요인 상태` · stamp 없으면 없음 · unknown", p)


def t15_factor_state_empty_basket():
    """(15) 잴 바스켓이 없는 run(보유 0 · 창 안 규칙 매도 0 — 보유가 없는 첫 run) — 요인 상태에 '(바스켓 없음)' 표시 행 하나(fired 아님 ·
    state none)를 써서 '안 쟀다'와 가른다 · risk_guard 요인 판정은 state ok·꺾인 요인 0 · crosscheck 요인 판정 행은 통과(2026-10-19)."""
    import market_map as mm
    import risk_guard as rg
    import crosscheck as cc
    p = []
    saved = (mm.MATERIAL_DIR, rg.DATA_DIR)
    with db_case() as tmp:
        try:
            mm.MATERIAL_DIR = rg.DATA_DIR = tmp
            _write(tmp, "snapshot_261019_kr.json", {"market": "KR", "generated_at": "2026-10-19T09:40:00+09:00",
                                                    "balance": {"currency": "KRW", "cash": 10_000_000, "positions": []}, "prices": {}})
            seed(axes=[{"id": "axA", "direction": "+", "status": "open", "driver": "dA", "names": [{"ticker": "111111", "sign": "+"}]}])
            res = mm.exposure_by_driver("KR", "261019")
            st = db.factor_state("261019_kr")
            if res.get("rows") or [(r["driver"], r["fired"], r["state"]) for r in st] != [("(바스켓 없음)", False, "none")]:
                p.append(f"표시 행: rows={res.get('rows')} state={[(r['driver'], r['fired'], r['state']) for r in st]}")
            fc = rg.factor_context({"run_id": "261019_kr"}, "KR")
            if fc.get("state") != "ok" or fc.get("fired"):
                p.append(f"risk_guard 요인 판정: {fc.get('state')} {fc.get('fired')}")
            summ = rg.factor_summary(fc, {}, [], [])
            if summ.get("short"):
                p.append(f"factor_short가 켜졌다: {summ}")
            rows = cc.check_driver_branch("kr", "261019", "## §7\n## §8\n")
            if len(rows) != 2 or not rows[1][1]:
                p.append(f"crosscheck 요인 판정 행: {rows}")
        finally:
            mm.MATERIAL_DIR, rg.DATA_DIR = saved
    report("(15) 잴 바스켓 없음 — '(바스켓 없음)' 표시 행 · risk_guard 꺾인 요인 0 · factor_short 아님 · crosscheck 통과", p)


def t16_thesis_review_and_set_evidence():
    """(16) 근거가 틀린 논지(2026-10-19) — `theses.py check`가 맨 위에 '★ 재검토 필요'를 띄운다 · `set-evidence`가 없는 사실·틀린 사실을
    거부하고 · 받치는 근거에 틀린 사실을 남기면 거부 · 빼고 더하면 corrections에 남고 재검토 목록에서 빠진다."""
    import theses as th
    p = []
    with db_case() as tmp:
        c = db.connect()
        for fid, st in (("F-900001", "disputed"), ("F-900002", "verified"), ("F-900003", "disputed")):
            c.execute("INSERT INTO ev_fact (id, kind, verified_status, legacy_text) VALUES (?, 'legacy', ?, ?)", (fid, st, f"사실 {fid}"))
        c.execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, note, fact_rev) VALUES ('F-900001', '261019_kr', 'MISMATCH', '조건을 뺀 일반화', 1)")
        seed(theses=[{"id": "kr-086790-t", "market": "KR", "ticker": "086790", "status": "armed", "axis": "x", "thesis": "x",
                      "expires": "2099-12-31", "evidence": [{"fact_id": "F-900001", "role": "support", "claim": "틀린 근거"}]}])
        snap = _write(tmp, "snapshot_261019_kr.json", {"market": "KR", "prices": {}, "balance": {"positions": []}})
        rc, out = _run(th.check, "KR", snap)
        if "★ 재검토 필요 1건" not in out or "kr-086790-t" not in out or "F-900001 MISMATCH 조건을 뺀 일반화" not in out:
            p.append(f"check 머리: {out[:400]!r}")
        for args, why in ((["F-999999"], "없는 사실"), (["F-900003"], "틀린 사실")):
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    th.cmd_set_evidence("kr-086790-t", args, ["F-900001"], "교체")
                p.append(f"{why}로 근거를 세웠다")
            except SystemExit:
                pass
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                th.cmd_set_evidence("kr-086790-t", ["F-900002"], [], "더하기만")
            p.append("받치는 근거에 틀린 사실을 남긴 채 통과했다")
        except SystemExit as e:
            if "아직 틀린 사실" not in str(e):
                p.append(f"남은 틀린 사실 거부 문구: {e}")
        rc, out = _run(th.cmd_set_evidence, "kr-086790-t", ["F-900002"], ["F-900001"], "F-900001 MISMATCH — 공시 원문 F-900002로")
        t = db.thesis_get("kr-086790-t")
        if rc != 0 or [e.get("fact_id") for e in t.get("evidence") or []] != ["F-900002"] \
                or not any("(set-evidence)" in str(x.get("what")) for x in t.get("corrections") or []) or db.theses_under_review():
            p.append(f"set-evidence: rc={rc} evidence={t.get('evidence')} corrections={t.get('corrections')} review={db.theses_under_review()}")
        rc, out = _run(th.check, "KR", snap)
        if "재검토 필요" in out:
            p.append("고친 뒤에도 check가 재검토를 띄운다")
    report("(16) 근거가 틀린 논지 — check 머리 '★ 재검토 필요' · set-evidence(없는·틀린 사실 거부 · 남은 틀린 근거 거부 · corrections · 해소)", p)


# ─────────────────────────────── 시나리오 v6 — 조건 동일성·발생·가지 완결성(2026-10-19 · Codex S04·S05·J02) ───────────────────────────────

def _jargs(sid, realized, **kw):
    d = dict(id=sid, realized=realized, acted=None, note="", batch="", carry_forward="", next_check="", price_cond=None,
             expire=False, observation=None, price_reaction=None)
    d.update(kw)
    return _ns(**d)


def t17_scenario_cond_identity():
    """(17) ← scenario_numeric_normalization_collision(Codex S04) — 부호·소수점을 지우던 정규화가 반대 조건을 한 행으로 합쳤다.
    올바른 동작: -5%와 5%는 두 행 · 5.0%와 50%는 두 행 · 유니코드 −와 -는 같은 조건 · 조건이 같아도 대응이 다르면 새 행 + possible_duplicate_of ·
    같은 파일 안 같은 조건은 병합하지 않는다 · 구조화 행은 같은 사건 + 같은 원자 + 대응 포함일 때만 병합 · 재승격 멱등."""
    import scenarios as sc
    p = []
    if sc.norm_text("−5% 이하") != sc.norm_text("-5% 이하") or sc.norm_text("5% 이하") == sc.norm_text("-5% 이하") \
            or sc.norm_text("5.0%") == sc.norm_text("50%") or sc.norm_text("≥ 5") != sc.norm_text(">=5"):
        p.append("norm_text — 부호·소수점·비교 기호 보존")
    with db_case() as tmp:
        seed(scenarios=[])
        a = _write(tmp, "signal_261016_kr.json", {"scenarios": [
            {"id": "S1", "condition": "영업이익 서프라이즈 -5% 이하", "responses": {"kr": "비중 축소"}},
            {"id": "S2", "condition": "마진 50% 이상", "responses": {"kr": "추가 매수"}},
            {"id": "S3", "condition": "CPI 하회", "responses": {"kr": "비중 확대"}},
            {"id": "S4", "condition": "같은 파일 같은 조건", "responses": {"kr": "대응 A"}},
            {"id": "S5", "condition": "같은 파일 같은 조건", "responses": {"kr": "대응 A"}}]})
        b = _write(tmp, "signal_261017_kr.json", {"scenarios": [
            {"id": "S1", "condition": "영업이익 서프라이즈 5% 이하", "responses": {"kr": "비중 축소"}},
            {"id": "S2", "condition": "영업이익 서프라이즈 −5% 이하", "responses": {"kr": "비중 축소"}},
            {"id": "S3", "condition": "마진 5.0% 이상", "responses": {"kr": "추가 매수"}},
            {"id": "S4", "condition": "CPI 하회", "responses": {"kr": "다른 대응 — 관망"}},
            {"id": "S5", "condition": "CPI 하회", "responses": {"kr": "비중 확대"}}]})
        _run(sc.cmd_promote, _ns(signal=str(a)))
        rc, out = _run(sc.cmd_promote, _ns(signal=str(b)))
        rows = {r["id"]: r for r in db.scenarios_all()}
        by = lambda f, lid: next((r for r in rows.values() if f"{f}#{lid}" in (r.get("sources") or [])), {})
        if rc != 0 or len(rows) != 8:
            p.append(f"행 수 {len(rows)} (기대 8 — A 5 + B 새 행 3) rc={rc} {out[-300:]}")
        if by("signal_261017_kr.json", "S1").get("id") == "SC-261016-1":
            p.append("5% 이하가 -5% 이하 행에 병합됐다(부호 유실)")
        if by("signal_261017_kr.json", "S2").get("id") != "SC-261016-1":
            p.append("유니코드 −5%가 -5% 행에 병합되지 않았다")
        if by("signal_261017_kr.json", "S3").get("id") == "SC-261016-2":
            p.append("5.0%가 50% 행에 병합됐다(소수점 유실)")
        b4 = by("signal_261017_kr.json", "S4")
        if b4.get("id") == "SC-261016-3" or "SC-261016-3" not in (b4.get("possible_duplicate_of") or []):
            p.append(f"조건 같고 대응 다른 행 — 새 행 + possible_duplicate_of 기대: {b4.get('id')} {b4.get('possible_duplicate_of')}")
        if by("signal_261017_kr.json", "S5").get("id") != "SC-261016-3":
            p.append("조건·대응이 같은 행이 병합되지 않았다(㊲ 회귀)")
        a5 = by("signal_261016_kr.json", "S5")
        if a5.get("id") == "SC-261016-4" or "SC-261016-4" not in (a5.get("possible_duplicate_of") or []):
            p.append(f"같은 파일 안 같은 조건이 병합됐거나 표시가 없다: {a5.get('id')} {a5.get('possible_duplicate_of')}")
        if {r.get("cond_status") for r in rows.values()} != {"text_only"}:
            p.append(f"글 행 cond_status: {sorted({str(r.get('cond_status')) for r in rows.values()})}")
        _run(sc.cmd_promote, _ns(signal=str(b)))
        if len(db.scenarios_all()) != 8 or len(db.scenario_get("SC-261016-1")["sources"]) != 2:
            p.append("재승격이 행이나 출처를 늘렸다")
        # 구조화 — 같은 사건(자연 열쇠) · 5와 5.0은 같은 원자 · 대응이 다르면 새 행
        ev = {"id": "E1", "subject": "010120 LS일렉트릭", "kind": "earnings", "scheduled": "2026-10-28", "window_end": "2026-10-29",
              "metrics": [{"name": "op_surprise_pct", "domain": "num", "unit": "%"}]}
        c = _write(tmp, "signal_261018_kr.json", {"events": [ev], "scenarios": [
            {"id": "S1", "event": "E1", "condition": "영업이익 컨센 +5% 이상", "responses": {"kr": "추가 매수"},
             "cond": [{"metric": "op_surprise_pct", "op": ">=", "value": 5}]},
            {"id": "S2", "event": "E1", "condition": "영업이익 컨센 -5% 이하", "responses": {"kr": "축소"},
             "cond": [{"metric": "op_surprise_pct", "op": "<=", "value": -5}]}]})
        dd = _write(tmp, "signal_261019_kr.json", {"events": [{**ev, "id": "EV_X"}], "scenarios": [
            {"id": "S1", "event": "EV_X", "condition": "OP 서프 5%↑(문구 다름)", "responses": {"kr": "추가 매수"},
             "cond": [{"metric": "op_surprise_pct", "op": ">=", "value": "5.0"}]},
            {"id": "S2", "event": "EV_X", "condition": "영업이익 컨센 -5% 이하", "responses": {"kr": "다른 대응"},
             "cond": [{"metric": "op_surprise_pct", "op": "<=", "value": -5}]}]})
        _run(sc.cmd_promote, _ns(signal=str(c)))
        rc, out = _run(sc.cmd_promote, _ns(signal=str(dd)))
        rows = {r["id"]: r for r in db.scenarios_all()}
        c1, c2 = by("signal_261018_kr.json", "S1"), by("signal_261018_kr.json", "S2")
        d1, d2 = by("signal_261019_kr.json", "S1"), by("signal_261019_kr.json", "S2")
        if not c1.get("event_id") or c1.get("cond_status") != "structured" or db.q1("SELECT COUNT(*) AS n FROM jd_event")["n"] != 1:
            p.append(f"구조화 행·사건 하나: {c1.get('event_id')} {c1.get('cond_status')} {out[-200:]}")
        if d1.get("id") != c1.get("id"):
            p.append("같은 사건·같은 원자(5 = 5.0)·같은 대응이 병합되지 않았다(문구만 다름)")
        if d2.get("id") == c2.get("id") or c2.get("id") not in (d2.get("possible_duplicate_of") or []):
            p.append(f"구조화 — 대응이 다르면 새 행 + 중복 표시: {d2.get('id')} {d2.get('possible_duplicate_of')}")
    report("(17) 시나리오 조건 동일성 — -5%/5% · 5.0%/50% 두 행 · −/- 한 행 · 대응 다르면 새 행+중복 표시 · 같은 파일 비병합 · 구조화 병합 · 멱등", p)


def t18_scenario_episodes():
    """(18) ← recurrent_factor_keeps_prior_done(Codex S05) — 같은 조건이 다시 일어나도 지난번 '대응 완료'가 남아 대기에 안 떴다.
    올바른 동작: y → 대기 1(발생 1) → act → 0 → y(같은 발생 지속) → 0 → n(발생 닫힘) → y → 대기 1(발생 2) → act → 0 ·
    '영향 없음' 대응은 대기에 안 나온다 · 완료는 발생 단위 행으로 남고 주문 참조가 실린다 · 관측값·가격 반응은 발생에."""
    import scenarios as sc
    p = []
    with db_case():
        seed(scenarios=[_sc("SC-1", actions=[{"market": "kr", "ticker": "", "what": "추가 매수", "done": False},
                                              {"market": "kr", "ticker": "", "what": "영향 없음 — 보유 유지", "done": False}])])

        def due():
            _, out = _run(sc.cmd_due, _ns(market="kr"))
            return [ln for ln in out.splitlines() if "☐ SC-1" in ln]
        _run(sc.cmd_judge, _jargs("SC-1", "y", note="1차 실현", observation='{"x": 1}', price_reaction="+3%"))
        d1 = due()
        if len(d1) != 1 or "발생 1" not in d1[0] or "영향 없음" in d1[0]:
            p.append(f"첫 실현 대기(대응 1 · 영향 없음 제외): {d1}")
        _run(sc.cmd_act, _ns(id="SC-1", n=1, note="주문", order_ref="trades_261019_kr.json#1"))
        if due() or db.scenario_get("SC-1").get("acted") is not True:
            p.append("act 뒤에도 대기 · 또는 acted가 안 섰다(영향 없음은 완료로 센다)")
        _run(sc.cmd_judge, _jargs("SC-1", "y", note="지속"))
        if due():
            p.append("같은 발생이 이어지는데 대응을 다시 요구했다")
        _run(sc.cmd_judge, _jargs("SC-1", "n", note="해제"))
        if due():
            p.append("해제된 조건이 대기에 남았다")
        _run(sc.cmd_judge, _jargs("SC-1", "y", note="재발"))
        d2 = due()
        if len(d2) != 1 or "발생 2" not in d2[0]:
            p.append(f"재발한 조건이 대기에 안 떴다(지난 발생의 완료가 남음): {d2}")
        _run(sc.cmd_act, _ns(id="SC-1", n=1, note="재발 대응", order_ref=""))
        if due():
            p.append("재발 대응 뒤에도 대기")
        eps = db.q("SELECT id, n, cleared_at, observation, price_reaction FROM jd_scenario_episode WHERE scenario_id='SC-1' ORDER BY n")
        if [e["n"] for e in eps] != [1, 2] or eps[0]["cleared_at"] is None or eps[1]["cleared_at"] is not None:
            p.append(f"발생 행: {[(e['n'], e['cleared_at']) for e in eps]}")
        elif json.loads(eps[0]["observation"] or "null") != {"x": 1} or eps[0]["price_reaction"] != "+3%":
            p.append(f"관측값·가격 반응: {eps[0]['observation']} {eps[0]['price_reaction']}")
        done = [(d["episode_id"], d["order_ref"]) for d in db.q("SELECT episode_id, order_ref FROM jd_scenario_response_done ORDER BY episode_id")]
        if len(eps) == 2 and done != [(eps[0]["id"], "trades_261019_kr.json#1"), (eps[1]["id"], None)]:
            p.append(f"발생 단위 완료 행: {done}")
    report("(18) 시나리오 발생 — y→act→0 · 지속 0 · n→y 재발이면 발생 2 대기 → act → 0 · 영향 없음 비대기 · 완료는 발생 단위(주문 참조)", p)


_LS_EVENT = {"id": "E1", "subject": "010120 LS일렉트릭", "kind": "earnings", "scheduled": "2026-10-28", "window_end": "2026-10-29",
             "metrics": [{"name": "op_surprise_pct", "domain": "num", "unit": "%"},
                         {"name": "dc", "domain": "enum", "values": ["up", "flat", "down"]}]}
_LS_BASE = [
    {"id": "S9", "event": "E1", "condition": "영업이익 컨센 +5% 이상이고 데이터센터 매출 성장", "responses": {"kr": "e1 추가 매수"},
     "cond": [{"metric": "op_surprise_pct", "op": ">=", "value": 5}, {"metric": "dc", "op": "is", "value": "up"}]},
    {"id": "S10", "event": "E1", "condition": "영업이익 컨센 -5% 이하", "responses": {"kr": "보유분 절반 축소"},
     "cond": [{"metric": "op_surprise_pct", "op": "<=", "value": -5}]},
    {"id": "S18", "event": "E1", "condition": "영업이익 ±5% 안이고 데이터센터 정체", "responses": {"kr": "유지"},
     "cond": [{"metric": "op_surprise_pct", "op": ">", "value": -5}, {"metric": "op_surprise_pct", "op": "<", "value": 5},
              {"metric": "dc", "op": "is", "value": "flat"}]}]
_LS_FILL = [
    {"id": "S19", "event": "E1", "condition": "10-28 실적 발표 지연", "responses": {"kr": "10-29 재판정"}, "next_check": "2026-10-29",
     "cond": [{"op": "delayed"}]},
    {"id": "S20", "event": "E1", "condition": "그 밖(+5% 이상인데 데이터센터 변화 없음 · ±5% 안인데 데이터센터 성장 등)",
     "responses": {"kr": "유지 · 10-28 추가 확인"}, "cond": [{"op": "else"}]}]


def t19_scenario_branch_coverage():
    """(19) ← scenario_branch_examples(Codex J02) — LS일렉트릭 S9/S10/S18은 '+5% 이상인데 데이터센터 정체' 같은 조합과 발표 지연을 덮지 못했다.
    올바른 동작: check가 종료 3과 미커버 칸·'지연 가지 없음'을 낸다 → delayed·else 행을 더하면 종료 0 · 대응이 다른 겹침은 종료 3 ·
    관측값이 해당하는 가지(match_cell) · judge --observation이 다른 가지면 경고 · 원장 사건으로도 같은 판정(check --open)."""
    import scenarios as sc
    p = []
    with db_case() as tmp:
        seed(scenarios=[])
        s1 = _write(tmp, "signal_261019_kr.json", {"market": "kr", "events": [_LS_EVENT], "scenarios": _LS_BASE + [
            {"id": "S30", "condition": "환율 1,450원 이상", "responses": {"kr": "수출주 비중 유지"}}]})
        rc, out = _run(sc.cmd_check, _ns(signal=str(s1), event="", open=False, out=""))
        need = ("지연 가지 없음", "미커버: op_surprise_pct > 5 ∧ dc=flat", "미커버: -5 < op_surprise_pct < 5 ∧ dc=up",
                "미커버: 발표 지연", "구조화 없는 숫자 조건 1 (S30)")
        if rc != 3 or any(x not in out for x in need):
            p.append(f"S9/S10/S18만 — 종료 {rc} · 빠진 줄 {[x for x in need if x not in out]} · {out[-400:]}")
        s2 = _write(tmp, "signal_261020_kr.json", {"market": "kr", "events": [_LS_EVENT], "scenarios": _LS_BASE + _LS_FILL})
        rc, out = _run(sc.cmd_check, _ns(signal=str(s2), event="", open=False, out=""))
        if rc != 0 or "미커버 0" not in out or "지연 가지 있음" not in out:
            p.append(f"S19 delayed · S20 else를 더해도 종료 {rc}: {out[-300:]}")
        s3 = _write(tmp, "signal_261021_kr.json", {"market": "kr", "events": [_LS_EVENT], "scenarios": _LS_BASE + _LS_FILL + [
            {"id": "S21", "event": "E1", "condition": "영업이익 컨센 +5% 이상", "responses": {"kr": "전량 매도"},
             "cond": [{"metric": "op_surprise_pct", "op": ">=", "value": 5}]}]})
        rc, out = _run(sc.cmd_check, _ns(signal=str(s3), event="", open=False, out=""))
        if rc != 3 or "겹침(대응 다름): op_surprise_pct = 5 ∧ dc=up — S9, S21" not in out:
            p.append(f"대응이 다른 겹침: 종료 {rc} · {out[-300:]}")
        br = []
        for r in _LS_BASE + _LS_FILL:
            atoms, st, _ = sc.parse_cond(r, _LS_EVENT["metrics"])
            br.append({"id": r["id"], "atoms": atoms, "status": st, "resp": sc._resp_set_signal(r, "kr")})
        got = [sc.match_cell(_LS_EVENT, br, o) for o in ({"op_surprise_pct": 7, "dc": "up"}, {"op_surprise_pct": 7, "dc": "flat"},
                                                         {"op_surprise_pct": -8})]
        if got != [["S9"], ["S20"], ["S10"]]:
            p.append(f"match_cell: {got}")
        # 원장 — 승격하면 사건과 가지가 원장에 실리고 check --open이 같은 판정
        _run(sc.cmd_promote, _ns(signal=str(s2)))
        rc, out = _run(sc.cmd_check, _ns(signal="", event="", open=True, out=""))
        if rc != 0 or "[가지] EV-" not in out or "미커버 0" not in out:
            p.append(f"원장 check --open: 종료 {rc} · {out[-300:]}")
        led = {r.get("local_id"): r for r in db.scenarios_all()}
        st = {k: led[k].get("cond_status") for k in ("S9", "S19", "S20") if k in led}
        if st != {"S9": "structured", "S19": "delayed", "S20": "else"}:
            p.append(f"원장 cond_status: {st}")
        if "S10" in led and "S9" in led:
            rc, out = _run(sc.cmd_judge, _jargs(led["S10"]["id"], "y", observation='{"op_surprise_pct": 7, "dc": "up"}'))
            if "관측값은" not in out or led["S9"]["id"] not in out:
                p.append(f"judge --observation이 다른 가지를 경고하지 않았다: {out[-300:]}")
    report("(19) 가지 완결성 — LS S9/S10/S18 종료 3(미커버·지연 없음) → S19 delayed·S20 else 종료 0 · 겹침 3 · match_cell · 원장 check · 관측 경고", p)


def t20_note_lint_scenario_cond():
    """(20) 노트 검사(signal) — limits.scenario_cond_required 날짜부터 ① 숫자 조건인데 cond 없음 ② 사건 미커버·겹침이 위반이다.
    그 날짜 전에는 검사하지 않는다(형식 첫날 run을 막지 않는다 · note_sources_required와 같은 날짜 규칙)."""
    import note_lint as nl
    p = []
    sig = {"market": "kr", "events": [_LS_EVENT],
           "scenarios": _LS_BASE + [{"id": "S30", "condition": "환율 1,450원 이상", "responses": {"kr": "유지"}}]}
    errs, _ = nl.lint(sig, "", False, cond_required=True)
    g = [e for e in errs if e.startswith("[가지]")]
    if not any("S30" in e and "cond[]" in e for e in g) or not any("사건 E1" in e and "미커버" in e for e in g):
        p.append(f"적용일 뒤 위반 두 가지: {g}")
    errs, _ = nl.lint(sig, "", False, cond_required=False)
    if any(e.startswith("[가지]") for e in errs):
        p.append("적용일 전인데 가지 위반을 냈다")
    errs, _ = nl.lint({**sig, "scenarios": _LS_BASE + _LS_FILL}, "", False, cond_required=True)
    if any(e.startswith("[가지]") for e in errs):
        p.append(f"완결한 가지에 위반: {[e for e in errs if e.startswith('[가지]')]}")
    if nl.sources_required("2026-10-21", today="2026-10-20") or not nl.sources_required("2026-10-21", today="2026-10-21"):
        p.append("적용일 날짜 규칙")
    report("(20) 노트 검사 가지 조건 — 적용일부터 숫자 조건 cond 없음·사건 미커버가 위반 · 그 전엔 검사 안 함 · 완결한 가지는 통과", p)


def _bars(n, start, step, last_ymd):
    """최신순 일봉 n개 — 맨 앞(최신) 날짜가 last_ymd, 종가는 오래된 쪽 start에서 step씩."""
    from datetime import datetime as _dt, timedelta as _td
    d0 = _dt.strptime(last_ymd, "%Y%m%d")
    return [{"date": (d0 - _td(days=i)).strftime("%Y%m%d"), "close": start + step * (n - 1 - i)} for i in range(n)]


def t21_measure_state():
    """(21) ← priced_failure_retains_unpriced_state(Codex U05) — 측정 실패가 근거 칸만 바꾸고 priced=False·측정일을 남겨 '미반영'으로 계속 셌다.
    올바른 동작: 축 전체를 못 잰 시도는 저장하지 않고 수혜 전부 unavailable(마지막 성공 그대로 · 선두 미정 사유) → 지도 단계 미반영 0 · 측정 상태
    줄 '실패 2' · 다시 잘 재면 valid → 미반영 1 · 일봉 기준일이 지난 세션이면 stale(보이되 안 셈) · 경계와 미측정을 가른다."""
    import kis_client
    import market_map as mm
    import stage
    p = []
    with db_case() as tmp:
        last = kis_client.last_session_date("KR").strftime("%Y%m%d")
        seed(watchlist={"_comment": "", "schema_version": "1.0", "KR": [], "US": [],
                        "benchmarks": {"KR": {"ticker": "069500"}, "US": {"ticker": "SPY", "excd": "AMS"}}},
             axes=[{"id": "ax", "name": "A", "direction": "+", "horizon_weeks": 8, "confidence": 0.6, "status": "open", "opened": "2026-10-01",
                    "beneficiaries": [{"ticker": "000001", "order": 1, "market": "KR", "priced": False, "priced_at": "2026-10-01",
                                       "priced_basis": "20일 +0.0% · 벤치 대비 +0.0%p · 선두 대비 -30.0%p", "measure_status": "valid",
                                       "measured_at": "2026-10-01", "priced_bars_asof": "2026-10-01"},
                                      {"ticker": "000002", "order": 2, "market": "KR"}]},
                   {"id": "closed", "name": "C", "direction": "+", "horizon_weeks": 8, "status": "miss", "opened": "2026-09-01",
                    "beneficiaries": [{"ticker": "000009", "order": 1, "market": "KR", "priced": False, "priced_at": "2026-10-01"}]}])
        bars = {"000001": [], "000002": _bars(21, 100, 1.5, last), "069500": _bars(21, 100, 0, last)}
        saved = (db.domestic_daily, kis_client.KisClient.for_mode)
        db.domestic_daily = lambda c, tk, *a, **k: list(bars.get(tk, []))
        kis_client.KisClient.for_mode = classmethod(lambda cls, **k: object())
        saved_an, saved_cap = stage.ANALYSIS, stage.capture_ok
        stage.ANALYSIS = tmp / "analysis"
        stage.ANALYSIS.mkdir()
        stage.capture_ok = lambda st, mk: None
        try:
            r = mm.priced_measure("ax")
            b1, b2 = (db.axis_get("ax")["beneficiaries"][i] for i in (0, 1))
            if r["complete"] or (b1.get("priced"), b1.get("priced_at"), b1.get("measure_status"), b2.get("measure_status")) != \
                    (False, "2026-10-01", "unavailable", "unavailable") or "000001(일봉 0행뿐)" not in str(b1.get("measure_error")) \
                    or "선두 미정" not in str(b1.get("measure_error")) or b2.get("priced_at"):
                p.append(f"부분 실패: {r.get('complete')} · {b1} · {b2}")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                stage.cmd_map(types.SimpleNamespace(market="kr", stamp="261019", refresh=False))
            md = (stage.ANALYSIS / "전망_261019_kr.md").read_text(encoding="utf-8")
            if "미반영 수혜 **0건**" not in md or "측정 상태: 유효 0 · 오래됨 0 · 실패 2 · 미측정 0" not in md or "실패 " not in md:
                p.append(f"실패 뒤 지도: {[l for l in md.splitlines() if '미반영' in l or '측정 상태' in l][:3]}")
            bars["000001"] = _bars(21, 100, 0, last)
            with contextlib.redirect_stdout(io.StringIO()):
                stage.cmd_map(types.SimpleNamespace(market="kr", stamp="261019", refresh=False))
            md = (stage.ANALYSIS / "전망_261019_kr.md").read_text(encoding="utf-8")
            b1, b2 = (db.axis_get("ax")["beneficiaries"][i] for i in (0, 1))
            if "미반영 수혜 **1건**" not in md or (mm.measure_state(b1), b1.get("priced"), b2.get("priced")) != ("valid", False, True):
                p.append(f"복구 뒤: {mm.measure_state(b1)} {b1.get('priced')} {b2.get('priced')} · "
                         f"{[l for l in md.splitlines() if '미반영' in l][:1]}")
            if mm.measure_state({**b1, "priced_bars_asof": "2026-01-02"}) != "stale":
                p.append("지난 세션 일봉이 stale이 아니다")
            if (mm.priced_label({"priced": None, "priced_at": "2026-10-01"}), mm.priced_label({"priced": None})) != ("경계", "미측정"):
                p.append("경계·미측정 구분")
        finally:
            db.domestic_daily, kis_client.KisClient.for_mode = saved
            stage.ANALYSIS, stage.capture_ok = saved_an, saved_cap
    report("(21) 후보 측정 상태 — 부분 실패는 저장 안 함(unavailable·마지막 성공 유지) · 미반영 0·실패 2 → 복구 valid·미반영 1 · stale · 경계/미측정", p)


def t22_forecast_cohorts():
    """(22) ← weekly_omits_newly_judged_old_forecast(Codex U06) — '최근 n일에 세운 것'만 세어 8주 전 전망이 어제 판정돼도 주간 채점에 안 잡혔다.
    올바른 동작: [전망] 줄의 판정·적중은 이 기간에 판정된 것 · [전망 기한] 도래·판정·미판정·기각·미성숙 · forecasts_due는 미판정만."""
    import market_map as mm
    import loss_review as lr
    from datetime import date as _date, timedelta as _td
    p = []
    with db_case():
        end = mm._run_day()
        iso = lambda d: (end - _td(days=d)).isoformat()
        seed(axes=[{"id": "A", "name": "A", "direction": "+", "horizon_weeks": 8, "status": "hit", "opened": iso(56), "judged_at": iso(1),
                    "excess_pct": 10.0, "beneficiaries": [{"ticker": "AAA", "order": 2, "market": "US"}]},
                   {"id": "B", "name": "B", "direction": "+", "horizon_weeks": 8, "status": "open", "opened": iso(60),
                    "beneficiaries": [{"ticker": "BBB", "order": 1, "market": "US"}]},
                   {"id": "C", "name": "C", "direction": "+", "horizon_weeks": 8, "status": "open", "opened": iso(2),
                    "beneficiaries": [{"ticker": "CCC", "order": 1, "market": "US"}]}])
        m = db.market_map_load()
        _, out = _run(mm.forecast_score, m, 7)
        first = next((l for l in out.splitlines() if l.startswith("[전망] ")), "")
        if not re.match(r"^\[전망\] 세움 1 · 선행 \d · 판정 1 · 적중 1( · 선행 초과 \+10\.0%p\(n=1\))?$", first):
            p.append(f"[전망] 줄: {first}")
        if "[전망 기한] 도래 2 · 판정 1 · 미판정 1 · 기각 0 · 미성숙 1" not in out:
            p.append(f"[전망 기한] 줄: {[l for l in out.splitlines() if l.startswith('[전망 기한]')]}")
        due = [r["axis_id"] for r in lr.forecasts_due(end.strftime("%y%m%d"))]
        if due != ["B"]:
            p.append(f"forecasts_due(미판정만): {due}")
    report("(22) 전망 코호트 — 이 기간 판정 1·적중 1 · 도래 2(판정 1·미판정 1)·미성숙 1 · forecasts_due는 미판정만", p)


def t23_thesis_structure_lint():
    """(23) ← research_quality(Codex G02) — 사실이 맞아도 결론이 맞는 것은 아니다. 적용일부터 매수마다 thesis_structure: 틀 문장·짧은 글 거부 ·
    메커니즘·반영/새로움은 출처 토큰 · 가장 강한 반대는 role: against 사실 id 인용 · 무효 관측은 관측 가능한 값 · 결정 기한은 세션 뒤 시계 안 ·
    적용일 전에는 검사하지 않는다."""
    import note_lint as nl
    p = []
    good = {"run_id": "261019_kr", "proposals": [{
        "ticker": "086790", "action": "BUY", "horizon_days": 35,
        "facts": [{"id": "F-000101", "role": "support"}, {"id": "F-000202", "role": "against"}],
        "thesis_structure": {"mechanism": "3Q 순이자마진 개선이 자사주 소각 확대로 이어진다 [S31] F-000101",
                             "priced_in_vs_new": "주가는 9월 고점 대비 -9% — 소각 확대 공시는 아직 가격에 없다 [S33]",
                             "strongest_counter": "대손비용이 1조 위로 오르면 소각 여력이 준다 — F-000202",
                             "kill_observation": "10-23 3Q 발표에서 대손비용 1조 원 초과",
                             "decision_deadline": "2026-11-10"}}]}
    errs, _ = nl.lint(good, "", False, cond_required=True)
    if [e for e in errs if e.startswith("[논지 구조]")]:
        p.append(f"근거 있는 구조에 위반: {[e for e in errs if e.startswith('[논지 구조]')]}")
    bad = json.loads(json.dumps(good))
    ts = bad["proposals"][0]["thesis_structure"]
    ts.update({"mechanism": "<메커니즘>", "priced_in_vs_new": "아직 반영되지 않았다고 본다 그렇다 확실히", "strongest_counter": "금리 하락 위험이 있다 정도로 본다 일단",
               "kill_observation": "분위기가 나빠지면 판단을 바꾼다 정도로 본다", "decision_deadline": "2027-03-01"})
    errs, _ = nl.lint(bad, "", False, cond_required=True)
    g = " | ".join(e for e in errs if e.startswith("[논지 구조]"))
    for need in ("mechanism가 비었거나 틀 문장", "priced_in_vs_new에 출처 토큰", "반대 근거 사실(F-000202)을 인용하지 않는다",
                 "kill_observation이 관측 가능한 값", "decision_deadline 2027-03-01"):
        if need not in g:
            p.append(f"위반 '{need}'가 없다: {g[:300]}")
    nofacts = json.loads(json.dumps(good))
    nofacts["proposals"][0]["facts"] = [{"id": "F-000101", "role": "support"}]
    if not any("role: against" in e for e in nl.lint(nofacts, "", False, cond_required=True)[0]):
        p.append("반대 근거 사실이 없는데 통과")
    if [e for e in nl.lint(bad, "", False, cond_required=False)[0] if e.startswith("[논지 구조]")]:
        p.append("적용일 전인데 논지 구조 위반을 냈다")
    report("(23) 논지 구조 검사 — 근거 있는 구조 통과 · 틀 문장·출처 없음·반대 미인용·관측 불가·기한 밖 위반 · 반대 사실 필수 · 적용일 전 검사 안 함", p)


for fn in (t1_thesis_roundtrip, t2_thesis_concurrent_save, t3_thesis_ctx_peaks, t4_scenario_interleaved,
           t5_scenario_two_processes, t6_scenario_commands, t7_market_map_row_scope, t8_market_map_logs_calendar,
           t9_allocation_record, t10_universe_apply, t11_main_names_refresh, t12_universe_review_coverage,
           t13_smoke_real_ledgers, t14_factor_state, t15_factor_state_empty_basket,
           t16_thesis_review_and_set_evidence, t17_scenario_cond_identity, t18_scenario_episodes,
           t19_scenario_branch_coverage, t20_note_lint_scenario_cond, t21_measure_state, t22_forecast_cohorts,
           t23_thesis_structure_lint):
    _safe(fn)

_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_judgment.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
