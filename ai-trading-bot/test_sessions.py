#!/usr/bin/env python3
"""
날짜 규칙 테스트 — 스탬프·기록 날짜는 **거래하는 세션의 현지 날짜**(국내 = 한국 · 미국 = 동부, 2026-10-14부터).

왜(2026-10-13 밤 사용자): "날짜 기록은 무조건 현지 날짜(국장이면 한국, 미장이면 미국) 기준으로." 장 마감 뒤·주말에
돌린 run은 그 run이 준비하는 **다음 거래일**이다. 전환일 전 스탬프는 옛 규칙(시작한 한국 날짜)으로 읽는다.
현재 run 포인터(저장소 op_current_run — 예전 `journal/current_run.json`)가 진행 중인 run의 스탬프를 잇는다.
실제 저장소는 건드리지 않는다 — 전부 `testkit.db_case()`의 빈 임시 저장소에서 돈다(2026-10-17).

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

import db              # noqa: E402
import sessions as ss   # noqa: E402
from testkit import db_case, seed   # noqa: E402

KST = timezone(timedelta(hours=9))
PASS, FAIL = "PASS", "FAIL"
results = []
_STATE = {"current": "(시작 전)", "done": False}
_TMP = Path(tempfile.mkdtemp(prefix="sstest_"))


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
        if cur.get("session_date") != "2026-10-13" or (db.current_run_read() or {}).get("stamp") != "261013":
            p.append(f"포인터를 못 썼다: {cur} · 저장소 {db.current_run_read()}")
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
        before = db.current_run_read()
        with contextlib.redirect_stderr(io.StringIO()):
            r = ss.write_current("kr", "260916", later)
        if r or db.current_run_read() != before:
            p.append("지난 스탬프(260916)로 포인터를 덮었다")
        # 포인터가 없으면 run_date는 시장 세션 날짜 → 시장도 없으면 한국 날짜
        db.kv_delete("op_current_run", "current")
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
    saved = (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL)
    td = _TMP / "scan"
    try:
        ss.DATA = td / "data"; ss.ANALYSIS = td / "analysis"; ss.SIGNALS = td / "signals"
        ss.JOURNAL = td / "journal"
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
        ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL = saved
    report("(5) scan — 주말 run의 앞날 스탬프(다음 거래일)를 센다 · 미래 날짜는 미실행이 아니다", p)


def t6_old_stamp_reads_session_no_rename():
    """옛 스탬프는 이름을 바꾸지 않고 세션 날짜로 읽는다(2026-10-17) — `rename_run.py`(10-12·10-13 미국 두 run을 제 이름으로
    옮기던 도구 · 전 텍스트 파일 정규식 치환)는 `_archive/`로 갔다. 대신 run의 세션 날짜는 run 행의 칸(`ex_run.session_date`)이고,
    전환일(2026-10-14) 전 스탬프는 그 run이 처음 손댄 파일 시각으로 옛 규칙을 적용해 읽는다(`session_iso`).
    잴 것: 10-13 01:24 KST에 시작한 미국 run(스탬프 261013)은 동부 10-12 세션 · 전환일 뒤 스탬프는 시각과 무관하게 그 날짜 ·
    run id는 그대로(261013_us)이고 세션 날짜는 칸에 남는다 · 실제 10-12 run(261012_us)과 행이 겹치지 않는다 · scan이 같은 날짜를 센다."""
    import os
    p = []
    saved = (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL)
    td = _TMP / "oldstamp"
    try:
        ss.DATA = td / "data"; ss.ANALYSIS = td / "analysis"; ss.SIGNALS = td / "signals"; ss.JOURNAL = td / "journal"
        for d in (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL):
            d.mkdir(parents=True, exist_ok=True)
        stamps = {("261013", "us"): k(2026, 10, 13, 1, 24),      # 옛 규칙 — 시작한 한국 날짜 스탬프 · 동부 월 10-12 장중
                  ("261012", "us"): k(2026, 10, 12, 22, 40),     # 같은 동부 10-12 세션의 다른 run(실제로 10-12에 시작)
                  ("261014", "us"): k(2026, 10, 15, 1, 0)}       # 전환일 뒤 — 스탬프 = 세션 날짜(시작 시각과 무관)
        for (st, m), t in stamps.items():
            for n in (f"material_{st}_{m}.md", f"snapshot_{st}_{m}.json"):
                f = ss.DATA / n
                f.write_text("x", encoding="utf-8")
                os.utime(f, (t.timestamp(), t.timestamp()))
        want = {("261013", "us"): "2026-10-12", ("261012", "us"): "2026-10-12", ("261014", "us"): "2026-10-14"}
        for (st, m), w in want.items():
            got = ss.session_iso(st, m)
            if got != w:
                p.append(f"{st}_{m} 세션 날짜 {got}(기대 {w})")
            db.run_start(st, m, session_date=got)
        rows = {r["run_id"]: r for r in db.q("SELECT run_id, stamp, session_date FROM ex_run")}
        if set(rows) != {"261013_us", "261012_us", "261014_us"}:
            p.append(f"run id가 바뀌었다(이름 바꾸기 없이 스탬프 그대로여야): {sorted(rows)}")
        elif rows["261013_us"]["session_date"] != "2026-10-12" or rows["261013_us"]["stamp"] != "261013":
            p.append(f"옛 스탬프 run 행의 세션 날짜 칸: {rows['261013_us']}")
        db.run_start("261013", "us")                              # 같은 run을 다시 열어도 세션 날짜 칸은 그대로
        if (db.run_get("261013", "us") or {}).get("session_date") != "2026-10-12":
            p.append("run을 다시 열자 세션 날짜 칸이 지워졌다")
        sc = {(r["stamp"], r["market"]): r["session_date"] for r in ss.scan(6, today=date(2026, 10, 16))}
        for key, w in want.items():
            if sc.get(key) != w:
                p.append(f"scan의 {key} 세션 날짜 {sc.get(key)}(기대 {w})")
        if Path(HERE / "rename_run.py").exists():
            p.append("rename_run.py가 아직 작업 폴더에 있다(_archive/로 갔어야)")
    finally:
        ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL = saved
    report("(6) 옛 스탬프는 이름을 바꾸지 않고 세션 날짜로 읽는다 — 전환일 전 시작 시각 규칙 · run 행 session_date 칸 · scan 일치", p)


def t7_ledgers_in_store():
    """심은 run 기록·회고가 저장소에서 읽히고(runs·_reviewed) · scan 원장은 op_session으로 통째 다시 쓰인다 · 포인터 행 하나."""
    import argparse
    p = []
    saved = (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL)
    _scan = ss.scan
    td = _TMP / "store"
    try:
        ss.DATA = td / "data"; ss.ANALYSIS = td / "analysis"; ss.SIGNALS = td / "signals"; ss.JOURNAL = td / "journal"
        for d in (ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL):
            d.mkdir(parents=True, exist_ok=True)
        import os
        for n in ("material_261015_kr.md", "snapshot_261015_kr.json"):
            f = ss.DATA / n
            f.write_text("x", encoding="utf-8")
            ts = k(2026, 10, 15, 9, 31).timestamp(); os.utime(f, (ts, ts))
        seed(logs={"run_log": [{"market": "kr", "session_date": "2026-10-15", "outcome": "ok", "orders": {"sent": 2},
                                "no_trade": False}],
                   "reviews": [{"market": "KR", "session": "2026-10-15", "signal": "signal_261015_kr.json"},
                               {"market": "US", "session": "2026-10-14", "phantom": True}]})
        if ("kr", "2026-10-15") not in ss._reviewed() or ("us", "2026-10-14") in ss._reviewed():
            p.append(f"회고 기록을 저장소에서 못 읽었다(또는 허깨비를 셌다): {ss._reviewed()}")
        rs = [r for r in ss.runs(4, today=date(2026, 10, 16)) if r["stamp"] == "261015" and r["market"] == "kr"]
        if not rs or rs[0].get("outcome") != "ok" or not rs[0].get("reviewed") or rs[0].get("orders") != {"sent": 2}:
            p.append(f"run 기록·회고가 runs()에 안 붙었다: {rs}")
        # scan의 오늘을 심은 기록에 맞춰 고정한다 — 실제 오늘을 쓰면 날짜가 지나 창(3일)이 그 run을 벗어난다(2026-10-19)
        ss.scan = lambda days, today=None: _scan(days, today=today or date(2026, 10, 16))
        with contextlib.redirect_stdout(io.StringIO()):
            ss.cmd_scan(argparse.Namespace(days=3))
        led = db.log_rows("sessions")
        if not led or not any(r.get("stamp") == "261015" and r.get("market") == "kr" for r in led):
            p.append(f"scan 원장이 저장소(op_session)에 없다: {len(led)}행")
        with contextlib.redirect_stdout(io.StringIO()):
            ss.cmd_scan(argparse.Namespace(days=3))
        if len(db.log_rows("sessions")) != len(led):
            p.append("scan을 두 번 돌렸더니 원장 행이 늘었다(통째 다시 쓰기여야 한다)")
        ss.write_current("kr", "261015", k(2026, 10, 15, 9, 31))
        ss.write_current("kr", "261015", k(2026, 10, 15, 9, 40))
        n_ptr = db.q1("SELECT COUNT(*) AS n FROM op_current_run")["n"]
        if n_ptr != 1:
            p.append(f"현재 run 포인터 행이 {n_ptr}개(1개여야 한다)")
    finally:
        ss.DATA, ss.ANALYSIS, ss.SIGNALS, ss.JOURNAL = saved
        ss.scan = _scan
    report("(7) 저장소 — 심은 run 기록·회고가 읽힌다 · scan 원장 op_session 통째 쓰기 · 포인터 한 행", p)


with db_case():
    for fn in (t1_session_date_table, t2_stamp_to_session_cutover, t3_prev_trading_day, t4_pointer, t5_scan_future_stamp,
               t6_old_stamp_reads_session_no_rename, t7_ledgers_in_store):
        _safe(fn)

shutil.rmtree(_TMP, ignore_errors=True)
_STATE["done"] = True
n_fail = sum(1 for s, _, _ in results if s == FAIL)
print(f"\nTEST-SUMMARY file=test_sessions.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
