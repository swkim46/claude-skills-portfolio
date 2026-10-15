#!/usr/bin/env python3
"""
날짜 규칙 테스트 — 스탬프·기록 날짜는 **거래하는 세션의 현지 날짜**(국내 = 한국 · 미국 = 동부, 2026-10-14부터).

왜(2026-10-13 밤 사용자): "날짜 기록은 무조건 현지 날짜(국장이면 한국, 미장이면 미국) 기준으로." 장 마감 뒤·주말에
돌린 run은 그 run이 준비하는 **다음 거래일**이다. 전환일 전 스탬프는 옛 규칙(시작한 한국 날짜)으로 읽는다.
현재 run 포인터(`journal/current_run.json`)가 진행 중인 run의 스탬프를 잇는다. 실제 원장·포인터는 건드리지 않는다.

    python3 test_sessions.py          # run_tests.py가 부른다
"""
import atexit
import io
import contextlib
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import sessions as ss   # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []
_STATE = {"current": "(시작 전)", "done": False}
_TMP = Path(tempfile.mkdtemp(prefix="sstest_"))
ss.CURRENT_RUN = _TMP / "current_run.json"          # 실제 포인터를 건드리지 않는다


def _halt_banner():
    if _STATE["done"]:
        return
    n, nf = len(results), sum(1 for s, _, _ in results if s == FAIL)
    print(f"TEST-HALTED file=test_sessions.py ran={n} failed={nf} last={_STATE['current']}")
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


def k(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=KST)


def t1_session_date_table():
    """계획표의 상황 + 주말·휴장·마감 뒤(실제 config/holidays.json — 국내 10-09 한글날 · 미국 11-26 추수감사절)."""
    p = []
    cases = [
        ("us", k(2026, 10, 13, 22, 35), date(2026, 10, 13), "화 22:35 KST = 동부 화 09:35(장 전·당일 세션)"),
        ("us", k(2026, 10, 13, 1, 24), date(2026, 10, 12), "화 01:24 KST = 동부 월 12:24(장중)"),
        ("us", k(2026, 10, 14, 6, 30), date(2026, 10, 14), "수 06:30 KST = 동부 화 17:30(마감 뒤 → 다음 거래일)"),
        ("us", k(2026, 10, 17, 14, 0), date(2026, 10, 19), "토 14:00 KST = 동부 토 01:00(주말 → 월)"),
        ("us", k(2026, 10, 17, 7, 0), date(2026, 10, 19), "토 07:00 KST = 동부 금 18:00(마감 뒤 → 월)"),
        ("us", k(2026, 10, 16, 12, 0), date(2026, 10, 16), "금 12:00 KST = 동부 목 23:00(마감 뒤 → 금)"),
        ("us", k(2026, 11, 26, 23, 0), date(2026, 11, 27), "추수감사절(동부 11-26 휴장) → 금"),
        ("kr", k(2026, 10, 14, 2, 0), date(2026, 10, 14), "국내 개장 전"),
        ("kr", k(2026, 10, 14, 10, 35), date(2026, 10, 14), "국내 장중"),
        ("kr", k(2026, 10, 13, 17, 0), date(2026, 10, 14), "국내 마감 뒤 → 다음 거래일"),
        ("kr", k(2026, 10, 8, 16, 0), date(2026, 10, 12), "목 마감 뒤 · 금 한글날 · 주말 → 월"),
        ("kr", k(2026, 10, 9, 10, 0), date(2026, 10, 12), "휴장일 당일 → 다음 거래일"),
    ]
    for m, now, want, why in cases:
        got = ss.session_date(m, now)
        if got != want:
            p.append(f"{m} {now:%m-%d %H:%M} → {got} (기대 {want} · {why})")
    if ss.session_stamp("us", k(2026, 10, 13, 22, 35)) != "261013":
        p.append("session_stamp 형식(YYMMDD)")
    report("(1) session_date — 장중·마감 뒤·주말·휴장의 거래 세션 현지 날짜", p)


def t2_stamp_to_session_cutover():
    p = []
    if ss.stamp_to_session("261014", "us") != date(2026, 10, 14):
        p.append("전환일 이후 스탬프는 그 자체가 세션 날짜여야 한다")
    if ss.stamp_to_session("261020", "kr") != date(2026, 10, 20):
        p.append("국내 전환일 이후")
    if ss.stamp_to_session("261013", "us", k(2026, 10, 13, 1, 24)) != date(2026, 10, 12):
        p.append("전환일 전 미국 스탬프는 옛 규칙(시작 시각의 동부 세션)이어야 한다")
    if ss.stamp_to_session("260919", "us", k(2026, 9, 19, 2, 13)) != date(2026, 9, 18):
        p.append("옛 토요일 스탬프 → 동부 금요일")
    if ss.stamp_to_session("261013", "kr", k(2026, 10, 13, 10, 35)) != date(2026, 10, 13):
        p.append("옛 국내 스탬프는 그 날짜")
    if ss.LOCAL_STAMP_FROM != date(2026, 10, 14):
        p.append(f"전환일이 바뀌었다: {ss.LOCAL_STAMP_FROM}")
    report("(2) stamp_to_session — 전환일(2026-10-14) 전은 옛 규칙 · 뒤는 스탬프 = 세션 날짜", p)


def t3_prev_trading_day():
    p = []
    if ss.prev_trading_day("kr", date(2026, 10, 12)) != date(2026, 10, 8):
        p.append("국내 월 10-12 앞은 목 10-08(한글날·주말 건너뜀)")
    if ss.prev_trading_day("us", date(2026, 10, 19)) != date(2026, 10, 16):
        p.append("미국 월 앞은 금")
    if ss.prev_trading_day("us", date(2026, 11, 27)) != date(2026, 11, 25):
        p.append("추수감사절 건너뜀")
    report("(3) prev_trading_day — 주말·휴장 건너뜀", p)


def t4_pointer():
    """포인터: 쓰기·시장 구분·18시간 만료·미완 run 이어받기·지난 스탬프 거부."""
    p = []
    saved_j = ss.JOURNAL
    jd = _TMP / "journal"; jd.mkdir(exist_ok=True)
    try:
        ss.JOURNAL = jd
        t0 = k(2026, 10, 13, 10, 35)
        cur = ss.write_current("kr", "261013", t0)
        if cur.get("session_date") != "2026-10-13" or not ss.CURRENT_RUN.exists():
            p.append(f"포인터를 못 썼다: {cur}")
        if ss.read_current("kr", t0 + timedelta(hours=2)).get("stamp") != "261013":
            p.append("같은 시장 2시간 뒤에 포인터를 못 읽는다")
        if ss.read_current("us", t0 + timedelta(hours=2)):
            p.append("다른 시장의 포인터를 읽었다")
        if ss.read_current("kr", t0 + timedelta(hours=19)):
            p.append("18시간 지난 포인터를 믿었다")
        # 국내 run이 마감 뒤(16:00)까지 이어지면 — 작업기록이 미완이면 그 스탬프를 잇는다
        (jd / "worklog_261013_kr.md").write_text("# 작업기록\n<!-- ✓ 작업기록-note -->\n", encoding="utf-8")
        later = k(2026, 10, 13, 16, 0)
        if ss.session_stamp("kr", later) != "261014":
            p.append("전제: 마감 뒤 session_stamp는 다음 거래일")
        if ss.run_stamp("kr", later) != "261013":
            p.append(f"미완 run을 잇지 않았다: {ss.run_stamp('kr', later)}")
        if ss.run_date("kr", later) != date(2026, 10, 13):
            p.append("run_date가 포인터 세션 날짜가 아니다")
        if ss.run_date("", later) != date(2026, 10, 13):
            p.append("시장 없는 run_date가 포인터를 안 읽었다")
        if ss.run_date("us", later) != ss.session_date("us", later):
            p.append("다른 시장 run_date는 그 시장 세션 날짜여야 한다")
        (jd / "worklog_261013_kr.md").write_text("<!-- ✓ 작업기록-execute -->\n", encoding="utf-8")
        if ss.run_stamp("kr", later) != "261014":
            p.append("끝난 run 뒤에는 다음 세션 스탬프여야 한다")
        # 지난 스탬프(재처리·테스트)로는 포인터를 덮지 않는다
        before = ss.CURRENT_RUN.read_text(encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            r = ss.write_current("kr", "260916", later)
        if r or ss.CURRENT_RUN.read_text(encoding="utf-8") != before:
            p.append("지난 스탬프(260916)로 포인터를 덮었다")
        # 포인터가 없으면 run_date는 시장 세션 날짜 → 시장도 없으면 한국 날짜
        ss.CURRENT_RUN.unlink()
        if ss.run_date("us", k(2026, 10, 14, 1, 0)) != date(2026, 10, 13):
            p.append("포인터 없이 미국 run_date(01:00 KST → 동부 10-13)")
        if ss.run_date("", k(2026, 10, 14, 1, 0)) != date(2026, 10, 14):
            p.append("포인터·시장 없으면 한국 날짜")
    finally:
        ss.JOURNAL = saved_j
    report("(4) 현재 run 포인터 — 시장 구분·18시간·미완 이어받기·지난 스탬프 거부", p)


def t5_scan_future_stamp():
    """토요일 미국 run은 월요일 스탬프(오늘보다 뒤)를 쓴다 — scan이 그 run을 세야 한다."""
    p = []
    saved = (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL, ss.LEDGER)
    td = _TMP / "scan"
    try:
        ss.DATA = td / "data"; ss.ANALYSIS = td / "analysis"; ss.SIGNALS = td / "signals"
        ss.JOURNAL = td / "journal"; ss.LEDGER = ss.JOURNAL / "sessions.jsonl"
        for d in (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL):
            d.mkdir(parents=True, exist_ok=True)
        import os
        for n in ("material_261019_us.md", "snapshot_261019_us.json"):
            f = ss.DATA / n
            f.write_text("x", encoding="utf-8")
            ts = k(2026, 10, 17, 14, 0).timestamp(); os.utime(f, (ts, ts))
        rows = ss.scan(3, today=date(2026, 10, 17))
        us = [r for r in rows if r["market"] == "us" and r["stamp"] == "261019"]
        if len(us) != 1 or us[0]["session_date"] != "2026-10-19":
            p.append(f"앞날 스탬프 run을 못 셌다: {[(r['stamp'], r['session_date']) for r in rows if r['market'] == 'us']}")
        if any(r["reached"] == "0 없음" and r["stamp_date"] > "2026-10-17" for r in rows):
            p.append("아직 안 온 날짜를 '0 없음'으로 적었다")
    finally:
        ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL, ss.LEDGER = saved
    report("(5) scan — 주말 run의 앞날 스탬프(다음 거래일)를 센다 · 미래 날짜는 미실행이 아니다", p)


def t6_rename_run():
    """두 run 이름 바꾸기(rename_run.py) — 임시 폴더의 가짜 run으로 건조 실행 → 적용 → 남은 표기 0 · 시각 보존 · id 보존."""
    import os
    import rename_run as rr
    p = []
    root = _TMP / "rename"
    for d in ("analysis", "journal", "signals", "data/run_evidence"):
        (root / d).mkdir(parents=True, exist_ok=True)
    t_run = k(2026, 10, 13, 1, 24)
    files = {
        "analysis/분석노트_261013_us_v1_0.md": "US 261013 · 직전 `intraday_touch_261013_us.json` · 리서치 `섹터_전력_261013_v1_0.md` · AL-261013-us",
        "analysis/분석노트_261013_kr_v1_0.md": "직전 run(US 261013) · 시그널 signal_261013_us.json · 국내 261013_kr 그대로",
        "analysis/섹터_전력_261013_v1_0.md": "# 섹터 리서치 (261013 · US)",
        "analysis/섹터_국내_261013_v1_0.md": "# 국내 섹터 리서치 — 이름 그대로",
        "data/run_evidence/intraday_touch_261013_us.json": "{}",
        "data/run_evidence/intraday_touch_261012_us.json": '{"real": "10-12"}',
        "signals/signal_261013_us.json": '{"run_id": "us:261013"}',
        "journal/allocation.jsonl": '{"id": "AL-261013-us", "stamp": "261013", "market": "us"}\n{"id": "AL-261013-kr", "stamp": "261013", "market": "kr"}\n',
        "journal/reviews.jsonl": '{"market": "US", "session": "2026-10-13", "signal": "signal_261013_us.json"}\n'
                                 '{"market": "KR", "session": "2026-10-13", "signal": "signal_261013_kr.json"}\n',
        "journal/equity_curve.jsonl": '{"date": "2026-10-13", "market": "US", "ts": "2026-10-13T02:43:08+09:00"}\n'
                                      '{"date": "2026-10-13", "market": "US", "ts": "2026-10-13T17:29:02+09:00"}\n'
                                      '{"date": "2026-10-13", "market": "KR", "ts": "2026-10-13T02:43:08+09:00"}\n',
        "journal/scenarios.json": json.dumps({"scenarios": [
            {"id": "SC-261013-1", "opened_by": "us:261013", "market": "us", "opened": "261013"},
            {"id": "SC-261013-2", "opened_by": "kr:261013", "market": "kr", "opened": "261013"}]}, ensure_ascii=False, indent=2) + "\n",
        "journal/fetch_log.jsonl": '{"note": "261013_us 그대로 둔다"}\n',
    }
    for rel, body in files.items():
        f = root / rel
        f.write_text(body, encoding="utf-8")
        ts = t_run.timestamp(); os.utime(f, (ts, ts))
    led = _TMP / "promotion_ledger.jsonl"          # 감사 원장(프로젝트 밖) — run 이름표만 바뀐다
    led.write_text('{"run": "ai클레임_261013_us_주문근거_판정.md", "status": "FAIL"}\n{"run": "ai클레임_261013_kr_판정.md"}\n',
                   encoding="utf-8")
    pl = rr.plan(root, "261013_us", "261012_us", ["섹터_전력_261013_v1_0.md"], started=t_run, ledger=led)
    names = {b.name for _, b in pl["renames"]}
    for want in ("분석노트_261012_us_v1_0.md", "섹터_전력_261012_v1_0.md", "intraday_touch_261012_us_b.json", "signal_261012_us.json"):
        if want not in names:
            p.append(f"이름 계획에 {want}가 없다: {sorted(names)}")
    if (root / "analysis/분석노트_261012_us_v1_0.md").exists():
        p.append("건조 실행이 파일을 바꿨다")
    with contextlib.redirect_stdout(io.StringIO()):
        rr.apply(root, pl)
    rd = lambda rel: (root / rel).read_text(encoding="utf-8")
    n1 = rd("analysis/분석노트_261012_us_v1_0.md")
    for want in ("US 261012", "intraday_touch_261012_us_b.json", "섹터_전력_261012_v1_0.md", "AL-261012-us"):
        if want not in n1:
            p.append(f"이 run 노트에 {want}가 없다: {n1}")
    kr = rd("analysis/분석노트_261013_kr_v1_0.md")
    if "US 261012" not in kr or "signal_261012_us.json" not in kr or "261013_kr" not in kr:
        p.append(f"다른 run 노트의 참조: {kr}")
    if rd("data/run_evidence/intraday_touch_261012_us.json") != '{"real": "10-12"}':
        p.append("이미 있던 10-12 파일을 덮었다")
    if not (root / "analysis/섹터_국내_261013_v1_0.md").exists():
        p.append("--also가 아닌 시장 표기 없는 파일을 바꿨다")
    al = rd("journal/allocation.jsonl")
    if '"id": "AL-261012-us", "stamp": "261012"' not in al or '"id": "AL-261013-kr", "stamp": "261013"' not in al:
        p.append(f"배분 원장: {al}")
    rv = [json.loads(x) for x in rd("journal/reviews.jsonl").splitlines() if x.strip()]
    if rv[0]["session"] != "2026-10-12" or rv[1]["session"] != "2026-10-13":
        p.append(f"회고 session: {rv}")
    eq = [json.loads(x) for x in rd("journal/equity_curve.jsonl").splitlines() if x.strip()]
    if [r["date"] for r in eq] != ["2026-10-12", "2026-10-13", "2026-10-13"]:
        p.append(f"자산 곡선 date(이 run 시간대의 미국 행만): {[r['date'] for r in eq]}")
    sc = json.loads(rd("journal/scenarios.json"))["scenarios"]
    if [(r["id"], r["opened_by"], r["opened"]) for r in sc] != [("SC-261013-1", "us:261012", "261012"), ("SC-261013-2", "kr:261013", "261013")]:
        p.append(f"시나리오(id 보존 · 이 run 행만 opened): {sc}")
    if "261013_us" not in rd("journal/fetch_log.jsonl"):
        p.append("fetch_log를 고쳤다")
    if abs((root / "analysis/분석노트_261012_us_v1_0.md").stat().st_mtime - t_run.timestamp()) > 1 or \
       abs((root / "journal/allocation.jsonl").stat().st_mtime - t_run.timestamp()) > 1:
        p.append("파일 시각을 보존하지 않았다")
    if rr.remaining(root, "261013_us"):
        p.append(f"옛 표기가 남았다: {rr.remaining(root, '261013_us')}")
    if not (root / "_rename_snapshots/261013_us_to_261012_us/analysis/분석노트_261013_us_v1_0.md").exists():
        p.append("원본 복사본이 없다")
    if "261013_us" not in rd("journal/rename_log.jsonl"):
        p.append("rename_log 기록이 없다")
    lt = led.read_text(encoding="utf-8")
    if "ai클레임_261012_us_주문근거_판정.md" not in lt or "ai클레임_261013_kr_판정.md" not in lt:
        p.append(f"감사 원장 이름표: {lt}")
    pl2 = rr.plan(root, "261012_us", "261011_us", [], ledger=led, ledger_only=True)
    if pl2["renames"] or set(pl2["edits"]) != {led}:
        p.append(f"--ledger-only가 원장 밖을 건드린다: {pl2['renames'][:2]} {list(pl2['edits'])[:3]}")
    with contextlib.redirect_stdout(io.StringIO()):
        rr.apply(root, pl2)
    if "ai클레임_261011_us_주문근거_판정.md" not in led.read_text(encoding="utf-8") or \
       not (root / "analysis/분석노트_261012_us_v1_0.md").exists():
        p.append("원장만 바꾸기 결과가 틀렸다")
    report("(6) rename_run — 이름·참조·회고·자산 곡선·시나리오·감사 원장 이름표 · 충돌은 _b · id·시각 보존 · 다른 run 파일은 참조만", p)


for fn in (t1_session_date_table, t2_stamp_to_session_cutover, t3_prev_trading_day, t4_pointer, t5_scan_future_stamp, t6_rename_run):
    _safe(fn)

shutil.rmtree(_TMP, ignore_errors=True)
_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_sessions.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
