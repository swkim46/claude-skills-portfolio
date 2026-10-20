#!/usr/bin/env python3
"""
risk_guard 거부 케이스 테스트 — Phase 1 완료 판정의 핵심.

실행: python3 test_risk_guard.py

이 테스트가 지키는 명제는 하나다: **어떤 식으로든 입력이 이상하면 주문은 0건이다.**
수익을 못 내는 것은 실패가 아니지만, 의도치 않은 주문이 나가는 것은 실패다.
네트워크·API 키 없이 파일 입력과 **임시 저장소**로 돌기 때문에 언제든 회귀 검사로 쓸 수 있다.

저장소(2026-10-17): 원장(논지·지도·달력·배분·자산 곡선·고점·감사 소급·감시 목록)은 `journal/trading.db` 하나가 원본이다.
테스트는 실제 저장소를 절대 열지 않는다 — 스위트 전체를 빈 임시 저장소로 감싸고(`testkit.db_case`), 원장이 필요한 케이스는
그 안에서 새 임시 저장소를 열어 `testkit.seed`로 심은 뒤 **심은 행이 보이는지 먼저 확인한다**(심기가 조용히 실패한 채
통과하는 테스트가 가장 나쁘다 — 옛 구조에서는 경로를 돌려 심은 파일을 모듈이 안 읽어도 몰랐다).
"""
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import risk_guard as rg
import db                    # noqa: E402
import testkit               # noqa: E402
HERE_DIR = Path(__file__).parent
# 게이트 엔진 — 저장소 루트의 .claude/skills/_stepgate/ (프로젝트 폴더의 형제).
STEPGATE_DIR = HERE_DIR.resolve().parent / ".claude" / "skills" / "_stepgate"
# 감시 목록 렌더본(config/watchlist.json) — 읽기만 하고 케이스마다 임시 저장소에 심는다
_WATCHLIST = json.loads((HERE_DIR / "config" / "watchlist.json").read_text(encoding="utf-8"))

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []

# ★ 2026-10-11 사용자: "테스트가 멈추지 않도록 · 멈추는 걸 모르지 않도록."
#   실사례: (66)-b가 검사 대상 코드의 sys.exit에 걸려 프로세스가 끝났고, 그 뒤 케이스가 10-03부터 일주일 동안 돌지 않았는데
#   아무도 몰랐다. 그래서 ① 케이스마다 예외·SystemExit를 잡아 FAIL로 적고 다음 케이스로 간다(`_safe`·`run_case`)
#   ② 그래도 요약 전에 끝나면 atexit이 '멈춤'을 크게 찍고 TEST-HALTED 줄을 남긴다 ③ 요약에 TEST-SUMMARY 줄 — `run_tests.py`가 센다.
import atexit  # noqa: E402
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"\n★★ 테스트가 중간에 멈췄다 — 마지막으로 시작한 케이스: {_STATE['current']} · 그때까지 {n}건(실패 {nf}) · "
          f"이 뒤 케이스는 돌지 않았다", file=sys.stderr)
    print(f"TEST-HALTED file=test_risk_guard.py ran={n} failed={nf} last={_STATE['current']}")
    sys.stdout.flush()


atexit.register(_halt_banner)

# ★ 스위트 전체를 빈 임시 저장소 하나로 감싼다(2026-10-17) — 원장을 심지 않는 케이스가 어떤 모듈을 거쳐 저장소를 열어도
#   실제 journal/trading.db에는 닿지 않는다(현재 run 포인터·세션 기록 포함 — 예전 `_ss_iso.CURRENT_RUN` 임시 파일 자리).
#   원장을 심는 케이스는 이 안에서 `testkit.db_case()`로 새 저장소를 열고 끝나면 이 저장소로 돌아온다.
import contextlib as _ctxlib  # noqa: E402
_DB_SUITE = _ctxlib.ExitStack()
_DB_SUITE.enter_context(testkit.db_case())
_DB_SUITE.enter_context(testkit.paper_limits())     # 계좌 모드 모의 고정 — 실제 limits.json의 계좌 모드와 무관하게 케이스가 같게 돈다
atexit.register(_DB_SUITE.close)


def _seed_check(theses=None, axes=None, calendar=None, watchlist=True, allocation=None, equity=None) -> list:
    """심은 행이 저장소에서 실제로 보이는가 — 안 보이면 문제 목록(케이스는 FAIL)."""
    out = []
    if watchlist and not (db.companies("KR") or db.companies("US")):
        out.append("심기 실패 — 감시 목록 행이 저장소에 없다")
    for t in theses or []:
        if not db.thesis_get(t.get("id")):
            out.append(f"심기 실패 — 논지 {t.get('id')}가 저장소에 없다")
    for a in axes or []:
        if not db.axis_get(a.get("id")):
            out.append(f"심기 실패 — 축 {a.get('id')}가 저장소에 없다")
    if calendar is not None and len(db.calendar_all()) != len(calendar):
        out.append(f"심기 실패 — 달력 {len(db.calendar_all())}행(기대 {len(calendar)})")
    if allocation is not None and len(db.allocation_rows()) != len(allocation):
        out.append(f"심기 실패 — 배분 {len(db.allocation_rows())}행(기대 {len(allocation)})")
    if equity is not None and len(db.equity_rows()) != len(equity):
        out.append(f"심기 실패 — 자산 곡선 {len(db.equity_rows())}행(기대 {len(equity)})")
    return out


def _safe(fn, *a, **k):
    """테스트 함수 하나를 격리해 돌린다 — 예외·SystemExit가 나도 FAIL로 적고 다음으로 간다(Ctrl-C만 통과)."""
    name = getattr(fn, "__name__", str(fn))
    _STATE["current"] = name
    try:
        return fn(*a, **k)
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001 — SystemExit 포함: 검사 대상의 sys.exit가 스위트를 끝내면 안 된다
        results.append((FAIL, f"{name} — 중단", f"{type(e).__name__}: {e}"))
        print(f"[{FAIL}] {name} — 중단({type(e).__name__}: {str(e)[:160]}) · 나머지 테스트는 계속 돈다")


def now_iso(minutes_ago: int = 0) -> str:
    return (datetime.now(KST) - timedelta(minutes=minutes_ago)).isoformat()


# 규칙별 테스트는 그 규칙을 명시적으로 켠다 — limits.json을 튜닝해도 테스트가 깨지지 않게.
# (배포 기본값은 minimal 프로필: 기계적 손절·서킷브레이커·비중상한 OFF)
ROOMY = {"daily_max_order_amount": {"KRW": 100_000_000, "USD": 100_000}}
STRICT = dict(ROOMY, **{
    "per_position_max_pct": 10.0, "max_positions": 8, "cash_floor_pct": 30.0,
    "stop_loss_pct": -7.0, "trailing_stop_from_peak_pct": -10.0,
    "portfolio_daily_loss_halt_pct": -3.0, "daily_max_orders": 4,
    "reject_rules": {"min_confidence": 0.5, "require_match_t1t2_for_buy": True,
                     "reject_if_any_mismatch": True, "max_weight_delta_per_day_pct": 5.0,
                     "forbidden_name_patterns": ["레버리지", "인버스"]},
})

BASE_SNAPSHOT = {
    "market": "KR",
    "generated_at": now_iso(),
    "day_pnl_pct": 0.0,
    "balance": {
        "currency": "KRW",
        "cash": 10_000_000,
        "positions": [
            {"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 10,
             "avg_price": 70000, "price": 71000, "eval_amt": 710000, "pnl_pct": 1.43},
        ],
    },
    "prices": {
        "005930": {"price": 71000, "change_pct": 1.43},
        "000660": {"price": 180000, "change_pct": -0.5},
        "999999": {"price": 5000, "change_pct": 0.0},
    },
}


# ★ 2026-09-22: 주식 비율은 상수가 아니라 시그널의 `allocation` 판단이다. 테스트 기본값 = 최초 판단·전부 넣기(95%).
def base_alloc(**over) -> dict:
    a = {"based_on": None, "target_invested_pct": 95, "regime": "테스트 국면 — 벤치마크 20일 +1.0% · 상승 섹터 8/14",
         "cash_reason": "없음", "cash_release_when": "", "reserved": [], "change": None}
    a.update(over)
    return a


# ★ 2026-10-17 — 제안 근거는 문장(evidence)이 아니라 사실 id(facts[{id, role, support}])다. risk_guard는 판정을 저장소에서 읽는다.
#   케이스마다 빈 저장소에 아래 셋을 같은 순서로 심어 id가 정해진다(시그널 run_id 'test-kr'은 run 형식이 아니라 검증 상태로 판정).
import facts                 # noqa: E402
FACT_OK, FACT_MISMATCH, FACT_T3 = "F-000001", "F-000002", "F-000003"
_SEED_RUN = ("260101", "kr")


def F(fid: str = FACT_OK, role: str = "support") -> list:
    """제안의 facts 칸 — 사실 하나."""
    return [{"id": fid, "role": role, "support": "논지 — 테스트 근거"}]


def seed_facts() -> list:
    """F-000001 = verified(T1 MATCH) · F-000002 = disputed(MISMATCH · 교정 전) · F-000003 = T3로만 MATCH(검증 상태 none).
    심은 상태가 저장소에서 보이지 않으면 문제 목록(케이스는 FAIL)."""
    import contextlib as _c, io as _io
    out, ids = [], []
    with _c.redirect_stdout(_io.StringIO()), _c.redirect_stderr(_io.StringIO()):
        for n, tier in ((1, 1), (2, 1), (3, 3)):
            r = facts.add_fact({"kind": "earnings", "ticker": "000660", "period": "2026Q3", "metric": f"M{n}", "actual": n,
                                "as_of": "2026-10-01", "url": f"https://src.example/rg-{n}", "tier": tier, "source": "테스트",
                                "quote": f"SK hynix reported test metric {n} for the third quarter"}, _SEED_RUN)
            ids.append(r["id"])
        # 검증자는 큐 행의 판(fact_rev)을 옮긴다(W03) — 새로 심은 사실은 판 1
        facts.add_verdicts([{"fact_id": ids[0], "fact_rev": 1, "verdict": "MATCH", "tier": 1},
                            {"fact_id": ids[1], "fact_rev": 1, "verdict": "MISMATCH", "tier": 1},
                            {"fact_id": ids[2], "fact_rev": 1, "verdict": "MATCH", "tier": 3}], run=_SEED_RUN, label="A")
    st = {r["id"]: r["verified_status"] for r in db.q("SELECT id, verified_status FROM ev_fact")}
    if ids != [FACT_OK, FACT_MISMATCH, FACT_T3] or st != {FACT_OK: "verified", FACT_MISMATCH: "disputed", FACT_T3: "none"}:
        out.append(f"심기 실패 — 근거 사실 {ids} 상태 {st}")
    return out


def base_signal(**over) -> dict:
    sig = {
        "schema_version": "1.0",
        "run_id": "test-kr",
        "market": "KR",
        "generated_at": now_iso(),
        "no_trade": False,
        "allocation": base_alloc(),
        "proposals": [{
            "ticker": "000660", "name": "SK하이닉스", "action": "BUY",
            "weight_target_pct": 5.0, "thesis": "테스트용 근거 문장.",
            "confidence": 0.7, "size_why": "테스트 — 기대 +15% · 손절 -7%",
            "facts": F(),
        }],
    }
    sig.update(over)
    for p in sig.get("proposals") or []:
        if isinstance(p, dict) and p.get("action") == "BUY":
            p.setdefault("size_why", "테스트 — 기대 +15% · 손절 -7%")
            p.setdefault("expected_upside_pct", 15.0)     # 2026-10-03 — BUY 필수(상승 여지 바닥 10% · 상한 없음)
    return sig


def run_case(name: str, **kw):
    """케이스 하나를 격리해 돈다 — 안에서 예외·SystemExit가 나도 FAIL로 적고 다음 케이스로 간다."""
    _STATE["current"] = name
    try:
        return _run_case_impl(name, **kw)
    except KeyboardInterrupt:
        raise
    except BaseException as e:               # noqa: BLE001
        results.append((FAIL, name, f"중단 — {type(e).__name__}: {e}"))
        print(f"[{FAIL}] {name} — 중단({type(e).__name__}: {str(e)[:160]}) · 나머지 테스트는 계속 돈다")


def _run_case_impl(name: str, *, signal, snapshot=None, kill=False,
             limits_patch=None, expect_orders=None, expect_exit=None,
             expect_reject_contains=None, extra_snapshots=None,
             extra_theses=None, extra_map=None, expect_order_reason_contains=None,
             expect_approved=None, extra_calendar=None, pre_seed=None):
    """케이스 하나를 임시 폴더 + 빈 임시 저장소에서 실행하고 결과를 검증한다.
    원장(논지·지도·달력·감시 목록)은 저장소에 심는다 — 배분 원장·고점·감사 소급도 이 저장소 안에서만 쓰고 읽는다."""
    tmp = Path(tempfile.mkdtemp(prefix="rgtest_"))
    try:
        cfg = tmp / "config"
        cfg.mkdir()
        limits = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
        # 갈래 ① 주문 근거 검증(2026-10-13)은 test_track1.py가 따로 본다 — 여기 케이스는 다른 규칙을 재므로 기본 끔(켜는 케이스는 limits_patch로)
        limits["fc_track1_required"] = False
        # 계좌 모드는 모의로 고정한다 — 실제 limits.json의 계좌 모드가 다르면 케이스가 주문당 상한·1주 하한·계좌 계열
        # 필터를 켠 채 돌아 수량·거부가 달라진다. 실계좌 규칙을 재는 케이스는 limits_patch로 켠다.
        limits["account_mode"], limits["real_trading_enabled"] = "paper", False
        limits["scenario_cond_required"] = False   # 형식 적용일은 그 형식을 재는 케이스가 limits_patch로 켠다(날짜가 지나도 안 깨지게)
        if limits_patch:
            limits.update(limits_patch)
        (cfg / "limits.json").write_text(json.dumps(limits), encoding="utf-8")
        if kill:
            (cfg / "KILL").write_text("", encoding="utf-8")

        snap_path = tmp / "snapshot.json"
        snap_path.write_text(json.dumps(snapshot or BASE_SNAPSHOT,
                                        ensure_ascii=False), encoding="utf-8")
        sig_path = tmp / "signal.json"
        if isinstance(signal, str):          # 깨진 JSON을 그대로 흘려보내는 경로
            sig_path.write_text(signal, encoding="utf-8")
        else:
            sig_path.write_text(json.dumps(signal, ensure_ascii=False), encoding="utf-8")
        out_path = tmp / "approved.json"

        # 모듈 전역 경로(설정·산출물 자리)를 임시 폴더로 갈아끼운다.
        saved = (rg.LIMITS_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR)
        rg.LIMITS_PATH = cfg / "limits.json"
        rg.KILL_PATH = cfg / "KILL"
        rg.SIGNALS_DIR = tmp
        # ★ 포트폴리오 분모가 `data/`(반대편 스냅샷)를, 일일 기록이 `journal/`(trades_*.json)을 읽으므로
        # 이 둘도 임시 폴더로 갈아끼운다. 안 갈면 **단위 테스트가 운영자 실계좌를 읽어**
        # 케이스가 그날 잔고에 따라 붙었다 떨어진다(2026-09-11에 실제로 ⑬이 깨졌다).
        # 반대편 스냅샷을 주지 않으면 `ok=False`로 그 시장 자산으로 물러선다 —
        # 개별 검사를 재는 케이스들은 그 상태가 맞다. 교차 시장 층은 아래 전용 케이스가 본다.
        rg.DATA_DIR = tmp / "data"
        rg.JOURNAL_DIR = tmp / "journal"
        rg.DATA_DIR.mkdir(exist_ok=True)
        rg.JOURNAL_DIR.mkdir(exist_ok=True)
        for fname, body in (extra_snapshots or {}).items():
            (rg.DATA_DIR / fname).write_text(json.dumps(body, ensure_ascii=False),
                                             encoding="utf-8")
        theses = (extra_theses or {}).get("theses")
        axes = (extra_map or {}).get("axes")
        problems = []
        # ★ 원장은 빈 임시 저장소에 심는다 — 배분 원장도 이 저장소 안에서만 이어진다(2026-09-22 격리의 저장소판).
        #   달력도 격리한다 — 안 하면 실제 일정이 새어 들어와 그날 일정에 따라 크기가 붙었다 떨어진다(2026-09-21 발견).
        with testkit.db_case(tmp / "db"):
            testkit.seed(watchlist=_WATCHLIST, theses=theses, axes=axes,
                         sectors=(extra_map or {}).get("sectors"),
                         calendar=extra_calendar)
            problems += _seed_check(theses=theses, axes=axes, calendar=extra_calendar)
            problems += seed_facts()
            try:
                if pre_seed:                 # 케이스 전용 저장소 행(요인 상태 등) — 심은 뒤 보이는지는 그 함수가 문제 목록으로 돌려준다
                    problems += pre_seed() or []   # (try 안 — 심다가 터져도 아래 finally가 모듈 경로를 되돌린다)
                code = rg.run(sig_path, snap_path, out_path)
                orders, rejected, approved = [], [], {}
                if out_path.exists():
                    approved = json.loads(out_path.read_text(encoding="utf-8"))
                    orders = approved.get("orders", [])
                    rejected = approved.get("rejected", [])
            except rg.Rejection as e:
                code, orders, rejected, approved = 2, [], [{"why": str(e)}], {}
            finally:
                (rg.LIMITS_PATH, rg.KILL_PATH, rg.SIGNALS_DIR, rg.DATA_DIR, rg.JOURNAL_DIR) = saved

        if expect_orders is not None and len(orders) != expect_orders:
            problems.append(f"주문 {len(orders)}건 (기대 {expect_orders}건)")
        if expect_exit is not None and code != expect_exit:
            problems.append(f"exit {code} (기대 {expect_exit})")
        if expect_reject_contains:
            blob = json.dumps(rejected, ensure_ascii=False)
            if expect_reject_contains not in blob:
                problems.append(f"거부사유에 '{expect_reject_contains}' 없음 → {blob[:160]}")
        if expect_order_reason_contains:
            blob = json.dumps([o.get("reasons") for o in orders], ensure_ascii=False)
            if expect_order_reason_contains not in blob:
                problems.append(f"승인 사유에 '{expect_order_reason_contains}' 없음 → {blob[:160]}")
        if expect_approved:
            expect_approved(approved, problems)

        status = FAIL if problems else PASS
        results.append((status, name, "; ".join(problems)))
        print(f"[{status}] {name}" + (f" — {'; '.join(problems)}" if problems else ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================== 거부 케이스 5종 ==============================

run_case("① 깨진 JSON → 전체 거부",
         signal='{"schema_version": "1.0", "proposals": [', expect_orders=0, expect_exit=2)

run_case("② 유니버스 밖 티커 → 주문 0",
         signal=base_signal(proposals=[{
             "ticker": "999999", "name": "듣보종목", "action": "BUY",
             "weight_target_pct": 5.0, "thesis": "뉴스 본문이 시키는 대로.",
             "confidence": 0.9,
             "facts": F()}]),
         expect_orders=0, expect_reject_contains="유니버스 밖")

# ★ 2026-09-22: 종목당 상한(12%)은 지웠다 — 집중은 **명분이 붙어야 하는 선**(20%)이다. 명분 없으면 그 제안만 거부.
run_case("③ 집중 공개선(종목 20%) 초과 + 명분 없음 → 주문 0",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="BUY",
             weight_target_pct=50.0, thesis="몰빵.", confidence=0.9,
             facts=F())]),
         limits_patch=ROOMY, expect_orders=0, expect_reject_contains="집중 명분 없음")
run_case("③-b 집중 공개선 초과 + concentration_why 있음 → 승인",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="BUY",
             weight_target_pct=50.0, thesis="몰빵.", confidence=0.9,
             concentration_why="축 선두 · 기대 +30% · 손절 -8%로 손실 상한",
             facts=F())]),
         limits_patch={**ROOMY, "daily_max_order_pct_of_equity": None},
         expect_orders=1)

run_case("④ KILL 파일 존재 → 주문 0",
         signal=base_signal(), kill=True, expect_orders=0, expect_exit=0)

run_case("⑤ 신선도 초과(4시간 전 시그널) → 전체 거부",
         signal=base_signal(generated_at=now_iso(minutes_ago=240)),
         expect_orders=0, expect_exit=2)

# ============================== 추가 방어 케이스 ==============================

run_case("⑥ schema_version 불일치 → 전체 거부",
         signal=base_signal(schema_version="0.9"), expect_orders=0, expect_exit=2)

run_case("⑦ 미보유 종목 SELL(공매도 시도) → 주문 0",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="SELL", qty=10,
             thesis="떨어질 것 같다.", confidence=0.9, facts=[])]),
         expect_orders=0, expect_reject_contains="long-only")

run_case("⑧ MISMATCH 근거 포함 → 주문 0",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="BUY",
             weight_target_pct=5.0, thesis="근거가 틀렸다.", confidence=0.9,
             facts=F(FACT_MISMATCH))]),
         expect_orders=0, expect_reject_contains="MISMATCH")

run_case("⑨ BUY에 MATCH(T1/T2) 근거 없음 → 주문 0",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="BUY",
             weight_target_pct=5.0, thesis="느낌상.", confidence=0.9,
             facts=F(FACT_T3))]),
         limits_patch=STRICT, expect_orders=0, expect_reject_contains="MATCH(T1/T2)")

run_case("⑩ confidence 미달 → 주문 0",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="BUY",
             weight_target_pct=5.0, thesis="반신반의.", confidence=0.3,
             facts=F())]),
         limits_patch=STRICT, expect_orders=0, expect_reject_contains="confidence")

run_case("⑪ thesis 빈 문자열(근거 없는 제안) → 전체 거부",
         signal=base_signal(proposals=[dict(
             ticker="000660", action="BUY", weight_target_pct=5.0,
             thesis="   ", confidence=0.9,
             facts=F())]),
         expect_orders=0, expect_exit=2)

run_case("⑫ 서킷브레이커(당일 −4%) → BUY 0",
         signal=base_signal(),
         snapshot={**BASE_SNAPSHOT, "day_pnl_pct": -4.0},
         limits_patch=STRICT, expect_orders=0, expect_reject_contains="서킷브레이커")

# ============================== 정상 동작 케이스 ==============================

run_case("⑬ 정상 BUY → 주문 1건 승인",
         signal=base_signal(), limits_patch=ROOMY, expect_orders=1, expect_exit=0)

run_case("⑭ no_trade=true → 주문 0건이되 정상 종료",
         signal=base_signal(no_trade=True, proposals=[]),
         expect_orders=0, expect_exit=0)

_loss_snapshot = json.loads(json.dumps(BASE_SNAPSHOT))
_loss_snapshot["balance"]["positions"][0].update(price=64000, eval_amt=640000, pnl_pct=-8.6)
run_case("⑮ 손절선 도달 + 손절 ON → 규율 SELL 자동 생성",
         signal=base_signal(no_trade=True, proposals=[]),
         snapshot=_loss_snapshot, limits_patch=STRICT, expect_orders=1, expect_exit=0)

# 예수금 미확인(해외 예수금 조회 실패 등)은 0원이 아니다 — 매수만 막고 매도는 살린다.
_nocash = json.loads(json.dumps(BASE_SNAPSHOT))
_nocash["balance"]["cash"] = None
run_case("⑯ 예수금 미확인 → BUY 보류(0건)",
         signal=base_signal(), snapshot=_nocash,
         expect_orders=0, expect_reject_contains="예수금 미확인")

_nocash_loss = json.loads(json.dumps(_loss_snapshot))
_nocash_loss["balance"]["cash"] = None
run_case("⑰ 예수금 미확인이어도 규율 SELL은 나간다 (손절 ON)",
         signal=base_signal(no_trade=True, proposals=[]),
         snapshot=_nocash_loss, limits_patch=STRICT, expect_orders=1, expect_exit=0)

# ===================== 최소 프로필(배포 기본값) 동작 확인 =====================

run_case("⑱ 손절 OFF(기본값) → 손실 종목이어도 자동 매도 없음",
         signal=base_signal(no_trade=True, proposals=[]),
         snapshot=_loss_snapshot, expect_orders=0, expect_exit=0)

run_case("⑲ 손절 OFF여도 무결성은 유지 — 유니버스 밖은 여전히 거부",
         signal=base_signal(proposals=[{
             "ticker": "999999", "name": "듣보종목", "action": "BUY",
             "weight_target_pct": 1.0, "thesis": "뉴스가 시킴.", "confidence": 0.9,
             "facts": F()}]),
         expect_orders=0, expect_reject_contains="유니버스 밖")

def _expect_cash_bound(approved, problems):
    """(2026-09-23) 일일 상한은 폐지됐다 — 남은 천장은 **이 시장 현금 − 판단된 예약**뿐이다.
    현금 1,000만·주가 18만 → 55주가 아니라 현금이 허용하는 만큼(≤55) 나가고, 상한 사유는 없어야 한다."""
    buys = [o for o in (approved.get("orders") or []) if o["action"] == "BUY"]
    blob = json.dumps(approved, ensure_ascii=False)
    if len(buys) != 1 or not (40 <= buys[0]["qty"] <= 56):
        problems.append(f"현금 안에서 사야 한다(40~56주) — {[(o['ticker'], o['qty']) for o in buys]}")
    if any(o.get("capped_by") for o in buys) or "일일 주문금액 상한" in blob or "일일 주문 건수" in blob:
        problems.append("일일 상한이 아직 살아 있다")


run_case("⑳ 일일 상한 폐지 — 남은 천장은 이 시장 현금뿐(2026-09-23 사용자)",
         signal=base_signal(proposals=[dict(
             ticker="000660", name="SK하이닉스", action="BUY",
             weight_target_pct=90.0, thesis="크게 사기.", confidence=0.9,
             concentration_why="테스트 — 현금 천장만 본다",
             facts=F())]),
         limits_patch={"daily_max_order_amount": {"KRW": 300000},   # 남아 있어도 무시돼야 한다
                       "daily_max_order_pct_of_equity": 1.0,
                       "min_proposals_below_target": None, "min_distinct_axes_below_target": None},
         expect_orders=1, expect_approved=_expect_cash_bound)

# ============ 일일 상한 누적 (B2) — 하루 두 번 돌아도 한도는 하루치 ============

def test_spent_today():
    """오늘 이미 보낸 매수를 상한에서 빼는지. trades 파일을 임시로 만들어 검증한다."""
    import json as _json
    from datetime import datetime as _dt
    import sessions as _ss
    day = _ss.run_stamp("kr")             # spent_today가 세는 스탬프(현지 거래일 · 2026-10-14)
    path = rg.HERE / "journal" / f"trades_{day}_kr.json"
    backup = path.read_text(encoding="utf-8") if path.exists() else None
    problems = []
    try:
        path.write_text(_json.dumps({"trades": [
            {"action": "BUY", "status": "SENT", "qty": 1, "price": 200000},
            {"action": "BUY", "status": "UNKNOWN", "qty": 1, "price": 50000},   # 나갔을 수 있음 → 포함
            {"action": "BUY", "status": "FAILED", "qty": 1, "price": 900000},   # 안 나감 → 제외
            {"action": "SELL", "status": "SENT", "qty": 1, "price": 700000},    # 매도 → 제외
        ]}, ensure_ascii=False), encoding="utf-8")
        amt, cnt = rg.spent_today("KR", "KRW")
        if amt != 250000:
            problems.append(f"금액 {amt} (기대 250000)")
        if cnt != 2:
            problems.append(f"건수 {cnt} (기대 2)")
        rg.spent_today("US", "USD")          # 파일 없는 시장도 죽지 않아야
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(backup, encoding="utf-8")

    status = FAIL if problems else PASS
    results.append((status, "㉑ 오늘 보낸 매수를 일일 상한에서 차감(SENT+UNKNOWN만)", "; ".join(problems)))
    print(f"[{status}] ㉑ 오늘 보낸 매수를 일일 상한에서 차감(SENT+UNKNOWN만)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_spent_today)


def test_kr_cash_field():
    """국내 현금은 D+2 정산금액을 읽어야 한다.

    예수금총액(dnca_tot_amt)은 결제 D+2라 매수 당일 줄지 않는다. 그걸 읽으면
    equity = cash + Σ평가액에서 오늘 산 금액이 두 번 잡힌다(2026-09-08 실체결로 발견).
    아래 summary는 그날 실제 응답 값이다 — 273,500원 매수 직후.
    """
    from kis_client import kr_cash_from_summary

    real = {"dnca_tot_amt": "10000000", "nxdy_excc_amt": "10000000",
            "prvs_rcdl_excc_amt": "9726470", "tot_evlu_amt": "10000470",
            "evlu_amt_smtl_amt": "274000"}
    problems = []

    cash, field = kr_cash_from_summary(real)
    if cash != 9726470.0:
        problems.append(f"D+2 정산금액을 안 읽었다: {cash:,.0f} (기대 9,726,470)")
    if "prvs_rcdl_excc_amt" not in field:
        problems.append(f"읽은 항목명이 안 남는다: {field!r}")

    # 이중계상 검산 — 이게 어긋나면 목표비중 환산이 과대해진다.
    equity = cash + float(real["evlu_amt_smtl_amt"])
    if abs(equity - float(real["tot_evlu_amt"])) > 1:
        problems.append(f"cash+평가액 {equity:,.0f} ≠ 증권사 총평가 {real['tot_evlu_amt']}")

    # 항목이 없을 때만 예수금총액으로 내려가되, 그 사실이 이름에 남아야 한다.
    cash2, field2 = kr_cash_from_summary({"dnca_tot_amt": "500"})
    if cash2 != 500.0 or "dnca_tot_amt" not in field2 or "과대" not in field2:
        problems.append(f"대체 경로가 이상하다: {cash2} / {field2!r}")

    cash3, field3 = kr_cash_from_summary({})
    if cash3 != 0.0:
        problems.append(f"빈 응답에서 0이 아니다: {cash3}")

    status = FAIL if problems else PASS
    results.append((status, "㉒ 국내 현금은 D+2 정산금액(이중계상 방지)", "; ".join(problems)))
    print(f"[{status}] ㉒ 국내 현금은 D+2 정산금액(이중계상 방지)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_kr_cash_field)


def test_kr_tick():
    """국내 지정가는 호가 격자 위에만 있어야 한다.

    2026-09-08 실측: 시세 API가 삼성전자 273,750을 줬는데 20만~50만 구간의 호가단위는
    500원이라 존재할 수 없는 가격이었고, 그대로 보내 [40030000] 호가단위 오류로 거부됐다.
    """
    from kis_client import kr_tick_size, snap_kr_price

    problems = []

    # 구간별 단위
    for price, want in ((1_500, 5), (3_000, 5), (10_000, 10), (30_000, 50),
                        (100_000, 100), (273_750, 500), (700_000, 1_000)):
        got = kr_tick_size(price)
        if got != want:
            problems.append(f"{price:,}원 단위 {got} (기대 {want})")

    # 그날 실제로 거부당한 값 — 매수는 올리고 매도는 내린다
    if snap_kr_price(273_750, "SELL") != 273_500:
        problems.append(f"273,750 SELL → {snap_kr_price(273_750, 'SELL'):,} (기대 273,500)")
    if snap_kr_price(273_750, "BUY") != 274_000:
        problems.append(f"273,750 BUY → {snap_kr_price(273_750, 'BUY'):,} (기대 274,000)")

    # 이미 격자 위면 건드리지 않는다 (아침에 통과한 매수가)
    for side in ("BUY", "SELL"):
        if snap_kr_price(273_500, side) != 273_500:
            problems.append(f"격자 위 값을 옮겼다: 273,500 {side} → {snap_kr_price(273_500, side):,}")

    # 결과는 언제나 격자 위여야 한다 — 특히 올림이 다음 구간으로 넘어갈 때.
    # 경계값이 다음 구간 단위의 배수가 아니면 여기서 깨진다.
    for price in (1_999, 4_999, 19_999, 49_999, 199_950, 499_900, 1_234_567, 1, 999_999):
        for side in ("BUY", "SELL"):
            out = snap_kr_price(price, side)
            if out % kr_tick_size(out):
                problems.append(f"{price:,} {side} → {out:,}는 격자 밖(단위 {kr_tick_size(out)})")
            if out <= 0:
                problems.append(f"{price:,} {side} → {out} (0 이하)")

    status = FAIL if problems else PASS
    results.append((status, "㉓ 국내 지정가를 호가 격자에 맞춘다", "; ".join(problems)))
    print(f"[{status}] ㉓ 국내 지정가를 호가 격자에 맞춘다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_kr_tick)


def test_day_record_append():
    """두 번째 집행이 그날 기록을 덮어쓰면 안 된다.

    이 파일은 감사 기록이면서 `spent_today()`의 입력이다. 덮어쓰면 일일 상한이
    초기화되어 하루 두 번 도는 국내장에서 한도가 두 배가 된다.
    2026-09-08 매도 테스트에서 아침 매수 기록이 실제로 사라지며 발견됐다.
    """
    from execute import merge_day_record

    tmp = Path(tempfile.mkdtemp(prefix="dayrec_"))
    problems = []
    try:
        p = tmp / "trades_260908_kr.json"

        buy = {"generated_at": "2026-09-08T10:05:54+09:00", "approved_ref": "a1",
               "balance_after": {"cash": 9726470},
               "trades": [{"status": "SENT", "action": "BUY", "ticker": "005930",
                           "qty": 1, "price": 273500}]}
        merge_day_record(p, buy)

        sell = {"generated_at": "2026-09-08T10:20:07+09:00", "approved_ref": "a2",
                "balance_after": {"cash": 9999644},
                "trades": [{"status": "SENT", "action": "SELL", "ticker": "005930",
                            "qty": 1, "price": 273500}]}
        merged = merge_day_record(p, sell)

        on_disk = json.loads(p.read_text(encoding="utf-8"))
        if len(on_disk["trades"]) != 2:
            problems.append(f"거래가 {len(on_disk['trades'])}건만 남았다 (기대 2건)")
        actions = [t["action"] for t in on_disk["trades"]]
        if actions != ["BUY", "SELL"]:
            problems.append(f"순서·내용이 어긋난다: {actions}")
        if len(on_disk.get("runs", [])) != 2:
            problems.append(f"run 메타가 {len(on_disk.get('runs', []))}개 (기대 2개)")
        # 최상위는 최신 run이어야 한다 — 잔고는 마지막 상태여야 의미가 있다.
        if on_disk["balance_after"]["cash"] != 9999644:
            problems.append("balance_after가 최신 run 것이 아니다")
        if on_disk["approved_ref"] != "a2":
            problems.append("최상위 approved_ref가 최신이 아니다")

        # 형식 이전 파일(runs 없음)에 덧붙여도 앞선 run이 복원돼야 한다.
        p2 = tmp / "trades_260908_us.json"
        p2.write_text(json.dumps({"generated_at": "x", "approved_ref": "old",
                                  "trades": [{"status": "SENT", "action": "BUY",
                                              "ticker": "AAPL", "qty": 1, "price": 1}]}),
                      encoding="utf-8")
        m2 = merge_day_record(p2, sell)
        if len(m2["trades"]) != 2 or len(m2["runs"]) != 2:
            problems.append(f"구형식 파일 병합 실패: 거래 {len(m2['trades'])} / run {len(m2['runs'])}")

        # ★ 깨진 파일은 **멈춘다**(2026-10-19 · R02) — 옛 판은 옆으로 치우고 새로 썼는데, 그러면 중복 검사가 빈 기록을 보고
        #   이미 나간 주문을 다시 보낸다. 원본은 손대지 않고 남아야 한다.
        import fill as _fill
        p3 = tmp / "trades_260909_kr.json"
        p3.write_text("{{{ 깨진 파일", encoding="utf-8")
        try:
            merge_day_record(p3, sell)
            problems.append("깨진 기록 위에 병합했다(멈춰야 한다)")
        except _fill.DayRecordCorrupt:
            pass
        if p3.read_text(encoding="utf-8") != "{{{ 깨진 파일" or list(tmp.glob("trades_260909_kr.corrupt_*.json")):
            problems.append("깨진 원본을 바꾸거나 옮겼다")
        # 같은 intent_key 행은 덧붙이지 않고 바꾼다 · 같은 run_key의 runs[] 항목도 바꾼다
        p4 = tmp / "trades_260910_kr.json"
        r1 = {"generated_at": "t1", "approved_ref": "a3", "run_key": "rk1",
              "trades": [{"status": "PENDING", "action": "BUY", "ticker": "000660", "qty": 1, "price": 1, "intent_key": "k:0:1"}]}
        merge_day_record(p4, r1)
        r2 = {"generated_at": "t2", "approved_ref": "a3", "run_key": "rk1",
              "trades": [{"status": "SENT", "action": "BUY", "ticker": "000660", "qty": 1, "price": 1, "intent_key": "k:0:1"}]}
        m4 = merge_day_record(p4, r2)
        if [t["status"] for t in m4["trades"]] != ["SENT"] or len(m4["runs"]) != 1:
            problems.append(f"intent_key·run_key 병합: 행 {[t['status'] for t in m4['trades']]} · run {len(m4['runs'])}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    status = FAIL if problems else PASS
    results.append((status, "㉔ 같은 날 두 번째 집행이 기록을 덮어쓰지 않는다", "; ".join(problems)))
    print(f"[{status}] ㉔ 같은 날 두 번째 집행이 기록을 덮어쓰지 않는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_day_record_append)


def test_cost_tracking():
    """비용·회전 기록: 같은 날 두 번 기록해도 누적이 부풀지 않아야 한다."""
    import journal as jr

    problems = []

    rows = [
        {"date": "2026-09-07", "market": "KR", "costs_today": 100, "equity": 1},
        {"date": "2026-09-08", "market": "KR", "costs_today": 30, "equity": 2},
        {"date": "2026-09-08", "market": "KR", "costs_today": 606, "equity": 3},   # 같은 날 재기록
        {"date": "2026-09-08", "market": "US", "costs_today": None, "equity": 4},
    ]
    folded = jr.latest_per_day(rows)
    if len(folded) != 3:
        problems.append(f"접은 결과가 {len(folded)}행 (기대 3행: KR 2일 + US 1일)")
    kr_0908 = [r for r in folded if r["date"] == "2026-09-08" and r["market"] == "KR"]
    if len(kr_0908) != 1 or kr_0908[0]["costs_today"] != 606:
        problems.append(f"같은 날 마지막 행이 안 남았다: {kr_0908}")
    # 누적을 접은 뒤에 세야 30+606이 아니라 606이 된다.
    cum = sum(r.get("costs_today") or 0 for r in folded if r["market"] == "KR")
    if cum != 706:
        problems.append(f"누적 제비용 {cum} (기대 706 = 100 + 606, 30은 같은 날에 덮인 값)")

    # 국내는 브로커 값을 그대로 쓴다 (2026-09-08 실측)
    kr = jr.today_cost_and_turnover(
        {"today_fees": 606.0, "today_buy_amt": 273500.0, "today_sell_amt": 273750.0}, "KR")
    if kr["fees"] != 606.0 or kr["turnover"] != 547250.0:
        problems.append(f"국내 비용·회전이 어긋난다: {kr}")
    if "broker" not in kr["source"]:
        problems.append(f"출처 표기 없음: {kr['source']!r}")

    # 해외는 제비용 항목이 없다 — 추정치를 실측인 척 적으면 안 된다.
    us = jr.today_cost_and_turnover({}, "US", stamp="999999")
    if us["fees"] is not None:
        problems.append(f"해외 비용을 지어냈다: {us['fees']}")
    if us["turnover"] != 0:
        problems.append(f"해외 회전 {us['turnover']} (기록 없으면 0이어야 한다)")

    status = FAIL if problems else PASS
    results.append((status, "㉕ 비용·회전 누적이 같은 날 재기록에 부풀지 않는다", "; ".join(problems)))
    print(f"[{status}] ㉕ 비용·회전 누적이 같은 날 재기록에 부풀지 않는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_cost_tracking)


def test_ai_claim_extraction():
    """검증 큐에는 **등록된 사실만** 오른다 — 노트가 라벨을 언급한 줄은 클레임이 아니다.

    노트가 자기 표기 규칙을 설명하는 줄이 클레임으로 잡히면, 존재하지 않는 사실이
    fact-check로 넘어간다. 검증할 수 없으니 MISMATCH가 나고 run 전체가 막힌다.
    2026-09-08 첫 실 분석에서 실제로 걸렸다. 2026-10-17부터 클레임 목록은 노트 줄을 뽑지 않고
    이 run 문서가 가리킨 등록 사실(`ev_claim_ref` — 노트는 render-note가 `{{F-id}}`·`[S n]`으로 단다)로 만든다
    (`facts.py queue` — 옛 extract_ai_claims는 `_archive/`). 그래서 라벨을 언급만 한 줄은 구조적으로 큐에 오를 수 없다 —
    여기서는 그 성질을 잰다. 등록만 하고 문서가 안 쓴 사실도 클레임이 아니다(2026-10-17 예행 D4).
    """
    import contextlib as _c, io as _io
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="claims_"))
    try:
        with testkit.db_case(tmp / "db"):
            note = ("# 노트\n\n- [AI] 미국 8월 비농업 일자리는 16만2000개 늘었다. {{F-000001}}\n"
                    "덧붙인 것은 `[AI]`(외부 사실)와 `[추정]`(해석)으로 갈라 단다.\n**`[AI]` 클레임 0건** — 재료 밖 사실 없음.\n")
            rows, _ = facts.queue("260908", "kr", None)
            if rows:
                problems.append(f"등록한 사실이 없는데 큐가 {len(rows)}건 — 노트 글자에서 클레임을 만들었다")
            with _c.redirect_stdout(_io.StringIO()), _c.redirect_stderr(_io.StringIO()):
                r = facts.add_fact({"kind": "release", "indicator_code": "US_NFP", "period": "2026-08", "actual": 162, "unit": "K",
                                    "as_of": "2026-09-05", "url": "https://www.bls.gov/news.release/empsit.nr0.htm", "tier": 1,
                                    "quote": "Total nonfarm payroll employment rose by 162,000 in August"}, ("260908", "kr"))
            rows, _ = facts.queue("260908", "kr", None)
            if rows:
                problems.append(f"등록만 하고 문서가 안 쓴 사실이 큐에 올랐다: {[x['fact_id'] for x in rows]}")
            facts.add_ref(r["id"], ("260908", "kr"), "note", "분석노트_260908_kr_v1_0.md", "§2", role="note")
            rows, _ = facts.queue("260908", "kr", None)
            if [x["fact_id"] for x in rows] != [r["id"]]:
                problems.append(f"큐는 노트가 가리킨 등록 사실 1건이어야 한다: {[x['fact_id'] for x in rows]} · 노트 {len(note)}자")
            if any("0건" in str(x.get("line")) for x in rows):
                problems.append("라벨을 언급한 줄을 클레임으로 잡았다")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    status = FAIL if problems else PASS
    results.append((status, "㉖ 검증 큐는 등록된 사실만 — [AI] 라벨 언급 줄을 클레임으로 오인하지 않는다", "; ".join(problems)))
    print(f"[{status}] ㉖ 검증 큐는 등록된 사실만 — [AI] 라벨 언급 줄을 클레임으로 오인하지 않는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_ai_claim_extraction)


def test_number_transcription():
    """노트의 숫자가 재료에 실제로 있는지 대조한다.

    fact-check는 `[AI]`(재료 밖 사실)만 본다. 정작 매매 판단을 움직이는 숫자는 대부분
    재료 안에서 온 것이라 아무 검증도 없었다 — 내가 잘못 옮겼을 때 잡을 자리가 없다.
    """
    from verify_numbers import check, parse_number, derived_ok

    tmp = Path(tempfile.mkdtemp(prefix="vnum_"))
    problems = []
    try:
        src = tmp / "material.md"
        src.write_text("9월 인상 확률은 49.4%에서 60.4%로 올랐다.\n"
                       "낸드 2분기 매출이 전 분기 대비 70% 증가했다.\n"
                       "자사주 매입 규모는 총 55조 원이다.\n"
                       "비농업 일자리가 16만2000개 늘었다.\n"
                       "SK하이닉스 1,848,000원 / 계좌 9,999,644원\n", encoding="utf-8")

        ok = tmp / "ok.md"
        ok.write_text("- 인상 확률 60.4%, 낸드 +70%, 자사주 55조, 비농업 16만2000개.\n"
                      "- 1주 1,848,000원은 계좌의 18.5%(계산: 1,848,000 ÷ 9,999,644).\n",
                      encoding="utf-8")
        miss = check(ok, [src])
        if miss:
            problems.append(f"맞는 노트를 걸렀다: {[m[1] for m in miss]}")

        bad = tmp / "bad.md"
        bad.write_text("- 인상 확률 64.0%였고 자사주는 5.5조다.\n", encoding="utf-8")
        got = {m[1] for m in check(bad, [src])}
        if "64.0" not in got or "5.5조" not in got:
            problems.append(f"틀린 숫자를 못 잡았다: {got}")

        # 라벨만 붙이고 값이 안 맞으면 통과하면 안 된다.
        if derived_ok("계좌의 99.9%(계산: 1,848,000 ÷ 9,999,644)", 99.9):
            problems.append("가짜 계산 근거가 통과했다")
        if not derived_ok("계좌의 18.5%(계산: 1,848,000 ÷ 9,999,644)", 18.5):
            problems.append("맞는 계산 근거가 거부됐다")

        # 한국어 자릿수 파싱
        for tok, want in (("55조", 55e12), ("16만2000", 162000), ("6조8212억", 6.8212e12),
                          ("1,848,000", 1848000)):
            got_v = parse_number(tok)
            if got_v is None or abs(got_v - want) > 1:
                problems.append(f"{tok} → {got_v} (기대 {want:,.0f})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    status = FAIL if problems else PASS
    results.append((status, "㉗ 노트 숫자를 재료와 대조(전사 검증)", "; ".join(problems)))
    print(f"[{status}] ㉗ 노트 숫자를 재료와 대조(전사 검증)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_number_transcription)


def test_universe_coverage():
    """유니버스 커버리지 집계 — 겹치는 표기를 두 번 세면 안 된다."""
    from universe_review import count_mentions, aliases_for

    problems = []
    text = ("네이버클라우드가 정부 사업자로 선정됐다. 네이버는 새벽배송을 시작한다. "
            "네이버 멤버십은 4,900원이다. 삼성전자와 SK하이닉스가 올랐다.")

    naver = {"ticker": "035420", "name": "NAVER", "aliases": ["네이버", "네이버클라우드"]}
    hits, used = count_mentions(text, naver)
    # 네이버클라우드 1 + 네이버 2 = 3. '네이버클라우드'를 '네이버'로 또 세면 4가 된다.
    if hits != 3:
        problems.append(f"네이버 집계 {hits} (기대 3) — {used}")
    if not any(u.startswith("네이버클라우드") for u in used):
        problems.append(f"긴 표기가 먼저 매칭되지 않았다: {used}")

    # 긴 것부터 정렬돼야 겹침이 안 생긴다
    order = aliases_for(naver)
    if order != sorted(order, key=len, reverse=True):
        problems.append(f"표기가 길이 내림차순이 아니다: {order}")

    # name과 aliases에 같은 값이 있어도 두 번 세지 않는다
    dup = {"ticker": "005930", "name": "삼성전자", "aliases": ["삼성전자"]}
    hits2, _ = count_mentions(text, dup)
    if hits2 != 1:
        problems.append(f"중복 표기를 두 번 셌다: {hits2} (기대 1)")

    # 재료에 없는 종목은 0
    absent = {"ticker": "MSFT", "name": "Microsoft", "aliases": ["마이크로소프트"]}
    if count_mentions(text, absent)[0] != 0:
        problems.append("없는 종목이 잡혔다")

    status = FAIL if problems else PASS
    results.append((status, "㉘ 유니버스 커버리지 집계(겹치는 표기 중복 방지)", "; ".join(problems)))
    print(f"[{status}] ㉘ 유니버스 커버리지 집계(겹치는 표기 중복 방지)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_universe_coverage)


def test_universe_gates():
    """유니버스 자동 편입의 관문 — 인젝션 방어가 여기 걸려 있다.

    워치리스트는 뉴스레터발 인젝션이 주문으로 번지는 유일한 관문이었고, 그 방어는
    '사람이 승인한다'에 기대고 있었다. 무인에서 그 승인을 뺀 이상, 같은 방어를
    **브로커가 주는 사실**로 다시 세워야 한다. 이 검사가 무너지면 방어가 없는 것이다.
    """
    import universe_apply as U

    rules = json.loads((Path(__file__).parent / "config" / "universe_rules.json")
                       .read_text(encoding="utf-8"))
    problems = []

    # 국내: 지수 편입이 핵심 방어 — 뉴스레터는 KOSPI200 편입을 바꿀 수 없다.
    good = {"rprs_mrkt_kor_name": "KOSPI200", "hts_avls": "634000",
            "acml_tr_pbmn": "29445757500", "stck_prpr": "317000",
            "mang_issu_cls_code": "N", "sltr_yn": "N", "invt_caful_yn": "N",
            "mrkt_warn_cls_code": "00"}

    def kr_verdict(patch):
        o = dict(good); o.update(patch)
        r = rules["KR"]
        try:
            U._flags_ok(o, r["forbid_flags"])
            if o["rprs_mrkt_kor_name"] not in r["require_market"]:
                raise U.Reject("market")
            if float(o["hts_avls"]) < r["min_market_cap_100m_krw"]:
                raise U.Reject("mcap")
            if float(o["acml_tr_pbmn"]) < r["min_daily_turnover_krw"]:
                raise U.Reject("turnover")
            return "PASS"
        except U.Reject as e:
            return f"REJECT({e})"

    if kr_verdict({}) != "PASS":
        problems.append(f"정상 종목이 거부됐다: {kr_verdict({})}")
    for patch, label in (({"rprs_mrkt_kor_name": "KOSDAQ"}, "지수 미편입"),
                         ({"mang_issu_cls_code": "Y"}, "관리종목"),
                         ({"sltr_yn": "Y"}, "정리매매"),
                         ({"mrkt_warn_cls_code": "02"}, "투자경고"),
                         ({"invt_caful_yn": "Y"}, "투자주의환기"),
                         ({"hts_avls": "500"}, "시가총액 미달"),
                         ({"acml_tr_pbmn": "1000000"}, "거래대금 미달")):
        if kr_verdict(patch) == "PASS":
            problems.append(f"{label}인데 통과했다")

    # ★ 2026-09-14 사용자 결정: **선물형만** 이름으로 배제. 레버리지·인버스는 통과해야 한다
    #   (대신 limits.json의 레버리지 규칙이 붙는다 — (53)이 그것을 잰다).
    pats = rules["forbidden_name_patterns"]
    for nm in ("KODEX WTI원유선물(H)", "KODEX 200선물인버스2X", "United States Oil Futures"):
        if not U.forbidden_name(nm, "000000", pats):
            problems.append(f"선물형을 못 걸렀다: {nm}")
    for nm in ("KODEX 레버리지", "삼성 곱버스", "Direxion Daily Semiconductor Bull 3X Shares"):
        if U.forbidden_name(nm, "000000", pats):
            problems.append(f"레버리지·인버스를 막았다(정책 위반): {nm}")
    if U.forbidden_name("삼성생명", "032830", pats):
        problems.append("정상 종목이 금지 패턴에 걸렸다")

    # 제외 규칙: 보유 중인 종목은 빼지 않는다 / 사유 코드는 허용 목록에서만
    rr = rules["remove"]
    if not rr.get("never_if_held"):
        problems.append("보유 종목 제외가 허용돼 있다")
    if "premise_falsified" not in rr["allowed_reasons"]:
        problems.append("사유 코드 목록이 비정상")
    if rules["cooling_days"] < 2:
        problems.append(f"냉각이 {rules['cooling_days']}일 — 한 번의 인젝션이 바로 반영된다")
    # 냉각은 **날짜**로 세야 한다. run으로 세면 같은 날 국내·미국 두 번 도는 것만으로 끝난다.
    src = (Path(__file__).parent / "universe_apply.py").read_text(encoding="utf-8")
    if 'pending.get(key, {}).get("dates"' not in src:
        problems.append("냉각을 날짜가 아니라 run으로 세고 있다")
    if "session_date" not in src:
        problems.append("universe_apply가 세션 날짜를 추적하지 않는다")
    if rules["min_universe"]["KR"] < 1 or rules["max_adds_per_run"] > 5:
        problems.append("유니버스 상·하한이 비정상")

    status = FAIL if problems else PASS
    results.append((status, "㉙ 유니버스 자동 편입 관문(인젝션 방어)", "; ".join(problems)))
    print(f"[{status}] ㉙ 유니버스 자동 편입 관문(인젝션 방어)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_universe_gates)


def test_new_caps():
    """비율 기반 일일 상한 + 투자 비중 상한.

    일일 상한을 비율로 두는 이유: 사용자가 정하는 유일한 값이 '계좌에 넣는 금액'이라,
    절대 금액으로 두면 입금할 때마다 사람이 다시 정해야 한다.
    투자 비중 상한은 안전장치가 아니라 시나리오 대응 여력이다 — 전액 투자면
    "CPI 하회 시 추가"가 물리적으로 불가능해진다.
    """
    problems = []
    balance = {"currency": "KRW", "cash": 10_000_000, "positions": []}

    def buys(n, price):
        return [{"ticker": f"00000{i}", "action": "BUY", "qty": 1, "price": price,
                 "source": "llm"} for i in range(n)]

    # (2026-09-23) 일일 상한은 폐지됐다 — 키가 남아 있어도 무시되고, 남은 천장은 이 시장 현금이다.
    lim = {"daily_max_order_pct_of_equity": 20.0, "daily_max_order_amount": {"KRW": 1_000_000},
           "daily_max_orders": 1, "portfolio_daily_loss_halt_pct": None}
    final, rej = rg.apply_run_limits(buys(3, 900_000), lim, balance, 0.0, "XX")
    if len(final) != 3:
        problems.append(f"상한을 폐지했는데 {len(final)}건만 통과(기대 3 — 현금 1,000만 안 270만)")
    if any("일일" in r["why"] for r in rej):
        problems.append(f"일일 상한 거부가 아직 나온다: {[r['why'][:40] for r in rej]}")

    # (2026-09-22) 투자 비중 상한 70%는 지웠다 — 비율은 allocation 판단이 정한다. 이미 60% 보유여도 현금 안에서 산다.
    held = {"currency": "KRW", "cash": 4_000_000,
            "positions": [{"ticker": "005930", "eval_amt": 6_000_000, "qty": 1}]}
    lim3 = {"daily_max_orders": None, "portfolio_daily_loss_halt_pct": None}
    final3, rej3 = rg.apply_run_limits(buys(3, 800_000), lim3, held, 0.0, "XX")
    if len(final3) != 3:
        problems.append(f"투자 비중 상한이 사라졌는데 {len(final3)}건만 통과 (기대 3 — 현금 400만 안)")

    # 상한이 없으면(=null) 통과해야 한다 — 껐다 켜는 프로필이 깨지지 않게
    lim4 = {"daily_max_orders": None, "portfolio_daily_loss_halt_pct": None,
            "cash_floor_pct": None}
    final4, _ = rg.apply_run_limits(buys(3, 900_000), lim4, balance, 0.0, "XX")
    if len(final4) != 3:
        problems.append(f"상한 전부 null인데 {len(final4)}건만 통과")

    status = FAIL if problems else PASS
    results.append((status, "㉚ 일일 상한·투자비중 상한 폐지 — 남은 천장은 현금", "; ".join(problems)))
    print(f"[{status}] ㉚ 일일 상한·투자비중 상한 폐지 — 남은 천장은 현금"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_new_caps)


def test_gate_token_precision():
    """무인 드라이버가 인정하는 게이트 토큰 — **어느 단계의 PASS든**이 아니라 그 단계의 것.

    예전 구현은 `glob("trade-run*")` + `"PASS" in read_text()`였다. 그래서 1단
    `capture` PASS만 디스크에 있어도 실주문이 나갈 수 있었고, 기권 run은
    `if proposals and …` 조건 때문에 게이트를 통째로 우회했다. 이 테스트가 지키는
    명제: **토큰의 cp가 그 자리에 필요한 cp와 같아야 하고, 이번 run의 것이어야 한다.**
    """
    problems = []
    import run_auto as ra

    with tempfile.TemporaryDirectory() as td:
        # 폴더는 **함수로** 갈아끼운다 — 환경변수로 흉내 내면 "엔진이 어느 변수를 읽는가"에
        # 테스트가 묶이고, 실제로 그렇게 묶여 있다가 `TMPDIR`/`TEMP` 불일치를 못 잡았다.
        old = ra.stepgate_dir
        gd = Path(td) / "claude_stepgate"
        gd.mkdir(parents=True, exist_ok=True)
        ra.stepgate_dir = lambda: gd
        led = gd / "trade-run__wf.ledger"
        since = datetime.now(KST) - timedelta(minutes=5)

        def put(cp, verdict="PASS"):
            led.write_text(json.dumps({"cp": cp, "verdict": verdict, "ts": "now"}),
                           encoding="utf-8")

        try:
            # ① capture PASS로는 dispatch 자리가 열리지 않는다 (실주문 경로)
            put("capture")
            if ra.gate_token(since):
                problems.append("capture PASS를 dispatch/no_trade 토큰으로 인정했다")

            # ② dispatch는 인정하고 cp를 그대로 보고한다
            put("dispatch")
            tok = ra.gate_token(since)
            if tok.get("cp") != "dispatch":
                problems.append(f"dispatch 토큰을 못 읽었다: {tok}")

            # ③ 기권도 토큰이 있어야 한다 — no_trade는 면제가 아니라 다른 cp다
            put("no_trade")
            if ra.gate_token(since).get("cp") != "no_trade":
                problems.append("no_trade 토큰을 인정하지 않았다")
            #    …그러나 그 토큰으로 dispatch 자리를 열 수는 없다
            if ra.gate_token(since).get("cp") == "dispatch":
                problems.append("no_trade 토큰이 dispatch로 읽혔다")

            # ④ verdict가 PASS가 아니면 무시
            put("dispatch", verdict="FAIL")
            if ra.gate_token(since):
                problems.append("FAIL 토큰을 통과로 읽었다")

            # ⑤ 이번 run 이전 토큰은 재사용 불가 (9/3자 토큰이 아직 유효했던 문제)
            put("dispatch")
            future = datetime.now(KST) + timedelta(minutes=5)
            if ra.gate_token(future):
                problems.append("run 시작 이전 토큰을 재사용했다")

            # ⑥ execute 자리는 execute 토큰만 — dispatch로 6단을 닫을 수 없다
            put("dispatch")
            if ra.gate_token(since, cps=("execute",)):
                problems.append("dispatch 토큰으로 execute 체크포인트가 닫혔다")
            put("execute")
            if ra.gate_token(since, cps=("execute",)).get("cp") != "execute":
                problems.append("execute 토큰을 못 읽었다")

            # ⑦ 미기록 집행은 자가정지 카운터에 들어간다(게이트 정상 동작과 구분)
            if ra.Outcome.FAIL_RECORD in ra.NOT_AN_INCIDENT:
                problems.append("FAIL_RECORD가 사고에서 면제돼 있다")

            # ⑧ 위임 세션 도구 목록에 --send가 없다
            for name in ("ANALYSIS_TOOLS", "EXECUTE_TOOLS"):
                if "--send" in getattr(ra, name):
                    problems.append(f"{name}에 --send가 들어 있다")

            # ⑨ 단계 명세 경로가 살아 있다 — 죽으면 위임 세션이 규칙 없이 일한다
            if not (ra.SKILL_REF / "stage6_execute.md").is_file():
                problems.append(f"SKILL_REF가 죽었다: {ra.SKILL_REF}")
        finally:
            ra.stepgate_dir = old

    status = FAIL if problems else PASS
    results.append((status, "㉛ 게이트 토큰 정밀도(cp·시각·--send)", "; ".join(problems)))
    print(f"[{status}] ㉛ 게이트 토큰 정밀도(cp·시각·--send)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_gate_token_precision)


def test_no_trade_streak():
    """연속 기권을 **세는 코드가 있는가.**

    `limits.no_trade_max_consecutive_below_floor: 2`를 넣어놓고 그 값을 읽는
    파이썬이 0곳이었다 — 규칙이 문서에만 있으면 집행되지 않는다.
    """
    problems = []
    import run_auto as ra

    with testkit.db_case():                       # run 기록은 저장소 op_run_log — 빈 임시 저장소에 심는다
        try:
            rows = [
                {"market": "kr", "no_trade": True},
                {"market": "kr", "no_trade": False},   # 여기서 끊긴다
                {"market": "us", "no_trade": True},    # 시장별로 따로 센다
                {"market": "kr", "no_trade": True},
                {"market": "kr", "no_trade": True},
            ]
            testkit.seed(logs={"run_log": rows})
            if db.log_rows("run_log") != rows:
                problems.append("심기 실패 — run 기록이 저장소에 그대로 안 보인다")
            n = ra.consecutive_no_trade("kr")
            if n != 2:
                problems.append(f"kr 연속 기권 {n}회 (기대 2 — 중간 매매에서 끊겨야 한다)")
            # 시장은 세션 주기가 따로 도므로 **그 시장의 행만** 보고 꼬리를 센다.
            # 사이에 낀 다른 시장의 매매가 이쪽 기권 연쇄를 끊어주면 안 된다.
            if ra.consecutive_no_trade("us") != 1:
                problems.append(
                    f"us 연속 기권 {ra.consecutive_no_trade('us')}회 (기대 1 — 자기 시장 행만 센다)")
            if ra.consecutive_no_trade("kr") != 2:
                problems.append("kr 집계가 us 행에 오염됐다")

            # (2026-09-22) 연속 기권 상한은 지웠다 — 기권은 allocation의 현금 명분과 gap 채우기가 판정한다. 카운터는 기록용.
        finally:
            pass

    status = FAIL if problems else PASS
    results.append((status, "㉜ 연속 기권 카운터", "; ".join(problems)))
    print(f"[{status}] ㉜ 연속 기권 카운터"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_no_trade_streak)


def test_stepgate_pat_resolve():
    """게이트 기준의 `pat` 자리표시자가 치환되는가.

    `file`만 치환하고 `pat`을 리터럴로 두면 `커버리지 [0-9]+/{roster}` 같은
    **워커 제출값과 대조하는 기준**이 영원히 0건이 되고, 그러면 "안 맞아서 막혔다"와
    "기준이 죽어 있다"가 구분되지 않는다. 실제로 그렇게 죽어 있었고, 그 결과
    통과한 시험을 내가 "그 기준이 작동한 증거"로 잘못 보고했다.
    """
    problems = []
    sys.path.insert(0, str(STEPGATE_DIR))
    try:
        import stepgate as sg
    except ImportError as ex:
        results.append((FAIL, "㉝ stepgate pat 치환", f"import 실패: {ex}"))
        print(f"[{FAIL}] ㉝ stepgate pat 치환 — import 실패: {ex}")
        return

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "note.md"
        f.write_text("커버리지 26/26 · 유니버스 26종목\n", encoding="utf-8")
        args = {"note": str(f), "roster": "26"}

        ok, why = sg.run_criterion(
            {"check": "contains", "file": "{note}", "pat": r"커버리지 [0-9]+/{roster}"}, args)
        if not ok:
            problems.append(f"치환된 pat이 히트하지 않았다: {why}")

        # 대조가 실제로 값을 본다 — 틀린 값이면 막혀야 한다(항상 통과하는 기준 금지)
        bad, _ = sg.run_criterion(
            {"check": "contains", "file": "{note}", "pat": r"커버리지 [0-9]+/{roster}"},
            {"note": str(f), "roster": "99"})
        if bad:
            problems.append("roster=99인데도 통과했다 — 기준이 값을 안 본다")

        # `|`는 이스케이프해야 표 구분자로 읽힌다(정규식 대안으로 읽혀 158건이 나온 적 있다)
        t = Path(td) / "tbl.md"
        t.write_text("| a | b |\n| 1 | 2 |\n", encoding="utf-8")
        n_alt, _ = sg.run_criterion(
            {"check": "contains", "file": str(t), "pat": r"\| [0-9] \|", "min": 1}, {})
        if not n_alt:
            problems.append("이스케이프한 표 구분자 패턴이 히트하지 않았다")

    status = FAIL if problems else PASS
    results.append((status, "㉝ stepgate pat 치환", "; ".join(problems)))
    print(f"[{status}] ㉝ stepgate pat 치환"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_stepgate_pat_resolve)


def test_fill_verdicts():
    """체결 판정 — **거부 · 미확인 · 미체결은 서로 다른 사건이다.**

    행선지가 다르기 때문에 구분해야 한다: 미체결은 걸려 있어서 기다리고, 거부는
    접수된 것이 없어서 지금 재판정하고, 미확인은 판정 자체를 못 한 것이다.
    *실사례(2026-09-10): MSFT가 `[40580000] 모의투자 장종료`로 거부됐는데 보유가 안
    늘었다는 이유로 '미체결'로 판정·보고됐다.*
    """
    problems = []
    import execute as ex

    orders = [{"ticker": "A", "action": "BUY", "qty": 4, "price": 100.0, "name": "가"},
              {"ticker": "B", "action": "BUY", "qty": 2, "price": 200.0, "name": "나"},
              {"ticker": "C", "action": "BUY", "qty": 3, "price": 300.0, "name": "다"},
              {"ticker": "D", "action": "BUY", "qty": 5, "price": 400.0, "name": "라"}]
    before = {"A": 0, "B": 0, "C": 1, "D": 0}
    after = {"currency": "KRW", "positions": [{"ticker": "A", "qty": 4},
                                              {"ticker": "C", "qty": 2}]}
    statuses = {"A": "SENT", "B": "SENT", "C": "SENT", "D": "FAILED"}

    unfilled, fills = ex._report_fills(orders, before, after, statuses)
    want = {"A": "FILLED", "B": "UNFILLED", "C": "PARTIAL", "D": "REJECTED"}
    for t, v in want.items():
        got = fills.get(t, {}).get("verdict")
        if got != v:
            problems.append(f"{t}: 판정 {got} (기대 {v})")
    # 거부는 '걸려 있는 주문'이 아니므로 미체결 건수에 넣지 않는다
    if unfilled != 2:
        problems.append(f"미체결·부분체결 {unfilled}건 (기대 2 — 거부는 안 센다)")
    if "기다리지 말" not in (fills.get("D", {}).get("note") or ""):
        problems.append("거부 건에 '기다리지 말 것' 안내가 없다")

    # 잔고 조회 실패는 **'보유 0'이 아니라 '모른다'** — 판정 0건이 나와야 한다
    n_none, f_none = ex._report_fills(orders, None, after, statuses)
    if f_none:
        problems.append(f"기준선 None인데 판정을 냈다: {len(f_none)}건")
    # …그리고 그 상태는 성공이 아니다(S5: 종료코드가 0이면 호출자가 성공으로 읽는다)
    if not (bool(orders) and not f_none):
        problems.append("판정 0건을 '미확인'으로 식별하지 못한다")

    # 미국 기준선을 빈 dict로 두면 보유분이 전부 '새로 늘어난 것'이 된다(S4)
    src = Path(ex.__file__).read_text(encoding="utf-8")
    if "if market == \"KR\" else {}" in src:
        problems.append("미국 체결 기준선이 여전히 빈 dict다")
    if "overseas_balance()" not in src:
        problems.append("미국 기준선을 overseas_balance로 읽지 않는다")

    status = FAIL if problems else PASS
    results.append((status, "㉞ 체결 판정(거부·미확인·미체결 구분)", "; ".join(problems)))
    print(f"[{status}] ㉞ 체결 판정(거부·미확인·미체결 구분)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_fill_verdicts)


def test_history_preserved():
    """재실행이 **손으로 채운 것과 앞 run의 근거를 지우지 않는가.**

    `tools_*.md`는 이미 쓰인 노트가 "이 run 숫자의 유일한 출처"로 지목하는 파일이고,
    `carry_*.md`에는 미체결분 행선지·대응 채점·중단재개를 사람이 적는다. 노트에는
    덮어쓰기 가드가 있었는데 이 둘에는 없어서, 장중 재개 한 번에 앞 판단이 소급 소멸했다.
    """
    problems = []
    import stage

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "carry_260910_kr.md"
        p.write_text("원본 — 미체결분 행선지: MSFT 대기열\n", encoding="utf-8")
        stage.write_preserving(p, "새 뼈대 — 빈 칸\n", "이어받기 파일")

        if p.read_text(encoding="utf-8") != "새 뼈대 — 빈 칸\n":
            problems.append("새 내용이 안 쓰였다")
        aside = list((Path(td) / "_superseded").glob("carry_260910_kr_*.md"))
        if len(aside) != 1:
            problems.append(f"보존본 {len(aside)}개 (기대 1)")
        elif "MSFT 대기열" not in aside[0].read_text(encoding="utf-8"):
            problems.append("보존본에 원본 내용이 없다")
        # 보존본이 부모 폴더의 `carry_*.md` 글롭에 걸리면 게이트가 낡은 파일을 센다
        if len(list(Path(td).glob("carry_*.md"))) != 1:
            problems.append("보존본이 부모 폴더 글롭에 잡힌다")
        # 파일이 없던 경우엔 그냥 쓴다(빈 _superseded를 만들지 않는다)
        q = Path(td) / "tools_260910_kr.md"
        stage.write_preserving(q, "첫 캡처\n", "도구 출력 캡처")
        if not q.exists() or list((Path(td) / "_superseded").glob("tools_*")):
            problems.append("첫 쓰기인데 보존본을 만들었다")

        # 두 번 더 치워도 서로 안 덮는다(같은 초에 겹치면 이력이 사라진다)
        for txt in ("2번째\n", "3번째\n"):
            stage.write_preserving(q, txt, "도구 출력 캡처")
        n = len(list((Path(td) / "_superseded").glob("tools_*")))
        if n != 2:
            problems.append(f"tools 보존본 {n}개 (기대 2 — 보존본끼리 덮었다)")

    # 호출 지점이 실제로 헬퍼를 쓰는가 — 헬퍼만 있고 안 쓰면 아무것도 안 지킨다
    src = Path(stage.__file__).read_text(encoding="utf-8")
    if "evid.write_text(" in src or "out.write_text(" in src:
        problems.append("tools_*/carry_* 쓰기가 아직 무가드 write_text다")

    status = FAIL if problems else PASS
    results.append((status, "㉟ 재실행이 이력을 덮지 않는다", "; ".join(problems)))
    print(f"[{status}] ㉟ 재실행이 이력을 덮지 않는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_history_preserved)


def test_crosscheck_catches_misses():
    """도구가 지목한 것을 노트가 안 다루면 **각서가 아니라 디스크 대조가** 잡는가.

    이 프로젝트에서 가장 비쌌던 누락은 "자료가 없어서"가 아니라 **"자료를 손에 들고"**
    생겼다. 1단이 깐 §6 표의 상승 1·2위를 4단이 안 보고 섹터 보드만 봤다 —
    종가는 +7.26%·+2.00%였고 둘 다 유니버스 안이었다. 게이트는 그걸 각서로만 물었고
    "우선순위를 지켰는가?"는 지키지 않아도 Y가 나온다.
    """
    import subprocess
    problems = []
    stamp, mkt = "260910", "kr"
    snap = Path(__file__).parent / "data" / f"snapshot_{stamp}_{mkt}.json"
    real = Path(__file__).parent / "analysis" / f"분석노트_{stamp}_{mkt}_v2_1.md"
    if not snap.exists() or not real.exists():
        results.append((PASS, "㊱ 대조가 실제 누락을 잡는다", "재료 없음 — 건너뜀"))
        print(f"[{PASS}] ㊱ 대조가 실제 누락을 잡는다 — 재료 없음, 건너뜀")
        return

    prices = json.loads(snap.read_text(encoding="utf-8"))["prices"]
    top = max(prices.items(), key=lambda kv: kv[1].get("change_pct") or -99)
    top_t, top_name = top[0], top[1]["name"]

    def run_cc(note_path):
        p = subprocess.run([sys.executable, "crosscheck.py", "--market", mkt,
                            "--stamp", stamp, "--note", str(note_path)],
                           cwd=Path(__file__).parent, capture_output=True, text=True)
        return p.returncode, p.stdout

    with tempfile.TemporaryDirectory() as td:
        # 상승 1위를 지운 노트 — 원래 사고를 재현한다
        stripped = real.read_text(encoding="utf-8").replace(top_t, "XXXXXX") \
                                                   .replace(top_name, "○○○")
        blind = Path(td) / "blind.md"
        blind.write_text(stripped, encoding="utf-8")
        rc, out = run_cc(blind)
        if rc == 0:
            problems.append(f"상승 1위 {top_name}를 지웠는데 대조가 통과했다")
        if f"{top_name}({top_t})" not in out or "노트에 이름이 없다" not in out:
            problems.append(f"{top_name}가 누락으로 지목되지 않았다")

        # 원본은 그 항목이 통과해야 한다 — 항상 FAIL이면 검사가 아니다
        rc2, out2 = run_cc(real)
        line = [l for l in out2.splitlines() if f"{top_name}({top_t})" in l]
        if not line or "**✗**" in line[0]:
            problems.append(f"원본 노트인데 {top_name} 항목이 실패로 나온다: {line[:1]}")
        if "[대조] 검사" not in out2:
            problems.append("게이트가 읽을 요약 줄([대조] 검사 N건 · 실패 K건)이 없다")

    status = FAIL if problems else PASS
    results.append((status, "㊱ 대조가 실제 누락을 잡는다", "; ".join(problems)))
    print(f"[{status}] ㊱ 대조가 실제 누락을 잡는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_crosscheck_catches_misses)


def test_scenario_ledger():
    """시나리오 id가 **run을 넘어 살아남는가**, 그리고 회피가 세어지는가.

    시그널의 `scenarios` id는 매 run 재사용된다(실측: 9/8 `A~E` → 9/9 `A~F` →
    9/10 오전 `A~G` → 오후 `S1~S8`). 어제의 `A`와 오늘의 `A`가 다른 조건을 가리키므로
    **"기다리기로 한 것이 실현됐는데 대응했는가"를 셀 방법이 없었다** —
    회피 감사 3대 지표 중 하나가 계산 불가였다.
    """
    problems = []
    import scenarios as sc

    # 시나리오 원장은 저장소 jd_scenario — 빈 임시 저장소에서 승격·판정한다(깨진 파일 보존 검사는 파일과 함께 사라졌다)
    with tempfile.TemporaryDirectory() as td, testkit.db_case(Path(td) / "db"):
        try:
            if db.scenarios_all():
                problems.append("빈 임시 저장소에 시나리오 행이 있다(격리 실패)")
            sigdir = Path(td) / "signals"
            sigdir.mkdir()

            def write_sig(name, rows):
                (sigdir / name).write_text(json.dumps({"scenarios": rows}, ensure_ascii=False),
                                           encoding="utf-8")
                return sigdir / name

            # 어제 run: id A·B / 오늘 run: 같은 조건인데 id가 S1로 바뀌고 새 조건 하나
            f1 = write_sig("signal_260909_kr.json",
                           [{"id": "A", "condition": "CPI 하회", "response": "삼성전자 진입"},
                            {"id": "B", "condition": "CPI 상회", "response": "보류"}])
            f2 = write_sig("signal_260910_kr.json",
                           [{"id": "S1", "condition": "CPI 하회", "response": "삼성전자 진입"},
                            {"id": "S2", "condition": "한화오션 본계약", "response": "추가"}])
            sc.cmd_promote(argparse_ns(signal=str(f1)))
            sc.cmd_promote(argparse_ns(signal=str(f2)))

            d = sc.load()
            ids = [r["id"] for r in d["scenarios"]]
            if len(ids) != 3:
                problems.append(f"시나리오 {len(ids)}건 (기대 3 — 같은 조건이 중복 승격됐다)")
            if len(set(ids)) != len(ids):
                problems.append(f"id가 충돌한다: {ids}")
            merged = [r for r in d["scenarios"] if len(r.get("sources", [])) > 1]
            if len(merged) != 1:
                problems.append("두 run에 걸친 같은 조건이 하나로 묶이지 않았다")
            elif "260909" not in merged[0]["id"]:
                problems.append(f"재등장 조건이 첫 등장의 id를 잃었다: {merged[0]['id']}")

            # 실현됐는데 대응 안 한 건이 감사에 잡히는가
            sc.cmd_judge(argparse_ns(id=ids[0], realized="y", acted="n", note="t"))
            sc.cmd_judge(argparse_ns(id=ids[1], realized="n", acted=None, note="t"))
            d = sc.load()
            r0 = next(r for r in d["scenarios"] if r["id"] == ids[0])
            r1 = next(r for r in d["scenarios"] if r["id"] == ids[1])
            if r0["status"] != "closed" or r0["acted"] is not False:
                problems.append(f"실현+미대응이 기록되지 않았다: {r0['status']}/{r0['acted']}")
            if r1["status"] != "open":
                problems.append("미실현 조건을 닫아버렸다 — 기다리는 것은 판단이다")
            if (db.scenario_get(ids[0]) or {}).get("status") != "closed":
                problems.append("판정이 저장소 행에 안 남았다")
        finally:
            pass

    status = FAIL if problems else PASS
    results.append((status, "㊲ 시나리오 영속 id·회피 집계", "; ".join(problems)))
    print(f"[{status}] ㊲ 시나리오 영속 id·회피 집계"
          + (f" — {'; '.join(problems)}" if problems else ""))


def argparse_ns(**kw):
    import argparse as _a
    return _a.Namespace(**kw)


_safe(test_scenario_ledger)


def test_sessions_ledger():
    """일과 원장이 **반쪽 run과 시각 이탈을 산출물에서 역산**하는가.

    "각 단계가 원장에 한 줄 쓴다"로 만들면 그 쓰기를 잊은 run이 원장에 안 남는데,
    잊는 run이 바로 원장이 잡아야 할 run이다. 그래서 파생으로 만들었다.
    """
    problems = []
    import sessions as ss
    from datetime import date as _date
    if not any((HERE_DIR / "analysis").glob("분석노트_*.md")):  # 공개본: 실제 run 산출물 없음
        results.append((PASS, "㊳ 일과 원장(반쪽 run·슬롯) 역산", "건너뜀 — 실제 run 산출물이 있을 때만 검사"))
        print("[SKIP] ㊳ 일과 원장(반쪽 run·슬롯) 역산 — 실제 run 산출물이 있을 때만 검사")
        return

    # 도달은 **최댓값**이어야 한다 — 첫 공백에서 멈추면 6단 분리 이전 run이 전부
    # "1단에서 끊김"으로 나온다(그때는 2단 산출물이 규격에 없었을 뿐이다).
    # ★ 창은 달력이 아니라 **실측 고정일(9/8)까지** 닿아야 한다 — `scan(6)`으로 두면 9/14부터
    #   9/8이 창 밖으로 밀려 테스트가 시간 때문에 깨진다(코드는 그대로인데).
    span = max(6, (datetime.now(KST).date() - _date(2026, 9, 8)).days + 1)
    rows = ss.scan(span)
    if not rows:
        problems.append(f"최근 {span}일에서 세션 행을 하나도 못 만들었다")
    if any(r["market"] not in ("kr", "us") for r in rows):
        problems.append("시장 값이 이상하다")
    # 스탬프가 주말이어도 거래소 기준일(session_date)은 평일이어야 한다(US 260919 = ET 09-18).
    if any(r.get("off_session") for r in rows):
        problems.append("주말·휴장을 세션으로 셌다: " + str([(r["stamp"], r["market"]) for r in rows if r.get("off_session")]))

    # 실측 고정: 미국 9/8·9/9는 결정까지 못 갔다(반쪽 run)
    half = {(r["session_date"], r["market"]) for r in rows if not r["complete"]
            and r["reached"] != "0 없음"}
    for want in (("2026-09-08", "us"), ("2026-09-09", "us")):
        if want not in half:
            problems.append(f"{want}를 반쪽 run으로 잡지 못했다 — {sorted(half)}")

    # 미국 슬롯은 **같은 날짜** 22:35 KST다(22:35 KST = 09:35 EDT).
    # 하루를 빼면 모든 미국 세션이 하루치 이탈로 잘못 찍힌다.
    v, why = ss.slot_verdict("us", _date(2026, 9, 9),
                             datetime(2026, 9, 9, 22, 40, tzinfo=KST))
    if v != "준수":
        problems.append(f"미국 슬롯 기준이 어긋났다: {v} {why}")
    v2, _ = ss.slot_verdict("kr", _date(2026, 9, 9),
                            datetime(2026, 9, 9, 22, 40, tzinfo=KST))
    if v2 != "이탈":
        problems.append("국내 09:35 슬롯에 밤 22:40이 '준수'로 나온다")

    status = FAIL if problems else PASS
    results.append((status, "㊳ 일과 원장(반쪽 run·슬롯) 역산", "; ".join(problems)))
    print(f"[{status}] ㊳ 일과 원장(반쪽 run·슬롯) 역산"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_sessions_ledger)


def test_form_state_integrity():
    """출제 기록(`.form`)이 **위조·만료·빈 출제**로 게이트를 무력화하지 못하는가.

    극성 회전은 "이번 회차에 어느 표현으로 물었나"가 정확할 때만 의미가 있다. 그 기록을
    아무나 쓸 수 있으면 회전이 장식이 되고, 특히 `asked: []`면 **모든 각서를 조용히
    건너뛰고 PASS**가 났다(빈 집합은 `None`이 아니라 회전 검사도 통과했다).
    낡은 출제도 문제다 — 디스크에 9/7·13:12자 `.form`이 남아 있었다.
    """
    import time as _t
    problems = []
    sys.path.insert(0, str(STEPGATE_DIR))
    import stepgate as sg

    # 게이트 폴더는 **엔진이 정한다** — 이쪽에서 베껴 쓰면 조용히 갈린다.
    import run_auto as ra
    if str(ra.stepgate_dir()) != str(sg.gate_dir()):
        problems.append(f"게이트 폴더가 갈렸다: {ra.stepgate_dir()} vs {sg.gate_dir()}")

    skill, cp, wf = "_selftest", "cp1", "wftest"
    p = Path(sg.form_state_path(skill, cp, wf))
    try:
        sg.write_form_state(skill, cp, wf, ["a", "b"], {"a": 0, "b": 1})
        st, err = sg.read_form_state(skill, cp, wf)
        if err or not st or set(st["asked"]) != {"a", "b"}:
            problems.append(f"정상 출제를 못 읽었다: {err}")

        # ① 서명 없는(손으로 쓴) 출제 — 예전엔 그대로 채점 기준이 됐다
        p.write_text(json.dumps({"asked": [], "variant": {}}), encoding="utf-8")
        st, err = sg.read_form_state(skill, cp, wf)
        if st is not None or not err:
            problems.append("서명 없는 출제 기록을 받아들였다")

        # ② 내용을 고친 출제 — asked를 비워 채점을 건너뛰려는 시도
        sg.write_form_state(skill, cp, wf, ["a", "b"], {"a": 0, "b": 1})
        d = json.loads(p.read_text(encoding="utf-8"))
        d["asked"] = []
        p.write_text(json.dumps(d), encoding="utf-8")
        st, err = sg.read_form_state(skill, cp, wf)
        if st is not None or "서명" not in (err or ""):
            problems.append(f"고친 출제 기록이 통과했다: {err}")

        # ③ 만료
        sg.write_form_state(skill, cp, wf, ["a"], {"a": 0})
        d = json.loads(p.read_text(encoding="utf-8"))
        d["ts"] = int(_t.time()) - sg.FORM_TTL_SEC - 60
        d["sig"] = sg._form_sig(d)              # 서명은 맞게 다시 만든다 — 만료만 시험
        p.write_text(json.dumps(d), encoding="utf-8")
        st, err = sg.read_form_state(skill, cp, wf)
        if st is not None or "만료" not in (err or ""):
            problems.append(f"만료된 출제가 통과했다: {err}")

        # ④ 다른 체크포인트의 출제를 갖다 붙이기
        sg.write_form_state(skill, "cp2", wf, ["a"], {"a": 0})
        other = Path(sg.form_state_path(skill, "cp2", wf))
        p.write_text(other.read_text(encoding="utf-8"), encoding="utf-8")
        st, err = sg.read_form_state(skill, cp, wf)
        if st is not None or not err:
            problems.append("다른 체크포인트의 출제 기록이 통과했다")
        other.unlink(missing_ok=True)
    finally:
        p.unlink(missing_ok=True)

    status = FAIL if problems else PASS
    results.append((status, "㊳-b 출제 기록 위조·만료·빈 출제 차단", "; ".join(problems)))
    print(f"[{status}] ㊳-b 출제 기록 위조·만료·빈 출제 차단"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_form_state_integrity)


def test_audit_sees_md_deliverables():
    """사후 감사가 **`.md` 산출물을 산출물로 세는가.**

    R1(게이트 미호출)·R3(FAIL 무시)·R6(비동기감사 미완료)·R7(해소게이트 누락)은 전부
    `deliverables`가 비면 발동하지 않는다. 그런데 그 목록을 채우는 정규식이
    `xlsx|docx|pptx`만 잡아서, 산출물이 `.md`뿐인 스킬(trade-run·world-study·fact-check)에서는
    **7규칙 중 4개가 조용히 꺼져 있었다.** 넓히면 이번엔 스크래치·백업이 오탐이 되므로
    포함과 제외를 같은 자리에서 시험한다.
    """
    problems = []
    sys.path.insert(0, str(STEPGATE_DIR))
    import stepgate as sg

    include = [
        "/x/life/ai-trading-bot/analysis/분석노트_260910_kr_v2_1.md",
        "/x/life/ai-trading-bot/journal/carry_260910_kr.md",
        "/x/y/deck_v1_0.pptx", "/x/y/report_v1_0.xlsx",
    ]
    exclude = [
        "/x/CLAUDE.md", "/x/y/README.md",                       # 리포 메타
        "/x/.claude/skills/trade-run/SKILL.md",                 # 스킬 파일 자체
        "/x/y/_trade-run_SKILL_backup_260910.md",               # 백업
        "/private/tmp/claude-501/w/scratchpad/note.md",         # 스크래치
        "/x/ai-trading-bot/_raw_sources/기사.md",                # fetch 원문
        "/home/user/.claude/projects/x/memory/foo.md",          # 메모리
        "/x/y/state.json",                                      # 상태 파일
    ]
    for fp in include:
        if not sg.is_deliverable_path(fp):
            problems.append(f"산출물인데 안 셌다: {fp}")
    for fp in exclude:
        if sg.is_deliverable_path(fp):
            problems.append(f"산출물이 아닌데 셌다(오탐): {fp}")
    if "md" not in sg.DELIVERABLE_EXT:
        problems.append("확장자 목록에 md가 없다 — R1·R3·R6·R7이 .md 스킬에서 죽는다")

    status = FAIL if problems else PASS
    results.append((status, "㊳-c 사후 감사가 .md 산출물을 센다", "; ".join(problems)))
    print(f"[{status}] ㊳-c 사후 감사가 .md 산출물을 센다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_audit_sees_md_deliverables)


def test_no_decorative_limits():
    """`limits.json`의 모든 키가 **읽히는가.**

    참조 0건인 한도는 장식이다. 그리고 장식은 죽은 게 아니라 **거짓말**이다 — 제거한
    `invested_band_pct: [50,70]`은 하한을 50이라고 말했지만 집행되는 하한은
    `min_invested_pct: 20`이었다. 문서를 읽은 사람은 틀린 숫자를 믿는다.

    모델이 읽는 키(`position_sizing.by_confidence` 구간표 등)는 스킬 명세가 그 사용을
    명시해야 통과한다 — "누가 읽는지"가 어디에도 없으면 그것도 장식이다.
    """
    problems = []
    here = Path(__file__).parent
    lim = json.loads((here / "config" / "limits.json").read_text(encoding="utf-8"))
    pysrc = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                      for p in here.glob("*.py"))
    skill = here.parent / ".claude" / "skills" / "trade-run"
    docsrc = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                       for p in [skill / "SKILL.md", *skill.glob("references/*.md")]
                       if p.exists())

    def walk(obj, prefix=""):
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.startswith("_"):          # `_note*` = 사람이 읽는 주석
                    continue
                out.append(k)
                out += walk(v, k)
        return out

    for key in sorted(set(walk(lim))):
        if key in pysrc:
            continue
        if key in docsrc:
            continue                            # 모델이 읽는다고 명세가 밝힌 키
        problems.append(f"참조 0건: `{key}` — 파이썬도 스킬 명세도 안 읽는다")

    status = FAIL if problems else PASS
    results.append((status, "㊳-d limits.json에 장식 키 0", "; ".join(problems)))
    print(f"[{status}] ㊳-d limits.json에 장식 키 0"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_no_decorative_limits)


def test_peaks_market_scoped():
    """미국 run이 **국내 고점을 지우지 않는가.**

    `position_peaks.json`이 시장 구분 없는 평평한 dict였고 `set(peaks) - held`로 안 들고
    있는 종목을 지웠다. 두 시장을 번갈아 돌리므로 **매 run 반대편 시장의 고점이 전멸**했다.
    트레일링 스톱이 매일 리셋되는 것이고, `stop_loss_pct: null`이라 잠복해 있었을 뿐이다.
    ★ 저장소(2026-10-17): 고점은 `ex_position_peak`(시장·종목 키) 한 표다 — 구버전 이관·깨진 파일 보존은 파일과 함께 사라졌다
    (이관은 migrate가 한 번 한다). 시장 분리와 '같은 함수로 읽고 쓴다'는 그대로 본다.
    """
    problems = []
    with testkit.db_case():
        testkit.seed(peaks={"KR": {"005930": {"peak_price": 100.0, "peak_at": "t"}},
                            "US": {"MSFT": {"peak_price": 500.0, "peak_at": "t"}}})
        if db.peaks_for_market("KR").get("005930", {}).get("peak_price") != 100.0 \
                or db.peaks_for_market("US").get("MSFT", {}).get("peak_price") != 500.0:
            problems.append(f"심기 실패 — 고점이 저장소에 안 보인다: {db.peaks_all()}")

        # 미국 run이 미국 보유만 갱신 — 국내 칸은 그대로여야 한다
        db.peaks_update("US", [{"ticker": "MSFT", "price": 510.0}])
        if "005930" not in db.peaks_for_market("KR"):
            problems.append("★ 미국 run이 국내 고점을 지웠다(원래 버그)")
        if db.peaks_for_market("US").get("MSFT", {}).get("peak_price") != 510.0:
            problems.append("미국 고점이 갱신되지 않았다")
        # 내려간 시세는 고점을 낮추지 않는다
        db.peaks_update("US", [{"ticker": "MSFT", "price": 490.0}])
        if db.peaks_for_market("US").get("MSFT", {}).get("peak_price") != 510.0:
            problems.append("내려간 시세가 고점을 낮췄다")
        # 국내 run이 판 종목은 국내 안에서만 지워진다
        db.peaks_update("KR", [])
        if db.peaks_for_market("KR"):
            problems.append("국내 보유 0인데 국내 고점이 남았다")
        if "MSFT" not in db.peaks_for_market("US"):
            problems.append("★ 국내 run이 미국 고점을 지웠다")

    # 쓰는 쪽(journal)과 읽는 쪽(risk_guard)이 같은 저장소 함수를 쓰는가 — 복제하면 갈린다. 옛 파일 모듈(peaks.py)은 안 쓴다.
    import journal as jn
    src = Path(rg.__file__).read_text(encoding="utf-8")
    jsrc = Path(jn.__file__).read_text(encoding="utf-8")
    if "db.peaks_for_market" not in src:
        problems.append("risk_guard가 저장소 함수로 고점을 읽지 않는다")
    if "db.peaks_update" not in jsrc:
        problems.append("journal이 저장소 함수로 고점을 쓰지 않는다")
    for nm, txt in (("risk_guard", src), ("journal", jsrc)):
        if re.search(r"^\s*(import peaks|from peaks )", txt, re.M):
            problems.append(f"{nm}가 아직 옛 고점 파일 모듈(peaks.py)을 쓴다")

    status = FAIL if problems else PASS
    results.append((status, "㊴ 고점 원장이 시장별로 분리", "; ".join(problems)))
    print(f"[{status}] ㊴ 고점 원장이 시장별로 분리"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_peaks_market_scoped)


def test_accum_files_not_reset():
    """누적 기록이 **기본값으로 갈음되어 이력을 지우지 않는가.**

    가장 비싼 자리는 벤치마크 기준점이다 — 못 읽어서 `{}`가 되면 `key not in base`가
    참이 되어 **기준점이 오늘로 재설정**되고 누적·초과수익이 0이 된다. 이 프로젝트의
    성공 지표(③벤치마크 병기 기록의 완전성)를 파괴한다.
    ★ 저장소(2026-10-17): 기준점·커버리지·유니버스는 저장소 행이다 — '깨진 파일 → 보존'은 파일과 함께 사라졌고,
    '있으면 다시 박지 않는다'·'못 읽으면 빈 이력으로 잇지 않는다'·'규모가 무너진 유니버스를 쓰지 않는다'를 본다.
    """
    problems = []
    import journal as jn
    import universe_review as ur
    import universe_apply as ua

    # ① 벤치마크 기준점: 최초 1회만 — 행이 있으면 다시 박지 않는다(코드 계약)
    jsrc = Path(jn.__file__).read_text(encoding="utf-8")
    if "db.benchmark_get(" not in jsrc or "if not b and" not in jsrc:
        problems.append("기준점을 '없을 때만' 박는 분기가 없다 — 기준점이 재설정될 수 있다")

    with testkit.db_case():
        # ② 커버리지: 저장소 행을 그대로 읽고, 읽었다고 답한다(빈 이력으로 갈음하지 않는다)
        cov0 = {"KR:005930": {"absent_runs": 2, "last_seen": "260915"}}
        testkit.seed(coverage=cov0)
        cov, cov_ok = ur.load_coverage()
        if cov != cov0 or not cov_ok:
            problems.append(f"커버리지 이력을 그대로 못 읽었다 — {cov} ok={cov_ok}")

        # ③ 유니버스: 규모 붕괴 가드 — 막으면 저장소를 건드리지 않는다
        before = {"schema_version": "1.0", "KR": [{"ticker": "a"}, {"ticker": "b"}], "benchmarks": {}}
        testkit.seed(watchlist=before)
        if [r.get("ticker") for r in db.companies("KR")] != ["a", "b"]:
            problems.append(f"심기 실패 — 유니버스 {[r.get('ticker') for r in db.companies('KR')]}")
        shrunk = {"KR": [{"ticker": "a"}]}                       # benchmarks·schema 유실
        if ua._write_watchlist_guarded(shrunk, before, 0):
            problems.append("★ 규모가 무너진 유니버스를 그대로 썼다")
        if [r.get("ticker") for r in db.companies("KR")] != ["a", "b"]:
            problems.append("가드가 막았는데 저장소 유니버스가 바뀌었다")
        keep = {"schema_version": "1.0", "KR": [{"ticker": "a"}], "benchmarks": {}}
        if not ua._write_watchlist_guarded(keep, before, 1):
            problems.append("정상 제거 1건인데 막았다(항상 막으면 가드가 아니다)")
        elif [r.get("ticker") for r in db.companies("KR")] != ["a"]:
            problems.append("정상 제거가 저장소 유니버스에 반영되지 않았다")

    status = FAIL if problems else PASS
    results.append((status, "㊵ 깨진 누적 파일이 이력을 지우지 않는다", "; ".join(problems)))
    print(f"[{status}] ㊵ 깨진 누적 파일이 이력을 지우지 않는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_accum_files_not_reset)


def test_failure_is_not_absence():
    """**실패가 '데이터 없음'으로 기록되지 않는가.**

    같은 계열의 결함이 여러 스크립트에 흩어져 있었다: 뉴스레터 fetch 실패를
    "수집하지 않았다"로, DART 전 종목 실패를 "공시 없음"으로, 시세를 못 받은 손절 트리거를
    "사람이 판정할 조건"으로, 이력 못 받은 섹터를 **행 삭제**로 기록했다.
    """
    problems = []
    import dart_feed as df
    import theses as th

    # ① DART — 전 종목 실패는 'ok/0건'이 아니라 error다
    md = df.to_markdown({"status": "ok", "filings": [], "errors": {"a": "x"},
                         "asked": 3, "skipped_reason": ""})
    if "조회 실패" not in md or "확인 안 됨" not in md:
        problems.append(f"일부 실패인데 '공시 없음'으로 적었다: {md[:90]}")
    md2 = df.to_markdown({"status": "ok", "filings": [], "errors": {},
                          "asked": 3, "skipped_reason": ""})
    if "공시 없음" not in md2:
        problems.append("진짜 0건인데 실패처럼 적었다(항상 경고면 신호가 아니다)")

    # ② 논지 트리거 — 값이 없어서 못 잰 것과 사람이 볼 조건을 구분한다
    why = {}
    v = th.eval_cond("price <= 100", {"price": None}, why)
    if v is not None or why.get("kind") != "missing_value":
        problems.append(f"값 부재를 구분하지 못한다: {v} {why}")
    why2 = {}
    v2 = th.eval_cond("CPI가 하회하면", {"price": 1}, why2)
    if v2 is not None or why2.get("kind") != "unparsed":
        problems.append(f"기계 판정 불가 조건을 구분하지 못한다: {v2} {why2}")
    why3 = {}
    if th.eval_cond("price <= 100", {"price": 90}, why3) is not True or why3:
        problems.append("정상 판정이 깨졌다")

    # ③ 미국 섹터 보드 — 못 받은 행을 지우지 않는다
    src = Path(__file__).parent / "market_map.py"
    t = src.read_text(encoding="utf-8")
    if "if len(hist) < 2:\n            continue" in t:
        problems.append("★ 이력 못 받은 섹터를 여전히 행에서 지운다")
    if '"error": err' not in t:
        problems.append("미수신 사유를 행에 남기지 않는다")

    # ④ 뉴스레터 — 실패 사유가 재료에 실린다
    ing = (Path(__file__).parent / "ingest.py").read_text(encoding="utf-8")
    if "수집을 시도했으나 실패했다" not in ing:
        problems.append("뉴스레터 실패를 '수집하지 않았다'와 구분하지 않는다")
    if "_preserve(snap_path" not in ing:
        problems.append("같은 날 재실행이 직전 스냅샷을 보존하지 않는다")

    status = FAIL if problems else PASS
    results.append((status, "㊶ 실패를 '없음'으로 기록하지 않는다", "; ".join(problems)))
    print(f"[{status}] ㊶ 실패를 '없음'으로 기록하지 않는다"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_failure_is_not_absence)


def test_no_orphan_state():
    """상태 파일마다 **읽는 코드가 있는가**, 그리고 회피 3지표가 계산되는가.

    쓰기만 하고 아무도 안 읽는 파일은 "기록하고 있다"는 착각만 준다. 실측으로
    `sector_history.jsonl`(적재만·독자 0 — 추세 계산이 목적인데 계산이 없었다)와
    `carried_runs`(읽기 2곳·쓰기 0곳 — 3run 이월 강등이 구조적으로 불가)가 그랬다.
    """
    problems = []
    here = Path(__file__).parent
    pysrc = {p.name: p.read_text(encoding="utf-8", errors="ignore")
             for p in here.glob("*.py")}
    blob = "\n".join(v for k, v in pysrc.items() if k != Path(__file__).name)

    for f in ("reviews.jsonl", "universe_log.jsonl", "sector_watch.jsonl",
              "equity_curve.jsonl", "bridge_log.jsonl", "sessions.jsonl",
              "scenarios.json", "sector_history.jsonl", "position_peaks.json",
              "run_log.jsonl"):
        if f not in blob:
            problems.append(f"참조 0건: {f} — 쓰기만 하는 파일이면 지워라")

    # `sector_history.jsonl`은 **읽는** 코드가 있어야 한다(적재만으로는 죽은 파일이다)
    mm = pysrc.get("market_map.py", "")
    if "def history_since" not in mm:
        problems.append("sector_history를 읽는 코드가 없다 — 적재만 하면 추세를 못 낸다")
    if '"close": r.get("close")' not in mm:
        problems.append("이력에 지수 레벨을 안 남긴다 — 관찰일 기준 수익을 계산할 수 없다")
    # `carried_runs`는 파생으로 채워져야 한다(쓰기를 추가하면 또 잊힌다)
    if "carried_runs\"] = n" not in mm.replace("'", '"'):
        problems.append("carried_runs를 파생하지 않는다 — 읽는 곳만 있고 값이 안 채워진다")

    # 회피 감사 3지표
    import journal as jn
    block = "\n".join(jn._avoidance_block(7))
    for k in ("기권율", "기다린 비용", "시나리오 실현↔대응"):
        if k not in block:
            problems.append(f"주간 리포트에 '{k}' 지표가 없다")
    if "기권율** 0/0" in block or "기권율** —" in block:
        problems.append("완주 run 0건을 '기권율 0'처럼 적는다 — 둘은 다른 사실이다")

    status = FAIL if problems else PASS
    results.append((status, "㊷ 고아 상태 0 · 회피 3지표 계산", "; ".join(problems)))
    print(f"[{status}] ㊷ 고아 상태 0 · 회피 3지표 계산"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_no_orphan_state)


def test_generator_matches_checker():
    """**1단이 만든 산출물이 게이트의 구조 기준을 실제로 통과하는가.**

    세 번 반복된 버그다 — 만드는 쪽과 검사하는 쪽이 같은 문자열 계약을 각자 하드코딩하고,
    한쪽만 바꿔서 깨진다: ① `gate_dir`(엔진은 TEMP, 내 코드는 TMPDIR) ② 스탬프 형식
    (`<!-- ✓ §N -->`에 시각을 끼워 넣어 `capture`가 5번 FAIL) ③ §6 유니버스 요약 줄
    (영어 run이 다듬어 써서 `note`가 막혔고, 어느 기준인지 몰라 200번 추측했다).

    그래서 **`stage.py`가 만든 것만으로 합성 노트를 만들어 루브릭 기준을 실제로 돌린다.**
    통과하는 기준의 **집합**을 기준선으로 못 박으므로, 생성 문구나 스탬프 형식을 또 바꾸면
    **그 자리에서** 이 테스트가 깨지고 어느 인덱스인지 알려준다.

    ★ 패턴 본문은 출력하지 않는다 — 인덱스와 개수만 본다(봉인 유지).
    """
    import json as _json
    problems = []
    here = Path(__file__).parent
    sys.path.insert(0, str(here))
    sys.path.insert(0, str(STEPGATE_DIR))
    import stage
    import stepgate as sg
    rub = (STEPGATE_DIR / "rubrics" / "trade-run.json")
    rb = _json.loads(rub.read_text(encoding="utf-8"))

    # 생성물만으로 통과해야 하는 기준 — 2026-09-11 측정. 줄어들면 계약이 깨진 것이다.
    # 2026-10-13: note 게이트에서 읽는 사람용 기준(절 스탬프 12개 · 심층·③④·parent·링크·바이트·전사 검증)을 rubric-drop crit=로 뺐다 —
    #   스탬프 5~16이 빠지고 23·38·39가 8·19·20으로 당겨졌다(같은 기준 — 앞에서 15개·19개가 빠짐). 생성 문구 계약은 그대로다.
    BASELINE = {"capture": {0, 3, 4, 7},
                "note": {2, 4, 8, 19, 20}}

    board = ("| 섹터 | 오늘 |\n|---|---|\n| 기술 | +1.00% |\n\n"
             + stage.GEN_NOTICE + "\n" + stage.gen_coverage_line(3, 15, ["금융", "소재"]))
    uni = ("| 종목 | 섹터 |\n|---|---|\n| `AAPL` Apple | 기술 |\n\n"
           + stage.GEN_NOTICE + "\n" + stage.gen_universe_line(17))
    note = stage.note_skeleton("us", "260911",
                               Path("data/run_evidence/tools_260911_us.md"), board, uni)
    note += "\n" + "\n".join(stage.stamp(k) for _, k, _ in stage.SECTIONS)

    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "분석노트_260911_us_v1_0.md"
        f.write_text(note, encoding="utf-8")
        args = {"deliverable": str(f), "note": str(f),
                "universe": "17", "roster": "15", "today": "2026-09-11"}
        for cp, base in BASELINE.items():
            got = set()
            for i, c in enumerate(rb[cp].get("criteria", [])):
                try:
                    ok, _msg = sg.run_criterion(c, args)
                except Exception:                         # noqa: BLE001
                    ok = False
                if ok:
                    got.add(i)
            lost = sorted(base - got)
            if lost:
                problems.append(
                    f"★ {cp} 기준 {lost}이(가) 더는 생성물과 맞지 않는다 — "
                    f"`stage.py`의 생성 문구·스탬프 형식을 바꿨다면 루브릭도 같이 봐야 한다")
            gained = sorted(got - base)
            if gained:
                problems.append(f"{cp} 기준 {gained}이(가) 새로 통과한다 — "
                                f"의도한 것이면 BASELINE을 갱신하라")

    status = FAIL if problems else PASS
    results.append((status, "㊸ 생성자↔검사자 계약 유지", "; ".join(problems)))
    print(f"[{status}] ㊸ 생성자↔검사자 계약 유지"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_generator_matches_checker)


def test_stamp_contract():
    """스탬프가 **레거시 `✓` 형태를 그대로 유지**하면서 시각도 남기는가.

    `<!-- ✓ §N -->`은 게이트가 글자 그대로 찾는 계약이다. 2026-09-10에 여기에 시각을
    끼워 넣었다가 `capture`가 5번 FAIL했고 노트는 스탬프를 두 벌 넣어 우회해야 했다.
    """
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import facts as sl            # 완료·작성 시각 정규식 — 옛 srcledger와 같은 정의(facts.RX_DONE·RX_WRITTEN)
    import stage

    for key in ("머리말", "§0", "§12", "작업기록-capture"):
        s = stage.stamp(key)
        if f"<!-- ✓ {key} -->" not in s:
            problems.append(f"{key}: 레거시 ✓ 형태가 깨졌다 — {s!r}")
        if not sl.RX_WRITTEN.search(s):
            problems.append(f"{key}: 작성 시각 주석이 없다 — {s!r}")
        # ✓ 줄 안에 시각이 섞이면 안 된다(그게 9/10의 실패다)
        first = s.splitlines()[0]
        if "written" in first:
            problems.append(f"{key}: ✓ 줄 안에 시각이 들어갔다 — {first!r}")

    # facts(옛 srcledger)가 두 형태를 모두 인식하는가
    note = "\n".join(stage.stamp(k) for k in ("머리말", "§0", "§3"))
    done = {m.group(1) for m in sl.RX_DONE.finditer(note)}
    timed = {m.group(1) for m in sl.RX_WRITTEN.finditer(note)}
    if done != {"머리말", "§0", "§3"} or timed != done:
        problems.append(f"facts 인식 불일치 — 완료 {sorted(done)} · 시각 {sorted(timed)}")
    # 시각 없는 절은 잡아내야 한다(항상 통과면 검사가 아니다)
    partial = note + "\n<!-- ✓ §9 -->"
    d2 = {m.group(1) for m in sl.RX_DONE.finditer(partial)}
    t2 = {m.group(1) for m in sl.RX_WRITTEN.finditer(partial)}
    if sorted(d2 - t2) != ["§9"]:
        problems.append(f"시각 없는 절을 못 잡는다: {sorted(d2 - t2)}")

    # 생성줄 계약이 한 곳에만 있는가
    src = (Path(__file__).parent / "crosscheck.py").read_text(encoding="utf-8")
    if "stage.gen_universe_line" not in src:
        problems.append("crosscheck가 생성 문구를 stage.py에서 가져오지 않는다(두 곳에 적혔다)")

    status = FAIL if problems else PASS
    results.append((status, "㊹ 스탬프·생성줄 계약", "; ".join(problems)))
    print(f"[{status}] ㊹ 스탬프·생성줄 계약"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_stamp_contract)

# ================== 포트폴리오 분모 (양 시장을 한 계좌로) ==================
# 왜 이 다섯 건인가 — 이 층에서 실제로 세 번 같은 유형으로 깨졌다:
#   ① 키만 넣고 리더가 없는 '장식 키'  ② 리더는 있지만 다른 한도에 가려 **닿지 않는 분기**
#   ③ 파일명 정렬로 최신을 고르다 **리허설 잔고를 실계좌로 읽은 것**
# 셋 다 py_compile과 기존 47건을 그대로 통과했다. 그래서 각각에 케이스를 붙인다.

def _mk_snap(market, cash, positions, fx=None):
    b = {"currency": "USD" if market == "us" else "KRW", "cash": cash,
         "positions": positions}
    if fx:
        b["exchange_rate"] = fx
    return {"market": market.upper(), "generated_at": now_iso(), "day_pnl_pct": 0.0,
            "balance": b, "prices": dict(BASE_SNAPSHOT["prices"])}


def test_portfolio_denominator():
    """㊺ 분모가 포트폴리오다 — 같은 퍼센트가 양 시장에서 같은 금액이어야 한다."""
    problems = []
    saved = rg.DATA_DIR
    tmp = Path(tempfile.mkdtemp(prefix="rgpf_"))
    _db = _ctxlib.ExitStack()
    try:
        _db.enter_context(testkit.db_case(tmp / "db"))     # 자산 곡선은 빈 임시 저장소 — 반대편은 스냅샷으로만 잰다
        if db.equity_rows():
            problems.append("빈 임시 저장소에 자산 곡선 행이 있다(격리 실패)")
        rg.DATA_DIR = tmp / "data"
        rg.DATA_DIR.mkdir()
        kr = _mk_snap("kr", 10_000_000, [])
        us = _mk_snap("us", 100_000, [], fx=1300.0)
        (rg.DATA_DIR / "snapshot_260911_kr.json").write_text(json.dumps(kr), encoding="utf-8")
        (rg.DATA_DIR / "snapshot_260911_us.json").write_text(json.dumps(us), encoding="utf-8")

        want = 10_000_000 + 100_000 * 1300.0        # 1억 4,000만원
        pf_kr = rg.portfolio_equity("KR", kr["balance"])
        pf_us = rg.portfolio_equity("US", us["balance"])
        for lbl, pf in (("KR", pf_kr), ("US", pf_us)):
            if not pf["ok"]:
                problems.append(f"{lbl} run이 포트폴리오를 못 쟀다: {pf['why']}")
            elif abs(pf["krw"] - want) > 1:
                problems.append(f"{lbl} 합산 {pf['krw']:,.0f} ≠ {want:,.0f}")
        # 같은 12%가 양 시장에서 같은 **원화 금액**이어야 한다
        if pf_kr["ok"] and pf_us["ok"]:
            a = rg.pf_local(pf_kr, "KR", kr["balance"])["equity"] * 0.12
            b = rg.pf_local(pf_us, "US", us["balance"])["equity"] * 0.12 * 1300.0
            if abs(a - b) > 1:
                problems.append(f"12%가 시장마다 다르다: KR {a:,.0f}원 vs US {b:,.0f}원")

        # ③ 날짜 없는 파일(리허설)은 후보가 아니다 — 파일명 정렬이면 'R' > '2'로 이긴다
        (rg.DATA_DIR / "snapshot_REHEARSAL_kr.json").write_text(
            json.dumps(_mk_snap("kr", 999_000_000, [])), encoding="utf-8")
        pf2 = rg.portfolio_equity("US", us["balance"])
        if pf2["ok"] and abs(pf2["krw"] - want) > 1:
            problems.append(f"리허설 스냅샷이 분모에 섞였다: {pf2['krw']:,.0f}")

        # 반대편이 없으면 '없음'이 아니라 '못 쟀다'
        for f in rg.DATA_DIR.glob("snapshot_*_us.json"):
            f.unlink()
        pf3 = rg.portfolio_equity("KR", kr["balance"])
        if pf3["ok"] or "못 쟀다" not in pf3["why"]:
            problems.append("반대편 스냅샷이 없는데 ok=True로 나왔다")
        if rg.pf_local(pf3, "KR", kr["balance"])["equity"] != 10_000_000:
            problems.append("못 쟀을 때 그 시장 자산으로 물러서지 않았다")
    finally:
        _db.close()
        rg.DATA_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)
    status = FAIL if problems else PASS
    results.append((status, "㊺ 분모가 포트폴리오 · 리허설 배제 · 못 쟀을 때 후퇴", "; ".join(problems)))
    print(f"[{status}] ㊺ 분모가 포트폴리오 · 리허설 배제 · 못 쟀을 때 후퇴"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_portfolio_denominator)

_AXIS_MAP = {"axes": [{"id": "ai_memory", "label": "AI 메모리",
                       "names": [{"ticker": "005930", "sign": "+"},
                                 {"ticker": "000660", "sign": "+"}]},
                      {"id": "hedge", "label": "역노출",
                       "names": [{"ticker": "000660", "sign": "-"}]}]}
_US_RICH = _mk_snap("us", 100_000, [], fx=1300.0)

# ㊻ 자금 배분 — 목표는 포트폴리오, 집행은 그 시장 현금. **현금에 맞춰 깎아서 산다** —
#    2026-09-11엔 거부했는데 모의 기간엔 계좌를 못 합쳐 국내 매수가 두 run 연속 0이 됐다(9/14·9/15).
def _shaved_within_cash(approved, problems):
    o = (approved.get("orders") or [{}])[0]
    if o.get("qty", 0) * o.get("price", 0) > 1_000_000:
        problems.append(f"깎은 주문이 현금을 넘는다: {o.get('qty')}×{o.get('price')}")
    if not approved.get("shaved"):
        problems.append("approved.shaved에 깎은 기록이 없다")


run_case("㊻ 그 시장 현금이 모자라면 「자금 배분」— 거부가 아니라 현금 안 수량으로 깎는다",
         signal=base_signal(),
         snapshot=_mk_snap("kr", 1_000_000, [{"market": "KR", "ticker": "005930",
             "name": "삼성전자", "qty": 10, "avg_price": 70000, "price": 71000,
             "eval_amt": 710_000, "pnl_pct": 1.43}]),
         limits_patch={**ROOMY, "axis_max_pct": None, "funding_max_per_run": 0,
                       "reserve_for_other_market_pct": None},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=1, expect_order_reason_contains="자금 배분",
         expect_approved=_shaved_within_cash)

run_case("㊻-b 현금으로 1주도 못 사면(회전 꺼짐) 그때만 「자금 배분」 거부",
         signal=base_signal(),
         snapshot=_mk_snap("kr", 50_000, [{"market": "KR", "ticker": "005930",
             "name": "삼성전자", "qty": 10, "avg_price": 70000, "price": 71000,
             "eval_amt": 710_000, "pnl_pct": 1.43}]),
         limits_patch={**ROOMY, "axis_max_pct": None, "funding_max_per_run": 0,
                       "reserve_for_other_market_pct": None},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=0, expect_reject_contains="자금 배분")

# ㊼ 축 집중 — 상한(25%)은 지웠다(2026-09-22). 공개선(40%)을 넘기면 명분이 있어야 한다.
def _with_small_why(sig: dict) -> dict:
    """크기 규칙(FB-261010-5 — 보유 평균보다 작은 새 종목은 small_why)과 무관한 케이스가 그 규칙에 먼저 걸리지 않게 한다."""
    for p in sig.get("proposals") or []:
        if p.get("action") == "BUY":
            p["small_why"] = {"kind": "binary_event", "why": "테스트 — 이 케이스는 다른 규칙을 본다"}
    return sig


run_case("㊼ 축 공개선(40%) 초과 + 명분 없음 → 그 제안만 거부(양 시장 합산)",
         signal=_with_small_why(base_signal()),
         snapshot=_mk_snap("kr", 10_000_000, [{"market": "KR", "ticker": "005930",
             "name": "삼성전자", "qty": 500, "avg_price": 70000, "price": 71000,
             "eval_amt": 60_000_000, "pnl_pct": 1.43}]),
         limits_patch={**ROOMY, "concentration_disclose_pct": {"position": 50.0, "axis": 25.0}},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         extra_map=_AXIS_MAP,
         expect_orders=0, expect_reject_contains="집중 명분 없음")

run_case("㊽ 축 여유가 있으면 명분 없이도 산다",
         signal=base_signal(),
         snapshot=_mk_snap("kr", 30_000_000, []),
         limits_patch={**ROOMY, "concentration_disclose_pct": {"position": 20.0, "axis": 40.0}},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         extra_map=_AXIS_MAP,
         expect_orders=1)


def test_judged_reserve():
    """㊾ 예약은 상수가 아니라 **판단**이다(2026-09-22) — `allocation.reserved`(없으면 논지 원장 기본값)만큼 이 시장 현금에서 뺀다."""
    problems = []
    saved = rg.DATA_DIR
    tmp = Path(tempfile.mkdtemp(prefix="rgres_"))
    try:
        rg.DATA_DIR = tmp / "data"
        rg.DATA_DIR.mkdir()
        th = [{"id": "kr-a", "market": "KR", "status": "armed", "ticker": "042660",
               "entry_triggers": [{"id": "e1", "check": "price <= 1", "size_pct": 4.0}]},
              {"id": "kr-c", "market": "KR", "status": "held", "ticker": "005930"}]
        with testkit.db_case(tmp / "db"):
            testkit.seed(theses=th)
            problems += _seed_check(theses=th, watchlist=False)
            bal = {"cash": 1_000_000, "currency": "KRW", "positions": [{"ticker": "005930", "qty": 10, "eval_amt": 9_000_000, "price": 900_000}]}
            # 기본값: armed kr-a 다음 칸 4% × 자산(1,000만) = 40만 예약 → 쓸 수 있는 현금 60만
            ctx, out = rg.allocation_context({"allocation": base_alloc()}, "KR", bal)
            if round(ctx["reserved_here"]) != 400_000 or round(ctx["deployable"]) != 600_000:
                problems.append(f"논지 원장 기본 예약 — reserved {ctx['reserved_here']:,.0f} deployable {ctx['deployable']:,.0f} (기대 40만/60만)")
            # 모델이 덮어쓰면 그 값
            ctx2, _ = rg.allocation_context({"allocation": base_alloc(reserved=[{"thesis_id": "kr-a", "market": "KR", "amount": 100_000, "why": "e1 한 칸만"}])}, "KR", bal)
            if round(ctx2["reserved_here"]) != 100_000:
                problems.append(f"판단 예약 덮어쓰기 실패 — {ctx2['reserved_here']:,.0f}")
            # 예약이 현금을 넘으면 deployable 0 → 자금 배분 거부(1주도 못 산다)
            order = [{"ticker": "000660", "action": "BUY", "qty": 10, "price": 100_000, "source": "llm"}]
            ctx3 = {"reserved_here": 1_000_000, "need": 0, "gap_amt": 0, "deployable": 0}
            f3, r3 = rg.apply_run_limits([dict(order[0])], {"daily_max_orders": None, "portfolio_daily_loss_halt_pct": None,
                                                            "funding_max_per_run": 0}, bal, 0.0, "KR", ctx3)
            if f3 or not any("자금 배분" in r["why"] for r in r3):
                problems.append(f"예약이 현금 전부면 매수가 거부돼야 한다 — {[o['qty'] for o in f3]} {[r['why'][:40] for r in r3]}")
            # 예약 0이면 현금 안에서 산다
            f4, _ = rg.apply_run_limits([dict(order[0])], {"daily_max_orders": None, "portfolio_daily_loss_halt_pct": None,
                                                           "funding_max_per_run": 0}, bal, 0.0, "KR", {"reserved_here": 0})
            if not f4 or f4[0]["qty"] != 10:
                problems.append(f"예약 0인데 현금(100만) 안 10주가 안 나갔다 — {[o['qty'] for o in f4]}")
    finally:
        rg.DATA_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)
    status = FAIL if problems else PASS
    results.append((status, "㊾ 판단된 예약 — 논지 원장 기본값·덮어쓰기·현금 차감", "; ".join(problems)))
    print(f"[{status}] ㊾ 판단된 예약 — 논지 원장 기본값·덮어쓰기·현금 차감"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_judged_reserve)


# ============== 두 시장을 하나의 run 흐름으로 (D2~D6) ==============
# 각 건이 **실제로 났던 결함**에 붙어 있다:
#   D2 교차 대응 소실 · D3 조인 키 미연결 · D4 등급 증발 · D5 산출 미인수 · D6 중복 조사

def test_cross_market_chain():
    """㊿ 조건은 시장에 속하지 않고 대응만 속한다 · 축이 양 시장을 잇는다."""
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import scenarios as sc, market_map as mm

    # 실데이터 정합성 검사 — 원장 사본(journal/·config/의 파일 렌더본)을 임시 저장소로 읽어 들여 본다(실제 저장소는 열지 않는다)
    _real = _ctxlib.ExitStack()
    _real.enter_context(testkit.db_case())
    try:
        _loaded = testkit.load_ledgers(Path(__file__).parent / "journal", Path(__file__).parent / "config")
        if not _loaded.get("theses") or not _loaded.get("axes") or not db.companies("KR"):
            problems.append(f"원장 사본 읽기 실패 — {_loaded} · 감시 목록 KR {len(db.companies('KR'))}행")

        # D2 — 한 문장에 두 시장 행동이 들어 있으면 **갈라 담아야** 한다.
        acts = sc.extract_actions("KR Samsung ladder e1 fires; MSFT position held.", "us")
        mks = [a["market"] for a in acts]
        if "kr" not in mks:
            problems.append(f"미국 run이 지시한 **국내 행동**이 사라졌다: {mks}")
        if "us" not in mks:
            problems.append(f"같은 문장의 미국 행동이 사라졌다: {mks}")
        # 통으로 보면 MSFT만 잡혀 앞 절이 삼켜진다 — 절 단위인지 확인한다.
        if len(acts) < 2:
            problems.append("한 문장을 통으로 봤다 — 절 단위로 갈라야 한다")
        # 시장 표지가 없으면 조용히 추측하지 않는다.
        q_ = sc.extract_actions("second rung, +4%.", "")
        if q_ and q_[0]["market"] not in ("?", ""):
            problems.append(f"시장을 못 가르는데 추측했다: {q_[0]['market']}")

        # D3 — 축 하나에서 양 시장 종목이 나와야 조인 키다.
        by_tk, by_id = mm.axes_index()
        if not by_id:
            problems.append("지도에 축이 없다")
        else:
            both = [a for a in by_id
                    if {mm._market_of(n.get("ticker")) for n in (by_id[a].get("names") or [])}
                    >= {"KR", "US"}]
            if not both:
                problems.append("양 시장 종목을 함께 단 축이 **하나도 없다** — 조인 키가 죽었다")
        if mm.match_axis("있을 리 없는 축 라벨 zzz"):
            problems.append("없는 라벨에 축 id를 붙였다 — 추측 금지")

        # D3 — 논지의 축 id는 지도에 실재해야 한다(고아 축 검출).
        th = db.theses_all()
        if not th:
            problems.append("논지 원장을 못 읽었다(저장소 사본 0행)")
        orphan = [t["id"] for t in th
                  if t.get("axis_id") and t["axis_id"] != "미지정" and t["axis_id"] not in by_id]
        if orphan:
            problems.append(f"지도에 없는 축을 가리키는 논지 {len(orphan)}건: {orphan[:3]}")
    finally:
        _real.close()

    status = FAIL if problems else PASS
    results.append((status, "㊿ 교차 시장 사슬(대응 분리·축 조인·고아 축 0)", "; ".join(problems)))
    print(f"[{status}] ㊿ 교차 시장 사슬(대응 분리·축 조인·고아 축 0)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_cross_market_chain)


def test_sector_and_sources_persist():
    """(51) 섹터 등급이 run을 넘어 남고, 출처가 공용 원장에 누적된다."""
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import market_map as mm

    tmp = Path(tempfile.mkdtemp(prefix="rgd4_"))
    _db = _ctxlib.ExitStack()
    try:
        # D4 — 미국 ETF 코드가 sectors에 **자리를 얻는가**(예전엔 담을 곳이 없었다). 지도·섹터 짝은 빈 임시 저장소에
        _db.enter_context(testkit.db_case(tmp / "db"))
        db.sector_bridge_save({"links": [{"us": "SMH", "kr": "0013", "kr_name": "전기·전자",
                                          "sign": "+", "confidence": "high"}]})
        if [(L.get("us"), L.get("kr")) for L in (db.sector_bridge_doc() or {}).get("links", [])] != [("SMH", "0013")]:
            problems.append("심기 실패 — 섹터 짝이 저장소에 안 보인다")

        class A:
            code, grade, market, name, note, research = "SMH", "A", "us", "반도체", "", ""
        mm.cmd_grade(A())
        got = {s["code"]: s for s in db.market_map_load()["sectors"]}
        if "SMH" not in got:
            problems.append("미국 섹터 등급이 저장되지 않았다 — 담을 자리가 없다")
        elif got["SMH"].get("grade") != "A":
            problems.append(f"등급이 안 남았다: {got['SMH'].get('grade')}")

        # D4 — 짝 섹터가 등급을 받으면 반대편이 그것을 **읽어야** 한다.
        class B:
            code, grade, market, name, note, research = "0013", "B", "kr", "전기·전자", "", ""
        mm.cmd_grade(B())
        pg = mm.paired_grades(["SMH"])
        if not pg or pg[0]["grade"] != "B":
            problems.append(f"짝 섹터 등급이 안 건너온다: {pg}")
    finally:
        _db.close()
        shutil.rmtree(tmp, ignore_errors=True)

    # D6 — 같은 사실을 다시 넣으면 **retrieved가 원래 시점을 지켜야** 한다(사실 저장소 facts.add_fact · 2026-10-17).
    tmp2 = Path(tempfile.mkdtemp(prefix="rgd6_"))
    import contextlib as _c, io as _io
    try:
        with testkit.db_case(tmp2 / "db"):
            row = {"kind": "metric", "subject_type": "asset", "subject_id": "BRENT", "metric_code": "price", "value": 108,
                   "unit": "USD", "as_of": "2026-09-10", "retrieved": "2026-09-11 02:19", "source": "Axios",
                   "url": "https://example.com/a", "tier": 2, "quote": "Brent crude topped $108 a barrel on Wednesday"}
            with _c.redirect_stdout(_io.StringIO()), _c.redirect_stderr(_io.StringIO()):
                r1 = facts.add_fact(dict(row), ("260911", "us"))
                r2 = facts.add_fact(dict(row, retrieved="2026-09-11 11:19"), ("260911", "kr"))
                other = {"kind": "release", "indicator_code": "US_PPI_MOM", "period": "2026-08", "actual": 0.4, "unit": "%",
                         "as_of": "2026-09-10", "url": "https://example.com/a", "tier": 2, "source": "Axios",
                         "quote": "US producer prices rose 0.4% in August from July"}
                r3 = facts.add_fact(other, ("260911", "us"))
            if r1["status"] != "신규" or r2["status"] != "재사용" or r2["id"] != r1["id"]:
                problems.append(f"신규/재사용 판정이 틀렸다: {r1['status']}, {r2['status']}")
            got = db.q1("SELECT retrieved FROM ev_fact WHERE id=?", (r1["id"],)) or {}
            if got.get("retrieved") != "2026-09-11 02:19":
                problems.append(f"재인용이 retrieved를 덮었다: {got.get('retrieved')} — 원래 가져온 시점을 지켜야 한다")
            runs = sorted(r["run_id"] for r in db.q("SELECT run_id FROM ev_fact_run WHERE fact_id=?", (r1["id"],)))
            if runs != ["260911_kr", "260911_us"]:
                problems.append(f"인용 run이 안 쌓인다: {runs}")
            # 같은 기사의 **다른 사실**은 합쳐지면 안 된다(URL로 묶으면 사실이 사라진다).
            if r3["status"] == "재사용" or r3["id"] == r1["id"]:
                problems.append("같은 URL의 다른 사실이 하나로 합쳐졌다 — 사실이 사라진다")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    status = FAIL if problems else PASS
    results.append((status, "(51) 섹터 등급 persist · 출처 공용 원장", "; ".join(problems)))
    print(f"[{status}] (51) 섹터 등급 persist · 출처 공용 원장"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_sector_and_sources_persist)


# ================== 지도를 전망으로 (E1~E6) ==================
# 붙어 있는 결함: 전망 없는 축(선행 0/8) · 감으로 잰 '미반영' · 결손 재료 위 판단(9/9) ·
# 일정 있는데 논지 없음(오라클) · 아쉬움이 감정으로만 남음

def test_forecast_layer():
    """(52) 전망 스키마·선행 판정·결손 생성줄·일정 게이트·priced 판정·스크린 파서."""
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import market_map as mm, ingest, screen_treasury as st

    # E1 — 검증은 거부해야 할 것을 거부하는가
    bad = {"id": "x", "name": "n", "thesis": "t", "direction": "up", "expected": "", "horizon_weeks": 99,
           "confidence": 2, "beneficiaries": []}
    if len(mm.forecast_validate(bad)) < 4:
        problems.append("전망 검증이 잘못된 필드를 통과시킨다")
    only_first = {"id": "x", "name": "n", "thesis": "t", "direction": "+", "expected": "+5%p", "horizon_weeks": 8,
                  "confidence": 0.6, "beneficiaries": [{"ticker": "005930", "order": 1}]}
    if not any("2차·3차" in p for p in mm.forecast_validate(only_first)):
        problems.append("2차·3차 수혜 없는 전망을 이유 없이 통과시킨다")
    only_first["no_secondary_why"] = "시험"
    only_first["no_cross_market_why"] = "시험(교차 시장 검사는 (78)이 본다)"
    if mm.forecast_validate(only_first):
        problems.append("no_secondary_why를 적었는데도 거부한다")

    # E1 — 선행 판정은 재료 첫 등장일로 **기계로** 갈린다
    tmp = Path(tempfile.mkdtemp(prefix="rgfc_"))
    saved = mm.MATERIAL_DIR
    try:
        mm.MATERIAL_DIR = tmp
        (tmp / "material_260909_kr.md").write_text("두산에너빌리티 +10%", encoding="utf-8")
        (tmp / "material_260908_kr.md").write_text("두산퓨얼셀 +12%", encoding="utf-8")
        # 1차(두산퓨얼셀)는 9/8 재료에 있다 — 그래도 2차(두산에너빌리티)가 9/9에야 뜨면 **선행**이다
        ax = {"opened": "2026-09-08", "beneficiaries": [
            {"ticker": "336260", "name": "두산퓨얼셀", "order": 1},
            {"ticker": "034020", "name": "두산에너빌리티", "order": 2}]}
        ok, why = mm.created_before_news(ax)
        ax2 = dict(ax, opened="2026-09-10")
        ok2, _ = mm.created_before_news(ax2)
        if ok is not True or ok2 is not False:
            problems.append(f"선행 판정이 틀렸다: 9/8 전망→{ok}({why}) · 9/10 전망→{ok2}")
        if "2차·3차" not in why:
            problems.append("선행을 1차까지 대조했다 — 1차는 뉴스가 말한 것이라 항상 '뉴스 후'가 된다")
        if mm.created_before_news({"opened": "", "beneficiaries": []})[0] is not None:
            problems.append("잴 수 없는 전망을 True/False로 추측했다")
    finally:
        mm.MATERIAL_DIR = saved
        shutil.rmtree(tmp, ignore_errors=True)

    # E2 — 결손 생성줄: 얇으면 ★, 정상이면 '재료 결손 없음'
    tmp2 = Path(tempfile.mkdtemp(prefix="rgmat_"))
    try:
        full = "## 계좌\n" + "".join(f"## [경제] 레터{i} — 2026/09/08\n" + "x" * 3000 + "\n" for i in range(4))
        (tmp2 / "material_260908_kr.md").write_text(full, encoding="utf-8")
        (tmp2 / "material_260909_kr.md").write_text("## 계좌\n짧다\n", encoding="utf-8")
        v_ok = ingest.material_deficit(tmp2 / "material_260908_kr.md", {"attempted": True}, True)
        v_bad = ingest.material_deficit(tmp2 / "material_260909_kr.md", {"attempted": True}, True)
        if v_ok != "재료 결손 없음":
            problems.append(f"정상 재료를 결손으로 봤다: {v_ok[:40]}")
        if not v_bad.startswith("★ 재료 결손"):
            problems.append(f"얇은 재료를 통과시켰다: {v_bad[:40]}")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    # E5 — 스크린 파서: 발행주식은 istc_totqy, 비율은 자기주식/발행
    if st._int("2,875,800") != 2875800 or st._int("-") != 0:
        problems.append("숫자 파서가 쉼표·대시를 못 다룬다")

    # E6 — 채점기: 선행 1·비선행 2를 심으면 선행률 33%
    tmp3 = Path(tempfile.mkdtemp(prefix="rgsc_"))
    saved = mm.MATERIAL_DIR
    try:
        mm.MATERIAL_DIR = tmp3
        (tmp3 / "material_260910_kr.md").write_text("A B C", encoding="utf-8")
        today = datetime.now(KST).strftime("%Y-%m-%d")
        m = {"axes": [
            {"id": "f1", "direction": "+", "opened": "2026-09-08", "updated": today, "beneficiaries": [{"ticker": "A", "order": 2}]},
            {"id": "f2", "direction": "+", "opened": "2026-09-12", "updated": today, "beneficiaries": [{"ticker": "B", "order": 1}]},
            {"id": "f3", "direction": "-", "opened": "2026-09-12", "updated": today, "beneficiaries": [{"ticker": "C", "order": 1}]}]}
        import io as _io, contextlib as _ctx
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            mm.forecast_score(m, 3650)
        out = buf.getvalue()
        if "선행 1" not in out or "세움 3" not in out:
            problems.append(f"선행률 계산이 틀렸다: {[l for l in out.splitlines() if l.startswith('[전망]')]}")
    finally:
        mm.MATERIAL_DIR = saved
        shutil.rmtree(tmp3, ignore_errors=True)

    status = FAIL if problems else PASS
    results.append((status, "(52) 전망 층(스키마·선행 판정·결손 생성줄·채점)", "; ".join(problems)))
    print(f"[{status}] (52) 전망 층(스키마·선행 판정·결손 생성줄·채점)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_forecast_layer)


# ================== 레버리지·인버스 허용 (2026-09-14 사용자 결정) ==================
# 막지 않는 대신 **수익을 지키는 세 규칙**이 실제로 붙는가: 상한/배수 · 축 노출×배수(인버스 음수) · 호흡 상한

def test_leverage_rules():
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import risk_guard as rg
    rules = json.loads(rg.LIMITS_PATH.parent.joinpath("universe_rules.json").read_text(encoding="utf-8"))

    # 선물형만 금지 — 레버리지·인버스는 통과
    pats = rules["forbidden_name_patterns"]
    if rg._forbidden("Direxion Daily Semiconductor Bull 3X Shares", "SOXL", pats):
        problems.append("SOXL이 금지 패턴에 걸린다 — 선물형만 배제해야 한다")
    if not rg._forbidden("KODEX WTI원유선물(H)", "261220", pats):
        problems.append("선물형이 통과한다")
    # 판별
    for nm, tk, want in (("Direxion Daily Semiconductor Bull 3X Shares", "SOXL", (3, False)),
                         ("ProShares UltraPro Short QQQ", "SQQQ", (3, True)),
                         ("KODEX 레버리지", "122630", (2, False)), ("NVIDIA", "NVDA", (1, False))):
        if rg.leverage_factor(nm, tk) != want:
            problems.append(f"{tk} 배수 판별 {rg.leverage_factor(nm, tk)} ≠ {want}")

    # 축 노출 — 배수 곱, 인버스 음수(지도 부호), 이중 부호 없음(지도 축은 임시 저장소에 심는다)
    _semis = [{"id": "semis", "names": [
        {"ticker": "NVDA", "sign": "+"}, {"ticker": "SOXL", "sign": "+"}, {"ticker": "SOXS", "sign": "-"}]}]
    with testkit.db_case():
        testkit.seed(axes=_semis)
        problems += _seed_check(axes=_semis, watchlist=False)
        idx = rg.axis_index()
        pf = {"ok": True, "fx": 1300.0, "krw": 1e8, "parts": {"US": {"positions": [
            {"ticker": "NVDA", "name": "NVIDIA", "eval_amt": 10000},
            {"ticker": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X Shares", "eval_amt": 1000},
            {"ticker": "SOXS", "name": "Direxion Daily Semiconductor Bear 3X Shares", "eval_amt": 2000}]}}}
        got = rg.axis_exposure(pf, "US", idx).get("semis")
        if got != 10000 + 3000 - 6000:
            problems.append(f"축 노출 {got} ≠ 7000 (NVDA 10,000 + SOXL 3×1,000 − SOXS 3×2,000)")
        if rg.axes_of(idx, "SOXS"):
            problems.append("SOXS가 +노출 축으로 잡힌다 — 인버스는 여력 검사 대상이 아니다")

    # 종목당 상한/배수 — 12% 상한이면 3배 ETF 목표 5%는 4%로 깎여야 한다(거부가 아니라)
    import io as _io, contextlib as _ctx
    lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
    lim.update({"leveraged_cap_divide_by_factor": True, "fc_track1_required": False})
    wl = {"US": [{"ticker": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X Shares", "excd": "AMEX"}]}
    bal = {"cash": 100_000, "currency": "USD", "positions": [], "exchange_rate": 1300.0}
    sig = {"market": "US", "proposals": [{"ticker": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X Shares",
                                          "action": "BUY", "weight_target_pct": 5.0, "confidence": 0.7, "excd": "AMEX",
                                          "size_why": "테스트", "expected_upside_pct": 30.0}]}
    saved_pe = rg.portfolio_equity
    rg.portfolio_equity = lambda m, b: {"krw": 1.3e8, "cash_krw": 1.3e8, "invested_krw": 0.0, "ok": True,
                                        "asof": "t", "fx": 1300.0, "parts": {}, "why": "t"}
    try:
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            acc, rej = rg.screen_proposals(sig, lim, wl, bal, {"SOXL": {"price": 100.0}})
        if not acc:
            problems.append(f"3배 ETF 5% 제안이 승인되지 않았다: {rej}")
        else:
            q = acc[0]["qty"]
            # (2026-09-22) 상한 대신 **증분을 배수로 나눈다** — 5%/3 = 1.67% × 100,000 = 1,667 → 16주(5%면 50주)
            if not (15 <= q <= 17):
                problems.append(f"증분을 배수로 안 나눴다 — qty {q} (기대 ≈16 = 5%/3)")
        if "배수로 나눔" not in buf.getvalue():
            problems.append("상한을 나눈 사실이 사유에 안 적힌다")
    finally:
        rg.portfolio_equity = saved_pe

    status = FAIL if problems else PASS
    results.append((status, "(53) 레버리지·인버스: 선물형만 금지 · 상한/배수 · 축×배수·인버스 음수", "; ".join(problems)))
    print(f"[{status}] (53) 레버리지·인버스: 선물형만 금지 · 상한/배수 · 축×배수·인버스 음수"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_leverage_rules)


# ============ 다른 세션이 보고한 도구 결함 4건 (2026-09-15) ============

def test_reported_defects_0915():
    """(54) 리허설 무시 · 1단 없으면 run 아님 · priced 병합 · universe_apply 명세 존재."""
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import stage, sessions as ss, market_map as mm

    # ① 운영 폴더에 6자리 스탬프가 아닌 산출물이 없어야 한다 — 파일명 정렬 함정의 재료다
    here = Path(__file__).parent
    stray = [f.name for pat in ("data/material_*.md", "data/snapshot_*.json", "signals/signal_*.json", "analysis/분석노트_*.md")
             for f in here.glob(pat) if not re.search(r"_(\d{6})_(kr|us)", f.name)]
    if stray:
        problems.append(f"운영 폴더에 스탬프 없는 산출물 {len(stray)}개: {stray[:3]} — _rehearsal/로")
    # ① latest_stamped가 REHEARSAL을 심어도 무시하는가
    tmp = Path(tempfile.mkdtemp(prefix="rgls_"))
    try:
        for n in ("signal_260909_kr.json", "signal_260914_kr.json", "signal_REHEARSAL_kr.json", "signal_ZZZ_kr.json"):
            (tmp / n).write_text("{}", encoding="utf-8")
        got = stage.latest_stamped(tmp, "signal", "kr", ".json")
        if not got or got.name != "signal_260914_kr.json":
            problems.append(f"latest_stamped가 {got and got.name}을 골랐다(기대 signal_260914_kr.json)")
        got2 = stage.latest_stamped(tmp, "signal", "kr", ".json", exclude_stamp="260914")
        if not got2 or got2.name != "signal_260909_kr.json":
            problems.append("exclude_stamp가 안 먹는다")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ② 1단 산출물 없이 섹터 리서치만 있으면 run이 아니다
    tmp2 = Path(tempfile.mkdtemp(prefix="rgss_"))
    saved = (ss.DATA, ss.JOURNAL, ss.ANALYSIS, ss.SIGNALS)
    try:
        for d in ("data", "journal", "analysis", "signals"):
            (tmp2 / d).mkdir()
        ss.DATA, ss.JOURNAL, ss.ANALYSIS, ss.SIGNALS = tmp2 / "data", tmp2 / "journal", tmp2 / "analysis", tmp2 / "signals"
        (tmp2 / "analysis" / "섹터_x_260914_v1_0.md").write_text("x", encoding="utf-8")
        r1 = ss.reached("260914", "us")[0]
        (tmp2 / "data" / "material_260914_us.md").write_text("x", encoding="utf-8")
        r2 = ss.reached("260914", "us")[0]
        if not r1.startswith("0"):
            problems.append(f"캡처 없는 세션을 run으로 봤다: {r1} — 허깨비 회고의 원인")
        if not r2.startswith("3"):
            problems.append(f"캡처가 있으면 3단이어야 하는데 {r2}")
    finally:
        ss.DATA, ss.JOURNAL, ss.ANALYSIS, ss.SIGNALS = saved
        shutil.rmtree(tmp2, ignore_errors=True)
    # ② phantom 행은 회고로 치지 않는다
    with testkit.db_case():                       # 회고 기록은 저장소 op_review — 빈 임시 저장소에 심는다
        _rv = [{"market": "US", "session": "2026-09-14", "phantom": True}, {"market": "KR", "session": "2026-09-14"}]
        testkit.seed(logs={"reviews": _rv})
        if db.log_rows("reviews") != _rv:
            problems.append("심기 실패 — 회고 기록이 저장소에 안 보인다")
        seen = ss._reviewed()
        if ("us", "2026-09-14") in seen or ("kr", "2026-09-14") not in seen:
            problems.append(f"phantom 처리 틀림: {seen}")

    # ③ forecast add 병합 — priced 보존
    tmp4 = Path(tempfile.mkdtemp(prefix="rgfa_"))
    saved_mat = mm.MATERIAL_DIR
    _db4 = _ctxlib.ExitStack()
    try:
        mm.MATERIAL_DIR = tmp4
        _db4.enter_context(testkit.db_case(tmp4 / "db"))   # 지도 축은 빈 임시 저장소에 심는다
        _ax4 = [{"id": "a", "name": "n", "thesis": "t", "direction": "+", "expected": "e",
                 "horizon_weeks": 4, "confidence": 0.6, "opened": "2026-09-08",
                 "beneficiaries": [{"ticker": "X", "order": 2, "priced": False, "priced_basis": "b", "priced_at": "d"}]}]
        testkit.seed(axes=_ax4)
        problems += _seed_check(axes=_ax4, watchlist=False)
        fc = tmp4 / "fc.json"
        fc.write_text(json.dumps([{"id": "a", "name": "n", "thesis": "t", "direction": "+", "expected": "e", "horizon_weeks": 4,
                                   "confidence": 0.6, "no_cross_market_why": "시험(교차 시장 검사는 (78)이 본다)",
                                   "beneficiaries": [{"ticker": "X", "order": 2, "why": "w"}]}]), encoding="utf-8")
        class A: sub = "add"; file = str(fc); replace_beneficiaries = False
        import io as _io, contextlib as _ctx
        with _ctx.redirect_stdout(_io.StringIO()):
            mm.cmd_forecast(A())
        b = db.axis_get("a")["beneficiaries"][0]
        if b.get("priced") is not False or b.get("priced_basis") != "b" or b.get("why") != "w":
            problems.append(f"재실행이 priced를 지웠거나 새 필드를 안 받았다: {b}")
    finally:
        _db4.close()
        mm.MATERIAL_DIR = saved_mat
        shutil.rmtree(tmp4, ignore_errors=True)

    # ④ universe_apply가 수동 파이프라인 명세(5단)에 있는가 — 무인 경로 전용이면 다시 낡는다
    spec = (here.parent / ".claude" / "skills" / "trade-run" / "references" / "stage5_decide.md")
    if spec.exists() and "universe_apply.py --signal" not in spec.read_text(encoding="utf-8"):
        problems.append("stage5_decide.md에 universe_apply 실행이 없다")

    status = FAIL if problems else PASS
    results.append((status, "(54) 보고된 결함 4건: 리허설 무시·1단 필수·priced 병합·universe_apply 명세", "; ".join(problems)))
    print(f"[{status}] (54) 보고된 결함 4건: 리허설 무시·1단 필수·priced 병합·universe_apply 명세"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_reported_defects_0915)


# ============ KIS 게이트웨이 500 (EGW00300) 재시도 — 2026-09-16 ============

def test_kis_transient_retry():
    """(55) 조회(GET)는 EGW00300·5xx에 물러섰다 재시도하고, 주문(POST)은 절대 재시도하지 않는다.
    journal은 현금을 못 읽으면 equity 행을 쓰지 않는다."""
    problems = []
    sys.path.insert(0, str(Path(__file__).parent))
    import urllib.error, io as _io, contextlib as _ctx
    import kis_client as kc

    calls = {"n": 0}
    def fake_urlopen(req, timeout=None, context=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            body = _io.BytesIO(b'{"rt_cd":"1","msg_cd":"EGW00300","msg1":"Gateway routing error"}')
            raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, body)
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"rt_cd":"0","msg_cd":"MCA00000","output":{"ok":1}}'
        return R()

    saved = (kc.urllib.request.urlopen, kc.TRANSIENT_BACKOFF_SEC, kc.time.sleep)
    try:
        kc.urllib.request.urlopen = fake_urlopen
        kc.TRANSIENT_BACKOFF_SEC = 0
        kc.time.sleep = lambda s: None
        c = kc.KisClient.__new__(kc.KisClient)
        c.base = "https://example.invalid"; c._ctx = None
        c._throttle = lambda: None
        # GET → 두 번 500 뒤 세 번째 성공
        res = c._request("GET", "/uapi/x", headers={})
        if res.get("rt_cd") != "0" or calls["n"] != 3:
            problems.append(f"GET 재시도가 안 됐다: 호출 {calls['n']}회, 결과 {res}")
        # POST → 첫 500에서 바로 예외, 재시도 0
        calls["n"] = 0
        try:
            c._request("POST", "/uapi/order", headers={}, body={"a": 1})
            problems.append("POST 500이 예외로 안 올라왔다")
        except kc.KisError:
            if calls["n"] != 1:
                problems.append(f"POST를 재시도했다({calls['n']}회) — 이중 주문 위험")
    finally:
        kc.urllib.request.urlopen, kc.TRANSIENT_BACKOFF_SEC, kc.time.sleep = saved

    # journal: cash None → 행 미기록
    src = (Path(__file__).parent / "journal.py").read_text(encoding="utf-8")
    if "if cash is None:" not in src or "쓰지 않는다" not in src:
        problems.append("journal이 현금 미확인 시 equity 행을 막지 않는다 — 누적 −100% 오염 위험")

    status = FAIL if problems else PASS
    results.append((status, "(55) KIS 500 재시도(GET만)·POST 무재시도·journal 잔고 실패 시 미기록", "; ".join(problems)))
    print(f"[{status}] (55) KIS 500 재시도(GET만)·POST 무재시도·journal 잔고 실패 시 미기록"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_kis_transient_retry)


# ============ 체결 확정 — 접수는 run의 끝이 아니다 (2026-09-16) ============

class _FakeFillClient:
    """fill.py용 가짜 브로커. `script[order_no]`는 폴링마다 꺼내 쓰는 상태 목록이고,
    마지막 항목이 남으면 계속 그것을 돌려준다. 정정·취소는 새 주문번호를 발급한다."""
    def __init__(self, script: dict, live: float = 0.0, raise_status=False):
        self.script = {k: list(v) for k, v in script.items()}
        self.live = live
        self.raise_status = raise_status
        self.modify_calls = []
        self.next_no = 900
        self.balance = {"cash": 0, "positions": []}

    def _pop(self, order_no):
        import kis_client as kc
        if self.raise_status:
            raise kc.KisError("HTTP 500 on GET status: EGW00300")
        seq = self.script.get(order_no)
        if not seq:
            return {"order_no": order_no, "found": False, "ord_qty": 0, "filled_qty": 0,
                    "remain_qty": 0, "avg_price": 0.0, "cancelled": False, "rejected": False,
                    "reject_reason": "", "orgno": "", "raw": {}}
        st = seq.pop(0) if len(seq) > 1 else seq[0]
        base = {"order_no": order_no, "found": True, "cancelled": False, "rejected": False,
                "reject_reason": "", "orgno": "00950", "raw": {}}
        base.update(st)
        return base

    def domestic_order_status(self, order_no, ticker="", date=""):
        return self._pop(order_no)

    def overseas_order_status(self, order_no, ticker="", excd="", date=""):
        return self._pop(order_no)

    def _mod(self, order_no, qty, price, cancel):
        self.next_no += 1
        new_no = f"{self.next_no:010d}"
        self.modify_calls.append({"orig": order_no, "qty": qty, "price": price, "cancel": cancel,
                                  "new": new_no})
        # 취소면 원주문이 잔량 0·체결 0(취소 표시)로 바뀐다. 정정이면 새 번호에 잔량이 옮겨간다.
        old = self.script.get(order_no) or [{}]
        last = dict(old[-1]) if old else {}
        if cancel:
            self.script[order_no] = [{**last, "remain_qty": 0, "cancelled": True}]
        else:
            self.script[order_no] = [{**last, "remain_qty": 0}]
            self.script[new_no] = self.script.get(new_no) or [
                {"ord_qty": qty, "filled_qty": 0, "remain_qty": qty, "avg_price": 0.0},
                {"ord_qty": qty, "filled_qty": qty, "remain_qty": 0, "avg_price": price}]
        return {"order_no": new_no, "orgno": "00950", "raw": {}}

    def domestic_modify(self, order_no, orgno, qty, price=0, cancel=False):
        return self._mod(order_no, qty, price, cancel)

    def overseas_modify(self, order_no, ticker, excd, qty, price=0.0, cancel=False):
        return self._mod(order_no, qty, price, cancel)

    def domestic_price(self, ticker):
        return {"price": self.live}

    def overseas_price(self, ticker, excd="NAS"):
        return {"price": self.live}

    def domestic_balance(self):
        return self.balance

    def overseas_balance(self, excd="NASD", currency="USD"):
        return self.balance


def _fill_record(market, ticker, qty, price, order_no, ts, status="SENT", extra=None):
    row = {"ts": ts, "status": status, "market": market, "ticker": ticker, "name": ticker,
           "action": "BUY", "qty": qty, "price": price, "source": "llm",
           "proposal": {"excd": "NYS"} if market == "US" else {},
           "result": {"order_no": order_no, "orgno": "00950", "raw": {}},
           "fill": {"verdict": "UNFILLED", "qty_before": 0}}
    if extra:
        row.update(extra)
    return {"generated_at": ts, "svr": "paper", "trades": [row], "fill_summary": {"sent": 1}}


def test_fill_state_machine():
    """(56) fill.py — 주문이 체결·부분·취소·만료 중 하나로 **확정될 때까지** 돈다.

    ① 두 번째 폴에서 체결 → FILLED·평균가·open_orders 0·종료 0·§11 생성줄
    ② 90초 넘게 미체결 → 현재가로 정정, **수량은 승인 금액 안**, 정정 뒤 체결 → FILLED(축소 기록)
    ③ 대기 초과 → 취소 → 체결 0이면 CANCELLED, 일부면 PARTIAL
    ④ 장 마감 뒤 잔량 → EXPIRED   ⑤ 상태 TR이 죽으면 예산 소진 → 종료 3·open_orders 1
    ⑥ 재전송 0건(가짜 클라이언트에 주문 함수가 없다 — 있으면 AttributeError로 터진다)
    ⑦ 루브릭 execute 기준: 확정 파일+노트가 미확정보다 기준을 **더** 통과한다(패턴 미출력)
    """
    import fill, stage
    from datetime import datetime as _dt, timedelta as _td
    problems = []
    cfg = {"fill_wait_minutes": 20, "fill_poll_sec": 20, "fill_chase_after_sec": 90,
           "fill_chase_max": 3, "fill_chase_max_pct": 0.5}

    def clock(start):
        state = {"now": start}
        def now_fn():
            return state["now"]
        def sleep_fn(sec):
            state["now"] = state["now"] + _td(seconds=sec)
        return now_fn, sleep_fn, state

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        t0 = _dt(2026, 9, 16, 0, 23, tzinfo=KST)          # 미국 정규장(EDT) 안

        # ① 체결
        rec = _fill_record("US", "VST", 30, 141.72, "0000041127", t0.isoformat())
        f1 = tdp / "trades_260916_us.json"; f1.write_text(json.dumps(rec), encoding="utf-8")
        note = tdp / "분석노트_260915_us_v1_0.md"
        note.write_text("## §10 한계\n\nx\n\n## §11 집행 결과\n\n| a |\n\n<!-- ✓ §11-집행 -->\n\n## §12 출처\n", encoding="utf-8")
        cli = _FakeFillClient({"0000041127": [
            {"ord_qty": 30, "filled_qty": 0, "remain_qty": 30, "avg_price": 0.0},
            {"ord_qty": 30, "filled_qty": 30, "remain_qty": 0, "avg_price": 141.66}]})
        now_fn, sleep_fn, st = clock(t0 + _td(seconds=5))
        rc = fill.confirm(f1, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, note=note, quiet=True)
        out = json.loads(f1.read_text(encoding="utf-8"))
        fr = out["trades"][0]["fill"]
        if rc != 0 or fr.get("verdict") != "FILLED" or abs(fr.get("avg_price", 0) - 141.66) > 1e-6:
            problems.append(f"① FILLED 기대 — rc={rc} fill={fr}")
        if out.get("fill_confirmed", {}).get("open_orders") != 0:
            problems.append(f"① open_orders 0 기대 — {out.get('fill_confirmed')}")
        want = stage.gen_fill_line(out["fill_confirmed"])
        ntxt = note.read_text(encoding="utf-8")
        if want not in ntxt or ntxt.index(want) > ntxt.index("<!-- ✓ §11-집행 -->"):
            problems.append("① §11에 생성줄이 스탬프 앞에 없다")
        if cli.modify_calls:
            problems.append("① 체결됐는데 정정·취소가 나갔다")
        # 멱등 — 다시 돌려도 줄이 하나
        fill.confirm(f1, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, note=note, quiet=True)
        if note.read_text(encoding="utf-8").count("<!-- gen:fill -->") != 1:
            problems.append("① 생성줄이 중복됐다(멱등 아님)")
        terminal_file, terminal_note = f1, note

        # ② 정정 — KR 10주 @84,500 승인(845,000). 현재가 84,700 → 정정가 84,700, 수량 845,000//84,700 = 9
        t1 = _dt(2026, 9, 16, 10, 40, tzinfo=KST)
        rec = _fill_record("KR", "042660", 10, 84500, "0000020259", t1.isoformat())
        f2 = tdp / "trades_260916_kr.json"; f2.write_text(json.dumps(rec), encoding="utf-8")
        cli = _FakeFillClient({"0000020259": [
            {"ord_qty": 10, "filled_qty": 0, "remain_qty": 10, "avg_price": 0.0}]}, live=84700)
        now_fn, sleep_fn, st = clock(t1 + _td(seconds=100))
        rc = fill.confirm(f2, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, quiet=True)
        out = json.loads(f2.read_text(encoding="utf-8")); fr = out["trades"][0]["fill"]
        mc = [m for m in cli.modify_calls if not m["cancel"]]
        if not mc or mc[0]["price"] != 84700 or mc[0]["qty"] != 9:
            problems.append(f"② 정정 84,700×9 기대 — {cli.modify_calls}")
        if rc != 0 or fr.get("verdict") != "FILLED" or fr.get("filled_qty") != 9:
            problems.append(f"② 정정 뒤 FILLED 9주 기대 — rc={rc} fill={fr}")
        if len(fr.get("order_chain", [])) != 2:
            problems.append(f"② 주문 체인 2개 기대 — {fr.get('order_chain')}")
        # 한도: 승인가 +0.5% = 84,922 → 현재가 86,000이면 84,900(스냅)까지만
        rec = _fill_record("KR", "042660", 10, 84500, "0000020260", t1.isoformat())
        f2b = tdp / "trades_260916b_kr.json"; f2b.write_text(json.dumps(rec), encoding="utf-8")
        cli = _FakeFillClient({"0000020260": [
            {"ord_qty": 10, "filled_qty": 0, "remain_qty": 10, "avg_price": 0.0}]}, live=86000)
        now_fn, sleep_fn, st = clock(t1 + _td(seconds=100))
        fill.confirm(f2b, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, quiet=True)
        mc = [m for m in cli.modify_calls if not m["cancel"]]
        if not mc or mc[0]["price"] > 84500 * 1.005 + 1e-6:
            problems.append(f"② 정정가가 승인가 +0.5%를 넘었다 — {mc}")

        # ③ 대기 초과 → 취소 → CANCELLED
        rec = _fill_record("KR", "042660", 10, 84500, "0000020261", t1.isoformat())
        f3 = tdp / "trades_260916c_kr.json"; f3.write_text(json.dumps(rec), encoding="utf-8")
        cli = _FakeFillClient({"0000020261": [
            {"ord_qty": 10, "filled_qty": 0, "remain_qty": 10, "avg_price": 0.0}]}, live=84500)
        now_fn, sleep_fn, st = clock(t1 + _td(minutes=21))
        rc = fill.confirm(f3, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, quiet=True)
        out = json.loads(f3.read_text(encoding="utf-8")); fr = out["trades"][0]["fill"]
        if rc != 0 or fr.get("verdict") != "CANCELLED" or not any(m["cancel"] for m in cli.modify_calls):
            problems.append(f"③ CANCELLED 기대 — rc={rc} fill={fr} calls={cli.modify_calls}")
        # ③-b 일부 체결 뒤 취소 → PARTIAL
        rec = _fill_record("KR", "042660", 10, 84500, "0000020262", t1.isoformat())
        f3b = tdp / "trades_260916d_kr.json"; f3b.write_text(json.dumps(rec), encoding="utf-8")
        cli = _FakeFillClient({"0000020262": [
            {"ord_qty": 10, "filled_qty": 3, "remain_qty": 7, "avg_price": 84500.0}]}, live=84500)
        now_fn, sleep_fn, st = clock(t1 + _td(minutes=21))
        rc = fill.confirm(f3b, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, quiet=True)
        out = json.loads(f3b.read_text(encoding="utf-8")); fr = out["trades"][0]["fill"]
        if rc != 0 or fr.get("verdict") != "PARTIAL" or fr.get("filled_qty") != 3:
            problems.append(f"③-b PARTIAL 3주 기대 — rc={rc} fill={fr}")
        if rg.sent_amount(out["trades"][0]) != 3 * 84500:
            problems.append(f"③-b spent가 체결분만 세야 한다 — {rg.sent_amount(out['trades'][0])}")

        # ④ 장 마감 뒤 잔량 → EXPIRED
        t4 = _dt(2026, 9, 16, 15, 20, tzinfo=KST)
        rec = _fill_record("KR", "042660", 10, 84500, "0000020263", t4.isoformat())
        f4 = tdp / "trades_260916e_kr.json"; f4.write_text(json.dumps(rec), encoding="utf-8")
        cli = _FakeFillClient({"0000020263": [
            {"ord_qty": 10, "filled_qty": 0, "remain_qty": 10, "avg_price": 0.0}]}, live=84500)
        now_fn, sleep_fn, st = clock(_dt(2026, 9, 16, 15, 31, tzinfo=KST))
        rc = fill.confirm(f4, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, quiet=True)
        out = json.loads(f4.read_text(encoding="utf-8")); fr = out["trades"][0]["fill"]
        if rc != 0 or fr.get("verdict") != "EXPIRED":
            problems.append(f"④ EXPIRED 기대 — rc={rc} fill={fr}")

        # ⑤ 상태 TR 사망 → 예산 소진 → 3
        rec = _fill_record("US", "VST", 30, 141.72, "0000041128", t0.isoformat())
        f5 = tdp / "trades_260916f_us.json"; f5.write_text(json.dumps(rec), encoding="utf-8")
        cli = _FakeFillClient({}, raise_status=True)
        now_fn, sleep_fn, st = clock(t0 + _td(seconds=5))
        rc = fill.confirm(f5, client=cli, cfg=cfg, now_fn=now_fn, sleep_fn=sleep_fn, max_sec=60, quiet=True)
        out = json.loads(f5.read_text(encoding="utf-8"))
        if rc != 3 or out.get("fill_confirmed", {}).get("open_orders") != 1:
            problems.append(f"⑤ 종료 3·open_orders 1 기대 — rc={rc} {out.get('fill_confirmed')}")
        pending_file = f5

        # ⑦ 루브릭 execute 기준 — 확정 파일이 미확정 파일보다 기준을 더 통과한다
        sys.path.insert(0, str(STEPGATE_DIR))
        import stepgate as sg
        rub = json.loads((STEPGATE_DIR / "rubrics" / "trade-run.json").read_text(encoding="utf-8"))
        crit = rub.get("execute", {}).get("criteria", [])
        def passing(deliv, note_):
            n = 0
            for c in crit:
                try:
                    ok, _ = sg.run_criterion(c, {"deliverable": str(deliv), "note": str(note_),
                                                 "today": "2026-09-16", "dir": td})
                except Exception:          # noqa: BLE001
                    ok = False
                n += 1 if ok else 0
            return n
        pend_note = tdp / "n2.md"; pend_note.write_text("## §11 집행 결과\n\nx\n", encoding="utf-8")
        gain = passing(terminal_file, terminal_note) - passing(pending_file, pend_note)
        if gain < 2:
            problems.append(f"⑦ 확정 파일이 execute 기준을 미확정보다 {gain}개 더 통과 (기대 ≥2 — 루브릭에 "
                            f"open_orders·미확정 0건 기준이 없다)")
        ids = [o.get("id") for o in rub.get("execute", {}).get("observations", []) if isinstance(o, dict)]
        if "fill_confirmed" not in ids:
            problems.append("⑦ execute 관찰 항목 fill_confirmed가 없다")

    status = FAIL if problems else PASS
    results.append((status, "(56) 체결 확정 — 체결·정정·취소·만료·TR사망·루브릭", "; ".join(problems)))
    print(f"[{status}] (56) 체결 확정 — 체결·정정·취소·만료·TR사망·루브릭"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_fill_state_machine)


def test_fill_consumers_and_hours():
    """(57) 소비자 정합 — `sent_amount`가 손수 FILLED로 바꾼 행·취소 잔량·FAILED를 옳게 세고,
    `order_excd`가 시세용 코드를 주문용으로 바꾸며, `market_session`이 장외를 가른다."""
    import kis_client as kc
    from datetime import datetime as _dt
    problems = []
    rows = [
        ({"action": "BUY", "status": "FILLED", "qty": 30, "price": 141.72, "fill": {"verdict": "FILLED"}}, 30 * 141.72),
        ({"action": "BUY", "status": "SENT", "qty": 10, "price": 100.0, "fill": {"verdict": "UNFILLED"}}, 1000.0),
        ({"action": "BUY", "status": "SENT", "qty": 10, "price": 100.0,
          "fill": {"verdict": "CANCELLED", "filled_qty": 0}}, None),
        ({"action": "BUY", "status": "SENT", "qty": 10, "price": 100.0,
          "fill": {"verdict": "PARTIAL", "filled_qty": 4, "avg_price": 99.0}}, 4 * 99.0),
        ({"action": "BUY", "status": "FAILED", "qty": 10, "price": 100.0}, None),
        ({"action": "SELL", "status": "SENT", "qty": 10, "price": 100.0}, None),
    ]
    for row, want in rows:
        got = rg.sent_amount(row)
        if (got is None) != (want is None) or (got is not None and abs(got - want) > 1e-6):
            problems.append(f"sent_amount({row['status']}/{(row.get('fill') or {}).get('verdict')}) = {got}, 기대 {want}")
    for src, want in (("NYS", "NYSE"), ("NAS", "NASD"), ("AMS", "AMEX"), ("NYSE", "NYSE"), ("", "NASD")):
        if kc.order_excd(src) != want:
            problems.append(f"order_excd({src!r}) = {kc.order_excd(src)!r}, 기대 {want!r}")
    cases = [
        ("US", _dt(2026, 9, 16, 11, 31, tzinfo=KST), False),    # 9/10 MSFT 장외 전송 시각
        ("US", _dt(2026, 9, 16, 0, 23, tzinfo=KST), True),      # EDT 정규장
        ("US", _dt(2026, 11, 10, 23, 0, tzinfo=KST), False),    # EST — 23:30 개장 전
        ("US", _dt(2026, 11, 10, 23, 45, tzinfo=KST), True),
        ("KR", _dt(2026, 9, 16, 15, 31, tzinfo=KST), False),
        ("KR", _dt(2026, 9, 16, 10, 33, tzinfo=KST), True),
        ("KR", _dt(2026, 9, 19, 10, 33, tzinfo=KST), False),    # 토요일
    ]
    for m, when, want in cases:
        got = kc.market_session(m, when)["is_open"]
        if got != want:
            problems.append(f"market_session({m}, {when:%m-%d %H:%M}) = {got}, 기대 {want}")
    # execute.py의 장외 거부 문구가 실제로 있다
    src = (HERE_DIR / "execute.py").read_text(encoding="utf-8")
    if "집행 거부 — 장외" not in src or "--force-hours" not in src:
        problems.append("execute.py에 장외 거부 경로가 없다")
    status = FAIL if problems else PASS
    results.append((status, "(57) 나간 금액 계산·거래소 코드 정규화·장외 판정", "; ".join(problems)))
    print(f"[{status}] (57) 나간 금액 계산·거래소 코드 정규화·장외 판정"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_fill_consumers_and_hours)


# ============ 미달 강제 — deficit_short (2026-09-16) ============

def _deficit_expect(short: bool, e0=None):
    def check(approved, problems):
        d = approved.get("deficit") or {}
        if approved.get("deficit_short") != short:
            problems.append(f"deficit_short={approved.get('deficit_short')} (기대 {short}) — {d.get('why')}")
        if e0 is not None and sorted(d.get("e0_missing") or []) != sorted(e0):
            problems.append(f"e0_missing={d.get('e0_missing')} (기대 {e0})")
    return check


def _three_props():
    sig = base_signal()
    sig["proposals"] = [
        {**sig["proposals"][0], "ticker": "000660", "name": "SK하이닉스"},
        {**sig["proposals"][0], "ticker": "005930", "name": "삼성전자", "weight_target_pct": 2.0},
        {**sig["proposals"][0], "ticker": "042660", "name": "한화오션"},
    ]
    return sig


def _with_conc_why(sig: dict) -> dict:
    """gap 채우기 시험용 — 매수에 집중 명분을 붙인다(2026-10-19 R05: 명분 없는 매수는 gap 채우기가 집중 공개선에서 멈춘다)."""
    for pp in sig.get("proposals") or []:
        if isinstance(pp, dict) and pp.get("action") == "BUY":
            pp.setdefault("concentration_why", "테스트 — 이 종목·축 집중을 감수하는 명분(gap 채우기 시험)")
    return sig


def _snap_with(price_add: dict, cash=10_000_000):
    s = _mk_snap("kr", cash, [])
    s["prices"].update(price_add)
    return s


_DEF_LIMITS = {**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
               "target_invested_pct": 60.0, "min_proposals_below_target": {"US": 3, "KR": 2},
               "min_distinct_axes_below_target": 2}
_TODAY = datetime.now(KST).strftime("%Y-%m-%d")
# 원장 날짜(논지 created·교정 ts)는 **세션 날짜**다(theses._run_day) — 장 마감 뒤엔 다음 거래일이라 벽시계 날짜와 다르다(2026-10-19).
import sessions as _sessions_mod  # noqa: E402
_SESSION_KR = _sessions_mod.session_date("kr").isoformat()

run_case("(59)-a 미달 + 제안 1건 → deficit_short=true (주문은 승인)",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, []),
         limits_patch=_DEF_LIMITS, extra_map=_AXIS_MAP,
         expect_orders=1, expect_approved=_deficit_expect(True))
run_case("(59)-b 미달 + 제안 3건·축 2개 → deficit_short=false",
         signal=_with_conc_why(_three_props()), snapshot=_snap_with({"042660": {"price": 84500, "change_pct": 0}}),
         limits_patch=_DEF_LIMITS, extra_map=_AXIS_MAP,
         expect_approved=_deficit_expect(False, e0=[]))
run_case("(59)-c 미달 + 같은 축 2건뿐 → 축 부족으로 short",
         signal=base_signal(proposals=_three_props()["proposals"][:2]),
         snapshot=_mk_snap("kr", 10_000_000, []),
         limits_patch=_DEF_LIMITS, extra_map=_AXIS_MAP,
         expect_approved=_deficit_expect(True))
run_case("(59)-d 오늘 armed한 논지에 제안이 없으면 e0_missing으로 short",
         signal=_three_props(), snapshot=_snap_with({"042660": {"price": 84500, "change_pct": 0}}),
         limits_patch=_DEF_LIMITS, extra_map=_AXIS_MAP,
         extra_theses={"theses": [{"id": "t1", "market": "KR", "ticker": "035420", "status": "armed",
                                   "created": _SESSION_KR},
                                  {"id": "t2", "market": "US", "ticker": "AAPL", "status": "armed",
                                   "created": _TODAY},
                                  {"id": "t3", "market": "KR", "ticker": "005830", "status": "armed",
                                   "created": "2026-09-01"}]},
         expect_approved=_deficit_expect(True, e0=["035420"]))
run_case("(59)-e 유효성 거부(유니버스 밖)는 안 세고 한도 거부는 센다",
         signal=base_signal(proposals=[
             {**base_signal()["proposals"][0], "ticker": "999999", "name": "밖"},
             {**base_signal()["proposals"][0], "ticker": "000660"},
             {**base_signal()["proposals"][0], "ticker": "005930", "weight_target_pct": 50.0}]),
         snapshot=_mk_snap("kr", 10_000_000, []),
         limits_patch={**_DEF_LIMITS, "per_position_max_pct": 12.0},
         extra_map=_AXIS_MAP,
         # 999999는 유니버스 밖(안 셈) · 000660 승인 · 005930은 상한 거부(셈) → 2건이지만 같은 축 → short
         expect_approved=_deficit_expect(True))
run_case("(59)-f 판단 목표 이상이면 검사 꺼짐",
         signal=base_signal(allocation=base_alloc(target_invested_pct=80, cash_reason="하락장 — KOSPI 60일선 아래 -3.1%",
                                                  cash_release_when="KOSPI 60일선 회복")),
         snapshot=_mk_snap("kr", 1_000_000, [{"market": "KR", "ticker": "005930", "name": "삼성전자",
                                              "qty": 100, "avg_price": 70000, "price": 71000,
                                              "eval_amt": 7_100_000, "pnl_pct": 1.43}]),
         limits_patch=_DEF_LIMITS, extra_map=_AXIS_MAP,
         expect_approved=_deficit_expect(False))
run_case("(59)-g 건수·축 요구가 없으면 gap 커버만 본다(키워서 채우면 short 아님)",
         signal=_with_conc_why(base_signal()), snapshot=_mk_snap("kr", 10_000_000, []),
         limits_patch={**_DEF_LIMITS, "min_proposals_below_target": None, "min_distinct_axes_below_target": None},
         extra_map=_AXIS_MAP, expect_approved=_deficit_expect(False))


def test_deficit_gate_wiring():
    """(59)-h 루브릭 dispatch·no_trade에 deficit_met 관찰 항목과 `deficit_short: false` 기준이 있고,
    approved 파일이 그 기준을 실제로 통과한다(패턴 미출력)."""
    problems = []
    sys.path.insert(0, str(STEPGATE_DIR))
    import stepgate as sg
    rub = json.loads((STEPGATE_DIR / "rubrics" / "trade-run.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as td:
        ok_f = Path(td) / "approved_ok.json"; ok_f.write_text('{"orders": [], "deficit_short": false}', encoding="utf-8")
        bad_f = Path(td) / "approved_bad.json"; bad_f.write_text('{"orders": [], "deficit_short": true}', encoding="utf-8")
        for cp, key in (("dispatch", "deliverable"), ("no_trade", "approved")):
            ids = [o.get("id") for o in rub.get(cp, {}).get("observations", []) if isinstance(o, dict)]
            if "deficit_met" not in ids:
                problems.append(f"{cp}에 deficit_met 관찰 항목이 없다")
            crit = rub.get(cp, {}).get("criteria", [])
            def n_pass(f):
                n = 0
                for c in crit:
                    try:
                        ok, _ = sg.run_criterion(c, {key: str(f), "note": str(f), "signal": str(f), "dir": td})
                    except Exception:      # noqa: BLE001
                        ok = False
                    n += 1 if ok else 0
                return n
            if n_pass(ok_f) - n_pass(bad_f) < 1:
                problems.append(f"{cp} 기준이 deficit_short true/false를 가르지 않는다")
    status = FAIL if problems else PASS
    results.append((status, "(59)-h 미달 게이트 배선(dispatch·no_trade)", "; ".join(problems)))
    print(f"[{status}] (59)-h 미달 게이트 배선(dispatch·no_trade)"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_deficit_gate_wiring)


# ============ 입금된 돈 안에서: 예약·회전 매도 (2026-09-16) ============

_HELD_005930 = [{"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 10,
                 "avg_price": 70000, "price": 71000, "eval_amt": 710_000, "pnl_pct": 1.43}]
# (2026-09-22) 상수 예약(25%)은 지웠다 — 예약은 armed 논지의 **다음 칸**(size_pct 25% × KR 자산 1.71M = 427,500)이 기본값이다.
_ARMED_OTHER = {"theses": [{"id": "a1", "market": "KR", "ticker": "035420", "status": "armed",
                            "created": "2026-09-10",
                            "entry_triggers": [{"id": "e1", "check": "price <= 1", "size_pct": 25.0}]}]}
_FUND_LIMITS = {**ROOMY, "funding_min_fill_ratio": 0.5,
                "funding_max_per_run": 1, "min_proposals_below_target": None, "min_distinct_axes_below_target": None}


def _expect_funding(approved, problems):
    orders = approved.get("orders") or []
    sells = [o for o in orders if o["action"] == "SELL" and o.get("source") == "funding"]
    buys = [o for o in orders if o["action"] == "BUY"]
    if len(sells) != 1 or sells[0]["ticker"] != "005930":
        problems.append(f"회전 매도 1건(005930) 기대 — {[(o['ticker'], o['action'], o.get('source')) for o in orders]}")
    if not buys or buys[0]["qty"] <= 3:
        problems.append(f"매도 대금으로 매수 수량이 늘어야 한다(>3) — {[(o['ticker'], o['qty']) for o in buys]}")
    if orders and orders[0]["action"] != "SELL":
        problems.append("회전 매도가 매수보다 앞에 있어야 한다")
    if not approved.get("funding"):
        problems.append("approved.funding 기록이 없다")
    blob = json.dumps(approved, ensure_ascii=False)
    if "입금" in blob or "환전" in blob:
        problems.append("사유에 입금·환전 요구가 있다")
    if "예약" not in blob:
        problems.append("판단된 예약(논지 원장 기본값) 기록이 없다")


run_case("(60)-a 현금 부족 → 대기 논지 몫 예약 → 최대 보유 회전 매도로 메운다(입금 요구 0)",
         signal=base_signal(), snapshot=_mk_snap("kr", 1_000_000, list(_HELD_005930)),
         limits_patch=_FUND_LIMITS, extra_theses=_ARMED_OTHER,
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_approved=_expect_funding)


def _expect_no_funding_shaved(approved, problems):
    orders = approved.get("orders") or []
    if any(o.get("source") == "funding" for o in orders):
        problems.append("funding_max_per_run 0인데 회전 매도가 나왔다")
    buys = [o for o in orders if o["action"] == "BUY"]
    if not buys or buys[0]["qty"] != 3:
        problems.append(f"예약(427,500) 뒤 현금 572,500으로 3주 기대 — {[(o['ticker'], o['qty']) for o in buys]}")


run_case("(60)-b 회전 꺼짐(funding_max_per_run 0) → 예약 뒤 현금만큼만 깎아서 산다",
         signal=base_signal(), snapshot=_mk_snap("kr", 1_000_000, list(_HELD_005930)),
         limits_patch={**_FUND_LIMITS, "funding_max_per_run": 0}, extra_theses=_ARMED_OTHER,
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=1, expect_approved=_expect_no_funding_shaved)

run_case("(60)-c 팔 것도 없고 1주도 못 사면 그때만 거부 — 사유에 입금 요구 없음",
         signal=base_signal(), snapshot=_mk_snap("kr", 50_000, []),
         limits_patch=_FUND_LIMITS,
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=0, expect_reject_contains="회전 매도 대상도 없다")


# ============ 손절은 장중 터치 즉시 — 기계가 판다 (2026-09-16) ============

def _held_thesis(check: str, sell_pct=None):
    t = {"id": "t-held", "market": "KR", "ticker": "005930", "status": "held", "created": "2026-09-10",
         "exit_triggers": [{"id": "x1", "check": check, "when": "테스트"}]}
    if sell_pct:
        t["exit_triggers"][0]["sell_pct"] = sell_pct
    return {"theses": [t]}


def _expect_sell(qty, full):
    def check(approved, problems):
        sells = [o for o in (approved.get("orders") or []) if o["action"] == "SELL"]
        if len(sells) != 1 or sells[0]["ticker"] != "005930":
            problems.append(f"규율 SELL 005930 1건 기대 — {[(o['ticker'], o['action']) for o in approved.get('orders') or []]}")
            return
        o = sells[0]
        if o["qty"] != qty or bool(o.get("full_exit")) != full or o.get("source") != "discipline":
            problems.append(f"qty={o['qty']} full_exit={o.get('full_exit')} source={o.get('source')} (기대 {qty}/{full}/discipline)")
        if o["price"] % 100:
            problems.append(f"매도 지정가가 호가 격자 밖이다: {o['price']}")
    return check


def _expect_no_sell(approved, problems):
    if any(o["action"] == "SELL" for o in (approved.get("orders") or [])):
        problems.append("조건 미충족인데 매도가 나왔다")


run_case("(61)-a 손절 `price <= 72000` 터치(시세 71,000) → 규율 SELL 전량 — 모델 재량 없음",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, list(_HELD_005930)),
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         extra_theses=_held_thesis("price <= 72000"),
         expect_approved=_expect_sell(10, True))
run_case("(61)-b 목표 `price >= 70000` 도달 → 규율 SELL 50%",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, list(_HELD_005930)),
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         extra_theses=_held_thesis("price >= 70000"),
         expect_approved=_expect_sell(5, False))
run_case("(61)-c 손절선 아래 아님 → 매도 없음",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, list(_HELD_005930)),
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         extra_theses=_held_thesis("price <= 60000"),
         expect_approved=_expect_no_sell)


def test_stops_offhours_and_close():
    """(61)-d stops.py는 장외면 아무것도 안 하고, fill은 전량 매도 확정 뒤 논지를 closed로 옮긴다."""
    import stops, fill
    problems = []
    saved = stops.market_session
    stops.market_session = lambda m, now=None: {"is_open": False, "why": "테스트 장외", "session_date": datetime.now(KST).date()}
    try:
        row = stops.watch("KR", send=True)
        if row.get("open") or row.get("sent"):
            problems.append(f"장외인데 돌았다: {row}")
    finally:
        stops.market_session = saved
    # 논지는 임시 저장소에 심는다 — fill은 저장소 논지 원장을 한 논지씩 고친다(2026-10-17)
    t = {"action": "SELL", "market": "KR", "ticker": "042660", "status": "SENT", "source": "discipline",
         "full_exit": True, "discipline_reasons": ["손절"],
         "fill": {"verdict": "FILLED", "filled_qty": 4, "avg_price": 81300}}
    with testkit.db_case():
        th0 = [{"id": "t1", "market": "KR", "ticker": "042660", "status": "held"},
               {"id": "t2", "market": "KR", "ticker": "042660", "status": "armed"}]
        testkit.seed(theses=th0)
        problems += _seed_check(theses=th0, watchlist=False)
        fill._close_theses_after_exit(t, datetime.now(KST), lambda *a, **k: None)
        st = {x["id"]: x["status"] for x in db.theses_all()}
        if st != {"t1": "closed", "t2": "armed"}:
            problems.append(f"held만 closed 기대 — {st}")
        if not any(h.get("status") == "closed" for h in (db.thesis_get("t1") or {}).get("history") or []):
            problems.append("closed 이력(history)이 안 남았다")
    with testkit.db_case():
        t["full_exit"] = False
        th3 = [{"id": "t3", "market": "KR", "ticker": "042660", "status": "held"}]
        testkit.seed(theses=th3)
        problems += _seed_check(theses=th3, watchlist=False)
        fill._close_theses_after_exit(t, datetime.now(KST), lambda *a, **k: None)
        if (db.thesis_get("t3") or {}).get("status") != "held":
            problems.append("부분 매도인데 논지를 닫았다")
    status = FAIL if problems else PASS
    results.append((status, "(61)-d stops 장외 무동작 · 전량 매도 후 논지 closed", "; ".join(problems)))
    print(f"[{status}] (61)-d stops 장외 무동작 · 전량 매도 후 논지 closed"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_stops_offhours_and_close)


# ============ 실패한 명령이 다시 실패하지 않게 (2026-09-16) ============

def test_command_failures_fixed():
    """(62) batch 파싱 · list --id · cycle 코드 검증 · stamp 도구 · review 자정 · portfolio_equity 최신 ·
    map --refresh 보존 · 표기 검사 · thesis_unsynced 갱신 · verify_numbers 3항 · execute 스탬프."""
    import scenarios, market_map as mm, stage, review, crosscheck as cc, verify_numbers as vn, execute as ex
    problems = []
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # batch 파싱(JSONL·JSON 배열·주석)
        b = tdp / "b.jsonl"; b.write_text('# c\n{"id":"A","realized":"y"}\n\n{"id":"B","realized":"n","acted":"n"}\n', encoding="utf-8")
        rows = scenarios._read_batch(b)
        if [r["id"] for r in rows] != ["A", "B"]:
            problems.append(f"batch 파싱 — {rows}")
        b2 = tdp / "b.json"; b2.write_text('[{"id":"C","realized":"y"}]', encoding="utf-8")
        if scenarios._read_batch(b2)[0]["id"] != "C":
            problems.append("batch JSON 배열 파싱 실패")
        # cycle 코드 검증 — 없는 코드는 기록 거부
        import io, contextlib
        with testkit.db_case(tdp / "db_cy"):          # 지도 섹터·섹터 관찰 기록은 저장소(빈 임시 저장소)
            _sec = [{"code": "0025", "name": "운수장비", "market": "KR"}]
            testkit.seed(sectors=_sec)
            if not db.sector_get("0025"):
                problems.append("심기 실패 — 섹터 0025가 저장소에 안 보인다")
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                rc = mm.cycle("kr", "", "0003,0025")
            if rc != 2 or db.log_rows("sector_watch"):
                problems.append(f"잘못된 섹터 코드가 기록됐다 rc={rc}")
        # stamp 도구 — 절 끝에 실제 시각으로, 멱등
        note = tdp / "n.md"; note.write_text("## §6 종목\n\nx\n\n## §7 제안\n\ny\n", encoding="utf-8")
        import types as _t
        stage.cmd_stamp(_t.SimpleNamespace(note=str(note), sec="§6", worklog="", stage=""))
        stage.cmd_stamp(_t.SimpleNamespace(note=str(note), sec="§6", worklog="", stage=""))
        txt = note.read_text(encoding="utf-8")
        if txt.count("<!-- ✓ §6 -->") != 1 or txt.index("<!-- ✓ §6 -->") > txt.index("## §7"):
            problems.append("stamp가 절 안에 한 번만 찍혀야 한다")
        if datetime.now(KST).strftime("%Y-%m-%d") not in txt:
            problems.append("stamp에 오늘 날짜(실제 시각)가 없다")
        # review 자정 넘긴 파일 폴백
        saved_j = review.JOURNAL; review.JOURNAL = tdp
        try:
            (tdp / "trades_260916_us.json").write_text(json.dumps({"signal_ref": "signals/signal_260915_us.json",
                "trades": [{"ticker": "VST", "action": "BUY", "status": "SENT"}]}), encoding="utf-8")
            rows = review.trade_rows("US", "2026-09-15")
            if not rows or rows[0]["ticker"] != "VST":
                problems.append(f"review가 다음 날 파일을 못 찾는다 — {rows}")
            if review.trade_rows("US", "2026-09-14") is not None:
                problems.append("무관한 세션에 다음 날 파일을 붙였다")
        finally:
            review.JOURNAL = saved_j
        # portfolio_equity — 저널(자산 곡선) 행이 스냅샷보다 새로우면 그것을 쓴다(자산 곡선은 임시 저장소에 심는다)
        saved_rg = rg.DATA_DIR
        rg.DATA_DIR = tdp / "data"
        rg.DATA_DIR.mkdir()
        try:
            # ★ 날짜는 오늘 기준 상대값 — 고정 날짜(09-15)는 10-01에 들어온 '스냅샷 신선도' 검사에 걸려 시간이 지나면 실패했다.
            _n = datetime.now(KST)
            _snap_t, _row_t = _n - timedelta(hours=2), _n - timedelta(hours=1)
            (rg.DATA_DIR / f"snapshot_{_snap_t:%y%m%d}_us.json").write_text(json.dumps({"generated_at": _snap_t.isoformat(),
                "balance": {"currency": "USD", "cash": 100000, "positions": [], "exchange_rate": 1300.0}}), encoding="utf-8")
            _eq = [{"ts": _row_t.isoformat(), "market": "US", "currency": "USD", "cash": 95707.7,
                    "positions": [{"ticker": "VST", "qty": 30, "price": 141.65, "eval_amt": 4249.5}]}]
            with testkit.db_case(tdp / "db_eq"):
                testkit.seed(equity=_eq)
                problems += _seed_check(equity=_eq, watchlist=False)
                pf = rg.portfolio_equity("KR", {"currency": "KRW", "cash": 1_000_000, "positions": []})
            us = (pf.get("parts") or {}).get("US") or {}
            if not pf.get("ok") or abs(us.get("invested", 0) - 4249.5) > 1 or "equity_curve" not in str(us.get("source")):
                problems.append(f"저널 행을 안 썼다 — {us.get('source')} invested={us.get('invested')}")
        finally:
            rg.DATA_DIR = saved_rg
        # map --refresh — 3단 절 보존
        saved_st = stage.ANALYSIS
        stage.ANALYSIS = tdp / "analysis"; stage.ANALYSIS.mkdir()
        _dbm = _ctxlib.ExitStack()
        try:
            _dbm.enter_context(testkit.db_case(tdp / "db_map"))   # 지도 축은 빈 임시 저장소에 심는다
            _axm = [{"id": "ax", "name": "A", "direction": "+", "horizon_weeks": 8,
                     "confidence": 0.6, "updated": datetime.now(KST).strftime("%Y-%m-%d"),
                     "beneficiaries": [{"ticker": "X", "order": 1, "priced": False}]}]
            testkit.seed(axes=_axm)
            problems += _seed_check(axes=_axm, watchlist=False)
            _cap_ok = stage.capture_ok
            stage.capture_ok = lambda st, mk: None      # 이 시험은 map --refresh의 절 보존만 잰다(capture 전제는 test_notes가 잰다 · v4)
            import market_map as _mm_map
            _pma = _mm_map.priced_measure_all            # 지도 단계 자동 측정(v9 · U05) — 증권사를 부르지 않게(측정은 test_judgment가 잰다)
            _mm_map.priced_measure_all = lambda *a_, **k_: {"axes": 0, "complete": 0, "failed": 0}
            with contextlib.redirect_stdout(io.StringIO()):
                stage.cmd_map(_t.SimpleNamespace(market="kr", stamp="260916", refresh=False))
                f = stage.ANALYSIS / "전망_260916_kr.md"
                t2 = f.read_text(encoding="utf-8").replace("<!-- TODO 전망 -->", "모델이 쓴 전망 본문 XYZ")
                f.write_text(t2, encoding="utf-8")
                stage.cmd_map(_t.SimpleNamespace(market="kr", stamp="260916", refresh=True))
            t3 = f.read_text(encoding="utf-8")
            if "모델이 쓴 전망 본문 XYZ" not in t3 or "### ax — A" not in t3:
                problems.append("map --refresh가 3단 절을 지웠거나 열린 전망 표를 안 만들었다")
        finally:
            stage.capture_ok = _cap_ok if "_cap_ok" in dir() else stage.capture_ok
            if "_pma" in dir():
                _mm_map.priced_measure_all = _pma
            _dbm.close()
            stage.ANALYSIS = saved_st
        # 표기 검사
        mk = {r[0]: r for r in cc.check_markers("③ **사는 쪽** a\n④ 안 사는 쪽 b\n")}
        if mk["표기 `③ 사는 쪽`"][1] or "장식 변형" not in mk["표기 `③ 사는 쪽`"][2] or not mk["표기 `④ 안 사는 쪽`"][1]:
            problems.append("표기 검사가 볼드 변형을 못 잡는다")
        # thesis_unsynced 갱신 — 논지 상태는 저장소에서 지금 읽는다(임시 저장소에 held로 심는다)
        with testkit.db_case(tdp / "db_th"):
            _th = [{"id": "kr-034020", "market": "KR", "ticker": "034020", "status": "held"}]
            testkit.seed(theses=_th)
            problems += _seed_check(theses=_th, watchlist=False)
            tr = tdp / "trades_260916_kr.json"
            tr.write_text(json.dumps({"trades": [{"ticker": "034020", "action": "BUY", "status": "SENT", "fill": {"verdict": "FILLED"}}],
                                      "thesis_unsynced": [{"ticker": "034020", "thesis_status": ["armed"]}]}), encoding="utf-8")
            if ex.refresh_thesis_unsynced(tr) != []:
                problems.append("thesis_unsynced가 held 반영 뒤 []가 아니다")
            if not json.loads(tr.read_text(encoding="utf-8")).get("thesis_resync"):
                problems.append("thesis_resync 전후 기록이 없다")
        # verify_numbers — 여러 식 · 조/억 · 스탬프
        if not vn.derived_ok("a 3 (계산: 1+1) b 7 (계산: 3+4)", 7):
            problems.append("두 번째 계산식을 안 본다")
        if not vn.unit_ok("2.4조", 2.4e12, {2.4326e12}):
            problems.append("조 단위 자릿수 허용(2.35~2.45조)이 없다")
        if vn.unit_ok("2.4조", 2.4e12, {2.6e12}) or vn.unit_ok("24,326억", 2.4326e12, {2.4e12}):
            problems.append("조·억 단위 허용이 너무 넓다")
        if ex._stamp_from(Path("signals/approved_260915_us.json")) != "260915":
            problems.append("execute 스탬프를 approved 파일명에서 못 딴다")
        # 시장 선택 — 열린 시장, 둘 다 닫혔으면 다음에 열리는 시장(시각 창 없음)
        from datetime import datetime as _dt
        cases = [(_dt(2026, 9, 18, 13, 0, tzinfo=KST), "kr", True), (_dt(2026, 9, 18, 0, 30, tzinfo=KST), "us", True),
                 (_dt(2026, 9, 18, 8, 0, tzinfo=KST), "kr", False), (_dt(2026, 9, 18, 17, 0, tzinfo=KST), "us", False),
                 (_dt(2026, 9, 18, 15, 45, tzinfo=KST), "us", False)]
        for when, want, want_open in cases:
            m, is_open, _why = stage.pick_market(when)
            if (m, is_open) != (want, want_open):
                problems.append(f"pick_market({when:%m-%d %H:%M}) = {m}/{is_open}, 기대 {want}/{want_open}")
    status = FAIL if problems else PASS
    results.append((status, "(62) 실패한 명령 재발 방지 — batch·cycle·stamp·review·분모·refresh·표기·unsynced·verify", "; ".join(problems)))
    print(f"[{status}] (62) 실패한 명령 재발 방지 — batch·cycle·stamp·review·분모·refresh·표기·unsynced·verify"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_command_failures_fixed)


# ============ 같은 승인은 한 번만 · 승인 초과는 게이트가 센다 (2026-09-21) ============

def _trow(tk, act, src, status="SENT", verdict="FILLED", ref="approved_X.json", filled=None, delta=None,
          ts="2026-09-21T12:55:42+09:00", order_no="1", extra=None):
    f = {"verdict": verdict, "qty_before": 0}
    if filled is not None:
        f["filled_qty"] = filled
        f["confirmed_at"] = ts
    if delta is not None:
        f["delta"] = delta
    r = {"ts": ts, "status": status, "market": "KR", "ticker": tk, "action": act, "source": src,
         "qty": 46, "price": 85400, "result": {"order_no": order_no}, "fill": f}
    if ref is not None:
        r["approved_ref"] = ref
    if extra:
        r.update(extra)
    return r


def test_idempotent_send():
    """(63)-a `execute.dedupe_against_day` — 같은 승인 파일명의 같은 건은 판정과 무관하게 건너뛴다."""
    import execute as ex
    problems = []
    orders = [{"ticker": "034020", "action": "SELL", "source": "funding", "qty": 46, "price": 85500},
              {"ticker": "373220", "action": "BUY", "source": "llm", "qty": 11, "price": 353000}]
    prior = {"trades": [_trow("034020", "SELL", "funding")]}
    to, dup, held = ex.dedupe_against_day(orders, prior, "approved_X.json")
    if [o["ticker"] for o in to] != ["373220"] or [o["ticker"] for o in dup] != ["034020"] or held:
        problems.append(f"같은 승인 재호출 — 보낼 {[o['ticker'] for o in to]} 건너뜀 {[o['ticker'] for o in dup]}")
    to, dup, held = ex.dedupe_against_day(orders, prior, "approved_X_reissue.json")
    if len(to) != 2 or dup or held:
        problems.append("재발행 파일명은 통과해야 한다")
    prior_open = {"trades": [_trow("034020", "SELL", "funding", verdict="UNFILLED", ref="approved_Y_stops_1300.json")]}
    to, dup, held = ex.dedupe_against_day(orders, prior_open, "approved_Y_stops_1320.json")
    if [o["ticker"] for o in held] != ["034020"] or len(to) != 1:
        problems.append(f"열린 주문 위 같은 매도는 보류돼야 한다 — held {[o['ticker'] for o in held]}")
    prior_failed = {"trades": [_trow("034020", "SELL", "funding", status="FAILED", verdict="REJECTED")]}
    to, dup, held = ex.dedupe_against_day(orders, prior_failed, "approved_X.json")
    if len(to) != 2:
        problems.append("FAILED(나가지 않은 것)는 다시 보낼 수 있어야 한다")
    prior_legacy = {"trades": [_trow("034020", "SELL", "funding", ref=None)]}
    to, dup, held = ex.dedupe_against_day(orders, prior_legacy, "approved_anything.json")
    if [o["ticker"] for o in dup] != ["034020"]:
        problems.append("approved_ref 없는 옛 행은 보수적으로 같은 것으로 봐야 한다")
    # 재발 경로 자체 — funding 전용 dedupe가 아니라 전 주문 dedupe여야 한다
    src = (Path(__file__).parent / "execute.py").read_text(encoding="utf-8")
    if "dedupe_against_day(orders" not in src or "approved_snapshot(approved)" not in src:
        problems.append("execute.main이 dedupe_against_day·approved_snapshot을 쓰지 않는다")
    status = FAIL if problems else PASS
    results.append((status, "(63)-a 멱등 전송 — 같은 승인은 한 번만", "; ".join(problems)))
    print(f"[{status}] (63)-a 멱등 전송 — 같은 승인은 한 번만" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_idempotent_send)


def _rec_0921():
    """2026-09-21 사고 모양 — 승인 [SELL 034020 46 · SELL 005930 4 · BUY 373220 11], 집행 SELL 034020 92."""
    ao = [{"ticker": "034020", "action": "SELL", "source": "funding", "qty": 46, "price": 85500},
          {"ticker": "005930", "action": "SELL", "source": "llm", "qty": 4, "price": 274000},
          {"ticker": "373220", "action": "BUY", "source": "llm", "qty": 11, "price": 353000}]
    run = {"generated_at": "x", "approved_ref": "signals/approved_260921_kr.json",
           "approved_at": "2026-09-21T12:51:53+09:00", "approved_orders": ao}
    return {"approved_ref": run["approved_ref"], "approved_at": run["approved_at"], "approved_orders": ao,
            "runs": [dict(run), dict(run), {**run, "approved_ref": "/abs/path/signals/approved_260921_kr.json"}],
            "trades": [
                _trow("034020", "SELL", "funding", filled=46, order_no="25560"),
                _trow("034020", "SELL", "funding", filled=46, order_no="26281", ts="2026-09-21T13:11:10+09:00",
                      extra={"incident": "★ 중복 전송 — 손으로 쓴 서술"}),
                _trow("005930", "SELL", "llm", filled=4, order_no="26290"),
                _trow("373220", "BUY", "llm", filled=11, order_no="26291"),
            ]}


def test_reconcile():
    """(63)-b `fill.reconcile` — 종목·방향별 승인 합 vs 집행 합."""
    import fill
    problems = []
    r = fill.reconcile(_rec_0921())
    if r["overfill"] != 1 or r["overfill_list"][0]["executed"] != 92 or r["overfill_list"][0]["approved"] != 46:
        problems.append(f"승인 46/집행 92가 초과 1건으로 잡혀야 한다 — {r['overfill_list']}")
    if r["sent_rows"] != 4 or r["refs"] != ["approved_260921_kr.json"] or not r["approved_known"]:
        problems.append(f"sent_rows={r['sent_rows']} refs={r['refs']} known={r['approved_known']}")
    # 즉시 판정 PARTIAL(delta만) · REJECTED · UNFILLED
    rec = _rec_0921()
    rec["trades"] = [_trow("373220", "BUY", "llm", verdict="PARTIAL", delta=2),
                     _trow("005930", "SELL", "llm", verdict="REJECTED", delta=0),
                     _trow("034020", "SELL", "funding", verdict="UNFILLED")]
    r = fill.reconcile(rec)
    if r["executed"] != {"373220:BUY": 2, "005930:SELL": 0, "034020:SELL": 0} or r["overfill"]:
        problems.append(f"즉시 판정 계수 — {r['executed']} 초과 {r['overfill']}")
    # 승인을 모르는 옛 참조 — 초과라고 말하지 않는다
    rec = _rec_0921()
    rec["runs"].append({"generated_at": "y", "approved_ref": "signals/approved_PAPERTEST_kr.json"})
    r = fill.reconcile(rec)
    if r["unknown_refs"] != ["approved_PAPERTEST_kr.json"] or r["approved_known"] or r["overfill"] != 1:
        problems.append(f"미상 참조 — unknown={r['unknown_refs']} known={r['approved_known']} 초과={r['overfill']}")
    rec = {"trades": [_trow("034020", "SELL", "funding", filled=46, ref=None)]}
    r = fill.reconcile(rec)
    if r["approved_known"] or r["overfill"]:
        problems.append("승인 참조가 하나도 없으면 초과를 말할 수 없다")
    status = FAIL if problems else PASS
    results.append((status, "(63)-b 승인 대비 대조 — 46/92 초과·즉시 판정·미상 참조", "; ".join(problems)))
    print(f"[{status}] (63)-b 승인 대비 대조 — 46/92 초과·즉시 판정·미상 참조" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_reconcile)


def test_incident_record():
    """(63)-c `fill.finalize` — 사고 자동 기록(멱등)·행 스탬프·생성줄·crosscheck·execute rc."""
    import fill, stage, crosscheck as cc, execute as ex
    problems = []
    saved = fill.JOURNAL_DIR
    with tempfile.TemporaryDirectory() as td, testkit.db_case():   # 사고 기록은 저장소 op_incident(빈 임시 저장소)
        tdp = Path(td)
        fill.JOURNAL_DIR = tdp
        try:
            if db.log_rows("incidents"):
                problems.append("빈 임시 저장소에 사고 기록이 있다(격리 실패)")
            p = tdp / "trades_260921_kr.json"
            rec = _rec_0921()
            now = datetime(2026, 9, 21, 15, 0, tzinfo=KST)
            rec = fill.finalize(rec, p, now)
            r = rec["reconcile"]
            if r.get("incidents") != ["INC-260921-1"] or not r.get("incident_recorded"):
                problems.append(f"사고 id — {r.get('incidents')} recorded={r.get('incident_recorded')}")
            rows = rec["trades"]
            if rows[0].get("incident_id") or rows[1].get("incident_id") != "INC-260921-1":
                problems.append("승인을 넘어선 둘째 행에만 incident_id가 찍혀야 한다")
            if rows[1].get("incident") != "★ 중복 전송 — 손으로 쓴 서술":
                problems.append("기존 incident 서술이 보존돼야 한다")
            lines = db.log_rows("incidents")
            fill.finalize(rec, p, now)                       # 두 번째 — 멱등
            lines2 = db.log_rows("incidents")
            if len(lines) != 1 or len(lines2) != 1 or lines2[0].get("note") != "★ 중복 전송 — 손으로 쓴 서술":
                problems.append(f"사고 기록(op_incident) {len(lines)}/{len(lines2)}행 (기대 1/1)")
            elif (lines2[0].get("id"), lines2[0].get("stamp"), lines2[0].get("market")) != ("INC-260921-1", "260921", "kr"):
                problems.append(f"사고 기록 칸 {lines2[0].get('id')} · {lines2[0].get('stamp')} · {lines2[0].get('market')}")
            rec = fill._with_summary(rec, now, p)
            line = stage.gen_fill_line(rec["fill_confirmed"])
            if "승인 초과 **1건**" not in line or "사고 기록 INC-260921-1" not in line:
                problems.append(f"생성줄 — {line}")
            clean = {"trades": [_trow("005930", "SELL", "llm", filled=4)], "approved_ref": "signals/a.json",
                     "approved_at": "t", "approved_orders": [{"ticker": "005930", "action": "SELL", "qty": 4}], "runs": []}
            clean = fill._with_summary(clean, now, tdp / "trades_260922_kr.json")
            if "승인 초과 **0건**" not in stage.gen_fill_line(clean["fill_confirmed"]) or clean["reconcile"]["overfill"]:
                problems.append("깨끗한 기록의 생성줄에 `승인 초과 **0건**`이 있어야 한다")
            # crosscheck
            p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            if not cc.check_reconcile(p)[0][1]:
                problems.append("초과+사고 기록은 crosscheck 통과여야 한다")
            bad = dict(rec); bad["reconcile"] = {**rec["reconcile"], "incident_recorded": False, "incidents": []}
            p2 = tdp / "trades_260923_kr.json"; p2.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            if cc.check_reconcile(p2)[0][1]:
                problems.append("초과인데 사고 기록이 없으면 crosscheck 실패여야 한다")
            p3 = tdp / "trades_260924_kr.json"; p3.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
            if not cc.check_reconcile(p3)[0][1]:
                problems.append("초과 0건은 crosscheck 통과여야 한다")
            # execute가 기록을 쓸 때도 대조가 붙는다
            p4 = tdp / "trades_260925_kr.json"
            merged = ex.merge_day_record(p4, _rec_0921())
            if (merged.get("reconcile") or {}).get("overfill") != 1 or not ex._overfill(p4).get("incident_recorded"):
                problems.append("merge_day_record가 finalize를 붙이지 않는다")
            if ex._report_overfill(p4) is not True:
                problems.append("_report_overfill이 True(rc 4 경로)여야 한다")
        finally:
            fill.JOURNAL_DIR = saved
    status = FAIL if problems else PASS
    results.append((status, "(63)-c 사고 자동 기록 — INC id·멱등·생성줄·crosscheck·rc 4", "; ".join(problems)))
    print(f"[{status}] (63)-c 사고 자동 기록 — INC id·멱등·생성줄·crosscheck·rc 4" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_incident_record)

_t63d = _held_thesis("price >= 70000")
_t63d["theses"][0]["exit_triggers"][0]["size_pct"] = 30
run_case("(63)-d 원장 표기 size_pct 30% → 규율 SELL 3주",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, list(_HELD_005930)),
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         extra_theses=_t63d, expect_approved=_expect_sell(3, False))


def _expect_qty(qty: int):
    """(2026-09-22) 이벤트 계수는 지웠다 — 달력에 무엇이 있든 수량이 같아야 한다(이벤트는 할인이 아니라 베팅)."""
    def check(approved, problems):
        buys = [o for o in (approved.get("orders") or []) if o["action"] == "BUY"]
        if len(buys) != 1 or buys[0]["qty"] != qty:
            problems.append(f"BUY 수량 {[o['qty'] for o in buys]} (기대 {qty})")
    return check


_ABOVE_FLOOR = _mk_snap("kr", 10_000_000, [{"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 70,
                                            "avg_price": 70000, "price": 71000, "eval_amt": 4_970_000, "pnl_pct": 1.4}])
run_case("(63)-e1 이벤트=베팅 — 지표 D-0이어도 크기 불변",
         signal=_with_conc_why(base_signal()), snapshot=_ABOVE_FLOOR,
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         extra_calendar=[{"date": _TODAY, "event": "CPI", "kind": "지표"}],
         expect_approved=_expect_qty(51))
run_case("(63)-e2 달력 없음 — 같은 수량",
         signal=_with_conc_why(base_signal()), snapshot=_ABOVE_FLOOR,
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         expect_approved=_expect_qty(51))
run_case("(63)-e3 메모 kind는 계수 없음",
         signal=_with_conc_why(base_signal()), snapshot=_ABOVE_FLOOR,
         limits_patch={**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
                       "min_proposals_below_target": None},
         extra_calendar=[{"date": _TODAY, "event": "UPPITY 휴간", "kind": "메모"}],
         expect_approved=_expect_qty(51))


def test_sessions_weekend_stamp_and_holidays():
    """(63)-f/g 세션 — KST 토요일 스탬프의 US run은 ET 금요일 세션 · 휴장은 세션이 아니다 · market_session 휴장."""
    import os
    from datetime import date as _date
    import sessions as ss
    import kis_client as kc
    import market_map as mm
    problems = []
    saved = (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL, kc.HOLIDAYS_PATH)
    # 세션 기록·회고 기록·달력은 저장소(op_session·op_review·cal_event) — 빈 임시 저장소에서 돈다
    with tempfile.TemporaryDirectory() as td, testkit.db_case():
        tdp = Path(td)
        try:
            ss.DATA = tdp / "data"; ss.ANALYSIS = tdp / "analysis"; ss.SIGNALS = tdp / "signals"
            ss.JOURNAL = tdp / "journal"
            for d in (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL):
                d.mkdir()
            kc.HOLIDAYS_PATH = tdp / "holidays.json"
            kc.HOLIDAYS_PATH.write_text(json.dumps({"kr": ["2026-09-24"], "us": []}), encoding="utf-8")

            def touch(p, when):
                p.write_text("x", encoding="utf-8")
                ts = when.timestamp(); os.utime(p, (ts, ts))
            touch(ss.DATA / "material_260918_kr.md", datetime(2026, 9, 18, 14, 8, tzinfo=KST))
            touch(ss.DATA / "snapshot_260918_kr.json", datetime(2026, 9, 18, 14, 8, tzinfo=KST))
            for n in ("material_260919_us.md", "snapshot_260919_us.json"):
                touch(ss.DATA / n, datetime(2026, 9, 19, 2, 13, tzinfo=KST))
            touch(ss.SIGNALS / "signal_260919_us.json", datetime(2026, 9, 19, 3, 4, tzinfo=KST))
            touch(ss.DATA / "material_260921_kr.md", datetime(2026, 9, 21, 10, 0, tzinfo=KST))
            touch(ss.DATA / "snapshot_260921_kr.json", datetime(2026, 9, 21, 10, 0, tzinfo=KST))
            testkit.seed(logs={"reviews": [{"market": "US", "session": "2026-09-19"}]})
            if db.log_rows("reviews") != [{"market": "US", "session": "2026-09-19"}]:
                problems.append("심기 실패 — 회고 기록이 저장소에 안 보인다")
            today = _date(2026, 9, 21)
            rows = ss.scan(14, today=today)
            us = [r for r in rows if r["market"] == "us" and r["stamp"] == "260919"]
            if len(us) != 1 or us[0]["session_date"] != "2026-09-18" or us[0]["reached"] != "5 decide":
                problems.append(f"US 260919 → ET 09-18 세션이어야 한다: {[(r['stamp'], r['session_date'], r['reached']) for r in us]}")
            if any(r.get("off_session") for r in rows):
                problems.append("주말·휴장 기준일 행이 남았다")
            if any(r["market"] == "us" and r["session_date"] == "2026-09-18" and r["reached"] == "0 없음" for r in rows):
                problems.append("스탬프가 다른 같은 세션의 run이 있는데 '0 없음' 행이 남았다")
            pv = ss.prev_run(14, "kr", "260921", today=today)
            if pv.get("market") != "us" or pv.get("stamp") != "260919" or not pv.get("reviewed"):
                problems.append(f"직전 run은 US 260919(회고 있음)여야 한다: {pv.get('market')} {pv.get('stamp')} reviewed={pv.get('reviewed')}")
            rows2 = ss.scan(3, today=_date(2026, 9, 25))
            if any(r["market"] == "kr" and r["session_date"] == "2026-09-24" for r in rows2):
                problems.append("휴장일(09-24)이 미실행 행으로 남았다")
            # market_session · run_auto.market_state · pick_market
            s = kc.market_session("KR", datetime(2026, 9, 24, 10, 0, tzinfo=KST))
            if s["is_open"] or not s.get("holiday") or "휴장" not in s["why"]:
                problems.append(f"휴장일 market_session — open={s['is_open']} why={s['why']}")
            if kc.market_session("KR", datetime(2026, 9, 23, 10, 0, tzinfo=KST))["is_open"] is not True:
                problems.append("평일 정규장은 열려 있어야 한다")
            if kc.next_trading_day("KR", _date(2026, 9, 23)) != _date(2026, 9, 25):
                problems.append("next_trading_day가 휴장을 건너뛰지 않는다")
            import run_auto
            st, why = run_auto.market_state("KR", datetime(2026, 9, 24, 1, 0, tzinfo=timezone.utc))
            if st != "holiday":
                problems.append(f"run_auto.market_state 휴장 → {st}")
            import stage
            m, is_open, why = stage.pick_market(datetime(2026, 9, 24, 10, 0, tzinfo=KST))
            if m != "us" or is_open:
                problems.append(f"국내 휴장일 낮에는 US를 준비해야 한다 → {m} open={is_open}")
            # (63)-h kind 어휘 — 달력은 저장소 cal_event(빈 임시 저장소)
            f = tdp / "ev.json"
            f.write_text(json.dumps([{"date": _TODAY, "event": "UPPITY 휴간", "kind": "휴간"}]), encoding="utf-8")
            import io, contextlib
            with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()):
                rc = mm.add(f, "event")
            if rc != 2 or db.calendar_all() or "허용 kind" not in err.getvalue():
                problems.append(f"어휘 밖 kind는 rc 2·미기록·어휘 안내여야 한다 (rc={rc})")
            f.write_text(json.dumps([{"date": _TODAY, "event": "UPPITY 휴간", "kind": "메모"}]), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mm.add(f, "event")
            if rc != 0 or [e.get("event") for e in db.calendar_all()] != ["UPPITY 휴간"]:
                problems.append(f"어휘 안 kind(메모)는 기록돼야 한다 (rc={rc})")
            # (2026-09-22) 이벤트 계수(halve_window)는 지웠다 — 달력 kind는 기록·분류용이다
            if hasattr(rg, "halve_window"):
                problems.append("halve_window가 아직 있다 — 이벤트 할인은 폐지됐다")
        finally:
            (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL, kc.HOLIDAYS_PATH) = saved
    status = FAIL if problems else PASS
    results.append((status, "(63)-f/g/h 세션 기준일·휴장·kind 어휘", "; ".join(problems)))
    print(f"[{status}] (63)-f/g/h 세션 기준일·휴장·kind 어휘" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_sessions_weekend_stamp_and_holidays)


def test_stamp_parent_section():
    """(63)-i `stage.py stamp --sec §11-규율|§11-집행` — 부모 절(`## §11 …`) 끝에 찍힌다 · `§1-A`는 `§10`에 안 걸린다."""
    import stage, argparse as _ap, io, contextlib
    problems = []
    with tempfile.TemporaryDirectory() as td:
        note = Path(td) / "n.md"
        note.write_text("## §1-A 이 시장\n\n본문\n\n## §10 한계\n\n한계\n\n## §11 집행 결과\n\n규율\n\n## §12 출처\n\n끝\n",
                        encoding="utf-8")
        for sec in ("§11-규율", "§11-집행", "§11-집행", "§1-A"):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = stage.cmd_stamp(_ap.Namespace(note=str(note), sec=sec, worklog="", stage=""))
            if rc != 0:
                problems.append(f"--sec {sec} rc {rc}")
        txt = note.read_text(encoding="utf-8")
        s11, s12, s10 = txt.index("## §11"), txt.index("## §12"), txt.index("## §10")
        for k in ("§11-규율", "§11-집행"):
            i = txt.find(f"<!-- ✓ {k} -->")
            if not (s11 < i < s12):
                problems.append(f"{k} 스탬프가 §11 절 안에 없다 (pos {i})")
        if txt.count("<!-- ✓ §11-집행 -->") != 1:
            problems.append("스탬프가 멱등이 아니다")
        i = txt.find("<!-- ✓ §1-A -->")
        if not (0 <= i < s10):
            problems.append("§1-A 스탬프가 §1-A 절이 아니라 다른 곳에 찍혔다")
    status = FAIL if problems else PASS
    results.append((status, "(63)-i stamp — 부모 절 탐색·부분일치 제거", "; ".join(problems)))
    print(f"[{status}] (63)-i stamp — 부모 절 탐색·부분일치 제거" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_stamp_parent_section)


def test_scenarios_persistent_id():
    """(63)-j `scenarios.py promote` — persistent_id 우선 · 별칭 · merge · due/audit/judge의 merged 처리."""
    import scenarios as sc, argparse as _ap, io, contextlib
    problems = []
    # 시나리오 원장은 저장소 jd_scenario — 빈 임시 저장소에 심는다
    with tempfile.TemporaryDirectory() as td, testkit.db_case(Path(td) / "db"):
        tdp = Path(td)
        try:
            _sc0 = [{"id": "SC-260915-1", "opened": "260915", "market": "kr", "status": "open", "realized": None,
                     "acted": None, "condition": "9/18 MOU 문서에 국내 기자재 명시", "sources": ["signal_260915_kr.json#S1"],
                     "judgments": [], "actions": [{"market": "KR", "ticker": "034020", "what": "e1"}]}]
            testkit.seed(scenarios=_sc0)
            if [r.get("id") for r in db.scenarios_all()] != ["SC-260915-1"]:
                problems.append("심기 실패 — 시나리오 SC-260915-1이 저장소에 안 보인다")
            def promote(name, rows):
                p = tdp / name
                p.write_text(json.dumps({"scenarios": rows}, ensure_ascii=False), encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return sc.cmd_promote(_ap.Namespace(signal=str(p)))
            promote("signal_260921_kr.json", [{"id": "S1", "condition": "MOU 서명 문서에 국내 기자재 직접 구매 명시",
                                              "persistent_id": "SC-260915-1"}])
            d = sc.load()["scenarios"]
            if len(d) != 1 or d[0].get("aliases") != ["MOU 서명 문서에 국내 기자재 직접 구매 명시"] \
                    or "signal_260921_kr.json#S1" not in d[0]["sources"]:
                problems.append(f"persistent_id 매칭 — 행 {len(d)} aliases={d[0].get('aliases')}")
            promote("signal_260922_kr.json", [{"id": "S4", "condition": "MOU 서명 문서에 국내 기자재 직접 구매 명시"}])
            if len(sc.load()["scenarios"]) != 1:
                problems.append("별칭 문구는 새 id 없이 매칭돼야 한다")
            promote("signal_260923_kr.json", [{"id": "S2", "condition": "전혀 다른 조건", "persistent_id": "SC-999999-9"}])
            d = sc.load()["scenarios"]
            if len(d) != 2 or d[1].get("claimed_persistent_id") != "SC-999999-9" or d[1]["id"] != "SC-260923-1":
                problems.append(f"원장에 없는 persistent_id — {[(r['id'], r.get('claimed_persistent_id')) for r in d]}")
            # merge
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sc.cmd_merge(_ap.Namespace(src="SC-260923-1", into="SC-260915-1"))
            d = sc.load()["scenarios"]
            a, b = d[1], d[0]
            if rc != 0 or a["status"] != "merged" or a["merged_into"] != "SC-260915-1" or "전혀 다른 조건" not in b["aliases"]:
                problems.append(f"merge — rc {rc} status={a['status']} aliases={b.get('aliases')}")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc_j = sc.cmd_judge(_ap.Namespace(id="SC-260923-1", realized="y", acted=None, note="", batch=""))
            if rc_j != 2:
                problems.append("병합된 id 판정은 rc 2여야 한다")
            # judge into → realized → due는 병합 행을 세지 않는다
            with contextlib.redirect_stdout(io.StringIO()):
                sc.cmd_judge(_ap.Namespace(id="SC-260915-1", realized="y", acted=None, note="", batch=""))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sc.cmd_due(_ap.Namespace(market="kr"))
            if "SC-260923-1" in buf.getvalue() or "SC-260915-1" not in buf.getvalue():
                problems.append("due — 병합 행은 빠지고 목적지 행은 나와야 한다")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                sc.cmd_audit(_ap.Namespace(days=3650, affects="", out=""))
            if "승격 1 " not in buf.getvalue():
                problems.append(f"audit — 병합 행을 제외해 승격 1이어야 한다: {buf.getvalue().splitlines()[2:3]}")
            # 병합된 id로 온 persistent_id는 목적지로 간다
            promote("signal_260924_kr.json", [{"id": "S9", "condition": "또 다른 문구", "persistent_id": "SC-260923-1"}])
            if len(sc.load()["scenarios"]) != 2:
                problems.append("병합된 id의 persistent_id는 목적지 행으로 흡수돼야 한다")
        finally:
            pass
    status = FAIL if problems else PASS
    results.append((status, "(63)-j scenarios — persistent_id·별칭·merge", "; ".join(problems)))
    print(f"[{status}] (63)-j scenarios — persistent_id·별칭·merge" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_scenarios_persistent_id)

_PS = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8")).get("position_sizing") or {}
_GAP_LIMITS = {**ROOMY, "axis_max_pct": None, "reserve_for_other_market_pct": None,
               "min_proposals_below_target": None, "position_sizing": {**_PS, "min_target_gap_pct": 3.0}}


def _gap_sig(exits=None, triggers=None, thesis_id=None):
    sig = base_signal()
    p = sig["proposals"][0]
    if exits is not None:
        p["exits"] = exits
    if triggers is not None:
        p["triggers"] = triggers
    if thesis_id:
        p["thesis_id"] = thesis_id
    return sig


run_case("(63)-k1 1차 목표 +1.7%(183,000/180,000) → 목표 여유 없음 거부",
         signal=_gap_sig(exits=[{"id": "x3", "check": "price >= 183000"}]),
         limits_patch=_GAP_LIMITS, expect_orders=0, expect_reject_contains="목표 여유 없음")
run_case("(63)-k2 1차 목표 +11% → 통과",
         signal=_gap_sig(exits=[{"id": "x3", "check": "price >= 200000"}, {"id": "x1", "check": "price <= 170000"}]),
         limits_patch=_GAP_LIMITS, expect_orders=1)
run_case("(63)-k3 제안에 exits 없어도 논지 exit_triggers를 본다",
         signal=_gap_sig(thesis_id="t-gap"), limits_patch=_GAP_LIMITS,
         extra_theses={"theses": [{"id": "t-gap", "market": "KR", "ticker": "000660", "status": "armed",
                                   "created": "2026-09-18", "exit_triggers": [{"id": "x3", "check": "price >= 182000"}]}]},
         expect_orders=0, expect_reject_contains="목표 여유 없음")
run_case("(63)-k4 진입 트리거의 price >=(돌파)는 목표가 아니다",
         signal=_gap_sig(triggers=[{"id": "e1", "check": "price >= 100"}]),
         limits_patch=_GAP_LIMITS, expect_orders=1)
run_case("(63)-k5 min_target_gap_pct 0이면 끔",
         signal=_gap_sig(exits=[{"id": "x3", "check": "price >= 183000"}]),
         limits_patch={**_GAP_LIMITS, "position_sizing": {**_PS, "min_target_gap_pct": 0}}, expect_orders=1)
_gap_two = _three_props()
_gap_two["proposals"] = _gap_two["proposals"][:2]
_gap_two["proposals"][0]["exits"] = [{"id": "x3", "check": "price >= 183000"}]
run_case("(63)-k6 목표 여유 거부는 미달 건수에 안 센다 → deficit_short",
         signal=_gap_two, snapshot=_mk_snap("kr", 10_000_000, []),
         limits_patch={**_DEF_LIMITS, "position_sizing": {**_PS, "min_target_gap_pct": 3.0}}, extra_map=_AXIS_MAP,
         expect_approved=_deficit_expect(True))


def test_set_trigger():
    """(63)-l `theses.py set-trigger` — 새 값으로 다시 걸고 옛 값은 corrections[]에."""
    import theses as th, io, contextlib
    problems = []
    with testkit.db_case():                       # 논지 원장은 저장소 — 빈 임시 저장소에 심는다
        try:
            _th = [{"id": "t", "ticker": "095610", "status": "armed",
                    "exit_triggers": [{"id": "x3", "check": "price >= 147800", "size_pct": 50.0, "basis": "old", "when": "목표"}]}]
            testkit.seed(theses=_th)
            problems += _seed_check(theses=_th, watchlist=False)
            with contextlib.redirect_stdout(io.StringIO()):
                # 2026-10-03 — 고정 목표 다리는 보유의 1/3 이하만(나머지는 추적 다리) → size_pct 33으로 다시 건다
                rc = th.cmd_set_trigger("t", "x3", "price >= 152000", "resistance_1 152,000 (20260922)", "진입 전에 목표가 메워짐",
                                        size_pct=33.0)
            t = th.load()["theses"][0]
            x3 = t["exit_triggers"][0]
            c = (t.get("corrections") or [{}])[0]
            if rc != 0 or x3["check"] != "price >= 152000" or x3["basis"] != "resistance_1 152,000 (20260922)" \
                    or x3["size_pct"] != 33.0 or c.get("before", {}).get("check") != "price >= 147800" \
                    or "set-trigger" not in c.get("what", ""):
                problems.append(f"set-trigger — {x3} corrections={t.get('corrections')}")
            for bad in (("t", "x3", "가격 >= 1", "b", "w"), ("t", "x9", "price >= 1", "b", "w"), ("t", "x3", "price >= 1", "", "w")):
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        th.cmd_set_trigger(*bad)
                    problems.append(f"거부돼야 한다: {bad}")
                except SystemExit:
                    pass
        finally:
            pass
    status = FAIL if problems else PASS
    results.append((status, "(63)-l theses set-trigger — corrections 기록·형식 검증", "; ".join(problems)))
    print(f"[{status}] (63)-l theses set-trigger — corrections 기록·형식 검증" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_set_trigger)


# ============ 상수가 아니라 판단이 비율을 정한다 (2026-09-22) ============

def _expect_alloc(**want):
    def check(approved, problems):
        al = approved.get("allocation") or {}
        d = approved.get("deficit") or {}
        for k, v in want.items():
            # need·covered_pct·short는 deficit(일일 상한을 뺀 실효값), 나머지는 allocation
            src = d if k in ("need", "covered_pct", "short", "approved_buy") else (al if k in al else d)
            got = src.get(k)
            if callable(v):
                if not v(got, approved):
                    problems.append(f"{k}={got!r} 조건 불만족")
            elif got != v:
                problems.append(f"{k}={got!r} (기대 {v!r})")
    return check


_GAP_SNAP = _mk_snap("kr", 10_000_000, [{"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 10,
                                         "avg_price": 70000, "price": 71000, "eval_amt": 710_000, "pnl_pct": 1.4}])
_NO_CAP = {**ROOMY, "daily_max_order_pct_of_equity": None, "min_proposals_below_target": None,
           "min_distinct_axes_below_target": None}

run_case("(64)-a allocation 없으면 거부",
         signal={k: v for k, v in base_signal().items() if k != "allocation"},
         expect_orders=0, expect_reject_contains="allocation 블록이 없다")
run_case("(64)-b 목표 60%인데 현금 명분 없음 → 거부",
         signal=base_signal(allocation=base_alloc(target_invested_pct=60)),
         expect_orders=0, expect_reject_contains="현금 명분 없음")
run_case("(64)-b′ 현금 명분은 있는데 해제 조건 없음 → 거부",
         signal=base_signal(allocation=base_alloc(target_invested_pct=60, cash_reason="하락장 KOSPI 60일선 -3%")),
         expect_orders=0, expect_reject_contains="해제 조건")
run_case("(64)-b″ regime에 숫자 없음 → 거부",
         signal=base_signal(allocation=base_alloc(regime="느낌상 강세")),
         expect_orders=0, expect_reject_contains="숫자가 없다")
run_case("(64)-c gap 88%p · 제안 합 5% → 증분을 키워 need를 채운다(scaled_up · covered 100 · short 아님)",
         signal=_with_conc_why(base_signal()), snapshot=_GAP_SNAP, limits_patch=_NO_CAP,
         expect_approved=_expect_alloc(covered_pct=lambda v, a: v is not None and v >= 95,
                                       short=False, gap_pct=lambda v, a: 85 < v < 92,
                                       need=lambda v, a: 9_000_000 < v < 9_600_000,
                                       scaled_up=lambda v, a: (a.get("scaled_up") or {}).get("k", 0) > 5))
run_case("(64)-d 일일 상한이 없으니 need = full gap이고 현금이 허용하면 그만큼 채운다",
         signal=_with_conc_why(base_signal()), snapshot=_GAP_SNAP,
         limits_patch={**ROOMY, "min_proposals_below_target": None, "min_distinct_axes_below_target": None},
         expect_approved=_expect_alloc(short=False, need=lambda v, a: 9_000_000 < v < 9_600_000,
                                       covered_pct=lambda v, a: v is not None and v >= 95))


# ★ R05(2026-10-19 · Codex 리뷰) — 집중 공개선은 gap 채우기·최종 묶음에서도 본다. 명분 없는 매수는 선에서 멈추고(거부 아님)
#   미달 사유에 "집중 선에서 멈춤"이 남는다. 리뷰 재현 concentration_before_scaling·real_concentration_after_cap의 올바른 동작.
def _held_line(ticker, line_head, max_pct, short=True):
    def chk(ap, problems):
        o = next((x for x in ap.get("orders") or [] if x.get("ticker") == ticker and x.get("action") == "BUY"), None)
        c = (ap.get("concentration") or {}).get(ticker) or {}
        d = ap.get("deficit") or {}
        if not o:
            problems.append(f"{ticker} 매수가 없다 — 거부 {ap.get('rejected')}")
            return
        if not str((o.get("held_at_line") or {}).get("line") or "").startswith(line_head):
            problems.append(f"held_at_line {o.get('held_at_line')}")
        val = c.get("pos_pct_after") if line_head == "종목" else max((c.get("axes_after") or {"x": 0}).values())
        if val is None or val > max_pct + 1e-6:
            problems.append(f"최종 비중 {val} > 선 {max_pct}")
        if c.get("line_hit"):
            problems.append(f"최종 묶음에 넘은 선이 남았다 {c.get('line_hit')}")
        if bool(ap.get("deficit_short")) != short or (short and "집중 선에서 멈춤" not in str(d.get("why"))):
            problems.append(f"deficit_short={ap.get('deficit_short')} · why={str(d.get('why'))[:160]}")
        if any(r.get("ticker") == ticker for r in ap.get("rejected") or []):
            problems.append(f"선에서 멈춰야 하는데 거부했다 {ap.get('rejected')}")
    return chk


run_case("(64)-e 명분 없는 5% 제안 → gap 채우기가 종목 20% 선에서 멈춤(95%까지 안 키움) · 미달 사유 '집중 선에서 멈춤'",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, []), limits_patch=_NO_CAP,
         expect_approved=_held_line("000660", "종목", 20.0))
run_case("(64)-f 보유 14.4% + 명분 없는 제안 → 배율이 20% 선에서 멈춤(3주 · 심사 크기 아래로는 안 내림)",
         signal=base_signal(),
         snapshot=_mk_snap("kr", 8_560_000, [{"market": "KR", "ticker": "000660", "name": "SK하이닉스", "qty": 8,
                                               "avg_price": 180000, "price": 180000, "eval_amt": 1_440_000, "pnl_pct": 0.0}]),
         limits_patch=_NO_CAP, expect_approved=_held_line("000660", "종목", 20.0))
run_case("(64)-g 같은 축 두 매수(명분 없음) → 축 40% 선에서 멈춤(앞 제안부터) · 최종 축 비중 ≤ 40%",
         signal=base_signal(proposals=[dict(base_signal()["proposals"][0]),
                                       dict(base_signal()["proposals"][0], ticker="005930", name="삼성전자",
                                            weight_target_pct=2.0)]),
         limits_patch={**_NO_CAP, "concentration_disclose_pct": {"position": 90.0, "axis": 40.0}},
         extra_map=_AXIS_MAP, expect_approved=_held_line("000660", "축", 40.0))
run_case("(64)-l 명분이 있으면 선을 넘어 키운다(공개 블록에 넘은 선·명분)",
         signal=_with_conc_why(base_signal()), snapshot=_mk_snap("kr", 10_000_000, []), limits_patch=_NO_CAP,
         expect_approved=lambda ap, pr: pr.extend(
             [] if ((ap.get("concentration") or {}).get("000660") or {}).get("why")
             and ((ap.get("concentration") or {}).get("000660") or {}).get("pos_pct_after", 0) > 20
             else [f"명분 있는 매수의 공개 블록 {(ap.get('concentration') or {}).get('000660')}"]))


def test_concentration_final_261019():
    """(64)-m 최종 묶음 안전망 — 명분 없이 선을 넘는 매수가 남으면 그 주문만 거부하고 그 매수를 위한 회전 매도도 뺀다 ·
    (64)-n 리뷰 재현(real_concentration_after_cap)을 함수 그대로: 실계좌 · 보유 15% + 5% 제안 → 배율·주문당 상한 뒤 20%."""
    import io
    import facts as _facts
    problems = []
    tmp = Path(tempfile.mkdtemp(prefix="rgconc_"))
    saved = rg.LIMITS_PATH
    # 반대편 시장 자산은 data 폴더 스냅샷에서 합쳐진다 — 실제 data를 읽으면 그날 운영 스냅샷에 따라 결과가 바뀐다(2026-10-19 운영 폴더에서
    # 미국 스냅샷이 자산을 키워 20% 선을 안 넘겼다). 빈 임시 폴더로 격리한다.
    saved_data = rg.DATA_DIR
    rg.DATA_DIR = tmp / "data"
    rg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with testkit.db_case(tmp / "db"), _ctxlib.redirect_stdout(io.StringIO()):
            lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
            bal = {"currency": "KRW", "cash": 10_000_000, "positions": []}
            final = [{"ticker": "000660", "name": "SK하이닉스", "market": "KR", "action": "BUY", "qty": 20, "price": 180000,
                      "source": "llm", "proposal": {"ticker": "000660"}},
                     {"ticker": "035420", "name": "NAVER", "market": "KR", "action": "SELL", "qty": 1, "price": 200000,
                      "source": "funding", "funds": "000660"}]
            kept, rej, blk = rg.concentration_final(final, lim, bal, "KR")
            if kept or len(rej) != 2 or not str(rej[0].get("why")).startswith("최종 수량 집중 명분 없음"):
                problems.append(f"(64)-m 안전망: 남김 {kept} · 거부 {rej}")
            if not (blk.get("000660") or {}).get("line_hit"):
                problems.append(f"(64)-m 공개 블록에 넘은 선이 없다 {blk}")
            # (64)-n — review_repro.py real_concentration_after_cap 그대로(실계좌 한도 · 보유 15% · 5% 제안 · need 8,000,000)
            fid = _facts.add_fact({"kind": "earnings", "ticker": "000660", "period": "2026Q3", "metric": "revenue",
                                   "actual": 100, "as_of": "2026-10-19", "url": "https://example.com/review-fixture",
                                   "tier": 1, "quote": "Reported revenue 100"}, ("261019", "kr"))["id"]
            _facts.add_verdicts([{"fact_id": fid, "fact_rev": 1, "verdict": "MATCH", "tier": 1}], run=("261019", "kr"), label="track1")
            lim_r = {**lim, "account_mode": "real", "real_trading_enabled": True}
            rg.LIMITS_PATH = tmp / "limits.json"
            rg.LIMITS_PATH.write_text(json.dumps(lim_r), encoding="utf-8")
            bal_r = {"currency": "KRW", "cash": 8_500_000,
                     "positions": [{"ticker": "000660", "qty": 15, "price": 100000, "eval_amt": 1_500_000, "avg_price": 100000}]}
            sig = {"market": "KR", "run_id": "261019_kr", "track1": {"drop": []}, "allocation": {"target_invested_pct": 95},
                   "proposals": [{"ticker": "000660", "action": "BUY", "weight_target_pct": 5, "confidence": 0.9,
                                  "expected_upside_pct": 20, "size_why": "review fixture",
                                  "facts": [{"id": fid, "role": "support", "support": "revenue growth"}]}]}
            watch = json.loads((HERE_DIR / "config" / "watchlist.json").read_text(encoding="utf-8"))
            acc, rej1 = rg.screen_proposals(sig, lim_r, watch, bal_r, {"000660": {"price": 100000}})
            actx = {"need": 8_000_000, "gap_amt": 8_000_000, "deployable": 8_500_000}
            fin, rej2 = rg.apply_run_limits(acc, lim_r, bal_r, 0, "KR", actx)
            fin, rej3, _, _ = rg.apply_order_cap(fin, lim_r, "KR", 1348.5)
            fin, rej4, blk2 = rg.concentration_final(fin, lim_r, bal_r, "KR")
            pos = (1_500_000 + sum(o["qty"] * o["price"] for o in fin if o["action"] == "BUY")) / 10_000_000 * 100
            if [o["qty"] for o in fin if o["action"] == "BUY"] != [5] or pos > 20.0 + 1e-9:
                problems.append(f"(64)-n 최종 수량 {[o['qty'] for o in fin]} · 비중 {pos:.1f}%(기대 5주 · 20%)")
            if rej1 + rej2 + rej3 + rej4:
                problems.append(f"(64)-n 거부 {rej1 + rej2 + rej3 + rej4}")
            if not actx.get("conc_held"):
                problems.append("(64)-n 선에서 멈춘 기록(conc_held)이 없다 — 미달 사유로 못 넘긴다")
    finally:
        rg.LIMITS_PATH = saved
        rg.DATA_DIR = saved_data
        shutil.rmtree(tmp, ignore_errors=True)
    status = FAIL if problems else PASS
    results.append((status, "(64)-m·n 최종 묶음 집중 안전망 · 실계좌 15%+5% 배율·상한 뒤 20%", "; ".join(problems)))
    print(f"[{status}] (64)-m·n 최종 묶음 집중 안전망 · 실계좌 15%+5% 배율·상한 뒤 20%" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_concentration_final_261019)


def test_alloc_ledger_chain():
    """(64)-h 배분 원장 — 이어받기(based_on)·바꿀 때만 이유·같은 run 재실행은 자기 id 제외."""
    import allocation as al
    problems = []
    with testkit.db_case():                       # 배분 원장은 저장소 jd_allocation — 빈 임시 저장소에서
        try:
            if db.allocation_rows():
                problems.append("빈 임시 저장소에 배분 행이 있다(격리 실패)")
            if al.validate(base_alloc(), "AL-260922-kr"):
                problems.append("원장이 비어 있으면 based_on: null 최초 판단이 통과해야 한다")
            row = al.record({"market": "KR", "allocation": base_alloc()}, "signals/approved_260922_kr.json")
            if row["id"] != "AL-260922-kr":
                problems.append(f"id {row['id']}")
            # 같은 run 재실행 — 자기 id를 빼면 원장은 비어 있는 것과 같다
            if al.validate(base_alloc(), "AL-260922-kr"):
                problems.append("같은 run 재실행이 자기 행 때문에 막혔다")
            al.record({"market": "KR", "allocation": base_alloc()}, "signals/approved_260922_kr.json")
            if len(al.rows()) != 1:
                problems.append(f"같은 id 재기록이 덮어쓰지 않고 {len(al.rows())}행")
            # 다음 run — based_on 없음 → 오류 · 맞으면 통과 · 바꿨는데 why 없음 → 오류
            if not al.validate(base_alloc(), "AL-260923-kr"):
                problems.append("직전 판단을 안 이었는데 통과")
            if al.validate(base_alloc(based_on="AL-260922-kr"), "AL-260923-kr"):
                problems.append("유지(based_on 일치·change null)가 막혔다")
            errs = al.validate(base_alloc(based_on="AL-260922-kr", target_invested_pct=70), "AL-260923-kr")
            if not any("change.why" in e for e in errs):
                problems.append(f"비율을 바꿨는데 change.why 요구가 없다: {errs}")
            if al.validate(base_alloc(based_on="AL-260922-kr", target_invested_pct=70,
                                      change={"from": 95, "to": 70, "why": "09-23 KOSPI -4% · 60일선 이탈"}), "AL-260923-kr"):
                problems.append("이유를 적은 변경이 막혔다")
            blk = al.block()
            if "AL-260922-kr" not in blk or "95%" not in blk:
                problems.append("prev 블록에 직전 id·비율이 없다")
            if [r.get("id") for r in db.allocation_rows()] != ["AL-260922-kr"]:
                problems.append(f"저장소 배분 행 {[r.get('id') for r in db.allocation_rows()]}(기대 AL-260922-kr 하나)")
        finally:
            pass
    status = FAIL if problems else PASS
    results.append((status, "(64)-h 배분 원장 이어받기·변경 이유·재실행 멱등", "; ".join(problems)))
    print(f"[{status}] (64)-h 배분 원장 이어받기·변경 이유·재실행 멱등" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_alloc_ledger_chain)


def test_research_expansion_checks():
    """(64)-j/k crosscheck — 리서치 재료 확장(사실 0 · 되풀이만 → 실패 · 새 사실 → 통과) · 새 전망·논지의 리서치 인용.
    2026-10-17: 확장은 리서치 파일의 `## 재료 확장` 절이 아니라 저장소 사실(`facts.py add` · 되풀이는 이번 run 재료 링크와 같은 URL)로
    센다 — 케이스는 빈 임시 저장소에 재료 링크·사실·지도·논지를 심고 잰다(리서치 파일명 인용은 그대로 파일)."""
    import crosscheck as cc
    import contextlib as _c, io as _io
    problems = []
    saved = cc.HERE
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "analysis").mkdir(); (root / "data").mkdir(); (root / "journal").mkdir()
        cc.HERE = root
        try:
            with testkit.db_case(root / "db"):
                st = "260930"
                run = (st, "kr")
                db.connect().execute("INSERT INTO mat_link (url, run_id) VALUES (?,?)", ("https://old.example.com/a", db.run_id(*run)))
                if cc.check_research_expansion("kr", st)[0][1]:
                    problems.append("조사 사실 0건인데 통과")
                base = {"kind": "earnings", "ticker": "005930", "period": "2026Q3", "actual": 1, "as_of": "2026-09-29", "tier": 2,
                        "source": "테스트", "quote": "Samsung reported a test metric for the third quarter"}
                with _c.redirect_stdout(_io.StringIO()), _c.redirect_stderr(_io.StringIO()):
                    ra = facts.add_fact({**base, "metric": "A", "url": "https://old.example.com/a"}, run)
                if ra["status"] != "되풀이" or cc.check_research_expansion("kr", st)[0][1]:
                    problems.append(f"재료에 있는 링크 되풀이인데 통과(등록 {ra['status']})")
                with _c.redirect_stdout(_io.StringIO()), _c.redirect_stderr(_io.StringIO()):
                    rb = facts.add_fact({**base, "metric": "B", "url": "https://new.example.com/b", "tier": 1}, run)
                r = cc.check_research_expansion("kr", st)[0]
                if not r[1] or "확장 1행" not in r[2]:
                    problems.append(f"새 사실 1행이면 통과여야 한다: {r}")
                today = "2026-09-30"
                testkit.seed(axes=[{"id": "ax1", "opened": today, "updated": today, "thesis": "뉴스레터만 보고 쓴 전망"},
                                   {"id": "ax0", "opened": "2026-09-01", "updated": "2026-09-01", "thesis": "옛 전망"}],
                             theses=[{"id": "t1", "created": today, "thesis": "근거: 재료 확장 행 — https://new.example.com/b"}])
                r = cc.check_research_cited("kr", st)[0]
                if r[1] or "전망 ax1" not in r[2] or "논지 t1" in r[2]:
                    problems.append(f"전망 ax1만 미인용이어야 한다: {r}")
                db.axis_update("ax1", lambda a: {**a, "thesis": f"근거 {rb['id']} — 2차 수혜"})
                if not cc.check_research_cited("kr", st)[0][1]:
                    problems.append("사실 id 인용이 통과하지 않는다")
                rf = root / "analysis" / f"섹터_테스트_{st}_v1_0.md"
                rf.write_text("# 리서치\n", encoding="utf-8")
                db.axis_update("ax1", lambda a: {**a, "thesis": f"근거 {rf.stem}"})
                if not cc.check_research_cited("kr", st)[0][1]:
                    problems.append("리서치 파일명 인용이 통과하지 않는다")
        finally:
            cc.HERE = saved
    # tier=data — 사실 저장소의 티어 판정(facts._tier · 옛 srcledger._tier와 같은 규칙)
    try:
        if facts._tier("data") != "data" or facts._tier("1") != 1 or facts._tier(2) != 2 or facts._tier("T1") != 1:
            problems.append("facts._tier가 data/1/2/T1을 못 판다")
    except Exception as e:                              # noqa: BLE001
        problems.append(f"facts._tier 예외 {type(e).__name__}")
    status = FAIL if problems else PASS
    results.append((status, "(64)-j/k 리서치 재료 확장·인용 검사 · tier=data", "; ".join(problems)))
    print(f"[{status}] (64)-j/k 리서치 재료 확장·인용 검사 · tier=data" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_research_expansion_checks)


def test_network_exceptions_and_funding_exclusion():
    """(65) 네트워크 예외 → KisError(재시도) · 회전 매도 후보에서 같은 run의 BUY 종목 제외 · sector_history 멱등."""
    import socket
    import kis_client as kc
    import market_map as mm
    problems = []
    # a) socket.timeout이 KisError로 감싸이고 GET은 재시도한다
    calls = {"n": 0}
    class _Cli(kc.KisClient):
        def __init__(self):
            self.base = "https://x"; self.svr = "paper"; self.app_key = "k" * 10
            self._ctx = None
        def _throttle(self):
            pass
    def fake_urlopen(req, timeout=20, context=None):
        calls["n"] += 1
        raise socket.timeout("timed out")
    saved = (kc.urllib.request.urlopen, kc.TRANSIENT_BACKOFF_SEC)
    kc.urllib.request.urlopen = fake_urlopen
    kc.TRANSIENT_BACKOFF_SEC = 0
    try:
        try:
            _Cli()._request("GET", "/p", {}, timeout=1)
            problems.append("타임아웃이 예외 없이 지나갔다")
        except kc.KisError as e:
            if "timed out" not in str(e) and "timeout" not in str(e).lower():
                problems.append(f"KisError 메시지에 원인이 없다: {e}")
        except Exception as e:                          # noqa: BLE001
            problems.append(f"KisError가 아닌 예외가 튀었다: {type(e).__name__}")
        if calls["n"] < 2:
            problems.append(f"GET 재시도가 없다(호출 {calls['n']}회)")
        calls["n"] = 0
        try:
            _Cli()._request("POST", "/o", {}, body={"a": 1}, timeout=1)
        except kc.KisError:
            pass
        if calls["n"] != 1:
            problems.append(f"주문 POST는 재시도하면 안 된다(호출 {calls['n']}회)")
    finally:
        kc.urllib.request.urlopen, kc.TRANSIENT_BACKOFF_SEC = saved
    # c) 회전 매도 후보에서 같은 run의 BUY 종목 제외 — LG엔솔을 사는 run에서 LG엔솔을 팔지 않는다
    bal = {"cash": 100_000, "currency": "KRW",
           "positions": [{"ticker": "373220", "name": "LG엔솔", "qty": 11, "price": 350_000, "eval_amt": 3_850_000},
                         {"ticker": "005930", "name": "삼성전자", "qty": 5, "price": 70_000, "eval_amt": 350_000}]}
    orders = [{"ticker": "373220", "action": "BUY", "qty": 8, "price": 350_000, "source": "llm"},
              {"ticker": "095610", "action": "BUY", "qty": 10, "price": 146_000, "source": "llm"}]
    lim = {"daily_max_orders": None, "portfolio_daily_loss_halt_pct": None, "funding_max_per_run": 1, "funding_min_fill_ratio": 0.5}
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        final, rej = rg.apply_run_limits(orders, lim, bal, 0.0, "KR", {"reserved_here": 0})
    fund = [o for o in final if o.get("source") == "funding"]
    if any(o["ticker"] == "373220" for o in fund):
        problems.append("같은 run에 사는 LG엔솔이 회전 매도 대상으로 나왔다")
    if fund and fund[0]["ticker"] != "005930":
        problems.append(f"회전 매도 대상은 매수 종목을 뺀 최대 보유(005930)여야 한다: {[o['ticker'] for o in fund]}")
    # d) sector_history 멱등
    with testkit.db_case():                       # 섹터 이력은 저장소 op_sector_history — 빈 임시 저장소에서
        try:
            if db.log_rows("sector_history"):
                problems.append("빈 임시 저장소에 섹터 이력이 있다(격리 실패)")
            rows = [{"key": "0001", "name": "x", "d1": 1.0, "d5": 2.0, "d20": 3.0}]
            with contextlib.redirect_stdout(io.StringIO()):
                mm._record_board(rows, "kr", 0.5, "2026-09-30")
                mm._record_board(rows, "kr", 0.7, "2026-09-30")
                mm._record_board(rows, "us", 0.7, "2026-09-30")
            lines = db.log_rows("sector_history")
            if len(lines) != 2 or [l for l in lines if l["market"] == "kr"][0]["benchmark_pct"] != 0.7:
                problems.append(f"(날짜, 시장)당 한 행·마지막 값이어야 한다: {[(l['date'], l['market'], l['benchmark_pct']) for l in lines]}")
        except AttributeError as e:
            problems.append(f"_record_board 호출 실패: {e}")
    status = FAIL if problems else PASS
    results.append((status, "(65) 네트워크 예외→KisError·POST 무재시도 · 회전 매도 BUY 제외 · sector_history 멱등", "; ".join(problems)))
    print(f"[{status}] (65) 네트워크 예외→KisError·POST 무재시도 · 회전 매도 BUY 제외 · sector_history 멱등"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_network_exceptions_and_funding_exclusion)


# ==== 소진된 다리 · 편입 거래소 코드 · 회전 매도 결합 · FX (2026-09-23) ====

_t66 = _held_thesis("price >= 70000")
_t66["theses"][0]["exit_triggers"][0]["size_pct"] = 50
_t66_fired = json.loads(json.dumps(_t66))
_t66_fired["theses"][0]["exit_triggers"][0].update(
    {"fired": True, "fired_at": "2026-09-21T23:58:00+09:00", "fired_note": "discipline SELL 6주 @438.5"})

run_case("(66)-a 소진된 다리(fired)는 다시 발화하지 않는다 — 톱니 차단",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, list(_HELD_005930)),
         limits_patch={**ROOMY, "min_proposals_below_target": None, "min_distinct_axes_below_target": None},
         extra_theses=_t66_fired, expect_approved=_expect_no_sell)
run_case("(66)-a′ 같은 다리가 살아 있으면 판다(대조군)",
         signal=base_signal(), snapshot=_mk_snap("kr", 10_000_000, list(_HELD_005930)),
         limits_patch={**ROOMY, "min_proposals_below_target": None, "min_distinct_axes_below_target": None},
         extra_theses=_t66, expect_approved=_expect_sell(5, False))


def test_leg_marked_on_fill():
    """(66)-b~g 체결이 다리를 소진 표시한다 — 부분 체결도 · 취소·만료는 아니다 · set-trigger가 되살린다 ·
    trigger_id가 approved→trades까지 남는다 · 톱니 재현(두 번째 평가에서 매도 0)."""
    import fill, theses as th, execute as ex
    problems = []
    # 논지 원장은 저장소 — 빈 임시 저장소에 심고(reseed), 고친 결과도 저장소에서 읽는다(cur)
    with testkit.db_case():
        try:
            def ledger():
                return {"theses": [{"id": "t-tsm", "market": "US", "ticker": "TSM", "status": "held",
                                    "created": "2026-09-16", "entered_on": "2026-09-16",
                                    "exit_triggers": [{"id": "x3", "check": "price >= 431.68", "size_pct": 50.0,
                                                       "when": "목표", "basis": "resistance_2 (과거 고정)"},
                                                      {"id": "x1", "check": "price <= 384.10", "size_pct": 100.0}]}]}
            def row(verdict="FILLED", qty=6, trigger_id="x3", reasons=None):
                return {"action": "SELL", "ticker": "TSM", "market": "US", "qty": qty, "price": 438.5,
                        "source": "discipline", "full_exit": False, "thesis_id": "t-tsm",
                        "trigger_id": trigger_id, "result": {"order_no": "0000000001"},
                        "discipline_reasons": reasons if reasons is not None else
                        ["논지 t-tsm x3 목표 `price >= 431.68` (시세 438.5) — 가격 조건은 모델 재량 없이 즉시 판다"],
                        "fill": {"verdict": verdict, "filled_qty": qty, "avg_price": 438.535}}
            def reseed():
                db.theses.put(ledger()["theses"][0])          # 같은 id 행을 통째로 바꾼다(옛 파일 다시 쓰기 자리)
                if (cur() or {}).get("exit_triggers", [{}])[0].get("fired"):
                    problems.append("심기 실패 — 다시 심은 논지의 다리가 소진 상태다")

            def cur():
                return db.thesis_get("t-tsm")
            now = datetime(2026, 9, 21, 23, 58, tzinfo=KST)
            # b) FILLED 부분 체결 → 다리 fired · 논지는 held
            reseed()
            problems += _seed_check(theses=ledger()["theses"], watchlist=False)
            fill._close_theses_after_exit(row(), now, lambda *a, **k: None)
            d = cur()
            g = d["exit_triggers"][0]
            if not g.get("fired") or not g.get("fired_at") or "6주" not in str(g.get("fired_note")):
                problems.append(f"부분 체결이 다리를 소진 표시하지 않는다 — {g}")
            if d["status"] != "held":
                problems.append("부분 체결인데 논지를 닫았다")
            if d["exit_triggers"][1].get("fired"):
                problems.append("다른 다리까지 찍혔다")
            # c) 재호출 멱등
            fill._close_theses_after_exit(row(), now, lambda *a, **k: None)
            if cur()["exit_triggers"][0]["fired_at"] != g["fired_at"]:
                problems.append("멱등이 아니다 — fired_at이 갱신됐다")
            # d) 톱니 재현: 소진 표시 뒤 같은 시세로 다시 평가하면 매도가 없다
            snap = {"market": "US", "balance": {"positions": [{"ticker": "TSM", "qty": 7, "price": 446.0, "avg_price": 417.0}]},
                    "prices": {"TSM": {"price": 446.0}}}
            if rg.thesis_exit_sells(snap, "US"):
                problems.append("소진된 다리가 다시 발화한다(톱니)")
            # e) 취소·만료는 찍지 않는다
            reseed()
            for v in ("CANCELLED", "EXPIRED", "REJECTED"):
                fill._close_theses_after_exit(row(verdict=v), now, lambda *a, **k: None)
            if cur()["exit_triggers"][0].get("fired"):
                problems.append("취소·만료·거부가 다리를 소진시켰다")
            # f) trigger_id가 없어도 사유 문구에서 되찾는다(옛 기록)
            reseed()
            fill._close_theses_after_exit(row(trigger_id=None), now, lambda *a, **k: None)
            if not cur()["exit_triggers"][0].get("fired"):
                problems.append("trigger_id 폴백(사유 문구)이 안 된다")
            # g) set-trigger가 소진 표시를 지운다
            import io, contextlib
            with contextlib.redirect_stdout(io.StringIO()):
                # 10-03 다리 규칙(고정 목표는 기준가 +10% 이상 · 저항 근거 · 1/3 이하)에 맞춘 값 — 476.62(+3.3%)는
                # theses.cmd_set_trigger가 종료시켜 이 뒤 테스트가 10-03부터 돌지 않았다(2026-10-11 발견).
                th.cmd_set_trigger("t-tsm", "x3", "price >= 512.00", "resistance_3 (20260922 · 과거 고정)", "소진된 다리를 다음 take로",
                                   size_pct=33.0)
            g2 = cur()["exit_triggers"][0]
            if g2.get("fired") or g2.get("fired_at") or g2["check"] != "price >= 512.00":
                problems.append(f"set-trigger가 다리를 되살리지 않았다 — {g2}")
            # h) trigger_id가 trades 행까지 남는 코드인가
            src = (Path(__file__).parent / "execute.py").read_text(encoding="utf-8")
            # 2026-10-19: 미전송·전송 행이 한 행 사전(row)에서 나온다 — 한 번이면 두 길 모두 남는다
            if src.count('"trigger_id": o.get("trigger_id")') < 1:
                problems.append("execute가 trigger_id를 trades 행에 안 남긴다")
        finally:
            pass
    status = FAIL if problems else PASS
    results.append((status, "(66)-b~h 체결이 다리를 소진 표시 · 멱등 · 취소 제외 · set-trigger 복원", "; ".join(problems)))
    print(f"[{status}] (66)-b~h 체결이 다리를 소진 표시 · 멱등 · 취소 제외 · set-trigger 복원"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_leg_marked_on_fill)


def test_universe_us_gates():
    """(67) 편입 — 시세용 거래소 코드로 묻는다 · 빈 응답은 미확인(거부 아님) · 명시적 불가는 거부 · 시총 20B · market 추론."""
    import universe_apply as ua
    from kis_client import quote_excd
    problems = []
    if [quote_excd(x) for x in ("NYSE", "NASD", "AMEX", "NYS", "NAS", None)] != ["NYS", "NAS", "AMS", "NYS", "NAS", "NAS"]:
        problems.append("quote_excd 매핑이 틀렸다")

    class Cli:
        def __init__(self, out): self.out, self.seen = out, []
        def _headers(self, tr): return {}
        def _check(self, res, what): return res
        def _request(self, method, path, headers=None, params=None, body=None, timeout=20):
            self.seen.append(params.get("EXCD"))
            return {"rt_cd": "0", "output": self.out}
    rules = json.loads((Path(__file__).parent / "config" / "universe_rules.json").read_text(encoding="utf-8"))
    full = {"e_ordyn": "매매 가능", "last": "377.14", "tomv": "108588788780", "tamt": "2862338644", "e_icod": "정유"}
    # a) 주문용 코드를 줘도 시세용으로 바꿔 묻는다
    c = Cli(full)
    info = ua.check_us(c, "VLO", "NYSE", rules)
    if c.seen != ["NYS"]:
        problems.append(f"시세 TR에 {c.seen}로 물었다(기대 ['NYS'])")
    if round(info["price"], 2) != 377.14:
        problems.append("가격 파싱 실패")
    # b) 빈 응답은 Unknown(거부 아님)
    try:
        ua.check_us(Cli({}), "GEV", "NYSE", rules)
        problems.append("빈 응답이 통과했다")
    except ua.Unknown:
        pass
    except Exception as e:                              # noqa: BLE001
        problems.append(f"빈 응답이 Unknown이 아니라 {type(e).__name__}")
    # b′) 플래그만 공백이어도 Unknown
    try:
        ua.check_us(Cli({**full, "e_ordyn": ""}), "GEV", "NYS", rules)
        problems.append("플래그 공백이 통과했다")
    except ua.Unknown:
        pass
    except Exception as e:                              # noqa: BLE001
        problems.append(f"플래그 공백이 Unknown이 아니라 {type(e).__name__}")
    # c) 명시적 불가는 Reject
    try:
        ua.check_us(Cli({**full, "e_ordyn": "매매 불가"}), "X", "NYS", rules)
        problems.append("매매 불가가 통과했다")
    except ua.Reject:
        pass
    # d) 시총 20B 경계 — IBKR(41.6B)은 통과, 15B는 거부
    try:
        ua.check_us(Cli({**full, "tomv": "41628806640", "tamt": "370973995"}), "IBKR", "NAS", rules)
    except Exception as e:                              # noqa: BLE001
        problems.append(f"IBKR(41.6B)이 거부됐다: {e}")
    try:
        ua.check_us(Cli({**full, "tomv": "15000000000"}), "SMALL", "NAS", rules)
        problems.append("시총 15B이 통과했다")
    except ua.Reject:
        pass
    # d′) 거래대금은 완결 세션 값으로(FB-261017-2) — 장중 누적이 $15M이어도 직전 세션 $725M이면 통과 · 둘 다 작으면 거부
    try:
        info2 = ua.check_us(Cli({**full, "tamt": "15000000", "pamt": "725491343"}), "MCK", "NYS", rules)
        if info2["turnover_usd"] != 725491343.0:
            problems.append(f"거래대금이 직전 세션 값이 아니다: {info2['turnover_usd']}")
    except Exception as e:                              # noqa: BLE001
        problems.append(f"장중 누적만 작은 대형주가 거부됐다: {e}")
    try:
        ua.check_us(Cli({**full, "tamt": "15000000", "pvol": "100000", "base": "100"}), "THIN", "NYS", rules)
        problems.append("직전 세션도 작은($10M) 종목이 통과했다")
    except ua.Reject:
        pass
    # e) market 추론 — 6자리 숫자는 KR
    src = (Path(__file__).parent / "universe_apply.py").read_text(encoding="utf-8")
    if "ticker.isdigit() and len(ticker) == 6" not in src:
        problems.append("market 추론이 티커 모양을 안 본다")
    if "in_uni" not in src or "대기열 정리" not in src:
        problems.append("이미 편입된 pending 행 정리가 없다")
    status = FAIL if problems else PASS
    results.append((status, "(67) 편입 — 시세 코드·미확인·시총 20B·market 추론", "; ".join(problems)))
    print(f"[{status}] (67) 편입 — 시세 코드·미확인·시총 20B·market 추론"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_universe_us_gates)


def test_funding_coupling_and_fx():
    """(68) 회전 매도는 자금 대상 매수가 나갈 수 있을 때만 · FX는 최신 한 시점 + 낡으면 사유에 적는다."""
    import execute as ex
    problems = []
    src = (Path(__file__).parent / "execute.py").read_text(encoding="utf-8")
    for need in ("회전 매도 보류", "자금 대상", "refresh_limit(client, tgt"):
        if need not in src:
            problems.append(f"execute에 결합 점검이 없다: {need}")
    # a) 자금 대상이 가격 이탈이면 precheck가 skip을 낸다(09-23 삼성생명→테스 재현)
    class C:
        def domestic_price(self, t): return {"price": 158000.0}
    lim = {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 1}
    _px, _q, _why, skip = ex.refresh_limit(C(), {"ticker": "095610", "action": "BUY", "qty": 11,
                                                 "price": 154700.0, "market": "KR"}, lim)
    if not skip or "가격 이탈" not in skip:
        problems.append(f"가격 이탈이 skip으로 안 나온다 — {skip}")
    # b) 자금 대상이 정상이면 skip 없음
    class C2:
        def domestic_price(self, t): return {"price": 155000.0}
    _px, _q, _why, skip2 = ex.refresh_limit(C2(), {"ticker": "095610", "action": "BUY", "qty": 11,
                                                   "price": 154700.0, "market": "KR"}, lim)
    if skip2:
        problems.append(f"정상 범위인데 skip이 났다 — {skip2}")
    # c) FX — 이번 run 스냅샷의 환율이 최신이면 그것을 쓴다
    bal = {"currency": "KRW", "cash": 1_000_000, "positions": [], "exchange_rate": 1358.2,
           "exchange_rate_at": datetime.now(KST).isoformat()}
    saved = rg.DATA_DIR
    with tempfile.TemporaryDirectory() as td, testkit.db_case():   # 자산 곡선은 빈 임시 저장소 — 반대편은 스냅샷으로만
        rg.DATA_DIR = Path(td) / "data"
        rg.DATA_DIR.mkdir()
        if db.equity_rows():
            problems.append("빈 임시 저장소에 자산 곡선 행이 있다(격리 실패)")
        (rg.DATA_DIR / "snapshot_260922_us.json").write_text(json.dumps(
            {"market": "US", "generated_at": datetime.now(KST).isoformat(),
             "balance": {"currency": "USD", "cash": 1000, "positions": [], "exchange_rate": 1384.3}}), encoding="utf-8")
        try:
            pf = rg.portfolio_equity("KR", bal)
            if pf.get("fx") != 1358.2:
                problems.append(f"이번 run 환율(1358.2)이 아니라 {pf.get('fx')}를 썼다")
            # d) 낡은 환율이면 사유에 경고
            old_at = (datetime.now(KST) - timedelta(hours=13)).isoformat()
            pf2 = rg.portfolio_equity("KR", {**bal, "exchange_rate_at": old_at})
            if "환율이" not in pf2["why"] or "시간 전" not in pf2["why"]:
                problems.append(f"낡은 환율 경고가 없다 — {pf2['why'][-80:]}")
        finally:
            rg.DATA_DIR = saved
    status = FAIL if problems else PASS
    results.append((status, "(68) 회전 매도 결합 점검 · FX 최신·낡음 경고", "; ".join(problems)))
    print(f"[{status}] (68) 회전 매도 결합 점검 · FX 최신·낡음 경고"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_funding_coupling_and_fx)


# ============ (69)~(72) 2026-10-03 — 수익 하한 10%·상한 없음 · 첫 피드백 승인분(FB-261003) ============

_one = base_signal()["proposals"][0]
run_case("(69)-a BUY에 expected_upside_pct가 없으면 거부(상승 여지를 적어야 산다)",
         signal=base_signal(proposals=[{**_one, "expected_upside_pct": None}]),
         expect_reject_contains="expected_upside_pct 없음")
run_case("(69)-b 노리는 상승폭 8% → 바닥 10% 미달 거부(1~2%·한 자릿수 자리는 사지 않는다)",
         signal=base_signal(proposals=[{**_one, "expected_upside_pct": 8.0}]),
         expect_reject_contains="상승 여지 바닥 미달")
run_case("(69)-c 1차 고정 목표 +9% → 목표 여유 바닥(10%) 거부",
         signal=base_signal(proposals=[{**_one, "exits": [{"id": "x3", "check": "price >= 196200"}]}]),
         snapshot=None, expect_reject_contains="목표 여유 없음")
run_case("(69)-d 증분 1.5%인데 증액 조건 없음 → 거부",
         signal=base_signal(proposals=[{**_one, "weight_target_pct": 1.5}]),
         expect_reject_contains="증액 조건 없음")
run_case("(69)-e 증분 1.5% + increase_when → 통과",
         signal=base_signal(proposals=[{**_one, "weight_target_pct": 1.5,
                                        "increase_when": {"when": "e1 체결", "to_pct": 4.0}}]),
         expect_orders=1)


def test_feedback_rules():
    problems = []
    import theses as th_
    import execute as ex_
    import kis_client as kc_
    import crosscheck as cc_
    tmp = Path(tempfile.mkdtemp(prefix="fbtest_"))

    def put_theses(rows):
        """논지 원장 통째 바꾸기(옛 theses.json 다시 쓰기 자리) — 저장소 행을 지우고 심은 뒤 보이는지 본다."""
        for t in db.theses_all():
            db.theses.delete(t["id"])
        testkit.seed(theses=rows)
        problems.extend(_seed_check(theses=rows, watchlist=False))
        if len(db.theses_all()) != len(rows):
            problems.append(f"심기 실패 — 논지 {len(db.theses_all())}행(기대 {len(rows)})")
    _db = _ctxlib.ExitStack()
    try:
        _db.enter_context(testkit.db_case(tmp / "db"))      # 논지·지도·고점은 빈 임시 저장소에
        # (70)-a 모델 매도 꼬리표 — 열린 논지 1개면 붙인다 · 2개면 모호 거부
        put_theses([{"id": "kr-a-260901", "ticker": "005930", "status": "held", "exit_triggers": []}])
        tid, trg, bad = rg.tag_sell({"ticker": "005930", "discipline": "x2 발화 — 매크로+종목"})
        if (tid, trg, bad) != ("kr-a-260901", "x2", None):
            problems.append(f"(70)-a 꼬리표 자동 부착 실패 — {(tid, trg, bad)}")
        tid, trg, bad = rg.tag_sell({"ticker": "005930"})
        if trg != "discretion":
            problems.append(f"(70)-b 트리거 없으면 discretion — {trg}")
        put_theses([{"id": "kr-a-260901", "ticker": "005930", "status": "held"},
                    {"id": "kr-b-260902", "ticker": "005930", "status": "armed"}])
        if not rg.tag_sell({"ticker": "005930"})[2]:
            problems.append("(70)-c 열린 논지 2개인데 모호 거부가 없다")
        # (70)-d 회전 매도 대상 — 축 확인·이익 중인 최대 보유는 빼고 논지 없는 보유부터
        _ax = [{"id": "ax", "direction": "+", "status": "open"}]
        testkit.seed(axes=_ax)
        problems.extend(_seed_check(axes=_ax, watchlist=False))
        put_theses([{"id": "kr-win-260901", "ticker": "AAA", "status": "held", "axis_id": "ax",
                     "exit_triggers": [{"id": "x1", "check": "price <= 80"}]},
                    {"id": "kr-weak-260901", "ticker": "BBB", "status": "held", "axis_id": "other",
                     "exit_triggers": [{"id": "x1", "check": "price <= 95"}]}])
        pos = [{"ticker": "AAA", "qty": 10, "price": 130, "avg_price": 100, "eval_amt": 1300, "pnl_pct": 30},
               {"ticker": "BBB", "qty": 5, "price": 100, "avg_price": 110, "eval_amt": 500, "pnl_pct": -9},
               {"ticker": "CCC", "qty": 3, "price": 50, "avg_price": 50, "eval_amt": 150, "pnl_pct": 0}]
        order = [p["ticker"] for p, _ in rg.rotation_candidates(pos, set())]
        if order != ["CCC", "BBB"]:
            problems.append(f"(70)-d 회전 후보 순서 {order} (기대 ['CCC','BBB'] — 승자 AAA 보호 · 논지 없는 CCC 먼저)")
        # (70)-e 추적 다리 — 고점 대비 하락이 폭을 넘으면 판다
        testkit.seed(peaks={"US": {"TTT": {"peak_price": 200.0, "peak_at": "t"}}})
        if db.peaks_for_market("US").get("TTT", {}).get("peak_price") != 200.0:
            problems.append("심기 실패 — 고점 TTT 200이 저장소에 안 보인다")
        put_theses([{"id": "us-t-260901", "ticker": "TTT", "market": "US", "status": "held",
                     "exit_triggers": [{"id": "x3", "check": "from_peak_pct <= -6", "size_pct": 100}]}])
        mk = lambda px: {"prices": {"TTT": {"price": px}}, "balance": {"positions": [
            {"ticker": "TTT", "qty": 10, "price": px, "avg_price": 150, "pnl_pct": 0}]}}
        if rg.thesis_exit_sells(mk(190.0), "US"):
            problems.append("(70)-e 고점 대비 −5%인데 추적 다리가 발화했다")
        s2 = rg.thesis_exit_sells(mk(186.0), "US")
        if not s2 or s2[0]["qty"] != 10 or "추적" not in s2[0]["reasons"][0]:
            problems.append(f"(70)-f 고점 대비 −7%면 남은 보유 전량 추적 매도 — {s2}")
        # (71)-a 다리 규칙 — 고정 목표 바닥·저항·1/3, 매크로+종목, 도달 확률
        t0 = {"id": "x", "market": "US", "ticker": "TTT"}
        _ref = th_._ref_price
        th_._ref_price = lambda t: 100.0
        try:
            if not any("바닥 아래" in x for x in th_.leg_problems(t0, {"id": "x3", "check": "price >= 108", "basis": "저항 108", "size_pct": 30}, "exit")):
                problems.append("(71)-a +8% 고정 목표가 통과했다")
            if th_.leg_problems(t0, {"id": "x3", "check": "price >= 125", "basis": "이전 고점 125(resistance)", "size_pct": 30}, "exit"):
                problems.append("(71)-b +25% · 저항 근거 · 30% 고정 목표가 거부됐다")
            if th_.leg_problems(t0, {"id": "x4", "check": "from_peak_pct <= -6", "basis": "band_1d 2% × 3"}, "exit"):
                problems.append("(71)-c 추적 다리가 거부됐다")
            if not th_.leg_problems(t0, {"id": "x2", "check": "judge", "when": "FOMC 추가 인상 시사 시 전량"}, "exit"):
                problems.append("(71)-d 매크로 단독 매도 다리가 통과했다")
            if th_.leg_problems(t0, {"id": "x2", "check": "judge", "when": "FOMC 추가 인상 시사", "stock": "D램 계약가 하락 전환"}, "exit"):
                problems.append("(71)-e 매크로+종목 다리가 거부됐다")
            if not th_.leg_problems(t0, {"id": "e1", "check": "price <= 90"}, "entry"):
                problems.append("(71)-f 도달 확률 없는 진입 다리가 통과했다")
            if th_.entry_reach_ok({"entry_triggers": [{"id": "e1", "check": "price <= 90", "reach_prob": 0.2, "reach_n": 300}]}):
                problems.append("(71)-g 도달 확률 20% 다리 하나뿐인데 통과했다")
            if not th_.entry_reach_ok({"entry_triggers": [{"id": "e1", "check": "price <= 90", "reach_prob": 0.6, "reach_n": 300}]}):
                problems.append("(71)-h 도달 확률 60% 다리가 있는데 거부됐다")
        finally:
            th_._ref_price = _ref
        # (72)-a 매도 대금 의존 매수 — 체결률만큼 줄이고, 0이면 보류
        presell = [{"ticker": "S1", "action": "SELL", "qty": 10, "price": 100.0}]
        buys = [{"ticker": "B1", "action": "BUY", "qty": 20, "price": 50.0, "needs_sell_fill": ["S1"], "sell_dependent_amt": 600.0}]
        day = {"trades": [{"ticker": "S1", "action": "SELL", "approved_ref": "ap.json", "fill": {"verdict": "PARTIAL", "filled_qty": 5, "avg_price": 100.0}}]}
        out, sk = ex_.adjust_sell_dependent(buys, presell, day, "ap.json")
        if not out or out[0]["qty"] != 14:
            problems.append(f"(72)-a 체결률 50%면 의존분 600의 절반(300=6주)을 뺀 14주 — {[(o['ticker'], o['qty']) for o in out]}")
        out, sk = ex_.adjust_sell_dependent([{**buys[0], "qty": 12}], presell, {"trades": []}, "ap.json")
        if out or not sk:
            problems.append(f"(72)-b 매도 미체결이고 의존분이 주문 전부면 보류 — {out} {sk}")
        # (72)-c 초당 한도 POST — 접수 전이면 재전송, 주문번호 있으면 안 함
        calls, seq = [], []

        class _R:
            def __init__(s, b): s.b = json.dumps(b).encode()
            def read(s): return s.b
            def __enter__(s): return s
            def __exit__(s, *a): return False
        _uo, _bk = kc_.urllib.request.urlopen, kc_.RATE_LIMIT_BACKOFF_SEC
        kc_.urllib.request.urlopen = lambda req, timeout=None, context=None: (calls.append(1), _R(seq[len(calls) - 1]))[1]
        kc_.RATE_LIMIT_BACKOFF_SEC = 0
        try:
            c = kc_.KisClient.__new__(kc_.KisClient); c.base = "https://x"; c._ctx = None; c._throttle = lambda: None
            seq[:] = [{"rt_cd": "1", "msg_cd": "EGW00201", "output": {}}, {"rt_cd": "0", "output": {"ODNO": "1"}}]
            r = c._request("POST", "/o", headers={}, body={})
            if (r.get("output") or {}).get("ODNO") != "1" or len(calls) != 2:
                problems.append(f"(72)-c 접수 전 초당 한도 주문을 다시 보내지 않았다 — 호출 {len(calls)}")
            calls.clear(); seq[:] = [{"rt_cd": "1", "msg_cd": "EGW00201", "output": {"ODNO": "9"}}, {"rt_cd": "0", "output": {"ODNO": "10"}}]
            c._request("POST", "/o", headers={}, body={})
            if len(calls) != 1:
                problems.append("(72)-d 주문번호가 있는데 다시 보냈다(중복 주문 위험)")
        finally:
            kc_.urllib.request.urlopen, kc_.RATE_LIMIT_BACKOFF_SEC = _uo, _bk
    finally:
        _db.close()
        shutil.rmtree(tmp, ignore_errors=True)
    status = FAIL if problems else PASS
    results.append((status, "(70)~(72) 매도 꼬리표·회전 대상·추적 다리·다리 규칙·매도 대금·주문 재전송", "; ".join(problems)))
    print(f"[{status}] (70)~(72) 매도 꼬리표·회전 대상·추적 다리·다리 규칙·매도 대금·주문 재전송"
          + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_feedback_rules)


def test_one_share_floor():
    """(73) 1주 하한 — 매수 수량 내림이 0주면 1주(이탈 한도 안) · 반센트 승인가 · 한도 밖은 미전송 · 정정·재발주 수량도 같다 ·
    클라이언트 측 미전송은 '브로커 거부'로 찍지 않는다(2026-10-09 US ASML·CAT 재현)."""
    import execute as ex
    import fill as fl
    from kis_client import buy_qty_within
    problems = []
    lim = {"send_refresh_max_pct": 2.0, "fill_aggressive_ticks": 1}

    class Q:
        def __init__(self, p): self.p = p
        def overseas_price(self, t, excd="NAS"): return {"price": self.p}
        def domestic_price(self, t): return {"price": self.p}
    # (승인가, 시세, 승인 수량, 기대 수량, 미전송 기대, 시장)
    cases = [(1791.005, 1791.005, 1, 1, False, "US"),    # 반센트 승인가 — 시세가 같아도 0주였다
             (1791.30, 1793.35, 1, 1, False, "US"),      # +0.11% — 10-09 reissue
             (799.40, 803.14, 1, 1, False, "US"),        # +0.47% — 10-09 CAT
             (1791.30, 1830.00, 1, 0, True, "US"),       # +2.16% > 2% — 가격 이탈은 그대로 미전송
             (107.345, 107.345, 16, 16, False, "US"),    # 반센트 다주 — 반올림으로 한 주를 잃지 않는다
             (100.00, 101.50, 16, 15, False, "US"),      # 다주 드리프트는 수량으로 흡수(한도는 금액)
             (154700.0, 155000.0, 1, 1, False, "KR")]    # 국내 1주도 같다
    for ap, live, q, want_q, want_skip, mkt in cases:
        px, nq, why, skip = ex.refresh_limit(Q(live), {"ticker": "X", "action": "BUY", "qty": q, "price": ap,
                                                       "market": mkt, "excd": "NASD"}, lim)
        if bool(skip) != want_skip or (not want_skip and nq != want_q):
            problems.append(f"refresh {ap}→{live} x{q}: 수량 {nq} 미전송 {skip!r} (기대 {want_q}·{want_skip})")
        floored = q == 1 and live > ap + 0.01                  # 시세 드리프트로 내림이 0주 → 하한이 작동하는 경우
        if not want_skip and floored != ("1주 하한" in why):
            problems.append(f"refresh {ap}→{live}: 1주 하한 사유 표기가 맞지 않다 — {why}")
    for args, want in ((("BUY", 1, 0.0, 799.40, 803.94), 1), (("BUY", 0, 0.0, 799.40, 803.94), 0),
                       (("SELL", 3, 0.0, 0.0, 10.0), 3), (("BUY", 16, 0.0, 1600.0, 101.60), 15)):
        if fl.chase_qty(*args) != want:
            problems.append(f"chase_qty{args} = {fl.chase_qty(*args)} (기대 {want})")
    if buy_qty_within(0, 100.0, 10.0) != 0 or buy_qty_within(3, 30.0, 10.0) != 3:
        problems.append("buy_qty_within 경계값")
    _u, fills = ex._report_fills([{"ticker": "ASML", "action": "BUY", "qty": 1, "price": 1791.0}],
                                 {"ASML": 0}, {"positions": []}, {"ASML": "UNSENT"})
    note = (fills.get("ASML") or {}).get("note", "")
    if "미전송" not in note or "거부했다" in note:
        problems.append(f"미전송이 거부로 찍힌다 — {note!r}")
    status = FAIL if problems else PASS
    results.append((status, "(73) 1주 하한·반센트·미전송 표기", "; ".join(problems)))
    print(f"[{status}] (73) 1주 하한·반센트·미전송 표기" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_one_share_floor)


# (74) 새 종목은 이 시장 보유 평균보다 작게 사지 않는다 — 작게 사려면 small_why(FB-261010-5 · 2026-10-11 사용자 승인)
_FULL_KR = _mk_snap("kr", 10_000_000, [{"market": "KR", "ticker": "005930", "name": "삼성전자", "qty": 500,
                                         "avg_price": 70000, "price": 71000, "eval_amt": 60_000_000, "pnl_pct": 1.43}])
run_case("(74)-a 거의 다 투자된 계좌 · 새 종목 5% < 보유 평균 · 사유 없음 → 거부",
         signal=base_signal(), snapshot=_FULL_KR, limits_patch={**ROOMY},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=0, expect_reject_contains="보유 평균")
run_case("(74)-b 같은 제안에 small_why(kind·why)가 있으면 통과",
         signal=_with_small_why(base_signal()), snapshot=_FULL_KR, limits_patch={**ROOMY},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=1)
_bad_kind = base_signal()
_bad_kind["proposals"][0]["small_why"] = {"kind": "cash", "why": "남은 현금이 이것뿐"}
run_case("(74)-c '남은 현금' 같은 받지 않는 사유면 거부(키우면 회전 매도가 메운다)",
         signal=_bad_kind, snapshot=_FULL_KR, limits_patch={**ROOMY},
         extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=0, expect_reject_contains="보유 평균")
run_case("(74)-d 투자비중이 목표보다 모자라고 현금이 있으면 건너뛴다(gap 채우기가 키운다)",
         signal=base_signal(), snapshot=_mk_snap("kr", 60_000_000, [{"market": "KR", "ticker": "005930", "name": "삼성전자",
             "qty": 140, "avg_price": 70000, "price": 71000, "eval_amt": 10_000_000, "pnl_pct": 1.43}]),
         limits_patch={**ROOMY}, extra_snapshots={"snapshot_260911_us.json": _US_RICH},
         expect_orders=1)


def test_queue_one_day():
    """(75) 하루만 제안된 대기 후보 → 재제안 또는 [내림](FB-261010-6 · 2026-10-11 사용자 승인)."""
    import stage as st_
    import crosscheck as cc_
    problems = []
    with testkit.db_case():                       # 편입 대기열은 저장소 op_pending — 빈 임시 저장소에 심는다
        try:
            _pend = {"US:AAA": {"dates": ["2026-09-23"], "name": "Alpha", "axis": "ax1"},
                     "US:BBB": {"dates": ["2026-10-07", "2026-10-09"], "name": "Beta"},
                     "KR:000001": {"dates": ["2026-10-01"], "name": "국내"}}
            testkit.seed(pending=_pend)
            if db.pending_load() != _pend:
                problems.append("심기 실패 — 편입 대기열이 저장소에 그대로 안 보인다")
            q = st_.queue_one_day("us", "991231")
            if [r[0] for r in q] != ["AAA"]:
                problems.append(f"(75)-a 하루만 제안된 미국 후보만 — {q}")
            ok_note = "## §8 결론이 어디로 갔는가\n- AAA Alpha [내림] 같은 축을 보유로 충분히 탄다\n## §9 지도"
            bad_note = "## §8 결론이 어디로 갔는가\n- AAA Alpha 지켜본다\n## §9 지도"
            if not cc_.check_queue_one_day("us", "991231", ok_note)[0][1]:
                problems.append("(75)-b [내림]이 있는데 실패")
            r = cc_.check_queue_one_day("us", "991231", bad_note)[0]
            if r[1] or "AAA" not in r[2]:
                problems.append(f"(75)-c 판정 없는 후보를 못 잡았다 — {r}")
        finally:
            pass
    status = FAIL if problems else PASS
    results.append((status, "(75) 하루만 제안된 대기 후보 → 재제안·[내림]", "; ".join(problems)))
    print(f"[{status}] (75) 하루만 제안된 대기 후보 → 재제안·[내림]" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_queue_one_day)


def test_runner_catches_halts():
    """(76) 테스트 실행기(run_tests.py)가 멈춤·요약 없음·건수 감소를 '이상'으로 잡는다 · 캡처에 [테스트] 블록(2026-10-11 사용자)."""
    import run_tests as rt_
    import stage as st_
    problems = []
    saved = rt_.LOGS
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rt_.LOGS = d / "logs"
        try:
            (d / "halt.py").write_text("import atexit, sys\natexit.register(lambda: print('TEST-HALTED file=halt.py ran=3 failed=0 last=X'))\n"
                                       "print('[PASS] a'); sys.exit(2)\n", encoding="utf-8")
            (d / "nosum.py").write_text("print('[PASS] a')\n", encoding="utf-8")
            (d / "fewer.py").write_text("print('TEST-SUMMARY file=fewer.py total=90 passed=90 failed=0')\n", encoding="utf-8")
            (d / "good.py").write_text("print('TEST-SUMMARY file=good.py total=5 passed=5 failed=0')\n", encoding="utf-8")
            if rt_.run_one(str(d / "halt.py"), 0)["ok"]:
                problems.append("(76)-a 중간 종료(TEST-HALTED)를 정상으로 봤다")
            if rt_.run_one(str(d / "nosum.py"), 0)["ok"]:
                problems.append("(76)-b 요약 줄 없음을 정상으로 봤다")
            if rt_.run_one(str(d / "fewer.py"), 121)["ok"]:
                problems.append("(76)-c 건수 감소(121→90)를 정상으로 봤다")
            if not rt_.run_one(str(d / "good.py"), 5)["ok"]:
                problems.append("(76)-d 정상 완주를 이상으로 봤다")
        finally:
            rt_.LOGS = saved
    if "### 테스트" not in st_.tests_block():
        problems.append("(76)-e 1단 캡처에 [테스트] 블록이 없다")
    status = FAIL if problems else PASS
    results.append((status, "(76) 테스트 실행기가 멈춤·요약 없음·건수 감소를 잡는다", "; ".join(problems)))
    print(f"[{status}] (76) 테스트 실행기가 멈춤·요약 없음·건수 감소를 잡는다" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_runner_catches_halts)


def test_scenario_feedback_261012():
    """(77) 시나리오를 짜는 방식의 구멍과 손실·오판 차선(FB-261012).

    a·b·c 여러 축에 걸린 종목은 시장이 거래하는 축(price_driver)을 정하고, 무효 판정 다리는 그 축의 지표로 건다
    d 보유 논지의 추적 다리 누락을 센다 · e 예정일이 지난 미실현 시나리오를 '오래 열림'으로 잡고 `--expire`로 닫는다
    f 발화한 되돌림 다리에 [하락 원인]이 없으면 대조가 잡는다 · g 손실은 순실현으로(매도 확정의 delta) · h 가격 하락만으로 오판을 주지 않는다
    """
    problems = []
    import theses as th
    import market_map as mm
    import scenarios as sc
    import crosscheck as cc
    import loss_review as lr
    from datetime import date as _date
    # 논지·지도·시나리오는 빈 임시 저장소에 심는다. crosscheck는 아직 렌더본 파일(journal/theses.json)을 읽으므로
    # 그 자리는 저장소 행을 렌더해 둔다(운영에서도 그 파일은 저장소의 렌더본이다).
    with tempfile.TemporaryDirectory() as td, testkit.db_case(Path(td) / "db"):
        d = Path(td)
        (d / "journal").mkdir()
        saved = (cc.HERE, lr.HERE, lr.OUTDIR)

        def render_theses():
            (d / "journal" / "theses.json").write_text(json.dumps({"schema_version": "1.0", "theses": db.theses_all()},
                                                                   ensure_ascii=False), encoding="utf-8")
        try:
            _ax77 = [
                {"id": "ax_a", "name": "A", "thesis": "t", "direction": "+", "horizon_weeks": 8, "status": "open",
                 "opened": "2026-10-01", "driver": "ai_capex", "conflicts_with": ["ax_c"],
                 "chain": [{"step": 1, "indicator": "변압기 수주잔고", "status": "확인"}],
                 "beneficiaries": [{"ticker": "999990", "order": 2}]},
                {"id": "ax_b", "name": "B", "thesis": "t", "direction": "+", "horizon_weeks": 8, "status": "open",
                 "opened": "2026-10-01", "chain": [{"step": 1, "indicator": "구리 가격", "status": "확인"}],
                 "names": [{"ticker": "999990", "sign": "+"}, {"ticker": "999991", "sign": "+"}]},
                {"id": "ax_c", "name": "C", "thesis": "t", "direction": "-", "horizon_weeks": 8, "status": "open",
                 "opened": "2026-10-01", "conflicts_with": ["ax_a"], "chain": []}]
            testkit.seed(axes=_ax77)
            problems += _seed_check(axes=_ax77, watchlist=False)
            if db.theses_all():
                problems.append("빈 임시 저장소에 논지가 있다(격리 실패)")

            db.connect().execute("INSERT OR IGNORE INTO ev_fact (id, kind, verified_status, legacy_text)"
                                 " VALUES ('F-800077', 'legacy', 'verified', '테스트 근거')")

            def thesis(tid, **kw):
                t = {"id": tid, "market": "KR", "ticker": "999990", "name": "테스트", "axis": "t", "axis_id": "ax_b",
                     "thesis": "t", "expires": "2026-12-31", "evidence": [{"fact_id": "F-800077", "role": "support"}],
                     "entry_triggers": [{"id": "e0", "when": "선진입", "check": "judge", "size_pct": 1.0}],
                     "exit_triggers": [{"id": "x2", "when": "논지 훼손 — 구리 가격 급락", "check": "judge", "size_pct": 100.0}]}
                t.update(kw)
                f = d / f"{tid}.json"
                f.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
                return f
            try:
                th.cmd_add(thesis("kr-t-a-261012"))
                problems.append("(77)-a 두 축 종목이 price_driver 없이 등록됐다")
            except SystemExit as e:
                if "price_driver" not in str(e):
                    problems.append(f"(77)-a 거부 사유가 price_driver가 아니다: {e}")
            try:
                th.cmd_add(thesis("kr-t-b-261012", price_driver="ax_a"))
                problems.append("(77)-b 무효 다리가 거래 축 지표를 안 말하는데 통과했다")
            except SystemExit as e:
                if "지표" not in str(e):
                    problems.append(f"(77)-b 거부 사유가 다르다: {e}")
            try:
                th.cmd_add(thesis("kr-t-c-261012", price_driver="ax_a",
                                  exit_triggers=[{"id": "x2", "when": "논지 훼손 — 변압기 수주잔고 QoQ 감소", "check": "judge",
                                                  "size_pct": 100.0}]))
            except SystemExit as e:
                problems.append(f"(77)-c 맞게 건 논지가 거부됐다: {e}")
            if len(th.held_without_trail([{"status": "held", "market": "KR", "exit_triggers": [{"check": "price <= 1"}]},
                                          {"status": "held", "market": "KR", "exit_triggers": [{"check": "from_peak_pct <= -9"}]}])) != 1:
                problems.append("(77)-d 추적 다리 없는 보유 논지를 세지 못했다")

            _sc77 = [{"id": "SC-260901-1", "market": "kr", "opened": "260901", "status": "open", "realized": False,
                      "condition": "9/18 MOU 결과가 국내 기자재 명시", "judgments": []},
                     {"id": "SC-261008-1", "market": "kr", "opened": "261008", "status": "open", "realized": False,
                      "condition": "10-20 실적 발표", "judgments": []}]
            testkit.seed(scenarios=_sc77)
            if [r.get("id") for r in db.scenarios_all()] != ["SC-260901-1", "SC-261008-1"]:
                problems.append("심기 실패 — 시나리오가 저장소에 안 보인다")
            st = {r["id"] for r in sc.stale(today=_date(2026, 10, 12))}
            if st != {"SC-260901-1"}:
                problems.append(f"(77)-e 오래 열린 시나리오 판정이 틀렸다: {st}")
            sc.cmd_judge(argparse_ns(id="SC-260901-1", realized="n", acted=None, note="t", next_check="", price_cond=None,
                                     expire=True, batch="", carry_forward=""))
            r = next(x for x in sc.load()["scenarios"] if x["id"] == "SC-260901-1")
            if r.get("status") != "expired":
                problems.append(f"(77)-e --expire가 미실현 가지를 닫지 못했다: {r.get('status')}")

            cc.HERE = d
            (d / "journal" / "carry_261012_kr.md").write_text(
                "★ 발화 — 이미 내린 결정이다. 집행한다.\n  [매수] 999990 테스트  조건 price <= 100  (현재 99.0)\n"
                "        비중 1.0%  · 논지 kr-t-c-261012\n\n", encoding="utf-8")
            if [t.get("id") for t in db.theses_all()] != ["kr-t-c-261012"]:
                problems.append(f"(77)-c 등록된 논지가 저장소에 없다: {[t.get('id') for t in db.theses_all()]}")
            for t in db.theses_all():
                db.thesis_update(t["id"], lambda x: x["entry_triggers"].append(
                    {"id": "e1", "when": "되돌림", "check": "price <= 100", "basis": "retrace_100 100"}))
            render_theses()
            bad = cc.check_drop_cause("kr", "261012", "## §6 종목\n999990 e1 발화 집행\n## §7\n")
            good = cc.check_drop_cause("kr", "261012", "## §6 종목\n[하락 원인] 999990 — 업종 동반 n · 회사 뉴스 없음 · "
                                                       "원인 수급 · 논지 동인 비해당 → 집행\n## §7\n")
            if bad[0][1] or not good[0][1]:
                problems.append(f"(77)-f 하락 원인 대조가 틀렸다: 누락={bad[0][1]} 기재={good[0][1]}")

            lr.HERE, lr.OUTDIR = d, d / "feedback"
            (d / "journal" / "trades_261001_kr.json").write_text(json.dumps({"trades": [
                {"action": "BUY", "ticker": "999991", "qty": 10, "price": 100, "status": "SENT",
                 "fill": {"verdict": "FILLED", "filled_qty": 10, "avg_price": 100.0}},
                {"action": "BUY", "ticker": "999992", "qty": 10, "price": 100, "status": "SENT",
                 "fill": {"verdict": "FILLED", "filled_qty": 10, "avg_price": 100.0}}]}), encoding="utf-8")
            (d / "journal" / "trades_261010_kr.json").write_text(json.dumps({"trades": [
                {"action": "SELL", "ticker": "999991", "qty": 10, "price": 90, "status": "SENT",
                 "fill": {"verdict": "FILLED", "delta": -10, "limit_price": 91}},
                {"action": "SELL", "ticker": "999992", "qty": 10, "price": 90, "status": "SENT",
                 "fill": {"verdict": "FILLED", "delta": -10, "limit_price": 91}}]}), encoding="utf-8")
            ev = lr.realized_events("kr")
            if round(sum(e["pnl"] for e in ev)) != -200:
                problems.append(f"(77)-g 매도 확정 delta로 실현 손익을 못 쟀다: {[e['pnl'] for e in ev]}")
            closed = {"status": "closed", "market": "KR", "axis_id": "ax_b", "entered_on": "2026-10-01", "horizon_days": 40,
                      "history": [{"ts": "2026-10-10T10:00:00", "status": "closed"}]}
            for t in db.theses_all():                     # 논지 원장 통째 바꾸기(옛 theses.json 다시 쓰기 자리)
                db.theses.delete(t["id"])
            _th77 = [{**closed, "id": "kr-loss-quiet-261001", "ticker": "999991", "exit_triggers": [{"id": "x1", "check": "price <= 80"}]},
                     {**closed, "id": "kr-loss-stop-261001", "ticker": "999992",
                      "exit_triggers": [{"id": "x1", "check": "price <= 90", "fired": True}]}]
            testkit.seed(theses=_th77)
            problems += _seed_check(theses=_th77, watchlist=False)
            render_theses()
            rows = {r["thesis_id"]: r for r in lr.candidates("261012")}
            q, s_ = rows.get("kr-loss-quiet-261001") or {}, rows.get("kr-loss-stop-261001") or {}
            if q.get("suggested") != "일시적":
                problems.append(f"(77)-h 시계가 안 울린 손실을 오판으로 봤다: {q.get('suggested')} {q.get('clocks')}")
            if s_.get("suggested") != "오판" or s_.get("suggested_type") != "동인":
                problems.append(f"(77)-h 손절 발화+동인 멀쩡을 오판·동인으로 못 봤다: {s_.get('suggested')}/{s_.get('suggested_type')}")
        finally:
            cc.HERE, lr.HERE, lr.OUTDIR = saved
    status = FAIL if problems else PASS
    results.append((status, "(77) 시나리오 구멍(거래 축·하락 원인·오래 열림)과 손실·오판 차선", "; ".join(problems)))
    print(f"[{status}] (77) 시나리오 구멍(거래 축·하락 원인·오래 열림)과 손실·오판 차선" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_scenario_feedback_261012)


def test_cross_market_261012():
    """(78) 국내·미국은 독립적이지 않다(2026-10-12 사용자) — 전망은 요인 단위, 시나리오는 이벤트 단위.

    a 한 시장 전망은 사유 없이 거부 · b 같은 요인 반대 방향은 연결·사유 없이 거부(사유는 어느 쪽에 있어도 된다)
    c respond가 반대 시장 행에 이 시장 대응을 더하고 중복은 거부 · d §7 행은 [KR]·[US] 또는 영향 없음 사유
    e xmarket이 이쪽 대응이 빈 반대 시장 시나리오를 띄우고, respond 뒤엔 내린다
    """
    problems = []
    import market_map as mm
    import scenarios as sc
    import crosscheck as cc
    # 지도·논지·시나리오는 빈 임시 저장소에 심는다(파일 경로 갈아끼우기 대신)
    with tempfile.TemporaryDirectory() as td, testkit.db_case(Path(td) / "db"):
        d = Path(td)
        (d / "journal").mkdir()
        saved = (mm.JOURNAL, mm.HERE)
        try:
            base = {"id": "ax_kr", "name": "n", "thesis": "t", "direction": "+", "expected": "e", "horizon_weeks": 8,
                    "confidence": 0.4, "status": "open", "driver": "f1",
                    "beneficiaries": [{"ticker": "000001", "order": 1}, {"ticker": "000002", "order": 2}]}
            if not any("한 시장" in x or "종목뿐" in x for x in mm.forecast_validate(base, [])):
                problems.append("(78)-a 한 시장 전망을 사유 없이 통과시켰다")
            if mm.forecast_validate({**base, "no_cross_market_why": "환율·외국인 수급·미국 동종을 봤고 경로가 없다"}, []):
                problems.append("(78)-a 사유를 적은 한 시장 전망을 거부했다")
            both = {**base, "beneficiaries": base["beneficiaries"] + [{"ticker": "ABCD", "order": 3}]}
            opp = {**both, "id": "ax_us", "direction": "-"}
            if not any("방향이 반대" in x for x in mm.forecast_validate(both, [both, opp])):
                problems.append("(78)-b 같은 요인 반대 방향을 연결·사유 없이 통과시켰다")
            if mm.forecast_validate(both, [both, {**opp, "split_why": {"ax_kr": "메커니즘이 다르다"}}]):
                problems.append("(78)-b 상대 쪽 split_why가 덮는데도 거부했다")
            if mm.forecast_validate({**both, "conflicts_with": ["ax_us"]}, [both, opp]):
                problems.append("(78)-b conflicts_with로 이었는데도 거부했다")

            mm.JOURNAL, mm.HERE = d / "journal", d
            testkit.seed(axes=[both], scenarios=[
                {"id": "SC-261009-1", "market": "us", "opened_by": "us:261009", "opened": "261009", "status": "open",
                 "realized": None, "condition": "core CPI (10-14) above 0.2%", "actions": [{"market": "US", "what": "hold 98"}],
                 "judgments": []}])
            problems += _seed_check(axes=[both], watchlist=False)
            if [r.get("id") for r in db.scenarios_all()] != ["SC-261009-1"] or db.theses_all():
                problems.append("심기 실패 — 시나리오 SC-261009-1만(논지 0) 저장소에 있어야 한다")
            r1 = mm.xmarket("kr", "261012", since="261008")
            if [x["id"] for x in r1["scenarios_missing_here"]] != ["SC-261009-1"]:
                problems.append(f"(78)-e 대응 빈 반대 시장 시나리오를 못 띄웠다: {r1['scenarios_missing_here']}")
            sc.cmd_respond(argparse_ns(id="SC-261009-1", market="kr", what="[KR] 배분 98 유지", ticker="", note="", replace=False))
            if sc.cmd_respond(argparse_ns(id="SC-261009-1", market="kr", what="또", ticker="", note="", replace=False)) != 2:
                problems.append("(78)-c 같은 시장 대응 중복을 거부하지 않았다")
            r2 = mm.xmarket("kr", "261012", since="261008")
            if r2["scenarios_missing_here"]:
                problems.append("(78)-c·e respond 뒤에도 대응 빈 행으로 남았다")
            note_bad = "## §7 제안\n| S1 | CPI 하회 | 반등 | [KR] 배분 100 |\n## §8\n"
            note_ok = "## §7 제안\n| S1 | CPI 하회 | 반등 | [KR] 배분 100 · [US 영향 없음 — 미국 보유는 같은 조건을 SC-1이 본다] |\n## §8\n"
            if cc.check_scenario_both_markets("kr", "261012", note_bad)[0][1] or not cc.check_scenario_both_markets("kr", "261012", note_ok)[0][1]:
                problems.append("(78)-d §7 두 시장 대응 대조가 틀렸다")
        finally:
            mm.JOURNAL, mm.HERE = saved
    status = FAIL if problems else PASS
    results.append((status, "(78) 국내·미국 교차 — 전망은 요인 단위 · 시나리오는 이벤트 단위", "; ".join(problems)))
    print(f"[{status}] (78) 국내·미국 교차 — 전망은 요인 단위 · 시나리오는 이벤트 단위" + (f" — {'; '.join(problems)}" if problems else ""))


_safe(test_cross_market_261012)


def test_real_account_261012():
    """(79)~(84) 실계좌 전환(2026-10-12 사용자 결정) — 주문당 상한·조회 전용·주간거래 매도 전용·통합증거금 필드·세금 원장·토큰 칸."""
    import kis_client as kc
    import execute as ex
    import fill as fl
    import tax_ledger as tl
    from datetime import date as _date

    # ── (79) 주문당 상한 · 2주 예외 · 래치 · 미달 검사 ──
    p79 = []
    lim = {"order_cap": {"max_krw_per_order": 1000000, "max_shares_when_price_over_cap": 2, "apply_in_paper": False}}
    if rg.order_cap_cfg({**lim, "account_mode": "paper"}):
        p79.append("모의 계좌에 상한을 걸었다(apply_in_paper false)")
    if not rg.order_cap_cfg({**lim, "account_mode": "real_readonly"}):
        p79.append("조회 전용 모드에 상한이 없다")
    orders = [{"ticker": "000660", "action": "BUY", "qty": 20, "price": 110000.0},
              {"ticker": "HYO", "action": "BUY", "qty": 5, "price": 1500000.0},
              {"ticker": "005930", "action": "SELL", "qty": 50, "price": 90000.0}]
    out, rej, _, trimmed = rg.apply_order_cap(orders, {**lim, "account_mode": "real"}, "KR", 1.0)
    q = {o["ticker"]: o["qty"] for o in out}
    if q.get("000660") != 9 or q.get("HYO") != 2 or q.get("005930") != 50 or rej:
        p79.append(f"국내 상한 계산 오류 {q} rej={rej}")
    if abs(trimmed - (11 * 110000 + 3 * 1500000)) > 1:
        p79.append(f"깎인 금액 {trimmed}")
    out_us, rej_us, _, _ = rg.apply_order_cap([{"ticker": "ASML", "action": "BUY", "qty": 4, "price": 800.0},
                                               {"ticker": "AAPL", "action": "BUY", "qty": 30, "price": 50.0}],
                                              {**lim, "account_mode": "real"}, "US", 1400.0)
    if {o["ticker"]: o["qty"] for o in out_us} != {"ASML": 2, "AAPL": 14}:
        p79.append(f"미국 상한(환율 환산) 오류 {[(o['ticker'], o['qty']) for o in out_us]}")
    _, rej0, _, _ = rg.apply_order_cap([{"ticker": "AAPL", "action": "BUY", "qty": 3, "price": 50.0}],
                                       {**lim, "account_mode": "real"}, "US", 0.0)
    if not rej0:
        p79.append("환율 미확인인데 미국 매수를 통과시켰다")
    if rg.real_latch_needs_cap({**lim, "real_trading_enabled": True}):
        p79.append("주문당 상한이 있는데 래치를 거부했다")
    if not rg.real_latch_needs_cap({"real_trading_enabled": True}):
        p79.append("상한 없이 래치를 열었다")
    cap = {**rg.order_cap_cfg({**lim, "account_mode": "real"}), "fx": 1.0}
    q1, note1, bad1 = ex.cap_check({"action": "BUY", "market": "KR", "qty": 9, "price": 110000.0}, 113000.0, 9, cap)
    if bad1 or q1 != 8 or not note1:
        p79.append(f"전송 직전 시세 상승 축소 오류 q={q1} bad={bad1}")
    q2, _, bad2 = ex.cap_check({"action": "BUY", "market": "KR", "qty": 20, "price": 110000.0}, 110000.0, 20, cap)
    if not bad2 or q2:
        p79.append("상한을 넘는 승인(우회)을 전송 직전에 막지 않았다")
    d = rg.deficit_check({"proposals": [{"action": "BUY", "ticker": "000660"}]}, {}, "KR", 10.0, [], "2026-10-13",
                         {"target_invested_pct": 90, "need": 5e6, "need_effective": 5e6, "cap_trimmed": 4e6},
                         [{"action": "BUY", "qty": 9, "price": 110000.0}])
    if "한도가 아니라 제안이 모자라다" in d.get("why", ""):
        p79.append("상한이 깎은 금액을 '제안 부족'으로 셌다")
    for nm, pr in (("(79) 주문당 상한 100만·1주 초과 2주·래치·미달 검사", p79),):
        results.append((FAIL if pr else PASS, nm, "; ".join(pr)))
        print(f"[{FAIL if pr else PASS}] {nm}" + (f" — {'; '.join(pr)}" if pr else ""))

    # ── (80) 조회 전용 모드는 주문을 막는다 · 계좌 모드 하나 ──
    p80 = []
    saved_env, saved_latch = kc.load_env, kc._real_trading_enabled
    kc.load_env = lambda: {"KIS_PAPER_APP_KEY": "p" * 12, "KIS_PAPER_APP_SECRET": "s", "KIS_PAPER_ACCOUNT": "11111111-01",
                           "KIS_REAL_APP_KEY": "r" * 12, "KIS_REAL_APP_SECRET": "s", "KIS_REAL_ACCOUNT": "22222222-01"}
    try:
        if kc.account_mode({"account_mode": "weird"}) != "paper":
            p80.append("모르는 계좌 모드를 paper로 떨어뜨리지 않았다")
        c = kc.KisClient.for_mode(cfg={"account_mode": "real_readonly"})
        if c.svr != "real" or not c.read_only or c.mode != "real_readonly":
            p80.append(f"real_readonly 팩토리 오류 svr={c.svr} ro={c.read_only}")
        sent = []
        c._request = lambda *a, **k: sent.append(a) or {"rt_cd": "0", "output": {"ODNO": "1"}}
        for fn, args in ((c.domestic_order, ("005930", "BUY", 1, 70000)), (c.overseas_order, ("AAPL", "BUY", 1, 100.0)),
                         (c.domestic_modify, ("1", "", 1, 70000)), (c.overseas_modify, ("1", "AAPL", "NASD", 1, 100.0)),
                         (c.overseas_daytime_order, ("AAPL", "SELL", 1, 100.0))):
            try:
                fn(*args)
                p80.append(f"{fn.__name__}가 조회 전용에서 나갔다")
            except kc.KisError:
                pass
        if sent:
            p80.append("조회 전용인데 주문 요청이 전송됐다")
        if kc.KisClient.for_mode(cfg={"account_mode": "real"}).read_only is not True:
            p80.append("real 모드의 조회 클라이언트(orders=False)가 주문 가능으로 열렸다")
        kc._real_trading_enabled = lambda: True
        try:
            kc.KisClient(svr="real", allow_real=True)        # limits.json account_mode는 paper — 열리면 안 된다
            if kc.account_mode() != "real":
                p80.append("account_mode가 real이 아닌데 실계좌 주문 클라이언트가 열렸다")
        except kc.KisError:
            pass
        try:
            ex.preflight({"limits_sha256": rg.sha256_of(rg.LIMITS_PATH), "generated_at": datetime.now(KST).isoformat(),
                          "schema_version": rg.SCHEMA_VERSION, "account_mode": "real"}, Path("x.json"),
                         {"account_mode": "paper"})
            p80.append("다른 계좌 모드의 승인을 통과시켰다")
        except rg.Rejection:
            pass
    finally:
        kc.load_env, kc._real_trading_enabled = saved_env, saved_latch
    results.append((FAIL if p80 else PASS, "(80) 조회 전용 모드 — 주문·정정·주간거래 전부 거부 · 승인 모드 대조", "; ".join(p80)))
    print(f"[{FAIL if p80 else PASS}] (80) 조회 전용 모드 — 주문·정정·주간거래 전부 거부 · 승인 모드 대조" + (f" — {'; '.join(p80)}" if p80 else ""))

    # ── (81) 주간거래 — 매도 전용 · 창 · 손절·추적 다리만 ──
    p81 = []
    c = kc.KisClient.__new__(kc.KisClient)
    c.svr, c.read_only, c.tr, c.cano, c.acnt_prdt_cd = "real", False, kc.TR["real"], "22222222", "01"
    c._headers = lambda tr: {"tr_id": tr}
    sent = []
    c._request = lambda method, path, headers=None, params=None, body=None, **k: sent.append((path, headers, body)) or {"rt_cd": "0", "output": {"ODNO": "9"}}
    try:
        c.overseas_daytime_order("AAPL", "BUY", 1, 100.0)
        p81.append("주간거래 매수가 나갔다")
    except ValueError:
        pass
    r = c.overseas_daytime_order("AAPL", "SELL", 2, 99.5, "NAS")
    if not sent or "daytime-order" not in sent[-1][0] or sent[-1][1]["tr_id"] != "TTTS6037U" \
            or sent[-1][2]["ORD_DVSN"] != "00" or sent[-1][2]["OVRS_EXCG_CD"] != "NASD" or r.get("session") != "daytime":
        p81.append(f"주간거래 매도 요청 형식 오류 {sent[-1:] }")
    cp = kc.KisClient.__new__(kc.KisClient)
    cp.svr, cp.read_only, cp.tr = "paper", False, kc.TR["paper"]
    try:
        cp.overseas_daytime_order("AAPL", "SELL", 1, 100.0)
        p81.append("모의 계좌에서 주간거래가 열렸다")
    except kc.KisError:
        pass
    dst_close = kc.us_daytime_session(datetime(2026, 10, 12, 17, 30, tzinfo=KST))
    std_open = kc.us_daytime_session(datetime(2026, 12, 14, 17, 30, tzinfo=KST))
    if dst_close["is_open"] or not std_open["is_open"] or not kc.us_daytime_session(datetime(2026, 10, 13, 10, 35, tzinfo=KST))["is_open"]:
        p81.append("주간거래 창(서머타임 17:00 · 표준시 18:00 · 10:35 열림) 오류")
    with testkit.db_case():                       # 논지는 빈 임시 저장소에 심는다
        try:
            _th81 = [{"id": "t1", "ticker": "AAA", "market": "US", "status": "held",
                      "exit_triggers": [{"id": "x1", "check": "price <= 100"}]},
                     {"id": "t2", "ticker": "BBB", "market": "US", "status": "held",
                      "exit_triggers": [{"id": "x2", "check": "price >= 50", "size_pct": 33}]}]
            testkit.seed(theses=_th81)
            p81 += _seed_check(theses=_th81, watchlist=False)
            snap = {"market": "US", "balance": {"positions": [
                {"ticker": "AAA", "qty": 10, "price": 90.0, "avg_price": 110.0, "eval_amt": 900.0},
                {"ticker": "BBB", "qty": 9, "price": 60.0, "avg_price": 40.0, "eval_amt": 540.0}]},
                "prices": {"AAA": {"price": 90.0}, "BBB": {"price": 60.0}}}
            kinds = {o["ticker"]: o.get("leg_kind") for o in rg.thesis_exit_sells(snap, "US")}
            if kinds != {"AAA": "stop", "BBB": "target"}:
                p81.append(f"다리 종류 표시 오류 {kinds}")
        finally:
            pass
    results.append((FAIL if p81 else PASS, "(81) 미국 주간거래 — 매도 전용 · 모의 불가 · 창 · 손절/추적만", "; ".join(p81)))
    print(f"[{FAIL if p81 else PASS}] (81) 미국 주간거래 — 매도 전용 · 모의 불가 · 창 · 손절/추적만" + (f" — {'; '.join(p81)}" if p81 else ""))

    # ── (82) 통합증거금 필드 · 공유 현금 한 번 · SOR 거부 시 KRX 재전송 · 체결조회 ALL ──
    p82 = []
    psr = {"ord_psbl_frcr_amt": "0.00", "frcr_ord_psbl_amt1": "7026.181345", "max_ord_psbl_qty": "0",
           "ovrs_max_ord_psbl_qty": "28", "exrt": "1338.0"}
    o_real, o_paper = kc.orderable_from_psamount(psr, "real"), kc.orderable_from_psamount({**psr, "ord_psbl_frcr_amt": "100000"}, "paper")
    if abs(o_real["orderable_cash"] - 7026.18) > 0.01 or o_real["max_qty"] != 28 or not o_real["shared"]:
        p82.append(f"통합증거금 미국 구매력 필드 오류 {o_real}")
    if o_paper["orderable_cash"] != 100000 or o_paper["shared"]:
        p82.append("모의 구매력 필드가 바뀌었다")
    m1 = kc.kr_cash_from_margin({"ovrs_stck_itgr_mgna_dvsn_name": "통합증거금신청", "stck_itgr_cash100_ord_psbl_amt": "9950248.76",
                                 "stck_cash100_max_ord_psbl_amt": "9950248.76"})
    m0 = kc.kr_cash_from_margin({"ovrs_stck_itgr_mgna_dvsn_name": "", "stck_cash100_max_ord_psbl_amt": "5000000"})
    if not m1 or not m1["shared"] or abs(m1["cash"] - 9950248.76) > 0.01 or not m0 or m0["shared"] or m0["cash"] != 5000000:
        p82.append(f"국내 주문가능(통합증거금) 오류 {m1} {m0}")
    with tempfile.TemporaryDirectory() as td, testkit.db_case():   # 자산 곡선 빈 저장소 — 스냅샷만으로 잰다
        saved_d = rg.DATA_DIR
        rg.DATA_DIR = Path(td)
        try:
            if db.equity_rows():
                p82.append("빈 임시 저장소에 자산 곡선 행이 있다(격리 실패)")
            (Path(td) / "snapshot_261012_us.json").write_text(json.dumps({
                "generated_at": datetime.now(KST).isoformat(),
                "balance": {"currency": "USD", "cash": 7000.0, "cash_shared": True, "exchange_rate": 1400.0,
                            "positions": [{"ticker": "AAPL", "eval_amt": 1000.0}]}}), encoding="utf-8")
            pf = rg.portfolio_equity("KR", {"currency": "KRW", "cash": 9800000.0, "cash_shared": True, "positions": []})
            if not pf.get("ok") or abs(pf["krw"] - (9800000 + 1000 * 1400)) > 1:
                p82.append(f"공유 현금을 두 번 셌다 {pf.get('krw')}")
            # 공유 규칙은 portfolio.nav 한 곳(2026-10-19 · Codex R06): 어느 쪽이든 공유 표시면 가장 새 조각(이번 run)의 현금만 센다
            pf_mix = rg.portfolio_equity("KR", {"currency": "KRW", "cash": 9800000.0, "positions": []})
            if abs(pf_mix["krw"] - (9800000 + 1000 * 1400)) > 1:
                p82.append(f"반대편만 공유 표시 — 현금을 한 번만 세지 않았다 {pf_mix.get('krw')}")
            (Path(td) / "snapshot_261012_us.json").write_text(json.dumps({
                "generated_at": datetime.now(KST).isoformat(),
                "balance": {"currency": "USD", "cash": 7000.0, "exchange_rate": 1400.0,
                            "positions": [{"ticker": "AAPL", "eval_amt": 1000.0}]}}), encoding="utf-8")
            pf2 = rg.portfolio_equity("KR", {"currency": "KRW", "cash": 9800000.0, "positions": []})
            if abs(pf2["krw"] - (9800000 + 8000 * 1400)) > 1:
                p82.append("공유 표시 없는 모의 계좌 합산이 바뀌었다")
        finally:
            rg.DATA_DIR = saved_d

    class _SorCli:
        kr_excg = "SOR"
        def __init__(self, err, kind="rejected"):
            self.calls, self.err, self.kind = [], err, kind
        def domestic_order(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 1:
                raise kc.KisError(self.err, kind=self.kind)     # 증권사 응답 거절(_check)은 kind=rejected
            return {"order_no": "7", "excg": kw.get("excg")}
    sc_ = _SorCli("국내 주문 실패 [APBK0000] SOR 주문 불가 종목")
    r = ex.place(sc_, {"market": "KR", "ticker": "005930", "action": "BUY", "qty": 1, "price": 70000})
    if len(sc_.calls) != 2 or sc_.calls[1].get("excg") != "KRX" or not r.get("fallback"):
        p82.append(f"SOR 거부 뒤 KRX 재전송 안 함 {sc_.calls}")
    # ★ 응답 유실(unknown)은 SOR이 받았을 수 있다 — 문구에 SOR이 있어도 KRX로 다시 보내지 않는다(2026-10-19 · R01)
    sc3 = _SorCli("network error on POST /order-cash: TimeoutError SOR", kind="unknown")
    try:
        ex.place(sc3, {"market": "KR", "ticker": "005930", "action": "BUY", "qty": 1, "price": 70000})
        p82.append("응답 유실을 KRX로 재전송했다(이중 주문 위험)")
    except kc.KisError:
        if len(sc3.calls) != 1:
            p82.append(f"응답 유실 뒤 재전송 {len(sc3.calls)}회")
    sc2 = _SorCli("국내 주문 실패 [40310000] 주문가능금액 부족")
    try:
        ex.place(sc2, {"market": "KR", "ticker": "005930", "action": "BUY", "qty": 1, "price": 70000})
        p82.append("거래소와 무관한 거부를 재전송했다")
    except kc.KisError:
        pass
    seen = {}
    c = kc.KisClient.__new__(kc.KisClient)
    c.svr, c.read_only, c.tr, c.cano, c.acnt_prdt_cd = "real", False, kc.TR["real"], "22222222", "01"
    c._headers = lambda tr: {"tr_id": tr}
    c._request = lambda method, path, headers=None, params=None, body=None, **k: seen.update(
        {"tr": headers["tr_id"], "params": params}) or {"rt_cd": "0", "output1": []}
    c.domestic_order_status("1", "005930")
    if seen.get("tr") != "TTTC0081R" or (seen.get("params") or {}).get("EXCG_ID_DVSN_CD") != "ALL":
        p82.append(f"실계좌 체결조회가 TTTC0081R·ALL이 아니다 {seen.get('tr')}")
    mod = []
    class _ModCli:
        kr_excg = "SOR"
        def domestic_modify(self, *a, **kw):
            mod.append(kw)
            return {"order_no": "8"}
    fl._modify(_ModCli(), {"market": "KR", "result": {"excg": "KRX"}}, "1", "", "", 1, 70000, True)
    fl._modify(_ModCli(), {"market": "KR", "result": {"excg": "SOR"}}, "1", "", "", 1, 70000, True)
    if mod != [{"cancel": True, "excg": "KRX"}, {"cancel": True}]:
        p82.append(f"원주문 거래소로 정정·취소하지 않았다 {mod}")
    results.append((FAIL if p82 else PASS, "(82) 통합증거금 필드 · 공유 현금 1회 · SOR→KRX 재전송 · 체결조회 ALL", "; ".join(p82)))
    print(f"[{FAIL if p82 else PASS}] (82) 통합증거금 필드 · 공유 현금 1회 · SOR→KRX 재전송 · 체결조회 ALL" + (f" — {'; '.join(p82)}" if p82 else ""))

    # ── (83) 세금 원장 — 결제일 환율 · 선입선출 · 결제일 계산 ──
    p83 = []
    if fl.settle_date("KR", _date(2026, 10, 16)) != "2026-10-20" or fl.settle_date("US", _date(2026, 10, 16)) != "2026-10-19":
        p83.append(f"결제일 계산 오류 KR {fl.settle_date('KR', _date(2026, 10, 16))} US {fl.settle_date('US', _date(2026, 10, 16))}")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "journal").mkdir()
        rec = {"svr": "real", "trades": [
            {"ts": "2026-10-13T23:00:00+09:00", "ticker": "AAA", "action": "BUY",
             "fill": {"verdict": "FILLED", "filled_qty": 10, "avg_price": 100.0, "settle_date": "2026-10-14", "settle_fx": 1300.0}},
            {"ts": "2026-10-20T23:00:00+09:00", "ticker": "AAA", "action": "SELL",
             "fill": {"verdict": "FILLED", "filled_qty": 10, "avg_price": 120.0, "settle_date": "2026-10-21", "settle_fx": None}}]}
        (Path(td) / "journal" / "trades_261013_us.json").write_text(json.dumps(rec), encoding="utf-8")
        saved_h = tl.HERE
        tl.HERE = Path(td)
        try:
            if tl.fill_fx(1400.0, today=_date(2026, 10, 20)) != 0:
                p83.append("결제일 전 체결에 환율을 채웠다")
            if tl.fill_fx(1400.0, today=_date(2026, 10, 21)) != 1:
                p83.append("결제일 당일 환율을 못 채웠다")
            led = tl.ledger(2026)
            want = 10 * 120 * 1400 - 10 * 100 * 1300 - 10 * 100 * 0.0025 * 1300 - 10 * 120 * (0.0025 + 0.0000206) * 1400
            if len(led["rows"]) != 1 or abs(led["net_gain_krw"] - want) > 2 or led["tax_krw"] != 0:
                p83.append(f"원화 손익(결제일 환율·선입선출·비용) 오류 {led['net_gain_krw']} vs {round(want)}")
            rec2 = json.loads((Path(td) / "journal" / "trades_261013_us.json").read_text(encoding="utf-8"))
            rec2["svr"] = "paper"
            (Path(td) / "journal" / "trades_261013_us.json").write_text(json.dumps(rec2), encoding="utf-8")
            if tl.ledger(2026)["rows"]:
                p83.append("모의 기록을 세금 원장에 넣었다")
        finally:
            tl.HERE = saved_h
    results.append((FAIL if p83 else PASS, "(83) 세금 원장 — 결제일 환율 · 선입선출 · 실계좌만", "; ".join(p83)))
    print(f"[{FAIL if p83 else PASS}] (83) 세금 원장 — 결제일 환율 · 선입선출 · 실계좌만" + (f" — {'; '.join(p83)}" if p83 else ""))

    # ── (84) 토큰 캐시 — 서버별 칸(옛 한 칸 형식도 읽는다) ──
    p84 = []
    with tempfile.TemporaryDirectory() as td:
        saved_tc = kc.TOKEN_CACHE
        kc.TOKEN_CACHE = Path(td) / ".token_cache.json"
        try:
            kc.TOKEN_CACHE.write_text(json.dumps({"svr": "paper", "app_key_tail": "pppppp", "access_token": "T1",
                                                  "expires_at": (datetime.now(KST) + timedelta(hours=5)).isoformat()}), encoding="utf-8")
            if set(kc.KisClient._read_token_file()) != {"paper"}:
                p84.append("옛 한 칸 형식을 못 읽었다")
            cr = kc.KisClient.__new__(kc.KisClient)
            cr.svr, cr.app_key, cr.app_secret, cr._token = "real", "r" * 12, "s", None
            cr._request = lambda *a, **k: {"access_token": "T2", "expires_in": 86400}
            cr.token()
            both = json.loads(kc.TOKEN_CACHE.read_text(encoding="utf-8"))
            if set(both) != {"paper", "real"} or both["paper"]["access_token"] != "T1":
                p84.append(f"실계좌 토큰이 모의 토큰을 지웠다 {list(both)}")
        finally:
            kc.TOKEN_CACHE = saved_tc
    results.append((FAIL if p84 else PASS, "(84) 토큰 캐시 서버별 칸 — 모의·실계좌가 서로 지우지 않는다", "; ".join(p84)))
    print(f"[{FAIL if p84 else PASS}] (84) 토큰 캐시 서버별 칸 — 모의·실계좌가 서로 지우지 않는다" + (f" — {'; '.join(p84)}" if p84 else ""))


_safe(test_real_account_261012)


def test_confirmed_axis_undersized_261017():
    """(85) 확인된 축의 소액 보유(FB-261017-8) — 열린 + 전망 축의 논지가 있고 현재가 > 평균가인 보유가 이 시장 보유 평균×0.5보다
    작으면 이 run의 증액 BUY 또는 holds[].small_why{kind, why, expected_upside_pct}(계좌 기여)가 있어야 한다 — 없으면 undersized_short."""
    D = "051910"
    pos = [{"market": "KR", "ticker": tk, "name": tk, "qty": 10, "avg_price": av, "price": px, "eval_amt": ev, "pnl_pct": 0.0}
           for tk, av, px, ev in (("005930", 70000, 71000, 400_000), ("000660", 170000, 180000, 400_000),
                                  ("035420", 180000, 190000, 400_000), (D, 9000, 10000, 100_000))]
    snap = _mk_snap("kr", 8_700_000, pos)
    snap["prices"][D] = {"price": 10000, "change_pct": 0.5}
    snap["prices"]["035420"] = {"price": 190000, "change_pct": 0.0}
    th = {"theses": [{"id": "kr-051910-axup-261010", "market": "KR", "ticker": D, "status": "held", "axis_id": "ax_up",
                      "created": "2026-10-10"}]}
    amap = {"axes": [{"id": "ax_up", "direction": "+", "status": "open", "driver": "energy"}]}
    avg_w = 1_300_000 / 10_000_000 * 100 / 4             # 3.25%
    line = round(avg_w * 0.5, 2)                          # 1.62~1.63%

    def _exp(short, **kw):
        def chk(ap, problems):
            u = ap.get("undersized") or {}
            if bool(ap.get("undersized_short")) is not short or bool(u.get("short")) is not short:
                problems.append(f"undersized_short={ap.get('undersized_short')} (기대 {short}) · {u.get('why')}")
            if kw.get("line") and abs(float(u.get("line") or 0) - line) > 0.01:
                problems.append(f"선 {u.get('line')} ≠ 보유 평균×0.5 {line}")
            rows = {r["ticker"]: r for r in u.get("rows") or []}
            if kw.get("no_row") and D in rows:
                problems.append(f"현재가 < 평균가인데 확인된 축 행이 있다: {rows[D]}")
            if kw.get("contrib") is not None and (rows.get(D) or {}).get("contribution_pp") != kw["contrib"]:
                problems.append(f"계좌 기여 {(rows.get(D) or {}).get('contribution_pp')} (기대 {kw['contrib']})")
            if kw.get("increase") and not (rows.get(D) or {}).get("increase"):
                problems.append(f"증액 BUY를 못 셌다: {rows.get(D)}")
            if kw.get("off") and "꺼짐" not in str(u.get("why")):
                problems.append(f"ratio 0인데 꺼짐이 아니다: {u.get('why')}")
            if short and "확인된 축 보유 051910(ax_up)" not in str(u.get("why")):
                problems.append(f"미결 문구: {u.get('why')}")
        return chk
    run_case("(85)-a 확인된 축 보유 1% < 보유 평균 3.25%×0.5 — 증액·사유 없음 → undersized_short",
             signal=_with_small_why(base_signal()), snapshot=snap, limits_patch={**ROOMY},
             extra_theses=th, extra_map=amap, expect_approved=_exp(True, line=True))
    w_d = round(100_000 / 10_000_000 * 100, 2)
    run_case("(85)-b holds[].small_why{etf_expression, 기대 30%} → 통과 · 계좌 기여 = 비중×기대 상승",
             signal=_with_small_why(base_signal(holds=[{"ticker": D, "why": "축 표현 중",
                                                        "small_why": {"kind": "etf_expression", "why": "ETF 소액으로 축을 표현 중",
                                                                      "expected_upside_pct": 30}}])),
             snapshot=snap, limits_patch={**ROOMY}, extra_theses=th, extra_map=amap,
             expect_approved=_exp(False, contrib=round(w_d * 30 / 100, 2)))
    buy_d = {"ticker": D, "name": "LG화학", "action": "BUY", "weight_target_pct": 1.5, "thesis": "확인된 축 증액",
             "confidence": 0.7, "size_why": "축 확인 — 증액 · 기대 +20% · 손절 -8%", "expected_upside_pct": 20.0,
             "facts": F(), "small_why": {"kind": "binary_event", "why": "테스트"},
             "increase_when": {"when": "다음 실적 확인", "to_pct": 3.0}}
    run_case("(85)-c 이 run의 BUY + increase_when.to_pct ≥ 선 → 통과 · increase",
             signal=base_signal(proposals=[buy_d]), snapshot=snap, limits_patch={**ROOMY},
             extra_theses=th, extra_map=amap, expect_approved=_exp(False, increase=True))
    snap_dn = json.loads(json.dumps(snap))
    for q in snap_dn["balance"]["positions"]:
        if q["ticker"] == D:
            q["price"] = 8500
            q["eval_amt"] = 85_000
    run_case("(85)-d 현재가 < 평균가 — 확인된 축이 아니다(행 없음)",
             signal=_with_small_why(base_signal()), snapshot=snap_dn, limits_patch={**ROOMY},
             extra_theses=th, extra_map=amap, expect_approved=_exp(False, no_row=True))
    lim_off = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))["position_sizing"]
    run_case("(85)-e ratio 0 → 꺼짐",
             signal=_with_small_why(base_signal()), snapshot=snap,
             limits_patch={**ROOMY, "position_sizing": {**lim_off, "confirmed_axis_min_vs_avg_ratio": 0}},
             extra_theses=th, extra_map=amap, expect_approved=_exp(False, off=True))


_safe(test_confirmed_axis_undersized_261017)


def test_factor_gate_261017():
    """(86) 요인 판정(FB-261017-7) — 1단 요인 상태가 꺾임이면 그 요인 종목 BUY는 시그널 factor_judgments[]의 그 종목 결정이
    '저점매수'+사유일 때만 통과 · 판정된 요인의 보유 멤버마다 결정 · 정리면 그 run의 SELL · 상태가 없으면 missing(경고만)."""
    rid = "260101_kr"                         # seed_facts의 판정 run(근거 사실 판정이 이 run에 있다)
    amap = {"axes": [{"id": "ax_f1", "direction": "+", "status": "open", "driver": "F1",
                      "names": [{"ticker": "000660", "sign": "+"}, {"ticker": "005930", "sign": "+"}]},
                     {"id": "ax_f2", "direction": "+", "status": "open", "driver": "F2",
                      "names": [{"ticker": "035420", "sign": "+"}]}]}
    snap = json.loads(json.dumps(BASE_SNAPSHOT))
    snap["prices"]["035420"] = {"price": 190000, "change_pct": 0.0}

    def seed_state(fired=True):
        def go():
            db.factor_state_put(rid, [
                {"driver": "F1", "state": {"fired": fired, "fired_dd": fired, "drawdown_pct": -9.1, "band3_pct": 6.0,
                                           "state": "fired" if fired else "calm", "n_members": 1, "n_with_bars": 1,
                                           "sells_n": 0, "sells_tickers": [], "fired_sell": False,
                                           "members": [{"ticker": "005930", "market": "KR", "held": True, "sign": 1, "has_bars": True}]}},
                {"driver": "F2", "state": {"fired": False, "state": "calm", "n_members": 0, "n_with_bars": 0, "members": []}}])
            got = {r["driver"]: r["fired"] for r in db.factor_state(rid)}
            return [] if got == {"F1": fired, "F2": False} else [f"심기 실패 — 요인 상태 {got}"]
        return go

    def sig(proposals=None, fj=None):
        sg = _with_small_why(base_signal(run_id=rid, **({"proposals": proposals} if proposals is not None else {})))
        if fj is not None:
            sg["factor_judgments"] = fj
        return sg

    def J(*decs, state="broken"):
        return [{"driver": "F1", "fired_by": ["drawdown"], "cause": "고객사 투자 하향 발표 [S3]", "state": state,
                 "decisions": [{"ticker": t, "decision": d, "why": w, "held": t == "005930"} for t, d, w in decs]}]

    def exp(short, **kw):
        def chk(ap, problems):
            f = ap.get("factor") or {}
            if bool(ap.get("factor_short")) is not short:
                problems.append(f"factor_short={ap.get('factor_short')} (기대 {short}) · {f.get('why')}")
            for k in ("unjudged", "undecided", "sell_missing"):
                if k in kw and f.get(k) != kw[k]:
                    problems.append(f"{k}={f.get(k)} (기대 {kw[k]})")
            if kw.get("state") and f.get("state") != kw["state"]:
                problems.append(f"state={f.get('state')} (기대 {kw['state']})")
            blob = json.dumps(ap.get("rejected") or [], ensure_ascii=False)
            if kw.get("no_factor_reject") and "요인 판정" in blob:
                problems.append(f"요인 거부가 나오면 안 된다: {blob[:200]}")
            if kw.get("buy_ok") and not any(o.get("ticker") == kw["buy_ok"] and o.get("action") == "BUY" for o in ap.get("orders") or []):
                problems.append(f"{kw['buy_ok']} BUY가 승인되지 않았다: {blob[:200]}")
        return chk
    common = dict(snapshot=snap, limits_patch={**ROOMY}, extra_map=amap)
    run_case("(86)-a 꺾인 요인 F1 · 판정 없음 → BUY 거부 '요인 판정 없음' · unjudged · short",
             signal=sig(), pre_seed=seed_state(), expect_reject_contains="요인 판정 없음",
             expect_approved=exp(True, unjudged=["F1"]), **common)
    run_case("(86)-b 판정 '유지' → BUY 거부",
             signal=sig(fj=J(("000660", "유지", "원인 미확인 — 지켜본다"), ("005930", "유지", "동인 유지"))), pre_seed=seed_state(),
             expect_reject_contains="요인 판정 유지", expect_approved=exp(False, undecided=[]), **common)
    run_case("(86)-c 판정 '저점매수'+사유 → BUY 통과 · 보유 멤버 결정 있음 → short 아님",
             signal=sig(fj=J(("000660", "저점매수", "원인이 일시 수급 — 동인 유지 · 바스켓 −9%"), ("005930", "유지", "동인 유지"))),
             pre_seed=seed_state(), expect_approved=exp(False, buy_ok="000660", no_factor_reject=True), **common)
    sell = dict(ticker="005930", name="삼성전자", action="SELL", qty=10, thesis="요인 F1 정리 판정", confidence=0.9, facts=[])
    run_case("(86)-d 보유 005930 '정리'인데 SELL 없음 → sell_missing · short",
             signal=sig(fj=J(("000660", "저점매수", "일시 수급"), ("005930", "정리", "동인 꺾임"))), pre_seed=seed_state(),
             expect_approved=exp(True, sell_missing=["F1:005930"]), **common)
    run_case("(86)-d2 '정리' + 이 run SELL → short 아님",
             signal=sig(proposals=[base_signal()["proposals"][0], sell],
                        fj=J(("000660", "저점매수", "일시 수급"), ("005930", "정리", "동인 꺾임"))),
             pre_seed=seed_state(), expect_approved=exp(False, sell_missing=[]), **common)
    buy_f2 = dict(base_signal()["proposals"][0], ticker="035420", name="NAVER")
    run_case("(86)-e 꺾이지 않은 요인 F2의 BUY는 요인 판정과 무관(판정 없는 F1은 보유 때문에 short)",
             signal=sig(proposals=[buy_f2]), pre_seed=seed_state(),
             expect_approved=exp(True, unjudged=["F1"], no_factor_reject=True), **common)
    run_case("(86)-f 판정은 있는데 보유 멤버 005930 결정 빠짐 → undecided · short",
             signal=sig(fj=J(("000660", "저점매수", "일시 수급"))), pre_seed=seed_state(),
             expect_approved=exp(True, undecided=["F1:005930"]), **common)
    run_case("(86)-g 요인 상태 없는 run → missing · 거부·short 없음",
             signal=sig(), expect_approved=exp(False, state="missing", no_factor_reject=True), **common)


_safe(test_factor_gate_261017)


def test_screen_memory_261019():
    """(87) 회차 기억 — 같은 run 안에서 유효성 사유로 거부된 제안이 고쳐져 다시 오면 그 제안만 '거부 회피 재실행'으로 거부하고
    나머지는 그대로 승인한다(2026-10-19 · 하나금융 근거 사실 교체로 깨끗한 2건까지 미전송). 같은 지문은 회피가 아니다 ·
    채우기 루프(요인 판정 없음)·데이터(현재가 없음)는 트리거가 아니다 · 한도는 첫 회차 해시로 고정 · run id를 모르면 기억 없음."""
    rid = "260101_kr"                         # seed_facts의 판정 run — 근거 사실 판정이 이 run에 있다
    amap = {"axes": [{"id": "ax_f1", "direction": "+", "status": "open", "driver": "F1",
                      "names": [{"ticker": "000660", "sign": "+"}]}]}

    def prop(**kw):
        return _with_small_why(base_signal(**kw))["proposals"][0]

    def row(pp, outcome, why="", cls="validity", ctx=None, sha=None, ticker=None):
        def go():
            return {"run_id": rid, "pass_no": 1, "mode": "main", "ticker": ticker or pp["ticker"], "action": pp["action"],
                    "fingerprint": rg.screen_fp(rg.screen_canon(pp)), "canon": rg.screen_canon(pp), "ctx": ctx or {},
                    "outcome": outcome, "why_class": cls if outcome == "rejected" else None, "why": why,
                    "limits_sha": sha or rg.sha256_of(rg.LIMITS_PATH)}
        return go

    def seed_rows(*makers, extra=None):
        def go():
            if extra:
                extra()
            db.screen_put([m() for m in makers])
            return [] if len(db.screen_rows(rid)) == len(makers) else ["심기 실패 — 회차 기록"]
        return go

    def exp(taint=False, order=None, pass_no=2, no_block=False, extra=None):
        def chk(ap, problems):
            scr = ap.get("screen")
            if no_block:
                if scr is not None:
                    problems.append(f"run id를 모르는데 screen 블록이 있다: {scr}")
                return
            if not scr or scr.get("pass") != pass_no:
                problems.append(f"screen 블록·회차: {scr}")
                return
            rj = [r for r in ap.get("rejected") or [] if r.get("ticker") == "000660"]
            is_t = any(r.get("why_class") == "taint" and str(r.get("why")).startswith("거부 회피 재실행") for r in rj)
            if is_t != taint:
                problems.append(f"거부 회피 판정 {is_t} (기대 {taint}) · {rj}")
            if taint and [t.get("ticker") for t in scr.get("tainted") or []] != ["000660"]:
                problems.append(f"screen.tainted: {scr.get('tainted')}")
            if taint and "000660" in ((ap.get("deficit") or {}).get("tickers") or []):
                problems.append("회피 제안을 미달 건수에 셌다")
            if order is not None and any(o.get("ticker") == "000660" and o.get("action") == "BUY" for o in ap.get("orders") or []) != order:
                problems.append(f"000660 BUY 승인 {not order} (기대 {order}) · 거부 {rj}")
            if not taint and scr.get("rows", 0) < 1:
                problems.append("이 회차 행을 안 남겼다")
            if extra:
                extra(ap, problems)
        return chk
    common = dict(limits_patch={**ROOMY}, snapshot=BASE_SNAPSHOT)
    bad = prop(run_id=rid, proposals=[dict(base_signal()["proposals"][0], facts=F(FACT_MISMATCH))])
    good = prop(run_id=rid)
    run_case("(87)-a 앞 회차 MISMATCH 거부 + 같은 제안 그대로 → 회피 아님(MISMATCH로 다시 거부) · 회차 2",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(bad)])),
             pre_seed=seed_rows(row(bad, "rejected", "근거에 MISMATCH 포함 (fact-check 불일치) — F-000002")),
             expect_reject_contains="MISMATCH", expect_approved=exp(taint=False, order=False), **common)
    run_case("(87)-b 앞 회차 MISMATCH 거부 + 근거 사실 id 교체 → 그 제안만 '거부 회피 재실행' · 미달 건수 제외",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good)])),
             pre_seed=seed_rows(row(bad, "rejected", "근거에 MISMATCH 포함 (fact-check 불일치) — F-000002")),
             expect_approved=exp(taint=True, order=False), **common)
    run_case("(87)-c 앞 회차 '요인 판정 없음'(채우기 루프) + 제안 바뀜 → 회피 아님 · 통과",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good)])),
             pre_seed=seed_rows(row(bad, "rejected", "요인 판정 없음 — 요인 `F1`가 꺾였다")),
             expect_approved=exp(taint=False, order=True), **common)

    def fired():
        db.factor_state_put(rid, [{"driver": "F1", "state": {"fired": True, "fired_dd": True, "state": "fired", "n_members": 0,
                                                            "n_with_bars": 0, "members": []}}])
    fj = [{"driver": "F1", "cause": "고객사 하향 [S3]", "state": "broken",
           "decisions": [{"ticker": "000660", "decision": "저점매수", "why": "일시 수급"}]}]
    sig_d = _with_small_why(base_signal(run_id=rid, proposals=[dict(good)]))
    sig_d["factor_judgments"] = fj
    run_case("(87)-d 앞 회차 '요인 판정 유지' + 결정을 저점매수로 → 회피(요인 판정 결정이 바뀜)",
             signal=sig_d, extra_map=amap,
             pre_seed=seed_rows(row(good, "rejected", "요인 판정 유지 — 요인 `F1`가 꺾인 동안 000660 매수는 …",
                                    ctx={"target": None, "factor": [["F1", "유지"]]}), extra=fired),
             expect_approved=exp(taint=True, order=False), **common)
    th = {"theses": [{"id": "kr-000660-t", "market": "KR", "ticker": "000660", "status": "armed", "axis_id": "ax_f1",
                      "created": "2026-01-01", "exit_triggers": [{"id": "x1", "check": "price >= 250000", "when": "목표"}]}]}
    good_t = prop(run_id=rid, proposals=[dict(base_signal()["proposals"][0], thesis_id="kr-000660-t")])
    run_case("(87)-e 앞 회차 '목표 여유 없음'(목표 190,000) + 논지 목표 다리를 250,000으로 올림 → 회피(1차 목표가 바뀜)",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good_t)])), extra_theses=th,
             pre_seed=seed_rows(row(good_t, "rejected", "목표 여유 없음 — 1차 목표 190,000이 현재가 +5.6%",
                                    ctx={"target": 190000.0, "factor": []})),
             expect_approved=exp(taint=True, order=False), **common)
    run_case("(87)-f 앞 회차 '유니버스 밖' + 같은 run에 유니버스에 들어옴 → 회피",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good)])),
             pre_seed=seed_rows(row(good, "rejected", "유니버스 밖 종목 (watchlist 미등재·미보유). watchlist_candidates로 제안할 것")),
             expect_approved=exp(taint=True, order=False), **common)
    run_case("(87)-g 앞 회차 '현재가를 알 수 없다'(데이터) + 제안 바뀜 → 회피 아님 · 통과",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good)])),
             pre_seed=seed_rows(row(bad, "rejected", "현재가를 알 수 없다 — 스냅샷에 시세 없음")),
             expect_approved=exp(taint=False, order=True), **common)
    run_case("(87)-h 앞 회차 승인 + 제안 편집(크기 사유) → 회피 아님 · 통과",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good, size_why="다시 쓴 크기 사유 — 기대 +15%")])),
             pre_seed=seed_rows(row(good, "accepted")),
             expect_approved=exp(taint=False, order=True), **common)
    run_case("(87)-i 첫 회차 한도 해시와 지금 한도가 다르면 전체 거부(한도 변경은 다음 run부터)",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good)])),
             pre_seed=seed_rows(row(good, "accepted", sha="deadbeef" * 8)),
             expect_exit=2, expect_reject_contains="한도가 run 중간에 바뀌었다", **common)
    run_case("(87)-k 앞 회차 '논지 근거 … 없다' + 논지의 근거 번호를 제안에 넣음 → 회피 아님(원장에 미리 적힌 근거) · 통과",
             signal=_with_small_why(base_signal(run_id=rid, proposals=[dict(good)])),
             pre_seed=seed_rows(row(bad, "rejected", "논지 근거 F-000001가 제안의 받치는 사실에 없다 — 주문은 논지 x의 근거 위에 선다")),
             expect_approved=exp(taint=False, order=True), **common)
    run_case("(87)-j run id를 모르면(test-kr) 기억 없음 — screen 블록 없음 · 거부 회피 없음",
             signal=_with_small_why(base_signal(proposals=[dict(good)])),
             expect_approved=exp(no_block=True), **common)


_safe(test_screen_memory_261019)


def test_thesis_review_261019():
    """(90)(91) 근거가 틀린 논지(2026-10-19) — 논지 근거 사실이 MISMATCH(disputed)면 그 논지의 매수를 받지 않고(주문을 고쳐 내는 것이
    출구가 아니다), 오늘 세운 논지면 부족 검사가 '처리하라'(basis_review)를 요구한다 · 근거를 다시 세우면(set-evidence) 막힘이 풀리고
    그 논지의 첫 매수 요구는 다음 run 몫(rebased_today)."""
    import io
    import theses as th_
    tid = "kr-000660-new-261019"
    th = {"theses": [{"id": tid, "market": "KR", "ticker": "000660", "status": "armed", "created": _SESSION_KR, "axis": "테스트 축",
                      "thesis": "테스트 논지", "expires": "2099-12-31"}]}

    def with_evidence(rebase=False):
        def go():
            db.thesis_update(tid, lambda t: {**t, "evidence": [{"fact_id": FACT_MISMATCH, "role": "support", "claim": "틀린 근거"}]})
            if rebase:
                with _ctxlib.redirect_stdout(io.StringIO()):
                    th_.cmd_set_evidence(tid, [FACT_OK], [FACT_MISMATCH], "근거 교체 — F-000002 MISMATCH")
            return [] if bool(db.theses_under_review()) != rebase else [f"심기 실패 — 재검토 목록 {db.theses_under_review()}"]
        return go

    def exp(review, rebased=None, buy=None):
        def chk(ap, problems):
            d = ap.get("deficit") or {}
            rj = [r for r in ap.get("rejected") or [] if r.get("ticker") == "000660"]
            hit = any("논지 재검토 중" in str(r.get("why")) for r in rj)
            if hit != bool(review):
                problems.append(f"논지 재검토 거부 {hit} (기대 {bool(review)}) · {rj}")
            br = d.get("basis_review") or []
            if len(br) != len(review or []) or any(not str(b).startswith(w) for b, w in zip(br, review or [])):
                problems.append(f"basis_review={br} (기대 머리 {review})")
            if rebased is not None and (d.get("rebased_today") or []) != rebased:
                problems.append(f"rebased_today={d.get('rebased_today')} (기대 {rebased})")
            if "000660" in (d.get("e0_missing") or []):
                problems.append(f"근거가 틀린(또는 오늘 다시 세운) 논지를 e0 제안 없음으로 셌다: {d.get('e0_missing')}")
            if review and not ap.get("deficit_short"):
                problems.append("처리 안 된 재검토 논지가 있는데 deficit_short가 아니다")
            if buy is not None and any(o.get("ticker") == "000660" and o.get("action") == "BUY" for o in ap.get("orders") or []) != buy:
                problems.append(f"000660 BUY 승인 {not buy} (기대 {buy})")
        return chk
    sig_buy = _with_small_why(base_signal(proposals=[dict(base_signal()["proposals"][0], thesis_id=tid)]))
    run_case("(90) 오늘 논지의 근거가 MISMATCH → 그 논지 매수 거부 '논지 재검토 중' · basis_review · e0 제안 없음으로 안 셈",
             signal=sig_buy, extra_theses=th, pre_seed=with_evidence(), limits_patch={**ROOMY},
             expect_approved=exp(["000660(" + tid + ")"], buy=False))
    run_case("(91)-a 근거를 다시 세움(set-evidence) → 막힘 풀림 · 매수 승인",
             signal=_with_small_why(base_signal(proposals=[dict(base_signal()["proposals"][0], thesis_id=tid)])),
             extra_theses=th, pre_seed=with_evidence(rebase=True), limits_patch={**ROOMY}, expect_approved=exp(None, buy=True))
    run_case("(91)-b 근거를 다시 세우고 이 run엔 매수 제안 없음 → rebased_today · e0 요구 없음",
             signal=_with_small_why(base_signal(proposals=[])),
             extra_theses=th, pre_seed=with_evidence(rebase=True), limits_patch={**ROOMY},
             expect_approved=exp(None, rebased=["000660"]))
    # (92) 논지 근거는 멀쩡한데 오늘 논지의 e0 제안이 유효성 사유(제안의 MISMATCH 사실)로 거부 → 주문을 다시 내라가 아니라 논지 처리를 요구
    sig_mm = _with_small_why(base_signal(proposals=[dict(base_signal()["proposals"][0], thesis_id=tid, facts=F(FACT_MISMATCH))]))
    run_case("(92)-a 오늘 논지의 e0가 제안의 MISMATCH 사실로 거부(논지 근거는 번호 없음)",
             signal=sig_mm, extra_theses=th, limits_patch={**ROOMY}, expect_reject_contains="MISMATCH")

    def chk_92a(ap, problems):
        d = ap.get("deficit") or {}
        br = d.get("basis_review") or []
        if not (len(br) == 1 and "e0 거부" in br[0]) or "000660" in (d.get("e0_missing") or []) or not ap.get("deficit_short"):
            problems.append(f"(92)-a basis_review={br} e0_missing={d.get('e0_missing')} short={ap.get('deficit_short')}")
    run_case("(92)-a' 같은 상황 → basis_review 'e0 거부' · deficit_short · e0 제안 없음으로 안 셈(주문이 아니라 논지 처리를 요구)",
             signal=_with_small_why(base_signal(proposals=[dict(base_signal()["proposals"][0], thesis_id=tid, facts=F(FACT_MISMATCH))])),
             extra_theses=th, limits_patch={**ROOMY}, expect_approved=chk_92a)

    def set_trigger_today():
        db.thesis_update(tid, lambda t: {**t, "exit_triggers": [{"id": "x1", "check": "price >= 250000", "when": "목표"}],
                                         "corrections": [{"ts": _SESSION_KR, "what": "x1 트리거 price >= 200000 → price >= 250000 (set-trigger)",
                                                          "why": "목표 재설정"}]})
        return []

    def chk_92b(ap, problems):
        d = ap.get("deficit") or {}
        if (d.get("rebased_today") or []) != ["000660"] or d.get("basis_review"):
            problems.append(f"(92)-b rebased_today={d.get('rebased_today')} basis_review={d.get('basis_review')}")
    run_case("(92)-b 오늘 원장에서 고침(set-trigger) → rebased_today · 처리 요구 없음(매수는 다음 run)",
             signal=_with_small_why(base_signal(proposals=[dict(base_signal()["proposals"][0], thesis_id=tid, facts=F(FACT_MISMATCH))])),
             extra_theses=th, pre_seed=set_trigger_today, limits_patch={**ROOMY}, expect_approved=chk_92b)


_safe(test_thesis_review_261019)


def test_thesis_evidence_basis_261019():
    """(89) 주문은 논지의 근거 위에 선다(2026-10-19) — 논지의 받치는 근거 번호가 제안의 받치는 사실에 없으면 거부(제안 쪽 번호만 바꿔
    넘기는 문을 닫는다) · 들어 있으면 통과 · 번호 없는 옛 논지는 검사를 건너뛰고 첫 승인 때 제안 근거를 논지 근거로 채택한다."""
    import io
    tid = "kr-000660-basis-261019"

    def th(evidence):
        return {"theses": [{"id": tid, "market": "KR", "ticker": "000660", "status": "armed", "created": "2026-10-01", "axis": "t",
                            "thesis": "t", "expires": "2099-12-31", **({"evidence": evidence} if evidence else {})}]}

    def add_ev(evidence):
        def go():
            db.thesis_update(tid, lambda t: {**t, "evidence": evidence})
            return []
        return go

    def sig(facts):
        return _with_small_why(base_signal(proposals=[dict(base_signal()["proposals"][0], thesis_id=tid, facts=facts)]))

    def exp(rejected=False, adopted=None):
        def chk(ap, problems):
            rj = [r for r in ap.get("rejected") or [] if r.get("ticker") == "000660"]
            hit = any(str(r.get("why")).startswith("논지 근거") for r in rj)
            if hit != rejected:
                problems.append(f"논지 근거 거부 {hit} (기대 {rejected}) · {rj}")
            if adopted is not None and ap.get("evidence_adopted") != adopted:
                problems.append(f"evidence_adopted={ap.get('evidence_adopted')} (기대 {adopted})")
        return chk
    run_case("(89)-a 논지 근거 F-000001이 제안의 받치는 사실에 없다(제안은 다른 사실로 바꿈) → 거부",
             signal=sig([{"id": FACT_T3, "role": "background", "support": "x"}] + F(FACT_OK, "background")),
             extra_theses=th(None), pre_seed=add_ev([{"fact_id": FACT_OK, "role": "support"}]), limits_patch={**ROOMY},
             expect_approved=exp(rejected=True))
    run_case("(89)-b 논지 근거가 제안의 받치는 사실에 들어 있다 → 통과 · 채택 없음(이미 번호 있음)",
             signal=sig(F(FACT_OK)), extra_theses=th(None), pre_seed=add_ev([{"fact_id": FACT_OK, "role": "support"}]),
             limits_patch={**ROOMY}, expect_approved=exp(rejected=False, adopted=[]))
    run_case("(89)-e 제안에 논지에 없는 받치는 사실이 더 있다 → 거부(새 받치는 사실은 set-evidence로 논지에 먼저)",
             signal=sig(F(FACT_OK) + F(FACT_T3)), extra_theses=th(None), pre_seed=add_ev([{"fact_id": FACT_OK, "role": "support"}]),
             limits_patch={**ROOMY}, expect_approved=exp(rejected=True))
    run_case("(89)-c 번호 없는 옛 논지 → 검사 건너뜀 · 첫 승인 때 제안 근거를 논지 근거로 채택",
             signal=sig(F(FACT_OK)), extra_theses=th([{"claim": "옛 근거 문장", "tier": "T2"}]), limits_patch={**ROOMY},
             expect_approved=exp(rejected=False, adopted=[tid]))
    # 채택이 원장에 실제로 쓰이는가(기록·교정 이력)
    p = []
    with testkit.db_case():
        testkit.seed(theses=th([{"claim": "옛 근거 문장"}])["theses"])
        for fid in ("F-000123", "F-000124", "F-000999"):           # 실제 경로에서는 '근거 사실 없음' 검사가 존재를 보장한다
            db.connect().execute("INSERT INTO ev_fact (id, kind, legacy_text) VALUES (?, 'legacy', '테스트')", (fid,))
        with _ctxlib.redirect_stdout(io.StringIO()):
            got = rg._adopt_thesis_evidence([{"source": "llm", "action": "BUY", "ticker": "000660", "thesis_id": tid,
                                              "facts": [{"id": "F-000123", "role": "support"}, {"id": "F-000124", "role": "against"}]}],
                                            "approved_261019_kr.json", datetime.now(KST))
        t = db.thesis_get(tid)
        fids = [e.get("fact_id") for e in t.get("evidence") or [] if e.get("fact_id")]
        if got != [tid] or fids != ["F-000123"] or not any("승인 시 자동" in str(c.get("what")) for c in t.get("corrections") or []):
            p.append(f"채택 기록: {got} {fids} {t.get('corrections')}")
        with _ctxlib.redirect_stdout(io.StringIO()):
            again = rg._adopt_thesis_evidence([{"source": "llm", "action": "BUY", "ticker": "000660", "thesis_id": tid,
                                                "facts": [{"id": "F-000999", "role": "support"}]}], "approved_x.json", datetime.now(KST))
        if again or [e.get("fact_id") for e in db.thesis_get(tid).get("evidence") or [] if e.get("fact_id")] != ["F-000123"]:
            p.append("번호가 이미 있는 논지에 또 채택했다")
    results.append((FAIL if p else PASS, "(89)-d 채택이 원장에 쓰인다(받치는 사실만 · corrections) · 두 번째는 안 한다", "; ".join(p)))
    print(f"[{FAIL if p else PASS}] (89)-d 채택이 원장에 쓰인다(받치는 사실만 · corrections) · 두 번째는 안 한다" + (f" — {'; '.join(p)}" if p else ""))


_safe(test_thesis_evidence_basis_261019)


def test_target_basis_261019():
    """(90) 목표 기준(2026-10-19 · Codex J01 · 하나금융 픽스처 — 수준 파일 as_of 2026-10-16 · 시세 122,500 · 실계좌 국내 바닥 10.3%):
    a) 1차 저항 +9.9%가 바닥 미달이라 2차 저항으로 옮기며 '바닥 미달'을 사유로 → 거부(선택 사유 없음 · 유효성) b) 같은 수준을 수준 자체의 근거
    (스윙 고점 3회·구간·S번호)로 → 통과(목표까지 거리 +15.4%) c) 임의성 낮은 겹침 133,983 → '목표 여유 없음'(9.4% < 10.3%) d) 기준 없음 → 거부
    (적용일 전엔 경고) e) 거부 뒤 수준을 바꾸거나 사유만 고치면 '거부 회피 재실행' f) 노리는 폭이 닻 수준까지 거리를 넘으면 불일치
    g) 기저율 줄(조건·n·시계) · 없으면 미측정 · 이 거부들은 유효성(최소 건수에 안 셈)."""
    import io
    p = []
    lim = {"account_mode": "real", "position_sizing": {"min_target_gap_pct": 10.0, "min_target_gap_pct_by_market": {"KR": 10.3}}}
    with testkit.db_case(), _ctxlib.redirect_stdout(io.StringIO()):
        for kind, val, meth in (("resistance_1", 134650.0, "1차 저항 — 스윙 고점 2회 (20260807, 20260814) · 구간 134,500~134,800"),
                                ("resistance_2", 141366.67, "2차 저항 — 스윙 고점 3회 (20260914, 20260918) · 구간 140,200~142,000"),
                                ("confluence_2", 133983.33, "기준 3개 겹침: retrace_100, retrace_50, resistance_1"),
                                ("confluence_1", 118359.67, "기준 3개 겹침: low_20, low_60, band_1d"),
                                ("ma_20", 130245.0, "20일 이동평균")):
            db.level_put("KR", "086790", kind, val, as_of="2026-10-16", method=meth)
        base = {"ticker": "086790", "action": "BUY", "expected_upside_pct": 15.0, "horizon_days": 35}
        tb = lambda kind, price, why: {**base, "target_basis": {"level_kind": kind, "level_price": price, "level_source": "data/run_evidence/levels.txt",
                                                                "chosen_reason": why, "chosen_at": "2026-10-19T10:40:00+09:00"}}
        bad_why = "1차 저항 +9.9%는 바닥 미달이라 2차로 잡았다 — 목표 여유를 넘기려고"
        good_why = "2차 저항 구간 140,200~142,000은 스윙 고점 3회(0914·0918)로 1차(2회)보다 확인 횟수가 많다 · 3Q 10-23 컨센 부합이면 9월 고점 재시험 [S31]"
        why_a, _, _ = rg.target_basis_check(tb("resistance_2", 141366.67, bad_why), 122500, "KR", lim, True)
        if not str(why_a).startswith("목표 기준 선택 사유 없음") or "confluence_2" not in str(why_a) or rg._why_class(why_a) != "validity":
            p.append(f"a) 바닥 미달 사유로 옮긴 수준: {why_a}")
        why_b, info_b, _ = rg.target_basis_check(tb("resistance_2", 141366.67, good_why), 122500, "KR", lim, True)
        if why_b or info_b.get("upside_to_target_pct") != 15.4:
            p.append(f"b) 근거 있는 2차 저항: {why_b} {info_b}")
        why_c, _, _ = rg.target_basis_check(tb("confluence_2", 133983.33, ""), 122500, "KR", lim, True)
        if not str(why_c).startswith("목표 여유 없음") or "+9.4%" not in str(why_c):
            p.append(f"c) 임의성 낮은 수준 9.4%: {why_c}")
        why_d, _, _ = rg.target_basis_check(dict(base), 122500, "KR", lim, True)
        why_d2, _, warn_d2 = rg.target_basis_check(dict(base), 122500, "KR", lim, False)
        if not str(why_d).startswith("목표 기준 없음") or why_d2 is not None or "경고만" not in str(warn_d2):
            p.append(f"d) 기준 없음: {why_d} · 적용일 전 {why_d2} {warn_d2}")
        canon_a = rg.screen_canon(tb("resistance_2", 141366.67, bad_why))
        prior = [{"ticker": "086790", "action": "BUY", "outcome": "rejected", "why_class": "validity", "why": why_a, "pass_no": 1,
                  "canon": canon_a, "ctx": {"level_kind": "resistance_2", "level_price": 141366.67, "target": None}}]
        t1 = rg.taint_of(prior, "086790", "BUY", rg.screen_canon(tb("resistance_2", 141366.67, good_why)),
                         {"level_kind": "resistance_2", "level_price": 141366.67, "target": None})
        prior_c = [{**prior[0], "why": why_c, "ctx": {"level_kind": "confluence_2", "level_price": 133983.33, "target": None}}]
        t2 = rg.taint_of(prior_c, "086790", "BUY", rg.screen_canon(tb("resistance_2", 141366.67, good_why)),
                         {"level_kind": "resistance_2", "level_price": 141366.67, "target": None})
        if not str(t1).startswith("거부 회피 재실행") or "목표 기준이 바뀌었다" not in str(t2):
            p.append(f"e) 거부 회피: 사유만 고침 {t1} · 수준 바꿈 {t2}")
        why_f, _, _ = rg.target_basis_check({**tb("resistance_1", 134650.0, good_why), "expected_upside_pct": 15.4}, 120000, "KR", lim, True)
        if not str(why_f).startswith("기대 상승폭 불일치"):
            p.append(f"f) 노리는 폭 > 닻 거리: {why_f}")
        none_br = rg.base_rate_info(base, "KR", "086790")
        db.base_rate_put("KR", "086790", "all", 20, 312, 0.56, 2.9, 2.7, as_of="2026-10-16")
        db.base_rate_put("KR", "086790", "below_ma20", 20, 115, 0.61, 7.5, 7.04, as_of="2026-10-16", baseline={"n": 312, "median_ret": 2.7})
        br_ = rg.base_rate_info(base, "KR", "086790")
        line = rg.target_line(info_b, br_, "KR")
        if none_br is not None or not br_ or br_.get("horizon_days") != 20 or "조건부 중앙값" not in line or "+20일" not in line \
                or "미측정" not in rg.target_line(info_b, None, "KR"):
            p.append(f"g) 기저율 줄: {none_br} · {br_} · {line}")
        if rg._why_class("상승 여지 바닥 미달 — expected_upside_pct 8.0% < 10%") != "validity":
            p.append("상승 여지 바닥 미달이 유효성 분류가 아니다(최소 건수에 세면 문턱 맞추기를 부른다)")
    results.append((FAIL if p else PASS, "(90) 목표 기준 — 바닥 사유로 수준 옮김 거부 · 수준 근거면 통과 · 임의성 낮은 수준 9.4% 거부 · 기준 없음 · 회피 · 폭 불일치 · 기저율 줄", "; ".join(p)))
    print(f"[{FAIL if p else PASS}] (90) 목표 기준 — 바닥 사유로 수준 옮김 거부 · 수준 근거면 통과 · 임의성 낮은 수준 9.4% 거부 · 기준 없음 · 회피 · 폭 불일치 · 기저율 줄"
          + (f" — {'; '.join(p)}" if p else ""))


_safe(test_target_basis_261019)




# ==================================== 요약 ====================================

print()
n_fail = sum(1 for s, _, _ in results if s == FAIL)
_STATE["done"] = True
print(f"{'=' * 60}")
print(f"총 {len(results)}건 · 통과 {len(results) - n_fail} · 실패 {n_fail}")
print(f"TEST-SUMMARY file=test_risk_guard.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
if n_fail:
    for s, name, why in results:
        if s == FAIL:
            print(f"  FAIL {name}: {why}")
sys.exit(1 if n_fail else 0)
