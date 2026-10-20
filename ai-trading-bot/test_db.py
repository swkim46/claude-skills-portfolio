#!/usr/bin/env python3
"""
저장소(db.py · db_schema.sql) 시험 — 빈 임시 저장소에서 원장 모양 왕복 · 판 충돌 · 다리 계산 칸 · 테스트 문지기 · 추가만 표 ·
사실 트리거 · 내보내기 · 일봉 캐시 · 고점·일정·감시 목록 · 집행 기록 이전 · run 시각·잔고·점검·감사 복사 · 백업·복원·검사를 끝까지 돈다.

왜(2026-10-17): 사실·데이터의 원본이 `journal/trading.db` 한 파일로 옮겨 간다. 원장을 통째로 읽고 쓰던 스크립트들이 `db.py`를 거쳐
행 단위로 쓰게 되므로, 왕복이 조금이라도 어긋나면(1 ↔ 1.0 ↔ True · null ↔ 키 없음 · 키 순서) 내보내기가 옛 파일과 달라지고,
판 충돌·추가만 보호·재검토 표시가 틀리면 주문·체결 기록이 조용히 덮이거나 틀린 근거로 주문이 나간다.

규칙: 케이스마다 새 저장소(스크래치 `testdb_*` 폴더의 AITB_DB)를 열고 `db.reset()`부터 부른다. 실제 `journal/trading.db`는 열지 않고
실제 `journal/`·`config/`에는 쓰지 않는다 — 내보내기는 AITB_DB 옆 `exports/`(db.export_path)로, 백업은 스크래치 폴더로 돌린다.
브로커·네트워크는 부르지 않는다(가짜 클라이언트가 호출 수를 센다).
`@held` 표시 케이스는 db.py 결함을 드러내는 시험이다 — 고쳐지기 전까지 FAIL이 정상이고, 요약 줄에 held=로 따로 센다(지금 0건).

    python3 test_db.py          # run_tests.py가 부른다(TESTS 목록에 올려야 돈다)
    AITB_TEST_ROOT=<폴더>        # 스크래치 뿌리 바꾸기 · AITB_TEST_KEEP=1 이면 케이스 폴더를 지우지 않는다
"""
import atexit
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).absolute().parent
sys.path.insert(0, str(HERE))

# 스크래치 뿌리 — 케이스마다 그 아래 testdb_<이름>_<pid>_<번호>/trading.db를 새로 연다.
SCRATCH = Path(os.environ.get("AITB_TEST_ROOT") or
               "/tmp/aitb_tests")
REAL_DB = HERE / "journal" / "trading.db"
# import 전에 임시 경로를 걸어 둔다 — 어느 케이스가 저장소를 먼저 열어도 실제 저장소가 아니다.
_BOOT = SCRATCH / f"testdb_boot_{os.getpid()}"
os.environ["AITB_DB"] = str(_BOOT / "trading.db")
os.environ.pop("AITB_EXPORT_REAL", None)

import db  # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []          # (상태, 이름, 문제, 보류?)
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for r in results if r[0] == FAIL)
    print(f"\n★★ 테스트가 중간에 멈췄다 — 마지막으로 시작한 케이스: {_STATE['current']} · 그때까지 {n}건(실패 {nf})",
          file=sys.stderr)
    print(f"TEST-HALTED file=test_db.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)


def held(fn):
    """보류: db 수정 대기 — db.py 결함을 드러내는 케이스(수정 요청 문서에 실패 사례·제안을 적었다)."""
    fn.held = True
    return fn


def _safe(fn):
    _STATE["current"] = fn.__name__
    try:
        fn()
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        h = getattr(fn, "held", False)
        results.append((FAIL, f"{fn.__name__} — 중단", f"{type(e).__name__}: {e}", h))
        print(f"[{FAIL}] {'(보류) ' if h else ''}{fn.__name__} — 중단({type(e).__name__}: {str(e)[:200]})")
    finally:
        db.reset()                           # 연결이 다음 케이스로 새지 않게


def report(label, problems, is_held=False):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems), is_held))
    tag = "(보류: db 수정 대기) " if is_held else ""
    print(f"[{status}] {tag}{label}" + (f" — {'; '.join(problems)}" if problems else ""))


_SEQ = [0]


@contextlib.contextmanager
def fresh(tag):
    """새 빈 저장소 — AITB_DB를 스크래치 testdb_<tag>_…/trading.db로 바꾸고 db.reset()부터 부른다."""
    _SEQ[0] += 1
    d = SCRATCH / f"testdb_{tag}_{os.getpid()}_{_SEQ[0]}"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    os.environ["AITB_DB"] = str(d / "trading.db")
    os.environ.pop("AITB_EXPORT_REAL", None)
    db.reset()
    path = db.db_path().resolve()
    if path == REAL_DB.resolve() or SCRATCH.resolve() not in path.parents:
        raise RuntimeError(f"시험 저장소가 스크래치 밖이다: {path}")
    try:
        yield d
    finally:
        db.reset()
        if os.environ.get("AITB_TEST_KEEP") != "1":
            shutil.rmtree(d, ignore_errors=True)


def _same(got, want, path="$", out=None):
    """값·타입·키 순서까지 같은가(1 ≠ 1.0 ≠ True · None ≠ 키 없음). 다른 자리를 문장 목록으로 돌려준다."""
    out = [] if out is None else out
    if type(got) is not type(want):
        out.append(f"{path}: 타입 {type(got).__name__} ≠ {type(want).__name__} ({got!r} vs {want!r})")
        return out
    if isinstance(want, dict):
        if list(got.keys()) != list(want.keys()):
            out.append(f"{path}: 키 순서 {list(got.keys())} ≠ {list(want.keys())}")
        for k in want:
            if k in got:
                _same(got[k], want[k], f"{path}.{k}", out)
    elif isinstance(want, list):
        if len(got) != len(want):
            out.append(f"{path}: 길이 {len(got)} ≠ {len(want)}")
        for i, (x, y) in enumerate(zip(got, want)):
            _same(x, y, f"{path}[{i}]", out)
    elif got != want:
        out.append(f"{path}: {got!r} ≠ {want!r}")
    return out


def _n(table, where="", args=()):
    return db.q1(f"SELECT COUNT(*) AS n FROM {table} {('WHERE ' + where) if where else ''}", args)["n"]


def _fact(i, **kw):
    """옛 sources.json 한 행 모양."""
    d = {"fact": f"사실 문장 {i}", "as_of": "2026-10-16", "retrieved": "2026-10-16", "source": "DART",
         "url": f"https://example.com/f{i}", "tier": 1, "load_bearing": True, "id": f"SRC-{i:04d}", "runs": ["kr:261016"]}
    d.update(kw)
    return d


def _seed_facts(*nums):
    for i in nums:
        db.facts_store.put(_fact(i))


def _thesis(tid="kr-mem-005930-261016"):
    """논지 한 행 — 컬럼 칸 · extra로 가야 하는 칸 · 자식 목록을 골고루."""
    return {
        "id": tid, "market": "KR", "ticker": "005930", "name": "삼성전자",
        "axis": "메모리 반도체",
        "expires": "2026-12-31",
        "horizon_days": 60,                   # 정수 칸
        "thesis": "HBM 증설이 4분기 메모리 가격을 끌어올린다",
        "evidence": [
            {"claim": "DRAM 계약가 +8%(10월)", "source_url": "https://example.com/dram", "tier": "T1", "verdict": "MATCH",
             "fact_id": "SRC-0001", "role": "support"},
            {"claim": "재고 일수 감소", "tier": 2, "fact_id": None, "meta": {"pages": [3, 4], "ok": True}},   # tier 숫자 → extra
        ],
        "status": "armed",
        "parent": None,                       # 명시적 null — 키 없음과 다르다
        "entry_price": 70000,                 # 정수 그대로(70000.0이 되면 안 된다)
        "expected_upside_pct": 12.0,          # 실수 그대로(12가 되면 안 된다)
        "priced_at_entry": True,              # 불리언
        "beneficiary_order": 1.0,             # 정수 칸에 실수 → extra
        "entry_triggers": [
            {"id": "e0", "when": "판단", "check": "judge", "size_pct": 2},
            {"id": "e1", "check": "price <= 68000", "size_pct": 1.5, "fired": False, "basis": "20일 저점"},
            {"id": "e2", "check": "price >= 75000", "size_pct": "1%", "stock": None},     # 숫자 칸에 문자열 → extra
        ],
        "exit_triggers": [
            {"id": "x1", "check": "from_peak_pct <= -8", "size_pct": 100.0, "fired": True,
             "fired_at": "2026-10-16T10:00:00+09:00", "fired_note": "고점 대비 -8.2%"},
            {"id": "x2", "check": "days_since_entry >= 20", "reach_prob": 0.35, "reach_n": 40},
            {"id": "x3", "check": "pnl_pct <= -10", "reach_n": 40.0, "fired": 1},          # 정수 칸 실수 · 불리언 칸 정수 → extra
        ],
        "entry_triggers_archived": [],        # 빈 목록
        "invalidation": "DRAM 계약가 2개월 연속 하락",
        "created": "2026-10-16",
        "history": [{"ts": "2026-10-16T09:40:00+09:00", "status": "armed", "note": "신설"}],
        "corrections": [{"ts": "2026-10-16T11:00:00+09:00", "what": "e1 68,500 → 68,000", "why": "지지 구간 아랫선"}],
        "research": ["a.md", "b.md"],         # 문자 칸에 목록 → extra
        "unknown_key": {"nested": [1, 1.0, True, None, "x"]},
    }


def _scenario(sid="SC-KR-261016-1", fact_ids=("SRC-0001",)):
    return {
        "id": sid, "market": "KR", "opened": "261016", "local_id": "S1", "status": "open",
        "realized": False,
        "acted": 0,                           # 불리언 칸에 정수 → extra
        "sources": ["kr:261016", "us:261015"],
        "aliases": [],
        "judgments": [
            {"when": "261016", "realized": None, "acted": False, "note": "아직", "auto": True,
             "fact_ids": list(fact_ids) if isinstance(fact_ids, (list, tuple)) else fact_ids},  # 사실 id 목록(json 칸)
            {"when": "261017", "realized": 1, "note": "절반"},                             # 정수 → extra
        ],
        "condition": "FOMC 25bp 인하", "expected": "성장주 강세", "response": "e1 앞당김", "opened_by": "kr",
        "axis": "ax_rates",
        "actions": [{"market": "KR", "ticker": "005930", "what": "e1 앞당김", "done": False},
                    {"market": "US", "ticker": "QQQ", "what": "관찰", "done": "부분"}],      # 불리언 칸에 문자열 → extra
        "next_check": "2026-10-29",
        "price_cond": True,
    }


def _axis(aid="ax_mem"):
    return {
        "id": aid, "name": "메모리 반도체", "label": "메모리",
        "note_261014": "직전 판단",           # 이름 규칙 칸(note_<stamp>) — 키 순서 사이사이에 끼워 둔다
        "thesis": "HBM 증설", "driver": "ai_capex", "direction": "up",
        "horizon_weeks": 8, "confidence": 0.6,
        "indicators": [{"name": "DRAM 현물가", "source": "DRAMeXchange", "current": "↑", "threshold": "2주 연속 하락",
                        "next": "2026-10-30", "history": [{"d": "1016", "v": 3.1}]}],
        "names": [{"ticker": "005930", "name": "삼성전자", "sign": "+", "in_universe": True, "market": "KR"}],
        "note_261016": "국내 run 판단 — 유지",
        "beneficiaries": [{"ticker": "000660", "name": "SK하이닉스", "order": 1, "why": "HBM 점유", "priced": False,
                           "in_universe": True},
                          {"ticker": "042700", "name": "한미반도체", "order": 2.0, "priced": None}],   # 순서 칸 실수 → extra
        "chain": [{"step": 1, "claim": "증설", "status": "확인"}, {"step": "2a", "claim": "가격"}],     # 문자열 step → extra
        "conflicts_with": ["ax_rates"],
        "note_261015b": "보조 판단",
        "note_261013": None,                  # 이름 규칙 칸인데 null → extra
        "note_261012_fc": "규칙 밖 이름 → extra",
        "created_before_news": False,
        "updated": "2026-10-16",
    }


def _sector(code="SEC-KR-SEMI"):
    return {"code": code, "name": "반도체", "grade": "A", "status": "live", "chains": ["ax_mem"],
            "names": [{"ticker": "005930", "name": "삼성전자", "why": "대표", "in_universe": True}],
            "next_event": "3Q 실적", "resolves_when": None, "carried_runs": 3, "updated": "2026-10-16",
            "market": "KR", "last_seen": "261016", "research": []}


def _allocation(aid="261016_kr_main"):
    return {"id": aid, "stamp": "261016", "market": "KR", "based_on": "approved_261016_kr.json",
            "target_invested_pct": 95, "regime": "risk_on", "cash_reason": None, "gap_pct": 12.5, "deployable": 1250000,
            "reserved": [{"thesis_id": "kr-a", "market": "KR", "amount": 20000, "why": "e1"},
                         {"thesis_id": "kr-b", "amount": "전액"}],                           # 숫자 칸에 문자열 → extra
            "recorded_at": "2026-10-16T10:40:00+09:00", "approved_ref": "approved_261016_kr.json",
            "notes": {"x": [1, 2.0, True]}}


def _watchlist():
    return {"_comment": "감시 목록", "schema_version": "1.0",
            "KR": [{"ticker": "005930", "name": "삼성전자", "added": "2026-09-09", "why": "축 추적", "axis": "메모리 반도체",
                    "aliases": ["삼성전자"]},
                   {"ticker": "000660", "name": "SK하이닉스", "added": "2026-09-09", "aliases": ["하이닉스", "SK하이닉스"],
                    "gates": ["over_1m"], "excd": None}],
            "US": [{"ticker": "AAPL", "excd": "NASD", "name": "Apple", "added": "2026-09-09", "axis": "미국 메가캡 테크",
                    "aliases": ["애플", "Apple"]}],
            "benchmarks": {"KR": "069500", "US": "SPY"}, "_comment_axis": "축", "_comment_dynamic": "동적"}


# ───────────────────────────── ① 원장 모양 왕복 ─────────────────────────────

def t01_roundtrip_thesis():
    p = []
    with fresh("t01"):
        _seed_facts(1, 2)
        t = _thesis()
        t2 = {"id": "kr-hynix-000660-261016", "ticker": "000660"}        # 키가 적은 행 — 없는 키는 없는 채로
        db.thesis_put(copy.deepcopy(t))
        db.thesis_put(copy.deepcopy(t2))
        p += _same(db.thesis_get(t["id"]), t, "get")
        p += _same(db.theses_all(), [t, t2], "all")
        # 왕복만 맞고 전부 extra로 가면 판단 컬럼이 빈다 — 컬럼·extra 분기를 실제로 탔는지 본다
        r = db.q1("SELECT * FROM jd_thesis WHERE id=?", (t["id"],))
        if not (r["entry_price"] == 70000 and type(r["entry_price"]) is int):
            p.append(f"entry_price 컬럼 {r['entry_price']!r}(정수 그대로여야)")
        if not (r["expected_upside_pct"] == 12.0 and type(r["expected_upside_pct"]) is float):
            p.append(f"expected_upside_pct 컬럼 {r['expected_upside_pct']!r}(실수 그대로여야)")
        if r["priced_at_entry"] != 1 or r["horizon_days"] != 60 or r["beneficiary_order"] is not None:
            p.append(f"불리언·정수 컬럼: {r['priced_at_entry']!r} {r['horizon_days']!r} {r['beneficiary_order']!r}")
        ex = json.loads(r["extra"])
        if set(ex) != {"beneficiary_order", "research", "unknown_key"}:
            p.append(f"extra 칸이 다르다: {sorted(ex)}")
        legs = db.q("SELECT src, ord, leg_id, size_pct, extra FROM jd_thesis_leg ORDER BY src, ord")
        if [(x["src"], x["ord"], x["leg_id"]) for x in legs] != [
                ("entry_triggers", 0, "e0"), ("entry_triggers", 1, "e1"), ("entry_triggers", 2, "e2"),
                ("exit_triggers", 0, "x1"), ("exit_triggers", 1, "x2"), ("exit_triggers", 2, "x3")]:
            p.append(f"다리 행: {[(x['src'], x['ord'], x['leg_id']) for x in legs]}")
        e2 = next(x for x in legs if x["leg_id"] == "e2")
        if e2["size_pct"] is not None or json.loads(e2["extra"]) != {"size_pct": "1%"}:
            p.append(f"e2 문자열 size_pct가 extra로 안 갔다: {e2}")
        fx = db.q("SELECT fact_id, extra FROM jd_thesis_fact ORDER BY ord")
        if [x["fact_id"] for x in fx] != ["SRC-0001", None] or set(json.loads(fx[1]["extra"])) != {"tier", "meta"}:
            p.append(f"근거 행: {fx}")
        ev = db.q("SELECT src, ts FROM jd_thesis_event ORDER BY src, ord")
        if [x["src"] for x in ev] != ["corrections", "history"]:
            p.append(f"이력·교정 행: {ev}")
        # 키가 빠진 채로 다시 쓰면 그 키가 사라지고 컬럼도 빈다
        t3 = copy.deepcopy(t)
        del t3["invalidation"]
        t3["entry_triggers"] = []
        db.thesis_put(t3)
        p += _same(db.thesis_get(t["id"]), t3, "키 삭제 뒤")
        r = db.q1("SELECT invalidation FROM jd_thesis WHERE id=?", (t["id"],))
        if r["invalidation"] is not None:
            p.append("빠진 키의 컬럼이 남았다")
        if _n("jd_thesis_leg", "src='entry_triggers'"):
            p.append("비운 진입 다리의 자식 행이 남았다")
    report("(1) 논지 왕복 — 키 순서·1/1.0/True·null/키 없음·타입 어긋남은 extra·빈 목록·다리·근거·이력·교정", p)


def t02_roundtrip_scenario():
    p = []
    with fresh("t02"):
        _seed_facts(1)
        s = _scenario()
        db.scenario_put(copy.deepcopy(s))
        p += _same(db.scenario_get(s["id"]), s, "get")
        r = db.q1("SELECT realized, acted, price_cond, extra FROM jd_scenario WHERE id=?", (s["id"],))
        if (r["realized"], r["acted"], r["price_cond"]) != (0, None, 1) or json.loads(r["extra"]) != {"acted": 0}:
            p.append(f"불리언 칸: {r}")
        lst = db.q("SELECT src, ord, value FROM jd_scenario_list ORDER BY src, ord")
        if [(x["src"], x["value"]) for x in lst] != [("sources", "kr:261016"), ("sources", "us:261015")]:
            p.append(f"문자열 목록 행: {lst}")
        j = db.q("SELECT when_, realized, auto, fact_ids, extra FROM jd_scenario_judgment ORDER BY ord")
        if j[0]["when_"] != "261016" or j[0]["auto"] != 1 or json.loads(j[0]["fact_ids"] or "null") != ["SRC-0001"] or json.loads(j[1]["extra"]) != {"realized": 1}:
            p.append(f"판정 행: {j}")
        # 목록 칸에 문자열이 아닌 원소가 섞이면 통째로 extra
        s2 = _scenario("SC-KR-261016-2")
        s2["sources"] = ["kr:261016", 7]
        s2["judgments"] = ["옛 문장 판정"]
        db.scenario_put(copy.deepcopy(s2))
        p += _same(db.scenario_get(s2["id"]), s2, "섞인 목록")
        p += _same(db.scenarios_all(), [s, s2], "all")
    report("(2) 시나리오 왕복 — 불리언 칸 0/1/False·문자열 목록·섞인 목록은 extra·판정·대응", p)


def t03_roundtrip_market_map():
    p = []
    with fresh("t03") as d:
        mm = {"schema_version": "1.0", "axes": [_axis("ax_mem"), {"id": "ax_rates", "name": "금리"}],
              "sectors": [_sector(), dict(_sector("SEC-KR-BANK"), carried_runs="3", chains=["x", None])],
              "_comment": "끝 칸"}
        db.market_map_save(copy.deepcopy(mm))
        p += _same(db.market_map_load(), mm, "market_map")
        notes = {x["k"]: x["text"] for x in db.q("SELECT k, text FROM jd_axis_note")}
        if notes != {"note_261014": "직전 판단", "note_261016": "국내 run 판단 — 유지", "note_261015b": "보조 판단"}:
            p.append(f"note_<stamp> 칸이 이름 규칙 표로 안 갔다: {notes}")
        ex = json.loads(db.q1("SELECT extra FROM dim_axis WHERE id='ax_mem'")["extra"])
        if set(ex) != {"note_261013", "note_261012_fc"}:
            p.append(f"축 extra: {sorted(ex)}")
        b = db.q("SELECT ticker, bord, priced, extra FROM jd_axis_beneficiary ORDER BY ord")
        if b[0]["bord"] != 1 or b[0]["priced"] != 0 or json.loads(b[1]["extra"]) != {"order": 2.0}:
            p.append(f"수혜 행: {b}")
        # 축 하나만 고치면 그 행만 바뀐다(다른 행 판 그대로)
        r0 = {a: db.axes.rev(a) for a in ("ax_mem", "ax_rates")}
        mm2 = copy.deepcopy(mm)
        mm2["axes"][1]["note_261017"] = "새 판단"
        n = db.market_map_save(mm2)
        if n != 1 or db.axes.rev("ax_mem") != r0["ax_mem"] or db.axes.rev("ax_rates") != r0["ax_rates"] + 1:
            p.append(f"바뀐 행만 써야 한다: n={n} 판 {r0} → {db.axes.rev('ax_mem')},{db.axes.rev('ax_rates')}")
        p += _same(db.market_map_load(), mm2, "고친 뒤")
        # 축 직접 접근
        db.axis_update("ax_rates", lambda a: a.update(driver="rates"))
        if db.axis_get("ax_rates").get("driver") != "rates" or db.sector_get("SEC-KR-SEMI")["carried_runs"] != 3:
            p.append("axis_update·sector_get")
        # 내보내기 = 옛 파일과 바이트 단위로 같다(들여쓰기 1 · 끝 줄바꿈 없음)
        with contextlib.redirect_stdout(io.StringIO()):
            out = db.export("market_map")
        if out.read_text(encoding="utf-8") != json.dumps(db.market_map_load(), ensure_ascii=False, indent=1):
            p.append("market_map 내보내기가 옛 형식과 다르다")
        if d not in out.parents:
            p.append(f"내보내기가 스크래치 밖: {out}")
    report("(3) 지도(축·섹터) 왕복 — note_<stamp> 이름 규칙 칸·수혜·고리·섞인 목록·바뀐 행만 쓰기·내보내기", p)


def t04_roundtrip_allocation_company_fact():
    p = []
    with fresh("t04"):
        a = _allocation()
        db.allocation_record(copy.deepcopy(a))
        p += _same(db.allocation_last("KR"), a, "배분")
        r = db.q1("SELECT target_invested_pct, gap_pct, deployable FROM jd_allocation")
        if [type(r[k]) for k in ("target_invested_pct", "gap_pct", "deployable")] != [int, float, int]:
            p.append(f"배분 숫자 타입: {r}")
        a2 = dict(copy.deepcopy(a), regime="risk_off")
        db.allocation_record(a2)                              # 같은 id → 바꾼다(행 하나)
        if _n("jd_allocation") != 1 or db.allocation_last("KR")["regime"] != "risk_off":
            p.append("같은 id 배분이 덧붙었다")
        if db.allocation_last("KR", before_stamp="261016") is not None:
            p.append("before_stamp 거르기")
        co = {"market": "KR", "ticker": "005930", "excd": None, "name": "삼성전자", "aliases": ["삼성", "Samsung"], "gates": [],
              "axis": "메모리", "run_id": 261016}            # 문자 칸에 정수 → extra
        db.companies_store.put(copy.deepcopy(co))
        p += _same(db.companies_store.get(("KR", "005930")), co, "종목")
        facts = [_fact(1), _fact(2, tier="T1", load_bearing=1, quote="원문 인용", minor=None, runs=[]),
                 _fact(3, runs=["kr:261016", 5], note_261015_fc="교정", split_pending=True)]
        for f in facts:
            db.facts_store.put(copy.deepcopy(f))
        p += _same(db.facts_store.all(), facts, "사실")
        r = db.q1("SELECT legacy_text, source_label, tier, load_bearing FROM ev_fact WHERE id='SRC-0001'")
        if (r["legacy_text"], r["source_label"], r["tier"], r["load_bearing"]) != ("사실 문장 1", "DART", 1, 1):
            p.append(f"사실 컬럼 이름 바꿈(fact→legacy_text·source→source_label): {r}")
    report("(4) 배분·종목·사실 왕복 — 숫자 타입·같은 id 덮기·문자열 목록·키 이름 바꾼 컬럼", p)


# ───────────────────────────── ② 읽고-고치고-쓰기 · 판 충돌 ─────────────────────────────

def t05_update_load_save():
    p = []
    with fresh("t05"):
        _seed_facts(1, 2)
        ids = ["A", "B", "C"]
        for i in ids:
            db.thesis_put(dict(_thesis(i)))
        nd = db.thesis_update("A", lambda t: t.update(status="held"))
        if nd["status"] != "held" or db.theses.rev("A") != 2 or db.thesis_get("A")["status"] != "held":
            p.append("update(fn이 None을 돌려주면 받은 dict를 쓴다)")
        db.thesis_set_status("A", "closed", note="청산", ts="2026-10-16T15:00:00+09:00")
        h = db.thesis_get("A")["history"][-1]
        if h != {"ts": "2026-10-16T15:00:00+09:00", "status": "closed", "note": "청산"}:
            p.append(f"thesis_set_status 이력: {h}")
        db.thesis_mark_fired("B", "e1", note="체결", at="2026-10-16T10:00:00+09:00")
        e1 = next(x for x in db.thesis_get("B")["entry_triggers"] if x["id"] == "e1")
        if (e1.get("fired"), e1.get("fired_at"), e1.get("fired_note")) != (True, "2026-10-16T10:00:00+09:00", "체결"):
            p.append(f"thesis_mark_fired: {e1}")
        try:
            db.thesis_update("없음", lambda t: t)
            p.append("없는 행 update가 KeyError가 아니다")
        except KeyError:
            pass
        r0 = db.theses.rev("C")

        def boom(t):
            t["status"] = "x"
            raise ValueError("fn 실패")
        try:
            db.thesis_update("C", boom)
        except ValueError:
            pass
        if db.theses.rev("C") != r0 or db.thesis_get("C")["status"] != "armed":
            p.append("fn이 실패했는데 썼다")
        # load → 한 행만 고쳐 save → 그 행만 쓴다
        docs = db.theses_load()
        revs = [db.theses.rev(i) for i in ids]
        docs[2]["status"] = "watch"
        n = db.theses_save(docs)
        if n != 1 or [db.theses.rev(i) for i in ids] != [revs[0], revs[1], revs[2] + 1]:
            p.append(f"save가 바뀐 행만 쓰지 않았다: n={n}")
        # 끝에 새 행 → 끝 순서로 들어간다
        docs = db.theses_load()
        docs.append(_thesis("D"))
        if db.theses_save(docs) != 1 or [t["id"] for t in db.theses_all()] != ["A", "B", "C", "D"]:
            p.append("새 행 추가·순서")
        # load 뒤 다른 writer가 고친 행을 이쪽도 고쳐 save → StaleWrite, 같은 save의 다른 행도 안 써진다
        docs = db.theses.load()
        other = db.DocStore(db.SPEC["thesis"])           # 다른 프로세스(손절 감시)
        other.update("A", lambda t: t.update(status="held"))
        docs[0]["status"] = "armed"
        docs[1]["status"] = "watch"
        rb = db.theses.rev("B")
        try:
            db.theses.save(docs)
            p.append("다른 writer가 고친 행을 덮어썼다(StaleWrite 없음)")
        except db.StaleWrite:
            pass
        if db.thesis_get("A")["status"] != "held" or db.theses.rev("B") != rb:
            p.append("StaleWrite인데 일부가 써졌다(트랜잭션이 안 묶였다)")
        # put(expect_rev)
        rv = db.theses.rev("B")
        d = db.thesis_get("B")
        if db.theses.put(dict(d, status="x1"), expect_rev=rv) != rv + 1:
            p.append("expect_rev가 맞는데 판이 안 올랐다")
        try:
            db.theses.put(dict(d, status="x2"), expect_rev=rv)
            p.append("옛 판(expect_rev)으로 덮어썼다")
        except db.StaleWrite:
            pass
        if db.thesis_get("B")["status"] != "x1":
            p.append("StaleWrite 뒤 값이 바뀌었다")
        try:
            db.theses.put({"ticker": "키 없음"})
            p.append("키 없는 행을 받았다")
        except ValueError:
            pass
    report("(5) update·load/save·put(expect_rev) — 읽고-고치고-쓰기 · 바뀐 행만 · 다른 writer는 StaleWrite · 실패 시 무변경", p)


def t05b_save_skips_untouched_stale_row():
    """load 뒤 다른 writer(손절 감시)가 A를 고쳐도, 이쪽이 B만 고쳤으면 B만 쓴다 — 통째 낙관적 잠금이 되면 행 단위 쓰기가 무의미해진다."""
    p = []
    with fresh("t05b"):
        _seed_facts(1, 2)
        for i in ("A", "B"):
            db.thesis_put(_thesis(i))
        docs = db.theses.load()
        other = db.DocStore(db.SPEC["thesis"])           # 손절 감시가 A를 고쳤다
        other.update("A", lambda t: t.update(status="held"))
        docs[1]["status"] = "watch"                      # 이쪽은 B만 고쳤다
        try:
            n = db.theses.save(docs)
            if n != 1:
                p.append(f"쓴 행 수 {n}(B 하나여야)")
        except db.StaleWrite as e:
            p.append(f"이쪽이 안 고친 행 A 때문에 B 저장이 거부됐다: {e}")
        if db.thesis_get("A")["status"] != "held":
            p.append("다른 writer의 A 변경이 덮였다")
        if db.thesis_get("B")["status"] != "watch":
            p.append("B가 저장되지 않았다")
    report("(5b) save — 이쪽이 안 고친 행은 다른 writer가 고쳤어도 건너뛰고, 고친 행만 쓴다", p)


# ───────────────────────────── ③ 다리 계산 칸 ─────────────────────────────

def t06_leg_derived():
    p = []
    with fresh("t06"):
        cases = [("price <= 68000", "price_le", 68000.0), ("price>=75000.5", "price_ge", 75000.5),
                 ("from_peak_pct <= -8", "from_peak", -8.0), ("days_since_entry >= 20", "days_since", 20.0),
                 ("pnl_pct <= -10", "pnl_le", -10.0), ("judge", "judge", None), (" judge ", "judge", None),
                 ("price < 5", "other", None), ("price <= 70000 or judge", "other", None), (None, None, None)]
        for expr, kind, level in cases:
            if db.parse_check(expr) != (kind, level):
                p.append(f"parse_check({expr!r}) = {db.parse_check(expr)} (기대 {(kind, level)})")
        legs = [{"id": f"L{i}", "check": c} for i, (c, _, _) in enumerate(cases) if c is not None] + [{"id": "Lnone"}]
        db.thesis_put({"id": "T", "ticker": "005930", "entry_triggers": legs[:5], "exit_triggers": legs[5:]})
        got = {r["leg_id"]: (r["check_kind"], r["level"]) for r in db.q("SELECT leg_id, check_kind, level FROM jd_thesis_leg")}
        for i, (c, kind, level) in enumerate(cases):
            if c is None:
                continue
            if got.get(f"L{i}") != (kind, level):
                p.append(f"다리 {c!r} 컬럼 = {got.get(f'L{i}')}")
        if got.get("Lnone") != (None, None):
            p.append(f"check 없는 다리: {got.get('Lnone')}")
        if type(got["L0"][1]) is not float:
            p.append("level이 실수가 아니다")
        if "check_kind" in json.dumps(db.thesis_get("T")) or "level" in db.thesis_get("T")["entry_triggers"][0]:
            p.append("계산 칸이 원장 dict로 새었다")
        db.thesis_update("T", lambda t: t["entry_triggers"][0].update(check="price <= 66000"))
        r = db.q1("SELECT check_kind, level FROM jd_thesis_leg WHERE leg_id='L0'")
        if (r["check_kind"], r["level"]) != ("price_le", 66000.0):
            p.append(f"check를 고친 뒤 계산 칸이 안 따라왔다: {r}")
    report("(6) 다리 계산 칸 — price<=·price>=·from_peak_pct<=-N·days_since_entry>=N·pnl_pct<=-N·judge·기타 → check_kind·level", p)


# ───────────────────────────── ④ 테스트 문지기 ─────────────────────────────

def t07_test_guard():
    p = []
    with fresh("t07") as d:
        saved_env = {k: os.environ.get(k) for k in ("AITB_DB", "AITB_TESTING")}
        saved_main, saved_argv0 = sys.modules.get("__main__"), sys.argv[0]
        fake = types.ModuleType("__main__")
        try:
            os.environ.pop("AITB_DB", None)
            os.environ.pop("AITB_TESTING", None)
            sys.modules["__main__"] = fake
            fake.__file__ = "/x/test_fake.py"
            try:
                db._guard(REAL_DB)
                p.append("test_ 실행 파일인데 AITB_DB 없이 통과했다")
            except db.TestGuard:
                pass
            db.reset()
            try:
                db.connect(d / "guard.db")                 # 문지기가 못 막아도 스크래치 파일만 생긴다
                p.append("connect가 문지기를 거치지 않았다")
            except db.TestGuard:
                pass
            fake.__file__ = "/x/run_auto.py"
            try:
                db._guard(REAL_DB)
            except db.TestGuard:
                p.append("운영 스크립트(run_auto.py)를 막았다")
            os.environ["AITB_TESTING"] = "1"
            try:
                db._guard(REAL_DB)
                p.append("AITB_TESTING=1인데 통과했다")
            except db.TestGuard:
                pass
            os.environ.pop("AITB_TESTING")
            del fake.__file__                              # 대화형 실행 — sys.argv[0]으로 본다
            sys.argv[0] = "test_shell.py"
            try:
                db._guard(REAL_DB)
                p.append("__file__ 없는 test_ 실행을 통과시켰다")
            except db.TestGuard:
                pass
            os.environ["AITB_DB"] = str(d / "trading.db")
            os.environ["AITB_TESTING"] = "1"
            try:
                db._guard(REAL_DB)
            except db.TestGuard:
                p.append("AITB_DB가 있는데 막았다")
        finally:
            sys.modules["__main__"] = saved_main
            sys.argv[0] = saved_argv0
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            db.reset()
        if os.environ.get("AITB_DB") != str(d / "trading.db"):
            p.append("AITB_DB를 되돌리지 못했다")
    report("(7) 테스트 문지기 — test_ 실행·AITB_TESTING은 AITB_DB 없이 저장소를 못 연다 · 운영 스크립트는 통과", p)


# ───────────────────────────── ⑤ 추가만 표 ─────────────────────────────

def _equity_row(date="2026-10-16", equity=10_000_000):
    return {"date": date, "ts": f"{date}T15:40:00+09:00", "market": "KR", "currency": "KRW", "equity": equity, "cash": 512_000,
            "n_positions": 2, "cum_pnl_pct": 1.25, "positions": [{"ticker": "005930", "qty": 10, "price": 70000}]}


def t08_append_only_tables():
    p = []
    with fresh("t08"):
        c = db.connect()
        db.run_start("261016", "KR")
        _seed_facts(1)
        aid = c.execute("INSERT INTO ex_approval (run_id, pass, ticker, market, action, qty, price, approved_path, approved_sha, body) "
                        "VALUES ('261016_kr','main','005930','kr','BUY',3,69500,'signals/a.json','sha','{}')").lastrowid   # v8 필수 칸
        oid = c.execute("INSERT INTO ex_order (approval_id, run_id, ticker, market, side, qty, order_no, sent_at, status, trade_key, body) "
                        "VALUES (?,'261016_kr','005930','kr','BUY',3,'0001','t','SENT','k','{}')", (aid,)).lastrowid
        fid = c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) "
                        "VALUES (?,'261016_kr','005930','kr',3,'FILLED','{}')", (oid,)).lastrowid
        vid = c.execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, fact_rev) VALUES ('SRC-0001','261016_kr','MATCH', 1)").lastrowid
        db.equity_append(_equity_row())
        eid = db.q1("SELECT rid FROM ex_equity_point")["rid"]
        db.screen_put([{"run_id": "261016_kr", "pass_no": 1, "ticker": "005930", "action": "BUY", "outcome": "accepted"}])
        sid = db.q1("SELECT id FROM ex_screen")["id"]
        for table, key, val, col in (("ex_approval", "id", aid, "qty"), ("ex_order", "id", oid, "qty"),
                                     ("ex_fill", "id", fid, "filled_qty"), ("ev_verdict", "id", vid, "verdict"),
                                     ("ex_equity_point", "rid", eid, "equity"), ("ex_screen", "id", sid, "outcome")):
            for verb, sql in (("UPDATE", f"UPDATE {table} SET {col}=? WHERE {key}=?"), ("DELETE", f"DELETE FROM {table} WHERE {key}=?")):
                try:
                    c.execute(sql, ((0, val) if verb == "UPDATE" else (val,)))
                    p.append(f"{table} {verb}가 통과했다")
                except sqlite3.DatabaseError as e:
                    if "추가만" not in str(e):
                        p.append(f"{table} {verb} 거부 문구: {e}")
            if _n(table) != 1:
                p.append(f"{table} 행이 바뀌었다")
        r = db.q1("SELECT qty FROM ex_approval WHERE id=?", (aid,))
        if r["qty"] != 3:
            p.append("승인 수량이 바뀌었다")
        # 추가는 계속 된다
        c.execute("INSERT INTO ex_approval (run_id, pass, ticker, market, action, qty, approved_path, approved_sha, body)"
                  " VALUES ('261016_kr','stops','005930','kr','SELL',3,'signals/s.json','sha2','{}')")
        if _n("ex_approval") != 2:
            p.append("추가가 막혔다")
        # 자산 곡선 초기화 스위치 — sys_meta allow_equity_reset='1'일 때만 지울 수 있다
        c.execute("INSERT OR REPLACE INTO sys_meta (k, v) VALUES ('allow_equity_reset','1')")
        c.execute("DELETE FROM ex_equity_point")
        if _n("ex_equity_point") or _n("ex_equity_position"):
            p.append("초기화 스위치를 켰는데 자산 곡선이 안 지워졌다(자식 행 포함)")
    report("(8) 추가만 표 — ex_approval·ex_order·ex_fill·ev_verdict·ex_equity_point·ex_screen의 UPDATE·DELETE 거부 · 추가는 됨 · 자산 곡선 초기화 스위치", p)


def t09_logs():
    p = []
    with fresh("t09") as d:
        for name, (table, ao, _) in db.LOGS.items():
            if not ao:
                continue
            for fn, args in ((db.log_replace, (name, [])), (db.log_upsert, (name, {"id": "x"}))):
                try:
                    fn(*args)
                    p.append(f"{fn.__name__}({name})가 추가만 기록을 고쳐 썼다")
                except RuntimeError:
                    pass
        db.log_append("incidents", {"ts": "2026-10-16T10:00:00+09:00", "market": "KR", "what": "x"})
        try:
            db.connect().execute("UPDATE op_incident SET body='{}'")
            p.append("op_incident UPDATE가 통과했다")
        except sqlite3.DatabaseError:
            pass
        # 통째 다시 쓰던 로그는 log_replace를 받는다
        db.log_append("sessions", {"ts": "a", "market": "KR", "stamp": "261016"})
        rows = [{"ts": "2026-10-16", "market": "US", "n": 1}, {"ts": "2026-10-17", "n": 2.0}]
        db.log_replace("sessions", copy.deepcopy(rows))
        p += _same(db.log_rows("sessions"), rows, "sessions")
        if db.log_rows("sessions", market="US") != rows[:1]:
            p.append("market 거르기(대소문자)")
        # 피드백 — id가 같으면 바꾸고, 없으면 붙인다 · 순서 유지 · 렌더본이 따라온다
        f1 = {"id": "FB-1", "ts": "2026-10-17", "status": "open", "market": "KR"}
        f2 = {"id": "FB-2", "ts": "2026-10-17", "status": "open", "market": "US"}
        db.log_append("feedback", f1)
        db.log_append("feedback", f2)
        f1b = dict(f1, status="applied", ts="2026-10-18")
        db.log_upsert("feedback", f1b)
        f3 = {"id": "FB-3", "ts": "2026-10-18", "status": "open"}
        db.log_upsert("feedback", f3)
        want = [f1b, f2, f3]
        p += _same(db.log_rows("feedback"), want, "feedback")
        out = db.export_path("feedback")
        if not out.exists() or out.read_text(encoding="utf-8") != "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in want):
            p.append("피드백 렌더본이 따라오지 않았다")
        if d not in out.parents:
            p.append(f"피드백 렌더본이 스크래치 밖: {out}")
    report("(9) 로그 — log_replace·log_upsert는 추가만 기록을 거부 · sessions 통째 쓰기 · feedback upsert(순서·렌더본)", p)


# ───────────────────────────── ⑥ 사실 트리거 ─────────────────────────────

def t10_fact_version_trigger():
    p = []
    with fresh("t10"):
        f = _fact(1)
        db.facts_store.put(copy.deepcopy(f))
        if _n("ev_fact_version"):
            p.append("첫 기록에 판 행이 생겼다")
        db.facts_store.put(dict(f, fact="고친 문장", as_of="2026-10-17"))
        v = db.q("SELECT * FROM ev_fact_version WHERE fact_id='SRC-0001' ORDER BY id")
        if len(v) != 1 or v[0]["version"] != 1:
            p.append(f"판 행: {v}")
        else:
            snap = json.loads(v[0]["snapshot"])
            if (snap.get("legacy_text"), snap.get("as_of"), snap.get("tier"), snap.get("url")) != (
                    "사실 문장 1", "2026-10-16", 1, "https://example.com/f1"):
                p.append(f"판 1 스냅숏이 옛 값이 아니다: {snap}")
        db.facts_store.update("SRC-0001", lambda d: d.update(url="https://example.com/new"))
        v = db.q("SELECT version, snapshot FROM ev_fact_version WHERE fact_id='SRC-0001' ORDER BY id")
        if [x["version"] for x in v] != [1, 2] or json.loads(v[1]["snapshot"])["legacy_text"] != "고친 문장":
            p.append(f"두 번째 판: {v}")
        # 판이 그대로인 갱신(재검토 표시 등)은 판 행을 만들지 않는다
        db.connect().execute("UPDATE ev_fact SET needs_review=1 WHERE id='SRC-0001'")
        if _n("ev_fact_version") != 2:
            p.append("판이 안 바뀐 UPDATE가 판 행을 만들었다")
        try:
            db.connect().execute("UPDATE ev_fact_version SET version=9")
            p.append("ev_fact_version UPDATE가 통과했다")
        except sqlite3.DatabaseError:
            pass
    report("(10) 사실 판 트리거 — rev가 오르면 ev_fact_version에 그때 값 · 판 그대로면 안 남김 · 판 행은 추가만", p)


def _mismatch_setup():
    """사실 둘(SRC-0001 대상 · SRC-0002 대조군)을 논지·제안·참조·시나리오 판정이 가리키게 깐다."""
    _seed_facts(1, 2)
    a = _thesis("TA")
    b = _thesis("TB")
    b["evidence"] = [{"claim": "대조군", "fact_id": "SRC-0002"}]
    db.thesis_put(a)
    db.thesis_put(b)
    c = db.connect()
    db.run_start("261016", "KR")
    for idx, fid in ((0, "SRC-0001"), (1, "SRC-0002")):
        c.execute("INSERT INTO ex_proposal (run_id, sha, idx, ticker, market, action) VALUES ('261016_kr', 'sig-test', ?, '005930', 'kr', 'BUY')", (idx,))
        c.execute("INSERT INTO ex_proposal_fact (run_id, sha, idx, fact_id, role) VALUES ('261016_kr', 'sig-test', ?, ?, 'support')", (idx, fid))
        c.execute("INSERT INTO ev_claim_ref (fact_id, run_id, doc_type, doc_id, slot) VALUES (?, '261016_kr', 'note', 'n1', ?)",
                  (fid, f"s{idx}"))
    db.scenario_put(_scenario("SA", fact_ids=["SRC-0001"]))
    db.scenario_put(_scenario("SB", fact_ids=["SRC-0002"]))
    return a


def _flags(fid):
    return {
        "ev_fact": db.q1("SELECT needs_review AS v FROM ev_fact WHERE id=?", (fid,))["v"],
        "ex_proposal_fact": db.q1("SELECT needs_review AS v FROM ex_proposal_fact WHERE fact_id=?", (fid,))["v"],
        "ex_proposal": db.q1("SELECT p.needs_review AS v FROM ex_proposal p JOIN ex_proposal_fact f USING (run_id, idx) "
                             "WHERE f.fact_id=?", (fid,))["v"],
        "jd_thesis_fact": db.q1("SELECT needs_review AS v FROM jd_thesis_fact WHERE fact_id=?", (fid,))["v"],
        "jd_thesis": db.q1("SELECT t.needs_review AS v FROM jd_thesis t JOIN jd_thesis_fact f ON f.pid=t.rid WHERE f.fact_id=?",
                           (fid,))["v"],
        "ev_claim_ref": db.q1("SELECT needs_review AS v FROM ev_claim_ref WHERE fact_id=?", (fid,))["v"],
        "jd_scenario_judgment": db.q1("SELECT MAX(needs_review) AS v FROM jd_scenario_judgment WHERE fact_ids=?", (json.dumps([fid]),))["v"],
    }


def t11_verdict_mismatch_trigger():
    p = []
    with fresh("t11"):
        a = _mismatch_setup()
        c = db.connect()
        for v in ("MATCH", "STALE", "UNVERIFIABLE"):
            c.execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, fact_rev) VALUES ('SRC-0001', '261016_kr', ?, 1)", (v,))
        if any(_flags("SRC-0001").values()):
            p.append(f"MISMATCH가 아닌 판정이 표시를 남겼다: {_flags('SRC-0001')}")
        rev0 = db.facts_store.rev("SRC-0001")
        c.execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, fact_rev) VALUES ('SRC-0001', '261016_kr', 'MISMATCH', 1)")
        got = _flags("SRC-0001")
        miss = [k for k, v in got.items() if v != 1]
        if miss:
            p.append(f"MISMATCH 표시가 안 간 곳: {miss}")
        other = {k: v for k, v in _flags("SRC-0002").items() if v}
        if other:
            p.append(f"다른 사실(SRC-0002)의 행까지 표시했다: {other}")
        if db.facts_store.rev("SRC-0001") != rev0 or _n("ev_fact_version"):
            p.append("재검토 표시가 사실 판을 올렸다")
        p += _same(db.thesis_get("TA"), a, "표시는 원장 dict로 새지 않는다")
    report("(11) MISMATCH 트리거 — ev_fact·제안 근거·제안·논지 근거·논지(fact_id로)·참조·시나리오 판정에 needs_review · 대조군 무변경", p)


def t11b_review_flag_survives_thesis_update():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: put()이 자식 행을 지우고 다시 넣어 jd_thesis_fact·jd_scenario_judgment의 needs_review가 0으로 돌아간다
    p = []
    with fresh("t11b"):
        _mismatch_setup()
        db.connect().execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, fact_rev) VALUES ('SRC-0001', '261016_kr', 'MISMATCH', 1)")
        db.thesis_set_status("TA", "held", note="체결", ts="2026-10-16T10:30:00+09:00")    # 재검토와 무관한 갱신
        db.scenario_update("SA", lambda s: s.update(next_check="2026-10-30"))
        got = _flags("SRC-0001")
        if got["jd_thesis_fact"] != 1:
            p.append(f"논지 근거 행 needs_review가 논지 갱신 뒤 {got['jd_thesis_fact']}로 돌아갔다")
        if got["jd_scenario_judgment"] != 1:
            p.append(f"시나리오 판정 needs_review가 시나리오 갱신 뒤 {got['jd_scenario_judgment']}로 돌아갔다")
        if got["jd_thesis"] != 1:
            p.append("논지 needs_review가 사라졌다")
    report("(11b) 재검토 표시는 무관한 논지·시나리오 갱신(put) 뒤에도 남는다", p)


def t11c_scenario_fact_ids_list():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: fact_ids를 목록(["SRC-0001"])으로 쓰면 extra로 가서 MISMATCH 트리거의 LIKE가 못 본다
    p = []
    with fresh("t11c"):
        _seed_facts(1)
        s = _scenario("SL", fact_ids=["SRC-0001"])
        db.scenario_put(copy.deepcopy(s))
        p += _same(db.scenario_get("SL"), s, "왕복")
        db.connect().execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, fact_rev) VALUES ('SRC-0001', '261016_kr', 'MISMATCH', 1)")
        v = db.q1("SELECT needs_review FROM jd_scenario_judgment WHERE ord=0")["needs_review"]
        if v != 1:
            p.append(f"목록 모양 fact_ids의 판정이 MISMATCH 표시를 못 받았다(needs_review={v})")
    report("(11c) 시나리오 판정 fact_ids가 목록이어도 MISMATCH 표시가 간다", p)


# ───────────────────────────── ⑦ 내보내기 ─────────────────────────────

def _fmt_text(name, value):
    fmt = (db.EXPORTS.get(name) or db.CHECK_ONLY[name])[1]
    if fmt[0] == "jsonl":
        return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in value)
    return json.dumps(value, ensure_ascii=False, indent=fmt[1]) + fmt[2]


def t12_exports_idempotent_mode_format():
    p = []
    with fresh("t12") as d:
        _seed_facts(1, 2)
        db.thesis_put(_thesis())
        db.equity_append(_equity_row())
        db.log_append("feedback", {"id": "FB-1", "ts": "2026-10-17", "status": "open"})
        db.coverage_save({"005930": {"last": "261016", "n": 3}})
        out = db.export("theses")
        if out != d / "exports" / "journal" / "theses.json":
            p.append(f"기본 경로가 AITB_DB 옆 exports가 아니다: {out}")
        if stat.S_IMODE(out.stat().st_mode) != 0o444:
            p.append(f"권한 {oct(stat.S_IMODE(out.stat().st_mode))}(0o444여야)")
        s1 = out.stat()
        db.export("theses")
        s2 = out.stat()
        if (s1.st_ino, s1.st_mtime_ns) != (s2.st_ino, s2.st_mtime_ns):
            p.append("같은 내용인데 다시 썼다")
        db.thesis_update(_thesis()["id"], lambda t: t.update(status="held"))
        db.export("theses")
        if json.loads(out.read_text(encoding="utf-8"))["theses"][0]["status"] != "held":
            p.append("바뀐 내용을 다시 쓰지 않았다(읽기 전용 파일 위에)")
        if stat.S_IMODE(out.stat().st_mode) != 0o444:
            p.append("다시 쓴 뒤 권한이 0o444가 아니다")
        for name, (real, fmt) in db.EXPORTS.items():
            o = db.export(name)
            txt = o.read_text(encoding="utf-8")
            if txt != _fmt_text(name, db.render(name)):
                p.append(f"{name}: 형식(들여쓰기·끝 줄바꿈)이 EXPORTS와 다르다")
            if fmt[0] == "jsonl":
                if not txt.endswith("\n") or not all(json.loads(ln) is not None for ln in txt.splitlines()):
                    p.append(f"{name}: jsonl 줄 형식")
            elif fmt[2] == "" and txt.endswith("\n"):
                p.append(f"{name}: 끝 줄바꿈이 없어야 한다")
            elif fmt[2] == "\n" and not txt.endswith("}\n"):
                p.append(f"{name}: 끝 줄바꿈이 있어야 한다")
            if stat.S_IMODE(o.stat().st_mode) != 0o444:
                p.append(f"{name}: 권한")
            if o != d / "exports" / real.relative_to(db.HERE):
                p.append(f"{name}: 경로 {o}")
    report("(12) 내보내기 — 멱등(같으면 안 씀) · 0o444 · 바뀌면 다시 씀 · EXPORTS 형식 · AITB_DB 옆 exports", p)


def t13_exports_match_old_files():
    """이전 직후 렌더본이 옛 파일과 바이트 단위로 같아야 한다 — 옛 파일 텍스트를 만들고, 그 내용을 넣고, 내보낸 결과와 비교한다."""
    p = []
    with fresh("t13"):
        _seed_facts(1, 2)
        old = {}
        theses = [_thesis(), {"id": "t2", "ticker": "000660", "status": "closed"}]
        for t in theses:
            db.thesis_put(copy.deepcopy(t))
        db.docmeta_put("theses", {"schema_version": "1.0", "_ko": ["schema_version", "theses"]})
        old["theses"] = json.dumps({"schema_version": "1.0", "theses": theses}, ensure_ascii=False, indent=2) + "\n"
        facts = [_fact(1), _fact(2), _fact(3, tier="T2", quote="원문")]
        db.facts_store.put(copy.deepcopy(facts[2]))
        db.docmeta_put("sources", {"schema_version": "1.1", "_ko": ["schema_version", "sources"]})
        old["sources"] = json.dumps({"schema_version": "1.1", "sources": facts}, ensure_ascii=False, indent=2)
        wl = _watchlist()
        db.watchlist_save(copy.deepcopy(wl))
        old["watchlist"] = json.dumps(wl, ensure_ascii=False, indent=2)
        mm = {"schema_version": "1.0", "axes": [_axis()], "sectors": [_sector()]}
        db.market_map_save(copy.deepcopy(mm))
        old["market_map"] = json.dumps(mm, ensure_ascii=False, indent=1)
        cov = {"005930": {"last": "261016", "n": 3, "ok": True}, "AAPL": {"last": None}}
        db.coverage_save(copy.deepcopy(cov))
        old["universe_coverage"] = json.dumps(cov, ensure_ascii=False, indent=2)
        eq = [_equity_row("2026-10-15", 9_950_000), dict(_equity_row("2026-10-16", 10_000_000.5), account_mode="paper")]
        for r in eq:
            db.equity_append(copy.deepcopy(r))
        old["equity_curve"] = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in eq)
        fb = [{"id": "FB-1", "ts": "2026-10-17", "status": "open", "evidence": {"n": 2}}]
        db.log_append("feedback", copy.deepcopy(fb[0]))
        old["feedback"] = json.dumps(fb[0], ensure_ascii=False) + "\n"
        for name in db.EXPORTS:
            got = db.export(name).read_text(encoding="utf-8")
            if got != old[name]:
                p.append(f"{name}: 옛 파일과 다르다(길이 {len(got)} vs {len(old[name])})")
    report("(13) 내보내기 = 옛 파일 바이트 단위 — 논지·출처·감시 목록·지도·유니버스 범위·자산 곡선·피드백", p)


def _live_stat():
    out = {}
    for name, (path, _) in list(db.EXPORTS.items()) + list(db.CHECK_ONLY.items()):
        try:
            s = path.stat()
            out[name] = (s.st_size, s.st_mtime_ns)
        except OSError:
            out[name] = None
    return out


def t14_export_path_redirect():
    p = []
    with fresh("t14") as d:
        for name in list(db.EXPORTS) + list(db.CHECK_ONLY):
            real = (db.EXPORTS.get(name) or db.CHECK_ONLY[name])[0]
            got = db.export_path(name)
            if got != d / "exports" / real.relative_to(db.HERE):
                p.append(f"{name}: AITB_DB 아래로 안 돌렸다 → {got}")
        # AITB_EXPORT_REAL=1이면 실제 경로 — 경로만 계산한다(쓰지 않는다)
        os.environ["AITB_EXPORT_REAL"] = "1"
        try:
            if db.export_path("theses") != db.EXPORTS["theses"][0]:
                p.append("AITB_EXPORT_REAL=1인데 실제 경로가 아니다")
        finally:
            os.environ.pop("AITB_EXPORT_REAL", None)
        saved = os.environ.pop("AITB_DB")
        try:
            if db.export_path("watchlist") != db.CONFIG / "watchlist.json":
                p.append("AITB_DB가 없으면 실제 경로여야 한다")
        finally:
            os.environ["AITB_DB"] = saved
        # 내보내기·자동 내보내기(자산 곡선·피드백·유니버스 범위)가 실제 렌더본을 건드리지 않는다
        before = _live_stat()
        _seed_facts(1)
        db.thesis_put(_thesis())
        db.equity_append(_equity_row())
        db.log_append("feedback", {"id": "FB-9", "ts": "2026-10-17"})
        db.coverage_save({"x": {"n": 1}})
        db.export_all()
        for name in db.CHECK_ONLY:
            db.export(name)
        if _live_stat() != before:
            p.append(f"실제 journal/config 렌더본이 바뀌었다: {[k for k, v in _live_stat().items() if before.get(k) != v]}")
        written = sorted(str(x.relative_to(d)) for x in (d / "exports").rglob("*") if x.is_file())
        want = sorted(str(Path("exports") / ((db.EXPORTS.get(n) or db.CHECK_ONLY[n])[0]).relative_to(db.HERE))
                      for n in list(db.EXPORTS) + list(db.CHECK_ONLY))
        if written != want:
            p.append(f"exports 아래 파일: {written}")
    report("(14) 내보내기 경로 — AITB_DB면 <폴더>/exports/<상대경로> · AITB_EXPORT_REAL=1·AITB_DB 없음은 실제 경로 · 실제 렌더본 무변경", p)


def t15_render_all_names():
    p = []
    with fresh("t15") as d:
        want = {"equity_curve": [], "feedback": [], "universe_coverage": {}, "universe_pending": {},
                "market_map": {"schema_version": "1.0", "axes": [], "sectors": []},
                "theses": {"schema_version": "1.0", "theses": []},
                "scenarios": {"scenarios": []},
                "calendar": {"schema_version": "1.0", "events": []},
                "sources": {"schema_version": "1.1", "sources": []},
                "watchlist": {"_comment": None, "schema_version": None, "KR": [], "US": [], "benchmarks": None},
                "position_peaks": {"_schema": 2, "KR": {}, "US": {}}}
        names = list(db.EXPORTS) + list(db.CHECK_ONLY)
        if sorted(names) != sorted(want):
            p.append(f"내보내기 이름 목록이 바뀌었다: {sorted(names)}")
        for n in names:
            try:
                p += _same(db.render(n), want.get(n), f"render({n})")
            except Exception as e:           # noqa: BLE001
                p.append(f"render({n}) 오류 {type(e).__name__}: {e}")
        try:
            db.render("없는이름")
            p.append("없는 이름이 KeyError가 아니다")
        except KeyError:
            pass
        # 값이 있을 때 — 시나리오 메타·일정·고점·대기
        db.docmeta_put("scenarios", {"schema_version": "2.0", "_ko": ["schema_version", "scenarios"]})
        _seed_facts(1)
        db.scenario_put(_scenario())
        db.peaks_set("KR", "005930", 72000.0, "2026-10-16T10:00:00+09:00")
        db.pending_save({"373220": {"why": "검토"}})
        r = db.render("scenarios")
        if list(r) != ["schema_version", "scenarios"] or r["schema_version"] != "2.0" or len(r["scenarios"]) != 1:
            p.append(f"render(scenarios) 메타: {list(r)}")
        if db.render("position_peaks")["KR"] != {"005930": {"peak_price": 72000.0, "peak_at": "2026-10-16T10:00:00+09:00"}}:
            p.append("render(position_peaks)")
        if db.render("universe_pending") != {"373220": {"why": "검토"}}:
            p.append("render(universe_pending)")
        for n in db.CHECK_ONLY:              # 이전 검사 전용 — dest로 스크래치에 쓸 수 있다
            o = db.export(n, dest=d / "check" / f"{n}.json")
            if o.read_text(encoding="utf-8") != _fmt_text(n, db.render(n)):
                p.append(f"{n}: dest 내보내기 형식")
    report("(15) render — 내보내기 이름 전부(검사 전용 포함) 빈 저장소 모양 · 메타 키 순서 · 없는 이름 KeyError", p)


# ───────────────────────────── ⑧ 일봉 캐시 ─────────────────────────────

def _days_back(end_ymd, n):
    e = datetime.strptime(end_ymd, "%Y%m%d")
    return [(e - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


class FakeKis:
    """브로커 대역 — 부른 횟수를 센다. 같은 날짜는 같은 값(겹치는 구간이 어긋나지 않게) · 오늘 행만 tick이 오르면 바뀐다."""

    def __init__(self, today):
        self.today = today
        self.calls = []
        self.tick = 0
        self.last = None

    def _px(self, d, base):
        v = base + int(d[-2:]) * 100
        return v + self.tick if d == self.today else v

    def domestic_daily(self, ticker, start, end):
        self.calls.append(("dd", ticker, start, end))
        self.last = [{"date": d, "close": self._px(d, 70000), "open": self._px(d, 69500), "high": self._px(d, 70500),
                      "low": self._px(d, 69000), "volume": 1_000_000 + int(d[-2:])}
                     for d in _days_back(end, 40) if d >= start]
        return copy.deepcopy(self.last)

    def domestic_index_daily(self, code, start, end):
        self.calls.append(("di", code, start, end))
        self.last = [{"date": d, "close": 2500.25 + int(d[-2:]), "open": 2490.5, "high": 2510.75, "low": 2480.0,
                      "volume": 300_000 + int(d[-2:])} for d in _days_back(end, 40) if d >= start]
        return copy.deepcopy(self.last)

    def overseas_daily(self, ticker, excd="NAS", bymd=""):
        self.calls.append(("od", ticker, excd, bymd))
        end = bymd or self.today
        self.last = [{"date": d, "close": 230.5 + int(d[-2:]) / 100 + (self.tick / 10 if d == self.today else 0),
                      "open": 229.0, "high": 232.25, "low": 228.5, "volume": 41_000_000 + int(d[-2:])} for d in _days_back(end, 5)]
        return copy.deepcopy(self.last)

    def domestic_sector_board(self, market="K"):
        self.calls.append(("sb", market))
        # kis_client.domestic_sector_board의 실제 반환 모양(등락률 내림차순)
        self.last = [{"code": "0013", "name": "전기·전자", "close": 3012.45, "change_pct": 1.23, "turnover_100m": 52310.5, "is_sector": True},
                     {"code": "0021", "name": "금융", "close": 812.1, "change_pct": -0.41, "turnover_100m": 8123.0, "is_sector": True}]
        return copy.deepcopy(self.last)


def _ymd(dt):
    return dt.strftime("%Y%m%d")


def _backdate(subject, key, minutes):
    ts = (db.datetime.now(KST) - timedelta(minutes=minutes)).isoformat(timespec="seconds")   # db의 시계(시험이 고정한다)
    db.connect().execute("UPDATE mk_bar_fetch SET fetched_at=? WHERE subject=? AND k=?", (ts, subject, key))


@contextlib.contextmanager
def _at(*dt):
    """db의 '지금'을 고정한다(일봉 확정 계약은 시계로 정해진다 · v5)."""
    saved = db.datetime
    db.datetime = _Clock
    _Clock.at = datetime(*dt, tzinfo=KST)
    try:
        yield
    finally:
        db.datetime = saved


def t16_bars_cache_domestic():
    """(16) 국내 일봉 확정 계약(v5 · U04) — 지난 구간은 받을 때 닫혀 있었으면 표(오래돼도) · 확정 읽기는 끝 날짜를 마지막 닫힌 세션으로
    자른다 · 장중 값(need_final=False)은 미확정으로 저장하고 TTL로만 · 마감 뒤 확정 읽기는 다시 받아 확정(판 +1 · 관측 이력) ·
    bars는 기본 확정만 · bars_asof는 그때 본 값."""
    p = []
    with fresh("t16"):
      with _at(2026, 10, 14, 10, 0):         # 수 10:00 KST — 국내 장중 · 마지막 닫힌 세션 10-13
          c = FakeKis("20261014")
          r1 = db.domestic_daily(c, "005930", "20261001", "20261010")
          p += _same(r1, c.last, "첫 조회 = 클라이언트 반환")
          r2 = db.domestic_daily(c, "005930", "20261001", "20261010")
          if len(c.calls) != 1:
              p.append(f"같은 구간 두 번째 조회가 브로커를 불렀다({len(c.calls)}회)")
          p += _same(r2, r1, "표에서 준 값(최신순·YYYYMMDD·정수)")
          db.domestic_daily(c, "005930", "20260920", "20261010")
          if len(c.calls) != 2:
              p.append("다른 구간인데 표에서 줬다")
          _backdate("KR:005930", "dd:20261001:20261010", 60 * 48)
          db.domestic_daily(c, "005930", "20261001", "20261010")
          if len(c.calls) != 2:
              p.append("받을 때 닫혀 있던 구간을 오래됐다고 다시 받았다")
          r3 = db.domestic_daily(c, "005930", "20261001", "20261014")       # 오늘(열린 세션)까지 — 확정 읽기는 10-13까지
          if len(c.calls) != 3 or c.calls[-1][3] != "20261013" or r3[0]["date"] != "20261013":
              p.append(f"확정 읽기의 끝 날짜 자르기: {c.calls[-1]} · 첫 행 {r3[0]['date'] if r3 else '-'}")
          db.domestic_daily(c, "005930", "20261001", "20261014")
          if len(c.calls) != 3:
              p.append("확정 구간 두 번째 조회가 브로커를 불렀다")
          r4 = db.domestic_daily(c, "005930", "20261001", "20261014", need_final=False)   # 장중 값
          if len(c.calls) != 4 or r4[0]["date"] != "20261014":
              p.append(f"장중 조회: {len(c.calls)}회 · {r4[0]['date'] if r4 else '-'}")
          db.domestic_daily(c, "005930", "20261001", "20261014", need_final=False)
          if len(c.calls) != 4:
              p.append("장중 조회 TTL 안 두 번째가 브로커를 불렀다")
          c.tick = 7
          _Clock.at = datetime(2026, 10, 14, 10, 20, tzinfo=KST)        # 20분 뒤 — TTL이 지났고 장중 값이 바뀌었다
          r5 = db.domestic_daily(c, "005930", "20261001", "20261014", need_final=False)
          if len(c.calls) != 5 or r5[0]["close"] != r4[0]["close"] + 7:
              p.append(f"TTL 지난 장중 조회를 다시 받지 않았다({len(c.calls)}회)")
          db.domestic_daily(c, "005930", "20261001", "20261014", ttl_min=0, need_final=False)
          if len(c.calls) != 6:
              p.append("ttl_min=0 장중 조회를 표에서 줬다")
          row = db.q1("SELECT is_final, version FROM mk_bar WHERE subject='KR:005930' AND date='2026-10-14'")
          if (row["is_final"], row["version"]) != (0, 2):
              p.append(f"장중 행: 미확정 · 판 2(값이 한 번 바뀜): {row}")
          if any(b["date"] == "2026-10-14" for b in db.bars("KR", "005930")) or \
                  not any(b["date"] == "2026-10-14" and b["is_final"] == 0 for b in db.bars("KR", "005930", final_only=False)):
              p.append("bars 기본(확정만)이 장중 행을 줬거나 final_only=False가 안 줬다")
      with _at(2026, 10, 14, 16, 0):                          # 마감 뒤 — 마지막 닫힌 세션 10-14
          n = len(c.calls)
          r6 = db.domestic_daily(c, "005930", "20261001", "20261014")
          row = db.q1("SELECT is_final, version, close FROM mk_bar WHERE subject='KR:005930' AND date='2026-10-14'")
          if len(c.calls) != n + 1 or r6[0]["date"] != "20261014" or (row["is_final"], row["version"]) != (1, 3):
              p.append(f"마감 뒤 확정 읽기: 호출 {len(c.calls) - n} · {row}")
          first = db.bars_asof("KR:005930", "2026-10-14", "2026-10-14T10:05:00+09:00")
          if not first or first["close"] != r4[0]["close"] or db.q1("SELECT COUNT(*) AS n FROM mk_bar_obs WHERE subject='KR:005930'"
                                                                     " AND date='2026-10-14'")["n"] != 3:
              p.append(f"관측 이력·bars_asof: {first}")
          b = db.q1("SELECT close, source FROM mk_bar WHERE subject='KR:005930' AND date='2026-10-13'")
          if type(b["close"]) is not int or b["source"] != "KIS":
              p.append(f"mk_bar 행: {b}")
    report("(16) 국내 일봉 확정 계약 — 닫힌 구간은 표(오래돼도) · 확정 읽기는 끝 날짜 자름 · 장중 값은 미확정·TTL · 마감 뒤 확정(판·이력) · bars 확정만", p)


def t17_bars_cache_overseas_index():
    """(17) 해외·지수 일봉 + bars — 미국 장중(KST 23:00)의 최신 조회는 마지막 닫힌 세션까지 · 같은 조회는 표 · 미국 마감 뒤엔 다시 받아
    그 세션 행이 확정 · 지난 기준일은 표 · 지수는 같은 구간 표 · bars 칸(확정·관측 시각·판 포함) · bars는 읽기만."""
    p = []
    with fresh("t17"):
      with _at(2026, 10, 14, 23, 0):         # KST 23:00 = 미국 10-14 장중 · 마지막 닫힌 세션 10-13
          c = FakeKis("20261014")
          o1 = db.overseas_daily(c, "AAPL", "NAS")
          o2 = db.overseas_daily(c, "AAPL", "NAS")
          if len(c.calls) != 1 or not o1 or max(r["date"] for r in o1) != "20261013":
              p.append(f"미국 장중 최신 조회: 호출 {len(c.calls)} · 마지막 {max(r['date'] for r in o1) if o1 else '-'}")
          p += _same(o2, o1, "해외 표 값(최신순·실수)")
          db.overseas_daily(c, "AAPL", "NAS", "20261008")
          db.overseas_daily(c, "AAPL", "NAS", "20261008")
          if len(c.calls) != 2:
              p.append("지난 기준일 해외 조회를 다시 받았다")
          i1 = db.domestic_index_daily(c, "0001", "20261001", "20261010")
          i2 = db.domestic_index_daily(c, "0001", "20261001", "20261010")
          if len(c.calls) != 3:
              p.append("지수 같은 구간 두 번째가 브로커를 불렀다")
          p += _same(i2, i1, "지수 표 값")
          if not _n("mk_bar", "subject='IDX:0001'"):
              p.append("지수가 IDX: 주제로 안 남았다")
          n0 = len(c.calls)
          r = db.domestic_daily(c, "005930", "20261001", "20261010")
          b = db.bars("KR", "005930", since="20261001", until="20261010")
          if [x["date"] for x in b] != [f"{x['date'][:4]}-{x['date'][4:6]}-{x['date'][6:]}" for x in reversed(r)]:
              p.append("bars: 날짜 오름차순·YYYY-MM-DD")
          if b and list(b[0]) != ["date", "open", "high", "low", "close", "volume", "is_final", "observed_at", "version"]:
              p.append(f"bars 칸: {list(b[0])}")
          if [x["close"] for x in b] != [x["close"] for x in reversed(r)]:
              p.append("bars 값")
          if len(db.bars("KR", "0001", index=True)) != len(i1) or len(db.bars("US", "AAPL")) < len(o1):
              p.append("bars(index=True)·미국")
          if len(c.calls) != n0 + 1:
              p.append("bars가 브로커를 불렀다")
      with _at(2026, 10, 15, 6, 30):                          # 미국 10-14 마감(05:00 KST) 뒤
          n = len(c.calls)
          o3 = db.overseas_daily(c, "AAPL", "NAS")
          if len(c.calls) != n + 1 or max(r["date"] for r in o3) != "20261014" or \
                  db.q1("SELECT is_final FROM mk_bar WHERE subject='US:AAPL' AND date='2026-10-14'")["is_final"] != 1:
              p.append("미국 마감 뒤 최신 조회가 다시 받지 않았거나 그 세션 행이 확정이 아니다")
    report("(17) 해외·지수 일봉 + bars — 장중 최신 = 마지막 닫힌 세션까지 · 마감 뒤 다시 받아 확정 · 지난 기준일·지수는 표 · bars 칸·읽기만", p)


def t18_sector_board_store():
    p = []
    with fresh("t18"):
        c = FakeKis(datetime.now(KST).strftime("%Y%m%d"))
        rows = db.domestic_sector_board(c, "K")
        p += _same(rows, c.last, "반환은 클라이언트 그대로")
        day = datetime.now(KST).date().isoformat()
        board = db.sector_board("KR", day)
        if [(b["code"], b["name"], b["idx"]) for b in board] != [("0013", "전기·전자", 3012.45), ("0021", "금융", 812.1)]:
            p.append(f"mk_sector_board 행: {board}")
        body = json.loads(db.q1("SELECT body FROM mk_sector_board WHERE code='0013'")["body"])
        if body != c.last[0]:
            p.append("원본 행이 body에 안 남았다")
        db.domestic_sector_board(c, "K")                # 같은 날 다시 → 같은 키는 바꾼다
        if _n("mk_sector_board") != 2 or len(c.calls) != 2:
            p.append("같은 날 다시 받은 보드가 행을 늘렸다")
    report("(18) 업종 보드 — 클라이언트 반환 그대로 · 오늘 날짜로 mk_sector_board에 남김 · 같은 날은 덮어쓰기", p)


def t18b_sector_board_change_pct():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: kis_client는 등락률을 change_pct로 주는데 db는 chg_pct·pct만 읽어 mk_sector_board.chg_pct가 늘 NULL
    p = []
    with fresh("t18b"):
        c = FakeKis(datetime.now(KST).strftime("%Y%m%d"))
        db.domestic_sector_board(c, "K")
        got = {b["code"]: b["chg_pct"] for b in db.sector_board("KR", datetime.now(KST).date().isoformat())}
        if got != {"0013": 1.23, "0021": -0.41}:
            p.append(f"등락률이 안 남았다: {got}")
    report("(18b) 업종 보드 등락률 — 클라이언트의 change_pct가 mk_sector_board.chg_pct로 간다", p)


class _Clock(datetime):
    """db 안의 '지금'을 고정한다(db.datetime 자리에 끼운다)."""
    at = None

    @classmethod
    def now(cls, tz=None):
        return cls.at.astimezone(tz) if tz is not None else cls.at.replace(tzinfo=None)


def t18c_overseas_live_session_after_kst_midnight():
    """(18c) 리뷰 재현 intraday_bar_cached_forever_when_date_becomes_past의 올바른 동작 — 한국 자정을 넘긴 미국 장중(KST 00:19 · 동부 10-19)에
    확정 읽기는 금요일(10-16)까지만 · 장중 값 읽기는 TTL로 다시 · 미국 마감 뒤 확정 읽기는 다시 받아 10-19가 확정."""
    p = []
    with fresh("t18c"):
        c = FakeKis("20261019")
        with _at(2026, 10, 20, 0, 19):
            r0 = db.overseas_daily(c, "AAPL", "NAS", "20261019")
            if not r0 or max(r["date"] for r in r0) != "20261016":
                p.append(f"자정 넘긴 장중 확정 읽기가 열린 세션 행을 줬다: {[r['date'] for r in r0]}")
            db.overseas_daily(c, "AAPL", "NAS", "20261019", need_final=False)
            c.tick = 5
        with _at(2026, 10, 20, 0, 30):
            n = len(c.calls)
            db.overseas_daily(c, "AAPL", "NAS", "20261019", need_final=False)
            if len(c.calls) != n + 1:
                p.append("11분 뒤 장중 값 읽기를 표에서 줬다(TTL)")
        with _at(2026, 10, 20, 9, 0):                    # 미국 10-19 마감 뒤
            n = len(c.calls)
            r1 = db.overseas_daily(c, "AAPL", "NAS", "20261019")
            if len(c.calls) != n + 1 or max(r["date"] for r in r1) != "20261019" or \
                    db.q1("SELECT is_final FROM mk_bar WHERE subject='US:AAPL' AND date='2026-10-19'")["is_final"] != 1:
                p.append("마감 뒤 확정 읽기가 다시 받지 않았거나 10-19가 확정이 아니다(부분 일봉이 굳는다)")
    report("(18c) 해외 일봉 — 자정 넘긴 미국 장중: 확정 읽기는 금요일까지 · 장중 값은 TTL · 마감 뒤 다시 받아 확정(리뷰 재현 U04)", p)


def t18d_finality_dst_finalize_holidays():
    """(18d) 확정 판정·재확인·휴장일 — 서머타임(10-30 금 세션은 05:00 KST 뒤 확정 · 11-02 월 세션은 06:00 KST 뒤) · 마감된 세션의 미확정 행을
    bars_finalize가 다시 물어 확정 · 브로커가 그 날 행을 안 주면 승격하지 않고 사유를 남긴다(같은 날 다시 안 묻는다) · 거래량 짐작 함수 없음 ·
    새 저장소의 휴장일 사본 · 커버리지 줄."""
    import kis_client
    p = []
    if not db._is_final("US", "2026-10-30", "2026-10-31T05:30:00+09:00") or db._is_final("US", "2026-11-02", "2026-11-03T05:30:00+09:00") \
            or not db._is_final("US", "2026-11-02", "2026-11-03T06:30:00+09:00"):
        p.append("서머타임 마감 판정")
    if hasattr(kis_client, "drop_incomplete_session"):
        p.append("거래량으로 미완성 세션을 짐작하는 함수가 남아 있다")
    with fresh("t18d"):
        n_h = db.q1("SELECT COUNT(*) AS n FROM mk_holiday")["n"]
        want = sum(len(v) for k, v in json.loads((HERE / "config" / "holidays.json").read_text(encoding="utf-8")).items() if k in ("kr", "us"))
        if n_h != want or "휴장일 표 커버리지 종료" not in db.holidays_coverage_line():
            p.append(f"휴장일 사본 {n_h}/{want}")
        c = FakeKis("20261014")
        with _at(2026, 10, 14, 10, 0):
            db.domestic_daily(c, "005930", "20261010", "20261014", ttl_min=0, need_final=False)
            db.domestic_daily(c, "000660", "20261010", "20261014", ttl_min=0, need_final=False)
        with _at(2026, 10, 14, 16, 0):
            todo = db.bars_to_finalize()
            gone = FakeKis("20261014")
            gone.domestic_daily = lambda t, s_, e_: [] if t == "000660" else FakeKis.domestic_daily(gone, t, s_, e_)
            r = db.bars_finalize(gone)
            f1 = db.q1("SELECT is_final FROM mk_bar WHERE subject='KR:005930' AND date='2026-10-14'")["is_final"]
            f2 = db.q1("SELECT is_final, final_check_err FROM mk_bar WHERE subject='KR:000660' AND date='2026-10-14'")
            if len(todo) != 2 or r["finalized"] != 1 or r["missing"] != 1 or f1 != 1 or f2["is_final"] != 0 or not f2["final_check_err"]:
                p.append(f"재확인: 대상 {len(todo)} · {r} · {f1} · {f2}")
            if db.bars_to_finalize():
                p.append("같은 날 다시 물을 대상이 남았다")
    report("(18d) 확정 판정(서머타임)·재확인(bars_finalize — 없는 행은 사유만)·거래량 짐작 없음·휴장일 사본·커버리지 줄", p)


# ───────────────────────────── ⑨ 고점·일정·감시 목록 ─────────────────────────────

def t19_peaks():
    p = []
    with fresh("t19"):
        pk = db.peaks_update("KR", [{"ticker": "005930", "price": 70000}, {"ticker": 660, "price": 200000.5}], now="t1")
        if pk != {"005930": {"peak_price": 70000.0, "peak_at": "t1"}, "660": {"peak_price": 200000.5, "peak_at": "t1"}}:
            p.append(f"첫 고점: {pk}")
        if type(pk["005930"]["peak_price"]) is not float:
            p.append("고점은 실수(옛 position_peaks.json 모양)")
        pk = db.peaks_update("kr", [{"ticker": "005930", "price": 69000}, {"ticker": "660", "price": 210000}], now="t2")
        if pk["005930"] != {"peak_price": 70000.0, "peak_at": "t1"} or pk["660"] != {"peak_price": 210000.0, "peak_at": "t2"}:
            p.append(f"올리기만 해야 한다: {pk}")
        db.peaks_update("US", [{"ticker": "AAPL", "price": 230.5}], now="t2")
        pk = db.peaks_update("KR", [{"ticker": "005930", "price": 69500}], now="t3")
        if list(pk) != ["005930"]:
            p.append(f"판 종목(660) 고점이 안 지워졌다: {pk}")
        if db.peaks_for_market("US") != {"AAPL": {"peak_price": 230.5, "peak_at": "t2"}}:
            p.append("다른 시장 고점을 건드렸다")
        before = db.peaks_all()
        db.peaks_update("KR", None)
        if db.peaks_all() != before:
            p.append("positions=None이 고점을 바꿨다")
        db.peaks_reset("KR")
        if db.peaks_all() != {"_schema": 2, "KR": {}, "US": {"AAPL": {"peak_price": 230.5, "peak_at": "t2"}}}:
            p.append(f"peaks_reset(KR): {db.peaks_all()}")
        db.peaks_reset()
        if db.peaks_all() != {"_schema": 2, "KR": {}, "US": {}}:
            p.append("peaks_reset()")
    report("(19) 고점 — 처음 기록·올리기만·판 종목 삭제·시장 분리·None은 무변경·peaks_reset", p)


def t19b_peak_kept_when_price_missing():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: 보유 중인데 가격이 비어(None) 넘어온 종목을 '판 종목'으로 보고 고점을 지운다(추적 손절 기준이 사라진다)
    p = []
    with fresh("t19b"):
        db.peaks_update("KR", [{"ticker": "005930", "price": 80000}], now="t1")
        db.peaks_update("KR", [{"ticker": "005930", "price": None, "qty": 10}], now="t2")
        if db.peaks_for_market("KR") != {"005930": {"peak_price": 80000.0, "peak_at": "t1"}}:
            p.append(f"가격만 빠진 보유 종목의 고점이 지워졌다: {db.peaks_for_market('KR')}")
    report("(19b) 고점 — 보유 중인데 가격이 없는 종목은 지우지 않는다(판 종목만 지운다)", p)


def t20_calendar():
    p = []
    with fresh("t20"):
        events = [
            {"date": "2026-10-20", "event": "SK하이닉스 3Q 실적", "axis": "ax_mem", "key": "earn-000660-3q", "tickers": ["000660"],
             "kind": "earnings", "added": "261016"},
            {"date": "2026-10-29", "event": "FOMC", "key": "fomc-1029", "axes": ["ax_rates"], "kind": "macro", "kind_fixed": True,
             "note": None},
            {"date": "2026-11-05", "event": "키 없는 일정", "tickers": [], "why": "확인", "note_261015": "교정"},
        ]
        doc = {"schema_version": "1.0", "events": events, "_comment": "끝"}
        if db.calendar_save(copy.deepcopy(doc)) != 3:
            p.append("calendar_save 반환")
        p += _same(db.calendar_doc(), doc, "일정 왕복")
        db.calendar_put({"date": "2026-10-30", "event": "FOMC(날짜 정정)", "key": "fomc-1029", "kind": "macro"})
        evs = db.calendar_all()
        # 같은 key는 바꾸고, 목록은 날짜순(옛 add-event·ingest가 넣을 때마다 날짜로 안정 정렬했다)
        if len(evs) != 3 or evs[1] != {"date": "2026-10-30", "event": "FOMC(날짜 정정)", "key": "fomc-1029", "kind": "macro"} \
                or [e["date"] for e in evs] != sorted(e["date"] for e in evs):
            p.append(f"같은 key put은 바꾸고 날짜순이어야 한다: {[(e.get('key'), e.get('date')) for e in evs]}")
        db.calendar_put({"date": "2026-11-10", "event": "새 일정"})
        if len(db.calendar_all()) != 4:
            p.append("key 없는 put은 붙여야 한다")
        if db.calendar_delete("earn-000660-3q") != 1 or db.calendar_delete("earn-000660-3q") != 0:
            p.append("calendar_delete 반환")
        if any(e.get("key") == "earn-000660-3q" for e in db.calendar_all()):
            p.append("지운 일정이 남았다")
        if _n("cal_event_list", "pid NOT IN (SELECT rid FROM cal_event)"):
            p.append("지운 일정의 목록 행이 고아로 남았다")
        db.calendar_save(copy.deepcopy(events[:1]))     # 목록만 주면 메타는 그대로 두고 행만 맞춘다
        d2 = db.calendar_doc()
        p += _same(d2["events"], events[:1], "목록 저장")
        if d2.get("_comment") != "끝":
            p.append("목록 저장이 메타를 지웠다")
    report("(20) 일정 — calendar_save 왕복(메타·키 순서) · 같은 key put은 바꿈 · key 없는 put은 붙임 · delete · 고아 없음", p)


def t21_watchlist_main_names():
    p = []
    with fresh("t21"):
        wl = _watchlist()
        if db.watchlist_save(copy.deepcopy(wl)) != 3:
            p.append("watchlist_save 반환")
        p += _same(db.watchlist_doc(), wl, "감시 목록 왕복")
        db.main_names_refresh("KR", [{"ticker": "005930", "name": "삼성전자", "mcap_100m_krw": 4_000_000},
                                     {"ticker": "373220", "name": "LG에너지솔루션", "mcap_100m_krw": 900_000}],
                              as_of="2026-10-16", source="KIS")
        mn = db.main_names("KR")
        if [(r["ticker"], r["main_rank"], r["in_watchlist"]) for r in mn] != [("005930", 1, 1), ("373220", 2, 0)]:
            p.append(f"main_names: {mn}")
        if [c["ticker"] for c in db.companies("KR")] != ["005930", "000660"]:
            p.append(f"companies()가 감시 목록 밖 행을 냈다: {[c['ticker'] for c in db.companies('KR')]}")
        if "373220" not in [c["ticker"] for c in db.companies("KR", universe=False)]:
            p.append("universe=False가 시총 상위 행을 안 냈다")
        p += _same(db.watchlist_doc(), wl, "시총 갱신 뒤 렌더본 무변경")
        db.main_names_refresh("KR", [{"ticker": "000660", "name": "SK하이닉스", "mcap": 1}], as_of="2026-10-17")
        if [r["ticker"] for r in db.main_names("KR")] != ["000660"]:
            p.append("다시 갱신하면 옛 순위가 지워져야 한다")
        if db.q1("SELECT in_watchlist FROM dim_company WHERE ticker='373220'")["in_watchlist"] != 0:
            p.append("순위에서 빠진 감시 목록 밖 행")
        if [c["ticker"] for c in db.companies("US")] != ["AAPL"]:
            p.append("미국 목록")
        p += _same(db.watchlist_doc(), wl, "재갱신 뒤 렌더본 무변경")
    report("(21) 감시 목록 — watchlist_save/doc 왕복 · 시총 상위 밖 종목은 in_watchlist=0 행 · companies()는 제외 · 순위 재갱신", p)


def t21d_watchlist_resave_existing_rows():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: 이미 있는 종목 행을 다시 저장하면 _put_company_row가 old["rowid"]에서 IndexError로 죽는다
    #   (rid INTEGER PRIMARY KEY 표에서 SELECT rowid의 칸 이름은 'rid'다) — 감시 목록 갱신이 전부 실패한다
    p = []
    with fresh("t21d"):
        wl = _watchlist()
        db.watchlist_save(copy.deepcopy(wl))
        db.main_names_refresh("KR", [{"ticker": "005930", "name": "삼성전자", "mcap_100m_krw": 4_000_000}], as_of="2026-10-16")
        wl2 = copy.deepcopy(wl)
        wl2["KR"] = wl2["KR"][:1]
        try:
            db.watchlist_save(wl2)
        except Exception as e:                   # noqa: BLE001
            p.append(f"같은 종목을 다시 저장하다 죽었다: {type(e).__name__}: {e}")
        if [c["ticker"] for c in db.companies("KR")] != ["005930"]:
            p.append(f"감시 목록에서 뺀 종목이 남았다: {[c['ticker'] for c in db.companies('KR')]}")
        r = db.q1("SELECT in_watchlist FROM dim_company WHERE ticker='000660'")
        if not r or r["in_watchlist"] != 0:
            p.append("뺀 종목 행은 남고 in_watchlist=0이어야 한다")
        try:
            db.watchlist_save(copy.deepcopy(wl))
        except Exception as e:                   # noqa: BLE001
            p.append(f"다시 넣다 죽었다: {type(e).__name__}")
        p += _same(db.watchlist_doc(), wl, "다시 넣은 뒤")
        if db.q1("SELECT main_rank FROM dim_company WHERE ticker='005930'")["main_rank"] != 1:
            p.append("감시 목록 저장이 시총 순위를 지웠다")
    report("(21d) 감시 목록 재저장 — 있는 종목 행 갱신 · 뺀 종목 in_watchlist=0 · 다시 넣기 · 시총 순위 보존", p)


def t21b_watchlist_empty_market():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: 한 시장 목록을 비우면 `NOT IN (NULL)`이 아무 행도 안 걸러 옛 종목이 감시 목록에 남는다
    p = []
    with fresh("t21b"):
        wl = _watchlist()
        db.watchlist_save(copy.deepcopy(wl))
        wl["KR"], wl["US"] = [], []               # 두 시장 다 비운다(있는 행 재저장 경로 t21d와 섞이지 않게)
        db.watchlist_save(wl)
        for m in ("KR", "US"):
            if db.companies(m):
                p.append(f"{m} 목록을 비웠는데 남았다: {[c['ticker'] for c in db.companies(m)]}")
            if db.watchlist_doc()[m] != []:
                p.append(f"렌더본 {m}가 비지 않았다")
    report("(21b) 감시 목록 — 한 시장 목록을 비우면 그 시장 종목이 전부 빠진다", p)


def t21c_company_put_no_market_key():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: company_put은 market을 ko에 남겨, 렌더본 watchlist.json의 그 행에만 "market" 칸이 생긴다
    p = []
    with fresh("t21c"):
        db.watchlist_save(_watchlist())
        db.company_put({"market": "KR", "ticker": "373220", "name": "LG에너지솔루션", "added": "2026-10-17", "aliases": []})
        row = next((r for r in db.watchlist_doc()["KR"] if r.get("ticker") == "373220"), None)
        if row is None:
            p.append("company_put한 종목이 감시 목록에 없다")
        elif "market" in row:
            p.append(f"렌더본 행에 market 칸이 생겼다: {list(row)}")
    report("(21c) company_put — 감시 목록 렌더본 행 모양이 다른 행과 같다(market 칸 없음)", p)


# ───────────────────────────── ⑩ 집행 기록 이전 ─────────────────────────────

ST, MK = "261016", "kr"


def _trade(ts, ticker, action, qty, price, order_no, fill, **kw):
    """journal/trades_*.json의 trades[] 한 행(실제 모양)."""
    t = {"ts": ts, "status": "SENT", "market": "KR", "approved_ref": f"approved_{ST}_{MK}.json", "ticker": ticker, "action": action,
         "qty": qty, "price": price, "source": "llm", "thesis_id": f"th-{ticker}", "trigger_id": "e1",
         "result": {"order_no": order_no, "order_time": "093512", "raw": {"rt_cd": "0"}}, "fill": fill}
    t.update(kw)
    return t


def _fill(before, after, req, limit, verdict="FILLED", **kw):
    f = {"qty_before": before, "qty_after": after, "delta": after - before, "requested": req, "limit_price": limit, "verdict": verdict}
    f.update(kw)
    return f


def _artifacts(root, trades=None, facts=None):
    (root / "signals").mkdir(parents=True, exist_ok=True)
    (root / "journal").mkdir(parents=True, exist_ok=True)
    sig = {"schema_version": "2.0", "run_id": f"{ST}_{MK}", "market": "KR", "generated_at": "2026-10-16T10:05:00+09:00",
           "no_trade": False, "no_trade_reason": None,
           "proposals": [
               {"ticker": "005930", "action": "BUY", "qty": 3, "thesis_id": "th-005930", "axis_id": "ax_mem", "weight_target_pct": 2.0,
                "expected_upside_pct": 12, "horizon_days": 60, "confidence": 0.6, "size_why": "e1 2%", "entry": {"id": "e1"},
                "exits": [{"id": "x1", "check": "price <= 60000"}], "risk_notes": "실적 변동",
                "facts": facts if facts is not None else [{"id": "SRC-0001", "role": "support", "support": "직접"},
                                                          {"id": "SRC-0002", "role": "risk"}]},
               {"ticker": "000660", "action": "SELL", "qty": 1, "thesis_id": "th-000660", "forecast_id": "ax_mem2", "facts": []}],
           "holds": [{"ticker": "010950", "thesis_id": "th-010950", "why": "논지 유지"}],
           "refill_candidates": [{"ticker": "035420", "action": "BUY", "thesis_id": "th-035420"}],
           "note_lint": {"ok": True}, "track1": {"rounds": 1}}
    (root / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
    ap = {"orders": [{"ticker": "005930", "action": "BUY", "qty": 3, "price": 69500, "source": "llm", "idx": 0, "reasons": ["통과"],
                      "signal_ref": {"signal": f"signal_{ST}_{MK}.json", "idx": 0}},
                     {"ticker": "000660", "action": "SELL", "qty": 1, "price": 200000, "source": "llm", "idx": 1}],
          "rejected": [{"ticker": "035420", "action": "BUY", "reasons": ["주문당 상한"]}],
          "screen": {"pass": 1, "mode": "main"}}                   # risk_guard가 승인 머리에 적는 심사 회차(W05 결과 귀속의 열쇠)
    (root / "signals" / f"approved_{ST}_{MK}.json").write_text(json.dumps(ap, ensure_ascii=False), encoding="utf-8")
    st = {"orders": [{"ticker": "010950", "side": "SELL", "qty": 2, "limit_price": 101000, "source": "discipline", "needs_sell_fill": True}]}
    (root / "signals" / f"approved_{ST}_{MK}_stops.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    if trades is None:
        trades = [_trade("2026-10-16T10:10:00+09:00", "005930", "BUY", 3, 69500, "0000011111", _fill(0, 3, 3, 69500), settle_fx=None),
                  _trade("2026-10-16T10:10:05+09:00", "000660", "SELL", 1, 200000, "0000022222", _fill(5, 4, -1, 200000))]
    tr = {"generated_at": "2026-10-16T10:10:06+09:00", "account_mode": "paper", "trades": trades, "fill_confirmed": True}
    (root / "journal" / f"trades_{ST}_{MK}.json").write_text(json.dumps(tr, ensure_ascii=False, indent=2), encoding="utf-8")
    return sig, ap, st, tr


def _ex_counts():
    return {t: _n(t) for t in ("ex_signal", "ex_proposal", "ex_proposal_fact", "ex_hold", "ex_approval", "ex_order", "ex_fill", "ex_fill_fx")}


def t22_import_run_artifacts():
    p = []
    with fresh("t22") as d:
        _seed_facts(1, 2)
        db.facts_store.update("SRC-0001", lambda f: f.update(as_of="2026-10-15"))    # 판 2 — 제안 근거가 그 판을 가리킨다
        root = d / "art"
        sig, ap, st, tr = _artifacts(root)
        out = db.import_run_artifacts(ST, "KR", here=root)
        if out != {"signal": 1, "proposals": 2, "approvals": 3, "orders": 2, "fills": 2}:
            p.append(f"반환: {out}")
        want = {"ex_signal": 1, "ex_proposal": 3, "ex_proposal_fact": 2, "ex_hold": 1, "ex_approval": 4, "ex_order": 2, "ex_fill": 2,
                "ex_fill_fx": 0}
        if _ex_counts() != want:
            p.append(f"행 수: {_ex_counts()}")
        s = db.q1("SELECT * FROM ex_signal")
        raw = (root / "signals" / f"signal_{ST}_{MK}.json").read_bytes()
        if (s["run_id"], s["path"], s["sha"], s["no_trade"]) != (f"{ST}_{MK}", f"signals/signal_{ST}_{MK}.json",
                                                                  hashlib.sha256(raw).hexdigest(), 0):
            p.append(f"ex_signal: {dict((k, s[k]) for k in ('run_id', 'path', 'no_trade'))}")
        if json.loads(s["note_lint"]) != {"ok": True} or json.loads(s["track1"]) != {"rounds": 1}:
            p.append("ex_signal note_lint·track1")
        pr = db.q("SELECT * FROM ex_proposal ORDER BY idx")
        if [(x["idx"], x["ticker"], x["is_refill_candidate"], x["axis_id"]) for x in pr] != [
                (0, "005930", 0, "ax_mem"), (1, "000660", 0, "ax_mem2"), (1000, "035420", 1, None)]:
            p.append(f"ex_proposal: {[(x['idx'], x['ticker'], x['is_refill_candidate'], x['axis_id']) for x in pr]}")
        if json.loads(pr[0]["body"]) != sig["proposals"][0] or json.loads(pr[0]["exits"]) != sig["proposals"][0]["exits"]:
            p.append("ex_proposal body·exits")
        pf = db.q("SELECT fact_id, role, support, fact_version FROM ex_proposal_fact ORDER BY fact_id")
        if [(x["fact_id"], x["role"], x["support"], x["fact_version"]) for x in pf] != [
                ("SRC-0001", "support", "직접", 2), ("SRC-0002", "risk", None, 1)]:
            p.append(f"ex_proposal_fact(그때 판): {pf}")
        h = db.q1("SELECT * FROM ex_hold")
        if (h["ticker"], h["thesis_id"], h["why"]) != ("010950", "th-010950", "논지 유지"):
            p.append(f"ex_hold: {h}")
        a = db.q("SELECT id, pass, ticker, action, qty, price, rejected_why, needs_sell_fill, approved_path FROM ex_approval ORDER BY id")
        a_ids = [x["id"] for x in a]
        got = [(x["pass"], x["ticker"], x["action"], x["qty"], x["price"]) for x in a]
        if got != [("main", "005930", "BUY", 3, 69500), ("main", "000660", "SELL", 1, 200000), ("main", "035420", "BUY", None, None),
                   ("stops", "010950", "SELL", 2, 101000)]:
            p.append(f"ex_approval: {got}")
        if json.loads(a[2]["rejected_why"]) != ["주문당 상한"] or a[3]["needs_sell_fill"] != 1:
            p.append("ex_approval 거부 사유·needs_sell_fill")
        o = db.q("SELECT * FROM ex_order ORDER BY id")
        if [(x["ticker"], x["side"], x["qty"], x["order_no"], x["sent_at"], x["status"], x["limit_price"], x["thesis_id"]) for x in o] != [
                ("005930", "BUY", 3, "0000011111", "2026-10-16T10:10:00+09:00", "SENT", 69500, "th-005930"),
                ("000660", "SELL", 1, "0000022222", "2026-10-16T10:10:05+09:00", "SENT", 200000, "th-000660")]:
            p.append(f"ex_order: {o}")
        f = db.q("SELECT * FROM ex_fill ORDER BY id")
        if [(x["order_id"], x["filled_qty"], x["verdict"], x["qty_before"], x["qty_after"], x["requested"], x["limit_price"]) for x in f] != [
                (o[0]["id"], 3, "FILLED", 0, 3, 3, 69500), (o[1]["id"], 1, "FILLED", 5, 4, -1, 200000)]:   # 체결 수량 부호 없음(v7 · R11)
            p.append(f"ex_fill: {[(x['order_id'], x['filled_qty'], x['verdict']) for x in f]}")
        if json.loads(f[0]["body"]) != tr["trades"][0]["fill"]:
            p.append("ex_fill body")
        lk = [(x["approval_link"], x["approval_id"]) for x in o]
        if lk != [("inferred", a_ids[0]), ("inferred", a_ids[1])] or [x["idx"] for x in db.q("SELECT idx FROM ex_approval ORDER BY id")] != [0, 1, None, 0]:
            p.append(f"승인 연결(해시 없는 옛 행 — 유일하면 inferred) · 승인 순번: {lk}")
        if db.run_get(ST, "KR")["market"] != "kr":
            p.append("ex_run 행")
        # 같은 파일로 다시 → 아무것도 안 늘어난다
        before = _ex_counts()
        out2 = db.import_run_artifacts(ST, "KR", here=root)
        if out2 != {"signal": 0, "proposals": 0, "approvals": 0, "orders": 0, "fills": 0} or _ex_counts() != before:
            p.append(f"재실행이 멱등이 아니다: {out2} {_ex_counts()}")
        # 결제일 환율이 있는 체결은 ex_fill_fx에
        with fresh("t22fx") as d2:
            _seed_facts(1, 2)
            r2 = d2 / "art"
            _artifacts(r2, trades=[_trade("2026-10-16T23:40:00+09:00", "AAPL", "BUY", 2, 230.5, "0000033333",
                                          _fill(0, 2, 2, 230.5, settle_date="2026-10-19"), settle_fx=1401.5,
                                          settle_fx_source="KIS", market="US")])
            db.import_run_artifacts(ST, "KR", here=r2)
            fx = db.q1("SELECT settle_fx, source FROM ex_fill_fx")
            if not fx or (fx["settle_fx"], fx["source"]) != (1401.5, "KIS"):
                p.append(f"ex_fill_fx: {fx}")
            if db.q1("SELECT settle_date FROM ex_fill")["settle_date"] != "2026-10-19":
                p.append("ex_fill settle_date")
    report("(22) 집행 기록 이전 — 시그널·제안·근거(그때 판)·보류·승인(본·손절·거부)·주문·체결·환율 · 재실행 멱등", p)


def t22b_import_rewritten_trades_file():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: trades 파일이 다시 쓰이면(체결 확인·손절 pass 추가) 파일 해시가 바뀌어 같은 주문·체결이 통째로 다시 들어간다
    p = []
    with fresh("t22b") as d:
        _seed_facts(1, 2)
        root = d / "art"
        t1 = _trade("2026-10-16T10:10:00+09:00", "005930", "BUY", 3, 69500, "0000011111", _fill(0, 3, 3, 69500))
        t2 = _trade("2026-10-16T10:10:05+09:00", "000660", "SELL", 1, 200000, "0000022222", None)      # 아직 미확정
        _artifacts(root, trades=[copy.deepcopy(t1), copy.deepcopy(t2)])
        db.import_run_artifacts(ST, "KR", here=root)
        t2b = dict(t2, fill=_fill(5, 4, -1, 200000))                                                 # fill.py가 확정을 적었다
        t3 = _trade("2026-10-16T13:00:00+09:00", "010950", "SELL", 2, 101000, "0000044444", _fill(2, 0, -2, 101000),
                    source="discipline")                                                             # 손절 pass가 덧붙였다
        tr = {"generated_at": "2026-10-16T13:00:01+09:00", "trades": [t1, t2b, t3], "fill_confirmed": True}
        (root / "journal" / f"trades_{ST}_{MK}.json").write_text(json.dumps(tr, ensure_ascii=False, indent=2), encoding="utf-8")
        db.import_run_artifacts(ST, "KR", here=root)
        nos = [r["order_no"] for r in db.q("SELECT order_no FROM ex_order ORDER BY id")]
        if sorted(nos) != ["0000011111", "0000022222", "0000044444"]:
            p.append(f"주문이 겹쳐 들어갔다: {nos}")
        fills = db.q("SELECT o.order_no FROM ex_fill f JOIN ex_order o ON o.id=f.order_id")
        if sorted(r["order_no"] for r in fills) != ["0000011111", "0000022222", "0000044444"]:
            p.append(f"체결 행: {sorted(r['order_no'] for r in fills)}")
    report("(22b) 집행 기록 이전 — 다시 쓰인 trades 파일은 새 주문·새 체결만 더한다(같은 주문번호는 한 번)", p)


def t22c_import_unknown_fact_id():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: 제안 근거가 ev_fact에 없는 id를 가리키면 외래키 오류로 그 run의 시그널·승인·주문·체결 이전이 통째로 실패한다
    p = []
    with fresh("t22c") as d:
        _seed_facts(1)
        root = d / "art"
        _artifacts(root, facts=[{"id": "SRC-0001", "role": "support"}, {"id": "F-999999", "role": "support"}])
        try:
            db.import_run_artifacts(ST, "KR", here=root)
        except sqlite3.DatabaseError as e:
            p.append(f"없는 사실 id 하나로 이전 전체가 실패했다: {type(e).__name__}: {e}")
        c = _ex_counts()
        if c["ex_order"] != 2 or c["ex_fill"] != 2 or c["ex_signal"] != 1:
            p.append(f"주문·체결·시그널이 안 남았다: {c}")
    report("(22c) 집행 기록 이전 — 근거 사실 id 하나가 없어도 주문·체결 기록은 남는다", p)


# ───────────────────────────── ⑪ run 기록·잔고·점검·감사·백업·검사 ─────────────────────────────

def t23_run_timing():
    p = []
    with fresh("t23"):
        rid = db.run_start(ST, "KR", account_mode="paper", session_date="2026-10-16")
        if rid != "261016_kr":
            p.append(f"run_id {rid}")
        db.stage_mark(ST, "KR", "capture", at="2026-10-16T09:30:00+09:00")
        db.stage_mark(ST, "KR", "capture", at="2026-10-16T09:45:00+09:00")        # 먼저 찍힌 시각이 남는다
        for stg, hm in (("carry", "09:40"), ("map", "09:52"), ("note", "10:05"), ("dispatch", "10:11")):
            db.stage_mark(ST, "KR", stg, at=f"2026-10-16T{hm}:00+09:00")
        db.stage_mark(ST, "KR", "sent", at="2026-10-16T10:12:30+09:00")
        db.stage_mark(ST, "KR", "filled", at="2026-10-16T10:20:00+09:00")
        db.run_close(ST, "KR", closed_at="2026-10-16T10:45:00+09:00", status="closed", facts_new=4)
        v = db.q1("SELECT * FROM v_run_timing WHERE run_id=?", (rid,))
        if (v["capture_at"], v["capture_to_send_min"], v["post_fill_min"], v["facts_new"]) != (
                "2026-10-16T09:30:00+09:00", 42.5, 25.0, 4):
            p.append(f"v_run_timing: {v}")
        try:
            db.stage_mark(ST, "KR", "lunch")
            p.append("없는 단계 이름을 받았다")
        except ValueError:
            pass
        db.run_start(ST, "kr")                                                       # 다시 불러도 계좌 모드를 None으로 덮지 않는다
        db.run_set(ST, "KR", audit_rounds=2, material_delay_h=1.5)
        r = db.run_get(ST, "KR")
        if (r["account_mode"], r["session_date"], r["audit_rounds"], r["material_delay_h"], r["status"]) != (
                "paper", "2026-10-16", 2, 1.5, "closed"):
            p.append(f"run_set·run_start 재호출: {r}")
        db.run_close("261017", "US")
        c1 = db.run_get("261017", "US")["closed_at"]
        db.run_close("261017", "US")
        if not c1 or db.run_get("261017", "US")["closed_at"] != c1:
            p.append("run_close는 처음 닫힌 시각을 지켜야 한다")
        # 미국 run — 시간대가 섞여도 분 계산이 맞다(22:35 KST → 09:50 ET = 22:50 KST)
        db.stage_mark("261016", "US", "capture", at="2026-10-16T22:35:00+09:00")
        db.stage_mark("261016", "US", "sent", at="2026-10-16T09:50:00-04:00")
        if db.q1("SELECT capture_to_send_min AS m FROM v_run_timing WHERE run_id='261016_us'")["m"] != 15.0:
            p.append("시간대가 섞인 분 계산")
    report("(23) run 기록 — run_start·stage_mark(첫 시각 유지)·run_set·run_close · v_run_timing 분(시간대 섞여도)", p)


def t24_balance_positions():
    p = []
    with fresh("t24"):
        b1 = {"dnca_tot_amt": 5_000_000, "tot_evlu_amt": 10_200_000, "ord_psbl_amt": 4_800_000, "currency": "KRW",
              "deposit_only": False, "raw": {"x": [1, 2]}}
        db.balance_put(ST, "KR", "capture", copy.deepcopy(b1), at="2026-10-16T09:30:00+09:00")
        b2 = {"cash": 3100.25, "exchange_rate": 1401.5, "exchange_rate_at": "2026-10-16T22:30:00+09:00", "buying_power": 3000.0,
              "equity": 7200.5, "currency": "USD", "cash_shared": True}
        db.balance_put(ST, "US", "capture", copy.deepcopy(b2))
        b3 = dict(b1, dnca_tot_amt=2_000_000)
        db.balance_put(ST, "KR", "post_fill", copy.deepcopy(b3))
        last = db.balance_last("KR")
        if last["phase"] != "post_fill" or last["cash"] != 2_000_000 or last["body"] != b3:
            p.append(f"balance_last(KR): {last}")
        cap = db.balance_last("kr", "capture")
        if (cap["cash"], cap["equity"], cap["orderable"], cap["deposit_only"], cap["run_id"], cap["at"]) != (
                5_000_000, 10_200_000, 4_800_000, 0, "261016_kr", "2026-10-16T09:30:00+09:00"):
            p.append(f"국내 대표 칸(dnca_tot_amt·tot_evlu_amt·ord_psbl_amt): {cap}")
        p += _same(cap["body"], b1, "body")
        us = db.balance_last("US")
        if (us["cash"], us["exchange_rate"], us["buying_power"], us["equity"], us["cash_shared"], us["deposit_only"]) != (
                3100.25, 1401.5, 3000.0, 7200.5, 1, None):
            p.append(f"미국 대표 칸: {us}")
        if db.balance_last("KR", "stops") is not None:
            p.append("없는 단계가 None이 아니다")
        n = db.positions_put(ST, "KR", "capture", [
            {"ticker": "005930", "name": "삼성전자", "qty": 10, "avg_price": 68000, "price": 70000, "eval_amt": 700000, "pnl_pct": 2.94},
            {"ticker": 660, "qty": 1}], at="2026-10-16T09:30:00+09:00")
        rows = db.q("SELECT * FROM ex_position ORDER BY id")
        if n != 2 or [(r["run_id"], r["market"], r["phase"], r["ticker"], r["qty"], r["pnl_pct"]) for r in rows] != [
                ("261016_kr", "kr", "capture", "005930", 10, 2.94), ("261016_kr", "kr", "capture", "660", 1, None)]:
            p.append(f"positions_put: {rows}")
        if db.positions_put(ST, "KR", "post_fill", []) != 0 or db.positions_put(ST, "KR", "post_fill", None) != 0 or _n("ex_position") != 2:
            p.append("빈 보유")
    report("(24) 잔고·보유 — balance_put 대표 칸(시장별 이름)·balance_last(단계)·body 원본 · positions_put", p)


def t25_real_check_loss_review():
    p = []
    with fresh("t25"):
        db.real_check_put("261019", "KR", [{"item": "① 실계좌라면 샀을까", "status": "✓", "note": "그림자 2건"},
                                           {"item": "② 매도 뒤 재투자", "status": "★"}])
        db.real_check_put("261019", "KR", [{"item": "② 매도 뒤 재투자", "status": "✓", "note": "채움"}])
        rows = {r["item"]: (r["status"], r["note"]) for r in db.q("SELECT * FROM op_real_check WHERE run_id='261019_kr'")}
        if rows != {"① 실계좌라면 샀을까": ("✓", "그림자 2건"), "② 매도 뒤 재투자": ("✓", "채움")}:
            p.append(f"op_real_check: {rows}")
        db.loss_review_put("2026-W42", [
            {"market": "KR", "ticker": "005930", "thesis_id": "th-005930", "verdict": "일시적", "why": "논지 시계 안", "net_realized": -12000},
            {"market": "US", "ticker": "XOM", "verdict": "오판", "basis": "8-K", "net_realized": -35.5}])
        r = db.q1("SELECT * FROM op_loss_review WHERE ticker='005930'")
        if (r["market"], r["basis"], r["verdict"], r["net_realized"]) != ("kr", "논지 시계 안", "일시적", -12000):
            p.append(f"op_loss_review(why → basis): {r}")
        if type(db.q1("SELECT net_realized FROM op_loss_review WHERE ticker='XOM'")["net_realized"]) is not float:
            p.append("순실현 실수 타입")
        row = {"market": "KR", "ticker": "005930", "thesis_id": "th-005930", "verdict": "관찰", "basis": "다음 주", "net_realized": -12000}
        db.loss_review_put("2026-W42", [row])
        r = db.q1("SELECT * FROM op_loss_review WHERE thesis_id='th-005930'")
        if _n("op_loss_review") != 2 or r["verdict"] != "관찰" or json.loads(r["body"]) != row:
            p.append(f"같은 (주·논지)는 바꾼다: {r}")
        # 같은 주 한 종목에 논지가 둘이면 행도 둘(합쳐지면 오판 비율이 틀어진다)
        db.loss_review_put("2026-W42", [{"market": "KR", "ticker": "005930", "thesis_id": "th-005930-b", "verdict": "오판"}])
        if _n("op_loss_review") != 3:
            p.append("같은 종목의 두 논지가 한 행으로 합쳐졌다")
        if db.q1("SELECT thesis_id FROM op_loss_review WHERE ticker='XOM'")["thesis_id"] != "us:XOM":
            p.append("논지 id 없는 행은 market:ticker로 채운다")
        if len(db.loss_review_rows("2026-W42")) != 3:
            p.append("loss_review_rows")
    report("(25) 실전 점검·손실 판정 — (run·항목)·(주·논지) 키로 덮어쓰기 · 한 종목 두 논지는 두 행 · why→basis · 시장 소문자", p)


def t26_audit_mirror():
    p = []
    with fresh("t26") as d:
        lines = [
            json.dumps({"ts": "2026-10-16 10:00:00", "skill": "fact-check", "cp": "track1", "run": "AI클레임_261016_kr_주문근거_판정.md",
                        "verdict": "PASS", "lenses": ["L1", "L2"], "round": 1}, ensure_ascii=False),
            json.dumps({"ts": "2026-10-16 11:00:00", "skill": "trade-run", "cp": "note", "deliverable": "analysis/분석노트_kr_261016.md",
                        "status": "FAIL", "lenses": "L3", "batch": "A"}, ensure_ascii=False),
            "not json {",
            "",
            json.dumps({"ts": "2026-10-16 23:00:00", "skill": "trade-run", "run": "x_261016_us", "verdict": "PASS"}),
            json.dumps({"ts": "2026-10-15 10:00:00", "skill": "trade-run", "run": "x_261015_kr", "verdict": "PASS"}),
        ]
        led = d / "promotion_ledger.jsonl"
        led.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raw = led.read_bytes()
        n = db.audit_mirror(ST, "KR", ledger=led)
        rows = db.q("SELECT * FROM op_audit ORDER BY id")
        if n != 2 or len(rows) != 2:
            p.append(f"복사 건수 {n}·{len(rows)}(이 run 줄 2개여야)")
        else:
            if [(r["run_id"], r["skill"], r["status"], r["lenses"]) for r in rows] != [
                    ("261016_kr", "fact-check", "PASS", "L1,L2"), ("261016_kr", "trade-run", "FAIL", "L3")]:
                p.append(f"op_audit 칸: {[(r['skill'], r['status'], r['lenses']) for r in rows]}")
            if rows[0]["body"] != lines[0] or rows[0]["line_sha"] != hashlib.sha256(lines[0].encode("utf-8")).hexdigest():
                p.append("body·line_sha")
            if rows[1]["deliverable"] != "analysis/분석노트_kr_261016.md":
                p.append("deliverable")
        if db.audit_mirror(ST, "KR", ledger=led) != 0 or _n("op_audit") != 2:
            p.append("재실행이 멱등이 아니다")
        if led.read_bytes() != raw:
            p.append("엔진 원장을 고쳤다")
        if db.audit_mirror(ST, "KR", ledger=d / "없음.jsonl") != 0:
            p.append("없는 원장")
        if db.audit_mirror("261016", "US", ledger=led) != 1:
            p.append("미국 run 줄")
        try:
            db.connect().execute("UPDATE op_audit SET status='PASS'")
            p.append("op_audit UPDATE가 통과했다")
        except sqlite3.DatabaseError:
            pass
    report("(26) 감사 복사 — 이 run 줄만(run·deliverable) · 깨진 줄 건너뜀 · 줄 해시로 멱등 · 엔진 원장 무변경", p)


def t27_backup_restore():
    p = []
    with fresh("t27") as d:
        saved = db.BACKUP_DIR
        db.BACKUP_DIR = d / "bk"                         # 실제 journal/_db_backup에 쓰지 않는다
        try:
            _seed_facts(1, 2)
            t = _thesis()
            db.thesis_put(copy.deepcopy(t))
            db.peaks_update("KR", [{"ticker": "005930", "price": 72000}], now="t1")
            b1 = db.backup("before switch 261019")
            b2 = db.backup("before switch 261019")
            if not b1.exists() or b1.name != "trading_before_switch_261019.db" or b2.name != "trading_before_switch_261019_2.db":
                p.append(f"백업 파일 이름: {b1.name} {b2.name}")
            if d not in b1.parents:
                p.append(f"백업이 스크래치 밖: {b1}")
            db.thesis_set_status(t["id"], "held", note="체결")
            db.thesis_mark_fired(t["id"], "e1", note="체결")
            db.peaks_update("KR", [{"ticker": "005930", "price": 80000}], now="t2")
            db.log_append("incidents", {"ts": "2026-10-19T09:31:00+09:00", "what": "전환 실패"})
            out = db.restore_tables(b1, ["jd_thesis", "jd_thesis_leg", "jd_thesis_event", "jd_thesis_fact", "ex_position_peak"])
            if out != {"jd_thesis": 1, "jd_thesis_leg": 6, "jd_thesis_event": 2, "jd_thesis_fact": 2, "ex_position_peak": 1}:
                p.append(f"되돌린 행 수: {out}")
            p += _same(db.thesis_get(t["id"]), t, "되돌린 논지")
            if db.peaks_for_market("KR") != {"005930": {"peak_price": 72000.0, "peak_at": "t1"}}:
                p.append("고점이 안 돌아왔다")
            if _n("op_incident") != 1:
                p.append("되돌리지 않을 표까지 바뀌었다")
            if db.connect().execute("PRAGMA foreign_key_check").fetchall():
                p.append("되돌린 뒤 외래키 위반")
            db.restore_tables(b1, ["ex_position_peak"])  # 두 번째 복원 — 붙였던 백업이 떼어졌다
        finally:
            db.BACKUP_DIR = saved
    report("(27) 백업·복원 — 같은 이름은 _2 · 표 몇 개만 되돌림(자식 표 포함) · 다른 표 무변경 · 외래키 무위반", p)


def t27b_restore_child_before_parent():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: restore_tables에 자식 표를 부모보다 먼저 주면 부모 DELETE의 ON DELETE CASCADE가 방금 넣은 자식 행을 지운다
    p = []
    with fresh("t27b") as d:
        saved = db.BACKUP_DIR
        db.BACKUP_DIR = d / "bk"
        try:
            _seed_facts(1, 2)
            t = _thesis()
            db.thesis_put(copy.deepcopy(t))
            b = db.backup("x")
            db.thesis_set_status(t["id"], "held")
            db.restore_tables(b, ["jd_thesis_leg", "jd_thesis_event", "jd_thesis_fact", "jd_thesis"])
            if _n("jd_thesis_leg") != 6 or _n("jd_thesis_event") != 2:
                p.append(f"자식 행이 사라졌다: 다리 {_n('jd_thesis_leg')} · 이력 {_n('jd_thesis_event')}")
            got = db.thesis_get(t["id"])
            if got is None or got.get("entry_triggers") != t["entry_triggers"]:
                p.append("되돌린 논지의 다리가 비었다")
        finally:
            db.BACKUP_DIR = saved
    report("(27b) 복원 — 표 순서(자식 먼저)와 무관하게 자식 행이 돌아온다", p)


def t27c_backup_follows_aitb_db():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: backup()은 AITB_DB와 무관하게 BACKUP_DIR(=실제 journal/_db_backup)에 쓴다(export_path와 같은 돌림이 없다)
    p = []
    with fresh("t27c") as d:
        saved = db.BACKUP_DIR
        live_like = d / "live_journal" / "_db_backup"   # 실제 journal/_db_backup 자리를 스크래치 안 대역으로 바꿔 둔다
        db.BACKUP_DIR = live_like
        try:
            out = db.backup("probe")
            if live_like in out.parents:
                p.append(f"AITB_DB 실행인데 BACKUP_DIR(실제 journal 자리)에 썼다: {out.relative_to(d)}")
            elif Path(os.environ["AITB_DB"]).parent not in out.parents:
                p.append(f"백업 위치: {out}")
        finally:
            db.BACKUP_DIR = saved
    report("(27c) 백업 — AITB_DB를 지정한 실행은 그 저장소 옆에 백업한다(실제 journal에 안 씀)", p)


def t27d_restore_refreshes_exports():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: restore_tables가 렌더본 표시(mark_export)를 안 해, 계좌 전환 실패 복원 뒤 theses.json이 전환된(틀린) 상태로 남는다
    p = []
    with fresh("t27d") as d:
        saved = db.BACKUP_DIR
        db.BACKUP_DIR = d / "bk"
        try:
            _seed_facts(1, 2)
            t = _thesis()
            db.thesis_put(copy.deepcopy(t))
            db.watchlist_save(_watchlist())
            b = db.backup("before_switch")
            db.thesis_set_status(t["id"], "held", note="전환")
            db.company_put({"market": "KR", "ticker": "373220", "name": "LG에너지솔루션"})
            db.restore_tables(b, ["jd_thesis", "jd_thesis_leg", "jd_thesis_event", "jd_thesis_fact", "dim_company", "dim_company_list"])
            res = db.doctor(verbose=False)["exports"]
            bad = {n: v for n, v in res.items() if n in ("theses", "watchlist") and v != "같음"}
            if bad:
                p.append(f"복원 뒤 렌더본이 원장과 다르다: {bad}")
            shown = json.loads(db.export_path("theses").read_text(encoding="utf-8"))["theses"][0]["status"]
            if shown != t["status"]:
                p.append(f"theses.json이 복원 전 상태({shown})를 보여 준다")
        finally:
            db.BACKUP_DIR = saved
    report("(27d) 복원 — 되돌린 표의 렌더본(theses·watchlist)도 따라온다", p)


def t29_export_after_commit():
    p = []
    with fresh("t29") as d:
        calls = []
        real_export = db.export

        def counting(name, dest=None):
            calls.append(name)
            return real_export(name, dest)
        db.export = counting
        try:
            _seed_facts(1, 2)
            if calls != ["sources", "sources"]:
                p.append(f"트랜잭션 밖 사실 쓰기는 쓰기마다 바로 내보내야 한다: {calls}")
            out = db.export_path("theses")
            calls.clear()
            db.thesis_put(_thesis("A"))
            if calls != ["theses"] or "\"A\"" not in out.read_text(encoding="utf-8"):
                p.append(f"논지 쓰기 뒤 렌더본: {calls}")
            # 바깥 트랜잭션 안의 여러 쓰기 → 커밋 뒤 한 번
            calls.clear()
            before = out.read_bytes()
            with db.tx():
                db.thesis_put(_thesis("B"))
                db.thesis_update("A", lambda t: t.update(status="held"))
                db.thesis_set_status("B", "held")
                if calls or out.read_bytes() != before:
                    p.append(f"커밋 전에 내보냈다(롤백되면 거짓 렌더): {calls}")
            if calls != ["theses"]:
                p.append(f"커밋 뒤 정확히 한 번이어야: {calls}")
            if out.read_text(encoding="utf-8") != _fmt_text("theses", db.render("theses")):
                p.append("커밋 뒤 렌더본이 원장과 다르다")
            # 롤백 → 렌더본 불변 · 대기 표시도 버린다
            calls.clear()
            before = {n: db.export_path(n).read_bytes() for n in ("theses", "sources")}
            try:
                with db.tx():
                    db.thesis_put(_thesis("C"))
                    db.facts_store.put(_fact(3))
                    db.market_map_save({"schema_version": "1.0", "axes": [_axis()], "sectors": []})
                    raise RuntimeError("중간 실패")
            except RuntimeError:
                pass
            if calls:
                p.append(f"롤백인데 내보냈다: {calls}")
            if {n: db.export_path(n).read_bytes() for n in ("theses", "sources")} != before:
                p.append("롤백인데 렌더본이 바뀌었다")
            if db.export_path("market_map").exists():
                p.append("롤백된 지도 렌더본이 생겼다")
            if db.thesis_get("C") is not None or db.facts_store.get("SRC-0003") is not None:
                p.append("롤백된 행이 남았다")
            if db._PENDING_EXPORTS:
                p.append(f"롤백 뒤 대기 표시가 남았다: {db._PENDING_EXPORTS}")
            db.coverage_put("x", {"n": 1})              # 다음 쓰기가 롤백된 표시를 끌고 나오지 않는다
            if calls != ["universe_coverage"]:
                p.append(f"롤백 뒤 첫 쓰기의 내보내기: {calls}")
            # 쓰는 자리마다 표시 — 바깥 트랜잭션 하나에 여러 쓰기 → 렌더본마다 한 번
            calls.clear()
            with db.tx():
                db.market_map_save({"schema_version": "1.0", "axes": [_axis()], "sectors": [_sector()]})
                db.watchlist_save(_watchlist())
                db.company_put({"market": "KR", "ticker": "373220", "name": "LG에너지솔루션"})
                db.facts_store.put(_fact(3))
                db.equity_append(_equity_row())
                db.coverage_save({"a": {"n": 1}})
                db.kv_put("op_coverage", "b", {"n": 2})
                db.log_append("feedback", {"id": "FB-1", "ts": "2026-10-17", "status": "open"})
                db.log_upsert("feedback", {"id": "FB-1", "ts": "2026-10-18", "status": "done"})
                db.log_append("sessions", {"ts": "2026-10-17", "market": "KR"})       # 렌더본 없는 로그
                db.scenario_put(_scenario())                                           # 시나리오는 EXPORTS 밖
                if calls:
                    p.append(f"트랜잭션 안에서 내보냈다: {calls}")
            want = ["equity_curve", "feedback", "market_map", "sources", "universe_coverage", "watchlist"]
            if sorted(calls) != want:
                p.append(f"커밋 뒤 내보내기(이름마다 한 번): {sorted(calls)}")
            res = db.doctor(verbose=False)["exports"]
            if set(res.values()) != {"같음"}:
                p.append(f"커밋 뒤 렌더본이 원장과 다르다: {res}")
            calls.clear()
            db.log_append("feedback", {"id": "FB-2", "ts": "2026-10-18"})
            db.theses.delete("B")
            if calls != ["feedback", "theses"]:
                p.append(f"트랜잭션 밖 로그·삭제의 내보내기: {calls}")
            # 렌더본 쓰기가 실패해도 원장 쓰기는 커밋된다(doctor가 '다름'으로 잡는다)

            def broken(name, dest=None):
                calls.append(name)
                raise OSError("디스크 가득(시험)")
            db.export = broken
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                db.thesis_put(_thesis("E"))
            if db.thesis_get("E") is None or "theses" not in err.getvalue():
                p.append(f"렌더본 실패가 원장 쓰기를 되돌렸거나 알리지 않았다: {err.getvalue()!r}")
        finally:
            db.export = real_export
        if d not in db.export_path("theses").parents:
            p.append("렌더본 경로가 스크래치 밖")
    report("(29) 렌더본 표시 — 트랜잭션 밖은 바로 · 안은 바깥 커밋 뒤 렌더본마다 한 번 · 롤백이면 안 씀 · 실패해도 원장은 커밋", p)


def t30_doc_history():
    p = []
    with fresh("t30"):
        _seed_facts(1, 2)
        t = _thesis("A")
        db.thesis_put(copy.deepcopy(t))
        if db.theses.history("A") != []:
            p.append("처음 넣은 행에 이력이 생겼다")
        db.thesis_update("A", lambda x: x.update(status="held"))
        db.thesis_set_status("A", "closed", note="청산", ts="2026-10-16T15:00:00+09:00")
        h = db.theses.history("A")
        if [x["rev"] for x in h] != [1, 2]:
            p.append(f"이력 판: {[x['rev'] for x in h]}")
        else:
            p += _same(h[0]["doc"], t, "판 1 모양(타입·키 순서)")
            if h[1]["doc"]["status"] != "held":
                p.append("판 2가 직전 모양이 아니다")
        last = db.thesis_get("A")
        db.theses.delete("A")
        h = db.theses.history("A")
        if not h or h[-1]["rev"] != 3 or not str(h[-1]["at"]).endswith("삭제"):
            p.append(f"삭제 직전 판: {h[-1] if h else None}")
        else:
            p += _same(h[-1]["doc"], last, "삭제 직전 모양")
        # 복합 키(종목)
        db.companies_store.put({"market": "KR", "ticker": "005930", "name": "삼성"})
        db.companies_store.put({"market": "KR", "ticker": "005930", "name": "삼성전자"})
        hc = db.companies_store.history(("KR", "005930"))
        if [x["doc"] for x in hc] != [{"market": "KR", "ticker": "005930", "name": "삼성"}]:
            p.append(f"복합 키 이력: {hc}")
        # 판 충돌(StaleWrite)이면 이력도 남지 않는다
        db.thesis_put(_thesis("B"))
        try:
            db.theses.put(_thesis("B"), expect_rev=99)
        except db.StaleWrite:
            pass
        if db.theses.history("B"):
            p.append("거부된 쓰기의 이력이 남았다")
        # 원장마다 나뉜다(같은 id라도)
        db.scenario_put(_scenario("B"))
        db.scenario_put(dict(_scenario("B"), status="closed"))
        if len(db.scenarios.history("B")) != 1 or db.theses.history("B"):
            p.append("원장 이름으로 나뉘지 않았다")
        try:
            db.connect().execute("UPDATE sys_doc_hist SET body='{}'")
            p.append("sys_doc_hist UPDATE가 통과했다")
        except sqlite3.DatabaseError as e:
            if "추가만" not in str(e):
                p.append(f"거부 문구: {e}")
    report("(30) 행 이력 — 갱신·삭제 직전 판을 sys_doc_hist에(첫 기록·거부된 쓰기는 없음) · 복합 키 · 원장별 · 추가만", p)


def t30b_watchlist_save_keeps_history():
    # 회귀 방지(2026-10-17 db.py 수정) — 고치기 전 결함: watchlist_save는 DocStore.put을 거치지 않아(_put_company_row) 감시 목록 행의 직전 판이 sys_doc_hist에 안 남는다
    #   (t21d의 재저장 IndexError가 먼저 고쳐져야 이 검사까지 간다)
    p = []
    with fresh("t30b"):
        wl = _watchlist()
        db.watchlist_save(copy.deepcopy(wl))
        wl["KR"][0]["axis"] = "HBM"
        db.watchlist_save(wl)
        h = db.companies_store.history(("KR", "005930"))
        if len(h) != 1 or h[0]["doc"].get("axis") != "메모리 반도체":
            p.append(f"감시 목록 저장으로 바뀐 종목 행의 직전 판이 없다: {h}")
    report("(30b) 감시 목록 저장도 바뀐 종목 행의 직전 판을 남긴다", p)


def _bridge():
    """config/sector_bridge.json 모양 — 메타 키가 목록 사이사이에 있다."""
    return {"_comment": "미국 섹터 ↔ 국내 업종 대응표", "_why": "짝을 고정한다", "schema_version": "1.0", "_note_us": "ETF로 잰다",
            "us_sectors": {"XLK": {"name": "기술", "excd": "AMS", "gics": True}, "SMH": {"name": "반도체", "excd": "NAS", "gics": False}},
            "_note_kr": "코드가 진실",
            "links": [{"us": "SMH", "kr": "0013", "kr_name": "전기·전자", "sign": "+", "confidence": "high", "note": "가장 강한 고리"},
                      {"us": "XLK", "kr": "0029", "kr_name": "IT 서비스", "sign": "+", "confidence": "mid", "note": "소프트웨어"},
                      {"us": "XLF", "kr": "0021", "kr_name": "금융", "sign": "+", "confidence": "low", "note": "금리"}],
            "_note_retired": "3회 역행으로 내린 짝",
            "retired_links": [{"us": "XLC", "kr": "0020", "kr_name": "통신", "sign": "+", "confidence": "low", "retired": "2026-09-28",
                               "by": "us:260928 carry", "why": "3회 연속 역행", "note": "요금 규제"}],
            "_note_gaps": "대응이 없다는 것 자체가 신호",
            "kr_without_us": [{"kr": "0026", "kr_name": "일반서비스", "why": "대응 범주 없음"}],
            "us_without_kr": [{"us": "XLE", "why": "순수 에너지 업종 없음"}, "XLRE"]}


def t31_sector_bridge():
    p = []
    with fresh("t31"):
        doc = _bridge()
        n = db.sector_bridge_save(copy.deepcopy(doc))
        if n != 7:
            p.append(f"저장 행 수 {n}")
        p += _same(db.sector_bridge_doc(), doc, "섹터 짝 왕복")
        if json.dumps(db.sector_bridge_doc(), ensure_ascii=False, indent=2) + "\n" != json.dumps(doc, ensure_ascii=False, indent=2) + "\n":
            p.append("옛 파일 텍스트와 다르다")
        live = [(r["kr_sector"], r["us_sector"], r["sign"], r["confidence"], r["why"]) for r in db.sector_links()]
        if live != [("0013", "SMH", "+", "high", "가장 강한 고리"), ("0029", "XLK", "+", "mid", "소프트웨어"), ("0021", "XLF", "+", "low", "금리")]:
            p.append(f"sector_links(live): {live}")
        if [(r["kr_sector"], r["us_sector"]) for r in db.sector_links("retired")] != [("0020", "XLC")]:
            p.append("sector_links(retired)")
        if len(db.sector_links("gap")) != 3:
            p.append("한쪽만 있는 짝(gap) 행 수")
        ok = db.sector_link_retire("XLK", "0029", why="3회 역행(10-14·10-15·10-16)", by="kr:261016 carry", day="2026-10-16")
        d2 = db.sector_bridge_doc()
        if not ok or [x["us"] for x in d2["links"]] != ["SMH", "XLF"]:
            p.append(f"내린 짝이 links에 남았다: {[x['us'] for x in d2['links']]}")
        want = dict(doc["links"][1], retired="2026-10-16", by="kr:261016 carry", why="3회 역행(10-14·10-15·10-16)")
        p += _same(d2["retired_links"][-1], want, "retired_links 끝")
        r = db.q1("SELECT status, retired_why FROM dim_sector_link WHERE us_sector='XLK'")
        if (r["status"], r["retired_why"]) != ("retired", "3회 역행(10-14·10-15·10-16)"):
            p.append(f"내린 행 컬럼: {r}")
        if [x["us_sector"] for x in db.sector_links()] != ["SMH", "XLF"]:
            p.append("판정용 live 목록에 내린 짝이 남았다")
        if db.sector_link_retire("XLK", "0029", why="x", by="x", day="x"):
            p.append("이미 내린 짝을 또 내렸다")
        p += _same({k: v for k, v in d2.items() if not k.endswith("links")}, {k: v for k, v in doc.items() if not k.endswith("links")},
                   "메타·gap 무변경")
        db.sector_bridge_save(copy.deepcopy(doc))         # 통째 다시 저장하면 그대로 맞춘다
        p += _same(db.sector_bridge_doc(), doc, "통째 재저장")
    report("(31) 섹터 짝 — sector_bridge_save/doc 왕복(메타 키 순서·문자열 원소) · sector_links(live·retired·gap) · sector_link_retire", p)


def t28_doctor():
    p = []
    with fresh("t28"):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = db.doctor()
        if res["integrity"] != "ok" or res["fk_violations"]:
            p.append(f"빈 저장소 integrity·FK: {res['integrity']} {res['fk_violations']}")
        if set(res["exports"].values()) != {"파일 없음"} or res["ok"] or "DOCTOR FAIL" not in buf.getvalue():
            p.append(f"내보내기 전 판정: {res['exports']} ok={res['ok']}")
        nz = {t: n for t, n in res["counts"].items() if n}
        if {k: v for k, v in nz.items() if k != "mk_holiday"} != {"sys_meta": 2, "dim_market": 2} or not nz.get("mk_holiday"):
            p.append(f"빈 저장소 행 수(판·휴장일 파일 시각 · 휴장일 사본): {nz}")
        db.export_all()
        res = db.doctor(verbose=False)
        if not res["ok"] or set(res["exports"].values()) != {"같음"}:
            p.append(f"내보낸 뒤 판정: {res['exports']}")
        _seed_facts(1, 2)
        db.thesis_put(_thesis())                         # 원장 쓰기는 렌더본을 스스로 따라 쓴다
        if not db.doctor(verbose=False)["ok"]:
            p.append("db 함수로 쓴 뒤 렌더본이 따라오지 않았다")
        db.connect().execute("UPDATE jd_thesis SET status='x'")         # db 함수를 거치지 않은 쓰기
        res = db.doctor(verbose=False)
        if res["exports"]["theses"] != "다름" or res["ok"]:
            p.append("렌더본과 어긋난 원장을 '다름'으로 못 잡았다")
        db.export("theses")
        if not db.doctor(verbose=False)["ok"]:
            p.append("다시 내보낸 뒤에도 FAIL")
        db.init_schema()                                 # 스키마는 멱등
        if _n("sys_meta") != 2 or _n("dim_market") != 2:
            p.append("스키마 재실행이 기준 행을 늘렸다")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = (db._cli(["doctor"]), db._cli(["sql", "DELETE FROM ex_run"]), db._cli(["show", "없는표"]),
                  db._cli(["sql", "SELECT COUNT(*) AS n FROM jd_thesis"]))
        if rc != (0, 2, 2, 0):
            p.append(f"CLI 종료코드: {rc}")
    report("(28) doctor — 빈 저장소 integrity·FK·행 수 · 렌더본 없음/같음/다름 · 스키마 멱등 · CLI(sql은 SELECT만)", p)


def t32_import_allocation_basis():
    """배분 판단의 외부 사실도 주문 근거 — 시그널 allocation의 regime_refs(S 번호·사실 id)와 facts가 jd_allocation_fact로 간다.
    도구 관측('tools:…')은 사실 행이 아니라 건너뛴다 · 못 찾은 S 번호는 missing_facts · 다시 옮기면 같은 결과(멱등)."""
    p = []
    with fresh("t32") as d:
        _seed_facts(1, 2, 3)
        rid = f"{ST}_{MK}"
        db.run_start(ST, MK)
        db.connect().execute("INSERT INTO ev_fact_run (fact_id, run_id, s_no) VALUES ('SRC-0002', ?, 28)", (rid,))
        root = d / "art"
        sig, _ap, _st, _tr = _artifacts(root)
        sig["allocation"] = {"based_on": "AL-261015-kr", "target_invested_pct": 98, "regime_refs": ["tools:국면재료", "S28", "S99"],
                             "facts": [{"id": "SRC-0003", "role": "support", "support": "현금 명분 숫자"}]}
        sig["holds"][0]["sources"] = ["S28", "data/run_evidence/levels_261016_kr.md"]   # 보유 판단 근거 — S 번호는 사실, 파일은 아님
        (root / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
        out = db.import_run_artifacts(ST, MK, here=root)
        rows = sorted((r["allocation_id"], r["fact_id"], r["role"]) for r in db.q("SELECT * FROM jd_allocation_fact"))
        want = sorted([(f"AL-{ST}-{MK.lower()}", "SRC-0002", "support"), (f"AL-{ST}-{MK.lower()}", "SRC-0003", "support")])
        if rows != want:
            p.append(f"jd_allocation_fact: {rows}")
        if "S99" not in (out.get("missing_facts") or []):
            p.append(f"못 찾은 S 번호가 missing_facts에 없다: {out.get('missing_facts')}")
        hf = [(r["ticker"], r["fact_id"]) for r in db.q("SELECT * FROM ex_hold_fact")]
        if hf != [("010950", "SRC-0002")]:
            p.append(f"보유 판단 sources(S 번호) → ex_hold_fact: {hf}")
        sig["allocation"]["regime_refs"].append("SRC-0001")
        (root / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
        db.import_run_artifacts(ST, MK, here=root)
        if _n("jd_allocation_fact") != 3:
            p.append(f"시그널이 바뀌면 다시 맞춘다(3행): {_n('jd_allocation_fact')}")
        import facts as _facts
        ob = _facts.order_basis(rid)
        if not any("ALLOC" in x for x in ob.get("SRC-0003", [])):
            p.append(f"facts.order_basis가 배분 근거를 못 본다: {ob}")
    report("(32) 배분 근거(regime_refs S 번호·사실 id·facts)·보유 판단 sources → 근거 표 · 도구·파일 참조는 건너뜀 · 멱등 · order_basis", p)


def t33_import_factor_judgments():
    """(33) 요인 판정(FB-261017-7) — 시그널 factor_judgments[]가 jd_factor_judgment·jd_factor_decision으로 간다 · 머리는 driver·
    state(intact|broken|unknown)·cause 필수 · 결정은 ticker·decision(저점매수|유지|정리) 필수 — 틀린 행은 factor_skipped · 다시 옮기면 같다."""
    p = []
    with fresh("t33") as d:
        _seed_facts(1, 2, 3)
        root = d / "art"
        sig, _ap, _st, _tr = _artifacts(root)
        sig["factor_judgments"] = [
            {"driver": "ai_capex", "scenario_id": "SC-261016-3", "fired_by": ["drawdown", "sells"], "cause": "고객사 투자 하향 [S3]",
             "state": "broken", "sources": ["S3"], "note_extra": "모르는 키는 extra",
             "decisions": [{"ticker": "010950", "held": True, "decision": "정리", "why": "동인 꺾임", "sources": ["S3"]},
                           {"ticker": "000660", "held": False, "decision": "저점매수", "why": "일시 수급"},
                           {"ticker": "005930", "decision": "관망", "why": "셋 밖 결정"}]},
            {"driver": "rates_up", "state": "꺾임", "cause": "x"},
            {"driver": "", "state": "intact", "cause": "x"}]
        (root / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
        out = db.import_run_artifacts(ST, MK, here=root)
        rid = f"{ST}_{MK}"
        fj = db.factor_judgments(rid)
        if out.get("factor_judgments") != 1 or len(out.get("factor_skipped") or []) != 3:
            p.append(f"수입 수·건너뜀: {out.get('factor_judgments')} {out.get('factor_skipped')}")
        if [(j["driver"], j["state"], j["fired_by"]) for j in fj] != [("ai_capex", "broken", ["drawdown", "sells"])]:
            p.append(f"판정 머리: {[(j['driver'], j['state'], j['fired_by']) for j in fj]}")
        elif sorted((x["ticker"], x["decision"], x["held"]) for x in fj[0]["decisions"]) != [("000660", "저점매수", 0), ("010950", "정리", 1)]:
            p.append(f"결정: {fj[0]['decisions']}")
        elif (fj[0].get("extra") or {}).get("note_extra") != "모르는 키는 extra":
            p.append(f"모르는 키 extra: {fj[0].get('extra')}")
        sig["generated_at"] = "2026-10-16T10:00:01+09:00"                 # 파일이 바뀌어야 다시 옮긴다
        (root / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
        db.import_run_artifacts(ST, MK, here=root)
        if _n("jd_factor_judgment") != 1 or _n("jd_factor_decision") != 2:
            p.append(f"다시 옮기면 같다(1·2): {_n('jd_factor_judgment')} {_n('jd_factor_decision')}")
        sig["factor_judgments"] = []
        sig["generated_at"] = "2026-10-16T10:00:02+09:00"
        (root / "signals" / f"signal_{ST}_{MK}.json").write_text(json.dumps(sig, ensure_ascii=False, indent=1), encoding="utf-8")
        db.import_run_artifacts(ST, MK, here=root)
        if _n("jd_factor_judgment") or _n("jd_factor_decision"):
            p.append("시그널에서 판정이 빠지면 표에서도 빠져야 한다")
    report("(33) 요인 판정 → jd_factor_judgment·decision · 필수 칸 · 틀린 행 factor_skipped · 멱등 · 빠지면 지움", p)


def t34_screen_rows():
    """(34) 규율 검사 회차 기억(ex_screen · 2026-10-19) — 두 회차를 넣으면 회차·기록 순으로 읽히고 canon·ctx가 dict로 돌아온다 ·
    run마다 따로 · run 행(ex_run)이 없어도 넣을 수 있다(첫 회차 전)."""
    p = []
    with fresh("t34"):
        rows1 = [{"run_id": "261019_kr", "pass_no": 1, "mode": "main", "ticker": "086790", "action": "BUY", "fingerprint": "aa",
                  "canon": {"facts": [["F-000093", "support"]], "ticker": "086790"}, "ctx": {"target": 141367.0, "factor": []},
                  "outcome": "rejected", "why_class": "validity", "why": "근거에 MISMATCH 포함 — F-000093", "limits_sha": "s1",
                  "approved_path": "approved_261019_kr.json"},
                 {"run_id": "261019_kr", "pass_no": 1, "mode": "main", "ticker": "005830", "action": "BUY", "fingerprint": "bb",
                  "canon": {"ticker": "005830"}, "ctx": {}, "outcome": "accepted", "limits_sha": "s1"}]
        db.screen_put(rows1)
        db.screen_put([{"run_id": "261019_kr", "pass_no": 2, "ticker": "086790", "action": "BUY", "outcome": "rejected",
                        "why_class": "taint", "why": "거부 회피 재실행 — …", "limits_sha": "s1"}])
        db.screen_put([{"run_id": "261019_us", "pass_no": 1, "ticker": "MSFT", "action": "BUY", "outcome": "accepted"}])
        got = db.screen_rows("261019_kr")
        if [(r["pass_no"], r["ticker"], r["outcome"]) for r in got] != [(1, "086790", "rejected"), (1, "005830", "accepted"),
                                                                         (2, "086790", "rejected")]:
            p.append(f"순서·내용: {[(r['pass_no'], r['ticker'], r['outcome']) for r in got]}")
        if got and (got[0]["canon"] != rows1[0]["canon"] or got[0]["ctx"] != rows1[0]["ctx"]):
            p.append(f"canon·ctx 복원: {got[0]['canon']} {got[0]['ctx']}")
        if len(db.screen_rows("261019_us")) != 1 or db.screen_rows("261020_kr"):
            p.append("run마다 따로 읽히지 않는다")
        if _n("ex_run"):
            p.append("ex_screen이 run 행을 만들었다(또는 요구했다)")
    report("(34) 회차 기억 ex_screen — 회차·기록 순 · canon·ctx 복원 · run별 · run 행 없이도 기록", p)


def t35_theses_under_review():
    """(35) 근거가 틀린 논지(2026-10-19) — 근거 사실이 MISMATCH로 판정 불일치(disputed)가 되면 재검토 목록에 오른다 · 반대(against)
    근거와 닫힌 논지는 아니다 · 표시를 지우면(thesis_review_clear) 근거 행·머리 표시가 0 · 사실 값을 교정하면(facts.correct) 목록에서 빠지고
    그 사실을 가리킨 표시도 풀린다."""
    import facts as _facts
    p = []
    with fresh("t35"):
        _seed_facts(1, 2, 3)
        t1 = _thesis("kr-a-261019")
        t1["evidence"] = [{"claim": "a", "fact_id": "SRC-0001", "role": "support"}, {"claim": "b", "fact_id": "SRC-0002", "role": "against"}]
        t2 = _thesis("kr-b-261019")
        t2["evidence"] = [{"claim": "c", "fact_id": "SRC-0002", "role": "support"}]
        t3 = _thesis("kr-c-261019")
        t3["status"], t3["evidence"] = "closed", [{"claim": "d", "fact_id": "SRC-0001", "role": "support"}]
        db.theses.put(t1)
        db.theses.put(t2)
        db.theses.put(t3)
        for fid in ("SRC-0001", "SRC-0002"):
            db.connect().execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, note, fact_rev) VALUES (?, '261019_kr', 'MISMATCH', '원문 값과 다르다', 1)", (fid,))
            db.connect().execute("UPDATE ev_fact SET verified_status='disputed' WHERE id=?", (fid,))
        got = {r["id"]: [f["fact_id"] for f in r["facts"]] for r in db.theses_under_review()}
        if got != {"kr-a-261019": ["SRC-0001"], "kr-b-261019": ["SRC-0002"]}:
            p.append(f"재검토 목록(반대 근거·닫힌 논지 제외): {got}")
        if "kr-c-261019" not in {r["id"] for r in db.theses_under_review(include_closed=True)}:
            p.append("include_closed가 닫힌 논지를 안 본다")
        flagged = db.q1("SELECT needs_review FROM jd_thesis WHERE id='kr-b-261019'")["needs_review"]
        db.thesis_review_clear("kr-b-261019")
        if not flagged or db.q1("SELECT needs_review FROM jd_thesis WHERE id='kr-b-261019'")["needs_review"] \
                or _n("jd_thesis_fact", "pid=(SELECT rid FROM jd_thesis WHERE id='kr-b-261019') AND needs_review=1"):
            p.append(f"표시 지우기: 전 {flagged}")
        with contextlib.redirect_stdout(io.StringIO()):
            _facts.correct("SRC-0001", {"as_of": "2026-10-19"}, "원문 다시 열람 — 기준일 정정")
        got2 = {r["id"] for r in db.theses_under_review()}
        if "kr-a-261019" in got2 or _n("jd_thesis_fact", "fact_id='SRC-0001' AND needs_review=1"):
            p.append(f"교정 뒤에도 재검토로 남았다: {got2}")
    report("(35) 근거가 틀린 논지 — disputed 근거만(반대·닫힘 제외) · 표시 지우기 · 교정하면 풀린다", p)


# ───────────────────────────── 2026-10-19 Codex R12 — 저장소 판 · 이전 · 복원 ─────────────────────────────

def t36_schema_version_drift():
    """(36) 저장소 판·스키마 차이 — 새 저장소 판 = 코드 판(= 스키마 파일 숫자) · 차이 0 · 파일 밖 칸을 더하면 doctor가 잡는다."""
    p = []
    with fresh("t36"):
        lit = re.search(r"VALUES \('schema_version', '(\d+)'\)", db.SCHEMA_PATH.read_text(encoding="utf-8"))
        if not lit or int(lit.group(1)) != db.SCHEMA_VERSION:
            p.append(f"스키마 파일 판 {lit and lit.group(1)} ≠ 코드 판 {db.SCHEMA_VERSION}")
        res = db.doctor(verbose=False)
        if res["schema_version"] != db.SCHEMA_VERSION or res["schema_drift"]:
            p.append(f"새 저장소: 판 {res['schema_version']} · 차이 {res['schema_drift'][:3]}")
        db.connect().execute("ALTER TABLE ex_run ADD COLUMN stray_col TEXT")
        res = db.doctor(verbose=False)
        if not any("ex_run.stray_col" in x for x in res["schema_drift"]) or res["ok"]:
            p.append(f"파일 밖 칸을 못 잡았다: {res['schema_drift'][:3]} ok={res['ok']}")
    report("(36) 저장소 판·스키마 차이 — 새 저장소 판 = 코드 판 = 파일 숫자 · 차이 0 · 파일 밖 칸은 doctor가 잡는다", p)


def _v1_db(path):
    """옛 판(v1) 저장소 — 판 1 스키마(testdata/db_schema_v1.sql)로 만들고 사실 둘·판정·교정·판 기록을 심는다(시각은 고정)."""
    c = sqlite3.connect(str(path))
    c.executescript((HERE / "testdata" / "db_schema_v1.sql").read_text(encoding="utf-8"))
    for fid, rid in (("F-000001", 1), ("F-000002", 2)):
        c.execute("INSERT INTO ev_fact (rid, id, kind, rev, verified_status, needs_review) VALUES (?,?,'earnings',1,'none',0)", (rid, fid))
        c.execute("INSERT INTO co_earnings (fact_id, ticker, period, metric, actual) VALUES (?,?,?,?,?)", (fid, "BAC", "2026Q3", "NII", 16.4))
    ins = ("INSERT INTO ev_verdict (id, fact_id, run_id, batch, round, verdict, tier, at) VALUES (?,?,?,?,?,?,?,?)")
    c.execute(ins, (1, "F-000001", "261001_us", "track1", 1, "MATCH", 1, "2026-10-01T10:00:00+09:00"))       # 교정 전
    c.execute(ins, (2, "F-000001", "261001_us", "A", 1, "MISMATCH", 1, "2026-10-01T11:00:00+09:00"))         # 교정과 같은 초
    c.execute(ins, (3, "F-000001", "261001_us", "track1", 2, "MATCH", 1, "2026-10-01T12:00:00+09:00"))       # 교정 뒤
    c.execute(ins, (4, "F-000002", "261001_us", "track1", 1, "MATCH", 1, "2026-10-01T10:00:00+09:00"))
    # F-000001 값 교정(판 1→2) · F-000002 표기 교정(판 1→2) · '이미 반영됨' 연결 행(판 안 올림)
    c.execute("INSERT INTO ev_fact_version (fact_id, version, snapshot, changed_at) VALUES ('F-000001', 1, '{}', '2026-10-01T02:00:00')")
    c.execute("INSERT INTO ev_correction (fact_id, old, new, reason, verdict_id, applied_at) VALUES "
              "('F-000001', '{\"actual\": 16.4}', '{\"actual\": 16.5}', '원문 값', 2, '2026-10-01T11:00:00+09:00')")
    c.execute("INSERT INTO ev_correction (fact_id, old, new, reason, verdict_id, applied_at) VALUES "
              "('F-000001', '{\"actual\": 16.5}', '{\"actual\": 16.5}', '이미 반영됨 · 다른 묶음', 2, '2026-10-01T11:30:00+09:00')")
    c.execute("INSERT INTO ev_fact_version (fact_id, version, snapshot, changed_at) VALUES ('F-000002', 1, '{}', '2026-10-01T03:00:00')")
    c.execute("INSERT INTO ev_correction (fact_id, old, new, reason, applied_at) VALUES "
              "('F-000002', '{\"source_label\": null}', '{\"source_label\": \"8-K\"}', '출처 이름', '2026-10-01T12:00:00+09:00')")
    c.execute("UPDATE ev_fact SET rev=2")                       # 판 기록이 이미 있어 트리거는 건너뛴다
    c.execute("UPDATE co_earnings SET actual=16.5 WHERE fact_id='F-000001'")
    c.execute("UPDATE ev_fact SET needs_review=0")              # MISMATCH 트리거가 세운 표시 — 교정이 풀었다
    c.execute("INSERT INTO ev_claim_ref (fact_id, run_id, doc_type, doc_id, slot, at) VALUES "
              "('F-000001', '261001_us', 'claims', 'track1', '1', '2026-10-01T09:00:00+09:00')")
    c.commit()
    c.close()


def t37_migrate_v1_to_v2():
    import facts
    """(37) 이전 v1→v2(R04) — 옛 저장소는 connect가 거부 · dry-run은 판 그대로 · migrate가 판정 판(교정 전 1 · 같은 초 MISMATCH 1 ·
    교정 뒤 2)과 값 판(값 교정 2 · 표기 교정 1)을 근거로 채움 · 행 수 유지 · 트리거·뷰 재생성(복사 중 재검토 표시 안 섬) · 차이 0 ·
    다시 돌리면 할 것 없음 · 백업으로 되돌리면 판 1."""
    p = []
    with fresh("t37") as d:
        db.reset()
        _v1_db(db.db_path())
        try:
            db.connect()
            p.append("옛 판 저장소를 connect가 열었다")
        except db.SchemaOutdated as e:
            if "db.py migrate" not in str(e):
                p.append(f"거부 문구: {e}")
        db.reset()
        with contextlib.redirect_stdout(io.StringIO()):
            rc = db._cli(["migrate", "--dry-run"])
        with db._gate_open():
            v_after_dry = db.schema_version()
        if rc != 0 or v_after_dry != 1:
            p.append(f"dry-run: rc={rc} 판 {v_after_dry}")
        db.reset()
        rep = db.migrate()
        bk = Path(rep["backup"] or "")
        if rep["to"] != db.SCHEMA_VERSION or not bk.exists() or rep["drift"]:
            p.append(f"migrate: {rep.get('to')} 백업 {bk.name} 차이 {rep.get('drift')}")
        fr = {r["id"]: r["fact_rev"] for r in db.q("SELECT id, fact_rev FROM ev_verdict ORDER BY id")}
        if fr != {1: 1, 2: 1, 3: 2, 4: 1}:
            p.append(f"판정 판(교정 전 1 · 같은 초 MISMATCH 1 · 교정 뒤 2 · 표기 교정 사실 1): {fr}")
        vr = {r["id"]: (r["rev"], r["value_rev"], r["needs_review"]) for r in db.q("SELECT id, rev, value_rev, needs_review FROM ev_fact")}
        if vr != {"F-000001": (2, 2, 0), "F-000002": (2, 1, 0)}:
            p.append(f"값 판(값 교정 2 · 표기 교정 1) · 복사 중 재검토 표시 없음: {vr}")
        if (db.q1("SELECT fact_rev FROM ev_claim_ref") or {}).get("fact_rev") != 1:
            p.append("클레임 참조 판")
        objs = {r["name"] for r in db.q("SELECT name FROM sqlite_master WHERE type IN ('trigger','view')")}
        if not {"tg_verdict_review", "tg_verdict_noupd", "tg_verdict_nodel", "v_verify_queue", "v_tax"} <= objs:
            p.append(f"트리거·뷰 재생성: {sorted(objs)[:8]}")
        g = facts.grade_verdicts(db.connect(), "261001_us", None, "track1")
        if g["F-000001"]["verdict"] != "MATCH" or g["F-000002"]["verdict"] != "MATCH":
            p.append(f"마지막 round 등급(교정 뒤 MATCH 셈 · 표기 교정은 그대로): {g}")
        if db.migrate()["steps"]:
            p.append("다시 돌렸는데 할 것이 있다")
        db.reset()
        r = db.restore(bk)
        db.reset()
        try:
            db.connect()
            p.append(f"복원 뒤 판 {r['schema_version']} 저장소를 connect가 열었다")
        except db.SchemaOutdated:
            pass
        if r["schema_version"] != 1:
            p.append(f"복원 뒤 판: {r}")
    report("(37) 이전 v1→v2 — connect 거부 · dry-run 판 그대로 · 판정 판·값 판을 근거로 · 행 수 · 트리거·뷰 · 멱등 · 백업으로 복원", p)


def t38_evidence_store():
    """(38) 원문 보관(v3) — norm_url(facts와 한 규칙) · 같은 내용 두 번 = 한 판(n_fetch 2) · 다른 내용 = 새 판 · 실패·빈 본문은 판 없는 시도 ·
    파일 이름 = 내용 해시 · 보관 폴더 = 임시 저장소 옆 · fetch_rows 거름 · link_body(옛 행: 받은 본문 / 실패 사유)."""
    import facts
    p = []
    with fresh("t38"):
        u = "https://WWW.Example.com/a/?utm_source=x&id=7#frag"
        if db.norm_url(u) != "https://example.com/a?id=7" or facts._norm_url(u) != db.norm_url(u):
            p.append(f"norm_url: {db.norm_url(u)}")
        r1 = db.evidence_record(u, "본문 하나", "", {"kind": "html"})
        r2 = db.evidence_record("https://example.com/a?id=7", "본문 하나", "", {"kind": "html"})
        r3 = db.evidence_record(u, "본문 둘", "", {"kind": "html"})
        r4 = db.evidence_record(u, "", "HTTP 403", {})
        r5 = db.evidence_record(u, "", "", {})
        if not (r1["version_id"] == r2["version_id"] != r3["version_id"]) or r4["version_id"] or r5["status"] != "empty":
            p.append(f"판: {r1['version_id']} {r2['version_id']} {r3['version_id']} · {r4} · {r5['status']}")
        v = db.version_get(r1["version_id"])
        if v["n_fetch"] != 2 or not v["path"].endswith(r1["content_sha"][:16] + ".txt") or not db.raw_abs(v["path"]).is_file() \
                or db.HERE in db.raw_abs(v["path"]).parents:
            p.append(f"판 행·파일: {v}")
        if len(db.fetch_rows()) != 5 or len(db.fetch_rows(url="https://example.com/a?id=7")) != 5 or db.fetch_rows(kind="pdf"):
            p.append(f"시도 행: {len(db.fetch_rows())}")
        c = db.connect()
        c.execute("INSERT INTO mat_link (id, url, fetched, body) VALUES (1, 'u1', 1, '옛 본문'), (2, 'u2', 0, 'HTTP 503')")
        if db.link_body(1) != ("옛 본문", "") or db.link_body(2) != (None, "HTTP 503"):
            p.append(f"link_body 옛 행: {db.link_body(1)} {db.link_body(2)}")
        try:
            c.execute("UPDATE ev_fetch SET status='ok'")
            p.append("ev_fetch UPDATE가 통과했다(추가만)")
        except sqlite3.DatabaseError:
            pass
    report("(38) 원문 보관 — norm_url 한 규칙 · 같은 내용 한 판 · 새 내용 새 판 · 실패·빈 본문은 시도만 · 내용 해시 이름 · 임시 보관 폴더 · link_body", p)


def t39_migrate_v3_evidence():
    """(39) 이전 v3 — 옛 접속 기록(op_fetch) 한 행 = 시도 한 행 · 덮어쓴 파일은 마지막으로 쓴 시도에만 판(앞 시도는 판 없음) · 없는 파일은
    판 없음 · 사실 → 판은 전부 inferred(인용 일치 판 우선) · verified_rev · 옛 링크 본문 → 판·이력 · 실패 사유 → 실패 시도 · 차이 0 · 멱등."""
    p = []
    with fresh("t39"):
        db.reset()
        _v1_db(db.db_path())
        rd = db.raw_dir()
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "aaa111.txt").write_text("둘째 조회 본문 — 3분기 순이자이익은 16.5B 달러로 집계됐다", encoding="utf-8")
        c = sqlite3.connect(str(db.db_path()))
        ops = [(1, "https://ir.example.com/a", "2026-10-16T09:40:00+09:00", 100, "", "data/_raw_fc/aaa111.txt"),
               (2, "https://ir.example.com/a/", "2026-10-16T09:50:00+09:00", 120, "", "data/_raw_fc/aaa111.txt"),
               (3, "https://blocked.example.com/b", "2026-10-16T09:55:00+09:00", 0, "HTTP 403", ""),
               (4, "https://ir.example.com/c", "2026-10-16T10:00:00+09:00", 90, "", "data/_raw_fc/ccc333.txt")]
        for seq, url, at, chars, err, rp in ops:
            c.execute("INSERT INTO op_fetch (seq, ts, body) VALUES (?,?,?)",
                      (seq, at, json.dumps({"at": at, "url": url, "kind": "html", "chars": chars, "err": err, "raw_path": rp})))
        c.execute("INSERT INTO ev_source (id, url, raw_path) VALUES (10, 'https://ir.example.com/a', 'data/_raw_fc/aaa111.txt')")
        for fid, rid, q in (("F-000003", 3, "3분기 순이자이익은 16.5B 달러로 집계됐다"), ("F-000004", 4, "이 문장은 보관 원문에 없는 인용입니다")):
            c.execute("INSERT INTO ev_fact (rid, id, kind, rev, verified_status, needs_review, source_id, url, quote)"
                      " VALUES (?,?,'earnings',1,'none',0,10,'https://ir.example.com/a',?)", (rid, fid, q))
        c.execute("UPDATE ev_fact SET verified_status='verified' WHERE id IN ('F-000001','F-000002')")
        c.execute("INSERT INTO mat_issue (id, run_id, day, received_at, content_sha) VALUES (1, '261001_kr', '2026-10-01',"
                  " '2026-10-01T08:00:00+09:00', 'sha-i1')")
        c.execute("INSERT INTO mat_link (id, issue_id, url, fetched, body, run_id) VALUES (1, 1, 'https://news.example.com/x', 1,"
                  " '링크 원문 — HBM 비중 61.5%', '261001_kr'), (2, 1, 'https://news.example.com/y', 0, 'HTTP 503', '261001_kr')")
        c.commit()
        c.close()
        rep = db.migrate()
        st3 = next((x for x in rep["steps"] if x["to"] == 3), {})
        if rep["to"] != db.SCHEMA_VERSION or rep["drift"] or (st3.get("rows_overwritten"), st3.get("files_missing"),
                                                               st3.get("versions_from_files")) != (1, 1, 1):
            p.append(f"이전 보고: to {rep['to']} 차이 {rep['drift']} · {st3}")
        fx = {r["op_fetch_seq"]: r for r in db.q("SELECT op_fetch_seq, version_id, status, error FROM ev_fetch WHERE op_fetch_seq IS NOT NULL")}
        if fx[1]["version_id"] is not None or fx[2]["version_id"] is None or fx[3]["status"] != "error" or fx[4]["version_id"] is not None:
            p.append(f"시도 → 판(덮어쓴 앞 시도·없는 파일은 판 없음): {fx}")
        va = db.version_get(fx[2]["version_id"])
        if va["first_seen"] != "2026-10-16T09:50:00+09:00" or va["provenance"] != "migrated" or va["path"] != "data/_raw_fc/aaa111.txt":
            p.append(f"판 A: {va}")
        fl = {r["id"]: (r["source_version_id"], r["source_version_link"]) for r in
              db.q("SELECT id, source_version_id, source_version_link FROM ev_fact WHERE id IN ('F-000003','F-000004')")}
        if fl != {"F-000003": (va["id"], "inferred"), "F-000004": (va["id"], "inferred")} or st3.get("facts_quote_absent_ids") != ["F-000004"]:
            p.append(f"사실 → 판(전부 inferred · 인용 없는 사실 보고): {fl} · {st3.get('facts_quote_absent_ids')}")
        vr = {r["id"]: r["verified_rev"] for r in db.q("SELECT id, verified_rev FROM ev_fact WHERE verified_status='verified'")}
        if vr != {"F-000001": 2, "F-000002": 1}:
            p.append(f"verified_rev(MATCH 판정의 가장 높은 판): {vr}")
        if db.link_body(1) != ("링크 원문 — HBM 비중 61.5%", "") or db.link_body(2) != (None, "HTTP 503") \
                or db.q1("SELECT COUNT(*) AS n FROM mat_link_version")["n"] != 1 \
                or db.q1("SELECT body FROM mat_link WHERE id=1")["body"] != "링크 원문 — HBM 비중 61.5%":
            p.append(f"옛 링크: {db.link_body(1)} {db.link_body(2)}")
        if db.migrate()["steps"]:
            p.append("다시 돌렸는데 할 것이 있다")
    report("(39) 이전 v3 — 시도 1:1 · 덮어쓴 파일은 마지막 시도에만 판 · 없는 파일 판 없음 · 사실 판 전부 inferred · verified_rev · 옛 링크 판·실패 · 멱등", p)


def t40_stage_rows():
    """(40) 단계 결과(v4 · U02) — 시도 번호 · 명령 행 · 산출물 해시 · 마지막 성공은 실패·진행 중을 건너뜀 · <단계>_at = 성공한 첫 시도의 끝(다시
    성공해도 안 움직임) · execute는 시작 시각 · 선택 실패 → [제한·1단] 줄 · 파일이 바뀌면 산출물 대조 실패."""
    p = []
    with fresh("t40") as d:
        f = d / "snap.json"
        f.write_text("{}", encoding="utf-8")
        s1 = db.stage_start(ST, "kr", "capture")
        db.stage_finish(s1, "failed", 2, [{"name": "재료 수집", "required": True, "rc": 2, "status": "failed"}], None, "ingest 실패")
        if db.stage_last_ok(db.run_id(ST, "kr"), "capture") or (db.run_get(ST, "kr") or {}).get("capture_at"):
            p.append("실패한 시도가 성공으로 잡혔다")
        s2 = db.stage_start(ST, "kr", "capture")
        steps = [{"name": "재료 수집", "required": True, "rc": 0, "status": "ok"},
                 {"name": "실계좌 점검", "required": False, "rc": 1, "status": "reduced", "note": "종료코드 1"}]
        db.stage_finish(s2, db.stage_status(steps), 0, steps, {"snapshot": f})
        r = db.run_get(ST, "kr")
        last = db.stage_last_ok(db.run_id(ST, "kr"), "capture")
        if not last or last["attempt"] != 2 or last["status"] != "reduced" or "snapshot" not in last["artifacts"] or not r["capture_at"]:
            p.append(f"두 번째 시도: {last}")
        cap0 = r["capture_at"]
        s3 = db.stage_start(ST, "kr", "capture")
        db.stage_finish(s3, "ok", 0, [], {"snapshot": f})
        if db.run_get(ST, "kr")["capture_at"] != cap0:
            p.append("성공한 첫 시도의 끝이 움직였다")
        if db.stage_limitations(db.run_id(ST, "kr")):
            p.append("마지막 성공 시도에 제한이 없는데 제한 줄이 나왔다")
        s4 = db.stage_start(ST, "kr", "carry")
        db.stage_finish(s4, "reduced", 0, steps[1:], None)
        if db.stage_limitations(db.run_id(ST, "kr")) != ["[제한·2단] 실계좌 점검 — 종료코드 1"]:
            p.append(f"제한 줄: {db.stage_limitations(db.run_id(ST, 'kr'))}")
        if not db.stage_artifact_matches(db.run_id(ST, "kr"), "capture", "snapshot", f):
            p.append("같은 파일인데 산출물 대조 실패")
        f.write_text('{"x": 1}', encoding="utf-8")
        if db.stage_artifact_matches(db.run_id(ST, "kr"), "capture", "snapshot", f):
            p.append("바뀐 파일을 같은 산출물로 봤다")
        s5 = db.stage_start(ST, "kr", "execute")
        started = db.q1("SELECT started_at FROM ex_stage WHERE id=?", (s5,))["started_at"]
        db.stage_finish(s5, "ok", 0)
        if db.run_get(ST, "kr")["sent_at"] != started:
            p.append("sent_at이 전송 단계 시작 시각이 아니다")
        try:
            db.stage_start(ST, "kr", "lunch")
            p.append("모르는 단계 이름을 받았다")
        except ValueError:
            pass
    report("(40) 단계 결과 — 시도 번호 · 실패는 성공 아님 · <단계>_at = 성공한 첫 시도 끝(불변) · execute = 시작 · 제한 줄 · 산출물 해시 대조", p)


def t41_audit_scope():
    """(41) 리뷰 재현 foreign_rehearsal_audit_imported의 올바른 동작 — 같은 이름의 감사 두 줄: 다른 프로젝트 사본의 판정 파일 → foreign ·
    이 프로젝트 → project · 경로 없음 → unknown(센다) · run 시작보다 이른 줄 → foreign · audit_rounds·실전 점검 ④는 foreign을 안 센다 ·
    범위 판정은 추가만 · 다시 판정하면 바뀐 행만 덧붙인다."""
    import facts
    import real_check
    p = []
    with fresh("t41") as d:
        other = d / "rehearsal" / "ai-trading-bot"
        (other / "analysis").mkdir(parents=True)
        (other / "stage.py").write_text("", encoding="utf-8")
        (other / "db_schema.sql").write_text("", encoding="utf-8")
        st, mk = "261019", "kr"
        db.run_start(st, mk)
        db.connect().execute("UPDATE ex_run SET started='2026-10-19T09:30:00+09:00' WHERE run_id=?", (db.run_id(st, mk),))
        series = f"AI클레임_{st}_{mk}_주문근거_판정.md"
        rows = [{"type": "finding", "skill": "fact-check", "cp": "verdict", "run": series, "status": "FAIL", "ts": "2026-10-19 10:00:00",
                 "findings_file": str(other / "analysis" / "findings.md"), "lenses": ["a"]},
                {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": series, "status": "PASS", "ts": "2026-10-19 11:00:00",
                 "findings_file": str(HERE / "analysis" / "findings.md")},
                {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": series, "status": "PASS", "ts": "2026-10-19 11:30:00"},
                {"type": "finding", "skill": "fact-check", "cp": "verdict", "run": series, "status": "FAIL", "ts": "2026-10-17 12:19:17"}]
        led = d / "ledger.jsonl"
        led.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        n = db.audit_mirror(st, mk, led)
        sc = [r["scope"] for r in db.q("SELECT scope FROM v_audit ORDER BY id")]
        if n != 4 or sc != ["foreign", "project", "unknown", "foreign"]:
            p.append(f"범위: {n}행 · {sc}")
        os.environ["AITB_STEPGATE_LEDGER"] = str(led)
        try:
            rounds = facts.audit_rounds(st, mk)
            c4 = real_check.c4_audit(st, mk)
        finally:
            os.environ.pop("AITB_STEPGATE_LEDGER", None)
        if [x[1] for x in rounds] != ["PASS", "PASS"] or "① 2회 · 마지막 PASS" not in c4[1]:
            p.append(f"foreign 제외 집계: {rounds} · {c4}")
        if db.audit_mirror(st, mk, led) != 0:
            p.append("같은 줄을 다시 넣었다")
        try:
            db.connect().execute("UPDATE op_audit_scope SET scope='project'")
            p.append("op_audit_scope UPDATE가 통과했다(추가만)")
        except sqlite3.DatabaseError:
            pass
        if db.audit_rescope(db.run_id(st, mk)):
            p.append("규칙이 그대로인데 다시 판정이 행을 덧붙였다")
        a0 = db.q1("SELECT reqid, deliverable_path FROM op_audit ORDER BY id LIMIT 1")
        if a0 is None:
            p.append("정체 칸 행 없음")
    report("(41) 감사 범위 — 다른 사본 foreign · 이 프로젝트 project · 경로 없음 unknown · 이른 줄 foreign · 집계는 foreign 제외 · 추가만(리뷰 재현 U03)", p)


def t42_run_mode_fixed():
    """(42) 리뷰 재현 account_run_collision의 올바른 동작 — 모의로 시작한 run을 같은 날 실계좌로 다시 시작하면 RunModeConflict · 행 1개 ·
    모의 그대로 · 같은 계열 라벨 변화(real_readonly → real)는 받는다 · 직접 UPDATE도 트리거가 막는다 · run_close는 closed_at을 채운다."""
    p = []
    with fresh("t42"):
        db.run_start("261019", "kr", account_mode="paper")
        try:
            db.run_start("261019", "kr", account_mode="real")
            p.append("다른 계열로 다시 시작했다")
        except db.RunModeConflict:
            pass
        r = db.q("SELECT account_mode FROM ex_run")
        if r != [{"account_mode": "paper"}]:
            p.append(f"run 행: {r}")
        db.run_start("261020", "us", account_mode="real_readonly")
        db.run_start("261020", "us", account_mode="real")
        if db.run_get("261020", "us")["account_mode"] != "real":
            p.append("같은 계열 라벨 변화를 못 받았다")
        try:
            db.connect().execute("UPDATE ex_run SET account_mode='paper' WHERE run_id='261020_us'")
            p.append("직접 UPDATE로 계열이 바뀌었다")
        except sqlite3.DatabaseError:
            pass
        db.run_close("261019", "kr")
        if not db.run_get("261019", "kr")["closed_at"]:
            p.append("run_close가 closed_at을 안 채웠다")
    report("(42) run 계좌 계열 고정 — 다른 계열 재시작 거부 · 행 1 · 같은 계열 라벨 변화 허용 · 트리거 · closed_at(리뷰 재현 R09)", p)


def t43_migrate_v4():
    """(43) 이전 v4 — ex_run 단계 시각마다 단계 행(unknown·migrated) · run 시각 그대로 · 감사 정체 칸·범위 판정(사본 경로 foreign) ·
    닫힌 run 감사 회차 다시 셈 · 멱등."""
    p = []
    with fresh("t43") as d:
        db.reset()
        _v1_db(db.db_path())
        c = sqlite3.connect(str(db.db_path()))
        c.execute("INSERT INTO ex_run (run_id, stamp, market, started, capture_at, note_at, closed_at, audit_rounds)"
                  " VALUES ('261019_kr','261019','kr','2026-10-19T09:30:00+09:00','2026-10-19T09:35:00+09:00',"
                  "'2026-10-19T11:00:00+09:00','2026-10-19T13:00:00+09:00', 2)")
        series = "AI클레임_261019_kr_주문근거_판정.md"
        for i, (st_, ts, ff) in enumerate((("FAIL", "2026-10-17 12:19:17", "/private/tmp/x/rehearsal/ai-trading-bot/analysis/f.md"),
                                           ("PASS", "2026-10-19 12:06:20", None))):
            body = {"type": "finding", "skill": "fact-check", "run": series, "status": st_, "ts": ts, "reqid": f"r{i}"}
            if ff:
                body["findings_file"] = ff
            c.execute("INSERT INTO op_audit (run_id, skill, status, at, line_sha, body, findings_file) VALUES (?,?,?,?,?,?,?)",
                      ("261019_kr", "fact-check", st_, ts, f"sha{i}", json.dumps(body, ensure_ascii=False), ff))
        c.commit()
        c.close()
        rep = db.migrate()
        st4 = next((x for x in rep["steps"] if x["to"] == 4), {})
        stg = {(r["stage"], r["status"], r["provenance"]) for r in db.q("SELECT stage, status, provenance FROM ex_stage")}
        if stg != {("capture", "unknown", "migrated"), ("note", "unknown", "migrated"), ("close", "unknown", "migrated")}:
            p.append(f"단계 행: {stg}")
        if st4.get("audit_scope") != {"project": 0, "foreign": 1, "unknown": 1} or db.q1("SELECT reqid FROM op_audit WHERE id=2")["reqid"] != "r1":
            p.append(f"감사 범위·정체 칸: {st4.get('audit_scope')}")
        if db.run_get("261019", "kr")["audit_rounds"] != 1 or st4.get("audit_rounds_changed") != [{"run": "261019_kr", "old": 2, "new": 1}]:
            p.append(f"감사 회차 다시 셈: {db.run_get('261019', 'kr')['audit_rounds']} · {st4.get('audit_rounds_changed')}")
        if rep["drift"] or db.migrate()["steps"]:
            p.append(f"차이·멱등: {rep['drift']}")
    report("(43) 이전 v4 — 단계 행(unknown·migrated) · run 시각 불변 · 감사 정체·범위(사본 foreign) · 닫힌 run 감사 회차 · 멱등", p)


def t44_migrate_v5():
    """(44) 이전 v5 — mk_bar 한 행 = 관측 한 번(받은 시각) · 확정은 그 시각 > 그 세션 마감으로 계산(장중에 받은 국내 행 미확정 · 마감 뒤 받은
    미국 행 확정) · 관측 이력 한 행씩 · 미국 거래소 = 성공한 od 키의 거래소 · final_through(빈 결과 NULL) · 휴장일 사본 · 일정 confirmed=1 · 멱등."""
    p = []
    with fresh("t44"):
        db.reset()
        _v1_db(db.db_path())
        c = sqlite3.connect(str(db.db_path()))
        rows = [("KR:069500", "2026-10-19", 86780, "2026-10-19T09:35:52+09:00"),
                ("KR:069500", "2026-10-16", 86000, "2026-10-17T12:50:00+09:00"),
                ("US:OWL", "2026-10-16", 15.2, "2026-10-17T12:50:00+09:00")]
        for subj, d, cl, fa in rows:
            c.execute("INSERT INTO mk_bar (subject, date, close, source, fetched_at) VALUES (?,?,?,'KIS',?)", (subj, d, cl, fa))
        c.execute("INSERT INTO mk_bar_fetch (subject, k, fetched_at, n) VALUES ('US:OWL','od:NAS:','2026-10-17T12:49:00+09:00',0),"
                  " ('US:OWL','od:NYS:','2026-10-17T12:50:00+09:00',100), ('KR:069500','dd:20260901:20261019','2026-10-19T09:35:52+09:00',35)")
        c.execute("INSERT INTO cal_event (date, event, key) VALUES ('2026-10-21','테스트 실적','x_er_261021')")
        c.commit()
        c.close()
        rep = db.migrate()
        st5 = next((x for x in rep["steps"] if x["to"] == 5), {})
        fin = {(r["subject"], r["date"]): (r["is_final"], r["exchange"]) for r in db.q("SELECT subject, date, is_final, exchange FROM mk_bar")}
        want = {("KR:069500", "2026-10-19"): (0, "KRX"), ("KR:069500", "2026-10-16"): (1, "KRX"), ("US:OWL", "2026-10-16"): (1, "NYS")}
        if fin != want or st5.get("bars_nonfinal") != 1:
            p.append(f"확정·거래소: {fin} · {st5.get('bars_nonfinal')}")
        ft = {r["k"]: r["final_through"] for r in db.q("SELECT k, final_through FROM mk_bar_fetch")}
        if ft != {"od:NAS:": None, "od:NYS:": "2026-10-16", "dd:20260901:20261019": "2026-10-16"}:
            p.append(f"final_through: {ft}")
        if db.q1("SELECT COUNT(*) AS n FROM mk_bar_obs")["n"] != 3 or not db.q1("SELECT COUNT(*) AS n FROM mk_holiday")["n"] \
                or db.q1("SELECT confirmed FROM cal_event")["confirmed"] != 1:
            p.append("관측 이력·휴장일·일정 confirmed")
        if rep["drift"] or db.migrate()["steps"]:
            p.append(f"차이·멱등: {rep['drift']}")
    report("(44) 이전 v5 — 관측 시각으로 확정 계산(장중 국내 미확정 · 마감 뒤 미국 확정) · 이력 · 미국 거래소 · final_through · 휴장일 · 일정 · 멱등", p)


def t45_migrate_v6():
    """(45) 이전 v6 — 기존 시나리오는 전부 text_only(조건을 지어내지 않는다) · '영향 없음' 대응 noop · 비어 있던 축·대응은 출처 태그가 가리키는
    시그널 행에서만, 값이 하나일 때만 채운다(축이 갈리면 needs_review) · 실현된 행마다 발생 1(첫 실현 판정 시각) · 완료 표시는 그 발생의 완료 행 · 멱등."""
    p = []
    with fresh("t45") as d:
        old_root = os.environ.get("AITB_PROJECT_ROOT")
        os.environ["AITB_PROJECT_ROOT"] = str(d)
        try:
            (d / "signals").mkdir()
            (d / "signals" / "signal_261016_kr.json").write_text(json.dumps({"scenarios": [
                {"id": "S1", "condition": "c1", "axis_id": "ai_memory", "response": "KR 추가"},
                {"id": "S2", "condition": "c2", "axis_id": "rates_up"},
                {"id": "S3", "condition": "c3", "axis_id": "power_grid", "response": "KR 유지"}]}, ensure_ascii=False), encoding="utf-8")
            (d / "signals" / "signal_261017_kr.json").write_text(json.dumps({"scenarios": [
                {"id": "S2", "condition": "c2", "axis_id": "banks"}]}, ensure_ascii=False), encoding="utf-8")
            db.reset()
            _v1_db(db.db_path())
            c = sqlite3.connect(str(db.db_path()))
            rows = ((1, "SC-1", 1, "closed", "c1", ["signal_261016_kr.json#S1"]),
                    (2, "SC-2", None, "open", "c2", ["signal_261016_kr.json#S2", "signal_261017_kr.json#S2"]),
                    (3, "SC-3", 0, "open", "c3", ["signal_261016_kr.json#S3"]))
            for rid, sid, realized, st_, cond, srcs in rows:
                ko = ["id", "market", "opened", "status", "realized", "condition", "axis", "sources"] + (["judgments", "actions"] if rid == 1 else [])
                c.execute("INSERT INTO jd_scenario (rid, id, market, opened, status, realized, condition, axis, ord, rev, ko)"
                          " VALUES (?,?,?,?,?,?,?,'',?,1,?)", (rid, sid, "kr", "261016", st_, realized, cond, rid, json.dumps(ko)))
                for i, t in enumerate(srcs):
                    c.execute("INSERT INTO jd_scenario_list (pid, src, ord, value) VALUES (?,?,?,?)", (rid, "sources", i, t))
            kj, ka = json.dumps(["when", "realized", "acted", "note"]), json.dumps(["market", "ticker", "what", "done", "done_at"])
            c.execute("INSERT INTO jd_scenario_judgment (pid, src, ord, when_, realized, acted, note, ko) VALUES (1,'judgments',0,'2026-10-16 22:10',1,1,'실현',?)", (kj,))
            c.execute("INSERT INTO jd_scenario_response (pid, src, ord, market, ticker, what, done, done_at, ko) VALUES"
                      " (1,'actions',0,'kr','','추가 매수',1,'2026-10-17 09:40',?), (1,'actions',1,'us','','영향 없음 — 무관',0,NULL,?)", (ka, ka))
            c.commit()
            c.close()
            rep = db.migrate()
            st6 = next((x for x in rep["steps"] if x["to"] == 6), {})
            sc = {r["id"]: r for r in db.q("SELECT id, cond_status, axis, response, needs_review FROM jd_scenario")}
            if {r["cond_status"] for r in sc.values()} != {"text_only"}:
                p.append(f"cond_status: {[(k, r['cond_status']) for k, r in sc.items()]}")
            if (sc["SC-1"]["axis"], sc["SC-1"]["response"], sc["SC-3"]["axis"], sc["SC-3"]["response"]) != ("ai_memory", "KR 추가", "power_grid", "KR 유지"):
                p.append(f"출처 시그널 행에서 축·대응: {sc['SC-1']} {sc['SC-3']}")
            if sc["SC-2"]["axis"] or sc["SC-2"]["needs_review"] != 1 or st6.get("axis_conflicts") != ["SC-2.axis"]:
                p.append(f"축이 갈린 행 — 비워 두고 재검토: {sc['SC-2']} {st6.get('axis_conflicts')}")
            if db.q1("SELECT noop FROM jd_scenario_response WHERE what LIKE '영향 없음%'")["noop"] != 1:
                p.append("영향 없음 대응 noop")
            eps = db.q("SELECT id, scenario_id, n, realized_at FROM jd_scenario_episode")
            done = db.q("SELECT episode_id, market, done_at FROM jd_scenario_response_done")
            if [(e["scenario_id"], e["n"], e["realized_at"]) for e in eps] != [("SC-1", 1, "2026-10-16T22:10:00+09:00")]:
                p.append(f"발생 1(첫 실현 판정 시각): {eps}")
            elif [(x["episode_id"], x["market"], x["done_at"]) for x in done] != [(eps[0]["id"], "kr", "2026-10-17 09:40")]:
                p.append(f"완료 행: {done}")
            if (st6.get("text_only"), st6.get("noop"), st6.get("episodes"), st6.get("responses_done")) != (3, 1, 1, 1):
                p.append(f"보고: {st6}")
            j = [x["note"] for x in db.q("SELECT note FROM jd_scenario_judgment WHERE pid=(SELECT rid FROM jd_scenario WHERE id='SC-3')")]
            if not any("(migration v6)" in str(x) for x in j):
                p.append(f"채운 사실이 판정 행에 남지 않았다: {j}")
            if rep["drift"] or db.migrate()["steps"]:
                p.append(f"차이·멱등: {rep['drift']}")
        finally:
            if old_root is None:
                os.environ.pop("AITB_PROJECT_ROOT", None)
            else:
                os.environ["AITB_PROJECT_ROOT"] = old_root
    report("(45) 이전 v6 — 전부 text_only · noop · 축·대응은 출처 시그널 행에서만(갈리면 재검토) · 실현 행 발생 1 · 완료 행 · 멱등", p)


def t46_order_approval_link():
    """(46) ← orders_without_approval(Codex R10) — 주문 행에 승인 연결이 없었다(가져오기가 approval_id를 안 넣음 · 같은 이름 승인 파일 두 판).
    올바른 동작: 집행기가 적은 승인 해시+순번으로 recorded · 같은 이름의 뒤 판이 들어와도 보낸 판을 가리킨다 · 승인 파일이 덮여 저장소에 그 판이
    없으면 그날 기록 runs[] 스냅샷으로 승인 행을 되살려 recorded · 해시 없는 옛 행은 유일할 때만 inferred, 애매하면 NULL."""
    p = []
    with fresh("t46") as d:
        _seed_facts(1, 2)
        root = d / "art"
        _artifacts(root)
        apf = root / "signals" / f"approved_{ST}_{MK}.json"
        sha1 = hashlib.sha256(apf.read_bytes()).hexdigest()
        trades = [_trade("2026-10-16T10:10:00+09:00", "005930", "BUY", 3, 69500, "0000011111", _fill(0, 3, 3, 69500),
                         approved_sha256=sha1, approved_idx=0, intent_key=f"{sha1[:16]}:0:1"),
                  _trade("2026-10-16T10:10:05+09:00", "000660", "SELL", 1, 200000, "0000022222", _fill(5, 4, -1, 200000),
                         approved_sha256=sha1, approved_idx=1, intent_key=f"{sha1[:16]}:1:1")]
        tf = root / "journal" / f"trades_{ST}_{MK}.json"
        tf.write_text(json.dumps({"trades": trades, "runs": []}, ensure_ascii=False), encoding="utf-8")
        db.import_run_artifacts(ST, "KR", here=root)
        o = db.q("SELECT o.ticker, o.approval_link, o.intent_key, a.approved_sha, a.idx FROM ex_order o LEFT JOIN ex_approval a ON a.id=o.approval_id ORDER BY o.id")
        if [(x["ticker"], x["approval_link"], x["approved_sha"], x["idx"]) for x in o] != [("005930", "recorded", sha1, 0), ("000660", "recorded", sha1, 1)]:
            p.append(f"recorded 연결: {[(x['ticker'], x['approval_link'], x['idx']) for x in o]}")
        # 같은 이름의 뒤 판(수량을 바꾼 재승인) — 이미 보낸 주문은 보낸 판을 계속 가리킨다
        ap2 = json.loads(apf.read_text(encoding="utf-8"))
        ap2["orders"][0]["qty"] = 5
        apf.write_text(json.dumps(ap2, ensure_ascii=False), encoding="utf-8")
        db.import_run_artifacts(ST, "KR", here=root)
        o2 = db.q("SELECT a.approved_sha FROM ex_order o JOIN ex_approval a ON a.id=o.approval_id ORDER BY o.id")
        if [x["approved_sha"] for x in o2] != [sha1, sha1] or _n("ex_approval") != 7:
            p.append(f"뒤 판이 들어온 뒤 연결: {[x['approved_sha'][:8] for x in o2]} · 승인 행 {_n('ex_approval')}")
    with fresh("t46b") as d:
        _seed_facts(1, 2)
        root = d / "art"
        _artifacts(root)
        apf = root / "signals" / f"approved_{ST}_{MK}.json"
        sha1 = hashlib.sha256(apf.read_bytes()).hexdigest()
        snap = [{"ticker": "005930", "action": "BUY", "source": "llm", "qty": 3, "price": 69500, "idx": 0},
                {"ticker": "000660", "action": "SELL", "source": "llm", "qty": 1, "price": 200000, "idx": 1}]
        apf.write_text(json.dumps({"orders": [{"ticker": "035420", "action": "BUY", "qty": 1, "price": 100}]}), encoding="utf-8")  # 덮였다
        trades = [_trade("2026-10-16T10:10:00+09:00", "005930", "BUY", 3, 69500, "0000011111", _fill(0, 3, 3, 69500),
                         approved_sha256=sha1, approved_idx=0, intent_key=f"{sha1[:16]}:0:1")]
        tf = root / "journal" / f"trades_{ST}_{MK}.json"
        tf.write_text(json.dumps({"trades": trades, "runs": [{"approved_ref": str(apf), "approved_sha256": sha1, "approved_orders": snap}]},
                                 ensure_ascii=False), encoding="utf-8")
        out = db.import_run_artifacts(ST, "KR", here=root)
        r = db.q1("SELECT o.approval_link, a.approved_sha, a.approved_path, a.idx, a.qty FROM ex_order o JOIN ex_approval a ON a.id=o.approval_id")
        if not r or (r["approval_link"], r["approved_sha"], r["idx"], r["qty"]) != ("recorded", sha1, 0, 3) \
                or not str(r["approved_path"]).startswith(f"journal:trades_{ST}_{MK}.json#runs[0]") or out.get("approvals_restored") != 2:
            p.append(f"덮인 승인 파일 — 스냅샷으로 되살린 연결: {dict(r) if r else None} · 되살린 행 {out.get('approvals_restored')}")
    with fresh("t46c") as d:
        _seed_facts(1, 2)
        root = d / "art"
        _artifacts(root, trades=[])
        db.import_run_artifacts(ST, "KR", here=root)                        # 판 1
        apf = root / "signals" / f"approved_{ST}_{MK}.json"
        ap2 = json.loads(apf.read_text(encoding="utf-8"))
        ap2["orders"][0]["price"] = 70000
        apf.write_text(json.dumps(ap2, ensure_ascii=False), encoding="utf-8")
        tf = root / "journal" / f"trades_{ST}_{MK}.json"
        tf.write_text(json.dumps({"trades": [_trade("2026-10-16T10:10:00+09:00", "005930", "BUY", 3, 69500, "0000011111", None),
                                             _trade("2026-10-16T10:10:05+09:00", "000660", "SELL", 1, 200000, "0000022222", None)]},
                                 ensure_ascii=False), encoding="utf-8")
        db.import_run_artifacts(ST, "KR", here=root)                        # 판 2 + 해시 없는 옛 거래 행
        o = {x["ticker"]: (x["approval_id"], x["approval_link"]) for x in db.q("SELECT ticker, approval_id, approval_link FROM ex_order")}
        if o.get("005930") != (None, None) or o.get("000660") != (None, None):
            p.append(f"같은 이름 두 판(판마다 같은 종목·방향 행)에 맞는 옛 행을 지어서 이었다: {o}")
    report("(46) 주문↔승인 연결 — 해시+순번 recorded · 뒤 판이 와도 보낸 판 · 덮인 파일은 runs[] 스냅샷으로 되살림 · 옛 행 애매하면 NULL", p)


def t47_fill_observations():
    """(47) ← cumulative_fill_as_multiple_rows(Codex R11) — 누적 체결 관측 행을 합쳐 PARTIAL 1 → FILLED 2를 3주로 셌다.
    올바른 동작: 관측 이력 2행 · 주문마다 마지막 관측(v_fill_latest) 2주 · v_tax 1행 · 체결 매수 2 · 규칙 매도 1건 · 같은 파일 재가져오기 0행 ·
    매도 수량은 부호 없음 · 결제일 환율은 주문 단위(다음 관측이 와도 v_tax에 남고 fills_find는 다시 내지 않는다)."""
    p = []
    with fresh("t47") as d:
        root = d / "art"
        (root / "signals").mkdir(parents=True)
        (root / "journal").mkdir(parents=True)
        tf = root / "journal" / f"trades_{ST}_us.json"
        ref = f"approved_{ST}_us.json"

        def write(buy_fill, sell_fill):
            tr = [_trade("2026-10-16T23:40:00+09:00", "AAA", "BUY", 2, 10.0, "0000055555", buy_fill, approved_ref=ref, market="US"),
                  _trade("2026-10-16T23:41:00+09:00", "BBB", "SELL", 3, 20.0, "0000066666", sell_fill, approved_ref=ref, market="US",
                         source="discipline", leg_kind="stop")]
            tf.write_text(json.dumps({"trades": tr}, ensure_ascii=False), encoding="utf-8")
        write({"qty_before": 0, "qty_after": 1, "filled_qty": 1, "avg_price": 10.0, "verdict": "PARTIAL"},
              {"qty_before": 3, "qty_after": 2, "delta": -1, "requested": -3, "avg_price": 20.0, "verdict": "PARTIAL"})
        db.import_run_artifacts(ST, "us", here=root)
        f1 = db.q1("SELECT f.id FROM ex_fill f JOIN ex_order o ON o.id=f.order_id WHERE o.ticker='AAA'")["id"]
        db.fill_fx_put(f1, 1400.0, "KIS")
        write({"qty_before": 0, "qty_after": 2, "filled_qty": 2, "avg_price": 10.0, "verdict": "FILLED", "confirmed_at": "2026-10-17T05:00:00+09:00"},
              {"qty_before": 3, "qty_after": 0, "delta": -3, "requested": -3, "avg_price": 20.0, "verdict": "FILLED"})
        db.import_run_artifacts(ST, "us", here=root)
        hist = {t: [r["filled_qty"] for r in db.q("SELECT f.filled_qty FROM ex_fill f JOIN ex_order o ON o.id=f.order_id WHERE o.ticker=? ORDER BY f.id", (t,))]
                for t in ("AAA", "BBB")}
        if hist != {"AAA": [1, 2], "BBB": [1, 3]}:
            p.append(f"관측 이력(부호 없음): {hist}")
        lat = {r["ticker"]: (r["filled_qty"], r["verdict"]) for r in db.q("SELECT ticker, filled_qty, verdict FROM v_fill_latest")}
        if lat != {"AAA": (2, "FILLED"), "BBB": (3, "FILLED")}:
            p.append(f"마지막 관측: {lat}")
        tax = db.q("SELECT ticker, filled_qty, settle_fx, side FROM v_tax ORDER BY ticker")
        if [(r["ticker"], r["filled_qty"], r["settle_fx"]) for r in tax] != [("AAA", 2, 1400.0), ("BBB", 3, None)]:
            p.append(f"v_tax(주문마다 한 행 · 환율 주문 단위): {[(r['ticker'], r['filled_qty'], r['settle_fx']) for r in tax]}")
        if db.q1("SELECT at FROM v_fill_latest WHERE ticker='AAA'")["at"] != "2026-10-17T05:00:00+09:00":
            p.append("관측 시각 = 확정 시각(confirmed_at)")
        import facts
        if facts.filled_buys(ST, "us") != {"AAA": 2}:
            p.append(f"체결 매수(합산 아님): {facts.filled_buys(ST, 'us')}")
        import market_map
        _, rs = market_map._rule_sells("us", ST)
        if [r["ticker"] for r in rs] != ["BBB"]:
            p.append(f"규칙 매도(주문당 한 번): {rs}")
        if db.fills_find(db.run_id(ST, "us"), "AAA", side="BUY") != []:
            p.append("환율이 붙은 주문의 새 관측을 fills_find가 다시 냈다")
        n0 = _n("ex_fill")
        db.import_run_artifacts(ST, "us", here=root)
        if _n("ex_fill") != n0:
            p.append("같은 파일 재가져오기가 관측 행을 늘렸다")
    report("(47) 체결 관측 — 이력 2행 · 마지막 관측 2주 · v_tax 1행(환율 주문 단위) · 체결 매수 2 · 규칙 매도 1 · 부호 없음 · 멱등", p)


def t48_migrate_v7():
    """(48) 이전 v7 — 승인 idx = 파일 안 순번 · 주문의 해시·순번·의도 키는 거래 행 원문에서(같은 의도 키가 둘이면 뒤 행 비움) · recorded /
    덮인 승인은 그날 기록 runs[]로 되살려 recorded / 옛 행 유일 inferred·애매 NULL · 음수 체결은 절댓값 관측 덧붙임 · 연결 칸만 고칠 수 있다 ·
    op_cash_flow(추가만) · 멱등."""
    p = []
    with fresh("t48") as d:
        old_root = os.environ.get("AITB_PROJECT_ROOT")
        os.environ["AITB_PROJECT_ROOT"] = str(d)
        try:
            (d / "journal").mkdir()
            (d / "journal" / "trades_261016_kr.json").write_text(json.dumps({"trades": [], "runs": [
                {"approved_ref": "/x/signals/approved_261016_kr.json", "approved_sha256": "S9",
                 "approved_orders": [{"ticker": "035720", "action": "BUY", "source": "llm", "qty": 2, "price": 50000, "idx": 0}]}]}),
                encoding="utf-8")
            db.reset()
            _v1_db(db.db_path())
            c = sqlite3.connect(str(db.db_path()))
            c.execute("INSERT INTO ex_run (run_id, stamp, market, started) VALUES ('261016_kr','261016','kr','2026-10-16T09:30:00+09:00')")
            ins_a = ("INSERT INTO ex_approval (id, run_id, pass, ticker, market, action, qty, price, rejected_why, approved_path, approved_sha, body)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,'{}')")
            for row in ((1, "261016_kr", "main", "005930", "kr", "BUY", 3, 69500, None, "signals/approved_261016_kr.json", "S1"),
                        (2, "261016_kr", "main", "000660", "kr", "SELL", 1, 200000, None, "signals/approved_261016_kr.json", "S1"),
                        (3, "261016_kr", "main", "035420", "kr", "BUY", None, None, '["상한"]', "signals/approved_261016_kr.json", "S1"),
                        (4, "261016_kr", "main", "005930", "kr", "BUY", 4, 69500, None, "signals/approved_261016_kr.json", "S2")):
                c.execute(ins_a, row)
            ins_o = ("INSERT INTO ex_order (id, run_id, ticker, market, side, qty, order_no, sent_at, status, approved_ref, trade_key, body)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
            b = lambda **k: json.dumps(k)
            for row in ((1, "261016_kr", "000660", "kr", "SELL", 1, "N1", "t1", "SENT", "approved_261016_kr.json", "k1", b()),
                        (2, "261016_kr", "005930", "kr", "BUY", 3, "N2", "t2", "SENT", "approved_261016_kr.json", "k2", b()),
                        (3, "261016_kr", "005930", "kr", "BUY", 3, "N3", "t3", "SENT", "approved_261016_kr.json", "k3",
                         b(approved_sha256="S1", approved_idx=0, intent_key="S1:0:1")),
                        (4, "261016_kr", "035720", "kr", "BUY", 2, "N4", "t4", "SENT", "approved_261016_kr.json", "k4",
                         b(approved_sha256="S9", approved_idx=0, intent_key="S9:0:1")),
                        (5, "261016_kr", "005930", "kr", "BUY", 3, None, "t3", "PENDING", "approved_261016_kr.json", "k5",
                         b(approved_sha256="S1", approved_idx=0, intent_key="S1:0:1"))):
                c.execute(ins_o, row)
            ins_f = "INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,?,?,?,?,?,'{}')"
            for row in ((1, "261016_kr", "000660", "kr", -1, "FILLED"), (3, "261016_kr", "005930", "kr", 1, "PARTIAL"),
                        (3, "261016_kr", "005930", "kr", 3, "FILLED")):
                c.execute(ins_f, row)
            c.commit()
            c.close()
            rep = db.migrate()
            st7 = next((x for x in rep["steps"] if x["to"] == 7), {})
            idx = {r["id"]: r["idx"] for r in db.q("SELECT id, idx FROM ex_approval WHERE id <= 4")}
            if idx != {1: 0, 2: 1, 3: None, 4: 0}:
                p.append(f"승인 idx(파일 안 순번 · 거부 행 없음): {idx}")
            o = {r["id"]: (r["approval_link"], r["approval_id"], r["intent_key"]) for r in db.q("SELECT id, approval_link, approval_id, intent_key FROM ex_order")}
            r4 = db.q1("SELECT approved_path, approved_sha, qty FROM ex_approval WHERE id=?", (o.get(4, (0, 0, 0))[1],)) or {}
            if (o[1][:2], o[2][:2], o[3], o[5][:2]) != (("inferred", 2), (None, None), ("recorded", 1, "S1:0:1"), ("recorded", 1)) or o[5][2] is not None:
                p.append(f"연결: {o}")
            if o[4][0] != "recorded" or not str(r4.get("approved_path", "")).startswith("journal:trades_261016_kr.json#runs[0]") or r4.get("qty") != 2:
                p.append(f"덮인 승인(S9) 되살림: {o[4]} {r4}")
            if st7.get("order_dup_intent") != [5] or st7.get("fill_sign_rows") != 1:
                p.append(f"보고(같은 의도 키 · 부호 행): {st7.get('order_dup_intent')} {st7.get('fill_sign_rows')}")
            if [r["filled_qty"] for r in db.q("SELECT filled_qty FROM v_fill_latest ORDER BY order_id")] != [1, 3]:
                p.append("마지막 관측(부호 없음)")
            try:
                db.connect().execute("UPDATE ex_order SET qty=9 WHERE id=1")
                p.append("주문 수량을 고칠 수 있다(추가만 표)")
            except sqlite3.DatabaseError:
                pass
            db.connect().execute("UPDATE ex_order SET approval_link='inferred' WHERE id=2")          # 연결 칸은 열려 있다
            cf = db.connect().execute("INSERT INTO op_cash_flow (family, date, kind, currency, amount, created_at) VALUES"
                                      " ('real','2026-10-20','deposit','KRW',5000000.0,'x')").lastrowid
            for sql in ("UPDATE op_cash_flow SET amount=1.0 WHERE id=?", "DELETE FROM op_cash_flow WHERE id=?"):
                try:
                    db.connect().execute(sql, (cf,))
                    p.append(f"입출금 표를 고쳤다: {sql[:20]}")
                except sqlite3.DatabaseError:
                    pass
            try:
                db.connect().execute("INSERT INTO op_cash_flow (family, date, kind, currency, amount, created_at) VALUES ('real','d','gift','KRW',1.0,'x')")
                p.append("입출금 종류 CHECK가 없다")
            except sqlite3.DatabaseError:
                pass
            if rep["drift"] or db.migrate()["steps"]:
                p.append(f"차이·멱등: {rep['drift']}")
        finally:
            if old_root is None:
                os.environ.pop("AITB_PROJECT_ROOT", None)
            else:
                os.environ["AITB_PROJECT_ROOT"] = old_root
    report("(48) 이전 v7 — 승인 순번 · 원문에서 의도 키(중복 비움) · recorded·되살림·inferred·NULL · 부호 관측 · 연결 칸만 열림 · 입출금 표 · 멱등", p)


def t49_ledger_domain_constraints():
    """(49) ← db_domain_constraints(Codex R12) — 'not-a-number' 수량·'BAD' 시장·'SIDEWAYS' 방향이 주문 표에 그대로 들어갔다.
    올바른 동작: 집행 장부 다섯 표가 STRICT + CHECK로 거부한다(자료형 · 시장 소문자 · 방향·상태·판정 어휘 · 수량 > 0 · 체결 수량 부호 없음 ·
    체결인데 0 · 환율 > 0 · 공유 현금 0/1 · 승인 행은 수량, 거부 행은 사유). 잔고 기록은 빈 문자열 숫자를 비워서 넣는다(수집이 죽지 않게)."""
    p = []
    with fresh("t49"):
        db.run_start("261016", "kr")
        c = db.connect()
        ok_o = ("INSERT INTO ex_order (run_id, ticker, market, side, qty, sent_at, status, trade_key, body) VALUES"
                " ('261016_kr','005930',?,?,?,'t',?,'k','{}')")
        oid = c.execute(ok_o, ("kr", "BUY", 3, "SENT")).lastrowid
        bad = [("주문 'not-a-number'·BAD·SIDEWAYS", "INSERT INTO ex_order (qty, market, side) VALUES ('not-a-number','BAD','SIDEWAYS')", ()),
               ("주문 시장 대문자", ok_o, ("KR", "BUY", 3, "SENT")),
               ("주문 방향 밖", ok_o, ("kr", "HOLD", 3, "SENT")),
               ("주문 수량 0", ok_o, ("kr", "BUY", 0, "SENT")),
               ("주문 수량 2.5", ok_o, ("kr", "BUY", 2.5, "SENT")),
               ("주문 상태 밖", ok_o, ("kr", "BUY", 3, "WEIRD")),
               ("체결 음수", "INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,'261016_kr','005930','kr',-1,'FILLED','{}')", (oid,)),
               ("체결인데 0", "INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,'261016_kr','005930','kr',0,'FILLED','{}')", (oid,)),
               ("판정 밖", "INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,'261016_kr','005930','kr',1,'DONE','{}')", (oid,)),
               ("승인 수량+거부 사유", "INSERT INTO ex_approval (run_id, pass, ticker, market, action, qty, rejected_why, approved_path, approved_sha, body)"
                                     " VALUES ('261016_kr','main','005930','kr','BUY',3,'x','p','s','{}')", ()),
               ("승인 수량도 사유도 없음", "INSERT INTO ex_approval (run_id, pass, ticker, market, action, approved_path, approved_sha, body)"
                                       " VALUES ('261016_kr','main','005930','kr','BUY','p','s','{}')", ()),
               ("잔고 공유 현금 2", "INSERT INTO ex_balance (market, phase, cash_shared, at) VALUES ('kr','capture',2,'t')", ()),
               ("잔고 환율 0", "INSERT INTO ex_balance (market, phase, exchange_rate, at) VALUES ('us','capture',0,'t')", ())]
        for label, sql, args in bad:
            try:
                c.execute(sql, args)
                p.append(f"{label}을(를) 받았다")
            except sqlite3.DatabaseError:
                pass
        fid = c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (?,'261016_kr','005930','kr',3,'FILLED','{}')",
                        (oid,)).lastrowid
        try:
            c.execute("INSERT INTO ex_fill_fx (fill_id, settle_fx) VALUES (?, 0)", (fid,))
            p.append("환율 0을 받았다")
        except sqlite3.DatabaseError:
            pass
        bid = db.balance_put("261016", "us", "capture", {"cash": "", "exrt": "", "currency": "USD", "equity": "1,234.5"})
        r = db.q1("SELECT cash, exchange_rate, equity, market FROM ex_balance WHERE id=?", (bid,))
        if (r["cash"], r["exchange_rate"], r["equity"], r["market"]) != (None, None, 1234.5, "us"):
            p.append(f"잔고 숫자 정규화(빈 문자열 → 비움 · '1,234.5' → 1234.5): {r}")
        objs = {x["name"]: x["sql"] for x in db.q("SELECT name, sql FROM sqlite_master WHERE type='table' AND name IN"
                                                  " ('ex_approval','ex_order','ex_fill','ex_fill_fx','ex_balance')")}
        if len(objs) != 5 or not all(str(v).rstrip().upper().endswith("STRICT") for v in objs.values()):
            p.append(f"STRICT 표: {sorted(k for k, v in objs.items() if str(v).rstrip().upper().endswith('STRICT'))}")
    report("(49) 집행 장부 제약 — 자료형·시장·방향·상태·판정·수량·부호·환율·공유 현금·승인/거부 규칙 거부 · 잔고 숫자 정규화 · STRICT 다섯", p)


def t50_migrate_v8():
    """(50) 이전 v8 — 옛 표의 대문자 시장·음수 체결·빈 플래그·사유 없는 거부 행을 맞춰 옮긴다(행 수·방향별 수량·판정별 |수량|·환율 합 동일) ·
    새 제약을 어기는 행(체결인데 수량 0)이 있으면 아무것도 안 바꾸고 멈춘다(판 그대로 · 롤백) · 멱등."""
    p = []
    for case in ("ok", "bad"):
        with fresh(f"t50{case}"):
            db.reset()
            _v1_db(db.db_path())
            c = sqlite3.connect(str(db.db_path()))
            c.execute("INSERT INTO ex_run (run_id, stamp, market, started) VALUES ('261016_kr','261016','kr','2026-10-16T09:30:00+09:00')")
            c.execute("INSERT INTO ex_approval (id, run_id, pass, ticker, market, action, qty, price, approved_path, approved_sha, body)"
                      " VALUES (1,'261016_kr','main','005930','KR','BUY',3,69500,'signals/a.json','S1','{}')")
            c.execute("INSERT INTO ex_approval (id, run_id, pass, ticker, market, action, approved_path, approved_sha, body)"
                      " VALUES (2,'261016_kr','main','000660','kr','BUY','signals/a.json','S1','{}')")          # 거부 행인데 사유 칸 없음
            c.execute("INSERT INTO ex_order (id, approval_id, run_id, ticker, market, side, qty, sent_at, status, trade_key, body)"
                      " VALUES (1,1,'261016_kr','005930','KR','SELL',3,'t','SENT','k','{}')")
            c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, verdict, body) VALUES (1,'261016_kr','005930','KR',?,?,'{}')",
                      (-3, "FILLED") if case == "ok" else (0, "FILLED"))
            c.execute("INSERT INTO ex_balance (run_id, market, phase, cash, at) VALUES ('261016_kr','KR','capture',1000,'t')")
            c.commit()
            c.close()
            if case == "bad":
                try:
                    db.migrate()
                    p.append("체결인데 수량 0인 행이 있는데 이전이 끝났다")
                except db.MigrationError as e:
                    if "체결인데 수량 0" not in str(e):
                        p.append(f"멈춘 사유: {e}")
                with db._gate_open():
                    v = db.schema_version(db.connect())
                    strict = str(db.q1("SELECT sql FROM sqlite_master WHERE name='ex_fill'")["sql"]).rstrip().upper().endswith("STRICT")
                if v != 7 or strict:                  # 판마다 따로 커밋 — 실패한 v8만 롤백되고 v7까지는 남는다
                    p.append(f"멈춘 뒤 판 {v}(기대 7) · ex_fill STRICT {strict}(기대 아님)")
                continue
            rep = db.migrate()
            st8 = next((x for x in rep["steps"] if x["to"] == 8), {})
            if st8.get("before", {}).get("rows") != st8.get("after", {}).get("rows") or st8["after"]["markets"] != ["kr"]:
                p.append(f"전후 대조: {st8.get('before')} → {st8.get('after')}")
            got = (db.q1("SELECT market FROM ex_order")["market"], db.q1("SELECT filled_qty FROM ex_fill ORDER BY id LIMIT 1")["filled_qty"],
                   db.q1("SELECT rejected_why, needs_sell_fill FROM ex_approval WHERE id=2"), db.q1("SELECT market, cash_shared FROM ex_balance"))
            if (got[0], got[1], got[2]["rejected_why"], got[2]["needs_sell_fill"], got[3]["market"], got[3]["cash_shared"]) != \
                    ("kr", 3, '"(사유 칸 없음)"', 0, "kr", 0):
                p.append(f"맞춘 값: {got}")
            if rep["drift"] or db.migrate()["steps"]:
                p.append(f"차이·멱등: {rep['drift']}")
    report("(50) 이전 v8 — 대문자 시장·음수 체결·빈 플래그·사유 없는 거부 행 정규화 · 합계 동일 · 위반 행이면 그 판만 롤백(v7 유지) · 멱등", p)


def t51_migrate_v9():
    """(51) 이전 v9 — 측정일 있는 수혜 행 → valid(측정 시각 = 측정일 · 숫자는 근거 문장에서 · 일봉 기준일 모름 → 읽을 때 stale) · '측정 실패' 근거 →
    unavailable · extra.score → 판정 칸 · 과거 run 출처 통계는 받은 호에서만 · mat_expect 0행이면 지움 · 링크 선택 칸은 비움 · 멱등."""
    p = []
    with fresh("t51"):
        db.reset()
        _v1_db(db.db_path())
        c = sqlite3.connect(str(db.db_path()))
        ko_ax = json.dumps(["id", "name", "direction", "status", "opened", "beneficiaries", "score"])
        c.execute("INSERT INTO dim_axis (rid, id, name, direction, status, opened, extra, ko, ord, rev) VALUES (1,'ax','A','+','hit','2026-09-01',?,?,0,1)",
                  (json.dumps({"score": {"judged": "2026-10-10", "excess_pct": 4.5, "note": "적중"}}), ko_ax))
        kb = json.dumps(["ticker", "order", "priced", "priced_basis", "priced_at", "market"])
        for o, (tk, pr, basis, at) in enumerate((("000001", 0, "20일 +1.0% · 벤치 대비 -2.0%p · 선두 대비 -12.5%p", "2026-10-01"),
                                                 ("000002", 1, "측정 실패 2026-10-02: 일봉 0행뿐", "2026-09-20"),
                                                 ("000003", None, None, None))):
            c.execute("INSERT INTO jd_axis_beneficiary (pid, src, ord, ticker, bord, priced, priced_basis, priced_at, market, ko)"
                      " VALUES (1,'beneficiaries',?,?,?,?,?,?, 'KR', ?)", (o, tk, o + 1, pr, basis, at, kb))
        for iid, (rid_, snd, day) in enumerate((("261016_kr", "S0", "2026/10/16"), ("261016_kr", "S0", "2026/10/15"),
                                               ("261016_kr", "S1", "2026/10/14"), ("261017_us", "", "2026/10/17")), start=1):
            c.execute("INSERT INTO mat_issue (id, run_id, sender, source, day, content_sha) VALUES (?,?,?,?,?,?)",
                      (iid, rid_, snd, "src" if not snd else snd, day, f"sha{iid}"))
            c.execute("INSERT INTO mat_issue_run (issue_id, run_id, ord) VALUES (?,?,?)", (iid, rid_, iid))
        c.execute("INSERT INTO mat_link (issue_id, ord, url) VALUES (1, 0, 'https://x')")
        c.commit()
        c.close()
        rep = db.migrate()
        st9 = next((x for x in rep["steps"] if x["to"] == 9), {})
        ax = db.axis_get("ax")
        bs = {b["ticker"]: b for b in ax["beneficiaries"]}
        import market_map as mm
        if (ax.get("judged_at"), ax.get("excess_pct"), ax.get("judge_note"), "score" in ax) != ("2026-10-10", 4.5, "적중", False):
            p.append(f"판정 칸: {ax.get('judged_at')} {ax.get('excess_pct')} {ax.get('judge_note')} score={'score' in ax}")
        b1, b2, b3 = bs["000001"], bs["000002"], bs["000003"]
        if (b1.get("measure_status"), b1.get("measured_at"), b1.get("priced_ret20"), b1.get("priced_vs_lead"), mm.measure_state(b1)) != \
                ("valid", "2026-10-01", 1.0, -12.5, "stale"):
            p.append(f"측정일 있는 행: {b1}")
        if (b2.get("measure_status"), b2.get("measured_at"), b2.get("priced"), b2.get("priced_at"), mm.measure_state(b2)) != \
                ("unavailable", "2026-10-02", True, "2026-09-20", "unavailable"):
            p.append(f"측정 실패 행: {b2}")
        if b3.get("measure_status") or mm.measure_state(b3) != "none":
            p.append(f"잰 적 없는 행: {b3}")
        ss = {(r["run_id"], r["source"]): (r["received"], r["newest_issue_day"], r["requested"]) for r in db.q("SELECT * FROM mat_source_stat")}
        if ss != {("261016_kr", "S0"): (2, "2026-10-16", None), ("261016_kr", "S1"): (1, "2026-10-14", None), ("261017_us", "src"): (1, "2026-10-17", None)}:
            p.append(f"출처 통계 backfill: {ss}")
        if db.q1("SELECT COUNT(*) AS n FROM sqlite_master WHERE name='mat_expect'")["n"] or db.q1("SELECT pick_status FROM mat_link")["pick_status"] is not None:
            p.append("mat_expect가 남았거나 링크 선택 칸이 채워졌다")
        if db.q("SELECT axis_id, ticker FROM v_forecast_outcome ORDER BY ticker")[:1] != [{"axis_id": "ax", "ticker": "000001"}]:
            p.append("결과 연결 뷰")
        if (st9.get("measure_valid"), st9.get("measure_unavailable"), st9.get("score_moved")) != (1, 1, 1):
            p.append(f"보고: {st9}")
        if rep["drift"] or db.migrate()["steps"]:
            p.append(f"차이·멱등: {rep['drift']}")
    report("(51) 이전 v9 — 측정 valid(stale로 읽힘)·실패 unavailable·미측정 · 판정 칸 · 출처 통계 backfill · mat_expect 삭제 · 결과 뷰 · 멱등", p)


def t52_signal_versions_and_outcomes():
    """(52) 판단 재현·결과(Codex J03) — 시그널이 다시 쓰여도 판마다 한 행(ex_signal_version)·제안은 판별 행(지금 판 = v_proposal) · 같은 파일 재가져오기
    0행 · 승인 머리 식별자(시그널·코드)가 승인 행에 · 승인 매수의 목표 거리·기저율이 그 판 제안 행에 · 결정 뒤 결과는 확정 봉으로만(승인·거부 모두 ·
    5·10·20세션 · 멱등 · 미확정 봉 제외)."""
    import feedback
    p = []
    with fresh("t52") as d:
        _seed_facts(1, 2)
        root = d / "art"
        sig, ap, st, tr = _artifacts(root)
        sp = root / "signals" / f"signal_{ST}_{MK}.json"
        sha1 = hashlib.sha256(sp.read_bytes()).hexdigest()
        db.import_run_artifacts(ST, "KR", here=root)
        sig2 = dict(sig, generated_at="2026-10-16T10:30:00+09:00")
        sig2["proposals"] = [dict(sig["proposals"][0], weight_target_pct=3.0)] + sig["proposals"][1:]
        sp.write_text(json.dumps(sig2, ensure_ascii=False, indent=1), encoding="utf-8")
        sha2 = hashlib.sha256(sp.read_bytes()).hexdigest()
        ap2 = {"signal_sha256": sha2, "code_commit": "abc1234", "code_sha": "c0de",
               "orders": [{"ticker": "005930", "action": "BUY", "qty": 3, "price": 69500, "source": "llm", "upside_to_target_pct": 15.4,
                           "base_rate": {"cond": "below_ma20", "n": 115, "median_pct": 7.04, "hit_pct": 0.61, "horizon_days": 20, "as_of": "2026-10-16"}}],
               "rejected": []}
        (root / "signals" / f"approved_{ST}_{MK}_v2.json").write_text(json.dumps(ap2, ensure_ascii=False), encoding="utf-8")
        db.import_run_artifacts(ST, "KR", here=root)
        vers = [r["sha"] for r in db.q("SELECT sha FROM ex_signal_version ORDER BY imported_at, sha")]
        cur = db.q1("SELECT sha FROM ex_signal")["sha"]
        by_sha = {r["sha"]: r["n"] for r in db.q("SELECT sha, COUNT(*) AS n FROM ex_proposal GROUP BY sha")}
        vp = db.q("SELECT idx, weight_target_pct FROM v_proposal ORDER BY idx")
        if set(vers) != {sha1, sha2} or cur != sha2 or by_sha != {sha1: 3, sha2: 3} or vp[0]["weight_target_pct"] != 3.0:
            p.append(f"판본: {len(vers)} · 지금 {cur == sha2} · 판별 {by_sha} · v_proposal {vp[:1]}")
        n0 = (_n("ex_signal_version"), _n("ex_proposal"))
        db.import_run_artifacts(ST, "KR", here=root)
        if (_n("ex_signal_version"), _n("ex_proposal")) != n0:
            p.append("같은 파일 재가져오기가 판본·제안 행을 늘렸다")
        a = db.q1("SELECT signal_sha, code_commit, code_sha FROM ex_approval WHERE approved_path LIKE '%_v2.json'")
        pr = db.q1("SELECT upside_to_target_pct, base_rate_cond, base_rate_n, base_rate_horizon_days FROM ex_proposal WHERE sha=? AND ticker='005930'", (sha2,))
        if (a["signal_sha"], a["code_commit"], a["code_sha"]) != (sha2, "abc1234", "c0de") or \
                (pr["upside_to_target_pct"], pr["base_rate_cond"], pr["base_rate_n"], pr["base_rate_horizon_days"]) != (15.4, "below_ma20", 115, 20):
            p.append(f"승인 머리·제안 기저율: {dict(a)} · {dict(pr)}")
        rid = db.run_id(ST, MK)
        db.screen_put([{"run_id": rid, "pass_no": 1, "mode": "main", "ticker": "005930", "action": "BUY", "outcome": "accepted",
                        "ctx": {"price": 100.0}, "signal_sha": sha2},
                       {"run_id": rid, "pass_no": 1, "mode": "main", "ticker": "000660", "action": "BUY", "outcome": "rejected",
                        "ctx": {"price": 200.0}, "signal_sha": sha2},
                       {"run_id": rid, "pass_no": 1, "mode": "main", "ticker": "035420", "action": "BUY", "outcome": "rejected", "ctx": {}}])
        c = db.connect()
        for k in range(21):
            dd = (datetime(2026, 10, 17) + timedelta(days=k)).strftime("%Y-%m-%d")
            for tk, base in (("005930", 100.0), ("000660", 200.0)):
                cl = base + (k + 1) * (1 if tk == "005930" else -2)
                c.execute("INSERT INTO mk_bar (subject, date, high, low, close, source, fetched_at, exchange, is_final) VALUES (?,?,?,?,?,?,?,?,?)",
                          (f"KR:{tk}", dd, cl + 1, cl - 1, cl, "KIS", "2026-11-30T16:00:00+09:00", "KRX", 0 if k == 20 else 1))
        n1 = feedback.outcomes()
        o = {(r["ticker"], r["horizon"]): (r["ret_pct"], r["max_up_pct"], r["max_down_pct"], r["filled"]) for r in db.q(
            "SELECT s.ticker, o.horizon, o.ret_pct, o.max_up_pct, o.max_down_pct, o.filled FROM ex_outcome o JOIN ex_screen s ON s.id = o.screen_id")}
        if n1 != 6 or o.get(("005930", 5)) != (5.0, 6.0, 0.0, 1) or o.get(("000660", 20))[0] != -20.0 or ("035420", 5) in o:
            p.append(f"결과 표: 새 {n1} · {o}")
        if feedback.outcomes() != 0:
            p.append("결과 표 재실행이 행을 늘렸다")
        if (feedback.outcome_median(True), feedback.outcome_median(False)) != (20.0, -20.0):
            p.append(f"결과 중앙값: {feedback.outcome_median(True)} {feedback.outcome_median(False)}")
    report("(52) 판단 재현·결과 — 시그널 판본·판별 제안·지금 판 뷰 · 재가져오기 0 · 승인 머리 식별자 · 제안 기저율 · 확정 봉 결과(승인·거부 · 멱등)", p)


def t54_outcome_fill_attribution():
    """(54) ← outcome_wrong_decision_fill(Codex W05)의 올바른 동작 — 실제 체결은 그 결정(심사 회차 → 승인 행 → 주문 → 체결)에만 붙는다.
    같은 run·종목의 거절 → 승인: 거절 행 filled 0·체결 수량·손익 없음(가상 수익은 그대로) · 승인 두 회차: 주문을 낸 회차에만 · 일부 체결은 그 수량 ·
    승인 행을 못 찾으면 unlinked(NULL) · 옛 계산 판 행은 다시 쓴다 · 중앙값은 run·종목의 마지막 결정 하나 · 계좌 계열로 거른다."""
    import feedback
    p = []
    with fresh("t54"):
        c = db.connect()
        rid = db.run_start("261019", "kr", account_mode="paper")
        rid2 = db.run_start("261020", "kr", account_mode="real")
        db.screen_put([
            {"run_id": rid, "pass_no": 1, "mode": "main", "ticker": "000660", "action": "BUY", "outcome": "rejected", "ctx": {"price": 100}, "signal_sha": "old"},
            {"run_id": rid, "pass_no": 2, "mode": "main", "ticker": "000660", "action": "BUY", "outcome": "accepted", "ctx": {"price": 100}, "signal_sha": "new"},
            {"run_id": rid, "pass_no": 2, "mode": "main", "ticker": "005930", "action": "BUY", "outcome": "accepted", "ctx": {"price": 100}, "signal_sha": "new"},
            {"run_id": rid, "pass_no": 3, "mode": "main", "ticker": "005930", "action": "BUY", "outcome": "accepted", "ctx": {"price": 100}, "signal_sha": "new2"},
            {"run_id": rid, "pass_no": 3, "mode": "main", "ticker": "035420", "action": "BUY", "outcome": "accepted", "ctx": {"price": 100}, "signal_sha": "new2"},
            {"run_id": rid2, "pass_no": 1, "mode": "main", "ticker": "000660", "action": "BUY", "outcome": "accepted", "ctx": {"price": 100}, "signal_sha": "r"}])

        def appr(run, sp, tk, sha):
            return c.execute("INSERT INTO ex_approval (run_id, pass, idx, ticker, market, action, qty, price, approved_path, approved_sha, body,"
                             " screen_pass) VALUES (?,'main',0,?,'kr','BUY',5,100.0,'signals/x.json',?, '{}', ?)", (run, tk, sha, sp)).lastrowid

        def order(run, aid, tk, key, q, verdict="FILLED"):
            oid = c.execute("INSERT INTO ex_order (approval_id, run_id, ticker, market, side, qty, order_no, sent_at, status, trade_key, body)"
                            " VALUES (?,?,?,'kr','BUY',5,?,'2026-10-19T11:00:00+09:00','SENT',?,'{}')", (aid, run, tk, key, key)).lastrowid
            c.execute("INSERT INTO ex_fill (order_id, run_id, ticker, market, filled_qty, avg_price, verdict, at, body)"
                      " VALUES (?,?,?,'kr',?,100.0,?,'2026-10-19T11:00:01+09:00','{}')", (oid, run, tk, q, verdict))
        a660 = appr(rid, 2, "000660", "s2")
        order(rid, a660, "000660", "o1", 5)
        appr(rid, 2, "005930", "s2")                                   # 회차 2 승인은 보내지 않았다(재심사로 대체)
        a930 = appr(rid, 3, "005930", "s3")
        order(rid, a930, "005930", "o2", 2, "PARTIAL")
        a660r = appr(rid2, 1, "000660", "sr")
        order(rid2, a660r, "000660", "o3", 5)
        for dd in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"):
            for tk in ("000660", "005930", "035420"):
                c.execute("INSERT INTO mk_bar (subject, date, high, low, close, source, fetched_at, exchange, is_final)"
                          " VALUES (?,?,110,100,110,'KIS','2026-10-27T16:00:00+09:00','KRX',1)", (f"KR:{tk}", dd))
        # 옛 계산 판(1)이 거절 결정에 체결을 붙여 둔 행 — 지금 규칙으로 다시 써야 한다
        old_sid = db.q1("SELECT id FROM ex_screen WHERE run_id=? AND pass_no=1", (rid,))["id"]
        c.execute("INSERT INTO ex_outcome (screen_id, horizon, ret_pct, filled, filled_qty, pnl_net, calc_version) VALUES (?,5,10.0,1,5,48.75,1)",
                  (old_sid,))
        feedback.outcomes()
        got = {(r["pass_no"], r["ticker"], r["run_id"][:6]): (r["filled"], r["filled_qty"], r["pnl_net"] is not None, r["fill_link"], r["calc_version"])
               for r in db.q("SELECT s.pass_no, s.ticker, s.run_id, o.filled, o.filled_qty, o.pnl_net, o.fill_link, o.calc_version FROM ex_outcome o"
                             " JOIN ex_screen s ON s.id=o.screen_id WHERE o.horizon=5")}
        want = {(1, "000660", "261019"): (0, None, False, "not_accepted", 2),
                (2, "000660", "261019"): (1, 5, True, "approval", 2),
                (2, "005930", "261019"): (0, None, False, "approval", 2),
                (3, "005930", "261019"): (1, 2, True, "approval", 2),
                (3, "035420", "261019"): (None, None, False, "unlinked", 2),
                (1, "000660", "261020"): (1, 5, True, "approval", 2)}
        if got != want:
            p.append(f"귀속: {got}")
        rets = {r["ret_pct"] for r in db.q("SELECT ret_pct FROM ex_outcome WHERE horizon=5")}
        if rets != {10.0}:
            p.append(f"가상 수익(모든 결정 같은 시계): {rets}")
        n_before = _n("ex_outcome")
        if feedback.outcomes() != 0 or _n("ex_outcome") != n_before:
            p.append("같은 계산 판 재실행이 행을 바꿨다")
        # 중앙값 평가 단위 — run·종목의 마지막 main 결정 하나 · 계열
        if feedback.outcome_median(False, horizon=5) is not None:
            p.append("거절 뒤 승인된 종목의 거절 행이 거절 표본에 들어갔다")
        rows_acc = db.q("SELECT s.id FROM ex_outcome o JOIN ex_screen s ON s.id=o.screen_id WHERE o.horizon=5 AND s.outcome='accepted'"
                        " AND s.id = (SELECT MAX(s2.id) FROM ex_screen s2 WHERE s2.run_id=s.run_id AND s2.ticker=s.ticker AND s2.mode='main')")
        if len(rows_acc) != 4 or feedback.outcome_median(True, horizon=5, family="real") != 10.0 \
                or feedback.outcome_median(True, horizon=5, family="paper") != 10.0:
            p.append(f"승인 표본(마지막 결정): {len(rows_acc)}")
        if feedback.outcome_median(True, horizon=5, family="nope") is not None:
            p.append("없는 계열인데 표본이 있다")
    report("(54) 결과 귀속 — 실제 체결은 그 결정(심사 회차→승인 행→주문)에만 · 거절은 체결 없음 · 일부 체결·미연결·옛 계산 판 재작성 · "
           "중앙값은 마지막 결정·계열(리뷰 재현 W05)", p)


def t53_migrate_v10():
    """(53) 이전 v10 — 기존 제안·근거 행에 그 run의 시그널 해시 · 지금 파일 해시가 같을 때만 시그널 판본 행(다르면 재구성 안 함) · 새 칸 비움 ·
    MISMATCH 판정 트리거가 판별 키로 다시 산다 · 승인 머리·심사 시그널 칸 · 결과 표 · 멱등."""
    p = []
    with fresh("t53") as d:
        old_root = os.environ.get("AITB_PROJECT_ROOT")
        os.environ["AITB_PROJECT_ROOT"] = str(d)
        try:
            (d / "signals").mkdir()
            body = json.dumps({"proposals": [{"ticker": "005930"}]})
            (d / "signals" / "signal_261016_kr.json").write_text(body, encoding="utf-8")
            (d / "signals" / "signal_261017_kr.json").write_text("{\"changed\": true}", encoding="utf-8")
            s1 = hashlib.sha256(body.encode("utf-8")).hexdigest()
            db.reset()
            _v1_db(db.db_path())
            c = sqlite3.connect(str(db.db_path()))
            for rid_, sha_ in (("261016_kr", s1), ("261017_kr", "stale-sha")):
                c.execute("INSERT INTO ex_run (run_id, stamp, market) VALUES (?,?,'kr')", (rid_, rid_[:6]))
                c.execute("INSERT INTO ex_signal (run_id, path, sha) VALUES (?,?,?)", (rid_, f"signals/signal_{rid_}.json", sha_))
            c.execute("INSERT INTO ex_proposal (run_id, idx, ticker, market, action) VALUES ('261016_kr', 0, '005930', 'kr', 'BUY')")
            c.execute("INSERT INTO ex_proposal (run_id, idx, ticker, market, action) VALUES ('261016_kr', 1, '000660', 'kr', 'BUY')")
            c.execute("INSERT INTO ex_proposal_fact (run_id, idx, fact_id, role) VALUES ('261016_kr', 0, 'F-000002', 'support')")
            c.commit()
            c.close()
            rep = db.migrate()
            st10 = next((x for x in rep["steps"] if x["to"] == 10), {})
            # 이전이 끝나면 렌더본을 다시 쓴다(hold_exports 계약) — 안 쓰면 새 칸이 든 표의 렌더본이 옛 모양이라 doctor가 실패한다(연기 시험 ⑫)
            if rep.get("doctor_ok") is not True or not rep.get("exported") or rep.get("export_error"):
                p.append(f"이전 뒤 렌더본·doctor: doctor_ok={rep.get('doctor_ok')} · exported={rep.get('exported')} · {rep.get('export_error')}")
            shas = {r["sha"] for r in db.q("SELECT sha FROM ex_proposal")} | {r["sha"] for r in db.q("SELECT sha FROM ex_proposal_fact")}
            if shas != {s1} or len(db.q("SELECT * FROM v_proposal")) != 2:
                p.append(f"제안 판 해시: {shas}")
            if (st10.get("signal_versions"), st10.get("signal_versions_unmatched")) != (1, 1) or db.q1("SELECT sha FROM ex_signal_version")["sha"] != s1:
                p.append(f"시그널 판본: {st10}")
            db.connect().execute("INSERT INTO ev_verdict (fact_id, run_id, verdict, fact_rev) VALUES ('F-000002','261016_kr','MISMATCH', 2)")
            if db.q1("SELECT needs_review FROM ex_proposal WHERE idx=0")["needs_review"] != 1 or db.q1("SELECT needs_review FROM ex_proposal WHERE idx=1")["needs_review"]:
                p.append("MISMATCH 트리거가 판별 키로 제안을 표시하지 않았다")
            cols_a = {r["name"] for r in db.q("PRAGMA table_info(ex_approval)")}
            if not {"signal_sha", "code_commit", "code_sha"} <= cols_a or "signal_sha" not in {r["name"] for r in db.q("PRAGMA table_info(ex_screen)")}:
                p.append("승인 머리·심사 시그널 칸")
            if rep["drift"] or db.migrate()["steps"]:
                p.append(f"차이·멱등: {rep['drift']}")
        finally:
            if old_root is None:
                os.environ.pop("AITB_PROJECT_ROOT", None)
            else:
                os.environ["AITB_PROJECT_ROOT"] = old_root
    report("(53) 이전 v10 — 제안·근거에 그 run 시그널 해시 · 파일이 같을 때만 판본 행 · 트리거 판별 키 · 승인 머리·심사 칸 · 멱등 · 끝나면 렌더본 다시 씀·doctor 통과", p)


CASES = (t01_roundtrip_thesis, t02_roundtrip_scenario, t03_roundtrip_market_map, t04_roundtrip_allocation_company_fact,
         t05_update_load_save, t05b_save_skips_untouched_stale_row, t06_leg_derived, t07_test_guard,
         t08_append_only_tables, t09_logs, t10_fact_version_trigger, t11_verdict_mismatch_trigger,
         t11b_review_flag_survives_thesis_update, t11c_scenario_fact_ids_list,
         t12_exports_idempotent_mode_format, t13_exports_match_old_files, t14_export_path_redirect, t15_render_all_names,
         t16_bars_cache_domestic, t17_bars_cache_overseas_index, t18_sector_board_store, t18b_sector_board_change_pct,
         t18c_overseas_live_session_after_kst_midnight, t18d_finality_dst_finalize_holidays,
         t19_peaks, t19b_peak_kept_when_price_missing, t20_calendar, t21_watchlist_main_names, t21b_watchlist_empty_market,
         t21c_company_put_no_market_key, t21d_watchlist_resave_existing_rows,
         t22_import_run_artifacts, t22b_import_rewritten_trades_file, t22c_import_unknown_fact_id,
         t23_run_timing, t24_balance_positions, t25_real_check_loss_review, t26_audit_mirror,
         t27_backup_restore, t27b_restore_child_before_parent, t27c_backup_follows_aitb_db, t27d_restore_refreshes_exports,
         t28_doctor, t29_export_after_commit, t30_doc_history, t30b_watchlist_save_keeps_history, t31_sector_bridge,
         t32_import_allocation_basis, t33_import_factor_judgments, t34_screen_rows,
         t35_theses_under_review, t36_schema_version_drift, t37_migrate_v1_to_v2, t38_evidence_store, t39_migrate_v3_evidence,
         t40_stage_rows, t41_audit_scope, t42_run_mode_fixed, t43_migrate_v4, t44_migrate_v5, t45_migrate_v6,
         t46_order_approval_link, t47_fill_observations, t48_migrate_v7, t49_ledger_domain_constraints, t50_migrate_v8,
         t51_migrate_v9, t52_signal_versions_and_outcomes, t53_migrate_v10, t54_outcome_fill_attribution)

for _fn in CASES:
    _safe(_fn)

db.reset()
shutil.rmtree(_BOOT, ignore_errors=True)
_STATE["done"] = True
n_fail = sum(1 for r in results if r[0] == FAIL)
n_held = sum(1 for r in results if r[0] == FAIL and r[3])
print(f"\nTEST-SUMMARY file=test_db.py total={len(results)} passed={len(results) - n_fail} failed={n_fail} held={n_held}")
sys.exit(1 if n_fail else 0)
