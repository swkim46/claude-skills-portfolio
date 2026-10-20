#!/usr/bin/env python3
"""
계좌 전환(account_switch.py) 시험 — 빈 임시 저장소(`testkit.db_case`)와 임시 폴더에서 적용·되돌리기를 끝까지 돈다
(실제 저장소·백업 폴더·한도 파일·브로커는 건드리지 않는다 — 백업은 임시 저장소 옆 `_db_backup/`에 간다: `db.backup_dir()`).

왜(2026-10-15): 운영자가 "전환"을 요청하면 이 스크립트가 원장을 바꾼다. 한 번에 맞아야 하는 쓰기 경로라
끝까지 도는 시험이 있어야 한다 — 논지 되돌림·만기·다리 체결 표시·고점·소급 void·한도 두 값(형식 보존)·복사본·되돌리기.

    python3 test_switch.py          # run_tests.py가 부른다
"""
import atexit
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import account_switch as sw  # noqa: E402
import db                    # noqa: E402
from testkit import db_case, seed   # noqa: E402

results = []
_STATE = {"done": False, "current": "(시작 전)"}


def _halt():
    if not _STATE["done"]:
        print(f"TEST-HALTED file=test_switch.py ran={len(results)} last={_STATE['current']}")


atexit.register(_halt)


def report(label, problems):
    results.append((not problems, label))
    print(f"[{'PASS' if not problems else 'FAIL'}] {label}" + (f" — {'; '.join(problems)}" if problems else ""))


LIMITS = '{\n "_comment": "x",\n "account_mode": "paper",\n "other": 1,\n "real_trading_enabled": false,\n "tail": [1, 2]\n}\n'


def _th_status(tid):
    t = db.thesis_get(tid)
    return t.get("status") if t else None


def t1_apply_and_back():
    _STATE["current"] = "t1"
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="sw_"))
    saved = (sw.HERE, sw.JOURNAL, sw.CONFIG, sw.EVID, sw.readers_check, sw.preconditions, sw.run_probe)
    try:
        (tmp / "journal").mkdir()
        (tmp / "config").mkdir()
        sw.HERE, sw.JOURNAL, sw.CONFIG, sw.EVID = tmp, tmp / "journal", tmp / "config", tmp / "data" / "run_evidence"
        bk_dir = db.backup_dir()
        if bk_dir == db.BACKUP_DIR:
            p.append(f"시험 백업이 실제 백업 폴더로 간다: {bk_dir}")
        sw.readers_check = lambda: [("x", 0, "시험")]
        sw.preconditions = lambda: []
        sw.run_probe = lambda st: (0, "**판정: 조회 통과(시험)**")
        theses = [
            {"id": "a", "ticker": "AAA", "market": "KR", "status": "held", "expires": "2099-01-01", "entered_on": "2026-10-01",
             "entry_triggers": [{"id": "e0", "check": "judge", "size_pct": 2, "fired": True, "fired_at": "x", "fired_note": "y"},
                                {"id": "e1", "check": "price <= 10", "size_pct": 1}],
             "exit_triggers": [{"id": "x1", "check": "price <= 5", "fired": True, "fired_at": "x", "fired_note": "y"}],
             "history": [{"ts": "2026-10-01", "status": "held"}]},
            {"id": "b", "ticker": "BBB", "market": "US", "status": "held", "expires": "2026-01-01", "entry_triggers": [], "exit_triggers": []},
            {"id": "c", "ticker": "CCC", "market": "US", "status": "closed", "entry_triggers": [{"id": "e0", "fired": True}]}]
        seed(theses=theses,
             peaks={"KR": {"AAA": {"peak_price": 12, "peak_at": "2026-10-01T10:00:00+09:00"}},
                    "US": {"BBB": {"peak_price": 5, "peak_at": "2026-10-01T23:00:00+09:00"}}},
             reversals=[{"id": "RV-1", "status": "pending", "market": "US", "ticker": "BBB", "ts": "2026-10-14T23:00:00+09:00"}])
        if _th_status("a") != "held" or not db.peaks_for_market("KR") or not db.reversals_pending():
            p.append(f"심은 행이 저장소에서 안 보인다: {_th_status('a')} {db.peaks_all()} {db.reversals_all()}")
        before_a = db.thesis_get("a")
        (tmp / "config" / "limits.json").write_text(LIMITS, encoding="utf-8")
        import kis_client
        saved_mode = kis_client.account_mode
        kis_client.account_mode = lambda cfg=None: "paper"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc_dry = sw.main(["--to", "real", "--stamp", "261016"])
            if rc_dry != 0 or _th_status("a") != "held" or list(bk_dir.glob("trading_paper_final_*.db")):
                p.append(f"예행이 저장소를 바꿨거나 실패: rc={rc_dry}")
            if "모의 행 섞임 0건" not in (sw.EVID / "account_switch_261016.md").read_text(encoding="utf-8"):
                p.append("예행 기록에 '모의 행 섞임 0건'이 없다(점검표 ⑫가 읽는다)")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sw.main(["--to", "real", "--apply", "--stamp", "261016"])
        finally:
            kis_client.account_mode = saved_mode
        th = {t["id"]: t for t in db.theses_all()}
        a = th["a"]
        if rc != 0 or a["status"] != "armed" or "entered_on" in a:
            p.append(f"held → armed·entered_on 제거: rc={rc} {a.get('status')} {'entered_on' in a}")
        if any(k.startswith("fired") for f in ("entry_triggers", "exit_triggers") for tr in a[f] for k in tr):
            p.append("다리 체결 표시가 남았다")
        if "체결 표시 해제: e0, x1" not in a["history"][-1].get("note", "") or not a.get("corrections"):
            p.append(f"이력·교정 기록: {a['history'][-1]}")
        if th["b"]["status"] != "expired":
            p.append("만기 지난 보유가 expired가 아니다")
        if th["c"]["status"] != "closed" or not th["c"]["entry_triggers"][0].get("fired"):
            p.append("닫힌 논지를 건드렸다")
        if db.peaks_all() != {"_schema": 2, "KR": {}, "US": {}}:
            p.append(f"고점이 비지 않았다: {db.peaks_all()}")
        if [r for r in db.reversals_all() if r.get("status") == "pending"]:
            p.append("소급 대기 행이 void되지 않았다")
        lim = (tmp / "config" / "limits.json").read_text(encoding="utf-8")
        if lim != LIMITS.replace('"paper"', '"real"').replace("false", "true"):
            p.append(f"한도 파일 두 값만 바뀌어야 한다(형식 보존): {lim!r}")
        bks = sorted(bk_dir.glob("trading_paper_final_261016*.db"))
        if len(bks) != 1:
            p.append(f"모의 상태 백업이 없다(또는 여럿): {[b.name for b in bks]}")
        else:
            r0 = db.connect(bks[0]).execute("SELECT status FROM jd_thesis WHERE id='a'").fetchone()
            if not r0 or r0[0] != "held":
                p.append(f"백업 속 논지 a가 held가 아니다: {r0 and r0[0]}")
        log = db.log_rows(sw.SWITCH_LOG)
        if not log or log[-1].get("to") != "real":
            p.append(f"전환 기록이 없다: {log}")
        kis_client.account_mode = lambda cfg=None: "real"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc2 = sw.main(["--to", "paper", "--apply", "--stamp", "261016"])
        finally:
            kis_client.account_mode = saved_mode
        after_a = db.thesis_get("a")
        if rc2 != 0 or (after_a or {}).get("status") != "held" or (tmp / "config" / "limits.json").read_text(encoding="utf-8") != LIMITS:
            p.append(f"되돌리기: rc={rc2} {(after_a or {}).get('status')}")
        if after_a != before_a:
            p.append("되돌린 논지 a가 전환 전과 다르다(다리·이력까지 그대로여야 한다)")
        if db.peaks_for_market("KR").get("AAA", {}).get("peak_price") != 12:
            p.append(f"고점이 복원되지 않았다: {db.peaks_all()}")
        rv1 = next((r for r in db.reversals_all() if r.get("id") == "RV-1"), {})
        if rv1.get("status") != "pending":
            p.append(f"void한 소급 행이 전환 전 상태로 돌아오지 않았다: {rv1}")
        if len([r for r in db.log_rows("audit_reversals") if r.get("id") == "RV-1"]) != 3:
            p.append("소급 사건은 덧붙이기만 해야 한다(심기·void·되돌림 3행)")
        log = db.log_rows(sw.SWITCH_LOG)
        if [r.get("to") for r in log] != ["real", "paper"]:
            p.append(f"전환 기록 두 행(real→paper)이어야 한다: {log}")
        sw.preconditions = lambda: ["진행 중 감사 1건(시험)"]
        kis_client.account_mode = lambda cfg=None: "paper"
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc3 = sw.main(["--to", "real", "--apply", "--stamp", "261017"])
        finally:
            kis_client.account_mode = saved_mode
        if rc3 != 3 or _th_status("a") != "held" or list(bk_dir.glob("trading_paper_final_261017*.db")):
            p.append(f"전제 위반인데 적용했다: rc={rc3}")
    finally:
        (sw.HERE, sw.JOURNAL, sw.CONFIG, sw.EVID, sw.readers_check, sw.preconditions, sw.run_probe) = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(1) 전환 적용·되돌리기 — 예행은 안 씀 · held→armed/expired · 다리 표시 해제 · 고점·소급 · 한도 두 값만 · 백업 · "
           "되돌리기는 백업 표 복원 + 소급 사건 덧붙임 · 전제 위반 거부", p)


def t2_readers_check_real():
    """t2 — 실제 readers_check를 모의·실계좌가 섞인 임시 기록 위에서 돌린다(2026-10-19 · Codex R07 — 옛 점검은 비용·통합자산·대시보드
    거래 칸이 글자 0이었다). 실계좌 모드에서 모든 읽기가 모의 행을 0건 집어야 하고, 점검 목록에 비용·회고 거래·당일 지출·통합자산 조각이 있다."""
    _STATE["current"] = "t2"
    import costs
    import dashboard
    import portfolio
    import regret
    import review
    import risk_guard as rg
    import loss_review
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="sw2_"))
    for d in ("journal", "signals", "data", "config"):
        (tmp / d).mkdir()
    targets = [(sw, "HERE"), (sw, "JOURNAL"), (sw, "CONFIG"), (costs, "HERE"), (review, "JOURNAL"), (regret, "HERE"),
               (dashboard, "JOURNAL_DIR"), (dashboard, "SIGNALS_DIR"), (dashboard, "CONFIG_DIR"), (rg, "JOURNAL_DIR"), (rg, "DATA_DIR"),
               (portfolio, "DATA_DIR"), (loss_review, "HERE")]
    saved = [(m, a, getattr(m, a)) for m, a in targets]
    try:
        for m, a in targets:
            setattr(m, a, {"JOURNAL": tmp / "journal", "JOURNAL_DIR": tmp / "journal", "SIGNALS_DIR": tmp / "signals",
                           "CONFIG": tmp / "config", "CONFIG_DIR": tmp / "config", "DATA_DIR": tmp / "data"}.get(a, tmp))
        shutil.copy2(Path(sw.__file__).parent / "config" / "limits.json", tmp / "config" / "limits.json")
        with db_case():
            st_p, st_r = "261016", "261017"
            buy = {"ts": "2026-10-16T10:00:00+09:00", "ticker": "005930", "action": "BUY", "qty": 1, "price": 70000, "status": "SENT",
                   "fill": {"verdict": "FILLED", "filled_qty": 1, "avg_price": 70000}}
            (tmp / "journal" / f"trades_{st_p}_kr.json").write_text(json.dumps({"svr": "paper", "generated_at": "2026-10-16T10:00:00+09:00",
                                                                               "trades": [buy]}), encoding="utf-8")
            (tmp / "journal" / f"trades_{st_r}_kr.json").write_text(json.dumps({"account_mode": "real", "generated_at": "2026-10-17T10:00:00+09:00",
                                                                               "trades": [buy]}), encoding="utf-8")
            (tmp / "signals" / f"approved_{st_p}_kr.json").write_text(json.dumps({"account_mode": "paper", "orders": [], "rejected": []}),
                                                                      encoding="utf-8")
            (tmp / "data" / f"snapshot_{st_p}_us.json").write_text(json.dumps({"account_mode": "paper", "generated_at": "2026-10-16T23:00:00+09:00",
                                                                               "balance": {"exchange_rate": 1400.0, "cash": 1, "positions": []}}),
                                                                   encoding="utf-8")
            seed(equity=[{"date": "2026-10-16", "ts": "2026-10-16T16:00:00+09:00", "market": "KR", "currency": "KRW", "equity": 9e6, "cash": 1e6,
                          "positions": [], "account_mode": "paper"},
                         {"date": "2026-10-17", "ts": "2026-10-17T16:00:00+09:00", "market": "KR", "currency": "KRW", "equity": 1e7, "cash": 1e7,
                          "positions": [], "account_mode": "real"}])
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                res = sw.readers_check()
        bad = [(n, k, why) for n, k, why in res if k]
        names = " ".join(n for n, _, _ in res)
        if bad:
            p.append(f"실계좌 모드에서 모의 행을 집은 읽기: {bad}")
        for need in ("costs.drag(30, real)", "review.trade_rows(KR)", "risk_guard.spent_today(KR)", "portfolio.nav_now(real)"):
            if need not in names:
                p.append(f"점검 목록에 {need}가 없다")
    finally:
        for m, a, v in saved:
            setattr(m, a, v)
        shutil.rmtree(tmp, ignore_errors=True)
    report("t2 실제 readers_check — 섞인 기록에서 모든 읽기가 모의 0건 · 비용·회고 거래·당일 지출·통합자산 조각을 실제로 부른다", p)


with db_case():
    t1_apply_and_back()
try:
    t2_readers_check_real()
except BaseException as e:                           # noqa: BLE001 — 한 케이스가 죽어도 요약 줄은 남긴다
    report(f"t2 실제 readers_check — 중단({type(e).__name__}: {str(e)[:160]})", ["중단"])
_STATE["done"] = True
n_fail = sum(1 for ok, _ in results if not ok)
print(f"\nTEST-SUMMARY file=test_switch.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
