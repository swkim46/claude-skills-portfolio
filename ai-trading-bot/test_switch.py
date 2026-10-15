#!/usr/bin/env python3
"""
계좌 전환(account_switch.py) 시험 — 임시 폴더에서 적용·되돌리기를 끝까지 돈다(실제 원장·한도 파일·브로커는 건드리지 않는다).

왜(2026-10-15): 월요일 첫 실전 run 전에 운영자가 "전환"하면 이 스크립트가 원장을 바꾼다. 한 번에 맞아야 하는 쓰기 경로라
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
import reversals as rv       # noqa: E402

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


def t1_apply_and_back():
    _STATE["current"] = "t1"
    p = []
    tmp = Path(tempfile.mkdtemp(prefix="sw_"))
    saved = (sw.HERE, sw.JOURNAL, sw.CONFIG, sw.EVID, sw.readers_check, sw.preconditions, sw.run_probe, rv.HERE)
    try:
        (tmp / "journal").mkdir()
        (tmp / "config").mkdir()
        sw.HERE, sw.JOURNAL, sw.CONFIG, sw.EVID = tmp, tmp / "journal", tmp / "config", tmp / "data" / "run_evidence"
        rv.HERE = tmp
        sw.readers_check = lambda: [("x", 0, "시험")]
        sw.preconditions = lambda: []
        sw.run_probe = lambda st: (0, "**판정: 조회 통과(시험)**")
        theses = {"theses": [
            {"id": "a", "ticker": "AAA", "market": "KR", "status": "held", "expires": "2099-01-01", "entered_on": "2026-10-01",
             "entry_triggers": [{"id": "e0", "check": "judge", "size_pct": 2, "fired": True, "fired_at": "x", "fired_note": "y"},
                                {"id": "e1", "check": "price <= 10", "size_pct": 1}],
             "exit_triggers": [{"id": "x1", "check": "price <= 5", "fired": True, "fired_at": "x", "fired_note": "y"}],
             "history": [{"ts": "2026-10-01", "status": "held"}]},
            {"id": "b", "ticker": "BBB", "market": "US", "status": "held", "expires": "2026-01-01", "entry_triggers": [], "exit_triggers": []},
            {"id": "c", "ticker": "CCC", "market": "US", "status": "closed", "entry_triggers": [{"id": "e0", "fired": True}]}]}
        (tmp / "journal" / "theses.json").write_text(json.dumps(theses), encoding="utf-8")
        (tmp / "journal" / "position_peaks.json").write_text(json.dumps({"_schema": 2, "KR": {"AAA": 12}, "US": {"BBB": 5}}),
                                                             encoding="utf-8")
        (tmp / "journal" / "audit_reversals.jsonl").write_text(
            json.dumps({"id": "RV-1", "status": "pending", "market": "US", "ticker": "BBB"}) + "\n", encoding="utf-8")
        (tmp / "config" / "limits.json").write_text(LIMITS, encoding="utf-8")
        import kis_client
        saved_mode = kis_client.account_mode
        kis_client.account_mode = lambda cfg=None: "paper"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc_dry = sw.main(["--to", "real", "--stamp", "261016"])
            if rc_dry != 0 or json.loads((tmp / "journal" / "theses.json").read_text())["theses"][0]["status"] != "held":
                p.append(f"예행이 원장을 바꿨거나 실패: rc={rc_dry}")
            if "모의 행 섞임 0건" not in (sw.EVID / "account_switch_261016.md").read_text(encoding="utf-8"):
                p.append("예행 기록에 '모의 행 섞임 0건'이 없다(점검표 ⑫가 읽는다)")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = sw.main(["--to", "real", "--apply", "--stamp", "261016"])
        finally:
            kis_client.account_mode = saved_mode
        th = {t["id"]: t for t in json.loads((tmp / "journal" / "theses.json").read_text(encoding="utf-8"))["theses"]}
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
        pk = json.loads((tmp / "journal" / "position_peaks.json").read_text(encoding="utf-8"))
        if pk != {"_schema": 2, "KR": {}, "US": {}}:
            p.append(f"고점이 비지 않았다: {pk}")
        if [r for r in rv.pending(jdir=tmp / "journal")]:
            p.append("소급 대기 행이 void되지 않았다")
        lim = (tmp / "config" / "limits.json").read_text(encoding="utf-8")
        if lim != LIMITS.replace('"paper"', '"real"').replace("false", "true"):
            p.append(f"한도 파일 두 값만 바뀌어야 한다(형식 보존): {lim!r}")
        cp = tmp / "journal" / "_paper_final_261016"
        if not (cp / "theses.json").exists() or json.loads((cp / "theses.json").read_text())["theses"][0]["status"] != "held":
            p.append("모의 상태 복사본이 없다")
        log = (tmp / "journal" / "account_switch.jsonl").read_text(encoding="utf-8")
        if '"to": "real"' not in log:
            p.append("전환 기록이 없다")
        kis_client.account_mode = lambda cfg=None: "real"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc2 = sw.main(["--to", "paper", "--apply", "--stamp", "261016"])
        finally:
            kis_client.account_mode = saved_mode
        th2 = {t["id"]: t for t in json.loads((tmp / "journal" / "theses.json").read_text(encoding="utf-8"))["theses"]}
        if rc2 != 0 or th2["a"]["status"] != "held" or (tmp / "config" / "limits.json").read_text(encoding="utf-8") != LIMITS:
            p.append(f"되돌리기: rc={rc2} {th2['a']['status']}")
        sw.preconditions = lambda: ["진행 중 감사 1건(시험)"]
        kis_client.account_mode = lambda cfg=None: "paper"
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc3 = sw.main(["--to", "real", "--apply", "--stamp", "261017"])
        finally:
            kis_client.account_mode = saved_mode
        if rc3 != 3 or json.loads((tmp / "journal" / "theses.json").read_text())["theses"][0]["status"] != "held":
            p.append(f"전제 위반인데 적용했다: rc={rc3}")
    finally:
        (sw.HERE, sw.JOURNAL, sw.CONFIG, sw.EVID, sw.readers_check, sw.preconditions, sw.run_probe, rv.HERE) = saved
        shutil.rmtree(tmp, ignore_errors=True)
    report("(1) 전환 적용·되돌리기 — 예행은 안 씀 · held→armed/expired · 다리 표시 해제 · 고점·소급 · 한도 두 값만 · 복사본 · 전제 위반 거부", p)


t1_apply_and_back()
_STATE["done"] = True
n_fail = sum(1 for ok, _ in results if not ok)
print(f"\nTEST-SUMMARY file=test_switch.py total={len(results)} passed={len(results) - n_fail} failed={n_fail}")
sys.exit(1 if n_fail else 0)
