#!/usr/bin/env python3
"""
이전(migrate.py) 테스트 — 작은 가짜 root(원장마다 몇 행 · 이상한 타입 포함)를 만들어 이전하고,
내보내기가 바이트 단위로 같은가 · 행 수 · FK · 되돌리기 · 실패 시 원본 보존 · --dry가 아무것도 안 쓰는가를 본다.

왜(2026-10-17): 원장 파일 → trading.db 이전은 한 번뿐이고, 렌더가 1바이트라도 다르면 봉인 게이트·포트폴리오 동기화가
다른 파일을 읽는다. 실제 원장으로 돌리기 전에 같은 경로를 가짜 데이터로 끝까지 돈다. 실제 journal/·config/는 건드리지 않는다
(임시 폴더 · AITB_DB 임시 경로 · 게이트 엔진 원장도 가짜).

    python3 test_migrate.py          # run_tests.py가 부른다(TMPDIR를 주면 그 아래에 만든다)
"""
import atexit
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
_TMP = Path(tempfile.mkdtemp(prefix="migrate_test_"))
os.environ["AITB_DB"] = str(_TMP / "boot.db")       # 실제 저장소를 열지 않는다(db._guard)
os.environ.pop("AITB_EXPORT_REAL", None)

import db        # noqa: E402
import migrate   # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []
_STATE = {"current": "(시작 전)", "done": False}


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_migrate.py ran={n} failed={nf} last={_STATE['current']}")
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
        print(f"[{FAIL}] {fn.__name__} — 중단({type(e).__name__}: {str(e)[:300]})")


def report(label, problems):
    status = FAIL if problems else PASS
    results.append((status, label, "; ".join(problems)))
    print(f"[{status}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


# ───────────────────────────── 가짜 root ─────────────────────────────

def wj(p: Path, obj, indent=2, tail=""):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=indent) + tail, encoding="utf-8")


def wl(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def wt(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


THESES = {"schema_version": "1.0", "theses": [
    {"id": "kr-a-261016", "market": "KR", "ticker": "005930", "name": "삼성전자", "status": "open",
     "entry_price": 100, "expected_upside_pct": 12.0, "horizon_days": "30", "priced_at_entry": True, "parent": None,
     "entry_triggers": [{"id": "e0", "when": "지금", "check": "price <= 101.5", "size_pct": 50, "fired": False},
                        {"id": "e1", "check": "judge", "size_pct": 50.0, "fired": True, "fired_at": None, "extra_x": [1, 2]}],
     "exit_triggers": [{"id": "x1", "check": "from_peak_pct <= -8", "fired": 0}],
     "evidence": [{"claim": "주장", "source_url": "https://example.com/a", "tier": 1, "verdict": "MATCH"}],
     "history": [{"ts": "2026-10-16T09:40:00+09:00", "status": "open", "note": "진입"}], "note_261016": "축 메모"},
    {"id": "us-b-261015", "market": "US", "ticker": "AAPL", "entry_price": 100.5, "status": "closed", "custom": {"k": None}},
]}
SCENARIOS = {"scenarios": [
    {"id": "SC-261016-1", "market": "KR", "opened": "261016", "status": "open", "realized": None, "acted": False,
     "judgments": [{"when": "261016", "realized": True, "note": "실현", "auto": 1}], "sources": ["SRC-0001"], "price_cond": True},
    {"id": "SC-261015-2", "status": "closed", "aliases": [], "actions": [{"ticker": "005930", "what": "매수", "done": True}]},
]}
MARKET_MAP = {"schema_version": "1.0", "axes": [
    {"id": "kr_semis", "name": "반도체", "horizon_weeks": 4, "confidence": 0.6, "created_before_news": False,
     "indicators": [{"name": "DRAM", "current": 1.5}], "beneficiaries": [{"ticker": "005930", "order": 1, "priced": None}],
     "conflicts_with": ["us_rates"], "note_261016": "메모", "note_261015b": "메모2"}],
    "sectors": [{"code": "0013", "market": "KR", "name": "전기·전자", "carried_runs": 2, "names": [{"ticker": "005930"}],
                 "chains": ["a", "b"]}]}
CALENDAR = {"schema_version": "1.0", "events": [
    {"date": "2026-10-28", "event": "FOMC", "key": "fomc-1028", "tickers": ["BAC"], "kind_fixed": True},
    {"date": "2026-10-29", "event": "삼성전자 실적", "axes": ["kr_semis"], "note_261015": "확인"}]}
SOURCES = {"schema_version": "1.1", "sources": [
    {"fact": "삼성전자 3Q26 잠정 영업이익 107.40조", "as_of": "2026-10-14", "retrieved": "2026-10-16 09:40", "source": "DART",
     "url": "https://example.com/a", "quote": "영업이익 107.40조", "tier": 1, "load_bearing": True, "id": "SRC-0001", "runs": ["kr:261016"]},
    {"fact": "KOSPI 10-15 종가 5554.22", "as_of": "2026-10-15", "retrieved": "2026-10-16 09:41", "source": "뉴스",
     "url": "https://example.com/b", "tier": 2, "load_bearing": True, "id": "SRC-0002", "runs": ["kr:261016"]},
    {"fact": "[fact-check ②-A 출처 보강] 무엇", "as_of": "미확인", "retrieved": "2026-10-16 10:00",
     "source": "fact-check ②-A verdict table (출처 등록 대기)", "url": "https://example.com/c", "tier": 1, "load_bearing": False,
     "id": "SRC-0003", "runs": ["kr:261016"]},
    {"fact": "09:35 캡처: 삼성전자 252,000", "as_of": "2026-10-16", "retrieved": "2026-10-16 09:35", "source": "broker:KIS ingest.py",
     "url": "", "tier": "data", "load_bearing": True, "id": "SRC-0004", "runs": ["kr:261016"], "minor": None},
    {"fact": "두산퓨얼셀 HyAxiom 미국 데이터센터 연료전지 공급계약 5,014억원(2025 매출의 110.25%)", "as_of": "2026-09-02",
     "retrieved": "2026-09-03 10:00", "source": "DART 단일판매·공급계약",
     "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260902800111 · https://example.com/d", "tier": 1, "load_bearing": True,
     "id": "SRC-0005", "runs": ["kr:261015", "kr:261016"], "history": [{"at": "x", "what": "y"}]},
]}
WATCHLIST = {"_comment": "감시 목록", "schema_version": "1.0",
             "KR": [{"ticker": "005930", "name": "삼성전자", "added": "2026-09-09", "why": "축", "axis": "반도체", "aliases": ["삼성전자"]},
                    {"ticker": "336260", "name": "두산퓨얼셀", "added": "2026-09-09", "why": "축", "axis": "전력", "aliases": [],
                     "gates": ["g1"], "added_by": "kr:260915", "run_id": "260915_kr"}],
             "US": [{"ticker": "AAPL", "excd": "NASD", "name": "Apple", "added": "2026-09-09", "why": "축", "axis": "테크",
                     "aliases": ["애플", "Apple"]}],
             "benchmarks": {"KR": "069500"}, "_comment_axis": "축"}


def make_root(base: Path, theses_indent: int = 2) -> Path:
    r = base / "ai-trading-bot"
    J, C, D, S, A = r / "journal", r / "config", r / "data", r / "signals", r / "analysis"
    wj(J / "theses.json", THESES, theses_indent, "\n")
    wj(J / "scenarios.json", SCENARIOS, 1)
    wj(J / "market_map.json", MARKET_MAP, 1)
    wj(J / "calendar.json", CALENDAR, 1)
    wj(J / "sources.json", SOURCES, 2)
    wj(J / "universe_coverage.json", {"KR:005930": {"seen": "261016", "n": 1}, "US:AAPL": {"seen": None}}, 2)
    wj(J / "universe_pending.json", {"KR:000660": {"why": "후보", "since": "261015"}}, 2)
    wj(J / "current_run.json", {"market": "kr", "stamp": "261016", "session_date": "2026-10-16",
                                "started": "2026-10-16T09:30:00+09:00"}, 1, "\n")
    wj(J / "position_peaks.json", {"_schema": 2, "KR": {"005930": {"peak_price": 112.0, "peak_at": "2026-10-16T10:00:00+09:00"},
                                                        "336260": {"peak_price": 45950.0, "peak_at": "2026-10-15T10:00:00+09:00"}},
                                   "US": {"AAPL": {"peak_price": 346.25, "peak_at": "2026-10-17T00:29:52+09:00"}}}, 2, "\n")
    wj(J / "refill_261016_kr.json", {"stamp": "261016", "market": "kr", "status": "none", "events": [], "short": False}, 2)
    wl(J / "allocation.jsonl", [
        {"id": "AL-261015-kr", "stamp": "261015", "market": "kr", "based_on": None, "target_invested_pct": 98, "gap_pct": 1.5,
         "reserved": [{"thesis_id": "kr-a-261016", "amount": 1000}]},
        {"id": "AL-261016-kr", "stamp": "261016", "market": "kr", "target_invested_pct": 97.5, "regime": "강세", "deployable": True}])
    wl(J / "fetch_log.jsonl", [{"at": "2026-10-16T09:40:00+09:00", "url": "https://example.com/a", "kind": "html", "chars": 10,
                                "err": "", "raw_path": "data/_raw_fc/x.txt"}, {"at": "2026-10-16T09:41:00+09:00", "url": "u2", "chars": 0}])
    wl(J / "sessions.jsonl", [{"session_date": "2026-10-16", "stamp": "261016", "market": "kr", "started_kst": "2026-10-16T09:30:05+09:00"}])
    wl(J / "stops_log.jsonl", [{"ts": "2026-10-16T10:00:00+09:00", "market": "KR", "fired": []}])
    wl(J / "regret.jsonl", [{"stamp": "261016", "market": "kr", "ticker": "000660", "ret": 12.5}])
    wl(J / "feedback.jsonl", [{"id": "FB-1", "week": "261012", "status": "open", "n": 1.0}, {"id": "FB-2", "ok": None}])
    wl(J / "audit_reversals.jsonl", [{"id": "RV-1", "status": "pending", "ts": "2026-10-16T11:00:00+09:00", "market": "kr"}])
    wl(J / "equity_curve.jsonl", [
        {"date": "2026-10-15", "ts": "2026-10-15T15:40:00+09:00", "market": "KR", "currency": "KRW", "equity": 10000000.0, "cash": 500,
         "n_positions": 1, "positions": [{"ticker": "005930", "qty": 1, "price": 110.0, "pnl_pct": None}], "cash_shared": False},
        {"date": "2026-10-16", "market": "KR", "equity": 10010000, "account_mode": "paper", "n_positions": "1", "extra_k": [1]}])
    for f in ("incidents", "push_log", "test_runs", "run_log", "reviews", "universe_log", "bridge_log", "rename_log",
              "sector_history", "sector_watch"):
        wl(J / f"{f}.jsonl", [{"ts": "2026-10-16T09:00:00+09:00", "market": "kr", "f": f}])
    wj(J / "trades_261016_kr.json", {"trades": [{"ticker": "005930", "action": "BUY", "qty": 1, "price": 100, "ts": "2026-10-16T09:50:00+09:00",
                                                "status": "sent", "result": {"order_no": "1"},
                                                "fill": {"filled_qty": 1, "avg_price": 100.0, "verdict": "filled"}}]}, 2)
    wt(J / "worklog_261016_kr.md", "# 작업 기록 — 261016 · KR\n")
    wj(C / "watchlist.json", WATCHLIST, 2)
    wj(C / "main_names.json", {"as_of": "2026-10-03T12:21:32+09:00", "source": "KIS", "pool_size": {"KR": 2}, "top_n": 2,
                               "KR": [{"ticker": "005930", "name": "삼성전자", "mcap_100m_krw": 16135729.0},
                                      {"ticker": "000660", "name": "SK하이닉스", "mcap_100m_krw": 13455669.0}],
                               "US": [{"ticker": "NVDA", "name": "NVIDIA", "excd": "NASD", "mcap_usd": 5.6e12}], "errors": []}, 2)
    wj(C / "sector_bridge.json", {"_comment": "대응표", "schema_version": "1.0", "us_sectors": {"SMH": {"name": "반도체"}},
                                  "links": [{"us": "SMH", "kr": "0013", "kr_name": "전기·전자", "sign": "+", "confidence": "high", "note": "강함"}],
                                  "_note_retired": "내린 짝",
                                  "retired_links": [{"us": "XLC", "kr": "0020", "kr_name": "통신", "sign": "+", "confidence": "low",
                                                     "retired": "2026-09-28", "why": "3회 역행", "note": "성격이 다름"}],
                                  "kr_without_us": [{"kr": "0026", "kr_name": "일반서비스", "why": "대응 없음"}],
                                  "us_without_kr": [{"us": "XLE", "why": "국내 없음"}]}, 2)
    wj(C / "benchmark.json", {"KR": {"start_equity": 10000000.0, "benchmark_ticker": "069500"}, "US": {"start_equity": 100000.0}}, 2, "\n")
    wj(C / "limits.json", {"max": 1}, 2)
    wj(S / "signal_261016_kr.json", {"generated_at": "2026-10-16T09:45:00+09:00", "no_trade": False,
                                     "proposals": [{"ticker": "005930", "action": "BUY", "thesis_id": "kr-a-261016", "weight_target_pct": 5,
                                                    "increase_when": {"cond": "x2", "n": 1}}],
                                     "holds": [{"ticker": "336260", "thesis_id": "kr-c", "why": "유지"}]}, 2)
    wj(S / "approved_261016_kr.json", {"orders": [{"idx": 0, "ticker": "005930", "side": "BUY", "qty": 1, "price": 100}], "rejected": []}, 2)
    wj(D / "snapshot_261016_kr.json", {"generated_at": "2026-10-16T09:35:50+09:00", "market": "KR", "account_mode": "paper",
                                       "balance": {"currency": "KRW", "cash": 1000.0, "positions": [
                                           {"ticker": "005930", "name": "삼성전자", "qty": 1, "avg_price": 100.0, "price": 110.0,
                                            "eval_amt": 110.0, "pnl_pct": 10.0}]}}, 2)
    wj(D / "snapshot_261016_kr_bal1103.json", {"generated_at": "2026-10-16T11:03:00+09:00", "balance": {"cash": 900.0, "positions": []}}, 2)
    wt(D / "material_261016_kr.md", MATERIAL)
    wj(D / "treasury_scan.json", {"000020": {"ticker": "000020", "name": "동화약품", "corp_cls": "Y", "period": "2026 반기",
                                             "stlm_dt": "2026-06-30", "issued": 27931470, "treasury": 213540, "ratio_pct": 0.76,
                                             "rcept_no": "20260814002645", "scanned": "2026-09-14"},
                                  "000360": {"ticker": "000360", "name": "삼환기업", "error": "정기보고서에 주식총수 없음",
                                             "scanned": "2026-09-14"}}, 2)
    wj(D / "feedback" / "손실판정_261012.json", {"week": "261012", "source": "x", "rows": [
        {"thesis_id": "kr-r1", "ticker": "032830", "market": "KR", "kind": "closed", "verdict": "일시적", "why": "a", "pnl": -31500.0},
        {"thesis_id": "kr-r2", "ticker": "032830", "market": "KR", "kind": "closed", "verdict": "일시적", "why": "b", "pnl": -9000.0},
        {"thesis_id": "us-t", "ticker": "TSM", "market": "US", "kind": "open", "verdict": "이익", "why": "c", "pnl": 396.55}]}, 2)
    wt(D / "run_evidence" / "real_check_261016_kr.md",
       "# 실전 점검표 — 261016 · KR (2026-10-16 13:01 KST · 파일에서 기계로)\n\n| 항목 | 판정 | 결과 |\n|---|---|---|\n"
       "| ① 실계좌라면 샀을까 | ✓ | 승인 매수 1건 |\n| ③ 시각 | ★ | 수집→전송 101분 |\n")
    wt(A / "AI클레임_261016_kr_A.md", CLAIMS)
    wt(A / "AI클레임_261016_kr_A_판정.md", VERDICTS)
    return r


MATERIAL = """# 매매 분석 재료 — 2026-10-16 09:35 KST · KR 시장

## 계좌 상태

- 총 평가액: 1 KRW

## 뉴스레터 원문

# 매매 판단 재료 — 2026/10/16 (최근 2일)

## [경제·금융] UPPITY 머니레터 — 2026/10/15
*(이미 읽음 — 같은 시장 직전 run 재료 `material_261015_kr.md`에 전문이 있다.)*
## [경제·금융] UPPITY 머니레터 — 2026/10/16
**오늘의 머니레터**

본문 한 줄.

### 이 발행분의 링크 (2)

> 용어 해설 안내

- [기사 A] https://example.com/a?utm_source=newsletter&utm_medium=email
- [기사 B] https://example.com/b

## 세상공부 — 사회·시장 읽기

*(출처 `세상공부_261016_노트_v1_0.md` · 사건 1건)*

- **사건 하나 — 무엇이 일어났다** (10/15 · UPPITY)
        - 정의 한 줄 `[AI]`
        - 해석 한 줄 `[추정]`

## 링크 원문 (유니버스·축 매칭)

### [기사 B](https://example.com/b?utm_campaign=x)  ← `삼성전자` `반도체`
링크 B 본문.
둘째 줄.
"""

CLAIMS = """# AI 클레임 — 분석노트_261016_kr_v1_0.md · 묶음 A

1. (L10) 삼성전자 3Q26 잠정 영업이익 107.40조 [AI] [S1]
2. (L12) KOSPI 10-15 종가 5554.22 [AI] [S2]
3. (L14) 어딘가의 주장 [AI]
"""

VERDICTS = """# AI 클레임 판정표 — 261016 KR · 묶음 A

| 원 문구 | 하위 클레임 | 판정 | 티어 | 원문 인용 | URL | T1 시도 | 비고 |
|---|---|---|---|---|---|---|---|
| "영업이익 107.40조"(클레임 1) | 영업이익 | MATCH | T1 | 영업이익 107.40조 | https://www.example.com/a/ | 열람 | 대상 |
| "5554.22"(클레임 2) | 종가 | MISMATCH | T2 | 5,554.22 | https://other.example.org/x | 시도 | 다른 값 |
| "주장"(클레임 3) | 주장 | UNVERIFIABLE | T3 | — | https://nowhere.example.net/p | 시도 | 검색: 주장 · 웹 · 10:00 |
"""


def fake_ledger(base: Path) -> Path:
    d = base / "stepgate"
    d.mkdir(parents=True, exist_ok=True)
    wl(d / "promotion_ledger.jsonl", [
        {"type": "finding", "ts": "2026-10-16 12:00:00", "skill": "fact-check", "cp": "verdict", "run": "ai클레임_261016_kr_a_판정.md",
         "status": "PASS"},
        {"type": "finding", "ts": "2026-10-16 12:00:00", "skill": "world-study", "run": "세상공부_261016"}])
    return d


def use_db(path: Path):
    os.environ["AITB_DB"] = str(path)
    db.reset()


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **k)
    return rc, buf.getvalue()


def shas(root: Path, rels) -> dict:
    return {r: migrate.sha256(root / r) for r in rels if (root / r).exists()}


EXPORTS = list(migrate.EXPORT_REL.values())


# ───────────────────────────── 테스트 ─────────────────────────────

def t1_dry_writes_nothing():
    p = []
    base = _TMP / "dry"
    root = make_root(base)
    os.environ["AITB_STEPGATE_LEDGER"] = str(fake_ledger(base) / "promotion_ledger.jsonl")
    use_db(base / "migrate_dry.db")
    before = sorted(str(x.relative_to(base)) for x in base.rglob("*"))
    rc, out = quiet(migrate.main, ["--dry", "--root", str(root)])
    after = sorted(str(x.relative_to(base)) for x in base.rglob("*"))
    if rc != 0:
        p.append(f"종료 코드 {rc}")
    if before != after:
        p.append(f"파일이 생겼다: {sorted(set(after) - set(before))}")
    for want in ("journal/theses.json | 2", "journal/sources.json | 5", "ev_verdict(맞춘 행 2 · 못 맞춘 행 1", "발행분 1 · 링크 2"):
        if want not in out:
            p.append(f"표에 '{want}' 없음")
    report("(1) --dry — 분류·행 수 표만 · 아무것도 쓰지 않는다(저장소·보관 폴더 없음)", p)


def t2_apply_roundtrip():
    p = []
    base = _TMP / "apply"
    root = make_root(base)
    os.environ["AITB_STEPGATE_LEDGER"] = str(fake_ledger(base) / "promotion_ledger.jsonl")
    dbp = base / "migrate_apply.db"
    use_db(dbp)
    rels = migrate.archive_list(root)
    before = shas(root, rels)
    rc, out = quiet(migrate.apply, root)
    if rc != 0:
        p.append(f"apply 종료 코드 {rc}: {out[-1500:]}")
        report("(2) --apply — 바이트 같은 렌더본 · 행 수 · FK · 삭제", p)
        return
    for r in EXPORTS:
        f = root / r
        if not f.exists():
            p.append(f"렌더본 없음 {r}")
        elif migrate.sha256(f) != before[r]:
            p.append(f"렌더본이 원본과 다르다 {r}")
        elif os.access(f, os.W_OK):
            p.append(f"렌더본이 읽기 전용이 아니다 {r}")
    for r in migrate.remove_list(root):
        if (root / r).exists():
            p.append(f"지워지지 않음 {r}")
    pre = sorted((root / "journal" / "_pre_db").iterdir())
    if len(pre) != 1:
        p.append(f"보관 폴더 수 {len(pre)}")
    else:
        man = json.loads((pre[0] / "manifest.json").read_text(encoding="utf-8"))["files"]
        if set(man) != set(before) or any(man[k]["sha256"] != before[k] for k in man):
            p.append("보관본 해시가 원본과 다르다")
    if (dbp.parent / "exports").exists():
        p.append("싣는 동안 자동 내보내기가 exports/에 썼다")
    if "config/limits.json" in json.dumps(before):
        p.append("이전 안 하는 설정을 보관했다")
    if not (root / "config" / "limits.json").exists() or not (root / "config" / "main_names.json").exists():
        p.append("config 파일을 지웠다")
    use_db(dbp)
    c = db.connect()

    def n(sql, args=()):
        return c.execute(sql, args).fetchone()[0]

    if c.execute("PRAGMA foreign_key_check").fetchall():
        p.append("FK 위반")
    want = {"jd_thesis": 2, "jd_scenario": 2, "dim_axis": 1, "dim_sector": 1, "cal_event": 2, "jd_allocation": 2,
            "ev_fact": 5, "ev_source": 5, "ev_fact_run": 6, "ev_verdict": 2, "op_fetch": 2, "op_feedback": 2,
            "ex_equity_point": 2, "op_coverage": 2, "op_pending": 1, "op_benchmark_anchor": 2, "op_current_run": 1,
            "ex_position_peak": 3, "ex_refill": 1, "dim_sector_link": 4, "mat_issue": 1, "mat_link": 2, "mat_ws_event": 3,
            "mat_link_match": 2, "ex_signal": 1, "ex_proposal": 1, "ex_hold": 1, "ex_approval": 1, "ex_order": 1, "ex_fill": 1,
            "ex_balance": 2, "ex_position": 1, "op_loss_review": 3, "op_real_check": 2, "op_audit": 1, "co_order": 1,
            "ev_verdict_orphan": 1, "co_treasury_holding": 2}   # 보유 1 + 조회 오류 1(stlm_dt '' · error)
    for t, k in want.items():
        if n(f"SELECT COUNT(*) FROM {t}") != k:
            p.append(f"{t} {n(f'SELECT COUNT(*) FROM {t}')}행(기대 {k})")
    if n("SELECT COUNT(*) FROM dim_company WHERE in_watchlist=1") != 3:
        p.append("감시 목록 3행이 아니다")
    if [r["ticker"] for r in db.main_names("KR")] != ["005930", "000660"]:
        p.append(f"시총 상위 순서 {db.main_names('KR')}")
    kinds = dict(c.execute("SELECT id, kind FROM ev_fact").fetchall())
    if kinds.get("SRC-0003") != "placeholder" or kinds.get("SRC-0004") != "market_state" or kinds.get("SRC-0005") != "order":
        p.append(f"사실 종류 {kinds}")
    r1 = dict(c.execute("SELECT verified_status, verified_tier, verified_run, origin, source_id, created_run FROM ev_fact "
                        "WHERE id='SRC-0001'").fetchone())
    if (r1["verified_status"], r1["verified_tier"], r1["verified_run"], r1["origin"]) != ("verified", 1, "261016_kr", "issue"):
        p.append(f"SRC-0001 검증·출처 {r1}")
    # 옛 불일치는 문장에 교정·전파돼 있다 — 재검증 전이라 '미검증'(다시 쓰이면 큐에 오른다) · 재검토 표시는 내린다
    if n("SELECT verified_status='none' FROM ev_fact WHERE id='SRC-0002'") != 1 or \
       n("SELECT needs_review FROM ev_fact WHERE id='SRC-0002'") != 0 or \
       n("SELECT COUNT(*) FROM ev_verdict WHERE fact_id='SRC-0002' AND verdict='MISMATCH'") < 1:
        p.append("옛 MISMATCH 사실은 판정 기록은 남고 상태는 none·재검토 0이어야 한다")
    if n("SELECT s_no FROM ev_fact_run WHERE fact_id='SRC-0005' AND run_id='261016_kr'") != 5 or \
       n("SELECT created_run='261015_kr' FROM ev_fact WHERE id='SRC-0005'") != 1:
        p.append("S 번호·처음 run")
    o = dict(c.execute("SELECT ticker, amount, pct_of_sales, announced_at FROM co_order").fetchone())
    if o != {"ticker": "336260", "amount": 501400000000, "pct_of_sales": 110.25, "announced_at": "2026-09-02"}:
        p.append(f"공급계약 상세 {o}")
    orp = [dict(r) for r in c.execute("SELECT reason, verdict, line, src_file FROM ev_verdict_orphan")]
    if orp != [{"reason": "URL이 출처 원장에 없음", "verdict": "UNVERIFIABLE", "line": 7,
                "src_file": "analysis/AI클레임_261016_kr_A_판정.md"}]:
        p.append(f"못 맞춘 판정 행(ev_verdict_orphan) {orp}")
    if sorted(r[0] for r in c.execute("SELECT match FROM ev_verdict")) != ["S(클레임)", "url"]:
        p.append(f"매칭 방법 {[r[0] for r in c.execute('SELECT match FROM ev_verdict')]}")
    th = [tuple(r) for r in c.execute("SELECT market, ticker, stlm_dt, issued, treasury, ratio_pct FROM co_treasury_holding"
                                      " WHERE stlm_dt<>''")]
    te = [tuple(r) for r in c.execute("SELECT ticker, error FROM co_treasury_holding WHERE stlm_dt=''")]
    if te != [("000360", "정기보고서에 주식총수 없음")]:
        p.append(f"조회 오류 행 {te}")
    if th != [("KR", "000020", "2026-06-30", 27931470, 213540, 0.76)] or \
       list((db.docmeta_get("treasury_scan_errors") or {}).keys()) != ["000360"]:
        p.append(f"자기주식 보유 {th} · 오류 {db.docmeta_get('treasury_scan_errors')}")
    lk = dict(c.execute("SELECT fetched, body FROM mat_link WHERE url='https://example.com/b'").fetchone())
    if lk["fetched"] != 1 or "둘째 줄" not in (lk["body"] or ""):
        p.append(f"링크 원문 {lk}")
    if sorted(tuple(x) for x in c.execute("SELECT subject_type, subject_id FROM mat_link_match")) != [("company", "KR:005930"), ("keyword", "반도체")]:
        p.append("링크 매칭")
    pr = c.execute("SELECT increase_when FROM ex_proposal").fetchone()[0]
    if json.loads(pr or "null") != {"cond": "x2", "n": 1}:
        p.append(f"increase_when {pr}")
    run = dict(c.execute("SELECT started, session_date, account_mode, capture_at FROM ex_run WHERE run_id='261016_kr'").fetchone())
    if run != {"started": "2026-10-16T09:30:05+09:00", "session_date": "2026-10-16", "account_mode": "paper",
               "capture_at": "2026-10-16T09:35:50+09:00"}:
        p.append(f"ex_run {run}")
    # 한 종목(032830)에 논지가 둘 — 키가 (주, 논지)라 둘 다 남는다 · 열린 논지(TSM)의 pnl은 실현 손익이 아니라 비운다
    if n("SELECT SUM(net_realized) FROM op_loss_review WHERE ticker='032830'") != -40500.0 or \
       n("SELECT net_realized IS NULL FROM op_loss_review WHERE ticker='TSM'") != 1 or \
       len((db.docmeta_get("loss_review_261012") or {}).get("rows") or []) != 3:
        p.append("손실 판정(논지 둘 · 열린 논지 실현 손익 비움 · 원본 문서는 docmeta)")
    if db.sector_bridge_doc() != json.loads((pre[0] / "config/sector_bridge.json").read_text(encoding="utf-8")):
        p.append("sector_bridge_doc() ≠ 원본")
    if n("SELECT COUNT(*) FROM sys_doc_hist") != 0:
        p.append("최초 삽입인데 sys_doc_hist가 쌓였다")
    if db.HERE != migrate.REAL_HERE:
        p.append(f"apply 뒤 db 뿌리가 제자리가 아니다: {db.HERE}")
    report("(2) --apply — 렌더본 7개 바이트 같음·읽기 전용 · 원장 22종 삭제 · 행 수 · FK · 종류·검증·재료·집행", p)

    # 다시 apply → 거부
    p = []
    rc, out = quiet(migrate.apply, root)
    if rc != 2:
        p.append(f"두 번째 apply 종료 코드 {rc}")
    rc2 = None
    try:
        use_db(dbp)
        migrate.migrate_from_files(root)
    except RuntimeError:
        rc2 = "거부"
    except Exception as e:  # noqa: BLE001
        rc2 = f"{type(e).__name__}"
    if rc2 != "거부":
        p.append(f"비어 있지 않은 저장소에 migrate_from_files: {rc2}")
    report("(3) 이미 이전한 root · 비어 있지 않은 저장소 — 거부", p)

    # 되돌리기
    p = []
    rc, out = quiet(migrate.rollback, root, pre[0].name)
    if rc != 0:
        p.append(f"rollback 종료 코드 {rc}: {out[-600:]}")
    after = shas(root, rels)
    if after != before:
        p.append(f"되돌린 파일이 원본과 다르다: {[k for k in before if after.get(k) != before[k]]}")
    if dbp.exists():
        p.append("저장소가 남았다")
    if not list(pre[0].glob("trading_rolledback_*.db")):
        p.append("저장소를 보관 폴더로 옮기지 않았다")
    for r in EXPORTS:
        if not os.access(root / r, os.W_OK):
            p.append(f"되돌린 뒤에도 읽기 전용 {r}")
    report("(4) --rollback — 원본 해시 그대로 · 저장소는 보관 폴더로", p)

    # --force: 저장소가 있으면 거부 → --force면 옛 저장소를 옮기고 진행
    p = []
    use_db(dbp)
    sqlite3.connect(str(dbp)).close()
    rc, _ = quiet(migrate.apply, root)
    if rc != 2:
        p.append(f"저장소가 있는데 apply 종료 코드 {rc}")
    rc, out = quiet(migrate.apply, root, True)
    if rc != 0:
        p.append(f"--force 종료 코드 {rc}: {out[-800:]}")
    newest = sorted((root / "journal" / "_pre_db").iterdir())[-1]
    if not (newest / "trading_replaced.db").exists():
        p.append("옛 저장소를 보관 폴더로 옮기지 않았다")
    report("(5) 저장소가 있으면 거부 · --force는 옛 저장소를 보관하고 진행", p)


def t3_doctor_fail_keeps_originals():
    p = []
    base = _TMP / "fail"
    root = make_root(base, theses_indent=4)          # 들여쓰기만 다른 원본은 원본 형식으로 대조해 통과한다 — 실패는 값 차이로 만든다
    os.environ["AITB_STEPGATE_LEDGER"] = str(fake_ledger(base) / "promotion_ledger.jsonl")
    dbp = base / "migrate_fail.db"
    use_db(dbp)
    rels = migrate.archive_list(root)
    before = shas(root, rels)
    real_render = migrate.db.render

    def bad_render(name):                            # 렌더가 원본과 값이 다른 경우(이전 결함) — 검사가 멈춰야 한다
        v = real_render(name)
        if name == "theses" and v.get("theses"):
            v["theses"][0]["status"] = "변조"
        return v
    migrate.db.render = bad_render
    try:
        rc, out = quiet(migrate.apply, root)
    finally:
        migrate.db.render = real_render
    if rc != 1:
        p.append(f"종료 코드 {rc}(기대 1)")
    if "theses: 첫 차이" not in out:
        p.append("첫 차이 위치를 보여 주지 않았다")
    if shas(root, rels) != before:
        p.append("원본이 바뀌었다")
    if any(os.access(root / r, os.W_OK) is False for r in rels):
        p.append("원본 권한이 바뀌었다")
    if dbp.exists():
        p.append("실패한 저장소가 제자리에 남았다")
    pre = sorted((root / "journal" / "_pre_db").iterdir())
    if not pre or not (pre[0] / "trading_failed.db").exists():
        p.append("실패한 저장소를 보관 폴더로 옮기지 않았다")
    report("(6) 검사 실패 — 멈춤 · 원본·권한 그대로 · 첫 차이 표시 · 저장소는 보관 폴더로", p)


def t4_real_folder_guard():
    """실제 폴더에 AITB_DB로 apply하면 거부 — 실제 폴더로 돌리지 않고, 가짜 root를 migrate.REAL_HERE로 잠깐 가리켜 같은 분기를 본다."""
    p = []
    base = _TMP / "guard"
    root = make_root(base)
    use_db(base / "migrate_guard.db")
    real_here = migrate.REAL_HERE
    migrate.REAL_HERE = root
    try:
        rc, out = quiet(migrate.apply, root)
    finally:
        migrate.REAL_HERE = real_here
    if rc != 2 or "거부" not in out:
        p.append(f"실제 폴더 + AITB_DB apply가 거부되지 않았다(rc={rc})")
    if (root / "journal" / "_pre_db").exists():
        p.append("거부했는데 보관 폴더가 생겼다")
    report("(7) 실제 폴더에 임시 저장소로 apply — 거부(실제 원장 삭제 사고 방지)", p)


for fn in (t1_dry_writes_nothing, t2_apply_roundtrip, t3_doctor_fail_keeps_originals, t4_real_folder_guard):
    _safe(fn)

db.reset()
shutil.rmtree(_TMP, ignore_errors=True)
_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_migrate.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
